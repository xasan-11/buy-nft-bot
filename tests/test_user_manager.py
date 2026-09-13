from __future__ import annotations

import asyncio
from types import SimpleNamespace

from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.sessions import StringSession

from src.telegram.user_manager import UserSessionManager
from tests.conftest import ADMIN_ID, OWNER_ID, STRANGER_ID


class FakeStringSession(StringSession):
    """A StringSession whose save() doesn't need a real auth key — only
    isinstance() and .save() are exercised by UserSessionManager."""

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

    async def run_until_disconnected(self):
        await asyncio.Event().wait()

    def on(self, builder):
        """register_offer_decline_handlers just needs this to exist and
        accept @client.on(...) — the handlers it registers are never
        exercised by these tests."""
        def decorator(callback):
            return callback
        return decorator


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
    dispatch for the manager's two handlers (/code, /password): matches the
    builder's compiled pattern against raw_text and sets pattern_match,
    same as the real NewMessage builder does."""

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


def make_manager(repo, phone=None, cleanup_tasks=None):
    settings = SimpleNamespace(owner_id=OWNER_ID, phone=phone)
    bot_client = FakeBotClient()
    manager = UserSessionManager(bot_client, repo, settings)
    manager.register_handlers()
    if cleanup_tasks is not None:
        cleanup_tasks.append(manager)
    return manager, bot_client


async def _cancel_background_tasks(manager):
    for task in manager.background_tasks:
        task.cancel()
    await asyncio.gather(*manager.background_tasks, return_exceptions=True)


async def test_a_random_stranger_can_log_in_their_own_account(repo):
    """The core multi-tenant guarantee: /login is not owner-restricted —
    any Telegram user can connect their own personal account."""
    manager, bot_client = make_manager(repo)
    fake_client = FakeUserClient(authorized=False)
    manager.clients[STRANGER_ID] = fake_client

    login_event = FakeBotEvent(STRANGER_ID, "/login +15551234567")
    await manager.handle_login_command(STRANGER_ID, "+15551234567", login_event)
    assert fake_client.send_code_calls == ["+15551234567"]

    code_event = FakeBotEvent(STRANGER_ID, "/code 12345")
    await bot_client.dispatch(code_event)

    assert manager.is_ready(STRANGER_ID)
    assert fake_client.sign_in_calls == [
        {"phone": "+15551234567", "code": "12345", "phone_code_hash": "hash-abc", "password": None}
    ]
    assert await repo.get_user_session(STRANGER_ID) == "fake-session-string"
    await _cancel_background_tasks(manager)


async def test_two_different_users_get_fully_independent_sessions(repo):
    manager, bot_client = make_manager(repo)
    client_a = FakeUserClient(authorized=False, session_string="session-A")
    client_b = FakeUserClient(authorized=False, session_string="session-B")
    manager.clients[ADMIN_ID] = client_a
    manager.clients[STRANGER_ID] = client_b

    await manager.handle_login_command(ADMIN_ID, "+15550000001", FakeBotEvent(ADMIN_ID, "/login"))
    await bot_client.dispatch(FakeBotEvent(ADMIN_ID, "/code 11111"))

    await manager.handle_login_command(STRANGER_ID, "+15550000002", FakeBotEvent(STRANGER_ID, "/login"))
    await bot_client.dispatch(FakeBotEvent(STRANGER_ID, "/code 22222"))

    assert manager.is_ready(ADMIN_ID)
    assert manager.is_ready(STRANGER_ID)
    assert await repo.get_user_session(ADMIN_ID) == "session-A"
    assert await repo.get_user_session(STRANGER_ID) == "session-B"
    assert manager.get_client(ADMIN_ID) is client_a
    assert manager.get_client(STRANGER_ID) is client_b
    await _cancel_background_tasks(manager)


async def test_code_command_ignored_when_not_awaiting_code(repo):
    manager, bot_client = make_manager(repo)
    # No /login yet: nothing in-flight for this user.

    event = FakeBotEvent(OWNER_ID, "/code 12345")
    await bot_client.dispatch(event)

    assert "kutilmayapti" in event.responses[-1]


async def test_dotted_code_is_parsed_as_digits_only(repo):
    """Telegram often blocks/hides a raw login code typed into any chat, so
    users are expected to split the digits with dots (e.g. "9.9.9.9.9" for
    code "99999") to get it past that — the manager must strip everything
    but digits before calling sign_in."""
    manager, bot_client = make_manager(repo)
    fake_client = FakeUserClient(authorized=False)
    manager.clients[OWNER_ID] = fake_client
    await manager.handle_login_command(OWNER_ID, "+15551234567", FakeBotEvent(OWNER_ID, "/login"))

    code_event = FakeBotEvent(OWNER_ID, "/code 9.9.9.9.9")
    await bot_client.dispatch(code_event)

    assert manager.is_ready(OWNER_ID)
    assert fake_client.sign_in_calls[-1]["code"] == "99999"


async def test_2fa_password_required_flow(repo):
    manager, bot_client = make_manager(repo)
    fake_client = FakeUserClient(authorized=False)
    fake_client.sign_in_exceptions = [SessionPasswordNeededError(request=None)]
    manager.clients[OWNER_ID] = fake_client
    await manager.handle_login_command(OWNER_ID, "+15551234567", FakeBotEvent(OWNER_ID, "/login"))

    code_event = FakeBotEvent(OWNER_ID, "/code 12345")
    await bot_client.dispatch(code_event)

    assert not manager.is_ready(OWNER_ID)
    assert "2FA" in code_event.responses[-1]

    password_event = FakeBotEvent(OWNER_ID, "/password mypassword")
    await bot_client.dispatch(password_event)

    assert manager.is_ready(OWNER_ID)
    assert fake_client.sign_in_calls[-1]["password"] == "mypassword"
    assert "✅" in password_event.responses[-1]
    assert await repo.get_user_session(OWNER_ID) == "fake-session-string"


async def test_invalid_code_resets_awaiting_state_without_setting_ready(repo):
    manager, bot_client = make_manager(repo)
    fake_client = FakeUserClient(authorized=False)
    fake_client.sign_in_exceptions = [PhoneCodeInvalidError(request=None)]
    manager.clients[OWNER_ID] = fake_client
    await manager.handle_login_command(OWNER_ID, "+15551234567", FakeBotEvent(OWNER_ID, "/login"))

    code_event = FakeBotEvent(OWNER_ID, "/code 00000")
    await bot_client.dispatch(code_event)

    assert not manager.is_ready(OWNER_ID)
    assert "noto'g'ri" in code_event.responses[-1].lower() or "eskirgan" in code_event.responses[-1].lower()


async def test_already_authorized_reports_immediately_without_resending_code(repo):
    manager, bot_client = make_manager(repo)
    fake_client = FakeUserClient(authorized=False)
    manager.clients[OWNER_ID] = fake_client
    await manager.handle_login_command(OWNER_ID, "+15551234567", FakeBotEvent(OWNER_ID, "/login"))
    await bot_client.dispatch(FakeBotEvent(OWNER_ID, "/code 12345"))
    assert manager.is_ready(OWNER_ID)

    event = FakeBotEvent(OWNER_ID, "/login +15551234567")
    await manager.handle_login_command(OWNER_ID, "+15551234567", event)

    assert "allaqachon" in event.responses[-1]
    assert fake_client.send_code_calls == ["+15551234567"]  # not sent a second time


async def test_restore_all_reconnects_every_user_with_a_stored_session(repo):
    await repo.set_user_session(ADMIN_ID, "stored-session-A")
    await repo.set_user_session(STRANGER_ID, "stored-session-B")

    manager, _ = make_manager(repo)

    def fake_build_user_client(settings, session_string):
        return FakeUserClient(authorized=True, session_string=session_string)

    import src.telegram.user_manager as user_manager_module
    original = user_manager_module.build_user_client
    user_manager_module.build_user_client = fake_build_user_client
    try:
        await manager.restore_all()
    finally:
        user_manager_module.build_user_client = original

    assert manager.is_ready(ADMIN_ID)
    assert manager.is_ready(STRANGER_ID)
    await _cancel_background_tasks(manager)


async def test_restore_all_leaves_user_with_invalid_session_not_ready(repo):
    await repo.set_user_session(STRANGER_ID, "stale-session")

    manager, _ = make_manager(repo)

    def fake_build_user_client(settings, session_string):
        return FakeUserClient(authorized=False, session_string=session_string)

    import src.telegram.user_manager as user_manager_module
    original = user_manager_module.build_user_client
    user_manager_module.build_user_client = fake_build_user_client
    try:
        await manager.restore_all()
    finally:
        user_manager_module.build_user_client = original

    assert not manager.is_ready(STRANGER_ID)


async def test_owner_activation_does_not_spawn_its_own_background_task(repo):
    """main.py drives the owner's run_until_disconnected() loop itself
    (alongside channel/market monitoring) — the manager must not also
    start a redundant second one for that same client."""
    manager, _ = make_manager(repo)
    fake_client = FakeUserClient(authorized=False)
    manager.clients[OWNER_ID] = fake_client
    await manager.handle_login_command(OWNER_ID, "+15551234567", FakeBotEvent(OWNER_ID, "/login"))

    code_event = FakeBotEvent(OWNER_ID, "/code 12345")
    # dispatch through the manager's own bot_client so /code is handled
    await manager.handle_code_command(OWNER_ID, "12345", code_event)

    assert manager.is_ready(OWNER_ID)
    assert manager.background_tasks == []
