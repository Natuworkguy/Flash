"""Standing rules: the user's own word on one kind of step, kept.

Ask first asks before every command and every change to a file; Auto
asks about none. A rule sits in between, for one kind of step:

- allow: "No need to ask before running `npm test`", or "before changing
  files in ~/code/site/src";
- ask: "Always ask before running `git push`", asked even in Auto and
  Careful;
- never: "Never run `docker system prune`": refused, and the model told
  so.

Only the user makes a rule: with the Always allow button on a question,
which says the rule it makes, with /rules in the terminal, or in
Settings. Nothing the model says makes or changes one. No rule touches
what always asks (an email, an iMessage, printing). An allow never
covers a command that chains, pipes or redirects, nor one Careful mode
calls high stakes, nor a change to a file outside its folder.

Kept in ~/.flash/rules.json.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from . import paths, stakes

VERDICTS = ("allow", "ask", "never")
KINDS = ("command", "files")
MAX_RULES = 200
MAX_MATCH = 300

# A command an allow covers is one command: none of these in it.
_COMPOUND = re.compile(r"[;&|<>`\n]|\$\(")

# Commands whose second word says what they do: `git status` and `git
# push` are two different things to allow.
TWO_WORDS = frozenset((
    "git", "npm", "pnpm", "yarn", "bun", "cargo", "go", "docker",
    "kubectl", "pip", "pip3", "uv", "poetry", "brew", "apt", "gh",
    "make", "swift", "dotnet", "flutter", "terraform", "rustup", "conda",
))

# Commands that run whatever comes after them: allowed only as written.
RUNNERS = frozenset((
    "python", "python3", "py", "node", "deno", "bash", "sh", "zsh",
    "fish", "pwsh", "powershell", "ruby", "perl", "php", "npx", "uvx",
    "env", "xargs", "time", "nice", "nohup", "watch",
))

# Never offered as an allow from a button: too much rides on them.
NO_ALLOW = frozenset((
    "sudo", "su", "doas", "rm", "rmdir", "del", "dd", "mkfs", "shred",
    "eval", "exec", "ssh", "scp", "kill", "killall", "pkill", "chmod",
    "chown", "shutdown", "reboot", "format", "diskutil",
))

_lock = threading.Lock()


def _path() -> Path:
    # Looked up each time, so a test that moves the home moves it.
    return Path.home() / ".flash" / "rules.json"


def load() -> list[dict]:
    """Every rule, oldest first."""

    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rules = data.get("rules") if isinstance(data, dict) else None
    return [r for r in rules or [] if _valid(r)]


def _valid(rule: object) -> bool:
    return (isinstance(rule, dict) and rule.get("kind") in KINDS
            and rule.get("verdict") in VERDICTS
            and isinstance(rule.get("match"), str)
            and bool(rule["match"].strip()))


def _save(rules: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"rules": rules[-MAX_RULES:]}, indent=2),
                    encoding="utf-8")
    paths.replace(temp, path)


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return command.split()


def _folder(match: str) -> Path:
    return Path(os.path.expanduser(match)).resolve()


def add(kind: str, match: str, verdict: str) -> dict:
    """Keep a rule: VERDICT for steps of KIND that MATCH. One that says
    the same of the same steps takes the place of the old one."""

    if kind not in KINDS:
        raise ValueError(f"A rule is about a command or files, not {kind!r}.")
    if verdict not in VERDICTS:
        raise ValueError("A rule says allow, ask or never.")
    match = " ".join(str(match or "").split())[:MAX_MATCH]
    if not match:
        raise ValueError("Say which command or which folder.")
    if kind == "files":
        match = str(_folder(match))
    elif kind == "command":
        match = " ".join(_tokens(match)) or match
    rule = {"id": uuid.uuid4().hex[:8], "kind": kind, "match": match,
            "verdict": verdict, "made": time.time()}
    with _lock:
        rules = [r for r in load()
                 if not (r["kind"] == kind and r["match"] == match)]
        rules.append(rule)
        _save(rules)
    return rule


def remove(rule_id: str) -> Optional[dict]:
    """Take the rule with RULE_ID (or its number in the list, from 1)
    away. The rule, or None when there is none."""

    with _lock:
        rules = load()
        key = str(rule_id or "").strip()
        index = next((i for i, r in enumerate(rules) if r.get("id") == key),
                     None)
        if index is None and key.isdigit() and 0 < int(key) <= len(rules):
            index = int(key) - 1
        if index is None:
            return None
        gone = rules.pop(index)
        _save(rules)
    return gone


def words(rule: dict) -> str:
    """RULE as the user would say it."""

    match = rule.get("match", "")
    if rule.get("kind") == "files":
        shown = match.replace(str(Path.home()), "~", 1)
        thing = f"changing files in {shown}"
        never = f"Never change files in {shown}"
    else:
        thing = f"running `{match}`"
        never = f"Never run `{match}`"
    return {
        "allow": f"No need to ask before {thing}",
        "ask": f"Always ask before {thing}",
        "never": never,
    }.get(rule.get("verdict", ""), thing)


def listing() -> str:
    """Every rule, numbered, for the terminal and the model alike."""

    rules = load()
    if not rules:
        return "No rules yet."
    return "\n".join(f"{n}. {words(r)}" for n, r in enumerate(rules, 1))


# --- Which rule a step falls under -----------------------------------------


def _parts(command: str) -> list[list[str]]:
    """COMMAND's commands, each as its words: what a never or an ask is
    looked for in, wherever in a chain it stands."""

    return [_tokens(part) for part in re.split(r"&&|\|\||[;|\n]", command)
            if part.strip()]


def _starts(words_: list[str], match: str) -> bool:
    want = _tokens(match)
    return bool(want) and words_[:len(want)] == want


def for_command(command: str) -> Optional[dict]:
    """The rule COMMAND falls under, never before ask before allow, or
    None. An allow counts only for a single plain command that is not
    high stakes."""

    command = str(command or "").strip()
    if not command:
        return None
    rules = [r for r in load() if r["kind"] == "command"]
    parts = _parts(command)
    for verdict in ("never", "ask"):
        for rule in rules:
            if rule["verdict"] == verdict and any(
                    _starts(part, rule["match"]) for part in parts):
                return rule
    if _COMPOUND.search(command) or stakes.why(command):
        return None
    whole = _tokens(command)
    for rule in rules:
        if rule["verdict"] == "allow" and _starts(whole, rule["match"]):
            return rule
    return None


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
    except ValueError:
        return False
    return True


def for_file(path: object) -> Optional[dict]:
    """The rule a change to the file at PATH falls under, never before
    ask before allow, or None. The deepest folder of a verdict wins."""

    try:
        target = Path(os.path.expanduser(str(path))).resolve()
    except (OSError, RuntimeError):
        return None
    rules = [r for r in load() if r["kind"] == "files"
             and _inside(target, Path(r["match"]))]
    for verdict in ("never", "ask", "allow"):
        found = [r for r in rules if r["verdict"] == verdict]
        if found:
            return max(found, key=lambda r: len(r["match"]))
    return None


# --- What an Always allow button would make ---------------------------------


def proposal_for_command(command: str) -> Optional[dict]:
    """The allow rule to offer beside a question about COMMAND, as
    {"kind", "match", "verdict", "words"}: its first word, or two for a
    tool like git, or the whole of it for one that runs other things.
    None for a command no allow could cover."""

    command = str(command or "").strip()
    if not command or _COMPOUND.search(command) or stakes.why(command):
        return None
    tokens = _tokens(command)
    if not tokens:
        return None
    first = Path(tokens[0]).name.lower()
    if first in NO_ALLOW or "=" in tokens[0]:
        return None
    if first in RUNNERS:
        match = tokens[:6]
    elif first in TWO_WORDS and len(tokens) > 1 \
            and not tokens[1].startswith("-"):
        match = tokens[:2]
    else:
        match = tokens[:1]
    rule = {"kind": "command", "match": " ".join(match), "verdict": "allow"}
    return dict(rule, words=words(rule))


def proposal_for_file(path: object) -> Optional[dict]:
    """The allow rule to offer beside a question about changing the file
    at PATH: its folder. None for a folder too wide to hand over, the
    home folder or one above it."""

    try:
        folder = Path(os.path.expanduser(str(path))).resolve().parent
    except (OSError, RuntimeError):
        return None
    home = Path.home().resolve()
    if folder == home or _inside(home, folder):
        return None
    rule = {"kind": "files", "match": str(folder), "verdict": "allow"}
    return dict(rule, words=words(rule))


def keep(proposal: Optional[dict]) -> Optional[dict]:
    """PROPOSAL, as proposal_for_command or proposal_for_file made it,
    kept as a rule. None when there was none."""

    if not proposal:
        return None
    return add(proposal["kind"], proposal["match"], proposal["verdict"])


# --- /rules in the terminal -------------------------------------------------

USAGE = ("/rules lists them. /rules allow|ask|never <command> makes one "
         "about a command, /rules allow|ask|never files <folder> one about "
         "changing files in a folder, /rules remove <number> takes one "
         "away.")


def command_line(rest: str) -> str:
    """What /rules REST says back, having done what it asks."""

    words_ = str(rest or "").split(None, 1)
    if not words_:
        return listing()
    verb = words_[0].lower()
    tail = words_[1].strip() if len(words_) > 1 else ""
    if verb in ("remove", "rm", "delete"):
        gone = remove(tail)
        return f"Removed: {words(gone)}" if gone else \
            f"No rule {tail or 'given'}. " + listing()
    if verb not in VERDICTS or not tail:
        return USAGE
    kind = "command"
    if tail.split(None, 1)[0].lower() == "files":
        kind = "files"
        tail = tail[len("files"):].strip()
        if not tail:
            return USAGE
    try:
        return f"Kept: {words(add(kind, tail, verb))}"
    except ValueError as exc:
        return str(exc)
