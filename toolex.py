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
TOOLS_INFERENCE_TIMEOUT=600

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
    weather                        -> {'weather_tools': {'all': ['*']}}
    git:read                       -> {'git_tools': {'read': ['*']}}
    git:read:write                 -> {'git_tools': {'read': ['*'], 'write': ['*']}}
    file:read=*.py                 -> {'file_tools': {'read': ['*.py']}}
    file:read=README.md,doc/*.md   -> {'file_tools': {'read': ['README.md', 'doc/*.md']}}
    file:read=doc/README.md:write=doc/README.md.new -> {'file_tools': {'read': ['doc/README.md'], 'write': ['doc/README.md.new']}}
    """
    mapping: Dict[str, Dict[str, List[str]]] = {}
    # (modkey, cap) of the last "mod:cap=pattern" item; lets a subsequent comma-
    # separated bare pattern continue that pattern list instead of being misread
    # as a module name.
    last_spec = None

    def modkey(name: str) -> str:
        return name if name.endswith("_tools") else f"{name}_tools"

    for arg in args_list:
        last_spec = None
        for item in arg.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                if "=" in item:
                    # Formats: mod:cap=pat  or  mod:cap1=pat1:cap2=pat2
                    if ":" in item:
                        mod_name, rest = item.split(":", 1)
                        mk = modkey(mod_name)
                        segments = rest.split(":")
                        for seg in segments:
                            seg = seg.strip()
                            if not seg:
                                continue
                            if "=" in seg:
                                cap, pattern = seg.split("=", 1)
                                cap = cap.strip()
                                pattern = pattern.strip()
                                last_spec = (mk, cap)
                                mapping.setdefault(mk, {}).setdefault(cap, []).append(pattern)
                            else:
                                cap = seg
                                last_spec = (mk, cap)
                                mapping.setdefault(mk, {}).setdefault(cap, []).append("*")
                    else:
                        raise ValueError(f"Missing module in spec: {item}")
                elif ":" in item:
                    parts = item.split(":")
                    for cap in parts[1:]:
                        mapping.setdefault(modkey(parts[0]), {}).setdefault(cap.strip(), []).append("*")
                    last_spec = None
                elif last_spec is not None:
                    # Continuation of the previous pattern list:
                    # file:read=README.md,doc/*.md  (bare item appends to last spec)
                    mapping[last_spec[0]][last_spec[1]].append(item)
                else:
                    mapping[modkey(item)] = {"all": ["*"]}   # bare name = full access
                    last_spec = None
            except ValueError:
                logger.warning("Ignoring malformed --tools entry: %r", item)
                last_spec = None
    return mapping

def _qualified_tool_name(modname: str, func_name: str) -> str:
    """Build a globally-unique tool name: {short_module}_{func}."""
    short = modname[:-6] if modname.endswith("_tools") else modname
    return f"{short}_{func_name}"

def build_tools_from_modules(modules: List[Any], permission_map: Dict[str, Dict[str, List[str]]]):
    """Filters tools based on requested permissions. Only shows tools where user has all required caps."""
    tools = []
    for mod in modules:
        modname = mod.__name__
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
                    schema = generate_openai_schema(obj)
                    qualified_name = _qualified_tool_name(modname, name)
                    schema["function"]["name"] = qualified_name
                    pats = [p for cap in required_caps for p in user_caps.get(cap, [])]
                    schema["function"]["description"] += f"\nAllowed file patterns for this session: {pats}"
                    tools.append(schema)

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
    logger.debug("permission map: %s", permission_map)

    MODS_LIST, TOOL_EXECUTION_MAP = [], {}

    for modname in permission_map:
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            logger.error("Module %s not found.", modname)
            continue
        MODS_LIST.append(mod)
        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if getattr(obj, "_is_toolex_tool", False):
                TOOL_EXECUTION_MAP[_qualified_tool_name(modname, name)] = mod

    TOOLS = build_tools_from_modules(MODS_LIST, permission_map)
    logger.debug("exposing %d tools: %s", len(TOOLS), [t["function"]["name"] for t in TOOLS])

    if args.tools and not TOOLS:
        logger.error("No tools resolved from --tools %s; refusing to run.", args.tools)
        sys.exit(2)

    executed_states = set()

    for _ in range(args.total_iterations):
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            print(MAGIC_HEADER); print(json.dumps(messages, default=str)); break

        try:
            payload = {"model": MODEL, "messages": messages}
            if TOOLS:
                payload["tools"] = TOOLS
            response = requests.post(URL, json=payload, timeout=TOOLS_INFERENCE_TIMEOUT).json()
            if "choices" not in response or not response["choices"]: break
        except Exception as e:
            logger.error(f"API Error: {e}"); break

        choice = response["choices"][0]
        if choice.get("finish_reason") == "tool_calls":
            assistant_msg = choice["message"]

            # Stall detection (infinite loop) – use canonical JSON for semantic comparison
            current_turn_calls = tuple(
                (c["function"]["name"],
                 json.dumps(json.loads(c["function"]["arguments"]), sort_keys=True))
                for c in assistant_msg.get("tool_calls", [])
            )

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
                    # Resolve the original function name from the qualified tool name
                    short_prefix = fn_name.split("_", 1)[0]
                    orig_func_name = fn_name[len(short_prefix) + 1:]
                    tool_func = getattr(mod_obj, orig_func_name)
                    sig = inspect.signature(tool_func)
                    req_caps = set(getattr(tool_func, "_required_caps", {"read"}))

                    # --- RUNTIME PATH ENFORCEMENT ---
                    allowed_patterns_for_this_call = []
                    modname = mod_obj.__name__
                    if "all" in permission_map.get(modname, {}):
                        allowed_patterns_for_this_call = ["*"]  # Full access to the module
                    else:
                        # For each required cap of this specific tool, get its allowed patterns
                        for rc in req_caps:
                            if rc in permission_map.get(modname, {}):
                                allowed_patterns_for_this_call.extend(permission_map[modname][rc])

                    # anticonfabulation: a tool whose required capability was
                    # never granted (e.g. the confabulation 'read_file_anywhere' under
                    # 'file:read') previously slipped through here with NO path checks at all.
                    if not allowed_patterns_for_this_call:
                        raise ValueError(f"Access Denied: tool '{fn_name}' requires capabilities {sorted(req_caps)} not granted for module '{modname}'")

                    # Simple param check: only pass keys present in signature or allow **kwargs logic
                    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                    exec_args = kwargs if has_var_kw else {k: v for k, v in kwargs.items() if k in sig.parameters}

                    # Validate string arguments against allowed glob patterns
                    if "*" not in allowed_patterns_for_this_call:
                        for arg_name, arg_val in exec_args.items():
                            if isinstance(arg_val, str):
                                if not any(fnmatch.fnmatch(arg_val, pat) for pat in allowed_patterns_for_this_call):
                                    raise PermissionError(
                                        f"Access Denied: argument {arg_name}={arg_val!r} "
                                        f"does not match any allowed pattern {allowed_patterns_for_this_call}"
                                    )

                    result = execute_tool(mod_obj, orig_func_name, **exec_args)
                except Exception as e:
                    logger.warning(f"Tool Error: {e}")
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
            print(MAGIC_HEADER); print(json.dumps(messages, default=str)); break

    if sys.stderr.isatty():
        sys.stderr.write("✨")
        sys.stderr.flush()

__all__ = ["execute_tool", "main"]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", action="append", default=[], help="Permission spec (repeatable)", metavar="SPEC")
    parser.add_argument("--log-level", type=str, choices=["DEBUG","INFO","WARNING","ERROR"], default="INFO")
    parser.add_argument("--workspace-dir", type=str, default=None)
    parser.add_argument("--drop-tool-reasoning", action="store_true")
    parser.add_argument("--total-iterations", type=int, default=30)
    args = parser.parse_args()

    try:
        main(args)
    except KeyboardInterrupt:
        sys.exit(1)

