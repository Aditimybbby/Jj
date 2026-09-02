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
import json
import random
import re
import time
import core.state as state
from discord.ext import commands
from component_v2_neura.parser import parse_v2_message, collect_text, buttons

MAX_GIVE = 100000
DEFAULT_SEND_PERCENT = 90

# OwO labels the two buttons on the give prompt; anything that reads like a
# cancel/decline must never be clicked, everything else on that prompt is the
# confirm action (the wording has changed between OwO releases).
CONFIRM_WORDS = ('confirm', 'yes', 'accept', 'send', 'sure', 'ok')
CANCEL_WORDS = ('cancel', 'no', 'decline', 'deny', 'abort', 'reject')


class Owner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cash_requested_at = 0
        self._transfer_channel_id = None
        self._awaiting_give_confirm = 0
        self._give_amount = 0
        self._confirm_clicked = {}

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
            await self._handle_give_confirm(message, owner_id)
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

    def _send_percent(self):
        """How much of the balance 'farmers send' hands over (default 90%).

        Sending 100% leaves the account with nothing to hunt/battle with, which
        stalls the farm until the next payout - so keep a slice behind.
        """
        try:
            pct = float(self._config().get('send_percent', DEFAULT_SEND_PERCENT))
        except (TypeError, ValueError):
            pct = DEFAULT_SEND_PERCENT
        return max(1.0, min(100.0, pct))

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

        pct = self._send_percent()
        amount = min(int(balance * pct / 100), MAX_GIVE)
        if amount < 1:
            self.bot.log("INFO", f"Owner command 'send': {pct:g}% of {balance} rounds to zero, nothing to transfer.")
            return

        self._awaiting_give_confirm = time.time()
        self._give_amount = amount
        self._confirm_clicked.clear()
        await self.bot.neura_enqueue(
            f"owo give <@{owner_id}> {amount}",
            priority=2,
            target_channel_id=self._transfer_channel_id
        )
        if amount >= MAX_GIVE and balance * pct / 100 > MAX_GIVE:
            self.bot.log("INFO", f"Owner command 'send': sending {amount} cowoncy (owo per-transfer cap), keeping {balance - amount}.")
        else:
            self.bot.log("SUCCESS", f"Owner command 'send': sending {amount} cowoncy ({pct:g}% of {balance}) to the owner.")

    def _confirm_window_open(self):
        """True while we are still waiting for OwO's give confirmation prompt."""
        if not self._awaiting_give_confirm:
            return False
        if time.time() - self._awaiting_give_confirm > 30:
            self._awaiting_give_confirm = 0
            return False
        return True

    @staticmethod
    def _looks_like_cancel(text):
        text = (text or '').lower()
        return any(word in text for word in CANCEL_WORDS)

    @classmethod
    def _pick_confirm(cls, entries):
        """Choose the confirm control out of a give prompt's buttons.

        ``entries`` is a list of ``(custom_id, label)``.  Prefer an explicit
        confirm word, fall back to the first non-cancel button - OwO puts
        Confirm first and Cancel second, and some builds ship an emoji-only
        label with no text at all.
        """
        usable = [(cid, label) for cid, label in entries if cid and not cls._looks_like_cancel(f"{label} {cid}")]
        for cid, label in usable:
            haystack = f"{label} {cid}".lower()
            if any(word in haystack for word in CONFIRM_WORDS):
                return cid, label
        return usable[0] if usable else (None, None)

    @commands.Cog.listener()
    async def on_socket_raw_receive(self, msg):
        """Click the confirm button on OwO's components-v2 give prompt.

        OwO's give confirmation is a components v2 message, which
        ``discord.py-self`` cannot model: ``message.components`` is empty, so the
        legacy click path below can never fire and the cowoncy stayed put. Read
        the raw gateway frame instead and click through ``bot.interactions``.
        """
        if not self._owner_id() or not self._confirm_window_open():
            return

        if isinstance(msg, (bytes, bytearray)):
            try:
                msg = msg.decode('utf-8', errors='replace')
            except Exception:
                return

        try:
            raw_data = json.loads(msg)
        except Exception:
            return

        if raw_data.get("t") not in ["MESSAGE_CREATE", "MESSAGE_UPDATE"]:
            return

        data = raw_data.get("d") or {}
        if str((data.get("author") or {}).get("id")) != self.bot.owo_bot_id:
            return
        if str(data.get("channel_id")) not in [str(c) for c in self.bot.channels]:
            return

        components = parse_v2_message(data)
        entries = [(c.custom_id, c.label) for c in buttons(components)]
        if not entries:
            return

        text = f"{data.get('content') or ''}\n{collect_text(components)}".lower()
        # a give prompt names the amount we just sent; that plus our own name is
        # enough to keep from confirming somebody else's transfer in a shared channel
        if 'give' not in text and 'gift' not in text and str(self._give_amount) not in text:
            return
        if not self.bot.identity.text_is_mine(text) and str(self._give_amount) not in text:
            return

        custom_id, label = self._pick_confirm(entries)
        if not custom_id:
            return

        message_id = str(data.get("id") or "")
        key = f"{message_id}:{custom_id}"
        # OwO edits the prompt after the click and MESSAGE_UPDATE re-enters here
        if key in self._confirm_clicked:
            return
        self._confirm_clicked[key] = time.time()

        await asyncio.sleep(random.uniform(0.6, 1.6))
        try:
            ok = await self.bot.interactions.click_button_raw(
                custom_id=custom_id,
                message_id=message_id,
                channel_id=int(data.get("channel_id")),
                author_id=(data.get("author") or {}).get("id"),
                guild_id=data.get("guild_id"),
                flags=data.get("flags", 0)
            )
        except Exception as e:
            self._confirm_clicked.pop(key, None)
            self.bot.log("ERROR", f"Owner command 'send': failed to click confirm: {e}")
            return

        if ok:
            self._awaiting_give_confirm = 0
            self.bot.log("SUCCESS", f"Owner command 'send': confirmed the transfer of {self._give_amount} cowoncy{f' ({label})' if label else ''}.")
        else:
            # let the next MESSAGE_UPDATE retry - a rejected interaction is not a
            # confirmed one, and dropping it silently loses the whole transfer
            self._confirm_clicked.pop(key, None)
            self.bot.log("WARN", "Owner command 'send': confirm click was rejected, will retry on the next prompt update.")

    async def _handle_give_confirm(self, message, owner_id):
        """Legacy fallback: click confirm on a classic (non-v2) give prompt.

        Kept for the case where OwO serves a plain embed with an action row that
        ``discord.py-self`` does model; the components-v2 path above handles the
        modern card.
        """
        if not self._confirm_window_open():
            return
        if not self.bot.is_message_for_me(message):
            return
        if not message.components:
            return

        entries = []
        for row in message.components:
            for btn in getattr(row, 'children', []) or []:
                if getattr(btn, 'disabled', False):
                    continue
                entries.append((getattr(btn, 'custom_id', None) or getattr(btn, 'label', ''), getattr(btn, 'label', '')))
        if not entries:
            return

        target_id, _ = self._pick_confirm(entries)
        if not target_id:
            return

        await asyncio.sleep(random.uniform(0.5, 1.5))
        try:
            for row in message.components:
                for btn in getattr(row, 'children', []) or []:
                    if getattr(btn, 'disabled', False):
                        continue
                    ident = getattr(btn, 'custom_id', None) or getattr(btn, 'label', '')
                    if ident != target_id:
                        continue
                    await btn.click()
                    self._awaiting_give_confirm = 0
                    self.bot.log("SUCCESS", "Owner command 'send': clicked confirm to complete the transfer.")
                    return
        except Exception as e:
            self.bot.log("ERROR", f"Owner command 'send': failed to click confirm button: {e}")


    async def register_actions(self):
        cfg = self._config()
        owner_id = self._owner_id()
        trigger = str(cfg.get('trigger', 'farmers')).lower().strip()
        if owner_id:
            self.bot.log("SYS", f"Owner commands active for {owner_id} - '{trigger} pay | {trigger} send ({self._send_percent():g}% of balance) | {trigger} showbal | {trigger} <any owo command>'")
        elif cfg.get('enabled', False):
            self.bot.log("WARN", f"Owner commands enabled but owner.user_id is not a Discord ID: {cfg.get('user_id')!r}")


async def setup(bot):
    cog = Owner(bot)
    await bot.add_cog(cog)