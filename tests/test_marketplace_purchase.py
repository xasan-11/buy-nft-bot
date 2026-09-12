from __future__ import annotations

from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions

from src.marketplace.purchase import execute_purchase


class FakePrice:
    def __init__(self, amount: int):
        self.amount = amount


class FakeInvoice:
    def __init__(self, prices):
        self.prices = prices


class FakeForm:
    def __init__(self, form_id: int, prices):
        self.form_id = form_id
        self.invoice = FakeInvoice(prices)


class FakeClient:
    """Stands in for a Telethon client, dispatching by request type just
    like the real `client(request)` call pattern."""

    def __init__(self, form=None, get_form_error=None, send_form_error=None):
        self.form = form
        self.get_form_error = get_form_error
        self.send_form_error = send_form_error
        self.sent = False

    async def __call__(self, request):
        if isinstance(request, functions.payments.GetPaymentFormRequest):
            if self.get_form_error:
                raise self.get_form_error
            return self.form
        if isinstance(request, functions.payments.SendStarsFormRequest):
            if self.send_form_error:
                raise self.send_form_error
            self.sent = True
            return object()
        raise AssertionError(f"unexpected request {request}")


def always_running():
    return True


def never_running():
    return False


async def test_execute_purchase_success():
    client = FakeClient(form=FakeForm(1, [FakePrice(150)]))
    outcome = await execute_purchase(client, "Gift-1", max_price=200, is_monitoring_running=always_running)
    assert outcome.success
    assert outcome.price_stars == 150
    assert client.sent


async def test_execute_purchase_listing_unavailable():
    client = FakeClient(get_form_error=RPCError(None, "STARGIFT_UNAVAILABLE"))
    outcome = await execute_purchase(client, "Gift-2", max_price=200, is_monitoring_running=always_running)
    assert not outcome.success
    assert "listing unavailable" in outcome.error


async def test_execute_purchase_price_exceeds_after_reverification():
    client = FakeClient(form=FakeForm(1, [FakePrice(250)]))
    outcome = await execute_purchase(client, "Gift-3", max_price=200, is_monitoring_running=always_running)
    assert not outcome.success
    assert outcome.price_stars == 250
    assert not client.sent


async def test_execute_purchase_aborts_if_stopped_before_payment():
    client = FakeClient(form=FakeForm(1, [FakePrice(150)]))
    outcome = await execute_purchase(client, "Gift-4", max_price=200, is_monitoring_running=never_running)
    assert not outcome.success
    assert "stopped" in outcome.error
    assert not client.sent


async def test_execute_purchase_handles_flood_wait():
    client = FakeClient(
        form=FakeForm(1, [FakePrice(150)]),
        send_form_error=FloodWaitError(None, capture=5),
    )
    outcome = await execute_purchase(client, "Gift-5", max_price=200, is_monitoring_running=always_running)
    assert not outcome.success
    assert "flood wait" in outcome.error.lower()


async def test_execute_purchase_handles_generic_rpc_error_on_send():
    client = FakeClient(
        form=FakeForm(1, [FakePrice(150)]),
        send_form_error=RPCError(None, "BALANCE_TOO_LOW"),
    )
    outcome = await execute_purchase(client, "Gift-6", max_price=200, is_monitoring_running=always_running)
    assert not outcome.success
    assert "BALANCE_TOO_LOW" in outcome.error
