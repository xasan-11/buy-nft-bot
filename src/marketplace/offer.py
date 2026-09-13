from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions, types


@dataclass
class OfferOutcome:
    success: bool
    offer_id: Optional[int] = None
    error: Optional[str] = None
    balance_too_low: bool = False


async def send_offer(
    client, owner_peer, slug: str, price_stars: int, duration_seconds: int
) -> OfferOutcome:
    """Sends a purchase offer for a resale gift.

    Calls payments.sendStarGiftOffer (core.telegram.org/method/payments.sendStarGiftOffer),
    the official, documented flow for proposing a price on a unique gift
    whose owner has offers enabled (starGiftUnique.offer_min_stars set).
    Telegram reserves price_stars from this account's balance for the
    lifetime of the offer and refunds it automatically if the owner
    declines or lets it expire — this function only reports whether the
    offer was accepted for sending, not whether the seller later accepts it.

    `offer_id` returned here is a locally-generated random_id used only for
    our own bookkeeping (star_gift_offers table) and de-duplication — it is
    not something the seller's payments.resolveStarGiftOffer call needs, so
    there's no need to recover Telegram's own service-message id.
    """
    random_id = int.from_bytes(os.urandom(8), "big", signed=True)
    try:
        await client(
            functions.payments.SendStarGiftOfferRequest(
                peer=owner_peer,
                slug=slug,
                price=types.StarsAmount(amount=price_stars, nanos=0),
                duration=duration_seconds,
                random_id=random_id,
            )
        )
    except FloodWaitError as e:
        return OfferOutcome(success=False, offer_id=random_id, error=f"flood wait: must wait {e.seconds}s")
    except RPCError as e:
        # BALANCE_TOO_LOW isn't a registered Telethon error class (no
        # BalanceTooLowError exists), so Telethon falls back to raising a
        # generic RPCError whose .message is the raw string Telegram sent —
        # read that directly rather than parsing str(e)'s formatted
        # "RPCError 400: BALANCE_TOO_LOW (caused by ...)" text, since that
        # formatting is an implementation detail we shouldn't depend on.
        server_message = (getattr(e, "message", None) or str(e)).upper()
        return OfferOutcome(
            success=False,
            offer_id=random_id,
            error=str(e),
            balance_too_low="BALANCE_TOO_LOW" in server_message,
        )

    return OfferOutcome(success=True, offer_id=random_id)
