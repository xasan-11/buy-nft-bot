from __future__ import annotations

from ..database.repository import Repository

UNAUTHORIZED_MESSAGE = "⛔ You are not authorized to use this bot."


async def is_authorized(repo: Repository, user_id: int) -> bool:
    return await repo.is_admin(user_id)


async def is_owner(repo: Repository, user_id: int) -> bool:
    return await repo.is_owner(user_id)
