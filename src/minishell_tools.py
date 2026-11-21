#!/usr/bin/env python3
"""minishell_tools — single-file mini shell for toolex.

One module contains everything: the CommandResult type, the @command
annotation, the command registry, both runners (host / podman), the
PathGuard, the pipe-only interpreter, all 29 shell commands, and the
single LLM-facing `shell` tool. The registry is built from this module's
own namespace at import time, so adding a command is writing one
decorated function — there is no wiring step and no second module.

Surface exposed to the LLM: exactly one tool (`shell`), declared
self_scoped (toolex skips its own path-argument checks because
enforcement lives in this interpreter). Individual commands are NOT
tools — @command never sets _is_toolex_tool, so build_tools_from_modules
cannot expose a command no matter how this module is loaded.

Grant model — installed once by toolex via configure(grant, engine).
Keys are capability names, values are fnmatch pattern lists (matched
with fnmatchcase: case-sensitive, `*` matches `/` — same semantics as
file_tools):

    read            read-cap commands touch paths inside the workspace
                    matching the patterns (["*"] = everything inside)
    read_anywhere   read-cap commands touch any host path; patterns, if
                    given, match resolved absolute paths
    write           tee/patch modify paths inside the workspace matching
                    the patterns
    write_anywhere  tee/patch modify any host path
    delete          rm moves a file to .trash/ — paths inside the
                    workspace matching the patterns
    delete_anywhere rm moves any host path into .trash/; patterns, if
                    given, match resolved absolute paths
    all / *         all six above with patterns ["*"] (toolex maps the
                    CLI cap `all` to `*`; also the standalone default)

Anywhere dominates scoped (read_anywhere over read, write_anywhere over
write, delete_anywhere over delete; the dominant scope's patterns
apply). The `shell` tool is visible under any minishell grant, but
per-stage dispatch still denies commands whose capabilities are not
covered (exit 126).

Enforcement is in-interpreter and pre-dispatch: PathGuard resolves every
stage's path operands (symlink-safe — a workspace symlink pointing
outside is denied), then applies containment + patterns for scoped
grants, or absolute-path patterns for anywhere grants. Denials exit 126
and name the violated constraint (missing grant / containment /
pattern) plus the allowed patterns, so the model can self-correct.
`help` and the schema index print the effective grant and the disabled
commands.

Canonical invocations:

    toolex.py --tools minishell                # read-only, contained in the workdir
    toolex.py --tools minishell:read=*.py      # read-only, only *.py paths
    toolex.py --tools minishell:read:write     # + tee/patch inside the workdir
    toolex.py --tools minishell:delete         # rm only — files move into .trash/
    toolex.py --tools minishell:read_anywhere  # host-wide read (engine caveats below)
    toolex.py --tools minishell:read --engine podman

Engines: `host` runs stages directly, cwd pinned to the workspace.
`podman` runs each stage in a throwaway no-network container with the
workspace mounted ro (rw only for write-cap stages). Caveats: under
podman, `read_anywhere` mounts the host root read-only at /host inside
the container — that substantially weakens the container boundary, and
`--engine host` is the expected pairing for anywhere grants;
`write_anywhere` under podman is rejected outright (configure() raises,
toolex exits 2) — a rw mount of the host root is not an acceptable
implementation.

patch under a patterned write grant: write targets are taken from the
piped diff's `---`/`+++` headers (`-p1` stripped) and each is checked
against the grant; feed the diff on stdin (file-operand diffs are
rejected under patterned grants) and `/dev/null` header operands are
skipped. With an unpatterned write grant no header parsing happens.

Standalone smoke test (no toolex involved; permissive default grant):

    python -c "import minishell_tools as m; print(m._REGISTRY.index(m._GRANT))"
"""

from __future__ import annotations

import fnmatch
import glob
import inspect
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Callable

from tooling import SANDBOX_CONFIG, discover_tools, tool, emit_invocation

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
    caps: frozenset[str]                 # subset of {"read", "write", "delete"}
    consumes_stdin: bool = False         # runner-mode: reads stdin when no file operands
    nonfile_operands: int = 0            # leading positional operands that are NOT files
    read_paths: str = "none"             # which operands are paths the stage reads (R10)
    write_paths: str = "none"            # which operands are paths the stage writes (R10)
    delete_paths: str = "none"           # which operands are paths the stage deletes (R10)
    argv: tuple[str, ...] = ()           # defaults to (name,) — PATH lookup
    native: bool = False                 # True → call func(args=…, stdin=…)
    func: Callable | None = None
    validate: Callable | None = None     # takes rest-tokens, raises ValueError to deny


# Operand classification descriptors (R10). Validated at decoration time —
# an unknown descriptor can never reach dispatch.
#   "none"                       no operand is a path of this kind
#   "all_positionals"            every operand token
#   "positionals_after_nonfile"  operand tokens minus the first `nonfile_operands`
#                                (grep pattern, sed script)
#   "leading_positionals"        operand tokens before the first option token
#                                (find: start points precede every predicate)
#   "diff_headers"               write targets named inside the diff on stdin —
#                                patch only, placeholder until Change 11. It names
#                                no static operand and is never a bypass: Change 10
#                                must deny a scoped patch stage until extraction exists.
_PATH_DESCRIPTOR_VALUES = frozenset({
    "none", "all_positionals", "positionals_after_nonfile",
    "leading_positionals", "diff_headers",
})


def command(name, *, doc="", caps="read", consumes_stdin=False,
            nonfile_operands=0, read_paths="none", write_paths="none",
            delete_paths="none", argv=None, native=False, validate=None):
    """Decorator: attach a frozen CommandSpec to a function.

    The spec carries the name, doc, and capability set directly — there are
    no wrapper layers that could re-derive them from the wrong place.
    @command NEVER sets _is_toolex_tool: that is the structural enforcement
    that no individual command can surface as an LLM tool.
    """

    def deco(f):
        caps_fs = frozenset(caps.split())
        if not caps_fs <= {"read", "write", "delete"}:
            raise ValueError(f"{name}: caps must be a subset of "
                             f"read/write/delete, got {caps!r}")
        if (read_paths not in _PATH_DESCRIPTOR_VALUES
                or write_paths not in _PATH_DESCRIPTOR_VALUES
                or delete_paths not in _PATH_DESCRIPTOR_VALUES):
            raise ValueError(f"{name}: unknown path descriptor "
                             f"(read={read_paths!r}, write={write_paths!r}, "
                             f"delete={delete_paths!r})")
        f._command_spec = CommandSpec(
            name=name, doc=(doc or inspect.getdoc(f) or "").strip(),
            caps=caps_fs, consumes_stdin=consumes_stdin,
            nonfile_operands=nonfile_operands,
            read_paths=read_paths, write_paths=write_paths,
            delete_paths=delete_paths,
            argv=tuple(argv) if argv else (name,), native=native,
            func=f if native else None, validate=validate)
        return f

    return deco


# ===========================================================================
# Registry (self-scan; capability filtering at dispatch, not build time)
# ===========================================================================

# R15: the granted cap names that cover each intrinsic capability. A spec is
# dispatchable when every cap it needs is covered — by the cap itself, its
# *_anywhere variant, or the '*' wildcard. Patterns are NOT checked here;
# that is PathGuard's job (consulted per stage from Change 10 on).
_COVERS: dict[str, tuple[str, ...]] = {
    "read": ("read", "read_anywhere"),
    "write": ("write", "write_anywhere"),
    "delete": ("delete", "delete_anywhere"),
}


def _allowed(spec: CommandSpec, grant: dict[str, list[str]] | frozenset[str]) -> bool:
    """Name-level dispatch check over the new capability vocabulary (R15).

    `grant` is the {cap: [patterns]} map; its KEYS are the granted cap names.
    A frozenset/set of bare names is also accepted (legacy and test callers).
    {read} is allowed iff the grant contains read, read_anywhere, or '*';
    {write} iff write, write_anywhere, or '*'; {delete} iff delete,
    delete_anywhere, or '*'.
    """
    names = grant.keys() if isinstance(grant, dict) else grant
    if "*" in names:
        return True
    return all(any(c in names for c in _COVERS.get(cap, (cap,)))
               for cap in spec.caps)


def _as_grant(grant) -> dict[str, list[str]]:
    """Coerce an incoming grant to the canonical {cap: [patterns]} shape.

    Non-dict inputs — e.g. the frozenset of bare cap names the pre-Change-8
    toolex passes — cannot carry patterns. Honoring them would silently widen
    scoped grants ('read=*.py' arrives as bare 'read'), so they normalize to
    an EMPTY grant: everything denied (exit 126) until the transport is
    upgraded (Change 8). A string where a list is expected is wrapped, not
    iterated (a bare '*.py' must not become five one-character patterns).
    """
    if not isinstance(grant, dict):
        return {}
    out: dict[str, list[str]] = {}
    for cap, pats in grant.items():
        if isinstance(pats, str):
            pats = [pats]
        out[str(cap)] = [str(p) for p in pats]
    return out


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

    def index(self, grant: dict[str, list[str]], limit: int | None = 4096) -> str:
        # Grant header makes the index self-describing; R7 wants the effective
        # scopes AND patterns visible: grant: read=*.py write=*.log
        grant = _as_grant(grant)
        if "*" in grant:
            grant_str = "*"
        elif grant:
            grant_str = " ".join(f"{cap}={','.join(sorted(pats))}"
                                 for cap, pats in sorted(grant.items()))
        else:
            grant_str = "(nothing granted)"
        allowed = [s for s in sorted(self._by_name.values(), key=lambda s: s.name)
                   if _allowed(s, grant)]
        lines = [f"grant: {grant_str}"]
        for s in allowed:
            tags = ""
            if "write" in s.caps:
                tags += "  [write]"
            if "delete" in s.caps:
                tags += "  [delete]"
            if s.consumes_stdin:
                tags += "  [stdin]"
            lines.append(f"{s.name} — {s.doc}{tags}")
        disabled = sorted(set(self._by_name) - {s.name for s in allowed})
        if disabled:
            lines.append(f"disabled this session: {', '.join(disabled)}")
        text = "\n".join(lines)
        if limit is None or len(text) <= limit:
            return text
        return text[:limit] + "\n…(run 'help' for the full list)"


# ===========================================================================
# Runners
# ===========================================================================

_TIMEOUT = 60  # seconds; a hung stage (tail -f, find on a huge tree) must not stall the agent loop


def _apply_redirects(res: CommandResult, redirects: dict[int, object]) -> CommandResult:
    if not redirects:
        return res
    out, err = res.stdout, res.stderr
    if redirects.get(1) == "devnull":
        out = ""
    if redirects.get(2) == "devnull":
        err = ""
    if redirects.get(2) == 1:
        out, err = out + err, ""
    if redirects.get(1) == 2:
        out, err = "", err + out
    return CommandResult(out, err, res.exit_code)


class HostRunner:
    """Direct execution on the host, cwd pinned to the workspace."""

    def run(self, argv, stdin, caps, redirects=None) -> CommandResult:
        kw = dict(capture_output=True, text=True,
                  cwd=SANDBOX_CONFIG["host_data_dir"], timeout=_TIMEOUT)
        if stdin is None:
            kw["stdin"] = subprocess.DEVNULL   # no TTY, ever — kills the hang class
        else:
            kw["input"] = stdin
        try:
            p = subprocess.run(argv, **kw)
            return _apply_redirects(CommandResult(p.stdout, p.stderr, p.returncode), redirects)
        except subprocess.TimeoutExpired:
            return CommandResult("", f"timed out after {_TIMEOUT}s", 124)
        except Exception as e:
            return CommandResult("", f"{type(e).__name__}: {e}", 1)


class PodmanRunner:
    """One throwaway container per stage: no network, ro mount unless the
    stage's caps include write. With host_root=True (an anywhere-read grant —
    read_anywhere or '*', R14) the host root is additionally mounted
    READ-ONLY at /host so anywhere-reads reach the host filesystem; writes
    stay confined to the workspace mount."""

    def __init__(self, host_root: bool = False):
        self._host_root = host_root

    def run(self, argv, stdin, caps, redirects=None) -> CommandResult:
        mount = "rw" if "write" in caps else "ro"
        cmd = ["podman", "run", "--rm", "--net=none", "--workdir", "/workspace",
               "-v", f"{SANDBOX_CONFIG['host_data_dir']}:/workspace:{mount}"]
        if self._host_root:
            cmd.extend(["-v", "/:/host:ro"])
        if stdin is not None:
            cmd.append("--interactive")
        cmd.extend([SANDBOX_CONFIG["image"], *argv])
        kw = dict(capture_output=True, text=True, timeout=_TIMEOUT)
        if stdin is None:
            kw["stdin"] = subprocess.DEVNULL
        else:
            kw["input"] = stdin
        try:
            p = subprocess.run(cmd, **kw)
            return _apply_redirects(CommandResult(p.stdout, p.stderr, p.returncode), redirects)
        except subprocess.TimeoutExpired:
            return CommandResult("", f"timed out after {_TIMEOUT}s", 124)
        except Exception as e:
            return CommandResult("", f"{type(e).__name__}: {e}", 1)


# ===========================================================================
# Safety validators
# ===========================================================================

@dataclass(frozen=True)
class GuardConfig:
    """Declarative configuration for command-line argument validation."""
    banned_long: frozenset[str] = field(default_factory=frozenset)
    banned_short: frozenset[str] = field(default_factory=frozenset)
    banned_tokens: frozenset[str] = field(default_factory=frozenset)
    max_positionals: int | None = None
    deny_all_options: bool = False
    options_with_args: frozenset[str] = field(default_factory=frozenset)


GUARD_CONFIGS = {
    "find": GuardConfig(
        banned_tokens={"-delete", "-exec", "-execdir", "-ok", "-okdir",
                      "-fprint", "-fprint0", "-fprintf", "-fls"}
    ),
    "sed": GuardConfig(
        banned_long={"--in-place", "--file"},
        banned_short={"i", "f"},
        options_with_args={"e", "l"}
    ),
    "sort": GuardConfig(
        banned_long={"--output", "--compress-program", "--random-source",
                     "--files0-from", "--temporary-directory"},
        banned_short={"o", "T"},
        options_with_args={"k", "t", "S"}
    ),
    "uniq": GuardConfig(
        max_positionals=1,
        options_with_args={"f", "s", "w"}
    ),
    "date": GuardConfig(
        banned_tokens={"-s*", "--set*", "-f*", "--file*"}
    ),
    "grep": GuardConfig(
        banned_long={"--file", "--exclude-from", "--include-from"},
        banned_short={"f"},
        options_with_args={"A", "B", "C", "e", "m"}
    ),
    "diff": GuardConfig(
        banned_long={"--exclude-from", "--from-file", "--to-file"},
        banned_short={"X"}
    ),
    "file": GuardConfig(
        banned_long={"--files-from"},
        banned_short={"f"}
    ),
    "rm": GuardConfig(deny_all_options=True),
    "patch": GuardConfig(
        banned_long={"strip", "directory", "output", "prefix", "suffix",
                     "backup", "reject-file", "input"},
        banned_short={"p", "b", "B", "d", "o", "z", "r", "i"}
    ),
}


def _validate_args(rest, cmd_name):
    """Generic argument validator based on a declarative GuardConfig."""
    config = GUARD_CONFIGS.get(cmd_name)
    if not config:
        return

    positionals = 0
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--":
            break
        if config.deny_all_options and tok.startswith("-"):
            raise ValueError(f"{cmd_name} options are disabled (single-file unlink only): {tok}")
        if any(tok == b or (b.endswith("*") and tok.startswith(b[:-1]))
               for b in config.banned_tokens):
            raise ValueError(f"{cmd_name} token is disabled: {tok}")
        if tok.startswith("--"):
            opt = tok.split("=", 1)[0]
            if any(opt.startswith(b) or b.startswith(opt) for b in config.banned_long):
                raise ValueError(f"{cmd_name} option {opt} is disabled: {tok}")
        elif tok.startswith("-") and tok != "-":
            group = tok[1:]
            if any(c in config.banned_short for c in group):
                raise ValueError(f"{cmd_name} option is disabled: {tok}")
            # Skip argument for options known to take one, to keep positional count accurate
            for j, char in enumerate(group):
                if char in config.options_with_args and j == len(group) - 1:
                    i += 1
                    break
        else:
            positionals += 1
            if config.max_positionals is not None and positionals > config.max_positionals:
                raise ValueError(f"{cmd_name} output operands are disabled (read-only command)")
        i += 1


# --- patch header extraction (R11) ------------------------------------------

_PATCH_BANNED_LONG = {"strip", "directory", "output", "prefix", "suffix",
                      "backup", "reject-file", "input"}


def _diff_operand(raw: str) -> str:
    """One ---/+++ operand: cut a tab-separated timestamp, strip an optional
    double-quote pair. Git's backslash escapes inside quoted names are not
    decoded — such operands fail extraction (fail-closed)."""
    path = raw.split("\t", 1)[0].strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    return path


def _p1_strip(path: str) -> str:
    """Mirror the pinned `patch -p1` operand reading: strip the smallest
    prefix containing one leading slash ('a/foo' -> 'foo', 'src/foo' ->
    'foo', 'foo' -> 'foo'). Precondition: path is relative."""
    parts = path.split("/")
    return "/".join(parts[1:]) if len(parts) > 1 else path


def _patch_target_paths(diff_text: str) -> list[str]:
    """R11: the paths `patch` may write are named in the diff's ---/+++
    header pairs, not on the argv. Returns first-seen, de-duplicated targets
    in header order. Raises ValueError (converted to a 126 denial by the
    caller) when no header pair exists or an operand is unusable.

    /dev/null is the create/delete sentinel and names no writable path: the
    real target of a creation or deletion diff is the opposite header, which
    is extracted and checked. The pair rule (--- immediately followed by +++)
    can also match adjacent hunk-body lines ('-- x' removed above '++ y');
    that only ever ADDS a candidate (fail-closed), never drops a real one."""
    targets: list[str] = []
    seen: set[str] = set()
    lines = diff_text.splitlines()
    i = 0
    while i < len(lines):
        if (lines[i].startswith("--- ") and i + 1 < len(lines)
                and lines[i + 1].startswith("+++ ")):
            for raw in (lines[i][4:], lines[i + 1][4:]):
                op = _diff_operand(raw)
                if op == "/dev/null":
                    continue
                if op.startswith("/") or not op:
                    raise ValueError(
                        f"diff header names an unusable target ({op!r}); under a "
                        f"patterned write grant targets must be workdir-relative")
                target = _p1_strip(op)
                if not target or target.startswith("/"):
                    raise ValueError(
                        f"diff header operand {op!r} does not yield a usable "
                        f"target path under -p1 stripping")
                if target not in seen:
                    seen.add(target)
                    targets.append(target)
            i += 2
            continue
        i += 1
    if not targets:
        raise ValueError("no ---/+++ header pair found; not a usable unified diff")
    return targets


# ===========================================================================
# PathGuard — grant-scoped path validation (used by configure() and the
# interpreter; a denied path never reaches subprocess)
# ===========================================================================

class PathGuard:
    """Validates path operands against the session's path grants *before* a
    stage is dispatched.

    Constructed by configure() from the grant dict {cap: [patterns]} handed
    over by toolex. The grant is never read from the environment
    (TOOLEX_PERMITTED_PATH_PATTERNS is process-global and would collide
    across modules in one session).

    check_read/check_write/check_delete raise PermissionError on violation
    and return None on success, mirroring file_tools' check_permitted_path:
    the message names the violated constraint — missing grant, containment,
    or pattern — and lists the allowed patterns so the model can
    self-correct. The interpreter catches these and renders them as the
    stage's result.

    Scope dominance: read_anywhere dominates read, write_anywhere dominates
    write, delete_anywhere dominates delete. An anywhere scope drops
    workspace containment and matches patterns against the resolved absolute
    path; a scoped scope requires containment in the workspace and matches
    against the workspace-relative POSIX form. fnmatchcase semantics; '*'
    matches '/' (parity with file_tools: '*.py' matches sub/b.py).

    Paths are resolved with Path.resolve(): symlinks are followed, so a
    workspace symlink pointing outside is denied under a scoped grant.
    Relative operands are anchored at the workspace root first — the runners
    pin a stage's cwd there, and the toolex process CWD is not a valid base
    once TOOLEX_WORKSPACE_DIR is set. resolve() is non-strict, so
    not-yet-existing write targets (tee new.log) resolve cleanly. TOCTOU
    between check and open is accepted at parity with file_tools; under
    --engine podman the ro/rw mount stays the hard backstop.
    """

    def __init__(self, grant: dict[str, list[str]]):
        # Same root the interpreter globs against (_WORKSPACE); resolved so
        # both sides of the containment comparison are symlink-honest.
        self._workdir = Path(SANDBOX_CONFIG["host_data_dir"]).resolve()
        self._read = self._dominant(grant, "read", "read_anywhere")
        self._write = self._dominant(grant, "write", "write_anywhere")
        self._delete = self._dominant(grant, "delete", "delete_anywhere")

    @staticmethod
    def _dominant(grant: dict, scoped: str,
                  anywhere: str) -> tuple[bool, tuple[str, ...]] | None:
        """The *_anywhere cap eclipses its scoped twin. Returns None when the
        family is not granted, else (anywhere, patterns). '*' (toolex maps
        the `all` grant to {"*": ["*"]}) counts as anywhere with its own
        patterns. An empty pattern list denies everything — fail-closed;
        toolex always sends ['*'] for a bare grant."""
        if "*" in grant:
            return (True, tuple(grant["*"]))
        if anywhere in grant:
            return (True, tuple(grant[anywhere]))
        if scoped in grant:
            return (False, tuple(grant[scoped]))
        return None

    def check_read(self, path: str) -> None:
        """Raise PermissionError unless `path` is within the read grant."""
        self._check(path, self._read, "read")

    def check_write(self, path: str) -> None:
        """Raise PermissionError unless `path` is within the write grant."""
        self._check(path, self._write, "write")

    def check_delete(self, path: str) -> None:
        """Raise PermissionError unless `path` is within the delete grant."""
        self._check(path, self._delete, "delete")

    def write_is_patterned(self) -> bool:
        """True when a write grant is active AND its pattern list constrains
        paths (anything other than the ['*'] sentinel). Drives the R11
        fallback: a patterned write grant requires diff-header extraction
        and per-target validation. An unscoped write grant (['*']) scoped
        to the workspace still requires target validation to prevent
        path-traversal escapes; only a trueത് anywhere grant skips all
        target checks."""
        return self._write is not None and list(self._write[1]) != ["*"]

    def write_needs_target_validation(self) -> bool:
        """True when patch targets must be extracted from diff headers and
        checked against the write grant. This is the case whenever the write
        grant is scoped (workspace-contained) AND the patterns are restrictive
        (not ['*']). A ['*'] pattern (even if scoped) or an anywhere grant
        skips target validation."""
        if self._write is None:
            return False
        anywhere, patterns = self._write
        if anywhere:
            return False
        if list(patterns) == ["*"]:
            return False
        return True

    def _check(self, path: str, scope: tuple[bool, tuple[str, ...]] | None,
               kind: str) -> None:
        if scope is None:
            raise PermissionError(
                f"Access Denied: {path!r} needs a {kind} grant; "
                f"no {kind} capability is active this session")
        anywhere, patterns = scope
        p = Path(path)
        if not p.is_absolute():
            # Relative operands are anchored at the workspace root: both
            # runners pin a stage's cwd there (HostRunner cwd=, PodmanRunner
            # --workdir /workspace). The toolex process CWD is NOT a valid
            # base once TOOLEX_WORKSPACE_DIR is set.
            p = self._workdir / p
        try:
            resolved = p.resolve()   # resolve() follows symlinks: escapes denied
        except (OSError, ValueError) as e:   # e.g. embedded NUL byte
            raise PermissionError(
                f"Access Denied: cannot resolve {path!r}: {e}") from e
        if anywhere:
            candidate = resolved.as_posix()
        else:
            # is_relative_to needs 3.9+; file_tools already relies on it.
            if not resolved.is_relative_to(self._workdir):
                raise PermissionError(
                    f"Access Denied: {path!r} resolves to {resolved.as_posix()!r}, "
                    f"outside the working directory {self._workdir.as_posix()!r} "
                    f"(workspace-scoped {kind} grant, patterns {sorted(patterns)})")
            candidate = resolved.relative_to(self._workdir).as_posix()
        if not any(fnmatch.fnmatchcase(candidate, pat) for pat in patterns):
            raise PermissionError(
                f"Access Denied: {path!r} resolves to {candidate!r}, which does "
                f"not match permitted {kind} patterns {sorted(patterns)}")


# ===========================================================================
# Commands
# ===========================================================================

# --- native builtins — the func hook is the extension point for future
#     pure-Python filters.

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
                    value = vals[vi]
                    out.append(str(int(value)) if conv == "d" else value)
                    vi += 1
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
    # "run 'help' for the full list" — help must be able to that promise.
    return CommandResult(_REGISTRY.index(_GRANT, limit=None) + "\n", "", 0)


@command("rm", doc="Move ONE file to .trash/ (no permanent delete, no directories, no recursion)",
         caps="delete", native=True, delete_paths="all_positionals", validate=lambda r: _validate_args(r, "rm"))
def _rm(args: list[str] | None = None, stdin: str | None = None) -> CommandResult:
    toks = args or []
    if len(toks) != 1 or toks[0].startswith("-"):
        return CommandResult("", "rm: exactly one file operand is required "
                                  "(single-file unlink only)", 1)
    target = Path(toks[0])
    if not target.is_absolute():
        target = Path(_WORKSPACE) / target

    trash_root = Path(_WORKSPACE) / _TRASH_DIR
    try:
        target.relative_to(trash_root)
    except ValueError:
        pass
    else:
        return CommandResult(
            "",
            f"rm: .trash/{target.name} is already deleted. "
            f"To destroy it permanently, remove it outside the sandbox.",
            1,
        )

    if target.is_dir():
        return CommandResult("", f"rm: {target.name} is a directory "
                                  "(only regular files can be removed)", 1)
    if not target.exists():
        return CommandResult("", f"rm: {target.name}: No such file or directory\n", 1)

    trash_root.mkdir(exist_ok=True, parents=True)
    dest = _unique_trash_path(trash_root, target.name)

    try:
        os.rename(target, dest)
    except OSError as e:
        return CommandResult("", f"rm: {e}", 1)

    return CommandResult("", f"moved {target.name} → .trash/{dest.name}\n", 0)


def _unique_trash_path(trash_dir: Path, name: str) -> Path:
    dest = trash_dir / name
    if not dest.exists():
        return dest
    stem, dot, ext = name.rpartition(".")
    suffix = ("." + ext) if dot else ""
    n = 1
    while dest.exists():
        dest = trash_dir / f"{stem}.{n}{suffix}"
        n += 1
    return dest


# ===========================================================================
# Interpreter
# ===========================================================================

_UNSUPPORTED = {"<", ">>", "&&", "||", ";", "(", ")", "&", "`", "|&"}
_PATTERN_FLAGS = {"-name", "-iname", "-path", "-ipath", "-regex", "-wholename"}
_WORKSPACE = SANDBOX_CONFIG["host_data_dir"]
_TRASH_DIR = ".trash"   # workspace-relative; rm's destination, no purge

_REDIRECT_RE = re.compile(r'^(\d?)(>)(/dev/null|&[12])$')
_SEP_REDIRECT_RE = re.compile(r'^([12]?)>$')
_SEP_REDIRECT_TARGETS = {"/dev/null", "&1", "&2"}


def _parse_redirect(token: str):
    m = _REDIRECT_RE.match(token)
    if not m:
        return None
    fd = int(m.group(1)) if m.group(1) else 1
    tgt = m.group(3)
    if tgt == "/dev/null":
        return (fd, "devnull")
    return (fd, int(tgt[1]))


def _extract_redirects(rest: list[str]) -> tuple[list[str], dict[int, object]]:
    redirects: dict[int, object] = {}
    filtered = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        r = _parse_redirect(tok)
        if r is not None:
            redirects[r[0]] = r[1]
        else:
            m = _SEP_REDIRECT_RE.match(tok)
            if m and i + 1 < len(rest) and rest[i + 1] in _SEP_REDIRECT_TARGETS:
                fd = int(m.group(1)) if m.group(1) else 1
                tgt = rest[i + 1]
                if tgt == "/dev/null":
                    redirects[fd] = "devnull"
                else:
                    redirects[fd] = int(tgt[1])
                i += 1
            else:
                filtered.append(tok)
        i += 1
    return filtered, redirects


def _glob_ws(pattern: str) -> list[str]:
    """Glob anchored to the workspace. glob.glob(root_dir=) needs Python >= 3.10;
    on older interpreters the join-and-strip fallback below produces the same
    result for workspace-relative patterns (the only ones we expand — see
    _split)."""
    if sys.version_info >= (3, 10):
        return glob.glob(pattern, recursive=True, root_dir=_WORKSPACE)
    hits = glob.glob(os.path.join(_WORKSPACE, pattern), recursive=True)
    return [os.path.relpath(h, _WORKSPACE) for h in hits]


def _lex(line: str) -> list[tuple[str, tuple[bool, ...]]]:
    tokens, chars, protected = [], [], []
    quote = None
    escaped = False
    escaped_in_double = False
    started = False

    def finish():
        nonlocal chars, protected, started
        if started:
            tokens.append(("".join(chars), tuple(protected)))
            chars, protected, started = [], [], False

    for char in line:
        if escaped:
            if escaped_in_double and char not in {'"', "\\"}:
                chars.append("\\")
                protected.append(True)
            chars.append(char)
            protected.append(True)
            escaped = False
            escaped_in_double = False
            continue
        if quote == "'":
            if char == "'":
                quote = None
            else:
                chars.append(char)
                protected.append(True)
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char == "\\":
                escaped = True
                escaped_in_double = True
            else:
                chars.append(char)
                protected.append(True)
            continue
        if char in {"'", '"'}:
            quote = char
            started = True
        elif char == "\\":
            escaped = True
            started = True
        elif char.isspace():
            finish()
        else:
            chars.append(char)
            protected.append(False)
            started = True

    if escaped:
        raise ValueError("No escaped character")
    if quote is not None:
        raise ValueError("No closing quotation")
    finish()
    return tokens


def _split_tokens(line: str) -> list[tuple[str, bool]]:
    tokens = _lex(line)
    out, prev = [], None
    for t, protected in tokens:
        # Expansion is clamped to workspace-relative tokens. Quoted or escaped
        # glob metacharacters are escaped in the glob pattern.
        operator = not any(protected) and t in (_UNSUPPORTED | {"|"})
        # Expansion is clamped to workspace-RELATIVE tokens: under PodmanRunner,
        # expanding an absolute glob like /etc/* would match on the HOST
        # filesystem, not inside the container. Absolute glob tokens pass
        # through literally and the command reports them as missing — safe and
        # self-explaining.
        pattern = "".join(
            glob.escape(char) if quoted else char
            for char, quoted in zip(t, protected)
        )
        has_unquoted_glob = any(
            not quoted and char in "*?["
            for char, quoted in zip(t, protected)
        )
        if (prev not in _PATTERN_FLAGS and not t.startswith("/")
                and has_unquoted_glob):
            out.extend((match, False) for match in sorted(_glob_ws(pattern)) or [t])
        else:
            out.append((t, operator))
        prev = t
    return out


def _split(line: str) -> list[str]:
    return [token for token, _ in _split_tokens(line)]


def _positionals(rest: list[str], *, skip_digit_values: bool) -> list[str]:
    """Operand tokens for path classification: not an option token, not '-'
    (stdin/stdout marker — never a path), and, when skip_digit_values, not a
    pure-digit token immediately following an option ('head -n 5' — the digit
    is an option value, not a file). Tokens after '--' are operands regardless
    of spelling. A bare '-' never marks the next token as an option value."""
    out, after_ddash, prev_option = [], False, False
    for t in rest:
        if not after_ddash and t == "--":
            after_ddash = True
            prev_option = False
        elif after_ddash:
            out.append(t)
            prev_option = False
        elif t.startswith("-"):
            prev_option = t != "-"
        elif skip_digit_values and prev_option and t.isdigit():
            prev_option = False
        else:
            out.append(t)
            prev_option = False
    return out


def _get_leading_paths(rest: list[str], spec: CommandSpec, kind: str) -> list[str]:
    """find: start-point operands precede the first predicate token; '!'
    is a predicate operator, never a path."""
    out = []
    for t in rest:
        if t.startswith("-") or t == "!":
            break
        out.append(t)
    return out


_PATH_STRATEGIES = {
    "none": lambda r, s, k: [],
    "diff_headers": lambda r, s, k: [],
    "all_positionals": lambda r, s, k: _positionals(r, skip_digit_values=(k == "read")),
    "positionals_after_nonfile": lambda r, s, k: _positionals(r, skip_digit_values=(k == "read"))[s.nonfile_operands:],
    "leading_positionals": _get_leading_paths,
}


def _stage_paths(rest: list[str], spec: CommandSpec, kind: str) -> list[str]:
    """Path operands of a stage per its CommandSpec classification (R10).
    kind is 'read', 'write', or 'delete'. Sole operand source for the
    stdin-refusal check and (Change 10) pre-dispatch PathGuard checks."""
    desc = getattr(spec, f"{kind}_paths")
    strategy = _PATH_STRATEGIES.get(desc)
    if strategy is None:
        raise ValueError(f"unknown path descriptor: {desc}")
    return strategy(rest, spec, kind)


def _call(func, args, stdin) -> CommandResult:
    try:
        return func(args=args, stdin=stdin)
    except Exception as e:
        return CommandResult("", f"{type(e).__name__}: {e}", 1)


class MiniShell:
    """Pipe-only interpreter: quote-aware tokenization, '|' stage splitting,
    per-stage dispatch through CommandSpecs, fail-fast error reporting."""

    def __init__(self, registry, runner, grant, guard):
        self._reg = registry
        self._runner = runner
        self._grant = grant
        self._guard = guard

    def _check_stage_paths(self, spec, rest, buffer=None):
        for p in _stage_paths(rest, spec, "read"):
            self._guard.check_read(p)
        for p in _stage_paths(rest, spec, "delete"):
            self._guard.check_delete(p)
        if spec.write_paths == "diff_headers":
            if self._guard.write_needs_target_validation():
                self._check_patch_targets(spec, rest, buffer)
            return
        for p in _stage_paths(rest, spec, "write"):
            self._guard.check_write(p)

    def _check_patch_targets(self, spec, rest, buffer):
        """R11: under a patterned write grant, patch's write targets are the
        paths named in the diff's ---/+++ headers. patch's operand grammar
        ([origfile [patchfile]]) lets an operand override those names, which
        header extraction cannot see — so scoped patch takes the diff from
        stdin only, with no file operands."""
        if "-" in rest or _stage_paths(rest, spec, "read"):
            raise PermissionError(
                "patch: file operands and '-' are disabled under a patterned write "
                "grant; pipe the unified diff in on stdin (targets are authorized "
                "from the ---/+++ headers)")
        if buffer is None:
            raise PermissionError(
                "patch: pipe the unified diff in on stdin so its target paths can "
                "be authorized before dispatch")
        try:
            targets = _patch_target_paths(buffer)
        except ValueError as e:
            raise PermissionError(f"patch: {e}") from None
        for t in targets:
            self._guard.check_write(t)

    def execute(self, line: str) -> CommandResult:
        try:
            tokens = _split_tokens(line)
        except ValueError as e:
            return CommandResult("", f"shell syntax error: {e}", 2)
        stages, cur = [], []
        for t, operator in tokens:
            if operator and t == "|":
                stages.append(cur); cur = []
            elif operator and t in _UNSUPPORTED:
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
            if not _allowed(spec, self._grant):
                return CommandResult("", f"permission denied: {name} requires "
                                         f"{' '.join(sorted(spec.caps))}", 126)
            if spec.validate:
                try:
                    spec.validate(rest)
                except ValueError as e:
                    return CommandResult("", f"{name}: {e}", 1)
            rest, redirects = _extract_redirects(rest)
            try:
                self._check_stage_paths(spec, rest, buffer)
            except PermissionError as e:
                fail_msg = f"pipeline stage {i} ({name}) denied, exit 126\n{e}"
                emit_invocation("❌️", fail_msg)
                return CommandResult("", fail_msg, 126)
            if spec.native:
                res = _call(spec.func, rest, buffer)
            else:
                if (spec.consumes_stdin and buffer is None
                        and not _stage_paths(rest, spec, "read")
                        and not _stage_paths(rest, spec, "write")):
                    return CommandResult("", f"{name} reads stdin; give it file arguments "
                                             f"or pipe something into it", 1)
                res = self._runner.run([*spec.argv, *rest], buffer, spec.caps, redirects)
            if not res.is_success:
                return CommandResult(res.stdout,
                                     f"pipeline stage {i} ({name}) failed, exit {res.exit_code}\n{res.stderr}",
                                     res.exit_code)
            buffer = res.stdout
        return CommandResult(buffer or "", "", 0)


# ===========================================================================
# Assembly
# ===========================================================================

# 1. Registry from native function namespace
_REGISTRY: CommandRegistry = CommandRegistry.from_namespace(dict(globals()))

# 2. Data-driven registration of Runner commands (Binary wrappers)
_BINARY_DEFS = [
    {"name": "ls", "doc": "List directory contents.", "read_paths": "all_positionals"},
    {"name": "pwd", "doc": "Print the current working directory."},
    {"name": "whoami", "doc": "Print the effective user name."},
    {"name": "which", "doc": "Locate a command in PATH."},
    {"name": "basename", "doc": "Strip directory and optional suffix from a filename."},
    {"name": "dirname", "doc": "Strip the last component from a filename."},
    {"name": "stat", "doc": "Display file or filesystem status.", "read_paths": "all_positionals"},
    {"name": "file", "doc": "Determine file type (-f/--files-from are disabled).",
     "read_paths": "all_positionals", "validate": lambda r: _validate_args(r, "file")},
    {"name": "du", "doc": "Estimate file and directory space usage.", "read_paths": "all_positionals"},
    {"name": "df", "doc": "Report filesystem disk space usage.", "read_paths": "all_positionals"},
    {"name": "cat", "doc": "Concatenate files to stdout.", "consumes_stdin": True,
     "read_paths": "positionals_after_nonfile"},
    {"name": "head", "doc": "Print the first lines of each file (default 10).", "consumes_stdin": True,
     "read_paths": "positionals_after_nonfile"},
    {"name": "tail", "doc": "Print the last lines of each file (default 10).", "consumes_stdin": True,
     "read_paths": "positionals_after_nonfile"},
    {"name": "wc", "doc": "Count lines, words, and bytes.", "consumes_stdin": True,
     "read_paths": "positionals_after_nonfile"},
    {"name": "uniq", "doc": "Report or omit repeated lines (input should be sorted; output files are disabled).",
     "consumes_stdin": True, "validate": lambda r: _validate_args(r, "uniq"), "read_paths": "positionals_after_nonfile"},
    {"name": "diff", "doc": "Compare files line by line ('-' reads stdin; -X/--exclude-from and --from-file/--to-file are disabled).",
     "consumes_stdin": True, "read_paths": "positionals_after_nonfile", "validate": lambda r: _validate_args(r, "diff")},
    {"name": "grep", "doc": "Print lines matching a pattern: grep [-i -v -n -c -E -F] PATTERN [FILE...]",
     "consumes_stdin": True, "nonfile_operands": 1, "read_paths": "positionals_after_nonfile", "validate": lambda r: _validate_args(r, "grep")},
    {"name": "sed", "doc": "Stream editor: sed 's/old/new/[g]' [FILE...] (sandboxed: -i, -f, w, W, r, e disabled)",
     "consumes_stdin": True, "nonfile_operands": 1, "argv": ("sed", "--sandbox"),
     "validate": lambda r: _validate_args(r, "sed"), "read_paths": "positionals_after_nonfile"},
    {"name": "cut", "doc": "Extract fields or characters per line; stdin-only in v1 — pipe into it.", "consumes_stdin": True},
    {"name": "tr", "doc": "Translate, squeeze, or delete characters; stdin-only in v1 — pipe into it.", "consumes_stdin": True},
    {"name": "sort", "doc": "Sort lines of text (-o and -T are disabled; use | tee <file>).",
     "consumes_stdin": True, "read_paths": "positionals_after_nonfile", "validate": lambda r: _validate_args(r, "sort")},
    {"name": "date", "doc": "Print the current date and time (-s/--set and -f/--file are disabled).",
     "validate": lambda r: _validate_args(r, "date")},
    {"name": "find", "doc": "Search a directory tree: find . -name '*.py' (destructive predicates disabled)",
     "validate": lambda r: _validate_args(r, "find"), "read_paths": "leading_positionals"},
    {"name": "patch", "doc": "Apply a unified diff to files (pipe the diff in; under a patterned write grant every diff-header target is checked)",
     "caps": "write", "consumes_stdin": True, "read_paths": "all_positionals", "write_paths": "diff_headers",
     "argv": ("patch", "-p1", "--no-backup-if-mismatch", "--reject-file=/dev/null"), "validate": lambda r: _validate_args(r, "patch")},
    {"name": "tee", "doc": "Write piped input to a file and to stdout.", "caps": "write", "consumes_stdin": True, "write_paths": "all_positionals"},
]

for d in _BINARY_DEFS:
    name = d["name"]
    _REGISTRY.register(CommandSpec(
        name=name,
        doc=d["doc"],
        caps=frozenset(d.get("caps", "read").split()),
        consumes_stdin=d.get("consumes_stdin", False),
        nonfile_operands=d.get("nonfile_operands", 0),
        read_paths=d.get("read_paths", "none"),
        write_paths=d.get("write_paths", "none"),
        delete_paths=d.get("delete_paths", "none"),
        argv=tuple(d["argv"]) if "argv" in d else (name,),
        native=False,
        func=None,
        validate=d.get("validate")
    ))

# Single source of truth for permissions (R4): cap name -> glob patterns.
_GRANT: dict[str, list[str]] = {"*": ["*"]}
_guard: PathGuard = PathGuard(_GRANT)
_runner: HostRunner | PodmanRunner = HostRunner()


def command_index() -> str:
    """Public helper: the granted command index. Fallback if toolex's schema
    generator reads the inner function's docstring instead of the wrapper's."""
    return _REGISTRY.index(_GRANT)


def _scope_phrase(verb: str, scope: tuple[bool, list[str]] | None) -> str | None:
    """Banner phrase for one capability family's dominant scope (R7); None
    when the family is not granted at all. `scope` is PathGuard._dominant's
    (anywhere, patterns) tuple."""
    if scope is None:
        return None
    anywhere, patterns = scope
    if not patterns:
        return f"{verb} (empty pattern grant — denies everything)"
    phrase = f"{verb} anywhere" if anywhere else f"{verb} in the workdir"
    if list(patterns) != ["*"]:
        phrase += f" (paths matching: {', '.join(sorted(set(patterns)))})"
    return phrase


def configure(grant, engine: str = "host"):
    """Called once by toolex after module load, BEFORE tool schemas are built."""
    global _GRANT, _guard, _runner, shell
    grant = _as_grant(grant)
    if "write_anywhere" in grant and engine == "podman":
        raise ValueError(
            "minishell: write_anywhere is not supported under --engine podman "
            "(the container must not mount the host root read-write); "
            "use --engine host, or scope the write grant to the workspace")
    _GRANT = grant
    _guard = PathGuard(_GRANT)
    read_scope = PathGuard._dominant(_GRANT, "read", "read_anywhere")
    delete_scope = PathGuard._dominant(_GRANT, "delete", "delete_anywhere")
    anywhere_read = bool(read_scope and read_scope[0])
    _runner = PodmanRunner(host_root=anywhere_read) if engine == "podman" else HostRunner()
    denied = sorted(s.name for s in _REGISTRY._by_name.values()
                    if not _allowed(s, _GRANT))
    mode = "; ".join(p for p in (_scope_phrase("read", read_scope),
                                 _scope_phrase("write",
                                     PathGuard._dominant(_GRANT, "write", "write_anywhere")),
                                 _scope_phrase("delete", delete_scope))
                     if p) or "no permissions"
    notes = [f"Permission mode: {mode}."]
    if denied:
        notes.append(f"Disabled this session: {', '.join(denied)}.")
    if engine == "podman" and anywhere_read:
        notes.append("Anywhere-read grants mount the host root read-only at /host "
                     "inside the container — prefix host paths with /host to read "
                     "them. This weakens the container boundary; --engine host is "
                     "the expected pairing for anywhere grants.")
    if _guard.write_needs_target_validation():
        notes.append("patch: write targets are authorized from the diff's ---/+++ "
                     "headers and must be workdir-relative; pipe the diff in on "
                     "stdin — file operands, -i, and absolute header paths are "
                     "disabled for scoped patch")
    if delete_scope is not None:
        (Path(_WORKSPACE) / _TRASH_DIR).mkdir(exist_ok=True)
        notes.append("rm moves files to .trash/ (no permanent delete in this "
                     "session; remove files from .trash/ outside the sandbox "
                     "to destroy them).")
    else:
        notes.append("This session has no delete primitive. If asked to delete a "
                     "file, do not simulate deletion by truncating it; state that "
                     "deletion is unavailable and give the exact command to run "
                     "outside the sandbox.")

    base_doc = (shell.__doc__ or "").split("\n\nPermission mode:")[0].rstrip()
    shell.__doc__ = (base_doc
                     + "\n\n" + " ".join(notes)
                     + "\n\nAvailable commands:\n" + _REGISTRY.index(_GRANT))


# ===========================================================================
# The LLM surface (the only _is_toolex_tool in the file)
# ===========================================================================

@tool(("read", "read_anywhere", "write", "write_anywhere", "delete", "delete_anywhere"),
      self_scoped=True, cap_match="any")
def shell(
    command_line: Annotated[
        str,
        "A minishell command line. Join stages with ' | ' — put spaces around the "
        "pipe ('a|b' reads as one token and fails). Quotes are honored. "
        "Only pipes are supported — no ';', '&&', or file-redirects. "
        "The full command index is in this description; 'help' re-prints it at runtime.",
    ] = "",
) -> CommandResult:
    """Execute one command line in minishell.

    Pipes only; the pipeline stops at the first failing stage. Available
    commands are listed below (also available at runtime via 'help').

    Semantics: '|' is the only separator — ';', '&&', '||', file-redirects, and
    subshells are rejected (exit 2) with instructions to split the work into
    multiple shell calls. The exceptions are `> /dev/null`, `2>/dev/null`, and `2>&1`.
    An unspaced pipe within a token remains part of that token (exit 127): put spaces around '|'.
    Fail-fast: the first non-zero stage ends the pipeline and the error names the stage
    and command — a deliberate divergence from bash's last-command exit
    status. Unknown commands exit 127 with a hint to run 'help'; permission
    denials exit 126; syntax errors exit 2; timeouts exit 124. A command that
    reads stdin refuses to run as the first stage without file arguments,
    instead of silently succeeding on empty input.

    If the user requests an operation no listed command supports, say so and
    provide the exact command to run outside the sandbox — never approximate
    a destructive operation with the closest destructive one available.

    Path scoping: each stage's path operands are resolved (symlink-safe)
    and checked against the session's read/write/delete grants before the
    stage runs; a denied path exits 126 and names the constraint plus the
    allowed patterns. The banner below (added when toolex configures the
    session) states the active mode and patterns.
    """
    emit_invocation("🥥", command_line)
    return MiniShell(_REGISTRY, _runner, _GRANT, _guard).execute(command_line)


__all__ = discover_tools(globals(), __name__)
