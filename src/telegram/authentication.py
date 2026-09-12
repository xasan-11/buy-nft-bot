from __future__ import annotations

from ..config.settings import Settings
from .client import build_user_client


async def run_login(settings: Settings) -> None:
    """One-time interactive login for the monitored/purchasing account.

    Telethon prompts for the login code on stdin and, if the account has
    two-factor auth enabled, the password via getpass (hidden input, never
    echoed or logged). No credential is ever sent to or requested by the
    control bot. Once this succeeds, a session file is stored under data/
    and subsequent app runs reuse it without prompting.
    """
    client = build_user_client(settings)
    await client.start(phone=settings.phone)
    me = await client.get_me()
    print(f"Logged in as {me.first_name} (id={me.id}). Session saved to data/user_session.session")
    await client.disconnect()
