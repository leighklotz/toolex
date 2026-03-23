#!/bin/bash

SCRIPT_DIR=~/wip/llamafiles/scripts/
. ${SCRIPT_DIR}/env.sh

~/wip/toolex/tclient.py "$*"
