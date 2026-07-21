"""Tests for :class:`goblin.tools.structured_finish.StructuredFinishTool`."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from goblin.output_schema import PhaseResult
from goblin.tools.structured_finish import (
    StructuredFinishAction,
    StructuredFinishTool,
    extract_structured_result,
    make_structured_finish_action_type,
)
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ReviewResult(BaseModel):
    title: str
    rating: int = Field(ge=0, le=5)


def _make_action_event(result: BaseModel):
    """Return an ActionEvent-like mock with the typed Finish action attached."""
    action_type = make_structured_finish_action_type(type(result))
    action = action_type(result=result)
    event = MagicMock()
    event.source = "agent"
    event.tool_name = StructuredFinishTool.name
    event.action = action
    return event


# ---------------------------------------------------------------------------
# Tool construction
# ---------------------------------------------------------------------------


class TestStructuredFinishToolCreate:
    def test_create_returns_one_tool(self):
        tools = StructuredFinishTool.create(output_model=PhaseResult)
        assert len(tools) == 1

    def test_create_uses_output_model_name(self):
        tool = StructuredFinishTool.create(output_model=PhaseResult)[0]
        assert tool.name == StructuredFinishTool.name

    def test_create_sets_action_type_with_output_model_field(self):
        tool = StructuredFinishTool.create(output_model=_ReviewResult)[0]
        action = tool.action_type(result=_ReviewResult(title="ok", rating=5))
        assert isinstance(action.result, _ReviewResult)
        assert action.result.rating == 5

    def test_create_requires_output_model_or_ref(self):
        with pytest.raises(ValueError, match="output_model"):
            StructuredFinishTool.create()

    def test_create_accepts_output_model_ref(self):
        tools = StructuredFinishTool.create(output_model_ref="goblin.output_schema.PhaseResult")
        assert len(tools) == 1
        assert tools[0].name == StructuredFinishTool.name

    def test_create_sets_finish_observation(self):
        tool = StructuredFinishTool.create(output_model=PhaseResult)[0]
        from openhands.sdk.tool.builtins import FinishObservation

        assert tool.observation_type is FinishObservation


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class TestStructuredFinishExecutor:
    def test_executor_returns_finish_observation_with_json(self):
        tool = StructuredFinishTool.create(output_model=PhaseResult)[0]
        action = tool.action_type(result=PhaseResult(success=True, message=["ok"]))
        observation = tool.executor(action)
        text = observation.text
        # Observation text should be the JSON dump of the structured result.
        import json

        parsed = json.loads(text)
        assert parsed["success"] is True
        assert parsed["message"] == ["ok"]

    def test_executor_handles_unset_result_via_model_dump(self):
        # If somehow the result didn't pass Pydantic validation, the executor
        # still produces an Observation rather than crashing.
        tool = StructuredFinishTool.create(output_model=PhaseResult)[0]
        # Construct an action with a non-BaseModel value via model_construct
        action = tool.action_type.model_construct(result={"success": True, "message": ["x"]})
        observation = tool.executor(action)
        assert observation.text


# ---------------------------------------------------------------------------
# extract_structured_result
# ---------------------------------------------------------------------------


class TestExtractStructuredResult:
    def test_returns_none_when_no_events(self):
        assert extract_structured_result([], PhaseResult) is None

    def test_returns_latest_finish_result(self):
        events = [
            _make_action_event(PhaseResult(success=True, message=["first"])),
            _make_action_event(PhaseResult(success=False, message=["second"])),
        ]
        result = extract_structured_result(events, PhaseResult)
        assert result is not None
        assert result.message == ["second"]

    def test_ignores_non_agent_events(self):
        agent_event = _make_action_event(PhaseResult(success=True, message=["ok"]))
        agent_event.source = "environment"
        events = [agent_event]
        assert extract_structured_result(events, PhaseResult) is None

    def test_ignores_non_finish_actions(self):
        event = MagicMock()
        event.source = "agent"
        event.action = MagicMock()  # not a StructuredFinishAction
        event.tool_name = "something_else"
        assert extract_structured_result([event], PhaseResult) is None

    def test_recovers_from_loose_dict_via_validate(self):
        """If a custom executor hands back a dict-like action, we still validate."""
        tool = StructuredFinishTool.create(output_model=PhaseResult)[0]
        action = tool.action_type.model_construct(result={"success": True, "message": ["recovered"]})
        event = MagicMock()
        event.source = "agent"
        event.tool_name = StructuredFinishTool.name
        event.action = action
        result = extract_structured_result([event], PhaseResult)
        assert result is not None
        assert result.message == ["recovered"]


# ---------------------------------------------------------------------------
# make_structured_finish_action_type
# ---------------------------------------------------------------------------


class TestMakeStructuredFinishActionType:
    def test_returns_subclass_of_structured_finish_action(self):
        action_type = make_structured_finish_action_type(PhaseResult)
        assert issubclass(action_type, StructuredFinishAction)

    def test_action_name_includes_model(self):
        action_type = make_structured_finish_action_type(_ReviewResult)
        assert "_ReviewResult" in action_type.__name__

    def test_forbids_extra_fields(self):
        action_type = make_structured_finish_action_type(PhaseResult)
        with pytest.raises(Exception):
            action_type.model_validate({"result": PhaseResult(success=True, message=["ok"]), "extra": "junk"})
