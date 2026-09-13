from __future__ import annotations

from typing import Optional

from telethon.errors import RPCError
from telethon.tl import functions, types


async def get_stars_balance(client) -> Optional[int]:
    """Reads this account's current Telegram Stars balance via
    payments.getStarsStatus (core.telegram.org/method/payments.getStarsStatus)
    with peer=InputPeerSelf — the official way to check your own balance,
    used here to know when it's safe to resume auto-offers after a
    BALANCE_TOO_LOW pause. Returns None if the balance can't be read right
    now (transient RPC error) rather than raising, so a polling loop can
    just skip that tick and try again later.
    """
    try:
        result = await client(
            functions.payments.GetStarsStatusRequest(peer=types.InputPeerSelf())
        )
    except RPCError:
        return None
    return result.balance.amount
