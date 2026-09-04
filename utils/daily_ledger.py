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


"""Cross-restart ledger for the once-a-day OwO commands.

`owo daily`, `owo cookie`, `owo run`, `owo pup` and `owo piku` are all daily:
OwO refuses them for the rest of the day the moment they have been used. That
was remembered in a plain dict (rpp - gone on every restart) or in one shared
JSON file that every account rewrote in place without a lock (daily, cookie), so
a restart, or two accounts saving in the same instant and truncating the file,
put every account back to "never ran" and the command went out again on its
normal timer. All day, every 60 seconds, for `owo run`.

One file, keyed by discord user id, written atomically under a lock:

    {"408785106942164992": {"daily": 1764547200.0, "rpp:run": 1764547200.0}}

Values are unix timestamps meaning "locked until"; anything in the past is
unlocked. Locks only ever extend, so a late duplicate reply cannot shorten one.
"""


import json
import os
import random
import threading
import time

from core.paths import DATA_DIR

_PATH = os.path.join(DATA_DIR, 'daily_locks.json')
_DAY = 86400

_lock = threading.Lock()
_cache = None
_dirty_at = 0.0

# keys the rest of the codebase locks. Kept here so a typo in a cog is visible.
DAILY = 'daily'
COOKIE = 'cookie'
RPP_KEYS = {'run': 'rpp:run', 'pup': 'rpp:pup', 'piku': 'rpp:piku'}


def _read():
    """The whole ledger, loaded once. Call with _lock held."""
    global _cache
    if _cache is None:
        try:
            with open(_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _write():
    """Atomic replace. Call with _lock held.

    A lost write costs one wasted command tomorrow, so this never raises into
    the bot loop.
    """
    tmp = _PATH + '.tmp'
    try:
        os.makedirs(os.path.dirname(_PATH) or '.', exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, indent=2, sort_keys=True)
        os.replace(tmp, _PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def next_daily_reset(jitter=300.0):
    """Unix time of the next 00:00 UTC, which is when OwO's daily counters roll.

    The epoch is aligned to UTC midnight, so this is just arithmetic. A small
    positive jitter keeps 200 accounts from all firing `owo daily` in the same
    second the reset lands - that pattern is exactly what earns a captcha.
    """
    base = float((int(time.time()) // _DAY + 1) * _DAY)
    return base + (random.uniform(0, jitter) if jitter else 0.0)


def locked_until(user_id, key):
    uid = str(user_id or '')
    if not uid or not key:
        return 0.0
    with _lock:
        entry = _read().get(uid) or {}
        try:
            return float(entry.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0


def is_locked(user_id, key):
    return locked_until(user_id, key) > time.time()


def remaining(user_id, key):
    return max(0.0, locked_until(user_id, key) - time.time())


def lock(user_id, key, until=None, seconds=None, force=False):
    """Lock a daily command until `until` (or for `seconds`).

    Only ever extends, so a late duplicate reply cannot shorten a lock - unless
    `force` is set, which is for a figure OwO stated itself ("you can give
    another cookie in 4h"). That one is authoritative and may shorten it.

    Returns the timestamp now in force, so a caller can log it.
    """
    uid = str(user_id or '')
    if not uid or not key:
        return 0.0
    if until is None:
        until = time.time() + float(seconds) if seconds else next_daily_reset()
    until = float(until)
    with _lock:
        data = _read()
        entry = data.setdefault(uid, {})
        try:
            current = float(entry.get(key) or 0.0)
        except (TypeError, ValueError):
            current = 0.0
        if until <= current and not force:
            return current
        entry[key] = until
        _prune_locked(data)
        _write()
    return until


def clear(user_id, key):
    """Forget a lock - used when OwO proves the command is available again."""
    uid = str(user_id or '')
    with _lock:
        data = _read()
        entry = data.get(uid)
        if not entry or key not in entry:
            return False
        entry.pop(key, None)
        if not entry:
            data.pop(uid, None)
        _write()
    return True


def snapshot(user_id):
    """Every live lock for one account, as {key: seconds_remaining}."""
    uid = str(user_id or '')
    now = time.time()
    with _lock:
        entry = dict(_read().get(uid) or {})
    out = {}
    for key, value in entry.items():
        try:
            rem = float(value) - now
        except (TypeError, ValueError):
            continue
        if rem > 0:
            out[key] = round(rem, 1)
    return out


def _prune_locked(data):
    """Drop expired entries so the file cannot grow forever. _lock held."""
    now = time.time()
    for uid in list(data.keys()):
        entry = data.get(uid)
        if not isinstance(entry, dict):
            data.pop(uid, None)
            continue
        for key in list(entry.keys()):
            try:
                if float(entry[key]) <= now:
                    entry.pop(key, None)
            except (TypeError, ValueError):
                entry.pop(key, None)
        if not entry:
            data.pop(uid, None)


def adopt_legacy(user_id, key, last_run, period=_DAY):
    """Seed a lock from a pre-ledger `last_run` timestamp, once.

    The old per-cog files (stats_daily.json, stats_cookie.json) recorded when a
    command last went out. Reading them in means an upgrade does not hand every
    account one free duplicate send.
    """
    try:
        last_run = float(last_run or 0)
    except (TypeError, ValueError):
        return 0.0
    if last_run <= 0:
        return 0.0
    until = last_run + float(period)
    if until <= time.time():
        return 0.0
    return lock(user_id, key, until=until)
