"""A spark calling the user: how the call rings wherever they are.

A spark's call is an entry in its private chat (teamchat.ring), ringing
for teamchat.RING_SECONDS. Every Flash that can answer it listens for one
(listen): the web server rings each open page, and the computer when no
page is open; the terminal rings in the terminal. Each says it is here
with a beat on disk, so a spark working in the keeper alone, with no
Flash open to answer, rings the desktop itself (rang), and the call is
missed, to call back when Flash is opened.

On the computer a ring is a ringtone through its speakers and a prompt
with Answer and Decline where the desktop can show one (a dialog on
macOS, a notification with actions on Linux and Windows), or a plain
notification where it cannot. Only one Flash rings the desktop for a
call: the first to claim it.
"""

import contextlib
import json
import math
import os
import shutil
import struct
import subprocess  # nosec B404 -- fixed programs, never a shell
import sys
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from . import sparks, teamchat

# How often a Flash that can answer says so, and how long one counts as
# gone without saying it.
BEAT_SECONDS = 5.0
BEAT_STALE = 20.0
# How often the calls rung here are looked at, to hear one ring out.
TICK_SECONDS = 1.0

Ring = Callable[[str, dict], None]

_handlers: list[tuple[Ring, Optional[Ring]]] = []
_rung: dict[str, str] = {}  # call id -> its chat, for the calls rung here
_lock = threading.Lock()
_started = False


def _folder() -> Path:
    return sparks.sparks_dir() / "ringers"


# ---- who can answer ------------------------------------------------------


def _beat_file() -> Path:
    return _folder() / f"{os.getpid()}.json"


def _beat() -> None:
    with contextlib.suppress(OSError):
        _folder().mkdir(parents=True, exist_ok=True)
        _beat_file().write_text(json.dumps({"pid": os.getpid(),
                                            "at": time.time()}))


def ringers_alive() -> bool:
    """Whether a Flash that can answer a call is open now."""

    now = time.time()
    with contextlib.suppress(OSError):
        for path in _folder().glob("*.json"):
            with contextlib.suppress(OSError, ValueError):
                beat = json.loads(path.read_text())
                if now - float(beat.get("at") or 0) < BEAT_STALE and \
                        sparks.alive(beat.get("pid")):
                    return True
    return False


def claim(call_id: str) -> bool:
    """Whether this Flash is the one to ring the desktop for CALL_ID: the
    first to ask is."""

    folder = _folder()
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
        # Old claims go, so the folder does not fill up.
        for old in folder.glob("claim-*"):
            with contextlib.suppress(OSError):
                if time.time() - old.stat().st_mtime > 3600:
                    old.unlink()
    try:
        fd = os.open(folder / f"claim-{call_id}",
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    os.close(fd)
    return True


# ---- hearing calls -------------------------------------------------------


def _ringing(entry: dict) -> bool:
    return entry.get("kind") == teamchat.CALL and \
        entry.get("from") == teamchat.SPARK and \
        teamchat._state(entry) == "ringing" and \
        time.time() - float(entry.get("at") or 0) < teamchat.RING_SECONDS


def _look(team_id: str) -> None:
    """TEAM_ID's chat changed: a call in it that started ringing, or one
    rung here that stopped, is passed on."""

    if not teamchat.is_dm(team_id):
        return
    entries = teamchat._read(team_id)["entries"]
    for entry in entries:
        if entry.get("kind") != teamchat.CALL or \
                entry.get("from") != teamchat.SPARK:
            continue
        call = str(entry.get("call") or "")
        with _lock:
            known = call in _rung
            if _ringing(entry) and not known:
                _rung[call] = team_id
            elif not _ringing(entry) and known:
                _rung.pop(call, None)
            else:
                continue
        for start, stop in list(_handlers):
            with contextlib.suppress(Exception):
                if not known:
                    start(team_id, dict(entry))
                elif stop is not None:
                    stop(team_id, dict(entry))


def _tick() -> None:
    while True:
        _beat()
        for _ in range(int(BEAT_SECONDS / TICK_SECONDS)):
            time.sleep(TICK_SECONDS)
            with _lock:
                rung = dict(_rung)
            # A call rung out with nobody there: marked missed, which
            # tells everyone listening it stopped.
            for team_id in set(rung.values()):
                with contextlib.suppress(Exception):
                    teamchat.open_call(team_id)


def _start_threads() -> None:
    """Hear other Flashes' chats, and say this one is here, from now on."""

    teamchat.watch()
    _beat()
    threading.Thread(target=_tick, daemon=True, name="spark-rings").start()


def listen(start: Ring, stop: Optional[Ring] = None) -> None:
    """Call START with a chat's id and its entry when a spark's call to
    the user starts ringing, and STOP when it stops (answered, declined
    or missed). Says from now on that this Flash can answer."""

    global _started

    _handlers.append((start, stop))
    with _lock:
        if _started:
            return
        _started = True
    teamchat.on_change(_look)
    _start_threads()
    # Calls that started ringing before this Flash was listening.
    with contextlib.suppress(OSError):
        for path in teamchat._folder().glob(teamchat.DM_PREFIX + "*.json"):
            _look(path.stem)


def rang(team_id: str, entry: dict) -> None:
    """A spark just called (teamchat.ring): with no Flash open to answer,
    the computer rings from here."""

    if _handlers or ringers_alive():
        return
    if claim(str(entry.get("call") or "")):
        ring_desktop(team_id, entry)


# ---- ringing the computer ------------------------------------------------


def ringing_now(team_id: str, call_id: str) -> bool:
    """Whether the call CALL_ID is still ringing."""

    entry = teamchat._call_entry(teamchat._read(team_id), call_id)
    return entry is not None and _ringing(entry)


def ring_desktop(team_id: str, entry: dict, answer_url: str = "",
                 on_answer: Optional[Callable[[], None]] = None) -> None:
    """Ring the computer for ENTRY's call, in the background: a ringtone
    and a prompt to answer or decline. Answered, ON_ANSWER is called, or
    ANSWER_URL opened in the browser. Missed, a notification says so."""

    call = str(entry.get("call") or "")
    name = str(entry.get("name") or "A spark")
    reason = str(entry.get("reason") or "")

    def going() -> bool:
        return ringing_now(team_id, call)

    def run() -> None:
        threading.Thread(target=ringtone, args=(going,), daemon=True).start()
        choice = prompt(f"{name} is calling", reason or "A call from a spark",
                        going, answer_url)
        if choice == "answer":
            if on_answer is not None:
                on_answer()
            elif answer_url:
                import webbrowser

                webbrowser.open(answer_url)
        elif choice == "decline":
            with contextlib.suppress(Exception):
                teamchat.call_step(team_id, call, "decline")
        else:
            # Rung out: wait for it to be marked, then say so if missed.
            for _ in range(int(teamchat.RING_SECONDS) + 5):
                if not going():
                    break
                time.sleep(1)
            found = teamchat._call_entry(teamchat._settled(team_id), call)
            if found is not None and teamchat._state(found) == "missed":
                from . import notify

                notify.desktop(f"Missed call from {name}",
                               reason or "Open Flash to call back.")

    threading.Thread(target=run, daemon=True, name="spark-ring").start()


def prompt(title: str, text: str, going: Callable[[], bool],
           answer_url: str = "") -> str:
    """Ask on the desktop whether to answer: "answer", "decline", or ""
    for no answer (it stopped ringing, or this desktop cannot ask)."""

    seconds = int(teamchat.RING_SECONDS)
    if sys.platform == "darwin":
        def quoted(value: str) -> str:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

        args = ["osascript", "-e",
                f"display dialog {quoted(text)} with title {quoted(title)} "
                'buttons {"Decline", "Answer"} default button "Answer" '
                f"giving up after {seconds}"]
        out = _run_while(args, going)
        return "answer" if "Answer" in out else \
            "decline" if "Decline" in out else ""
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        args = ["notify-send", "--app-name=Flash", "--urgency=critical",
                f"--expire-time={seconds * 1000}", "--wait",
                "--action=answer=Answer", "--action=decline=Decline",
                title, text]
        out = _run_while(args, going).strip()
        return out if out in ("answer", "decline") else ""
    if os.name == "nt" and answer_url:
        with contextlib.suppress(Exception):
            from winotify import Notification  # type: ignore

            toast = Notification(app_id="Flash", title=title, msg=text,
                                 launch=answer_url)
            toast.add_actions(label="Answer", launch=answer_url)
            toast.show()
            return ""
    from . import notify

    notify.desktop(title, text)
    return ""


def _run_while(args: list[str], going: Callable[[], bool]) -> str:
    """Run ARGS (a desktop's prompt) until it answers or the call stops
    ringing: what it printed."""

    try:
        proc = subprocess.Popen(  # nosec B603 -- fixed program, text args
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True)
    except OSError:
        return ""
    while proc.poll() is None:
        if not going():
            with contextlib.suppress(OSError):
                proc.terminate()
            return ""
        time.sleep(0.3)
    return proc.stdout.read() if proc.stdout else ""


def _tone_file() -> Path:
    """A ring, made once: two tones together for two seconds, then four
    of quiet, as a phone rings."""

    path = sparks.sparks_dir() / "ringers" / "ring.wav"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 22050
    frames = bytearray()
    for i in range(rate * 6):
        t = i / rate
        if t < 2:
            fade = min(1.0, t / 0.05, (2 - t) / 0.05)
            v = 0.18 * fade * (math.sin(2 * math.pi * 440 * t)
                               + math.sin(2 * math.pi * 480 * t)) / 2
        else:
            v = 0.0
        frames += struct.pack("<h", int(v * 32767))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return path


def _player(path: Path) -> Optional[list[str]]:
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay", str(path)]
    for name in ("paplay", "aplay", "pw-play"):
        if shutil.which(name):
            return [name, str(path)]
    return None


def ringtone(going: Callable[[], bool]) -> None:
    """Ring through the computer's speakers while GOING says so, at most
    as long as a call rings."""

    try:
        path = _tone_file()
    except OSError:
        return
    end = time.time() + teamchat.RING_SECONDS
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME
                               | winsound.SND_ASYNC | winsound.SND_LOOP)
            while going() and time.time() < end:
                time.sleep(0.3)
            winsound.PlaySound(None, 0)
        return
    args = _player(path)
    if args is None:
        return
    while going() and time.time() < end:
        try:
            proc = subprocess.Popen(  # nosec B603 -- fixed program
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return
        while proc.poll() is None:
            if not going() or time.time() >= end:
                with contextlib.suppress(OSError):
                    proc.terminate()
                return
            time.sleep(0.2)
