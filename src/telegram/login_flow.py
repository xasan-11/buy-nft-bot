from __future__ import annotations

import logging
import re
from asyncio import Event
from typing import Optional

from telethon import events
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from ..config.settings import Settings
from ..database.repository import Repository
from ..bot.authorization import UNAUTHORIZED_MESSAGE

logger = logging.getLogger("telegram.login")

# Stored in application_settings (not .env — a .env file/commit can leak,
# the database is the one place this project already treats as private
# runtime state) so a completed /login survives every future restart
# without needing TELEGRAM_SESSION_STRING set at all.
SESSION_STRING_SETTING_KEY = "telegram_user_session_string"


def _digits_only(raw: Optional[str]) -> str:
    """Strips everything but digits from a /code argument, so a code sent
    as "9.9.9.9.9" (or with spaces/dashes) is read as "99999"."""
    return re.sub(r"\D", "", raw) if raw else ""


class UserLoginCoordinator:
    """Bridges Telethon's interactive login (phone -> code -> optional 2FA
    password) onto the control bot's chat with the owner, since a host like
    Railway gives the process no terminal to type a login code into.

    Call register_handlers() once at startup to wire /login, /code and
    /password on the control bot (owner-only — see _owner_only below), then
    run start() as a background task. `ready` is only set once user_client
    is confirmed authorized (immediately, if a stored/env session is still
    valid) — every caller that needs the user account (channel/market
    monitoring, purchasing, offers) must await it before starting, so a
    fresh deploy with no valid session yet never touches the account.
    """

    def __init__(
        self,
        user_client,
        bot_client,
        repo: Repository,
        settings: Settings,
    ):
        self.user_client = user_client
        self.bot_client = bot_client
        self.repo = repo
        self.settings = settings
        self.ready = Event()
        self._phone: Optional[str] = None
        self._phone_code_hash: Optional[str] = None
        # None | "code" | "password" — which reply we're currently expecting
        # from the owner. Guards /code and /password against firing outside
        # of an actual in-progress login (e.g. a stray /code sent later).
        self._awaiting: Optional[str] = None

    def register_handlers(self) -> None:
        async def _owner_only(event) -> bool:
            if event.sender_id == self.settings.owner_id:
                return True
            await event.respond(UNAUTHORIZED_MESSAGE)
            return False

        @self.bot_client.on(events.NewMessage(pattern=r"^/login(?:\s+(\S+))?$"))
        async def _login(event):
            if not await _owner_only(event):
                return
            if self.ready.is_set():
                await event.respond("✅ Akkount allaqachon avtorizatsiyalangan.")
                return
            phone = event.pattern_match.group(1) or self.settings.phone
            if not phone:
                await event.respond(
                    "⚠️ Telefon raqamni kiriting, masalan: /login +998901234567"
                )
                return
            await self._request_code(phone, event)

        @self.bot_client.on(events.NewMessage(pattern=r"^/code(?:\s+(\S+))?$"))
        async def _code(event):
            if not await _owner_only(event):
                return
            if self._awaiting != "code":
                await event.respond(
                    "ℹ️ Hozir kod kutilmayapti. Avval /login <telefon_raqam> yuboring."
                )
                return
            # Telegram itself often intercepts/hides a raw login code typed
            # into any chat (its own anti-phishing heuristic — see
            # core.telegram.org/api/auth#preventing-phishing), so the digits
            # are typically sent split up with dots/spaces/dashes between
            # them (e.g. "9.9.9.9.9" for code 99999) to dodge that. Only the
            # digits matter here — everything else is stripped.
            code = _digits_only(event.pattern_match.group(1))
            if not code:
                await event.respond(
                    "⚠️ Kodni kiriting. Agar Telegram kodni to'g'ridan-to'g'ri "
                    "yuborishga to'sqinlik qilsa, raqamlar orasiga nuqta qo'yib yuboring, "
                    "masalan: /code 9.9.9.9.9 (bu 99999 deb qabul qilinadi)."
                )
                return
            await self._submit_code(code, event)

        @self.bot_client.on(events.NewMessage(pattern=r"^/password(?:\s+(.+))?$"))
        async def _password(event):
            if not await _owner_only(event):
                return
            if self._awaiting != "password":
                await event.respond("ℹ️ Hozir parol kutilmayapti.")
                return
            password = event.pattern_match.group(1)
            if not password:
                await event.respond("⚠️ Parolni kiriting, masalan: /password mypassword")
                return
            await self._submit_password(password, event)

    async def start(self) -> None:
        """Connects user_client and either confirms an existing session is
        still valid or kicks off the bot-mediated login flow. Returns as
        soon as that first step is done — it does NOT wait for the owner to
        actually finish /code / /password, since those arrive later via the
        independent handlers registered above. Safe to run as a background
        task; never raises for the "not logged in yet" case, only for
        actual send-code failures (network/Telegram errors), which are
        reported to the owner instead of raised.
        """
        await self.user_client.connect()
        if await self.user_client.is_user_authorized():
            self.ready.set()
            logger.info("User account already authorized via stored session.")
            return

        logger.info("User account not authorized — starting bot-mediated login.")
        if self.settings.phone:
            await self._request_code(self.settings.phone, event=None)
        else:
            await self._notify_owner(
                "🔑 Telegram akkountga kirish kerak. Kodni yuborishim uchun avval "
                "/login <telefon_raqam> buyrug'ini yuboring."
            )

    async def _notify_owner(self, text: str) -> None:
        try:
            await self.bot_client.send_message(self.settings.owner_id, text)
        except Exception:
            logger.exception("Failed to message owner during login flow")

    async def _reply(self, event, text: str) -> None:
        if event is not None:
            await event.respond(text)
        else:
            await self._notify_owner(text)

    async def _request_code(self, phone: str, event) -> None:
        try:
            sent = await self.user_client.send_code_request(phone)
        except Exception as e:
            logger.exception("Failed to send login code to %s", phone)
            await self._reply(event, f"❌ Kod yuborishda xatolik: {e}")
            return
        self._phone = phone
        self._phone_code_hash = sent.phone_code_hash
        self._awaiting = "code"
        await self._reply(
            event,
            "📩 Kod yuborildi. Iltimos kodni shu yerga yozing: /code 12345 shaklida.\n"
            "Agar Telegram kodni chatda ko'rsatishga/yuborishga to'sqinlik qilsa, "
            "raqamlar orasiga nuqta qo'yib yuboring: /code 9.9.9.9.9 (99999 sifatida qabul qilinadi).",
        )

    async def _submit_code(self, code: str, event) -> None:
        try:
            await self.user_client.sign_in(
                phone=self._phone, code=code, phone_code_hash=self._phone_code_hash
            )
        except SessionPasswordNeededError:
            self._awaiting = "password"
            await self._reply(event, "🔒 2FA yoqilgan. Parolni yuboring: /password <parol>")
            return
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            self._awaiting = None
            await self._reply(
                event,
                f"❌ Kod noto'g'ri yoki eskirgan. Qaytadan /login {self._phone} deb yozing.",
            )
            return
        except Exception as e:
            logger.exception("Sign-in with code failed")
            self._awaiting = None
            await self._reply(event, f"❌ Kirishda xatolik: {e}")
            return
        await self._finish(event)

    async def _submit_password(self, password: str, event) -> None:
        try:
            await self.user_client.sign_in(password=password)
        except Exception as e:
            logger.exception("2FA sign-in failed")
            await self._reply(event, f"❌ Parol noto'g'ri yoki xatolik: {e}")
            return
        await self._finish(event)

    async def _finish(self, event) -> None:
        self._awaiting = None
        if isinstance(self.user_client.session, StringSession):
            session_string = self.user_client.session.save()
            await self.repo.set_setting(SESSION_STRING_SETTING_KEY, session_string)
            logger.info(
                "Saved new user session string to application_settings (%s).",
                SESSION_STRING_SETTING_KEY,
            )
        self.ready.set()
        await self._reply(event, "✅ Muvaffaqiyatli login qilindi.")
