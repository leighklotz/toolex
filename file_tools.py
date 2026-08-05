#!/usr/bin/env python3

import sys
from typing import Dict, List, Optional, Annotated
from tooling import tool, discover_tools

@tool("read")
def read_file(
    file_path: Annotated[str, "The path to the file you wish to read. Can be relative or absolute."]
) -> str:
    """Returns the contents of a text file as a single string."""
    print(f" 🤖📥{file_path}", file=sys.stderr, end='')
    try:
        with open(file_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File not found at {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"

@tool("write")
def write_file(
    file_path: Annotated[str, "The path where the content will be written. Creates file if missing."],
    content: Annotated[str, "The full string content to write into the file."]
) -> str:
    """Writes text content to a file, overwriting existing content or creating new files."""
    print(f" 🤖💾{file_path}", file=sys.stderr, end='')
    try:
        with open(file_path, "w") as f:
            f.write(content)
        return "File written successfully."
    except Exception as e:
        return f"Error writing file: {e}"

@tool("edit")
def edit_file(
    file_path: Annotated[str, "The path to the existing file that needs modification."],
    edit_instructions: Annotated[str, "A string containing replacement instructions using the format 'replace:old_text:new_text'. Example: 'replace:hello:hi'"]
) -> str:
    """Applies a specific text substitution to an existing file. Requires precise formatting."""
    print(f" 🤖📝✒️ {file_path}", file=sys.stderr, end='')
    try:
        # Re-using logic from the user prompt but ensuring it's contained in this module context
        with open(file_path, "r") as f:
            file_content = f.read()

        if "replace:" in edit_instructions:
            parts = edit_instructions.split(":", 2)
            if len(parts) < 3:
                return "Error: Invalid format. Use 'replace:old_string:new_string'"
            
            _, old_str, new_str = parts[0], parts[1], parts[2]
            new_content = file_content.replace(old_str, new_str)
            
            with open(file_path, "w") as f:
                f.write(new_content)
            return "File edited successfully."
        else:
            return "Error: Invalid edit instructions. You must use the format 'replace:old_string:new_string'"
    except FileNotFoundError:
        return f"Error: File not found at {file_path}"
    except Exception as e:
        return f"Error editing file: {e}"

@tool("read")
def search_files(
    file_pattern: Annotated[str, "A glob pattern to match files (e.g., '*.py' or './src/*.txt')."], 
    search_string: Annotated[str, "The literal text string you are looking for within the files."]
) -> str:
    """Searches through multiple files matching a pattern and returns names of files containing the search string."""
    print(f" 🤖🔍'{search_string}' in '{file_pattern}'", file=sys.stderr, end='')
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
                print(f"Error reading file {filepath}: {e}", file=sys.stderr)
    return "\n".join(results)


### File must end with this line
__all__ = discover_tools(globals(), __name__)
