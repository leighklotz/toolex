#!/bin/bash

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"

source "${SCRIPT_DIR}/pipetest.sh"


# PROMPT='Use `git diff` and `git status` to understand the changes in the current repo. Write a `git commit` command describing the uncommited changes, ready to execute in a bash fence.'
PROMPT='Use `git status` and `git diff` and analyze the results to output a bash fence containing a `git commit -a` command to commit the staged and unstaged changes. Summarize the impact/import of the changes in the first message line. Write a detailed, imperative message with multiple vertically aligned `-m` flags summarizing specific changes. Properly quote the bash strings. Ignore untracked files. Omit commentary after the command. If there are no changes to commit, output `echo no changes` instead of a `git commit -a` command.
'

if [ "$*" != '' ]; then
    PROMPT_ADD="Append '$*' flags to all \`git diff\` commands."
    printf "PROMPT_ADD: %s\n" "$PROMPT_ADD" >&2
    PROMPT="${PROMPT_ADD}\n${PROMPT}"
    printf "PROMPT: %s\n" "$PROMPT" >&2    
fi

${SCRIPT_DIR}/toolex.sh "$PROMPT" | pipetest "Commit" | unfence | bash
