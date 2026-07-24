# Optimization Plan: Provider-Targeted LiteLLM Compilation

## Problem

The Nuitka standalone build of `goblin` currently generates **~8,900 C source files** and the compilation hangs near the end with a single `clang` process pinned at 100% CPU. Profiling shows the bottleneck is not the linker (`mold`/`lld` are correctly configured) but the C compilation of one or more very large generated translation units.

## Root Cause

`openhands-sdk` depends on `litellm` for LLM abstraction. LiteLLM ships with **~140 providers** under `litellm/llms/` (134 subdirectories in the installed package) and an `LlmProviders` enum with a matching entry for each. Although many providers are lazy-loaded at runtime, the top-level `litellm/__init__.py` and `litellm/main.py` still perform static imports for dozens of provider configs and handlers, e.g.:

```python
from .llms.anthropic.chat.transformation import AnthropicConfig
from .llms.bedrock.chat import BedrockConverseLLM
from .llms.openai.openai import OpenAIChatCompletion
...
```

Nuitka follows every static import, so the build includes all of those provider trees plus their transitive dependencies (Pydantic models, HTTP clients, authentication helpers, etc.). Most of these providers are never used by `goblin` or `openhands-sdk` at runtime, but they still generate C code, object files, and link-time weight.

The "hang at the end" is consistent with `clang` optimizing a monolithic generated file for a heavily imported provider subtree (e.g. `bedrock`, `vertex_ai`, or `azure`, which have deep module trees and large Pydantic schemas).

## Goal

Allow the Nuitka build to target a **whitelist of LiteLLM providers** so that only the providers actually needed at runtime are compiled and linked. This should:

- Reduce the generated C file count from ~8,900 to a much smaller number.
- Shorten the final single-core compile/link phases.
- Keep the binary functional for the supported providers.

## Proposed Solution

### 1. Identify the Minimal Provider Set

Audit `openhands-sdk` and `goblin` to determine which LiteLLM providers are required. From the current import graph, the core surface area is:

- `litellm` top-level module (`completion`, `acompletion`, `model_cost`, `provider_list`, `get_llm_provider`, etc.)
- `litellm.types.utils`, `litellm.types.llms.openai`, `litellm.types.responses.main`
- `litellm.exceptions`
- `litellm.cost_calculator`
- `litellm.responses.main`, `litellm.responses.streaming_iterator`
- `litellm.utils`
- `litellm.llms.base_llm`, `litellm.llms.custom_httpx`, `litellm.llms.custom_llm`

The actual provider handlers/configs that `openhands-sdk` references statically include at least:

- `openai`
- `anthropic`
- `bedrock`
- `azure` / `azure_ai`
- `vertex_ai`
- `gemini`
- `groq`
- `cohere`
- `openai_like`
- `huggingface`
- `together_ai`, `fireworks_ai`, `mistral`, `deepseek`, etc. (depending on supported model configs)

A reasonable default whitelist for a generic `goblin` binary is:

```text
openai, anthropic, azure, azure_ai, bedrock, gemini, vertex_ai, groq, cohere,
openai_like, together_ai, fireworks_ai, mistral, deepseek, openrouter
```

Users who need niche providers can override the whitelist at build time.

### 2. Patch LiteLLM at Build Time

The main blocker is that `litellm/__init__.py` and `litellm/main.py` statically import provider classes. Nuitka’s `--nofollow-import-to` cannot remove top-level imports without breaking `import litellm` at runtime.

The cleanest approach is a **build-time source patch** that runs before Nuitka:

- Create `scripts/patch_litellm_providers.py`.
- Accept a provider whitelist (e.g. env var `LITELLM_PROVIDERS=openai,anthropic,azure`).
- Locate the installed `litellm` package in the uv venv.
- Rewrite `litellm/__init__.py` and `litellm/main.py` to:
  - Keep imports for whitelisted providers.
  - Remove or stub out imports for non-whitelisted providers.
  - Preserve core types, utils, exceptions, cost calculation, and response helpers.
- Do **not** modify files in-place; write patched copies to a temporary directory and point Nuitka at it via `PYTHONPATH` or `--include-plugin-directory`, or patch a venv copy that is restored after the build.

A less invasive fallback is to provide **stub modules** for excluded providers so that `import litellm` succeeds but the excluded provider code is essentially empty. Nuitka will compile the stubs instead of full provider trees.

### 3. Pass Provider Whitelist to Nuitka

After patching, add Nuitka flags to explicitly exclude any remaining provider subtrees that are still importable but unused. The existing `NUITKA_EXTRA_FLAGS` hook in `goblin/Taskfile.yml` can be used for experimentation:

```sh
NUITKA_EXTRA_FLAGS="--nofollow-import-to=litellm.llms.ai21 --nofollow-import-to=litellm.llms.replicate ..." \
  task goblin:build-binary
```

Long-term, the patch script should generate the correct `--nofollow-import-to` list automatically.

### 4. Configuration Mechanism

Introduce a build configuration file `litellm_providers.txt` or a Taskfile var:

```yaml
# goblin/Taskfile.yml
vars:
  LITELLM_PROVIDERS: openai,anthropic,azure,azure_ai,bedrock,gemini,vertex_ai,groq
```

Or read from an env var:

```sh
LITELLM_PROVIDERS=openai,anthropic task goblin:build-binary
```

### 5. Update `setup:build-tools` / `build-binary`

Add a pre-build step to `build-binary`:

```sh
# before uv run python -m nuitka
uv run python ../scripts/patch_litellm_providers.py --providers "$LITELLM_PROVIDERS"
```

The script should:

1. Parse the whitelist.
2. Patch `litellm/__init__.py` and `litellm/main.py`.
3. Generate a list of excluded `litellm.llms.*` packages.
4. Export the exclusions in a form the build task can consume (e.g. write to a temp file that `build-binary` reads into `NUITKA_EXTRA_FLAGS`).

## Implementation Steps

1. **Audit provider usage**
   - Run `python -m nuitka --show-modules ...` or inspect the import graph to confirm which `litellm.llms.*` modules are reached from `goblin/__main__.py`.
   - Cross-reference with the `LlmProviders` enum in `litellm/types/utils.py`.

2. **Build the patch script**
   - `scripts/patch_litellm_providers.py`
   - Use AST parsing or line-based filtering to remove non-whitelisted provider imports from `litellm/__init__.py` and `litellm/main.py`.
   - Keep fallback stubs minimal to avoid `AttributeError` when code references an excluded provider class.

3. **Add a default whitelist**
   - Store in `goblin/litellm_providers.txt` or as a Taskfile var.
   - Document how users can override it.

4. **Wire into `build-binary`**
   - Call the patch script before Nuitka.
   - Feed generated `--nofollow-import-to` flags into `NUITKA_EXTRA_FLAGS`.

5. **Validate**
   - Run a full build and compare generated C file count.
   - Test runtime functionality for each whitelisted provider (e.g. `goblin validate`, a small LLM call smoke test).
   - Ensure excluded providers raise a clear runtime error or are simply unavailable.

## Risks and Mitigation

| Risk | Mitigation |
|---|---|
| Patching litellm breaks on version updates | Pin litellm version in `goblin/pyproject.toml` and add a version check to the patch script. |
| openhands-sdk uses a provider not in the whitelist | Start with a broad whitelist; run runtime tests before shrinking it. |
| Stubbed provider classes cause `AttributeError` at runtime | Provide minimal stub classes with the same names for excluded providers, or patch call sites to fail gracefully. |
| Static imports in litellm are hard to parse safely | Use AST rewriting rather than regex; fall back to no-op if parsing fails. |
| Build becomes non-deterministic | Patch into a temp directory; never modify the installed package in `.venv` in place. |

## Expected Outcome

- Generated C file count should drop from ~8,900 to roughly **1,000–3,000**, depending on the whitelist.
- The final single-core `clang` compile/link phase should no longer appear to hang.
- Build time should decrease proportionally, especially on clean builds where `ccache` is cold.

## Alternative Approaches (Not Recommended)

- **Use `--nofollow-import-to=litellm.llms.*` without patching**: Will break `import litellm` because of static top-level provider imports.
- **Fork litellm**: Heavy maintenance burden; a build-time patch is easier to keep in sync.
- **Compile as `--module` instead of `--standalone`**: Avoids the problem entirely but changes the deliverable from a self-contained binary to a Python extension module.
