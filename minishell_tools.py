#!/usr/bin/env python3
from __future__ import annotations

import re
import shlex
import inspect
import sys
from typing import Dict, Callable
from tooling import tool, CommandResult, discover_tools


class MiniShell:
    def __init__(self, tool_registry: Dict[str, Callable]):
        self.tool_registry = tool_registry

    def execute(self, command_line: str) -> CommandResult:
        print(f"⚙️{command_line} ".rstrip(), file=sys.stderr, end='')
        stages = [s.strip() for s in re.split(r'\s*\|\s*', command_line) if s.strip()]
        if not stages:
            return CommandResult("", "Empty pipeline", 1)

        last_stdout = ""
        final_result = None
        for stage in stages:
            tokens = shlex.split(stage)
            if not tokens:
                continue
            cmd_name = tokens[0]
            args_str = " ".join(tokens[1:]) if len(tokens) > 1 else ""
            result = self._run_step(cmd_name, args_str, last_stdout)
            if not result.is_success:
                return result
            last_stdout = result.stdout
            final_result = result
        return final_result or CommandResult("", "Empty pipeline", 0)

    def _run_step(self, name: str, args: str, input_data: str) -> CommandResult:
        if name in self.tool_registry:
            if False:
                # usually redundant with the tool itself
                print(f"⚙️".rstrip(), file=sys.stderr, end='')
            func = self.tool_registry[name]
            sig = inspect.signature(func)
            param_names = [p.name for p in sig.parameters.values()]

            kwargs = {}
            if "args" in param_names:
                kwargs["args"] = args
            else:
                # Fallback: try positional
                pass

            # Inject stdin if supported
            for stdin_name in ("stdin", "input_data"):
                if stdin_name in param_names:
                    kwargs[stdin_name] = input_data
                    break

            try:
                res = func(**kwargs)
                if isinstance(res, CommandResult):
                    return res
                # Compatibility: some tools still return dict or str
                if isinstance(res, dict):
                    # Assume first value is stdout
                    val = next(iter(res.values()))
                    return CommandResult(stdout=str(val), exit_code=0)
                return CommandResult(stdout=str(res), exit_code=0)
            except Exception as e:
                return CommandResult("", str(e), 1)

        return CommandResult("", f"Unknown command: {name}", 127)


# Global registry built from imported tools
# This will be populated at runtime by the orchestrator
_TOOL_REGISTRY: Dict[str, Callable] = {}


def set_tool_registry(registry: Dict[str, Callable]):
    global _TOOL_REGISTRY
    _TOOL_REGISTRY = registry


@tool("read")
def minishell_execute(command_line: str) -> CommandResult:
    """
    Execute a piped command line using available tools.
    Example: 'ls | grep py'
    Supports pipe '|' and propagates exit codes. No redirects.
    """
    shell = MiniShell(_TOOL_REGISTRY)
    return shell.execute(command_line)


__all__ = discover_tools(globals(), __name__)
