"""Phone calls Flash holds itself, on a Mac, in UltraAuto only.

"Hey Flash, call John and tell him I'm running late", or "take the next
call from John and find out what he wants": the call goes through the
iPhone's calling on the Mac (FaceTime), and Flash talks on it in its own
voice, towards the goal it was given, then hangs up and says what was
said.

A Mac lets no app into a call's sound, so two loopback devices carry it:
the call hears Flash through one (BlackHole 2ch, made the Mac's
microphone for the call) and Flash hears the call through the other
(BlackHole 16ch, made the Mac's speaker). Both are free; so is the
command that switches them. The Mac's own sound devices are put back
when the call ends.

On every call Flash says, first thing, that it is an automated assistant
calling for the user: it never passes itself off as them.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess  # nosec B404
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import computer, voice
from .paths import FLASH_DIR

# The devices that carry the call's sound, and what installs them.
TO_CALL = "BlackHole 2ch"      # Flash speaks into it: the call's microphone
FROM_CALL = "BlackHole 16ch"   # the call plays into it: what Flash hears
SWITCH = "SwitchAudioSource"
INSTALL = "brew install blackhole-2ch blackhole-16ch switchaudio-osx"

# How long a call may last, how long Flash waits for a word before it
# hangs up, and how long a pause ends what someone is saying.
MOST_SECONDS = 10 * 60
QUIET_SECONDS = 40.0
PAUSE_SECONDS = 0.9
# How long Flash waits for the call to be picked up, or to come in.
PICK_UP_SECONDS = 45.0
# The model's word for "this call is over".
HANG_UP = "[hang up]"
RATE = voice.SAMPLE_RATE
BLOCK = 0.1  # seconds of sound read at a time
CALLS_DIR = FLASH_DIR / "calls"

# Finds a button by its name in the call's alerts and windows, and
# presses it: "Call" to confirm one Flash places, "Accept" to take one
# that comes in (only when its alert names WANT, given as argv[1]), and
# "End" to hang up. Prints what it pressed, or nothing.
_PRESS = r"""
function run(argv) {
  const names = JSON.parse(argv[0]);
  const want = (argv[1] || "").toLowerCase();
  const se = Application("System Events");
  const get = (x, what) => {
    try { return x[what](); } catch (e) { return ""; }
  };
  for (const proc of ["NotificationCenter", "FaceTime"]) {
    let all;
    try { all = se.processes.byName(proc).entireContents(); }
    catch (e) { continue; }
    if (want) {
      const said = all.filter((x) => get(x, "role") === "AXStaticText")
        .map((x) => String(get(x, "value") || "")).join(" ");
      if (!said.toLowerCase().includes(want)) continue;
    }
    for (const x of all) {
      const name = String(get(x, "name") || get(x, "description") || "");
      if (get(x, "role") === "AXButton" && names.includes(name)) {
        x.click();
        return name;
      }
    }
  }
  return "";
}
"""


class CallError(Exception):
    pass


@dataclass
class Call:
    """One call as it went: who, what for, and what each side said."""

    who: str
    number: str
    goal: str
    incoming: bool
    lines: list[tuple[str, str]] = field(default_factory=list)
    ended: str = ""

    def transcript(self) -> str:
        return "\n".join(f"{who}: {said}" for who, said in self.lines)


def missing() -> str:
    """What this Mac lacks for Flash to talk on calls, as the command that
    installs it; "" when nothing."""

    if computer.system() != "mac":
        return "Flash can only talk on calls on a Mac."
    devices = _devices()
    lacking = [d for d in (TO_CALL, FROM_CALL) if d not in devices]
    if lacking or not shutil.which(SWITCH):
        return ("For Flash to hear a call and speak on it, install the "
                f"free audio loopback devices and switcher: {INSTALL}")
    return ""


def _devices() -> set[str]:
    try:
        import sounddevice
    except ImportError:
        return set()
    try:
        return {str(d["name"]) for d in sounddevice.query_devices()}
    except Exception:  # noqa: BLE001  (no audio system at all)
        return set()


def _switch(kind: str, name: str = "") -> str:
    """The Mac's default KIND (input or output) device, set to NAME when
    given. What it was."""

    was = subprocess.run(  # nosec B603 B607 -- a fixed program
        [SWITCH, "-c", "-t", kind], capture_output=True, text=True,
        timeout=10, check=False).stdout.strip()
    if name:
        subprocess.run(  # nosec B603 B607
            [SWITCH, "-s", name, "-t", kind], capture_output=True,
            timeout=10, check=False)
    return was


@contextlib.contextmanager
def _routed():
    """The call's sound through the loopback devices, and the Mac's own
    devices back afterwards, whatever happened."""

    was_in = _switch("input", TO_CALL)
    was_out = _switch("output", FROM_CALL)
    try:
        yield
    finally:
        if was_in:
            _switch("input", was_in)
        if was_out:
            _switch("output", was_out)


def _press(names: list[str], want: str = "") -> str:
    try:
        return computer._run(["osascript", "-l", "JavaScript", "-e", _PRESS,
                              json.dumps(names), want], timeout=20).strip()
    except computer.ComputerError:
        return ""


def _number_of(who: str) -> tuple[str, str]:
    """WHO's name and phone number: a number as it is, a name from
    Contacts."""

    shown, handle = computer.recipient(who)
    if "@" in handle:
        raise CallError(f"{shown} has only an email address in Contacts, "
                        "and a call needs a phone number.")
    return shown, handle


# --- Sound ----------------------------------------------------------------


class Line:
    """The call's sound, both ways: what the other side says, heard a
    sentence at a time, and Flash's voice into the call."""

    def __init__(self) -> None:
        import sounddevice

        self.sd = sounddevice
        self.device_rate = int(sounddevice.query_devices(
            FROM_CALL, "input")["default_samplerate"])
        self.stream = sounddevice.RawInputStream(
            device=FROM_CALL, channels=1, dtype="int16",
            samplerate=self.device_rate,
            blocksize=int(self.device_rate * BLOCK))
        self.stream.start()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.stream.stop()
            self.stream.close()

    def hear(self, longest: float = QUIET_SECONDS,
             stopping: Callable[[], bool] = lambda: False) -> str:
        """What the other side says next, once they pause: "" if they
        say nothing for LONGEST seconds."""

        threshold = voice.silence_threshold()
        heard = bytearray()
        quiet = 0.0
        waited = 0.0
        frames = int(self.device_rate * BLOCK)
        while not stopping():
            block, _ = self.stream.read(frames)
            block = bytes(block)
            loud = voice._level(block) > threshold
            if heard:
                heard += block
                quiet = 0.0 if loud else quiet + BLOCK
                if quiet >= PAUSE_SECONDS:
                    break
            elif loud:
                heard += block
            else:
                waited += BLOCK
                if waited >= longest:
                    return ""
        if not heard:
            return ""
        pcm = voice._resample(bytes(heard), self.device_rate, RATE)
        text, why = voice.transcribe(pcm)
        if why:
            raise CallError(why)
        return text.strip()

    def say(self, text: str) -> None:
        """TEXT in Flash's voice, into the call."""

        wav, why = voice.synthesize(voice.speakable(text))
        if why:
            raise CallError(why)
        if not wav:
            return
        with wave.open(io.BytesIO(wav)) as read:
            rate = read.getframerate()
            pcm = read.readframes(read.getnframes())
        out = self.sd.RawOutputStream(device=TO_CALL, channels=1,
                                      dtype="int16", samplerate=rate)
        with out:
            out.write(pcm)
            # Played out before the next word is listened for.
            time.sleep(0.15)
        # Its own voice, still on its way back through the call, is not
        # what the other side said.
        with contextlib.suppress(Exception):
            frames = int(self.device_rate * BLOCK)
            while self.stream.read_available >= frames:
                self.stream.read(frames)


# --- Talking ----------------------------------------------------------------


def _instructions(call: Call, user: str) -> str:
    them = call.who or "the other person"
    opening = (
        f"You answered a call from {them} for {user}."
        if call.incoming else f"You called {them} for {user}."
    )
    return (
        f"You are Flash, an AI assistant, on a phone call. {opening} "
        f"What {user} wants from it: "
        f"{call.goal or 'find out what they want'}.\n"
        "- First, say plainly that you are an automated assistant, Flash, "
        f"calling (or answering) for {user}. Never claim to be {user} or a "
        "person.\n"
        "- Talk as people do on the phone: one or two short sentences, "
        "then let them answer. No lists, no markdown, nothing to read.\n"
        f"- Agree to nothing {user} did not ask for: no payments, no "
        "personal details, no promises. Say you will pass it on.\n"
        f"- Once the goal is done, or they say goodbye, say a short goodbye "
        f"and end with {HANG_UP}."
    )


def _reply(call: Call, user: str, ask: Callable[[list[dict]], str]) -> str:
    messages = [{"role": "system", "content": _instructions(call, user)}]
    for who, said in call.lines:
        messages.append({"role": "assistant" if who == "Flash" else "user",
                         "content": said})
    if not call.lines or call.lines[-1][0] == "Flash":
        messages.append({"role": "user", "content": "(silence on the line)"})
    return ask(messages).strip()


def talk(call: Call, line: Line, user: str, ask: Callable[[list[dict]], str],
         stopping: Callable[[], bool] = lambda: False) -> None:
    """The conversation: Flash and the other side in turn, until the goal
    is met, they go quiet, or it runs too long."""

    started = time.monotonic()
    if call.incoming:
        first = ""
    else:
        # Picked up: they say hello, or Flash speaks up after a moment.
        first = line.hear(longest=6.0, stopping=stopping)
    if first:
        call.lines.append((call.who or "Them", first))
    while not stopping():
        if time.monotonic() - started > MOST_SECONDS:
            call.ended = "it ran past ten minutes"
            break
        said = _reply(call, user, ask)
        done = HANG_UP in said.lower()
        said = said.replace(HANG_UP, "").replace(HANG_UP.title(), "").strip()
        if said:
            call.lines.append(("Flash", said))
            line.say(said)
        if done:
            call.ended = "the call was done"
            break
        heard = line.hear(stopping=stopping)
        if not heard:
            call.ended = "the other side went quiet"
            break
        call.lines.append((call.who or "Them", heard))
    if stopping() and not call.ended:
        call.ended = "you stopped it"


def _keep(call: Call) -> Path:
    """The call's transcript, kept: what Flash says it did is checkable."""

    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H.%M")
    path = CALLS_DIR / f"{stamp} {'from' if call.incoming else 'to'} " \
        f"{call.who or call.number}.md"
    path.write_text(
        f"# Call {'from' if call.incoming else 'to'} {call.who} "
        f"({call.number})\n\nGoal: {call.goal}\n\n{call.transcript()}\n\n"
        f"Ended: {call.ended}\n", encoding="utf-8")
    return path


def _hang_up() -> None:
    if not _press(["End", "End Call", "Hang Up"]):
        with contextlib.suppress(computer.ComputerError):
            computer._run(["osascript", "-e",
                           'tell application "FaceTime" to quit'])


def place(who: str, goal: str, user: str,
          ask: Callable[[list[dict]], str],
          stopping: Callable[[], bool] = lambda: False) -> Call:
    """Call WHO and talk to them for USER, towards GOAL."""

    why = missing()
    if why:
        raise CallError(why)
    shown, number = _number_of(who)
    call = Call(who=shown, number=number, goal=goal, incoming=False)
    with _routed():
        computer._run(["open", f"tel:{number}"])
        # The Mac asks before it dials: Flash says yes.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not _press(["Call"]):
            time.sleep(0.5)
        line = Line()
        try:
            talk(call, line, user, ask, stopping)
        finally:
            line.close()
            _hang_up()
    return call


def take_next(who: str, goal: str, user: str,
              ask: Callable[[list[dict]], str], minutes: float = 30,
              stopping: Callable[[], bool] = lambda: False) -> Optional[Call]:
    """Wait up to MINUTES for a call from WHO, answer it, and talk for
    USER, towards GOAL. None if none came."""

    why = missing()
    if why:
        raise CallError(why)
    shown = who
    number = ""
    with contextlib.suppress(computer.ComputerError):
        shown, number = _number_of(who)
    want = shown.split(" (")[0]
    deadline = time.monotonic() + max(1.0, minutes) * 60
    while time.monotonic() < deadline and not stopping():
        if _press(["Accept", "Answer"], want=want):
            break
        time.sleep(1.0)
    else:
        return None
    call = Call(who=shown, number=number, goal=goal, incoming=True)
    with _routed():
        time.sleep(1.0)
        line = Line()
        try:
            talk(call, line, user, ask, stopping)
        finally:
            line.close()
            _hang_up()
    return call


def summary(call: Call, path: Path) -> str:
    """What happened on CALL, for the chat."""

    said = call.transcript() or "(nothing was said)"
    return (f"The call {'from' if call.incoming else 'to'} {call.who} "
            f"ended: {call.ended}.\n{said}\nTranscript kept at {path}.")

