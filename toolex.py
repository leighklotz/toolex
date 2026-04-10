#!/usr/bin/env python3

#  **Usage examples**
# ./toolex.py --tools git_tools 'What were recent changes?'
# or
# ./toolex.py --tools git_tools --tools weather_tools 'Is it too hot today to work on git?'

import logging
import importlib
import inspect
import json
import os
import requests
import sys
from pathlib import Path
from typing import get_origin, get_args, Any, Union
import argparse

# Logging – a simple, module‑level logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Configuration
VIA_API_CHAT_BASE = os.getenv("VIA_API_CHAT_BASE", "http://127.0.0.1:5000")
URL = f"{VIA_API_CHAT_BASE}/v1/chat/completions"

# Utilities for building OpenAI‑style tool specifications
def _array_type_from_annotation(ann):
    """Return the inner type of List[T], Optional[List[T]], etc."""
    if get_origin(ann) is Union:
        for a in get_args(ann):
            if get_origin(a) in (list, tuple, set):
                return get_args(a)[0]
    elif get_origin(ann) in (list, tuple, set):
        return get_args(ann)[0]
    return None


def build_tools_from_module(module):
    """Return a list of OpenAI‑style tool dicts from a module."""
    tools = []

    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if getattr(obj, "_is_toolex_tool", False):
            sig = inspect.signature(obj)
            params = {}
            required = []

            for p in sig.parameters.values():
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

            tools.append(
                {
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
            )
    return tools


# CLI arguments
parser = argparse.ArgumentParser()
parser.add_argument(
    "--tools",
    action="append",
    default=[],
    help="Add a Python module that contains tool functions (can be used multiple times)",
)
parser.add_argument(
    "prompt",
    nargs=argparse.REMAINDER,
    help="Prompt to send to the model",
)

args = parser.parse_args()
MODULE_NAMES = args.tools or ["git_tools"]  # default if none provided

# Import tool modules
MODULES = [importlib.import_module(name) for name in MODULE_NAMES]

# Build the OpenAI‑tool list that will be sent to the LLM
TOOLS = [t for m in MODULES for t in build_tools_from_module(m)]

# execute_tool – the heart of the question
def execute_tool(tool_name: str, *args, **kwargs):
    """
    Execute a registered tool by name, log what happened, and
    return its raw result.
    """
    tool = None
    for module in MODULES:
        tool = getattr(module, tool_name, None)
        if tool:
            break

    if tool is None or not callable(tool):
        raise ValueError(f"Unknown tool: {tool_name!r}")

    try:
        result = tool(*args, **kwargs)
    except Exception as exc:
        raise RuntimeError(f"Execution of tool {tool_name!r} failed: {exc}") from exc

    logger.debug(
        "execute_tool('%s') → %s (args=%s, kwargs=%s)",
        tool_name,
        json.dumps(result, default=str),
        args,
        kwargs,
    )

    return result


# chat loop
def main(prompt: str):
    messages = [{"role": "user", "content": prompt}]
    TOTAL_ITERATIONS = 10

    for i in range(TOTAL_ITERATIONS):
        response = requests.post(URL, json={"messages": messages, "tools": TOOLS}).json()
        if not 'choices' in response:
            import pdb;pdb.set_trace()
        choice = response["choices"][0]

        if choice["finish_reason"] == "tool_calls":
            assistant_msg = choice["message"]
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_msg["content"],
                    "tool_calls": assistant_msg["tool_calls"],
                }
            )

            for call in assistant_msg["tool_calls"]:
                fn = call["function"]
                name, arguments = fn["name"], json.loads(fn["arguments"])
                logger.debug(f"Calling {name}({arguments})")

                result = execute_tool(name, **arguments)
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
            # Extract only the message object to maintain consistent schema
            messages.append(choice["message"])
            print(json.dumps(messages, default=str, indent=4))
            break


__all__ = [
    "execute_tool",
    "main",
]

if __name__ == "__main__":
    # The prompt is everything after the last option
    main(" ".join(args.prompt) or "Answer briefly: What's the weather like in Paris?")
