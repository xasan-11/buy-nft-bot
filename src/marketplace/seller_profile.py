from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from telethon.errors import RPCError
from telethon.tl import functions, types


@dataclass
class SellerProfile:
    level: int
    gift_count: int
    username: Optional[str] = None
    user_id: Optional[int] = None

    @property
    def profile_link(self) -> Optional[str]:
        """The best link to the seller's own Telegram profile: their public
        @username if they have one, otherwise a tg://user?id= deep link (only
        meaningful for user owners — a channel without a username has no
        equivalent, so this is None in that case, and the offer-sent
        notification simply omits the "profilga o'tish" button)."""
        if self.username:
            return f"https://t.me/{self.username}"
        if self.user_id:
            return f"tg://user?id={self.user_id}"
        return None


async def get_seller_profile(client, owner_peer) -> Optional[SellerProfile]:
    """Looks up the resale listing owner's Stars profile level, displayed
    gift count, and public identity — everything the offer-eligibility
    filter and the offer-sent notification need.

    `level` comes from users.getFullUser's UserFull.stars_rating.level (see
    core.telegram.org/type/StarsRating): Telegram's own account level, which
    increases as the account spends more Stars. There is no equivalent
    "level" for channels — a channel owner is reported as level 0, which
    only passes the filter if an admin explicitly sets /setofferlevel 0.

    `gift_count` comes from UserFull/ChatFull.stargifts_count — the number
    of gifts the owner currently has on display on their profile. This is
    the same "how many gifts does this seller already hold" the spec asks
    for, read directly rather than via a separate getSavedStarGifts call.

    `username`/`user_id` come from the `users`/`chats` list every full-info
    response carries alongside the full object itself — used to build a
    clickable link to the seller's profile (see `profile_link`).

    Returns None (fail closed — never send an offer) if the owner's profile
    can't be resolved at all (e.g. privacy settings, deleted account).
    """
    try:
        if isinstance(owner_peer, types.PeerChannel):
            result = await client(functions.channels.GetFullChannelRequest(channel=owner_peer))
            full = result.full_chat
            channel = next((c for c in result.chats if getattr(c, "id", None) == full.id), None)
            return SellerProfile(
                level=0,
                gift_count=getattr(full, "stargifts_count", None) or 0,
                username=getattr(channel, "username", None),
                user_id=None,
            )

        result = await client(functions.users.GetFullUserRequest(id=owner_peer))
        full = result.full_user
        rating = getattr(full, "stars_rating", None)
        level = rating.level if rating is not None else 0
        gift_count = getattr(full, "stargifts_count", None) or 0
        user = next((u for u in result.users if getattr(u, "id", None) == full.id), None)
        return SellerProfile(
            level=level,
            gift_count=gift_count,
            username=getattr(user, "username", None),
            user_id=full.id,
        )
    except RPCError:
        return None
