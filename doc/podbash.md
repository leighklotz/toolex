This document describes the `podbash_tools` module for **Toolex**, which is designed to allow an LLM to execute shell commands on a host machine through a sandboxed environment using **Podman** (a daemonless container engine).

This module augments existing **capability-based security** with a read-only container execution wrapper. Always use caution when executing untrusted or potentially inaccurately generated code, even in sandboxed environments.

### 1. The Architecture: "Sandbox Wrap" Pattern
Instead of running LLM commands directly on your host machine (which would be incredibly dangerous), this system uses two layers of protection defined in `tooling.py`:

*   **`@tool(capabilities)`**: A decorator that labels a function with its intent. 
    *   `read` = The LLM can only view data.
    *   `write` = The LLM is allowed to modify/delete files.
*   **`@sandbox_wrap(name, command)`**: This wraps the Python function so that when it's called, instead of running on your actual laptop, it spawns a **Podman container**, mounts your data folder into it, runs the command inside that "bubble," and then destroys the bubble immediately.

At tool execution time, the capability-based security model assumes `read` unless an explicit `--tool podbash:write` argument is provided.

### 2. Component Breakdown
#### `podbash_tools.py`
This is a collection of standard Linux commands turned into Python tools.
*   **Read Tools:** Commands like `ls`, `pwd`, `cat`, and `grep` are decorated with `@tool("read")`. These are executed in the container with **Read-Only (`:ro`)** mounts. If an LLM tries to run `rm` through a "read" tool, it will fail at the filesystem level.
*   **Write Tools:** The `do_rm` (remove) function is decorated with `@tool("write")`. This informs the system that this specific command requires **Read-Write (`:rw`)** access to the mounted directory.

#### `toolex-sandbox/Dockerfile`
This defines the "universe" inside the sandbox. 
*   It uses a lightweight Debian image.
*   It installs only essential tools (`bash`, `grep`, `findutils`, etc.).
*   **Security Feature:** It creates a non-root user (`sandboxuser`). Even if an LLM manages to execute arbitrary code, they are trapped as a low-privilege user inside a container that has no access to the host's sensitive system files.

#### `tooling.py`
*   **Dynamic Permission Logic:** When `run_podman_tool` is called, it inspects whether the function has `read` or `write` capabilities. It then constructs a Podman command that mounts your host directory (`host_data_dir`) as either `:ro` (safe) or `:rw` (dangerous).

See `README.md` for security properties:

1.  **Automatic Permission Escalation (Least Privilege):** The system doesn't grant "all access" by default. It inspects the `@tool` tag to decide whether to mount the folder as Read-Only or Read-Write.
2.  **The "Chroot" Effect (Filesystem Isolation):** Because the command runs inside a container, the LLM perceives `/workspace` as its entire world. If an LLM tries to run `ls /`, they will not see your actual computer files; they will only see the minimal filesystem provided by the Docker image.
3.  **Zero Residue (Ephemeral Environments):** Because Podman runs with the `--rm` flag, every tool call is a fresh start. If an LLM attempts to modify environment variables or install software via `export PATH=...`, those changes vanish the millisecond the command finishes. It cannot "poison" its environment for subsequent turns.

### Summary of Workflow
1.  **LLM wants to view files:** Calls `get_ls()`. 
2.  **System detects `@tool("read")`**: Starts a Podman container $\rightarrow$ Mounts `/your/data` as **Read-Only** $\rightarrow$ Runs `ls` $\rightarrow$ Returns results $\rightarrow$ Deletes Container.
3.  **LLM wants to delete a file:** Calls `do_rm()`.
4.  **System detects `@tool("write")`**: Starts a Podman container $\rightarrow$ Mounts `/your/data` as **Read-Write** $\rightarrow$ Runs `rm` $\rightarrow$ Returns results $\rightarrow$ Deletes Container.
