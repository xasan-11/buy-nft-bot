from __future__ import annotations

from telethon.errors import FloodWaitError, RPCError
from telethon.extensions.binaryreader import BinaryReader
from telethon.tl import types

from src.marketplace.paid_reaction import (
    MAX_STARS_PER_REACTION_CALL,
    SendPaidReactionRequest,
    send_all_stars_as_reaction,
)


class FakeClient:
    def __init__(self, errors=None):
        # Queue of exceptions to raise on successive calls; None means success.
        self.errors = list(errors) if errors else []
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if self.errors:
            exc = self.errors.pop(0)
            if exc:
                raise exc
        return object()


def test_request_serializes_and_deserializes_round_trip():
    """Hand-written binary encoding for a TL method not present in
    Telethon's generated schema — this proves _bytes()/from_reader agree
    with each other and with Telethon's own BinaryReader, the same
    machinery used for every other request in this project."""
    peer = types.InputPeerChannel(channel_id=12345, access_hash=6789)
    request = SendPaidReactionRequest(peer=peer, msg_id=42, count=100, random_id=999888777)

    raw = bytes(request)

    reader = BinaryReader(raw)
    constructor_id = reader.read_int(signed=False)
    assert constructor_id == SendPaidReactionRequest.CONSTRUCTOR_ID

    rebuilt = SendPaidReactionRequest.from_reader(reader)
    assert rebuilt.peer.channel_id == 12345
    assert rebuilt.peer.access_hash == 6789
    assert rebuilt.msg_id == 42
    assert rebuilt.count == 100
    assert rebuilt.random_id == 999888777


async def test_send_all_stars_sends_one_call_when_under_the_chunk_cap():
    client = FakeClient()
    peer = types.InputPeerChannel(channel_id=1, access_hash=1)

    outcome = await send_all_stars_as_reaction(client, peer, msg_id=10, balance=500)

    assert outcome.sent == 500
    assert outcome.remaining_balance == 0
    assert outcome.error is None
    [request] = client.requests
    assert request.count == 500
    assert request.msg_id == 10
    assert request.peer is peer


async def test_send_all_stars_chunks_above_the_cap():
    client = FakeClient()
    peer = types.InputPeerChannel(channel_id=1, access_hash=1)
    balance = MAX_STARS_PER_REACTION_CALL + 800

    outcome = await send_all_stars_as_reaction(client, peer, msg_id=10, balance=balance)

    assert outcome.sent == balance
    assert outcome.remaining_balance == 0
    counts = [r.count for r in client.requests]
    assert counts == [MAX_STARS_PER_REACTION_CALL, 800]


async def test_send_all_stars_uses_a_fresh_random_id_per_chunk():
    client = FakeClient()
    peer = types.InputPeerChannel(channel_id=1, access_hash=1)

    await send_all_stars_as_reaction(client, peer, msg_id=10, balance=MAX_STARS_PER_REACTION_CALL * 2)

    random_ids = [r.random_id for r in client.requests]
    assert len(set(random_ids)) == 2


async def test_flood_wait_is_retried_not_raised(monkeypatch):
    import src.marketplace.paid_reaction as paid_reaction_module

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(paid_reaction_module.asyncio, "sleep", fake_sleep)

    client = FakeClient(errors=[FloodWaitError(None, capture=3), None])
    peer = types.InputPeerChannel(channel_id=1, access_hash=1)

    outcome = await send_all_stars_as_reaction(client, peer, msg_id=10, balance=100)

    assert outcome.sent == 100
    assert outcome.error is None
    assert sleeps == [3]
    assert len(client.requests) == 2  # first attempt (flood-waited) + retry


async def test_rpc_error_mid_stream_stops_and_reports_partial_success():
    client = FakeClient(errors=[None, RPCError(None, "REACTION_INVALID")])
    peer = types.InputPeerChannel(channel_id=1, access_hash=1)
    balance = MAX_STARS_PER_REACTION_CALL * 2  # two chunks needed

    outcome = await send_all_stars_as_reaction(client, peer, msg_id=10, balance=balance)

    assert outcome.sent == MAX_STARS_PER_REACTION_CALL  # only the first chunk went through
    assert outcome.remaining_balance == MAX_STARS_PER_REACTION_CALL
    assert outcome.error is not None
    assert "REACTION_INVALID" in outcome.error


async def test_zero_balance_sends_nothing():
    client = FakeClient()
    peer = types.InputPeerChannel(channel_id=1, access_hash=1)

    outcome = await send_all_stars_as_reaction(client, peer, msg_id=10, balance=0)

    assert outcome.sent == 0
    assert outcome.remaining_balance == 0
    assert client.requests == []
