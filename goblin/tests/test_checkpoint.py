"""Tests for Checkpoint — init, phase lifecycle, run status, queries, load, atomic writes, and concurrency."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from goblin.checkpoint import Checkpoint


def _read_checkpoint(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestCheckpointInit:
    def test_creates_goblin_dir_and_file(self, tmp_path):
        Checkpoint(str(tmp_path), "abc123", "test.yaml", {"key": "val"})
        file_path = tmp_path / ".goblin" / "run-abc123.json"
        assert file_path.exists()
        data = _read_checkpoint(str(file_path))
        assert data["run_id"] == "abc123"
        assert data["status"] == "in_progress"
        assert data["context"] == {"key": "val"}
        assert data["phases"] == {}


# ---------------------------------------------------------------------------
# Phase lifecycle
# ---------------------------------------------------------------------------


class TestCheckpointPhaseLifecycle:
    @pytest.mark.asyncio
    async def test_phase_started_records_running(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("s1", {"context": {"x": 1}})
        data = _read_checkpoint(cp.path)
        assert data["phases"]["s1"]["status"] == "running"
        assert data["phases"]["s1"]["input"] == {"context": {"x": 1}}

    @pytest.mark.asyncio
    async def test_phase_completed_records_output(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("s1", {})
        await cp.phase_completed("s1", {"success": True, "message": ["ok"]})
        data = _read_checkpoint(cp.path)
        assert data["phases"]["s1"]["status"] == "completed"
        assert data["phases"]["s1"]["output"]["success"] is True
        assert data["phases"]["s1"]["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_phase_failed_records_output(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("s1", {})
        await cp.phase_failed("s1", {"success": False})
        data = _read_checkpoint(cp.path)
        assert data["phases"]["s1"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_phase_skipped_records_skipped(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_skipped("s1")
        data = _read_checkpoint(cp.path)
        assert data["phases"]["s1"]["status"] == "skipped"
        assert data["phases"]["s1"]["input"] is None
        assert data["phases"]["s1"]["output"] is None


# ---------------------------------------------------------------------------
# Run status
# ---------------------------------------------------------------------------


class TestCheckpointRunStatus:
    @pytest.mark.asyncio
    async def test_mark_completed(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.mark_completed()
        data = _read_checkpoint(cp.path)
        assert data["status"] == "completed"

    @pytest.mark.asyncio
    async def test_mark_failed(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.mark_failed()
        data = _read_checkpoint(cp.path)
        assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


class TestCheckpointQueries:
    @pytest.mark.asyncio
    async def test_completed_phase_ids(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("s1", {})
        await cp.phase_completed("s1", {"success": True})
        await cp.phase_started("s2", {})
        await cp.phase_completed("s2", {"success": True})
        await cp.phase_started("s3", {})
        await cp.phase_failed("s3", {"success": False})
        assert await cp.completed_phase_ids() == {"s1", "s2"}

    @pytest.mark.asyncio
    async def test_get_results(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("s1", {})
        await cp.phase_completed("s1", {"success": True, "message": ["a"]})
        await cp.phase_started("s2", {})
        await cp.phase_completed("s2", {"success": True, "message": ["b"]})
        await cp.phase_started("s3", {})
        await cp.phase_failed("s3", {"success": False})
        assert await cp.get_results() == {
            "s1": {"success": True, "message": ["a"]},
            "s2": {"success": True, "message": ["b"]},
        }


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


class TestCheckpointLoad:
    @pytest.mark.asyncio
    async def test_load_round_trip(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {"key": "val"})
        await cp.phase_started("s1", {})
        await cp.phase_completed("s1", {"success": True, "message": ["done"]})

        loaded = await Checkpoint.load(cp.path)
        assert await loaded.completed_phase_ids() == {"s1"}
        assert (await loaded.get_results())["s1"] == {"success": True, "message": ["done"]}

    @pytest.mark.asyncio
    async def test_load_resets_running_phases(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("s1", {})
        # s1 is still "running" — not completed

        loaded = await Checkpoint.load(cp.path)
        assert "s1" not in await loaded.completed_phase_ids()
        assert "s1" not in loaded._data["phases"]

    @pytest.mark.asyncio
    async def test_load_sets_status_in_progress(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.mark_completed()

        loaded = await Checkpoint.load(cp.path)
        assert loaded._data["status"] == "in_progress"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


class TestCheckpointAtomicWrite:
    @pytest.mark.asyncio
    async def test_no_tmp_file_left_after_flush(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("s1", {})
        await cp.phase_completed("s1", {"success": True})
        assert os.path.exists(cp.path)
        assert not os.path.exists(cp.path + ".tmp")


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestCheckpointConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_phase_updates_are_safe(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})

        async def complete_phase(phase_id: str):
            await cp.phase_started(phase_id, {})
            await asyncio.sleep(0)  # yield to interleave with other tasks
            await cp.phase_completed(phase_id, {"success": True})

        await asyncio.gather(
            complete_phase("a"),
            complete_phase("b"),
            complete_phase("c"),
        )

        data = _read_checkpoint(cp.path)
        assert data["phases"]["a"]["status"] == "completed"
        assert data["phases"]["b"]["status"] == "completed"
        assert data["phases"]["c"]["status"] == "completed"
        assert await cp.completed_phase_ids() == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_finished_phase_ids_includes_all_terminal_statuses(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("a", {})
        await cp.phase_completed("a", {"success": True})
        await cp.phase_skipped("b")
        await cp.phase_started("c", {})
        await cp.phase_failed("c", {"success": False})

        assert await cp.finished_phase_ids() == {"a", "b", "c"}
