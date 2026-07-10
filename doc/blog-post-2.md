# Beyond Pipes: Adding Capabilities to Command-Line Language Models

In the previous article, we treated language models as command-line filters operating within a POSIX shell environment.

That model turns out to be surprisingly powerful. Many development tasks can be expressed naturally as transformations over text streams:

```bash
history | tail -40 | help summarize what I accomplished today
```

```bash
lx *.py | help look for dead code
```

```bash
help write a python script | unfence python | python
```

In each case, the model consumes text, emits text, and remains confined to standard input and standard output.

Eventually, however, the model requires information that exists outside the conversation itself.

Consider a few common development questions:

* Which branches have not yet been merged into `main`?
* Summarize the changes in this repository and suggest a commit message.
* Which files changed between these two commits?
* Which directories are consuming the most disk space?

Without additional capabilities, answering these questions requires the user to gather context manually:

```bash
git branch --no-merged main | help explain these branches
```

or:

```bash
git status
git diff
git log --oneline -10
```

followed by:

```bash
help write a commit message using this information
```

This workflow is perfectly functional, but it requires the user to spend time collecting context rather than interpreting results.

## The Capability Problem

The challenge is straightforward:

How can a language model inspect local state without receiving unrestricted access to the machine?

Many contemporary agent systems address this problem by exposing a complete execution environment consisting of shell access, filesystem access, browser automation, interpreters, planning systems, and background processes.

These systems can be remarkably capable. They also make it substantially more difficult to reason about what operations are available to the model, what modifications it may perform, and what actions occurred during a particular interaction.

Questions that are trivial in ordinary shell workflows become surprisingly difficult to answer:

* What commands can the model execute?
* Which files may it access?
* What modifications may it make?
* Which actions occurred during this session?

The Unix philosophy has traditionally favored explicit interfaces, small composable tools, and least privilege. There is little reason to abandon those principles when introducing language models into the development workflow.

## Introducing `toolex`

`toolex` extends the `answer` pipeline model by introducing explicit capabilities.

Rather than exposing a shell, filesystem, or execution environment, `toolex` exposes a collection of narrowly scoped operations that can be granted independently according to the requirements of a particular workflow. The model receives access only to the capabilities that have been intentionally exposed to it and remains unaware of the remainder of the host environment.

A typical interaction becomes:

```bash
ask "What branches are not merged into main?" | tools git
```

The model determines that additional information is required, requests the appropriate capability, receives the result, and continues the conversation normally.

From the user's perspective, the interaction remains conversational:

```text
🤖 git branch --no-merged main
```

The result of that operation becomes part of the conversation history and can be used immediately by subsequent inference steps.

Similarly:

```bash
ask "Summarize the repository changes and prepare a commit command" \
    | tools git
```

or:

```bash
ask "Find the largest directories consuming disk space" \
    | tools bash
```

The model gains the ability to inspect selected portions of the local environment without receiving unrestricted access to the system as a whole.

## Preserving the Pipeline Model

An important design constraint of `toolex` is that it does not replace `answer` or introduce a parallel execution model.

Instead, it adds an additional stage to the existing pipeline architecture.

The conversation remains a stream flowing through standard input and output. Tool invocations simply become additional messages within the same conversation history, allowing the resulting interactions to retain the same properties that make shell pipelines attractive in the first place:

* transparency,
* composability,
* reproducibility,
* inspectability.

Unlike many agent systems, there is no hidden daemon maintaining internal state and no long-running process accumulating context in the background. The conversation itself remains the state, and the shell remains responsible for orchestration.

## Explicit Capabilities and Explicit Trust

The objective of `toolex` is not to maximize model autonomy.

The objective is to maximize usefulness while preserving operator control.

Repository inspection, file access, and constrained execution are all valuable capabilities when they are granted intentionally and within clearly defined boundaries. Arbitrary command execution, unrestricted filesystem access, and broad environmental visibility should generally require explicit approval rather than being treated as defaults.

In practice, this means that information gathering and state modification remain separate concerns.

For example, the following workflow allows the model to inspect repository state and prepare a commit command:

```bash
ask "$PROMPT" | tools git | unfence | bash
```

The model may invoke `git status` and `git diff`, generate a conventional commit command, and emit it as a fenced shell block.

Execution, however, remains subject to an explicit approval step:

```text
🤖 Found targeted block (bash). Proceed? (y/N)
```

The command does not execute until the operator confirms it.

This distinction is intentional. The model may propose actions, inspect state, and prepare commands, but authority to perform side effects remains outside the model and inside the shell pipeline itself.

The result behaves much more like a traditional shell utility than a virtual employee operating inside a sandboxed workstation.

## Conclusion

The original `answer` model treated language models as composable filters connected through shell pipelines.

`toolex` extends that model by allowing those filters to interact with carefully selected portions of the outside world while preserving the same principles of transparency and least privilege that have made shell environments effective for decades.

The resulting architecture allows the model to observe selected portions of the local environment while preserving operator control over actions that affect that environment.

The next article examines the implementation itself: how ordinary Python functions become model-callable tools, how permissions are enforced, and how tool calls are represented and transported through the same pipeline architecture used by `answer`.
