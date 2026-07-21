"""Output-shape logic for goblin agents.

Single home for everything related to the data shape an agent returns at the
end of a phase or a swarm coordinator run:

- ``PhaseResult`` / ``SwarmCoordinatorResult`` — the canonical Pydantic models
  used as defaults.
- ``resolve_output_model(blueprint, phase)`` — turns a blueprint phase's
  ``output_model`` / ``output_schema`` declaration into a concrete Pydantic
  class the runner can validate against.
- ``response_format_payload(model)`` — the OpenAI-flavoured JSON-schema
  ``response_format`` dict litellm expects.
- ``provider_response_format(model, provider_prefix)`` — translates the
  above into provider-native structured-output keys (``vllm``,
  ``gemini``, etc.) and writes the result onto ``litellm_extra_body``.
- ``coerce_to_model(text, model)`` — three-tier textual fallback for the
  cases where the in-loop typed Finish tool didn't fire.
- ``validate_against_schema(data, schema)`` — defence-in-depth JSON Schema
  check kept for blueprints that still declare an inline ``output_schema``.

Pydantic is the canonical source of truth for output shapes. JSON Schema
fragments in blueprints are accepted as a convenience for declarative
blueprints but are generated into Pydantic at load time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

if TYPE_CHECKING:
    from goblin.blueprint import V1Blueprint
    from goblin.phase import PhaseProtocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical result models
# ---------------------------------------------------------------------------


class PhaseResult(BaseModel):
    """Default output shape for a prompt phase.

    ``success`` and ``message`` are the contract goblin relies on for phase
    results: orchestrator skip/retry/abort logic, downstream Jinja conditions
    (``steps.<id>.success``), and the human-readable message stream.

    ``extra="allow"`` lets user-defined ``output_model`` classes extend this
    contract with arbitrary additional fields (typed via ``result`` in the
    Finish tool) without forcing every consumer to know about them up front.
    """

    model_config = ConfigDict(extra="allow")

    success: bool
    message: list[str]


class SwarmCoordinatorResult(BaseModel):
    """Default output shape for the swarm coordinator's per-round evaluation.

    The coordinator's only job each round is to decide whether the goal is
    met (``done=true``) or more tasks are still required (``done=false``)
    and emit a short human-readable summary. Worker agents and their DB-
    bound ``complete_task`` calls are the authoritative source of truth for
    individual task progress.
    """

    model_config = ConfigDict(extra="allow")

    done: bool
    message: list[str]


# ---------------------------------------------------------------------------
# Provider prefix → response format translation
# ---------------------------------------------------------------------------


class _ProviderResponseFormat:
    """Translate a Pydantic model into a provider-native structured-output dict.

    Returned by :func:`provider_response_format_for_prefix`. Use
    :meth:`apply_to_llm_kwargs` to merge the result onto an LLM kwargs dict
    (e.g. ``LLM(...)`` constructor arguments).

    The OpenHands SDK exposes provider-side parameters through
    ``litellm_extra_body``, so all keys — OpenAI ``response_format``,
    vLLM ``guided_json``, Gemini ``response_schema`` — land in that one
    bucket at construction time.
    """

    def __init__(self, *, extra_body: dict | None):
        self._extra_body = extra_body or {}

    def apply_to_llm_kwargs(self, kwargs: dict) -> None:
        """Merge the provider-specific keys into *kwargs* in place."""
        if not self._extra_body:
            return
        merged = dict(kwargs.get("litellm_extra_body") or {})
        merged.update(self._extra_body)
        kwargs["litellm_extra_body"] = merged


def _schema_hash(schema: dict[str, Any]) -> str:
    """Return a short, stable hash of a JSON schema for cache keys."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def response_format_payload(model: type[BaseModel], *, strict: bool = False, name: str | None = None) -> dict:
    """Build an OpenAI-flavoured ``response_format`` dict for *model*.

    OpenAI and any litellm provider that accepts the same shape (including
    providers routed through litellm's proxy) consume this directly. Providers
    that want a different key (vLLM ``guided_json``, Gemini
    ``response_schema``, ...) translate the resulting dict via
    :func:`provider_response_format_for_prefix`.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name or model.__name__,
            "schema": model.model_json_schema(),
            "strict": strict,
        },
    }


def provider_response_format_for_prefix(
    model: type[BaseModel],
    *,
    provider_prefix: str | None,
    strict: bool = False,
    override_format: str | None = None,
) -> _ProviderResponseFormat:
    """Return the structured-output payload for the given model and provider.

    *provider_prefix* is the litellm-style prefix on the model id
    (``"vllm/"``, ``"gemini/"``, ``"openai/"``, ``"anthropic/"``,
    ``None`` for unknown). When *override_format* is provided (one of
    ``"json_schema"``, ``"guided_json"``, ``"response_schema"``) it takes
    precedence over the provider-default mapping.

    The returned payload is always placed in ``litellm_extra_body`` because
    that's the only provider-side hook the OpenHands SDK exposes.
    """
    schema = model.model_json_schema()
    chosen = override_format or _default_format_for_prefix(provider_prefix)

    if chosen == "guided_json":
        return _ProviderResponseFormat(extra_body={"guided_json": schema})
    if chosen == "response_schema":
        return _ProviderResponseFormat(
            extra_body={
                "response_schema": schema,
                "response_mime_type": "application/json",
            }
        )
    return _ProviderResponseFormat(extra_body={"response_format": response_format_payload(model, strict=strict)})


def _default_format_for_prefix(prefix: str | None) -> str:
    """Pick the provider's preferred structured-output key, by litellm prefix."""
    if not prefix:
        return "json_schema"
    base = prefix.split("/", 1)[0].lower()
    if base in {"vllm"}:
        return "guided_json"
    if base in {"gemini", "vertex_ai"}:
        return "response_schema"
    return "json_schema"


# ---------------------------------------------------------------------------
# Output model resolution
# ---------------------------------------------------------------------------


_REGISTRY: dict[str, type[BaseModel]] = {}


def register_output_model(name: str, model: type[BaseModel]) -> None:
    """Register a Pydantic model so blueprints can reference it by dotted name.

    Users opt their models in by calling
    ``goblin.output_schema.register_output_model("myapp.Summary", Summary)``
    at import time. The blueprint's ``output_model: "myapp.Summary"`` then
    resolves to the registered class without goblin doing any I/O at import.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ValueError("model must be a BaseModel subclass")
    _REGISTRY[name] = model


def resolve_output_model(blueprint: V1Blueprint, phase: PhaseProtocol) -> type[BaseModel]:
    """Return the Pydantic model that the runner should validate the agent against.

    Lookup order:

    1. ``phase.output_model`` — a string. Either a key previously registered
       via :func:`register_output_model` or an importable dotted path
       (``myapp.models.SummaryResult``) which is imported and cached.
    2. ``phase.output_schema`` — an inline JSON Schema fragment. Converted to
       a Pydantic model via :func:`json_schema_to_pydantic` (uses
       ``pydantic.create_model`` for the common object-with-properties case,
       and falls back to a permissive model with ``extra="allow"`` if the
       schema can't be expressed directly).
    3. Otherwise, :class:`PhaseResult`.
    """
    raw_output_model = getattr(phase, "output_model", None)
    if raw_output_model:
        return _resolve_dotted_or_registered(str(raw_output_model))

    raw_output_schema = getattr(phase, "output_schema", None)
    if raw_output_schema is not None:
        schema_hash = _schema_hash(raw_output_schema)
        cache_key = f"{blueprint.spec.get('name', 'bp')}::{phase.id}::{schema_hash}"
        cached = _json_schema_cache.get(cache_key)
        if cached is not None:
            return cached
        generated_name = f"goblin.generated.{blueprint.spec.get('name', 'bp')}::{phase.id}::{schema_hash}"
        model = json_schema_to_pydantic(raw_output_schema, name=generated_name)
        register_output_model(generated_name, model)
        _json_schema_cache[cache_key] = model
        return model

    return PhaseResult


_json_schema_cache: dict[str, type[BaseModel]] = {}


def _resolve_dotted_or_registered(name: str) -> type[BaseModel]:
    if name in _REGISTRY:
        return _REGISTRY[name]
    module_name, _, attr = name.rpartition(".")
    if not module_name:
        raise ValueError(f"output_model {name!r} is neither registered nor importable")
    import importlib

    module = importlib.import_module(module_name)
    try:
        model = getattr(module, attr)
    except AttributeError as exc:
        raise ValueError(f"output_model {name!r} not found in module {module_name!r}") from exc
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ValueError(f"output_model {name!r} resolved to {model!r}, not a BaseModel subclass")
    _REGISTRY[name] = model
    return model


def resolve_output_model_by_ref(ref: str) -> type[BaseModel]:
    """Resolve a dotted-name string to a registered Pydantic class.

    Public entry point used by tools (e.g. ``StructuredFinishTool``) that
    need to materialize an output model from a YAML-friendly string rather
    than a Python class object. Falls back to :func:`PhaseResult` if the
    ref is empty/None so unconfigured callers still get a valid default.
    """
    if not ref:
        return PhaseResult
    return _resolve_dotted_or_registered(ref)


def ref_for_model(model: type[BaseModel]) -> str | None:
    """Return the registered dotted-name reference for *model*, if any.

    The canonical default :class:`PhaseResult` is recognised by identity
    rather than registration so the reference stays stable even when the
    registry is cleared between tests.
    """
    if model is PhaseResult:
        return "goblin.output_schema.PhaseResult"
    for ref, registered in _REGISTRY.items():
        if registered is model:
            return ref
    return None


def reset_output_schema_cache() -> None:
    """Clear the inline-schema cache and registry.

    Intended for test isolation. User-registered models are also removed;
    tests that need a stable registration should re-register each run.
    """
    _json_schema_cache.clear()
    _REGISTRY.clear()


def json_schema_to_pydantic(schema: dict[str, Any], name: str = "InlineOutputModel") -> type[BaseModel]:
    """Build a Pydantic class from a JSON Schema fragment.

    Supports the common case used by goblin blueprints: a single object with
    ``properties`` and ``required``. The produced model uses ``extra="allow"``
    so blueprints can extend ``PhaseResult``'s contract without breaking
    runtime validation.

    Schemas that don't fit this shape get a permissive model that accepts
    any object — the runner still gets a typed shape downstream via
    ``model_validate``, and the blueprint's own JSON Schema validation
    (see :func:`validate_against_schema`) catches stricter constraints.
    """
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        return _permissive_object_model(schema, name=name)

    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for field_name, prop in properties.items():
        fields[field_name] = (_python_type_for(prop), _field_default(prop, field_name in required))
    if not fields:
        return _permissive_object_model(schema, name=name)
    return create_model(name, __base__=PhaseResult, **fields)  # type: ignore[call-overload]


def _permissive_object_model(schema: dict[str, Any], name: str = "InlineOutputModel") -> type[BaseModel]:
    display_name = schema.get("title") or name

    class _Inline(PhaseResult):
        model_config = ConfigDict(extra="allow")

    _Inline.__name__ = display_name
    _Inline.__qualname__ = display_name
    return _Inline


def _python_type_for(prop: dict[str, Any]) -> Any:
    json_type = prop.get("type")
    if isinstance(json_type, list):
        return _python_type_for({"type": json_type[0]}) if json_type else Any
    if json_type == "string":
        return str
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        inner = prop.get("items") or {}
        return list[_python_type_for(inner)]
    if json_type == "object":
        return dict
    if json_type == "null":
        return type(None)
    return Any


def _field_default(prop: dict[str, Any], required: bool) -> Any:
    if required:
        return ...
    if "default" in prop:
        return prop["default"]
    return None


# ---------------------------------------------------------------------------
# Validation helper kept for inline-schema blueprints
# ---------------------------------------------------------------------------


def validate_against_schema(data: Any, schema: dict[str, Any]) -> list[str]:
    """Return human-readable errors when *data* doesn't satisfy *schema*.

    Used as a defence-in-depth check on blueprints that declare an inline
    JSON Schema fragment alongside the Pydantic model. Not the primary parse
    path — :func:`coerce_to_model` and the typed Finish tool handle that.
    """
    from jsonschema import Draft7Validator

    return [e.message for e in Draft7Validator(schema).iter_errors(data)]


# ---------------------------------------------------------------------------
# Three-tier textual coercion
# ---------------------------------------------------------------------------


def coerce_to_model(text: str, model: type[BaseModel]) -> BaseModel | None:
    """Best-effort parse of *text* into a *model* instance.

    Three tiers, ordered by cost:

    1. Direct ``model_validate_json`` — well-formed JSON.
    2. JSON-object extraction from prose (markdown fences, balanced braces).
    3. ``json_repair.repair_json`` to rescue near-JSON with a small defect.
    """
    if not text:
        return None

    candidates = [text]
    extracted = _extract_json_object(text)
    if extracted is not None and extracted != text:
        candidates.append(extracted)

    for raw in candidates:
        try:
            return model.model_validate_json(raw)
        except (ValidationError, ValueError):
            pass

    repaired = _repair_to_object(text)
    if repaired is not None:
        try:
            return model.model_validate(repaired)
        except (ValidationError, ValueError):
            pass

    return None


def _repair_to_object(text: str) -> Any:
    """Run ``json_repair`` on *text* and return the parsed object if any.

    ``json_repair`` accepts either a JSON string or an arbitrary string with
    embedded JSON; we pass through the input verbatim and let it decide.
    """
    try:
        import json_repair
    except ImportError:
        return None
    try:
        result = json_repair.repair_json(text, return_objects=True)
    except (ValueError, TypeError):
        return None
    if result is None or isinstance(result, str):
        return None
    return result


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _extract_json_object(text: str) -> str | None:
    """Pull a JSON object out of *text*, handling prose and code fences.

    Unlike ``json.loads(text)``, this:

    - Strips markdown code fences even when the JSON inside has trailing
      commentary that would otherwise fail strict parsing.
    - Uses a balanced-brace scan so nested objects (``{"meta": {"a": 1}}``)
      are returned whole instead of matching only the inner fragment.
    - When the text contains multiple JSON objects (e.g. ``{'a':1} junk
      {'b':2}``) returns the *last* one — the model's final answer rather
      than its first attempt — instead of silently parsing only the first.
    - Returns ``None`` when no JSON-shaped substring exists, so callers can
      fall through to the repair tier rather than get a ``JSONDecodeError``.
    """
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return json.dumps(parsed)
        # The fenced block wasn't a JSON object — fall through to scan the
        # raw text in case there's a different object nearby.

    last_dict_match: str | None = None
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue

        start = i
        depth = 0
        in_string = False
        escape = False
        while i < n:
            ch = text[i]
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"' and in_string:
                in_string = False
            elif ch == '"' and not in_string:
                in_string = True
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            parsed = json.loads(candidate)
                        except json.JSONDecodeError:
                            pass
                        else:
                            if isinstance(parsed, dict):
                                last_dict_match = candidate
                        break
            i += 1
        i += 1

    return last_dict_match
