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

# `owo level` answers with the level AND the xp bar in one message, e.g.
#   "**Routo**, you are level **24**!  `12,340/18,000` xp"
# so the level and the xp progress both have to come out of the same reply.
LEVEL_PHRASES = ("you are", "is now", "leveled up", "your level", "level up", "levelled up")
LEVEL_RE = re.compile(r'\b(?:lvl|level)\s*(?:is|:|-)?\s*\**\s*`?\s*(\d{1,4})\b')
LEVEL_SUFFIX_RE = re.compile(r'\b(\d{1,4})\s*\**\s*(?:lvl|level)\b')
XP_PAIR_RE = re.compile(r'([\d,]+)\s*/\s*([\d,]+)\s*\**\s*`?\s*(?:xp|exp)?')
XP_NEEDED_RE = re.compile(r'need\D{0,12}([\d,]+)\s*\**\s*(?:xp|exp)')

NO_TEAM_PHRASES = (
    "do not have an active battle team",
    "don't have an active battle team",
    "do not have a battle team",
    "don't have a battle team",
    "you do not have a team",
    "you don't have a team",
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
    r'(?P<emoji><a?:\w+:\d+>|[\U0001F000-\U0001FAFF☀-➿⬀-⯿][️‍\U0001F000-\U0001FAFF]*)'
    r'(?P<count>[⁰¹²³⁴-⁹]*)'
)
SUPERSCRIPTS = {'⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
                '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'}
# rarest first so "uncommon" is never matched as "common"
RARITY_RANK = (
    ("distorted", 8), ("hidden", 7), ("fabled", 6), ("legendary", 5),
    ("mythical", 4), ("mythic", 4), ("epic", 3), ("uncommon", 1), ("rare", 2), ("common", 0),
)
RANK_NAMES = {0: "common", 1: "uncommon", 2: "rare", 3: "epic", 4: "mythical",
              5: "legendary", 6: "fabled", 7: "hidden", 8: "distorted"}
UNOWNED = ("❓", "❔", "question")

# animals whose tier we know without ever reading the zoo
STATIC_RANKS = {
    name: rank
    for tier, rank in RARITY_RANK
    for name in ZOO_TIERS.get(tier, ())
}


def parse_level_xp(content):
    """(level, xp, xp_needed) out of an `owo level` reply - any of them may be None."""
    if "level" not in content and "lvl" not in content:
        return None, None, None

    xp = xp_needed = None
    xp_match = XP_PAIR_RE.search(content)
    if xp_match:
        try:
            xp = int(xp_match.group(1).replace(',', ''))
            xp_needed = int(xp_match.group(2).replace(',', ''))
        except ValueError:
            xp = xp_needed = None
        if xp_needed is not None and (xp_needed <= 0 or xp > xp_needed * 50):
            xp = xp_needed = None

    if xp is None:
        needed_match = XP_NEEDED_RE.search(content)
        if needed_match:
            try:
                xp_needed = int(needed_match.group(1).replace(',', ''))
            except ValueError:
                xp_needed = None

    # a bare "level" mention is not enough - owo says "level" in plenty of other replies
    if xp is None and xp_needed is None and not any(p in content for p in LEVEL_PHRASES):
        return None, None, None

    level = None
    match = LEVEL_RE.search(content) or LEVEL_SUFFIX_RE.search(content)
    if match:
        try:
            level = int(match.group(1))
        except ValueError:
            level = None
    if level is not None and not 0 < level < 10000:
        level = None

    return level, xp, xp_needed


class Others(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.zoo = False
        self._team_setup_at = 0
        self._animal_ranks = dict(STATIC_RANKS)
        self._owned = []
        self._owned_at = 0
        self._want_team_check = False
        self._last_team_action = 0

    # ── zoo / team ───────────────────────────────────────────────────────────

    def _team_cfg(self):
        return self.bot.config.get('commands', {}).get('team', {})

    def rank_of(self, animal):
        return self._animal_ranks.get(str(animal).lower(), -1)

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
            aligned = bool(tier_names) and len(slots) == len(tier_names)

            for slot, (emoji, digits) in enumerate(slots):
                if int(digits) == 0 or any(marker in emoji for marker in UNOWNED):
                    continue

                # owo renders every animal as a custom emoji whose name is the animal,
                # so that beats guessing from the slot index - and it is the only thing
                # that works for legendary/fabled/hidden rows we have no name list for
                custom = CUSTOM_EMOJI_RE.fullmatch(emoji)
                if custom:
                    animal = custom.group(1).lower()
                elif aligned:
                    animal = tier_names[slot]
                elif slot < len(tier_names):
                    animal = tier_names[slot]
                else:
                    # a unicode emoji in a row we cannot line up - no usable name
                    continue

                found.append((rank, animal))
                if rank > self._animal_ranks.get(animal, -1):
                    self._animal_ranks[animal] = rank

        # keep the strongest copy of every animal, rarest first
        best = {}
        for rank, animal in found:
            if rank > best.get(animal, -1):
                best[animal] = rank
        return sorted(best.items(), key=lambda item: -item[1])

    def parse_team(self, raw):
        """Animal names currently on the battle team, in slot order."""
        names = [name.lower() for name in CUSTOM_EMOJI_RE.findall(raw)]
        if not names:
            # a text-only team listing quotes the names instead
            names = [n.lower() for n in re.findall(r'`\s*([a-z][a-z_\- ]{1,20})\s*`', raw, re.IGNORECASE)]
        seen = []
        for name in names:
            if name in UNOWNED or name in seen:
                continue
            seen.append(name)
        return seen

    async def request_team_check(self, reason=""):
        """Read the zoo, then the team, so we can swap in anything rarer."""
        if not self._team_cfg().get('enabled', True):
            return
        if time.time() - self._last_team_action < 45:
            return
        self._last_team_action = time.time()
        self.zoo = True
        self._want_team_check = True
        await self.bot.neura_enqueue("zoo", priority=3, _cmd_id="zoo")
        if reason:
            self.bot.log("TEAM", f"Checking the zoo for a better team ({reason})")

    async def _apply_team_upgrade(self, current):
        """Swap the weakest team slots for the rarest animals we own."""
        cfg = self._team_cfg()
        slots = max(1, int(cfg.get('slots', 3) or 3))
        add_template = cfg.get('add_template', 'team add {animal}')
        remove_template = cfg.get('remove_template', 'team remove {slot}')

        owned = [(animal, rank) for animal, rank in self._owned]
        if not owned:
            self.bot.log("WARN", "Zoo has no animals we can read - team unchanged")
            return

        wanted = [animal for animal, _rank in owned[:slots]]
        current = current[:slots]

        # anything already in place stays put; everything else is a candidate swap
        keep = [animal for animal in current if animal in wanted]
        missing = [animal for animal in wanted if animal not in keep]
        free_slots = [i + 1 for i, animal in enumerate(current) if animal not in keep]
        free_slots += list(range(len(current) + 1, slots + 1))

        if not missing:
            best_rank = RANK_NAMES.get(self.rank_of(wanted[0]), '?') if wanted else '?'
            self.bot.log("TEAM", f"Team already holds the rarest animals we own (top tier: {best_rank})")
            return

        replaced = [
            animal for i, animal in enumerate(current)
            if (i + 1) in free_slots and animal
        ]

        # drop the weak ones from the highest slot down so the lower indexes stay valid
        for slot in sorted([s for s in free_slots if s <= len(current)], reverse=True):
            await self.bot.neura_enqueue(remove_template.format(slot=slot), priority=3)

        for animal, slot in zip(missing, free_slots):
            await self.bot.neura_enqueue(add_template.format(animal=animal, slot=slot), priority=3)
            self.bot.log(
                "TEAM",
                f"Team slot {slot}: adding {animal} ({RANK_NAMES.get(self.rank_of(animal), 'unknown')})"
            )

        if replaced:
            self.bot.log("TEAM", f"Dropped {', '.join(replaced)} for rarer animals")

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
                    is_initial = self.bot.stats.get('current_cash') is None
                    self.bot.stats['current_cash'] = int(cash_str)
                    self.bot.stats['last_cash_update'] = time.time()
                    state.record_snapshot(self.bot.user_id)
                    if is_initial:
                        self.bot.log("SYS", f"Initial Cash Balance synced: {cash_str} cowoncy")
                    else:
                        self.bot.log("INFO", f"Cash Updated: {cash_str} cowoncy")
            except Exception:
                pass
            return

        level, xp, xp_needed = parse_level_xp(content)
        if level is not None or xp is not None:
            if not self.bot.is_message_for_me(message, role="header") and not self.bot.is_message_for_me(message):
                self.bot.log("DEBUG", f"Level reply ignored, not recognised as mine: {content.splitlines()[0][:80]}")
                return
            st = self.bot.stats
            changed = []
            if level is not None and st.get('level') != level:
                st['level'] = level
                changed.append(f"level {level}")
            if xp is not None:
                st['xp'] = xp
                st['xp_needed'] = xp_needed
                changed.append(f"{xp:,}/{xp_needed:,} xp" if xp_needed else f"{xp:,} xp")
            elif xp_needed is not None:
                st['xp_needed'] = xp_needed
            if changed:
                st['last_level_update'] = time.time()
                state.save_account_stats()
                self.bot.log("INFO", f"OwO level synced: {', '.join(changed)}")
            return

        if any(phrase in content for phrase in NO_TEAM_PHRASES):
            if not self.bot.is_message_for_me(message):
                return
            if time.time() - self._team_setup_at < 60:
                return
            self._team_setup_at = time.time()
            await self.request_team_check("no battle team")
            return

        if "'s zoo!" in content and self.zoo:
            if not self.bot.is_message_for_me(message, role="header"):
                return
            self.zoo = False
            self._owned = self.parse_zoo(message.content)
            self._owned_at = time.time()

            if not self._owned:
                self.bot.log("WARN", "Zoo has no animals we can read - team not built")
                self._want_team_check = False
                return

            top = ", ".join(f"{a} ({RANK_NAMES.get(r, '?')})" for a, r in self._owned[:3])
            self.bot.log("TEAM", f"Zoo read: {len(self._owned)} animals owned, rarest are {top}")

            if self._want_team_check:
                await self.bot.neura_enqueue("team", priority=3, _cmd_id="team")
            return

        if self._want_team_check and ("'s team" in content or "battle team" in content) and "add" not in content:
            if not self.bot.is_message_for_me(message, role="header"):
                return
            self._want_team_check = False
            current = self.parse_team(message.content)
            self.bot.log("TEAM", f"Current team: {', '.join(current) if current else 'empty'}")
            await self._apply_team_upgrade(current)
            weapons = self.bot.get_cog('Weapons')
            if weapons:
                weapons.note_team(current or [animal for animal, _r in self._owned[:3]])
            return

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

        team_cfg = self._team_cfg()
        if team_cfg.get('enabled', True):
            interval = max(300, int(team_cfg.get('check_interval_min', 45) or 45) * 60)
            await self.bot.neura_register_command(
                "team_scan",
                self._team_scan_tick,
                priority=self.bot.get_cmd_priority("team_scan", 5),
                delay=interval,
                initial_offset=90,
            )
            self.bot.log("SYS", f"Team Manager configured (zoo re-check every {interval // 60}m).")
        else:
            self.bot.cmd_states.pop('team_scan', None)

    async def _team_scan_tick(self):
        """Timer hook - kicks off a zoo read and returns None so nothing is sent twice."""
        await self.request_team_check("scheduled check")
        return None


async def setup(bot):
    cog = Others(bot)
    await bot.add_cog(cog)
