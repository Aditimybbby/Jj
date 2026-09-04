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


from discord.ext import commands
import json
import os
import asyncio
import time
import random
import re

import core.state as state
from utils import daily_ledger

# OwO refuses a second daily with any of these, and only some of them say "wait"
_REFUSALS = ("wait", "already claimed", "already got", "come back", "next daily", "tomorrow")


class Daily(commands.Cog):
    """`owo daily`, once per UTC day.

    The claim is recorded in utils/daily_ledger.py the moment the command is
    sent (core/bot.py does it), so a reply this cog cannot parse - a components
    v2 card, a nickname the identity matcher has never seen - costs one command
    instead of one every ten seconds until midnight. stats_daily.json is still
    read once, so an upgrade does not hand every account a free re-claim.
    """

    def __init__(self, bot):
        self.bot = bot
        self.active = True
        self.cooldown = 86400
        # DATA_DIR is the volume; a relative 'data/' path is lost on every redeploy
        self.stats_file = os.path.join(state.DATA_DIR, 'stats_daily.json')
        self.last_daily_sent = 0
        self._adopt_legacy_state()

    def _adopt_legacy_state(self):
        if daily_ledger.is_locked(self.bot.user_id, daily_ledger.DAILY):
            return
        try:
            with open(self.stats_file, 'r', encoding='utf-8') as f:
                last_run = (json.load(f) or {}).get(str(self.bot.user_id), 0)
        except (OSError, ValueError, AttributeError):
            return
        daily_ledger.adopt_legacy(self.bot.user_id, daily_ledger.DAILY, last_run)

    @property
    def last_run(self):
        until = daily_ledger.locked_until(self.bot.user_id, daily_ledger.DAILY)
        return max(0.0, until - 86400.0) if until else 0.0

    def trigger_action(self):
        """Post-send bookkeeping. The ledger lock is already in place."""
        self.bot.log("INFO", "Sending daily command...")
        self.last_daily_sent = time.time()
        self.cooldown = 86400
        if 'daily' in self.bot.cmd_states:
            self.bot.cmd_states['daily']['delay'] = 86400

    async def register_actions(self):
        cfg = self.bot.config.get('commands', {}).get('daily', {})
        if not cfg.get('enabled', False):
            return
        remaining = daily_ledger.remaining(self.bot.user_id, daily_ledger.DAILY)
        # a fresh account claims shortly after warmup rather than instantly: the
        # first seconds after login are already busy enough without adding to them
        delay = remaining if remaining > 0 else 45
        await self.bot.neura_register_command("daily", "daily", priority=self.bot.get_cmd_priority("daily", 4), delay=max(10, delay), initial_offset=0)

    @commands.Cog.listener()
    async def on_message(self, message):
        await self._process_response(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        await self._process_response(after)

    async def _process_response(self, message):
        core_config = self.bot.config.get('core', {})
        monitor_id = str(core_config.get('monitor_bot_id', '408785106942164992'))
        if str(message.author.id) != monitor_id: return
        if self.bot.owo_user is None:
            self.bot.owo_user = message.author

        all_channels = [str(c) for c in self.bot.channels]

        if str(message.channel.id) not in all_channels:
            return

        full_content = self.bot.get_full_content(message)
        if not full_content:
            return

        recent = (time.time() - self.last_daily_sent) < 25.0
        if not recent and not self.bot.is_message_for_me(message):
            return
        # the reply has to be about the daily before its numbers are read as a
        # daily cooldown - plenty of OwO lines carry an "Nm Ns"
        if "daily" not in full_content:
            return
        if not any(word in full_content for word in _REFUSALS):
            return

        h_match = re.search(r'(\d+)\s*[hH]', full_content)
        m_match = re.search(r'(\d+)\s*[mM]', full_content)
        s_match = re.search(r'(\d+)\s*[sS]', full_content)

        h = int(h_match.group(1)) if h_match else 0
        m = int(m_match.group(1)) if m_match else 0
        s = int(s_match.group(1)) if s_match else 0
        total_seconds = (h * 3600) + (m * 60) + s

        if total_seconds > 0:
            self.cooldown = min(172800, total_seconds) + random.randint(10, 30)
            self.bot.log("COOLDOWN", f"Daily wait synced: {h}h {m}m {s}s remaining.")
        else:
            # refused with no figure: the next UTC reset is when it can work again
            self.cooldown = max(60.0, daily_ledger.next_daily_reset() - time.time())
            self.bot.log("COOLDOWN", "Daily already claimed - waiting for the next reset.")

        # force: OwO's own figure outranks the lock taken when the command was sent
        daily_ledger.lock(self.bot.user_id, daily_ledger.DAILY, seconds=self.cooldown, force=True)
        if 'daily' in self.bot.cmd_states:
            self.bot.cmd_states['daily']['delay'] = self.cooldown
            self.bot.cmd_states['daily']['last_ran'] = time.time()
        self.last_daily_sent = 0


async def setup(bot):
    cog = Daily(bot)
    await bot.add_cog(cog)
