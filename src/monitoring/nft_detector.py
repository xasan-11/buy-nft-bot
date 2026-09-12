from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .price_parser import extract_price_stars

# t.me/nft/<slug> and tg://nft?slug=<slug> are Telegram's own deep-link
# formats for a specific collectible gift (see core.telegram.org/api/links
# and core.telegram.org/api/gifts). The slug maps 1:1 to a real gift via
# payments.getUniqueStarGift / inputInvoiceStarGiftResale, which is what
# makes it a *reliable* identifier — unlike a free-text gift name or emoji,
# which cannot be resolved back to a specific on-chain listing.
_SLUG_PATTERN = re.compile(
    r"(?:https?://)?t\.me/nft/([A-Za-z0-9_\-]+)|tg://nft\?slug=([A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)


@dataclass
class ParsedListing:
    slug: str
    price_stars: Optional[int]
    source_text: str


def detect_listing(text: str) -> Optional[ParsedListing]:
    """Modular NFT/Gift detector.

    This is intentionally a single, reliable strategy: only a direct link to
    a unique gift is accepted as identification. Posts that merely mention a
    gift by name/emoji without a resolvable link are skipped rather than
    guessed at, per "if the NFT/Gift cannot be reliably identified, DO NOT
    purchase it." Additional channel-specific strategies can be added here
    later as further functions chained in front of this one.
    """
    if not text:
        return None

    match = _SLUG_PATTERN.search(text)
    if not match:
        return None

    slug = match.group(1) or match.group(2)
    if not slug:
        return None

    price = extract_price_stars(text)
    return ParsedListing(slug=slug, price_stars=price, source_text=text)
