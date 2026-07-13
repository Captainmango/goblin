from __future__ import annotations

from typing import Any

from openhands.sdk import LLM

from goblin.utils.config import get_config

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


def build_llm(model: str | dict | None = None) -> LLM:
    """Construct an LLM from central configuration or a resolved config dict.

    Required: GOBLIN_MODEL_API_KEY (legacy fallback: MODEL_API_KEY)
    Optional: GOBLIN_BASE_URL (legacy fallback: OPENHANDS_BASE_URL)

    When *model* is a dict it is treated as a resolved OpenHands LLM
    configuration object. Missing `api_key` and `base_url` values fall back to
    the environment variables above. String *model* values are treated as the
    model identifier only and fall back to environment-based credentials.
    """
    if isinstance(model, dict):
        kwargs: dict[str, Any] = dict(model)
        if not kwargs.get("model"):
            kwargs["model"] = _DEFAULT_MODEL
        config = get_config()
        if not kwargs.get("api_key"):
            api_key = config.model_api_key
            if api_key is not None:
                kwargs["api_key"] = api_key
        if not kwargs.get("base_url"):
            kwargs["base_url"] = config.base_url
        return LLM(**kwargs)

    config = get_config()
    return LLM(
        model=model or _DEFAULT_MODEL,
        api_key=config.require_model_api_key(),
        base_url=config.base_url,
    )
