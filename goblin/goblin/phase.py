from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Protocol, cast

from pydantic import BaseModel

from openhands.sdk import Agent, LocalWorkspace, Tool
from openhands.sdk.conversation import get_agent_final_response

from goblin.conversation import run_conversation
from goblin.cost_control import CircuitBreaker, LimitExceeded
from goblin.enums import LoadProjectStatus
from goblin.output_schema import (
    PhaseResult,
    coerce_to_model,
    register_output_model,
    resolve_output_model,
    validate_against_schema,
)
from goblin.prompts import BASE_SYSTEM_PROMPT, build_system_prompt
from goblin.tools.structured_finish import StructuredFinishTool, extract_structured_result
from goblin.tools.tool_map import build_tool_filter_regex, resolve_tools
from goblin.utils.llm import build_llm

logger = logging.getLogger(__name__)


def _validate_output_schema(data: dict, schema: dict) -> list[str]:
    """Backward-compatible thin wrapper around :func:`validate_against_schema`.

    Kept as a module-level symbol so existing callers continue to work; new
    code should prefer :func:`validate_against_schema` directly.
    """
    return validate_against_schema(data, schema)


__all__ = [
    "PhaseProtocol",
    "PromptPhase",
    "LoadProjectPhase",
    "PhaseResult",
    "_validate_output_schema",
    "register_output_model",
]


if TYPE_CHECKING:
    from goblin.blueprint import V1Blueprint


class PhaseProtocol(Protocol):
    id: str
    condition: str | None
    foreach: str | None
    on_failure: dict | None
    depends_on: list[str] | None

    async def run(self, context: dict) -> Dict: ...


class PromptPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.prompt_template: str = raw["prompt"]
        self.allowed_tools: list[str] = raw.get("allowed_tools", [])
        self.hooks_config: dict = raw.get("hooks", {})
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")
        self.mcp_servers_config: dict | None = raw.get("mcp_servers")
        self.history: list[str] | None = raw.get("history")
        self.max_iterations: int | None = raw.get("max_iterations")
        self.max_tokens: int | None = raw.get("max_tokens")
        self.max_cost: float | None = raw.get("max_cost")
        self.model: str | None = raw.get("model")
        self.llm_config: dict | None = raw.get("llm")
        self.depends_on: list[str] | None = raw.get("depends_on")
        self.output_schema: dict | None = raw.get("output_schema")
        self.output_model: str | None = raw.get("output_model")
        self.output_schema_format: str | None = raw.get("output_schema_format")
        self.worktree_config: dict | None = raw.get("worktree")
        self.notifications_config: dict | None = raw.get("notifications")
        self._blueprint = blueprint

    def _resolve_output_model(self) -> type[BaseModel]:
        """Resolve the Pydantic output model for this phase, cached per instance."""
        cached = getattr(self, "_cached_output_model", None)
        if cached is not None:
            return cached
        model = resolve_output_model(self._blueprint, self)
        self._cached_output_model = model
        return model

    def _structured_output_configured(self) -> bool:
        """True when the phase opts into the structured-output machinery.

        The default flow is chat-text-in, wrapped-JSON-out: the agent responds
        in free-form text and :meth:`run` wraps that text into a
        :class:`PhaseResult`. The structured-output path (typed Finish tool,
        ``response_format`` follow-up, schema validation) only kicks in when
        the phase declares ``output_model`` or ``output_schema``.
        """
        return bool(getattr(self, "output_model", None) or getattr(self, "output_schema", None))

    def _finish_tool_params(self, output_model: type[BaseModel]) -> dict:
        """Build the ``Tool.params`` dict the registry passes to ``StructuredFinishTool.create``.

        ``Tool.params`` must be JSON-serialisable (it lives in the
        conversation's persisted state), so we pass the output model via a
        dotted-name reference rather than the class object. Inline JSON
        Schemas are converted to Pydantic models by :mod:`goblin.output_schema`
        at resolve time; the model can then be referenced via a generated
        name. ``PhaseResult`` and any registered model pass through with
        their canonical name.
        """
        from goblin.output_schema import (
            _REGISTRY,
            json_schema_to_pydantic,
            register_output_model,
        )

        # PhaseResult and registered models: emit a dotted-name reference.
        for ref, registered in _REGISTRY.items():
            if registered is output_model:
                return {"output_model_ref": ref}

        # Inline JSON Schema output model: cache it under a stable name.
        raw_schema = getattr(self, "output_schema", None)
        if raw_schema is not None and getattr(self, "output_model", None) is None:
            generated = json_schema_to_pydantic(raw_schema)
            name = f"goblin.generated.{self._blueprint.spec.get('name', 'bp')}::{self.id}"
            register_output_model(name, generated)
            return {"output_model_ref": name}

        # Fall back to PhaseResult's reference so the typed Finish tool still
        # emits a well-formed payload even when the output model came from an
        # unknown source. The runner re-resolves to the real model afterwards.
        return {"output_model_ref": "goblin.output_schema.PhaseResult"}

    async def run(self, context: dict) -> Dict:
        prompt = self.prompt_template.format(**context)

        history_context = context.get("_history_context", "")
        if history_context:
            prompt = history_context + prompt

        worktree_config = self._blueprint._resolve_worktree_config(self)
        if "Worktree" in self.allowed_tools and worktree_config is None:
            return {
                "success": False,
                "message": [
                    "Worktree tool is listed in allowed_tools but no worktree configuration is defined. "
                    "Add `defaults.worktree` or `phase.worktree` to the blueprint."
                ],
                "prompt": prompt,
                "output": "",
                "spend": {},
            }

        tool_params: dict[str, dict] = {}
        if worktree_config is not None and "Worktree" in self.allowed_tools:
            tool_params["Worktree"] = {
                "repo_root": self._blueprint.project_path or os.getcwd(),
                "base_dir": worktree_config.get("base_dir", ".goblin/worktrees"),
                "deps": worktree_config.get("deps", []),
                "auto_link": worktree_config.get("auto_link", False),
            }

        if "AskUser" in self.allowed_tools:
            notifications_config = self._blueprint._resolve_notifications_config(self) or {}
            tool_params["AskUser"] = {"label": notifications_config.get("label")}

        breaker = CircuitBreaker(
            max_iterations=self.max_iterations,
            max_tokens=self.max_tokens,
            max_cost=self.max_cost,
        )

        llm_config = self._blueprint._resolve_llm_config(self, context)
        structured_configured = self._structured_output_configured()

        if structured_configured:
            output_model = self._resolve_output_model()
            system_prompt = build_system_prompt(output_model)
            llm = build_llm(model=llm_config, output_model=output_model)
        else:
            output_model = PhaseResult
            system_prompt = BASE_SYSTEM_PROMPT
            llm = build_llm(model=llm_config)

        if self._blueprint.project_refs:
            system_prompt = system_prompt + "\n\n" + self._blueprint.project_refs

        base_tools = resolve_tools(self.allowed_tools, tool_params=tool_params)
        if structured_configured:
            # Replace any inherited `finish` tool spec with our typed Finish
            # tool spec. The Tool registry's ``.create()`` builds the actual
            # ``ToolDefinition`` at conversation-startup, so the resolved
            # ``output_model`` flows through ``params`` (dotted-name form)
            # and is materialised in :meth:`StructuredFinishTool.create`.
            structured_spec_params = self._finish_tool_params(output_model)
            structured_spec = Tool(name=StructuredFinishTool.name, params=structured_spec_params)
            tools = [t for t in base_tools if t.name not in ("finish", StructuredFinishTool.name)]
            tools.append(structured_spec)
        else:
            tools = list(base_tools)

        agent = Agent(
            llm=llm,
            tools=tools,
            mcp_config=self._blueprint._build_mcp_servers(self, context),
            system_prompt=system_prompt,
            filter_tools_regex=build_tool_filter_regex(self.allowed_tools),
            critic=None,
        )

        workspace = self._blueprint._get_workspace(context)

        try:
            conversation = await run_conversation(
                agent,
                workspace,
                prompt,
                hook_config=self._blueprint._build_hooks(self),
                max_iterations=self.max_iterations or 500,
                max_cost=self.max_cost,
            )
            breaker.absorb(conversation)
        except LimitExceeded as exc:
            return {
                "success": False,
                "message": [f"Circuit breaker: {exc.reason} limit exceeded"],
                "prompt": prompt,
                "output": "",
                "spend": exc.tracker,
            }

        text_output = get_agent_final_response(conversation.state.events) or ""

        if structured_configured:
            structured: BaseModel | None = extract_structured_result(conversation.state.events, output_model)
            if structured is None:
                structured = await self._request_structured_output(prompt, output_model, llm_config=llm_config)
            if structured is None:
                structured = self._fallback_result(text_output, output_model)
        else:
            structured = self._wrap_chat_text(text_output)

        # At this point structured is guaranteed to be an instance of
        # output_model (every code path that creates it does so via that
        # class), but pyrefly doesn't track the runtime invariant. Cast
        # to the concrete type so the field accesses below type-check.
        assert structured is not None
        typed_result = cast(output_model, structured)  # type: ignore[valid-type]

        # Optional defence-in-depth: when a blueprint still declares an inline
        # JSON Schema alongside the Pydantic model, validate against it.
        if self.output_schema is not None:
            schema_errors = validate_against_schema(typed_result.model_dump(), self.output_schema)
            if schema_errors:
                logger.warning(
                    "Output schema validation failed",
                    extra={
                        "event": "phase.output_schema_failed",
                        "phase_id": self.id,
                        "errors": schema_errors,
                    },
                )
                return {
                    "success": False,
                    "message": [f"Output schema validation failed: {e}" for e in schema_errors],
                    "prompt": prompt,
                    "output": text_output,
                    "spend": breaker.tracker,
                }

        # Dump to a dict so we don't need to know the model's concrete field
        # names at this call site — downstream phases consume whatever the
        # output_model declared, not just ``success``/``message``.
        dumped = typed_result.model_dump()
        result: Dict[str, Any] = {
            "success": dumped.get("success", False),
            "message": list(dumped.get("message", []) or []),
            "prompt": prompt,
            "output": text_output,
            "spend": breaker.tracker,
        }
        # Surface any extra fields the output model carries so downstream
        # phases can reference them via Jinja (`steps.<id>.extra_field`).
        for key, value in dumped.items():
            if key not in result:
                result[key] = value
        return result

    async def _request_structured_output(
        self,
        original_prompt: str,
        output_model: type[BaseModel],
        *,
        llm_config,
    ) -> BaseModel | None:
        """Layer 2: tool-less follow-up agent with ``response_format`` attached.

        Called when the in-loop Finish tool didn't fire. The follow-up agent
        is a fresh, stateless LLM call (no tool loop), so it can carry the
        response_format payload directly. Anthropic may drop the parameter
        for some models; the cheap ``coerce_to_model`` fallback handles the
        resulting prose output.
        """
        follow_up = (
            "Your previous attempt did not return a structured answer. "
            "Respond with the structured final answer now.\n\n"
            f"Original task:\n{original_prompt}"
        )
        # ``build_llm`` accepts ``output_model`` directly and routes the right
        # provider-native structured-output key (response_format /
        # guided_json / response_schema) onto ``litellm_extra_body``. Going
        # through that path keeps the follow-up LLM consistent with the
        # primary run's provider mapping.
        llm = build_llm(model=llm_config, output_model=output_model)
        agent = Agent(llm=llm, tools=[], system_prompt=BASE_SYSTEM_PROMPT, critic=None)
        workspace = LocalWorkspace(working_dir=".")
        conversation = await run_conversation(
            agent,
            workspace,
            follow_up,
            hook_config=None,
            max_iterations=2,
            max_cost=None,
        )
        text = get_agent_final_response(conversation.state.events) or ""
        return coerce_to_model(text, output_model)

    @staticmethod
    def _wrap_chat_text(text: str) -> BaseModel:
        """Default path: take the agent's free-form chat output and wrap it.

        Phases that don't declare ``output_model`` or ``output_schema`` skip
        the typed Finish tool and ``response_format`` machinery entirely.
        The agent responds in plain text; we wrap the whole transcript as a
        single ``PhaseResult`` entry so downstream Jinja conditions
        (``steps.<id>.success``) and the message stream keep working without
        the LLM having to know about the harness's internal contract.
        """
        if text:
            return PhaseResult(success=True, message=[text])
        return PhaseResult(success=True, message=["Agent produced no output"])

    @staticmethod
    def _fallback_result(text: str, output_model: type[BaseModel]) -> BaseModel:
        """Final fallback when nothing in the layers produced a valid model.

        Try :func:`coerce_to_model` one last time on the Layer 2 text; if that
        also fails, return a synthetic ``success=False`` result so the caller
        at least sees a structured PhaseResult rather than an exception.
        """
        coerced = coerce_to_model(text, output_model)
        if coerced is not None:
            return coerced
        # Synthesize a minimal failure for the default PhaseResult shape;
        # for arbitrary user-defined models we accept losing the typed
        # contract rather than fabricating unknown fields.
        try:
            return output_model(success=False, message=["Agent produced no structured output"])
        except Exception:
            return PhaseResult(success=False, message=["Agent produced no structured output"])


class LoadProjectPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.path_template: str = raw["load_project"]
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")
        self.depends_on: list[str] | None = raw.get("depends_on")
        self._blueprint = blueprint

    async def run(self, context: dict) -> dict:
        path = self.path_template.format(**context)
        self._blueprint._load_project(path)
        return {"success": True, "status": LoadProjectStatus.LOADED, "path": path}
