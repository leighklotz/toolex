from __future__ import annotations

import sys
import inspect

from dataclasses import dataclass
from typing import Protocol, List, Optional
from tooling import CommandResult
from minishell_engine import MiniShell
from functools import wraps


class Engine:
    engine = None

    def set_engine(name, all_commands_registry):
        if name == "minishell":
            Engine.engine = MiniShellExecutor(all_commands_registry)
        elif name == "host":
            Engine.engine = HostExecutor(all_commands_registry)
        elif name == "podman":
            Engine.engine = PodmanExecutor(all_commands_registry)
        else:
            raise RuntimeException(f"unknown engine {name=}")


class Executor(Protocol):
    name: str
    def run(self, base_cmd: List[str], args: str, stdin: Optional[str], caps: set) -> CommandResult: ...

class HostExecutor:
    name = "host"
    def __init__(self, registry):
        self.caps = set()
        self.registry = registry

    def run(self, cmd, args, stdin_data):
        """Runs the command directly on host machine with stdin support."""
        args_list = shlex.split(args) if args and args.strip() else []
        full_cmd = list(cmd) + args_list

        try:
            result = subprocess.run(
                full_cmd,
                cwd=os.getcwd(),
                capture_output=True,
                text=True,
                input=stdin_data
            )
            return CommandResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
        except Exception as exc:
            return CommandResult("", str(exc), 1)

class PodmanExecutor:
    name = "podman"
    def __init__(self, registry):
        self.caps = set()
        self.registry = registry

    def run(self, cmd, args, stdin_data):
        """Executes a command inside a Podman container."""
        mount_mode = "rw" if "write" in caps else "ro"
        args_list = shlex.split(args) if args and args.strip() else []
        tool_argv = list(base_cmd) + args_list

        podman_cmd = [
            "podman", "run", "--rm",
            "--net", "none",
            "--workdir", "/workspace",
            "-v", f"{SANDBOX_CONFIG['host_data_dir']}:/workspace:{mount_mode}",
            SANDBOX_CONFIG["image"],
        ] + tool_argv

        try:
            result = subprocess.run(podman_cmd, capture_output=True, text=True, input=stdin_data)
            return CommandResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
        except Exception as exc:
            return CommandResult("", str(exc), 1)

class MiniShellExecutor:
    name = "minishell"

    def __init__(self, registry):
        self.caps = set()
        self.registry = registry

    def run(self, base_cmd, args, stdin, caps):
        # MiniShell is itself a pipeline interpreter. For a first-class engine we
        # want the engine to *be* the interpreter, not a tool that calls tools.
        # The simplest orthogonal step is to make MiniShell implement the same
        # run(name,cmd,args,stdin) signature and be selectable like host/podman.
        from minishell_engine import MiniShell
        shell = MiniShell(self.registry)
        # treat base_cmd[0] as the pipeline command line
        cmd_line = " ".join([base_cmd] + [args])
        return shell.execute(cmd_line if stdin is None else f"{cmd_line} | cat")

# --- HOST EXECUTION ---
def exec_wrap(engine: Executor, base_cmd: List[str]):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            sig = inspect.signature(f)
            payload = kwargs.get("args", "")
            stdin = kwargs.get("stdin") or kwargs.get("input_data")
            caps = getattr(f, "_required_caps", set())
            return Engine.engine.run(base_cmd, str(payload), stdin, caps)
        wrapper._command_name = base_cmd[0]
        return wrapper
    return decorator
