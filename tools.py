#!/usr/bin/env python
"""Command‑line utilities for simple git interaction and other helpers."""

from __future__ import annotations

import os
import sys
import subprocess
from typing import Dict, List, Optional

# ----------------------------------------------------------------------
# Decorator that marks a function as a tool
# ----------------------------------------------------------------------
def tool(func):
    """Tag a function as a `toolex` tool.

    The decorator simply sets ``_is_toolex_tool = True`` on the function
    so that the runtime can detect it.
    """
    func._is_toolex_tool = True
    return func

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
        # Return a friendly error message instead of the raw exception
        return {name: f"Error: {exc}"}
    except Exception as exc:  # pragma: no cover
        return {name: f"Unknown error: {exc}"}
