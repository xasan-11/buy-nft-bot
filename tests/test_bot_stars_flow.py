from __future__ import annotations

import itertools

from telethon import events
from telethon.tl import functions, types

from src.bot.bot_app import register_bot_handlers
from src.marketplace.paid_reaction import MAX_STARS_PER_REACTION_CALL, SendPaidReactionRequest
from src.monitoring.channel_monitor import ChannelMonitor, MonitorState
from src.monitoring.offer_registry import OfferRegistry
from tests.conftest import ADMIN_ID, OWNER_ID, STRANGER_ID

_message_id_counter = itertools.count(1)


class FakeMessage:
    def __init__(self, id):
        self.id = id


class FakeEvent:
    def __init__(self, text: str, sender_id: int, pattern_match=None, chat_id=None):
        self.raw_text = text
        self.sender_id = sender_id
        self.pattern_match = pattern_match
        self.chat_id = chat_id if chat_id is not None else sender_id
        self.responses = []

    async def respond(self, message=None, *, file=None, buttons=None, **kwargs):
        self.responses.append({"text": message, "file": file, "buttons": buttons})
        return FakeMessage(next(_message_id_counter))


class FakeBotClient:
    def __init__(self):
        self._builders = []
        self.deleted = []

    def on(self, builder):
        def decorator(callback):
            self._builders.append((builder, callback))
            return callback
        return decorator

    async def delete_messages(self, chat_id, message_ids):
        self.deleted.append((chat_id, list(message_ids)))

    async def send(self, text: str, sender_id: int) -> list:
        fired = []
        for builder, callback in self._builders:
            if not isinstance(builder, events.NewMessage):
                continue
            match = builder.pattern(text) if builder.pattern else True
            if not match:
                continue
            event = FakeEvent(text, sender_id, pattern_match=match if match is not True else None)
            await callback(event)
            fired.append(event)
        return fired


class FakeTargetClient:
    """Stands in for one target account's own TelegramClient: answers
    balance checks and records every paid-reaction request sent through
    it, without touching real Telegram."""

    def __init__(self, balance=0, entity=None, entity_error=None):
        self.balance = balance
        self._entity = entity or types.InputPeerChannel(channel_id=999, access_hash=1)
        self._entity_error = entity_error
        self.paid_reaction_requests = []

    async def get_entity(self, ref):
        if self._entity_error:
            raise self._entity_error
        return self._entity

    async def __call__(self, request):
        if isinstance(request, functions.payments.GetStarsStatusRequest):
            return type("Result", (), {"balance": type("Amt", (), {"amount": self.balance})()})()
        if isinstance(request, SendPaidReactionRequest):
            self.paid_reaction_requests.append(request)
            self.balance -= request.count
            return object()
        raise AssertionError(f"unexpected request {request}")


class FakeSessionManager:
    def __init__(self, clients=None, ready=None):
        self._clients = clients or {}
        self._ready = set(ready or ())

    def is_ready(self, telegram_id: int) -> bool:
        return telegram_id in self._ready

    def get_client(self, telegram_id: int):
        return self._clients.get(telegram_id)


class FakeUserClient:
    """The owner's shared scanning client — never touched by the Stars
    features (they always act through a target account's own client)."""

    async def get_entity(self, username):
        raise AssertionError("Stars features must never use the scanning client")

    async def __call__(self, request):
        raise AssertionError("Stars features must never use the scanning client")


class FakeNotifier:
    def __getattr__(self, name):
        async def _noop(*args, **kwargs):
            return None
        return _noop


TARGET_ID = 777


async def make_bot(repo, target_balance=0, target_client=None, ready_target=True):
    await repo.add_admin(ADMIN_ID, added_by=OWNER_ID)
    await repo.set_user_session(TARGET_ID, "some-stored-session")

    bot_client = FakeBotClient()
    user_client = FakeUserClient()
    notifier = FakeNotifier()
    monitor = ChannelMonitor(user_client, repo, MonitorState(repo), 200, notifier)
    state = MonitorState(repo)
    await state.load()
    market_state = MonitorState(repo, key="market_monitoring_status")
    await market_state.load()
    offer_registry = OfferRegistry(repo)

    target = target_client or FakeTargetClient(balance=target_balance)
    session_manager = FakeSessionManager(
        clients={TARGET_ID: target}, ready={TARGET_ID} if ready_target else set()
    )

    register_bot_handlers(
        bot_client, repo, monitor, state, notifier, market_state, offer_registry, session_manager
    )
    return bot_client, target


# ------------------------------------------------------------- owner-only gate

async def test_stars_button_rejects_non_owner(repo):
    bot_client, _ = await make_bot(repo)

    fired = await bot_client.send("⭐ Stars", ADMIN_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("not authorized" in (t or "").lower() for t in texts)


async def test_take_stars_button_rejects_stranger(repo):
    bot_client, _ = await make_bot(repo)

    fired = await bot_client.send("⭐ Take stars", STRANGER_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("not authorized" in (t or "").lower() for t in texts)


# --------------------------------------------------------------- "⭐ Stars"

async def test_stars_reports_balance_for_connected_account(repo):
    bot_client, _ = await make_bot(repo, target_balance=1500)

    await bot_client.send("⭐ Stars", OWNER_ID)
    fired = await bot_client.send(str(TARGET_ID), OWNER_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any(f"Akkount {TARGET_ID}" in (t or "") and "1500" in (t or "") for t in texts)


async def test_stars_reports_error_for_unknown_account(repo):
    bot_client, _ = await make_bot(repo)

    await bot_client.send("⭐ Stars", OWNER_ID)
    fired = await bot_client.send("424242", OWNER_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("ulanmagan" in (t or "") for t in texts)


async def test_stars_reports_error_for_non_numeric_id(repo):
    bot_client, _ = await make_bot(repo)

    await bot_client.send("⭐ Stars", OWNER_ID)
    fired = await bot_client.send("not-a-number", OWNER_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("raqam" in (t or "").lower() for t in texts)


async def test_stars_reports_error_when_account_not_ready(repo):
    bot_client, _ = await make_bot(repo, ready_target=False)

    await bot_client.send("⭐ Stars", OWNER_ID)
    fired = await bot_client.send(str(TARGET_ID), OWNER_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("faol emas" in (t or "") for t in texts)


# ---------------------------------------------------------- "⭐ Take stars"

async def test_take_stars_sends_full_balance_as_one_reaction(repo):
    bot_client, target = await make_bot(repo, target_balance=800)

    await bot_client.send("⭐ Take stars", OWNER_ID)
    await bot_client.send(str(TARGET_ID), OWNER_ID)
    fired = await bot_client.send("https://t.me/examplechannel/123", OWNER_ID)

    assert len(target.paid_reaction_requests) == 1
    assert target.paid_reaction_requests[0].count == 800
    assert target.paid_reaction_requests[0].msg_id == 123

    texts = [r["text"] for e in fired for r in e.responses]
    assert any(
        "800" in (t or "") and str(TARGET_ID) in (t or "") and "Qolgan balans: 0" in (t or "")
        for t in texts
    )


async def test_take_stars_chunks_above_the_per_call_cap(repo):
    balance = MAX_STARS_PER_REACTION_CALL + 500
    bot_client, target = await make_bot(repo, target_balance=balance)

    await bot_client.send("⭐ Take stars", OWNER_ID)
    await bot_client.send(str(TARGET_ID), OWNER_ID)
    await bot_client.send("https://t.me/examplechannel/999", OWNER_ID)

    counts = [r.count for r in target.paid_reaction_requests]
    assert counts == [MAX_STARS_PER_REACTION_CALL, 500]


async def test_take_stars_rejects_unknown_account_before_asking_for_link(repo):
    bot_client, _ = await make_bot(repo)

    await bot_client.send("⭐ Take stars", OWNER_ID)
    fired = await bot_client.send("999999", OWNER_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("ulanmagan" in (t or "") for t in texts)
    # never prompted for a link since the account itself was invalid
    assert not any("havola" in (t or "").lower() for t in texts)


async def test_take_stars_reprompts_on_invalid_link_without_losing_account_id(repo):
    bot_client, target = await make_bot(repo, target_balance=100)

    await bot_client.send("⭐ Take stars", OWNER_ID)
    await bot_client.send(str(TARGET_ID), OWNER_ID)
    await bot_client.send("not a link", OWNER_ID)
    fired = await bot_client.send("https://t.me/examplechannel/5", OWNER_ID)

    # the second (valid) message still completed the flow for the same account
    assert len(target.paid_reaction_requests) == 1


async def test_take_stars_reports_zero_balance_without_sending(repo):
    bot_client, target = await make_bot(repo, target_balance=0)

    await bot_client.send("⭐ Take stars", OWNER_ID)
    await bot_client.send(str(TARGET_ID), OWNER_ID)
    fired = await bot_client.send("https://t.me/examplechannel/1", OWNER_ID)

    assert target.paid_reaction_requests == []
    texts = [r["text"] for e in fired for r in e.responses]
    assert any("balansi 0" in (t or "") for t in texts)


async def test_take_stars_reports_entity_resolution_failure(repo):
    target = FakeTargetClient(balance=100, entity_error=ValueError("Cannot find any entity"))
    bot_client, _ = await make_bot(repo, target_client=target)

    await bot_client.send("⭐ Take stars", OWNER_ID)
    await bot_client.send(str(TARGET_ID), OWNER_ID)
    fired = await bot_client.send("https://t.me/examplechannel/1", OWNER_ID)

    assert target.paid_reaction_requests == []
    texts = [r["text"] for e in fired for r in e.responses]
    assert any("topib bo'lmadi" in (t or "") for t in texts)


# -------------------------------------------------------- plain-text /commands

async def test_slash_stars_command_reports_balance(repo):
    bot_client, _ = await make_bot(repo, target_balance=42)

    fired = await bot_client.send(f"/stars {TARGET_ID}", OWNER_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("42" in (t or "") for t in texts)


async def test_slash_stars_command_rejects_non_owner(repo):
    bot_client, _ = await make_bot(repo, target_balance=42)

    fired = await bot_client.send(f"/stars {TARGET_ID}", ADMIN_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("not authorized" in (t or "").lower() for t in texts)


async def test_slash_takestars_command_sends_in_one_shot(repo):
    bot_client, target = await make_bot(repo, target_balance=300)

    fired = await bot_client.send(f"/takestars {TARGET_ID} https://t.me/examplechannel/77", OWNER_ID)

    assert len(target.paid_reaction_requests) == 1
    assert target.paid_reaction_requests[0].count == 300
    assert target.paid_reaction_requests[0].msg_id == 77
    texts = [r["text"] for e in fired for r in e.responses]
    assert any("300" in (t or "") for t in texts)


async def test_slash_takestars_command_rejects_non_owner(repo):
    bot_client, target = await make_bot(repo, target_balance=300)

    fired = await bot_client.send(f"/takestars {TARGET_ID} https://t.me/examplechannel/77", STRANGER_ID)

    assert target.paid_reaction_requests == []
    texts = [r["text"] for e in fired for r in e.responses]
    assert any("not authorized" in (t or "").lower() for t in texts)


async def test_slash_takestars_without_args_prompts_usage(repo):
    bot_client, _ = await make_bot(repo)

    fired = await bot_client.send("/takestars", OWNER_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("Usage" in (t or "") for t in texts)


async def test_stars_and_take_stars_sessions_are_independent_per_owner_message(repo):
    """Regression guard: starting a "⭐ Take stars" flow must not leave a
    stale pending_action if the owner instead answers a completely
    different button first."""
    bot_client, target = await make_bot(repo, target_balance=100)

    await bot_client.send("⭐ Take stars", OWNER_ID)  # opens take_stars flow
    await bot_client.send("⭐ Stars", OWNER_ID)  # changes their mind
    fired = await bot_client.send(str(TARGET_ID), OWNER_ID)  # answers the Stars prompt

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("joriy balans" in (t or "") for t in texts)
    assert target.paid_reaction_requests == []  # take_stars flow was abandoned
