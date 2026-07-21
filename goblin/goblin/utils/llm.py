from __future__ import annotations

from typing import Any

from openhands.sdk import LLM
from pydantic import BaseModel

from goblin.output_schema import provider_response_format_for_prefix
from goblin.utils.config import get_config


def _effective_model(model: str | None) -> str:
    """Return the model identifier to use, falling back to GOBLIN_DEFAULT_MODEL."""
    config = get_config()
    effective = model or config.default_model
    if not effective:
        raise RuntimeError("No model configured. Set GOBLIN_DEFAULT_MODEL or specify a model in the blueprint.")
    return effective


def _model_id(model: str | dict | None) -> str | None:
    """Pull the litellm-style model identifier out of a resolved config."""
    if isinstance(model, dict):
        return model.get("model")
    return model


def build_llm(
    model: str | dict | None = None,
    *,
    output_model: type[BaseModel] | None = None,
    response_format: dict | None = None,
) -> LLM:
    """Construct an LLM from central configuration or a resolved config dict.

    Required: GOBLIN_MODEL_API_KEY (legacy fallback: MODEL_API_KEY)
    Optional: GOBLIN_BASE_URL (legacy fallback: OPENHANDS_BASE_URL)
    Optional: GOBLIN_DEFAULT_MODEL (fallback when no model is provided)

    When *model* is a dict it is treated as a resolved OpenHands LLM
    configuration object. Missing `model`, `api_key` and `base_url` values fall
    back to the environment variables above. String *model* values are treated
    as the model identifier only and fall back to environment-based credentials.
    If no model can be resolved, a RuntimeError is raised.

    Structured output:

    - If *response_format* is provided, attach it to the LLM so litellm picks
      it up on the next completion. This is the follow-up tool-less Layer 2
      call shape (see :mod:`goblin.phase`).
    - If *output_model* is provided and no explicit *response_format* was
      given, resolve the provider's preferred structured-output key
      (``vllm/`` → ``guided_json``, ``gemini/`` → ``response_schema``, ...)
      and attach it as either ``response_format`` or
      ``litellm_extra_body`` depending on the provider.
    """
    if isinstance(model, dict):
        kwargs: dict[str, Any] = dict(model)
        kwargs["model"] = _effective_model(kwargs.get("model"))
        config = get_config()
        if not kwargs.get("api_key"):
            api_key = config.model_api_key
            if api_key is not None:
                kwargs["api_key"] = api_key
        if not kwargs.get("base_url"):
            kwargs["base_url"] = config.base_url
    else:
        config = get_config()
        kwargs = {
            "model": _effective_model(model),
            "api_key": config.require_model_api_key(),
            "base_url": config.base_url,
        }

    if response_format is not None:
        # User supplied response_format wins over output_model. Place it in
        # litellm_extra_body because that's the only provider-side hook the
        # OpenHands SDK exposes.
        merged = dict(kwargs.get("litellm_extra_body") or {})
        merged["response_format"] = response_format
        kwargs["litellm_extra_body"] = merged
    elif output_model is not None:
        provider_prefix = _model_id(kwargs.get("model") or model)
        prefix = provider_prefix.split("/", 1)[0] if isinstance(provider_prefix, str) else None
        fmt = provider_response_format_for_prefix(output_model, provider_prefix=prefix)
        fmt.apply_to_llm_kwargs(kwargs)

    return LLM(**kwargs)
