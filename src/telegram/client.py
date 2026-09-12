from __future__ import annotations

from telethon import TelegramClient

from ..config.settings import Settings


def build_user_client(settings: Settings) -> TelegramClient:
    """The account being monitored/used to purchase. Session persists to disk
    under data/, which is gitignored and never logged."""
    session_path = settings.session_dir / "user_session"
    return TelegramClient(str(session_path), settings.api_id, settings.api_hash)


def build_bot_client(settings: Settings) -> TelegramClient:
    """The admin-only control bot (separate Bot API account via BotFather)."""
    session_path = settings.session_dir / "bot_session"
    return TelegramClient(str(session_path), settings.api_id, settings.api_hash)
