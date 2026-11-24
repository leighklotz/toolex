"""Shared constants, helpers, and fixtures for the minishell suite.

Split from test_minishell.py — the §9 test plan for minishell_tools.py,
updated for the read/write path-scoping patch set (Changes 1–13) and the
rm primitive. The groups now live in:

* test_minishell_parser.py                 parser/redirects
* test_minishell_registry_and_dispatch.py  registry, operand classification,
                                           dispatch, path grants (PathGuard),
                                           pre-dispatch enforcement, patch
                                           under a patterned write grant,
                                           rm (the delete primitive)
* test_minishell_pipeline_and_runner.py    pipeline, runner, e2e sed
* test_minishell_configure_and_schema.py   configure/assembly, schema
                                           surface, tooling decorator
                                           contract
* test_minishell_toolex.py                 toolex (unit)

Companion file: pairs with tooling-2.py, minishell_tools-2.py, and toolex-2.py —
move all four into place together (the tests import the *installed* module
names).

Run from the repo root:            pytest tests/ -q

Patch set notes (this revision):
* Grants are {cap: [patterns]} dicts now (read, read_anywhere, write,
  write_anywhere, or the '*' wildcard that toolex's `all` maps to). The old
  frozenset-of-names transport is fail-closed: _as_grant normalizes it to an
  empty grant, so every test passes dicts and one test pins the legacy
  frozenset denial explicitly.
* MiniShell takes (registry, context) with the SessionContext bundle (grant, guard, runner).
  The PathGuard is built from the same grant dict and is what actually checks path operands before
  dispatch (exit 126); _allowed remains the name-level gate.
* FakeRunner matches the runner contract's full signature — the interpreter
  hands runners a fourth `redirects` argument (the `> /dev/null` family) —
  and records it in a parallel .redirects list so the per-call tuples keep
  their pre-redirect shape.
* PathGuard anchors relative paths at SANDBOX_CONFIG['host_data_dir'] at
  CONSTRUCTION time. The `ws` fixture patches that key too, so guards built
  inside a test body anchor containment at the fixture workspace.
* `>` with a non-special target is NOT a redirect: the tokens pass through
  literally (pinned). Standing invariant: any future `> file` support must
  route through the write grant (check_write), never bypass PathGuard.
* configure() mutates module globals and is not idempotent: the `fresh`
  fixture reloads minishell_tools before AND after every test that uses it
  (configure / schema-surface groups, plus the rm .trash-dir test), so test
  ordering cannot leak state.
* minishell_tools._WORKSPACE is bound at import time: glob tests patch
  the module attribute (monkeypatch.setattr), not the SANDBOX_CONFIG dict.
* rm is the new native write command: a single-file move to .trash/ (no
  permanent delete in-sandbox; .trash-resident operands are refused), no
  options (so no -r/-rf recursion), exactly one operand, write-grant-gated
  and PathGuard-checked like tee; configure() creates .trash/ eagerly under
  a write grant. The "moved → .trash/" note goes to stderr and the
  interpreter discards a successful pipeline's stderr, so tests observe the
  move itself, not the note. The prompt-guardrail tests pin the two
  anti-truncation lines (static docstring + mode-conditional banner note).
* The real-schema-generator test skips (not fails) when the imported
  `toolex` lacks `generate_openai_schema` — that indicates a stale or
  shadowing `toolex` module on sys.path, i.e. an environment problem,
  not a code regression. It prints the loaded module's path so the
  shadowing copy can be found.
* toolex.parse_permissions is still xfail(strict=True): it pins the
  commit-3 contract (parser cap vocabulary). When that lands it XPASSES
  and the suite fails until the marker is removed — the signal to un-xfail.
"""

import importlib
import subprocess
import types

import pytest

import minishell_tools

# The '*' wildcard grant — what toolex's `all` normalizes to (Change 8) and
# the module's permissive standalone default (Change 7). Dicts only: the
# frozenset transport fail-closes to an empty grant.
WILD = {"*": ["*"]}

EXPECTED_COMMANDS = {
    "ls", "pwd", "whoami", "basename", "dirname", "stat", "file", "du", "df",
    "cat", "head", "tail", "wc", "uniq", "diff",
    "grep", "sed", "cut", "tr", "sort",
    "date", "find",
    "echo", "printf", "help",
    "patch", "tee", "rm",
}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class FakeRunner:
    """Records (argv, stdin, caps) per stage; returns scripted results in order.

    The interpreter passes a fourth `redirects` argument (the extracted
    `> /dev/null` family). It is recorded in the parallel .redirects list so
    the per-call tuples keep their three-element shape."""

    def __init__(self, results=None):
        self.calls = []
        self.redirects = []
        self._results = list(results or [])

    def run(self, argv, stdin, caps, redirects=None):
        self.calls.append((list(argv), stdin, frozenset(caps)))
        self.redirects.append(dict(redirects or {}))
        if self._results:
            return self._results.pop(0)
        return minishell_tools.CommandResult("", "", 0)


def run_error(line, **kw):
    res, fake = run_line(line, **kw)
    assert res.error is not None
    return res, fake


def run_line(line, granted=WILD, results=None):
    """Execute `line` through a real MiniShell over a FakeRunner.

    `granted` is a grant dict {cap: [patterns]} (or a legacy frozenset, which
    _as_grant fail-closes to an empty grant). The same normalized dict builds
    the PathGuard, which anchors relative paths at SANDBOX_CONFIG
    ['host_data_dir'] as patched by the ws fixture (import-time default
    otherwise)."""
    fake = FakeRunner(results)
    grant = minishell_tools._as_grant(granted)
    guard = minishell_tools.PathGuard(grant)
    ctx = minishell_tools.SessionContext(grant=grant, guard=guard, runner=fake)
    sh = minishell_tools.MiniShell(minishell_tools._REGISTRY, ctx)
    return sh.execute(line), fake


def names_in(index_text):
    """Command names from an index blob ('name — doc' lines; no doc contains ' — ')."""
    return {ln.split(" — ", 1)[0] for ln in index_text.splitlines() if " — " in ln}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Workspace with a.py/b.py (+ sub/c.py), patched into _WORKSPACE — and
    into SANDBOX_CONFIG['host_data_dir'], so PathGuard instances built while
    the fixture is active anchor their containment at the same directory."""
    d = tmp_path / "ws"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n")
    (d / "b.py").write_text("y = 2\n")
    sub = d / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("z = 3\n")
    monkeypatch.setattr(minishell_tools, "_WORKSPACE", str(d))
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(d))
    return d


@pytest.fixture
def fresh():
    """Pristine module for configure / schema-surface tests (reload in, reload out)."""
    mod = importlib.reload(minishell_tools)
    yield mod
    importlib.reload(minishell_tools)


@pytest.fixture
def fake_proc(monkeypatch):
    """Replaces subprocess.run; records argv/kw; outcome swappable per test."""
    log = {"argv": None, "kw": None}
    box = {"outcome": ("OUT", "ERR", 0)}   # tuple -> SimpleNamespace, Exception -> raise

    def _run(argv, **kw):
        log["argv"], log["kw"] = list(argv), kw
        out = box["outcome"]
        if isinstance(out, Exception):
            raise out
        return types.SimpleNamespace(stdout=out[0], stderr=out[1], returncode=out[2])

    monkeypatch.setattr(minishell_tools.subprocess, "run", _run)
    return log, box
