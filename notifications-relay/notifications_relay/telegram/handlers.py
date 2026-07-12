from __future__ import annotations

import uuid

from telegram import Update
from telegram.ext import ContextTypes

from notifications_relay.core.pairing import PairingService
from notifications_relay.exceptions import PairingExpiredError, PairingNotFoundError
from notifications_relay.store import Store
from notifications_relay.telegram.config import TelegramConfig
from notifications_relay.telegram.security import is_allowed


async def pair_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pairing_service: PairingService,
    config: TelegramConfig,
) -> None:
    """Handle ``/pair <token>`` from Telegram."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    if user is None or chat is None or message is None:
        return

    if not is_allowed(user.id, config.allowed_user_ids):
        await message.reply_text("You are not authorized to pair with this bot.")
        return

    args = context.args or []
    if len(args) != 1:
        await message.reply_text("Usage: /pair <token>")
        return

    token = args[0]
    try:
        await pairing_service.claim(token, str(user.id), str(chat.id))
    except PairingNotFoundError:
        await message.reply_text("Invalid or already-used pairing token.")
        return
    except PairingExpiredError:
        await message.reply_text("This pairing token has expired.")
        return

    await message.reply_text("Pairing complete. You will now receive notifications here.")


async def unpair_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    store: Store,
    config: TelegramConfig,
) -> None:
    """Handle ``/unpair`` from Telegram."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    if user is None or chat is None or message is None:
        return

    if not is_allowed(user.id, config.allowed_user_ids):
        await message.reply_text("You are not authorized to use this bot.")
        return

    pairing = await store.get_pairing_by_telegram_user_id(str(user.id))
    if pairing is None:
        await message.reply_text("No active pairing found for your account.")
        return

    await store.revoke_pairing(pairing["token"])
    await message.reply_text("Your pairing has been removed.")


async def reply_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    store: Store,
    inbox_ttl_seconds: int,
) -> None:
    """Store Telegram replies to bot messages in the inbox."""
    message = update.message
    if message is None or message.reply_to_message is None:
        return

    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    pairing = await store.get_pairing_by_telegram_user_id(str(user.id))
    if pairing is None:
        return

    mapping = await store.get_question_mapping(
        chat_id=str(chat.id),
        telegram_message_id=str(message.reply_to_message.message_id),
    )
    if mapping is None:
        return

    await store.create_inbox(
        message_id=str(uuid.uuid4()),
        token=mapping["token"],
        telegram_user_id=str(user.id),
        chat_id=str(chat.id),
        question_id=mapping["question_id"],
        text=message.text or "",
        ttl_seconds=inbox_ttl_seconds,
    )
