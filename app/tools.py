import os
import subprocess
from typing import Any, Callable

SYSTEM_PROMPT = """
Answer concisely. Use shell only when command output is needed.
When using shell, call the tool without extra text first.
""".strip()


def shell_command_args(command: str, *, os_name: str | None = None):
    os_name = os_name or os.name
    if os_name == "nt":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
    return command


def run_shell_command(
    command: str,
    *,
    os_name: str | None = None,
    process_runner: Callable[..., Any] = subprocess.run,
    output: Callable[[str], None] = print,
) -> str:
    output(f"Executing shell command: {command}")
    os_name = os_name or os.name
    args = shell_command_args(command, os_name=os_name)

    kwargs = dict(capture_output=True, text=True, timeout=15)
    if os_name != "nt":
        kwargs["shell"] = True

    result = process_runner(args, **kwargs)
    parts = [result.stdout.strip(), result.stderr.strip()]
    combined = "\n".join(part for part in parts if part)

    if result.returncode and combined:
        return f"(exit {result.returncode})\n{combined}"
    return combined or "(no output)"


def shell_tool(command: str) -> str:
    return run_shell_command(command)


tools = [{"function_declarations": [{"name": "shell", "description": "Run a shell command.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "Command."}}, "required": ["command"]}}]}]

FUNCTIONS = {"shell": shell_tool}

def shell_tool(command: str) -> str:
    return run_shell_command(command)

def run_tool(call, *, functions: dict[str, Callable[..., str]] | None = None):
    name, args = call
    functions = functions or FUNCTIONS
    func = functions.get(name)
    if func is None:
        return f"Unknown tool: {name}"
    try:
        return func(**args)
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"
