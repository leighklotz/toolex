#!/usr/bin/env python3

import sys
from typing import Dict, List, Optional
from tooling import tool, run_tool

# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------
@tool("read")
def get_ls(args: Optional[str] = "") -> Dict[str, str]:
    """Return the output of ``ls`` (optionally with specified args)."""
    print(f"🤖 ls {args}", file=sys.stderr)
    return run_tool("ls", ["ls"], args)

@tool("read")
def get_pwd(args: Optional[str] = "") -> Dict[str, str]:
    """Return the current working directory."""
    print(f"🤖 pwd {args}", file=sys.stderr)
    return run_tool("pwd", ["pwd"], args)

@tool("read")
def get_cat(args: Optional[str] = "") -> Dict[str, str]:
    """Return the content of a file using ``cat``."""
    print(f"🤖 cat {args}", file=sys.stderr)
    return run_tool("cat", ["cat"], args)

@tool("read")
def get_whoami(args: Optional[str] = "") -> Dict[str, str]:
    """Return the current user."""
    print(f"🤖 whoami {args}", file=sys.stderr)
    return run_tool("whoami", ["whoami"], args)

@tool("read")
def get_date(args: Optional[str] = "") -> Dict[str, str]:
    """Return the current system date and time."""
    print(f"🤖 date {args}", file=sys.stderr)
    return run_tool("date", ["date"], args)

@tool("read")
def get_find(args: Optional[str] = "") -> Dict[str, str]:
    """Search for files using the ``find`` command."""
    print(f"🤖 find {args}", file=sys.stderr)
    return run_tool("find", ["find"], args)

@tool("read")
def get_df(args: Optional[str] = "") -> Dict[str, str]:
    """Return disk space usage using ``df``."""
    print(f"🤖 df {args}", file=sys.stderr)
    return run_tool("df", ["df"], args)

@tool("write")
def do_rm(args: Optional[str] = "") -> Dict[str, str]:
    """Delete file"""
    raise Exception("rm is not implemented")
    print(f"🤖 rm {args}", file=sys.stderr)
    return run_tool("rm", ["rm"], args)

@tool("write exec")
def run_command(command: str) -> str:
    """Executes a shell command and returns stdout and stderr."""
    print(f"🤖 running command: {command}", file=sys.stderr)
    import subprocess
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        return result.stdout + "\n" + result.stderr
    except subprocess.CalledProcessError as e:
        return f"Error running command: {e.stderr}\n{e.stdout}"


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
    "get_df",
    "do_rm",
    "run_command"
]

