import os

from openhands.sdk import LLM
from pydantic import SecretStr

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"


def build_llm(model: str | None = None) -> LLM:
    """Construct an LLM from environment variables.

    Required: OPENHANDS_MODEL, OPENHANDS_API_KEY
    Optional: OPENHANDS_BASE_URL
    """
    return LLM(
        model=model or _DEFAULT_MODEL,
        api_key=SecretStr(os.environ["MODEL_API_KEY"]),
        base_url=os.environ.get("OPENHANDS_BASE_URL"),
    )
