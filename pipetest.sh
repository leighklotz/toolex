#!/usr/bin/env bash

# pipetest – forward piped data after a Y/N confirmation
# -----------------------------------------------------------------------
#   Read all data from standard input (the "pipe").
#   Ask the user “Y or N? ” (case‑insensitive, newline required).
#   If the answer starts with “y” (or “yes”) the data is forwarded
#   to stdout.  Otherwise nothing is written.
# -----------------------------------------------------------------------

pipetest() {
    # 1. Capture stdin into a temporary file – this allows very large input.
    local tmpfile
    if ! tmpfile=$(mktemp --tmpdir="$(mktemp -d)" pipetest.XXXXXX 2>/dev/null) ; then
        printf >&2 "pipetest: could not create temporary file\n"
        exit 1
    fi
    trap 'rm -f "$tmpfile"' EXIT

    cat >"$tmpfile"


    # 3. Prompt from stderr (visible in the terminal) and read a full line.
    (echo -n "🤖 "; head -10 "$tmpfile"; printf "\n🤖 Y or N? ") >&2 

    read -r -t 0 -s reply < /dev/tty     # non‑blocking: only keep the first char
    # If the first char isn’t 'y'/'Y', read the rest of the line to discard it.
    if [[ ! "${reply}" =~ ^[yY]$ ]] ; then
        # Drain the remainder of the line (including the newline)
        read -r reply < /dev/tty
    fi
    printf >&2 "\n"

    # 4. If the first character is 'y' or 'Y', output the captured data.
    case "${reply,,}" in
        y*) cat "$tmpfile" ;;
    esac
}
