from __future__ import annotations

import asyncio
import logging
import threading

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from notifications_relay.core.pairing import PairingService
from notifications_relay.store import Store
from notifications_relay.telegram.config import TelegramConfig
from notifications_relay.telegram.handlers import pair_handler, reply_handler, unpair_handler

logger = logging.getLogger(__name__)


class TelegramBot:
    """Lifecycle manager for the python-telegram-bot polling loop."""

    def __init__(
        self,
        token: str,
        store: Store,
        pairing_service: PairingService,
        inbox_ttl_seconds: int,
        config: TelegramConfig,
    ) -> None:
        self.token = token
        self.store = store
        self.pairing_service = pairing_service
        self.inbox_ttl_seconds = inbox_ttl_seconds
        self.config = config
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the bot polling loop in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the bot loop to stop and wait for the thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_app())
        finally:
            loop.close()

    async def _run_app(self) -> None:
        application = Application.builder().token(self.token).build()

        application.add_handler(
            CommandHandler(
                "pair",
                lambda update, context: pair_handler(update, context, self.pairing_service, self.config),
            )
        )
        application.add_handler(
            CommandHandler(
                "unpair",
                lambda update, context: unpair_handler(update, context, self.store, self.config),
            )
        )
        application.add_handler(
            MessageHandler(
                filters.REPLY & ~filters.COMMAND,
                lambda update, context: reply_handler(update, context, self.store, self.inbox_ttl_seconds),
            )
        )

        await application.initialize()
        updater = application.updater
        if updater is None:
            await application.shutdown()
            return

        await updater.start_polling()
        await application.start()

        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

        await application.stop()
        await updater.stop()
        await application.shutdown()
