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
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import computer, voice
from .paths import FLASH_DIR
from .theme import DIM, tool_result

# The devices that carry the call's sound, and what installs them.
TO_CALL = "BlackHole 2ch"      # Flash speaks into it: the call's microphone
FROM_CALL = "BlackHole 16ch"   # the call plays into it: what Flash hears


def to_call() -> str:
    """The device Flash speaks into: CALL_MIC_DEVICE, else TO_CALL."""

    return voice._setting("CALL_MIC_DEVICE", TO_CALL)


def from_call() -> str:
    """The device Flash hears the call on: CALL_SPEAKER_DEVICE, else
    FROM_CALL."""

    return voice._setting("CALL_SPEAKER_DEVICE", FROM_CALL)

SWITCH = "SwitchAudioSource"
INSTALL = "brew install blackhole-2ch blackhole-16ch switchaudio-osx"

# How long a call may last, how long Flash waits for a word before it
# hangs up, and how long a pause ends what someone is saying.
MOST_SECONDS = 10 * 60
QUIET_SECONDS = 40.0
PAUSE_SECONDS = 0.9
# How long Flash waits for the call to be picked up, or to come in.
PICK_UP_SECONDS = 45.0
# The longest one turn of theirs is listened to before Flash answers.
LONGEST_TURN = 30.0
# The model's word for "this call is over".
HANG_UP = "[hang up]"
RATE = voice.SAMPLE_RATE
BLOCK = 0.1  # seconds of sound read at a time
CALLS_DIR = FLASH_DIR / "calls"

# Finds a button by its name in the call's alerts and windows, and
# presses it: "Call" to confirm one Flash places, "Accept" to take one
# that comes in (only when its alert names WANT, given as argv[1]), and
# "End" to hang up. A notification's buttons are no buttons on macOS 13
# and later but actions of the notification ("Name:Call ..."), so those
# are looked through too. Prints what it pressed, or nothing.
_PRESS = r"""
function run(argv) {
  const names = JSON.parse(argv[0]);
  const want = (argv[1] || "").toLowerCase();
  const se = Application("System Events");
  const get = (x, what) => {
    try { return x[what](); } catch (e) { return ""; }
  };
  const actionName = (a) => {
    const raw = String(get(a, "description") || get(a, "name") || "");
    const m = raw.match(/Name:([^\n]*)/);
    return (m ? m[1] : raw).trim();
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
    for (const x of all) {
      let acts;
      try { acts = x.actions(); } catch (e) { continue; }
      for (const a of acts) {
        const name = actionName(a);
        if (names.includes(name)) {
          try { a.perform(); return name; } catch (e) {}
        }
      }
    }
  }
  return "";
}
"""

# Where the call stands: "live" while there is a call to end, and
# "connected" once its timer runs (it is picked up). Prints JSON.
_STATE = r"""
function run() {
  const se = Application("System Events");
  const get = (x, what) => {
    try { return x[what](); } catch (e) { return ""; }
  };
  const ends = ["End", "End Call", "Hang Up"];
  let live = false, connected = false;
  for (const proc of ["NotificationCenter", "FaceTime"]) {
    let all;
    try { all = se.processes.byName(proc).entireContents(); }
    catch (e) { continue; }
    for (const x of all) {
      const role = get(x, "role");
      const name = String(get(x, "name") || get(x, "description") || "");
      if (role === "AXButton" && ends.includes(name)) live = true;
      const shown = String(get(x, "value") || name).trim();
      if (role === "AXStaticText" && /^\d{1,2}:\d{2}(:\d{2})?$/.test(shown))
        connected = true;
      let acts = [];
      try { acts = x.actions(); } catch (e) {}
      for (const a of acts) {
        const raw = String(get(a, "description") || get(a, "name") || "");
        if (ends.some((e) => raw.includes("Name:" + e))) live = true;
      }
    }
  }
  return JSON.stringify({live: live, connected: connected && live});
}
"""

# FaceTime keeps its own microphone and speaker, chosen in its Video
# menu, whatever the Mac's defaults are: the loopback devices are picked
# there too (the microphone's is the first of its name, the speaker's the
# last).
_FACETIME_DEVICES = r"""
function run(argv) {
  const se = Application("System Events");
  let menu;
  try {
    const bar = se.processes.byName("FaceTime").menuBars[0];
    menu = bar.menuBarItems.byName("Video").menus[0];
  } catch (e) { return ""; }
  const items = menu.menuItems();
  const nameOf = (i) => { try { return i.name(); } catch (e) { return ""; } };
  const named = (n) => items.filter((i) => nameOf(i) === n);
  const did = [];
  const mic = named(argv[0]), out = named(argv[1]);
  try { if (mic.length) { mic[0].click(); did.push("mic"); } } catch (e) {}
  try {
    if (out.length) { out[out.length - 1].click(); did.push("out"); }
  } catch (e) {}
  return did.join(",");
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
    # A call the user is already on (Google Meet, Zoom...), joined by
    # Flash through the loopback devices the user picked in it.
    joined: bool = False
    lines: list[tuple[str, str]] = field(default_factory=list)
    ended: str = ""
    # The live transcript, a line at a time as the call goes: set on
    # the first thing said.
    live: Optional[Path] = None

    def transcript(self) -> str:
        return "\n".join(f"{who}: {said}" for who, said in self.lines)

    def say(self, who: str, said: str) -> None:
        """Record one turn: kept, shown on the page (or the terminal) as
        it happens, and added to the call's live transcript file."""

        self.lines.append((who, said))
        line = f"[{time.strftime('%H:%M:%S')}] {who}: {said}"
        tool_result(line, style=DIM)
        try:
            if self.live is None:
                CALLS_DIR.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y-%m-%d %H.%M.%S")
                name = f"{stamp} {self.who or 'call'} live.txt"
                self.live = CALLS_DIR / name
            with self.live.open("a", encoding="utf-8") as out:
                out.write(line + "\n")
        except OSError:
            pass  # the call goes on; the kept transcript still has it


def missing() -> str:
    """What this Mac lacks for Flash to talk on calls, as the command that
    installs it; "" when nothing."""

    if computer.system() != "mac":
        return "Flash can only talk on calls on a Mac."
    devices = _devices()
    lacking = [d for d in (to_call(), from_call()) if d not in devices]
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

    was_in = _switch("input", to_call())
    was_out = _switch("output", from_call())
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
    except computer.ComputerError as exc:
        if "assistive access" in str(exc) or "-25211" in str(exc):
            raise CallError(
                "Flash isn't allowed to press Call: give the app that runs "
                "Flash (Terminal, or whichever you start it from) "
                "Accessibility access in System Settings, Privacy & "
                "Security, Accessibility, then restart it and try again."
            ) from None
        return ""


def _state() -> dict:
    """Where the call stands: {"live": bool, "connected": bool}."""

    try:
        got = json.loads(computer._run(
            ["osascript", "-l", "JavaScript", "-e", _STATE],
            timeout=20).strip() or "{}")
    except (computer.ComputerError, ValueError):
        return {"live": False, "connected": False}
    return {"live": bool(got.get("live")),
            "connected": bool(got.get("connected"))}


def _facetime_devices() -> None:
    with contextlib.suppress(computer.ComputerError):
        computer._run(["osascript", "-l", "JavaScript", "-e",
                       _FACETIME_DEVICES, to_call(), from_call()],
                      timeout=20)


def _picked_up(call: Call, stopping: Callable[[], bool]) -> bool:
    """Waits for the call Flash placed to be picked up. False, with why in
    CALL.ended, when it never was."""

    deadline = time.monotonic() + PICK_UP_SECONDS
    seen_live = False
    while time.monotonic() < deadline:
        if stopping():
            call.ended = "you stopped it"
            return False
        now = _state()
        if now["connected"]:
            return True
        if now["live"]:
            seen_live = True
        elif seen_live:
            call.ended = "they didn't pick up"
            return False
        time.sleep(0.7)
    if seen_live:
        # Still ringing, or a Mac that shows no timer: talk anyway; a
        # quiet line ends it.
        return True
    call.ended = "they didn't pick up"
    return False


class _StillOn:
    """STOPPING, true too once the call has ended on its own (they hung
    up). The call is looked at every EVERY seconds on a thread of its own,
    so listening never waits on it."""

    def __init__(self, stopping: Callable[[], bool], every: float = 4.0,
                 look: Callable[[], dict] = lambda: _state()) -> None:
        self.stopping = stopping
        self.why = ""
        self._over = threading.Event()
        self._thread = threading.Thread(
            target=self._watch, args=(every, look), daemon=True,
            name="call watch")
        self._thread.start()

    def _watch(self, every: float, look: Callable[[], dict]) -> None:
        while not self._over.wait(every):
            if not look()["live"]:
                self.why = "they hung up"
                return

    def __call__(self) -> bool:
        return bool(self.why) or self.stopping()

    def close(self) -> None:
        self._over.set()


def _number_of(who: str, facetime: bool = False) -> tuple[str, str]:
    """WHO's name and phone number: a number as it is, a name from
    Contacts. With FACETIME, an email address does too: FaceTime Audio
    calls one."""

    shown, handle = computer.recipient(who)
    if "@" in handle and not facetime:
        raise CallError(f"{shown} has only an email address in Contacts: "
                        "call them with FaceTime Audio instead.")
    return shown, handle


# How a call is made: through the iPhone, or FaceTime Audio, over the
# internet, which needs no iPhone and reaches an email address too.
PHONE = "phone"
FACETIME = "facetime_audio"


def _dial(handle: str, via: str) -> None:
    scheme = "facetime-audio" if via == FACETIME else "tel"
    computer._run(["open", f"{scheme}:{handle}"])


# --- Sound ----------------------------------------------------------------


class Line:
    """The call's sound, both ways: what the other side says, heard a
    sentence at a time, and Flash's voice into the call."""

    def __init__(self) -> None:
        import sounddevice

        self.sd = sounddevice
        self.device_rate = int(sounddevice.query_devices(
            from_call(), "input")["default_samplerate"])
        self.stream = sounddevice.RawInputStream(
            device=from_call(), channels=1, dtype="int16",
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

        base = voice.silence_threshold()
        # The line's own hiss sets the bar too, so a noisy line is not
        # taken for someone who never stops talking: it is learned from
        # the first moments of the line, and kept up after.
        floor = getattr(self, "floor", None)
        learning: list[float] = []
        heard = bytearray()
        quiet = 0.0
        waited = 0.0
        frames = int(self.device_rate * BLOCK)
        while not stopping():
            block, _ = self.stream.read(frames)
            block = bytes(block)
            level = voice._level(block)
            if floor is None:
                learning.append(level)
                if len(learning) * BLOCK >= 0.5:
                    # Someone talking from the first moment is no hiss.
                    floor = self.floor = min(min(learning), base * 3)
                waited += BLOCK
                continue
            loud = level > max(base, floor * 2.5)
            if not loud:
                floor = self.floor = floor * .9 + level * .1
            if heard:
                heard += block
                quiet = 0.0 if loud else quiet + BLOCK
                if quiet >= PAUSE_SECONDS or \
                        len(heard) >= self.device_rate * 2 * LONGEST_TURN:
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
        out = self.sd.RawOutputStream(device=to_call(), channels=1,
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
    if call.joined:
        opening = (f"{user} brought you into a call they are on, with "
                   f"{them}, to talk to them.")
    elif call.incoming:
        opening = f"You answered a call from {them} for {user}."
    else:
        opening = f"You called {them} for {user}."
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
        call.say(call.who or "Them", first)
    while not stopping():
        if time.monotonic() - started > MOST_SECONDS:
            call.ended = "it ran past ten minutes"
            break
        said = _reply(call, user, ask)
        done = HANG_UP in said.lower()
        said = said.replace(HANG_UP, "").replace(HANG_UP.title(), "").strip()
        if said:
            call.say("Flash", said)
            line.say(said)
        if done:
            call.ended = "the call was done"
            break
        heard = line.hear(stopping=stopping)
        if not heard and stopping():
            break
        if not heard:
            call.ended = "the other side went quiet"
            break
        call.say(call.who or "Them", heard)
    if stopping() and not call.ended:
        call.ended = getattr(stopping, "why", "") or "you stopped it"


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
          stopping: Callable[[], bool] = lambda: False,
          via: str = PHONE) -> Call:
    """Call WHO and talk to them for USER, towards GOAL: on the phone, or
    with FaceTime Audio (VIA)."""

    why = missing()
    if why:
        raise CallError(why)
    shown, number = _number_of(who, facetime=True)
    if "@" in number:
        # Only FaceTime calls an email address.
        via = FACETIME
    call = Call(who=shown, number=number, goal=goal, incoming=False)
    with _routed():
        _dial(number, via)
        try:
            # The Mac asks before it dials, in a notification: Flash says
            # yes.
            deadline = time.monotonic() + 15
            pressed = ""
            while time.monotonic() < deadline and not stopping():
                pressed = _press(["Call"])
                if pressed or _state()["live"]:
                    break
                time.sleep(0.5)
            if not pressed and not _state()["live"]:
                raise CallError(
                    "The Mac asked to confirm the call and Flash couldn't "
                    "press Call. Give Flash Accessibility access (System "
                    "Settings, Privacy & Security, Accessibility) and try "
                    "again.")
            _facetime_devices()
            if not _picked_up(call, stopping):
                return call
            line = Line()
            on = _StillOn(stopping)
            try:
                talk(call, line, user, ask, on)
            finally:
                on.close()
                line.close()
        finally:
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
    # A FaceTime Audio call comes in the same way, Accept and all.
    with contextlib.suppress(computer.ComputerError, CallError):
        shown, number = _number_of(who, facetime=True)
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
        _facetime_devices()
        line = Line()
        on = _StillOn(stopping)
        try:
            talk(call, line, user, ask, on)
        finally:
            on.close()
            line.close()
            _hang_up()
    return call


def join(goal: str, user: str, ask: Callable[[list[dict]], str],
         who: str = "",
         stopping: Callable[[], bool] = lambda: False) -> Call:
    """Talk on a call USER is already on (Google Meet, Zoom...), towards
    GOAL, until told to stop, the call goes quiet, or it runs too long.

    The user sets the call app's microphone to to_call() and its speaker
    to from_call(); Flash opens those devices by name, so the Mac's own
    devices are never switched and there is nothing to put back. Nothing
    is hung up either: the call is the user's.
    """

    why = missing()
    if why:
        raise CallError(why)
    call = Call(who=who or "the people on the call", number="", goal=goal,
                incoming=True, joined=True)
    line = Line()
    try:
        talk(call, line, user, ask, stopping)
    finally:
        line.close()
    return call


def summary(call: Call, path: Path) -> str:
    """What happened on CALL, for the chat."""

    said = call.transcript() or "(nothing was said)"
    if call.joined:
        return (f"Flash left the call with {call.who}: {call.ended}.\n"
                f"{said}\nTranscript kept at {path}.")
    return (f"The call {'from' if call.incoming else 'to'} {call.who} "
            f"ended: {call.ended}.\n{said}\nTranscript kept at {path}.")
