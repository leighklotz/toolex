#!/usr/bin/env python3

import importlib
import inspect
import json
import os
import sys
import pkgutil
import requests
from pathlib import Path

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
        # Only functions that have the attribute added by @tool
        if getattr(obj, "_is_llmfoo_tool", False):
            func_sig = inspect.signature(obj)
            params = {}
            for p in func_sig.parameters.values():
                params[p.name] = {
                    "type": "string" if p.annotation is str else "integer",
                    "description": p.name,
                }
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": obj.__name__,
                        "description": (obj.__doc__ or "").strip(),
                        "parameters": {
                            "type": "object",
                            "properties": params,
                            "required": list(params.keys()),
                        },
                    },
                }
            )
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
        {"role": "user", "content": prompt},
    ]

    TOTAL_ITERATIONS = 10
    for i in range(TOTAL_ITERATIONS):
        # print(f"🤖 Progress: {i + 1}/{TOTAL_ITERATIONS}")
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
                print(f"🤖 --> {name}({', '.join(arguments)})")

                result = execute_tool(name, arguments)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result),
                    }
                )
        else:
            print(f"\nAssistant: {choice['message']['content']}")
            break

if __name__ == "__main__":
    main(' '.join(sys.argv[1:]) or "What's the weather like in Paris?")

