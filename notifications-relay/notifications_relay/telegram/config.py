from __future__ import annotations

from goblin_common.config import secret_env


class TelegramConfig:
    """Telegram-specific configuration loaded from environment.

    The provider is optional: when ``TELEGRAM_BOT_TOKEN`` is unset the config
    still loads with :attr:`bot_token` set to ``None`` so the relay can start
    without Telegram support. Callers that require Telegram (e.g. the outbound
    provider) should raise an error themselves rather than forcing every
    lifespan startup to provide a token.
    """

    def __init__(self, bot_token: str | None, allowed_user_ids: set[int]) -> None:
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids

    @classmethod
    def from_env(cls) -> TelegramConfig:
        """Load Telegram configuration from environment variables."""
        bot_token = secret_env("TELEGRAM_BOT_TOKEN")
        raw_ids = secret_env("TELEGRAM_ALLOWED_USER_IDS")
        allowed_user_ids: set[int] = set()
        bot_token = bot_token.get_secret_value() if bot_token else None

        if raw_ids:
            allowed_user_ids = {int(uid.strip()) for uid in raw_ids.get_secret_value().split(",") if uid.strip()}
        return cls(bot_token=bot_token, allowed_user_ids=allowed_user_ids)
