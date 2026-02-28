#!/usr/bin/env bash
set -euo pipefail

MIN_MAJOR=3
MIN_MINOR=11

error() { printf "\033[31merror:\033[0m %s\n" "$1" >&2; exit 1; }
info()  { printf "\033[34m==>\033[0m %s\n" "$1"; }

# --- Check python3 is installed ---
if ! command -v python3 &>/dev/null; then
    error "python3 is not installed. Install Python ${MIN_MAJOR}.${MIN_MINOR}+ and try again."
fi

# --- Check python version >= 3.11 ---
py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
py_major=$(echo "$py_version" | cut -d. -f1)
py_minor=$(echo "$py_version" | cut -d. -f2)

if [ "$py_major" -lt "$MIN_MAJOR" ] || { [ "$py_major" -eq "$MIN_MAJOR" ] && [ "$py_minor" -lt "$MIN_MINOR" ]; }; then
    error "Python ${MIN_MAJOR}.${MIN_MINOR}+ is required (found ${py_version})."
fi

info "Found Python ${py_version}"

# --- Check pipx is installed ---
if ! command -v pipx &>/dev/null; then
    error "pipx is not installed. See https://pipx.pypa.io/stable/installation/"
fi

info "Found pipx"

# --- Install with pipx from local project ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

info "Installing goblin..."
pipx install "$SCRIPT_DIR" --force --python python3

info "Done! Run 'goblin --help' to get started."
