#!/usr/bin/env python3

import sys
import subprocess
from typing import Dict, List, Optional
from tooling import tool, run_podman_tool, sandbox_wrap, discover_tools



# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------
@tool("read")
@sandbox_wrap("ls", ["ls"])
def get_ls(args: Optional[str] = "") -> Dict[str, str]:
    """List directory contents. Args: standard 'ls' flags and paths."""
    pass

@tool("read")
@sandbox_wrap("pwd", ["pwd"])
def get_pwd(args: Optional[str] = "") -> Dict[str, str]:
    """Print current working directory using 'pwd'."""
    pass

@tool("read")
@sandbox_wrap("cat", ["cat"])
def get_cat(args: Optional[str] = "") -> Dict[str, str]:
    """Read file contents. Args: filename and options (e.g., '-n')."""
    pass

@tool("read")
@sandbox_wrap("whoami", ["whoami"])
def get_whoami(args: Optional[str] = "") -> Dict[str, str]:
    """Display the current effective username."""
    pass

@tool("read")
@sandbox_wrap("date", ["date"])
def get_date(args: Optional[str] = "") -> Dict[str, str]:
    """Print system date and time."""
    pass

@tool("read")
@sandbox_wrap("find", ["find"])
def get_find(args: Optional[str] = "") -> Dict[str, str]:
    """Search for files in a directory hierarchy. Args: search expression/paths."""
    pass

@tool("read")
@sandbox_wrap("df", ["df"])
def get_df(args: Optional[str] = "") -> Dict[str, str]:
    """Report file system disk space usage. Args: mountpoints or flags."""
    pass

@tool("read")
@sandbox_wrap("wc", ["wc"])
def get_wc(args: Optional[str] = "") -> Dict[str, str]:
    """Print newline, word, and byte counts. Args: filename and options."""
    pass

@tool("read")
@sandbox_wrap("grep", ["grep"])
def get_grep(args: Optional[str] = "") -> Dict[str, str]:
    """Search for patterns in text using regular expressions. Args: pattern/file/flags."""
    pass

@tool("write")
@sandbox_wrap("rm", ["rm"])
def do_rm(args: Optional[str] = "") -> Dict[str, str]: 
    """Remove files or directories. Args: target paths and flags (e.g., '-rf')."""
    raise Exception("rm is not implemented")
