from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.sessions import StringSession

from src.telegram.login_flow import SESSION_STRING_SETTING_KEY, UserLoginCoordinator
from tests.conftest import OWNER_ID, STRANGER_ID


class FakeStringSession(StringSession):
    """A StringSession whose save() doesn't need a real auth key — only
    isinstance() and .save() are exercised by UserLoginCoordinator."""

    def __init__(self, saved="fake-session-string"):
        super().__init__()
        self._saved = saved

    def save(self):
        return self._saved


class FakeUserClient:
    def __init__(self, authorized=False, session_string="fake-session-string"):
        self.session = FakeStringSession(session_string)
        self._authorized = authorized
        self.connect_called = False
        self.send_code_calls = []
        self.sign_in_calls = []
        self.send_code_exception = None
        # Queue of exceptions to raise on successive sign_in calls; None means success.
        self.sign_in_exceptions = []

    async def connect(self):
        self.connect_called = True

    async def is_user_authorized(self):
        return self._authorized

    async def send_code_request(self, phone):
        self.send_code_calls.append(phone)
        if self.send_code_exception:
            raise self.send_code_exception
        return SimpleNamespace(phone_code_hash="hash-abc")

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        self.sign_in_calls.append(
            {"phone": phone, "code": code, "phone_code_hash": phone_code_hash, "password": password}
        )
        if self.sign_in_exceptions:
            exc = self.sign_in_exceptions.pop(0)
            if exc:
                raise exc
        self._authorized = True


class FakeBotEvent:
    def __init__(self, sender_id, raw_text):
        self.sender_id = sender_id
        self.raw_text = raw_text
        self.pattern_match = None
        self.responses = []

    async def respond(self, text):
        self.responses.append(text)


class FakeBotClient:
    """Mimics just enough of Telethon's events.NewMessage(pattern=...)
    dispatch for login_flow's three handlers: matches the builder's compiled
    pattern against raw_text and sets pattern_match, same as the real
    NewMessage builder does."""

    def __init__(self):
        self.handlers = []
        self.sent_messages = []

    def on(self, builder):
        def decorator(callback):
            self.handlers.append((builder.pattern, callback))
            return callback
        return decorator

    async def dispatch(self, event):
        for pattern, callback in self.handlers:
            match = pattern(event.raw_text) if pattern else None
            if pattern and not match:
                continue
            event.pattern_match = match
            await callback(event)

    async def send_message(self, user_id, text):
        self.sent_messages.append((user_id, text))


def make_coordinator(repo, phone=None, authorized=False):
    settings = SimpleNamespace(owner_id=OWNER_ID, phone=phone)
    user_client = FakeUserClient(authorized=authorized)
    bot_client = FakeBotClient()
    coordinator = UserLoginCoordinator(user_client, bot_client, repo, settings)
    coordinator.register_handlers()
    return coordinator, user_client, bot_client


async def test_already_authorized_sets_ready_without_messaging_owner(repo):
    coordinator, user_client, bot_client = make_coordinator(repo, phone="+15551234567", authorized=True)

    await coordinator.start()

    assert coordinator.ready.is_set()
    assert user_client.send_code_calls == []
    assert bot_client.sent_messages == []


async def test_start_with_phone_set_sends_code_immediately(repo):
    coordinator, user_client, bot_client = make_coordinator(repo, phone="+15551234567", authorized=False)

    await coordinator.start()

    assert not coordinator.ready.is_set()
    assert user_client.send_code_calls == ["+15551234567"]
    assert bot_client.sent_messages
    assert "Kod yuborildi" in bot_client.sent_messages[-1][1]


async def test_start_without_phone_prompts_for_login_command(repo):
    coordinator, user_client, bot_client = make_coordinator(repo, phone=None, authorized=False)

    await coordinator.start()

    assert not coordinator.ready.is_set()
    assert user_client.send_code_calls == []
    assert bot_client.sent_messages
    assert "/login" in bot_client.sent_messages[-1][1]


async def test_login_code_password_restricted_to_owner(repo):
    coordinator, user_client, bot_client = make_coordinator(repo, phone=None, authorized=False)
    await coordinator.start()

    event = FakeBotEvent(STRANGER_ID, "/login +15551234567")
    await bot_client.dispatch(event)
    assert user_client.send_code_calls == []
    assert event.responses and "not authorized" in event.responses[-1].lower()

    coordinator._awaiting = "code"
    event = FakeBotEvent(STRANGER_ID, "/code 12345")
    await bot_client.dispatch(event)
    assert user_client.sign_in_calls == []

    coordinator._awaiting = "password"
    event = FakeBotEvent(STRANGER_ID, "/password secret")
    await bot_client.dispatch(event)
    assert user_client.sign_in_calls == []


async def test_full_code_login_completes_and_saves_session(repo):
    coordinator, user_client, bot_client = make_coordinator(repo, phone=None, authorized=False)
    await coordinator.start()

    login_event = FakeBotEvent(OWNER_ID, "/login +15551234567")
    await bot_client.dispatch(login_event)
    assert user_client.send_code_calls == ["+15551234567"]
    assert coordinator._awaiting == "code"

    code_event = FakeBotEvent(OWNER_ID, "/code 12345")
    await bot_client.dispatch(code_event)

    assert coordinator.ready.is_set()
    assert user_client.sign_in_calls == [
        {"phone": "+15551234567", "code": "12345", "phone_code_hash": "hash-abc", "password": None}
    ]
    assert "✅" in code_event.responses[-1]
    saved = await repo.get_setting(SESSION_STRING_SETTING_KEY)
    assert saved == "fake-session-string"


async def test_dotted_code_is_parsed_as_digits_only(repo):
    """Telegram often blocks/hides a raw login code typed into any chat, so
    the owner is expected to split the digits with dots (e.g. "9.9.9.9.9"
    for code "99999") to get it past that — the bot must strip everything
    but digits before calling sign_in."""
    coordinator, user_client, bot_client = make_coordinator(repo, phone="+15551234567", authorized=False)
    await coordinator.start()

    code_event = FakeBotEvent(OWNER_ID, "/code 9.9.9.9.9")
    await bot_client.dispatch(code_event)

    assert coordinator.ready.is_set()
    assert user_client.sign_in_calls[-1]["code"] == "99999"


async def test_2fa_password_required_flow(repo):
    coordinator, user_client, bot_client = make_coordinator(repo, phone="+15551234567", authorized=False)
    user_client.sign_in_exceptions = [SessionPasswordNeededError(request=None)]
    await coordinator.start()
    assert coordinator._awaiting == "code"

    code_event = FakeBotEvent(OWNER_ID, "/code 12345")
    await bot_client.dispatch(code_event)

    assert not coordinator.ready.is_set()
    assert coordinator._awaiting == "password"
    assert "2FA" in code_event.responses[-1]

    password_event = FakeBotEvent(OWNER_ID, "/password mypassword")
    await bot_client.dispatch(password_event)

    assert coordinator.ready.is_set()
    assert user_client.sign_in_calls[-1]["password"] == "mypassword"
    assert "✅" in password_event.responses[-1]
    saved = await repo.get_setting(SESSION_STRING_SETTING_KEY)
    assert saved == "fake-session-string"


async def test_invalid_code_resets_awaiting_state_without_setting_ready(repo):
    coordinator, user_client, bot_client = make_coordinator(repo, phone="+15551234567", authorized=False)
    user_client.sign_in_exceptions = [PhoneCodeInvalidError(request=None)]
    await coordinator.start()

    code_event = FakeBotEvent(OWNER_ID, "/code 00000")
    await bot_client.dispatch(code_event)

    assert not coordinator.ready.is_set()
    assert coordinator._awaiting is None
    assert "noto'g'ri" in code_event.responses[-1].lower() or "eskirgan" in code_event.responses[-1].lower()


async def test_code_command_ignored_when_not_awaiting_code(repo):
    coordinator, user_client, bot_client = make_coordinator(repo, phone="+15551234567", authorized=False)
    # start() not called: nothing in-flight, coordinator._awaiting stays None.

    event = FakeBotEvent(OWNER_ID, "/code 12345")
    await bot_client.dispatch(event)

    assert user_client.sign_in_calls == []
    assert "kutilmayapti" in event.responses[-1]
