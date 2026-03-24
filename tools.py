#!/usr/bin/env python
"""
Command‑line tools for simple git interaction and other utilities.

- ``tool`` – very small decorator that tags functions as tools.
- ``get_weather`` – stub that returns a hard‑coded weather description.
- ``get_git_status`` – returns the plain output of ``git status``.
- ``get_git_diff`` – returns the plain output of ``git diff`` with optional
  arguments.
- ``get_git_has_changes`` – detects whether there are unstaged changes.
"""

from __future__ import annotations

import os
import subprocess
from typing import Dict, List

# ---------------------------------------------------------------------------
# Small decorator that marks a function as a tool by setting an attribute.
# ---------------------------------------------------------------------------

def tool(func):
    """Decorator that tags a function as a tool.

    The decorator simply sets ``_is_toolex_tool = True`` on the function so
    that the runtime can detect it.
    """
    func._is_toolex_tool = True
    return func


# ---------------------------------------------------------------------------
# Helper to run a command and return its output (or a short error string).
# ---------------------------------------------------------------------------

def _run(name: str, args: List[str], more_args: List[str] | None = None) -> Dict[str, str]:
    """
    Run a command and return its standard output as a string.

    Parameters
    ----------
    name:
        Logical name of the command – used as the key in the returned dict.
    args:
        Base command split into individual elements.
    more_args:
        Optional additional arguments to be appended to ``args``.
    """
    cmd = args + (more_args or [])
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


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

@tool
def get_weather(location: str) -> Dict[str, str]:
    """Return a dummy weather description for a given location."""
    location = location.strip().lower()
    if location in {"paris", "london"}:
        return {"temperature": "14°C", "condition": "partly cloudy"}
    return {"temperature": "unknown", "condition": "unknown"}


@tool
def get_git_status(args: List[str] | None = None) -> Dict[str, str]:
    """Return the output of ``git status`` (optionally with extra args)."""
    return _run("git_status", ["git", "status"], list(args or []))


@tool
def get_git_diff(args: List[str] | None = None) -> Dict[str, str]:
    """Return the output of ``git diff`` (optionally with extra args)."""
    return _run("git_diff", ["git", "diff"], list(args or []))


@tool
def get_git_has_changes() -> Dict[str, int]:
    """Return ``0`` if there are no unstaged changes, ``1`` otherwise.

    The function runs ``git diff --quiet`` which exits with status 0 when the
    working tree is clean and 1 when there are unstaged changes.  The
    result is returned in a dictionary so that callers can use the key
    ``has_changes``.

    The implementation uses ``subprocess.run`` directly so that we can inspect
    the return code without caring about any output.
    """
    cmd = ["git", "diff", "--quiet"]
    # ``check=False`` lets us capture the return code
    result = subprocess.run(
        cmd,
        cwd=os.getcwd(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    # Return 0 for clean, 1 for changes
    return {"has_changes": result.returncode}


# ---------------------------------------------------------------------------
# How to discover the tools
# ---------------------------------------------------------------------------

__all__ = [
    "tool",
    "get_weather",
    "get_git_status",
    "get_git_diff",
    "get_git_has_changes",
]
