#!/usr/bin/env -S bash -e

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"

if command -v apt > /dev/null 2>&1; then
    sudo apt install kiwix-tools
elif command -v rpm > /dev/null 2>&1; then
    rpm -qf /usr/bin/kiwix-search
else
    echo "No installation emthod for kiwix-tools OS package"
    exit -1
fi
