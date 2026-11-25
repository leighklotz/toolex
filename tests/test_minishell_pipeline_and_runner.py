"""minishell_tools §9 test plan — pipeline, runner, and e2e sed groups.

Split from test_minishell.py; the shared constants, helpers, and fixtures
live in conftest.py (see its module docstring for the patch-set notes).
"""

import subprocess

import pytest

import minishell_tools

from conftest import run_line


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


def test_rm_can_sit_mid_pipeline(ws):
    # rm's "moved → .trash/" note is stderr-only and a successful stage's
    # stderr is dropped, so the next stage still gets an empty buffer.
    (ws / "a.txt").write_text("x\n")
    res, fake = run_line("rm a.txt | wc",
                         results=[minishell_tools.CommandResult("0 0 0\n", "", 0)])
    assert res.exit_code == 0
    assert len(fake.calls) == 1                        # only wc forked (rm is native)
    assert fake.calls[0][0][:1] == ["wc"]
    assert fake.calls[0][1] == ""                      # rm's stdout is empty


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
    grant = minishell_tools._as_grant({"read": ["*"]})
    guard = minishell_tools.PathGuard(grant)
    ctx = minishell_tools.SessionContext(grant=grant, guard=guard,
                                         runner=minishell_tools.HostRunner())
    return minishell_tools.MiniShell(minishell_tools._REGISTRY, ctx)


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
