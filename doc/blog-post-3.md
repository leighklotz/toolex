# Building `toolex`: Turning Functions into Capabilities

The previous article introduced `toolex` as a capability layer for `answer`. Rather than giving a language model unrestricted access to a shell or filesystem, `toolex` exposes a carefully selected collection of capabilities that can be granted independently according to the requirements of a particular workflow.

This article focuses on the implementation: how we bridge the gap between standard Python code and structured LLM tool-calling.

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
2. This function requires a specific capability (e.g., `read`).

Conceptually, the result is little more than:

```python
get_weather._is_toolex_tool = True
get_weather._required_caps = {"read"}
```

That turns out to be sufficient information to support discovery, permission enforcement, and schema generation.

## Discovery Through Reflection

Many extensible systems introduce registries, manifests, configuration files, or plugin managers. `toolex` uses Python reflection instead.

Tool discovery consists largely of inspecting imported modules using `inspect`. Functions marked with the decorator become available capabilities instantly upon being imported. Adding a new tool is therefore equivalent to adding a module; there is no additional registration step and no distinction between a tool implementation and an ordinary library function other than the presence of the decorator.

## Semantic Schemas from Type Hints

OpenAI-compatible APIs expect tool definitions to be represented as JSON schemas. Writing those schemas by hand quickly becomes repetitive and error-prone, especially when trying to explain complex constraints to an LLM.

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

Our parser supports granular scoping using colon-delimited syntax (e.g., `--tools git:read weather:write`). This allows for highly specific permission sets where you can grant full access to one module while restricting others, or grant `read` access globally while requiring explicit `write` permissions elsewhere. The core logic remains a simple subset test:

```python
required_caps.issubset(granted_caps)
```

## Error Resilience & Self-Correction

One of the most common failure points in agentic workflows is an unhandled exception during tool execution (like a shell command returning a non-zero exit code). In many systems, this crashes the entire loop or leaves the model "hanging."

`toolex` treats errors as part of the conversation. When a tool fails, we catch the exception and format it into a structured JSON error message returned to the LLM as an assistant/tool response. This turns a hard crash into a conversational feedback loop: the model sees *why* the command failed (e.g., `Permission denied` or `File not found`) and can attempt to self-correct its next move.

## Shell Commands Are Already Tools

Many useful capabilities already exist as CLI programs:
* `git status`
* `pwd`
* `df -h`

The `bash_wrap()` helper converts existing shell commands into model-callable capabilities with very little additional code. This allows us to turn an entire ecosystem of command-line utilities into a library of LLM tools without writing custom Python logic for each one. We use `shlex.split` during execution to ensure that quoted arguments and whitespace are handled exactly as they would be in a real shell, preventing injection errors and parsing bugs.

## Tool Calls Become Conversation State

One of the easiest mistakes when building agent systems is introducing hidden state or external "side-car" databases for tool history.

`toolex` deliberately avoids this by treating tool invocations as standard conversation messages. An interaction looks like a natural flow:
1. **Assistant:** Requests to call `git_status()`.
2. **Tool:** Returns the actual output of `git status`.
3. **Assistant:** Receives that text and continues reasoning.

Because these are just message objects, tool usage is inherently visible, inspectable, and reproducible in exactly the same way as any other prompt or response. There is no session daemon; there is only the conversation.

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

## Why Avoid More Infrastructure?

An obvious question is whether this architecture eventually grows into a scheduler, planner, or heavy orchestration layer. Perhaps.

For now, our design constraints remain:
* Tools should be ordinary functions with semantic metadata via `Annotated`.
* Permissions should be granular and namespaced.
* Errors should be conversational feedback for self-correction.
* The shell remains the orchestrator.

The implementation is intentionally boring. That is probably its most useful property.
