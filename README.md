# toolex – LLM tooling

`toolex` is a lightweight framework that turns ordinary Python functions into LLM‑ready tools that can be called from an OpenAI‑compatible API. The framework is intentionally small, making it easy to add new tools and keep your codebase self‑contained.

`toolex` is designed to be a part of [`answer`](https://github.com/leighklotz/answer). Below is standalone documentation.

## Project layout

```text
 ├── bash_tools.py        # shell utilities (ls, pwd, command execution, …)
 ├── file_tools.py        # file read/write/edit/search with path-pattern grants
 ├── git_tools.py         # git utilities (status, diff, commit, …)
 ├── minishell_tools.py   # pipe-only mini shell exposed as one LLM tool
 ├── test_minishell.py    # pytest suite for minishell + toolex integration
 ├── toolex.py / toolex.sh
 ├── tooling.py           # @tool decorator, host/podman runners, discover_tools
 └── weather_tools.py
```

* `tooling.py` – defines the `@tool` decorator helpers.
* `bash_tools.py` – registers a handful of **shell** utilities (ls, pwd, cat, find, command execution, etc.).
* `git_tools.py` – registers a handful of **git** utilities (status, diff, merge, commit, etc.).
* `toolex.py` – parses CLI arguments, auto‑generates *OpenAI-style* tool schemas, sends a request to the `VIA_API_CHAT_BASE` (defaults to `http://127.0.0.1:5000/`), and orchestrates the tool calls.
* `help-commit.sh` – shows how to build a prompt that instructs the model to inspect the repository and emit a `git commit -a` command. It relies on `unfence` to confirm you want to run the committed command.

## Getting started

### 1. Install dependencies

You can set up the environment using either `uv` (recommended) or standard `pip`.

**Using `uv`:**
```bash
uv sync
```

**Using `pip` and `venv`:**
```bash
mkvenv
# Ensure you are in your virtual environment before proceeding
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start a local OpenAI mimic (e.g. via `fairseq-openai` or `wml.llm`)
```bash
# Set the base URL for your local API provider
export VIA_API_CHAT_BASE="http://127.0.0.1:5000"

# Optional: the model name toolex sends with each request
# (toolex defaults to "gemma-4-26b-qat-batch" when unset).
export HX_MODEL="gpt-4o"
```

### 3. Run the client with a tool
```bash
ask "What is the current status of the repository?" | tools git
```

### 4. Run the client with multiple tools
```bash
ask "What is the ratio of git commits to current temperature in Paris?" | tools git weather
```

The client will:

1. Discover all functions decorated with `@tool`.
2. Build the OpenAI tool schema and send the prompt.
3. When the model decides to call a tool, the script will invoke it locally. The client supports multi-step agentic workflows with up to 30 automatic tool iterations before concluding or timing out.
4. The final output of the model (or your manual input) will be printed.

## Running `help-commit.sh`

```bash
# From the top directory
./help-commit.sh
```

* `help-commit.sh` creates a prompt that asks the model to:
  * Run `git status` and `git diff`.
  * Print a commit command in a fenced bash block **or** say nothing if there are no changes.
* `unfence` prompts you "Y or N?" before piping the command to `bash`.
* The result is a one‑liner `git commit -a -m "…"` with proper quoting.

Example output (when changes exist):

````bash
🤖 git status
git status

🤖 git diff
git diff

🤖 Y or N? Y

git commit -a \
  -m "Add new utility functions" \
  -m "Refactor CLI handling" \
  -m "Update README"
````

If there are no changes to commit:

````
🤖
```bash
echo no changes
```
````

## Command Line Arguments

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--tools <module[:capability>=<pattern>...]` | List of tools/modules to enable. You can restrict capabilities (e.g., `git:read`) or apply specific path allowances using the `=pattern` syntax (e.g., `file:read_anywhere=/var/*`). Supports multiple patterns via comma-separation (e.g., `/var/*,/etc/*`). | None |
| `--workspace-dir <path>` | Sets the working directory for shell tools | Current dir / `$TOOLEX_WORKSPACE_DIR` |
| `--engine {host,podman}` | Execution engine for modules with a `configure()` hook (minishell). Under podman, `write_anywhere` is rejected outright (`configure()` raises; toolex exits 2). An anywhere-**read** grant mounts the host root read-only at `/host` inside the container — prefix host paths with `/host`; `--engine host` is the expected pairing for anywhere grants. | host |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | Logging verbosity. | `INFO` |
| `--total-iterations <n>` | Maximum agentic tool-call rounds per run. | `30` |
| `--drop-tool-reasoning` | Omit assistant `reasoning_content` from the piped conversation history. | off |

### Fine-Grained Permissions

You can control exactly what tools an LLM can access and which files/directories are reachable using colon (`:`) and equal (`=`) syntax.

Restrict a module to specific predefined capabilities defined in its implementation (e.g., `read`, `write`, `edit`, or `all`). This is applied via the `--tools` flag.

*   **Specific Capability:** `./toolex.py --tools git:read "Show me diff"`
*   **All Capabilities for a Module:** `./toolex.py --tools git:all "Commit this change"`

For tools that interact with the filesystem, you can restrict access to specific paths or patterns using the `=pattern` syntax. This is particularly powerful for granting broad but controlled access via allowances.

**Syntax:** `module:capability=pattern[,pattern...]`

| Intent | Syntax Example | Note |
| :--- | :--- | :--- |
| **Specific File** | `file:read=README.md` | Restricts the tool to only that single file. |
| **Directory/Glob** | `file:read=doc/*.md` | Allows access to everything matching the pattern within a directory. |
| **Multiple Patterns** | `file:read=doc/*.md,etc/*.conf` | Use commas to allow multiple distinct paths or patterns in one command. |

| Capability | Scope | Meaning |
| :--- | :--- | :--- |
| `read` / `write` | workspace | Contained in `--workspace-dir`; patterns match workspace-relative paths |
| `read_anywhere` / `write_anywhere` | host | Patterns match resolved absolute paths; dominates the scoped twin |
| `edit` / `edit_anywhere` | workspace / host | Mutating like the write family; used by the file-edit tools |
| `all` | both | Every capability at once, with the given patterns (the CLI's `all` is normalized to the `*` wildcard internally) |

Patterns are matched case-sensitively with `fnmatch` semantics — `*` also matches `/`, so `*.py` matches `sub/b.py`. An empty pattern list (`module:read=`) denies every path: grants fail closed.

> [!TIP]
> When using shell-related tools with wildcards (e.g., `*.py`), always wrap your tool arguments in quotes (e.g., `--tools 'file:read=*.py'`) to prevent your local terminal from expanding the glob before it reaches `toolex`.

### Session preamble

Before the first model call, toolex prepends a one-line system message stating the effective grants, e.g.:

```text
Permissions — readable files: *.py; shell: read in the workdir (paths matching: *.py); no write.
```

When a previously generated history is piped back in, toolex replaces its own stale preamble in place instead of stacking a second one.

## Extending the system

* **Add a new module** – put a `.py` file with one or more `@tool` functions.
* **Tell the client** – pass the module name via `--tools`. You can restrict access using colon-separated permissions. Multiple permissions can be specified for a single module (e.g., `git:read:write`), or grant full access to all capabilities using `:all`.

  ```bash
  # Default behavior: uses 'read' permission for git_tools
  ./toolex.py --tools git "What is the status?"

  # Explicitly request read-only mode via specific capability
  ./toolex.py --tools git:read "Show me diff"

  # Grant multiple specific permissions (e.g., read and write)
  ./toolex.py --tools git:read:write "List files and commit changes"

  # Grant all capabilities defined in the module
  ./toolex.py --tools git:all "Commit these changes"

  # Mixing multiple modules with different permissions
  ./toolex.py --tools bash:read --tools git:write "List files and then commit them"
  ```

### Tool decorator options

* `path_args` — frozenset of parameter names that are filesystem paths; toolex
  validates only these against the granted patterns at call time.
* `self_scoped=True` — for tools that enforce path grants inside their own
  engine (e.g. minishell's interpreter). Exempt from toolex's "cannot enforce
  scoped path grants" checks; capability checks still apply.
* `cap_match` — `"all"` (default) requires every declared capability to be
  granted for the tool to be visible; `"any"` requires at least one.


### module configure() hook
Modules exposing `configure()` receive the grant as a `{cap: [patterns]}` dict
(the CLI's `all` is renamed to `*`) plus the `--engine` choice. A `ValueError`
raised from `configure()` makes toolex exit 2. A legacy frozenset grant
fail-closes to an empty grant — nothing is permitted. `configure()` runs
before tool schemas are built, so it can also rewrite tool docstrings:
minishell appends its permission banner and the granted command index to the
`shell` description.

## Pipeline Mode (JSON)

`toolex.py` is designed to be used within agentic pipelines via `stdin` and `stdout`. It can ingest an existing OpenAI-style message history in JSON format, resolve tool calls, and output the updated conversation array back to a pipe.

* **Input:** If valid JSON representing a list of messages is provided on `stdin`, the client treats it as the starting conversation state.
* **Output:** The script outputs a magic header (`Content-Type: application/x-llm-history+json`) followed by the updated, tool‑resolved JSON history to `stdout`.

This allows for complex shell orchestration like:
`ask "my prompt" | tools git --tools bash | answer`

## minishell

`minishell_tools` exposes exactly one `shell` tool over a pipe-only, 28-command mini shell (`ls`, `grep`, `sed`, `find`, `patch`, `tee`, …; run `help` for the index). Individual commands never surface as separate tools. The `shell` tool is visible under any grant that names at least one minishell capability (`cap_match="any"`), but per-stage dispatch still denies commands whose capabilities the grant does not cover (exit 126).

```bash
toolex.py --tools minishell                      # read-only, contained in the workdir
toolex.py --tools 'minishell:read=*.py'          # read-only, only *.py paths
toolex.py --tools minishell:read:write           # + tee/patch inside the workdir
toolex.py --tools 'minishell:read:write=*.log'   # writes limited to *.log (tee/patch)
toolex.py --tools minishell:read_anywhere        # host-wide read (see --engine caveats)
toolex.py --tools minishell:read --engine podman
```

After `configure()`, the tool description carries a permission banner — the active mode and patterns, the disabled command list, engine notes — plus the granted command index; `help` re-prints the index at runtime with a `grant: …` header and the disabled list. toolex also opens the session with a one-line `Permissions — …` system message (see *Session preamble* above).

Per-command validators disable flags that would touch unclassified paths or escape the grant — e.g. `sed -i`/`-f` (sed runs with `--sandbox` pinned, so `w`/`W`/`r`/`e` script side effects are blocked too), `grep -f`/`--exclude-from`, `diff -X`/`--from-file`, `file -f`, `sort -o`/`-T`/`--random-source`, `date -s`/`-f`, and destructive `find` predicates. The denial message names the offending flag.

Each stage's path operands are classified per command (separately on the read and write side) and resolved symlink-safe before anything is dispatched. Scoped grants require workspace containment and match workspace-relative patterns; `*_anywhere` grants drop containment, dominate their scoped twin, and match resolved absolute paths. A denied path exits 126 and names the violated constraint (missing grant, containment, or pattern) plus the allowed patterns, so the model can self-correct.

Under a **patterned** write grant, `patch` is stdin-only: its write targets come from the piped diff's `---`/`+++` headers (`-p1` stripped, `/dev/null` operands skipped), each target must be workdir-relative and match the write patterns, and retargeting options are banned — the argv is pinned to `patch -p1 --no-backup-if-mismatch --reject-file=/dev/null`. With an unpatterned (`*`) write grant no header parsing happens.

Under `--engine podman`, each stage runs in a throwaway no-network container with the workspace mounted read-only (read-write only for write-cap stages). An anywhere-read grant additionally mounts the host root read-only at `/host` — prefix host paths with `/host` — and `write_anywhere` is rejected outright.

Failure codes: validator denials exit 1, path/permission denials 126, unknown commands 127, syntax errors 2, timeouts 124. The pipeline stops at the first failing stage and the error names the stage and command.

### Redirects
Only `> /dev/null`, `2>/dev/null`, and `2>&1` are honored. Any other `>` target stays a literal argument — it can never become a hidden write channel — so file redirection remains unsupported until it can be gated on the write grant.

## `toolex.sh`

`toolex.sh` is a Convenience wrapper for systems where the binary is installed in `~/wip/toolex`. All flags are forwarded to `toolex.py`.

```bash
./toolex.sh --tools git "What's up?"
```
