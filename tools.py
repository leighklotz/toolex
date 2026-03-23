#!/usr/bin/env python

import os
import subprocess

# ---------------------------------------------------------------------------
# Simple decorator that records a marker attribute on the function.
# ---------------------------------------------------------------------------
def tool(func):
    """
    Decorator that marks a function as a tool by setting the
    ``_is_llmfoo_tool`` attribute to ``True``.
    """
    func._is_llmfoo_tool = True
    return func

@tool
def get_weather(location: str) -> dict:
    """Get the current weather for a given location."""
    # Dummy implementation – replace with real API calls
    if location.lower() in {"paris", "london"}:
        return {"temperature": "14°C", "condition": "partly cloudy"}
    return {"temperature": "unknown", "condition": "unknown"}

def _run(name, args) -> dict:
    """
    Return the output of named command as a string.
    """
    try:
        output = subprocess.check_output(
            args,
            cwd=os.getcwd(),          # optional: restrict to current dir
            stderr=subprocess.STDOUT, # capture errors too
            text=True                 # returns str instead of bytes
        )
        return {name: output.strip()}
    except Exception as e:
        return {name: f"Error: {e}"}


@tool
def get_git_status() -> dict:
    """
    Return the output of `git status` as a string.
    """
    return _run('git_status', ['git', 'status'])

