#!/usr/bin/env python3

import sys
import subprocess
import inspect
from typing import Dict, List, Optional
from tooling import tool, discover_tools, CommandResult
from engines import exec_wrap
from functools import wraps
from engines import Engine


@tool("read")
def minishell_execute(command_line: str) -> CommandResult:
    """
    Execute a piped command line using available tools.
    Example: 'ls | grep py'
    Supports pipe '|' and propagates exit codes. No redirects.
    """
    return Engine.engine.run(command_line)

# ----------------------------------------------------------------------
# Public tools
# ----------------------------------------------------------------------
@tool("read")
@exec_wrap(Engine.engine, "ls")
def get_ls(args=""): pass

@tool("read")
@exec_wrap(Engine.engine, "pwd")
def get_pwd(args): pass

@tool("read")
@exec_wrap(Engine.engine, "cat")
def get_cat(args, stdin): pass

@tool("read")
@exec_wrap(Engine.engine, "whoami")
def get_whoami(args): pass

@tool("read")
@exec_wrap(Engine.engine, "date")
def get_date(args): pass

def validate_find_args(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        sig = inspect.signature(func)
        param_names = [p.name for p in sig.parameters.values()]

        payload = None
        if args and not isinstance(args[0], (dict, list)):
            payload = args[0]
        elif "args" in kwargs:
            payload = kwargs["args"]

        arg_payload = str(payload).strip() if payload is not None else ""

        # <- your requirement
        if not arg_payload or arg_payload == ".":
            raise ValueError(
                "get_find requires a non-empty search expression; '.' is not allowed because it may produce too much data."
            )
        return func(*args, **kwargs)
    return wrapper



@tool("read")
@validate_find_args
@exec_wrap(Engine.engine, "find")
def get_find(args): pass

@tool("read")
@exec_wrap(Engine.engine, "df")
def get_df(args): pass

@tool("read")
@exec_wrap(Engine.engine, "wc")
def get_wc(args, stdin): pass

@tool("read")
@exec_wrap(Engine.engine, "head")
def get_head(args, stdin): pass

@tool("read")
@exec_wrap(Engine.engine, "tail")
def get_tail(args, stdin): pass

@tool("read")
@exec_wrap(Engine.engine, "grep")
def get_grep(args, stdin): pass

@tool("write")
@exec_wrap(Engine.engine, "patch")
def get_grep(args, stdin): pass

### File must end with this line
__all__ = discover_tools(globals(), __name__)
