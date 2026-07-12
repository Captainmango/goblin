"""Tests that spec/v1.json contains the new cost control fields."""

from __future__ import annotations

import json

from goblin_cli.blueprint import V1Blueprint


def _load_schema() -> dict:
    with open("../spec/v1.json") as f:
        return json.load(f)


class TestSchemaHasCostControlFields:
    def test_phase_has_max_iterations(self):
        schema = _load_schema()
        phase_props = schema["definitions"]["phase"]["properties"]
        assert "max_iterations" in phase_props
        assert phase_props["max_iterations"]["type"] == "integer"

    def test_phase_has_max_tokens(self):
        schema = _load_schema()
        phase_props = schema["definitions"]["phase"]["properties"]
        assert "max_tokens" in phase_props
        assert phase_props["max_tokens"]["type"] == "integer"

    def test_phase_has_max_cost(self):
        schema = _load_schema()
        phase_props = schema["definitions"]["phase"]["properties"]
        assert "max_cost" in phase_props
        assert phase_props["max_cost"]["type"] == "number"

    def test_swarm_has_max_tokens(self):
        schema = _load_schema()
        swarm_props = schema["definitions"]["swarm"]["properties"]
        assert "max_tokens" in swarm_props

    def test_swarm_has_max_cost(self):
        schema = _load_schema()
        swarm_props = schema["definitions"]["swarm"]["properties"]
        assert "max_cost" in swarm_props

    def test_swarm_coordinator_has_limit_fields(self):
        schema = _load_schema()
        coord_props = schema["definitions"]["swarmCoordinator"]["properties"]
        assert "max_iterations" in coord_props
        assert "max_tokens" in coord_props
        assert "max_cost" in coord_props

    def test_swarm_agent_has_limit_fields(self):
        schema = _load_schema()
        agent_props = schema["definitions"]["swarmAgent"]["properties"]
        assert "max_iterations" in agent_props
        assert "max_tokens" in agent_props
        assert "max_cost" in agent_props


class TestBlueprintParsesNewFields:
    def test_prompt_phase_with_limits(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "analyze",
                        "prompt": "Review code.",
                        "max_iterations": 20,
                        "max_tokens": 50000,
                        "max_cost": 0.5,
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert phase.max_iterations == 20
        assert phase.max_tokens == 50000
        assert phase.max_cost == 0.5

    def test_swarm_with_limits(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "build",
                        "swarm": {
                            "goal": "Implement auth",
                            "coordinator": {
                                "prompt": "Plan tasks.",
                                "max_iterations": 10,
                                "max_tokens": 20000,
                                "max_cost": 0.25,
                            },
                            "agents": [
                                {
                                    "name": "backend",
                                    "prompt": "Implement.",
                                    "max_iterations": 50,
                                    "max_tokens": 100000,
                                    "max_cost": 1.0,
                                }
                            ],
                            "max_rounds": 5,
                            "max_iterations": 100,
                            "max_tokens": 500000,
                            "max_cost": 5.0,
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert phase.max_rounds == 5
        assert phase.max_iterations == 100
        assert phase.max_tokens == 500000
        assert phase.max_cost == 5.0
        assert phase.coordinator_config["max_iterations"] == 10
        assert phase.coordinator_config["max_tokens"] == 20000
        assert phase.coordinator_config["max_cost"] == 0.25
        assert phase.agents_config[0]["max_iterations"] == 50
        assert phase.agents_config[0]["max_tokens"] == 100000
        assert phase.agents_config[0]["max_cost"] == 1.0

    def test_backward_compatible_swarm_without_new_fields(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        "id": "build",
                        "swarm": {
                            "goal": "Implement auth",
                            "coordinator": {"prompt": "Plan tasks."},
                            "agents": [{"name": "backend", "prompt": "Implement."}],
                            "max_rounds": 5,
                            "max_iterations": 100,
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert phase.max_rounds == 5
        assert phase.max_iterations == 100
        assert phase.max_tokens is None
        assert phase.max_cost is None
