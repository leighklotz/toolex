#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# navigator
#   Walks through files, showing `hx describe -`
#   output for each.  Navigate with:
#     Ctrl-N  next
#     Ctrl-P  previous
#     Ctrl-C  quit
# ──────────────────────────────────────────────

# --- Hallux bootstrap (adjust path if needed) ---
HX_BOOTSTRAP="${HX_BOOTSTRAP:-${HOME}/wip/answer/bin/commands/hx-bootstrap.sh}"
source "$HX_BOOTSTRAP"
hx enable

# --- args ---
if [[ $# -lt 1 ]]; then
    printf 'Usage: %s <file> [file …]\n' "$0" >&2
    exit 1
fi

files=("$@")
count=${#files[@]}
index=0

# --- read one raw keystroke from the TTY ---
read_key() {
    local key
    stty -echo < /dev/tty
    IFS= read -r -n1 key < /dev/tty
    stty echo < /dev/tty
    printf '%s' "$key"
}

# --- restore terminal if we die unexpectedly ---
cleanup() {
    stty icanon echo < /dev/tty 2>/dev/null
    printf '\n'
}
trap cleanup INT TERM

# --- main loop ---
while true; do
    fn="${files[$index]}"

    # show the description (cat → hx describe reads stdin via -)
    clear
    (echo -e "# ${fn}\n"; cat -- "$fn" | hx describe -) | innomd


    key="$(read_key)"

    case "$key" in
        $'\x0e' | 'n' | 'N')   # Ctrl-N → next
            (( index < count - 1 )) && (( ++index )) || index=0
            ;;
        $'\x10' | 'p' | 'P' )   # Ctrl-P → previous
            (( index > 0 )) && (( index-- )) || index=$(( count - 1 ))
            ;;
        'x' | 'X')   # x → exit
            printf '\n  👋\n'
            exit 0
            ;;
        *)         # anything else: just re-prompt
            printf "you pressed '%s'\n" "$key"
            printf '  (press ^N / ^P / x)\n> '
            ;;
    esac
done
