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

PROMPT = """This is the transcript of a meeting I just recorded, written \
down as it was said by a speech model, so expect misheard words, and \
nothing says who said what. Write it up:

- What it was about and where it ended, in a few sentences.
- Decisions made.
- Action items: what, who if it was said, and by when if it was said.
- Questions left open.

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
