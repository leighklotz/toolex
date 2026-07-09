#!/usr/bin/env python3

import sys
from typing import Dict, List, Optional
from tooling import tool, run_tool, bash_wrap, discover_tools

# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------
@tool("read")
@bash_wrap("ls", ["ls"])
def get_ls(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("pwd", ["pwd"])
def get_pwd(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("cat", ["cat"])
def get_cat(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("whoami", ["whoami"])
def get_whoami(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("date", ["date"])
def get_date(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("find", ["find"])
def get_find(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("df", ["df"])
def get_df(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("read")
@bash_wrap("wc", ["wc"])
def get_wc(args: Optional[str] = "") -> Dict[str, str]: pass

@tool("write")
@bash_wrap("rm", ["rm"])
def do_rm(args: Optional[str] = "") -> Dict[str, str]: 
    """Delete file"""
    raise Exception("rm is not implemented")

@tool("write exec")
def run_command(command: str) -> str:
    """Executes a shell command and returns stdout and stderr."""
    print(f"🤖 running command: {command}", file=sys.stderr, end='')
    import subprocess
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        if result.stderr:
            return result.stdout + "\nstderr:\n" + result.stderr
        else:
            return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error running command: {e.stderr}\n{e.stdout}"


__all__ = discover_tools(globals(), __name__)

