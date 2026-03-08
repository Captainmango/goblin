from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from goblin.enums import RunStatus, PhaseStatus


class Checkpoint:
    def __init__(self, run_dir: str, run_id: str, blueprint_path: str, context: dict):
        os.makedirs(os.path.join(run_dir, ".goblin"), exist_ok=True)
        self._path = os.path.join(run_dir, ".goblin", f"run-{run_id}.json")
        self._data = {
            "run_id": run_id,
            "blueprint_path": blueprint_path,
            "context": context,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": RunStatus.IN_PROGRESS,
            "phases": {},
        }
        self._flush()

    def phase_started(self, phase_id: str, input_data: dict) -> None:
        self._data["phases"][phase_id] = {
            "status": PhaseStatus.RUNNING,
            "input": input_data,
            "output": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._flush()

    def phase_completed(self, phase_id: str, output: dict) -> None:
        self._data["phases"][phase_id]["status"] = PhaseStatus.COMPLETED
        self._data["phases"][phase_id]["output"] = output
        self._data["phases"][phase_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._flush()

    def phase_failed(self, phase_id: str, output: dict) -> None:
        self._data["phases"][phase_id]["status"] = PhaseStatus.FAILED
        self._data["phases"][phase_id]["output"] = output
        self._data["phases"][phase_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._flush()

    def phase_skipped(self, phase_id: str) -> None:
        self._data["phases"][phase_id] = {
            "status": PhaseStatus.SKIPPED,
            "input": None,
            "output": None,
            "started_at": None,
            "completed_at": None,
        }
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._flush()

    def mark_completed(self) -> None:
        self._data["status"] = RunStatus.COMPLETED
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._flush()

    def mark_failed(self) -> None:
        self._data["status"] = RunStatus.FAILED
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._flush()

    def completed_phase_ids(self) -> set[str]:
        return {
            phase_id
            for phase_id, phase_data in self._data["phases"].items()
            if phase_data["status"] == PhaseStatus.COMPLETED
        }

    def get_results(self) -> dict:
        return {
            phase_id: phase_data["output"]
            for phase_id, phase_data in self._data["phases"].items()
            if phase_data["status"] == PhaseStatus.COMPLETED and phase_data["output"] is not None
        }

    @classmethod
    def load(cls, path: str) -> Checkpoint:
        with open(path) as f:
            data = json.load(f)

        instance = cls.__new__(cls)
        instance._path = path
        instance._data = data

        # Remove any phases that were still running when the process died
        running_ids = [phase_id for phase_id, phase_data in data["phases"].items() if phase_data["status"] == "running"]
        for phase_id in running_ids:
            del instance._data["phases"][phase_id]

        instance._data["status"] = RunStatus.IN_PROGRESS
        instance._flush()
        return instance

    @property
    def path(self) -> str:
        return self._path

    def _flush(self) -> None:
        tmp_path = self._path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(json.dumps(self._data, indent=2, default=str))
        os.replace(tmp_path, self._path)
