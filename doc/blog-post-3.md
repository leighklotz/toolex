# Building `toolex`: Turning Functions into Capabilities

The previous article introduced `toolex` as a capability layer for `answer`. Rather than giving a language model unrestricted access to a shell or filesystem, `toolex` exposes a carefully selected collection of capabilities that can be granted independently according to the requirements of a particular workflow.

This article focuses on the implementation: how we bridge the gap between standard Python code and structured LLM tool-calling, and what the current production reality looks like in August 2026.

One of the primary design constraints was that a tool should feel no more complicated than writing an ordinary Python function. There are no base classes to inherit from, no servers to register with, and no schemas to write by hand.

If a developer can write a function, they can write a tool.

## Tools Are Just Functions

The smallest useful tool looks like this:

```python
@tool("read")
def get_weather(location: str):
    ...
```

The function remains an ordinary Python function. The decorator simply attaches metadata describing two properties:
1. This function should be visible to the model.
2. This function requires a specific capability, e.g., `read`.

Conceptually, the result is little more than:

```python
get_weather._is_toolex_tool = True
get_weather._required_caps = {"read"}
```

That turns out to be sufficient information to support discovery, permission enforcement, and schema generation.

## Discovery Through Reflection

Many extensible systems introduce registries, manifests, configuration files, or plugin managers. `toolex` uses Python reflection instead.

Tool discovery consists largely of inspecting imported modules using `inspect`. Functions marked with the decorator become available capabilities instantly upon being imported. Adding a new tool is therefore equivalent to adding a module; there is no additional registration step and no distinction between a tool implementation and an ordinary library function other than the presence of the decorator. Each module ends with `__all__ = discover_tools(globals(), __name__)`, so a module's exports are exactly its tool surface.

## Semantic Schemas from Type Hints

OpenAI-compatible APIs expect tool definitions to be represented as JSON schemas. Writing those schemas by hand quickly becomes repetitive and error-prone.

Instead, `toolex` derives them directly from Python signatures using reflection. But we go beyond basic types:

### Beyond Primitives
While a simple `str` or `int` is mapped automatically, `toolex` also handles complex structures like `List[str]` and maps them to correct JSON schema arrays with appropriate item definitions.

### Semantic Intent via `Annotated`
The real power comes from Python's `typing.get_type_hints(include_extras=True)`. By using `Annotated`, developers can attach semantic instructions directly to the arguments:

```python
from typing import Annotated

def get_weather(location: str, unit: Annotated[str, "must be 'celsius' or 'fahrenheit'"]):
    ...
```

`toolex` extracts that metadata and injects it into the JSON schema. This allows the LLM to understand not just *that* a parameter is a string, but exactly what format and values are expected, leading to much higher tool-calling accuracy.

## Granular Permissions

Many frameworks implement authorization using complex policy engines or role hierarchies. `toolex` uses Python sets for speed and simplicity, but with namespaced precision.

A declaration such as:

```python
@tool("write exec")
```

becomes a requirement set of `{"write", "exec"}`.

Our parser supports granular scoping using colon-delimited syntax e.g. `--tools git:read --tools weather:write`, with a bare module name granting the whole module. Tools that touch the filesystem accept glob patterns as well: `--tools 'file:read=*.py'`, comma-separated lists like `file:read=README.md,doc/*.md`, or multiple capabilities in one spec (`mod:cap1=pat1:cap2=pat2`, e.g. `file:read=doc/README.md:write=doc/README.md.new`). The permitted patterns are appended to each tool's schema description, so the model knows the boundary it is working inside. This allows for highly specific permission sets where you can grant full access to one module while restricting others, or grant `read` access globally while requiring explicit `write` permissions elsewhere. The core logic remains a simple subset test:

```python
required_caps.issubset(granted_caps)
```

*Current conditions.* The permission system is capability-based, and the boundary is enforced twice. `toolex.py` re-checks every call at runtime: a call to a tool whose capability was never granted is refused outright, string arguments are matched against the granted globs before execution, and every refusal arrives as a conversational error naming the allowed patterns so the model can self-correct. The `file` module applies real path confinement — reads, writes, edits, and searches stay inside the working directory (`--workspace-dir` / `$TOOLEX_WORKSPACE_DIR`) unless you deliberately grant the `read_anywhere`/`write_anywhere`/`edit_anywhere` capabilities, and `check_permitted_path` additionally matches each file it is about to open against the granted patterns (passed through as `TOOLEX_PERMITTED_PATH_PATTERNS`), skipping out-of-bounds files during glob searches. Elsewhere, enforcement is logical, not OS-enforced: the `bash` module's `read`-capable tools such as `get_cat` can still read any path the user process can read, e.g. `/etc/shadow`. Bash tools in `bash_tools.py` are wrapped with `@bash_wrap` and run directly on the host via `subprocess.run`; the sandboxed equivalents live in `podbash_tools.py` behind `@sandbox_wrap` and run only when you request the `podbash` module.

## Error Resilience & Self-Correction

One of the most common failure points in agentic workflows is an unhandled exception during tool execution. In many systems, this crashes the entire loop or leaves the model "hanging."

`toolex` treats errors as part of the conversation. When a tool fails, we catch the exception and format it into a structured JSON error message returned to the LLM as an assistant/tool response. This turns a hard crash into a conversational feedback loop: the model sees *why* the command failed and can attempt to self-correct its next move.

Error handling is currently inconsistent across modules; some tools return error strings, others raise. `do_rm` is a placeholder that raises `Exception("rm is not implemented")`. Normalisation of error reporting is ongoing.

Stall detection is active: identical `(name,arguments)` tuples — compared via canonical JSON so key-order differences don't evade detection — are tracked in `executed_states` and a warning is injected if the model repeats them, with graceful loop termination. The whole agentic run is bounded by `--total-iterations` (default 30).

## Shell Commands Are Already Tools

Many useful capabilities already exist as CLI programs:
* `git status`
* `pwd`
* `df -h`

The `bash_wrap()` helper converts existing shell commands into model-callable capabilities with very little additional code. We use `shlex.split` during execution to ensure that quoted arguments and whitespace are handled exactly as they would be in a real shell, preventing injection errors and parsing bugs.

A wrapped tool is also an honest one: when the underlying command exits non-zero, `run_bash_tool` returns the command's stderr as the tool result instead of raising, so the model sees *why* `git` failed and can adjust its next call. And a wrapper can carry its own policy — `run_git_query` in `git_tools.py` forwards arbitrary `git` invocations only after checking the first non-flag argument against a whitelist of `branch`, `log`, `show`, and `status`, so a complex read query cannot smuggle in a destructive subcommand.

*Current conditions.* `bash_wrap` executes directly on the host; there is no OS sandbox for `bash_tools.py`, and read tools expose arbitrary host file reads. The sandboxed mirror is real now: request the `podbash` module and the same tools run inside a Podman container with no network and the workspace mounted read-only unless a `write` capability is granted — plus `eval_bash`, an `eval`-capability tool that executes arbitrary bash strictly inside that sandbox. Host write tools still include `git merge/checkout/commit`; permissions there remain advisory.

## Tool Calls Become Conversation State

One of the easiest mistakes when building agent systems is introducing hidden state or external "side-car" databases for tool history.

`toolex` deliberately avoids this by treating tool invocations as standard conversation messages. An interaction looks like a natural flow:
1. **Assistant:** Requests to call `get_git_status()`.
2. **Tool:** Returns the actual output of `git status`.
3. **Assistant:** Receives that text and continues reasoning.

Because these are just message objects, tool usage is inherently visible, inspectable, and reproducible in exactly the same way as any other prompt or response. There is no session daemon; there is only the conversation.

The magic header `Content-Type: application/x-llm-history+json` continues to be the transport boundary between `ask`/`help`, `toolex`, and `answer`. Cache hits are shown as 🎯, misses as ✨, with additional emojis for piped content, `bx` shell input and `lx` file input; tool calls trace live to stderr as they run (🚀 for host commands, 🏖️ for sandboxed ones, 🤖 for file-module activity). Assistant reasoning traces ride along in the history by default and can be stripped with `TOOLS_FLAGS=--drop-tool-reasoning` when you want leaner pipelines.

## The Pipeline Remains the Interface

Because `toolex` consumes and emits the same structured message format used by `answer`, tool-aware conversations behave like ordinary shell pipelines.

For example:
```bash
ask "Summarize repository changes" | tools git
```

`tools` checks whether it is talking to a terminal: at a TTY it routes the final response through `answer` and you get plain text; in a pipeline it keeps going as a machine-readable conversation, allowing later stages to extract code blocks or request further execution approval.

The shell remains responsible for orchestration.
The conversation remains responsible for state.
The tools merely provide additional observations about the local environment.

## Known Operational Limitations in August 2026

Real use has exposed implementation gaps that affect how capabilities should be described:

* **Unsafe temp handling in `answer`.** `_mktemp_reg` falls back to `mktemp -u`, a classic TOCTOU race. Cleanup via `trap '_cleanup_run_dir'` is PID-guarded but subshells can leak.
* **Input validation.** JSON from the model is trusted after a superficial jq check. No schema validation of tool results before feeding back to the model.

These issues do not invalidate the design goals, but they are the work that keeps the system intentionally boring: tools are ordinary functions with semantic metadata via `Annotated`, permissions are granular and namespaced, errors are conversational feedback for self-correction, and the shell remains the orchestrator. The next step is to close the gaps without adding infrastructure.
