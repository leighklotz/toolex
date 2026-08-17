#!/usr/bin/env python3

import sys
import os
import glob
from typing import Annotated
from pathlib import Path
from tooling import tool, discover_tools

WORKING_DIR=os.getcwd()

def check_working_dir(file_path):
    """
    Ensure a path is inside the allowed working directory,
    a safety check used by tools that should not be able to access files outside the worktree.

    Args:
        file_path (str): The path to check. Can be relative or absolute.

    Raises:
        Exception: If `file_path` does not resolve to a location inside `WORKING_DIR`.
    """
    if not Path(file_path).resolve().is_relative_to(Path(WORKING_DIR).resolve()):
        raise Exception(f"cannot access {file_path=} as it is not inside working directory {WORKING_DIR=}")

# tool name is python function name; @tool capabilities are permissions
@tool(capabilities="read")
def read_file_in_workdir(
    file_path: Annotated[str, "The path to the file you wish to read. Can be relative or absolute. file_path must be inside working dir."]
) -> str:
    """Returns the contents of a text file as a single string."""
    print(f"🤖📥{file_path}", file=sys.stderr, end='')
    check_working_dir(file_path)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File not found at {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"

@tool("read_anywhere")
def read_file_anywhere(
    file_path: Annotated[str, "The path to the file you wish to read. Can be relative or absolute."]
) -> str:
    """Returns the contents of a text file as a single string."""
    print(f"🤖📥{file_path}", file=sys.stderr, end='')
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File not found at {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"

@tool("write_anywhere")
def write_file_anywhere(
    file_path: Annotated[str, "The path where the content will be written. Creates file if missing."],
    content: Annotated[str, "The full string content to write into the file."]
) -> str:
    """Writes text content to a file, overwriting existing content or creating new files."""
    print(f"🤖💾{file_path}", file=sys.stderr, end='')
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return "File written successfully."
    except Exception as e:
        return f"Error writing file: {e}"

@tool(capabilities="write")
def write_file_in_workdir(
    file_path: Annotated[str, "The path where the content will be written. Creates file if missing. file_path must be inside working dir."],
    content: Annotated[str, "The full string content to write into the file."]
) -> str:
    """Writes text content to a file, overwriting existing content or creating new files."""
    print(f"🤖💾{file_path}", file=sys.stderr, end='')
    check_working_dir(file_path)
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return "File written successfully."
    except Exception as e:
        return f"Error writing file: {e}"

@tool(capabilities="edit")
def edit_file_in_workdir(
    file_path: Annotated[str, "The path to the existing file that needs modification. file_path must be inside working dir."],
    edit_instructions: Annotated[str, "A string containing replacement instructions using the format 'replace:old_text:new_text'. Example: 'replace:hello:hi'"]
) -> str:
    """Applies a specific text substitution to an existing file. Requires precise formatting."""
    print(f"🤖📝✒️ {file_path}", file=sys.stderr, end='')
    check_working_dir(file_path)
    try:
        # Re-using logic from the user prompt but ensuring it's contained in this module context
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()

        if "replace:" not in edit_instructions:
            return "Error: Invalid edit instructions. You must use the format 'replace:old_string:new_string'"

        parts = edit_instructions.split(":", 2)
        if len(parts) < 3:
            return "Error: Invalid format. Use 'replace:old_string:new_string'"

        _, old_str, new_str = parts[0], parts[1], parts[2]
        new_content = file_content.replace(old_str, new_str)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return "File edited successfully."
    except FileNotFoundError:
        return f"Error: File not found at {file_path}"
    except Exception as e:
        return f"Error editing file: {e}"

@tool(capabilities="edit_anywhere")
def edit_file_anywhere(
    file_path: Annotated[str, "The path to the existing file that needs modification."],
    edit_instructions: Annotated[str, "A string containing replacement instructions using the format 'replace:old_text:new_text'. Example: 'replace:hello:hi'"]
) -> str:
    """Applies a specific text substitution to an existing file. Requires precise formatting."""
    print(f"🤖📝✒️ {file_path}", file=sys.stderr, end='')
    try:
        # Re-using logic from the user prompt but ensuring it's contained in this module context
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
        if not "replace:" in edit_instructions:
            return f"Error: Invalid edit instructions for {file_path=}. You must use the format 'replace:old_string:new_string'"
        parts = edit_instructions.split(":", 2)
        if len(parts) < 3:
            return f"Error: Invalid format for {file_path=}. Use 'replace:old_string:new_string'"

        _, old_str, new_str = parts[0], parts[1], parts[2]
        new_content = file_content.replace(old_str, new_str)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return f"File {file_path=} edited successfully."
    except Exception as e:
        return f"Error editing {file_path=}: {e}"

@tool(capabilities="read")
def search_files_in_workdir(file_pattern: Annotated[str, "A glob pattern to match files (e.g., '*.py' or './src/*.txt'). file_pattern must be inside working dir."],
    search_string: Annotated[str, "The literal text string you are looking for within the files."]
) -> str:
    """Searches through multiple files matching a pattern and returns names of files containing the search string."""
    print(f"🤖🔍'{search_string}' in '{file_pattern}'", file=sys.stderr, end='')

    results = []
    for file_path in glob.glob(file_pattern):
        if os.path.isfile(file_path):
            check_working_dir(file_path)
            try:
                with open(file_path, 'r') as f:
                    if search_string in f.read():
                        results.append(file_path)
            except Exception as e:
                print(f"Error reading file {file_path}: {e}", file=sys.stderr)
    return "\n".join(results)

@tool(capabilities="read_anywhere")
def search_files_anywhere(
    file_pattern: Annotated[str, "A glob pattern to match files (e.g., '*.py' or './src/*.txt')."],
    search_string: Annotated[str, "The literal text string you are looking for within the files."]
) -> str:
    """Searches through multiple files matching a pattern and returns names of files containing the search string."""
    print(f"🤖🔍'{search_string}' in '{file_pattern}'", file=sys.stderr, end='')

    results = []
    for file_path in glob.glob(file_pattern):
        if os.path.isfile(file_path):
            try:
                with open(file_path, 'r') as f:
                    if search_string in f.read():
                        results.append(file_path)
            except Exception as e:
                print(f"Error reading file {file_path}: {e}", file=sys.stderr)
    return "\n".join(results)


### File must end with this line
__all__ = discover_tools(globals(), __name__)
