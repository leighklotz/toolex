#!/bin/bash

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"

source "${SCRIPT_DIR}/pipetest.sh"


PROMPT='Analyze `git status` and `git diff`. Output a bash fence with `git commit -a`. Write a detailed, imperative message with multiple vertically aligned `-m` flags summarizing specific changes. Ignore untracked files.'

${SCRIPT_DIR}/tclient.sh "$PROMPT" | pipetest | unfence | bash
