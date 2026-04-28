# enable.sh: source this file

[[ ":$PATH:" != *":$HOME/wip/toolex:"* ]] && export PATH="$HOME/wip/toolex:$PATH"

if [[ "$PS1" != *"🛠️"* ]]; then
    export TOOLEX_OLD_PS1="$PS1"
    [[ "$PS1" == *'$'* ]] && PS1="${PS1/\\$/🛠️\\$}" || PS1="🛠️{PS1}"
fi

# source ~/wip/toolex/functions.sh
