from __future__ import annotations

from dataclasses import dataclass

from telethon.errors import RPCError
from telethon.tl import functions, types


class ListingUnavailable(Exception):
    """Raised when Telegram reports the resale listing no longer exists or
    is no longer purchasable (already sold, delisted, invalid slug, etc.)."""


@dataclass
class VerifiedListing:
    form_id: int
    invoice: object
    price_stars: int


async def verify_and_price_listing(client, slug: str) -> VerifiedListing:
    """Confirms a resale listing is still real and returns its live price.

    Calls payments.getPaymentForm with an inputInvoiceStarGiftResale built
    from the slug extracted from the channel post. This is the official,
    documented flow for buying a gift currently on resale
    (core.telegram.org/api/gifts, core.telegram.org/method/payments.getPaymentForm).
    It does not charge anything by itself — Telegram only builds a payment
    form for a listing that is still valid, which is exactly the
    "verify listing still exists / still available" step of the pipeline.
    The price is read back from the form's invoice rather than trusted from
    the channel post, since the post price can be stale.
    """
    invoice_input = types.InputInvoiceStarGiftResale(
        slug=slug, to_id=types.InputPeerSelf()
    )
    try:
        form = await client(functions.payments.GetPaymentFormRequest(invoice=invoice_input))
    except RPCError as e:
        raise ListingUnavailable(str(e)) from e

    price = sum(p.amount for p in form.invoice.prices)
    return VerifiedListing(form_id=form.form_id, invoice=invoice_input, price_stars=price)
