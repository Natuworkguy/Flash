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

`by:` says who wrote it, for the web UI's label: `flash` for one the
model made with skill_manage, `you` for one made in the page. A skill
from before the field existed has none, unless the review wrote it.

The knowledge base is guides that come with Flash, in flash/knowledge,
and with extensions (a "knowledge" folder in their manifest): how to do
each kind of thing well with Flash's tools. They are read the same way
and listed the same way, and cannot be changed: a skill the user saves
under a guide's name is used instead of it.
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

# Who can have written a skill: the model, or the user in the web UI.
AUTHORS = ("flash", "you")


class SkillError(ValueError):
    """A skill that could not be read or written, and why."""


@dataclass
class Skill:
    name: str
    description: str
    body: str
    managed: bool
    path: Path
    by: str = ""
    # Where a guide in the knowledge base comes from: "flash", or the
    # extension's name. "" for the user's own skills.
    builtin: str = ""


def skills_dir() -> Path:
    return FLASH_DIR / "skills"


KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


def _guides(folder: Path, source: str, prefix: str = "") -> list[Skill]:
    """The guides in FOLDER, each a <name>.md with a description."""

    found = []
    try:
        files = sorted(folder.glob("*.md"))
    except OSError:
        return []
    for file in files:
        try:
            fields, body = _parse(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        name = (fields.get("name") or file.stem).strip().lower()
        if prefix and not name.startswith(prefix + "-"):
            name = f"{prefix}-{name}"
        if not NAME_RE.match(name) or not body:
            continue
        found.append(Skill(name=name, description=fields.get(
            "description", ""), body=body[:MAX_SKILL_CHARS], managed=False,
            path=file, builtin=source))
    return found


def knowledge() -> list[Skill]:
    """The knowledge base: Flash's own guides, then each extension's,
    by name, the first to take a name keeping it."""

    from . import extensions  # deferred: extensions imports a lot

    found = _guides(KNOWLEDGE_DIR, "flash")
    for extension in extensions.installed():
        if extension.knowledge is not None:
            found += _guides(extension.knowledge, extension.name,
                             extension.name)
    taken: set[str] = set()
    kept = []
    for guide in found:
        if guide.name not in taken:
            taken.add(guide.name)
            kept.append(guide)
    return kept


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
    if skill.by:
        lines.append(f"by: {skill.by}")
    lines += ["---", "", skill.body.strip(), ""]
    return "\n".join(lines)


def load(path: Path) -> Skill:
    """The skill in folder PATH."""

    try:
        text = (path / SKILL_FILE).read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"could not read {path.name}: {exc}") from exc

    fields, body = _parse(text)
    managed = fields.get("managed", "").lower() == "true"
    by = fields.get("by", "").lower()

    return Skill(
        name=path.name,
        description=fields.get("description", ""),
        body=body,
        managed=managed,
        path=path,
        by=by if by in AUTHORS else ("flash" if managed else ""),
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


def find(name: str, guides: bool = True) -> Optional[Skill]:
    """The user's skill called NAME, else (with GUIDES) the knowledge
    base's guide."""

    try:
        name = _check_name(name)
    except SkillError:
        return None

    folder = skills_dir() / name
    if (folder / SKILL_FILE).is_file():
        try:
            return load(folder)
        except SkillError:
            pass

    if guides:
        return next((g for g in knowledge() if g.name == name), None)
    return None


def knowledge_listing() -> str:
    """The knowledge base's block for the system prompt: its guides'
    names and what each is for, the user's skills of the same name
    standing in for theirs."""

    own = {s.name for s in all_skills()}
    guides = [g for g in knowledge() if g.name not in own]
    if not guides:
        return ""
    lines = [f"- {g.name}: {g.description}"
             + (f" (from {g.builtin})" if g.builtin != "flash" else "")
             for g in guides]
    return (
        "=== Knowledge base ===\n"
        "Guides to doing things well with your tools. Before a task one "
        "covers, call skill_view with its name and follow it; read "
        "several when a task spans them.\n" + "\n".join(lines)
    )


def listing() -> str:
    """The skills block for the system prompt, with the knowledge base
    before it; "" with neither."""

    guides = knowledge_listing()
    own = _own_listing()
    return "\n\n".join(part for part in (guides, own) if part)


def _own_listing() -> str:
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
        known = ", ".join(s.name for s in all_skills() + knowledge()) \
            or "none saved yet"
        raise SkillError(f"No skill called {name!r}. Skills: {known}.")

    if skill.builtin:
        if path:
            raise SkillError(f"{skill.name} is a guide, with no other files")
        source = "Flash" if skill.builtin == "flash" else \
            f"the {skill.builtin} extension"
        return (f"Guide: {skill.name} (from {source})\n"
                f"{skill.description}\n\n{skill.body}")

    if not path:
        head = f"Skill: {skill.name}\n{skill.description}\n\n"
        extras = sorted(
            p.relative_to(skill.path).as_posix()
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
    by: str = "",
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
    existing = find(name, guides=False)
    if existing is None and action != "create" and find(name) is not None:
        raise SkillError(
            f"{name} is a guide in the knowledge base, which cannot be "
            "changed: create a skill of the same name to use instead of it")

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
            by=by if by in AUTHORS else "",
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

    skill = find(name, guides=False)
    if skill is None:
        return False
    shutil.rmtree(skill.path)
    return True
