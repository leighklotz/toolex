"""minishell_tools §9 test plan — toolex (unit) group.

Split from test_minishell.py; the shared constants, helpers, and fixtures
live in conftest.py (see its module docstring for the patch-set notes).
"""

import types

import pytest

import minishell_tools


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
