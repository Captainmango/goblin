from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class RelayConfig(BaseModel):
    """Runtime configuration for the notifications relay."""

    api_key: str = Field(..., min_length=1)
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: Path = Path(".goblin/relay.db")
    pairing_token_ttl_seconds: int = 900
    question_mapping_ttl_seconds: int = 3600

    @field_validator("db_path", mode="before")
    @classmethod
    def _coerce_db_path(cls, value: str | os.PathLike[str]) -> Path:
        return Path(value)

    @classmethod
    def from_env(cls) -> RelayConfig:
        """Load configuration from environment variables."""
        api_key = os.environ.get("RELAY_API_KEY", "")
        return cls(
            api_key=api_key,
            host=os.environ.get("RELAY_HOST", "127.0.0.1"),
            port=int(os.environ.get("RELAY_PORT", "8000")),
            db_path=os.environ.get("RELAY_DB_PATH", ".goblin/relay.db"),
            pairing_token_ttl_seconds=int(os.environ.get("RELAY_PAIRING_TOKEN_TTL_SECONDS", "900")),
            question_mapping_ttl_seconds=int(os.environ.get("RELAY_QUESTION_MAPPING_TTL_SECONDS", "3600")),
        )
