from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Callable, Optional

from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions

from .listing import ListingUnavailable, verify_and_price_listing


@dataclass
class PurchaseOutcome:
    success: bool
    price_stars: Optional[int] = None
    error: Optional[str] = None


async def execute_purchase(
    client,
    slug: str,
    max_price: int,
    is_monitoring_running: Callable,
) -> PurchaseOutcome:
    """Runs the verify -> re-check -> pay steps of the purchase pipeline for
    one listing.

    `is_monitoring_running` is re-invoked immediately before the
    money-moving SendStarsFormRequest call (sync or async callable), so a
    /stop issued while this coroutine was already in flight still prevents
    the purchase from completing.
    """
    try:
        verified = await verify_and_price_listing(client, slug)
    except ListingUnavailable as e:
        return PurchaseOutcome(success=False, error=f"listing unavailable: {e}")

    if verified.price_stars > max_price:
        return PurchaseOutcome(
            success=False,
            price_stars=verified.price_stars,
            error="price exceeds maximum after re-verification",
        )

    running = is_monitoring_running()
    if inspect.isawaitable(running):
        running = await running
    if not running:
        return PurchaseOutcome(
            success=False,
            price_stars=verified.price_stars,
            error="monitoring stopped before purchase",
        )

    try:
        await client(
            functions.payments.SendStarsFormRequest(
                form_id=verified.form_id, invoice=verified.invoice
            )
        )
    except FloodWaitError as e:
        return PurchaseOutcome(
            success=False,
            price_stars=verified.price_stars,
            error=f"flood wait: must wait {e.seconds}s",
        )
    except RPCError as e:
        return PurchaseOutcome(success=False, price_stars=verified.price_stars, error=str(e))

    return PurchaseOutcome(success=True, price_stars=verified.price_stars)
