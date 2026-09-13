from __future__ import annotations

from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions, types

from src.marketplace.offer import send_offer


class FakeOfferClient:
    def __init__(self, error=None):
        self.error = error
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return object()


async def test_send_offer_success_builds_correct_request():
    client = FakeOfferClient()
    peer = types.PeerUser(user_id=123)

    outcome = await send_offer(client, peer, "Gift-1", 125, 21600)

    assert outcome.success is True
    assert outcome.offer_id is not None
    [request] = client.requests
    assert isinstance(request, functions.payments.SendStarGiftOfferRequest)
    assert request.peer is peer
    assert request.slug == "Gift-1"
    assert request.price.amount == 125
    assert request.duration == 21600
    assert request.random_id == outcome.offer_id


async def test_send_offer_flood_wait_is_reported_not_raised():
    client = FakeOfferClient(error=FloodWaitError(None, capture=5))

    outcome = await send_offer(client, types.PeerUser(user_id=1), "Gift-2", 125, 21600)

    assert outcome.success is False
    assert "flood wait" in outcome.error


async def test_send_offer_rpc_error_is_reported_not_raised():
    client = FakeOfferClient(error=RPCError(None, "RESELL_STARS_TOO_FEW"))

    outcome = await send_offer(client, types.PeerUser(user_id=1), "Gift-3", 125, 21600)

    assert outcome.success is False
    assert outcome.error is not None
    assert outcome.balance_too_low is False


async def test_send_offer_flags_balance_too_low():
    client = FakeOfferClient(error=RPCError(None, "BALANCE_TOO_LOW"))

    outcome = await send_offer(client, types.PeerUser(user_id=1), "Gift-4", 125, 21600)

    assert outcome.success is False
    assert outcome.balance_too_low is True


async def test_send_offer_balance_too_low_detection_is_case_insensitive():
    client = FakeOfferClient(error=RPCError(None, "balance_too_low"))

    outcome = await send_offer(client, types.PeerUser(user_id=1), "Gift-5", 125, 21600)

    assert outcome.balance_too_low is True
