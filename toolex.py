#!/usr/bin/env python3

#  **Usage examples**
# ask "what is the git status " | toolex.py --tools git | answer
# or
# ask "what is the weather in paris" | toolex.py --tools git --tools weather | answer
# or (for specific permissions)
# ask "read file" | toolex.py --tools git

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
MODEL = os.getenv("MODEL", 'gemma-4-26b-qat-batch')
URL = f"{VIA_API_CHAT_BASE}/v1/chat/completions"
MAGIC_HEADER = "Content-Type: application/x-llm-history+json"
FAIL_ON_TOOL_ERROR = False

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

def build_tools_from_modules(modules: List[Any], permission_map: Dict[str, dict]):
    """Filters tools based on requested permissions and injects path constraints
    from 'module:capability=glob' grants into the schema descriptions."""
    tools = []
    for mod in modules:
        modname = mod.__name__
        spec = permission_map.get(modname, {})
        granted_caps = spec.get("caps", set())
        patterns = sorted({p for plist in spec.get("patterns", {}).values() for p in plist})

        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if getattr(obj, "_is_toolex_tool", False):
                required = getattr(obj, "_required_caps", {"read"})
                # Allow if user has 'all' OR the tool's requirement is a subset of what was granted
                if "all" in granted_caps or required.issubset(granted_caps):
                    schema = generate_openai_schema(obj)
                    if patterns:
                        constraint = (
                            " You may only access paths matching these glob patterns "
                            f"(relative to the working directory): {', '.join(patterns)}."
                        )
                        schema["function"]["description"] += constraint
                        for prop in schema["function"]["parameters"]["properties"].values():
                            prop["description"] += constraint
                    tools.append(schema)
    return tools

def set_permitted_patterns_env(permission_map: Dict[str, dict], modname: str, required_caps) -> None:
    """
    Export the path constraints that apply to the tool about to run as
    TOOLEX_PERMITTED_PATH_PATTERNS (JSON list of glob patterns relative to the
    workspace). Tool modules that enforce per-file access (file_tools.py) read
    this. Unconstrained grants unset the variable.
    """
    spec = permission_map.get(modname) or {}
    granted = spec.get("caps", set())
    patterns_map = spec.get("patterns", {})
    caps = required_caps if isinstance(required_caps, (set, list)) else {required_caps}

    patterns: List[str] = []
    if "all" not in granted:
        for cap in caps:
            patterns.extend(patterns_map.get(cap, []))

    if patterns:
        os.environ["TOOLEX_PERMITTED_PATH_PATTERNS"] = json.dumps(sorted(set(patterns)))
    else:
        os.environ.pop("TOOLEX_PERMITTED_PATH_PATTERNS", None)


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
    if func_name in mod_registry:
        return mod_registry[func_name]
    raise ValueError(f"Could not find registered tool function: {func_name}")

def parse_permissions(args_list):
    """
    Converts CLI tool specs into a permission map.

    Supported spec forms (specs for the same module are merged):
        mod                     grant 'read' (default), unconstrained
        mod:cap                 grant capability `cap`, unconstrained
        mod:cap=pat[,pat...]    grant capability `cap` limited to the given glob
                                patterns (relative to the workspace; no colons in patterns)
        mod:read=a.md:write     several capability specs after one module name
        mod:all                 grant every capability the module defines, unconstrained

    Returns:
        { 'file_tools': {
              'caps': {'read'},                     # granted capabilities
              'patterns': {'read': ['README.md']},  # only for constrained caps
          } }
    """
    mapping = {}

    for item in args_list:
        if ":" in item:
            base_name, *cap_specs = item.split(":")
        else:
            base_name, cap_specs = item, []

        modname = f"{base_name}_tools"

        if modname not in mapping:
            spec = importlib.util.find_spec(modname)
            if spec is None:
                logger.error(f"Tool module '{modname}' does not exist")
                sys.exit(1)
            module = importlib.import_module(modname)
            valid_perms = set()
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if getattr(obj, "_is_toolex_tool", False):
                    caps = getattr(obj, "_required_caps", {"read"})
                    valid_perms.update(caps if isinstance(caps, (set, list)) else {caps})
            mapping[modname] = {"caps": set(), "patterns": {}, "valid": valid_perms}

        entry = mapping[modname]

        if not cap_specs:
            # Bare module name keeps the historical default: unconstrained 'read'.
            if "read" not in entry["valid"]:
                logger.error(f"Permission 'read' does not exist for tool {base_name}")
                sys.exit(1)
            entry["caps"].add("read")
            continue

        for cap_spec in cap_specs:
            if "=" in cap_spec:
                cap, _, pattern_str = cap_spec.partition("=")
                patterns = [p for p in pattern_str.split(",") if p]
                if not patterns:
                    logger.error(f"Permission '{cap_spec}' has '=' but no patterns")
                    sys.exit(1)
            else:
                cap, patterns = cap_spec, None

            if cap == "all":
                if patterns is not None:
                    logger.warning(f"'all' ignores pattern constraints in '{cap_spec}'")
                entry["caps"] |= entry["valid"]
                continue

            if cap not in entry["valid"]:
                logger.error(f"Permission '{cap}' does not exist for tool {base_name}")
                sys.exit(1)

            entry["caps"].add(cap)
            if patterns is not None:
                entry["patterns"].setdefault(cap, []).extend(patterns)

    for entry in mapping.values():
        entry.pop("valid", None)
    return mapping


def main(args):
    # Set logging level from argument
    numeric_level = getattr(logging, args.log_level.upper(), None)
    if isinstance(numeric_level, int):
        logger.setLevel(numeric_level)
    else:
        raise ValueError(f"Invalid log level: {args.log_level}")

    # Set workspace directory for sandbox tools before any tool modules are loaded
    if args.workspace_dir:
        os.environ["TOOLEX_WORKSPACE_DIR"] = args.workspace_dir

    # Determine initial messages from stdin
    messages = []
    header_line = sys.stdin.readline().strip()
    if header_line and header_line == MAGIC_HEADER:
        try:
            messages = json.load(sys.stdin)
        except Exception as e:
            logger.error(f"Failed to parse JSON from stdin: {e}")

    # --tools may be repeated and each occurrence carries one or more specs;
    # flatten [[a, b], [c]] -> [a, b, c]
    tools_list = [spec for group in args.tools for spec in group]

    # Permission mapping and module loading logic
    permission_map = parse_permissions(tools_list)
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

    TOOLS = build_tools_from_modules(MODS_LIST, permission_map)
    executed_states = set()
    for i in range(args.total_iterations):
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
            j = {"model": MODEL, "messages": messages, "tools": TOOLS}
            _ui_status("✨")
            logger.debug(f"requests.post {URL=}")
            response = requests.post(URL, json=j).json()
            logger.debug(f"request={j} response={response}")
            if "choices" not in response or len(response["choices"]) == 0:
                logger.error(f"Unexpected response format: {response}")
                break
        except Exception as e:
            logger.error(f"Request failed: {e}")
            break

        choice = response["choices"][0]
        if choice.get("finish_reason") == "tool_calls":
            assistant_msg = choice["message"]

            # --- LOOP BREAKER: State Extraction & Check ---
            current_turn_calls = tuple(
                (call["function"]["name"], call["function"]["arguments"])
                for call in assistant_msg.get("tool_calls", [])
            )

            if current_turn_calls in executed_states:
                logger.warning(f"Stall detected: LLM generated identical tool arguments as a previous turn. {current_turn_calls=}")

                messages.append({
                    "role": "user",
                    "content": "[System Error: Infinite execution loop terminated. You are passing identical parameters back to the same tool. Abandon this loop and summarize your progress immediately.]"
                })

                try:
                    _ui_status("✨")
                    final_resp = requests.post(URL, json={"model": MODEL, "messages": messages}).json()
                    if "choices" in final_resp and len(final_resp["choices"]) > 0:
                        messages.append(final_resp["choices"][0]["message"])
                except Exception as e:
                    logger.error(f"Failed to fetch graceful loop exit response: {e}")

                print(MAGIC_HEADER)
                print(json.dumps(messages, default=str))
                break

            executed_states.add(current_turn_calls)

            # Build the message to append back into history
            history_entry = {
                "role": "assistant",
                "content": assistant_msg.get("content"),
                "tool_calls": assistant_msg.get("tool_calls") or [],
            }

            # If not drop_tool_reasoning, maintain logic/context chain
            if not args.drop_tool_reasoning and "reasoning_content" in assistant_msg:
                history_entry["reasoning_content"] = assistant_msg["reasoning_content"]
                logger.debug("Appended reasoning content to history.")

            messages.append(history_entry)

            for call in assistant_msg["tool_calls"]:
                fn = call["function"]
                name, arguments = fn["name"], json.loads(fn["arguments"])

                try:
                    # Get the module that contains this function
                    mod_obj = find_module_for_func(TOOL_EXECUTION_MAP, name)

                    # Get the actual function object from that module
                    tool_func = getattr(mod_obj, name)

                    # Export this tool's path constraints (may unset the env var)
                    set_permitted_patterns_env(
                        permission_map,
                        mod_obj.__name__,
                        getattr(tool_func, "_required_caps", {"read"}),
                    )

                    # Use inspect to get the signature of the tool function
                    sig = inspect.signature(tool_func)

                    # Check if the function accepts **kwargs (VAR_KEYWORD)
                    has_var_keyword = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values())

                    if has_var_keyword:

                        # If it accepts **kwargs, we don't filter; all provided kwargs are valid
                        filtered_args = arguments
                    else:
                        # Otherwise, only pass the keys that match defined parameters to avoid TypeError
                        filtered_args = {k: v for k, v in arguments.items() if k in sig.parameters}
                    # Execute with filtered arguments
                    result = execute_tool(mod_obj, name, **filtered_args)
                except Exception as e:
                    if FAIL_ON_TOOL_ERROR:
                        raise e
                    else:
                        logger.error(f"Reporting tool execution error: {e}")
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
            # Final response from Assistant (already contains all fields including reasoning)
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
        help="List of tools/permissions (e.g., --tools git git:read:write weather:read)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level (default: INFO)",
    )
    parser.add_argument(
        "--workspace-dir",
        type=str,
        default=None,
        help="Workspace directory to mount into the sandbox when using podbash tools (default: TOOLEX_WORKSPACE_DIR env var or current directory)",
    )
    parser.add_argument(
            "--drop-tool-reasoning",
            action="store_true",
            help="Drop reasoning_content from assistant messages inside the tool loop"
        )
    parser.add_argument(
        "--total-iterations",
        type=int,
        help="Maximum tool iterations, total. Default 30",
        default=30
    )
    args = parser.parse_args()
    try:
        main(args)
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user. Exiting")
        sys.exit(1)
