#!/usr/bin/env bash

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"
ANSWER_DIR=~/wip/answer/
TOOLEX_PY=~/wip/toolex/toolex.py

source "${ANSWER_DIR}/bin/commands/hx-bootstrap.sh" && hx core

export MODEL=$(hx model)

if [ -f "${SCRIPT_DIR}/.venv/bin/activate" ]; then
    # Pip-style: Use the local virtual environment if it exists
    source "${SCRIPT_DIR}/.venv/bin/activate"
    python "$TOOLEX_PY" "$@"

elif command -v uv > /dev/null 2>&1; then
    # UV-style: Run via uv (automatically handles dependencies from pyproject.toml)
    uv run -- python "$TOOLEX_PY" "$@"
else
    # Fallback to system Python
    python3 "$TOOLEX_PY" "$@"
fi
