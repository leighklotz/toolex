#!/usr/bin/env python3

import importlib
import inspect
import json
import os
import sys
import pkgutil
import requests
from pathlib import Path
from typing import get_origin, get_args, Any

VIA_API_CHAT_BASE = os.getenv("VIA_API_CHAT_BASE", "http://127.0.0.1:5000/")
URL = f"{VIA_API_CHAT_BASE}/v1/chat/completions"

# ------------------------------------------------------------
# 1. Discover all @tool‑decorated functions from tools.py
# ------------------------------------------------------------
MODULE_NAME = "tools"          # filename without .py
MODULE = importlib.import_module(MODULE_NAME)

def build_tools_from_module(module):
    """Return a list of OpenAI‑style tool dicts from a module."""
    tools = []
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if getattr(obj, "_is_toolex_tool", False):
            func_sig = inspect.signature(obj)
            params = {}
            required = []

            for p in func_sig.parameters.values():
                ann = p.annotation
                # --- detect list/sequence ---------------------------------
                origin = get_origin(ann)
                if origin in (list, tuple, set):
                    item_type = get_args(ann)[0]
                    # map item_type to JSON schema type
                    type_map = {str: "string", int: "integer", float: "number"}
                    prop_type = type_map.get(item_type, "string")
                    params[p.name] = {
                        "type": "array",
                        "items": {"type": prop_type},
                        "description": p.name,
                    }
                else:
                    type_map = {str: "string", int: "integer", float: "number"}
                    prop_type = type_map.get(ann, "string")
                    params[p.name] = {
                        "type": prop_type,
                        "description": p.name,
                    }

                # Only mark as required if no default value
                if p.default is inspect._empty:
                    required.append(p.name)

            tools.append({
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
            })
    return tools

TOOLS = build_tools_from_module(MODULE)

# ------------------------------------------------------------
# 2. Execute a tool
# ------------------------------------------------------------
def execute_tool(name: str, arguments: dict):
    func = getattr(MODULE, name, None)
    if not func:
        return {"error": f"Unknown tool '{name}'"}
    result = func(**arguments)
    return result

# ------------------------------------------------------------
# 3. Main chat loop
# ------------------------------------------------------------
def main(prompt):
    messages = [
        {"role": "user", "content": f"Answer briefly using tools:\n{prompt}"},
    ]

    TOTAL_ITERATIONS = 10
    for i in range(TOTAL_ITERATIONS):
        # print(f"🤖 Progress: {i + 1}/{TOTAL_ITERATIONS}", file=sys.stderr)
        response = requests.post(URL, json={"messages": messages, "tools": TOOLS}).json()
        choice = response["choices"][0]
        if choice["finish_reason"] == "tool_calls":
            # Assistant said “I need to call a tool”
            messages.append(
                {
                    "role": "assistant",
                    "content": choice["message"]["content"],
                    "tool_calls": choice["message"]["tool_calls"],
                }
            )

            for tool_call in choice["message"]["tool_calls"]:
                fn = tool_call["function"]
                name = fn["name"]
                arguments = json.loads(fn["arguments"])
                print(f"🤖 --> {name}({', '.join(f'{k}={v}' for k, v in arguments.items())})", file=sys.stderr)
                #print(f"🤖 --> {name}({', '.join(arguments.keys())})", file=sys.stderr)

                result = execute_tool(name, arguments)
                if 'error' in result:
                    print(f"⚠️ --> {json.dumps(result)}", file=sys.stderr)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result),
                    }
                )
        else:
            print(f"{choice['message']['content']}")
            break

if __name__ == "__main__":
    main(' '.join(sys.argv[1:]) or "Answer briefly: What's the weather like in Paris?")
