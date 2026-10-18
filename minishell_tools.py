#!/usr/bin/env python3
"""minishell_tools — single-file mini shell for toolex.

One module contains everything: the CommandResult type, the @command
annotation, the command registry, both runners (host / podman), the
pipe-only interpreter, all 28 shell commands, and the single LLM-facing
`shell` tool. The registry is built from this module's own namespace at
import time, so adding a command is writing one decorated function —
there is no wiring step and no second module.

Surface exposed to the LLM: exactly one tool (`shell`). Individual
commands are NOT tools — @command never sets _is_toolex_tool, so
build_tools_from_modules cannot expose a command no matter how this
module is loaded.

Canonical invocations:

    toolex.py --tools minishell         # read + write (patch, tee)
    toolex.py --tools minishell:read    # read-only; patch/tee absent
    toolex.py --tools minishell:read --engine podman

Standalone smoke test (no toolex involved):

    python -c "import minishell_tools as m; print(m._REGISTRY.index(m._GRANTED))"
"""

from __future__ import annotations

import glob
import inspect
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Annotated, Callable

from tooling import SANDBOX_CONFIG, discover_tools, tool

# ===========================================================================
# Results
# ===========================================================================

_MAX_OUTPUT = 100_000


def _truncate(s: str, limit: int = _MAX_OUTPUT) -> str:
    # len() counts characters, not bytes — the marker says "characters", on purpose.
    return s if len(s) <= limit else s[:limit] + f"\n…[truncated {len(s) - limit} characters]"


@dataclass(frozen=True)
class CommandResult:
    """Structured result of a minishell pipeline. toolex serializes it
    duck-typed via to_payload() — the model never sees a dataclass repr."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0

    def to_payload(self) -> dict:
        return {"stdout": _truncate(self.stdout),
                "stderr": _truncate(self.stderr),
                "exit_code": self.exit_code}


# ===========================================================================
# Specs and the @command decorator
# ===========================================================================

@dataclass(frozen=True)
class CommandSpec:
    name: str
    doc: str
    caps: frozenset[str]                 # subset of {"read", "write"}
    consumes_stdin: bool = False         # runner-mode: reads stdin when no file operands
    nonfile_operands: int = 0            # leading positional operands that are NOT files
    stdin_only: bool = False             # operands are never files (tr, cut)
    argv: tuple[str, ...] = ()           # defaults to (name,) — PATH lookup
    native: bool = False                 # True → call func(args=…, stdin=…)
    func: Callable | None = None
    validate: Callable | None = None     # takes rest-tokens, raises ValueError to deny


def command(name, *, doc="", caps="read", consumes_stdin=False,
            nonfile_operands=0, stdin_only=False,
            argv=None, native=False, validate=None):
    """Decorator: attach a frozen CommandSpec to a function.

    The spec carries the name, doc, and capability set directly — there are
    no wrapper layers that could re-derive them from the wrong place.
    @command NEVER sets _is_toolex_tool: that is the structural enforcement
    that no individual command can surface as an LLM tool.
    """

    def deco(f):
        caps_fs = frozenset(caps.split())
        if not caps_fs <= {"read", "write"}:
            raise ValueError(f"{name}: caps must be a subset of read/write, got {caps!r}")
        f._command_spec = CommandSpec(
            name=name, doc=(doc or inspect.getdoc(f) or "").strip(),
            caps=caps_fs, consumes_stdin=consumes_stdin,
            nonfile_operands=nonfile_operands, stdin_only=stdin_only,
            argv=tuple(argv) if argv else (name,), native=native,
            func=f if native else None, validate=validate)
        return f

    return deco


# ===========================================================================
# Registry (self-scan; capability filtering at dispatch, not build time)
# ===========================================================================

def _allowed(spec: CommandSpec, granted: frozenset[str]) -> bool:
    """Wildcard grant ('*', used by tests/standalone) permits everything;
    otherwise the spec's caps must be a subset of the grant."""
    return "*" in granted or spec.caps.issubset(granted)


class CommandRegistry:
    """Holds ALL commands; the runtime grant filters index(), help, and
    dispatch. No scan-time permission lookup — there is no key to mismatch."""

    def __init__(self):
        self._by_name: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec):
        if spec.name in self._by_name:
            raise ValueError(f"duplicate shell command: {spec.name}")  # name shadowing dies here
        self._by_name[spec.name] = spec

    @classmethod
    def from_namespace(cls, ns: dict) -> "CommandRegistry":
        reg = cls()
        for obj in ns.values():
            spec = getattr(obj, "_command_spec", None)
            if spec is not None:
                reg.register(spec)
        return reg

    def get(self, name):
        return self._by_name.get(name)

    def index(self, granted: frozenset[str], limit: int | None = 4096) -> str:
        # Grant header makes the index self-describing.
        if "*" in granted:
            grant_str = "*"
        else:
            grant_str = " ".join(sorted(granted))
        lines = [f"grant: {grant_str}"]
        for s in sorted(self._by_name.values(), key=lambda s: s.name):
            if not _allowed(s, granted):
                continue
            tags = ""
            if "write" in s.caps:
                tags += "  [write]"
            if s.consumes_stdin:
                tags += "  [stdin]"
            lines.append(f"{s.name} — {s.doc}{tags}")
        text = "\n".join(lines)
        if limit is None or len(text) <= limit:
            return text
        return text[:limit] + "\n…(run 'help' for the full list)"


# ===========================================================================
# Runners
# ===========================================================================

_TIMEOUT = 60  # seconds; a hung stage (tail -f, find on a huge tree) must not stall the agent loop


class HostRunner:
    """Direct execution on the host, cwd pinned to the workspace."""

    def run(self, argv, stdin, caps) -> CommandResult:
        kw = dict(capture_output=True, text=True,
                  cwd=SANDBOX_CONFIG["host_data_dir"], timeout=_TIMEOUT)
        if stdin is None:
            kw["stdin"] = subprocess.DEVNULL   # no TTY, ever — kills the hang class
        else:
            kw["input"] = stdin
        try:
            p = subprocess.run(argv, **kw)
            return CommandResult(p.stdout, p.stderr, p.returncode)
        except subprocess.TimeoutExpired:
            return CommandResult("", f"timed out after {_TIMEOUT}s", 124)
        except Exception as e:
            return CommandResult("", f"{type(e).__name__}: {e}", 1)


class PodmanRunner:
    """One throwaway container per stage: no network, ro mount unless the
    stage's caps include write."""

    def run(self, argv, stdin, caps) -> CommandResult:
        mount = "rw" if "write" in caps else "ro"
        cmd = ["podman", "run", "--rm", "--net=none", "--workdir", "/workspace",
               "-v", f"{SANDBOX_CONFIG['host_data_dir']}:/workspace:{mount}",
               SANDBOX_CONFIG["image"], *argv]
        kw = dict(capture_output=True, text=True, timeout=_TIMEOUT)
        if stdin is None:
            kw["stdin"] = subprocess.DEVNULL
        else:
            kw["input"] = stdin
        try:
            p = subprocess.run(cmd, **kw)
            return CommandResult(p.stdout, p.stderr, p.returncode)
        except subprocess.TimeoutExpired:
            return CommandResult("", f"timed out after {_TIMEOUT}s", 124)
        except Exception as e:
            return CommandResult("", f"{type(e).__name__}: {e}", 1)


# ===========================================================================
# Safety validators (shared pattern)
# ===========================================================================

def _reject(rest, bad, msg):
    """Exact matches and prefix patterns (entries ending in '*')."""
    prefixes = tuple(p[:-1] for p in bad if p.endswith("*"))
    hit = [t for t in rest if t in bad or t.startswith(prefixes)]
    if hit:
        raise ValueError(f"{msg}: {', '.join(hit)}")


def _guard_find(rest):
    # bare '.' (and './') are ALLOWED: bounded by the 60 s stage timeout, the
    # 100 KB truncation, and the predicate ban below. Denying '.' outright
    # would break the single most common invocation, find . -name '*.py'.
    _reject(rest, {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                   "-fprint", "-fprintf", "-fls"},
            "destructive find predicates are disabled (modify files with patch)")


def _guard_sed(rest):
    # '*' catches attached forms: sed -i.bak
    _reject(rest, {"-i", "-i*", "--in-place", "--in-place*"},
            "sed -i is disabled (use patch)")


def _guard_sort(rest):
    # '*' catches attached forms: sort -ofile
    _reject(rest, {"-o", "-o*", "--output", "--output*"},
            "sort -o is disabled (use | tee <file>)")


def _guard_date(rest):
    # '*' catches attached forms: date --set=2024-01-01
    _reject(rest, {"-s", "-s*", "--set", "--set*"},
            "date -s is disabled (read-only shell)")


# ===========================================================================
# Commands — 28 total, one decorator each. The index renders at ~2.2 KB,
# under the 4 KB schema cap. Trimming the set is deleting a decorator.
#
# Runner-command bodies are NEVER executed: the interpreter dispatches them
# through a Runner using spec.argv (+ parsed tokens), not func. Each stub
# exists so @command has a function to annotate; doc= feeds the index.
# ===========================================================================

# --- read · no stdin -------------------------------------------------------

@command("ls", doc="List directory contents.")
def _ls(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("pwd", doc="Print the current working directory.")
def _pwd(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("whoami", doc="Print the effective user name.")
def _whoami(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("which", doc="Locate a command in PATH.")
def _which(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("basename", doc="Strip directory and optional suffix from a filename.")
def _basename(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("dirname", doc="Strip the last component from a filename.")
def _dirname(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("stat", doc="Display file or filesystem status.")
def _stat(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("file", doc="Determine file type.")
def _file(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("du", doc="Estimate file and directory space usage.")
def _du(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("df", doc="Report filesystem disk space usage.")
def _df(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


# --- read · consume stdin · operands are files ------------------------------

@command("cat", doc="Concatenate files to stdout.", consumes_stdin=True)
def _cat(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("head", doc="Print the first lines of each file (default 10).", consumes_stdin=True)
def _head(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("tail", doc="Print the last lines of each file (default 10).", consumes_stdin=True)
def _tail(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("wc", doc="Count lines, words, and bytes.", consumes_stdin=True)
def _wc(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("uniq", doc="Report or omit repeated lines (input should be sorted).", consumes_stdin=True)
def _uniq(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("diff", doc="Compare files line by line ('-' reads stdin).", consumes_stdin=True)
def _diff(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


# --- read · consume stdin · special operand shapes / validators --------------

@command("grep", doc="Print lines matching a pattern: grep [-i -v -n -c -E -F] PATTERN [FILE...]",
         consumes_stdin=True, nonfile_operands=1)   # first positional is the pattern, rest are files
def _grep(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("sed", doc="Stream editor: sed 's/old/new/[g]' [FILE...]  (sandboxed: -i, w, W, r, e disabled)",
         consumes_stdin=True, nonfile_operands=1,   # first positional is the script
         argv=("sed", "--sandbox"),                 # pinned before user tokens; GNU sed has no --no-sandbox
         validate=_guard_sed)
def _sed(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("cut", doc="Extract fields or characters per line; stdin-only in v1 — pipe into it.",
         consumes_stdin=True, stdin_only=True)
def _cut(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("tr", doc="Translate, squeeze, or delete characters; stdin-only in v1 — pipe into it.",
         consumes_stdin=True, stdin_only=True)
def _tr(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("sort", doc="Sort lines of text (-o is disabled; use | tee <file>).",
         consumes_stdin=True, validate=_guard_sort)
def _sort(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


# --- read · no stdin · validators --------------------------------------------

@command("date", doc="Print the current date and time (-s/--set is disabled).",
         validate=_guard_date)
def _date(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("find", doc="Search a directory tree: find . -name '*.py'  (destructive predicates disabled)",
         validate=_guard_find)
def _find(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


# --- native builtins — the only native=True commands in v1; the func hook is
#     the extension point for future pure-Python filters.

@command("echo", doc="Print arguments joined by spaces, then a newline.", native=True)
def _echo(args: list[str] | None = None, stdin: str | None = None) -> CommandResult:
    parts = args or []
    return CommandResult((" ".join(parts) + "\n") if parts else "\n", "", 0)


@command("printf", doc="C-style printf: printf 'fmt' args... (%s %d %f %%, \\n \\t)", native=True)
def _printf(args: list[str] | None = None, stdin: str | None = None) -> CommandResult:
    toks = args or []
    if not toks:
        return CommandResult("", "", 0)
    fmt, vals = toks[0], toks[1:]
    out, vi, i = [], 0, 0
    try:
        while i < len(fmt):
            c = fmt[i]
            if c == "\\" and i + 1 < len(fmt):
                out.append({"n": "\n", "t": "\t", "\\": "\\"}.get(fmt[i + 1], "\\" + fmt[i + 1]))
                i += 2
            elif c == "%" and i + 1 < len(fmt):
                conv = fmt[i + 1]
                if conv == "%":
                    out.append("%")
                elif conv in "sd":
                    out.append(str(vals[vi])); vi += 1
                elif conv == "f":
                    out.append(f"{float(vals[vi]):f}"); vi += 1
                else:
                    raise ValueError(f"unsupported conversion %{conv}")
                i += 2
            else:
                out.append(c); i += 1
    except IndexError:
        return CommandResult("", "printf: not enough arguments for format", 1)
    except Exception as e:
        return CommandResult("", f"printf: {e}", 1)
    return CommandResult("".join(out), "", 0)


@command("help", doc="Print the full command index for the current permission grant.", native=True)
def _help(args: str = "", stdin: str | None = None) -> CommandResult:
    # limit=None: the schema-embedded index truncates at 4096 with the hint
    # "run 'help' for the full list" — help must be able to keep that promise.
    return CommandResult(_REGISTRY.index(_GRANTED, limit=None) + "\n", "", 0)


# --- write — the only mutating commands in v1 ---------------------------------

@command("patch", doc="Apply a unified diff to files (pipe the diff in, or name the file).",
         caps="write", consumes_stdin=True)
def _patch(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


@command("tee", doc="Write piped input to a file and to stdout.",
         caps="write", consumes_stdin=True)
def _tee(args: str = "", stdin: str | None = None) -> CommandResult:
    raise NotImplementedError


# ===========================================================================
# Interpreter
# ===========================================================================

_UNSUPPORTED = {">", "<", ">>", "&&", "||", ";", "(", ")", "&", "`", "|&"}
_PATTERN_FLAGS = {"-name", "-iname", "-path", "-ipath", "-regex", "-wholename"}
_WORKSPACE = SANDBOX_CONFIG["host_data_dir"]


def _glob_ws(pattern: str) -> list[str]:
    """Glob anchored to the workspace. glob.glob(root_dir=) needs Python >= 3.10;
    on older interpreters the join-and-strip fallback below produces the same
    result for workspace-relative patterns (the only ones we expand — see
    _split)."""
    if sys.version_info >= (3, 10):
        return glob.glob(pattern, recursive=True, root_dir=_WORKSPACE)
    hits = glob.glob(os.path.join(_WORKSPACE, pattern), recursive=True)
    return [os.path.relpath(h, _WORKSPACE) for h in hits]


def _split(line: str) -> list[str]:
    tokens = shlex.split(line, posix=True)   # quote-aware — a whitespace split would break 'grep "a|b" f'
    out, prev = [], None
    for t in tokens:                         # bash-like glob expansion
        # Expansion is clamped to workspace-RELATIVE tokens: under PodmanRunner,
        # expanding an absolute glob like /etc/* would match on the HOST
        # filesystem, not inside the container. Absolute glob tokens pass
        # through literally and the command reports them as missing — safe and
        # self-explaining.
        if (prev not in _PATTERN_FLAGS and not t.startswith("/")
                and any(c in t for c in "*?[")):
            out.extend(sorted(_glob_ws(t)) or [t])   # no match → literal, bash-style
        else:
            out.append(t)
        prev = t
    return out


def _has_file_operand(rest, spec: CommandSpec) -> bool:
    """True when the stage's remaining tokens contain a file operand.
    stdin_only commands (tr, cut) never take file operands. For the rest, the
    first `nonfile_operands` positional tokens (pattern / script) don't count:
    'grep foo' has NO file operand; 'grep foo f.py' does."""
    if spec.stdin_only:
        return False
    positional = [t for t in rest if not t.startswith("-")]
    return len(positional) > spec.nonfile_operands


def _call(func, args, stdin) -> CommandResult:
    try:
        return func(args=args, stdin=stdin)
    except Exception as e:
        return CommandResult("", f"{type(e).__name__}: {e}", 1)


class MiniShell:
    """Pipe-only interpreter: shlex tokenization, '|' stage splitting,
    per-stage dispatch through CommandSpecs, fail-fast error reporting."""

    def __init__(self, registry, runner, granted):
        self._reg = registry
        self._runner = runner
        self._granted = granted

    def execute(self, line: str) -> CommandResult:
        try:
            tokens = _split(line)
        except ValueError as e:
            return CommandResult("", f"shell syntax error: {e}", 2)
        stages, cur = [], []
        for t in tokens:
            if t == "|":
                stages.append(cur); cur = []
            elif t in _UNSUPPORTED:
                return CommandResult("", f"'{t}' is not supported (pipes only); "
                                         f"split the work into multiple shell calls", 2)
            else:
                cur.append(t)
        stages.append(cur)

        # Reject empty segments BEFORE any stage runs.
        for i, toks in enumerate(stages, 1):
            if not toks:
                return CommandResult("", f"empty pipeline segment at stage {i}", 2)

        buffer = None
        for i, toks in enumerate(stages, 1):
            name, rest = toks[0], toks[1:]
            spec = self._reg.get(name)
            if spec is None:
                return CommandResult("", f"command not found: {name} (run 'help')", 127)
            if not _allowed(spec, self._granted):
                return CommandResult("", f"permission denied: {name} requires "
                                         f"{' '.join(sorted(spec.caps))}", 126)
            if spec.validate:
                try:
                    spec.validate(rest)
                except ValueError as e:
                    return CommandResult("", f"{name}: {e}", 1)
            if spec.native:
                res = _call(spec.func, rest, buffer)
            else:
                if spec.consumes_stdin and buffer is None and not _has_file_operand(rest, spec):
                    return CommandResult("", f"{name} reads stdin; give it file arguments "
                                             f"or pipe something into it", 1)
                res = self._runner.run([*spec.argv, *rest], buffer, spec.caps)
            if not res.is_success:
                return CommandResult(res.stdout,
                                     f"pipeline stage {i} ({name}) failed, exit {res.exit_code}\n{res.stderr}",
                                     res.exit_code)
            buffer = res.stdout
        return CommandResult(buffer or "", "", 0)


# ===========================================================================
# Assembly (the whole integration layer)
# ===========================================================================

# Placement: the _REGISTRY assignment must come AFTER the last @command
# definition in the file — it scans the module namespace at import time.

_REGISTRY: CommandRegistry = CommandRegistry.from_namespace(dict(globals()))
_GRANTED: frozenset[str] = frozenset({"*"})   # permissive until toolex configures (tests, standalone)
_runner: HostRunner | PodmanRunner = HostRunner()


def command_index() -> str:
    """Public helper: the granted command index. Fallback if toolex's schema
    generator reads the inner function's docstring instead of the wrapper's."""
    return _REGISTRY.index(_GRANTED)


def configure(granted, engine: str = "host"):
    """Called once by toolex after module load, BEFORE tool schemas are built.

    Emits a permission banner + denied list into the shell tool's docstring
    so the LLM sees the mode and exclusions before acting — no set-difference
    inference required.
    """
    global _GRANTED, _runner, shell
    _GRANTED = frozenset(granted)
    _runner = PodmanRunner() if engine == "podman" else HostRunner()
    denied = sorted(s.name for s in _REGISTRY._by_name.values()
                    if not _allowed(s, _GRANTED))
    mode = "read-only" if "write" not in _GRANTED else "read+write"
    shell.__doc__ = (shell.__doc__ or "").rstrip() + (
        f"\n\nPermission mode: {mode}."
        + (f" Disabled this session: {', '.join(denied)}." if denied else "")
        + "\n\nAvailable commands:\n" + _REGISTRY.index(_GRANTED))


# ===========================================================================
# The LLM surface (the only _is_toolex_tool in the file)
# ===========================================================================

@tool("read")
def shell(
    command_line: Annotated[
        str,
        "A minishell command line. Join stages with ' | ' — put spaces around the "
        "pipe ('a|b' reads as one token and fails). Quotes are honored. "
        "Only pipes are supported — no ';', '&&', or redirects. "
        "The full command index is in this description; 'help' re-prints it at runtime.",
    ] = "",
) -> CommandResult:
    """Execute one command line in minishell.

    Pipes only; the pipeline stops at the first failing stage. Available
    commands are listed below (also available at runtime via 'help').

    Semantics: '|' is the only separator — ';', '&&', '||', redirects, and
    subshells are rejected (exit 2) with instructions to split the work into
    multiple shell calls. Tokenization is shlex-based, so an unspaced pipe
    reads as one unknown token (exit 127): put spaces around '|'. Fail-fast:
    the first non-zero stage ends the pipeline and the error names the stage
    and command — a deliberate divergence from bash's last-command exit
    status. Unknown commands exit 127 with a hint to run 'help'; permission
    denials exit 126; syntax errors exit 2; timeouts exit 124. A command that
    reads stdin refuses to run as the first stage without file arguments,
    instead of silently succeeding on empty input.
    """
    return MiniShell(_REGISTRY, _runner, _GRANTED).execute(command_line)


__all__ = discover_tools(globals(), __name__)
