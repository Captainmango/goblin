from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime

from filelock import FileLock, Timeout

from goblin.enums import PhaseStatus, RunStatus


class Checkpoint:
    def __init__(self, run_dir: str, run_id: str, blueprint_path: str, context: dict):
        os.makedirs(os.path.join(run_dir, ".goblin"), exist_ok=True)
        self._path = os.path.join(run_dir, ".goblin", f"run-{run_id}.json")
        self._data = {
            "run_id": run_id,
            "blueprint_path": blueprint_path,
            "context": context,
            "started_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "status": RunStatus.IN_PROGRESS,
            "phases": {},
        }
        self._lock = asyncio.Lock()
        self._file_lock_path = self._path + ".lock"
        self._file_lock_timeout = 30.0
        self._flush()

    async def phase_started(self, phase_id: str, input_data: dict) -> None:
        async with self._lock:
            self._data["phases"][phase_id] = {
                "status": PhaseStatus.RUNNING,
                "input": input_data,
                "output": None,
                "started_at": datetime.now(UTC).isoformat(),
                "completed_at": None,
            }
            self._data["updated_at"] = datetime.now(UTC).isoformat()
            await self._flush_async()

    async def phase_completed(self, phase_id: str, output: dict) -> None:
        async with self._lock:
            self._data["phases"][phase_id]["status"] = PhaseStatus.COMPLETED
            self._data["phases"][phase_id]["output"] = output
            self._data["phases"][phase_id]["completed_at"] = datetime.now(UTC).isoformat()
            self._data["updated_at"] = datetime.now(UTC).isoformat()
            await self._flush_async()

    async def phase_failed(self, phase_id: str, output: dict) -> None:
        async with self._lock:
            self._data["phases"][phase_id]["status"] = PhaseStatus.FAILED
            self._data["phases"][phase_id]["output"] = output
            self._data["phases"][phase_id]["completed_at"] = datetime.now(UTC).isoformat()
            self._data["updated_at"] = datetime.now(UTC).isoformat()
            await self._flush_async()

    async def phase_skipped(self, phase_id: str) -> None:
        async with self._lock:
            self._data["phases"][phase_id] = {
                "status": PhaseStatus.SKIPPED,
                "input": None,
                "output": None,
                "started_at": None,
                "completed_at": None,
            }
            self._data["updated_at"] = datetime.now(UTC).isoformat()
            await self._flush_async()

    async def mark_completed(self) -> None:
        async with self._lock:
            self._data["status"] = RunStatus.COMPLETED
            self._data["updated_at"] = datetime.now(UTC).isoformat()
            await self._flush_async()

    async def mark_failed(self) -> None:
        async with self._lock:
            self._data["status"] = RunStatus.FAILED
            self._data["updated_at"] = datetime.now(UTC).isoformat()
            await self._flush_async()

    async def completed_phase_ids(self) -> set[str]:
        async with self._lock:
            return {
                phase_id
                for phase_id, phase_data in self._data["phases"].items()
                if phase_data["status"] == PhaseStatus.COMPLETED
            }

    async def finished_phase_ids(self) -> set[str]:
        """Return IDs of phases that are completed, skipped, or failed."""
        async with self._lock:
            return {
                phase_id
                for phase_id, phase_data in self._data["phases"].items()
                if phase_data["status"] in (PhaseStatus.COMPLETED, PhaseStatus.SKIPPED, PhaseStatus.FAILED)
            }

    async def skipped_phase_ids(self) -> set[str]:
        async with self._lock:
            return {
                phase_id
                for phase_id, phase_data in self._data["phases"].items()
                if phase_data["status"] == PhaseStatus.SKIPPED
            }

    async def get_results(self) -> dict:
        async with self._lock:
            return {
                phase_id: phase_data["output"]
                for phase_id, phase_data in self._data["phases"].items()
                if phase_data["status"] == PhaseStatus.COMPLETED and phase_data["output"] is not None
            }

    @classmethod
    async def load(cls, path: str) -> Checkpoint:
        instance = cls.__new__(cls)
        instance._path = path
        instance._lock = asyncio.Lock()
        instance._file_lock_path = path + ".lock"
        instance._file_lock_timeout = 30.0

        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, instance._read_locked, path)
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
        """Synchronous flush for internal use (init/load)."""
        tmp_path = self._path + ".tmp"
        payload = json.dumps(self._data, indent=2, default=str)
        lock = FileLock(self._file_lock_path)
        try:
            with lock.acquire(timeout=self._file_lock_timeout):
                with open(tmp_path, "w") as f:
                    f.write(payload)
                os.replace(tmp_path, self._path)
        except Timeout:
            raise TimeoutError(f"Checkpoint lock acquisition timed out: {self._file_lock_path}")

    async def _flush_async(self) -> None:
        """Asynchronous flush that yields the event loop while writing."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._flush)

    def _read_locked(self, path: str) -> dict:
        lock = FileLock(self._file_lock_path)
        try:
            with lock.acquire(timeout=self._file_lock_timeout):
                with open(path) as f:
                    return json.load(f)
        except Timeout:
            raise TimeoutError(f"Checkpoint lock acquisition timed out: {self._file_lock_path}")
