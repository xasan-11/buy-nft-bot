from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from ..config.settings import Settings


def build_user_client(settings: Settings, session_string: str = "") -> TelegramClient:
    """The account being monitored/used to purchase.

    Uses a StringSession (the whole login session as one string) instead of
    a file on disk: a host with no interactive terminal (Railway etc.) has
    nowhere to type the login code Telegram sends on first auth, and an
    ephemeral/non-persistent filesystem would lose a file-based session
    across redeploys anyway.

    An empty session_string is a valid, expected input — it produces a
    fresh, unauthenticated client. main.run() resolves the actual string to
    pass here (a previously completed /login flow's session, stored in the
    database, takes precedence over the TELEGRAM_SESSION_STRING env var
    from generate_session.py) and, if the client comes back unauthenticated
    after connecting, drives login through the control bot instead of
    failing — see telegram/user_manager.py.
    """
    return TelegramClient(StringSession(session_string), settings.api_id, settings.api_hash)


def build_bot_client(settings: Settings) -> TelegramClient:
    """The admin-only control bot (separate Bot API account via BotFather)."""
    session_path = settings.session_dir / "bot_session"
    return TelegramClient(str(session_path), settings.api_id, settings.api_hash)
