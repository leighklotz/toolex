#!/usr/bin/env python3
from typing import Dict, List, Optional
from tooling import tool, run_bash_tool, bash_wrap, discover_tools

# --- READ ONLY TOOLS (Standard) ---

@tool("read")
@bash_wrap("git_status", ["git", "status"])
def get_git_status(args: Optional[str] = "") -> Dict[str, str]: 
    """`git status`: returns the current working tree status. Use args to filter."""
    pass

@tool("read")
@bash_wrap("git_diff", ["git", "diff"])
def get_git_diff(args: Optional[str] = "") -> Dict[str, str]: 
    """`git diff`: shows changes between commits or the working tree."""
    pass

@tool("read")
@bash_wrap("git_branch", ["git", "branch"])
def get_git_branch(args: Optional[str] = "") -> Dict[str, str]: 
    """`git branch`: lists branches. Use args like '-r' for remote."""
    pass

@tool("read")
@bash_wrap("git_log", ["git", "log"])
def get_git_log(args: Optional[str] = "") -> Dict[str, str]: 
    """`git log`: shows commit history. Use args for limiting results."""
    pass

# --- ADVANCED QUERY TOOL (The Fix) ---

@tool("read")
@bash_wrap("git_query", ["git"])
def run_git_query(args: Optional[str] = "") -> Dict[str, str]: 
    """
    Executes complex Git queries with advanced formatting and sorting.
    Allowed subcommands (prefixes): 'branch', 'log', 'show', 'status'.
    Example args: "-r --format='%(authordate:short) %(refname)'"
    Example args: "log -1 origin/main --format=%ai"
    """
    # Whitelist to prevent the LLM from using complex formatting with destructive commands.
    allowed_prefixes = ["branch", "log", "show", "status"]
    
    parts = args.strip().split()
    if not parts:
        return {"git_query": "Error: No subcommand provided."}

    # Check if the first non-flag argument is in our whitelist
    subcommand_found = False
    for part in parts:
        if not part.startswith("-"):
            if part not in allowed_prefixes:
                return {"git_query": f"Error: Subcommand '{part}' is not permitted for queries."}
            subcommand_found = True
            break
    
    if not subcommand_found:
         return {"git_query": "Error: No valid Git subcommand provided (e.g., 'branch' or 'log')."}

    pass # Execution handled by @bash_wrap

# --- WRITE TOOLS (High Privilege) ---

@tool("write")
@bash_wrap("git_merge", ["git", "merge"])
def do_git_merge(args: Optional[str] = "") -> Dict[str, str]: 
    """`git merge`: merges specified branches."""
    pass

@tool("write")
@bash_wrap("git_checkout", ["git", "checkout"])
def do_git_checkout(args: Optional[str] = "") -> Dict[str, str]: 
    """`git checkout`: switches branches or restores files."""
    pass

__all__ = discover_tools(globals(), __name__)
