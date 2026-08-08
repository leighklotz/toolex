#!/usr/bin/env -S bash -x

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE}")")"

FN="wikipedia_en_all_mini_2026-06.zim"

cd "${SCRIPT_DIR}"
wget -c "https://download.kiwix.org/zim/wikipedia/$FN" -O "zims/$FN"
