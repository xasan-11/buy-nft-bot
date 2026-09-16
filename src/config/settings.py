from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    phone: Optional[str]
    session_string: str
    bot_token: str
    owner_id: int
    max_nft_price: int
    poll_interval: int
    market_poll_concurrency: int
    market_poll_interval_seconds: int
    session_dir: Path
    db_path: Path
    connection_retries: int
    retry_delay: int
    connection_timeout: int

    @classmethod
    def load(cls) -> "Settings":
        api_id_raw = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        # Optional, and only ever used as the owner's own phone number (see
        # telegram/user_manager.py) — if unset, the control bot asks the
        # owner for it via /login <phone> instead of assuming one. Every
        # other user always supplies their own phone via /login.
        phone = os.getenv("TELEGRAM_PHONE") or None
        bot_token = os.getenv("CONTROL_BOT_TOKEN")
        owner_raw = os.getenv("OWNER_TELEGRAM_ID")

        missing = [
            name
            for name, val in [
                ("TELEGRAM_API_ID", api_id_raw),
                ("TELEGRAM_API_HASH", api_hash),
                ("CONTROL_BOT_TOKEN", bot_token),
                ("OWNER_TELEGRAM_ID", owner_raw),
            ]
            if not val
        ]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        data_dir = BASE_DIR / "data"
        data_dir.mkdir(exist_ok=True)

        return cls(
            api_id=int(api_id_raw),
            api_hash=api_hash,
            phone=phone,
            session_string=os.getenv("TELEGRAM_SESSION_STRING", ""),
            bot_token=bot_token,
            owner_id=int(owner_raw),
            max_nft_price=_int_env("MAX_NFT_PRICE", 200),
            poll_interval=_int_env("POLL_INTERVAL", 1),
            market_poll_concurrency=_int_env("MARKET_POLL_CONCURRENCY", 3),
            market_poll_interval_seconds=_int_env("MARKET_POLL_INTERVAL_SECONDS", 5),
            session_dir=data_dir,
            db_path=data_dir / "app.db",
            # Telethon's MTProto connection retries/backoff — bumped above
            # Telethon's own defaults (5 retries / 1s delay) since an
            # unstable network otherwise leaves the client permanently
            # disconnected mid-login (see telegram/user_manager.py).
            connection_retries=_int_env("TELEGRAM_CONNECTION_RETRIES", 10),
            retry_delay=_int_env("TELEGRAM_RETRY_DELAY", 2),
            connection_timeout=_int_env("TELEGRAM_CONNECTION_TIMEOUT", 10),
        )
