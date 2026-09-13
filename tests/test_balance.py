from __future__ import annotations

from telethon.errors import RPCError
from telethon.tl import functions, types

from src.marketplace.balance import get_stars_balance


class FakeStarsAmount:
    def __init__(self, amount):
        self.amount = amount


class FakeStarsStatus:
    def __init__(self, amount):
        self.balance = FakeStarsAmount(amount)


class FakeBalanceClient:
    def __init__(self, amount=None, error=None):
        self.amount = amount
        self.error = error
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return FakeStarsStatus(self.amount)


async def test_get_stars_balance_returns_amount():
    client = FakeBalanceClient(amount=250)

    balance = await get_stars_balance(client)

    assert balance == 250
    [request] = client.requests
    assert isinstance(request, functions.payments.GetStarsStatusRequest)
    assert isinstance(request.peer, types.InputPeerSelf)


async def test_get_stars_balance_returns_none_on_rpc_error():
    client = FakeBalanceClient(error=RPCError(None, "SOME_ERROR"))

    balance = await get_stars_balance(client)

    assert balance is None
