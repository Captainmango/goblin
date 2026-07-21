#!/usr/bin/env python3
"""Import-time regression guard for goblin.cli.

Fails if importing goblin.cli takes longer than the configured threshold
(500 ms by default). Run from the repo root via:

    task goblin:check-imports

"""

import subprocess
import sys

THRESHOLD_MS = 500


def main() -> int:
    cmd = [sys.executable, "-X", "importtime", "-c", "from goblin.cli import cli"]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    # importtime writes to stderr
    lines = proc.stderr.splitlines()

    cli_time_ms = None
    for line in lines:
        # Format: "import time:      self |   cumulative | package"
        # The package column is the last field and cumulative time is in microseconds.
        if line.strip().endswith("goblin.cli"):
            parts = line.split("|")
            if len(parts) >= 2:
                try:
                    cli_time_us = int(parts[1].strip())
                    cli_time_ms = cli_time_us / 1000
                except ValueError:
                    pass

    if cli_time_ms is None:
        print("ERROR: Could not determine goblin.cli import time from trace.")
        return 1

    if cli_time_ms > THRESHOLD_MS:
        print(f"FAIL: goblin.cli import time is {cli_time_ms} ms, exceeds threshold of {THRESHOLD_MS} ms.")
        return 1

    print(f"OK: goblin.cli import time is {cli_time_ms} ms (threshold: {THRESHOLD_MS} ms).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
