import subprocess
from colorama import Fore, Style

SYSTEM_PROMPT = """
You can use tools when useful.
Use the provided function-calling interface instead of text-based tool
directives.
""".strip()


def shell_tool(command: str) -> str:
    print(Fore.BLUE + f"Executing shell command: {command}" + Style.RESET_ALL)

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout or result.stderr or "(no output)"


# Tool schema expected by google-generativeai function calling.
tools = [
    {
        "function_declarations": [
            {
                "name": "shell",
                "description": "Execute shell commands and return output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute.",
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
