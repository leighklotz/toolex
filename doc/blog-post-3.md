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

Tool discovery consists largely of inspecting imported modules using `inspect`. Functions marked with the decorator become available capabilities instantly upon being imported. Adding a new tool is therefore equivalent to adding a module; there is no additional registration step and no distinction between a tool implementation and an ordinary library function other than the presence of the decorator.

*Current conditions.* Production use has surfaced naming hygiene issues that break this model silently. `git_tools.py` shipped with duplicate function names:

* `get_git_branch` is defined twice – first for `git branch`, second for `git grep`. The second definition wins.
* `do_git_checkout` is defined twice – once for `checkout`, once for `commit`. The commit definition overwrites checkout.

Because `discover_tools` only sees the last definition, the tool surface was incomplete until naming was made unique. Duplicate names are now part of the module review checklist.

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

Our parser supports granular scoping using colon-delimited syntax e.g. `--tools git:read weather:write`. This allows for highly specific permission sets where you can grant full access to one module while restricting others, or grant `read` access globally while requiring explicit `write` permissions elsewhere. The core logic remains a simple subset test:

```python
required_caps.issubset(granted_caps)
```

*Current conditions.* The permission system is capability-based and enforced via `required.issubset(user_perms)`. It is logical, not OS-enforced. A `read`-capable tool such as `get_cat` can still read any path the user process can read, e.g. `/etc/shadow`. Bash tools in `bash_tools.py` are wrapped with `@bash_wrap` and run directly on the host via `subprocess.run`; the Podman sandbox code exists but is not used by default.

## Error Resilience & Self-Correction

One of the most common failure points in agentic workflows is an unhandled exception during tool execution. In many systems, this crashes the entire loop or leaves the model "hanging."

`toolex` treats errors as part of the conversation. When a tool fails, we catch the exception and format it into a structured JSON error message returned to the LLM as an assistant/tool response. This turns a hard crash into a conversational feedback loop: the model sees *why* the command failed and can attempt to self-correct its next move.

Error handling is currently inconsistent across modules; some tools return error strings, others raise. `do_rm` is a placeholder that raises `Exception("rm is not implemented")`. Normalisation of error reporting is ongoing.

Stall detection is active: identical `(name,arguments)` tuples are tracked in `executed_states` and a warning is injected if the model repeats them, with graceful loop termination.

## Shell Commands Are Already Tools

Many useful capabilities already exist as CLI programs:
* `git status`
* `pwd`
* `df -h`

The `bash_wrap()` helper converts existing shell commands into model-callable capabilities with very little additional code. We use `shlex.split` during execution to ensure that quoted arguments and whitespace are handled exactly as they would be in a real shell, preventing injection errors and parsing bugs.

*Current conditions.* `bash_wrap` executes directly on the host. There is no OS sandbox for `bash_tools.py` by default; only Podman sandbox code exists and is unused here. Read tools expose arbitrary file reads; write tools include `git merge/checkout/commit`. Permissions are advisory.

## Tool Calls Become Conversation State

One of the easiest mistakes when building agent systems is introducing hidden state or external "side-car" databases for tool history.

`toolex` deliberately avoids this by treating tool invocations as standard conversation messages. An interaction looks like a natural flow:
1. **Assistant:** Requests to call `git_status()`.
2. **Tool:** Returns the actual output of `git status`.
3. **Assistant:** Receives that text and continues reasoning.

Because these are just message objects, tool usage is inherently visible, inspectable, and reproducible in exactly the same way as any other prompt or response. There is no session daemon; there is only the conversation.

The magic header `Content-Type: application/x-llm-history+json` continues to be the transport boundary between `ask`/`help`, `toolex`, and `answer`. Cache hits are shown as 🏆, misses as ✨, with additional emojis for piped content, `bx` shell input and `lx` file input.

## The Pipeline Remains the Interface

Because `toolex` consumes and emits the same structured message format used by `answer`, tool-aware conversations behave like ordinary shell pipelines.

For example:
```bash
ask "Summarize repository changes" | tools git
```

The first example terminates at an interactive shell and produces plain text for the user. The second continues as a machine-readable pipeline, allowing later stages to extract code blocks or request further execution approval.

The shell remains responsible for orchestration.
The conversation remains responsible for state.
The tools merely provide additional observations about the local environment.

## Known Operational Limitations in August 2026

Real use has exposed implementation gaps that affect how capabilities should be described:

* **Unsafe temp handling in `answer`.** `_mktemp_reg` falls back to `mktemp -u`, a classic TOCTOU race. Cleanup via `trap '_cleanup_run_dir'` is PID-guarded but subshells can leak.
* **No request timeouts.** `requests.post` and `curl` have no timeouts in the current release, allowing long hangs.
* **Cache hygiene.** Caching is request-hash based with no TTL/eviction. Permissions of existing cache files are never checked.
* **Input validation.** JSON from the model is trusted after a superficial jq check. No schema validation of tool results before feeding back to the model.
* **Answer `_infer` edge case.** It reads first line then cats the rest – if the first line is not a header the line is prepended back, which can corrupt JSON.

These issues do not invalidate the design goals, but they are the work that keeps the system intentionally boring: tools are ordinary functions with semantic metadata via `Annotated`, permissions are granular and namespaced, errors are conversational feedback for self-correction, and the shell remains the orchestrator. The next step is to close the gaps without adding infrastructure.
