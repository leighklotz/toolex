#!/usr/bin/env python3
"""test_minishell.py — the §9 test plan for minishell_tools.py (commit 2).

Run from the repo root:            pytest test_minishell.py -q

Group order mirrors §9: parser, registry, dispatch, pipeline, runner,
configure/assembly, schema surface, toolex (unit).

Commit-2 notes:
* Every group except `toolex (unit)` must be green BEFORE toolex.py is
  touched (§8 step 2).
* The two `toolex (unit)` tests are xfail(strict=True): they pin the
  commit-3 contract (parser cap vocabulary + _serialize_result seam).
  When commit 3 lands they XPASS and the suite fails until the markers
  are removed — that is the signal to un-xfail them.
* configure() mutates module globals and is not idempotent: the `fresh`
  fixture reloads minishell_tools before AND after every test in the
  configure / schema-surface groups, so test ordering cannot leak state.
* minishell_tools._WORKSPACE is bound at import time: glob tests patch
  the module attribute (monkeypatch.setattr), not the SANDBOX_CONFIG dict.
"""

import importlib
import subprocess
import types
from typing import get_args, get_type_hints

import pytest

import minishell_tools

WILD = frozenset({"*"})

EXPECTED_COMMANDS = {
    "ls", "pwd", "whoami", "which", "basename", "dirname", "stat", "file", "du", "df",
    "cat", "head", "tail", "wc", "uniq", "diff",
    "grep", "sed", "cut", "tr", "sort",
    "date", "find",
    "echo", "printf", "help",
    "patch", "tee",
}
assert len(EXPECTED_COMMANDS) == 28


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class FakeRunner:
    """Records (argv, stdin, caps) per stage; returns scripted results in order."""

    def __init__(self, results=None):
        self.calls = []
        self._results = list(results or [])

    def run(self, argv, stdin, caps):
        self.calls.append((list(argv), stdin, frozenset(caps)))
        if self._results:
            return self._results.pop(0)
        return minishell_tools.CommandResult("", "", 0)


def run_line(line, granted=WILD, results=None):
    """Execute `line` through a real MiniShell over a FakeRunner."""
    fake = FakeRunner(results)
    sh = minishell_tools.MiniShell(minishell_tools._REGISTRY, fake, frozenset(granted))
    return sh.execute(line), fake


def names_in(index_text):
    """Command names from an index blob ('name — doc' lines; no doc contains ' — ')."""
    return {ln.split(" — ", 1)[0] for ln in index_text.splitlines() if ln.strip()}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Workspace with a.py/b.py (+ sub/c.py), patched into _WORKSPACE."""
    d = tmp_path / "ws"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n")
    (d / "b.py").write_text("y = 2\n")
    sub = d / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("z = 3\n")
    monkeypatch.setattr(minishell_tools, "_WORKSPACE", str(d))
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


@pytest.mark.parametrize("op", [">", "<", ">>", "&&", "||", ";", "(", ")", "&", "`", "|&"])
def test_unsupported_operator_rejected(op):
    res, fake = run_line(f"echo a {op} b")
    assert res.exit_code == 2
    assert op in res.stderr and "pipes only" in res.stderr
    assert fake.calls == []


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
    assert fake.calls == []


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_registry_holds_exactly_28():
    assert set(minishell_tools._REGISTRY._by_name) == EXPECTED_COMMANDS


def test_registry_holds_all_regardless_of_grant():
    reg = minishell_tools._REGISTRY
    assert reg.get("patch") is not None and reg.get("patch").caps == frozenset({"write"})
    assert reg.get("tee").caps == frozenset({"write"})
    assert reg.get("ls").caps == frozenset({"read"})
    assert reg.get("no_such_command") is None


def test_index_filtered_by_grant():
    reg = minishell_tools._REGISTRY
    read_names = {s.name for s in reg._by_name.values()
                  if minishell_tools._allowed(s, frozenset({"read"}))}
    assert "patch" not in read_names and "tee" not in read_names
    assert names_in(reg.index(frozenset({"read"}))) == read_names
    wild = names_in(reg.index(WILD))
    assert {"patch", "tee"} <= wild and len(wild) == 28


def test_index_limit_hint_and_unlimited():
    reg = minishell_tools._REGISTRY
    trunc = reg.index(WILD, limit=10)
    assert trunc.endswith("(run 'help' for the full list)")
    assert "whoami" not in trunc                       # only the first 10 chars survived
    full = reg.index(WILD, limit=None)
    assert full == reg.index(WILD, limit=10 ** 6)      # unlimited == effectively-unlimited
    assert len(full.splitlines()) == 28


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


def test_index_tags_stdin_commands():
    by_name = {ln.split(" — ", 1)[0]: ln
               for ln in minishell_tools._REGISTRY.index(WILD).splitlines()}
    for name in ("cat", "head", "tail", "wc", "uniq", "diff", "grep", "sed",
                 "cut", "tr", "sort", "patch", "tee"):
        assert "[stdin]" in by_name[name], name
    for name in ("ls", "pwd", "date", "find", "echo", "printf", "help"):
        assert "[stdin]" not in by_name[name], name


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
])
def test_write_denied_under_read_grant(line, msg):
    res, fake = run_line(line, granted=frozenset({"read"}))
    assert res.exit_code == 126
    assert res.stderr == msg
    assert fake.calls == []


@pytest.mark.parametrize("line,frag", [
    ("find . -delete", "destructive find predicates"),
    ("find x -exec rm {}", "destructive find predicates"),
    ("find x -execdir rm {}", "destructive find predicates"),
    ("find . -ok rm {}", "destructive find predicates"),
    ("sed -i s/a/b/ f.txt", "sed -i is disabled"),
    ("sed -i.bak s/a/b/ f.txt", "sed -i is disabled"),
    ("sed --in-place s/a/b/ f.txt", "sed -i is disabled"),
    ("sort -o out.txt", "sort -o is disabled"),
    ("sort -ofile.txt", "sort -o is disabled"),
    ("sort --output out.txt", "sort -o is disabled"),
    ("date -s 2024-01-01", "date -s is disabled"),
    ("date --set=2024-01-01", "date -s is disabled"),
])
def test_validators_block_destructive_flags(line, frag):
    res, fake = run_line(line)
    assert res.exit_code == 1
    assert frag in res.stderr
    assert fake.calls == []


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
    ("sed s/a/b/ file.py", ["sed", "s/a/b/", "file.py"]),
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
    assert res.stdout == minishell_tools._REGISTRY.index(minishell_tools._GRANTED, limit=None) + "\n"
    assert len(res.stdout.splitlines()) == 28
    assert "run 'help' for the full list" not in res.stdout


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


# ---------------------------------------------------------------------------
# configure / assembly  (fresh module per test — configure mutates globals)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("engine,expected", [("host", "HostRunner"), ("podman", "PodmanRunner")])
def test_configure_flips_runner(fresh, engine, expected):
    fresh.configure(frozenset({"read", "write"}), engine=engine)
    assert type(fresh._runner).__name__ == expected


def test_configure_grant_enforced_at_dispatch(fresh):
    fresh.configure(frozenset({"read"}), engine="host")
    assert fresh._GRANTED == frozenset({"read"})
    assert "patch" not in names_in(fresh.command_index())
    fake = FakeRunner()
    res = minishell_tools.MiniShell(fresh._REGISTRY, fake, fresh._GRANTED).execute("patch f.diff")
    assert res.exit_code == 126
    assert fake.calls == []


def test_configure_rewrites_shell_docstring(fresh):
    assert "Available commands:" not in fresh.shell.__doc__
    fresh.configure(frozenset({"*"}), engine="host")
    assert "Available commands:" in fresh.shell.__doc__


def test_command_index_helper_matches_registry(fresh):
    assert fresh.command_index() == fresh._REGISTRY.index(fresh._GRANTED)


# ---------------------------------------------------------------------------
# schema surface
# ---------------------------------------------------------------------------

def test_discover_tools_finds_exactly_shell():
    assert minishell_tools.__all__ == ["shell"]
    assert minishell_tools.discover_tools(dict(vars(minishell_tools)),
                                          minishell_tools.__name__) == ["shell"]


def test_doc_after_configure_lists_core_commands(fresh):
    fresh.configure(frozenset({"*"}), engine="host")
    doc = fresh.shell.__doc__ or ""
    for name in ("ls", "grep", "find", "patch"):
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
    Skipped (not failed) if toolex / requests is not importable in the test env."""
    toolex = pytest.importorskip("toolex")
    fresh.configure(frozenset({"*"}), engine="host")
    desc = toolex.generate_openai_schema(fresh.shell)["function"]["description"]
    assert "Available commands:" in desc
    assert "grep" in desc and "patch" in desc


# ---------------------------------------------------------------------------
# toolex (unit) — commit-3 contract, xfail(strict=True) until wired.
# On commit 3 these XPASS and the suite FAILS: remove the markers then.
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


@pytest.mark.xfail(strict=True, reason="wired in commit 3 (_serialize_result seam for to_payload)")
def test_toolex_serialization_duck_types_to_payload():
    toolex = pytest.importorskip("toolex")
    payload = toolex._serialize_result(minishell_tools.CommandResult("s", "e", 3))
    assert payload == {"stdout": "s", "stderr": "e", "exit_code": 3}
