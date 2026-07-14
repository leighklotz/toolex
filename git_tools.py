#!/usr/bin/env python3
from typing import Dict, List, Optional
from tooling import tool, run_bash_tool, bash_wrap, discover_tools

@tool("read")
@bash_wrap("git_status", ["git", "status"])
def get_git_status(args: Optional[str] = "") -> Dict[str, str]: 
    """`git status`: returns the current working tree status (staged, unstaged, untracked)."""
    pass

@tool("read")
@bash_wrap("git_diff", ["git", "diff"])
def get_git_diff(args: Optional[str] = "") -> Dict[str, str]: 
    """`git diff`: shows changes between commits or the working tree. Use args to specify paths."""
    pass

@tool("read")
@bash_wrap("git_branch", ["git", "branch"])
def get_git_branch(args: Optional[str] = "") -> Dict[str, str]: 
    """`git branch`: lists branches in the local repository. Use -a to see remote branches."""
    pass

@tool("read")
@bash_wrap("git_log", ["git", "log"])
def get_git_log(args: Optional[str] = "") -> Dict[str, str]: 
    """`git log`: shows the commit history. Use args for formatting or limiting results."""
    pass

@tool("write")
@bash_wrap("git_merge", ["git", "merge"])
def do_git_merge(args: Optional[str] = "") -> Dict[str, str]: 
    """`git merge`: merges specified remote or local branch into the current branch."""
    pass

@tool("write")
@bash_wrap("git_checkout", ["git", "checkout"])
def do_git_checkout(args: Optional[str] = "") -> Dict[str, str]: 
    """`git checkout`: switches branches or restores working tree files."""
    pass

@tool("write")
@bash_wrap("git", ["git", "pull"])
def do_git_pull(args: Optional[str] = "") -> Dict[str, str]: 
    """`git pull`: fetches from and integrates with another repository or local branch."""
    pass

@tool("write")
@bash_wrap("git_rebase", ["git", "rebase"])
def do_git_rebase(args: Optional[str] = "") -> Dict[str, str]: 
    """`git rebase`: reapplies commits on top of another base tip."""
    pass

__all__ = discover_tools(globals(), __name__)
