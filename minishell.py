import shlex
import inspect
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, List, Union, Tuple, Set, Any, Dict
from tooling import CommandResult, _truncate, SANDBOX_CONFIG

@dataclass(frozen=True)
class CommandSpec:
    name: str                      # shell name (e.g., "ls")
    argv: Tuple[str, ...]          # base command to run via runner (e.g., ("ls",))
    caps: frozenset                # permissions required (e.g., {"read"})
    consumes_stdin: bool           # if True, the engine injects previous stage's stdout here
    doc: str                       # description for LLM index and 'help' command
    mode: str                      # "native" | "runner"
    func: Optional[Callable] = None # used only in "native" mode (built-ins)
    validate: Optional[Callable] = None

def command(name: str, *, doc: str = "", caps: Union[str, Set[str]] = "read",
            consumes_stdin: bool = False, argv: Optional[List[str]] = None,
            native: bool = False, validate: Optional[Callable] = None):
    """Annotation that makes a function a minishell COMMAND (Internal Shell Command).
    Note: This does NOT make it an LLM tool. Use @tool in separate modules for that.
    """
    def decorator(f: Callable) -> Callable:
        cap_set = frozenset(caps.split() if isinstance(caps, str) else caps)
        spec = CommandSpec(
            name=name,
            argv=tuple(argv if argv is not None else [name]),
            caps=cap_set,
            consumes_stdin=consumes_stdin,
            doc=(doc or (inspect.getdoc(f) or "")).strip(),
            mode="native" if native else "runner",
            func=f if native else None,
            validate=validate,
        )
        f._command_spec = spec
        return f
    return decorator

class CommandRegistry:
    def __init__(self):
        self._by_name: Dict[str, CommandSpec] = {}

    def scan(self, mod, permission_map: dict, module_key: str = None):
        key = module_key or mod.__name__
        granted = frozenset((permission_map.get(key) or {}).keys())
        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            spec = getattr(obj, "_command_spec", None)
            if not spec: continue

            # Security check at registration time (prevents accidental leakage)
            if "all" not in granted and not spec.caps.issubset(granted):
                continue
            
            if spec.name in self._by_name:
                raise ValueError(f"Duplicate shell command name detected: {spec.name}")
            self._by_name[spec.name] = spec

    def get(self, name: str) -> Optional[CommandSpec]:
        return self._by_name.get(name)

    def index(self, limit: int = 2000) -> str:
        lines = []
        for s in sorted(self._by_name.values(), key=lambda x: x.name):
            tag = "  [stdin]" if s.consumes_stdin else ""
            lines.append(f"{s.name} — {s.doc}{tag}")
        text = "\n".join(lines)
        return text[:limit] + ("\n...[truncated]" if len(text) > limit else "")

class HostRunner:
    def run(self, argv: List[str], stdin_data: Optional[str], caps: frozenset) -> CommandResult:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, input=stdin_data)
            return CommandResult(_truncate(r.stdout), _truncate(r.stderr), r.returncode)
        except Exception as e:
            return CommandResult("", str(e), 1)

class PodmanRunner:
    def run(self, argv: List[str], stdin_data: Optional[str], caps: frozenset) -> CommandResult:
        try:
            mode = "rw" if "write" in caps else "ro"
            cmd = [
                "podman", "run", "--rm", "--net", "none", "--workdir", "/workspace",
                "-v", f"{SANDBOX_CONFIG['host_data_dir']}:/workspace:{mode}",
                SANDBOX_CONFIG["image"]
            ] + argv
            r = subprocess.run(cmd, capture_output=True, text=True, input=stdin_data)
            return CommandResult(_truncate(r.stdout), _truncate(r.stderr), r.returncode)
        except Exception as e:
            return CommandResult("", str(e), 1)

class MiniShell:
    UNSUPPORTED = {">", "<", ">>", "<<", "&&", "||", ";", "(", ")", "&"}

    def __init__(self, registry: CommandRegistry, runner: Any, granted: frozenset):
        self.registry = registry
        self.runner = runner
        self.granted = granted

    def execute(self, line: str) -> CommandResult:
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError as e:
            return CommandResult("", f"shell syntax error: {e}", 2)

        if not tokens: return CommandResult("", "", 0)

        # Pipeline parsing (Fixes D7 quote-blindness via shlex)
        stages, cur = [], []
        for t in tokens:
            if t == "|": stages.append(cur); cur = []
            elif t in self.UNSUPPORTED:
                return CommandResult("", f"'{t}' is not supported (pipes only; use multiple calls)", 2)
            else: cur.append(t)
        stages.append(cur)

        if any(not s for s in stages):
            return CommandResult("", "shell syntax error: empty command in pipeline", 2)


        buffer: Optional[str] = None
        last_res = CommandResult()

        for idx, toks in enumerate(stages, 1):
            name = toks[0]; args = toks[1:]
            spec = self.registry.get(name)
            if not spec: return CommandResult("", f"command not found: {name} (run 'help')", 127)
            
            # Runtime enforcement of caps and validation
            if "all" not in self.granted and not spec.caps.issubset(self.granted):
                return CommandResult("", f"permission denied: {name}", 126)

            if spec.validate:
                try: spec.validate(" ".join(args))
                except ValueError as e: return CommandResult("", str(e), 1)

            # Execution Logic (Fixes D8/Plan B semantics)
            if spec.mode == "native":
                if spec.consumes_stdin and buffer is None:
                    return CommandResult("", f"{name} requires piped input", 1)
                try:
                    res = spec.func(" ".join(args), buffer or "")
                except Exception as e:
                    return CommandResult("", f"{name}: {e}", 1)
            else:
                if spec.consumes_stdin and buffer is None:
                    return CommandResult("", f"{name} requires piped input", 1)
                res = self.runner.run(list(spec.argv) + args, buffer, spec.caps)

            # Normalization (D3 fix - ensures we always work with CommandResult objects)
            if not isinstance(res, CommandResult): res = CommandResult(str(res), "", 0)
            last_res = res

            if not res.is_success: # Fail-fast logic
                return CommandResult(res.stdout, f"stage {idx} ({name}) failed (exit {res.exit_code}):\n{res.stderr}", res.exit_code)
            buffer = res.stdout

        return last_res
