#!/usr/bin/env python3
# git_tools.py

import sys
from typing import Dict, List, Optional
from tooling import tool, run_tool

# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------
@tool
def get_weather(location: str) -> Dict[str, str]:
    """Return weather for a given location."""
    location = location.strip().lower()
    print(f"🤖 weather {location}", file=sys.stderr)
    if location in {"paris", "london"}:
        return {"temperature": "14°C", "condition": "partly cloudy"}
    return {"temperature": "unknown", "condition": "unknown"}

@tool
def get_location() -> Dict[str, str]:
    """Return the location."""
    print(f"🤖 get_location", file=sys.stderr)
    return { "location": "paris" }

# ----------------------------------------------------------------------
# Tool discovery
# ----------------------------------------------------------------------
__all__ = [
    "get_weather",
    "get_location",
]
