from __future__ import annotations

import re
from typing import Optional

# A price explicitly labeled ("Price: 125 stars") is trusted over a bare
# number next to a star emoji, since posts often contain several numbers
# (gift number/edition, previous price, etc.) that are not the sale price.
# Note: \b only applies after the word-forming alternatives (stars/xtr) — a
# trailing \b right after the ⭐ emoji would never match, since neither the
# emoji nor whatever follows it (space/end-of-string) is a word character,
# so there is never a word/non-word transition at that position.
# (?<![\d.,]) stops a match from starting in the middle of a larger dotted
# or decimal-looking number (e.g. the "250" tail of "1.250"), which would
# otherwise silently misparse an unparseable number as a clean integer.
_LABELED_PATTERN = re.compile(
    r"(?:price|cost|buy(?:\s+for)?|sale)\s*[:\-]?\s*(?<![\d.,])(\d[\d,]*)\s*(?:⭐|stars?\b|xtr\b)",
    re.IGNORECASE,
)
_BARE_STAR_PATTERN = re.compile(
    r"(?<![\d.,])(\d[\d,]*)\s*(?:⭐|stars?\b|xtr\b)", re.IGNORECASE
)


def _to_int(raw: str) -> Optional[int]:
    cleaned = raw.strip().replace(",", "")
    if not cleaned.isdigit():
        # Stars are always whole numbers; anything else is not reliably
        # parseable (e.g. a stray decimal point) so it is treated as unknown.
        return None
    return int(cleaned)


def extract_price_stars(text: str) -> Optional[int]:
    """Extracts a Stars price from free-form post text.

    Returns None whenever the price cannot be determined *unambiguously* —
    per spec, an unknown price must never be treated as eligible for
    purchase.
    """
    if not text:
        return None

    labeled_raw = {m.group(1) for m in _LABELED_PATTERN.finditer(text)}
    labeled_values = {v for v in (_to_int(r) for r in labeled_raw) if v is not None}
    if labeled_values:
        if len(labeled_values) == 1:
            return next(iter(labeled_values))
        return None  # conflicting labeled prices, do not guess

    bare_raw = {m.group(1) for m in _BARE_STAR_PATTERN.finditer(text)}
    bare_values = {v for v in (_to_int(r) for r in bare_raw) if v is not None}
    if len(bare_values) == 1:
        return next(iter(bare_values))
    return None
