#!/usr/bin/env python3

import sys
from typing import Dict, List, Optional
from tooling import tool, run_tool, bash_wrap

# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------


@tool("read")
@bash_wrap("git_status", ["git", "status"])
def get_git_status(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("git_diff", ["git", "diff"])
def get_git_diff(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("git_branch", ["git", "branch"])
def get_git_branch(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("write")
@bash_wrap("git_merge", ["git", "merge"])
def do_git_merge(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("write")
@bash_wrap("git_checkout", ["git", "checkout"])
def do_git_checkout(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("write")
@bash_wrap("git_pull", ["git", "pull"])
def do_git_pull(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("write")
@bash_wrap("git_rebase", ["git", "rebase"])
def do_git_rebase(args: Optional[str] = "") -> Dict[str, str]: pass


# ----------------------------------------------------------------------
# Tool discovery
# ----------------------------------------------------------------------
__all__ = [
    name for name, obj in globals().items() 
    if getattr(obj, "_is_toolex_tool", False) and obj.__module__ == __name__
]

