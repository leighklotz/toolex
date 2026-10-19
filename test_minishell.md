# test_minishell.md — running and reading the minishell test suite

`test_minishell.py` is the §9 test plan for `minishell_tools.py` — "commit 2"
of the minishell rollout, as the file's own docstring calls it, with the
sed-hardening fix applied on top. It is self-contained: the interpreter and
registry run against a `FakeRunner`, and the runner group monkeypatches
`subprocess.run`, so the suite needs no podman, no network, and no LLM. Two
things touch the real world: the sed e2e group needs GNU `sed` (with
`--sandbox`) and `printf` on `PATH`, and the toolex-dependent tests need
`toolex` (and its `requests` dependency) importable.

## Running

From the repo root, so `minishell_tools`, `tooling`, and `toolex` resolve on
`sys.path`:

    pytest test_minishell.py -q

`pyproject.toml` has no pytest configuration section — default rootdir
discovery applies.

## Expected result

115 test items (64 test functions, 13 of them parametrized):

- **`toolex` importable (the normal case): `114 passed, 1 xfailed`.**
- **`toolex`/`requests` not importable: `109 passed, 6 skipped`.** The five
  `toolex (unit)` items and `test_real_schema_generator_sees_configured_index`
  all skip via `pytest.importorskip`.

The one XFAIL is `test_toolex_parse_permissions_collects_spec_caps`
(`xfail(strict=True)`, reason "wired in commit 3 (adapted §7.1: spec-cap
vocabulary)"). Its body fails against today's `toolex.py`, as the marker
expects: unknown caps like `:fly` are kept instead of dropped, and a bare
`minishell` grant still expands to read-only instead of the module's declared
spec caps (the `:write` half of the assertion already parses). When commit 3
lands, the body passes and strict xfail turns it into `[XPASS(strict)]` — a
suite failure that is the signal to remove the marker, deliberately.

Two corrections to statements still floating around — the old note here and
the test module's own docstring both say "the two `toolex (unit)` tests are
xfail(strict=True)", which is stale:

- Only one marker remains. `test_toolex_serialization_duck_types_to_payload`
  is unmarked because its target already exists: `toolex._serialize_result`
  duck-types `to_payload()`, so it runs unmarked and passes at this commit.
- A skip inside a strict-xfail test reports **SKIPPED, not XFAIL** (pytest
  checks `rep.skipped` before consulting the xfail stash). The old note's
  "(or SKIPPED if toolex/requests can't be imported)" attached the skip to
  the wrong branch: the marker's `importorskip` only prevents a collection
  error when `toolex` is absent, and the item then shows up as skipped.

`test_real_schema_generator_sees_configured_index` has one extra skip guard:
if the imported `toolex` lacks `generate_openai_schema` — a stale or
shadowing module winning `sys.path` — it skips with a diagnostic that prints
the loaded module's `__file__`. That is an environment problem, not a code
regression. Against the repo's own `toolex.py` it passes: `configure()`
appends the granted index to `shell`'s docstring before the schema is
generated, which is the §5.8 ordering the test pins.

## What the suite covers

Group order mirrors §9: parser, registry, dispatch, pipeline, runner, e2e
sed, configure/assembly, schema surface, toolex (unit).

- **parser** — quote-aware tokenization (`_split`/`_split_tokens`): a quoted
  pipe stays inside one stage; quoted/escaped glob metacharacters stay
  literal; mixed quoting (`ls 'literal*'?.txt`); glob expansion is
  workspace-anchored, sorted, non-recursive, passes the literal through on
  no-match, and never expands absolute tokens; globbing is suppressed after
  find pattern flags (`-name`, `-iname`). Unsupported operators
  (`> < >> && || ; ( ) & \` |&`) are rejected with exit 2 and zero runner
  calls, as are unbalanced quotes, blank input, and empty pipeline segments —
  the empty-segment check runs before any stage executes.
- **registry** — exactly the 28 expected commands (`EXPECTED_COMMANDS`
  asserts the count at import time); all specs stay registered regardless of
  grant, and filtering happens only in `index()`; the 4096-character
  truncation hint ("run 'help' for the full list") and its unlimited mode;
  duplicate names and out-of-vocabulary caps raise at register/decoration
  time; `[stdin]` and `[write]` index tags.
- **dispatch** — unknown command → 127 with the "run 'help'" hint; an
  unspaced pipe (`ls|grep`) reads as one unknown token; permission denied →
  126 with the exact `permission denied: <cmd> requires <cap>` message;
  validators block every destructive spelling — `find -delete/-exec/
  -execdir/-ok/-fprint0`, `sed -i` bare/suffixed/clustered/long, `sort -o`
  bare/attached/long plus `--compress-program`, `date -s/--set=`, `uniq`
  output operands — with exit 1 and zero runner calls; the sed validator
  does not misread option arguments (`-e 's/a/i/'` is fine); the stdin guard
  ("…reads stdin; give it file arguments or pipe something into it") fires
  only as a first stage with no file operand; native `help` forks nothing
  and prints the full untruncated index.
- **pipeline** (mocked runner) — three-stage buffer chaining; fail-fast
  names stage and command (`pipeline stage 2 (grep) failed, exit 1`) and
  keeps the failing stage's stdout; `to_payload()` truncates stdout and
  stderr independently at 100 000 characters (the pipeline itself does not
  truncate); native `echo`/`printf` — `%s %d %f %%`, backslash escapes,
  missing-argument exit 1, non-numeric `%d` rejection; the stage's caps come
  from its spec (`tee` is called with `{"write"}`).
- **runner** (monkeypatched `subprocess.run`) — HostRunner: workspace-pinned
  `cwd`, `input=` vs `stdin=DEVNULL`, `capture_output`/`text`, 60 s timeout;
  timeout → exit 124 with "timed out after 60s"; `OSError` → exit 1.
  PodmanRunner: `--rm --net=none`, `--workdir /workspace`, `ro`/`rw` mount
  selected from the stage's caps, image from `SANDBOX_CONFIG["image"]`,
  `--interactive` (never `-t`/`--tty`) only when stdin is piped.
- **e2e: real sed** (needs `sed`/`printf` on `PATH`) — under a read grant and
  a real HostRunner, `--sandbox` (pinned ahead of user tokens via
  `argv=("sed", "--sandbox")`) blocks every side-effect script — `w`, `W`,
  `e`, `r`, `s///w`, `s///e` — creating nothing in the workspace, while
  normal edits (`s/b/B/g` on a file, `s/b/B/` on a pipe) still work. This
  group is the regression fence for the sed capability-label bug found live
  in an earlier session (read-granted `sed 'w …'` was creating files).
- **configure / assembly** — the `fresh` fixture reloads `minishell_tools`
  before and after every test in this and the schema-surface group, because
  `configure()` mutates module globals and is not idempotent. Covers the
  host/podman runner flip, grant enforcement at dispatch after configure,
  the docstring rewrite (permission banner + "Available commands:" + the
  granted index), and the `command_index()` helper.
- **schema surface** — `discover_tools` finds exactly `["shell"]` (commands
  carry `_command_spec`, never `_is_toolex_tool`); the `Annotated`
  description keeps the pipe guidance ("put spaces around", "Only pipes are
  supported"); the pre-configure docstring carries no index (the BEFORE half
  of the ordering pin; the AFTER half is the real-schema-generator test);
  after configure the docstring lists `ls`, `grep`, `find`, `patch`.
- **toolex (unit)** — the strict-xfail parser pin described above; the
  serialization duck-typing test; `build_tools_from_modules` raising
  `ValueError` ("cannot enforce scoped path grants") when a non-`*` pattern
  grant would attach to a tool without `_path_args` (minishell's `shell` has
  none); `_build_session_preamble` labeling minishell `read+write` vs
  `read-only`.

## Maintenance notes

- Every group except `toolex (unit)` must be green before `toolex.py` is
  touched (§8 step 2) — that ordering is why the toolex pins are marked at
  all.
- `minishell_tools._WORKSPACE` is bound at import time: glob tests patch the
  module attribute (`monkeypatch.setattr`). Runner and e2e tests instead
  patch `SANDBOX_CONFIG["host_data_dir"]` (`monkeypatch.setitem`), because
  the runners and `configure()` read the dict at call time.
- Adding a 29th command means updating `EXPECTED_COMMANDS`, the
  `test_index_tags_stdin_commands` lists, and (for a stdin-consuming
  command) the stdin-guard test — decorator-only registration makes a new
  command otherwise invisible to the count assertions, by design.
- The configure/schema-surface fixtures reload the module in both
  directions; do not convert them to module-scope fixtures.

## Related files

- `minishell_tools.py` — the module under test; canonical invocations in its
  docstring: `toolex.py --tools minishell`, `--tools minishell:read`,
  `--tools minishell:read --engine podman`.
- `fix1-sed-argv.diff`, `fix2-tests.diff` — the sed-hardening diffs applied
  after the capability-label bug: argv pinning in the source, and the e2e
  sed group plus `--sandbox` argv expectations in the tests.
- `.hallux/minishell-4/` — the session log behind the sed fix
  (`what-is-wrong-1.md`, `test-fail-1.md`, `test-fail-2-response-1-glm.md`).
