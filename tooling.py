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


# --- HOST EXECUTION ---
def bash_wrap(name: str, cmd: List[str]):
    """Runs the command directly on host machine."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs):
            sig = inspect.signature(f)
            param_names = [p.name for p in sig.parameters.values()]

            payload = None
            if args and not isinstance(args[0], (dict, list)):
                payload = args[0]
            elif "args" in kwargs:
                payload = kwargs["args"]

            arg_payload = str(payload).strip() if payload is not None else ""

            stdin_data = None
            for p in ("stdin", "input_data"):
                if p in param_names and p in kwargs:
                    stdin_data = kwargs.get(p)
                    break

            print(f"🚀{' '.join(cmd)} {arg_payload}", file=sys.stderr, end='')
            return run_bash_tool(name, cmd, arg_payload, stdin_data=stdin_data)
        wrapper._command_name = name
        return wrapper
    return decorator


def run_bash_tool(name: str, cmd: List[str], args: Optional[str] = "", stdin_data: Optional[str] = None) -> CommandResult:
    """Runs the command directly on host machine with stdin support."""
    args_list = shlex.split(args) if args and args.strip() else []
    full_cmd = list(cmd) + args_list

    try:
        result = subprocess.run(
            full_cmd,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            input=stdin_data
        )
        return CommandResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
    except Exception as exc:
        return CommandResult("", str(exc), 1)


# --- PODMAN SANDBOX ---
def sandbox_wrap(name: str, cmd: List[str]):
    """Wraps a function so it executes inside a Podman container."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs):
            sig = inspect.signature(f)
            param_names = [p.name for p in sig.parameters.values()]

            payload = None
            if args and not isinstance(args[0], (dict, list)):
                payload = args[0]
            elif "args" in kwargs:
                payload = kwargs["args"]

            arg_payload = str(payload).strip() if payload is not None else ""

            stdin_data = None
            for p in ("stdin", "input_data"):
                if p in param_names and p in kwargs:
                    stdin_data = kwargs.get(p)
                    break

            print(f"{SANDBOX_EMOJI} [SANDBOX {name}]: {' '.join(cmd)} {arg_payload}".rstrip(), file=sys.stderr)
            return run_podman_tool(name, cmd, arg_payload, f._required_caps if hasattr(f, '_required_caps') else set(), stdin_data=stdin_data)
        return wrapper
    return decorator


def run_podman_tool(name: str, base_cmd: List[str], args: str, caps: set, stdin_data: Optional[str] = None) -> CommandResult:
    """Executes a command inside a Podman container."""
    mount_mode = "rw" if "write" in caps else "ro"
    args_list = shlex.split(args) if args and args.strip() else []
    tool_argv = list(base_cmd) + args_list

    podman_cmd = [
        "podman", "run", "--rm",
        "--net", "none",
        "--workdir", "/workspace",
        "-v", f"{SANDBOX_CONFIG['host_data_dir']}:/workspace:{mount_mode}",
        SANDBOX_CONFIG["image"],
    ] + tool_argv

    try:
        result = subprocess.run(podman_cmd, capture_output=True, text=True, input=stdin_data)
        return CommandResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
    except Exception as exc:
        return CommandResult("", str(exc), 1)


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
