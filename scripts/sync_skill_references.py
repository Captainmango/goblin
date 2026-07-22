#!/usr/bin/env python3
"""Sync skill reference material from the repo docs and schema.

Copies docs/ (excluding development/ and api/, which are irrelevant to
blueprint authoring) and spec/v1.json into the references/ directory of every
skill under skills/ that mentions ``references/`` in its SKILL.md.

Skills installed with ``npx skills add`` only receive their own directory, so
each skill that needs the docs must bundle its own copy. This script keeps
those copies in sync from a single source — opt a skill in by pointing its
SKILL.md at ``references/``.

Run from the repo root via:

    task skills:sync-references

Run this after changing the Goblin docs or schema so skills stay current.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
SPEC_FILE = REPO_ROOT / "spec" / "v1.json"
SKILLS_DIR = REPO_ROOT / "skills"

# Top-level docs sections excluded from the snapshot.
EXCLUDED_DOCS = ("development", "api")


def find_consumer_skills() -> list[Path]:
    """Return skill dirs whose SKILL.md points at a bundled references/ snapshot."""
    consumers = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        if "references/" in skill_md.read_text(encoding="utf-8"):
            consumers.append(skill_md.parent)
    return consumers


def sync_references(skill_dir: Path) -> int:
    """Replace a skill's references/ snapshot; return the number of files synced."""
    ref_dir = skill_dir / "references"
    if ref_dir.exists():
        shutil.rmtree(ref_dir)
    (ref_dir / "docs").mkdir(parents=True)
    (ref_dir / "spec").mkdir()

    shutil.copytree(
        DOCS_DIR,
        ref_dir / "docs",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*EXCLUDED_DOCS),
    )
    shutil.copy2(SPEC_FILE, ref_dir / "spec" / SPEC_FILE.name)

    return sum(1 for path in ref_dir.rglob("*") if path.is_file())


def main() -> int:
    if not DOCS_DIR.is_dir():
        print(f"FAIL: docs directory not found at {DOCS_DIR}")
        return 1
    if not SPEC_FILE.is_file():
        print(f"FAIL: schema not found at {SPEC_FILE}")
        return 1

    consumers = find_consumer_skills()
    if not consumers:
        print("No skills reference the docs snapshot; nothing to do.")
        return 0

    for skill_dir in consumers:
        file_count = sync_references(skill_dir)
        print(f"OK: synced {file_count} file(s) into {skill_dir / 'references'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
