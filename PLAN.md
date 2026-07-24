# PLAN — Compiling Goblin with Nuitka

## Goal

Use [Nuitka](https://nuitka.net) to compile the `goblin` CLI into a native binary
distribution, so the harness can be deployed and run on machines that do not have
Python 3.13, uv, or the workspace source checkout installed.

## 1. Nuitka research summary

Nuitka is a Python-to-C compiler written in Python. It translates Python modules into
a C-level program that links against `libpython`, producing executables or extension
modules that are drop-in replacements for the interpreted program. Key facts from the
user manual (https://nuitka.net/doc/user-manual.html):

- **Compatibility**: supports CPython 3.4–3.14 (we are on 3.13.14 — fully supported).
  Must be CPython (we use it via uv — fine).
- **C compiler requirement**: on Linux, gcc ≥ 5.1 (we have gcc 11.4 — fine) or clang.
  Architecture x86_64 is fully supported.
- **Modes** relevant to us:
  - `--mode=standalone` — produces a `<name>.dist/` folder containing the binary,
    an embedded copy of the Python runtime, and all dependent modules/extensions.
    The folder can be copied to another machine and run as-is. **This is the
    recommended first target.**
  - `--mode=onefile` — packs the standalone folder into a single self-extracting
    executable. Build on top of standalone only after standalone works; issues like
    missing data files are much easier to diagnose in standalone mode.
  - (`--mode=module` / `--mode=package` produce extension modules — not relevant for
    a CLI application.)
- **Import following**: in standalone mode, imports are followed recursively by
  static analysis. Function-level and conditional imports *are* seen by the
  analyser. Imports that only exist at runtime (e.g. `importlib.import_module(name)`
  with a computed name) are **not** seen and must be forced with
  `--include-module` / `--include-package`.
- **Data files**: non-code files are never included automatically. Options:
  `--include-package-data=<pkg>` (auto-detect data inside a package),
  `--include-data-files=<src>=<dst>`, `--include-data-dir=<src>=<dst>`.
  `importlib.resources` access works in compiled binaries provided the data files
  are included.
- **Onefile runtime**: the binary self-extracts to a temp dir on every run
  (default `--onefile-tempdir-spec="{TEMP}/onefile_{PID}_{TIME}"`). A cached spec
  such as `{CACHE_DIR}/{PRODUCT}/{VERSION}` avoids repeated unpacking.
- **Not a cross-compiler**: binaries must be built per OS/arch. On Linux, the binary
  links against the build machine's glibc — build on the *oldest* glibc you intend
  to support (e.g. in a container) for portability.
- **Tooling extras**: Nuitka downloads a C cache tool on first run
  (`--assume-yes-for-downloads` makes this non-interactive); `zstandard` is used for
  onefile compression; a GitHub Action (`Nuitka/Nuitka-Action`) exists for CI builds.
- **License**: Nuitka is AGPL-3.0 but with a runtime exception
  (`LICENSE-RUNTIME.txt`); compiled output is not tainted. No licensing obstacle for
  distributing goblin binaries.

## 2. Goblin analysis (what Nuitka will see)

Entry point and dependency closure:

- Console script: `goblin = "goblin.cli:cli"` (Click group). `cli.py` lazy-imports
  `goblin.runner` and `goblin.notifications.cli` **inside functions** — statically
  visible to Nuitka, so they will be followed correctly. (This lazy-loading also
  exists to keep `goblin.cli` import time < 500 ms; see `scripts/check_import_time.py`.
  The discipline should be preserved — it also keeps the compiled binary's startup fast.)
- Workspace dependencies `goblin-common` and `notifications-relay` are regular
  installed packages in the shared `.venv`; Nuitka will compile them into the
  distribution automatically. Note: `notifications-relay` development is frozen
  (AGENTS.md) — compilation does not read or modify its source beyond normal import
  analysis, so this is compatible with the freeze.
- Heavy third-party deps: `openhands-sdk` / `openhands-tools` (~=1.29.3), `pydantic`,
  `jinja2`, `jsonschema`, `pyyaml`, `click`, `pyturso`, `json-repair`, `filelock`.

Identified compilation concerns, in rough order of risk:

| # | Concern | Where | Mitigation |
|---|---------|-------|------------|
| 1 | JSON schema data file loaded via `importlib.resources.files("goblin").joinpath("spec/v1.json")` | `goblin/validation.py` | Add `--include-package-data=goblin` (or explicit `--include-data-files`). Verify with `goblin validate`. |
| 2 | Runtime-dynamic import of user-supplied dotted model paths: `importlib.import_module(module_name)` | `goblin/output_schema.py` (`_resolve_dotted_or_registered`) | Names come from blueprint YAML at runtime — invisible to static analysis. In practice these resolve to registered models or stdlib/site-packages modules. Document the limitation; provide `--include-module` escape hatch in the build task for known external model modules. |
| 3 | `pyturso` ships a native extension (.so) | dependency | Standalone mode copies extension modules automatically; verify Turso backend works from the binary. |
| 4 | `openhands-sdk` / `openhands-tools` size and potential plugin/data-file behaviour | dependencies | Largest unknown. Nuitka ships package configuration for many popular packages, but openhands coverage is unverified. Mitigate empirically: compile, then smoke-test an LLM blueprint and an MCP blueprint. |
| 5 | External processes spawned at runtime: MCP stdio servers (`npx`/`uvx`/node), `git` (`tools/worktree_tool.py`), terminal tool | `blueprint.py`, `swarm.py`, `tools/` | Unaffected by compilation, but the **target machine still needs these tools on PATH**. Document as runtime requirements of the binary. |
| 6 | CWD-relative runtime file behaviour: `.env` loading, `logs/*.jsonl`, `.goblin/` checkpoints | `cli.py`, config | All relative to the *invocation* directory, not the executable — works unchanged in standalone/onefile. No change needed. |
| 7 | Binary size / build time | — | Standalone folder will be large (embedded runtime + SDK). Use ccache (Nuitka auto-downloads), `--jobs=$(nproc)`, and exclude test packages (`--nofollow-import-to='*.tests'`). Measure and iterate. |
| 8 | glibc portability | — | Build inside the project's Docker base image (`infra/docker/base`) or another oldest-supported-distro container if distributing beyond the dev machine. |

## 3. Recommended approach

1. Compile the **whole goblin program** in **standalone mode first**, then offer
   **onefile** as the distributable artifact once standalone is verified.
2. Drive Nuitka through the existing uv workspace venv (`uv run python -m nuitka`)
   so the exact locked dependency versions are what gets compiled.
3. Keep the build reproducible via a Taskfile task (`task goblin:build-binary`) with
   all Nuitka options stored in the repo (Taskfile vars or `nuitka-project` comments
   in the entry shim), not passed ad hoc on the command line.

### 3.1 Source change required (one file, three lines)

Add `goblin/goblin/__main__.py` as a clean compilation entry point (this also gives
`python -m goblin` support for free):

```python
from __future__ import annotations

from goblin.cli import cli

if __name__ == "__main__":
    cli()
```

### 3.2 Dependencies

Add to the goblin project's dev dependency group (do **not** add to runtime deps —
Nuitka is build-time only):

```sh
uv add --group dev nuitka zstandard --project goblin
```

(`zstandard` enables onefile compression and avoids a first-run download prompt.)

### 3.3 Build command (standalone — first milestone)

```sh
uv run python -m nuitka \
    --mode=standalone \
    --main=goblin/goblin/__main__.py \
    --output-dir=dist/nuitka \
    --output-filename=goblin \
    --include-package=goblin \
    --include-package-data=goblin \
    --nofollow-import-to='*.tests' \
    --assume-yes-for-downloads \
    --remove-output \
    --jobs=$(nproc) \
    --show-progress
```

Notes on the flags:

- `--include-package=goblin` guarantees the full package (including lazily imported
  modules) is compiled in, independent of any analysis gap.
- `--include-package-data=goblin` pulls in `goblin/spec/v1.json` (concern #1).
- `--nofollow-import-to='*.tests'` keeps test trees of dependencies out of the binary.
- `--assume-yes-for-downloads` lets Nuitka fetch its ccache tool non-interactively.
- The workspace deps (`goblin-common`, `notifications-relay`) are followed
  automatically because goblin imports them.

### 3.4 Onefile command (second milestone)

Same as above, replacing the mode and adding a cached unpack location:

```sh
uv run python -m nuitka \
    --mode=onefile \
    --main=goblin/goblin/__main__.py \
    --output-dir=dist/nuitka \
    --output-filename=goblin \
    --include-package=goblin \
    --include-package-data=goblin \
    --nofollow-import-to='*.tests' \
    --assume-yes-for-downloads \
    --remove-output \
    --jobs=$(nproc) \
    --onefile-tempdir-spec="{CACHE_DIR}/goblin/{VERSION}"
```

### 3.5 Taskfile integration

Add to `goblin/Taskfile.yml` (and optionally surface at the root):

```yaml
  build-binary:
    desc: Compile the goblin CLI to a native binary with Nuitka (standalone)
    cmds:
      - uv run python -m nuitka --mode=standalone --main=goblin/__main__.py --output-dir=../dist/nuitka --output-filename=goblin --include-package=goblin --include-package-data=goblin --nofollow-import-to='*.tests' --assume-yes-for-downloads --remove-output --show-progress
    silent: true

  build-onefile:
    desc: Compile the goblin CLI to a single-file native binary with Nuitka
    cmds:
      - uv run python -m nuitka --mode=onefile --main=goblin/__main__.py --output-dir=../dist/nuitka --output-filename=goblin --include-package=goblin --include-package-data=goblin --nofollow-import-to='*.tests' --assume-yes-for-downloads --remove-output --show-progress --onefile-tempdir-spec="{CACHE_DIR}/goblin/{VERSION}"
    silent: true
```

## 4. Verification strategy

The existing pytest suites keep running against **source** (unchanged workflows).
The compiled binary gets its own smoke-test ladder, run manually at first:

1. **Help/startup**: `dist/nuitka/goblin.dist/goblin --help` matches
   `uv run goblin --help` output; binary starts fast (no extraction delay in
   standalone).
2. **Schema data file (concern #1)**: run the binary's `validate` against every
   example blueprint — equivalent of `task goblin:validate`:
   `dist/nuitka/goblin.dist/goblin validate examples/*.yaml` must report all valid.
3. **Dotted output-model resolution (concern #2)**: run a blueprint that uses a
   registered output model, and one that uses a dotted path into a module we
   explicitly include; confirm both resolve.
4. **Notifications subcommand**: `goblin notifications --help` loads the lazily
   imported `goblin.notifications.cli` correctly from the binary.
5. **Turso backend (concern #3)**: run a swarm blueprint against a local libSQL
   file using the binary.
6. **LLM end-to-end (concern #4)**: run a minimal real blueprint with
   `GOBLIN_MODEL_API_KEY` set; confirm an agent step completes and checkpoint files
   appear under `.goblin/`.
7. **MCP end-to-end (concern #5)**: run a blueprint with a stdio MCP server (e.g. an
   `npx`-based one) on a machine with node installed; confirm tool calls work.
8. **Clean-machine test**: copy the `.dist` folder (or onefile binary) to a machine/
   container without Python/uv and repeat steps 1–2, plus 6 if credentials allow.

Only after 1–8 pass on standalone, repeat 1–2 and 6 with the onefile artifact.

## 5. Rollout phases

- [ ] **Phase 1 — Prep**: add `nuitka` + `zstandard` to goblin's dev group; add
  `goblin/goblin/__main__.py`; confirm `uv run python -m nuitka --version`.
- [ ] **Phase 2 — Standalone bring-up**: wire `task goblin:build-binary`; run it;
  fix fallout (expected: data-file inclusion; possible openhands package-config
  gaps — check Nuitka's compilation report, `--report=compilation-report.xml`).
- [ ] **Phase 3 — Smoke ladder**: execute verification steps 1–8; file fixes for any
  dynamic-import misses with targeted `--include-module` flags in the task.
- [ ] **Phase 4 — Onefile**: wire `task goblin:build-onefile`; re-run smoke ladder
  subset; decide which artifact is the canonical distribution.
- [ ] **Phase 5 — Portable builds (optional)**: build inside `infra/docker/base` for
  older-glibc portability; consider `Nuitka/Nuitka-Action` in CI for
  Linux/macOS/Windows matrix builds.
- [ ] **Phase 6 — Docs**: update `AGENTS.md` (new tasks, dev-only Nuitka dep) and
  goblin `README.md` (binary usage, runtime requirements: git/node for tools/MCP,
  env vars still required, `--include-module` escape hatch for external output
  models).

## 6. Explicitly out of scope

- Compiling `notifications-relay` as a service (it is a frozen FastAPI app; only its
  client-facing modules ride along inside the goblin binary as a dependency).
- Changing any goblin runtime behaviour beyond the `__main__.py` addition.
- Windows/macOS artifacts in the first iteration (Nuitka supports them; revisit once
  Linux standalone/onefile is proven).
- Replacing the Docker base image distribution path — Nuitka artifacts complement,
  not replace, existing packaging.
