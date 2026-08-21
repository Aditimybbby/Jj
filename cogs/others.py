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
import time
import re
import random
import core.state as state
from discord.ext import commands

LEVEL_RE = re.compile(r'(?:you are|leveled up to|is now)\s*\**\s*level\s*\**\s*(\d+)')
NO_TEAM_PHRASES = (
    "do not have an active battle team",
    "don't have an active battle team",
    "do not have a battle team",
    "don't have a battle team",
    "you do not have a team",
)

# every zoo row lists the whole tier in a fixed order, with a question mark holding
# the slot of an animal you have never caught - so the slot index gives us the name
# owo expects in "owo team add <name>" (names from the owo wiki)
ZOO_TIERS = {
    "common": ("bee", "bug", "snail", "beetle", "butterfly"),
    "uncommon": ("chick", "mouse", "chicken", "rabbit", "chipmunk"),
    "rare": ("sheep", "pig", "cow", "dog", "cat"),
    "epic": ("crocodile", "tiger", "penguin", "elephant", "whale"),
    "mythic": ("dragon", "unicorn", "snowman", "ghost", "dove"),
    "mythical": ("dragon", "unicorn", "snowman", "ghost", "dove"),
}
CUSTOM_EMOJI_RE = re.compile(r'<a?:(\w+):\d+>')

# a zoo row looks like:  common   🐝¹⁰  🐛⁰⁵  ❓⁰⁰ ...
# the superscript is how many you own, and ❓⁰⁰ is a slot you have never caught
ZOO_TOKEN_RE = re.compile(
    r'(?P<emoji><a?:\w+:\d+>|[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF][\uFE0F\u200D\U0001F000-\U0001FAFF]*)'
    r'(?P<count>[\u2070\u00b9\u00b2\u00b3\u2074-\u2079]*)'
)
SUPERSCRIPTS = {'\u2070': '0', '\u00b9': '1', '\u00b2': '2', '\u00b3': '3', '\u2074': '4',
                '\u2075': '5', '\u2076': '6', '\u2077': '7', '\u2078': '8', '\u2079': '9'}
# rarest first so "uncommon" is never matched as "common"
RARITY_RANK = (
    ("distorted", 8), ("hidden", 7), ("fabled", 6), ("legendary", 5),
    ("mythical", 4), ("mythic", 4), ("epic", 3), ("uncommon", 1), ("rare", 2), ("common", 0),
)
UNOWNED = ("\u2753", "\u2754", "question")


class Others(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.zoo = False
        self._team_setup_at = 0

    def parse_zoo(self, raw):
        """Animals you actually own, rarest first, named the way owo team add wants them."""
        found = []
        for line in raw.splitlines():
            head = line.replace('*', '').strip().lower()[:24]
            match = next(((word, rank) for word, rank in RARITY_RANK if word in head), None)
            if match is None:
                continue
            tier, rank = match
            tier_names = ZOO_TIERS.get(tier, ())

            # every animal carries a superscript count; the tier icon in front of the
            # row does not, and counting it would shift every slot by one
            slots = [
                (token.group('emoji'), ''.join(SUPERSCRIPTS[c] for c in token.group('count')))
                for token in ZOO_TOKEN_RE.finditer(line)
                if token.group('count')
            ]
            if tier_names and len(slots) != len(tier_names):
                self.bot.log("DEBUG", f"Zoo row '{tier}' has {len(slots)} slots, expected {len(tier_names)} - skipped")
                continue

            for slot, (emoji, digits) in enumerate(slots):
                if int(digits) == 0 or any(marker in emoji for marker in UNOWNED):
                    continue
                if slot < len(tier_names):
                    animal = tier_names[slot]
                else:
                    custom = CUSTOM_EMOJI_RE.fullmatch(emoji)
                    animal = custom.group(1) if custom else emoji
                found.append((rank, animal))
        found.sort(key=lambda item: -item[0])
        return [animal for _rank, animal in found]

    async def _auto_accept_rules(self, message):
        all_channels = [str(c) for c in self.bot.channels]
        if str(message.channel.id) not in all_channels:
            return

        content = message.content.lower()
        if "**you must accept these rules to use the bot!**" in content:
            if message.components:
                await asyncio.sleep(random.uniform(0.6, 1.7))
                try:
                    comp = message.components[0]
                    if hasattr(comp, 'children'):
                        btn = comp.children[0]
                        if not btn.disabled:
                            await btn.click()
                            self.bot.log("SUCCESS", "Auto-Accepted OwO Rules")
                except Exception as e:
                    self.bot.log("ERROR", f"Failed to accept rules: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        await self._auto_accept_rules(message)

        monitor_id = str(self.bot.config.get('core', {}).get('monitor_bot_id', '408785106942164992'))
        if str(message.author.id) != monitor_id:
            return
        
        if self.bot.owo_user is None:
            self.bot.owo_user = message.author
        
        all_channels = [str(c) for c in self.bot.channels]
        if str(message.channel.id) not in all_channels:
            return

        content = self.bot.get_full_content(message)

        if "you currently have" in content and "cowoncy" in content:
            if not self.bot.is_message_for_me(message, role="header"):
                self.bot.log("DEBUG", f"Balance reply ignored, not recognised as mine: {content.splitlines()[0][:80]}")
                return
            try:
                cash_match = re.search(r'you currently have[^\d]*([\d,]+)', content)
                if cash_match:
                    cash_str = cash_match.group(1).replace(',', '')
                    is_initial = self.bot.stats['current_cash'] is None
                    self.bot.stats['current_cash'] = int(cash_str)
                    self.bot.stats['last_cash_update'] = time.time()
                    state.record_snapshot(self.bot.user_id)
                    if is_initial:
                        self.bot.log("SYS", f"Initial Cash Balance synced: {cash_str} cowoncy")
                    else:
                        self.bot.log("INFO", f"Cash Updated: {cash_str} cowoncy")
            except:
                pass

        elif LEVEL_RE.search(content):
            if not self.bot.is_message_for_me(message, role="header"):
                self.bot.log("DEBUG", f"Level reply ignored, not recognised as mine: {content.splitlines()[0][:80]}")
                return
            level = int(LEVEL_RE.search(content).group(1))
            if self.bot.stats.get('level') != level:
                self.bot.stats['level'] = level
                self.bot.log("INFO", f"OwO level synced: {level}")

        elif any(phrase in content for phrase in NO_TEAM_PHRASES):
            if not self.bot.is_message_for_me(message):
                return
            if time.time() - self._team_setup_at < 60:
                return
            self._team_setup_at = time.time()
            self.zoo = True
            await self.bot.neura_enqueue("zoo", priority=2)
            self.bot.log("SYS", "No battle team - reading the zoo to build one")

        elif "'s zoo!" in content and self.zoo:
            if not self.bot.is_message_for_me(message, role="header"):
                return
            self.zoo = False
            animals = self.parse_zoo(message.content)

            if not animals:
                self.bot.log("WARN", "Zoo has no animals we can read - team not built")
                return

            for animal in animals[:3]:
                await self.bot.neura_enqueue(f"team add {animal}", priority=2)
                self.bot.log("CMD", f"Team: adding {animal}")

    async def register_actions(self):
        cfg = self.bot.config.get('utilities', {}).get('stats_sync', {})

        if cfg.get('balance', True):
            await self.bot.neura_register_command(
                "cash_sync",
                "owo cash",
                priority=self.bot.get_cmd_priority("cash_sync", 4),
                delay=max(300, int(cfg.get('balance_interval_s', 900))),
                initial_offset=25,
            )
        if cfg.get('level', True):
            await self.bot.neura_register_command(
                "level_sync",
                "owo level",
                priority=self.bot.get_cmd_priority("level_sync", 4),
                delay=max(600, int(cfg.get('level_interval_s', 3600))),
                initial_offset=45,
            )

async def setup(bot):
    cog = Others(bot)
    await bot.add_cog(cog)
