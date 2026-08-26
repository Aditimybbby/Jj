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
import io
import json
import time
import re
import random
import core.state as state
from discord.ext import commands
from component_v2_neura import parse_v2_message, collect_text, is_image_only, media_image_url
from modules.level_ocr import parse_level_card, ocr_status, ocr_engine_path

# ── level ────────────────────────────────────────────────────────────────────
# owo prints the word "level" in hunt results, battle logs, quest lines, weapon
# and pet replies, so matching it loosely is how a random number ended up on the
# dashboard labelled as the account level. Only these phrasings are owo talking
# about *our own* level, and a blank level beats a wrong one.
SELF_LEVEL_PHRASES = (
    "you are level", "you're level", "you are now level", "you're now level",
    "your level is", "your current level", "you have reached level",
    "you reached level", "you leveled up", "you levelled up",
    "you are lvl", "you're lvl", "your lvl is",
)
# a level that belongs to an animal, a weapon or a pet.
#
# "'s level" is deliberately NOT here: `owo level` answers with a card headed
# "<username>'s Level", so treating a possessive as foreign-player noise aborted the
# parse of our own card and the dashboard level stayed blank forever. Another player's
# card cannot reach the parser anyway - both callers gate on identity first
# (_v2_is_mine in the v2 path, is_message_for_me(role="header") in the legacy one).
LEVEL_NOISE = ("weapon level", "your pet", "battle log")

# xp has to be spelled out. The old pattern had "(?:xp|exp)?" optional, so any
# "12/20" - quest progress, battle hp, an inventory count - was read as an xp bar,
# and a non-None xp then waved the level guard through with whatever number the
# level regex happened to find first.
XP_LABELLED_RE = re.compile(r'(?:xp|exp)\s*\**\s*:?\s*`?\s*([\d,]+)\s*/\s*([\d,]+)')
XP_PAIR_RE = re.compile(r'([\d,]+)\s*/\s*([\d,]+)\s*\**\s*`?\s*(?:xp|exp)\b')
XP_NEEDED_RE = re.compile(r'need\D{0,12}([\d,]+)\s*\**\s*(?:xp|exp)')
LEVEL_RE = re.compile(r'\b(?:lvl|level)\s*(?:is|:|-)?\s*\**\s*`?\s*(\d{1,4})\b')
LEVEL_SUFFIX_RE = re.compile(r'\b(\d{1,4})\s*\**\s*(?:lvl|level)\b')

NO_TEAM_PHRASES = (
    "do not have an active battle team",
    "don't have an active battle team",
    "do not have a battle team",
    "don't have a battle team",
    "you do not have a team",
    "you don't have a team",
)

HUNT_PHRASES = ("you found:", "caught a", "caught an")
# owo says this when `team add` was handed something it does not recognise
BAD_ANIMAL_PHRASES = ("invalid animal", "could not find that animal", "is not a valid animal",
                      "you do not own", "don't own that")

# every zoo row lists the whole tier in a fixed order, with a question mark holding
# the slot of an animal you have never caught - so the slot index gives us the name
# owo expects in "owo team add <name>" (names + order from the owo wiki "All Animals")
ZOO_TIERS = {
    "common": ("bee", "bug", "snail", "beetle", "butterfly"),
    "uncommon": ("chick", "mouse", "chicken", "rabbit", "chipmunk"),
    "rare": ("sheep", "pig", "cow", "dog", "cat"),
    "epic": ("crocodile", "tiger", "penguin", "elephant", "whale"),
    "mythic": ("dragon", "unicorn", "snowman", "ghost", "dove"),
    "mythical": ("dragon", "unicorn", "snowman", "ghost", "dove"),
    "patreon": ("pbird", "pdolphin", "pogre", "pscorpion", "ptiger"),
    "gem": ("camel", "fish", "panda", "shrimp", "spider"),
    "legendary": ("deer", "fox", "lion", "owl", "squid"),
    "fabled": ("boar", "eagle", "frog", "gorilla", "wolf"),
    "bot": ("dinobot", "giraffbot", "slothbot", "hedgebot", "lobbot"),
    "hidden": ("koala", "lizard", "monkey", "snake", "octopus"),
    "distorted": ("glitchparrot", "glitchotter", "glitchraccoon", "glitchflamingo", "glitchzebra"),
    # special/event animals are dynamic (e.g. 2026july_pecan) - no fixed slot list,
    # so the parser falls back to the custom-emoji name, which is exactly what
    # `owo team add` accepts for them.
    "special": (),
}
CUSTOM_EMOJI_RE = re.compile(r'<a?:(\w+):\d+>')

# unicode fallbacks for the same lists. A zoo row drawn with plain emoji used to be
# thrown away, which is the main reason rare animals never made it onto the team.
# OwO renders every animal as a *custom* server emoji (named after the animal), so
# the custom-emoji-name path in ``_name_for`` is the one that actually fires for the
# higher tiers; these unicode maps are a safety net for the handful of clients /
# embeds that fall back to standard emoji. Names follow the OwO wiki "All Animals"
# page (https://owobot.fandom.com/wiki/All_Animals) so `owo team add <name>` works.
# Note: octopus has no single-codepoint unicode glyph, so it relies on the custom
# emoji path (or the ZOO_TIERS slot index) - that is intentional.
UNICODE_ANIMALS = {
    # common
    "🐝": "bee", "🐛": "bug", "🐌": "snail", "🪲": "beetle", "🦋": "butterfly",
    # uncommon
    "🐤": "chick", "🐭": "mouse", "🐔": "chicken", "🐰": "rabbit", "🐿": "chipmunk",
    # rare
    "🐑": "sheep", "🐷": "pig", "🐮": "cow", "🐶": "dog", "🐱": "cat",
    # epic
    "🐊": "crocodile", "🐯": "tiger", "🐧": "penguin", "🐘": "elephant", "🐳": "whale",
    # mythical
    "🐲": "dragon", "🦄": "unicorn", "⛄": "snowman", "👻": "ghost", "🕊": "dove",
    # gem
    "🐫": "camel", "🐟": "fish", "🐼": "panda", "🦐": "shrimp", "🕷": "spider",
    # legendary
    "🦌": "deer", "🦊": "fox", "🦁": "lion", "🦉": "owl", "🐙": "squid",
    # fabled
    "🐗": "boar", "🦅": "eagle", "🐸": "frog", "🦍": "gorilla", "🐺": "wolf",
    # hidden (octopus has no plain glyph - handled by custom emoji / slot index)
    "🐨": "koala", "🦎": "lizard", "🐵": "monkey", "🐍": "snake",
}
# selectors and joiners that ride along with an emoji but are not part of its identity
EMOJI_MODIFIERS = ("️", "︎", "‍", "⃣")

# OwO writes the lower zoo tiers as Discord *shortcodes*. The zoo row arrives on the
# wire as the literal text ":bee:²⁷³  :bug:²⁷⁸  :snail:²⁷² ...", which is neither a
# unicode glyph nor a custom emoji - Discord's client turns it into a picture at render
# time, so it only looks like an emoji. ZOO_TOKEN_RE matched `<:name:id>` and unicode
# glyphs only, so every one of those tokens was skipped: a live 26-animal zoo parsed as
# exactly one animal (the single legendary that owo does draw as a custom emoji). That
# one number fed the zoo panel, the battle team picker and the hunt watcher alike.
#
# Keys are the shortcode without its colons, values are the names `owo team add`
# expects. OwO picks the "2" variants for several rows (🐁 :mouse2: not 🐭 :mouse:,
# 🐇 :rabbit2:, 🐈 :cat2:, 🐕 :dog2:, 🐖 :pig2:, 🐄 :cow2:, 🐅 :tiger2:, ⛄ :snowman2:),
# so both spellings are mapped rather than guessing which one a future card uses.
SHORTCODE_RE = re.compile(r':(\w+):')
SHORTCODE_ANIMALS = {
    # common
    "bee": "bee", "bug": "bug", "snail": "snail", "beetle": "beetle",
    "butterfly": "butterfly",
    # uncommon
    "baby_chick": "chick", "hatched_chick": "chick", "chick": "chick",
    "mouse2": "mouse", "mouse": "mouse",
    "rooster": "chicken", "chicken": "chicken",
    "rabbit2": "rabbit", "rabbit": "rabbit",
    "chipmunk": "chipmunk",
    # rare
    "sheep": "sheep", "pig2": "pig", "pig": "pig", "cow2": "cow", "cow": "cow",
    "dog2": "dog", "dog": "dog", "cat2": "cat", "cat": "cat",
    # epic
    "crocodile": "crocodile", "tiger2": "tiger", "tiger": "tiger",
    "penguin": "penguin", "elephant": "elephant", "whale": "whale", "whale2": "whale",
    # mythical
    "dragon_face": "dragon", "dragon": "dragon",
    "unicorn": "unicorn", "unicorn_face": "unicorn",
    "snowman2": "snowman", "snowman": "snowman",
    "ghost": "ghost", "dove": "dove", "dove_of_peace": "dove",
    # gem
    "camel": "camel", "fish": "fish", "panda_face": "panda", "panda": "panda",
    "shrimp": "shrimp", "spider": "spider",
    # legendary
    "deer": "deer", "fox": "fox", "fox_face": "fox",
    "lion": "lion", "lion_face": "lion", "owl": "owl", "squid": "squid",
    # fabled
    "boar": "boar", "eagle": "eagle", "frog": "frog", "gorilla": "gorilla",
    "wolf": "wolf",
    # hidden
    "koala": "koala", "lizard": "lizard", "monkey_face": "monkey", "monkey": "monkey",
    "snake": "snake", "octopus": "octopus",
}

# a zoo row looks like:  common   🐝¹⁰  🐛⁰⁵  ❓⁰⁰ ...
# the count is how many you own, and ❓ is a slot you have never caught.
#
# owo does not always render the count as superscript - components v2 cards put it in
# an inline code span (`🐝`10``) and some rows use plain trailing digits. A
# superscript-only group read those as "no count", which fell through to owned=1 and
# invented one of every animal in the row.
#
# The alternation order matters: `<a?:\w+:\d+>` has to be tried before `:\w+:` or a
# custom emoji would match from its second colon and lose its name.
ZOO_TOKEN_RE = re.compile(
    r'(?P<emoji><a?:\w+:\d+>|:\w+:|[\U0001F000-\U0001FAFF☀-➿⬀-⯿][️‍\U0001F000-\U0001FAFF]*)'
    r'(?:(?P<sup>[⁰¹²³⁴-⁹]+)|`\s*(?P<code>\d{1,4})\s*`|(?P<plain>\d{1,4})(?!\d))?'
)
SUPERSCRIPTS = {'⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
                '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'}
# rarest first so "uncommon" is never matched as "common", and so a tier word that
# contains another (e.g. "mythical" vs "mythic") is tried in the right order.
# Ranks are ordered by rarity (higher = rarer); values only need to be monotonic.
RARITY_RANK = (
    ("distorted", 12), ("hidden", 11), ("special", 10), ("fabled", 9),
    ("bot", 8), ("legendary", 7), ("gem", 6), ("mythical", 5), ("mythic", 5),
    ("patreon", 4), ("epic", 3), ("rare", 2), ("uncommon", 1), ("common", 0),
)
RANK_NAMES = {0: "common", 1: "uncommon", 2: "rare", 3: "epic", 4: "patreon",
              5: "mythical", 6: "gem", 7: "legendary", 8: "bot", 9: "fabled",
              10: "special", 11: "hidden", 12: "distorted"}
UNOWNED = ("❓", "❔", "question")

# animals whose tier we know without ever reading the zoo
STATIC_RANKS = {
    name: rank
    for tier, rank in RARITY_RANK
    for name in ZOO_TIERS.get(tier, ())
}


def emoji_key(emoji):
    """An emoji stripped of variation selectors, for dictionary lookups."""
    return ''.join(ch for ch in emoji if ch not in EMOJI_MODIFIERS)


def animal_from_token(token):
    """Animal name for one zoo/hunt token, whichever way owo drew it, or None.

    Deliberately returns None for anything it cannot name: the hunt watcher and the
    team reader feed this straight into rank lookups, so inventing a name there would
    put a nonexistent animal on the battle team.
    """
    custom = CUSTOM_EMOJI_RE.fullmatch(token or '')
    if custom:
        return custom.group(1).lower()
    short = SHORTCODE_RE.fullmatch(token or '')
    if short:
        return SHORTCODE_ANIMALS.get(short.group(1).lower())
    return UNICODE_ANIMALS.get(emoji_key(token or ''))


def parse_level_xp(content):
    """(level, xp, xp_needed) out of an owo reply - any of them may be None.

    Deliberately strict. Everything here feeds the dashboard, and the previous
    version happily reported an animal's level as the account level.
    """
    if "level" not in content and "lvl" not in content:
        return None, None, None
    if any(noise in content for noise in LEVEL_NOISE):
        return None, None, None

    xp = xp_needed = None
    xp_match = XP_LABELLED_RE.search(content) or XP_PAIR_RE.search(content)
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

    # an "N/M xp" bar with the unit spelled out only ever shows up on our own card
    anchor = min((content.find(p) for p in SELF_LEVEL_PHRASES if p in content), default=-1)
    if anchor == -1 and xp is None:
        return None, None, None

    # read the number next to the phrase, not the first "level N" anywhere in the
    # message - a level card also lists weapon and pet levels further down
    window = content[anchor:anchor + 80] if anchor != -1 else content
    level = None
    match = LEVEL_RE.search(window) or LEVEL_SUFFIX_RE.search(window)
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
        self._team = []
        self._want_level_until = 0.0
        self._level_image_warned = False
        self._level_parse_warned = 0.0
        # register_actions re-runs on every ready and on config changes, so the OCR
        # availability line is latched to one report per process
        self._ocr_state_logged = False
        self._handled_v2 = {}

    # ── level ────────────────────────────────────────────────────────────────

    def _store_level(self, level, xp, xp_needed, source="text", rank=None, card_url=None):
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
        if rank is not None:
            st['rank'] = rank
            changed.append(f"rank #{rank:,}")
        if level is not None or xp is not None:
            st['level_source'] = source
        if card_url:
            st['level_card_url'] = card_url
        elif source != "image":
            # a card we could read in text supersedes any picture still on the panel
            st.pop('level_card_url', None)
        if changed:
            st['last_level_update'] = time.time()
            state.save_account_stats()
            self.bot.log("INFO", f"OwO level synced: {', '.join(changed)}")

    async def _read_level_image(self, url):
        """OwO drew the level as a picture - OCR it instead of giving up.

        Downloads the attachment through the bot's own (proxy-aware) session and runs
        the reader in ``modules.level_ocr``. Returns the reader's dict, with any field
        that could not be read left as ``None``.
        """
        blank = {'level': None, 'xp': None, 'xp_needed': None, 'rank': None, 'text': ''}
        if not url:
            return blank
        session = getattr(self.bot, 'session', None)
        if session is None or getattr(session, 'closed', True):
            return blank
        try:
            # Discord's CDN 403s a request without a browser-ish User-Agent, so a
            # download that looked like an expiring link was really a rejected one.
            # discord.py-self's session already sends a client UA; state it anyway so
            # this does not depend on a library internal staying the way it is.
            async with session.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                              '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            }) as resp:
                if resp.status != 200:
                    # a silent `return` here made a 403 indistinguishable from an
                    # unreadable image, which is the whole reason this looked like
                    # "OCR does not work"
                    self.bot.log(
                        "WARN",
                        f"Level card download returned HTTP {resp.status} - the level "
                        "number cannot be read from it."
                    )
                    return blank
                data = await resp.read()
        except Exception as e:
            self.bot.log("ERROR", f"Level image download failed: {e}")
            return blank

        # OCR is CPU-bound and takes on the order of a second or two per card. Every
        # account shares one asyncio loop, so running it inline froze the *whole farm*
        # for that long - and for the full pass list it was closer to a minute.
        try:
            result = await asyncio.to_thread(parse_level_card, data)
        except Exception as e:
            self.bot.log("ERROR", f"Level card OCR failed: {e}")
            return blank
        if result.get('level') is not None or result.get('xp') is not None:
            self._level_image_warned = False
        return result

    def _ocr_card_is_mine(self, blob):
        """Does the OCR'd card text carry one of our own names?

        owo renders the account name onto the level card, so once OCR can read the card
        we can *confirm* ownership instead of relying only on "we asked for one a moment
        ago" - which is all an image-only payload otherwise offers, and which two farm
        accounts in the same channel can satisfy at the same time.
        """
        idents = {str(self.bot.user.name or '').lower(),
                  str(getattr(self.bot, 'display_name', '') or '').lower()}
        # owo prints the plain name, so a mention-style identifier is no use here
        for ident in getattr(self.bot, 'identifiers', []) or []:
            ident = str(ident).lower()
            if not ident.startswith('<@'):
                idents.add(ident)
        return any(ident and len(ident) >= 3 and ident in blob for ident in idents)

    async def _handle_level_image(self, card_url):
        """`owo level` answered with a picture. OCR it, and keep the picture regardless.

        The image is the only place the number exists now, so it is stored either way:
        the dashboard renders owo's own card, which is real data, instead of a blank
        KPI with nothing to show for it.
        """
        result = await self._read_level_image(card_url)
        blob = result.get('text') or ''

        # Only meaningful when OCR actually read something: a blank blob means the
        # engine is missing, not that the card belongs to somebody else.
        if blob and not self._ocr_card_is_mine(blob):
            # deliberately does not clear the window - another account's card must not
            # consume our pending request, or ours would be dropped when it arrives
            self.bot.log(
                "INFO",
                "Ignored an `owo level` card: OCR read the name on it and it is not ours."
            )
            return

        lvl, xpv = result.get('level'), result.get('xp')
        if lvl is not None or xpv is not None:
            self._want_level_until = 0.0
            self._store_level(lvl, xpv, result.get('xp_needed'), source="image",
                              rank=result.get('rank'), card_url=card_url)
        else:
            self._note_level_unreadable(card_url)

    def _note_level_unreadable(self, card_url=None):
        """last-resort: the image could not be OCRed either. Say so, don't guess."""
        self._want_level_until = 0.0
        st = self.bot.stats
        st['level_source'] = 'image'
        if card_url:
            st['level_card_url'] = card_url
            st['last_level_update'] = time.time()
            state.save_account_stats()
        if self._level_image_warned:
            return
        self._level_image_warned = True
        why = ocr_status()
        detail = f"OCR is unavailable because {why}" if why else "OCR could not read it"
        shown = "The card itself is shown on the dashboard" if card_url else "Nothing to show"
        self.bot.log(
            "WARN",
            f"OwO answered `owo level` with an image card and {detail}. "
            f"{shown}; the level number stays blank rather than being guessed."
        )

    def _note_level_unparsed(self, raw_text):
        """The reply is clearly a level card, but no number came out of it.

        The v2 path used to just fall through here, so a card owo had worded in a way
        the regexes miss left the dashboard blank with nothing in the log to say why.
        The window is deliberately left armed - this may be an unrelated card that
        merely mentions "level", and dropping the latch would lose the real reply.
        """
        if time.time() - self._level_parse_warned < 300:
            return
        self._level_parse_warned = time.time()
        sample = " ".join(raw_text.split())[:200]
        self.bot.log(
            "WARN",
            f"Could not read a level out of OwO's reply - dashboard left unchanged. Card text: {sample}"
        )

    # ── zoo / team ───────────────────────────────────────────────────────────

    def _team_cfg(self):
        return self.bot.config.get('commands', {}).get('team', {})

    def rank_of(self, animal):
        return self._animal_ranks.get(str(animal).lower(), -1)

    # ── read by the dashboard (/api/stats) ───────────────────────────────────

    @property
    def current_team(self):
        return list(self._team)

    @property
    def owned_count(self):
        return len(self._owned)

    @property
    def zoo_data(self):
        """Owned animals with their rarity tier, rarest-first, for the dashboard."""
        return [
            {'animal': animal, 'rarity': RANK_NAMES.get(rank, 'unknown'), 'rank': rank}
            for animal, rank in self._owned
        ]

    def rarity_name(self, animal):
        return RANK_NAMES.get(self.rank_of(animal), 'unknown')

    @staticmethod
    def _is_tier_badge(emoji):
        """True for the rarity badge owo prints in front of a zoo row.

        Matched by name rather than by position: a row where every animal is owned
        exactly once carries no superscripts at all, and the count heuristic below
        cannot tell badge from animal there - which let the badge through as a fake
        animal named "epictier" that `team add` would only ever reject.
        """
        custom = CUSTOM_EMOJI_RE.fullmatch(emoji or '') or SHORTCODE_RE.fullmatch(emoji or '')
        if not custom:
            return False
        name = custom.group(1).lower()
        if 'tier' in name:
            return True
        # a shortcode that names an animal is an animal, not a badge - ":dove:" would
        # otherwise be thrown away on the mythical row if owo ever renamed a tier
        if name in SHORTCODE_ANIMALS:
            return False
        return any(name == word or name == f"{word}s" for word, _rank in RARITY_RANK)

    @classmethod
    def _drop_tier_icon(cls, tokens, tier, tier_names):
        """The emoji in front of a zoo row is the tier badge, not an animal."""
        if not tokens:
            return tokens
        first_emoji, first_count = tokens[0][0], tokens[0][1]
        if cls._is_tier_badge(first_emoji):
            return tokens[1:]
        custom = CUSTOM_EMOJI_RE.fullmatch(first_emoji)
        if custom and tier in custom.group(1).lower():
            return tokens[1:]
        if tier_names and len(tokens) == len(tier_names) + 1:
            return tokens[1:]
        # a badge never carries an owned-count superscript, an animal usually does.
        # Only applies when the leading token is not a name we recognise: with
        # shortcodes now parsed, the first token of a row is usually a real animal
        # whose count owo happened to omit.
        if (not first_count and animal_from_token(first_emoji) is None
                and any(token[1] for token in tokens[1:])):
            return tokens[1:]
        return tokens

    def _name_for(self, emoji, tier_names, slot):
        """What `owo team add` should be handed for this zoo slot."""
        custom = CUSTOM_EMOJI_RE.fullmatch(emoji)
        if custom:
            # owo names every animal emoji after the animal, and this is the only
            # thing that works for legendary/fabled/hidden rows we have no list for
            return custom.group(1).lower()
        short = SHORTCODE_RE.fullmatch(emoji)
        if short:
            name = short.group(1).lower()
            mapped = SHORTCODE_ANIMALS.get(name)
            if mapped:
                return mapped
            # an unmapped shortcode still sits in a known slot of a known tier
            if slot < len(tier_names):
                return tier_names[slot]
            # ...and if the tier has no fixed list (event animals), the shortcode name
            # is what owo itself uses for the emoji, so it is the best handle we have
            return name
        known = UNICODE_ANIMALS.get(emoji_key(emoji))
        if known:
            return known
        if slot < len(tier_names):
            return tier_names[slot]
        # last resort: owo takes the animal emoji itself in `team add`. Dropping the
        # token here is exactly how the ultra-rare rows used to be ignored.
        return emoji_key(emoji)

    def parse_zoo(self, raw):
        """Animals you actually own, rarest first, named the way owo team add wants them."""
        found = []
        for line in raw.splitlines():
            # Keep the custom-emoji NAME when stripping the id, because OwO prints
            # the tier badge as an emoji whose *name* carries the tier word -
            # e.g. <:commonTier:123...>, <:legendaryTier:456...>. Replacing the
            # whole emoji with a space (the old code) erased "commonTier" along
            # with the id, so the tier word was gone and RARITY_RANK never matched,
            # which silently dropped every single animal - the whole zoo read as
            # empty and the dashboard showed "No zoo data yet".
            bare = CUSTOM_EMOJI_RE.sub(r' \1 ', line).replace('*', '').replace('`', '').lower()
            match = next(((word, rank) for word, rank in RARITY_RANK if word in bare), None)
            if match is None:
                continue
            tier, rank = match
            tier_names = ZOO_TIERS.get(tier, ())

            tokens = []
            for token in ZOO_TOKEN_RE.finditer(line):
                # normalise the count to plain ascii digits here, whichever way owo
                # rendered it, so everything downstream (including _drop_tier_icon's
                # "did this token carry a count" test) sees one shape
                sup = token.group('sup')
                if sup:
                    count = ''.join(SUPERSCRIPTS[ch] for ch in sup)
                else:
                    count = token.group('code') or token.group('plain') or ''
                # keep what owo printed straight after the emoji: an unowned marker can
                # sit there rather than replacing the animal, and under "no superscript
                # means one copy" that slot would otherwise read as an animal we own
                tail = line[token.end():token.end() + 2]
                tokens.append((token.group('emoji'), count, tail))
            tokens = self._drop_tier_icon(tokens, tier, tier_names)

            for slot, (emoji, count, tail) in enumerate(tokens):
                # no count at all means owo printed a single copy, not zero.
                # Requiring one is what made whole rows vanish from the parse.
                owned = int(count) if count else 1
                if owned == 0 or any(marker in emoji or marker in tail for marker in UNOWNED):
                    continue

                animal = self._name_for(emoji, tier_names, slot)
                if not animal:
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
            for token in ZOO_TOKEN_RE.finditer(raw):
                # unicode glyph or ":shortcode:" - the team card uses the same
                # rendering as the zoo row it was picked from
                known = animal_from_token(token.group('emoji'))
                if known:
                    names.append(known)
        if not names:
            # a text-only team listing quotes the names instead
            names = [n.lower() for n in re.findall(r'`\s*([a-z][a-z_\- ]{1,20})\s*`', raw, re.IGNORECASE)]
        seen = []
        for name in names:
            if name in UNOWNED or name in seen:
                continue
            # the rarity badge owo prints in front of a team row (<:LegendaryTier:..>)
            # is not an animal - drop it so `team add` never gets handed "legendarytier"
            if self._is_tier_badge(f"<:{name}:0>"):
                continue
            seen.append(name)
        return seen

    def _weakest_team_rank(self):
        if not self._team:
            return -1
        return min(self.rank_of(animal) for animal in self._team)

    async def request_team_check(self, reason=""):
        """Read the zoo, then the team, so we can swap in anything rarer."""
        cfg = self._team_cfg()
        if not cfg.get('enabled', True):
            return
        cooldown = max(20, int(cfg.get('min_action_gap_s', 45) or 45))
        if time.time() - self._last_team_action < cooldown:
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

        if not missing:
            best_rank = RANK_NAMES.get(self.rank_of(wanted[0]), '?') if wanted else '?'
            self.bot.log("TEAM", f"Team already holds the rarest animals we own (top tier: {best_rank})")
            return

        # hysteresis: only churn the team when the swap actually buys rarity. Without
        # it a tie inside one tier makes every scan remove and re-add the same animals.
        if len(current) >= slots:
            gain = max(self.rank_of(animal) for animal in missing)
            weakest = min(self.rank_of(animal) for animal in current)
            if gain <= weakest:
                self.bot.log(
                    "TEAM",
                    f"Nothing rarer than the current {RANK_NAMES.get(weakest, '?')} slot - team left alone"
                )
                return

        free_slots = [i + 1 for i, animal in enumerate(current) if animal not in keep]
        free_slots += list(range(len(current) + 1, slots + 1))

        replaced = [
            animal for i, animal in enumerate(current)
            if (i + 1) in free_slots and animal
        ]

        # drop the weak ones from the highest slot down so the lower indexes stay valid
        for slot in sorted([s for s in free_slots if s <= len(current)], reverse=True):
            await self.bot.neura_enqueue(remove_template.format(slot=slot), priority=3)

        added = []
        for animal, slot in zip(missing, free_slots):
            await self.bot.neura_enqueue(add_template.format(animal=animal, slot=slot), priority=3)
            added.append(animal)
            self.bot.log(
                "TEAM",
                f"Team slot {slot}: adding {animal} ({RANK_NAMES.get(self.rank_of(animal), 'unknown')})"
            )

        if replaced:
            self.bot.log("TEAM", f"Dropped {', '.join(replaced)} for rarer animals")

        # remember what the team should look like so the zoo watcher has something to
        # compare a fresh catch against before the next full scan
        self._team = (keep + added)[:slots]

    def _animals_in(self, raw, content):
        """Animal names a hunt result mentions, by emoji or by name."""
        names = set()
        for name in CUSTOM_EMOJI_RE.findall(raw):
            names.add(name.lower())
        for token in ZOO_TOKEN_RE.finditer(raw):
            # covers unicode glyphs AND the ":shortcode:" text owo actually sends -
            # without the shortcode branch a hunt drawn that way named no animal at
            # all, so the watcher fell back to the tier hint and missed every catch
            # whose tier word owo did not spell out
            known = animal_from_token(token.group('emoji'))
            if known:
                names.add(known)
        for word in re.findall(r'\*\*\s*([a-z][a-z_\- ]{1,20})\s*\*\*', content):
            candidate = word.strip()
            if candidate in self._animal_ranks:
                names.add(candidate)
        return names

    async def _watch_hunt(self, message, content):
        """A catch rarer than the weakest team slot earns an immediate re-check.

        This is the zoo watcher: a hunt result is the exact moment the zoo changes,
        so there is no reason to poll for it.
        """
        cfg = self._team_cfg()
        if not cfg.get('enabled', True) or not cfg.get('watch_zoo', True):
            return

        slots = max(1, int(cfg.get('slots', 3) or 3))
        weakest = self._weakest_team_rank()
        # owo prints the rarity next to the catch, which covers animals we have
        # never seen and therefore cannot rank ourselves
        tier_hint = next((rank for word, rank in RARITY_RANK if word in content), -1)

        best_rank, best_name = -1, None
        for name in self._animals_in(message.content or "", content):
            rank = self.rank_of(name)
            if rank < 0:
                rank = tier_hint
            if rank > best_rank:
                best_rank, best_name = rank, name

        if best_name is None:
            if tier_hint > weakest and len(self._team) >= slots:
                await self.request_team_check(f"caught a {RANK_NAMES.get(tier_hint, '?')} animal")
            return

        if best_name in self._team:
            return
        if len(self._team) >= slots and best_rank <= weakest:
            return

        await self.request_team_check(
            f"caught {best_name} ({RANK_NAMES.get(best_rank, 'unknown')})"
        )

    async def _handle_zoo(self, raw, content):
        self.zoo = False
        self._owned = self.parse_zoo(raw)
        self._owned_at = time.time()

        if not self._owned:
            self.bot.log(
                "WARN",
                f"Zoo read but no animals could be parsed out of it - zoo panel and team "
                f"left unchanged. Card text: {' '.join(str(raw).split())[:200]}"
            )
            self._want_team_check = False
            return

        top = ", ".join(f"{a} ({RANK_NAMES.get(r, '?')})" for a, r in self._owned[:3])
        self.bot.log("TEAM", f"Zoo read: {len(self._owned)} animals owned, rarest are {top}")

        if self._want_team_check:
            await self.bot.neura_enqueue("team", priority=3, _cmd_id="team")

    async def _handle_team(self, raw):
        self._want_team_check = False
        current = self.parse_team(raw)
        self._team = current
        self.bot.log("TEAM", f"Current team: {', '.join(current) if current else 'empty'}")
        await self._apply_team_upgrade(current)
        weapons = self.bot.get_cog('Weapons')
        if weapons:
            weapons.note_team(self._team or [animal for animal, _r in self._owned[:3]])

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

    # ── components v2 ────────────────────────────────────────────────────────

    def _v2_is_mine(self, full_text):
        """components v2 messages are invisible to discord.py-self, so match by name."""
        if f"<@{self.bot.user.id}>" in full_text or f"<@!{self.bot.user.id}>" in full_text:
            return True
        idents = {self.bot.user.name.lower(), (self.bot.display_name or "").lower()}
        for ident in getattr(self.bot, 'identifiers', []):
            idents.add(ident.replace("<@", "").replace("!", "").replace(">", "").lower())
        return any(ident and len(ident) >= 2 and ident in full_text for ident in idents)

    def _seen_v2(self, data):
        """True the second time the same v2 message reaches us (owo edits its cards)."""
        key = str(data.get("id"))
        stamp = f"{data.get('edited_timestamp') or ''}|{len(data.get('components') or [])}"
        if self._handled_v2.get(key) == stamp:
            return True
        if len(self._handled_v2) > 60:
            self._handled_v2.clear()
        self._handled_v2[key] = stamp
        return False

    @commands.Cog.listener()
    async def on_socket_raw_receive(self, msg):
        """`owo level`, `owo zoo` and `owo team` now answer with components v2 cards.

        discord.py-self models none of that, so message.content is empty and the
        on_message path below never sees them - hence reading the raw payload.
        """
        # discord.py-self zlib-decompresses binary frames before dispatching
        # socket_raw_receive, so we normally get a str. Decode defensively anyway -
        # the old `isinstance(msg, bytes): return` would have silently thrown away
        # every large V2 card (zoo/team/level) if a future change ever passed bytes
        # through, which is exactly the "panels always empty" failure mode.
        if isinstance(msg, (bytes, bytearray)):
            try:
                msg = msg.decode('utf-8', errors='replace')
            except Exception:
                return
        if not getattr(self.bot, 'is_ready', False):
            return
        try:
            raw_data = json.loads(msg)
        except Exception:
            return
        if raw_data.get("t") not in ("MESSAGE_CREATE", "MESSAGE_UPDATE"):
            return

        data = raw_data.get("d") or {}
        if str((data.get("author") or {}).get("id")) != self.bot.owo_bot_id:
            return
        if str(data.get("channel_id")) not in [str(c) for c in self.bot.channels]:
            return

        components = parse_v2_message(data)
        if not components:
            return

        raw_text = f"{data.get('content') or ''}\n{collect_text(components)}"
        content = raw_text.lower()

        # An image-only card carries no text whatsoever - `owo level` is one
        # media_gallery holding level.png, with content "", embeds [] and (this is the
        # part that used to defeat the OCR branch) an empty `attachments` list too.
        # There is therefore no username in it to match, so _v2_is_mine below can only
        # ever answer "not mine" and the reply was dropped before anything looked at it.
        # Attribute it the only way actually available: we asked for it seconds ago (the
        # level window is open), it arrived in one of our channels, and owo named the
        # picture after the command it answers. The window is closed by the handler so a
        # single request can only ever claim one card.
        if time.time() <= self._want_level_until and is_image_only(components):
            card_url = media_image_url(components, name_contains=("level", "profile"))
            if card_url:
                if self._seen_v2(data):
                    return
                try:
                    await self._handle_level_image(card_url)
                except Exception as e:
                    self.bot.log("ERROR", f"Level image card handling failed: {e}")
                return

        if not self._v2_is_mine(content):
            return

        try:
            if self.zoo and "zoo" in content:
                if self._seen_v2(data):
                    return
                await self._handle_zoo(raw_text, content)
                return

            if self._want_team_check and ("'s team" in content or "battle team" in content):
                if self._seen_v2(data):
                    return
                await self._handle_team(raw_text)
                return

            if time.time() <= self._want_level_until:
                level, xp, xp_needed = parse_level_xp(content)
                if level is not None or xp is not None:
                    self._want_level_until = 0.0
                    self._store_level(level, xp, xp_needed, source="v2")
                elif data.get("attachments"):
                    # OwO rendered the level as an image card - download and OCR it
                    att = data["attachments"][0]
                    img_url = att.get("proxy_url") or att.get("url")
                    lvl, xpv, xpn, rank = await self._read_level_image(img_url)
                    if lvl is not None or xpv is not None:
                        self._want_level_until = 0.0
                        self._store_level(lvl, xpv, xpn, source="image", rank=rank)
                    else:
                        self._note_level_unreadable()
                elif ("level" in content or "lvl" in content) and not any(
                        noise in content for noise in LEVEL_NOISE):
                    self._note_level_unparsed(raw_text)
        except Exception as e:
            self.bot.log("ERROR", f"V2 card handling failed: {e}")

    # ── plain messages ───────────────────────────────────────────────────────

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

        if any(phrase in content for phrase in BAD_ANIMAL_PHRASES) and "team" in content:
            self.bot.log("WARN", f"OwO rejected a team change: {content.splitlines()[0][:120]}")
            return

        level, xp, xp_needed = parse_level_xp(content)
        if level is not None or xp is not None:
            if not self.bot.is_message_for_me(message, role="header") and not self.bot.is_message_for_me(message):
                self.bot.log("DEBUG", f"Level reply ignored, not recognised as mine: {content.splitlines()[0][:80]}")
                return
            self._want_level_until = 0.0
            self._store_level(level, xp, xp_needed)
            return

        # `owo level` answered with an image card - OCR it instead of leaving the
        # old number sitting there as if it were fresh
        if (time.time() <= self._want_level_until and message.attachments
                and self.bot.is_message_for_me(message, role="header")):
            att = message.attachments[0]
            img_url = getattr(att, 'proxy_url', None) or getattr(att, 'url', None)
            lvl, xpv, xpn, rank = await self._read_level_image(img_url)
            if lvl is not None or xpv is not None:
                self._want_level_until = 0.0
                self._store_level(lvl, xpv, xpn, source="image", rank=rank)
            else:
                self._note_level_unreadable()
            return

        if any(phrase in content for phrase in HUNT_PHRASES):
            if self.bot.is_message_for_me(message):
                await self._watch_hunt(message, content)
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
            # `content` (get_full_content) not message.content: owo renders the zoo as an
            # embed, so message.content is an empty string and the parser was handed
            # nothing to read - every legacy-path zoo scan found zero animals.
            await self._handle_zoo(content, content)
            return

        # the old "add not in content" guard skipped a legitimate team listing whenever
        # the word appeared anywhere in it; the pending flag already scopes this
        if self._want_team_check and ("'s team" in content or "battle team" in content):
            if not self.bot.is_message_for_me(message, role="header"):
                return
            # same embed problem as the zoo card above
            await self._handle_team(content)
            return

        # Last resort, and deliberately last: the level window is open and this reply
        # mentions a level, yet none of the branches above claimed it. Say so instead of
        # dropping it silently. Placed after hunt/zoo/team so it can never shadow them.
        if (time.time() <= self._want_level_until and ("level" in content or "lvl" in content)
                and not any(noise in content for noise in LEVEL_NOISE)
                and self.bot.is_message_for_me(message, role="header")):
            self._note_level_unparsed(content)
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
            # owo answers `owo level` with a rendered picture, so whether the number
            # ever reaches the dashboard depends entirely on OCR being present. Say
            # which it is at startup instead of leaving a silently blank KPI.
            if not self._ocr_state_logged:
                self._ocr_state_logged = True
                why = ocr_status()
                if why:
                    self.bot.log(
                        "WARN",
                        f"Level OCR is off: {why}. The dashboard will show OwO's level "
                        "card as a picture instead of the number."
                    )
                else:
                    self.bot.log("SYS", f"Level OCR ready ({ocr_engine_path()}).")
        if cfg.get('zoo', True):
            await self.bot.neura_register_command(
                "zoo_sync",
                self._zoo_sync_tick,
                priority=self.bot.get_cmd_priority("zoo_sync", 5),
                delay=max(600, int(cfg.get('zoo_interval_s', 1800))),
                initial_offset=60,
            )
        else:
            self.bot.cmd_states.pop('zoo_sync', None)

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
            watching = "on" if team_cfg.get('watch_zoo', True) else "off"
            self.bot.log(
                "SYS",
                f"Team Manager configured (zoo re-check every {interval // 60}m, catch watcher {watching})."
            )
        else:
            self.bot.cmd_states.pop('team_scan', None)

    def trigger_action(self):
        """Post-send hook for level_sync - arm the level parser now the read is out.

        The window used to be opened by the scheduler hook at *enqueue* time, but a
        priority-4 command can sit in the queue behind min_command_interval, a stealth
        human break or a captcha pause for much longer than the window lasted. The reply
        then arrived unarmed, was dropped, and nothing retried it for level_interval_s.
        """
        self._want_level_until = time.time() + 180

    async def _zoo_sync_tick(self):
        """Arm the zoo parser for the dashboard, then let the queue send the read.

        Deliberately independent of commands.team.enabled. The zoo panel is fed from
        _handle_zoo, which only runs while self.zoo is set, and the only thing that used
        to set it was the team manager - so with team management off nothing ever sent
        `owo zoo` and the panel stayed empty for good.

        self.zoo is a latch rather than a deadline, so arming it at enqueue time is safe:
        it stays set until a zoo card actually clears it.
        """
        self.zoo = True
        return "owo zoo"

    async def _team_scan_tick(self):
        """Timer hook - kicks off a zoo read and returns None so nothing is sent twice."""
        await self.request_team_check("scheduled check")
        return None


async def setup(bot):
    cog = Others(bot)
    await bot.add_cog(cog)
