from __future__ import annotations

from goblin_common.config import secret_env
from notifications_relay.exceptions import RelayError


class TelegramConfig:
    """Telegram-specific configuration loaded from environment."""

    def __init__(self, bot_token: str, allowed_user_ids: set[int]) -> None:
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids

    @classmethod
    def from_env(cls) -> TelegramConfig:
        """Load Telegram configuration from environment variables."""
        bot_token = secret_env("TELEGRAM_BOT_TOKEN")
        raw_ids = secret_env("TELEGRAM_ALLOWED_USER_IDS")
        allowed_user_ids: set[int] = set()
        bot_token = bot_token.get_secret_value() if bot_token else None

        if bot_token is None:
            raise RelayError("TELGRAM_BOT_TOKEN is a required envvar for the Telegram provider")
        if raw_ids:
            allowed_user_ids = {int(uid.strip()) for uid in raw_ids.get_secret_value().split(",") if uid.strip()}
        return cls(bot_token=bot_token, allowed_user_ids=allowed_user_ids)
        
