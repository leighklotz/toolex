# toolex – Git Tool Demo

`toolex` is a lightweight framework that turns ordinary Python functions into LLM‑ready tools that can be called from an OpenAI‑compatible API. The framework is intentionally small, making it easy to add new tools and keep your codebase self‑contained.

## Project layout

```text
├── bash_tools.py       # shell-related tools (ls, pwd, cat, find, etc.)
├── git_tools.py        # git-related tools (status, diff, etc.)
├── help-commit.sh      # Bash helper that asks the LLM to generate a commit command
├── pipetest.sh         # Safe‑pipeline helper – prompts for Y/N before forwarding data
├── toolex.py           # Thin client that talks to a local /v1/chat/completions endpoint
├── toolex.sh           # Convenience shell wrapper for `toolex.py`
├── tooling.py          # Decorator & CLI helpers used by all tools
├── weather_tools.py    # A duplicate of get_weather for demonstration
├── __init__.py
└── README.md           # ← you’re here
```

* `tooling.py` – defines the `@tool` decorator and `run_tool` helper.
* `bash_tools.py` – registers a handful of **shell** utilities (ls, pwd, cat, find, df, etc.).
* `git_tools.py` – registers a handful of **git** utilities (status, diff, merge, etc.).
* `toolex.py` – parses CLI arguments, auto‑generates *OpenAI‑style* tool schemas, sends a request to the `VIA_API_CHAT_BASE` (defaults to `http://127.0.0.1:5000/`), and orchestrates the tool calls.
* `help-commit.sh` – shows how to build a prompt that instructs the model to inspect the repository and emit a `git commit -a` command. It relies on `pipetest.sh` to confirm you want to run the committed command.

## Getting started

```bash
# 1. Install dependencies
mkvenv
pip install -r requirements.txt   # (requirements.py/pytest omitted for brevity)

# 2. Start a local OpenAI mimic (e.g. via `fairseq-openai` or `wml.llm`)
export VIA_API_CHAT_BASE="http://127.0.0.1:5000"

# 3. Run the client
./toolex.py --tools git_tools "What is the current status of the repository?"
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
* `pipetest.sh` prompts you “Y or N?” before piping the command to `bash`.
* The result is a one‑liner `git commit -a -m "…"` with proper quoting.

Example output (when changes exist):

```bash
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

If there are no changes:

```bash
echo no changes
```

## Extending the system

* **Add a new module** – put a `.py` file with one or more `@tool` functions.
* **Tell the client** – pass the module name with `--tools`, e.g.:

  ```bash
  # Using bash tools
  ./toolex.py --tools bash_tools "List all files in the current directory"

  # Using multiple tool modules
  ./toolex.py --tools git_tools --tools weather_tools "What's the weather in London and what is my git status?"
  ```

* The client will automatically pick up the new tool, generate the correct schema, and use it when the model asks for it.

## `toolex.sh`

Convenience wrapper for systems where the binary is installed in `~/wip/toolex`. All flags are forwarded to `toolex.py`.

```bash
./toolex.sh --tools git_tools "What's up?"
```




