"""minishell_tools §9 test plan — registry, operand classification, dispatch,
path grants (PathGuard), pre-dispatch path enforcement, patch under a
patterned write grant, and rm (the delete primitive) groups.

Split from test_minishell.py; the shared constants, helpers, and fixtures
live in conftest.py (see its module docstring for the patch-set notes).
"""

import pytest

import minishell_tools

from conftest import EXPECTED_COMMANDS, WILD, names_in, run_line


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
    # rm: single-file move to .trash/ — its one operand is write-classified, no reads.
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
    # rm options are all banned — single-file move-to-trash only, no -r/-rf ever:
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
    # non-matching operand is denied before the native move-to-trash runs.
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
    # The rm operand must obey the same containment rule as tee's.
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
# rm — the delete primitive: single-file move to .trash/ (native write command)
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
    assert res.exit_code == 1                   # directories are refused (no recursion)
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
    assert (ws / "a.txt").exists() and (ws / "b.txt").exists()   # nothing was moved


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


def test_rm_moves_to_trash(ws):
    (ws / "stale.txt").write_text("gone soon\n")
    res, _ = run_line("rm stale.txt")
    assert res.exit_code == 0
    assert not (ws / "stale.txt").exists()
    assert (ws / ".trash" / "stale.txt").exists()
    assert (ws / ".trash" / "stale.txt").read_text() == "gone soon\n"


def test_rm_rejects_trash_resident_operand(ws):
    (ws / ".trash").mkdir(exist_ok=True)
    (ws / ".trash" / "foo.txt").write_text("already gone\n")
    res, _ = run_line("rm .trash/foo.txt")
    assert res.exit_code == 1
    assert "already deleted" in res.stderr
    assert (ws / ".trash" / "foo.txt").exists()   # survived


def test_rm_trash_collision_naming(ws):
    (ws / "x.txt").write_text("1\n")
    run_line("rm x.txt")
    (ws / "x.txt").write_text("2\n")
    res, _ = run_line("rm x.txt")
    assert res.exit_code == 0
    assert (ws / ".trash" / "x.txt").read_text() == "1\n"
    assert (ws / ".trash" / "x.1.txt").read_text() == "2\n"


def test_rm_trash_dir_created_by_configure(ws, fresh):
    fresh.configure({"read": ["*"], "write": ["*"]}, engine="host")
    assert (ws / ".trash").is_dir()
