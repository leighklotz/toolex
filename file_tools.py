#!/usr/bin/env python3

import sys
from typing import Dict, List, Optional
from tooling import tool, discover_tools

@tool("read")
def read_file(file_path: str) -> str:
    """Returns the contents of a file as text."""
    print(f"🤖📥{file_path}", file=sys.stderr, end='')
    try:
        with open(file_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File not found at {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"

@tool("write")
def write_file(file_path: str, content: str) -> str:
    """Writes content to a file, creating it if it does not exist."""
    print(f"🤖💾{file_path}", file=sys.stderr, end='')
    try:
        with open(file_path, "w") as f:
            f.write(content)
        return "File written successfully."
    except Exception as e:
        return f"Error writing file: {e}"

@tool("write")
def edit_file(file_path: str, edit_instructions: str) -> str:
    """Applies a specific change to an existing file."""
    print(f"🤖📝✒️ {file_path}", file=sys.stderr, end='')
    try:
        file_content = read_file(file_path)
        if "Error" in file_content:
            return file_content
        # Simple example - replace a string.  More complex edits would require parsing.
        if "replace:" in edit_instructions:
            _, old_str, new_str = edit_instructions.split(":", 2)
            new_content = file_content.replace(old_str, new_str)
            write_file(file_path, new_content)
            return "File edited successfully."
        else:
            return "Error: Invalid edit instructions. Use 'replace:old_string:new_string'"
    except Exception as e:
        return f"Error editing file: {e}"

@tool("read")
def search_files(file_pattern: str, search_string: str) -> str:
    """Search file contents for a pattern."""
    print(f"🤖🔍'{search_string}' in '{file_pattern}'", file=sys.stderr, end='')
    import glob
    import os

    results = []
    for filepath in glob.glob(file_pattern):
        if os.path.isfile(filepath):
            try:
                with open(filepath, 'r') as f:
                    if search_string in f.read():
                        results.append(filepath)
            except Exception as e:
                print(f"Error reading file {filepath}: {e}")
    return "\n".join(results)

__all__ = discover_tools(globals(), __name__)
