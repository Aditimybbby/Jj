# This file is part of LazyFarmers.
# Copyright (c) 2025-Present Routo
#
# LazyFarmers is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with LazyFarmers. If not, see <https://www.gnu.org/licenses/>.


"""
The single reader/writer for a space's accounts.json and its proxy pool.

Every public function takes the owning space id first (see core/spaces.py), so a
dashboard user can only ever touch the accounts and proxies inside their own
space. Pass spaces.ADMIN_SPACE for the operator's own farm.
"""



import asyncio
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import aiohttp
except ImportError:
    aiohttp = None

from core import spaces

DEFAULT_PROXY_TYPE = "socks5"
SUPPORTED_TYPES = ("http", "https", "socks5", "socks4")

PROXY_ID_RE = re.compile(r"^px_[0-9a-f]{8}$")
# an account name is rendered in the dashboard and stored in proxy records,
# so keep it to characters that cannot turn into markup or a path
ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,40}$")


def _new_proxy_id():
    return f"px_{secrets.token_hex(4)}"


def valid_proxy_id(proxy_id):
    return bool(PROXY_ID_RE.match(str(proxy_id or "")))


def valid_account_name(name):
    return bool(ACCOUNT_NAME_RE.match(str(name or "")))


# Every function in here is a read-modify-write of a json file, and the callers do
# not share a thread: the dashboard's routes run on waitress' worker threads while
# bot.flag_account() and bot._persist_user_id() run on the asyncio loop (once per
# READY *and* once per reconnect, so a 50-account farm coming up is a hundred-odd
# rewrites racing whatever the operator is editing). Unsynchronised that produced
# two distinct failures:
#
#   lost update  - the edit was read, modified and saved, then a bot's write that
#                  had loaded the older file saved on top of it. The accounts page
#                  looked like it had done nothing.
#   torn file    - both writers opened, truncated and interleaved the *same*
#                  accounts.json.tmp before one of them promoted it. The result is
#                  invalid json, load_accounts returned [], the dashboard showed
#                  zero accounts, and the next save persisted that emptiness -
#                  destroying every stored token for real.
#
# One reentrant lock covers accounts.json and proxies.json together, because
# sync_proxy_assignments and auto_assign write both and would otherwise need a
# lock order. Hold time is a json.dump of a few hundred small dicts.
_FILE_LOCK = threading.RLock()


class AccountsUnreadable(RuntimeError):
    """accounts.json exists but neither it nor its backup could be parsed.

    Deliberately loud. Returning an empty list here is what let a damaged file be
    laundered into a permanent wipe of the whole farm.
    """


def _write_json(path, payload):
    """Atomically replace `path`, keeping the previous contents as `path.bak`.

    The temp name carries pid+thread id so two writers can never share it, and the
    backup is what makes a torn or truncated file recoverable instead of fatal.

    Everything inside the lock is deliberately cheap. `flag_account`/`_persist_user_id`
    call this from the asyncio loop (once per READY), and the dashboard's waitress
    threads call it too, so the loop thread has to be able to take `_FILE_LOCK` and
    get out fast. An earlier version fsync'd the temp file and copied the whole old
    file byte-for-byte into `.bak`, both while holding the lock - on a network-backed
    volume that is tens to hundreds of ms of blocking disk I/O, and when a web thread
    held the lock across it the loop thread stalled behind it: heartbeats stopped,
    accounts dropped and reconnected with a cold channel cache (the fetch_channel
    "Missing Access" flood), and every loop-bound dashboard route hung with them.
    Two atomic renames give the same crash-safety - `path` is always either the old
    file or the new one, never a half-written one - without reading or fsyncing a
    single byte under the lock.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with _FILE_LOCK:
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            if os.path.exists(path):
                try:
                    # a rename, not a byte copy: promoting the current file to .bak
                    # is a metadata op, so the lock is held for microseconds
                    os.replace(path, path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _read_json(path):
    """(payload, ok). ok is False when the file is there but unparseable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, False
        return data, True
    except (json.JSONDecodeError, ValueError):
        return None, False
    except OSError:
        return None, False


def load_proxies(owner):
    path = spaces.proxies_path(owner)
    with _FILE_LOCK:
        if not os.path.exists(path):
            # missing-with-a-backup is a crash in _write_json's rename window, not a
            # fresh space: recover the list rather than seeding (and then saving) empty
            if os.path.exists(path + ".bak"):
                data, ok = _read_json(path + ".bak")
                if ok:
                    return data.get("proxies", [])
            save_proxies(owner, [])
            return []
        data, ok = _read_json(path)
        if ok:
            return data.get("proxies", [])
        # the pool is re-derivable by re-importing, so a bad read is not fatal
        # here - but prefer the backup over losing the whole list
        data, ok = _read_json(path + ".bak")
        return data.get("proxies", []) if ok else []


def save_proxies(owner, proxies):
    with _FILE_LOCK:
        _write_json(spaces.proxies_path(owner), {"proxies": proxies})


def load_accounts(owner):
    """The space's accounts, or the last good backup if the file is damaged.

    Raises AccountsUnreadable when both copies are unparseable. Every caller that
    might then write the result back has to see the failure rather than a plausible
    empty list.
    """
    path = spaces.accounts_path(owner)
    with _FILE_LOCK:
        if os.path.exists(path):
            data, ok = _read_json(path)
            if ok:
                return data.get("accounts", [])
        elif not os.path.exists(path + ".bak"):
            # a brand-new space has neither file: that is a real empty, not a loss
            return []
        # Reaching here means the file is missing-but-a-backup-exists (a crash in the
        # rename window of _write_json) or present-but-unparseable. Both recover from
        # .bak rather than reporting empty - an empty here is what a save would then
        # persist over the real tokens.
        backup, ok = _read_json(path + ".bak")
        if ok:
            recovered = backup.get("accounts", [])
            print(f"[!] {path} was missing or unreadable - recovered {len(recovered)} "
                  f"account(s) from accounts.json.bak", flush=True)
            _write_json(path, {"accounts": recovered})
            return recovered

        raise AccountsUnreadable(f"{path} is corrupt and accounts.json.bak cannot be read")


def load_accounts_or_empty(owner):
    """load_accounts for read-only callers that must not crash on a bad file.

    Only for paths that display or count accounts. Never use it as the read half of
    a read-modify-write - that is exactly how a corrupt file becomes a real wipe.
    """
    try:
        return load_accounts(owner)
    except AccountsUnreadable as exc:
        print(f"[!] {exc}", flush=True)
        return []


def save_accounts(owner, accounts):
    with _FILE_LOCK:
        _write_json(spaces.accounts_path(owner), {"accounts": accounts})


def wants_autostart(account):
    """True when this account should come up by itself when the process starts.

    A missing flag means yes: configs written before autostart existed, and every
    freshly added account, are expected to farm without being told twice.
    """
    return bool(account.get("autostart", True))


def set_account_autostart(owner, name, autostart):
    """Persist whether an account comes back up on the next process start.

    The dashboard's Start/Stop buttons are the operator stating intent, not just
    poking the current process - so an account they stopped has to stay stopped
    across a redeploy, a crash or a plain restart.
    """
    return set_accounts_autostart(owner, [name], autostart)


def set_accounts_autostart(owner, names, autostart):
    """Same, for many accounts in one read-modify-write.

    Start All / Stop All used to call the single-name version once per account:
    200 accounts meant 200 full reads and up to 200 fsync'd rewrites of
    accounts.json, all inside one web request, while the browser waited.
    """
    wanted = {str(n) for n in (names or []) if n}
    if not wanted:
        return 0
    accounts = load_accounts(owner)
    changed = 0
    for account in accounts:
        if str(account.get("name")) not in wanted:
            continue
        if wants_autostart(account) != bool(autostart):
            account["autostart"] = bool(autostart)
            changed += 1
    if changed:
        save_accounts(owner, accounts)
    return changed


def set_account_status(owner, name, status, reason=None):
    """Record why an account is unusable so the dashboard can group it separately."""
    if not name:
        return
    accounts = load_accounts(owner)
    changed = False
    for account in accounts:
        if str(account.get("name")) != str(name):
            continue
        if account.get("status", "ok") == status and account.get("status_reason") == reason:
            return
        account["status"] = status
        account["status_reason"] = reason
        account["status_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        changed = True
    if changed:
        save_accounts(owner, accounts)


def set_account_user_id(owner, name, user_id):
    """Remember which discord account a config entry logged in as.

    Nothing used to persist this, so `spaces.owner_for_account` could only answer
    for accounts running in *this* process - its accounts.json fallback matched
    nothing and `owns_account()` said no. The dashboard then returned an empty
    /api/stats for a stopped account even to the space that owns it, hiding all
    its history. Written once per ready; a token swapped onto an existing entry
    overwrites it.
    """
    if not name or not user_id:
        return False
    accounts = load_accounts(owner)
    changed = False
    for account in accounts:
        if str(account.get("name")) != str(name):
            continue
        if str(account.get("user_id") or "") != str(user_id):
            account["user_id"] = str(user_id)
            changed = True
    if changed:
        save_accounts(owner, accounts)
    return changed


def _normalize_type(proxy_type):
    proxy_type = (proxy_type or DEFAULT_PROXY_TYPE).lower().strip()
    if proxy_type not in SUPPORTED_TYPES:
        return DEFAULT_PROXY_TYPE
    return proxy_type


def _proxy_fingerprint(host, port, username="", password="", proxy_type=DEFAULT_PROXY_TYPE):
    return f"{_normalize_type(proxy_type)}://{username}:{password}@{host}:{port}"


def parse_proxy_line(line):
    """
    parse a single proxy line.
    support: host:port, user:pass@host:port, socks5://..., http://...
    returns (proxy_dict, error_message).
    """
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None, "empty"

    proxy_type = DEFAULT_PROXY_TYPE
    username = ""
    password = ""
    host = ""
    port = None

    if "://" in line:
        parsed = urlparse(line)
        proxy_type = _normalize_type(parsed.scheme)
        host = parsed.hostname or ""
        port = parsed.port
        username = parsed.username or ""
        password = parsed.password or ""
        if not host or not port:
            return None, f"invalid URL: {line}"
    else:
        auth_part = None
        host_part = line
        if "@" in line:
            auth_part, host_part = line.rsplit("@", 1)

        if auth_part and ":" in auth_part:
            username, password = auth_part.split(":", 1)

        if ":" not in host_part:
            return None, f"missing port: {line}"

        host, port_str = host_part.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return None, f"invalid port: {line}"

    if not host or not port:
        return None, f"invalid format: {line}"

    label = f"{host}:{port}"
    return {
        "id": _new_proxy_id(),
        "label": label,
        "type": _normalize_type(proxy_type),
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "enabled": True,
        "status": "unknown",
        "last_check": None,
        "assigned_to": None,
    }, None


def build_proxy_url(proxy_dict):
    if not proxy_dict:
        return None
    proxy_type = _normalize_type(proxy_dict.get("type"))
    host = proxy_dict.get("host", "")
    port = proxy_dict.get("port")
    if not host or not port:
        return None
    return f"{proxy_type}://{host}:{port}"


def get_proxy_auth(proxy_dict):
    if not proxy_dict:
        return None
    username = proxy_dict.get("username") or ""
    password = proxy_dict.get("password") or ""
    if username:
        return aiohttp.BasicAuth(username, password)
    return None


def get_proxy_by_id(owner, proxy_id, proxies=None):
    if not proxy_id:
        return None
    for proxy in (proxies if proxies is not None else load_proxies(owner)):
        if proxy.get("id") == proxy_id and proxy.get("enabled", True):
            return proxy
    return None


def resolve_account_proxy(owner, account, proxies=None):
    """Turn an account's proxy_id into (url, auth, label).

    `proxies` lets a caller that resolves many accounts in a row read the pool
    once: without it, starting a 200-account farm re-read and re-parsed
    proxies.json 200 times, on the event loop, before the first login.
    """
    proxy_id = account.get("proxy_id") if account else None
    if not proxy_id:
        return None, None, "direct"

    proxy = get_proxy_by_id(owner, proxy_id, proxies=proxies)
    if not proxy:
        return None, None, "direct"

    label = proxy.get("label") or f"{proxy.get('host')}:{proxy.get('port')}"
    return build_proxy_url(proxy), get_proxy_auth(proxy), label


def bulk_import(owner, text, limit=None):
    """Add every parseable proxy line to a space's pool.

    `limit` caps the pool size so one space cannot fill the disk with a paste;
    lines past the cap come back as errors rather than being silently dropped.
    """
    existing = load_proxies(owner)
    fingerprints = {
        _proxy_fingerprint(p.get("host"), p.get("port"), p.get("username", ""), p.get("password", ""), p.get("type"))
        for p in existing
    }

    added = []
    errors = []
    for i, raw_line in enumerate(text.splitlines(), start=1):
        proxy, err = parse_proxy_line(raw_line)
        if err == "empty":
            continue
        if err:
            errors.append({"line": i, "text": raw_line.strip(), "error": err})
            continue

        fp = _proxy_fingerprint(
            proxy["host"], proxy["port"], proxy.get("username", ""), proxy.get("password", ""), proxy.get("type")
        )
        if fp in fingerprints:
            errors.append({"line": i, "text": raw_line.strip(), "error": "duplicate"})
            continue

        if limit is not None and len(existing) >= limit:
            errors.append({"line": i, "text": raw_line.strip(),
                           "error": f"pool limit reached ({limit})"})
            continue

        fingerprints.add(fp)
        existing.append(proxy)
        added.append(proxy)

    if added:
        save_proxies(owner, existing)
    return {"added": added, "errors": errors, "total": len(existing)}


async def _request_through_proxy(proxy_dict, timeout=5):
    url = build_proxy_url(proxy_dict)
    auth = get_proxy_auth(proxy_dict)
    proxy_type = _normalize_type(proxy_dict.get("type"))

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    try:
        if proxy_type in ("socks4", "socks5"):
            from aiohttp_socks import ProxyConnector

            connector = ProxyConnector.from_url(url, rdns=True)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout_cfg) as session:
                async with session.get("https://discord.com/api/v9/gateway") as resp:
                    return resp.status < 500
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.get("https://discord.com/api/v9/gateway", proxy=url, proxy_auth=auth) as resp:
                return resp.status < 500
    except Exception:
        return False


async def test_proxy(proxy_dict):
    ok = await _request_through_proxy(proxy_dict)
    proxy_dict["status"] = "ok" if ok else "fail"
    proxy_dict["last_check"] = datetime.now(timezone.utc).isoformat()
    return ok


async def test_all_proxies(owner):
    proxies = load_proxies(owner)
    results = []
    for proxy in proxies:
        if not proxy.get("enabled", True):
            continue
        ok = await test_proxy(proxy)
        results.append({"id": proxy.get("id"), "ok": ok})
    save_proxies(owner, proxies)
    return results


def _sync_assigned_to(owner, proxies, accounts):
    account_names = {a.get("name"): a for a in accounts}
    proxy_ids = {p.get("id") for p in proxies}
    for proxy in proxies:
        assigned = proxy.get("assigned_to")
        if assigned and assigned not in account_names:
            proxy["assigned_to"] = None
    for acc in accounts:
        pid = acc.get("proxy_id")
        if pid and pid in proxy_ids:
            for proxy in proxies:
                if proxy.get("id") == pid:
                    proxy["assigned_to"] = acc.get("name")
    save_proxies(owner, proxies)


def auto_assign(owner):
    proxies = load_proxies(owner)
    accounts = load_accounts(owner)

    free_proxies = [
        p for p in proxies
        if p.get("enabled", True)
        and not p.get("assigned_to")
        and p.get("status") == "ok"
    ]
    unassigned_accounts = [a for a in accounts if not a.get("proxy_id")]

    assigned = []
    for acc, proxy in zip(unassigned_accounts, free_proxies):
        acc["proxy_id"] = proxy["id"]
        proxy["assigned_to"] = acc.get("name")
        assigned.append({"account": acc.get("name"), "proxy_id": proxy["id"]})

    if assigned:
        save_accounts(owner, accounts)
        save_proxies(owner, proxies)
    return assigned


def remove_proxy(owner, proxy_id):
    proxies = load_proxies(owner)
    proxies = [p for p in proxies if p.get("id") != proxy_id]
    save_proxies(owner, proxies)

    accounts = load_accounts(owner)
    changed = False
    for acc in accounts:
        if acc.get("proxy_id") == proxy_id:
            acc["proxy_id"] = None
            changed = True
    if changed:
        save_accounts(owner, accounts)
    return True


def remove_all_proxies(owner):
    save_proxies(owner, [])
    accounts = load_accounts(owner)
    changed = False
    for acc in accounts:
        if acc.get("proxy_id"):
            acc["proxy_id"] = None
            changed = True
    if changed:
        save_accounts(owner, accounts)
    return True


def remove_failed_proxies(owner):
    proxies = load_proxies(owner)
    failed_ids = {p["id"] for p in proxies if p.get("status") == "fail"}
    proxies = [p for p in proxies if p.get("id") not in failed_ids]
    save_proxies(owner, proxies)

    if failed_ids:
        accounts = load_accounts(owner)
        changed = False
        for acc in accounts:
            if acc.get("proxy_id") in failed_ids:
                acc["proxy_id"] = None
                changed = True
        if changed:
            save_accounts(owner, accounts)
    return len(failed_ids)



def unassign_proxy_from_accounts(owner, proxy_id):
    accounts = load_accounts(owner)
    changed = False
    for acc in accounts:
        if acc.get("proxy_id") == proxy_id:
            acc["proxy_id"] = None
            changed = True
    if changed:
        save_accounts(owner, accounts)


def sync_proxy_assignments(owner):
    proxies = load_proxies(owner)
    accounts = load_accounts(owner)
    _sync_assigned_to(owner, proxies, accounts)


def mask_token(token):
    if not token or len(token) < 12:
        return token
    return f"{token[:6]}...{token[-4:]}"
