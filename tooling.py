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
    "host_data_dir": "/home/klotz/wip/toolex", # What the LLM sees as /workspace
}

# Logging
logger = logging.getLogger(__name__)

def tool(capabilities: Union[str, List[str], Callable] = None):
    """
    Decorator that marks a function as an LLM-callable tool with specific permissions.
    Handles various calling styles for flexibility:
        @tool                 -> No capabilities (defaults to empty set)
        @tool("write")         -> Single capability string
        @tool(["read", "exec"]) -> Multiple capabilities list/tuple
        @tool()                -> Explicitly no capabilities
    """
    # Case 1: User used @tool (no parentheses). 'capabilities' is the function being decorated.
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
            
            print(f"🚀{' '.join(cmd)} {arg_payload}", file=sys.stderr)
            return run_bash_tool(name, cmd, arg_payload)
        return wrapper
    return decorator

def run_bash_tool(name: str, cmd: List[str], args: Optional[str] = "") -> Dict[str, Any]:
    """Runs the command directly on host machine."""
    args_str = (args or "").strip()
    full_cmd = list(cmd)
    if args_str:
        full_cmd += shlex.split(args_str)
        
    try:
        output = subprocess.check_output(
            full_cmd, cwd=os.getcwd(), stderr=subprocess.STDOUT, text=True,                
        )
        return {name: output.strip()}
    except Exception as exc: # Simplified for brevity
        if True:
            raise
        else:
            print(f"🚀{' '.join(cmd)} {arg_payload} {str(exc)}", file=sys.stderr)
            return {name: f"Error: {str(exc)}"}

# --- PODMAN SANDBOX ---
def sandbox_wrap(name: str, cmd: List[str]):
    """Wraps a function so it executes inside a Podman container."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            cmd_label = " ".join(cmd)
            
            # FIX: Check positional args OR keyword 'args'
            payload = None
            if args and not isinstance(args[0], (dict, list)):
                payload = args[0]
            elif "args" in kwargs:
                payload = kwargs["args"]

            arg_payload = str(payload).strip() if payload is not None else ""

            print(f"{SANDBOX_EMOJI}️[SANDBOX {name}]: {' '.join(cmd)}{arg_payload}", file=sys.stderr)
            return run_podman_tool(name, cmd, arg_payload, f._required_caps if hasattr(f, '_required_caps') else set())
        return wrapper
    return decorator

def run_podman_tool(name: str, base_cmd: List[str], args: str, caps: set) -> Dict[str, Any]:
    """Executes a command inside a Podman container."""
    # Determine if we can write to the mounted directory or just read it
    mount_mode = "rw" if "write" in caps else "ro"
    
    full_subcommand_str = f"{' '.join(base_cmd)} {args}"

    podman_cmd = [
        "podman", "run", "--rm",
        "--net", "none", # No internet!
        "-v", f"{SANDBOX_CONFIG['host_data_dir']}:/workspace:{mount_mode}",
        SANDBOX_CONFIG["image"],
        "/bin/bash", "-c", 
        f"cd /workspace && {full_subcommand_str}"
    ]
    # logger.info(f"{podman_cmd=}")

    try:
        output = subprocess.check_output(
            podman_cmd, stderr=subprocess.STDOUT, text=True
        )
        return {name: output.strip()}
    except subprocess.CalledProcessError as exc:
        return {name: f"Container Error:\n{exc.output}"}
    except Exception as exc: 
        return {name: f"Sandbox System Error: {str(exc)}"}

def discover_tools(namespace: Dict[str, Any], module_name: str) -> List[str]:
    """Scans namespace for tools belonging to the current module."""
    return [
        name for name, obj in namespace.items() 
        if getattr(obj, "_is_toolex_tool", False) 
        and getattr(obj, "__module__", None) == module_name
    ]
