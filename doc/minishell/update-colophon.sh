#!/bin/bash

source ~/wip/answer/bin/commands/hx-bootstrap.sh
hx core

cd ~/wip/toolex/
(lx doc/minishell/COLOPHON.md; ls -ltr minishell_tools.py test_minishell.py; bx git log --oneline -15; lx $(find -L .hallux/.bash_history -type f -newer doc/minishell/COLOPHON.md | sort)) \
  | ask 'The first block is the current doc/minishell/COLOPHON.md. The bash history files at the end cover everything since the colophon was written. Update the COLOPHON to cover anything that happened to minishell_tools.py and test_minishell.py since what it already documents, keeping the same style and structure. Output the complete updated COLOPHON.md only.' \
  | answer > doc/minishell/COLOPHON-2.md

echo "suggested commands"
echo "innomd doc/minishell/COLOPHON-2.md"
echo "mv doc/minishell/COLOPHON-2.md doc/minishell/COLOPHON.md"
echo "git add doc/minishell/COLOPHON.md && help-commit"

