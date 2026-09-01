from minishell import command, CommandResult
import inspect

@command("ls", doc="List directory contents.")
def get_ls(args: str = "") -> CommandResult: ...

@command("cat", "Read file content.", consumes_stdin=True)
def get_cat(args: str = "", stdin: str | None = None) -> CommandResult: ...

@command("grep", "Search patterns in text.", consumes_stdin=True)
def get_grep(args: str = "", stdin: str | None = None) -> CommandResult: ...

@command("patch", "Apply diffs.", caps="write", consumes_stdin=True)
def do_patch(args: str = "", stdin: str | None = None) -> CommandResult: ...

def _validate_find(args: str):
    if args.strip().startswith("."): raise ValueError("Search of '.' is banned for safety.")

@command("find", "Search files.", validate=_validate_find)
def get_find(args: str = "") -> CommandResult: ...
