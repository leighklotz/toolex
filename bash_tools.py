#!/usr/bin/env python3

import sys
from typing import Dict, List, Optional
from tooling import tool, run_tool

# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------
@tool
def get_ls(args: Optional[str] = "") -> Dict[str, str]:
    """Return the output of ``ls`` (optionally with specified args)."""
    print(f"🤖 ls {args}", file=sys.stderr)
    return run_tool("ls", ["ls"], args)

@tool
def get_pwd(args: Optional[str] = "") -> Dict[str, str]:
    """Return the current working directory."""
    print(f"🤖 pwd {args}", file=sys.stderr)
    return run_tool("pwd", ["pwd"], args)

@tool
def get_cat(args: Optional[str] = "") -> Dict[str, str]:
    """Return the content of a file using ``cat``."""
    print(f"🤖 cat {args}", file=sys.stderr)
    return run_tool("cat", ["cat"], args)

@tool
def get_whoami(args: Optional[str] = "") -> Dict[str, str]:
    """Return the current user."""
    print(f"🤖 whoami {args}", file=sys.stderr)
    return run_tool("whoami", ["whoami"], args)

@tool
def get_date(args: Optional[str] = "") -> Dict[str, str]:
    """Return the current system date and time."""
    print(f"🤖 date {args}", file=sys.stderr)
    return run_tool("date", ["date"], args)

@tool
def get_find(args: Optional[str] = "") -> Dict[str, str]:
    """Search for files using the ``find`` command."""
    print(f"🤖 find {args}", file=sys.stderr)
    return run_tool("find", ["find"], args)

@tool
def get_df(args: Optional[str] = "") -> Dict[str, str]:
    """Return disk space usage using ``df``."""
    print(f"🤖 df {args}", file=sys.stderr)
    return run_tool("df", ["df"], args)

# ----------------------------------------------------------------------
# Tool discovery
# ----------------------------------------------------------------------
__all__ = [
    "get_ls",
    "get_pwd",
    "get_cat",
    "get_whoami",
    "get_date",
    "get_find",
    "get_df"
]

