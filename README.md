# toolex – LLM tooling

`toolex` is a lightweight framework that turns ordinary Python functions into LLM‑ready tools that can be called from an OpenAI‑compatible API. The framework is intentionally small, making it easy to add new tools and keep your codebase self‑contained.

`toolex` is designed to work with [`answer`](https://github.com/leighklotz/answer)

## Project layout

```text
├── bash_tools.py       # shell-related utilities (ls, pwd, command execution, etc.)
├── git_tools.py        # git-related tools (status, diff, etc.)
├── help-commit.sh      # Bash helper that asks the LLM to generate a commit command
├── toolex.py           # Thin client that talks to a local /v1/chat/completions endpoint
├── toolex.sh           # Convenience shell wrapper for `toolex.py`
├── tooling.py          # Decorator & CLI helpers used by all tools
├── weather_tools.py    # A duplicate of get_weather for demonstration
├── __init__.py
└── README.md           # ← you’re here
```

* `tooling.py` – defines the `@tool` decorator and `run_tool` helper.
* `bash_tools.py` – registers a handful of **shell** utilities (ls, pwd, cat, find, command execution, etc.).
* `git_tools.py` – registers a handful of **git** utilities (status, diff, merge, etc.).
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
export VIA_API_CHAT_BASE="http://127.0.0.1:5000"
```

### 3. Run the client with a tool
```bash
./toolex.py --tools git "What is the current status of the repository?"
```

### 4. Run the client with multiple tools
```bash
./toolex.py --tools git weather "What is the ratio of git commits to current temperature in Paris?"
```

The client will:

1. Discover all functions decorated with `@tool`.
2. Build the OpenAI tool schema and send the prompt.
3. When the model decides to call a tool, the script will invoke it locally.
4. The final output of the model (or your manual input) will be printed.

## Running `help-commit.sh`

```bash
# From the top directory
./help-commit.sh
```

* `help-commit.sh` creates a prompt that asks the model to:
  * Run `git status` and `git diff`.
  * Print a commit command in a fenced bash block **or** say nothing if there are no changes.
* `unfence` prompts you “Y or N?” before piping the command to `bash`.
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
```

If there are no changes to commit:

```bash
echo no changes
```
````

## Extending the system

* **Add a new module** – put a `.py` file with one or more `@tool` functions.
* **Tell the client** – pass the module name via `--tools`. You can restrict access using colon-separated permissions (e.g., `:read`, `:write`) or grant full access to all capabilities defined in a module by using `:all`.

  ```bash
  # Default behavior: uses ':read' permission for git_tools
  ./toolex.py --tools git "What is the status?"

  # Explicitly request read-only mode via specific capability
  ./toolex.py --tools git:read "Show me diff"

  # Grant all capabilities defined in the module (e.g., write, execute)
  ./toolex.py --tools git:all "Commit these changes"

  # Mixing multiple modules with different permissions
  ./toolex.py --tools bash:read --tools git:write "List files and then commit them"
```
````

## Pipeline Mode (JSON)

`toolex.py` is designed to be used within agentic pipelines via `stdin` and `stdout`. It can ingest an existing OpenAI-style message history in JSON format, resolve tool calls, and output the updated conversation array back to a pipe.

* **Input:** If valid JSON representing a list of messages is provided on `stdin`, the client treats it as the starting conversation state.
* **Output:** The script outputs a magic header (`Content-Type: application/x-llm-history+json`) followed by the updated, tool‑resolved JSON history to `stdout`.

This allows for complex shell orchestration like:
`ask "my prompt" | tools git --tools bash | answer`

## `toolex.sh`

Convenience wrapper for systems where the binary is installed in `~/wip/toolex`. All flags are forwarded to `toolex.py`.

```bash
./toolex.sh --tools git "What's up?"
```
