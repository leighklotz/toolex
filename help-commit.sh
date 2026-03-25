#!/bin/bash

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"

source "${SCRIPT_DIR}/pipetest.sh"


# PROMPT='Use `git diff` and `git status` to understand the changes in the current repo. Write a `git commit` command describing the uncommited changes, ready to execute in a bash fence.'
PROMPT='Use `git status` and `git diff` and analyze the results to generate a `git commit -a` command to commit the staged and unstaged changes. Write a detailed, imperative message with multiple vertically aligned `-m` flags summarizing specific changes. Summarize the impact/import of the changes in the first message line. Properly quote the bash strings. Ignore untracked files. Omit commentary after the command. If there are no changes to comment, output `echo no changes` instead of a `git commit -a` command.
'

${SCRIPT_DIR}/tclient.sh "$PROMPT" | pipetest | unfence | bash
