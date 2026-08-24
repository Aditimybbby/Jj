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

"""Read the level/XP/rank that OwO renders as a *picture* on `owo level`.

OwO no longer prints ``you are level N`` in text - it replies with a rendered
profile card (an image attachment). The dashboard used to give up and show
"image card - unreadable", leaving the level blank forever.

This module downloads that attachment, runs Tesseract OCR over several
pre-processing passes (the card has a purple/orange gradient that defeats a
single pass), and pulls out three fields with tolerant regex:

* ``level``      - the big ``LVL N`` number
* ``xp``         - ``XP: 3,497/5,049`` -> (current, needed)
* ``rank``       - ``Rank: #50,083,276``

Any field that cannot be read is returned as ``None`` rather than guessed.
"""

import io
import re

try:
    from PIL import Image, ImageOps, ImageEnhance
    _PIL = True
except Exception:  # pragma: no cover - pillow is in requirements but be safe
    _PIL = False

try:
    import pytesseract
    _TESS = True
except Exception:  # pragma: no cover
    _TESS = False


# ── regexes ─────────────────────────────────────────────────────────────────
# Tesseract garbles the stylised "LVL" label ("vL0", "LVL 9}", "tvLO"), so we
# tolerate spaces / junk between the letters and the number.
LEVEL_RE = re.compile(r'l\s*v\s*l\D{0,8}(\d{1,4})', re.IGNORECASE)
XP_RE = re.compile(r'(?:xp|exp)\D{0,4}([\d,]+)\D{0,3}/\D{0,3}([\d,]+)', re.IGNORECASE)
RANK_RE = re.compile(r'rank\D{1,5}#?\s*([\d, ]{4,20})', re.IGNORECASE)


def _available():
    return _PIL and _TESS


def _prep(img, scale, contrast, invert, thr):
    """grayscale -> autocontrast -> contrast boost -> optional invert/threshold."""
    base = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    g = ImageOps.grayscale(base)
    g = ImageOps.autocontrast(g)
    g = ImageEnhance.Contrast(g).enhance(contrast)
    if invert:
        g = ImageOps.invert(g)
    if thr is not None:
        g = g.point(lambda x, t=thr: 255 if x > t else 0, 'L')
    return g


def _ocr_all(img):
    """Yield every OCR string we can squeeze out of the card."""
    seen = set()
    for scale in (3, 4):
        for contrast in (2.0, 3.0):
            for invert in (False, True):
                for thr in (None, 140, 160, 180):
                    for psm in (6, 11):
                        try:
                            g = _prep(img, scale, contrast, invert, thr)
                            text = pytesseract.image_to_string(g, config=f'--psm {psm}')
                        except Exception:
                            continue
                        if text and text not in seen:
                            seen.add(text)
                            yield text


def _best_int(raw, lo=None, hi=None):
    if raw is None:
        return None
    try:
        val = int(str(raw).replace(',', '').replace(' ', ''))
    except ValueError:
        return None
    if lo is not None and val < lo:
        return None
    if hi is not None and val > hi:
        return None
    return val


def parse_level_card(image_bytes):
    """(level, xp, xp_needed, rank) from a raw OwO level-card image.

    Returns ``(None, None, None, None)`` if OCR is unavailable or nothing can be
    read - the caller then falls back to the old "unreadable" note.
    """
    if not _available():
        return None, None, None, None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    except Exception:
        return None, None, None, None

    blob = "\n".join(_ocr_all(img))

    # level - the rarest field, but the regex tolerates tesseract's mangling of
    # the stylised "LVL" label. Take the first sane hit.
    level = None
    for m in LEVEL_RE.finditer(blob):
        cand = _best_int(m.group(1), lo=0, hi=9999)
        if cand is not None:
            level = cand
            break

    # xp - very reliable, the small text reads cleanly
    xp = xp_needed = None
    xm = XP_RE.search(blob)
    if xm:
        xp = _best_int(xm.group(1), lo=0)
        xp_needed = _best_int(xm.group(2), lo=1)
        if xp is not None and xp_needed is not None and xp > xp_needed * 50:
            xp = xp_needed = None

    # rank - cosmetic, stored for the dashboard
    rank = None
    rm = RANK_RE.search(blob)
    if rm:
        rank = _best_int(rm.group(1))

    return level, xp, xp_needed, rank
