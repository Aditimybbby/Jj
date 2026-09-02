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

"""The cowoncy ledger behind the Earning tab.

How it counts
-------------
It is a *cash-flow* ledger. Every figure comes from a change in
``stats['current_cash']`` - the number OwO itself reports - and an OwO reply or a
command we sent only decides which bucket a change lands in. Nothing is ever
credited from a parsed reward figure alone.

That is a deliberate choice. Summing "you sold N animals for M cowoncy" lines is
easy and wrong: OwO rewords its replies, a reply can be missed while the gateway
reconnects, and a spend nobody parsed then shows up as pure profit. Here the
buckets always reconcile - ``gained - spent`` equals ``current_cash - start_cash``
by construction - so the worst a missed reply can do is file cowoncy under
"other", never invent it.

`owo hunt` is free in OwO, so "spent on hunt" is not the hunt command: it is the
upkeep a hunting account actually pays (huntbot dispatches, shop and lootbox
buys). Both are reported separately so the tab can show where the money went.
"""

import asyncio
import re
import time

from discord.ext import commands

from core import state

# What OwO charges cowoncy for, mapped to the bucket it belongs in. Matched against
# the command *as sent*, after shortform substitution - so `hb` as well as `huntbot`.
AUTOHUNT_CMDS = ("huntbot", "hb", "autohunt")
UPKEEP_CMDS = ("shop", "buy", "lootbox", "lb", "crate", "wc", "sell")
HUNT_CMDS = ("hunt", "h")

# A sell reply, only to label the next cash rise and show a "last sale" line - the
# figure it captures is never added to the ledger. Written loosely on purpose.
SELL_RE = re.compile(r"sold[^\n]*?\*{0,2}([\d][\d,]*)\*{0,2}\s*(?:<a?:[^>]+>\s*)?cowoncy",
                     re.IGNORECASE)

# How long an armed command keeps claiming the next cash change. Long enough for a
# 15-minute cash_sync to be the only sample in between, short enough that a
# dispatch two hours ago cannot absorb a shop purchase now.
ARM_TTL_S = 20 * 60
# a nudge is one extra `owo cash`; at most one per this window whatever happens
NUDGE_GAP_S = 45


class Earning(commands.Cog):
    """Keeps ``stats['earning']`` in step with the account's real cowoncy."""

    def __init__(self, bot):
        self.bot = bot
        self.task: asyncio.Task | None = None
        # (bucket, amount_or_None, armed_at) - the most recent arming wins a change
        self._armed: list[tuple[str, int | None, float]] = []
        self._last_nudge = 0.0
        self._announced = None

    async def cog_load(self):
        self.task = asyncio.create_task(self.main_loop())

    async def cog_unload(self):
        if self.task:
            self.task.cancel()

    # ── config ────────────────────────────────────────────────────────────────

    def _cfg(self):
        cfg = self.bot.config.get('earning')
        return cfg if isinstance(cfg, dict) else {}

    def enabled(self):
        return bool(self._cfg().get('enabled', False))

    async def register_actions(self):
        """Announce the mode when it flips. Owns no scheduler slot of its own.

        Hunting, the huntbot and the periodic sell are all real commands owned by
        their own cogs; earning mode only rewrites their gates (see
        ``NeuraBot._apply_earning_overlay``). This cog just keeps the books, so
        there is nothing to register - but it is called on every ready and on every
        relevant config change, which is exactly when the ledger should be opened.
        """
        on = self.enabled()
        if on:
            self._ledger(open_if_missing=True)
        if on != self._announced:
            self._announced = on
            if on:
                cfg = self._cfg()
                extra = "exclusive" if cfg.get('exclusive', True) else "alongside everything else"
                self.bot.log("SYS", f"Earning mode ON ({extra}): hunt + huntbot "
                                    f"{cfg.get('huntbot_cash', 3000)} + sell "
                                    f"{cfg.get('sell_type', 'all')} every "
                                    f"{cfg.get('sell_interval_min', 20)}m.")
            else:
                self.bot.log("SYS", "Earning mode OFF - command gates back to your config.")

    # ── the ledger ────────────────────────────────────────────────────────────

    def _stats(self):
        uid = getattr(self.bot, 'user_id', None)
        if not uid and getattr(self.bot, 'user', None):
            uid = str(self.bot.user.id)
        if not uid:
            return None
        return state.account_stats.get(str(uid))

    def _ledger(self, open_if_missing=False):
        """The account's ledger dict, back-filled with any key a new version added."""
        stats = self._stats()
        if stats is None:
            return None
        led = stats.get('earning')
        if not isinstance(led, dict):
            led = state.empty_earning()
            stats['earning'] = led
        else:
            for key, value in state.empty_earning().items():
                led.setdefault(key, value)
        if open_if_missing and not led.get('started_at'):
            led['started_at'] = time.time()
            # start_cash is left None until the first reading: stamping today's 0 as
            # the opening balance would report the whole account as profit
            led['start_cash'] = None
        return led

    def reset(self):
        """Start a fresh run. Called by the dashboard's reset button."""
        stats = self._stats()
        if stats is None:
            return False
        led = state.empty_earning()
        led['started_at'] = time.time()
        cash = stats.get('current_cash')
        led['start_cash'] = cash if isinstance(cash, (int, float)) else None
        led['last_cash'] = led['start_cash']
        led['last_cash_at'] = time.time() if led['start_cash'] is not None else None
        stats['earning'] = led
        self._armed.clear()
        self.bot.log("SYS", "Earning ledger reset.")
        return True

    def _note(self, led, text):
        led['last_event'] = text
        led['last_event_at'] = time.time()

    # ── arming: what a cash change gets blamed on ─────────────────────────────

    def _arm(self, bucket, amount=None):
        now = time.time()
        self._armed = [a for a in self._armed if now - a[2] < ARM_TTL_S]
        self._armed.append((bucket, amount, now))
        # keep it short - only the newest few can plausibly explain a change
        del self._armed[:-6]

    def _claim(self, want_spend):
        """Pop the newest arming that can explain a change of this direction."""
        now = time.time()
        self._armed = [a for a in self._armed if now - a[2] < ARM_TTL_S]
        for index in range(len(self._armed) - 1, -1, -1):
            bucket, amount, _ = self._armed[index]
            is_spend = bucket != 'sell'
            if is_spend == want_spend:
                del self._armed[index]
                return bucket, amount
        return None, None

    def note_sent(self, content):
        """Called from ``NeuraBot._raw_send`` for every message that reached OwO.

        Sync and swallowing nothing on purpose - the caller wraps it - so the exact
        figure we handed the huntbot is recorded at the moment it goes out rather
        than re-derived from config that may have changed since.
        """
        if not self.enabled():
            return
        parts = str(content or '').lower().split()
        if not parts:
            return
        prefix = str(getattr(self.bot, 'prefix', 'owo ')).strip().lower()
        if parts[0] == prefix and len(parts) > 1:
            parts = parts[1:]
        elif prefix and parts[0].startswith(prefix):
            parts[0] = parts[0][len(prefix):]
        base = parts[0]
        if not base:
            return

        led = self._ledger(open_if_missing=True)
        if led is None:
            return

        if base in HUNT_CMDS:
            led['hunts'] = led.get('hunts', 0) + 1
            return
        if base in AUTOHUNT_CMDS:
            amount = None
            for token in parts[1:]:
                digits = token.replace(',', '')
                if digits.isdigit():
                    amount = int(digits)
                    break
            # a bare `owo huntbot` is a status check and costs nothing
            if amount:
                self._arm('autohunt', amount)
            return
        if base in UPKEEP_CMDS:
            self._arm('sell' if base == 'sell' else 'hunt')

    # ── OwO's side ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message):
        if not self.enabled():
            return
        monitor = str(self.bot.config.get('core', {}).get('monitor_bot_id',
                                                         '408785106942164992'))
        if str(message.author.id) != monitor:
            return
        if str(message.channel.id) not in [str(c) for c in self.bot.channels]:
            return
        if not self.bot.is_message_for_me(message):
            return

        content = (message.content or '').lower()
        if not content:
            return

        led = self._ledger(open_if_missing=True)
        if led is None:
            return

        match = SELL_RE.search(content)
        if match and 'sold' in content:
            try:
                amount = int(match.group(1).replace(',', ''))
            except (TypeError, ValueError):
                amount = None
            led['sold_count'] = led.get('sold_count', 0) + 1
            led['last_sell_amount'] = amount
            self._arm('sell', amount)
            self._note(led, f"sold animals for {amount:,}" if amount else "sold animals")
            await self._nudge_cash()
            return

        if 'i will be back in' in content or 'beep boop' in content:
            # the dispatch itself is only *labelled* here; the spend is booked when
            # the cash reading moves, so a status check that took nothing costs the
            # ledger nothing either
            if any(bucket == 'autohunt' for bucket, _, _ in self._armed):
                led['autohunt_runs'] = led.get('autohunt_runs', 0) + 1
                self._note(led, "huntbot dispatched")
                await self._nudge_cash()

    async def _nudge_cash(self):
        """Ask for a cash reading so the next sample brackets what just happened.

        Without this the only samples are ``cash_sync``'s 15-minute ticks, and a sell
        and a huntbot dispatch inside one tick net out into a single change that has
        to be filed under one bucket.
        """
        now = time.time()
        if now - self._last_nudge < NUDGE_GAP_S:
            return
        self._last_nudge = now
        try:
            await asyncio.sleep(6)
            # the prefix is spelled out: `cash` is not in _fix_command's `known` list,
            # so a bare "cash" would be posted as chat and never answered
            await self.bot.neura_enqueue(f"{self.bot.prefix}cash", priority=4)
        except Exception:
            pass

    # ── sampling ──────────────────────────────────────────────────────────────

    def _apply_cash(self, led, cash):
        previous = led.get('last_cash')
        led['last_cash'] = cash
        led['last_cash_at'] = time.time()
        if led.get('start_cash') is None:
            led['start_cash'] = cash
            self._note(led, f"opening balance {cash:,}")
            return
        if not isinstance(previous, (int, float)):
            return
        delta = int(cash) - int(previous)
        if delta == 0:
            return

        if delta > 0:
            bucket, _ = self._claim(want_spend=False)
            key = 'gained_sell' if bucket == 'sell' else 'gained_other'
            led[key] = led.get(key, 0) + delta
        else:
            bucket, _ = self._claim(want_spend=True)
            key = {'autohunt': 'spent_autohunt', 'hunt': 'spent_hunt'}.get(bucket, 'spent_other')
            led[key] = led.get(key, 0) + (-delta)

    async def main_loop(self):
        while True:
            try:
                if not self.enabled():
                    await asyncio.sleep(30)
                    continue
                led = self._ledger(open_if_missing=True)
                stats = self._stats()
                if led is not None and stats is not None:
                    cash = stats.get('current_cash')
                    stamp = stats.get('last_cash_update')
                    # act on a *reading*, not on the number: two readings of the same
                    # balance are not a change, and a fresh reading of the same number
                    # still proves nothing moved
                    if isinstance(cash, (int, float)) and stamp:
                        if stamp != led.get('_stamp'):
                            led['_stamp'] = stamp
                            self._apply_cash(led, int(cash))
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.bot.log("ERROR", f"Earning ledger error: {e}")
                await asyncio.sleep(30)


async def setup(bot):
    await bot.add_cog(Earning(bot))
