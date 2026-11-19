"""minishell_tools §9 test plan — parser/redirects group.

Split from test_minishell.py; the shared constants, helpers, and fixtures
live in conftest.py (see its module docstring for the patch-set notes).
"""

import pytest

import minishell_tools

from conftest import run_line


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
