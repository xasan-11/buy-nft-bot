from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
    phone: str
    bot_token: str
    owner_id: int
    max_nft_price: int
    poll_interval: int
    market_poll_concurrency: int
    market_poll_interval_seconds: int
    session_dir: Path
    db_path: Path

    @classmethod
    def load(cls) -> "Settings":
        api_id_raw = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        phone = os.getenv("TELEGRAM_PHONE")
        bot_token = os.getenv("CONTROL_BOT_TOKEN")
        owner_raw = os.getenv("OWNER_TELEGRAM_ID")

        missing = [
            name
            for name, val in [
                ("TELEGRAM_API_ID", api_id_raw),
                ("TELEGRAM_API_HASH", api_hash),
                ("TELEGRAM_PHONE", phone),
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
            bot_token=bot_token,
            owner_id=int(owner_raw),
            max_nft_price=_int_env("MAX_NFT_PRICE", 200),
            poll_interval=_int_env("POLL_INTERVAL", 1),
            market_poll_concurrency=_int_env("MARKET_POLL_CONCURRENCY", 3),
            market_poll_interval_seconds=_int_env("MARKET_POLL_INTERVAL_SECONDS", 5),
            session_dir=data_dir,
            db_path=data_dir / "app.db",
        )
