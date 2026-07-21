from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING

from goblin.checkpoint import Checkpoint
from goblin.enums import FailureAction
from goblin.hooks import HOOK_REGISTRY
from goblin.phase import LoadProjectPhase, PhaseProtocol

if TYPE_CHECKING:
    from goblin.blueprint import V1Blueprint

logger = logging.getLogger(__name__)


class PhaseOrchestrator:
    """Runs a blueprint's phase graph: dependency waves, condition/foreach dispatch,
    checkpointing, and failure handling.

    Constructed fresh for each `V1Blueprint.run()` call — `_checkpoint_lock` and
    `_failed_abort` are per-run state, not per-blueprint state.
    """

    def __init__(self, blueprint: V1Blueprint):
        self._blueprint = blueprint
        self._checkpoint_lock = asyncio.Lock()
        self._failed_abort = asyncio.Event()

    async def _handle_phase_failure(self, phase: PhaseProtocol, context: dict, phase_result: dict) -> tuple[bool, dict]:
        """Handle a failed phase. Returns (should_continue, final_result)."""
        on_failure = phase.on_failure or {}
        action = FailureAction(on_failure.get("action", FailureAction.ABORT))
        notify_hook = on_failure.get("notify")

        logger.warning("Phase failed", extra={"event": "phase.failed", "phase_id": phase.id})

        if notify_hook and notify_hook in HOOK_REGISTRY:
            hook_val = HOOK_REGISTRY[notify_hook]
            if callable(hook_val):
                hook_val(phase_result)

        if action == FailureAction.SKIP:
            logger.warning("Skipping failed phase", extra={"event": "phase.skip_on_failure", "phase_id": phase.id})
            return True, phase_result

        if action == FailureAction.RETRY:
            max_retries = on_failure.get("max_retries", 1)
            result = phase_result
            for attempt in range(1, max_retries + 1):
                logger.warning(
                    "Retrying phase",
                    extra={
                        "event": "phase.retry",
                        "phase_id": phase.id,
                        "attempt": attempt,
                        "max_retries": max_retries,
                    },
                )
                result = await phase.run(context)
                if result.get("success", True):
                    return True, result
            logger.error(
                "Phase failed after retries",
                extra={"event": "phase.retry_exhausted", "phase_id": phase.id, "max_retries": max_retries},
            )
            return False, result

        logger.error("Aborting blueprint", extra={"event": "phase.abort", "phase_id": phase.id})
        return False, phase_result

    async def _record_checkpoint(self, checkpoint: Checkpoint | None, phase_id: str, result: dict) -> None:
        """Record a finished phase result as completed or failed, based on result['success'].

        Does NOT mark the overall run as failed — use `_abort_and_record` for that.
        """
        if not checkpoint:
            return
        async with self._checkpoint_lock:
            if result.get("success", True):
                await checkpoint.phase_completed(phase_id, result)
            else:
                await checkpoint.phase_failed(phase_id, result)

    async def _abort_and_record(self, checkpoint: Checkpoint | None, phase_id: str, result: dict) -> None:
        """Record a phase as failed, mark the whole run as failed, and set the abort flag.

        Used when a phase's on_failure action is 'abort', or when 'retry' exhausts
        its max_retries without success.
        """
        if checkpoint:
            async with self._checkpoint_lock:
                await checkpoint.phase_failed(phase_id, result)
                await checkpoint.mark_failed()
        self._failed_abort.set()

    def _build_phase_context(self, phase: PhaseProtocol, base_context: dict, results: dict) -> dict:
        """Attach `_history_context` to *base_context* if the phase declares `history`."""
        if not (hasattr(phase, "history") and phase.history):
            return base_context
        history_context = self._blueprint.build_history_context(phase.history, results)
        return {**base_context, "_history_context": history_context}

    async def _run_single_phase(
        self, phase: PhaseProtocol, context: dict, results: dict, checkpoint: Checkpoint | None = None
    ) -> dict | None:
        """Run a single phase including condition, foreach, history, checkpoint, and failure handling.

        Returns the phase result, or None if the phase was skipped (condition false or checkpoint already done).
        """
        if self._failed_abort.is_set():
            return None

        if checkpoint:
            completed = await checkpoint.completed_phase_ids()
            skipped = await checkpoint.skipped_phase_ids()
            if phase.id in completed | skipped:
                logger.info(
                    "Skipping phase — already completed",
                    extra={"event": "phase.checkpoint_skip", "phase_id": phase.id},
                )
                return None

        if phase.condition and not self._blueprint.evaluate_condition(phase.condition, results):
            logger.info(
                "Skipping phase — condition not met",
                extra={"event": "phase.condition_skip", "phase_id": phase.id},
            )
            if checkpoint:
                async with self._checkpoint_lock:
                    await checkpoint.phase_skipped(phase.id)
            return None

        if phase.foreach:
            return await self._run_foreach_phase(phase, context, results, checkpoint)
        return await self._run_non_foreach_phase(phase, context, results, checkpoint)

    async def _run_non_foreach_phase(
        self, phase: PhaseProtocol, context: dict, results: dict, checkpoint: Checkpoint | None
    ) -> dict:
        phase_context = self._build_phase_context(phase, context, results)

        if checkpoint:
            async with self._checkpoint_lock:
                await checkpoint.phase_started(
                    phase.id, {"context": {k: v for k, v in phase_context.items() if k != "_history_context"}}
                )

        result = await phase.run(phase_context)

        if result.get("success", True):
            await self._record_checkpoint(checkpoint, phase.id, result)
            return result

        should_continue, final = await self._handle_phase_failure(phase, phase_context, result)
        if not should_continue:
            await self._abort_and_record(checkpoint, phase.id, final)
            return final

        await self._record_checkpoint(checkpoint, phase.id, final)
        return final

    async def _run_foreach_phase(
        self, phase: PhaseProtocol, context: dict, results: dict, checkpoint: Checkpoint | None
    ) -> dict:
        assert phase.foreach is not None
        items = self._blueprint.resolve_foreach(phase.foreach, results, context)
        if checkpoint:
            async with self._checkpoint_lock:
                await checkpoint.phase_started(phase.id, {"foreach": phase.foreach, "item_count": len(items)})

        iteration_results: list[dict] = []
        for item in items:
            if self._failed_abort.is_set():
                break

            phase_context = self._build_phase_context(phase, {**context, **item}, results)
            result = await phase.run(phase_context)
            iteration_results.append(result)

            if not result.get("success", True):
                should_continue, final = await self._handle_phase_failure(phase, phase_context, result)
                if not should_continue:
                    final_result = {"iterations": iteration_results, "success": False}
                    await self._abort_and_record(checkpoint, phase.id, final_result)
                    return final_result
                # For skip or retry-success, continue the foreach loop.
                # NOTE: iteration_results keeps the original failed `result`, not
                # the post-retry `final` — this is a pre-existing quirk carried
                # over unchanged from before this refactor (a successful retry's
                # output is discarded from `iterations`, only its success/failure
                # of a *later* item is affected).

        final_result = {
            "iterations": iteration_results,
            "success": all(r.get("success", True) for r in iteration_results),
        }
        await self._record_checkpoint(checkpoint, phase.id, final_result)
        return final_result

    async def run(self, phases: list[PhaseProtocol], context: dict, checkpoint: Checkpoint | None = None) -> dict:
        if checkpoint:
            results = await checkpoint.get_results()
            # On resume, only skip completed and skipped phases.
            # Failed phases are re-run to preserve original behavior.
            completed = await checkpoint.completed_phase_ids()
            skipped = await checkpoint.skipped_phase_ids()
            finished = completed | skipped
        else:
            results = {}
            finished = set()

        pending = {p.id for p in phases}
        pending -= finished

        while pending and not self._failed_abort.is_set():
            ready = [
                p
                for p in phases
                if p.id in pending and p.depends_on is not None and all(dep in finished for dep in p.depends_on)
            ]

            if not ready:
                break

            # LoadProject barrier: if a load_project is ready, only run load_project phases,
            # and at most one per wave.
            if any(isinstance(p, LoadProjectPhase) for p in ready):
                ready = [p for p in ready if isinstance(p, LoadProjectPhase)][:1]

            # Run all ready phases concurrently
            wave_results = await asyncio.gather(
                *(self._run_single_phase(p, context, results, checkpoint) for p in ready)
            )

            for p, result in zip(ready, wave_results):
                finished.add(p.id)
                pending.discard(p.id)
                if result is not None:
                    results[p.id] = copy.deepcopy(result)

        if checkpoint and not self._failed_abort.is_set():
            await checkpoint.mark_completed()

        return results
