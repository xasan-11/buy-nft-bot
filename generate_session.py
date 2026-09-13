"""One-time interactive login that produces a StringSession.

Run this once, locally, on a machine with a real terminal:

    python generate_session.py

You will be prompted for the login code Telegram sends you, and for your
2FA password if you have one enabled (hidden input, never logged or sent
anywhere). On success, the whole login session is printed as one string —
paste it into Railway (or wherever the bot runs) as the TELEGRAM_SESSION_STRING
environment variable. No .session file is written; the string alone is
enough to authenticate the account.
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()


async def main() -> None:
    api_id_raw = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")

    missing = [
        name
        for name, val in [
            ("TELEGRAM_API_ID", api_id_raw),
            ("TELEGRAM_API_HASH", api_hash),
            ("TELEGRAM_PHONE", phone),
        ]
        if not val
    ]
    if missing:
        raise SystemExit(
            "Missing required environment variables: " + ", ".join(missing)
        )

    async with TelegramClient(StringSession(), int(api_id_raw), api_hash) as client:
        await client.start(phone=phone)
        me = await client.get_me()
        session_string = client.session.save()

        print(f"\nLogged in as {me.first_name} (id={me.id}).\n")
        print("TELEGRAM_SESSION_STRING value (copy the line below into Railway Variables):\n")
        print(session_string)
        print()


if __name__ == "__main__":
    asyncio.run(main())
