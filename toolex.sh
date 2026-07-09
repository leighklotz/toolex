#!/usr/bin/env bash

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"
ANSWER_BIN_DIR=~/wip/answer/bin
TOOLEX_PY=~/wip/toolex/toolex.py

source "${ANSWER_BIN_DIR}/env.sh"
if [ -f "${SCRIPT_DIR}/.venv/bin/activate" ]; then
    source "${SCRIPT_DIR}/.venv/bin/activate"
fi

"${TOOLEX_PY}" "$@"
