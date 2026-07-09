#!/usr/bin/env python
"""Command‑line utilities for simple git interaction and other helpers."""

from __future__ import annotations

import os
import sys
import subprocess
from typing import Dict, List, Optional
from functools import wraps  # Added to preserve function metadata

# ----------------------------------------------------------------------
# Decorator that marks a function as a tool
# ----------------------------------------------------------------------
def tool(capabilities=None):
    """
    Decorator for toolex tools with permission-based filtering.
    Usage: 
        @tool | @tool()      -> No capabilities
        @tool('write')       -> Single capability
        @tool('read exec')   -> Multiple capabilities
    """
    if callable(capabilities):
        func = capabilities
        caps_str = ""
    else:
        func = None
        caps_str = capabilities or ""

    def wrap_decorator(f):
        f._is_toolex_tool = True
        f._required_caps = set(caps_str.split()) if isinstance(caps_str, str) else set()
        return f

    if callable(capabilities):
        # @tool
        return wrap_decorator(func)
    else:
        # @tool('read') or @tool() 
        def decorator(f):
            f._is_toolex_tool = True
            f._required_caps = set(caps_str.split()) if isinstance(caps_str, str) else set()
            return f
        return decorator

# ----------------------------------------------------------------------
# Implement bash_wrap to reduce boilerplate for shell commands
# ----------------------------------------------------------------------
def bash_wrap(name: str, cmd: List[str]):
    """
    Decorator that wraps a function into a standard command-execution pattern.
    It replaces the function's body with logic that prints the command and calls run_tool.

    Args:
        name: The key used in the returned dictionary.
        cmd: The list of base command arguments (e.g., ["git", "status"]).
    """
    def decorator(f):
        @wraps(f)
        def wrapper(args: Optional[str] = "") -> Dict[str, str]:
            # Format the command string for logging/printing as seen in get_git_status
            cmd_label = " ".join(cmd)
            print(f"🤖 {cmd_label} {args}", file=sys.stderr, end='')
            return run_tool(name, cmd, args)
        return wrapper
    return decorator

# ----------------------------------------------------------------------
# Helper to run a command and return its output (or a short error string)
# ----------------------------------------------------------------------
def run_tool(name: str, cmd: List[str], args: str) -> Dict[str, str]:
    """
    Run a command and return its standard output as a string.

    Parameters
    ----------
    name:
        Logical name of the command – used as the key in the returned dict.
    cmd:
        Base command split into individual elements.
    args:
        Optional space‑separated additional arguments to be appended to `cmd`.
    """
    if args:
        args = args.strip()
    if args:
        cmd = cmd + args.split(' ')
    try:
        output = subprocess.check_output(
            cmd,
            cwd=os.getcwd(),          # keep within the current working dir
            stderr=subprocess.STDOUT,  # capture errors too
            text=True,                # bytes → str
        )
        return {name: output.strip()}
    except subprocess.CalledProcessError as exc:
        return {name: f"Error: {exc}"}
    except Exception as exc:  # pragma: no cover
        return {name: f"Unknown error: {exc}"}

def discover_tools(namespace: Dict[str, Any], module_name: str) -> List[str]:
    """
    Scans a namespace to identify tool names belonging to a specific module.
    Used primarily to populate __all__ for clean exports.
    """
    return [
        name for name, obj in namespace.items() 
        if getattr(obj, "_is_toolex_tool", False) 
        and getattr(obj, "__module__", None) == module_name
    ]

