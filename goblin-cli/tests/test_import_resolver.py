"""Tests for blueprint import resolution (goblin_cli/import_resolver.py)."""

from __future__ import annotations

import os
from typing import Any

import pytest
import yaml

from goblin_cli.import_resolver import (
    resolve_imports,
    _deep_merge,
    _rewrite_condition,
)
from goblin_cli.runner import BlueprintRunner
from goblin_cli.validation import validate_blueprint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: str, data: dict[str, Any]) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(data, f)


def _make_blueprint(
    name: str = "test",
    phases: list[dict] | None = None,
    vars: dict | None = None,
    defaults: dict | None = None,
    workspace: dict | None = None,
    imports: list[dict] | None = None,
) -> dict[str, Any]:
    bp: dict[str, Any] = {"name": name}
    if phases is not None:
        bp["phases"] = phases
    if vars is not None:
        bp["vars"] = vars
    if defaults is not None:
        bp["defaults"] = defaults
    if workspace is not None:
        bp["workspace"] = workspace
    if imports is not None:
        bp["imports"] = imports
    return {"version": "v1", "blueprint": bp}


# ---------------------------------------------------------------------------
# Basic import resolution
# ---------------------------------------------------------------------------


class TestBasicImports:
    def test_import_with_prefix_merges_phases(self, tmp_path):
        setup = _make_blueprint(
            name="setup",
            phases=[
                {"id": "checkout", "prompt": "Clone"},
                {"id": "install", "prompt": "Install", "depends_on": ["checkout"]},
            ],
        )
        setup_path = tmp_path / "setup.yaml"
        _write_yaml(str(setup_path), setup)

        parent = _make_blueprint(
            name="deploy",
            imports=[{"path": "./setup.yaml", "as": "setup"}],
            phases=[{"id": "deploy", "prompt": "Deploy", "depends_on": ["setup.install"]}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        phases = resolved["blueprint"]["phases"]
        ids = [p["id"] for p in phases]
        assert ids == ["setup.checkout", "setup.install", "deploy"]
        assert phases[1]["depends_on"] == ["setup.checkout"]
        assert phases[2]["depends_on"] == ["setup.install"]

    def test_import_without_prefix_no_collision(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[{"id": "c1", "prompt": "Child"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml"}],
            phases=[{"id": "p1", "prompt": "Parent"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        ids = [p["id"] for p in resolved["blueprint"]["phases"]]
        assert ids == ["c1", "p1"]

    def test_import_without_prefix_collision_raises(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[{"id": "dup", "prompt": "Child"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml"}],
            phases=[{"id": "dup", "prompt": "Parent"}],
        )

        with pytest.raises(ValueError, match="Duplicate phase ID 'dup'"):
            resolve_imports(parent, str(tmp_path), str(tmp_path))


# ---------------------------------------------------------------------------
# Merge semantics
# ---------------------------------------------------------------------------


class TestMergeSemantics:
    def test_vars_shallow_merge_parent_wins(self, tmp_path):
        child = _make_blueprint(
            name="child",
            vars={"a": "1", "b": "2"},
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            vars={"b": "3", "c": "4"},
            phases=[{"id": "p2", "prompt": "world"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        assert resolved["blueprint"]["vars"] == {"a": "1", "b": "3", "c": "4"}

    def test_defaults_deep_merge(self, tmp_path):
        child = _make_blueprint(
            name="child",
            defaults={"hooks": {"pre_tool": ["a"]}, "model": "child-model"},
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            defaults={"hooks": {"post_tool": ["b"]}},
            phases=[{"id": "p2", "prompt": "world"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        assert resolved["blueprint"]["defaults"] == {
            "hooks": {"pre_tool": ["a"], "post_tool": ["b"]},
            "model": "child-model",
        }

    def test_defaults_parent_overrides_child_key(self, tmp_path):
        child = _make_blueprint(
            name="child",
            defaults={"hooks": {"pre_tool": ["a"], "post_tool": ["b"]}},
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            defaults={"hooks": {"pre_tool": ["x"]}},
            phases=[{"id": "p2", "prompt": "world"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        assert resolved["blueprint"]["defaults"] == {
            "hooks": {"pre_tool": ["x"], "post_tool": ["b"]},
        }

    def test_workspace_parent_replaces_imported(self, tmp_path):
        child = _make_blueprint(
            name="child",
            workspace={"type": "local", "working_dir": "/child"},
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            workspace={"type": "remote", "host": "http://example.com"},
            phases=[{"id": "p2", "prompt": "world"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        assert resolved["blueprint"]["workspace"] == {
            "type": "remote",
            "host": "http://example.com",
        }

    def test_workspace_imported_preserved_when_parent_missing(self, tmp_path):
        child = _make_blueprint(
            name="child",
            workspace={"type": "local", "working_dir": "/child"},
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p2", "prompt": "world"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        assert resolved["blueprint"].get("workspace") == {
            "type": "local",
            "working_dir": "/child",
        }


# ---------------------------------------------------------------------------
# Reference rewriting
# ---------------------------------------------------------------------------


class TestReferenceRewriting:
    def test_depends_on_rewritten_with_prefix(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[
                {"id": "a", "prompt": "A"},
                {"id": "b", "prompt": "B", "depends_on": ["a"]},
            ],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p", "prompt": "P", "depends_on": ["c.b"]}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        phases = {p["id"]: p for p in resolved["blueprint"]["phases"]}
        assert phases["c.b"]["depends_on"] == ["c.a"]
        assert phases["p"]["depends_on"] == ["c.b"]

    def test_history_rewritten_with_prefix(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[
                {"id": "a", "prompt": "A"},
                {"id": "b", "prompt": "B", "history": ["a"]},
            ],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p", "prompt": "P"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        phases = {p["id"]: p for p in resolved["blueprint"]["phases"]}
        assert phases["c.b"]["history"] == ["c.a"]

    def test_condition_rewritten_with_prefix(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[
                {"id": "check", "prompt": "Check"},
                {
                    "id": "act",
                    "prompt": "Act",
                    "condition": "{{ steps.check.success }}",
                },
            ],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p", "prompt": "P"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        phases = {p["id"]: p for p in resolved["blueprint"]["phases"]}
        assert phases["c.act"]["condition"] == "{{ steps.c.check.success }}"

    def test_condition_longest_match_first(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[
                {"id": "a", "prompt": "A"},
                {"id": "ab", "prompt": "AB"},
                {
                    "id": "act",
                    "prompt": "Act",
                    "condition": "{{ steps.ab.success and steps.a.success }}",
                },
            ],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p", "prompt": "P"}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        phases = {p["id"]: p for p in resolved["blueprint"]["phases"]}
        cond = phases["c.act"]["condition"]
        assert "steps.c.ab.success" in cond
        assert "steps.c.a.success" in cond


# ---------------------------------------------------------------------------
# Nested imports
# ---------------------------------------------------------------------------


class TestNestedImports:
    def test_nested_import_resolution(self, tmp_path):
        grandchild = _make_blueprint(
            name="grandchild",
            phases=[{"id": "g1", "prompt": "G"}],
        )
        grandchild_path = tmp_path / "grandchild.yaml"
        _write_yaml(str(grandchild_path), grandchild)

        child = _make_blueprint(
            name="child",
            imports=[{"path": "./grandchild.yaml", "as": "gc"}],
            phases=[{"id": "c1", "prompt": "C", "depends_on": ["gc.g1"]}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "ch"}],
            phases=[{"id": "p1", "prompt": "P", "depends_on": ["ch.c1"]}],
        )

        resolved = resolve_imports(parent, str(tmp_path), str(tmp_path))
        phases = {p["id"]: p for p in resolved["blueprint"]["phases"]}
        assert "ch.gc.g1" in phases
        assert phases["ch.c1"]["depends_on"] == ["ch.gc.g1"]
        assert phases["p1"]["depends_on"] == ["ch.c1"]


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_direct_cycle_raises(self, tmp_path):
        a_path = tmp_path / "a.yaml"
        b_path = tmp_path / "b.yaml"

        a = _make_blueprint(
            name="a",
            imports=[{"path": "./b.yaml", "as": "b"}],
            phases=[{"id": "a1", "prompt": "A"}],
        )
        b = _make_blueprint(
            name="b",
            imports=[{"path": "./a.yaml", "as": "a"}],
            phases=[{"id": "b1", "prompt": "B"}],
        )
        _write_yaml(str(a_path), a)
        _write_yaml(str(b_path), b)

        with pytest.raises(ValueError, match="Import cycle detected"):
            resolve_imports(a, str(tmp_path), str(tmp_path))

    def test_indirect_cycle_raises(self, tmp_path):
        a_path = tmp_path / "a.yaml"
        b_path = tmp_path / "b.yaml"
        c_path = tmp_path / "c.yaml"

        a = _make_blueprint(
            name="a",
            imports=[{"path": "./b.yaml", "as": "b"}],
            phases=[{"id": "a1", "prompt": "A"}],
        )
        b = _make_blueprint(
            name="b",
            imports=[{"path": "./c.yaml", "as": "c"}],
            phases=[{"id": "b1", "prompt": "B"}],
        )
        c = _make_blueprint(
            name="c",
            imports=[{"path": "./a.yaml", "as": "a"}],
            phases=[{"id": "c1", "prompt": "C"}],
        )
        _write_yaml(str(a_path), a)
        _write_yaml(str(b_path), b)
        _write_yaml(str(c_path), c)

        with pytest.raises(ValueError, match="Import cycle detected"):
            resolve_imports(a, str(tmp_path), str(tmp_path))


# ---------------------------------------------------------------------------
# Diamond import deduplication
# ---------------------------------------------------------------------------


class TestDiamondImport:
    def test_diamond_import_deduplicated(self, tmp_path):
        d_path = tmp_path / "d.yaml"
        b_path = tmp_path / "b.yaml"
        c_path = tmp_path / "c.yaml"

        d = _make_blueprint(
            name="d",
            phases=[{"id": "d1", "prompt": "D"}],
        )
        b = _make_blueprint(
            name="b",
            imports=[{"path": "./d.yaml", "as": "d"}],
            phases=[{"id": "b1", "prompt": "B"}],
        )
        c = _make_blueprint(
            name="c",
            imports=[{"path": "./d.yaml", "as": "d"}],
            phases=[{"id": "c1", "prompt": "C"}],
        )
        a = _make_blueprint(
            name="a",
            imports=[
                {"path": "./b.yaml", "as": "b"},
                {"path": "./c.yaml", "as": "c"},
            ],
            phases=[{"id": "a1", "prompt": "A"}],
        )
        _write_yaml(str(d_path), d)
        _write_yaml(str(b_path), b)
        _write_yaml(str(c_path), c)

        resolved = resolve_imports(a, str(tmp_path), str(tmp_path))
        ids = [p["id"] for p in resolved["blueprint"]["phases"]]
        # d1 is imported by B and C both with prefix "d", then by A with prefixes "b" and "c"
        assert ids.count("b.d.d1") == 1
        assert ids.count("c.d.d1") == 1
        assert "b.d.d1" in ids
        assert "c.d.d1" in ids


# ---------------------------------------------------------------------------
# Version mismatch
# ---------------------------------------------------------------------------


class TestVersionMismatch:
    def test_version_mismatch_raises(self, tmp_path):
        child = {"version": "v2", "blueprint": {"name": "child", "phases": [{"id": "p1", "prompt": "hello"}]}}
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p2", "prompt": "world"}],
        )

        with pytest.raises(ValueError, match="Version mismatch"):
            resolve_imports(parent, str(tmp_path), str(tmp_path))


# ---------------------------------------------------------------------------
# Path traversal / sandboxing
# ---------------------------------------------------------------------------


class TestPathSandboxing:
    def test_absolute_path_rejected(self, tmp_path):
        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "/etc/passwd"}],
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        with pytest.raises(ValueError, match="Absolute import paths are not allowed"):
            resolve_imports(parent, str(tmp_path), str(tmp_path))

    def test_parent_traversal_rejected(self, tmp_path):
        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "../secrets.yaml"}],
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        with pytest.raises(ValueError, match="cannot traverse outside"):
            resolve_imports(parent, str(tmp_path), str(tmp_path))

    def test_missing_file_raises(self, tmp_path):
        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./missing.yaml"}],
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        with pytest.raises(FileNotFoundError, match="Imported blueprint not found"):
            resolve_imports(parent, str(tmp_path), str(tmp_path))

    def test_symlink_escape_rejected(self, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        real_file = real_dir / "target.yaml"
        _write_yaml(
            str(real_file),
            _make_blueprint(name="target", phases=[{"id": "t1", "prompt": "T"}]),
        )

        link_dir = tmp_path / "link"
        link_dir.mkdir()
        symlink = link_dir / "target.yaml"
        os.symlink(str(real_file), str(symlink))

        # real_file is outside link_dir, so resolving via symlink should escape
        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./target.yaml"}],
            phases=[{"id": "p1", "prompt": "hello"}],
        )
        with pytest.raises(ValueError, match="escapes the blueprint directory"):
            resolve_imports(parent, str(link_dir), str(link_dir))


# ---------------------------------------------------------------------------
# Validation integration
# ---------------------------------------------------------------------------


class TestValidationIntegration:
    def test_validate_resolves_imports(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[{"id": "c1", "prompt": "Child"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent_path = tmp_path / "parent.yaml"
        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p1", "prompt": "Parent", "depends_on": ["c.c1"]}],
        )
        _write_yaml(str(parent_path), parent)

        errors = validate_blueprint(str(parent_path))
        assert errors == []

    def test_validate_catches_bad_imported_dependency(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[{"id": "c1", "prompt": "Child"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent_path = tmp_path / "parent.yaml"
        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p1", "prompt": "Parent", "depends_on": ["c.nonexistent"]}],
        )
        _write_yaml(str(parent_path), parent)

        # V1Blueprint validation runs after merge, so dependency check should fail
        from goblin_cli.blueprint import V1Blueprint

        with pytest.raises(ValueError, match="dependency 'c.nonexistent' does not exist"):
            V1Blueprint(resolve_imports(parent, str(tmp_path), str(tmp_path))["blueprint"])


# ---------------------------------------------------------------------------
# Runner integration
# ---------------------------------------------------------------------------


class TestRunnerIntegration:
    def test_runner_loads_blueprint_with_imports(self, tmp_path):
        child = _make_blueprint(
            name="child",
            phases=[{"id": "c1", "prompt": "Child"}],
        )
        child_path = tmp_path / "child.yaml"
        _write_yaml(str(child_path), child)

        parent_path = tmp_path / "parent.yaml"
        parent = _make_blueprint(
            name="parent",
            imports=[{"path": "./child.yaml", "as": "c"}],
            phases=[{"id": "p1", "prompt": "Parent", "depends_on": ["c.c1"]}],
        )
        _write_yaml(str(parent_path), parent)

        runner = BlueprintRunner(str(parent_path))
        ids = [p.id for p in runner.blueprint.phases]
        assert ids == ["c.c1", "p1"]


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_empty_override_returns_base(self):
        assert _deep_merge({"a": 1}, {}) == {"a": 1}

    def test_empty_base_returns_override(self):
        assert _deep_merge({}, {"a": 1}) == {"a": 1}

    def test_nested_dict_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        assert _deep_merge(base, override) == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_list_replacement(self):
        base = {"x": [1, 2]}
        override = {"x": [3]}
        assert _deep_merge(base, override) == {"x": [3]}


class TestRewriteCondition:
    def test_simple_rewrite(self):
        assert _rewrite_condition("steps.foo.success", "pre", ["foo"]) == "steps.pre.foo.success"

    def test_no_rewrite_for_unknown_id(self):
        assert _rewrite_condition("steps.bar.success", "pre", ["foo"]) == "steps.bar.success"

    def test_longest_match_wins(self):
        cond = "steps.a.success and steps.ab.success"
        result = _rewrite_condition(cond, "pre", ["a", "ab"])
        assert "steps.pre.ab.success" in result
        assert "steps.pre.a.success" in result

    def test_does_not_rewrite_inside_word(self):
        cond = "steps.foobar.success"
        result = _rewrite_condition(cond, "pre", ["foo", "foobar"])
        assert result == "steps.pre.foobar.success"
