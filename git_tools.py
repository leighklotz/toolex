#!/usr/bin/env python3
# git_tools.py

import sys
from typing import Dict, List, Optional
from tools import tool, run_tool

# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------
@tool
def get_git_status(args: Optional[str] = "") -> Dict[str, str]:
    """Return the output of ``git status`` (optionally with extra args)."""
    print(f"🤖 git status {args}", file=sys.stderr)
    return run_tool("git_status", ["git", "status"], args)

@tool
def get_git_diff(args: Optional[str] = "") -> Dict[str, str]:
    """Return the output of ``git diff`` (optionally with specified args)."""
    print(f"🤖 git diff {args}", file=sys.stderr)
    return run_tool("git_diff", ["git", "diff"], args)

@tool
def do_git_merge(args: Optional[str] = "") -> Dict[str, str]:
    """Do `git merge` (optionally with specified args)."""
    print(f"🤖 git merge {args}", file=sys.stderr)
    return run_tool("git_merge", ["git", "merge"], args)

@tool
def do_git_checkout(args: Optional[str] = "") -> Dict[str, str]:
    """Do `git checkout` (optionally with specified args)."""
    print(f"🤖 git checkout {args}", file=sys.stderr)
    return run_tool("git_checkout", ["git", "checkout"], args)

@tool
def do_git_pull(args: Optional[str] = "") -> Dict[str, str]:
    """Do `git pull` (optionally with specified args)."""
    print(f"🤖 git pull {args}", file=sys.stderr)
    return run_tool("git_pull", ["git", "pull"], args)

# ----------------------------------------------------------------------
# Tool discovery
# ----------------------------------------------------------------------
__all__ = [
    "get_git_status",
    "get_git_diff",
     "do_git_merge",
     "do_git_checkout",
     "do_git_pull"
]
