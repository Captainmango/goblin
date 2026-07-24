"""Tests for scripts/patch_litellm_providers.py.

These tests exercise the LiteLLM build-time patcher by invoking it in a
subprocess and checking the patched package can still be imported.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "patch_litellm_providers.py"
WHITELIST_FILE = REPO_ROOT / "goblin" / "litellm_providers.txt"


@pytest.fixture(scope="module")
def patched_litellm(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Run the patch script once and return the patched package dir and flags file."""
    tmp_path = tmp_path_factory.mktemp("litellm-patch")
    output_dir = tmp_path / "patched"
    flags_file = tmp_path / "nofollow.flags"
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--providers-file",
        str(WHITELIST_FILE),
        "--output-dir",
        str(output_dir),
        "--flags-file",
        str(flags_file),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (output_dir / "litellm" / "__init__.py").exists()
    assert flags_file.exists()
    return output_dir / "litellm", flags_file


def test_patched_litellm_imports(patched_litellm: tuple[Path, Path]) -> None:
    """The patched package imports successfully and uses the patched copy."""
    litellm_dir, _ = patched_litellm
    env = os.environ.copy()
    env["PYTHONPATH"] = str(litellm_dir.parent)
    result = subprocess.run(
        [sys.executable, "-c", "import litellm; print(litellm.__file__)"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert str(litellm_dir / "__init__.py") in result.stdout


def test_whitelisted_provider_preserved(patched_litellm: tuple[Path, Path]) -> None:
    """A whitelisted provider still contains real implementation code."""
    litellm_dir, _ = patched_litellm
    anthropic_init = litellm_dir / "llms" / "anthropic" / "__init__.py"
    assert anthropic_init.exists()
    text = anthropic_init.read_text(encoding="utf-8")
    assert "Stub module" not in text


def test_excluded_provider_is_stubbed(patched_litellm: tuple[Path, Path]) -> None:
    """An excluded provider is replaced with the stub module."""
    litellm_dir, _ = patched_litellm
    ai21_init = litellm_dir / "llms" / "ai21" / "__init__.py"
    assert ai21_init.exists()
    assert "Stub module" in ai21_init.read_text(encoding="utf-8")


def test_nofollow_flags_generated(patched_litellm: tuple[Path, Path]) -> None:
    """The flags file contains at least one --nofollow-import-to entry."""
    _, flags_file = patched_litellm
    lines = flags_file.read_text(encoding="utf-8").strip().splitlines()
    assert any(line.startswith("--nofollow-import-to=litellm.llms.") for line in lines)


def test_env_var_overrides_whitelist(tmp_path: Path) -> None:
    """LITELLM_PROVIDERS environment variable overrides the whitelist file."""
    output_dir = tmp_path / "patched"
    flags_file = tmp_path / "nofollow.flags"
    env = os.environ.copy()
    env["LITELLM_PROVIDERS"] = "openai,anthropic"
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--providers-file",
        str(WHITELIST_FILE),
        "--output-dir",
        str(output_dir),
        "--flags-file",
        str(flags_file),
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "Whitelisted providers: anthropic, openai" in result.stdout


def test_parse_providers_helper() -> None:
    """The helper strips comments and whitespace and supports commas."""
    # Import the helper directly by loading the script as a module.
    spec = importlib.util.spec_from_file_location("patch_litellm_providers", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    providers = module._parse_providers("openai\n# comment\nanthropic, azure\n")
    assert providers == ["openai", "anthropic", "azure"]


def test_whitelisted_provider_entry_points(patched_litellm: tuple[Path, Path]) -> None:
    """Core litellm entry points resolve for whitelisted providers."""
    litellm_dir, _ = patched_litellm
    env = os.environ.copy()
    env["PYTHONPATH"] = str(litellm_dir.parent)
    code = """
import litellm
from litellm.utils import get_llm_provider

print("completion:", callable(litellm.completion))
print("acompletion:", callable(litellm.acompletion))
model, provider, _, _ = get_llm_provider("openai/gpt-4")
print(f"resolved: {model} @ {provider}")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "resolved: gpt-4 @ openai" in result.stdout
