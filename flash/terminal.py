"""What the user ran in VS Code's terminal, so Flash knows what just broke.

A small hook, installed into the user's shell with /hook install, records
every command run in VS Code's integrated terminal (it switches itself
off anywhere else) as one line of LOG_PATH: when, exit code, how long,
where, and the command. Before each message, Flash attaches the commands
run since the last one, so "why did that fail?" needs no pasting.

A shell hook sees commands and exit codes, never output: that goes
straight to the terminal. Flash re-runs a failed command itself when it
needs the error text and the command is safe to repeat.
"""

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

FLASH_DIR = Path.home() / ".flash"
LOG_PATH = FLASH_DIR / "terminal.log"

MAX_LOG_LINES = 500
SHOWN = 8
FIRST_LOOK_SECONDS = 15 * 60
COMMAND_CHARS = 200

BEGIN = "# >>> flash terminal hook >>>"
END = "# <<< flash terminal hook <<<"

HEADER = (
    "=== Commands the user ran in VS Code's terminal since their last "
    "message ==="
)
FOOTER = "=== End of terminal commands ==="

# Installed as ~/.flash/hook.zsh. $? is read first thing in precmd, and
# the hook goes first in precmd_functions, so no other prompt hook can
# overwrite the exit status before it is recorded.
ZSH_HOOK = r"""# Flash: logs commands run in VS Code's terminal (see /hook).
[[ "$TERM_PROGRAM" == "vscode" ]] || return 0
zmodload zsh/datetime 2>/dev/null || return 0
typeset -g _flash_cmd="" _flash_start=0
_flash_preexec() {
  _flash_cmd="$1"
  _flash_start=$EPOCHREALTIME
}
_flash_precmd() {
  local code=$?
  [[ -n "$_flash_cmd" ]] || return $code
  local cmd="$_flash_cmd"
  _flash_cmd=""
  [[ "$cmd" == " "* ]] && return $code
  local took=$(( EPOCHREALTIME - _flash_start ))
  cmd="${cmd//$'\n'/ }"
  cmd="${cmd//$'\t'/ }"
  ( umask 077; mkdir -p "$HOME/.flash"
    print -r -- "${EPOCHREALTIME/,/.}	${code}	${took%.*}	${PWD}	${cmd}" \
      >> "$HOME/.flash/terminal.log" )
  return $code
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec _flash_preexec
precmd_functions=(_flash_precmd ${precmd_functions:#_flash_precmd})
"""

# Installed as ~/.flash/hook.bash. Bash has no preexec, so the command
# comes from history and there is no duration; the first prompt only
# notes where history stands, or the last command of the previous
# session would be logged as if it had just run.
BASH_HOOK = r"""# Flash: logs commands run in VS Code's terminal (see /hook).
[[ "$TERM_PROGRAM" == "vscode" ]] || return 0
_flash_last=""
_flash_ready=0
_flash_prompt() {
  local code=$?
  local entry
  entry=$(HISTTIMEFORMAT= history 1)
  if [[ $_flash_ready == 0 ]]; then
    _flash_ready=1; _flash_last="$entry"; return $code
  fi
  [[ -z "$entry" || "$entry" == "$_flash_last" ]] && return $code
  _flash_last="$entry"
  local cmd
  cmd=$(printf '%s' "$entry" | sed -E 's/^ *[0-9]+\*? {2}//')
  [[ "$cmd" == " "* ]] && return $code
  cmd="${cmd//$'\n'/ }"
  cmd="${cmd//$'\t'/ }"
  local stamp=${EPOCHREALTIME:-$(date +%s)}
  ( umask 077; mkdir -p "$HOME/.flash"
    printf '%s\t%s\t-\t%s\t%s\n' "${stamp/,/.}" "$code" "$PWD" "$cmd" \
      >> "$HOME/.flash/terminal.log" )
  return $code
}
if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
  PROMPT_COMMAND=(_flash_prompt "${PROMPT_COMMAND[@]}")
else
  PROMPT_COMMAND="_flash_prompt${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
fi
"""

HOOKS = {"zsh": ZSH_HOOK, "bash": BASH_HOOK}
RC_FILES = {"zsh": ".zshrc", "bash": ".bashrc"}

_SECRETS = [
    re.compile(
        r"(\b[A-Za-z_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z_]*=)"
        r"(\"[^\"]*\"|'[^']*'|[^\s'\"]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(--?(?:password|passwd|token|secret|api[-_]?key|auth)[= ])"
        r"(\"[^\"]*\"|'[^']*'|[^\s'\"]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(Authorization:\s*(?:Bearer|Basic)\s+)([^\s'\"]+)", re.IGNORECASE
    ),
    re.compile(r"(://[^/\s:@]+:)([^@\s/]+)(@)"),
]


@dataclass
class Command:
    """One command the user ran."""

    time: float
    exit: int
    took: Optional[int]
    cwd: str
    command: str


# --- reading the log -----------------------------------------------------


def _parse(line: str) -> Optional[Command]:
    parts = line.rstrip("\n").split("\t", 4)
    if len(parts) != 5:
        return None
    stamp, code, took, cwd, command = parts
    try:
        return Command(
            float(stamp), int(code),
            None if took in ("", "-") else int(took), cwd, command,
        )
    except ValueError:
        return None


def read() -> list[Command]:
    """Every logged command, oldest first; trims the log as it goes."""

    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    if len(lines) > MAX_LOG_LINES:
        lines = lines[-MAX_LOG_LINES:]
        try:
            LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    parsed = (_parse(line) for line in lines)
    return [command for command in parsed if command]


def redact(command: str) -> str:
    """COMMAND with anything that looks like a secret blanked out."""

    for pattern in _SECRETS:
        command = pattern.sub(
            lambda m: m.group(1) + "[redacted]"
            + (m.group(3) if m.lastindex and m.lastindex >= 3 else ""),
            command,
        )
    return command


def _where(cwd: str) -> str:
    home = str(Path.home())
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd


def _line(command: Command) -> str:
    text = redact(command.command)
    if len(text) > COMMAND_CHARS:
        text = text[: COMMAND_CHARS - 1] + "…"
    mark = "✓" if command.exit == 0 else f"✗ exit {command.exit}"
    took = f" · {command.took}s" if command.took else ""
    return f"{mark}{took} · {_where(command.cwd)} · {text}"


def since(after: float) -> str:
    """The commands run after AFTER, as the block put before a message."""

    recent = [
        c for c in read()
        if c.time > after and c.command.split()[:1] != ["flash"]
    ]
    if not recent:
        return ""

    lines = [HEADER]
    if len(recent) > SHOWN:
        lines.append(f"({len(recent) - SHOWN} earlier left out)")
    lines += [_line(c) for c in recent[-SHOWN:]]
    lines.append(FOOTER)
    return "\n".join(lines)


def start_time() -> float:
    """Where to start looking on a session's first message."""

    return time.time() - FIRST_LOOK_SECONDS


# --- installing the hook -------------------------------------------------


def current_shell() -> str:
    """"zsh" or "bash" if that is the user's shell, else ""."""

    name = os.path.basename(os.environ.get("SHELL", ""))
    return name if name in HOOKS else ""


def hook_path(shell: str) -> Path:
    return FLASH_DIR / f"hook.{shell}"


def rc_path(shell: str) -> Path:
    return Path.home() / RC_FILES[shell]


def rc_block(shell: str) -> str:
    hook = f'"$HOME/.flash/hook.{shell}"'
    return (
        f"{BEGIN}\n"
        "# Lets Flash see the commands you run in VS Code's terminal.\n"
        f"[ -f {hook} ] && . {hook}\n"
        f"{END}\n"
    )


def installed(shell: str) -> bool:
    try:
        return BEGIN in rc_path(shell).read_text(encoding="utf-8")
    except OSError:
        return False


def write_hook(shell: str) -> None:
    """(Re)write the hook script itself, so fixes reach an installed hook."""

    FLASH_DIR.mkdir(parents=True, exist_ok=True)
    hook_path(shell).write_text(HOOKS[shell], encoding="utf-8")


def install(shell: str) -> str:
    write_hook(shell)
    if installed(shell):
        return f"Already set up in {rc_path(shell)}."

    rc = rc_path(shell)
    try:
        existing = rc.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    gap = "" if not existing or existing.endswith("\n\n") else (
        "\n" if existing.endswith("\n") else "\n\n"
    )
    with rc.open("a", encoding="utf-8") as handle:
        handle.write(gap + rc_block(shell))
    return (
        f"Added to {rc}. Open a new VS Code terminal (or run "
        f"`source {rc}`) for it to take effect."
    )


def remove(shell: str) -> str:
    rc = rc_path(shell)
    try:
        text = rc.read_text(encoding="utf-8")
    except OSError:
        return f"Nothing to remove: {rc} has no Flash hook."

    # The blank line install() put before the block goes with it.
    block = re.compile(
        r"\n?" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n",
        re.DOTALL,
    )
    cleaned, count = block.subn("", text)
    if not count:
        return f"Nothing to remove: {rc} has no Flash hook."

    rc.write_text(cleaned, encoding="utf-8")
    hook_path(shell).unlink(missing_ok=True)
    return f"Removed from {rc}. Terminals already open keep it until closed."
