#!/usr/bin/env python3

#  **Usage examples**
# ask "what is the git status " toolex.py --tools git | answer
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
from typing import get_origin, get_args, Union
import argparse

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARN)

# Configuration
VIA_API_CHAT_BASE = os.getenv("VIA_API_CHAT_BASE", "http://127.0.0.1:5000")
URL = f"{VIA_API_CHAT_BASE}/v1/chat/completions"
MAGIC_HEADER = "Content-Type: application/x-llm-history+json"


def _array_type_from_annotation(ann):
    """Return the inner type of List[T], Optional[List[T]], etc."""
    if get_origin(ann) is Union:
        for a in get_args(ann):
            if get_origin(a) in (list, tuple, set):
                return get_args(a)[0]
    elif get_origin(ann) in (list, tuple, set):
        return get_args(ann)[0]
    return None


def generate_openai_schema(obj):
    """Generate the OpenAI tool dictionary for a given function object."""
    sig = inspect.signature(obj)
    params = {}
    required = []

    for p in sig.parameters.values(): # Fixed: parameters is an attribute, not method
        item_type = _array_type_from_annotation(p.annotation)
        if item_type:
            # list/tuple/set → array of simple types
            attr = {str: "string", int: "integer", float: "number"}.get(
                item_type, "string"
            )
            params[p.name] = {
                "type": "array",
                "items": {"type": attr},
                "description": p.name,
            }
        else:
            # singular value
            attr = {str: "string", int: "integer", float: "number"}.get(
                p.annotation, "string"
            )
            params[p.name] = {"type": attr, "description": p.name}

        if p.default is inspect._empty:
            required.append(p.name)

    return {
        "type": "function",
        "function": {
            "name": obj.__name__,
            "description": (obj.__doc__ or "").strip(),
            "parameters": {
                "type": "object",
                "properties": params,
                "required": required,
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


def tool_to_module_name(user_tool_id: str) -> tuple[str, str]:
    """
    Converts 'foo' or 'foo:read' into ('foo_tools', permission).
    If no permission is present, default to ":read".
    User must include ":all" or ":write" or ":exec" etc over override readonly.
    """
    if ":" in user_tool_id:
        base_name, perm = user_tool_id.split(":", 1)
    else:
        base_name, perm = user_tool_id, "read"
    return f"{base_name}_tools", perm

# execute_tool – the heart of the question
def execute_tool(tool_module_obj, tool_func_name: str, *args, **kwargs):
    """
    Execute a registered tool by name from its module.
    Note: We pass the module object to avoid global state dependency on MODULES list if possible.
    """
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

    logger.debug(
        "execute_tool('%s') → %s (args=%s, kwargs=%s)",
        tool_func_name,
        json.dumps(result, default=str),
        args,
        kwargs,
    )

    return result


def main(args):
    # Set logging level from argument
    numeric_level = getattr(logging, args.log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {args.log_level}")
    logger.setLevel(numeric_level)

    # Determine initial messages
    messages = []

    # Expect the magic header present at the start of the string
    header_line = sys.stdin.readline().strip()
    try:
        if header_line:
            logger.debug(f"{header_line=}\n")
            messages = json.load(sys.stdin)
            logger.debug(f"Loaded message history from stdin via --pipe: {messages=}")
    except Exception:
        logger.error(
            f"Failed to parse JSON from stdin: {header_line=}",
            exc_info=True,
        )

    # Permission mapping and module loading logic
    global MODULES, TOOLS
    permission_map = parse_permissions(args.tools) 
    
    MODULES = []
    for modname in permission_map.keys():
        try:
            mod = importlib.import_module(modname)
            MODULES.append(mod)
        except ImportError as e:
            raise ImportError(f"Tool module {modname} does not exist") from e

    TOOLS = build_tools_filtered(MODULES, permission_map)
    
    # We need to keep track of which module belongs to which user-facing tool ID 
    # for the execute_tool step later.
    # In this implementation, we'll use a map in global scope or pass it down.
    # For simplicity with existing structure:
    global TOOL_EXECUTION_MAP
    TOOL_EXECUTION_MAP = {} # Map tool_name -> module_obj

    TOTAL_ITERATIONS = 10
    for i in range(TOTAL_ITERATIONS):
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            print(MAGIC_HEADER)
            print(json.dumps(messages, default=str))

        try:
            j = {"messages": messages, "tools": TOOLS}
            response = requests.post(URL, json=j).json()
            logger.debug(f"inference: {j}")
        except Exception as e:
            logger.error(f"Request failed: {e}")
            break

        if "choices" not in response:
            logger.error(f"Unexpected response format: {response}")
            break

        choice = response["choices"][0]

        if choice.get("finish_reason") == "tool_calls":
            assistant_msg = choice["message"]
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_msg.get("content"),
                    "tool_calls": assistant_msg["tool_calls"],
                }
            )

            for call in assistant_msg["tool_calls"]:
                fn = call["function"]
                name, arguments = fn["name"], json.loads(fn["arguments"])
                logger.debug(f"Calling {name}({arguments})")

                # Find the module and tool via mapping logic
                # Since tools in OpenAI schema are just function names, 
                # we need to find which module they came from.
                found_mod = None
                for mod in MODULES:
                    if hasattr(mod, name): # Simple check; ideally use TOOL_EXECUTION_MAP built during build_tools_filtered
                         found_mod = mod
                         break
                
                # Refined search logic to handle the module/name distinction
                try:
                    result = execute_tool(find_module_for_func(name), name, **arguments)
                except Exception as e:
                    logger.error(f"Tool execution error: {e}")
                    raise e

                if not isinstance(result, (dict, list, str, int, float, bool)):
                    result = {"result": str(result)}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, default=str),
                    }
                )
        else:
            # print the full JSON history.
            messages.append(choice["message"])
            print(MAGIC_HEADER)
            print(json.dumps(messages))
            break


def find_module_for_func(func_name):
    """Helper to locate which loaded module contains a specific function name."""
    # In a production environment, one would maintain a registry of func -> module
    for mod in MODULES:
        if hasattr(mod, func_name) and getattr(mod, func_name, None).__name__ == func_name:
            return mod
    raise ValueError(f"Could not find module for function {func_name}")


def parse_permissions(args_list):
    """
    Converts ['foo', 'git:read'] into:
    { 'foo_tool': {'all'}, 'git_tool': {'read'} }
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

        if perm == "all":
            mapping[modname].add("all")
        else:
            # Validate permission exists by checking the module's tool functions
            module = importlib.import_module(modname)
            valid_perms = set()
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if getattr(obj, "_is_toolex_tool", False):
                    # Get the tags defined by @tool(...) via _required_caps
                    caps = getattr(obj, "_required_caps", {"read"})
                    valid_perms.update(caps if isinstance(caps, (set, list)) else {caps})
            
            if perm not in valid_perms:
                logger.error(f"Permission '{perm}' does not exist for tool {item}")
                sys.exit(1)
            mapping[modname].add(perm)

    return mapping

def build_tools_filtered(modules, permission_map):
    """
    If perOnly includes tools where func._required_caps <= granted_permissions.
    """
    tools = []
    for mod in modules:
        modname = mod.__name__
        # Get the set of permissions for this module
        granted_permissions = permission_map.get(modname, set())
        
        logger.debug(f"Checking mod={modname}, granted_perms={granted_permissions}")

        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if getattr(obj, "_is_toolex_tool", False):
                # The tool's specific requirement (should be a set)
                required = getattr(obj, "_required_caps", {"read"})
                
                # FIX: Check if "all" is IN the set, OR check subset relationship
                if "all" in granted_permissions or required.issubset(granted_permissions):
                    schema = generate_openai_schema(obj) # Ensure this function exists in your scope
                    tools.append(schema)
    return tools


__all__ = [
    "execute_tool",
    "main",
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tools",
        nargs="+",  # Changed from action="append" to nargs="+"
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
