#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import sys
import subprocess
import shlex
from typing import Dict, List, Optional, Any, Union, Callable
from functools import wraps



# --- SANDBOX CONFIGURATION ---
SANDBOX_EMOJI = "\U0001F3D6" # 🏖️

# TODO: move elsewhere
SANDBOX_CONFIG = {
    "image": "toolex-sandbox",          # The image you built with Podman
    "host_data_dir": os.environ.get("TOOLEX_WORKSPACE_DIR", os.getcwd()), # What the LLM sees as /workspace
}

# Logging
logger = logging.getLogger(__name__)

def tool(capabilities: Union[str, List[str], Callable] = None,
         *,
         path_args: Optional[frozenset[str]] = None):
    """
    Decorator that marks a function as an LLM-callable tool with specific permissions.
    Handles various calling styles for flexibility:
        @tool                 -> No capabilities (defaults to empty set)
        @tool("write")         -> Single capability string
        @tool(["read", "exec"]) -> Multiple capability list/tuple
        @tool()                -> Explicitly no capabilities

    path_args:
        Optional frozenset of parameter names that represent file-system paths
        (or glob patterns).  toolex.py's runtime enforcement validates *only*
        these arguments against the session's allowed glob patterns.  Omit for
        tools whose parameters are never paths (e.g. shell, git).

    The decorator also prepends a "Capabilities: …" header to the function's
    docstring so the OpenAI schema generator (which reads inspect.getdoc)
    always includes the capability line in the tool description.
    """
    # Case 1: User used @tool (no parentheses). 'capabilities' is the function being decorated.
    if callable(capabilities):
        func = capabilities
        func._is_toolex_tool = True
        func._required_caps = set()
        func._path_args = frozenset()
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
        f._path_args = frozenset(path_args) if path_args else frozenset()

        # Render the capability into the docstring so it appears in the
        # generated tool schema description — a uniform, machine-parseable
        # header that never drifts from the grant.
        if caps_set:
            cap_header = f"Capabilities: {' '.join(sorted(caps_set))}"
            existing_doc = (f.__doc__ or "").strip()
            f.__doc__ = cap_header + "\n" + existing_doc if existing_doc else cap_header

        return f

    # Case 2 & 3: User used @tool(...) or @tool(). Return the decorator factory.
    return decorator

# --- HOST EXECUTION ---
def bash_wrap(name: str, cmd: List[str]):
    """Runs the command directly on host machine."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            cmd_label = " ".join(cmd)
            payload = None
            if args and not isinstance(args[0], (dict, list)):
                payload = args[0]
            elif "args" in kwargs:
                payload = kwargs["args"]
            
            arg_payload = str(payload).strip() if payload is not None else ""
            
            print(f"🚀{' '.join(cmd)} {arg_payload}", file=sys.stderr, end='', flush=True)
            return run_bash_tool(name, cmd, arg_payload)
        return wrapper
    return decorator

def run_bash_tool(name: str, cmd: List[str], args: Optional[str] = "") -> Dict[str, Any]:
    """Runs the command directly on host machine. Returns stderr on failure."""
    args_list = shlex.split(args) if args.strip() else []
    full_cmd = list(cmd) + args_list

    try:
        result = subprocess.run(
            full_cmd, 
            cwd=os.getcwd(), 
            capture_output=True, 
            text=True,
            check=True
        )
        return {name: result.stdout.strip()}
    except subprocess.CalledProcessError as exc:
        # CRITICAL FIX: Return the actual error from Git so the LLM knows WHY it failed.
        error_report = f"Command '{' '.join(full_cmd)}' exited with status {exc.returncode}.\nSTDERR: {exc.stderr.strip()}"
        return {name: error_report}
    except Exception as exc: 
        return {name: f"System Error: {str(exc)}"}

# --- PODMAN SANDBOX ---
def sandbox_wrap(name: str, cmd: List[str]):
    """Wraps a function so it executes inside a Podman container."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            payload = None
            if args and not isinstance(args[0], (dict, list)):
                payload = args[0]
            elif "args" in kwargs:
                payload = kwargs["args"]

            arg_payload = str(payload).strip() if payload is not None else ""

            print(f"{SANDBOX_EMOJI} [SANDBOX {name}]: {' '.join(cmd)} {arg_payload}".rstrip(), file=sys.stderr)
            return run_podman_tool(name, cmd, arg_payload, f._required_caps if hasattr(f, '_required_caps') else set())
        return wrapper
    return decorator

def run_podman_tool(name: str, base_cmd: List[str], args: str, caps: set) -> Dict[str, Any]:
    """Executes a command inside a Podman container."""
    mount_mode = "rw" if "write" in caps else "ro"
    args_list = shlex.split(args) if args.strip() else []
    tool_argv = list(base_cmd) + args_list

    podman_cmd = [
        "podman", "run", "--rm",
        "--net", "none", # No internet!
        "--workdir", "/workspace",
        "-v", f"{SANDBOX_CONFIG['host_data_dir']}:/workspace:{mount_mode}",
        SANDBOX_CONFIG["image"],
    ] + tool_argv

    try:
        result = subprocess.run(podman_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return {name: result.stdout.strip()}
        else:
            return {name: f"Container Error (Exit {result.returncode}):\n{result.stderr.strip()}"}
    except Exception as exc: 
        return {name: f"Sandbox System Error: {str(exc)}"}

def discover_tools(namespace: Dict[str, Any], module_name: str) -> List[str]:
    """Scans namespace for tools belonging to the current module."""
    return [
        name for name, obj in namespace.items() 
        if getattr(obj, "_is_toolex_tool", False) 
        and getattr(obj, "__module__", None) == module_name
    ]

# --- EVENG LOGGING ---
def emit_invocation(tool_icon: str, **kwargs) -> None:
    """Print one text fragment to stderr:  🤖<icon> key="val" key2="val2"
    Strings are double-quoted; bools become true/false; everything else is repr'd.
    """
    robot = "🤖"
    parts = []
    for k, v in kwargs.items():
        if isinstance(v, str):
            parts.append(f'{k}="{v}"')
        elif isinstance(v, bool):
            parts.append(f"{k}={'true' if v else 'false'}")
        else:
            parts.append(f"{k}={v!r}")
    line = f"{robot}{tool_icon} {' '.join(parts)}"
    print(line, file=sys.stderr, flush=True, end='')

