#!/usr/bin/env python3

#  **Usage examples**
# ask "what is the git status " | toolex.py --tools git | answer
# or
# ask "what is the weather in paris" | toolex.py --tools git --tools weather | answer
# or (for specific permissions)
# ask "read file" | toolex.py --tools git:read

import logging
import importlib
import inspect
import json
import os
import requests
import sys
from typing import get_origin, get_args, Union, Any, Dict, List, Annotated, get_type_hints
import argparse

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Configuration
VIA_API_CHAT_BASE = os.getenv("VIA_API_CHAT_BASE", "http://127.0.0.1:5000")
URL = f"{VIA_API_CHAT_BASE}/v1/chat/completions"
MAGIC_HEADER = "Content-Type: application/x-llm-history+json"

def generate_openai_schema(obj):
    """Generates an OpenAI tool schema using Strategic Docstrings and Annotated metadata."""
    sig = inspect.signature(obj)
    params = {}
    required = []
    
    # Get type hints including 'Annotated' extras for semantic descriptions
    type_hints = get_type_hints(obj, include_extras=True)

    for p in sig.parameters.values():
        p_name = p.name
        ann = type_hints.get(p_name, p.annotation)
        origin = get_origin(ann)
        args = get_args(ann)

        # 1. Extract Description (from Annotated or Docstring fallback)
        description = p.name # Default to name if no description found
        if origin is Annotated and args:
            # The first arg of 'Annotated' is the type, subsequent are metadata/descriptions
            for item in args[1:]:
                if isinstance(item, str):
                    description = item
                    break

        # 2. Determine JSON Type (Handling Arrays vs Scalars)
        mapping = {str: "string", int: "integer", float: "number", bool: "boolean"}
        
        if origin in (list, tuple, set) or (origin is None and hasattr(ann, '__origin__') and ann.__origin__ in (list, tuple, set)):
            json_type = "array"
            # Get the inner type from args if available
            inner_type_obj = args[0] if args else str
            inner_type_name = mapping.get(inner_type_obj, "string")
            item_schema = {"type": inner_type_name}
        else:
            # Map Python types to JSON Types
            real_type = origin if origin in mapping else ann
            json_type = mapping.get(real_type, "string")
            item_schema = None

        params[p_name] = {
            "description": description,
            **({"type": json_type, "items": item_schema} if json_type == "array" else {"type": json_type})
        }

        if p.default is inspect.Parameter.empty:
            required.append(p_name)

    return {
        "type": "function",
        "function": {
            "name": obj.__name__,
            "description": (inspect.getdoc(obj) or "").strip(),
            "parameters": {
                "type": "object", 
                "properties": params, 
                "required": required
            },
        },
    }

def build_tools_from_module(module):
    """Return a list of OpenAI‑style tool dicts from a module."""
    tools = []
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if getattr(obj, "_is_toolex_tool", False):
            tools.append(generate_openai_schema(obj))
    return tools

def build_tools_filtered(modules: List[Any], permission_map: Dict[str, set]):
    """Filters tools based on requested permissions."""
    tools = []
    for mod in modules:
        modname = mod.__name__
        granted_perms = permission_map.get(modname, {"read"}) 

        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if getattr(obj, "_is_toolex_tool", False):
                required = getattr(obj, "_required_caps", {"read"})
                user_perms = permission_map.get(modname, set())

                # Allow if user has 'all' OR the tool's requirement is a subset of what was granted
                if "all" in user_perms or required.issubset(user_perms):
                    tools.append(generate_openai_schema(obj))
    return tools

def tool_to_module_name(user_tool_id: str) -> tuple[str, str]:
    """
    Converts 'foo' or 'foo:read' into ('foo_tools', permission).
    If no permission is present, default to ":read".
    User must include ":all" or ":write" etc over override readonly.
    """
    if ":" in user_tool_id:
        base_name, perm = user_tool_id.split(":", 1)
    else:
        base_name, perm = user_tool_id, "read"
    return f"{base_name}_tools", perm

def execute_tool(tool_module_obj, tool_func_name: str, *args, **kwargs):
    """Execute a registered tool by name from its module."""
    try:
        tool = getattr(tool_module_obj, tool_func_name)
    except AttributeError as e:
        raise ValueError(f"Function {tool_func_name} not found in module.") from e

    if not callable(tool):
        raise ValueError(f"Attribute {tool_func_name} is not a function")

    try:
        result = tool(*args, **kwargs)
    except Exception as exc:
        raise RuntimeError(f"Execution of tool {tool_func_name!r} failed: {exc}") from exc

    return result

def find_module_for_func(mod_registry: Dict[str, Any], func_name: str):
    """Helper to locate which loaded module contains a specific function name."""
    # Use the mapping built during execution setup to avoid global state dependency issues
    if func_name in mod_registry:
        return mod_registry[func_name]
    raise ValueError(f"Could not find registered tool function: {func_name}")

def parse_permissions(args_list):
    """
    Converts ['foo', 'git:read'] into:
    { 'foo_tools': {'all'}, 'git_tools': {'read'} }
    Errors if tool or permission does not exist.
    """
    mapping = {}
    for item in args_list:
        modname, perm = tool_to_module_name(item)
        if modname not in mapping:
            spec = importlib.util.find_spec(modname)
            if spec is None:
                logger.error(f"Tool {item} does not exist")
                sys.exit(1)
            mapping[modname] = set()

        # We need to load the module temporarily to validate perms if it's a specific perm request
        module = importlib.import_module(modname)
        valid_perms = set()
        for name, obj in inspect.getmembers(module, inspect.isfunction):
            if getattr(obj, "_is_toolex_tool", False):
                caps = getattr(obj, "_required_caps", {"read"})
                valid_perms.update(caps if isinstance(caps, (set, list)) else {caps})

        if perm == "all":
            mapping[modname].add("all")
        elif perm in valid_perms:
            mapping[modname].add(perm)
        else:
            logger.error(f"Permission '{perm}' does not exist for tool {item}")
            sys.exit(1)

    return mapping

def main(args):
    # Set logging level from argument
    numeric_level = getattr(logging, args.log_level.upper(), None)
    if isinstance(numeric_level, int):
        logger.setLevel(numeric_level)
    else:
        raise ValueError(f"Invalid log level: {args.log_level}")

    # Determine initial messages from stdin
    messages = []
    header_line = sys.stdin.readline().strip()
    if header_line and header_line == MAGIC_HEADER:
        try:
            messages = json.load(sys.stdin)
        except Exception as e:
            logger.error(f"Failed to parse JSON from stdin: {e}")

    # Permission mapping and module loading logic
    permission_map = parse_permissions(args.tools) 
    MODS_LIST = []
    TOOL_EXECUTION_MAP = {} # Map function name -> module object for fast lookup during loop

    for modname in permission_map.keys():
        try:
            mod = importlib.import_module(modname)
            MODS_LIST.append(mod)
            # Register all functions in this module to the execution map if they are tools
            for name, obj in inspect.getmembers(mod, inspect.isfunction):
                if getattr(obj, "_is_toolex_tool", False):
                    TOOL_EXECUTION_MAP[name] = mod
        except ImportError as e:
            raise ImportError(f"Tool module {modname} does not exist") from e

    TOOLS = build_tools_filtered(MODS_LIST, permission_map)
    
    TOTAL_ITERATIONS = 10
    for i in range(TOTAL_ITERATIONS):
        # If assistant is done thinking and has no tool calls, it's a final response
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            print(MAGIC_HEADER)
            print(json.dumps(messages, default=str))
            break

        try:
            j = {"messages": messages, "tools": TOOLS}
            _ui_status("✨")
            response = requests.post(URL, json=j).json()
            if "choices" not in response or len(response["choices"]) == 0:
                logger.error(f"Unexpected response format: {response}")
                break
        except Exception as e:
            logger.error(f"Request failed: {e}")
            break

        choice = response["choices"][0]
        if choice.get("finish_reason") == "tool_calls":
            assistant_msg = choice["message"]
            messages.append({
                "role": "assistant",
                "content": assistant_msg.get("content"),
                "tool_calls": assistant_msg["tool_calls"],
            })

            for call in assistant_msg["tool_calls"]:
                fn = call["function"]
                name, arguments = fn["name"], json.loads(fn["arguments"])

                try:
                    mod_obj = find_module_for_func(TOOL_EXECUTION_MAP, name)
                    result = execute_tool(mod_obj, name, **arguments)
                except Exception as e:
                    logger.error(f"Tool execution error: {e}")
                    # Append error to messages so LLM knows why it failed instead of crashing
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps({"error": str(e)}),
                    })
                    continue

                if not isinstance(result, (dict, list, str, int, float, bool)):
                    result = {"result": str(result)}

                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, default=str),
                })
        else:
            # Final response from Assistant
            messages.append(choice["message"])
            print(MAGIC_HEADER)
            print(json.dumps(messages))
            break

def _ui_status(icon: str):
    if sys.stderr.isatty():
        sys.stderr.write(icon)
        sys.stderr.flush()

__all__ = [
    "execute_tool",
    "main",
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tools",
        nargs="+", 
        default=[],
        help="List of tools/permissions (e.g., --tools git weather:read)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level (default: INFO)",
    )
    args = parser.parse_args()
    main(args)
