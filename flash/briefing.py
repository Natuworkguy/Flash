"""The briefing: what came in while you were away, as a feed of cards.

Each card is one short thing Flash knows about: a spark's report, a wait
that finished, an unread email or iMessage, a chat that answered that
you have not read, a wait coming up soon, something new in a connector,
a spark's @user, or a notification Flash sent you. The page shows them one
at a time, full screen, newest first. A card that is dismissed stays gone.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Iterable
from typing import Any, Optional

from . import workspace

# How far back the feed looks, and how far ahead for what is coming up.
DAY = 24 * 60 * 60
LOOK_BACK = DAY
LOOK_AHEAD = DAY
# How much of one card's words it keeps, and how many of each kind.
BODY_CHARS = 600
TITLE_CHARS = 120
MOST_EACH = 8
MOST_CARDS = 40
# Dismissals older than this are let go: their cards are long out of the
# feed by then.
FORGET_AFTER = 14 * DAY
# What connectors said that was new, for the feed: kept this long.
NEWS_KEPT = 30
# Pings and notifications, kept on disk so a keeper's reach an open
# Flash: this many, newest last.
NOTICES = "briefing-notices.json"
NOTICES_KEPT = 50

DISMISSED = "briefing.json"
# When the briefing was last opened, and when the one before that was:
# a team says what changed since then.
SEEN = "briefing-seen.json"
# The furthest back a team's news goes, however long since the last one.
MOST_SINCE = 7 * DAY
STATUS_CHARS = 160

_lock = threading.Lock()
_news: list[dict] = []

KINDS = {
    "spark": "Spark report",
    "wait": "Wait finished",
    "email": "Email",
    "imessage": "iMessage",
    "chat": "Chat",
    "reminder": "Coming up",
    "news": "News",
    "team": "Team",
    "ping": "Mention",
    "notify": "Notification",
}


def _clip(text: str, most: int, one_line: bool = False) -> str:
    text = str(text or "").strip()
    if one_line:
        text = " ".join(text.split())
    return text if len(text) <= most else text[:most - 1].rstrip() + "…"


def _card(kind: str, key: str, title: str, body: str, at: float,
          **extra: Any) -> dict:
    return {
        "id": f"{kind}:{key}",
        "kind": kind,
        "label": KINDS[kind],
        "title": _clip(title, TITLE_CHARS, True) or KINDS[kind],
        "body": _clip(body, BODY_CHARS),
        "at": float(at or 0.0),
        **extra,
    }


# --- Dismissals ----------------------------------------------------------


def dismissed() -> dict[str, float]:
    """Dismissed cards, by id, with when."""

    found = workspace._read(DISMISSED, {})
    if not isinstance(found, dict):
        return {}
    return {k: float(v) for k, v in found.items()
            if isinstance(v, (int, float))}


def dismiss(card_ids: Iterable[str], now: Optional[float] = None) -> int:
    """Take these cards out of the feed for good. How many there are."""

    now = time.time() if now is None else now
    with _lock:
        kept = {k: v for k, v in dismissed().items()
                if now - v < FORGET_AFTER}
        for card_id in card_ids:
            if card_id:
                kept[str(card_id)[:200]] = now
        workspace._write(DISMISSED, kept)
    stale()
    return len(kept)


def undismiss(card_id: str) -> None:
    with _lock:
        kept = dismissed()
        if kept.pop(card_id, None) is not None:
            workspace._write(DISMISSED, kept)


# --- Since the last briefing --------------------------------------------


def _seen() -> dict:
    found = workspace._read(SEEN, {})
    return found if isinstance(found, dict) else {}


def opened(now: Optional[float] = None) -> float:
    """The briefing was opened: when the last one was, for what changed
    since, kept for refreshing this one."""

    now = time.time() if now is None else now
    with _lock:
        last = float(_seen().get("at") or 0)
        since = max(last or now - LOOK_BACK, now - MOST_SINCE)
        workspace._write(SEEN, {**_seen(), "at": now, "since": since})
    return since


def mark_seen(card_ids: Iterable[str]) -> None:
    """The cards the briefing just showed: not new any more."""

    with _lock:
        seen = _seen()
        seen["ids"] = sorted({str(i) for i in card_ids})[:500]
        workspace._write(SEEN, seen)
    _new["value"] = False
    _new["at"] = time.time()


def has_new(session: Any = None) -> bool:
    """Whether a card is in the feed that the last briefing did not show."""

    shown = set(_seen().get("ids") or [])
    return any(c["id"] not in shown for c in cards(session))


# What has_new last said, and when: looked at again, on a thread of its
# own, once it is older than NEW_EVERY, so asking never waits on email.
NEW_EVERY = 120
_new: dict = {"value": False, "at": 0.0, "running": False}


def new_waiting(session: Any = None,
                changed: Optional[Any] = None) -> bool:
    """has_new, as last found; CHANGED is called when a look in the
    background finds it different."""

    now = time.time()
    if now - _new["at"] >= NEW_EVERY and not _new["running"]:
        _new["running"] = True

        def look() -> None:
            try:
                found = has_new(session)
            except Exception:  # noqa: BLE001  (a source down: no dot)
                found = _new["value"]
            was = _new["value"]
            _new.update(value=found, at=time.time(), running=False)
            if found != was and changed is not None:
                changed()

        threading.Thread(target=look, daemon=True).start()
    return bool(_new["value"])


def stale() -> None:
    """Something was read or dismissed: the dot is looked at again next
    time it is asked, not once NEW_EVERY is up."""

    _new["at"] = 0.0


def last_since(now: Optional[float] = None) -> float:
    """What the briefing open now counts from: the one before it."""

    now = time.time() if now is None else now
    since = float(_seen().get("since") or 0)
    return max(since or now - LOOK_BACK, now - MOST_SINCE)


# --- Connector news ------------------------------------------------------


def note(source: str, text: str, now: Optional[float] = None) -> None:
    """Something new a connector said (triggers.Watcher): a card for the
    feed, besides waking whatever spark was waiting for it."""

    text = str(text or "").strip()
    if not text:
        return
    now = time.time() if now is None else now
    key = hashlib.sha256(f"{source}\n{text}".encode()).hexdigest()[:16]
    with _lock:
        if any(n["key"] == key for n in _news):
            return
        _news.append({"key": key, "source": source, "text": text, "at": now})
        del _news[:-NEWS_KEPT]


def notice(kind: str, title: str, body: str, now: Optional[float] = None,
           **extra: Any) -> None:
    """A ping or a notification ("ping" or "notify"), for the feed: one
    the user may have missed when it came."""

    now = time.time() if now is None else now
    item = {"kind": kind, "title": str(title or ""), "body": str(body or ""),
            "at": now, **extra}
    item["key"] = hashlib.sha256(
        f"{kind}\n{title}\n{body}\n{now}".encode()).hexdigest()[:16]
    with _lock:
        found = workspace._read(NOTICES, [])
        kept = [n for n in found if isinstance(n, dict)] \
            if isinstance(found, list) else []
        kept = [n for n in kept if now - float(n.get("at") or 0) < LOOK_BACK]
        workspace._write(NOTICES, (kept + [item])[-NOTICES_KEPT:])


def _notice_cards(now: float) -> list[dict]:
    found = workspace._read(NOTICES, [])
    cards = []
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        at = float(item.get("at") or 0)
        if now - at > LOOK_BACK:
            continue
        extra = {k: v for k, v in item.items()
                 if k not in ("kind", "title", "body", "at", "key")}
        cards.append(_card(item["kind"], str(item.get("key") or ""),
                           item.get("title") or "", item.get("body") or "",
                           at, **extra))
    return cards


def _news_cards(now: float) -> list[dict]:
    with _lock:
        news = list(_news)
    cards = []
    for item in news:
        if now - item["at"] > LOOK_BACK:
            continue
        lines = item["text"].splitlines()
        head = lines[0].rstrip(":") if lines else item["source"]
        head = re.sub(r"\s*\(from its .* tool\)$", "", head)
        body = "\n".join(lines[1:]) or item["text"]
        cards.append(_card("news", item["key"], head, body, item["at"],
                           source=item["source"]))
    return cards


# --- Sources -------------------------------------------------------------


def _spark_cards(now: float, told: set[str]) -> list[dict]:
    from . import sparks

    cards = []
    for spark in sparks.all_sparks():
        if spark.id in told:
            continue
        for report in spark.reports:
            if report.read or report.quiet or now - report.at > LOOK_BACK:
                continue
            title = spark.name + (" needs you" if report.approval
                                  else " could not finish" if report.failed
                                  else "")
            cards.append(_card(
                "spark", f"{spark.id}:{int(report.at)}", title, report.text,
                report.at, spark=spark.id, who=spark.name,
                colour=spark.colour, failed=report.failed,
                approval=report.approval,
            ))
    return cards


def _first(text: str) -> str:
    """A report's first line, as a status: plain, and short."""

    line = next((ln for ln in _plain(text).splitlines() if ln.strip()), "")
    return _clip(line, STATUS_CHARS, True)


def _team_cards(since: float, told: set[str]) -> list[dict]:
    """A card for each team where something changed since SINCE: what each
    spark on it has to say for itself (its newest report, or what it is
    doing now), and how much the team chat said. The sparks it speaks for
    go in TOLD, so their reports are not cards of their own as well."""

    from . import sparks, teamchat

    cards = []
    for team in sparks.teams():
        members = sparks.members(team.id)
        if not members:
            continue
        lines, latest, speaking = [], 0.0, []
        for spark in members:
            new = [r for r in spark.reports if r.at > since and not r.quiet]
            if new:
                report = max(new, key=lambda r: r.at)
                mark = (" (needs you)" if report.approval
                        else " (could not finish)" if report.failed else "")
                lines.append(f"{spark.name}{mark}: {_first(report.text)}")
                latest = max(latest, report.at)
                speaking.append(spark.id)
            elif spark.status == sparks.WORKING:
                doing = _clip(spark.activity, STATUS_CHARS, True)
                lines.append(f"{spark.name}: working"
                             + (f", {doing[0].lower()}{doing[1:]}"
                                if doing else ""))
        said = [
            e for e in teamchat._live(teamchat._read(team.id)["entries"])
            if e.get("kind") == teamchat.SPARK and e.get("at", 0) > since
        ]
        if said:
            latest = max(latest, max(e.get("at", 0) for e in said))
            lines.append(f"{len(said)} new message"
                         f"{'s' if len(said) != 1 else ''} in the team chat")
        if not latest:
            continue
        quiet = len(members) - len(speaking)
        if quiet and speaking:
            lines.append(f"{quiet} other{'s' if quiet != 1 else ''}: "
                         "nothing new")
        told.update(speaking)
        cards.append(_card(
            "team", f"{team.id}:{int(latest)}", team.name, "\n".join(lines),
            latest, team=team.id, colour=team.colour,
            who=f"{len(speaking)} of {len(members)} with news",
        ))
    return cards


def _wait_cards(now: float, titles: dict[str, str]) -> list[dict]:
    from . import waits

    with waits._lock:
        listed = list(waits._load())
    cards = []
    for wait in listed:
        if wait.status in (waits.DONE, waits.GAVE_UP) and wait.ended and \
                now - wait.ended <= LOOK_BACK:
            gave_up = wait.status == waits.GAVE_UP
            cards.append(_card(
                "wait", wait.id,
                ("Stopped waiting for " if gave_up else "Done waiting for ")
                + wait.what,
                wait.said or wait.then, wait.ended, chat=wait.chat,
                chat_title=titles.get(wait.chat, ""), gave_up=gave_up,
            ))
        elif wait.status == waits.WAITING and wait.at and \
                now <= wait.at <= now + LOOK_AHEAD:
            cards.append(_card(
                "reminder", wait.id, wait.what, wait.then, wait.at,
                chat=wait.chat, chat_title=titles.get(wait.chat, ""),
                due=wait.at,
            ))
    return cards


def _email_cards() -> list[dict]:
    from . import mail

    if not mail.configured():
        return []
    cards = []
    for message in mail.inbox(unread=True, limit=MOST_EACH):
        sender = re.sub(r"\s*<[^>]*>", "", message.get("from") or "")
        cards.append(_card(
            "email", f"{message.get('account', '')}:{message.get('uid')}",
            message.get("subject") or "(no subject)",
            message.get("snippet") or "", message.get("at") or 0.0,
            who=sender.strip('" ') or message.get("from") or "",
            uid=message.get("uid"), account=message.get("account", ""),
            link=message.get("link", ""),
        ))
    return cards


_IMESSAGE_LINE = re.compile(
    r"^(?P<stamp>.+?) · (?P<who>.+?)(?: in (?P<place>.+?))?"
    r" \(unread\): (?P<words>.*)$")


def _imessage_cards(now: float) -> list[dict]:
    from . import computer

    if not computer.allowed("imessage_read"):
        return []
    said = computer.read_imessages(unread_only=True, limit=MOST_EACH)
    # Mark read, where Flash may: a conversation with one person, as
    # Messages cannot be told to open a group's.
    can_mark = computer.allowed("imessage_mark")
    cards = []
    for line in said.splitlines():
        found = _IMESSAGE_LINE.match(line.strip())
        if not found:
            continue
        key = hashlib.sha256(line.encode()).hexdigest()[:16]
        who = found["who"]
        place = found["place"] or ""
        cards.append(_card(
            "imessage", key, who + (f" in {place}" if place else ""),
            found["words"], _stamp(found["stamp"], now), who=who,
            place=place, markable=can_mark and not place,
        ))
    return cards


def _stamp(stamp: str, now: float) -> float:
    """When an iMessage line's "Fri Oct 23, 01:50" was, this year."""

    year = time.localtime(now).tm_year
    try:
        at = time.mktime(time.strptime(f"{year} {stamp}",
                                       "%Y %a %b %d, %H:%M"))
    except ValueError:
        return now
    # Late December's messages, read in January.
    return at if at <= now + DAY else at - 365 * DAY


def _chat_cards(session: Any, now: float) -> list[dict]:
    cards = []
    for chat in list(getattr(session, "chats", {}).values()):
        if chat.busy or now - chat.updated > LOOK_BACK:
            continue
        at = next((n for n in range(len(chat.log) - 1, -1, -1)
                   if chat.log[n].get("type") in ("assistant", "user")), -1)
        last = chat.log[at] if at >= 0 else None
        if not last or last.get("type") != "assistant" or \
                not str(last.get("text") or "").strip():
            continue
        # A reply the user has had open on the page is not news.
        if at < getattr(chat, "seen", 0):
            continue
        cards.append(_card(
            "chat", f"{chat.id}:{int(chat.updated)}", chat.title,
            _plain(last["text"]), chat.updated, chat=chat.id,
            spark=chat.spark,
        ))
    return cards


def _plain(text: str) -> str:
    """A reply's words without the Markdown that would show as marks."""

    text = re.sub(r"```.*?```", " [code] ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|`)", "", text)
    return text.strip()


# --- The feed ------------------------------------------------------------


def cards(session: Any = None, now: Optional[float] = None,
          problems: Optional[list[str]] = None,
          since: Optional[float] = None) -> list[dict]:
    """Every card not dismissed, newest first; what was coming up goes
    first of all. Teams say what changed since SINCE (the briefing before
    this one). A source that fails is left out, and named in PROBLEMS."""

    now = time.time() if now is None else now
    since = last_since(now) if since is None else since
    told: set[str] = set()
    titles = {c.id: c.title
              for c in getattr(session, "chats", {}).values()} \
        if session is not None else {}
    sources = (
        ("teams", lambda: _team_cards(since, told)),
        ("sparks", lambda: _spark_cards(now, told)),
        ("waits", lambda: _wait_cards(now, titles)),
        ("email", _email_cards),
        ("iMessage", lambda: _imessage_cards(now)),
        ("chats", lambda: _chat_cards(session, now) if session else []),
        ("connectors", lambda: _news_cards(now)),
        ("pings and notifications", lambda: _notice_cards(now)),
    )
    found: list[dict] = []
    for name, source in sources:
        try:
            made = source()
        except Exception as error:  # noqa: BLE001  (one source, not all)
            if problems is not None:
                problems.append(f"{name}: {error}")
            continue
        by_kind: dict[str, int] = {}
        for card in sorted(made, key=lambda c: -c["at"]):
            by_kind[card["kind"]] = by_kind.get(card["kind"], 0) + 1
            if by_kind[card["kind"]] <= MOST_EACH:
                found.append(card)
    gone = dismissed()
    found = [c for c in found if c["id"] not in gone]
    # Coming up, soonest first; then the rest, newest first.
    found.sort(key=lambda c: (0, c["at"]) if c["kind"] == "reminder"
               else (1, -c["at"]))
    return found[:MOST_CARDS]
