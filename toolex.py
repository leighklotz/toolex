#!/usr/bin/env python3

#  **Usage examples**
# ask "what is the git status " toolex.py --tools git_tools  | answer
# or
# ask "what is the weather in paris" | toolex.py --tools git_tools,weather_tools
# or

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
logger.setLevel(logging.DEBUG)

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


def main(args):
    # Determine initial messages
    messages = []

    # Expect the magic header present at the start of the string
    line = sys.stdin.readline().strip()
    try:
        logger.info(f"header line is {line=}\n")
        messages = json.load(sys.stdin)
        logger.info(f"Loaded message history from stdin via --pipe: {messages=}")
    except Exception:
        logger.error(
            f"Failed to parse JSON from stdin. Data snippet: {input_data[:50]!r}",
            exc_info=True,
        )

    global MODULES, TOOLS
    MODULES = [importlib.import_module(name) for name in args.tools or ["git_tools"]]
    TOOLS = [t for m in MODULES for t in build_tools_from_module(m)]

    TOTAL_ITERATIONS = 10
    for i in range(TOTAL_ITERATIONS):
        # CHECK: If the last message is an assistant message with NO tool calls,
        # it means the conversation is already "finished".
        # We should stop and just return the history.
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            # print the full JSON history.
            print(MAGIC_HEADER)
            print(json.dumps(messages, default=str))

        try:
            response = requests.post(
                URL, json={"messages": messages, "tools": TOOLS}
            ).json()
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
            # print the full JSON history.
            messages.append(choice["message"])
            print(MAGIC_HEADER)
            print(messages)
            break


# Build the OpenAI‑tool list that will be sent to the LLM
__all__ = [
    "execute_tool",
    "main",
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tools",
        action="append",
        default=[],
        help="Add a Python module that contains tools functions",
    )
    args = parser.parse_args()
    main(args)
