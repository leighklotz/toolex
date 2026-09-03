from minishell import command, CommandResult
import inspect

# Set during bootstrap; gives native builtins access to the live registry.
_registry = None

def set_registry(reg):
    global _registry
    _registry = reg

@command("pwd", doc="Show Current Directory.")
def get_pwd(args: str = "") -> CommandResult: ...

@command("ls", doc="List directory contents.")
def get_ls(args: str = "") -> CommandResult: ...

@command("cat", doc="Read file content.", consumes_stdin=True)
def get_cat(args: str = "", stdin: str | None = None) -> CommandResult: ...

@command("grep", doc="Search patterns in text.", consumes_stdin=True)
def get_grep(args: str = "", stdin: str | None = None) -> CommandResult: ...

@command("patch", doc="Apply diffs.", caps="write", consumes_stdin=True)
def do_patch(args: str = "", stdin: str | None = None) -> CommandResult: ...

def _validate_find(args: str):
    first = args.split()[0] if args.split() else ""
    if not first or first.startswith((".", "-")):   # '-' catches implicit-'.' forms like `find -name x`
        raise ValueError("find: give an explicit starting path ('.' is banned for safety).")

@command("find", doc="Search files.", validate=_validate_find)
def get_find(args: str = "") -> CommandResult: ...

@command("help", doc="List available commands, or show usage for one.", native=True)
def do_help(args: str = "", _stdin: str = "") -> CommandResult:
    if _registry is None:
        return CommandResult("", "help: registry not initialized", 1)

    if args.strip():
        # Show detail for a single command
        spec = _registry.get(args.strip())
        if spec is None:
            return CommandResult("", f"help: unknown command '{args.strip()}'", 1)
        caps = " ".join(sorted(spec.caps)) if spec.caps else "none"
        stdin_note = " [reads stdin]" if spec.consumes_stdin else ""
        return CommandResult(
            f"{spec.name}{stdin_note}\n"
            f"  {spec.doc}\n"
            f"  capabilities: {caps}",
            "", 0,
        )

    # No args: print the full index
    return CommandResult(_registry.index(), "", 0)
