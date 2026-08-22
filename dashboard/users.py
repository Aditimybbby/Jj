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
Author: Routo
LazyFarmers - https://github.com/routo-loop/neura-self

Activation keys and dashboard user accounts.

The admin generates a one-time activation key that is good for N days. A user
redeems it on /activate with an email and a password of their choosing; that
creates a dashboard login which stops working when the key's duration runs out
or when the admin revokes it.

NOTE ON PASSWORDS: they are stored in clear text on purpose - the operator asked
to be able to read a user's password back from the admin panel. The store file
therefore holds credentials and is gitignored. Do not expose it.
"""


import json
import os
import re
import secrets
import string
import threading
import time
import uuid

import core.state as state


STORE_FILE = os.path.join(state.CONFIG_DIR, 'dashboard_users.json')
_lock = threading.Lock()

DAY = 86400
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
KEY_ALPHABET = string.ascii_uppercase + string.digits


def _empty_store():
    return {'keys': [], 'users': []}


def _read():
    try:
        with open(STORE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return _empty_store()
    except Exception:
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault('keys', [])
    data.setdefault('users', [])
    return data


def _write(store):
    os.makedirs(os.path.dirname(STORE_FILE) or '.', exist_ok=True)
    tmp = STORE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(store, f, indent=4)
    os.replace(tmp, STORE_FILE)


# --------------------------------------------------------------------------
# activation keys
# --------------------------------------------------------------------------

def _new_key():
    blocks = [''.join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(3)]
    return 'LF-' + '-'.join(blocks)


def normalise_key(raw):
    return re.sub(r'[^A-Z0-9]', '', str(raw or '').upper())


def _key_matches(entry, wanted):
    return normalise_key(entry.get('key')) == wanted


def generate_keys(days, count=1, note=''):
    """Create `count` unused keys, each worth `days` days once redeemed."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return [], 'Duration must be at least 1 day'
    count = max(1, min(50, int(count or 1)))

    created = []
    with _lock:
        store = _read()
        existing = {normalise_key(k.get('key')) for k in store['keys']}
        for _ in range(count):
            key = _new_key()
            while normalise_key(key) in existing:
                key = _new_key()
            existing.add(normalise_key(key))
            entry = {
                'key': key,
                'days': days,
                'note': str(note or '')[:200],
                'created_at': time.time(),
                'used_by': None,
                'used_at': None,
            }
            store['keys'].append(entry)
            created.append(entry)
        _write(store)
    return created, None


def list_keys():
    with _lock:
        return list(_read()['keys'])


def delete_key(key):
    wanted = normalise_key(key)
    with _lock:
        store = _read()
        before = len(store['keys'])
        store['keys'] = [k for k in store['keys'] if not _key_matches(k, wanted)]
        removed = before - len(store['keys'])
        if removed:
            _write(store)
    return removed > 0


def key_status(key):
    """(entry, error) for an activation key without redeeming it."""
    wanted = normalise_key(key)
    if not wanted:
        return None, 'Enter an activation key'
    with _lock:
        for entry in _read()['keys']:
            if _key_matches(entry, wanted):
                if entry.get('used_by'):
                    return None, 'That key has already been used'
                return dict(entry), None
    return None, 'That key is not valid'


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------

def _public_user(user):
    """The shape handed to the admin panel - includes the password by request."""
    out = dict(user)
    out['days_left'] = days_left(user)
    out['expired'] = is_expired(user)
    return out


def days_left(user):
    expires = user.get('expires_at') or 0
    if not expires:
        return None
    return max(0, round((expires - time.time()) / DAY, 1))


def is_expired(user):
    expires = user.get('expires_at') or 0
    return bool(expires) and time.time() >= expires


def is_active(user):
    return bool(user) and not user.get('revoked') and not is_expired(user)


def redeem_key(key, email, password):
    """Turn a one-time key into a dashboard login. Returns (user, error)."""
    wanted = normalise_key(key)
    email = str(email or '').strip().lower()
    password = str(password or '')

    if not wanted:
        return None, 'Enter an activation key'
    if not EMAIL_RE.match(email):
        return None, 'Enter a valid email address'
    if len(password) < 6:
        return None, 'Password must be at least 6 characters'

    with _lock:
        store = _read()

        entry = next((k for k in store['keys'] if _key_matches(k, wanted)), None)
        if entry is None:
            return None, 'That key is not valid'
        if entry.get('used_by'):
            return None, 'That key has already been used'
        if any(u.get('email') == email for u in store['users']):
            return None, 'An account with that email already exists'

        now = time.time()
        days = int(entry.get('days') or 0)
        user = {
            'id': f"u_{uuid.uuid4().hex[:12]}",
            'email': email,
            'password': password,
            'key': entry.get('key'),
            'days': days,
            'created_at': now,
            'expires_at': now + (days * DAY),
            'revoked': False,
            'last_login': None,
        }
        entry['used_by'] = email
        entry['used_at'] = now
        store['users'].append(user)
        _write(store)

    return _public_user(user), None


def authenticate(email, password):
    """(user, error) for an email/password pair."""
    email = str(email or '').strip().lower()
    with _lock:
        store = _read()
        user = next((u for u in store['users'] if u.get('email') == email), None)
        if not user or user.get('password') != password:
            return None, 'Invalid Credentials'
        if user.get('revoked'):
            return None, 'Your access has been removed'
        if is_expired(user):
            return None, 'Your access has expired'
        user['last_login'] = time.time()
        _write(store)
        return _public_user(user), None


def get_user(user_id):
    with _lock:
        user = next((u for u in _read()['users'] if u.get('id') == user_id), None)
        return _public_user(user) if user else None


def list_users():
    with _lock:
        return [_public_user(u) for u in _read()['users']]


def _mutate_user(user_id, fn):
    with _lock:
        store = _read()
        user = next((u for u in store['users'] if u.get('id') == user_id), None)
        if not user:
            return None, 'No such user'
        error = fn(user)
        if error:
            return None, error
        _write(store)
        return _public_user(user), None


def set_revoked(user_id, revoked):
    def apply(user):
        user['revoked'] = bool(revoked)
        return None
    return _mutate_user(user_id, apply)


def extend_user(user_id, days):
    try:
        days = float(days)
    except (TypeError, ValueError):
        return None, 'Days must be a number'
    if days == 0:
        return None, 'Days must not be zero'

    def apply(user):
        # extending an already-expired user restarts from now, not from the
        # stale expiry, so "+7 days" always means seven days from today
        base = max(user.get('expires_at') or 0, time.time())
        user['expires_at'] = max(time.time(), base + (days * DAY))
        return None
    return _mutate_user(user_id, apply)


def set_password(user_id, password):
    password = str(password or '')
    if len(password) < 6:
        return None, 'Password must be at least 6 characters'

    def apply(user):
        user['password'] = password
        return None
    return _mutate_user(user_id, apply)


def delete_user(user_id):
    with _lock:
        store = _read()
        before = len(store['users'])
        store['users'] = [u for u in store['users'] if u.get('id') != user_id]
        removed = before - len(store['users'])
        if removed:
            _write(store)
    return removed > 0
