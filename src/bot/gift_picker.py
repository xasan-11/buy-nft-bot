from __future__ import annotations

from dataclasses import dataclass

PAGE_SIZE = 8
FALLBACK_EMOJI = "🎁"


@dataclass
class GiftOption:
    id: int
    title: str
    stars: int
    emoji: str


def _extract_emoji(sticker) -> str:
    """A gift type's sticker carries its own emoji as a
    DocumentAttributeSticker.alt (e.g. "🍪" for a cookie) — this is the
    "gift o'zining emoji/belgisi" the picker shows instead of ever sending
    the sticker itself as media (which was hitting DocumentInvalidError)."""
    for attr in getattr(sticker, "attributes", None) or []:
        alt = getattr(attr, "alt", None)
        if alt:
            return alt
    return FALLBACK_EMOJI


def resalable_gift_options(gifts) -> list:
    """Which gift *types* are worth offering on right now: only ones that
    currently have at least one instance listed for resale (same signal
    market_monitor.resalable_gift_ids already uses) — selecting a type with
    zero resale listings would never match anything."""
    return [
        GiftOption(
            id=g.id,
            title=getattr(g, "title", None) or f"Gift #{g.id}",
            stars=g.stars,
            emoji=_extract_emoji(getattr(g, "sticker", None)),
        )
        for g in gifts
        if getattr(g, "availability_resale", None)
    ]


def total_pages(option_count: int, page_size: int = PAGE_SIZE) -> int:
    if option_count <= 0:
        return 1
    return (option_count - 1) // page_size + 1


def paginate(options: list, page: int, page_size: int = PAGE_SIZE) -> list:
    start = page * page_size
    return options[start:start + page_size]


def toggle_selection(selected: set, gift_id: int) -> set:
    """Pure toggle — returns a new set rather than mutating, so a test (or
    caller) never has to guess whether the input set was reused."""
    updated = set(selected)
    if gift_id in updated:
        updated.discard(gift_id)
    else:
        updated.add(gift_id)
    return updated


def gift_button_text(emoji: str, title: str, stars: int, selected: bool) -> str:
    mark = "✅" if selected else "⬜"
    return f"{mark} {emoji} {title} — {stars}⭐"


def page_caption(page: int, pages: int, selected_count: int) -> str:
    return f"📄 Sahifa {page + 1}/{pages} — {selected_count} ta tanlangan"
