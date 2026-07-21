from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Protocol, cast

from pydantic import BaseModel

from goblin.cost_control import CircuitBreaker, LimitExceeded
from goblin.enums import LoadProjectStatus
from goblin.output_schema import (
    PhaseResult,
    coerce_to_model,
    validate_against_schema,
)
from goblin.prompts import BASE_SYSTEM_PROMPT, build_structured_follow_up_prompt, build_system_prompt

logger = logging.getLogger(__name__)


__all__ = [
    "PhaseProtocol",
    "PromptPhase",
    "LoadProjectPhase",
    "PhaseResult",
]


if TYPE_CHECKING:
    from goblin.blueprint import V1Blueprint


class PhaseProtocol(Protocol):
    id: str
    condition: str | None
    foreach: str | None
    on_failure: dict | None
    depends_on: list[str] | None

    async def run(self, context: dict) -> dict: ...


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
        from goblin.output_schema import resolve_output_model

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
        from goblin.output_schema import ref_for_model

        ref = ref_for_model(output_model)
        if ref is None:
            # Fall back to PhaseResult's reference so the typed Finish tool still
            # emits a well-formed payload even when the output model came from an
            # unknown source. The runner re-resolves to the real model afterwards.
            ref = "goblin.output_schema.PhaseResult"
        return {"output_model_ref": ref}

    async def run(self, context: dict) -> dict:
        from openhands.sdk import Agent, Tool
        from openhands.sdk.conversation import get_agent_final_response

        from goblin.conversation import run_conversation
        from goblin.tools.structured_finish import extract_structured_result
        from goblin.tools.tool_map import build_tool_filter_regex, resolve_tools
        from goblin.utils.llm import build_llm

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
            try:
                output_model = self._resolve_output_model()
            except (ValueError, ImportError) as exc:
                logger.warning(
                    "output_model_resolution_failed",
                    extra={
                        "event": "phase.output_model_resolution_failed",
                        "phase_id": self.id,
                        "output_model": self.output_model,
                        "error": str(exc),
                    },
                )
                return {
                    "success": False,
                    "message": [f"Could not resolve output_model for phase '{self.id}': {exc}"],
                    "prompt": prompt,
                    "output": "",
                    "spend": {},
                }
            system_prompt = build_system_prompt(output_model)
            # Keep the provider response_format/guided_json payload off the
            # primary tool loop; it belongs on the follow-up call only.
            llm = build_llm(model=llm_config)
        else:
            output_model = PhaseResult
            system_prompt = BASE_SYSTEM_PROMPT
            llm = build_llm(model=llm_config)

        if self._blueprint.project_refs:
            system_prompt = system_prompt + "\n\n" + self._blueprint.project_refs

        base_tools = resolve_tools(self.allowed_tools, tool_params=tool_params)
        if structured_configured:
            # Replace any inherited `finish` tool spec with our typed Finish
            # tool spec. The registry key is ``"structured_finish"`` but the
            # runtime tool name is ``"finish"``, so the OpenHands SDK treats
            # the call as a terminal Finish action.
            structured_spec_params = self._finish_tool_params(output_model)
            structured_spec = Tool(name="structured_finish", params=structured_spec_params)
            tools = [t for t in base_tools if t.name not in ("finish", "structured_finish")]
            tools.append(structured_spec)
        else:
            tools = list(base_tools)

        agent_kwargs: dict[str, Any] = {
            "llm": llm,
            "tools": tools,
            "mcp_config": self._blueprint._build_mcp_servers(self, context),
            "system_prompt": system_prompt,
            "filter_tools_regex": build_tool_filter_regex(
                self.allowed_tools,
                extra_builtin_names=["structured_finish"] if structured_configured else None,
            ),
            "critic": None,
        }
        if structured_configured:
            # Disable the default FinishTool/ThinkTool so the only ``finish``
            # in the agent's tool surface is our typed one.
            agent_kwargs["include_default_tools"] = []
        agent = Agent(**agent_kwargs)

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
                try:
                    structured = await self._request_structured_output(
                        prompt, text_output, output_model, llm_config=llm_config, breaker=breaker
                    )
                except LimitExceeded as exc:
                    return {
                        "success": False,
                        "message": [f"Circuit breaker: {exc.reason} limit exceeded"],
                        "prompt": prompt,
                        "output": text_output,
                        "spend": exc.tracker,
                    }
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
        result: dict[str, Any] = {
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
        prior_text: str,
        output_model: type[BaseModel],
        *,
        llm_config,
        breaker: CircuitBreaker | None = None,
    ) -> BaseModel | None:
        """Layer 2: tool-less follow-up agent with ``response_format`` attached.

        Called when the in-loop Finish tool didn't fire. The follow-up agent
        is a fresh, stateless LLM call (no tool loop), so it can carry the
        response_format payload directly. We include the prior run's final
        text and the original task so the follow-up can answer from the work
        already done rather than fabricating from the prompt alone.

        When *breaker* is supplied, the follow-up runs against the phase's
        remaining budget and its spend is folded back into the breaker so the
        final phase result accounts for both conversation turns.
        """
        from openhands.sdk import Agent, LocalWorkspace
        from openhands.sdk.conversation import get_agent_final_response

        from goblin.conversation import run_conversation
        from goblin.utils.llm import build_llm

        follow_up = (
            "Your previous attempt did not return a structured answer. "
            "Here is what you produced:\n\n"
            f"{prior_text}\n\n"
            "Respond with the structured final answer now.\n\n"
            f"Original task:\n{original_prompt}"
        )
        # ``build_llm`` accepts ``output_model`` directly and routes the right
        # provider-native structured-output key (response_format /
        # guided_json / response_schema) onto ``litellm_extra_body``. Going
        # through that path keeps the follow-up LLM consistent with the
        # primary run's provider mapping.
        llm = build_llm(
            model=llm_config,
            output_model=output_model,
            output_schema_format=self.output_schema_format,
        )
        agent = Agent(
            llm=llm,
            tools=[],
            system_prompt=build_structured_follow_up_prompt(output_model),
            critic=None,
        )
        workspace = LocalWorkspace(working_dir=".")

        remaining_budget = None
        if breaker is not None and breaker.max_cost is not None:
            remaining_budget = max(0.0, breaker.max_cost - breaker.tracker.cost)

        conversation = await run_conversation(
            agent,
            workspace,
            follow_up,
            hook_config=None,
            max_iterations=2,
            max_cost=remaining_budget,
        )
        if breaker is not None:
            breaker.absorb(conversation)
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
        return PhaseResult(success=False, message=["Agent produced no text output"])

    @staticmethod
    def _fallback_result(text: str, output_model: type[BaseModel]) -> BaseModel:
        """Final fallback when nothing in the layers produced a valid model.

        Try :func:`coerce_to_model` one last time on the Layer 2 text; if that
        also fails, return a synthetic ``success=False`` :class:`PhaseResult`
        so the caller at least sees a structured result rather than an
        exception. User-defined models that lack ``success``/``message`` fields
        lose the typed contract here by design — fabricating unknown fields
        would be a worse lie.
        """
        coerced = coerce_to_model(text, output_model)
        if coerced is not None:
            return coerced
        logger.warning(
            "structured_output_fallback_failed",
            extra={"event": "phase.structured_fallback_failed"},
        )
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
