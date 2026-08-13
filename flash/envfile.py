"""Persists NAME=VALUE pairs to Flash's dotenv file."""

from pathlib import Path


def _format_value(value: str) -> str:
    if value == "" or any(c in value for c in " \t#\"'"):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def set_env_var(path: str, name: str, value: str) -> None:
    """Create or update a NAME=VALUE line in the env file at path."""

    file = Path(path)
    lines = (
        file.read_text(encoding="utf-8").splitlines()
        if file.exists()
        else []
    )

    new_line = f"{name}={_format_value(value)}"
    prefix = f"{name}="

    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)

    file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def unset_env_var(path: str, name: str) -> bool:
    """Remove NAME's line from the env file at path.

    Returns True if a line was removed.
    """

    file = Path(path)
    if not file.exists():
        return False

    prefix = f"{name}="
    lines = file.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.strip().startswith(prefix)]

    if len(kept) == len(lines):
        return False

    text = "\n".join(kept) + ("\n" if kept else "")
    file.write_text(text, encoding="utf-8")
    return True
