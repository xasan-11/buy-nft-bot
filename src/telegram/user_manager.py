from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from asyncio import Event
from dataclasses import dataclass
from typing import Dict, Optional

from telethon import events
from telethon.errors import (
    ApiIdInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from ..config.settings import Settings
from ..database.repository import Repository
from .client import build_user_client
from .offer_decline import register_offer_decline_handlers

logger = logging.getLogger("telegram.user_manager")


def _digits_only(raw: Optional[str]) -> str:
    """Strips everything but digits from a /code argument, so a code sent
    as "9.9.9.9.9" (or with spaces/dashes) is read as "99999"."""
    return re.sub(r"\D", "", raw) if raw else ""


def _mask_phone(phone: Optional[str]) -> str:
    """Partially hides a phone number for the owner login notification —
    keeps the leading "+<country code>"-ish prefix and the last 2 digits,
    replacing everything else with asterisks (e.g. "+998901234548" ->
    "+998*******48"). Never exposes the full number. A phone too short to
    meaningfully mask (or missing entirely) is reported as unknown rather
    than shown outright."""
    if not phone:
        return "noma'lum"
    phone = phone.strip()
    if len(phone) <= 6:
        return "noma'lum"
    prefix, suffix = phone[:4], phone[-2:]
    return prefix + ("*" * (len(phone) - len(prefix) - len(suffix))) + suffix


@dataclass
class _LoginProgress:
    phone: Optional[str] = None
    phone_code_hash: Optional[str] = None
    # None | "code" | "password" — which reply is currently expected from
    # this user. Guards /code and /password against firing outside of an
    # actual in-progress login (e.g. a stray /code sent later).
    awaiting: Optional[str] = None


class UserSessionManager:
    """Owns one TelegramClient per Telegram user who has completed /login,
    keyed by that user's own Telegram id — every user (owner included) goes
    through the same bot-mediated phone -> code -> optional 2FA flow, and
    ends up with their own isolated StringSession persisted in
    users.session_string (see database/repository.py), so every user's
    login and personal account are fully independent of everyone else's.

    This is the multi-tenant generalization of what used to be the single
    owner-only UserLoginCoordinator (telegram/login_flow.py, now removed):
    any Telegram user can /login their own account and immediately use the
    offer-tashlash pipeline with it — see bot/bot_app.py, which forwards
    /login (and the "🔑 Kirish" button) here via handle_login_command, and
    registers /code + /password directly on this manager via
    register_handlers().

    The owner's client is special only in how main.py wires it up: it's
    created and connected eagerly (so it can be handed to ChannelMonitor/
    MarketMonitor immediately, exactly like the single-tenant app did) and
    main.py itself drives its run_until_disconnected() loop — see
    run_forever_tasks(). Every other user's client is instead started
    dynamically, the moment their login finishes or their stored session is
    restored at startup (see _activate/restore_all).
    """

    def __init__(self, bot_client, repo: Repository, settings: Settings):
        self.bot_client = bot_client
        self.repo = repo
        self.settings = settings
        self.clients: Dict[int, object] = {}
        self._ready_events: Dict[int, Event] = {}
        self._login_progress: Dict[int, _LoginProgress] = {}
        # run_until_disconnected() tasks this manager itself spawned (every
        # non-owner client) — tracked so main.py can cancel/await them on
        # shutdown alongside everything else.
        self.background_tasks: list = []

    # ------------------------------------------------------------- queries
    def is_ready(self, telegram_id: int) -> bool:
        ev = self._ready_events.get(telegram_id)
        return ev is not None and ev.is_set()

    def get_client(self, telegram_id: int):
        return self.clients.get(telegram_id)

    def _ready_event(self, telegram_id: int) -> Event:
        return self._ready_events.setdefault(telegram_id, Event())

    async def wait_ready(self, telegram_id: int) -> None:
        await self._ready_event(telegram_id).wait()

    # --------------------------------------------------------------- setup
    def register_handlers(self) -> None:
        """/login itself is routed through bot_app.py (do_login ->
        handle_login_command below), since it shares the same
        pending-value-after-button-tap flow every other command uses. Only
        /code and /password — which are always a direct reply to this
        manager's own prompt, never a button — are registered here."""

        @self.bot_client.on(events.NewMessage(pattern=r"^/code(?:\s+(\S+))?$"))
        async def _code(event):
            await self.handle_code_command(event.sender_id, event.pattern_match.group(1), event)

        @self.bot_client.on(events.NewMessage(pattern=r"^/password(?:\s+(.+))?$"))
        async def _password(event):
            await self.handle_password_command(event.sender_id, event.pattern_match.group(1), event)

    async def _reply(self, telegram_id: int, event, text: str) -> None:
        if event is not None:
            await event.respond(text)
            return
        try:
            await self.bot_client.send_message(telegram_id, text)
        except Exception:
            logger.exception("Failed to message user %s during login flow", telegram_id)

    # --------------------------------------------------------- /login flow
    async def handle_login_command(self, telegram_id: int, phone: Optional[str], event) -> None:
        if self.is_ready(telegram_id):
            await self._reply(telegram_id, event, "✅ Akkount allaqachon avtorizatsiyalangan.")
            return
        phone = phone or (self.settings.phone if telegram_id == self.settings.owner_id else None)
        if not phone:
            await self._reply(
                telegram_id, event, "⚠️ Telefon raqamni kiriting, masalan: /login +998901234567"
            )
            return
        progress = self._login_progress.get(telegram_id)
        # A repeated /login for the same phone while a code (or 2FA
        # password) is already pending must NOT trigger another
        # send_code_request — Telegram only offers a limited number of
        # delivery methods (SMS, flash-call, ...) per phone number in a
        # given window, and re-requesting on every impatient retry burns
        # through them until the server refuses outright with
        # SendCodeUnavailableError. Just point the user back at the step
        # they're already on.
        if progress is not None and progress.phone == phone and progress.awaiting in ("code", "password"):
            if progress.awaiting == "code":
                await self._reply(
                    telegram_id, event,
                    "ℹ️ Kod allaqachon yuborilgan. Iltimos, oldin yuborilgan kodni /code shaklida yuboring.",
                )
            else:
                await self._reply(
                    telegram_id, event, "ℹ️ 2FA paroli kutilmoqda. /password <parol> deb yuboring.",
                )
            return
        await self._request_code(telegram_id, phone, event)

    async def handle_code_command(self, telegram_id: int, raw_code: Optional[str], event) -> None:
        progress = self._login_progress.get(telegram_id)
        if progress is None or progress.awaiting != "code":
            await self._reply(
                telegram_id, event, "ℹ️ Hozir kod kutilmayapti. Avval /login <telefon_raqam> yuboring."
            )
            return
        # Telegram itself often intercepts/hides a raw login code typed
        # into any chat (its own anti-phishing heuristic — see
        # core.telegram.org/api/auth#preventing-phishing), so the digits
        # are typically sent split up with dots/spaces/dashes between them
        # (e.g. "9.9.9.9.9" for code 99999) to dodge that. Only the digits
        # matter here — everything else is stripped.
        code = _digits_only(raw_code)
        if not code:
            await self._reply(
                telegram_id, event,
                "⚠️ Kodni kiriting. Agar Telegram kodni to'g'ridan-to'g'ri "
                "yuborishga to'sqinlik qilsa, raqamlar orasiga nuqta qo'yib yuboring, "
                "masalan: /code 9.9.9.9.9 (bu 99999 deb qabul qilinadi).",
            )
            return
        await self._submit_code(telegram_id, code, event)

    async def handle_password_command(self, telegram_id: int, password: Optional[str], event) -> None:
        progress = self._login_progress.get(telegram_id)
        if progress is None or progress.awaiting != "password":
            await self._reply(telegram_id, event, "ℹ️ Hozir parol kutilmayapti.")
            return
        if not password:
            await self._reply(telegram_id, event, "⚠️ Parolni kiriting, masalan: /password mypassword")
            return
        await self._submit_password(telegram_id, password, event)

    async def _get_or_create_client(self, telegram_id: int):
        client = self.clients.get(telegram_id)
        if client is None:
            stored = await self.repo.get_user_session(telegram_id)
            client = build_user_client(self.settings, stored)
            self.clients[telegram_id] = client
        return client

    async def _ensure_connected(self, telegram_id: int, client) -> bool:
        """Reconnects a client whose MTProto connection has dropped
        (network blip, idle server-side disconnect, proxy failure, etc.)
        before any request is sent through it. Without this check,
        send_code_request()/sign_in() fail with "Cannot send requests
        while disconnected" — and since the same disconnected client
        object keeps getting handed back on every retry, the same call
        would otherwise fail identically forever."""
        if client.is_connected():
            return True
        logger.warning("Telegram client for user %s is disconnected — reconnecting", telegram_id)
        try:
            await client.connect()
        except ApiIdInvalidError:
            logger.exception(
                "Reconnect failed for user %s: TELEGRAM_API_ID/TELEGRAM_API_HASH are invalid",
                telegram_id,
            )
            return False
        except (asyncio.TimeoutError, ConnectionError, OSError):
            logger.exception(
                "Reconnect failed for user %s: network/proxy problem while connecting to Telegram",
                telegram_id,
            )
            return False
        except Exception:
            logger.exception("Reconnect failed for user %s with an unexpected error", telegram_id)
            return False
        if not client.is_connected():
            logger.error("Telegram client for user %s still disconnected after connect()", telegram_id)
            return False
        return True

    async def _request_code(self, telegram_id: int, phone: str, event) -> None:
        client = await self._get_or_create_client(telegram_id)
        if not await self._ensure_connected(telegram_id, client):
            await self._reply(
                telegram_id, event,
                "❌ Telegramga ulanib bo'lmadi. Internet aloqasi yoki proksi sozlamalarini "
                "tekshirib, birozdan so'ng qaytadan /login urinib ko'ring.",
            )
            return
        try:
            sent = await client.send_code_request(phone)
        except SendCodeUnavailableError:
            logger.warning(
                "send_code_request for %s (user %s): Telegram exhausted all delivery methods "
                "(SMS/flash-call) for this number — must wait before retrying",
                phone, telegram_id,
            )
            await self._reply(
                telegram_id, event,
                "❌ Bu telefon raqami uchun kod yuborishning barcha usullari (SMS/qo'ng'iroq) "
                "vaqtincha tugatilgan — Telegram serveri shu raqamga qayta-qayta kod so'ralganini "
                "cheklaydi. Iltimos, birozdan (odatda bir necha soatdan) so'ng qaytadan /login urinib ko'ring.",
            )
            return
        except ApiIdInvalidError:
            logger.exception(
                "send_code_request failed for user %s: TELEGRAM_API_ID/TELEGRAM_API_HASH are invalid",
                telegram_id,
            )
            await self._reply(telegram_id, event, "❌ Kod yuborishda xatolik: API sozlamalari noto'g'ri.")
            return
        except (asyncio.TimeoutError, ConnectionError, OSError):
            logger.exception(
                "send_code_request failed for user %s: network/proxy problem or connection dropped "
                "mid-request",
                telegram_id,
            )
            await self._reply(
                telegram_id, event,
                "❌ Kod yuborishda xatolik: tarmoq bilan bog'liq muammo. Birozdan so'ng qaytadan urinib ko'ring.",
            )
            return
        except Exception as e:
            logger.exception("Failed to send login code to %s for user %s", phone, telegram_id)
            await self._reply(telegram_id, event, f"❌ Kod yuborishda xatolik: {e}")
            return
        self._login_progress[telegram_id] = _LoginProgress(
            phone=phone, phone_code_hash=sent.phone_code_hash, awaiting="code"
        )
        await self._reply(
            telegram_id, event,
            "📩 Kod yuborildi. Iltimos kodni shu yerga yozing: /code 12345 shaklida.\n"
            "Agar Telegram kodni chatda ko'rsatishga/yuborishga to'sqinlik qilsa, "
            "raqamlar orasiga nuqta qo'yib yuboring: /code 9.9.9.9.9 (99999 sifatida qabul qilinadi).",
        )

    async def _submit_code(self, telegram_id: int, code: str, event) -> None:
        progress = self._login_progress[telegram_id]
        client = self.clients[telegram_id]
        if not await self._ensure_connected(telegram_id, client):
            progress.awaiting = None
            await self._reply(
                telegram_id, event,
                "❌ Telegramga ulanish uzilgan va qayta ulanib bo'lmadi. "
                f"Qaytadan /login {progress.phone} deb yozing.",
            )
            return
        try:
            await client.sign_in(phone=progress.phone, code=code, phone_code_hash=progress.phone_code_hash)
        except SessionPasswordNeededError:
            progress.awaiting = "password"
            await self._reply(telegram_id, event, "🔒 2FA yoqilgan. Parolni yuboring: /password <parol>")
            return
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            progress.awaiting = None
            await self._reply(
                telegram_id, event,
                f"❌ Kod noto'g'ri yoki eskirgan. Qaytadan /login {progress.phone} deb yozing.",
            )
            return
        except Exception as e:
            logger.exception("Sign-in with code failed for user %s", telegram_id)
            progress.awaiting = None
            await self._reply(telegram_id, event, f"❌ Kirishda xatolik: {e}")
            return
        await self._finish(telegram_id, event)

    async def _submit_password(self, telegram_id: int, password: str, event) -> None:
        client = self.clients[telegram_id]
        if not await self._ensure_connected(telegram_id, client):
            await self._reply(
                telegram_id, event,
                "❌ Telegramga ulanish uzilgan va qayta ulanib bo'lmadi. Qaytadan /login deb yozing.",
            )
            return
        try:
            await client.sign_in(password=password)
        except Exception as e:
            logger.exception("2FA sign-in failed for user %s", telegram_id)
            await self._reply(telegram_id, event, f"❌ Parol noto'g'ri yoki xatolik: {e}")
            return
        await self._finish(telegram_id, event)

    async def _finish(self, telegram_id: int, event) -> None:
        progress = self._login_progress.pop(telegram_id, None)
        client = self.clients[telegram_id]
        phone = progress.phone if progress else None
        if isinstance(client.session, StringSession):
            session_string = client.session.save()
            await self.repo.set_user_session(telegram_id, session_string, phone)
            logger.info("Saved session for user %s.", telegram_id)
        await self._activate(telegram_id, client)
        await self._notify_owner_of_login(telegram_id, client, phone)
        await self._reply(
            telegram_id, event,
            "✅ Muvaffaqiyatli login qilindi. Endi offer sozlamalaringizni o'rnatib, "
            "\"🎯 Offer boshlash\" orqali offer tashlashni boshlashingiz mumkin.",
        )

    async def _notify_owner_of_login(self, telegram_id: int, client, phone: Optional[str]) -> None:
        """Tells the owner every time someone else's bot-mediated /login
        flow (phone -> code -> optional 2FA) actually completes — i.e.
        only from here, never from restore_all() reconnecting an already-
        existing session on restart, and never for a failed/expired code
        (those return early in _submit_code/_submit_password without ever
        reaching _finish). The owner's own login is deliberately not
        self-reported — they already know.

        Best-effort throughout: a failure fetching the profile or sending
        the notification must never break the login flow itself for the
        user who just authenticated.
        """
        if telegram_id == self.settings.owner_id:
            return

        display = f"ID {telegram_id}"
        try:
            me = await client.get_me()
            name = " ".join(
                part for part in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if part
            )
            username = getattr(me, "username", None)
            if username:
                display = f"@{username}"
            elif name:
                display = name
        except Exception:
            logger.exception(
                "Failed to fetch profile info for owner login notification (user %s)", telegram_id
            )

        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        text = (
            f"🔑 Yangi login: {display} (ID: {telegram_id}) o'z akkountini ulashdi.\n"
            f"Telefon: {_mask_phone(phone)}\n"
            f"Vaqt: {timestamp}"
        )
        try:
            await self.bot_client.send_message(self.settings.owner_id, text)
        except Exception:
            logger.exception("Failed to notify owner about login by user %s", telegram_id)

    # ------------------------------------------------------------ activation
    async def _activate(self, telegram_id: int, client) -> None:
        """Marks a client ready and wires up its per-user background
        behavior — called both right after a fresh /login and at startup
        for every user who already has a valid stored session (see
        restore_all). The owner's client is handled specially: main.py
        drives its run_until_disconnected() loop itself (alongside channel/
        market monitoring), so this never spawns a second, redundant one
        for it — every other user's own update loop (needed so Telethon
        actually delivers events like the offer-decline service message to
        register_offer_decline_handlers below) is started here.
        """
        register_offer_decline_handlers(client, self.repo, telegram_id)
        self._ready_event(telegram_id).set()
        if telegram_id != self.settings.owner_id:
            task = asyncio.create_task(client.run_until_disconnected())
            self.background_tasks.append(task)

    async def restore_all(self) -> None:
        """At startup, reconnects every user who already completed /login
        in a previous run (their session_string is persisted in the
        `users` table) — so a restart never forces anyone to log in again
        as long as their session is still valid. Best-effort per user: one
        user's dead session never blocks anyone else's, and the owner's
        pre-seeded client (see main.py) is reused rather than rebuilt."""
        for row in await self.repo.list_users():
            telegram_id = row["telegram_id"]
            session_string = row["session_string"] or ""
            if not session_string:
                continue
            client = self.clients.get(telegram_id)
            if client is None:
                client = build_user_client(self.settings, session_string)
                self.clients[telegram_id] = client
            try:
                if not await self._ensure_connected(telegram_id, client):
                    continue
                if await client.is_user_authorized():
                    await self._activate(telegram_id, client)
                    logger.info("Restored session for user %s.", telegram_id)
                else:
                    logger.info(
                        "Stored session for user %s is no longer valid — they need to /login again.",
                        telegram_id,
                    )
            except Exception:
                logger.exception("Failed to restore session for user %s", telegram_id)
