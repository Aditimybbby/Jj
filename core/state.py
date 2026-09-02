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
Shared mutable state: live bot instances, per-account stats and the log sink.
"""



import time
import json
import os
import re
import datetime
import threading
from collections import deque
import utils.history_tracker as ht

from core import spaces
from core.paths import BASE_DIR, CONFIG_DIR, DATA_DIR, USERS_DIR

# move the pre-spaces single-tenant files into the admin space, once
spaces.migrate_legacy()

# discord user id (str) -> owner id, owned by core.spaces so history_tracker can
# reach it without importing this module
account_owners = spaces.account_owners

log_config = {}
LOG_MISC_PATH = os.path.join(CONFIG_DIR, 'logmisc.json')
if os.path.exists(LOG_MISC_PATH):
    with open(LOG_MISC_PATH, 'r') as f:
        log_config = json.load(f)

bot_instances = []
stats = {
    'uptime_start': time.time()
}


def owner_of(bot_id):
    """Which space a running discord account belongs to."""
    return account_owners.get(str(bot_id or '')) or spaces.ADMIN_SPACE


def bots_for(owner):
    """Live bots inside one space."""
    return [b for b in bot_instances if getattr(b, 'space_owner', spaces.ADMIN_SPACE) == owner]


def owns_bot(owner, bot):
    if bot is None:
        return False
    return getattr(bot, 'space_owner', spaces.ADMIN_SPACE) == owner


def visible_logs(entries, owner, limit=None):
    """Filter the log sink down to what one space is allowed to read.

    Lines carrying neither an owner nor a bot_id came from the process itself;
    only the operator sees those.
    """
    out = []
    for entry in entries:
        tag = entry.get('owner')
        if tag is None:
            bot_id = entry.get('bot_id')
            tag = owner_of(bot_id) if bot_id else None
        if tag is None:
            if owner != spaces.ADMIN_SPACE:
                continue
        elif tag != owner:
            continue
        out.append(entry)
        if limit and len(out) >= limit:
            break
    return out

checking_gems = {}
missing_gems_cache = {}
STATS_FILE = os.path.join(DATA_DIR, 'stats.json')

account_stats = {}


def empty_earning():
    """A fresh earning ledger.

    The ledger is a *cash-flow* ledger, not a sum of parsed rewards: every figure
    below comes from a change in ``current_cash``, and an OwO reply only decides
    which bucket a change lands in. That is what keeps it honest - the buckets
    always add up to ``current_cash - start_cash``, so no wording change on OwO's
    side can make the tab claim a profit that is not in the account.
    """
    return {
        'started_at': None,
        'start_cash': None,
        'last_cash': None,
        'last_cash_at': None,
        'gained_sell': 0,
        'gained_other': 0,
        'spent_autohunt': 0,
        'spent_hunt': 0,
        'spent_other': 0,
        'sold_count': 0,
        'hunts': 0,
        'autohunt_runs': 0,
        # free, but they are the reinvestment: a stronger team hunts better
        'battles': 0,
        'team_changes': 0,
        'last_sell_amount': None,
        'last_event': None,
        'last_event_at': None,
    }


def empty_owner_send():
    """A fresh daily gifting allowance for `farmers send`.

    OwO limits how much cowoncy one account may gift per day, and it does not tell
    you how much of that is left - so the only way to stop `farmers send` from
    firing a give OwO will refuse is to count what we sent ourselves. ``day`` is a
    UTC date string, which is when OwO's day rolls over.
    """
    return {'day': None, 'sent': 0}


def get_empty_stats():
    return {
        'uptime_start': time.time(),
        'last_reset_date': datetime.datetime.now().strftime("%Y-%m-%d"),
        'start_cash': 0,
        'current_cash': 0,
        'cowoncy_history': [],
        'gems_used': 0,
        'captchas_solved': 0,
        'bans_detected': 0,
        'warnings_detected': 0,
        'hunt_count': 0,
        'battle_count': 0,
        'owo_count': 0,
        'last_captcha_msg': '',
        'current_captcha': None,
        'captchas_solved_today': 0,
        'captcha_success_count': 0,
        'pending_commands': [],
        'last_cooldown': {},
        'total_cmd_count': 0,
        'other_count': 0,
        'username': 'Unknown',
        'level': None,
        'xp': None,
        'xp_needed': None,
        'rank': None,
        # 'text' when owo printed the numbers, 'image' when it sent a rendered card we
        # could not read. The dashboard shows the card itself rather than a stale number.
        'level_source': None,
        'level_card_url': None,
        'last_level_update': None,
        'quest_data': [],
        'quest_source': None,
        'quest_card_url': None,
        'quest_seals': None,
        'next_quest_timer': None,
        'next_quest_at': None,
        'session_hunt_count': 0,
        'session_battle_count': 0,
        'session_owo_count': 0,
        # survives a restart, unlike uptime_start - an earning run is measured from
        # when the operator switched the mode on, not from the last reboot
        'earning': empty_earning(),
        # how much `farmers send` has already handed over today. OwO caps a day's
        # gifting per account, so this has to outlive a restart or a reboot would
        # hand the account a fresh allowance it does not have.
        'owner_send': empty_owner_send(),
        'gambling_stats': {
            'total_wins': 0,
            'total_losses': 0,
            'total_wagered': 0,
            'net_profit': 0,
            'current_streak': 0,
            'best_streak': 0,
            'worst_streak': 0,
            'biggest_win': 0,
            'last_outcome': None
        }
    }

def _write_account_stats():
    """Serialise account_stats to STATS_FILE. Only ever called by save_account_stats."""
    serializable_stats = {}
    for uid, st in account_stats.items():
        serializable_stats[uid] = {
            'last_reset_date': st.get('last_reset_date'),
            'captchas_solved': st.get('captchas_solved', 0),
            'bans_detected': st.get('bans_detected', 0),
            'warnings_detected': st.get('warnings_detected', 0),
            'hunt_count': st.get('hunt_count', 0),
            'battle_count': st.get('battle_count', 0),
            'owo_count': st.get('owo_count', 0),
            'total_cmd_count': st.get('total_cmd_count', 0),
            'other_count': st.get('other_count', 0),
            'gems_used': st.get('gems_used', 0),
            'username': st.get('username', 'Unknown'),
            'level': st.get('level'),
            'xp': st.get('xp'),
            'xp_needed': st.get('xp_needed'),
            'rank': st.get('rank'),
            # without these the dashboard forgot on every restart *why* the level was
            # blank and which card to show, and silently fell back to "never checked"
            # for the six hours until the next `owo level`
            'level_source': st.get('level_source'),
            'level_card_url': st.get('level_card_url'),
            'last_level_update': st.get('last_level_update'),
            'quest_data': st.get('quest_data', []),
            'quest_source': st.get('quest_source'),
            'quest_card_url': st.get('quest_card_url'),
            'quest_seals': st.get('quest_seals'),
            'next_quest_timer': st.get('next_quest_timer'),
            'next_quest_at': st.get('next_quest_at'),
            'current_cash': st.get('current_cash', 0),
            # an earning run is meant to be read over days; without this the tab
            # reset to zero on every restart and "per hour" became meaningless
            'earning': st.get('earning') or empty_earning(),
            # the daily gift allowance is only meaningful if it survives a restart
            'owner_send': st.get('owner_send') or empty_owner_send(),
            'gambling_stats': st.get('gambling_stats', {
                'total_wins': 0, 'total_losses': 0, 'total_wagered': 0,
                'net_profit': 0, 'current_streak': 0, 'best_streak': 0,
                'worst_streak': 0, 'biggest_win': 0, 'last_outcome': None
            })
        }

    # STATS_FILE lives under DATA_DIR (the volume), not a relative ./config.
    # Written to a sibling and renamed: a plain open('w') truncates the real file
    # first, so a crash (or a Railway redeploy) landing in that window left an
    # empty stats.json and every account's totals started again from zero.
    os.makedirs(os.path.dirname(STATS_FILE) or '.', exist_ok=True)
    tmp = STATS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(serializable_stats, f, indent=4)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATS_FILE)


# stats.json is rewritten in full every time, and log_command calls this on every
# SUCCESS / ALARM / SECURITY line - from the asyncio loop. On a busy farm that is
# a multi-kilobyte fsync several times a second on the loop the dashboard waits
# on, which is how Start and Stop ended up timing out. Coalesce them.
STATS_SAVE_INTERVAL = 3.0
_stats_last_write = 0.0
_stats_timer = None
_stats_lock = threading.Lock()


def save_account_stats(force=False):
    """Persist account stats, at most once every STATS_SAVE_INTERVAL seconds.

    force=True writes now and is what shutdown uses - a debounced write that
    never fires because the process exited first would lose the whole session.
    """
    global _stats_last_write, _stats_timer
    with _stats_lock:
        if _stats_timer is not None:
            _stats_timer.cancel()
            _stats_timer = None
        due = time.time() - _stats_last_write
        if not force and due < STATS_SAVE_INTERVAL:
            _stats_timer = threading.Timer(STATS_SAVE_INTERVAL - due,
                                           lambda: save_account_stats(force=True))
            _stats_timer.daemon = True
            _stats_timer.start()
            return
        _stats_last_write = time.time()
    try:
        _write_account_stats()
    except Exception as e:
        print(f"Error saving stats: {e}")

def load_account_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                saved = json.load(f)
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                for uid, st in saved.items():
                    new_st = get_empty_stats()
                    last_date = st.get('last_reset_date')
                    if last_date != today:
                        st['hunt_count'] = 0
                        st['battle_count'] = 0
                        st['owo_count'] = 0
                        st['total_cmd_count'] = 0
                        st['other_count'] = 0
                        st['gems_used'] = 0
                        st['captchas_solved_today'] = 0
                        st['last_reset_date'] = today
  
                    new_st.update(st)
                    new_st['session_hunt_count'] = 0
                    new_st['session_battle_count'] = 0
                    new_st['session_owo_count'] = 0
                    
                    account_stats[uid] = new_st
        except Exception as e:
            print(f"Error loading stats: {e}")

command_logs = deque(maxlen=1000)
full_session_history = []

# _raw_send appends " (1.2s)" to the log line when stealth typing timed the send,
# so the raw text is not a clean command string
_TYPING_SUFFIX_RE = re.compile(r'\s*\(\d+(?:\.\d+)?s\)\s*$')


def _sender_prefix(bot_id):
    """The owo prefix the account that emitted this line is configured with."""
    for bot in bot_instances:
        if str(getattr(bot, 'user_id', '')) == str(bot_id):
            return str(getattr(bot, 'prefix', 'owo ') or 'owo ').lower()
    return 'owo '


def log_command(type, message, status="info", bot_name=None, bot_id=None, owner=None):
    hex_color = log_config.get("colors", {}).get(type, "#ffffff")

    if "Sent: owo " in message:
        split_msg = message.split("Sent: owo ")
        if len(split_msg) > 1:
            cmd_part = split_msg[1].split(" ")[0].lower()
            if cmd_part in log_config.get("commands", {}):
                hex_color = log_config["commands"][cmd_part]
    elif "RPP: owo " in message:
        hex_color = log_config.get("commands", {}).get("rpp", "#00ffff")

    entry = {
        "time": time.strftime("%I:%M:%S %p"),
        "timestamp": time.time(),
        "type": type,
        "message": message,
        "status": status,
        "color": hex_color,
        "bot_name": bot_name,
        "bot_id": bot_id,
        # which space may see this line. Bot-attributed entries are resolved from
        # bot_id; the dashboard passes owner explicitly for its own SYS lines.
        "owner": owner or (owner_of(bot_id) if bot_id else None),
    }
    
    command_logs.appendleft(entry)
    if len(full_session_history) >= 500:
        full_session_history.pop(0)
    full_session_history.append(entry)
    
    if type in ["CMD", "SUCCESS", "ALARM", "SECURITY"] and bot_id and bot_id in account_stats:
        st = account_stats[bot_id]

        cmd = "other"
        if type == "CMD":
            parts = message.split("Sent: ")
            if len(parts) > 1:
                full_text = _TYPING_SUFFIX_RE.sub('', parts[1]).lower().strip()
                prefix = _sender_prefix(bot_id)
                bare = prefix.strip()
                # Not everything the bot sends is an owo command: level_grind posts
                # chat quotes and the captcha handler posts bare answers. Both used
                # to land in other_count/total_cmd_count and inflate the dashboard
                # totals. The guard this replaces looked for "level quote:" /
                # "level grind:", strings nothing has ever emitted.
                if full_text.startswith(prefix):
                    cmd_parts = full_text.split()
                    cmd_text = cmd_parts[1] if len(cmd_parts) > 1 else bare
                elif full_text == bare:
                    cmd_text = bare
                else:
                    return

                if cmd_text in ["hunt", "h"]:
                    cmd = "hunt"
                    st['hunt_count'] = st.get('hunt_count', 0) + 1
                    st['session_hunt_count'] = st.get('session_hunt_count', 0) + 1
                elif cmd_text in ["battle", "b"]:
                    cmd = "battle"
                    st['battle_count'] = st.get('battle_count', 0) + 1
                    st['session_battle_count'] = st.get('session_battle_count', 0) + 1
                elif cmd_text == bare:
                    cmd = "owo"
                    st['owo_count'] = st.get('owo_count', 0) + 1
                    st['session_owo_count'] = st.get('session_owo_count', 0) + 1
                elif "autohunt" in cmd_text:
                    # counts as a command like any other. It used to be mapped to
                    # cmd_type "captcha", which is the only thing that ever
                    # incremented the history db's captcha column - so the number
                    # the analytics page labelled "Captchas Solved" was really
                    # "times autohunt was re-armed", and a real solve was never
                    # recorded at all. Real solves go through track_captcha below.
                    cmd = "other"
                    st['other_count'] = st.get('other_count', 0) + 1
                else:
                    st['other_count'] = st.get('other_count', 0) + 1

                st['total_cmd_count'] = st.get('total_cmd_count', 0) + 1

        msg_low = message.lower()
        if type == "SUCCESS":
            if any(k in msg_low for k in ["captcha solved", "verified", "resuming"]):
                st['captchas_solved'] = st.get('captchas_solved', 0) + 1
                st['captcha_success_count'] = st.get('captcha_success_count', 0) + 1
                st['captchas_solved_today'] = st.get('captchas_solved_today', 0) + 1
                ht.track_captcha(owner=owner_of(bot_id), account_id=bot_id,
                                 account_name=st.get('username') or bot_name)

        elif type in ["ALARM", "SECURITY"]:
            if "ban detected" in msg_low:
                st['bans_detected'] = st.get('bans_detected', 0) + 1
            elif any(k in msg_low for k in ["captcha warning", "captcha detected", "captcha identified", "image captcha"]):
                st['warnings_detected'] = st.get('warnings_detected', 0) + 1
        
        if type in ["SUCCESS", "ALARM", "SECURITY"]:
            save_account_stats()

        if type == "CMD":
            # per-account, so the analytics page can finally break the numbers
            # down instead of summing every account in the space into one row
            ht.track_command(None, cmd, owner=owner_of(bot_id), account_id=bot_id,
                             account_name=st.get('username') or bot_name)

def record_snapshot(user_id):
    if user_id not in account_stats: return
    st = account_stats[user_id]

    if st.get('current_cash') is None: return
    now = time.time()
    if not st.get('start_cash'):
        st['start_cash'] = st['current_cash']
    st.setdefault('cowoncy_history', []).append((now, st['current_cash']))

    ht.track_cash(None, st['current_cash'], owner=owner_of(user_id), account_id=user_id)

    if len(st['cowoncy_history']) > 100:
        st['cowoncy_history'].pop(0)