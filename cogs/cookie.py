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
import asyncio
import time
import json
import random
import os
import re

import core.state as state
from utils import daily_ledger

# "you can give another cookie in 4h 12m", "you already gave a cookie today"
_COOLDOWN_HINTS = ("wait", "already", "cooldown", "come back", "another cookie", "in a bit")


def _target_id(raw):
    """The user id in a configured cookie target, or None if it is not one.

    `commands.cookie.id` ships as the string `cookie-target-id` so the settings
    page has something to show. Registering that as a command meant sending
    `owo cookie cookie-target-id` once a day, every day, to be told it is not a
    user - so the target is checked here instead of being trusted.
    """
    text = str(raw or '').strip()
    if not text:
        return None
    match = re.fullmatch(r'<@!?(\d{5,25})>', text)
    if match:
        return match.group(1)
    return text if re.fullmatch(r'\d{5,25}', text) else None


class Cookie(commands.Cog):
    """`owo cookie <id>`, once a day.

    The 24 hour cooldown lives in utils/daily_ledger.py, which is shared,
    atomic and survives restarts. It used to live in stats_cookie.json, rewritten
    in place by every account with no lock: two accounts saving in the same
    moment truncated the file, every account then read "never sent", and the
    cookie went out again on a 10 second timer.
    """

    def __init__(self, bot):
        self.bot = bot
        self.active = True
        # DATA_DIR is the volume; a relative 'data/' path is lost on every redeploy
        self.stats_file = os.path.join(state.DATA_DIR, 'stats_cookie.json')
        self.last_cookie_sent = 0
        self._adopt_legacy_state()

    def _adopt_legacy_state(self):
        """Carry a pre-ledger stats_cookie.json timestamp over, once."""
        if daily_ledger.is_locked(self.bot.user_id, daily_ledger.COOKIE):
            return
        try:
            with open(self.stats_file, 'r', encoding='utf-8') as f:
                last_run = (json.load(f) or {}).get(str(self.bot.user_id), 0)
        except (OSError, ValueError, AttributeError):
            return
        daily_ledger.adopt_legacy(self.bot.user_id, daily_ledger.COOKIE, last_run)

    @property
    def last_run(self):
        """When the cookie was last spent, derived from the ledger."""
        until = daily_ledger.locked_until(self.bot.user_id, daily_ledger.COOKIE)
        return max(0.0, until - 86400.0) if until else 0.0

    def _remaining(self):
        return daily_ledger.remaining(self.bot.user_id, daily_ledger.COOKIE)

    def trigger_action(self):
        """Post-send: park the timer on the far side of the cooldown.

        core/bot.py has already locked the ledger for 24 hours by the time this
        runs, so this only keeps the scheduler from re-testing every second.
        """
        if 'cookie' not in self.bot.cmd_states:
            return
        cfg = self.bot.config.get('commands', {}).get('cookie', {})
        target = _target_id(cfg.get('id'))
        if not target:
            return
        self.bot.log("INFO", f"Sending cookie command to {target}...")
        self.bot.cmd_states['cookie']['content'] = f"cookie {target}"
        self.last_cookie_sent = time.time()
        self.bot.cmd_states['cookie']['delay'] = 86400

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

        # a reply seconds after our own cookie went out is ours whether or not the
        # matcher can find our name in it - OwO words this one several ways, and
        # some of them name nobody
        recent = (time.time() - self.last_cookie_sent) < 25.0
        if not recent and not self.bot.is_message_for_me(message):
            return

        if any(hint in full_content for hint in _COOLDOWN_HINTS) and "cookie" in full_content:
            # the reply has to be about cookies before its numbers are read as a
            # cookie cooldown: a battle line that happens to contain "5s" would
            # otherwise shorten the real cooldown and start the refusals again
            self._sync_cooldown(full_content)
            self.last_cookie_sent = 0
            return

        is_sender = self.bot.is_message_for_me(message, role="target", keyword="got a cookie from") or \
                    self.bot.is_message_for_me(message, role="source", keyword="sent a cookie to")

        if not (is_sender or recent): return

        if "sent a cookie to" in full_content or "got a cookie from" in full_content:
            daily_ledger.lock(self.bot.user_id, daily_ledger.COOKIE, seconds=86400)
            self.bot.log("SUCCESS", "Cookie successfully sent.")

    def _sync_cooldown(self, message):
        h_match = re.search(r'(\d+)\s*[hH]', message)
        m_match = re.search(r'(\d+)\s*[mM]', message)
        s_match = re.search(r'(\d+)\s*[sS]', message)

        h = int(h_match.group(1)) if h_match else 0
        m = int(m_match.group(1)) if m_match else 0
        s = int(s_match.group(1)) if s_match else 0
        total_seconds = (h * 3600) + (m * 60) + s

        if total_seconds <= 0:
            return
        wait = min(172800, total_seconds) + random.randint(10, 30)
        self.bot.log("COOLDOWN", f"Cookie cooldown synced: {h}h {m}m {s}s remaining.")
        # force: OwO's own figure is authoritative, so it may shorten the 24 hour
        # lock core/bot.py took when the command went out
        daily_ledger.lock(self.bot.user_id, daily_ledger.COOKIE, seconds=wait, force=True)
        if 'cookie' in self.bot.cmd_states:
            self.bot.cmd_states['cookie']['delay'] = wait
            self.bot.cmd_states['cookie']['last_ran'] = time.time()

    async def register_actions(self):
        cfg = self.bot.config.get('commands', {}).get('cookie', {})
        if not cfg.get('enabled', False):
            return
        target = _target_id(cfg.get('id'))
        if not target:
            raw = cfg.get('id')
            if raw:
                self.bot.log("WARN", f"Cookie target {raw!r} is not a user id - set "
                                     f"commands.cookie.id to a discord id or turn the module off")
            return

        remaining = self._remaining()
        if remaining > 0:
            h = int(remaining // 3600)
            m = int((remaining % 3600) // 60)
            self.bot.log("INFO", f"Cookie on cooldown: {h}h {m}m remaining.")
            delay = remaining
        else:
            delay = 30

        cmd = f"cookie {target}"
        await self.bot.neura_register_command("cookie", cmd, priority=self.bot.get_cmd_priority("cookie", 4), delay=max(10, delay), initial_offset=0)


async def setup(bot):
    cog = Cookie(bot)
    await bot.add_cog(cog)
