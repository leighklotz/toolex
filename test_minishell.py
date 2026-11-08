#!/usr/bin/env python3
"""test_minishell-2.py — the §9 test plan for minishell_tools.py, updated for
the read/write path-scoping patch set (Changes 1–13) and the rm primitive.

Companion file: pairs with tooling-2.py, minishell_tools-2.py, and toolex-2.py —
move all four into place together (it imports the *installed* module names).

Run from the repo root:            pytest test_minishell.py -q

Group order: parser/redirects, registry, operand classification, dispatch,
path grants (PathGuard), pre-dispatch enforcement, patch under a patterned
write grant, rm (the delete primitive), pipeline, runner, e2e sed,
configure/assembly, schema surface, tooling decorator contract, toolex (unit).

Patch-set notes (this revision):
* Grants are {cap: [patterns]} dicts now (read, read_anywhere, write,
  write_anywhere, or the '*' wildcard that toolex's `all` maps to). The old
  frozenset-of-names transport is fail-closed: _as_grant normalizes it to an
  empty grant, so every test passes dicts and one test pins the legacy
  frozenset denial explicitly.
* MiniShell takes (registry, runner, grant, guard). The PathGuard is built
  from the same grant dict and is what actually checks path operands before
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
  fixture reloads minishell_tools before AND after every test in the
  configure / schema-surface groups, so test ordering cannot leak state.
* minishell_tools._WORKSPACE is bound at import time: glob tests patch
  the module attribute (monkeypatch.setattr), not the SANDBOX_CONFIG dict.
* rm is the new native write command: single-file os.unlink, no options
  (so no -r/-rf recursion), exactly one operand, write-grant-gated and
  PathGuard-checked like tee. The prompt-guardrail tests pin the two
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
from typing import get_args, get_type_hints

import pytest

import minishell_tools

# The '*' wildcard grant — what toolex's `all` normalizes to (Change 8) and
# the module's permissive standalone default (Change 7). Dicts only: the
# frozenset transport fail-closes to an empty grant.
WILD = {"*": ["*"]}

EXPECTED_COMMANDS = {
    "ls", "pwd", "whoami", "which", "basename", "dirname", "stat", "file", "du", "df",
    "cat", "head", "tail", "wc", "uniq", "diff",
    "grep", "sed", "cut", "tr", "sort",
    "date", "find",
    "echo", "printf", "help",
    "patch", "tee", "rm",
}
assert len(EXPECTED_COMMANDS) == 29


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
    sh = minishell_tools.MiniShell(minishell_tools._REGISTRY, fake, grant, guard)
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


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def test_pipe_splits_stages():
    res, fake = run_line("ls -la | grep py")
    assert res.exit_code == 0
    assert [c[0] for c in fake.calls] == [["ls", "-la"], ["grep", "py"]]


def test_quotes_survive_tokenization():
    assert minishell_tools._split('grep "a|b" file.txt') == ["grep", "a|b", "file.txt"]


def test_quoted_pipe_is_one_stage():
    res, fake = run_line('grep "a|b" file.txt',
                         results=[minishell_tools.CommandResult("m\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls == [(["grep", "a|b", "file.txt"], None, frozenset({"read"}))]


def test_quoted_operators_and_wildcards_are_literal(ws):
    res, fake = run_line("echo '|'")
    assert res.stdout == "|\n" and fake.calls == []
    assert minishell_tools._split("echo '*.py'") == ["echo", "*.py"]
    res, fake = run_line("echo '*.py'")
    assert res.stdout == "*.py\n" and fake.calls == []


def test_mixed_quoted_and_unquoted_glob_characters(ws):
    (ws / "literal*1.txt").touch()
    (ws / "literalA1.txt").touch()
    assert minishell_tools._split("ls 'literal*'?.txt") == ["ls", "literal*1.txt"]


def test_glob_expands_sorted(ws):
    # sub/c.py must NOT match: "*.py" is not recursive across directories
    assert minishell_tools._split("ls *.py") == ["ls", "a.py", "b.py"]


def test_glob_no_match_passes_literal(ws):
    assert minishell_tools._split("ls *.zzz") == ["ls", "*.zzz"]


@pytest.mark.parametrize("flag", ["-name", "-iname"])
@pytest.mark.parametrize("quoted", [True, False])
def test_glob_suppressed_after_find_pattern_flags(ws, flag, quoted):
    pat = '"*.py"' if quoted else "*.py"
    assert minishell_tools._split(f"find . {flag} {pat}") == ["find", ".", flag, "*.py"]


def test_glob_anchored_to_workspace_not_process_cwd(tmp_path, monkeypatch):
    wsd = tmp_path / "ws"
    wsd.mkdir()
    (wsd / "a.py").write_text("")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "z.py").write_text("")          # would match if glob ran against the process cwd
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(minishell_tools, "_WORKSPACE", str(wsd))
    assert minishell_tools._split("ls *.py") == ["ls", "a.py"]


def test_glob_absolute_tokens_not_expanded(ws):
    abs_pat = str(ws / "*.py")             # exists in the workspace — still must not expand
    assert minishell_tools._split(f"ls {abs_pat}") == ["ls", abs_pat]
    assert minishell_tools._split("ls /etc/*.conf") == ["ls", "/etc/*.conf"]


# NOTE: ">" is no longer in the rejected set — only `/dev/null`, `&1`, `&2`
# targets are honored as redirects; other targets pass through literally
# (see the redirect tests below).
@pytest.mark.parametrize("op", ["<", ">>", "&&", "||", ";", "(", ")", "&", "`", "|&"])
def test_unsupported_operator_rejected(op):
    res, fake = run_line(f"echo a {op} b")
    assert res.exit_code == 2
    assert op in res.stderr and "pipes only" in res.stderr
    assert fake.calls == []


def test_devnull_redirect_is_stripped_and_handed_to_the_runner():
    res, fake = run_line("echo a > /dev/null")     # native: tokens consumed, no fork
    assert res.stdout == "a\n" and res.exit_code == 0 and fake.calls == []
    res, fake = run_line("ls > /dev/null",
                         results=[minishell_tools.CommandResult("x\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls == [(["ls"], None, frozenset({"read"}))]   # redirect stripped from argv
    assert fake.redirects == [{1: "devnull"}]


@pytest.mark.parametrize("line,redirect", [
    ("ls 2>&1", {2: 1}),
    ("ls 2>/dev/null", {2: "devnull"}),
])
def test_stderr_redirects_reach_the_runner(line, redirect):
    res, fake = run_line(line, results=[minishell_tools.CommandResult("", "", 0)])
    assert res.exit_code == 0
    assert fake.calls == [(["ls"], None, frozenset({"read"}))]
    assert fake.redirects == [redirect]


def test_bare_gt_to_a_file_is_a_literal_token_not_a_redirect(ws):
    # KEEP THIS IN PLACE UNTIL WE CAN SAFELY IMPLEMENT REDIRECTION.
    # R12 invariant: `> file` never becomes a hidden write channel — the
    # tokens pass through as ordinary arguments and no file is created.
    # (Any future `> file` support must route through PathGuard.check_write.)
    res, fake = run_line("echo a > b")
    assert res.stdout == "a > b\n" and res.exit_code == 0 and fake.calls == []
    assert not (ws / "b").exists()


def test_apply_redirects_semantics():
    cr = minishell_tools.CommandResult("out\n", "err\n", 0)
    assert minishell_tools._apply_redirects(cr, {1: "devnull"}).stdout == ""
    assert minishell_tools._apply_redirects(cr, {2: "devnull"}).stderr == ""
    merged = minishell_tools._apply_redirects(cr, {2: 1})
    assert (merged.stdout, merged.stderr) == ("out\nerr\n", "")
    swapped = minishell_tools._apply_redirects(cr, {1: 2})
    assert (swapped.stdout, swapped.stderr) == ("", "err\nout\n")


def test_unbalanced_quotes_rejected():
    res, fake = run_line('echo "abc')
    assert res.exit_code == 2
    assert "shell syntax error" in res.stderr
    assert fake.calls == []


@pytest.mark.parametrize("line", ["", "   "])
def test_blank_input_rejected(line):
    res, fake = run_line(line)
    assert res.exit_code == 2
    assert fake.calls == []


@pytest.mark.parametrize("line,stage", [
    ("ls | | grep", "stage 2"),
    ("| grep", "stage 1"),
    ("ls |", "stage 2"),
])
def test_empty_segment_rejected(line, stage):
    res, fake = run_line(line)
    assert res.exit_code == 2
    assert stage in res.stderr
    assert fake.calls == []                # must be rejected BEFORE any stage runs


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_registry_holds_exactly_29():
    assert set(minishell_tools._REGISTRY._by_name) == EXPECTED_COMMANDS


def test_registry_holds_all_regardless_of_grant():
    reg = minishell_tools._REGISTRY
    assert reg.get("patch") is not None and reg.get("patch").caps == frozenset({"write"})
    assert reg.get("tee").caps == frozenset({"write"})
    assert reg.get("rm").caps == frozenset({"write"})
    assert reg.get("ls").caps == frozenset({"read"})
    assert reg.get("no_such_command") is None


def test_covers_mapping():
    # R15: the granted cap names that cover each intrinsic capability.
    assert minishell_tools._COVERS == {
        "read": ("read", "read_anywhere"),
        "write": ("write", "write_anywhere"),
    }


def test_index_filtered_by_grant():
    reg = minishell_tools._REGISTRY
    read_names = {s.name for s in reg._by_name.values()
                  if minishell_tools._allowed(s, {"read": ["*"]})}
    assert "patch" not in read_names and "tee" not in read_names and "rm" not in read_names
    assert names_in(reg.index({"read": ["*"]})) == read_names
    # *_anywhere covers the scoped twin at name level (R15)
    anywhere = {s.name for s in reg._by_name.values()
                if minishell_tools._allowed(s, {"read_anywhere": ["*"]})}
    assert anywhere == read_names
    wild = names_in(reg.index(WILD))
    assert {"patch", "tee", "rm"} <= wild and len(wild) == 29


def test_index_header_states_the_grant_and_disabled_list():
    reg = minishell_tools._REGISTRY
    assert reg.index({"read": ["*"]}).splitlines()[0] == "grant: read=*"
    assert reg.index({"read": ["*.py"]}).splitlines()[0] == "grant: read=*.py"
    assert reg.index(WILD).splitlines()[0] == "grant: *"
    read_index = reg.index({"read": ["*"]})
    assert "disabled this session: patch, rm, tee" in read_index
    # an empty grant (the fail-closed normalization) says so explicitly
    assert "(nothing granted)" in reg.index({})


def test_index_limit_hint_and_unlimited():
    reg = minishell_tools._REGISTRY
    trunc = reg.index(WILD, limit=10)
    assert trunc.endswith("(run 'help' for the full list)")
    assert "whoami" not in trunc                       # only the first 10 chars survived
    full = reg.index(WILD, limit=None)
    assert full == reg.index(WILD, limit=10 ** 6)      # unlimited == effectively-unlimited
    assert len(names_in(full)) == 29


def test_duplicate_name_raises_at_register():
    reg = minishell_tools.CommandRegistry()

    def spec():
        return minishell_tools.CommandSpec(name="dup", doc="", caps=frozenset({"read"}))

    reg.register(spec())
    with pytest.raises(ValueError, match="duplicate"):
        reg.register(spec())


def test_caps_outside_read_write_raise_at_decoration():
    with pytest.raises(ValueError, match="read/write"):
        @minishell_tools.command("badcaps", caps="read exec")
        def _bad(args="", stdin=None):
            return minishell_tools.CommandResult()


def test_unknown_path_descriptor_raises_at_decoration():
    with pytest.raises(ValueError, match="path descriptor"):
        @minishell_tools.command("badpath", read_paths="everywhere")
        def _bad(args="", stdin=None):
            return minishell_tools.CommandResult()


def test_index_tags_stdin_commands():
    by_name = {ln.split(" — ", 1)[0]: ln
               for ln in minishell_tools._REGISTRY.index(WILD).splitlines()}
    for name in ("cat", "head", "tail", "wc", "uniq", "diff", "grep", "sed",
                 "cut", "tr", "sort", "patch", "tee"):
        assert "[stdin]" in by_name[name], name
    for name in ("ls", "pwd", "date", "find", "echo", "printf", "help", "rm"):
        assert "[stdin]" not in by_name[name], name


# ---------------------------------------------------------------------------
# operand classification (R10) — read_paths/write_paths replace stdin_only
# ---------------------------------------------------------------------------

def test_operand_classification_per_command():
    reg = minishell_tools._REGISTRY
    for name in ("ls", "stat", "file", "du", "df"):
        assert reg.get(name).read_paths == "all_positionals", name
    for name in ("cat", "head", "tail", "wc", "uniq", "diff", "grep", "sed", "sort"):
        spec = reg.get(name)
        assert spec.read_paths == "positionals_after_nonfile", name
        assert spec.write_paths == "none", name
    assert reg.get("grep").nonfile_operands == 1 and reg.get("sed").nonfile_operands == 1
    assert reg.get("find").read_paths == "leading_positionals"
    # recorded deviation: sort/uniq keep their file operand read-classified so
    # `sort f.txt` does not regress; the output side stays banned by validators.
    assert reg.get("sort").write_paths == "none"
    assert reg.get("uniq").write_paths == "none"
    assert reg.get("patch").read_paths == "all_positionals"
    assert reg.get("patch").write_paths == "diff_headers"
    assert reg.get("tee").write_paths == "all_positionals"
    assert reg.get("tee").read_paths == "none"
    # rm: single-file unlink — its one operand is write-classified, no reads.
    assert reg.get("rm").write_paths == "all_positionals"
    assert reg.get("rm").read_paths == "none"
    assert reg.get("rm").native is True
    for name in ("pwd", "whoami", "which", "basename", "dirname", "date",
                 "echo", "printf", "help", "cut", "tr"):
        spec = reg.get(name)
        assert spec.read_paths == "none" and spec.write_paths == "none", name


def test_deleted_names_stay_deleted():
    # Change 13's stale-string sweep, as executable assertions.
    assert not hasattr(minishell_tools.CommandSpec, "stdin_only")
    assert not hasattr(minishell_tools, "_has_file_operand")
    assert not hasattr(minishell_tools, "_GRANTED")


def test_as_grant_normalizes_and_fail_closes():
    # legacy frozenset transport cannot carry patterns → empty grant
    assert minishell_tools._as_grant(frozenset({"read", "write"})) == {}
    assert minishell_tools._as_grant("not-a-dict") == {}
    # a bare string pattern is wrapped, not iterated
    assert minishell_tools._as_grant({"read": "*.py"}) == {"read": ["*.py"]}
    assert minishell_tools._as_grant({"read": ["a", "b"]}) == {"read": ["a", "b"]}


def test_legacy_frozenset_transport_fail_closes(ws):
    res, fake = run_line("cat a.py", granted=frozenset({"read"}))
    assert res.exit_code == 126
    assert "permission denied" in res.stderr
    assert fake.calls == []
    assert "(nothing granted)" in minishell_tools._REGISTRY.index(frozenset({"read"}))


def test_digit_option_value_is_not_a_file_operand():
    # Change 6 delta: `head -n 5` without stdin must refuse, not dispatch.
    res, fake = run_line("head -n 5")
    assert res.exit_code == 1
    assert res.stderr == "head reads stdin; give it file arguments or pipe something into it"
    assert fake.calls == []


def test_ddash_marks_following_tokens_as_operands():
    # Change 6 delta: `cat -- -file` works (the '-' is an operand, not an option).
    res, fake = run_line("cat -- -file",
                         results=[minishell_tools.CommandResult("ok\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls[0][0] == ["cat", "--", "-file"]


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def test_unknown_command_127():
    res, fake = run_line("frobnicate x")
    assert res.exit_code == 127
    assert "command not found: frobnicate" in res.stderr
    assert "run 'help'" in res.stderr
    assert fake.calls == []


def test_unspaced_pipe_is_single_unknown_token():
    res, fake = run_line("ls|grep x")
    assert res.exit_code == 127
    assert "ls|grep" in res.stderr
    assert fake.calls == []


@pytest.mark.parametrize("line,msg", [
    ("patch f.diff", "permission denied: patch requires write"),
    ("tee out.txt", "permission denied: tee requires write"),
    ("rm out.txt", "permission denied: rm requires write"),
])
def test_write_denied_under_read_grant(line, msg):
    res, fake = run_line(line, granted={"read": ["*"]})
    assert res.exit_code == 126
    assert res.stderr == msg
    assert fake.calls == []


@pytest.mark.parametrize("line,frag", [
    ("find . -delete", "destructive find predicates"),
    ("find x -exec rm {}", "destructive find predicates"),
    ("find x -execdir rm {}", "destructive find predicates"),
    ("find . -ok rm {}", "destructive find predicates"),
    ("find . -fprint0 out.txt", "destructive find predicates"),
    ("sed -i s/a/b/ f.txt", "sed -i is disabled"),
    ("sed -i.bak s/a/b/ f.txt", "sed -i is disabled"),
    ("sed -ni 's/a/b/p' input.txt", "sed -i is disabled"),
    ("sed --in-place s/a/b/ f.txt", "sed -i is disabled"),
    ("sort -o out.txt", "sort -o is disabled"),
    ("sort -ofile.txt", "sort -o is disabled"),
    ("sort -ro output.txt input.txt", "sort -o is disabled"),
    ("sort --output out.txt", "sort -o is disabled"),
    ("sort --compress-program=cat", "sort --compress-program is disabled"),
    ("sort --compress-program cat", "sort --compress-program is disabled"),
    ("date -s 2024-01-01", "date -s/-f are disabled"),
    ("date --set=2024-01-01", "date -s/-f are disabled"),
    ("uniq input.txt output.txt", "uniq output operands are disabled"),
    ("uniq - output.txt", "uniq output operands are disabled"),
    # Change 9 hardening (grant-free validators — banned under any grant):
    ("date -f /etc/dates", "date -s/-f are disabled"),
    ("date --file=/etc/dates", "date -s/-f are disabled"),
    ("sed -f script.sed in.txt", "sed -f is disabled"),
    ("sed --file=script.sed in.txt", "sed --file is disabled"),
    ("sort -T /tmp", "sort -T is disabled"),
    ("sort --random-source=/dev/urandom", "sort --random-source is disabled"),
    ("sort --files0-from=list.txt", "sort --files0-from is disabled"),
    ("sort --temporary-directory=/tmp", "sort --temporary-directory is disabled"),
    ("grep -f pats.txt file.txt", "grep -f is disabled"),
    ("grep --exclude-from=x foo f.txt", "grep --exclude-from is disabled"),
    ("grep --include-from=x foo f.txt", "grep --include-from is disabled"),
    ("diff -X excl a b", "diff exclude/from-file options are disabled"),
    ("diff --from-file=old new", "diff exclude/from-file options are disabled"),
    ("file -f list.txt", "file -f is disabled"),
    ("file --files-from=list.txt", "file -f is disabled"),
    ("patch -p2 x.diff", "option -p is disabled"),
    ("patch -b x.diff", "option -b is disabled"),
    ("patch --output=out x.diff", "option --output is disabled"),
    # rm options are all banned — single-file unlink only, no -r/-rf ever:
    ("rm -r dir", "rm options are disabled"),
    ("rm -rf dir", "rm options are disabled"),
    ("rm -f file.txt", "rm options are disabled"),
    ("rm -i file.txt", "rm options are disabled"),
    ("rm --recursive dir", "rm options are disabled"),
    ("rm -- file.txt", "rm options are disabled"),
])
def test_validators_block_destructive_flags(line, frag):
    res, fake = run_line(line)
    assert res.exit_code == 1
    assert frag in res.stderr
    assert fake.calls == []


def test_sed_option_argument_is_not_misread_as_in_place_flag():
    res, fake = run_line("sed -e 's/a/i/' file.txt",
                         results=[minishell_tools.CommandResult("ok\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls[0][0] == ["sed", "--sandbox", "-e", "s/a/i/", "file.txt"]


def test_validator_passthrough_and_caps_flow():
    res, fake = run_line("find . -name '*.py'",
                         results=[minishell_tools.CommandResult("a.py\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls == [(["find", ".", "-name", "*.py"], None, frozenset({"read"}))]


def test_bare_dot_find_reaches_runner():
    res, fake = run_line("find .", results=[minishell_tools.CommandResult("", "", 0)])
    assert res.exit_code == 0
    assert fake.calls[0][0] == ["find", "."]


@pytest.mark.parametrize("line,cmd", [
    ("grep foo", "grep"),
    ("sed s/a/b/", "sed"),
    ("tr a b", "tr"),
    ("cut -d, -f1", "cut"),
    ("head -5", "head"),
])
def test_stdin_guard_denials_message_text(line, cmd):
    res, fake = run_line(line)
    assert res.exit_code == 1
    assert res.stderr == f"{cmd} reads stdin; give it file arguments or pipe something into it"
    assert fake.calls == []


@pytest.mark.parametrize("line,argv", [
    ("cat notes.txt", ["cat", "notes.txt"]),
    ("grep foo file.py", ["grep", "foo", "file.py"]),
    ("sed s/a/b/ file.py", ["sed", "--sandbox", "s/a/b/", "file.py"]),
])
def test_stdin_guard_passes_with_file_operand(line, argv):
    res, fake = run_line(line, results=[minishell_tools.CommandResult("ok\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls[0][0] == argv


def test_mid_pipeline_stdin_guard_does_not_fire():
    res, fake = run_line("cat f.txt | grep foo",
                         results=[minishell_tools.CommandResult("hello foo\n", "", 0),
                                  minishell_tools.CommandResult("hello foo\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls[0][1] is None
    assert fake.calls[1][1] == "hello foo\n"


def test_help_untruncated_matches_registry():
    res, fake = run_line("help")
    assert res.exit_code == 0
    assert fake.calls == []                              # native — no fork
    assert res.stdout == minishell_tools._REGISTRY.index(minishell_tools._GRANT, limit=None) + "\n"
    assert len(names_in(res.stdout)) == 29
    assert "run 'help' for the full list" not in res.stdout


# ---------------------------------------------------------------------------
# path grants (PathGuard unit)  [Changes 5, 7]
# ---------------------------------------------------------------------------

def test_pathguard_wildcard_grant_reads_and_writes_anywhere():
    guard = minishell_tools.PathGuard({"*": ["*"]})
    guard.check_read("/etc/hostname")        # anywhere scope: absolute host paths
    guard.check_write("/tmp/anything.txt")
    guard.check_read("notes.txt")            # relative operands anchor at the workdir


def test_pathguard_no_grant_denies_everything():
    guard = minishell_tools.PathGuard({})
    with pytest.raises(PermissionError, match="needs a read grant"):
        guard.check_read("a.py")
    with pytest.raises(PermissionError, match="needs a write grant"):
        guard.check_write("a.py")


def test_pathguard_scoped_requires_workspace_containment(ws):
    guard = minishell_tools.PathGuard({"read": ["*"]})
    guard.check_read("a.py")                                     # contained → OK
    with pytest.raises(PermissionError, match="outside the working directory"):
        guard.check_read(str(ws.parent / "elsewhere.txt"))


def test_pathguard_scoped_patterns_match_workspace_relative(ws):
    guard = minishell_tools.PathGuard({"read": ["*.py"]})
    guard.check_read("a.py")
    guard.check_read("sub/c.py")        # fnmatch '*' crosses '/' (file_tools parity)
    with pytest.raises(PermissionError, match="does not match permitted read patterns"):
        guard.check_read("notes.txt")


def test_pathguard_anywhere_matches_absolute_patterns():
    guard = minishell_tools.PathGuard({"read_anywhere": ["/etc/*"]})
    guard.check_read("/etc/hostname")
    with pytest.raises(PermissionError, match="does not match permitted read patterns"):
        guard.check_read("/var/log/messages")


def test_pathguard_anywhere_dominates_scoped_and_replaces_its_patterns(ws):
    guard = minishell_tools.PathGuard({"read": ["*.py"], "read_anywhere": ["/etc/*"]})
    guard.check_read("/etc/hostname")     # the anywhere cap wins the family…
    with pytest.raises(PermissionError):  # …and ITS patterns replace the scoped ones
        guard.check_read("a.py")


def test_pathguard_write_family_is_independent():
    guard = minishell_tools.PathGuard({"read_anywhere": ["*"]})
    guard.check_read("/etc/hostname")
    with pytest.raises(PermissionError, match="needs a write grant"):
        guard.check_write("out.txt")


def test_pathguard_empty_pattern_list_denies_everything(ws):
    guard = minishell_tools.PathGuard({"read": [], "write": []})
    with pytest.raises(PermissionError, match="does not match permitted read patterns"):
        guard.check_read("a.py")
    with pytest.raises(PermissionError, match="does not match permitted write patterns"):
        guard.check_write("a.py")
    assert guard.write_is_patterned() is True


def test_pathguard_symlink_escape_is_denied(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x\n")
    (ws / "link").symlink_to(outside)
    guard = minishell_tools.PathGuard({"read": ["*"]})
    with pytest.raises(PermissionError, match="outside the working directory"):
        guard.check_read("link/secret.txt")


def test_pathguard_write_is_patterned_sentinel():
    assert minishell_tools.PathGuard({"write": ["*"]}).write_is_patterned() is False
    assert minishell_tools.PathGuard({"write": ["*.log"]}).write_is_patterned() is True
    assert minishell_tools.PathGuard({"*": ["*"]}).write_is_patterned() is False
    assert minishell_tools.PathGuard({"read": ["*"]}).write_is_patterned() is False
    assert minishell_tools.PathGuard({}).write_is_patterned() is False


def test_pathguard_denial_message_names_constraint_and_patterns(ws):
    guard = minishell_tools.PathGuard({"read": ["*.py"]})
    with pytest.raises(PermissionError) as ei:
        guard.check_read("notes.txt")
    msg = str(ei.value)
    assert msg.startswith("Access Denied") and "notes.txt" in msg and "'*.py'" in msg


def test_pathguard_ignores_the_env_transport(monkeypatch, ws):
    # standing invariant: PathGuard must not use TOOLEX_PERMITTED_PATH_PATTERNS
    monkeypatch.setenv("TOOLEX_PERMITTED_PATH_PATTERNS", "*")
    guard = minishell_tools.PathGuard({})          # env var must not leak in
    with pytest.raises(PermissionError):
        guard.check_read("a.py")


# ---------------------------------------------------------------------------
# pre-dispatch path enforcement  [Changes 5, 10]
# ---------------------------------------------------------------------------

def test_scoped_read_denial_renders_stage_and_exit_126(ws):
    res, fake = run_line("cat notes.txt", granted={"read": ["*.py"]})
    assert res.exit_code == 126
    assert res.stderr.startswith("pipeline stage 1 (cat) denied, exit 126\nAccess Denied")
    assert "does not match permitted read patterns" in res.stderr
    assert fake.calls == []


def test_scoped_write_denial_for_tee(ws):
    res, fake = run_line("echo hi | tee out.txt",
                         granted={"read": ["*"], "write": ["*.log"]})
    assert res.exit_code == 126
    assert "pipeline stage 2 (tee) denied, exit 126" in res.stderr
    assert "does not match permitted write patterns" in res.stderr
    assert fake.calls == []                        # echo is native; tee never dispatched


def test_scoped_write_denial_for_rm(ws):
    # rm's operand is write-classified: under a patterned write grant a
    # non-matching operand is denied BEFORE the native unlink runs.
    res, fake = run_line("rm out.txt",
                         granted={"read": ["*"], "write": ["*.log"]})
    assert res.exit_code == 126
    assert "pipeline stage 1 (rm) denied, exit 126" in res.stderr
    assert "does not match permitted write patterns" in res.stderr
    assert fake.calls == []                        # native — the only fail-signal is the 126


def test_scoped_grant_still_runs_matching_paths(ws):
    res, fake = run_line("cat a.py", granted={"read": ["*.py"]},
                         results=[minishell_tools.CommandResult("x = 1\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls == [(["cat", "a.py"], None, frozenset({"read"}))]


def test_empty_pattern_grant_denies_at_dispatch_despite_name_level_grant(ws):
    # {"read": []} covers read at NAME level but denies every path — fail-closed.
    res, fake = run_line("cat a.py", granted={"read": [], "write": []})
    assert res.exit_code == 126
    assert "does not match permitted read patterns []" in res.stderr
    assert fake.calls == []


def test_scoped_grant_denies_absolute_operands(ws):
    res, fake = run_line("cat /etc/passwd", granted={"read": ["*"]})
    assert res.exit_code == 126
    assert "outside the working directory" in res.stderr
    assert fake.calls == []


def test_scoped_rm_denies_absolute_operands(ws):
    # The unlink operand must obey the same containment rule as tee's.
    res, fake = run_line("rm /etc/passwd", granted={"read": ["*"], "write": ["*"]})
    assert res.exit_code == 126
    assert "outside the working directory" in res.stderr
    assert fake.calls == []


def test_anywhere_grant_reads_host_paths():
    res, fake = run_line("cat /etc/passwd", granted={"read_anywhere": ["*"]},
                         results=[minishell_tools.CommandResult("root:x:0\n", "", 0)])
    assert res.exit_code == 0
    assert fake.calls == [(["cat", "/etc/passwd"], None, frozenset({"read"}))]


def test_glob_expansion_happens_before_path_checks(ws):
    # R13: expanded matches are ordinary tokens in the stage's rest — every
    # one of them is checked; nothing slips through because of the glob.
    res, fake = run_line("cat *.py", granted={"read": ["a.py"]})
    assert res.exit_code == 126
    assert "'b.py'" in res.stderr
    assert fake.calls == []


def test_validator_denial_beats_path_denial(ws):
    # Precedence: validator (exit 1) fires before the path check (126).
    res, fake = run_line("sed -i s/a/b/ a.py", granted={"read": ["*.py"]})
    assert res.exit_code == 1
    assert "sed -i is disabled" in res.stderr
    assert fake.calls == []


def test_scoped_tee_still_runs_matching_paths(ws):
    res, fake = run_line("echo hi | tee run.log",
                         granted={"read": ["*"], "write": ["*.log"]},
                         results=[minishell_tools.CommandResult("hi\n", "", 0)])
    assert res.exit_code == 0
    argv, stdin, caps = fake.calls[0]
    assert argv == ["tee", "run.log"] and stdin == "hi\n"
    assert caps == frozenset({"write"})


# ---------------------------------------------------------------------------
# patch under a patterned write grant  [Changes 10–11]
# ---------------------------------------------------------------------------

def test_patch_argv_is_pinned():
    spec = minishell_tools._REGISTRY.get("patch")
    assert spec.argv == ("patch", "-p1", "--no-backup-if-mismatch", "--reject-file=/dev/null")


def test_p1_strip_mirrors_patch_p1():
    assert minishell_tools._p1_strip("a/one.py") == "one.py"
    assert minishell_tools._p1_strip("src/deep/one.py") == "deep/one.py"
    assert minishell_tools._p1_strip("one.py") == "one.py"


def test_diff_operand_cuts_timestamp_and_quotes():
    assert minishell_tools._diff_operand(
        "a/one.py\t2024-01-01 10:00:00.000000000 +0000") == "a/one.py"
    assert minishell_tools._diff_operand('"a/sp ace.py"\t2024-01-01') == "a/sp ace.py"


def test_patch_target_paths_from_header_pairs():
    diff = ("--- a/one.py\n+++ b/one.py\n@@ -1 +1 @@\n-x\n+y\n"
            "--- /dev/null\n+++ b/two.py\n@@ -0,0 +1 @@\n+new\n")
    # /dev/null is the create/delete sentinel and names no writable target;
    # duplicates across the ---/+++ pair collapse to one entry.
    assert minishell_tools._patch_target_paths(diff) == ["one.py", "two.py"]


def test_patch_target_paths_reject_absolute_and_headerless_diffs():
    with pytest.raises(ValueError, match="unusable target"):
        minishell_tools._patch_target_paths("--- a/x.py\n+++ /tmp/evil.py\n")
    with pytest.raises(ValueError, match="header pair"):
        minishell_tools._patch_target_paths("hello\nworld\n")


def test_scoped_patch_checks_header_targets_and_pins_argv(ws):
    diff = "--- a/one.py\n+++ b/one.py\n@@ -1 +1 @@\n-x\n+y\n"
    res, fake = run_line("cat in.diff | patch",
                         granted={"read": ["*"], "write": ["*.py"]},
                         results=[minishell_tools.CommandResult(diff, "", 0),
                                  minishell_tools.CommandResult("", "", 0)])
    assert res.exit_code == 0
    assert fake.calls[1][0] == ["patch", "-p1", "--no-backup-if-mismatch",
                                "--reject-file=/dev/null"]
    assert fake.calls[1][1] == diff                    # the piped diff is the stdin
    assert fake.calls[1][2] == frozenset({"write"})


def test_scoped_patch_rejects_file_operands_and_dash(ws):
    # patch's operand grammar ([origfile [patchfile]]) could override the
    # header targets — so under a patterned write grant it is denied outright.
    grant = {"read": ["*"], "write": ["*.py"]}
    for line in ("patch in.diff", "patch -"):
        res, fake = run_line(line, granted=grant)
        assert res.exit_code == 126
        assert "file operands and '-' are disabled" in res.stderr
        assert fake.calls == []


def test_scoped_patch_requires_the_diff_on_stdin(ws):
    res, fake = run_line("patch", granted={"read": ["*"], "write": ["*.py"]})
    assert res.exit_code == 126
    assert "pipe the unified diff in on stdin" in res.stderr
    assert fake.calls == []


def test_scoped_patch_denies_targets_outside_the_write_pattern(ws):
    diff = "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-x\n+y\n"
    res, fake = run_line("cat in.diff | patch",
                         granted={"read": ["*"], "write": ["*.py"]},
                         results=[minishell_tools.CommandResult(diff, "", 0)])
    assert res.exit_code == 126
    assert "does not match permitted write patterns" in res.stderr
    assert len(fake.calls) == 1                       # only the cat stage ran


def test_scoped_patch_denies_absolute_header_targets(ws):
    diff = "--- a/x.py\n+++ /tmp/evil.py\n@@ -1 +1 @@\n-x\n+y\n"
    res, fake = run_line("cat in.diff | patch",
                         granted={"read": ["*"], "write": ["*.py"]},
                         results=[minishell_tools.CommandResult(diff, "", 0)])
    assert res.exit_code == 126
    assert "unusable target" in res.stderr
    assert len(fake.calls) == 1


def test_scoped_patch_denies_a_headerless_stdin_diff(ws):
    res, fake = run_line("echo hello | patch",
                         granted={"read": ["*"], "write": ["*.py"]},
                         results=[minishell_tools.CommandResult("hello\n", "", 0)])
    assert res.exit_code == 126
    assert "no ---/+++ header pair" in res.stderr
    assert len(fake.calls) == 0                       # echo is native


def test_unpatterned_write_grant_skips_header_extraction(ws):
    # With the ['*'] write sentinel no header parsing happens — patch behaves
    # like any stdin command (refuses without piped input) instead of being
    # denied outright. This pins the patterned/unpatterned fork.
    res, fake = run_line("patch", granted={"read": ["*"], "write": ["*"]})
    assert res.exit_code == 1
    assert "reads stdin" in res.stderr
    assert fake.calls == []


# ---------------------------------------------------------------------------
# rm — the delete primitive (native write command)
# ---------------------------------------------------------------------------

def test_rm_removes_a_single_file(ws):
    (ws / "stale.txt").write_text("gone soon\n")
    res, fake = run_line("rm stale.txt")
    assert res.exit_code == 0
    assert fake.calls == []                     # native — no fork, no runner
    assert not (ws / "stale.txt").exists()


def test_rm_missing_file_reports_error_not_crash(ws):
    res, _ = run_line("rm no_such_file.txt")
    assert res.exit_code == 1
    assert "rm:" in res.stderr                  # os.error surfaced, not an exception


def test_rm_refuses_directories(ws):
    (ws / "dir").mkdir()
    res, _ = run_line("rm dir")
    assert res.exit_code == 1                   # os.unlink cannot remove a directory
    assert (ws / "dir").is_dir()


def test_rm_requires_exactly_one_operand(ws):
    (ws / "a.txt").write_text("x\n")
    (ws / "b.txt").write_text("y\n")
    res, _ = run_line("rm")
    assert res.exit_code == 1
    assert "exactly one file operand" in res.stderr
    res, _ = run_line("rm a.txt b.txt")
    assert res.exit_code == 1
    assert "exactly one file operand" in res.stderr
    assert (ws / "a.txt").exists() and (ws / "b.txt").exists()   # nothing was unlinked


def test_rm_succeeds_under_write_grant(ws):
    (ws / "stale.log").write_text("x\n")
    res, fake = run_line("rm stale.log", granted={"read": ["*"], "write": ["*"]})
    assert res.exit_code == 0
    assert fake.calls == []
    assert not (ws / "stale.log").exists()


def test_rm_rejected_under_read_only_grant(ws):
    (ws / "precious.txt").write_text("keep me\n")
    res, fake = run_line("rm precious.txt", granted={"read": ["*"]})
    assert res.exit_code == 126
    assert "permission denied: rm requires write" in res.stderr
    assert fake.calls == []
    assert (ws / "precious.txt").exists()       # the file survived


def test_rm_denied_for_out_of_workdir_operand_even_under_write_grant(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("do not touch\n")
    res, fake = run_line(f"rm {outside}", granted={"read": ["*"], "write": ["*"]})
    assert res.exit_code == 126
    assert "outside the working directory" in res.stderr
    assert fake.calls == []
    assert outside.exists()                     # the file survived


def test_rm_operand_is_pattern_checked_under_scoped_write(ws):
    (ws / "keep.py").write_text("x\n")
    (ws / "notes.txt").write_text("y\n")
    res, fake = run_line("rm notes.txt", granted={"read": ["*"], "write": ["*.py"]})
    assert res.exit_code == 126
    assert "does not match permitted write patterns" in res.stderr
    assert fake.calls == []
    res, _ = run_line("rm keep.py", granted={"read": ["*"], "write": ["*.py"]})
    assert res.exit_code == 0
    assert not (ws / "keep.py").exists()


def test_rm_native_unlink_operates_on_the_workspace_root(ws, tmp_path):
    # Relative operands anchor at _WORKSPACE even though the toolex process
    # CWD differs — the native command has no runner cwd to inherit.
    deep = ws / "sub"
    (deep / "gone.py").write_text("z\n")
    monkey_cwd = tmp_path / "elsewhere"
    monkey_cwd.mkdir()
    import os as _os
    old_cwd = _os.getcwd()
    _os.chdir(monkey_cwd)
    try:
        res, _ = run_line("rm sub/gone.py")
        assert res.exit_code == 0
        assert not (deep / "gone.py").exists()
        assert not (monkey_cwd / "gone.py").exists()
    finally:
        _os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# pipeline (mocked runner)
# ---------------------------------------------------------------------------

def test_three_stage_buffer_chaining():
    res, fake = run_line(
        "cat f.txt | grep a | head -1",
        results=[minishell_tools.CommandResult("apple\nbanana\ncherry\n", "", 0),
                 minishell_tools.CommandResult("apple\ncherry\n", "", 0),
                 minishell_tools.CommandResult("apple\n", "", 0)])
    assert res.exit_code == 0
    assert res.stdout == "apple\n"
    assert len(fake.calls) == 3
    assert fake.calls[0][1] is None
    assert fake.calls[1][1] == "apple\nbanana\ncherry\n"
    assert fake.calls[2][1] == "apple\ncherry\n"


def test_fail_fast_names_stage_and_command():
    res, fake = run_line(
        "cat f | grep x | head",
        results=[minishell_tools.CommandResult("data\n", "", 0),
                 minishell_tools.CommandResult("", "boom", 1),
                 minishell_tools.CommandResult("NEVER", "", 0)])
    assert res.exit_code == 1
    assert len(fake.calls) == 2                        # head never ran
    assert "pipeline stage 2 (grep) failed, exit 1" in res.stderr
    assert "boom" in res.stderr
    assert res.stdout == ""                            # failing stage's stdout


def test_rm_can_sit_mid_pipeline():
    # rm produces no stdout; a following stage gets an empty buffer.
    res, fake = run_line("rm a.txt | wc",
                         results=[minishell_tools.CommandResult("0 0 0\n", "", 0)])
    assert res.exit_code == 0
    assert len(fake.calls) == 1                        # only wc forked (rm is native)
    assert fake.calls[0][0][:1] == ["wc"]
    assert fake.calls[0][1] == ""                      # rm's empty stdout fed wc


def test_truncation_caps_100k_stdout():
    big = "x" * 200_000
    res, _ = run_line("cat big.txt", results=[minishell_tools.CommandResult(big, "", 0)])
    assert res.stdout == big                           # pipeline does not truncate…
    payload = res.to_payload()
    assert payload["stdout"] == "x" * 100_000 + "\n…[truncated 100000 characters]"  # …to_payload does
    assert payload["exit_code"] == 0


def test_truncation_applies_to_stderr_independently():
    payload = minishell_tools.CommandResult("", "e" * 200_000, 1).to_payload()
    assert payload["stdout"] == ""
    assert payload["stderr"] == "e" * 100_000 + "\n…[truncated 100000 characters]"


def test_native_echo():
    res, fake = run_line("echo hello world")
    assert res.stdout == "hello world\n" and res.exit_code == 0 and fake.calls == []
    res, _ = run_line("echo")
    assert res.stdout == "\n" and res.exit_code == 0


@pytest.mark.parametrize("line,expect", [
    (r"printf 'x%sy\n' A", "xAy\n"),
    (r"printf '%d-%f\n' 7 2.5", "7-2.500000\n"),
    (r"printf '50%%\n'", "50%\n"),
])
def test_native_printf(line, expect):
    res, fake = run_line(line)
    assert res.exit_code == 0 and res.stdout == expect and fake.calls == []


def test_printf_missing_argument():
    res, _ = run_line(r"printf '%d %d' 1")
    assert res.exit_code == 1
    assert "not enough arguments" in res.stderr


def test_printf_integer_conversion_rejects_non_numeric():
    res, _ = run_line("printf '%d' abc")
    assert res.exit_code == 1
    assert "invalid literal" in res.stderr


def test_caps_flow_from_spec_and_piped_stdin_reaches_runner():
    res, fake = run_line("echo hi | tee out.txt",
                         results=[minishell_tools.CommandResult("hi\n", "", 0)])
    assert res.exit_code == 0
    argv, stdin, caps = fake.calls[0]
    assert argv == ["tee", "out.txt"]
    assert stdin == "hi\n"                             # native echo's stdout fed the stage
    assert caps == frozenset({"write"})                # caps came from the spec, not a wrapper


# ---------------------------------------------------------------------------
# runner (monkeypatched subprocess.run — no real podman, no real processes)
# ---------------------------------------------------------------------------

def test_host_runner_argv_input_cwd_contract(tmp_path, monkeypatch, fake_proc):
    log, _ = fake_proc
    d = tmp_path / "ws"
    d.mkdir()
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(d))
    r = minishell_tools.HostRunner().run(["ls", "-l"], "hello", frozenset({"read"}))
    assert (r.stdout, r.stderr, r.exit_code) == ("OUT", "ERR", 0)
    assert log["argv"] == ["ls", "-l"]
    kw = log["kw"]
    assert kw["input"] == "hello" and "stdin" not in kw
    assert kw["cwd"] == str(d)                         # pinned to the workspace
    assert kw["capture_output"] is True and kw["text"] is True
    assert kw["timeout"] == 60


def test_host_runner_stdin_none_uses_devnull(tmp_path, monkeypatch, fake_proc):
    log, _ = fake_proc
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(tmp_path))
    minishell_tools.HostRunner().run(["pwd"], None, frozenset({"read"}))
    assert log["kw"]["stdin"] is subprocess.DEVNULL and "input" not in log["kw"]


def test_host_runner_timeout_exit_124(fake_proc):
    log, box = fake_proc
    box["outcome"] = subprocess.TimeoutExpired("tail -f", 60)
    r = minishell_tools.HostRunner().run(["tail", "-f"], None, frozenset({"read"}))
    assert r.exit_code == 124
    assert r.stderr == "timed out after 60s"
    assert r.stdout == ""


def test_host_runner_oserror_exit_1(fake_proc):
    log, box = fake_proc
    box["outcome"] = FileNotFoundError(2, "No such file or directory")
    r = minishell_tools.HostRunner().run(["ls"], None, frozenset({"read"}))
    assert r.exit_code == 1
    assert "FileNotFoundError" in r.stderr
    assert r.stdout == ""


@pytest.mark.parametrize("caps,mode", [(frozenset({"read"}), "ro"), (frozenset({"write"}), "rw")])
def test_podman_runner_isolation_contract(tmp_path, monkeypatch, fake_proc, caps, mode):
    log, _ = fake_proc
    d = tmp_path / "ws"
    d.mkdir()
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(d))
    r = minishell_tools.PodmanRunner().run(["ls"], None, caps)
    cmd = log["argv"]
    assert cmd[0] == "podman" and "--rm" in cmd
    assert "--net=none" in cmd
    assert cmd[cmd.index("--workdir") + 1] == "/workspace"
    assert cmd[cmd.index("-v") + 1] == f"{d}:/workspace:{mode}"
    assert minishell_tools.SANDBOX_CONFIG["image"] in cmd
    assert cmd[-1] == "ls"
    assert log["kw"]["stdin"] is subprocess.DEVNULL
    assert r.stdout == "OUT" and r.exit_code == 0


def test_podman_runner_default_mounts_no_host_root(tmp_path, monkeypatch, fake_proc):
    log, _ = fake_proc
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(tmp_path))
    minishell_tools.PodmanRunner().run(["ls"], None, frozenset({"read"}))
    assert "/:/host:ro" not in log["argv"]


def test_podman_runner_host_root_mounts_host_readonly(tmp_path, monkeypatch, fake_proc):
    # R14: an anywhere-read grant adds a READ-ONLY host-root mount at /host.
    log, _ = fake_proc
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(tmp_path))
    minishell_tools.PodmanRunner(host_root=True).run(
        ["cat", "/host/etc/hostname"], None, frozenset({"read"}))
    argv = log["argv"]
    mounts = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-v"]
    assert mounts == [f"{tmp_path}:/workspace:ro", "/:/host:ro"]


def test_podman_runner_attaches_piped_stdin(tmp_path, monkeypatch, fake_proc):
    log, _ = fake_proc
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(tmp_path))
    minishell_tools.PodmanRunner().run(["tee", "out.txt"], "hello", frozenset({"write"}))
    assert "--interactive" in log["argv"]
    assert "-t" not in log["argv"] and "--tty" not in log["argv"]
    assert log["kw"]["input"] == "hello"


# ---------------------------------------------------------------------------
# e2e: real sed over a tmp workspace, read grant (needs sed on PATH)
# ---------------------------------------------------------------------------

def _read_grant_shell():
    # The guard is built HERE so it snapshots the SANDBOX_CONFIG
    # host_data_dir the calling test has just monkeypatched.
    guard = minishell_tools.PathGuard({"read": ["*"]})
    return minishell_tools.MiniShell(minishell_tools._REGISTRY,
                                     minishell_tools.HostRunner(),
                                     {"read": ["*"]}, guard)


@pytest.mark.parametrize("script", [
    "w out.txt",             # file write
    "W out.txt",             # file write
    "e touch escape.txt",    # command execution
    "r /etc/passwd",         # file read (info leak)
    "s/b/B/w out.txt",       # s///w flag
    "s/b/B/e",               # s///e flag
])
def test_sed_sandbox_blocks_side_effect_scripts(tmp_path, monkeypatch, script):
    d = tmp_path / "ws"
    d.mkdir()
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(d))
    res = _read_grant_shell().execute(f"printf abc | sed '{script}'")
    assert res.exit_code != 0
    assert "sandbox" in res.stderr
    assert not (d / "out.txt").exists()
    assert not (d / "escape.txt").exists()


def test_sed_sandbox_keeps_normal_edits(tmp_path, monkeypatch):
    d = tmp_path / "ws"
    d.mkdir()
    monkeypatch.setitem(minishell_tools.SANDBOX_CONFIG, "host_data_dir", str(d))
    (d / "in.txt").write_text("abc\n")
    res = _read_grant_shell().execute("sed 's/b/B/g' in.txt")
    assert res.exit_code == 0 and res.stdout == "aBc\n"
    res = _read_grant_shell().execute("printf abc | sed 's/b/B/'")
    assert res.exit_code == 0 and res.stdout == "aBc"


def test_sed_argv_pinned_before_user_tokens():
    spec = minishell_tools._REGISTRY.get("sed")
    assert spec.argv == ("sed", "--sandbox")


# ---------------------------------------------------------------------------
# configure / assembly  (fresh module per test — configure mutates globals)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("engine,expected", [("host", "HostRunner"), ("podman", "PodmanRunner")])
def test_configure_flips_runner(fresh, engine, expected):
    fresh.configure({"*": ["*"]}, engine=engine)
    assert type(fresh._runner).__name__ == expected


def test_configure_grant_enforced_at_dispatch(fresh):
    fresh.configure({"read": ["*"]}, engine="host")
    assert fresh._GRANT == {"read": ["*"]}
    assert "patch" not in names_in(fresh.command_index())
    assert "disabled this session:" in fresh.command_index()
    fake = FakeRunner()
    res = minishell_tools.MiniShell(fresh._REGISTRY, fake,
                                    fresh._GRANT, fresh._guard).execute("patch f.diff")
    assert res.exit_code == 126
    assert fake.calls == []


def test_configure_rewrites_shell_docstring(fresh):
    assert "Available commands:" not in fresh.shell.__doc__
    fresh.configure({"*": ["*"]}, engine="host")
    assert "Available commands:" in fresh.shell.__doc__
    # Change 7/12: the wildcard grant reads as ANYWHERE for both families.
    assert "Permission mode: read anywhere; write anywhere." in fresh.shell.__doc__


def test_configure_banner_states_scoped_patterns_and_disabled(fresh):
    fresh.configure({"read": ["*.py"]}, engine="host")
    doc = fresh.shell.__doc__ or ""
    assert "Permission mode: read in the workdir (paths matching: *.py)." in doc
    assert "Disabled this session: patch, rm, tee." in doc


def test_configure_patterned_write_advertises_patch_header_rules(fresh):
    fresh.configure({"read": ["*"], "write": ["*.py"]}, engine="host")
    doc = fresh.shell.__doc__ or ""
    assert "---/+++" in doc and "stdin" in doc


def test_configure_rejects_write_anywhere_under_podman_before_mutation(fresh):
    with pytest.raises(ValueError, match="write_anywhere"):
        fresh.configure({"write_anywhere": ["*"]}, engine="podman")
    # fail-closed: the ValueError fires BEFORE any module state is touched
    assert fresh._GRANT == {"*": ["*"]}
    assert type(fresh._runner).__name__ == "HostRunner"


def test_configure_podman_anywhere_read_mounts_host_root_readonly(fresh):
    fresh.configure({"read_anywhere": ["*"], "write": ["*"]}, engine="podman")
    assert type(fresh._runner).__name__ == "PodmanRunner"
    assert fresh._runner._host_root is True
    assert "/host" in (fresh.shell.__doc__ or "")


def test_command_index_helper_matches_registry(fresh):
    assert fresh.command_index() == fresh._REGISTRY.index(fresh._GRANT)


def test_static_docstring_carries_the_never_approximate_line(fresh):
    # Standing invariant (the truncation incident): the un-configured shell
    # docstring must already tell the model to refuse unsupported operations
    # rather than improvise a destructive approximation.
    doc = fresh.shell.__doc__ or ""
    assert "never approximate" in doc
    assert "provide the exact command to run outside the sandbox" in doc


def test_no_delete_primitive_note_in_read_only_absent_in_write(fresh):
    # Mode-conditional guardrail: read-only sessions have no unlink, so the
    # banner must warn against truncation-as-deletion; a write-granted
    # session HAS rm, so the note would be false and must be absent.
    fresh.configure({"read": ["*"]}, engine="host")
    assert "no delete primitive" in (fresh.shell.__doc__ or "")
    assert "do not simulate deletion by truncating it" in (fresh.shell.__doc__ or "")

    fresh.configure({"read": ["*"], "write": ["*"]}, engine="host")
    assert "no delete primitive" not in (fresh.shell.__doc__ or "")


# ---------------------------------------------------------------------------
# schema surface
# ---------------------------------------------------------------------------

def test_discover_tools_finds_exactly_shell():
    assert minishell_tools.__all__ == ["shell"]
    assert minishell_tools.discover_tools(dict(vars(minishell_tools)),
                                          minishell_tools.__name__) == ["shell"]


def test_doc_after_configure_lists_core_commands(fresh):
    fresh.configure({"*": ["*"]}, engine="host")
    doc = fresh.shell.__doc__ or ""
    for name in ("ls", "grep", "find", "patch", "rm"):
        assert name in doc


def test_annotation_carries_pipe_guidance():
    hints = get_type_hints(minishell_tools.shell, include_extras=True)
    meta = " ".join(str(a) for a in get_args(hints["command_line"])[1:])
    assert "put spaces around" in meta                 # the unspaced-pipe trap
    assert "Only pipes are supported" in meta


def test_doc_before_configure_has_no_index(fresh):
    """§9 ordering test (BEFORE half): pre-configure docstring carries no index.
    test_real_schema_generator_* pins the AFTER half with toolex's real generator."""
    doc = fresh.shell.__doc__ or ""
    assert "Available commands:" not in doc
    assert "ls — " not in doc


def test_no_command_surfaces_as_tool():
    for obj in vars(minishell_tools).values():
        if getattr(obj, "_command_spec", None) is not None:
            assert not getattr(obj, "_is_toolex_tool", False)
    assert minishell_tools.discover_tools(dict(vars(minishell_tools)),
                                          minishell_tools.__name__) == ["shell"]


def test_real_schema_generator_sees_configured_index(fresh):
    """Pins §5.8's ordering assumption against toolex's actual schema generator.
    Skipped (not failed) if toolex / requests is not importable in the test env,
    or if the imported toolex predates the schema generator (stale/shadowing copy)."""
    toolex = pytest.importorskip("toolex")
    if not hasattr(toolex, "generate_openai_schema"):
        pytest.skip(
            "imported 'toolex' has no generate_openai_schema "
            f"(loaded from {getattr(toolex, '__file__', '<unknown>')}); "
            "repo toolex.py is not winning sys.path resolution"
        )
    fresh.configure({"*": ["*"]}, engine="host")
    desc = toolex.generate_openai_schema(fresh.shell)["function"]["description"]
    assert "Available commands:" in desc
    assert "grep" in desc and "patch" in desc


# ---------------------------------------------------------------------------
# tooling decorator contract  [Change 1]
# ---------------------------------------------------------------------------

def test_shell_tool_declares_self_scoped_any_cap():
    shell = minishell_tools.shell
    assert shell._is_toolex_tool is True
    assert shell._self_scoped is True
    assert shell._cap_match == "any"
    assert shell._required_caps == {"read", "read_anywhere", "write", "write_anywhere"}
    assert shell._path_args == frozenset()


def test_tool_decorator_sets_scope_attrs_in_both_forms():
    import tooling

    @tooling.tool("read", self_scoped=True, cap_match="any")
    def _scoped():
        pass

    assert _scoped._self_scoped is True and _scoped._cap_match == "any"
    assert _scoped._required_caps == {"read"}

    @tooling.tool("read")
    def _default():
        pass

    assert _default._self_scoped is False and _default._cap_match == "all"

    @tooling.tool
    def _bare():
        pass

    assert _bare._self_scoped is False and _bare._cap_match == "all"
    assert _bare._required_caps == set() and _bare._path_args == frozenset()


# ---------------------------------------------------------------------------
# toolex (unit)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="wired in commit 3 (adapted §7.1: spec-cap vocabulary)")
def test_toolex_parse_permissions_collects_spec_caps():
    toolex = pytest.importorskip("toolex")
    # ':write' validates against the caps declared by the module's _command_specs
    assert toolex.parse_permissions(["minishell:write"]) == {"minishell_tools": {"write": ["*"]}}
    # unknown caps are warned about and dropped, not silently kept
    pm = toolex.parse_permissions(["minishell:fly"])
    assert "fly" not in pm.get("minishell_tools", {})
    # a bare grant expands to every declared spec cap (read + write), not just read
    assert set(toolex.parse_permissions(["minishell"])["minishell_tools"]) == {"read", "write"}


def test_toolex_serialization_duck_types_to_payload():
    toolex = pytest.importorskip("toolex")
    payload = toolex._serialize_result(minishell_tools.CommandResult("s", "e", 3))
    assert payload == {"stdout": "s", "stderr": "e", "exit_code": 3}


@pytest.mark.parametrize("patterns", [["allowed.txt"], ["*.txt", "doc/*"]])
def test_toolex_rejects_scoped_grants_without_path_metadata(patterns):
    # A NON-self-scoped tool with no _path_args still cannot honor a scoped
    # grant: build_tools_from_modules raises (Change 3, unchanged for them).
    toolex = pytest.importorskip("toolex")
    module = types.SimpleNamespace(__name__="bash_tools")

    def get_cat():
        pass

    get_cat._is_toolex_tool = True
    get_cat._required_caps = {"read"}
    module.get_cat = get_cat
    with pytest.raises(ValueError, match="cannot enforce scoped path grants"):
        toolex.build_tools_from_modules([module], {"bash_tools": {"read": patterns}})


def test_toolex_self_scoped_shell_is_exempt_from_the_scoped_pattern_gate():
    # Change 2/3: the real shell declares _self_scoped=True, so a scoped read
    # grant no longer raises — the schema builds and is exposed.
    toolex = pytest.importorskip("toolex")
    schemas = toolex.build_tools_from_modules(
        [minishell_tools], {"minishell_tools": {"read": ["allowed.txt"]}})
    assert [s["function"]["name"] for s in schemas] == ["minishell_shell"]


def test_toolex_shell_visible_under_any_single_cap():
    # cap_match="any": any minishell grant — even write-only — surfaces shell.
    toolex = pytest.importorskip("toolex")
    for grant in ({"write": ["*"]}, {"read_anywhere": ["*"]}, {"*": ["*"]}):
        schemas = toolex.build_tools_from_modules(
            [minishell_tools], {"minishell_tools": grant})
        assert [s["function"]["name"] for s in schemas] == ["minishell_shell"]


def test_toolex_preamble_states_minishell_scopes_and_patterns():
    # Change 12: the preamble mirrors the module banner's _scope_phrase words —
    # "read+write"/"read-only" are gone; both scopes with their patterns remain.
    toolex = pytest.importorskip("toolex")
    preamble = toolex._build_session_preamble({"minishell_tools": {"*": ["*"]}})
    assert "shell: read anywhere; write anywhere" in preamble
    read_only = toolex._build_session_preamble({"minishell_tools": {"read": ["*"]}})
    assert "shell: read in the workdir; no write" in read_only
    patterned = toolex._build_session_preamble({"minishell_tools": {"read": ["*.py"]}})
    assert "shell: read in the workdir (paths matching: *.py); no write" in patterned
    # 'all' is normalized like the configure() call site: wildcard anywhere
    all_grant = toolex._build_session_preamble({"minishell_tools": {"all": ["*"]}})
    assert "shell: read anywhere; write anywhere" in all_grant
    empty = toolex._build_session_preamble({"minishell_tools": {"read": []}})
    assert "shell: empty pattern grant — denies everything; no write" in empty
    nothing = toolex._build_session_preamble({"minishell_tools": {}})
    assert "shell: no permissions" in nothing
