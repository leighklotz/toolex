#!/usr/bin/env bash

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"
ANSWER_DIR=~/wip/answer

source ${ANSWER_DIR}/env.sh
source ${SCRIPT_DIR}/.venv/bin/activate

~/wip/toolex/toolex.py "$@"
