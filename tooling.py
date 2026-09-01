from __future__ import annotations
import os
import sys
import subprocess
import shlex
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union, Callable, Set

logger = logging.getLogger(__name__)

# Configuration for Podman sandboxing
SANDBOX_CONFIG = {
    "image": "toolex-sandbox",
    "host_data_dir": os.environ.get("TOOLEX_WORKSPACE_DIR", os.getcwd()),
}

def _truncate(s: str, limit: int = 1024 * 100) -> str:
    """Truncate output to protect LLM context windows."""
    if len(s) <= limit:
        return s
    # Convert bytes if it's not a string for some reason (defensive)
    if not isinstance(s, str):
        s = str(s)
    truncated_part = f"\\n...[truncated {len(s) - limit} bytes]"
    return s[:limit] + truncated_part

@dataclass(frozen=True)
class CommandResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0

    def to_payload(self) -> dict:
        """Format for LLM tool message response."""
        return {
            "stdout": _truncate(self.stdout),
            "stderr": _truncate(self.stderr),
            "exit_code": self.exit_code,
        }

def tool(capabilities: Union[str, List[str], Callable] = None):
    """Decorator for legacy LLM tools (to be used only by minishell-specific wrapper)."""
    if callable(capabilities):
        f = capabilities
        f._is_toolex_tool = True
        return f

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

