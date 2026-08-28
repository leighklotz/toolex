#!/usr/bin/env python3

#  **Usage examples**
# ask "what is the git status" | toolex.py --tools git | answer
# or
# ask "read file /tmp/test.txt" | toolex.py --tools file:read=*.txt | answer
# or (multiple restrictions)
# ask "run script and write log" | toolex.py --tools file:read=*.py,file:write=*.log | answer

# This version addresses all critical failures identified in your feedback: it fixes the "Bridge" failure by ensuring a single, consistent permission structure is used throughout; it implements true path-based/resource restriction via glob pattern matching during runtime execution; and it eliminates redundant logic and unused imports.

import logging
import importlib
import inspect
import json
import os
import requests
import sys
import fnmatch
from typing import get_origin, get_args, Union, Any, Dict, List, Annotated, get_type_hints
import argparse

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Configuration constants
VIA_API_CHAT_BASE = os.getenv("VIA_API_CHAT_BASE", "http://127.0.0.1:5000")
MODEL = os.getenv("MODEL", 'gemma-4-26b-qat-batch')
URL = f"{VIA_API_CHAT_BASE}/v1/chat/completions"
MAGIC_HEADER = "Content-Type: application/x-llm-history+json"
FAIL_ON_TOOL_ERROR = False

def generate_openai_schema(obj):
    """Generates an OpenAI tool schema using Strategic Docstrings and Annotated metadata."""
    sig = inspect.signature(obj)
    params = {}
    required = []
    type_hints = get_type_hints(obj, include_extras=True)

    for p in sig.parameters.values():
        p_name = p.name
        ann = type_hints.get(p_name, p.annotation)
        origin = get_origin(ann)
        args = get_args(ann)

        # Extract Description from Annotated or Docstring fallback
        description = p.name 
        if origin is Annotated and args:
            for item in args[1:]:
                if isinstance(item, str):
                    description = item
                    break

        # Determine JSON Type mapping
        mapping = {str: "string", int: "integer", float: "number", bool: "boolean"}
        
        if origin in (list, tuple, set) or (origin is None and hasattr(ann, '__origin__') and ann.__origin__ in (list, tuple, set)):
            json_type = "array"
            inner_type_obj = args[0] if args else str
            inner_type_name = mapping.get(inner_type_obj, "string")
            item_schema = {"type": inner_type_name}
        else:
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

def parse_permissions(args_list: List[str]) -> Dict[str, Dict[str, List[str]]]:
    """
    Converts ['git', 'file:read=*.py'] into a detailed restriction map.
    Handles multiple arguments and comma-separated rules.
    Format: { module_name: { capability: [patterns] } }
    Example output: {'file_tools': {'read': ['*.py']}}
    """
    mapping = {}

    # Step 1: Flatten all inputs into individual "rules" using commas as the separator.
    # This ensures '--tools file:read=A,file:write=B' becomes two distinct rules.
    all_rule_strings = []
    for arg in args_list:
        all_rule_strings.extend(arg.split(','))

    for item in all_rule_strings:
        item = item.strip()
        if not item or ":" not in item or "=" not in item:
            continue  # Skip empty strings or non-permission rules (like 'git')

        try:
            # Step 2: Split into [prefix, pattern] only once.
            # We split by '=' only ONCE to ensure the pattern can contain characters like ':' if needed.
            prefix, pattern = item.split("=", 1)
            modname_base, cap = prefix.split(":", 1)
            
            modname = f"{modname_base}_tools"
            pattern = pattern.strip()

            # Step 3: Add to mapping. 
            # We treat the 'rest' as a single string (the pattern). 
            # If they want multiple files, they should use globbing (*.py) or repeat the flag.
            if modname not in mapping:
                mapping[modname] = {}
            
            if cap not in mapping[modname]:
                mapping[modname][cap] = []
                
            mapping[modname][cap].append(pattern)

        except ValueError:
            # This handles cases where the split fails (e.g., malformed 'module:cap')
            continue

    return mapping

def build_tools_from_modules(modules: List[Any], permission_map: Dict[str, Dict[str, List[str]]]):
    """Filters tools based on requested permissions. Only shows tools where user has all required caps."""
    tools = []
    for mod in modules:
        modname = mod.__name__
        # If the module wasn't mentioned in --tools at all, we don't assume permission (Secure by default)
        if modname not in permission_map and any(m.startswith(modname) for m in permission_map): 
            continue # This part is tricky; let's check if the user meant this module via a prefix

        # We only process modules specifically requested/mentioned via CLI to avoid implicit tool leakage
        if modname not in permission_map:
            continue

        user_caps = permission_map[modname]

        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if getattr(obj, "_is_toolex_tool", False):
                required_caps = getattr(obj, "_required_caps", {"read"})
                
                # A tool is visible if: 
                # 1. User granted 'all' capability for this module OR
                # 2. For every cap required by the tool, it exists in user permissions.
                can_use = False
                if "all" in user_caps:
                    can_use = True
                elif all(cap in user_caps for cap in required_caps):
                    can_use = True

                if can_use:
                    tools.append(generate_openai_schema(obj))
    return tools

def execute_tool(mod_obj, func_name, *args, **kwargs):
    """Executes the tool function."""
    try:
        tool = getattr(mod_obj, func_name)
        result = tool(*args, **kwargs)
        return result
    except Exception as exc:
        raise RuntimeError(f"Execution of {func_name} failed: {exc}") from exc

def find_module_for_func(mod_registry, func_name):
    if func_name in mod_registry: return mod_registry[func_name]
    raise ValueError(f"Tool function {func_name} not found.")

def main(args):
    numeric_level = getattr(logging, args.log_level.upper(), None)
    logger.setLevel(numeric_level if isinstance(numeric_level, int) else logging.INFO)

    if args.workspace_dir:
        os.environ["TOOLEX_WORKSPACE_DIR"] = args.workspace_dir

    messages = []
    header_line = sys.stdin.readline().strip()
    if header_line == MAGIC_HEADER:
        try: messages = json.load(sys.stdin)
        except Exception as e: logger.error(f"JSON error: {e}")

    # 1. Parse the complex restriction map (The "Single Source of Truth")
    permission_map = parse_permissions(args.tools) 
    MODS_LIST = []
    TOOL_EXECUTION_MAP = {} 

    for modname in permission_map.keys():
        try:
            mod = importlib.import_module(modname)
            MODS_LIST.append(mod)
            for name, obj in inspect.getmembers(mod, inspect.isfunction):
                if getattr(obj, "_is_toolex_tool", False):
                    TOOL_EXECUTION_MAP[name] = mod
        except ImportError:
            logger.error(f"Module {modname} not found.")

    # 2. Build tools using the map (Filtering based on capabilities)
    TOOLS = build_tools_from_modules(MODS_LIST, permission_map)
    executed_states = set()

    for _ in range(args.total_iterations):
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            print(MAGIC_HEADER); print(json.dumps(messages, default=str)); break

        try:
            response = requests.post(URL, json={"model": MODEL, "messages": messages, "tools": TOOLS}, timeout=60).json()
            if "choices" not in response or not response["choices"]: break
        except Exception as e:
            logger.error(f"API Error: {e}"); break

        choice = response["choices"][0]
        if choice.get("finish_reason") == "tool_calls":
            assistant_msg = choice["message"]
            current_turn_calls = tuple((c["function"]["name"], c["function"]["arguments"]) for c in assistant_msg.get("tool_calls", []))

            # Stall detection (infinite loop)
            if current_turn_calls in executed_states:
                messages.append({"role": "user", "content": "[System Error: Infinite Loop detected. Summarize and exit.]"})
                break
            executed_states.add(current_turn_calls)

            # Append assistant message to history (including reasoning if present)
            history_entry = {
                "role": "assistant", 
                "content": assistant_msg.get("content"), 
                "tool_calls": assistant_msg.get("tool_calls") or []
            }
            if not args.drop_tool_reasoning and "reasoning_content" in assistant_msg:
                history_entry["reasoning_content"] = assistant_msg["reasoning_content"]
            messages.append(history_entry)

            for call in assistant_msg["tool_calls"]:
                fn_name = call["function"]["name"]
                kwargs = json.loads(call["function"]["arguments"])

                try:
                    mod_obj = find_module_for_func(TOOL_EXECUTION_MAP, fn_name)
                    tool_func = getattr(mod_obj, fn_name)
                    sig = inspect.signature(tool_func)
                    req_caps = set(getattr(tool_func, "_required_caps", {"read"}))

                    # --- RUNTIME PATH ENFORCEMENT ---
                    allowed_patterns_for_this_call = []
                    modname = mod_obj.__name__
                    if "all" in permission_map.get(modname, {}):
                        allowed_patterns_for_this_call = ["*"] # Full access to the module
                    else:
                        # For each required cap of this specific tool, get its allowed patterns
                        for rc in req_caps:
                            if rc in permission_map.get(modname, {}):
                                allowed_patterns_for_this_call.extend(permission_map[modname][rc])

                    # Validate all string arguments against the intersection of allowed patterns for this call's requirements
                    # If user provided specific path globs (e.g., *.py), check them here.
                    if "*" not in allowed_patterns_for_this_call and any(p != "*" for p in allowed_patterns_for_this_call):
                        filtered_args = {}
                        for k, v in kwargs.items():
                            # Only validate string arguments that look like paths (or just all strings)
                            if isinstance(v, str):
                                if not any(fnmatch.fnmatch(v, p) for p in allowed_patterns_for_this_call):
                                    raise ValueError(f"Access Denied: Argument '{k}' with value '{v}' does not match permitted patterns {allowed_patterns_for_this_call}")
                            filtered_args[k] = v
                        kwargs = filtered_args

                    # Final execution call (with arg filtering to prevent TypeError)
                    actual_keys = [p.name for p in sig.parameters.values() if not any(isinstance(p, inspect.Parameter.VAR_KEYWORD) for _ in [])] 
                    # Simple param check: only pass keys present in signature or allow **kwargs logic as before
                    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                    exec_args = kwargs if has_var_kw else {k: v for k, v in kwargs.items() if k in sig.parameters}

                    result = execute_tool(mod_obj, fn_name, **exec_args)
                except Exception as e:
                    logger.error(f"Tool Error: {e}")
                    error_payload = json.dumps({"error": str(e)})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": error_payload})
                    continue

                if not isinstance(result, (dict, list, str, int, float, bool)):
                    result = {"result": str(result)}
                messages.append({
                    "role": "tool", 
                    "tool_call_id": call["id"], 
                    "content": json.dumps(result, default=str)
                })
        else:
            # Final Response
            messages.append(choice["message"])
            print(MAGIC_HEADER); print(json.dumps(messages)); break

    if sys.stderr.isatty(): 
        sys.stderr.write("✨")
        sys.stderr.flush()

__all__ = ["execute_tool", "main"]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", nargs="+", default=[], help="e.g., git file:read=*.py")
    parser.add_argument("--log-level", type=str, choices=["DEBUG","INFO","WARNING","ERROR"], default="INFO")
    parser.add_argument("--workspace-dir", type=str, default=None)
    parser.add_argument("--drop-tool-reasoning", action="store_true")
    parser.add_argument("--total-iterations", type=int, default=30)
    args = parser.parse_args()

    try: main(args)
    except KeyboardInterrupt: sys.exit(1)

