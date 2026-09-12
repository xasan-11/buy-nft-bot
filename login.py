"""One-time interactive login for the monitored Telegram user account.

Run this once before starting the application:

    python login.py

You will be prompted for the login code Telegram sends you, and for your
2FA password if you have one enabled (hidden input, never logged or sent
anywhere). The resulting session is saved under data/user_session.session
and reused by the main application afterwards.
"""

from __future__ import annotations

import asyncio

from src.config.settings import Settings
from src.telegram.authentication import run_login


def main() -> None:
    settings = Settings.load()
    asyncio.run(run_login(settings))


if __name__ == "__main__":
    main()
