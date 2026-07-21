"""Tests for :func:`goblin.output_schema.response_format_payload` and provider mapping."""

from __future__ import annotations

from pydantic import BaseModel, Field

from goblin.output_schema import (
    PhaseResult,
    provider_response_format_for_prefix,
    response_format_payload,
)


class _Summary(BaseModel):
    title: str
    score: float = Field(ge=0.0)


class TestResponseFormatPayload:
    def test_basic_payload_shape(self):
        payload = response_format_payload(PhaseResult)
        assert payload["type"] == "json_schema"
        schema = payload["json_schema"]
        assert schema["name"] == "PhaseResult"
        assert schema["strict"] is False
        assert "properties" in schema["schema"]
        assert "success" in schema["schema"]["properties"]

    def test_custom_name(self):
        payload = response_format_payload(PhaseResult, name="MyName")
        assert payload["json_schema"]["name"] == "MyName"

    def test_strict_flag_propagated(self):
        payload = response_format_payload(PhaseResult, strict=True)
        assert payload["json_schema"]["strict"] is True

    def test_user_model_includes_fields(self):
        payload = response_format_payload(_Summary)
        assert "title" in payload["json_schema"]["schema"]["properties"]
        assert "score" in payload["json_schema"]["schema"]["properties"]


class TestProviderResponseFormat:
    def test_openai_uses_response_format(self):
        fmt = provider_response_format_for_prefix(PhaseResult, provider_prefix="openai/gpt-4o")
        kwargs: dict = {}
        fmt.apply_to_llm_kwargs(kwargs)
        assert "response_format" in kwargs["litellm_extra_body"]
        assert kwargs["litellm_extra_body"]["response_format"]["type"] == "json_schema"

    def test_vllm_uses_guided_json(self):
        fmt = provider_response_format_for_prefix(PhaseResult, provider_prefix="vllm/my-model")
        kwargs: dict = {}
        fmt.apply_to_llm_kwargs(kwargs)
        assert "guided_json" in kwargs["litellm_extra_body"]
        assert "response_format" not in kwargs["litellm_extra_body"]
        # The guided_json payload should include PhaseResult's properties
        assert "success" in kwargs["litellm_extra_body"]["guided_json"]["properties"]

    def test_gemini_uses_response_schema(self):
        fmt = provider_response_format_for_prefix(PhaseResult, provider_prefix="gemini/pro")
        kwargs: dict = {}
        fmt.apply_to_llm_kwargs(kwargs)
        body = kwargs["litellm_extra_body"]
        assert "response_schema" in body
        assert body["response_mime_type"] == "application/json"

    def test_vertex_ai_uses_response_schema(self):
        fmt = provider_response_format_for_prefix(PhaseResult, provider_prefix="vertex_ai/proj")
        kwargs: dict = {}
        fmt.apply_to_llm_kwargs(kwargs)
        assert "response_schema" in kwargs["litellm_extra_body"]

    def test_anthropic_falls_back_to_json_schema(self):
        # Anthropic may drop response_format; we still ship it so providers that
        # do honour it work, and Layer 3 catches the cases that don't.
        fmt = provider_response_format_for_prefix(PhaseResult, provider_prefix="anthropic/claude")
        kwargs: dict = {}
        fmt.apply_to_llm_kwargs(kwargs)
        assert "response_format" in kwargs["litellm_extra_body"]

    def test_unknown_prefix_uses_json_schema(self):
        fmt = provider_response_format_for_prefix(PhaseResult, provider_prefix="unknown/model")
        kwargs: dict = {}
        fmt.apply_to_llm_kwargs(kwargs)
        assert "response_format" in kwargs["litellm_extra_body"]

    def test_override_format_beats_default(self):
        # Even for an OpenAI provider, an explicit override switches the key.
        fmt = provider_response_format_for_prefix(
            PhaseResult, provider_prefix="openai/gpt-4o", override_format="guided_json"
        )
        kwargs: dict = {}
        fmt.apply_to_llm_kwargs(kwargs)
        assert "guided_json" in kwargs["litellm_extra_body"]
        assert "response_format" not in kwargs["litellm_extra_body"]

    def test_existing_extra_body_preserved(self):
        fmt = provider_response_format_for_prefix(PhaseResult, provider_prefix="openai/gpt-4o")
        kwargs: dict = {"litellm_extra_body": {"custom_key": "value"}}
        fmt.apply_to_llm_kwargs(kwargs)
        assert kwargs["litellm_extra_body"]["custom_key"] == "value"
        assert "response_format" in kwargs["litellm_extra_body"]

    def test_none_prefix_uses_default_json_schema(self):
        fmt = provider_response_format_for_prefix(PhaseResult, provider_prefix=None)
        kwargs: dict = {}
        fmt.apply_to_llm_kwargs(kwargs)
        assert "response_format" in kwargs["litellm_extra_body"]


class TestBuildLlmProviderExtras:
    """End-to-end check that build_llm wires the right payload per provider."""

    def test_openai_emits_response_format(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "test")
        from goblin.utils.llm import build_llm

        llm = build_llm(model="openai/gpt-4o", output_model=PhaseResult)
        assert "response_format" in llm.litellm_extra_body

    def test_vllm_emits_guided_json(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "test")
        from goblin.utils.llm import build_llm

        llm = build_llm(model="vllm/my-model", output_model=PhaseResult)
        assert "guided_json" in llm.litellm_extra_body

    def test_gemini_emits_response_schema(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "test")
        from goblin.utils.llm import build_llm

        llm = build_llm(model="gemini/gemini-2.0", output_model=PhaseResult)
        assert "response_schema" in llm.litellm_extra_body
        assert llm.litellm_extra_body["response_mime_type"] == "application/json"

    def test_explicit_response_format_overrides_output_model(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "test")
        from goblin.utils.llm import build_llm

        explicit = {"type": "json_object"}
        llm = build_llm(model="openai/gpt-4o", output_model=PhaseResult, response_format=explicit)
        assert llm.litellm_extra_body["response_format"] == explicit

    def test_no_output_model_no_extras(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "test")
        from goblin.utils.llm import build_llm

        llm = build_llm(model="openai/gpt-4o")
        assert llm.litellm_extra_body == {}
