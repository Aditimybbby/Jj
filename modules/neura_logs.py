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


import time
import json
import os
from rich.console import Console

def _plain_logs_wanted():
    """Colour costs ~557us a line and holds rich's console lock.

    That is invisible for one account and 4.5s of CPU per minute at 500, all of it
    serialising every bot behind one lock, for markup a hosting provider's log
    viewer renders as escape codes anyway. So: plain lines when running on a host,
    colour when a human is watching a terminal. LAZYFARMERS_PLAIN_LOGS overrides.
    """
    override = os.environ.get('LAZYFARMERS_PLAIN_LOGS', '').strip().lower()
    if override in ('1', 'true', 'yes'):
        return True
    if override in ('0', 'false', 'no'):
        return False
    return any(key.startswith('RAILWAY_') for key in os.environ)


def _console_levels():
    """Which log types are allowed onto the host console.

    On a host (Railway) the per-account farming chatter - every hunt, gamble,
    cooldown and stealth-typing line - is just noise in the provider's log
    viewer, and the dashboard already keeps all of it. So the host console is
    held to problems and security events; everything else is dropped from stdout
    but still recorded for the website. LAZYFARMERS_CONSOLE_LEVELS overrides with
    a comma list of types, or the word 'all' to restore the full firehose. Only
    applies in plain/host mode - a human watching a real terminal still sees
    every line in colour.
    """
    raw = os.environ.get('LAZYFARMERS_CONSOLE_LEVELS', '').strip()
    if raw:
        if raw.lower() == 'all':
            return None
        return {part.strip().upper() for part in raw.split(',') if part.strip()}
    return {'ERROR', 'WARN', 'SECURITY', 'ALARM'}


class NeuraLogs:
    def __init__(self):
        self.console = Console()
        self.plain = _plain_logs_wanted()
        self.console_levels = _console_levels()
        self.log_config = {}
        self.last_logs = {}
        self._load_config()

    def _load_config(self):
        try:
            # logmisc.json lives on the volume, not in the (ephemeral) repo copy
            from core.paths import CONFIG_DIR
            config_path = os.path.join(CONFIG_DIR, 'logmisc.json')
            if not os.path.exists(config_path):
                config_path = os.path.join(os.getcwd(), 'config', 'logmisc.json')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.log_config = json.load(f)

                    import core.state as state
                    state.log_config = self.log_config
        except Exception:
            pass

    def log(self, bot, log_type, message):
        now = time.time()
        bot_uid = bot.user.id if (hasattr(bot, '_connection') and bot.user) else (getattr(bot, 'user_id', 'initialization'))
        dedup_key = f"{bot_uid}:{log_type}:{message}"
        if now - self.last_logs.get(dedup_key, 0) < 1.0:
            return
        # the dedup map is keyed by the whole message, so it grows forever unless trimmed
        if len(self.last_logs) > 2000:
            cutoff = now - 60
            self.last_logs = {k: v for k, v in self.last_logs.items() if v > cutoff}
        self.last_logs[dedup_key] = now

        username = bot.username if hasattr(bot, 'username') else "Bot"

        # Host console stays clean: routine per-account chatter never reaches the
        # provider's log viewer, but the dashboard log view still gets every line
        # through _record. Errors, warnings and security events always pass.
        if self.plain and self.console_levels is not None and log_type not in self.console_levels:
            self._record(bot, log_type, message, username)
            return

        if self.plain:
            # one write, no markup parsing, no shared console lock
            print(f"[{username}] {time.strftime('%I:%M:%S %p')} [{log_type}]  {message}", flush=True)
            self._record(bot, log_type, message, username)
            return

        type_colors = self.log_config.get("colors", {})
        colors = {
            'SYS': 'cyan',
            'CMD': 'green',
            'INFO': 'blue',
            'SUCCESS': 'bright_green',
            'COOLDOWN': 'bright_yellow',
            'ALARM': 'bright_red',
            'ERROR': 'red',
            'SECURITY': 'red',
            'AutoHunt': 'bright_cyan',
            'STEALTH': 'yellow'
        }

        for k, v in type_colors.items():
            colors[k] = v.replace('#', '') 

        color = colors.get(log_type, "white")
        t = time.strftime("%I:%M:%S %p")
        
        username = bot.username if hasattr(bot, 'username') else "Bot"
        name_tag = f"[[magenta]{username}[/magenta]] "
        
        if log_type == "STEALTH":
            self.console.print(f"{name_tag}[dim]{t}[/dim] [[bold yellow]{log_type}[/bold yellow]]  {message}")
        else:
            if log_type in type_colors:
                rich_color = type_colors[log_type]
                self.console.print(f"\r{name_tag}[dim]{t}[/dim] [[bold {rich_color}]{log_type}[/bold {rich_color}]]  {message}")
            else:
                self.console.print(f"\r{name_tag}[dim]{t}[/dim] [[bold {color}]{log_type}[/bold {color}]]  {message}")

        self._record(bot, log_type, message, username)

    def _record(self, bot, log_type, message, username):
        """Feed the dashboard log view and the stats counters behind it."""
        import core.state as state
        bot_id = str(bot.user.id) if (hasattr(bot, '_connection') and bot.user) else (getattr(bot, 'user_id', None))
        # pass the space explicitly: before the first READY there is no bot_id for
        # log_command to resolve an owner from, so "Starting bot...", "Login failed"
        # and every other startup line would only ever reach the admin's log view
        state.log_command(log_type, message, "info", bot_name=username, bot_id=bot_id,
                          owner=getattr(bot, 'space_owner', None))


neura_logger = NeuraLogs()
