import yaml
from goblin.blueprint import Blueprint, V1Blueprint

BLUEPRINT_REGISTRY: dict[int, type] = {
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
            raise ValueError(
                f"Unsupported workflow version {version}. Supported: {supported}"
            )
        return blueprint_cls(raw["workflow"])

    async def run(self, context: dict = {}) -> dict:
        results = await self.blueprint.run(context)
        print(results)
        return results
