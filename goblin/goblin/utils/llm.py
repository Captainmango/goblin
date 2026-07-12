from openhands.sdk import LLM

from goblin.utils.config import get_config

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


def build_llm(model: str | None = None) -> LLM:
    """Construct an LLM from central configuration.

    Required: GOBLIN_MODEL_API_KEY (legacy fallback: MODEL_API_KEY)
    Optional: GOBLIN_BASE_URL (legacy fallback: OPENHANDS_BASE_URL)
    """
    config = get_config()
    return LLM(
        model=model or _DEFAULT_MODEL,
        api_key=config.require_model_api_key(),
        base_url=config.base_url,
    )
