"""Recording a meeting: what is said, written down as it is said by the
listening model on this computer, and once it ends, handed to the model
to write up: what it was about, what was decided, who does what.

The page sends the microphone a piece at a time, as voice mode does.
Each stretch of speech the listening model finishes is kept with the
moment it began, so the transcript reads [12:04] ... down the page. It
is saved as it goes, so a Flash that stops mid-meeting loses at most
the last few seconds, and once it ends it is kept for good in
~/.flash/meetings and attached to a new chat, which the model answers
with the write-up.
"""

import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import voice, workspace
from .paths import FLASH_DIR

# The longest a meeting is listened to, and how often it is saved.
LONGEST_SECONDS = 4 * 3600
SAVE_SECONDS = 10.0
# A transcript up to this long goes to the model in the message itself;
# a longer one it reads from its file, in parts.
INLINE_CHARS = 24_000
# The page is sent the last few stretches every time, since the one being
# heard, and the one before it, can still change.
RESENT = 3
MAX_OPEN = 2
# Live notes are brought up to date this often, and only once this many
# new words have been said since the last time.
NOTES_SECONDS = 150.0
NOTES_WORDS = 60
# How much of the meeting so far a turn asked in it is given.
CONTEXT_CHARS = 8_000

# Someone in the meeting talking to Flash: "Flash, put that in a sheet",
# "hey flash can you look up ...". The listening model writes no commas,
# so it is "flash" near the start of a stretch, or after hey or okay,
# with something asked after it, and not a flash drive or a flash sale.
_ADDRESSED = re.compile(
    r"(?:^(?:\S+\s+){0,2}|\b(?:hey|hi|okay|ok|so)\s+)flash\b[\s,.!:]+"
    r"(?!(?:drive|drives|sale|sales|card|cards|light|lights|flood|floods|"
    r"back|backs|point|memory|player|photo|photos|storage|news|mob|fiction|"
    r"bang|forward|forwards|cut|cuts|of|is|was|in|on|and|or)\b)"
    r"(?P<ask>\S.{8,})", re.I)

LIVE_NOTE = """You are in a meeting that is happening now: Flash is \
listening, and what is said is written down as it is said by a speech \
model on this computer, so expect misheard words, and nothing says who \
said what. {who}

Work while they talk, as one of the people in the room would:
- What you make goes in the chat as it is made: a document or a plan in \
a canvas, a whiteboard as an HTML canvas (sticky notes, boxes and arrows \
they can move), numbers in a sheet, things to do in a checklist. Keep \
each one in the same canvas id and update it as the meeting goes, \
rather than making a new one each time.
- Keep the reply itself to a line or two: they are talking, not reading.
- Use what was said; do not make up what was not.

The meeting so far ({length}):

{transcript}"""

ASKED = "Said in the meeting just now, to you: \"{ask}\""

NOTES_ASK = """Bring the meeting's live notes up to date from what has \
been said since you last did: one canvas, id {cid}, Markdown, with what \
it is about so far, decisions, action items (who, by when, if said) and \
open questions. Call canvas with the whole new notes; reply with nothing \
else unless something said needs an answer from you."""

PROMPT = """This is the transcript of a meeting I just recorded, written \
down as it was said by a speech model, so expect misheard words, and \
nothing says who said what. Write it up:

- What it was about and where it ended, in a few sentences.
- Decisions made.
- Action items: what, who if it was said, and by when if it was said.
- Questions left open.

Put the action items in a checklist block, so they can be ticked off. \
If you kept live notes or made anything in a canvas during the meeting, \
bring it up to date with how the meeting ended too.

Keep to what was said. Leave out a part with nothing in it rather than \
make something up."""


class MeetingError(ValueError):
    """A meeting that could not start, or go on, and why."""


def folder() -> Path:
    return FLASH_DIR / "meetings"


def clock(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return (f"{hours}:" if hours else "") + \
        f"{rest // 60:0{2 if hours else 1}d}:{rest % 60:02d}"


class Meeting:
    """One meeting being recorded."""

    def __init__(self, hearing) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.began = datetime.now().astimezone()
        self._hearing = hearing
        self._samples = 0
        # When each stretch of speech began, seconds into the meeting.
        self._stamps: list[float] = []
        self._lock = threading.Lock()
        self._saved = 0.0
        self.ended = False
        # The chat the meeting happens in: asks said aloud go there, and
        # the write-up when it ends.
        self.chat = ""
        # Live notes kept as it goes, and where they were last brought
        # up to date.
        self.live_notes = False
        self._noted_at = time.monotonic()
        self._noted_words = 0
        # The stretches already answered as asks, by their place.
        self._asked: set[int] = set()

    @property
    def seconds(self) -> float:
        return self._samples / voice.SAMPLE_RATE

    def _stamp(self, pieces: list[str], at: Optional[float] = None) -> None:
        """Mark the stretches heard for the first time as begun AT, the
        start of the audio they were heard in."""

        while len(self._stamps) < len(pieces):
            self._stamps.append(self.seconds if at is None else at)

    def hear(self, pcm: bytes) -> None:
        """Hear PCM, 16 kHz mono 16-bit, the next piece of the meeting."""

        with self._lock:
            if self.ended:
                raise MeetingError("That meeting has ended.")
            if self.seconds >= LONGEST_SECONDS:
                raise MeetingError(
                    f"A meeting is recorded for {clock(LONGEST_SECONDS)} at "
                    "most. Stop this one, and start another to go on."
                )
            block = voice.BLOCK_FRAMES * 2
            for start in range(0, len(pcm) - len(pcm) % 2, block):
                piece = pcm[start:start + block]
                began = self.seconds
                self._hearing.feed(piece)
                self._samples += len(piece) // 2
                self._stamp(self._hearing.pieces(), began)
            if time.monotonic() - self._saved >= SAVE_SECONDS:
                self._save(self._hearing.pieces())

    def lines(self, pieces: Optional[list[str]] = None) -> list[dict]:
        """What was said, each stretch with when it began."""

        pieces = self._hearing.pieces() if pieces is None else pieces
        self._stamp(pieces)
        return [
            {"at": round(self._stamps[n], 1), "text": text.strip()}
            for n, text in enumerate(pieces) if text.strip()
        ]

    def since(self, count: int) -> dict:
        """For the page, which has COUNT lines: the ones from where it
        may be out of date on."""

        with self._lock:
            lines = self.lines()
        start = max(0, min(count, len(lines)) - RESENT)
        return {"from": start, "lines": lines[start:],
                "seconds": round(self.seconds, 1)}

    def asks(self) -> list[dict]:
        """Things said to Flash in stretches the listening model has
        finished, each once: [{"at", "ask", "line"}]."""

        with self._lock:
            lines = self.lines()
        found = []
        # The last stretch can still change, so it waits until another
        # begins, or the meeting ends.
        for n, line in enumerate(lines[:-1] if not self.ended else lines):
            if n in self._asked:
                continue
            m = _ADDRESSED.search(line["text"])
            if m:
                self._asked.add(n)
                found.append({"at": line["at"], "ask": m.group("ask").strip(),
                              "line": line["text"]})
        return found

    def words(self) -> int:
        with self._lock:
            lines = self.lines()
        return sum(len(line["text"].split()) for line in lines)

    def notes_due(self) -> bool:
        """Live notes are on, it has been a while, and enough was said."""

        if not self.live_notes or self.ended:
            return False
        words = self.words()
        if (time.monotonic() - self._noted_at < NOTES_SECONDS
                or words - self._noted_words < NOTES_WORDS):
            return False
        self._noted_at = time.monotonic()
        self._noted_words = words
        return True

    def notes_id(self) -> str:
        return f"meeting-notes-{self.id}"

    def context(self, chars: int = CONTEXT_CHARS) -> str:
        """For a turn in the meeting's chat: what it is, and the end of
        what has been said so far."""

        with self._lock:
            lines = self.lines()
        said = "\n".join(f"[{clock(line['at'])}] {line['text']}"
                         for line in lines)
        if len(said) > chars:
            cut = said[-chars:]
            said = "...\n" + cut[cut.find("\n") + 1:]
        who = (f"Live notes are on: they are kept in the canvas with id "
               f"{self.notes_id()}." if self.live_notes else "")
        minutes = round(self.seconds / 60)
        length = f"{minutes} minute{'s' if minutes != 1 else ''} in" \
            if minutes else "under a minute in"
        return LIVE_NOTE.format(who=who, length=length,
                                transcript=said or "(nothing yet)")

    def transcript(self, lines: list[dict]) -> str:
        minutes = round(self.seconds / 60)
        long = (f"{minutes} minute{'s' if minutes != 1 else ''}"
                if minutes else "Under a minute")
        head = (
            f"# Meeting, {self.began:%B} {self.began.day}, "
            f"{self.began:%H:%M}\n\n"
            f"{long}, written down as it was said by the listening model "
            "on this computer.\n\n"
        )
        return head + "\n".join(
            f"[{clock(line['at'])}] {line['text']}" for line in lines
        ) + "\n"

    def _path(self) -> Path:
        return folder() / f"{self.began:%Y-%m-%d %H.%M} {self.id}.md"

    def _save(self, pieces: list[str]) -> None:
        self._saved = time.monotonic()
        try:
            folder().mkdir(parents=True, exist_ok=True)
            self._path().write_text(
                self.transcript(self.lines(pieces)), encoding="utf-8",
            )
        except OSError:
            pass

    def end(self) -> list[dict]:
        """Stop listening. Returns everything said."""

        with self._lock:
            if self.ended:
                return []
            self.ended = True
            pieces = self._hearing.ended()
            self._save(pieces)
            return self.lines(pieces)

    def discard(self) -> None:
        with self._lock:
            if not self.ended:
                self.ended = True
                self._hearing.close()
        self._path().unlink(missing_ok=True)

    def file_name(self) -> str:
        began = self.began
        return f"Meeting {began:%b} {began.day} {began:%H.%M}.md"


class Meetings:
    """The meetings a Flash is recording: one at a time, as a rule."""

    def __init__(self) -> None:
        self._open: dict[str, Meeting] = {}
        self._lock = threading.Lock()

    def start(self) -> Meeting:
        missing = voice.listening_missing()
        if missing:
            raise MeetingError(
                f"Recording a meeting needs {', '.join(missing)}. "
                + voice.install_hint()
            )
        if not voice.listening_installed(voice.vosk_model()):
            raise MeetingError(
                "The listening model is not here yet: download one in "
                "Settings, Voice, or start voice mode once."
            )
        hearing, why = voice._hearing(live=True)
        if hearing is None:
            raise MeetingError(why)
        meeting = Meeting(hearing)
        with self._lock:
            # A page that went away mid-meeting: the oldest is ended, and
            # what it heard is kept in the meetings folder.
            while len(self._open) >= MAX_OPEN:
                oldest = min(self._open.values(), key=lambda m: m.began)
                self._open.pop(oldest.id).end()
            self._open[meeting.id] = meeting
        return meeting

    def in_chat(self, chat_id: str) -> Optional[Meeting]:
        """The meeting happening in the chat CHAT_ID, if one is."""

        with self._lock:
            return next((m for m in self._open.values()
                         if chat_id and m.chat == chat_id), None)

    def get(self, meeting_id: str) -> Meeting:
        with self._lock:
            found = self._open.get(str(meeting_id or ""))
        if found is None:
            raise MeetingError("That meeting is not being recorded.")
        return found

    def stop(self, meeting_id: str) -> tuple[Meeting, list[dict]]:
        meeting = self.get(meeting_id)
        with self._lock:
            self._open.pop(meeting.id, None)
        return meeting, meeting.end()

    def discard(self, meeting_id: str) -> None:
        meeting = self.get(meeting_id)
        with self._lock:
            self._open.pop(meeting.id, None)
        meeting.discard()


def keep(meeting: Meeting, lines: list[dict]) -> dict:
    """Attach the transcript, as a file the model can read, marked as a
    meeting's for the message it goes with. What the page shows of it."""

    if not lines:
        raise MeetingError("Nothing was heard, so there is nothing to keep.")
    return workspace.keep_upload(
        meeting.file_name(), meeting.transcript(lines).encode("utf-8"),
        meeting={"seconds": round(meeting.seconds),
                 "began": meeting.began.isoformat(timespec="minutes")},
    )


def past(query: str = "", limit: int = 20) -> list[dict]:
    """Meetings kept, newest first: each with its file, when, how long
    it ran and its opening words; with QUERY, only those that say all
    its words, and where they say them."""

    try:
        files = sorted(folder().glob("*.md"), reverse=True)
    except OSError:
        files = []
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    found = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        said = [line for line in text.splitlines() if line.startswith("[")]
        lower = text.lower()
        if words and not all(w in lower for w in words):
            continue
        head = text.splitlines()[0].lstrip("# ") if text else path.stem
        length = re.search(r"\n\n(.+?), written down", text)
        item = {"path": str(path), "title": head,
                "length": length.group(1) if length else "",
                "opening": " ".join(line.split("] ", 1)[-1] for line in said[:3])[:200]}
        if words:
            item["matches"] = [line for line in said
                               if any(w in line.lower() for w in words)][:6]
        found.append(item)
        if len(found) >= limit:
            break
    return found


def note(info: dict) -> str:
    """What the model is given of a meeting's transcript: all of it in
    the message when it is short, where to read it when it is long."""

    path = Path(info["path"])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return f"Attached file: {path}"
    if len(text) <= INLINE_CHARS:
        return f"The transcript, kept at {path}:\n\n{text}"
    lines = len(re.findall(r"\n", text))
    return (
        f"The transcript is at {path}, {lines} lines, too long to send at "
        "once. Read all of it with your read tool, a part at a time, "
        "noting what matters as you go, before you write anything."
    )
