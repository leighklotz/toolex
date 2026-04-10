#!/usr/bin/env bash

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"
LLAMAFILES_SCRIPT_DIR=~/wip/llamafiles/scripts/
. ${LLAMAFILES_SCRIPT_DIR}/env.sh


source ${SCRIPT_DIR}/.venv/bin/activate


~/wip/toolex/toolex.py "$@"
