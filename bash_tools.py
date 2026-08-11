#!/usr/bin/env python3

import sys
import subprocess
import inspect
from typing import Dict, List, Optional
from tooling import tool, run_bash_tool, bash_wrap, discover_tools
from functools import wraps



# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------
@tool("read")
@bash_wrap("ls", ["ls"])
def get_ls(args: Optional[str] = "") -> Dict[str, str]:
    """List directory contents. Args: standard 'ls' flags and paths."""
    pass

@tool("read")
@bash_wrap("pwd", ["pwd"])
def get_pwd(args: Optional[str] = "") -> Dict[str, str]:
    """Print current working directory using 'pwd'."""
    pass

@tool("read")
@bash_wrap("cat", ["cat"])
def get_cat(args: Optional[str] = "", stdin: Optional[str] = None) -> Dict[str, str]:
    """Read file contents. Args: filename and options (e.g., '-n')."""
    pass

@tool("read")
@bash_wrap("whoami", ["whoami"])
def get_whoami(args: Optional[str] = "") -> Dict[str, str]:
    """Display the current effective username."""
    pass

@tool("read")
@bash_wrap("date", ["date"])
def get_date(args: Optional[str] = "") -> Dict[str, str]:
    """Print system date and time."""
    pass

def validate_find_args(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        sig = inspect.signature(func)
        param_names = [p.name for p in sig.parameters.values()]

        payload = None
        if args and not isinstance(args[0], (dict, list)):
            payload = args[0]
        elif "args" in kwargs:
            payload = kwargs["args"]

        arg_payload = str(payload).strip() if payload is not None else ""

        # <- your requirement
        if not arg_payload or arg_payload == ".":
            raise ValueError(
                "get_find requires a non-empty search expression; '.' is not allowed because it may produce too much data."
            )
        return func(*args, **kwargs)
    return wrapper



@tool("read")
@validate_find_args
@bash_wrap("find", ["find"])
def get_find(args: Optional[str] = "") -> Dict[str, str]:
    """Search for files in a directory hierarchy. Args: search expression/paths."""
    pass

@tool("read")
@bash_wrap("df", ["df"])
def get_df(args: Optional[str] = "") -> Dict[str, str]:
    """Report file system disk space usage. Args: mountpoints or flags."""
    pass

@tool("read")
@bash_wrap("wc", ["wc"])
def get_wc(args: Optional[str] = "", stdin: Optional[str] = None) -> Dict[str, str]:
    """Print newline, word, and byte counts. Args: filename and options."""
    pass

@tool("read")
@bash_wrap("head", ["head"])
def get_head(args: Optional[str] = "", stdin: Optional[str] = None) -> Dict[str, str]:
    """Output head lines of input or file"""
    pass

@tool("read")
@bash_wrap("tail", ["tail"])
def get_tail(args: Optional[str] = "", stdin: Optional[str] = None) -> Dict[str, str]:
    """Output tail lines of input or file"""
    pass

@tool("read")
@bash_wrap("grep", ["grep"])
def get_grep(args: Optional[str] = "", stdin: Optional[str] = None) -> Dict[str, str]:
    """Search for patterns in text using regular expressions. Args: pattern/file/flags."""
    pass

@tool("write")
@bash_wrap("patch", ["patch"])
def get_grep(args: Optional[str] = "", stdin: Optional[str] = None) -> Dict[str, str]:
    """Posix patch program."""
    pass

### File must end with this line
__all__ = discover_tools(globals(), __name__)
