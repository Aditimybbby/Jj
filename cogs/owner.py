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
"""


import re
import time
import core.state as state
from discord.ext import commands

MAX_GIVE = 100000


class Owner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cash_requested_at = 0
        self._transfer_channel_id = None

    def _config(self):
        return self.bot.config.get('owner', {})

    def _owner_id(self):
        cfg = self._config()
        if not cfg.get('enabled', False):
            return None
        owner_id = str(cfg.get('user_id', '')).strip()
        return owner_id if owner_id.isdigit() else None

    @commands.Cog.listener()
    async def on_message(self, message):
        owner_id = self._owner_id()
        if not owner_id:
            return
        if str(message.channel.id) not in [str(c) for c in self.bot.channels]:
            return

        if str(message.author.id) == self.bot.owo_bot_id:
            await self._handle_cash_reply(message, owner_id)
        elif str(message.author.id) == owner_id:
            await self._handle_trigger(message, owner_id)

    def _known_account_names(self):
        return {str(a.get('name', '')).lower() for a in getattr(self.bot, 'accounts', []) if a.get('name')}

    def _selector_targets_me(self, token):
        """'farmers acc2 bal' / 'farmers <user id> bal' - only that account reacts."""
        token = token.lower()
        if token.isdigit():
            return token == str(self.bot.user.id)
        return token == str(getattr(self.bot, 'account_name', '')).lower()

    def _is_selector(self, token):
        return token.isdigit() or token.lower() in self._known_account_names()

    async def _handle_trigger(self, message, owner_id):
        if str(self.bot.user.id) == owner_id:
            return

        trigger = str(self._config().get('trigger', 'farmers')).lower().strip()
        raw = (message.content or "").strip()
        if not trigger or not raw.lower().startswith(trigger):
            return

        action = raw[len(trigger):].strip()
        if not action:
            return

        parts = action.split(None, 1)
        if len(parts) == 2 and self._is_selector(parts[0]):
            if not self._selector_targets_me(parts[0]):
                return
            action = parts[1].strip()
            if not action:
                return

        lowered = action.lower()

        if lowered.startswith('pay'):
            await self.bot.neura_enqueue(
                f"owo pray <@{owner_id}>",
                priority=2,
                target_channel_id=message.channel.id
            )
            self.bot.log("SYS", "Owner command 'pay': praying for the owner.")
        elif lowered.startswith('showbal') or lowered.startswith('bal'):
            await self.bot.neura_enqueue("owo cash", priority=2, target_channel_id=message.channel.id)
            self.bot.log("SYS", "Owner command 'showbal': posting balance.")
        elif lowered.startswith('send'):
            self._cash_requested_at = time.time()
            self._transfer_channel_id = message.channel.id
            await self.bot.neura_enqueue("owo cash", priority=2, target_channel_id=message.channel.id)
            self.bot.log("SYS", "Owner command 'send': checking balance before transferring.")
        else:
            # anything else is forwarded as-is, so "farmers team add bee2" runs "owo team add bee2"
            prefix = self.bot.prefix.strip().lower()
            command = action[len(prefix):].strip() if lowered.startswith(prefix + ' ') else action
            if not command:
                return
            await self.bot.neura_enqueue(
                f"{self.bot.prefix}{command}",
                priority=2,
                target_channel_id=message.channel.id
            )
            self.bot.log("SYS", f"Owner command: running '{self.bot.prefix}{command}'")

    async def _handle_cash_reply(self, message, owner_id):
        if not self._cash_requested_at:
            return
        if time.time() - self._cash_requested_at > 120:
            self._cash_requested_at = 0
            return
        if not self.bot.is_message_for_me(message):
            return

        match = re.search(r'you currently have[^\d]*([\d,]+)', self.bot.get_full_content(message))
        if not match:
            return

        self._cash_requested_at = 0
        balance = int(match.group(1).replace(',', ''))

        st = state.account_stats.get(self.bot.user_id, {})
        st['current_cash'] = balance
        st['last_cash_update'] = time.time()

        if balance < 1:
            self.bot.log("INFO", "Owner command 'send': no cowoncy to transfer.")
            return

        amount = min(balance, MAX_GIVE)
        await self.bot.neura_enqueue(
            f"owo give <@{owner_id}> {amount}",
            priority=2,
            target_channel_id=self._transfer_channel_id
        )
        if balance > amount:
            self.bot.log("INFO", f"Owner command 'send': sending {amount} cowoncy (owo per-transfer cap), {balance - amount} left over.")
        else:
            self.bot.log("SUCCESS", f"Owner command 'send': sending {amount} cowoncy to the owner.")


    async def register_actions(self):
        cfg = self._config()
        owner_id = self._owner_id()
        trigger = str(cfg.get('trigger', 'farmers')).lower().strip()
        if owner_id:
            self.bot.log("SYS", f"Owner commands active for {owner_id} - '{trigger} pay | {trigger} send | {trigger} showbal | {trigger} <any owo command>'")
        elif cfg.get('enabled', False):
            self.bot.log("WARN", f"Owner commands enabled but owner.user_id is not a Discord ID: {cfg.get('user_id')!r}")


async def setup(bot):
    cog = Owner(bot)
    await bot.add_cog(cog)