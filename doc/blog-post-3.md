# Building `toolex`: Turning Functions into Capabilities

The previous article introduced `toolex` as a capability layer for `answer`. Rather than giving a language model unrestricted access to a shell or filesystem, `toolex` exposes a carefully selected collection of capabilities that can be granted independently according to the requirements of a particular workflow.

This article focuses on the implementation.

One of the primary design constraints was that a tool should feel no more complicated than writing an ordinary Python function. There are no base classes to inherit from, no servers to register with, and no schemas to write by hand.

If a developer can write a function, they can write a tool.

## Tools Are Just Functions

The smallest useful tool looks like this:

```python
@tool("read")
def get_weather(location: str):
    ...
```

The function remains an ordinary Python function.

The decorator simply attaches metadata describing two properties:

* this function should be visible to the model, and
* this function requires the `read` capability.

Conceptually, the result is little more than:

```python
get_weather._is_toolex_tool = True
get_weather._required_caps = {"read"}
```

That turns out to be sufficient information to support discovery, permission enforcement, and schema generation.

## Discovery Through Reflection

Many extensible systems introduce registries, manifests, configuration files, or plugin managers.

`toolex` uses Python reflection instead.

Tool discovery consists largely of inspecting imported modules:

```python
inspect.getmembers(module, inspect.isfunction)
```

Functions marked with `_is_toolex_tool` become available capabilities.

Adding a new tool is therefore equivalent to importing a Python module. There is no additional registration step, and no distinction between a tool implementation and an ordinary Python library function other than the presence of the decorator.

## Schemas from Type Hints

OpenAI-compatible APIs expect tool definitions to be represented as JSON schemas.

Writing those schemas by hand quickly becomes repetitive and error-prone.

Instead, `toolex` derives them directly from Python signatures and type annotations.

Given:

```python
def get_weather(location: str):
```

the framework automatically derives an equivalent schema describing a single required string parameter named `location`.

Lists become arrays.

Integers become integers.

Optional values become optional schema members.

From the developer's perspective, the implementation remains ordinary Python code while the framework handles translation into the wire protocol expected by the model API.

## Permissions Are Sets

Many frameworks implement authorization using policy engines, access control lists, or role hierarchies.

`toolex` uses Python sets.

A declaration such as:

```python
@tool("write exec")
```

becomes:

```python
{"write", "exec"}
```

A command line invocation such as:

```bash
tools git:read
```

produces:

```python
{"read"}
```

Determining whether a tool should be exposed to the model therefore reduces to a subset test:

```python
required.issubset(granted)
```

There is very little machinery hiding underneath the abstraction, which makes the resulting behavior relatively easy to inspect and reason about.

## Shell Commands Are Already Tools

Many useful capabilities already exist:

* `git status`
* `git diff`
* `pwd`
* `df`
* `find`

The `bash_wrap()` helper converts existing shell commands into model-callable capabilities with very little additional code:

```python
@tool("read")
@bash_wrap("git_status", ["git", "status"])
def get_git_status(args=""):
    pass
```

The wrapper executes the command, captures its output, and returns the result to the model as structured data.

The same mechanism is used for filesystem inspection commands, repository operations, and various shell utilities. Existing command-line programs become capabilities rather than requiring bespoke implementations for every operation.

## Tool Calls Become Conversation State

One of the easiest mistakes when building agent systems is introducing hidden state.

`toolex` deliberately avoids this by treating tool invocations as conversation messages.

Conceptually, an interaction looks something like:

```text
assistant:
  call git_status()

tool:
  On branch main
  ...
```

Those messages continue flowing through the pipeline exactly like ordinary conversation history.

Tool usage therefore becomes visible, inspectable, and reproducible in exactly the same way as prompts and responses.

There is no session daemon.

There is no background memory service.

There is no separate execution database.

The conversation remains the state.

## The Pipeline Remains the Interface

Because `toolex` consumes and emits the same structured message format used by `answer`, tool-aware conversations continue to behave like ordinary shell pipelines.

For example:

```bash
ask "Summarize repository changes" | tools git
```

or:

```bash
ask "$PROMPT" | tools git | unfence | bash
```

The first example terminates at an interactive shell and automatically produces plain text for the user.

The second continues as a machine-readable pipeline, allowing later stages to extract code blocks and request execution approval.

The shell remains responsible for orchestration.

The conversation remains responsible for state.

The tools merely provide additional observations about the local environment.

## Why Avoid More Infrastructure?

An obvious question is whether this architecture eventually grows into a scheduler, planner, orchestration layer, or execution environment.

Perhaps.

For now, the design constraints remain deliberately simple:

* tools should be ordinary functions,
* permissions should be explicit,
* conversations should remain portable,
* state should remain inspectable,
* and the shell should remain responsible for orchestration.

The implementation is intentionally boring.

That is probably its most useful property.
