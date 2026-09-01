from typing import Annotated, Any, Optional
from tooling import tool, CommandResult
from minishell import CommandRegistry, MiniShell, HostRunner

_shell: Optional[MiniShell] = None

def configure(registry: CommandRegistry, runner: Any, granted: frozenset):
    """Called by toolex.py during startup."""
    global _shell
    if _shell is None:
        # If no shell was configured yet, create it with the provided permissions
        _shell = MiniShell(registry, runner, granted)

@tool("read")
def shell(command_line: Annotated[str, "A command line. Use '|' for pipes. Start with 'help'."]) -> CommandResult:
    """Execute a command in minishell. 127=Command Not Found, 126=Permission Denied."""
    global _shell
    if not _shell:
        return CommandResult("", "minishell not configured", 1)
    # The shell returns the result; toolex.py will call .to_payload() on it later (Fixes D3).
    return _shell.execute(command_line)

def bootstrap(permission_map):
    """Fallback for local testing."""
    import bash_tools # This fills the registry via scan in __init__ logic elsewhere
    global _shell_instance
    if _shell_instance is None:
        # Defaulting to local/safe execution for tool testing environments
        registry = CommandRegistry() 
        runner = HostRunner() # or PodmanRunner depending on environment configuration
        import bash_tools
        registry.scan(bash_tools, permission_map=permission_map)
        configure(registry, runner)

