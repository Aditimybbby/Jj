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
        'last_level_update': None,
        'quest_data': [],
        'next_quest_timer': None,
        'session_hunt_count': 0,
        'session_battle_count': 0,
        'session_owo_count': 0,
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

def save_account_stats():
    try:
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
                'last_level_update': st.get('last_level_update'),
                'quest_data': st.get('quest_data', []),
                'next_quest_timer': st.get('next_quest_timer'),
                'current_cash': st.get('current_cash', 0),
                'gambling_stats': st.get('gambling_stats', {
                    'total_wins': 0, 'total_losses': 0, 'total_wagered': 0,
                    'net_profit': 0, 'current_streak': 0, 'best_streak': 0,
                    'worst_streak': 0, 'biggest_win': 0, 'last_outcome': None
                })
            }
        
        # STATS_FILE lives under DATA_DIR (the volume), not a relative ./config
        os.makedirs(os.path.dirname(STATS_FILE) or '.', exist_ok=True)
        with open(STATS_FILE, 'w') as f:
            json.dump(serializable_stats, f, indent=4)
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
                    cmd = "captcha"
                else:
                    st['other_count'] = st.get('other_count', 0) + 1

                st['total_cmd_count'] = st.get('total_cmd_count', 0) + 1
   
        msg_low = message.lower()
        if type == "SUCCESS":
            if any(k in msg_low for k in ["captcha solved", "verified", "resuming"]):
                st['captchas_solved'] = st.get('captchas_solved', 0) + 1
                st['captcha_success_count'] = st.get('captcha_success_count', 0) + 1
                st['captchas_solved_today'] = st.get('captchas_solved_today', 0) + 1
            
        elif type in ["ALARM", "SECURITY"]:
            if "ban detected" in msg_low:
                st['bans_detected'] = st.get('bans_detected', 0) + 1
            elif any(k in msg_low for k in ["captcha warning", "captcha detected", "captcha identified", "image captcha"]):
                st['warnings_detected'] = st.get('warnings_detected', 0) + 1
        
        if type in ["SUCCESS", "ALARM", "SECURITY"]:
            save_account_stats()
        
        if type == "CMD":
            history = ht.load_history()
            ht.track_command(history, cmd, owner=owner_of(bot_id))

def record_snapshot(user_id):
    if user_id not in account_stats: return
    st = account_stats[user_id]

    if st.get('current_cash') is None: return
    now = time.time()
    if not st.get('start_cash'):
        st['start_cash'] = st['current_cash']
    st.setdefault('cowoncy_history', []).append((now, st['current_cash']))
    
    history = ht.load_history()
    ht.track_cash(history, st['current_cash'], owner=owner_of(user_id))
    
    if len(st['cowoncy_history']) > 100:
        st['cowoncy_history'].pop(0)