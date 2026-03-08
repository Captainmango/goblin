"""Tests for Checkpoint — init, phase lifecycle, run status, queries, load, and atomic writes."""

from __future__ import annotations

import json
import os

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
    def test_phase_started_records_running(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.phase_started("s1", {"context": {"x": 1}})
        data = _read_checkpoint(cp.path)
        assert data["phases"]["s1"]["status"] == "running"
        assert data["phases"]["s1"]["input"] == {"context": {"x": 1}}

    def test_phase_completed_records_output(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.phase_started("s1", {})
        cp.phase_completed("s1", {"success": True, "message": ["ok"]})
        data = _read_checkpoint(cp.path)
        assert data["phases"]["s1"]["status"] == "completed"
        assert data["phases"]["s1"]["output"]["success"] is True
        assert data["phases"]["s1"]["completed_at"] is not None

    def test_phase_failed_records_output(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.phase_started("s1", {})
        cp.phase_failed("s1", {"success": False})
        data = _read_checkpoint(cp.path)
        assert data["phases"]["s1"]["status"] == "failed"

    def test_phase_skipped_records_skipped(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.phase_skipped("s1")
        data = _read_checkpoint(cp.path)
        assert data["phases"]["s1"]["status"] == "skipped"
        assert data["phases"]["s1"]["input"] is None
        assert data["phases"]["s1"]["output"] is None


# ---------------------------------------------------------------------------
# Run status
# ---------------------------------------------------------------------------


class TestCheckpointRunStatus:
    def test_mark_completed(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.mark_completed()
        data = _read_checkpoint(cp.path)
        assert data["status"] == "completed"

    def test_mark_failed(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.mark_failed()
        data = _read_checkpoint(cp.path)
        assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


class TestCheckpointQueries:
    def test_completed_phase_ids(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.phase_started("s1", {})
        cp.phase_completed("s1", {"success": True})
        cp.phase_started("s2", {})
        cp.phase_completed("s2", {"success": True})
        cp.phase_started("s3", {})
        cp.phase_failed("s3", {"success": False})
        assert cp.completed_phase_ids() == {"s1", "s2"}

    def test_get_results(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.phase_started("s1", {})
        cp.phase_completed("s1", {"success": True, "message": ["a"]})
        cp.phase_started("s2", {})
        cp.phase_completed("s2", {"success": True, "message": ["b"]})
        cp.phase_started("s3", {})
        cp.phase_failed("s3", {"success": False})
        assert cp.get_results() == {
            "s1": {"success": True, "message": ["a"]},
            "s2": {"success": True, "message": ["b"]},
        }


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


class TestCheckpointLoad:
    def test_load_round_trip(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {"key": "val"})
        cp.phase_started("s1", {})
        cp.phase_completed("s1", {"success": True, "message": ["done"]})

        loaded = Checkpoint.load(cp.path)
        assert loaded.completed_phase_ids() == {"s1"}
        assert loaded.get_results()["s1"] == {"success": True, "message": ["done"]}

    def test_load_resets_running_phases(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.phase_started("s1", {})
        # s1 is still "running" — not completed

        loaded = Checkpoint.load(cp.path)
        assert "s1" not in loaded.completed_phase_ids()
        assert "s1" not in loaded._data["phases"]

    def test_load_sets_status_in_progress(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.mark_completed()

        loaded = Checkpoint.load(cp.path)
        assert loaded._data["status"] == "in_progress"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


class TestCheckpointAtomicWrite:
    def test_no_tmp_file_left_after_flush(self, tmp_path):
        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.phase_started("s1", {})
        cp.phase_completed("s1", {"success": True})
        assert os.path.exists(cp.path)
        assert not os.path.exists(cp.path + ".tmp")
