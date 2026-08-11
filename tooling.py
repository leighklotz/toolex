#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import sys
import subprocess
import shlex
import inspect
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Union, Callable
from functools import wraps


# --- SANDBOX CONFIGURATION ---
SANDBOX_EMOJI = "\U0001F3D6" # 🗖

# TODO: move elsewhere
SANDBOX_CONFIG = {
    "image": "toolex-sandbox",
    "host_data_dir": os.environ.get("TOOLEX_WORKSPACE_DIR", os.getcwd()),
}

# Logging
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0


def tool(capabilities: Union[str, List[str], Callable] = None):
    """
    Decorator that marks a function as an LLM-callable tool with specific permissions.
    """
    if callable(capabilities):
        func = capabilities
        func._is_toolex_tool = True
        func._required_caps = set()
        return func

    def decorator(f: Callable) -> Callable:
        if isinstance(capabilities, str):
            caps_set = set(capabilities.split())
        elif isinstance(capabilities, (list, tuple)):
            caps_set = set(capabilities)
        else:
            caps_set = set()

        f._is_toolex_tool = True
        f._required_caps = caps_set
        return f

    return decorator

def discover_tools(namespace: Dict[str, Any], module_name: str) -> List[str]:
    """Scans namespace for tools belonging to the current module."""
    return [
        name for name, obj in namespace.items()
        if getattr(obj, "_is_toolex_tool", False)
        and getattr(obj, "__module__", None) == module_name
    ]


# Compatibility shim for legacy dict consumers
def as_dict(result: CommandResult, name: str) -> Dict[str, Any]:
    if result.is_success:
        return {name: result.stdout.strip()}
    else:
        error_report = f"Command exited with status {result.exit_code}.\nSTDERR: {result.stderr.strip()}"
        return {name: error_report}
