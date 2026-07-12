from __future__ import annotations

import os
from goblin_common.config import secret_env


class TelegramConfig:
    """Telegram-specific configuration loaded from environment."""

    def __init__(self, bot_token: str | None, allowed_user_ids: set[int]) -> None:
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids

    @classmethod
    def from_env(cls) -> TelegramConfig:
        """Load Telegram configuration from environment variables."""
        bot_token = secret_env("TELEGRAM_BOT_TOKEN")
        raw_ids = secret_env("TELEGRAM_ALLOWED_USER_IDS")
        allowed_user_ids: set[int] = set()
        if raw_ids:
            allowed_user_ids = {int(uid.strip()) for uid in raw_ids.split(",") if uid.strip()}
        return cls(bot_token=bot_token, allowed_user_ids=allowed_user_ids)
