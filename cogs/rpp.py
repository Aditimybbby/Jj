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


import asyncio
import time
import random
from discord.ext import commands

from utils import daily_ledger

# OwO grants run / pup / piku once each per day, and its refusals are worded
# differently for each. Any of these phrases in a reply of ours means the command
# it belongs to is spent until the daily reset.
_EXHAUSTED_PHRASES = {
    "run": ("too tired to run", "too tired", "already ran", "ran away too many"),
    "piku": ("garden is out of carrots", "out of carrots", "no carrots", "already fed"),
    "pup": ("no puppies", "no more puppies", "out of puppies", "already played"),
}

# a refusal that names no command. Attributed to whichever one we just sent.
_GENERIC_REFUSALS = ("come back tomorrow", "try again tomorrow", "come back later",
                     "too many times", "already used")


class RPP(commands.Cog):
    """The daily run / pup / piku commands.

    Exhaustion used to live in a plain dict on the cog, so every restart said
    "all three are available" and the farm walked back into three refusals -
    then kept trying on the 60 second timer for the rest of the day, because a
    refusal that did not carry the account's name never reached the matcher
    below. It is a ledger on disk now (utils/daily_ledger.py), and core/bot.py
    locks the key the moment the command is *sent*, so a reply nobody could
    parse costs one command instead of a thousand.
    """

    def __init__(self, bot):
        self.bot = bot
        self.active = True
        self.task = None
        self.last_run = 0
        # what went out most recently, so an unattributed refusal can be pinned on it
        self.pending = None
        self.pending_at = 0.0

    def _available(self):
        cfg = self.bot.config.get('commands', {}).get('rpp', {})
        wanted = cfg.get('active_commands', ["run", "pup", "piku"])
        if not isinstance(wanted, (list, tuple)):
            wanted = [str(wanted)]
        out = []
        for name in wanted:
            key = daily_ledger.RPP_KEYS.get(str(name).strip().lower())
            if key and not daily_ledger.is_locked(self.bot.user_id, key):
                out.append(str(name).strip().lower())
        return out

    def trigger_action(self):
        """Both the scheduler's content hook and its post-send callback.

        Returning None tells the scheduler there is nothing to send, which is the
        right answer once all three are spent - it re-arms the timer without
        putting anything on the wire.
        """
        cfg = self.bot.config.get('commands', {}).get('rpp', {})
        try:
            interval = max(45.0, float(cfg.get('interval_s', 90)))
        except (TypeError, ValueError):
            interval = 90.0

        if 'rpp' in self.bot.cmd_states:
            self.bot.cmd_states['rpp']['delay'] = interval

        available = self._available()
        if not available:
            # nothing is left today: idle until the reset rather than re-testing
            # three locked keys every 90 seconds
            if 'rpp' in self.bot.cmd_states:
                self.bot.cmd_states['rpp']['delay'] = max(interval, 900.0)
            return None

        cmd = random.choice(available)
        self.pending = cmd
        self.pending_at = time.time()
        return f"owo {cmd}"

    async def register_actions(self):
        cfg = self.bot.config.get('commands', {}).get('rpp', {})
        if cfg.get('enabled', False):
            self.bot.log("SYS", "RPP Module configured.")
            try:
                interval = max(45.0, float(cfg.get('interval_s', 90)))
            except (TypeError, ValueError):
                interval = 90.0

            def rpp_dispatch():
                return self.trigger_action()

            await self.bot.neura_register_command("rpp", rpp_dispatch, priority=self.bot.get_cmd_priority("rpp", 3), delay=interval, initial_offset=15)

    def _mark_spent(self, cmd, why):
        key = daily_ledger.RPP_KEYS.get(cmd)
        if not key:
            return
        until = daily_ledger.lock(self.bot.user_id, key)
        hours = max(0.0, (until - time.time()) / 3600.0)
        self.bot.log("COOLDOWN", f"RPP: {cmd} exhausted ({why}). Next available in {round(hours, 1)}h")
        if cmd == self.pending:
            self.pending = None

    @commands.Cog.listener()
    async def on_message(self, message):
        monitor_id = str(self.bot.config.get('core', {}).get('monitor_bot_id', '408785106942164992'))
        if str(message.author.id) != monitor_id:
            return
        if self.bot.owo_user is None:
            self.bot.owo_user = message.author
        if str(message.channel.id) not in [str(c) for c in self.bot.channels]:
            return

        full_text = self.bot.get_full_content(message)
        if not full_text:
            return

        # A refusal has to be ours before a command is marked spent. The name check
        # is the reliable half; the other half is "we sent one of these seconds ago
        # and OwO answered", which is what carries the cases the matcher misses -
        # a nickname it does not know, or a reply with no name in it at all.
        recent = self.pending and (time.time() - self.pending_at) < 30.0
        if not recent and not self.bot.is_message_for_me(message):
            return

        for cmd, phrases in _EXHAUSTED_PHRASES.items():
            if any(phrase in full_text for phrase in phrases):
                self._mark_spent(cmd, "OwO refused it")
                return

        if not recent:
            return

        # OwO throttled us instead of answering: the command was never actually
        # spent, so release the lock core/bot.py took when it went out
        if "slow down" in full_text or "try the command again" in full_text:
            key = daily_ledger.RPP_KEYS.get(self.pending)
            if key and daily_ledger.clear(self.bot.user_id, key):
                self.bot.log("COOLDOWN", f"RPP: {self.pending} was throttled, not spent - retrying later")
            return

        # a refusal naming no command can only be answering the one we just sent -
        # unless it is plainly about something else that also refuses "tomorrow"
        if any(word in full_text for word in ("daily", "cookie", "hunt", "battle", "quest", "vote")):
            return
        if any(phrase in full_text for phrase in _GENERIC_REFUSALS):
            named = [c for c in daily_ledger.RPP_KEYS if c in full_text]
            self._mark_spent(named[0] if named else self.pending, "spent for today")


async def setup(bot):
    cog = RPP(bot)
    await bot.add_cog(cog)
