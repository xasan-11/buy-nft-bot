from __future__ import annotations

from telethon import events

from ..monitoring.channel_monitor import ChannelMonitor


def register_handlers(user_client, monitor: ChannelMonitor) -> None:
    """Wires Telethon's event-driven updates (no polling) to the monitor.

    Handles new posts and edited posts (e.g. a price change or a listing
    being marked sold). Deleted posts are handled separately so a pending
    purchase for a listing whose post just disappeared can be short-circuited
    before it reaches the purchase stage.
    """

    async def _dispatch(event) -> None:
        chat_id = event.chat_id
        if chat_id is None or chat_id not in monitor.channel_ids:
            return
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        await monitor.handle_message(chat_id, username, event.id, event.raw_text or "")

    @user_client.on(events.NewMessage())
    async def _on_new_message(event):
        await _dispatch(event)

    @user_client.on(events.MessageEdited())
    async def _on_edited_message(event):
        await _dispatch(event)

    @user_client.on(events.MessageDeleted())
    async def _on_deleted_message(event):
        if event.chat_id is None or event.chat_id not in monitor.channel_ids:
            return
        for message_id in event.deleted_ids:
            await monitor.handle_deleted_message(event.chat_id, message_id)
