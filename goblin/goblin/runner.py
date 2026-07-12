import logging
import os
import uuid

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
        with open(blueprint_path) as f:
            raw = yaml.safe_load(f)
        base_dir = os.path.dirname(os.path.abspath(blueprint_path))
        raw = resolve_imports(raw, base_dir, base_dir)
        errors = validate_blueprint(blueprint_path, raw=raw)
        if errors:
            _raise_validation_error(blueprint_path, errors)
        self.blueprint = self._load_blueprint(raw)
        self.blueprint_path = blueprint_path
        self._run_dir = run_dir or os.getcwd()
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
            run_dir=self._run_dir,
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
    async def resume(cls, checkpoint_path: str) -> dict:
        checkpoint = await Checkpoint.load(checkpoint_path)
        blueprint_path = checkpoint._data["blueprint_path"]
        context = checkpoint._data["context"]

        with open(blueprint_path) as f:
            raw = yaml.safe_load(f)

        base_dir = os.path.dirname(os.path.abspath(blueprint_path))
        raw = resolve_imports(raw, base_dir, base_dir)
        errors = validate_blueprint(blueprint_path, raw=raw)
        if errors:
            _raise_validation_error(blueprint_path, errors)

        runner = cls.__new__(cls)
        runner.blueprint = runner._load_blueprint(raw)
        runner.blueprint_path = blueprint_path
        runner._run_dir = os.path.dirname(os.path.dirname(checkpoint_path))
        runner._run_id = checkpoint._data["run_id"]

        logger.info("Resuming blueprint", extra={"event": "blueprint.resume", "run_id": runner._run_id})
        results = await runner.blueprint.run(context, checkpoint=checkpoint)
        logger.info("Blueprint complete", extra={"event": "blueprint.complete"})
        return results
