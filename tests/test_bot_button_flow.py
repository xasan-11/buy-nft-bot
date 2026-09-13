from __future__ import annotations

import itertools

from telethon import events
from telethon.tl import functions

from src.bot import gift_picker
from src.bot.bot_app import register_bot_handlers
from src.monitoring.channel_monitor import ChannelMonitor, MonitorState
from src.monitoring.offer_state import OfferState
from tests.conftest import ADMIN_ID, OWNER_ID

_message_id_counter = itertools.count(1)


class FakeMessage:
    def __init__(self, id):
        self.id = id


class FakeEvent:
    """Simulates a Telethon NewMessage event well enough for bot_app's
    handlers: .respond() records what was sent (text, file, buttons) and
    returns a message-like object with a unique .id, exactly like
    render_gift_page relies on to remember which messages to delete later."""

    def __init__(self, text: str, sender_id: int, pattern_match=None, chat_id=None):
        self.raw_text = text
        self.sender_id = sender_id
        self.pattern_match = pattern_match
        self.chat_id = chat_id if chat_id is not None else sender_id
        self.responses = []  # list of dicts: {"text": ..., "file": ..., "buttons": ...}

    async def respond(self, message=None, *, file=None, buttons=None, **kwargs):
        self.responses.append({"text": message, "file": file, "buttons": buttons})
        return FakeMessage(next(_message_id_counter))


class FakeCallbackEvent:
    """Simulates a Telethon CallbackQuery event: .edit() and .answer() just
    record what happened rather than talking to Telegram."""

    def __init__(self, sender_id: int, chat_id=None, pattern_match=None):
        self.sender_id = sender_id
        self.chat_id = chat_id if chat_id is not None else sender_id
        self.pattern_match = pattern_match
        self.edits = []
        self.answers = []
        self.responses = []

    async def edit(self, *args, **kwargs):
        self.edits.append({"args": args, "kwargs": kwargs})

    async def answer(self, message=None, **kwargs):
        self.answers.append({"message": message, "kwargs": kwargs})

    async def respond(self, message=None, **kwargs):
        self.responses.append({"text": message, **kwargs})
        return FakeMessage(next(_message_id_counter))


class FakeBotClient:
    """Records every @bot_client.on(events.NewMessage(...)) and
    @bot_client.on(events.CallbackQuery(...)) the way Telethon's real client
    would, without needing a live connection. `send()` simulates an incoming
    text message (NewMessage handlers only); `send_callback()` simulates an
    inline button tap (CallbackQuery handlers only) — mirroring each
    builder's actual .pattern / .match compiled-regex semantics from
    telethon/events/{newmessage,callbackquery}.py."""

    def __init__(self):
        self._builders = []
        self.deleted = []  # list of (chat_id, message_ids) from delete_messages calls

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

    async def send_callback(self, data: str, sender_id: int) -> list:
        data_bytes = data.encode("utf-8")
        fired = []
        for builder, callback in self._builders:
            if not isinstance(builder, events.CallbackQuery):
                continue
            match = builder.match(data_bytes) if callable(builder.match) else (data_bytes == builder.match)
            if not match:
                continue
            event = FakeCallbackEvent(sender_id, pattern_match=match if match is not True else None)
            await callback(event)
            fired.append(event)
        return fired


class FakeStarGiftType:
    def __init__(self, id, stars=100, availability_resale=None, title=None):
        self.id = id
        self.stars = stars
        self.availability_resale = availability_resale
        self.title = title
        self.sticker = f"sticker-{id}"


class FakeUserClient:
    def __init__(self, gift_types=None):
        self._gift_types = gift_types if gift_types is not None else []

    async def get_entity(self, username):
        return type("Entity", (), {"id": -100, "username": username})()

    async def __call__(self, request):
        if isinstance(request, functions.payments.GetStarGiftsRequest):
            return type("Result", (), {"gifts": self._gift_types})()
        raise AssertionError(f"unexpected request {request}")


class FakeNotifier:
    """Swallows every notify_* call the real Notifier exposes — these tests
    only care about the bot's own replies, not admin broadcasts."""

    def __getattr__(self, name):
        async def _noop(*args, **kwargs):
            return None
        return _noop


async def make_bot(repo, gift_types=None):
    await repo.add_admin(ADMIN_ID, added_by=OWNER_ID)
    bot_client = FakeBotClient()
    user_client = FakeUserClient(gift_types)
    notifier = FakeNotifier()
    monitor = ChannelMonitor(user_client, repo, MonitorState(repo), 200, notifier)
    state = MonitorState(repo)
    await state.load()
    market_state = MonitorState(repo, key="market_monitoring_status")
    await market_state.load()
    offer_state = OfferState(repo)
    await offer_state.load()

    register_bot_handlers(bot_client, repo, monitor, state, notifier, market_state, offer_state)
    return bot_client, offer_state


async def test_status_button_runs_do_status(repo):
    bot_client, _ = await make_bot(repo)

    fired = await bot_client.send("📊 Holat", ADMIN_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("Status" in t for t in texts if t)


async def test_setmaxprice_button_prompts_then_applies_value(repo):
    bot_client, _ = await make_bot(repo)

    prompt_events = await bot_client.send("💰 Max narx", ADMIN_ID)
    assert any("narx" in (r["text"] or "").lower() for e in prompt_events for r in e.responses)

    reply_events = await bot_client.send("300", ADMIN_ID)
    assert await repo.get_max_price() == 300
    assert any("300" in (r["text"] or "") for e in reply_events for r in e.responses)


async def test_setmaxprice_button_reprompts_on_invalid_value(repo):
    bot_client, _ = await make_bot(repo)

    await bot_client.send("💰 Max narx", ADMIN_ID)
    await bot_client.send("not-a-number", ADMIN_ID)
    assert await repo.get_max_price() == 200  # unchanged

    # still pending -> a valid value now succeeds
    await bot_client.send("500", ADMIN_ID)
    assert await repo.get_max_price() == 500


async def test_fresh_button_tap_cancels_stale_pending_flow(repo):
    bot_client, _ = await make_bot(repo)

    await bot_client.send("💰 Max narx", ADMIN_ID)  # start a setmaxprice flow
    await bot_client.send("📊 Holat", ADMIN_ID)  # change their mind, tap another button
    await bot_client.send("300", ADMIN_ID)  # this must NOT be swallowed as the old prompt's answer

    assert await repo.get_max_price() == 200  # setmaxprice flow was abandoned, never applied


async def test_addadmin_button_rejects_non_owner_admin(repo):
    bot_client, _ = await make_bot(repo)  # make_bot already seeds ADMIN_ID as a non-owner admin

    fired = await bot_client.send("👤➕ Admin qo'sh", ADMIN_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("not authorized" in (t or "").lower() for t in texts)
    assert not await repo.is_admin(999999)


async def test_stop_offer_button_stops_immediately(repo):
    bot_client, offer_state = await make_bot(repo)
    await offer_state.start()

    fired = await bot_client.send("🛑 Offer to'xtatish", ADMIN_ID)

    assert offer_state.is_active() is False
    texts = [r["text"] for e in fired for r in e.responses]
    assert any("to'xtatildi" in (t or "") for t in texts)


# ------------------------------------------------------- gift-type picker flow

async def test_start_offer_button_opens_picker_with_one_message_per_resalable_gift(repo):
    gift_types = [
        FakeStarGiftType(1, stars=100, availability_resale=5, title="Plush Pepe"),
        FakeStarGiftType(2, stars=200, availability_resale=None, title="Not Resalable"),
        FakeStarGiftType(3, stars=150, availability_resale=2, title="Desk Calendar"),
    ]
    bot_client, _ = await make_bot(repo, gift_types=gift_types)

    fired = await bot_client.send("🎯 Offer boshlash", ADMIN_ID)

    # a single plain-text message, no media/document of any kind
    all_responses = [r for e in fired for r in e.responses]
    assert len(all_responses) == 1
    response = all_responses[0]
    assert response.get("file") is None

    assert "Sahifa 1/1" in response["text"]
    assert "0 ta tanlangan" in response["text"]

    button_texts = [btn.text for row in response["buttons"] for btn in row]
    assert any("Plush Pepe" in t for t in button_texts)
    assert any("Desk Calendar" in t for t in button_texts)
    assert not any("Not Resalable" in t for t in button_texts)
    # no sticker alt was faked for these gifts -> falls back to the generic emoji
    assert any(gift_picker.FALLBACK_EMOJI in t for t in button_texts)


async def test_start_offer_button_shows_no_gifts_message_when_none_resalable(repo):
    bot_client, _ = await make_bot(repo, gift_types=[FakeStarGiftType(1, availability_resale=None)])

    fired = await bot_client.send("🎯 Offer boshlash", ADMIN_ID)

    texts = [r["text"] for e in fired for r in e.responses]
    assert any("mavjud gift turi topilmadi" in (t or "") for t in texts)


async def test_gift_toggle_callback_flips_selection_and_edits_button(repo):
    gift_types = [FakeStarGiftType(1, stars=100, availability_resale=5, title="Plush Pepe")]
    bot_client, _ = await make_bot(repo, gift_types=gift_types)
    await bot_client.send("🎯 Offer boshlash", ADMIN_ID)

    fired = await bot_client.send_callback("giftsel:1", ADMIN_ID)

    assert len(fired) == 1
    assert len(fired[0].edits) == 1
    edited_buttons = fired[0].edits[0]["kwargs"]["buttons"]
    assert edited_buttons[0][0].text.startswith("✅")

    # tapping again toggles back off
    fired_again = await bot_client.send_callback("giftsel:1", ADMIN_ID)
    edited_buttons_again = fired_again[0].edits[0]["kwargs"]["buttons"]
    assert edited_buttons_again[0][0].text.startswith("⬜")


async def test_gift_start_callback_without_selection_shows_alert_and_does_not_start(repo):
    gift_types = [FakeStarGiftType(1, stars=100, availability_resale=5, title="Plush Pepe")]
    bot_client, offer_state = await make_bot(repo, gift_types=gift_types)
    await bot_client.send("🎯 Offer boshlash", ADMIN_ID)

    fired = await bot_client.send_callback("giftstart", ADMIN_ID)

    assert len(fired) == 1
    assert fired[0].answers[0]["kwargs"].get("alert") is True
    assert "kamida bitta" in fired[0].answers[0]["message"]
    assert offer_state.is_active() is False


async def test_gift_start_callback_with_selection_persists_and_starts_unbounded(repo):
    gift_types = [
        FakeStarGiftType(1, stars=100, availability_resale=5, title="Plush Pepe"),
        FakeStarGiftType(2, stars=200, availability_resale=3, title="Desk Calendar"),
    ]
    bot_client, offer_state = await make_bot(repo, gift_types=gift_types)
    await bot_client.send("🎯 Offer boshlash", ADMIN_ID)
    await bot_client.send_callback("giftsel:2", ADMIN_ID)

    fired = await bot_client.send_callback("giftstart", ADMIN_ID)

    assert offer_state.is_active() is True  # unbounded — no target-count concept exists
    assert await repo.get_offer_selected_gift_types() == {2}
    texts = [r["text"] for e in fired for r in e.responses]
    assert any("1 ta" in (t or "") for t in texts)


async def test_gift_page_navigation_replaces_messages(repo):
    gift_types = [
        FakeStarGiftType(i, stars=100, availability_resale=1, title=f"Gift {i}")
        for i in range(1, 11)  # 10 gifts -> 2 pages at page size 8
    ]
    bot_client, _ = await make_bot(repo, gift_types=gift_types)
    page1_events = await bot_client.send("🎯 Offer boshlash", ADMIN_ID)
    page1_message_count = sum(len(e.responses) for e in page1_events)

    fired = await bot_client.send_callback("giftpage:next", ADMIN_ID)

    # the previous page's message was deleted before the next page was sent
    assert bot_client.deleted, "expected old page message to be deleted on navigation"
    deleted_chat_id, deleted_ids = bot_client.deleted[-1]
    assert len(deleted_ids) == page1_message_count

    response = fired[0].responses[0]
    assert "Sahifa 2/2" in response["text"]
    button_texts = [btn.text for row in response["buttons"] for btn in row]
    assert any("Gift 9" in t for t in button_texts)
    assert any("Gift 10" in t for t in button_texts)
