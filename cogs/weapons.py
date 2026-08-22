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

Keeps the best weapons we own bolted onto the battle team. `owo weapon` is read on a
timer, the strongest unequipped weapons are picked, and each one is equipped to a team
slot. The equip command is a template in settings so it can be corrected without a
code change if owo renames it.
"""


import re
import time

from discord.ext import commands

QUALITY_RANK = (
    ("distorted", 8), ("hidden", 7), ("fabled", 6), ("legendary", 5),
    ("mythical", 4), ("mythic", 4), ("epic", 3), ("uncommon", 1), ("rare", 2), ("common", 0),
)
RANK_NAMES = {0: "common", 1: "uncommon", 2: "rare", 3: "epic", 4: "mythical",
              5: "legendary", 6: "fabled", 7: "hidden", 8: "distorted"}

# `001` or **001** or plain 001 at the head of a weapon row
WEAPON_ID_RE = re.compile(r'(?:^|[\s`*\[(])([0-9a-z]{3,4})(?:[`*\])]|\s|$)', re.IGNORECASE)
STAR_RE = re.compile(r'(\d{1,3})\s*(?:%|star)')
EQUIPPED_MARKERS = ("equipped", "in use", "⚔️")


class Weapons(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.team = []
        self._last_plan = []
        self._last_equip_at = 0
        self._awaiting_list = False

    # ── config ───────────────────────────────────────────────────────────────

    def _cfg(self):
        return self.bot.config.get('commands', {}).get('weapon', {})

    def _slots(self):
        configured = int(self._cfg().get('slots', 3) or 3)
        return max(1, min(configured, len(self.team) or configured))

    def note_team(self, team):
        """Others tells us what is on the team so we know how many slots to arm."""
        self.team = list(team or [])

    # ── parsing ──────────────────────────────────────────────────────────────

    def parse_weapons(self, raw):
        """[(rank, stars, weapon_id)] for every weapon that is not already equipped."""
        found = []
        for line in raw.splitlines():
            lowered = line.lower()
            quality = next(((word, rank) for word, rank in QUALITY_RANK if word in lowered), None)
            if quality is None:
                continue
            if any(marker in lowered for marker in EQUIPPED_MARKERS):
                continue

            id_match = WEAPON_ID_RE.search(line)
            if not id_match:
                continue
            weapon_id = id_match.group(1).lstrip('0') or id_match.group(1)

            stars = 0
            star_match = STAR_RE.search(lowered)
            if star_match:
                try:
                    stars = int(star_match.group(1))
                except ValueError:
                    stars = 0

            found.append((quality[1], stars, weapon_id))

        # strongest first, de-duplicated by id
        found.sort(key=lambda item: (-item[0], -item[1]))
        seen = set()
        unique = []
        for rank, stars, weapon_id in found:
            if weapon_id in seen:
                continue
            seen.add(weapon_id)
            unique.append((rank, stars, weapon_id))
        return unique

    # ── flow ─────────────────────────────────────────────────────────────────

    async def request_weapon_check(self, reason=""):
        cfg = self._cfg()
        if not cfg.get('enabled', True):
            return
        if time.time() - self._last_equip_at < 60:
            return
        self._awaiting_list = True
        await self.bot.neura_enqueue(cfg.get('list_command', 'weapon'), priority=4, _cmd_id="weapon")
        if reason:
            self.bot.log("WEAPON", f"Reading the weapon inventory ({reason})")

    async def _equip(self, weapons):
        cfg = self._cfg()
        template = cfg.get('equip_template', 'weapon equip {weapon} {slot}')
        slots = self._slots()

        plan = [(weapon_id, slot) for slot, (_r, _s, weapon_id) in enumerate(weapons[:slots], start=1)]
        if not plan:
            self.bot.log(
                "WARN",
                "No unequipped weapons found in `owo weapon` - nothing to assign. "
                "If owo changed the layout, adjust commands.weapon.equip_template in settings."
            )
            return

        if plan == self._last_plan and time.time() - self._last_equip_at < 3600:
            self.bot.log("WEAPON", "Best weapons are already assigned - nothing to do")
            return

        self._last_plan = plan
        self._last_equip_at = time.time()

        for (weapon_id, slot), (rank, stars, _id) in zip(plan, weapons[:slots]):
            await self.bot.neura_enqueue(template.format(weapon=weapon_id, slot=slot), priority=4)
            label = RANK_NAMES.get(rank, 'unknown')
            extra = f" {stars}%" if stars else ""
            self.bot.log("WEAPON", f"Equipping weapon {weapon_id} ({label}{extra}) to slot {slot}")

    @commands.Cog.listener()
    async def on_message(self, message):
        cfg = self._cfg()
        if not cfg.get('enabled', True):
            return

        monitor_id = str(self.bot.config.get('core', {}).get('monitor_bot_id', '408785106942164992'))
        if str(message.author.id) != monitor_id:
            return
        if str(message.channel.id) not in [str(c) for c in self.bot.channels]:
            return

        content = self.bot.get_full_content(message)

        if "weapon" not in content:
            return

        if self._awaiting_list and ("'s weapon" in content or "weapon inventory" in content or "weapons!" in content):
            if not self.bot.is_message_for_me(message, role="header"):
                return
            self._awaiting_list = False
            weapons = self.parse_weapons(f"{message.content}\n{self._embed_text(message)}")
            await self._equip(weapons)
            return

        if not self.bot.is_message_for_me(message):
            return

        if "equipped" in content and ("weapon" in content or "to your" in content):
            self.bot.log("SUCCESS", "Weapon equipped.")
        elif "you don't have any weapons" in content or "no weapons" in content:
            self._awaiting_list = False
            self._last_equip_at = time.time()
            self.bot.log("WEAPON", "No weapons owned yet - will retry on the next check.")

    @staticmethod
    def _embed_text(message):
        chunks = []
        for embed in message.embeds or []:
            if embed.title:
                chunks.append(embed.title)
            if embed.description:
                chunks.append(embed.description)
            for field in embed.fields:
                chunks.append(f"{field.name}\n{field.value}")
        return "\n".join(chunks)

    async def register_actions(self):
        cfg = self._cfg()
        if not cfg.get('enabled', True):
            self.bot.cmd_states.pop('weapon_scan', None)
            return

        interval = max(600, int(cfg.get('check_interval_min', 60) or 60) * 60)
        await self.bot.neura_register_command(
            "weapon_scan",
            self._weapon_scan_tick,
            priority=self.bot.get_cmd_priority("weapon_scan", 5),
            delay=interval,
            initial_offset=150,
        )
        self.bot.log("SYS", f"Weapon Manager configured (re-check every {interval // 60}m).")

    async def _weapon_scan_tick(self):
        await self.request_weapon_check("scheduled check")
        return None


async def setup(bot):
    await bot.add_cog(Weapons(bot))
