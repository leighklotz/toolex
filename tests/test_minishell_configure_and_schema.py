"""minishell_tools §9 test plan — configure/assembly, schema surface, and
tooling decorator contract groups.

Split from test_minishell.py; the shared constants, helpers, and fixtures
live in conftest.py (see its module docstring for the patch-set notes).
"""

import dataclasses
from typing import get_args, get_type_hints

import pytest

import minishell_tools

from conftest import FakeRunner, names_in


# ---------------------------------------------------------------------------
# configure / assembly  (fresh module per test — configure mutates globals)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("engine,expected", [("host", "HostRunner"), ("podman", "PodmanRunner")])
def test_configure_flips_runner(fresh, engine, expected):
    fresh.configure({"*": ["*"]}, engine=engine)
    assert type(fresh._CONTEXT.runner).__name__ == expected


def test_configure_grant_enforced_at_dispatch(fresh):
    fresh.configure({"read": ["*"]}, engine="host")
    assert fresh._CONTEXT.grant == {"read": ["*"]}
    assert "patch" not in names_in(fresh.command_index())
    assert "disabled this session:" in fresh.command_index()
    fake = FakeRunner()
    ctx = dataclasses.replace(fresh._CONTEXT, runner=fake)
    res = minishell_tools.MiniShell(fresh._REGISTRY, ctx).execute("patch f.diff")
    assert res.exit_code == 126
    assert fake.calls == []


def test_configure_rewrites_shell_docstring(fresh):
    assert "Available commands:" not in fresh.shell.__doc__
    fresh.configure({"*": ["*"]}, engine="host")
    assert "Available commands:" in fresh.shell.__doc__
    assert "read_anywhere" in fresh.shell.__doc__
    assert "write_anywhere" in fresh.shell.__doc__
    assert "delete_anywhere" in fresh.shell.__doc__

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
    assert fresh._CONTEXT.grant == {"*": ["*"]}
    assert type(fresh._CONTEXT.runner).__name__ == "HostRunner"


def test_configure_podman_anywhere_read_mounts_host_root_readonly(fresh):
    fresh.configure({"read_anywhere": ["*"], "write": ["*"]}, engine="podman")
    assert type(fresh._CONTEXT.runner).__name__ == "PodmanRunner"
    assert fresh._CONTEXT.runner._host_root is True
    assert "/host" in (fresh.shell.__doc__ or "")


def test_command_index_helper_matches_registry(fresh):
    assert fresh.command_index() == fresh._REGISTRY.index(fresh._CONTEXT.grant)


def test_static_docstring_carries_the_never_approximate_line(fresh):
    # Standing invariant (the truncation incident): the un-configured shell
    # docstring must already tell the model to refuse unsupported operations
    # rather than improvise a destructive approximation.
    doc = fresh.shell.__doc__ or ""
    assert "never approximate" in doc
    assert "provide the exact command to run outside the sandbox" in doc


def test_no_delete_primitive_note_in_read_only_absent_in_delete(fresh):
    # Mode-conditional guardrail: read-only sessions have no delete primitive,
    # so the banner must warn against truncation-as-deletion; a write-granted
    # session HAS rm (move to .trash/), so the note would be false and must be
    # absent — replaced by the rm→.trash note.
    fresh.configure({"read": ["*"]}, engine="host")
    assert "no delete primitive" in (fresh.shell.__doc__ or "")
    assert "do not simulate deletion by truncating it" in (fresh.shell.__doc__ or "")

    fresh.configure({"read": ["*"], "delete": ["*"]}, engine="host")
    print(f"*** doc={fresh.shell.__doc__}")
    if "no delete primitive" in fresh.shell.__doc__:
        print("*** YES YES ***")
    else:
        print("*** NO NO ***")        

    assert "no delete primitive" not in (fresh.shell.__doc__ or "")
    assert "rm moves files to .trash/" in (fresh.shell.__doc__ or "")


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
    assert shell._required_caps == {"read", "read_anywhere", "write", "write_anywhere", "delete", "delete_anywhere"}
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
