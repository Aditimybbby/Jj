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


import asyncio
import re
import time
import random
import json
import core.state as state
from discord.ext import commands
from neura_engines.quest_engine import NeuraQuestEngine
from component_v2_neura import parse_v2_message, collect_text, buttons

QUEST_TITLE_RE = re.compile(r'^\W{0,4}(\d{1,2})\s*[.)\-]\s*(.+)$')
PROGRESS_RE = re.compile(r'\b(\d+)\s*/\s*(\d+)\b')
# words owo puts on a claim control, on the custom_id or on the visible label
CLAIM_HINTS = ("claim", "reward")
# owo swaps the "N/M" counter for a tick once a quest is finished
DONE_MARKERS = ("✅", "☑", "✔", "🎉", "completed!", "quest complete")

class Quest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active = True
        self.task = None
        self.engine = NeuraQuestEngine(self.bot)
        self._claimed = {}
        self._claim_lock = asyncio.Lock()
        self._last_recheck = 0.0

    async def register_actions(self):
        cfg = self.bot.config.get('commands', {}).get('quest', {})
        if cfg.get('enabled', True):
            self.bot.log("SYS", "Quest Module configured.")
            ih = cfg.get('interval_h', 6)
            await self.bot.neura_register_command("quest", "quest", priority=self.bot.get_cmd_priority("quest", 4), delay=ih * 3600, initial_offset=10)
            self.trigger_action()
            
            self.engine.start()

    def trigger_action(self):
        cfg = self.bot.config.get('commands', {}).get('quest', {})
        ih = cfg.get('interval_h', 6)
        
        if 'quest' in self.bot.cmd_states:
            self.bot.cmd_states['quest']['delay'] = ih * 3600

    @commands.Cog.listener()
    async def on_socket_raw_receive(self, msg):
        if not self.active or self.bot.paused:
            return

        if isinstance(msg, bytes):
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
        if not components:
            return

        v2_text = collect_text(components)
        content = data.get("content") or ""
        full_text = f"{content}\n{v2_text}".lower()

        if "quest log" in full_text or "checklist" in full_text:
            if not self._v2_text_is_mine(full_text):
                return

            await self._parse_quests_v2(components, data, v2_text)

    def _v2_text_is_mine(self, full_text):
        """components v2 messages are invisible to discord.py-self, so match by name."""
        if f"<@{self.bot.user.id}>" in full_text or f"<@!{self.bot.user.id}>" in full_text:
            return True
        idents = {self.bot.user.name.lower(), (self.bot.display_name or "").lower()}
        for ident in getattr(self.bot, 'identifiers', []):
            idents.add(ident.replace("<@", "").replace("!", "").replace(">", "").lower())
        return any(ident and len(ident) >= 2 and ident in full_text for ident in idents)

    async def _parse_quests_v2(self, components, message_data, v2_text=None):
        text = v2_text if v2_text is not None else collect_text(components)
        text = text.replace('*', '').replace('`', '')
        text_lines = [line.strip().lower() for line in text.split('\n') if line.strip()]

        quests = []
        current_quest = None
        for line in text_lines:
            title_match = QUEST_TITLE_RE.match(line)
            if title_match:
                title = title_match.group(2).strip()
                current_quest = {
                    'slot': int(title_match.group(1)),
                    'description': title,
                    'title': title,
                    'current': 0,
                    'total': 1,
                    'completed': False,
                }
                quests.append(current_quest)
                self._apply_progress(current_quest, title)
                continue

            if current_quest is not None and self._apply_progress(current_quest, line):
                current_quest = None

        st = self.bot.stats
        old_quests = st.get('quest_data', [])

        cleaned_quests = []
        for q in quests:
            desc_text = q['description']
            cleaned_quests.append({
                'description': desc_text,
                'current': q['current'],
                'total': q['total'],
                'completed': q['completed'],
            })

            if q['completed']:
                was_completed = any(
                    oq.get('description', '').lower() == desc_text.lower() and oq.get('completed')
                    for oq in old_quests
                )
                if not was_completed:
                    self.bot.log("SUCCESS", f"QUEST COMPLETED: {desc_text}")

        if cleaned_quests:
            st['quest_data'] = cleaned_quests
            self.bot.log("SYS", f"Dashboard synced: {len(cleaned_quests)} V2 quests tracked.")

        #  global timer in v2 text lines
        timer_pattern = r'next quest.*?\bin\s*(\d+\w+(?:\s*\d+\w+)*)'
        for line in text_lines:
            timer_match = re.search(timer_pattern, line)
            if timer_match:
                st['next_quest_timer'] = timer_match.group(1).upper()
                break

        await self._claim_rewards(components, message_data, sum(1 for q in quests if q['completed']))

    @staticmethod
    def _claim_targets(components):
        """Every *enabled* claim control on the card.

        `buttons()` already drops the disabled ones and owo only enables a claim
        button while the reward is actually waiting, so this is a far better signal
        than re-deriving completion from the "N/M" text. Claiming used to be gated on
        that text plus a slot number scraped out of the custom_id, and when either
        guess missed - a finished quest rendered with a tick instead of a counter, a
        custom_id whose trailing digits were not the slot - the reward was left
        sitting there. That is the "completes but never claims" bug.
        """
        found = []
        for comp in buttons(components):
            haystack = f"{comp.custom_id} {comp.label or ''}".lower()
            if any(hint in haystack for hint in CLAIM_HINTS):
                found.append((comp.custom_id, comp.label or comp.custom_id))
        return found

    async def _claim_rewards(self, components, message_data, completed_count):
        cfg = self.bot.config.get('commands', {}).get('quest', {})
        if not cfg.get('auto_claim', True):
            return

        channel_id = message_data.get("channel_id")
        message_id = str(message_data.get("id") or "")
        if not channel_id or not message_id:
            return

        targets = self._claim_targets(components)
        if not targets:
            if completed_count:
                await self._recheck_for_claim(completed_count)
            return

        for custom_id, label in targets:
            key = f"{message_id}:{custom_id}"
            # owo edits the quest card after every claim and MESSAGE_UPDATE brings us
            # straight back here, so without this the same button is clicked on a loop
            if key in self._claimed:
                continue
            self._claimed[key] = time.time()

            async with self._claim_lock:
                # awaited, not fire-and-forget: click_button_raw returns whether discord
                # accepted the interaction, and throwing that away meant a rejected claim
                # looked identical to a successful one
                try:
                    ok = await self.bot.interactions.click_button_raw(
                        custom_id=custom_id,
                        message_id=message_data.get("id"),
                        channel_id=int(channel_id),
                        author_id=(message_data.get("author") or {}).get("id"),
                        guild_id=message_data.get("guild_id"),
                        flags=message_data.get("flags", 0)
                    )
                except Exception as e:
                    ok = False
                    self.bot.log("ERROR", f"Quest claim raised: {e}")

                if ok:
                    self.bot.log("SUCCESS", f"Quest reward claimed ({label}).")
                else:
                    # forget the key so the next card retries instead of skipping forever
                    self._claimed.pop(key, None)
                    self.bot.log("ERROR", f"Quest reward claim rejected ({label}) - retrying on the next quest card.")

                await asyncio.sleep(random.uniform(1.1, 2.2))

        if len(self._claimed) > 200:
            self._claimed.clear()

    async def _recheck_for_claim(self, completed_count):
        """Finished quests, but the card carried no claim button - ask for a fresh one.

        Deliberately enqueued under its own id: the scheduled `quest` slot sits on a
        six-hour cooldown, and waiting that long to collect a finished reward is the
        very thing being fixed here.
        """
        if time.time() - self._last_recheck < 300:
            return
        self._last_recheck = time.time()
        self.bot.log(
            "WARN",
            f"{completed_count} quest(s) finished but the card had no claim button - "
            "requesting a fresh quest log."
        )
        await self.bot.neura_enqueue("owo quest", priority=4, _cmd_id="quest_claim_recheck")

    @staticmethod
    def _apply_progress(quest, line):
        progress_match = PROGRESS_RE.search(line)
        if not progress_match:
            if any(marker in line for marker in DONE_MARKERS):
                quest['current'] = quest['total']
                quest['completed'] = True
                return True
            return False
        current, total = int(progress_match.group(1)), int(progress_match.group(2))
        if total <= 0:
            return False
        quest['current'] = current
        quest['total'] = total
        quest['completed'] = current >= total or any(marker in line for marker in DONE_MARKERS)
        return True

    @commands.Cog.listener()
    async def on_message(self, message):
        core_config = self.bot.config.get('core', {})
        monitor_id = str(core_config.get('monitor_bot_id', '408785106942164992'))
        
        if str(message.author.id) != monitor_id:
            return
        if self.bot.owo_user is None:
            self.bot.owo_user = message.author
        all_channels = [str(c) for c in self.bot.channels]
        if str(message.channel.id) not in all_channels:
            return

        full_text = self.bot.get_full_content(message)
        if "quest log" not in full_text and "checklist" not in full_text:
            return
        if not self.bot.is_message_for_me(message, role="header"):
            return

        completed = self._parse_quests_legacy(full_text)

        # a legacy embed can still carry real buttons, and the old code skipped the
        # whole message whenever it did - so a claimable reward on an embed card was
        # never even looked at
        if message.components:
            await self._claim_legacy(message, completed)

    async def _claim_legacy(self, message, completed_count):
        """Claim from an embed-style card using discord.py-self's own button click."""
        cfg = self.bot.config.get('commands', {}).get('quest', {})
        if not cfg.get('auto_claim', True):
            return

        for row in message.components:
            for btn in getattr(row, 'children', []):
                if getattr(btn, 'disabled', False):
                    continue
                haystack = f"{getattr(btn, 'custom_id', '') or ''} {getattr(btn, 'label', '') or ''}".lower()
                if not any(hint in haystack for hint in CLAIM_HINTS):
                    continue

                key = f"{message.id}:{getattr(btn, 'custom_id', '') or haystack}"
                if key in self._claimed:
                    continue
                self._claimed[key] = time.time()
                try:
                    await asyncio.sleep(random.uniform(0.8, 1.8))
                    await btn.click()
                    self.bot.log("SUCCESS", f"Quest reward claimed ({getattr(btn, 'label', None) or 'claim'}).")
                except Exception as e:
                    self._claimed.pop(key, None)
                    self.bot.log("ERROR", f"Quest reward claim failed: {e}")
                return

        if completed_count:
            await self._recheck_for_claim(completed_count)

    def _parse_quests_legacy(self, text):
        progress_pattern = r'progress:\s*\[(\d+)/(\d+)\]'
        timer_pattern = r'next quest in:\s*(\d+h \d+m \d+s)'
        
        clean_text = text.replace(':blank:', '').replace('*', '')
        lines = [line.strip() for line in clean_text.split('\n') if line.strip()]
        
        new_quest_data = []
        current_description = None
        
        st = self.bot.stats
        old_quests = st.get('quest_data', [])
        
        for i, line in enumerate(lines):
            if "reward:" in line.lower():
                desc_part = re.split(r'reward:', line, flags=re.IGNORECASE)[0].strip()
                desc_part = desc_part.replace('‣', '').strip()
                
                if desc_part:
                    raw_desc = desc_part
                else:
                    raw_desc = lines[i-1] if i > 0 else ""
                
                clean_desc = re.sub(r'^\d+[\)\.]\s*', '', raw_desc)
                clean_desc = re.sub(r'<[^>]*>', '', clean_desc)
                clean_desc = clean_desc.replace('`', '').strip()
                
                if clean_desc and 'quest log' not in clean_desc.lower() and 'quests belong' not in clean_desc.lower():
                    current_description = clean_desc
            
            progress_match = re.search(progress_pattern, line, re.IGNORECASE)
            if progress_match and current_description:
                current = int(progress_match.group(1))
                total = int(progress_match.group(2))
                
                is_completed = current >= total
                quest_item = {
                    'description': current_description,
                    'current': current,
                    'total': total,
                    'completed': is_completed
                }
                new_quest_data.append(quest_item)
                
                if is_completed:
                    was_completed = any(q['description'] == current_description and q.get('completed') for q in old_quests)
                    if not was_completed:
                        self.bot.log("SUCCESS", f"QUEST COMPLETED: {current_description}")
                
                current_description = None

        timer_match = re.search(timer_pattern, text, re.IGNORECASE)
        next_timer = timer_match.group(1).upper() if timer_match else None
        
        valid_quests = [q for q in new_quest_data if 'progress' not in q['description'].lower()]

        if valid_quests or "quest log" in text.lower():
            st['quest_data'] = valid_quests

        st['next_quest_timer'] = next_timer
        return sum(1 for q in valid_quests if q.get('completed'))

async def setup(bot):
    cog = Quest(bot)
    await bot.add_cog(cog)