from __future__ import annotations

import logging
import uuid
from pathlib import Path

import yaml

from goblin.blueprint import Blueprint, V1Blueprint
from goblin.checkpoint import Checkpoint
from goblin.import_resolver import resolve_imports
from goblin.validation import format_errors, validate_blueprint

logger = logging.getLogger(__name__)

BLUEPRINT_REGISTRY: dict[str, type] = {
    "v1": V1Blueprint,
}


def _raise_validation_error(blueprint_path: str, errors: list[dict]) -> None:
    formatted = format_errors(errors)
    raise ValueError(
        f"Blueprint '{blueprint_path}' failed schema validation.\n"
        f"Run 'goblin validate {blueprint_path}' for details.\n"
        f"{formatted}"
    )


class BlueprintRunner:
    def __init__(self, blueprint_path: str, run_dir: str | None = None):
        path = Path(blueprint_path)
        raw = yaml.safe_load(path.read_text())
        base_dir = path.resolve().parent
        raw = resolve_imports(raw, str(base_dir), str(base_dir))
        errors = validate_blueprint(str(path), raw=raw)
        if errors:
            _raise_validation_error(str(path), errors)
        self.blueprint = self._load_blueprint(raw)
        self.blueprint_path = str(path)
        self._run_dir = Path(run_dir) if run_dir else Path.cwd()
        self._run_id = str(uuid.uuid4())

    def _load_blueprint(self, raw: dict) -> Blueprint:
        version = raw.get("version")
        if version is None:
            raise ValueError("Blueprint is missing required 'version' key")
        blueprint_cls = BLUEPRINT_REGISTRY.get(version)
        if blueprint_cls is None:
            supported = sorted(BLUEPRINT_REGISTRY.keys())
            raise ValueError(f"Unsupported blueprint version {version}. Supported: {supported}")
        return blueprint_cls(raw["blueprint"])

    async def run(self, context: dict | None = None) -> dict:
        logger.info("Blueprint started", extra={"event": "blueprint.start", "run_id": self._run_id})
        checkpoint = Checkpoint(
            run_dir=str(self._run_dir),
            run_id=self._run_id,
            blueprint_path=self.blueprint_path,
            context=context or {},
        )
        logger.info("Checkpoint file", extra={"event": "blueprint.checkpoint", "path": checkpoint.path})
        results = await self.blueprint.run(context, checkpoint=checkpoint)
        logger.debug("Blueprint results", extra={"event": "blueprint.results", "results": results})
        logger.info("Blueprint complete", extra={"event": "blueprint.complete"})
        return results

    @classmethod
    async def from_checkpoint(cls, checkpoint_path: str) -> BlueprintRunner:
        """Rehydrate a runner from a persisted checkpoint."""
        checkpoint = await Checkpoint.load(checkpoint_path)
        runner = cls.__new__(cls)
        runner.blueprint_path = checkpoint.blueprint_path
        runner._run_dir = Path(checkpoint_path).resolve().parent.parent
        runner._run_id = checkpoint.run_id

        path = Path(runner.blueprint_path)
        raw = yaml.safe_load(path.read_text())
        base_dir = path.resolve().parent
        raw = resolve_imports(raw, str(base_dir), str(base_dir))
        errors = validate_blueprint(str(path), raw=raw)
        if errors:
            _raise_validation_error(runner.blueprint_path, errors)
        runner.blueprint = runner._load_blueprint(raw)
        return runner

    @classmethod
    async def resume(cls, checkpoint_path: str) -> dict:
        """Resume a blueprint run from a checkpoint file."""
        runner = await cls.from_checkpoint(checkpoint_path)
        checkpoint = await Checkpoint.load(checkpoint_path)
        context = checkpoint.context

        logger.info("Resuming blueprint", extra={"event": "blueprint.resume", "run_id": runner._run_id})
        results = await runner.blueprint.run(context, checkpoint=checkpoint)
        logger.info("Blueprint complete", extra={"event": "blueprint.complete"})
        return results
