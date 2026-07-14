#!/usr/bin/env python
from __future__ import annotations

import os
import sys
import subprocess
import shlex
from typing import Dict, List, Optional, Any, Union, Callable
from functools import wraps

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
        """The actual decorator that attaches metadata to the function."""
        # Determine if we are using a list/tuple or splitting a string
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

def bash_wrap(name: str, cmd: List[str]):
    """Wraps a function into a standard command-execution pattern for logging."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            cmd_label = " ".join(cmd)
            # Print to stderr so it doesn't pollute the JSON/LLM stdout stream
            print(f"🤖 Executing: {cmd_label} with args={args}", file=sys.stderr)
            return run_bash_tool(name, cmd, str(args[0]) if args else "")
        return wrapper
    return decorator

def run_bash_tool(name: str, cmd: List[str], args: Optional[str] = "") -> Dict[str, Any]:
    """Runs a shell command and returns output as a dictionary."""
    # Ensure we have a clean string to work with
    args_str = (args or "").strip()
    
    # Create a new list so we don't mutate the original 'cmd' list passed in
    full_cmd = list(cmd)
    if args_str:
        # Use shlex to split arguments correctly (respecting quotes)
        full_cmd += shlex.split(args_str)
        
    try:
        output = subprocess.check_output(
            full_cmd,
            cwd=os.getcwd(),          
            stderr=subprocess.STDOUT,  
            text=True,                
        )
        return {name: output.strip()}
    except subprocess.CalledProcessError as exc:
        # Return detailed error for LLM debugging (exit code + stderr/stdout content)
        error_msg = f"Command Error (Exit Code {exc.returncode}):\n{exc.output}"
        return {name: error_msg}
    except Exception as exc: 
        # Catch-all for system errors like 'file not found' or permission issues
        return {name: f"System Error: {str(exc)}"}

def discover_tools(namespace: Dict[str, Any], module_name: str) -> List[str]:
    """Scans namespace for tools belonging to the current module."""
    return [
        name for name, obj in namespace.items() 
        if getattr(obj, "_is_toolex_tool", False) 
        and getattr(obj, "__module__", None) == module_name
    ]
