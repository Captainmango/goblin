from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from telegram import Bot

from notifications_relay.api import health, inbox, pairings, send
from notifications_relay.config import RelayConfig, get_config
from notifications_relay.core.dispatcher import NotificationDispatcher
from notifications_relay.core.inbox import InboxService
from notifications_relay.core.pairing import PairingService
from notifications_relay.store import Store
from notifications_relay.telegram.bot import TelegramBot
from notifications_relay.telegram.config import TelegramConfig
from notifications_relay.telegram.provider import TelegramNotificationProvider


def create_app(config: RelayConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    config = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        store = Store(config.db_path)
        await store.connect()
        app.state.store = store
        app.state.config = config

        pairing_service = PairingService(
            store,
            token_ttl_seconds=config.pairing_token_ttl_seconds,
        )
        app.state.pairing_service = pairing_service

        inbox_service = InboxService(
            store,
            ttl_seconds=config.question_mapping_ttl_seconds,
        )
        app.state.inbox_service = inbox_service

        dispatcher = NotificationDispatcher(
            store,
            mapping_ttl_seconds=config.question_mapping_ttl_seconds,
        )
        app.state.dispatcher = dispatcher

        telegram_config = TelegramConfig.from_env()
        telegram_bot: TelegramBot | None = None
        if telegram_config.bot_token:
            bot = Bot(telegram_config.bot_token)
            provider = TelegramNotificationProvider(
                bot=bot,
                store=store,
                config=telegram_config,
                mapping_ttl_seconds=config.question_mapping_ttl_seconds,
            )
            dispatcher.register("telegram", provider)
            telegram_bot = TelegramBot(
                token=telegram_config.bot_token,
                store=store,
                pairing_service=pairing_service,
                inbox_ttl_seconds=config.question_mapping_ttl_seconds,
                config=telegram_config,
            )
            telegram_bot.start()

        try:
            yield
        finally:
            if telegram_bot is not None:
                telegram_bot.stop()
            await store.close()

    app = FastAPI(
        title="Notifications Relay",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(pairings.router)
    app.include_router(send.router)
    app.include_router(inbox.router)

    return app
