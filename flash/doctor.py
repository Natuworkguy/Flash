"""flash doctor: whether everything Flash leans on is there and working,
and, for what is not, how to fix it.

Each check says ok, warn (works, but something is missing or off),
fail (something Flash needs is broken) or off (not set up, which is
fine), in a line, with the fix for anything not ok. Looking changes
nothing. A fix that is a command to run (installing a package, pulling
a model, starting Ollama) is run when asked: `flash doctor --fix`,
`/doctor fix`, or its Run button in Settings in the web UI.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

OK, WARN, FAIL, OFF = "ok", "warn", "fail", "off"
# Less free than this on the disk Flash keeps its things on is a warning.
LOW_DISK = 2 * 1024 ** 3
OLLAMA_TIMEOUT = 4


@dataclass
class Check:
    name: str
    state: str
    said: str
    fix: str = ""
    # The command that fixes it, when one does; BACKGROUND for one that
    # keeps running, started and left to it (`ollama serve`).
    command: list[str] = field(default_factory=list)
    background: bool = False

    def to_dict(self) -> dict:
        return {**asdict(self), "fixable": self.fixable}

    @property
    def fixable(self) -> bool:
        return bool(self.command) and self.state in (WARN, FAIL)


def _python() -> Check:
    # Flash will not install on an older one, so it is only said.
    version = ".".join(map(str, sys.version_info[:3]))
    return Check("Python", OK, f"Python {version}, {sys.executable}")


def _packages() -> Check:
    from . import installer

    missing = []
    for requirement in installer.required():
        name, _, marker = requirement.partition(";")
        if marker and "Windows" in marker and os.name != "nt":
            continue
        name = re.split(r"[<>=!~\[ ]", name.strip(), maxsplit=1)[0]
        module = {v: k for k, v in installer.IMPORT_NAMES.items()}.get(
            name, name).replace("-", "_")
        if name.startswith("types-"):
            continue
        if importlib.util.find_spec(module) is None:
            missing.append(name)
    if missing:
        return Check(
            "Packages", FAIL, "Missing: " + ", ".join(missing) + ".",
            f"{sys.executable} -m pip install " + " ".join(missing),
            command=[sys.executable, "-m", "pip", "install", *missing])
    return Check("Packages", OK, "Everything Flash needs is installed.")


def _ollama(host: str, model: str) -> list[Check]:
    from . import models

    try:
        import ollama

        client = ollama.Client(host=host, timeout=OLLAMA_TIMEOUT)
        here = models.installed_names(client)
    except Exception:  # noqa: BLE001  (any failure is the same answer)
        here = None
    if here is None:
        local = re.search(r"//(localhost|127\.0\.0\.1)(:|/|$)", host)
        start: list[str] = []
        if local and sys.platform == "darwin" and \
                Path("/Applications/Ollama.app").exists():
            start = ["open", "-a", "Ollama"]
        elif local and shutil.which("ollama"):
            start = ["ollama", "serve"]
        return [Check(
            "Ollama", FAIL, f"Flash cannot reach Ollama at {host}.",
            "Start it (open the Ollama app, or run `ollama serve`), or "
            "install it from ollama.com. Another host goes in OLLAMA_HOST.",
            command=start, background=start[:1] == ["ollama"],
        )]
    found = [Check("Ollama", OK,
                   f"Ollama at {host}, with {len(here)} model"
                   f"{'' if len(here) == 1 else 's'}.")]
    if not model:
        found.append(Check("Model", WARN, "No model is picked.",
                           "/model in the terminal, or the model picker "
                           "in the web UI."))
    elif model.startswith("@"):
        found.append(Check("Model", OK, f"{model}, from an extension."))
    elif models.full_name(model) not in here and model not in here:
        found.append(Check("Model", FAIL, f"{model} is not in Ollama.",
                           f"ollama pull {model}, or pick another with "
                           "/model.",
                           command=["ollama", "pull", model]
                           if shutil.which("ollama") else []))
    else:
        found.append(Check("Model", OK, model))
    return found


def _folder() -> Check:
    from .paths import FLASH_DIR

    try:
        FLASH_DIR.mkdir(parents=True, exist_ok=True)
        probe = FLASH_DIR / ".doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("Flash's folder", FAIL,
                     f"{FLASH_DIR} cannot be written: {exc}.",
                     f"Check who owns {FLASH_DIR} and that the disk is "
                     "not full.")
    free = shutil.disk_usage(FLASH_DIR).free
    if free < LOW_DISK:
        return Check("Flash's folder", WARN,
                     f"Only {free / 1024 ** 3:.1f} GB free on its disk.",
                     "Free some space: models and chats need room.")
    return Check("Flash's folder", OK, str(FLASH_DIR))


def _extensions() -> list[Check]:
    from . import extensions

    loaded = extensions.installed()
    problems = extensions.problems()
    if not loaded and not problems:
        return [Check("Extensions", OFF, "None installed.")]
    found = [Check("Extensions", OK if not problems else WARN,
                   f"{len(loaded)} working"
                   + (f", {len(problems)} not loading" if problems else "")
                   + ".")]
    for problem in problems:
        found.append(Check("Extension", WARN, problem,
                           "flash remove <name>, or install it again."))
    return found


def _connectors() -> list[Check]:
    from . import connectors

    config = connectors.read_config()
    if not config:
        return [Check("Connectors", OFF, "None connected.")]
    found = []
    for name, spec in config.items():
        command = spec.get("command")
        if command and not shutil.which(str(command)):
            found.append(Check(
                f"Connector {name}", FAIL,
                f"Its command, {command}, is not on this computer.",
                f"Install it, or /mcp remove {name}."))
        elif not command and not spec.get("url"):
            found.append(Check(f"Connector {name}", FAIL,
                               "It has no command or URL.",
                               f"/mcp remove {name}, and add it again."))
    if not found:
        found.append(Check("Connectors", OK, f"{len(config)} set up."))
    return found


def _vault() -> Check:
    from . import vault

    have = vault.names()
    if not have:
        return Check("Secrets", OFF, "None kept.")
    unreadable = [n for n in have if vault.get(n) is None]
    if unreadable:
        return Check(
            "Secrets", FAIL, "Cannot be opened: " + ", ".join(unreadable)
            + ". Their key is gone from " + vault.where_kept() + ".",
            "Remove them with /secret rm NAME and keep them again.")
    return Check("Secrets", OK, f"{len(have)}, with the key in "
                 f"{vault.where_kept()}.")


def _sparks() -> Check:
    from . import keepalive, sparks

    count = len(sparks.all_sparks())
    if not count:
        return Check("Sparks", OFF, "None yet.")
    working = sum(1 for s in sparks.all_sparks() if not s.paused)
    if keepalive.installed():
        return Check("Sparks", OK, f"{count}, kept working while Flash is "
                     "closed.")
    return Check("Sparks", WARN if working else OK,
                 f"{count}, working only while Flash is open.",
                 "/sparks always on keeps them working when it is not."
                 if working else "")


def _mac() -> list[Check]:
    from . import computer, computeruse

    found = []
    if computeruse.switched_on():
        why = computeruse.missing()
        found.append(Check(
            "Computer Use", WARN if why else OK,
            why or "On. macOS asks for Accessibility and Screen Recording "
            "the first time.",
            "System Settings > Privacy & Security > Accessibility and "
            "Screen Recording: turn on the app Flash runs in." if why
            else ""))
    if computer.allowed("imessage_read"):
        try:
            with open(computer.MESSAGES_DB, "rb"):
                pass
            found.append(Check("iMessages", OK, "Messages can be read."))
        except PermissionError:
            found.append(Check(
                "iMessages", FAIL, "macOS keeps Messages private from the "
                "app Flash runs in.",
                "System Settings > Privacy & Security > Full Disk Access: "
                "turn on the app Flash runs in, then reopen it."))
        except OSError:
            found.append(Check("iMessages", WARN,
                               "There is no Messages history here."))
    return found


def _computer_use_elsewhere() -> list[Check]:
    from . import computeruse

    if not computeruse.switched_on():
        return []
    why = computeruse.missing()
    return [Check("Computer Use", WARN if why else OK, why or "On.",
                  "Install what it names." if why else "")]


def run(host: str = "", model: str = "") -> list[Check]:
    """Every check, in order. HOST and MODEL default to Flash's own."""

    host = host or os.getenv("OLLAMA_HOST") or "http://localhost:11434"
    model = model or os.getenv("MODEL") or ""
    found: list[Check] = [_python(), _packages()]
    found += _ollama(host, model)
    found.append(_folder())
    for part in (_extensions, _connectors):
        found += part()
    found.append(_vault())
    found.append(_sparks())
    found += _mac() if sys.platform == "darwin" else \
        _computer_use_elsewhere()
    return found


MARKS = {OK: "✓", WARN: "!", FAIL: "✗", OFF: "·"}


def report(checks: list[Check],
           say: Optional[Callable[[str, str], None]] = None) -> int:
    """CHECKS as lines, through SAY(line, state); how many failed."""

    say = say or (lambda line, state: print(line))
    for check in checks:
        say(f"{MARKS[check.state]} {check.name}: {check.said}", check.state)
        if check.fix and check.state != OK:
            say(f"    {check.fix}", "fix")
    failed = sum(1 for c in checks if c.state == FAIL)
    warned = sum(1 for c in checks if c.state == WARN)
    say("All good." if not failed and not warned else
        f"{failed} to fix, {warned} to look at.",
        FAIL if failed else WARN if warned else OK)
    return failed


def fixable(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.fixable]


def hint(checks: list[Check], how: str = "flash doctor --fix") -> str:
    """The line saying how many can be fixed by a command, and how; ""
    when none can."""

    count = len(fixable(checks))
    if not count:
        return ""
    return (f"Run {how} to fix {count} fixable "
            f"issue{'' if count == 1 else 's'}.")


FIX_TIMEOUT = 30 * 60


def run_fix(check: Check) -> tuple[bool, str]:
    """Run CHECK's command: whether it worked, and what it said (its
    last lines). One that keeps running is started and left to run."""

    if not check.command:
        return False, "There is no command that fixes this."
    try:
        if check.background:
            subprocess.Popen(  # nosec B603 -- a fixed command
                check.command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            return True, f"Started {' '.join(check.command)}."
        done = subprocess.run(  # nosec B603 -- a fixed command
            check.command, capture_output=True, text=True,
            timeout=FIX_TIMEOUT, check=False)
    except FileNotFoundError:
        return False, f"{check.command[0]} is not on this computer."
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    said = "\n".join((done.stdout + done.stderr).strip().splitlines()[-6:])
    return done.returncode == 0, said or "Done."


def fix_all(checks: list[Check],
            say: Optional[Callable[[str, str], None]] = None) -> int:
    """Run every fix there is a command for, saying each; how many
    worked."""

    say = say or (lambda line, state: print(line))
    worked = 0
    for check in fixable(checks):
        say(f"Fixing {check.name}: {' '.join(check.command)}", "fix")
        ok, said = run_fix(check)
        worked += ok
        say(("  ✓ " if ok else "  ✗ ") + said.replace("\n", "\n    "),
            OK if ok else FAIL)
    return worked
