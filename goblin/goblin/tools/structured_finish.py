"""Typed Finish tool whose argument shape is set at runtime from a Pydantic model.

This replaces the plain ``finish`` tool (``FinishTool`` from the OpenHands
SDK) for phases that declare an ``output_model`` or ``output_schema``. The
agent's ``finish(...)`` call carries a single ``result`` field whose schema
matches the desired output type, which is then validated against the same
Pydantic class by the executor.

Pushing the full output schema into the tool's argument schema would bloat
every LLM call (even turns that don't intend to finish), so we keep the
tool surface minimal — a single ``result: <model>`` — and rely on the
follow-up ``response_format`` call and ``coerce_to_model`` fallback in
:mod:`goblin.output_schema` to enforce the shape on the actual final answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from openhands.sdk.tool import (
    Action,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.sdk.tool.builtins import FinishObservation

if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation

STRUCTURED_FINISH_DESCRIPTION = (
    "Signal completion of the task with a structured result. Call this tool with a single "
    "`result` argument shaped to match the requested output type. The executor validates the "
    "result against the declared schema and surfaces the JSON to the caller."
)


class StructuredFinishAction(Action):
    """Marker base class for dynamically built typed Finish actions.

    A subclass is generated for each output model via
    :func:`make_structured_finish_action_type`. Code that needs to look at
    a finished action's payload checks ``isinstance(action, StructuredFinishAction)``
    then accesses ``action.result``.
    """

    model_config = ConfigDict(extra="forbid")

    result: BaseModel = Field(description="Final structured answer shaped per the phase's output schema.")


class StructuredFinishExecutor(ToolExecutor):
    """Stores the validated Pydantic instance on the action for later extraction.

    The conversation-state path doesn't expose per-action return values to
    callers (only the conversation's event log), so we stash the validated
    object on the action itself for :func:`extract_structured_result` to
    pick up. The Observation is the action's JSON dump so any human
    looking at the event log can read what was emitted.
    """

    def __init__(self, model: type[BaseModel]):
        self._model = model

    def __call__(
        self,
        action: Action,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> FinishObservation:
        payload = getattr(action, "result", None)
        if isinstance(payload, BaseModel):
            text = payload.model_dump_json()
        elif isinstance(payload, dict):
            # ``model_construct`` (used by extract tests) can produce actions
            # whose result is a plain dict rather than a BaseModel instance.
            # Wrap it in the output model so the JSON shape stays consistent.
            wrapped = self._model.model_validate(payload)
            text = wrapped.model_dump_json()
        elif payload is None:
            # No payload at all — emit a default-shaped empty body so callers
            # that key off the observation text still see a JSON object.
            wrapped = self._model.model_construct()
            text = wrapped.model_dump_json()
        else:
            wrapped = self._model(result=payload)
            text = wrapped.model_dump_json()
        return FinishObservation.from_text(text=text)


def make_structured_finish_action_type(model: type[BaseModel]) -> type[StructuredFinishAction]:
    """Build a ``StructuredFinishAction`` subclass whose ``result`` is the given model.

    ``pydantic.create_model`` materialises a new class with the strict
    ``result: <Model>`` field declared inline. The dynamic class is what
    OpenHands' tool dispatcher uses to validate the LLM's tool arguments;
    Pydantic raises ``ValidationError`` for anything that doesn't match the
    schema before our executor ever sees it.
    """
    return create_model(  # type: ignore[call-overload]
        f"{model.__name__}FinishAction",
        __base__=StructuredFinishAction,
        result=(model, Field(..., description=f"Final structured answer of type {model.__name__}.")),
    )


def _resolve_output_model_param(params: dict[str, Any]) -> type[BaseModel]:
    """Translate a Tool-spec ``params`` dict into a Pydantic class.

    Accepts either an already-imported class under ``output_model`` (only
    useful for in-process callers) or a dotted-name ``output_model_ref``
    that the registry resolves at tool-creation time.
    """
    from goblin.output_schema import resolve_output_model_by_ref

    cls = params.get("output_model")
    if isinstance(cls, type) and issubclass(cls, BaseModel):
        return cls
    ref = params.get("output_model_ref")
    if isinstance(ref, str):
        return resolve_output_model_by_ref(ref)
    raise ValueError("StructuredFinishTool requires `params.output_model` or `params.output_model_ref`")


class StructuredFinishTool(ToolDefinition):
    """Typed Finish tool whose action schema is the phase's output model.

    Registered under the key ``"structured_finish"`` so callers can pass a
    ``Tool(name="structured_finish", params={...})`` spec, but the runtime
    tool name is ``"finish"``. Using the same name as the OpenHands
    ``FinishTool`` lets the SDK's finish-detection logic recognise the call
    and terminate the conversation.

    ``params`` accepts either:
      - ``output_model``: a Pydantic class object (for in-process callers
        that have the class available directly).
      - ``output_model_ref``: a dotted-name string (e.g.
        ``"goblin._demo_models.RepoSummary"``) that the registry resolves
        through :mod:`goblin.output_schema`'s lookup chain. The ref form
        is what blueprint YAML uses because Pydantic class objects aren't
        YAML-serialisable.
    """

    # Match the OpenHands FinishTool name so the agent loop treats this as
    # the terminal tool.
    name = "finish"

    @classmethod
    def create(  # type: ignore[override]
        cls,
        conv_state: Any = None,  # noqa: ARG003
        **params: Any,
    ) -> Sequence["StructuredFinishTool"]:
        output_model = _resolve_output_model_param(params)
        action_type = make_structured_finish_action_type(output_model)
        return [
            cls(
                action_type=action_type,
                observation_type=FinishObservation,
                description=STRUCTURED_FINISH_DESCRIPTION,
                executor=StructuredFinishExecutor(output_model),
                annotations=ToolAnnotations(
                    title="finish",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


register_tool("structured_finish", StructuredFinishTool)


def extract_structured_result(events, output_model: type[BaseModel]):
    """Return the ``result`` from the agent's typed Finish call, or ``None``.

    Walks the conversation event log looking for an ``ActionEvent`` whose
    source is ``"agent"`` and whose action is a ``StructuredFinishAction``
    (or any Finish action whose ``result`` validates against the requested
    output model). Returns the most recent such action's validated
    ``result`` instance — the one the agent emitted last.

    Uses duck typing (attribute checks) instead of an ``isinstance`` check on
    :class:`openhands.sdk.event.ActionEvent` so tests can pass plain
    ``MagicMock``-backed events without importing the SDK's event types.
    """
    for event in reversed(events or []):
        if not hasattr(event, "action") or not hasattr(event, "source"):
            continue
        if getattr(event, "source", None) != "agent":
            continue
        action = getattr(event, "action", None)
        if action is None:
            continue
        if not isinstance(action, StructuredFinishAction):
            continue
        result = getattr(action, "result", None)
        if isinstance(result, output_model):
            return result
        if result is None:
            continue
        try:
            if isinstance(result, BaseModel):
                return output_model.model_validate(result.model_dump())
            return output_model.model_validate(result)
        except Exception:
            continue
    return None
