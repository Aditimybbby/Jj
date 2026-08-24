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

class NeuraLogs:
    def __init__(self):
        self.console = Console()
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

        import core.state as state
        bot_id = str(bot.user.id) if (hasattr(bot, '_connection') and bot.user) else (getattr(bot, 'user_id', None))
        # pass the space explicitly: before the first READY there is no bot_id for
        # log_command to resolve an owner from, so "Starting bot...", "Login failed"
        # and every other startup line would only ever reach the admin's log view
        state.log_command(log_type, message, "info", bot_name=username, bot_id=bot_id,
                          owner=getattr(bot, 'space_owner', None))

neura_logger = NeuraLogs()
