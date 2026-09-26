"""Skills: procedures Flash writes down so it does them right next time.

Memory holds facts ("the user deploys with fly"). A skill holds a way
of doing a class of task: the steps, the commands that worked, the
pitfalls that cost time. Each lives in ~/.flash/skills/<name>/SKILL.md:

    ---
    name: release-flow
    description: Cut a Flash release: bump, tag, push, verify
    managed: true
    ---
    1. Bump flash/version.py ...

Only the names and descriptions are sent on every turn. The model reads
a skill in full with skill_view when a task matches it, which keeps
the cost of a large library to a line per skill.

`managed: true` marks a skill Flash's background review wrote. The
review may change only those: a skill the user wrote, or asked for, is
theirs, and a pass nobody is watching does not get to rewrite it.
"""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .paths import FLASH_DIR

SKILL_FILE = "SKILL.md"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

MAX_DESCRIPTION_CHARS = 120
MAX_SKILL_CHARS = 8000

# The list in the system prompt stops here. Past it, the names alone
# would cost a local model a noticeable slice of its window.
MAX_LISTED = 40
MAX_LISTING_CHARS = 3000

ACTIONS = ("create", "patch", "rewrite", "delete")


class SkillError(ValueError):
    """A skill that could not be read or written, and why."""


@dataclass
class Skill:
    name: str
    description: str
    body: str
    managed: bool
    path: Path


def skills_dir() -> Path:
    return FLASH_DIR / "skills"


def _check_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not NAME_RE.match(name):
        raise SkillError(
            f"{name!r} is not a skill name: lowercase letters, digits and "
            "hyphens, naming the kind of task rather than today's "
            "instance of it"
        )
    return name


def _parse(text: str) -> tuple[dict[str, str], str]:
    """The frontmatter fields and the body of a SKILL.md."""

    if not text.startswith("---"):
        return {}, text.strip()

    head, sep, body = text[3:].partition("\n---")
    if not sep:
        return {}, text.strip()

    fields = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            fields[key.strip().lower()] = value.strip()

    return fields, body.strip()


def _render(skill: Skill) -> str:
    lines = [
        "---",
        f"name: {skill.name}",
        f"description: {skill.description}",
    ]
    if skill.managed:
        lines.append("managed: true")
    lines += ["---", "", skill.body.strip(), ""]
    return "\n".join(lines)


def load(path: Path) -> Skill:
    """The skill in folder PATH."""

    try:
        text = (path / SKILL_FILE).read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"could not read {path.name}: {exc}") from exc

    fields, body = _parse(text)

    return Skill(
        name=path.name,
        description=fields.get("description", ""),
        body=body,
        managed=fields.get("managed", "").lower() == "true",
        path=path,
    )


def all_skills() -> list[Skill]:
    """Every readable skill, by name."""

    try:
        folders = sorted(
            p for p in skills_dir().iterdir()
            if (p / SKILL_FILE).is_file()
        )
    except OSError:
        return []

    found = []
    for folder in folders:
        try:
            found.append(load(folder))
        except SkillError:
            continue
    return found


def find(name: str) -> Optional[Skill]:
    try:
        name = _check_name(name)
    except SkillError:
        return None

    folder = skills_dir() / name
    if not (folder / SKILL_FILE).is_file():
        return None

    try:
        return load(folder)
    except SkillError:
        return None


def listing() -> str:
    """The skills block for the system prompt, or "" with none saved."""

    skills = all_skills()

    if not skills:
        return ""

    lines = []
    used = 0

    for skill in skills[:MAX_LISTED]:
        line = f"- {skill.name}: {skill.description or '(no description)'}"
        if used + len(line) > MAX_LISTING_CHARS:
            break
        lines.append(line)
        used += len(line) + 1

    left = len(skills) - len(lines)
    if left:
        lines.append(f"- ...and {left} more, not listed to save room")

    return (
        "=== Skills ===\n"
        "Saved procedures from past work, for this user. When a task "
        "matches one, call skill_view with its name before starting, and "
        "follow it.\n"
        + "\n".join(lines)
    )


def view(name: str, path: str = "") -> str:
    """SKILL.md, or one of the skill's other files."""

    skill = find(name)

    if skill is None:
        known = ", ".join(s.name for s in all_skills()) or "none saved yet"
        raise SkillError(f"No skill called {name!r}. Skills: {known}.")

    if not path:
        head = f"Skill: {skill.name}\n{skill.description}\n\n"
        extras = sorted(
            str(p.relative_to(skill.path))
            for p in skill.path.rglob("*")
            if p.is_file() and p.name != SKILL_FILE
        )
        tail = (
            "\n\nOther files, readable with skill_view path=...: "
            + ", ".join(extras)
            if extras else ""
        )
        return head + skill.body + tail

    base = skill.path.resolve()
    target = (base / path).resolve()

    if base not in target.parents or not target.is_file():
        raise SkillError(f"{skill.name} has no file {path!r}")

    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillError(f"could not read {path}: {exc}") from exc


def _write(skill: Skill) -> None:
    if len(skill.body) > MAX_SKILL_CHARS:
        raise SkillError(
            f"the skill is {len(skill.body)} characters; keep it under "
            f"{MAX_SKILL_CHARS} by cutting narrative and keeping the steps "
            "and pitfalls"
        )

    skill.path.mkdir(parents=True, exist_ok=True)
    (skill.path / SKILL_FILE).write_text(_render(skill), encoding="utf-8")


def _description(value: Optional[str]) -> str:
    text = " ".join((value or "").split())
    if not text:
        raise SkillError("a skill needs a one-line description")
    if len(text) > MAX_DESCRIPTION_CHARS:
        raise SkillError(
            f"the description is {len(text)} characters; the limit is "
            f"{MAX_DESCRIPTION_CHARS}, since it is sent on every turn"
        )
    return text


def manage(
    action: str,
    name: str,
    *,
    description: Optional[str] = None,
    content: Optional[str] = None,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    managed_only: bool = False,
) -> str:
    """Create, patch, rewrite, or delete a skill; say what happened.

    With MANAGED_ONLY, as the background review runs it, a skill the
    user owns cannot be changed or removed.
    """

    action = (action or "").strip().lower()
    if action not in ACTIONS:
        raise SkillError(
            f"unknown action {action!r}; use one of {', '.join(ACTIONS)}"
        )

    name = _check_name(name)
    existing = find(name)

    if action == "create":
        if existing is not None:
            raise SkillError(
                f"{name} already exists; call skill_view on it, then patch "
                "it rather than creating a second one"
            )
        if not (content or "").strip():
            raise SkillError("create needs the skill's content")
        _write(Skill(
            name=name,
            description=_description(description),
            body=content or "",
            managed=managed_only,
            path=skills_dir() / name,
        ))
        return f"Created skill {name}."

    if existing is None:
        raise SkillError(f"No skill called {name!r} to {action}.")

    if managed_only and not existing.managed:
        raise SkillError(
            f"{name} was written by the user, so the background review "
            "leaves it alone"
        )

    if action == "delete":
        shutil.rmtree(existing.path)
        return f"Deleted skill {name}."

    if description is not None and description.strip():
        existing.description = _description(description)

    if action == "rewrite":
        if not (content or "").strip():
            raise SkillError("rewrite needs the skill's new content")
        existing.body = content or ""
        _write(existing)
        return f"Rewrote skill {name}."

    old = old_string or ""
    count = existing.body.count(old) if old else 0

    if count != 1:
        where = "not found" if count == 0 else f"found {count} times"
        raise SkillError(
            f"old_string was {where} in {name}; call skill_view and copy "
            "an exact passage that appears once"
        )

    existing.body = existing.body.replace(old, new_string or "", 1)
    _write(existing)
    return f"Patched skill {name}."


def remove(name: str) -> bool:
    """Delete a skill for /skills remove. False if there was none."""

    skill = find(name)
    if skill is None:
        return False
    shutil.rmtree(skill.path)
    return True
