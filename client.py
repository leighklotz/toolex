#!/usr/bin/env python3

import os
import json
import requests

VIA_API_CHAT_BASE = os.getenv("VIA_API_CHAT_BASE",  "http://127.0.0.1:5000/")
url = f"{VIA_API_CHAT_BASE}/v1/chat/completions"

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a given location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                },
                "required": ["location"]
            }
        }
    }
]


def execute_tool(name, arguments):
    # stub that does not actually call a function
    if name == "get_weather":
        return {"temperature": "14°C", "condition": "partly cloudy"}
    return {"error": f"Unknown tool: {name}"}


messages = [{"role": "user", "content": "What's the weather like in Paris?"}]

total_iterations = 10
for i in range(total_iterations):
    print(f"Progress: {i+1}/{total_iterations}")
    response = requests.post(url, json={"messages": messages, "tools": tools}).json()
    choice = response["choices"][0]

    if choice["finish_reason"] == "tool_calls":
        messages.append({
            "role": "assistant",
            "content": choice["message"]["content"],
            "tool_calls": choice["message"]["tool_calls"],
        })

        for tool_call in choice["message"]["tool_calls"]:
            name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])
            result = execute_tool(name, arguments)
            # print(f"Tool call: {name}({arguments}) => {result}")

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": json.dumps(result),
            })
    else:
        print(f"\nAssistant: {choice['message']['content']}")
        break
