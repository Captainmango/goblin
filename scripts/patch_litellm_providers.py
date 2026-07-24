"""Build-time patcher that shrinks the LiteLLM footprint for Nuitka.

LiteLLM ships ~140 providers under ``litellm/llms/``. Nuitka follows every
static import and compiles the transitive closure of each one, which makes the
native build slow and prone to hanging in clang on a few very large provider
trees.

This script copies the installed ``litellm`` package into a temporary directory
and replaces every non-whitelisted provider under ``litellm/llms/`` with a tiny
stub package. The stub satisfies the ``from litellm.llms.X.Y.Z import Name``
imports that remain in ``litellm/__init__.py`` and elsewhere, while preventing
Nuitka from descending into the full provider subtree.

Usage::

    python scripts/patch_litellm_providers.py \
        --providers-file goblin/litellm_providers.txt \
        --output-dir /tmp/litellm-patched \
        --flags-file /tmp/litellm-nofollow.flags

The provider list can also be supplied via the ``LITELLM_PROVIDERS`` environment
variable as a comma-separated list; it overrides the file.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import shutil
import sys
from pathlib import Path

# Providers that are part of LiteLLM's own core machinery and must never be
# stubbed, even if they are not in the user whitelist.
_CORE_PROVIDERS: frozenset[str] = frozenset(
    {
        "base_llm",
        "custom_httpx",
        "deprecated_providers",
    }
)

# LiteLLM version the patcher is known to work with. The patching strategy
# relies on the package layout under litellm/llms/, which has been stable but
# may change in future releases.
_SUPPORTED_LITELLM_VERSION_SPEC: str = ">=1.92.0,<1.93"


_STUB_MODULE = '''\
from __future__ import annotations

"""Stub module for a LiteLLM provider excluded from the native build.

All attribute access returns a placeholder type so that imports such as
``from litellm.llms.<provider>.chat.transformation import SomeConfig`` succeed
without pulling the real provider code into the binary. Using an excluded
provider at runtime raises a clear error.
"""

__all__: list[str] = []


import abc


class _ExcludedProviderType(abc.ABCMeta):
    """Metaclass that produces placeholder types for excluded providers.

    Inheriting from ``abc.ABCMeta`` lets stubbed providers act as base classes
    alongside ``abc.ABC`` subclasses used by LiteLLM subsystems such as
    ``litellm.rag`` without triggering a metaclass conflict.
    """

    def __getattr__(cls, name: str) -> type:
        return _make_excluded_type(f"{cls.__name__}.{name}")

    def __call__(cls, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Distinguish class creation (metaclass invocation with name, bases,
        # namespace) from normal instantiation. Stubbed providers are used as
        # base classes by other LiteLLM subsystems (e.g. litellm.rag), and
        # their metaclass must create real classes in that case. For normal
        # instantiation we return an inert instance so the excluded provider
        # cannot be used at runtime.
        if (
            len(args) == 3
            and isinstance(args[0], str)
            and isinstance(args[1], tuple)
            and isinstance(args[2], dict)
        ):
            return super().__call__(*args, **kwargs)
        return _ExcludedProviderInstance(cls.__name__)

    def __bool__(cls) -> bool:
        return False


class _ExcludedProviderInstance:
    """Placeholder instance for an excluded provider."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, name: str) -> type:
        return _make_excluded_type(f"{self._name}.{name}")

    def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            f"LiteLLM provider '{self._name}' was excluded from this native build. "
            f"Add it to the LITELLM_PROVIDERS whitelist and rebuild if you need it."
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"<ExcludedProvider {self._name}>"


def _make_excluded_type(name: str) -> type:
    return _ExcludedProviderType(name, (), {})


def __getattr__(name: str) -> type:
    return _make_excluded_type(name)
'''


def _find_litellm_package() -> Path:
    """Return the directory containing the installed ``litellm`` package."""
    spec = importlib.util.find_spec("litellm")
    if spec is None or spec.origin is None:
        raise RuntimeError("litellm is not installed or cannot be located")
    return Path(spec.origin).parent


def _check_litellm_version() -> None:
    """Warn if the installed litellm version is outside the supported range."""
    version = importlib.metadata.version("litellm")
    parts = version.split(".")
    if len(parts) < 2:
        raise RuntimeError(f"Cannot parse litellm version: {version}")
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except ValueError as exc:
        raise RuntimeError(f"Cannot parse litellm version: {version}") from exc
    if not (major == 1 and 92 <= minor < 93):
        raise RuntimeError(
            f"litellm {version} is not supported by this patch script. "
            f"Expected {_SUPPORTED_LITELLM_VERSION_SPEC}. "
            "Update the script or pin litellm to a supported version."
        )


def _parse_providers(raw: str) -> list[str]:
    """Parse a comma/newline/whitespace separated provider list.

    Empty lines and ``#`` comments are ignored.
    """
    providers: list[str] = []
    for line in raw.splitlines():
        line = line.split("#", 1)[0]
        for item in line.split(","):
            stripped = item.strip()
            if stripped:
                providers.append(stripped)
    return providers


def _read_providers_file(path: Path) -> list[str]:
    return _parse_providers(path.read_text(encoding="utf-8"))


def _provider_directories(llms_dir: Path) -> list[str]:
    """List immediate provider package names under ``litellm/llms/``."""
    return sorted(entry.name for entry in llms_dir.iterdir() if entry.is_dir() and entry.name != "__pycache__")


def _copy_litellm(source: Path, destination: Path) -> Path:
    """Copy the installed ``litellm`` package into ``destination/litellm``."""
    target = destination / "litellm"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=False)
    return target


def _write_stub(provider_dir: Path) -> None:
    """Replace every Python file under a provider tree with a stub module.

    The directory structure is preserved so that deep imports such as
    ``litellm.llms.tencent.cost_calculator`` still resolve, but each module
    only exports placeholder types.
    """
    root_init = provider_dir / "__init__.py"
    if not root_init.exists():
        root_init.write_text(_STUB_MODULE, encoding="utf-8")
    for path in list(provider_dir.rglob("*")):
        if path.is_file() and path.suffix == ".py":
            path.write_text(_STUB_MODULE, encoding="utf-8")
        elif path.is_file():
            # Drop non-Python provider assets; they are not needed by core litellm.
            path.unlink()
        elif path.is_dir():
            init_file = path / "__init__.py"
            if not init_file.exists():
                init_file.write_text(_STUB_MODULE, encoding="utf-8")


def _patch_litellm(litellm_dir: Path, whitelist: set[str]) -> list[str]:
    """Stub every provider under ``litellm/llms/`` not in *whitelist*.

    Returns the list of provider names that were stubbed.
    """
    llms_dir = litellm_dir / "llms"
    if not llms_dir.is_dir():
        raise RuntimeError(f"Expected litellm/llms/ directory at {llms_dir}")

    stubbed: list[str] = []
    for name in _provider_directories(llms_dir):
        if name in whitelist or name in _CORE_PROVIDERS:
            continue
        _write_stub(llms_dir / name)
        stubbed.append(name)
    return sorted(stubbed)


def _write_flags_file(path: Path, stubbed: list[str]) -> None:
    """Write ``--nofollow-import-to=...`` flags, one per line."""
    lines = [f"--nofollow-import-to=litellm.llms.{name}" for name in stubbed]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Patch LiteLLM to include only whitelisted providers for Nuitka.")
    parser.add_argument(
        "--providers-file",
        type=Path,
        help="Path to a file listing whitelisted providers (one per line, comments allowed).",
    )
    parser.add_argument(
        "--providers",
        help="Comma-separated whitelist of providers; overrides --providers-file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write the patched litellm package into.",
    )
    parser.add_argument(
        "--flags-file",
        type=Path,
        required=True,
        help="Path to write the generated --nofollow-import-to flags into.",
    )
    args = parser.parse_args(argv)

    env_providers = os.getenv("LITELLM_PROVIDERS")
    if args.providers:
        whitelist = _parse_providers(args.providers)
    elif env_providers:
        whitelist = _parse_providers(env_providers)
    elif args.providers_file:
        whitelist = _read_providers_file(args.providers_file)
    else:
        parser.error("Specify a provider whitelist via --providers, LITELLM_PROVIDERS, or --providers-file.")

    whitelist_set = set(whitelist) | _CORE_PROVIDERS

    _check_litellm_version()
    source = _find_litellm_package()
    patched = _copy_litellm(source, args.output_dir)
    stubbed = _patch_litellm(patched, whitelist_set)
    _write_flags_file(args.flags_file, stubbed)

    print(f"Patched litellm: {patched}")
    print(f"Whitelisted providers: {', '.join(sorted(whitelist))}")
    print(f"Stubbed providers: {len(stubbed)}")
    print(f"Wrote Nuitka flags: {args.flags_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
