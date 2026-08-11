#!/usr/bin/env python3
from typing import Dict, List, Optional
from tooling import tool, run_bash_tool, bash_wrap, discover_tools, CommandResult

### THese two are legacy
### New tools with engine factoring do not yet support subcommand restrictions (git foo)

def run_bash_tool(name: str, cmd: List[str], args: Optional[str] = "", stdin_data: Optional[str] = None) -> CommandResult:
    """Runs the command directly on host machine with stdin support."""
    args_list = shlex.split(args) if args and args.strip() else []
    full_cmd = list(cmd) + args_list

    try:
        result = subprocess.run(
            full_cmd,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            input=stdin_data
        )
        return CommandResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
    except Exception as exc:
        return CommandResult("", str(exc), 1)


def bash_wrap(name: str, cmd: List[str]):
    """Runs the command directly on host machine."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs):
            sig = inspect.signature(f)
            param_names = [p.name for p in sig.parameters.values()]

            payload = None
            if args and not isinstance(args[0], (dict, list)):
                payload = args[0]
            elif "args" in kwargs:
                payload = kwargs["args"]

            arg_payload = str(payload).strip() if payload is not None else ""

            stdin_data = None
            for p in ("stdin", "input_data"):
                if p in param_names and p in kwargs:
                    stdin_data = kwargs.get(p)
                    break

            print(f"🚀{' '.join(cmd)} {arg_payload}", file=sys.stderr, end='')
            return run_bash_tool(name, cmd, arg_payload, stdin_data=stdin_data)
        wrapper._command_name = name
        return wrapper
    return decorator


# --- READ ONLY TOOLS (Standard) ---

@tool("read")
@bash_wrap("git_status", ["git", "status"])
def get_git_status(args: Optional[str] = "") -> Dict[str, str]: 
    """`git status`: returns the current working tree status. Use args to filter."""
    pass

@tool("read")
@bash_wrap("git_diff", ["git", "diff"])
def get_git_diff(args: Optional[str] = "") -> Dict[str, str]: 
    """`git diff`: shows changes between commits or the working tree."""
    pass

@tool("read")
@bash_wrap("git_branch", ["git", "branch"])
def get_git_branch(args: Optional[str] = "") -> Dict[str, str]: 
    """`git branch`: lists branches. Use args like '-r' for remote."""
    pass

@tool("read")
@bash_wrap("git_log", ["git", "log"])
def get_git_log(args: Optional[str] = "") -> Dict[str, str]: 
    """`git log`: shows commit history. Use args for limiting results."""
    pass

# --- ADVANCED QUERY TOOL (The Fix) ---

@tool("read")
@bash_wrap("git_query", ["git"])
def run_git_query(args: Optional[str] = "") -> Dict[str, str]: 
    """
    Executes complex Git queries with advanced formatting and sorting.
    Allowed subcommands (prefixes): 'branch', 'log', 'show', 'status'.
    Example args: "-r --format='%(authordate:short) %(refname)'"
    Example args: "log -1 origin/main --format=%ai"
    """
    # Whitelist to prevent the LLM from using complex formatting with destructive commands.
    allowed_prefixes = ["branch", "log", "show", "status"]
    
    parts = args.strip().split()
    if not parts:
        return {"git_query": "Error: No subcommand provided."}

    # Check if the first non-flag argument is in our whitelist
    subcommand_found = False
    for part in parts:
        if not part.startswith("-"):
            if part not in allowed_prefixes:
                return {"git_query": f"Error: Subcommand '{part}' is not permitted for queries."}
            subcommand_found = True
            break
    
    if not subcommand_found:
         return {"git_query": "Error: No valid Git subcommand provided (e.g., 'branch' or 'log')."}

    pass # Execution handled by @bash_wrap

# --- WRITE TOOLS (High Privilege) ---

@tool("write")
@bash_wrap("git_merge", ["git", "merge"])
def do_git_merge(args: Optional[str] = "") -> Dict[str, str]: 
    """`git merge`: merges specified branches."""
    pass

@tool("write")
@bash_wrap("git_checkout", ["git", "checkout"])
def do_git_checkout(args: Optional[str] = "") -> Dict[str, str]: 
    """`git checkout`: switches branches or restores files."""
    pass

@tool("write")
@bash_wrap("git_commit", ["git", "commit"])
def do_git_checkout(args: Optional[str] = "") -> Dict[str, str]: 
    """`git commit`: adds changes to local repository."""
    pass


### File must end with this line
__all__ = discover_tools(globals(), __name__)
