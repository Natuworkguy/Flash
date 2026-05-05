import os
import subprocess

from colorama import Fore, Style

SYSTEM_PROMPT = """
Answer concisely. Use shell only when command output is needed.
When using shell, call the tool without extra text first.
""".strip()


def shell_tool(command: str) -> str:
    print(Fore.BLUE + f"Executing shell command: {command}" + Style.RESET_ALL)

    if os.name == "nt":
        args = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=15,
        )
    else:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
        )

    parts = [result.stdout.strip(), result.stderr.strip()]
    output = "\n".join(part for part in parts if part)

    if result.returncode and output:
        return f"(exit {result.returncode})\n{output}"

    return output or "(no output)"


# Tool schema expected by google-generativeai function calling.
tools = [
    {
        "function_declarations": [
            {
                "name": "shell",
                "description": "Run a shell command.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Command.",
                        }
                    },
                    "required": ["command"],
                },
            }
        ]
    }
]


FUNCTIONS = {
    "shell": shell_tool,
}


def run_tool(call):
    name, args = call

    func = FUNCTIONS.get(name)
    if func is None:
        return f"Unknown tool: {name}"

    try:
        return func(**args)
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"
