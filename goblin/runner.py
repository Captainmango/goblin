import logging

import yaml
from goblin.blueprint import Blueprint, V1Blueprint

logger = logging.getLogger(__name__)

BLUEPRINT_REGISTRY: dict[str, type] = {
    "v1": V1Blueprint,
}


class WorkflowRunner:
    def __init__(self, workflow_path: str):
        with open(workflow_path) as f:
            raw = yaml.safe_load(f)
        self.blueprint = self._load_blueprint(raw)

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
        logger.info("Workflow started", extra={"event": "workflow.start"})
        results = await self.blueprint.run(context)
        logger.debug("Workflow results", extra={"event": "workflow.results", "results": results})
        logger.info("Workflow complete", extra={"event": "workflow.complete"})
        return results
