import logging
import os
import uuid

import yaml
from goblin.blueprint import Blueprint, V1Blueprint
from goblin.checkpoint import Checkpoint

logger = logging.getLogger(__name__)

BLUEPRINT_REGISTRY: dict[str, type] = {
    "v1": V1Blueprint,
}


class WorkflowRunner:
    def __init__(self, workflow_path: str, run_dir: str | None = None):
        with open(workflow_path) as f:
            raw = yaml.safe_load(f)
        self.blueprint = self._load_blueprint(raw)
        self.workflow_path = workflow_path
        self._run_dir = run_dir or os.getcwd()
        self._run_id = str(uuid.uuid4())

    def _load_blueprint(self, raw: dict) -> Blueprint:
        version = raw.get("version")
        if version is None:
            raise ValueError("Workflow is missing required 'version' key")
        blueprint_cls = BLUEPRINT_REGISTRY.get(version)
        if blueprint_cls is None:
            supported = sorted(BLUEPRINT_REGISTRY.keys())
            raise ValueError(f"Unsupported workflow version {version}. Supported: {supported}")
        return blueprint_cls(raw["workflow"])

    async def run(self, context: dict | None = None) -> dict:
        logger.info("Workflow started", extra={"event": "workflow.start", "run_id": self._run_id})
        checkpoint = Checkpoint(
            run_dir=self._run_dir,
            run_id=self._run_id,
            blueprint_path=self.workflow_path,
            context=context or {},
        )
        logger.info("Checkpoint file", extra={"event": "workflow.checkpoint", "path": checkpoint.path})
        results = await self.blueprint.run(context, checkpoint=checkpoint)
        logger.debug("Workflow results", extra={"event": "workflow.results", "results": results})
        logger.info("Workflow complete", extra={"event": "workflow.complete"})
        return results

    @classmethod
    async def resume(cls, checkpoint_path: str) -> dict:
        checkpoint = Checkpoint.load(checkpoint_path)
        blueprint_path = checkpoint._data["blueprint_path"]
        context = checkpoint._data["context"]

        with open(blueprint_path) as f:
            raw = yaml.safe_load(f)

        runner = cls.__new__(cls)
        runner.blueprint = runner._load_blueprint(raw)
        runner.workflow_path = blueprint_path
        runner._run_dir = os.path.dirname(os.path.dirname(checkpoint_path))
        runner._run_id = checkpoint._data["run_id"]

        logger.info("Resuming workflow", extra={"event": "workflow.resume", "run_id": runner._run_id})
        results = await runner.blueprint.run(context, checkpoint=checkpoint)
        logger.info("Workflow complete", extra={"event": "workflow.complete"})
        return results
