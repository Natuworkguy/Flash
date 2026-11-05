"""Past conversations, searched: the web page's chats and the terminal's,
so Flash can find what was said before ("what did we settle on for the
logo?") and read it back to sum it up.

Only what the user and Flash said to each other is searched and shown,
never a tool's output: what a web page or a file said is not the
conversation, and could carry instructions that are not the user's.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Optional

SNIPPET = 220          # characters around a match
SNIPPETS = 3           # matches shown from one chat
MOST = 10              # chats one search can return
TURN_CHARS = 1500      # one message, read back, at most
READ_CHARS = 14000     # a whole chat, read back, at most

# Words too common to say anything about which chat is meant.
STOP = frozenset("""
a an and are as at be been but by can could did do does for from had has
have he her his how i if in into is it its me my no not of on or our she so
that the their them then there these they this to was we were what when
where which who why will with would you your about just like we've i'm it's
""".split())


@dataclass
class Found:
    id: str
    where: str            # "web" or "terminal"
    title: str
    updated: float
    project: str = ""
    score: float = 0.0
    snippets: list[str] = field(default_factory=list)


def _text(message: dict) -> str:
    """What a message says, as text: its parts joined when it has parts."""

    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(str(part.get("text") or "") for part in content
                           if isinstance(part, dict))
    return " ".join(str(content or "").split())


def turns(messages: list) -> list[tuple[str, str]]:
    """The conversation in MESSAGES: (who, text) for each message the
    user or Flash wrote, tool calls and tool results left out."""

    said = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") not in ("user",
                                                            "assistant"):
            continue
        text = _text(m)
        if text:
            said.append(("You" if m["role"] == "user" else "Flash", text))
    return said


def _chats() -> Iterator[tuple[str, str, str, float, str, list]]:
    """Every kept conversation: (id, where, title, updated, project,
    messages)."""

    from . import sessions, workspace

    for chat in workspace.load_chats():
        yield (str(chat.get("id")), "web", str(chat.get("title") or ""),
               float(chat.get("updated") or 0), str(chat.get("project") or ""),
               chat.get("messages") or [])
    for path in sessions.folder().glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict):
            yield (str(raw.get("id") or path.stem), "terminal",
                   str(raw.get("title") or ""), float(raw.get("updated") or 0),
                   "", raw.get("messages") or [])


def terms(query: str) -> list[str]:
    words = re.findall(r"[\w'-]+", (query or "").lower())
    kept = [w.strip("'-") for w in words if w not in STOP and len(w) > 1]
    return list(dict.fromkeys(w for w in kept if w))


def _snippet(text: str, words: list[str], phrase: str) -> str:
    low = text.lower()
    at = low.find(phrase) if phrase else -1
    if at < 0:
        at = min((low.find(w) for w in words if w in low), default=0)
    start = max(0, at - SNIPPET // 3)
    end = min(len(text), start + SNIPPET)
    cut = text[start:end]
    return ("…" if start else "") + cut + ("…" if end < len(text) else "")


def search(query: str, limit: int = 5, exclude: str = "") -> list[Found]:
    """The kept conversations that best match QUERY, the best first.

    Every word of the query that is in a chat counts for each time it is
    said; the whole query said as one phrase counts for more, a match in
    the title for more still, and a recent chat a little more than an old
    one. A chat with fewer than half the words in it is left out."""

    words = terms(query)
    if not words:
        return []
    phrase = " ".join((query or "").lower().split())
    now = time.time()
    found = []
    for chat_id, where, title, updated, project, messages in _chats():
        if exclude and chat_id == exclude:
            continue
        said = turns(messages)
        title_low = title.lower()
        present = set()
        score = 0.0
        hits = []
        for who, text in said:
            low = text.lower()
            n = sum(low.count(w) for w in words)
            if not n:
                continue
            present.update(w for w in words if w in low)
            bonus = 5 if len(words) > 1 and phrase in low else 0
            score += n + bonus
            hits.append((n + bonus, who, text))
        for w in words:
            if w in title_low:
                present.add(w)
                score += 3
        coverage = len(present) / len(words)
        if not score or coverage < .5:
            continue
        age_days = max(0.0, (now - updated) / 86400)
        score *= (.5 + coverage) * (1 + .3 * math.exp(-age_days / 30))
        hits.sort(key=lambda h: -h[0])
        found.append(Found(
            id=chat_id, where=where, title=title or "Untitled",
            updated=updated, project=project, score=round(score, 2),
            snippets=[f"{who}: {_snippet(text, words, phrase)}"
                      for _, who, text in hits[:SNIPPETS]]))
    found.sort(key=lambda f: -f.score)
    return found[:max(1, min(int(limit or 5), MOST))]


def when(stamp: float, now: Optional[float] = None) -> str:
    """STAMP said for a person: "today", "3 days ago (Oct 30)"."""

    now = now or time.time()
    days = int((now - stamp) // 86400)
    day = time.strftime("%b %d", time.localtime(stamp)).replace(" 0", " ")
    if days <= 0:
        return f"today ({day})"
    if days == 1:
        return f"yesterday ({day})"
    if days < 60:
        return f"{days} days ago ({day})"
    return time.strftime("%b %d, %Y", time.localtime(stamp)).replace(" 0", " ")


def describe(found: list[Found], query: str) -> str:
    if not found:
        return (f"No past conversation mentions {query!r}. Try other words "
                "for it, or fewer of them.")
    lines = [f"{len(found)} past conversation{'s' if len(found) > 1 else ''}"
             f" about {query!r}, the best match first:"]
    for f in found:
        where = "the terminal" if f.where == "terminal" else "the web page"
        lines.append(f"\n[{f.id}] {f.title}: {when(f.updated)}, in {where}"
                     + (f", project {f.project}" if f.project else ""))
        lines += [f"  {s}" for s in f.snippets]
    lines.append("\nCall search_chats with a chat_id to read one of these.")
    return "\n".join(lines)


def _cut(text: str) -> str:
    return text if len(text) <= TURN_CHARS else text[:TURN_CHARS] + "…"


def read(chat_id: str, around: str = "") -> str:
    """Conversation CHAT_ID as text, You and Flash in turn. A long one is
    cut down: to the part about AROUND when it is given, else to its
    start and its end."""

    for found_id, where, title, updated, project, messages in _chats():
        if found_id != chat_id:
            continue
        said = turns(messages)
        if not said:
            return f"[{chat_id}] {title or 'Untitled'} has nothing said in it."
        lines = [f"{who}: {_cut(text)}" for who, text in said]
        head = (f"[{chat_id}] {title or 'Untitled'}: {when(updated)}, "
                f"{len(said)} messages, in "
                f"{'the terminal' if where == 'terminal' else 'the web page'}"
                + (f", project {project}" if project else "") + "\n")
        if sum(len(x) + 1 for x in lines) <= READ_CHARS:
            return head + "\n".join(lines)
        words = terms(around)
        if words:
            # the messages around the one that matches most
            best = max(range(len(lines)), key=lambda i: sum(
                lines[i].lower().count(w) for w in words))
            lo = hi = best
            size = len(lines[best])
            while size < READ_CHARS and (lo > 0 or hi < len(lines) - 1):
                if hi < len(lines) - 1:
                    hi += 1
                    size += len(lines[hi]) + 1
                if lo > 0 and size < READ_CHARS:
                    lo -= 1
                    size += len(lines[lo]) + 1
            kept = lines[lo:hi + 1]
            before = f"… {lo} earlier messages left out …\n" if lo else ""
            return head + before + "\n".join(kept) \
                + (f"\n… {len(lines) - 1 - hi} later messages left out …"
                   if hi < len(lines) - 1 else "")
        # no subject: as much of its start and its end as fits, in turn
        start, end, size, i, j = [], [], 0, 0, len(lines) - 1
        while i <= j:
            if size + len(lines[i]) > READ_CHARS:
                break
            start.append(lines[i])
            size += len(lines[i]) + 1
            i += 1
            if i > j or size + len(lines[j]) > READ_CHARS:
                break
            end.append(lines[j])
            size += len(lines[j]) + 1
            j -= 1
        left = j - i + 1
        return head + "\n".join(start) \
            + (f"\n… {left} messages left out …\n" if left > 0 else "\n") \
            + "\n".join(reversed(end))
    return f"No past conversation {chat_id!r}. search_chats finds their ids."
