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

By default every call opens with a fixed greeting, spoken in code, saying
it is an automated assistant calling for the user (first name only). The
user can set their own greeting instead, or let Flash write one; the
model is always told never to pass itself off as them.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import os
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
from .paths import ENV_PATH, FLASH_DIR
from .theme import DIM, tool_call, tool_result

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


# The Mac's own speakers from before a call took them over, so the user
# can still hear it: set while the call's sound is routed.
_speakers = ""

# The call going now, for Stop and the page's mic button: who started it
# ("" for no one known), its line once it has one, and the Mac's own
# devices ("input", "output") from before the call switched them.
_lock = threading.Lock()
_owner: Optional[str] = None
_line: Optional[Line] = None
_was: dict[str, str] = {}


def monitor() -> str:
    """The device the user hears the call on, both sides: "" when
    CALL_MONITOR is off, or when it would be a device Flash listens on or
    speaks into, which would feed the call back into itself."""

    if voice._setting("CALL_MONITOR", "on").lower() in (
            "off", "0", "false", "no"):
        return ""
    name = voice._setting("CALL_MONITOR_DEVICE", _speakers)
    if not name:
        with contextlib.suppress(Exception):
            import sounddevice

            name = str(sounddevice.query_devices(kind="output")["name"])
    if name in (to_call(), from_call()):
        return ""
    return name


def monitor_settings() -> dict:
    """The call monitor as Settings shows it: on or off, the device
    chosen ("" for the Mac's speakers from before the call), and the
    output devices it may play to, never the call's own."""

    devices: list[str] = []
    with contextlib.suppress(Exception):  # no audio system at all
        import sounddevice

        for d in sounddevice.query_devices():
            name = str(d["name"])
            if (d["max_output_channels"] > 0 and name not in devices
                    and name not in (TO_CALL, FROM_CALL,
                                     to_call(), from_call())):
                devices.append(name)
    return {
        "on": voice._setting("CALL_MONITOR", "on").lower() not in (
            "off", "0", "false", "no"),
        "device": voice._setting("CALL_MONITOR_DEVICE", ""),
        "devices": devices,
    }


def set_monitor(on: Optional[bool] = None,
                device: Optional[str] = None) -> dict:
    """Turn the call monitor on or off, or choose its device ("" for the
    Mac's speakers), here and in ~/.flash.env."""

    from .envfile import set_env_var, unset_env_var

    if on is not None:
        os.environ["CALL_MONITOR"] = "on" if on else "off"
        set_env_var(ENV_PATH, "CALL_MONITOR", "on" if on else "off")
    if device is not None:
        device = device.strip()
        if device in (TO_CALL, FROM_CALL, to_call(), from_call()):
            raise ValueError(
                f"{device} carries the call itself, so it can't be the "
                "monitor: the call would hear itself.")
        if device:
            os.environ["CALL_MONITOR_DEVICE"] = device
            set_env_var(ENV_PATH, "CALL_MONITOR_DEVICE", device)
        else:
            os.environ.pop("CALL_MONITOR_DEVICE", None)
            unset_env_var(ENV_PATH, "CALL_MONITOR_DEVICE")
    return monitor_settings()


SWITCH = "SwitchAudioSource"
INSTALL = "brew install blackhole-2ch blackhole-16ch switchaudio-osx"

# How long a call may last, how long Flash waits for a word before it
# hangs up, and how long a pause ends what someone is saying.
MOST_SECONDS = 10 * 60
QUIET_SECONDS = 40.0
PAUSE_SECONDS = 0.9  # the default; CALL_SILENCE_SECONDS sets it
PAUSE_LEAST = 0.3
PAUSE_MOST = 3.0
# How a call opens (CALL_GREETING_MODE): a fixed greeting, the user's own
# (CALL_GREETING), or words Flash writes itself.
GREETING_MODES = ("default", "custom", "own")
# How long Flash waits for the call to be picked up, or to come in.
PICK_UP_SECONDS = 45.0
# The longest Flash waits for the Mac's "Call" prompt to be answered
# (by Flash, or by you) before it gives up.
CONFIRM_SECONDS = 30.0
# The longest one look at the call's windows may take: a slow look is
# skipped, so a wait never outlasts its deadline by much. A look takes
# about 2 to 6 seconds on macOS 27 with FaceTime and Phone open.
LOOK_SECONDS = 15
# Looks in a row that must find no call before it counts as over: one
# look can miss the End button (a slow or failed read, a redrawn window).
GONE_LOOKS = 3
# The longest one turn of theirs is listened to before Flash answers.
LONGEST_TURN = 30.0
# The model's word for "this call is over".
HANG_UP = "[hang up]"
# Said by Flash when the other side wants the user, or something needs the
# user's own answer: the user is pinged to join, and Flash holds the line.
GET_USER = "[get the user]"
# How long Flash holds the line for the user to come on, before it goes
# back and says they could not.
JOIN_WAIT = 90.0
RATE = voice.SAMPLE_RATE
BLOCK = 0.1  # seconds of sound read at a time
CALLS_DIR = FLASH_DIR / "calls"

# Both scripts below read the call's alerts and windows a level at a
# time, each property of a whole level in one request: asked of every
# element in turn, as before, they took over a minute on macOS 27 and
# always ran out of time, so Flash never saw a call. _LEVELS gives, for
# each process, its levels as [spec, rows], a row {role, subrole, name,
# value} per element there.
_LEVELS = r"""
function levels(se, proc, enough) {
  const flat = (a) => Array.isArray(a) ? a.flatMap(flat) : [a];
  const got = (spec, what, n) => {
    try { return flat(spec[what]()); } catch (e) { return Array(n).fill(""); }
  };
  if (!se.uiElementsEnabled()) throw new Error("not allowed assistive access");
  const out = [];
  // Asking for a process that isn't running waits ~3 s for System
  // Events to give up; the list of running ones costs under 0.1 s.
  if (!se.processes.name().includes(proc)) return out;
  let spec;
  try { spec = se.processes.byName(proc).windows; spec.name(); }
  catch (e) { return out; }
  for (let depth = 0; depth < 20; depth++) {
    spec = spec.uiElements;
    let roles;
    try { roles = flat(spec.role()); } catch (e) { break; }
    if (!roles.length) break;
    const n = roles.length, none = Array(n).fill("");
    // Only what is used is asked: each request costs a few ms a level.
    const names = got(spec, "name", n);
    const banners = proc === "NotificationCenter";
    const subs = banners ? got(spec, "subrole", n) : none;
    const texts = roles.includes("AXStaticText");
    const values = texts ? got(spec, "value", n) : none;
    const descs = names.some((x) => !x) ? got(spec, "description", n) : none;
    out.push([spec, roles.map((role, i) => ({
      role: role, subrole: String(subs[i] || ""),
      name: String(names[i] || descs[i] || ""),
      value: String(values[i] || ""),
    }))]);
    // Found what was wanted: the levels below need no reading.
    if (enough && enough(out[out.length - 1][1])) break;
  }
  return out;
}
const PROCS = ["NotificationCenter", "FaceTime", "Phone"];
const BANNER = /Notification/;
const actionName = (a) => {
  let raw = "";
  try { raw = String(a.description() || a.name() || ""); } catch (e) {}
  const m = raw.match(/Name:([^\n]*)/);
  return (m ? m[1] : raw).trim();
};
"""

# Finds a button by its name in the call's alerts and windows, and
# presses it: "Call" to confirm one Flash places (macOS 27's "Click to
# Call" banner, for an iPhone call and a FaceTime Audio one alike, has
# it), "Accept" to take one that comes in (only when its alert names
# WANT, given as argv[1]), and "End" to hang up. A notification's
# buttons are no buttons on macOS 13 to 26 but actions of the
# notification ("Name:Call ..."), so those are looked through too.
# Prints what it pressed, or nothing.
_PRESS = _LEVELS + r"""
function run(argv) {
  const names = JSON.parse(argv[0]);
  const want = (argv[1] || "").toLowerCase();
  const se = Application("System Events");
  const flat = (a) => Array.isArray(a) ? a.flatMap(flat) : [a];
  for (const proc of PROCS) {
    // Pressing only (no WANT to read): stop at the level the button is on.
    const all = levels(se, proc, want ? null : (rows) => rows.some(
      (x) => x.role === "AXButton" && names.includes(x.name)));
    if (want) {
      const said = all.flatMap(([, rows]) => rows)
        .filter((x) => x.role === "AXStaticText")
        .map((x) => x.value || x.name).join(" ");
      if (!said.toLowerCase().includes(want)) continue;
    }
    for (const [spec, rows] of all) {
      for (const x of rows) {
        if (x.role !== "AXButton" || !names.includes(x.name)) continue;
        for (const key of ["description", "name"]) {
          let hit = [];
          const by = {role: "AXButton"};
          by[key] = x.name;
          try { hit = flat(spec.whose(by)()); } catch (e) {}
          for (const b of hit) {
            try { b.actions.byName("AXPress").perform(); return x.name; }
            catch (e) {}
          }
        }
      }
    }
    for (const [spec, rows] of all) {
      const subs = [...new Set(rows.map((x) => x.subrole)
        .filter((sub) => BANNER.test(sub)))];
      for (const sub of subs) {
        let hit = [];
        try { hit = flat(spec.whose({subrole: sub})()); } catch (e) {}
        for (const x of hit) {
          let acts = [];
          try { acts = x.actions(); } catch (e) {}
          for (const a of acts) {
            const name = actionName(a);
            if (names.includes(name)) {
              try { a.perform(); return name; } catch (e) {}
            }
          }
        }
      }
    }
  }
  return "";
}
"""

# A call coming in, read but not answered: the words of the alert that
# has an Accept or Answer button (or notification action), so who is
# calling. Prints nothing when none is ringing.
_RINGING = _LEVELS + r"""
function run() {
  const names = ["Accept", "Answer"];
  const se = Application("System Events");
  const flat = (a) => Array.isArray(a) ? a.flatMap(flat) : [a];
  for (const proc of PROCS) {
    const all = levels(se, proc, null);
    const rows = all.flatMap(([, r]) => r);
    let ringing = rows.some(
      (x) => x.role === "AXButton" && names.includes(x.name));
    for (const [spec, r] of all) {
      if (ringing) break;
      for (const sub of new Set(r.map((x) => x.subrole)
          .filter((s) => BANNER.test(s)))) {
        let hit = [];
        try { hit = flat(spec.whose({subrole: sub})()); } catch (e) {}
        for (const x of hit) {
          let acts = [];
          try { acts = x.actions(); } catch (e) {}
          if (acts.some((a) => names.includes(actionName(a)))) ringing = true;
        }
      }
    }
    if (ringing) {
      return rows.filter((x) => x.role === "AXStaticText")
        .map((x) => x.value || x.name).filter(Boolean).join(" ")
        || "a call";
    }
  }
  return "";
}
"""

# Where the call stands: "live" while there is a call to end, and
# "connected" once its timer runs (it is picked up). Prints JSON.
_STATE = _LEVELS + r"""
function run() {
  const se = Application("System Events");
  const flat = (a) => Array.isArray(a) ? a.flatMap(flat) : [a];
  const ends = ["End", "End Call", "Hang Up"];
  let live = false, connected = false, ringing = false;
  const seen = [];
  // While it rings the call's banner reads "FaceTime Audio…" (or
  // "Calling…"); once answered that text goes, timer or not.
  const RINGS = /(…|\.\.\.)$|^(calling|ringing)\b/i;
  for (const proc of PROCS) {
    // Both seen: the levels below can tell nothing more.
    const sure = (rows) => {
      for (const x of rows) {
        if (x.role === "AXButton" && ends.includes(x.name)) live = true;
        if (x.role === "AXStaticText" && /(^|\s)\d{1,2}:\d{2}(:\d{2})?$/.test(
          (x.value || x.name).trim())) connected = true;
      }
      return live && connected;
    };
    for (const [spec, rows] of levels(se, proc, sure)) {
      for (const x of rows) {
        if (x.role === "AXButton" && ends.includes(x.name)) live = true;
        const shown = (x.value || x.name).trim();
        if (x.role === "AXStaticText" &&
            /(^|\s)\d{1,2}:\d{2}(:\d{2})?$/.test(shown)) connected = true;
        if (x.role === "AXStaticText" && proc !== "Phone" &&
            RINGS.test(shown)) ringing = true;
        if (seen.length < 60 && shown && shown !== "button" &&
            (x.role === "AXButton" || x.role === "AXStaticText"))
          seen.push(`${proc}:${x.role.slice(2)}:${shown.slice(0, 30)}`);
      }
      if (live) continue;
      const subs = [...new Set(rows.map((x) => x.subrole)
        .filter((sub) => BANNER.test(sub)))];
      for (const sub of subs) {
        let hit = [];
        try { hit = flat(spec.whose({subrole: sub})()); } catch (e) {}
        for (const x of hit) {
          let acts = [];
          try { acts = x.actions(); } catch (e) {}
          if (acts.some((a) => ends.includes(actionName(a)))) live = true;
        }
      }
    }
  }
  return JSON.stringify({live: live, connected: connected && live,
                         ringing: ringing && live, seen: seen});
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


# False when this process only serves the web UI: its calls show on the
# page and in CALLS_DIR, not in the terminal it was started from.
terminal = True


def _mark(step: str, since: float) -> None:
    """How long STEP took to come, from SINCE, in calls/timing.log: where
    a call's first seconds go."""

    with contextlib.suppress(OSError):
        CALLS_DIR.mkdir(parents=True, exist_ok=True)
        with (CALLS_DIR / "timing.log").open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%H:%M:%S')} "
                      f"+{time.monotonic() - since:5.1f}s {step}\n")


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
        at = time.strftime('%H:%M:%S')
        line = f"[{at}] {who}: {said}"
        # The page draws the turn as it is said; the terminal prints it.
        if not tool_call({"who": who, "said": said, "at": at,
                          "flash": who == "Flash", "with": self.who}) \
                and terminal:
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
        encoding="utf-8", errors="replace",
        timeout=10, check=False).stdout.strip()
    if name:
        subprocess.run(  # nosec B603 B607
            [SWITCH, "-s", name, "-t", kind], capture_output=True,
            timeout=10, check=False)
    return was


def restore() -> None:
    """The Mac's own input and output back, if a call switched them: once,
    however many times it is asked (the call's end, Stop, Flash
    quitting)."""

    global _speakers

    with _lock:
        was = dict(_was)
        _was.clear()
    _speakers = ""
    for kind in ("input", "output"):
        if was.get(kind):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                _switch(kind, was[kind])


atexit.register(restore)


@contextlib.contextmanager
def _routed():
    """The call's sound through the loopback devices, and the Mac's own
    devices back afterwards, whatever happened."""

    global _speakers

    try:
        for kind, name in (("input", to_call()), ("output", from_call())):
            was = _switch(kind, name)
            if was and was != name:
                with _lock:
                    _was.setdefault(kind, was)
        _speakers = _was.get("output", "")
        yield
    finally:
        restore()


@contextlib.contextmanager
def _holding(owner: str, who: str):
    """The call as the one going now, for halt() and set_mic(), with the
    page told when it starts and ends."""

    global _owner

    with _lock:
        _owner = owner
    tool_call({"state": "start", "with": who})
    try:
        yield
    finally:
        with _lock:
            _owner = None
        tool_call({"state": "end", "with": who})


@contextlib.contextmanager
def _opened():
    """The call's Line, open while the call talks, and closed (the mic
    passthrough with it) whatever happened."""

    global _line

    line = Line()
    with _lock:
        _line = line
    try:
        yield line
    finally:
        with _lock:
            _line = None
        line.close()


def _mine(owner: str) -> bool:
    return _owner is not None and (not owner or not _owner
                                   or owner == _owner)


def halt(owner: str = "") -> bool:
    """Stop the call going now, at once: Flash stops speaking mid-word
    and listening, the user's mic leaves the call, and the Mac's own
    devices come back. OWNER (a chat): only its own call. Whether there
    was one."""

    with _lock:
        if not _mine(owner):
            return False
        line = _line
    if line is not None:
        line.halt()
    restore()
    return True


def live() -> dict:
    """The call going now, as the page shows it."""

    with _lock:
        line = _line
        on = _owner is not None
    return {"live": on, "talking": line is not None,
            "mic": bool(line is not None and getattr(line, "mic", None)),
            "paused": bool(line is not None and line._paused()),
            "taken": bool(line is not None and getattr(line, "taken", False)),
            "asking": bool(line is not None and getattr(line, "asking_since", 0.0))}


def set_paused(on: bool, owner: str = "") -> dict:
    """Hold Flash on the call: it stops speaking at once and answers
    nothing until resumed, while the call and its listening go on."""

    with _lock:
        line = _line if _mine(owner) else None
    if line is None:
        raise CallError("Flash isn't talking on a call right now.")
    line.pause(on)
    return live()


def take_over(on: bool, owner: str = "") -> dict:
    """The user takes the call over from Flash (ON), or hands it back:
    taking it puts their mic in and Flash goes quiet, still writing down
    what the other side says; handing back takes the mic out, and Flash
    carries on from where the call is."""

    with _lock:
        line = _line if _mine(owner) else None
    if line is None:
        raise CallError("Flash isn't on a call right now.")
    if on:
        line.set_mic(True)
        line.pause(True)
        line.taken = True
        line.asking_since = 0.0
        tool_call({"state": "taken"})
    else:
        line.taken = False
        line.handed_back = True
        line.set_mic(False)
        line.pause(False)
        tool_call({"state": "back"})
    return live()


def set_mic(on: bool, owner: str = "") -> dict:
    """The user's own microphone into the call alongside Flash's voice,
    or out of it again."""

    with _lock:
        line = _line if _mine(owner) else None
    if line is None:
        raise CallError("Flash isn't talking on a call right now.")
    line.set_mic(on)
    return live()


def _is_call_device(name: str) -> bool:
    """Whether NAME carries the call's own sound: never the user's mic."""

    return (not name or "blackhole" in name.lower()
            or name in (TO_CALL, FROM_CALL, to_call(), from_call()))


def _system_mic() -> str:
    """The computer's default input as it is now. PortAudio keeps the one
    it saw when Flash started, so ask the system first when it can."""

    if shutil.which(SWITCH):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            name = subprocess.run(  # nosec B603 B607 -- a fixed program
                [SWITCH, "-c", "-t", "input"], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=10,
                check=False).stdout.strip()
            if name:
                return name
    with contextlib.suppress(Exception):
        import sounddevice

        return str(sounddevice.query_devices(kind="input")["name"])
    return ""


def _user_mic() -> str:
    """The user's own microphone: CALL_USER_MIC, else the computer's
    input from before the call, else its input now. Never a device the
    call's sound goes through, which would feed the call back into
    itself."""

    chosen = voice._setting("CALL_USER_MIC", "")
    if chosen and _is_call_device(chosen):
        raise CallError(
            f"My mic is set to {chosen}, which carries the call itself. "
            "Pick your own mic under Settings, Voice, Calls, My mic.")
    for name in (chosen, _was.get("input", ""), _system_mic()):
        if not _is_call_device(name):
            return name
    raise CallError(
        "Flash can't find your own microphone: the computer's input is a "
        "device the call goes through. Pick yours under Settings, Voice, "
        "Calls, My mic.")


def mic_settings() -> dict:
    """"My mic" as Settings shows it: the mic chosen ("" for the system
    default) and the inputs it may be, never the call's own."""

    devices: list[str] = []
    with contextlib.suppress(Exception):  # no audio system at all
        import sounddevice

        for d in sounddevice.query_devices():
            name = str(d["name"])
            if (d["max_input_channels"] > 0 and name not in devices
                    and not _is_call_device(name)):
                devices.append(name)
    return {"mic": voice._setting("CALL_USER_MIC", ""), "mics": devices}


def pause_seconds() -> float:
    """How long the other side must be quiet before Flash answers:
    CALL_SILENCE_SECONDS, kept between the shortest and longest allowed."""

    try:
        seconds = float(voice._setting("CALL_SILENCE_SECONDS",
                                       str(PAUSE_SECONDS)))
    except ValueError:
        return PAUSE_SECONDS
    if seconds != seconds:  # NaN
        return PAUSE_SECONDS
    return min(max(seconds, PAUSE_LEAST), PAUSE_MOST)


def silence_settings() -> dict:
    """The wait for silence as Settings shows it."""

    return {"silence": pause_seconds(), "silence_least": PAUSE_LEAST,
            "silence_most": PAUSE_MOST}


def set_pause_seconds(seconds: str) -> dict:
    """Set how long the other side must be quiet before Flash answers,
    here and in ~/.flash.env."""

    from .envfile import set_env_var

    try:
        value = float(seconds)
    except ValueError:
        raise ValueError("The wait for silence takes a number of "
                         "seconds.") from None
    if not PAUSE_LEAST <= value <= PAUSE_MOST:
        raise ValueError(f"The wait for silence goes from {PAUSE_LEAST} "
                         f"to {PAUSE_MOST} seconds.")
    value = round(value, 1)
    os.environ["CALL_SILENCE_SECONDS"] = str(value)
    set_env_var(ENV_PATH, "CALL_SILENCE_SECONDS", str(value))
    return silence_settings()


def set_user_mic(device: str) -> dict:
    """Choose the user's mic for calls ("" for the system default), here
    and in ~/.flash.env."""

    from .envfile import set_env_var, unset_env_var

    device = device.strip()
    if device and _is_call_device(device):
        raise ValueError(f"{device} carries the call itself, so it can't "
                         "be your mic: the call would hear itself.")
    if device:
        os.environ["CALL_USER_MIC"] = device
        set_env_var(ENV_PATH, "CALL_USER_MIC", device)
    else:
        os.environ.pop("CALL_USER_MIC", None)
        unset_env_var(ENV_PATH, "CALL_USER_MIC")
    return mic_settings()


class _MicPass:
    """The user's microphone, a block at a time, into the device the call
    hears Flash on. The Mac mixes it with Flash's voice there; nothing of
    it goes where Flash listens, or to the monitor."""

    def __init__(self, sd, mic: str, into: str) -> None:
        rate = int(sd.query_devices(mic, "input")["default_samplerate"])
        self.frames = int(rate * BLOCK)
        self.inp = sd.RawInputStream(device=mic, channels=1, dtype="int16",
                                     samplerate=rate, blocksize=self.frames)
        self.out = sd.RawOutputStream(device=into, channels=1,
                                      dtype="int16", samplerate=rate,
                                      blocksize=self.frames)
        self.done = threading.Event()
        self.inp.start()
        self.out.start()
        self._heard(mic)
        self.thread = threading.Thread(target=self._pass, daemon=True,
                                       name="call mic")
        self.thread.start()

    def _heard(self, mic: str) -> None:
        """Pass the first second on, and stop with a reason when it is
        all silence: the system gives an app without microphone access
        zeros, not an error, so the call would hear nothing."""

        for _ in range(int(1 / BLOCK)):
            block, _ = self.inp.read(self.frames)
            self.out.write(bytes(block))
            if any(bytes(block)):
                return
        self.close()
        raise CallError(
            f"{mic} gave Flash only silence, so the call couldn't hear "
            "you. Let the app Flash runs in use the microphone (on a Mac: "
            "System Settings, Privacy & Security, Microphone), or pick "
            "another under Settings, Voice, Calls, My mic.")

    def _pass(self) -> None:
        while not self.done.is_set():
            try:
                block, _ = self.inp.read(self.frames)
                if self.done.is_set():
                    return
                self.out.write(bytes(block))
            except Exception:  # noqa: BLE001  (a device gone: it ends)
                return

    def close(self) -> None:
        self.done.set()
        if getattr(self, "thread", None) is not None:
            self.thread.join(timeout=1.0)
        for stream in (self.inp, self.out):
            with contextlib.suppress(Exception):
                stream.stop()
                stream.close()


def _no_access(exc: Exception) -> bool:
    """Whether EXC is macOS refusing Flash its Accessibility access."""

    said = str(exc)
    # A timeout's message quotes the script, which names it too.
    return ("could not run" not in said
            and ("assistive access" in said or "-25211" in said))


_NO_ACCESS = ("Flash isn't allowed to press Call: give the app that runs "
              "Flash (Terminal, or whichever you start it from) "
              "Accessibility access in System Settings, Privacy & "
              "Security, Accessibility, then restart it and try again.")


def _press(names: list[str], want: str = "") -> str:
    try:
        return computer._run(["osascript", "-l", "JavaScript", "-e", _PRESS,
                              json.dumps(names), want],
                             timeout=LOOK_SECONDS).strip()
    except computer.ComputerError as exc:
        if _no_access(exc):
            raise CallError(_NO_ACCESS) from None
        return ""


def ringing() -> str:
    """The words of a call alert ringing now, saying who is calling, or
    "" when none is. Nothing is pressed: the call is left to ring."""

    if computer.system() != "mac":
        return ""
    try:
        return computer._run(["osascript", "-l", "JavaScript", "-e",
                              _RINGING], timeout=LOOK_SECONDS).strip()
    except computer.ComputerError:
        return ""


def _state() -> dict:
    """Where the call stands: {"live": bool, "connected": bool}."""

    try:
        got = json.loads(computer._run(
            ["osascript", "-l", "JavaScript", "-e", _STATE],
            timeout=LOOK_SECONDS).strip() or "{}")
    except computer.ComputerError as exc:
        if _no_access(exc):
            # Without it no call can be seen at all: say so, not "no call".
            raise CallError(_NO_ACCESS) from None
        return {"live": False, "connected": False}
    except ValueError:
        return {"live": False, "connected": False}
    return {"live": bool(got.get("live")),
            "connected": bool(got.get("connected")),
            "ringing": bool(got.get("ringing")),
            "seen": list(got.get("seen") or [])}


def _facetime_devices() -> None:
    with contextlib.suppress(computer.ComputerError):
        computer._run(["osascript", "-l", "JavaScript", "-e",
                       _FACETIME_DEVICES, to_call(), from_call()],
                      timeout=20)


def _confirmed(stopping: Callable[[], bool]) -> bool:
    """Waits for the Mac's "Call" prompt to be answered, pressing Call
    on it. True once Flash pressed it or the call is live (you may have
    clicked it yourself); False when stopped or out of time."""

    deadline = time.monotonic() + CONFIRM_SECONDS
    while time.monotonic() < deadline and not stopping():
        if _press(["Call"]) or _state()["live"]:
            return True
        time.sleep(0.5)
    return False


def _picked_up(call: Call, stopping: Callable[[], bool]) -> bool:
    """Waits for the call Flash placed to be picked up. False, with why in
    CALL.ended, when it never was."""

    deadline = time.monotonic() + PICK_UP_SECONDS
    seen_live = False
    rang = False
    gone = 0
    was = None
    began = deadline - PICK_UP_SECONDS
    while time.monotonic() < deadline:
        if stopping():
            call.ended = "you stopped it"
            return False
        now = _state()
        ringing = bool(now.get("ringing"))
        if (now["live"], now["connected"], ringing) != was:
            # What the screen showed each time the state changed: how a
            # pick-up looks on this Mac, for when it is missed.
            was = (now["live"], now["connected"], ringing)
            _mark(f"look live={was[0]} connected={was[1]} "
                  f"ringing={was[2]}: "
                  + " | ".join(now.get("seen", [])), began)
        if now["connected"]:
            return True
        # No timer on this Mac's banner: the call was answered when its
        # "FaceTime Audio…" goes while the call is still there.
        if ringing:
            rang = True
        elif rang and now["live"]:
            return True
        if now["live"]:
            seen_live = True
            gone = 0
        elif seen_live:
            gone += 1
            if gone >= GONE_LOOKS:
                call.ended = "they didn't pick up"
                return False
        # Looking takes seconds already (the Phone app's window is deep):
        # no long wait on top of it, or the pick-up is noticed late.
        time.sleep(0.1)
    if seen_live:
        # Still ringing, or a Mac that shows no timer: talk anyway; a
        # quiet line ends it.
        return True
    call.ended = (f"Flash couldn't see the call start on this Mac within "
                  f"{PICK_UP_SECONDS:.0f} seconds, so it stopped")
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
        gone = 0
        while not self._over.wait(every):
            gone = 0 if look()["live"] else gone + 1
            if gone >= GONE_LOOKS:
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
    _hide_call_app()


# Hidden, not minimized: a hidden app's windows still answer the
# Accessibility reads that see the call (checked on this Mac), and the
# call's End button is on its banner anyway.
_HIDE = """
tell application "System Events"
  repeat with n in {"Phone", "FaceTime"}
    if exists process n then set visible of process n to false
  end repeat
end tell
"""


def _hide_call_app() -> None:
    """Puts the Phone or FaceTime app that came up for the call out of the
    way, so it does not sit in front of what the user is doing."""

    with contextlib.suppress(computer.ComputerError):
        computer._run(["osascript", "-e", _HIDE], timeout=5)


# --- Sound ----------------------------------------------------------------


class Line:
    """The call's sound, both ways: what the other side says, heard a
    sentence at a time, and Flash's voice into the call."""

    def __init__(self) -> None:
        import sounddevice

        self.sd = sounddevice
        self.stopped = threading.Event()
        self.mic: Optional[_MicPass] = None
        self.device_rate = int(sounddevice.query_devices(
            from_call(), "input")["default_samplerate"])
        self.stream = sounddevice.RawInputStream(
            device=from_call(), channels=1, dtype="int16",
            samplerate=self.device_rate,
            blocksize=int(self.device_rate * BLOCK))
        self.stream.start()
        # The user hears the call too, on their own speakers.
        self.monitor = None
        if name := monitor():
            with contextlib.suppress(Exception):
                self.monitor = sounddevice.RawOutputStream(
                    device=name, channels=1, dtype="int16",
                    samplerate=self.device_rate)
                self.monitor.start()

    def _halted(self) -> bool:
        stopped = getattr(self, "stopped", None)
        return bool(stopped and stopped.is_set())

    def _paused(self) -> bool:
        paused = getattr(self, "paused", None)
        return bool(paused and paused.is_set())

    def pause(self, on: bool) -> None:
        """Hold Flash's voice, cutting off what it is saying, or let it
        speak again."""

        if getattr(self, "paused", None) is None:
            self.paused = threading.Event()
        if on:
            self.paused.set()
        else:
            self.paused.clear()

    def halt(self) -> None:
        """Stop now: speaking mid-word, listening, the user's mic."""

        if getattr(self, "stopped", None) is None:
            self.stopped = threading.Event()
        self.stopped.set()
        self.set_mic(False)

    def set_mic(self, on: bool) -> None:
        """The user's own microphone into the call, or out of it."""

        mic = getattr(self, "mic", None)
        if not on:
            self.mic = None
            if mic is not None:
                mic.close()
            return
        if mic is None and not self._halted():
            mine = _user_mic()
            try:
                self.mic = _MicPass(self.sd, mine, to_call())
            except CallError:
                raise
            except Exception as exc:  # PortAudio's own errors
                raise CallError(
                    f"Flash couldn't open your mic, {mine}: {exc}") from exc

    def close(self) -> None:
        self.set_mic(False)
        with contextlib.suppress(Exception):
            self.stream.stop()
            self.stream.close()
        if getattr(self, "monitor", None):
            with contextlib.suppress(Exception):
                self.monitor.stop()
                self.monitor.close()

    def _monitor(self, pcm: bytes) -> None:
        """PCM, at the line's rate, on the user's speakers: never a
        reason for the call to fail."""

        if getattr(self, "monitor", None):
            with contextlib.suppress(Exception):
                self.monitor.write(pcm)

    def _play(self, write: Callable[[bytes], None], pcm: bytes,
              rate: int) -> bool:
        """PCM through WRITE a block at a time, so a halt or a pause cuts
        it short. Whether it all played."""

        step = max(1, int(rate * BLOCK)) * 2
        for at in range(0, len(pcm), step):
            if self._halted() or self._paused():
                return False
            write(pcm[at:at + step])
        return True

    def hear(self, longest: float = QUIET_SECONDS,
             stopping: Callable[[], bool] = lambda: False) -> str:
        """What the other side says next, once they pause: "" if they
        say nothing for LONGEST seconds."""

        base = voice.silence_threshold()
        pause = pause_seconds()
        # The line's own hiss sets the bar too, so a noisy line is not
        # taken for someone who never stops talking: it is learned from
        # the first moments of the line, and kept up after.
        floor = getattr(self, "floor", None)
        learning: list[float] = []
        heard = bytearray()
        quiet = 0.0
        waited = 0.0
        frames = int(self.device_rate * BLOCK)
        while not stopping() and not self._halted():
            block, _ = self.stream.read(frames)
            block = bytes(block)
            self._monitor(block)
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
                if quiet >= pause or \
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

        if self._halted() or self._paused():
            return
        wav, why = voice.synthesize(voice.speakable(text))
        if why:
            raise CallError(why)
        self.play(wav)

    def play(self, wav: bytes) -> None:
        """WAV, already spoken, into the call."""

        if not wav or self._halted() or self._paused():
            return
        with wave.open(io.BytesIO(wav)) as read:
            rate = read.getframerate()
            pcm = read.readframes(read.getnframes())
        out = self.sd.RawOutputStream(device=to_call(), channels=1,
                                      dtype="int16", samplerate=rate)
        # Flash's voice on the user's speakers as well, alongside.
        heard = threading.Thread(target=self._play, daemon=True, args=(
            self._monitor, voice._resample(pcm, rate, self.device_rate),
            self.device_rate))
        with out:
            heard.start()
            if self._play(out.write, pcm, rate):
                # Played out before the next word is listened for.
                time.sleep(0.15)
        heard.join(timeout=5)
        # Its own voice, still on its way back through the call, is not
        # what the other side said.
        with contextlib.suppress(Exception):
            frames = int(self.device_rate * BLOCK)
            while self.stream.read_available >= frames:
                self.stream.read(frames)


# --- Talking ----------------------------------------------------------------


def _first_name(user: str) -> str:
    """USER's first name only, or "" when it is not known."""

    user = (user or "").strip()
    if not user or user == "the user":
        return ""
    return user.split()[0]


def greeting_settings() -> dict:
    """How a call opens, as Settings shows it: CALL_GREETING_MODE is
    default (a fixed greeting), custom (CALL_GREETING) or own (Flash
    writes its own)."""

    mode = voice._setting("CALL_GREETING_MODE", "default").lower()
    if mode not in GREETING_MODES:
        mode = "default"
    return {"greeting_mode": mode,
            "greeting": voice._setting("CALL_GREETING", "")}


def set_greeting(mode: str, text: str = "") -> dict:
    """Choose how calls open, here and in ~/.flash.env: MODE is default,
    custom (with TEXT, where {name} is the user's first name) or own."""

    from .envfile import set_env_var

    mode = mode.strip().lower()
    if mode not in GREETING_MODES:
        raise ValueError("The call greeting is default, custom or own.")
    text = " ".join(text.split())
    if mode == "custom":
        if not text:
            raise ValueError("Type the greeting to say first.")
        if len(text) > 300:
            raise ValueError("Keep the greeting under 300 characters.")
        os.environ["CALL_GREETING"] = text
        set_env_var(ENV_PATH, "CALL_GREETING", text)
    os.environ["CALL_GREETING_MODE"] = mode
    set_env_var(ENV_PATH, "CALL_GREETING_MODE", mode)
    return greeting_settings()


def _greeting(call: Call, user: str) -> str:
    """The fixed words said first on CALL, or "" when Flash writes its
    own. The default says it is automated, with the first name only."""

    settings = greeting_settings()
    name = _first_name(user)
    if settings["greeting_mode"] == "own":
        return ""
    custom = settings["greeting"]
    if settings["greeting_mode"] == "custom" and custom:
        said = custom.replace("{name}", name or "the person I work for")
        return " ".join(said.split())
    doing = ("joining" if call.joined else
             "answering" if call.incoming else "calling")
    if name:
        if doing == "calling":
            return ("Hi, this is an automated assistant calling on behalf "
                    f"of {name}.")
        return f"Hi, this is an automated assistant {doing} for {name}."
    return f"Hi, this is an automated assistant {doing}."


def _instructions(call: Call, user: str, greeted: str = "") -> str:
    them = call.who or "the other person"
    user = _first_name(user) or "the user"
    if call.joined:
        opening = (f"{user} brought you into a call they are on, with "
                   f"{them}, to talk to them.")
    elif call.incoming:
        opening = f"You answered a call from {them} for {user}."
    else:
        opening = f"You called {them} for {user}."
    if greeted:
        first = (f"- You have already said, aloud: \"{greeted}\" Do not "
                 "greet or introduce yourself again: carry straight on "
                 "with why, in a sentence or two. ")
    else:
        first = ("- First, say plainly that you are an automated assistant "
                 f"calling (or answering) for {user}, and why, in a short "
                 "greeting. ")
    return (
        f"You are an AI assistant on a phone call. {opening} "
        f"What {user} wants from it: "
        f"{call.goal or 'find out what they want'}.\n"
        f"{first}Never claim to be {user} or a person.\n"
        "- Talk as people do on the phone: one or two short sentences, "
        "then let them answer. No lists, no markdown, nothing to read.\n"
        f"- Agree to nothing {user} did not ask for: no payments, no "
        "personal details, no promises. Say you will pass it on.\n"
        f"- If they ask to speak to {user} themselves, or something needs "
        f"{user}'s own answer that you cannot give, say you will get "
        f"{user} on the line, ask them to hold a moment, and end with "
        f"{GET_USER}. {user} is pinged and joins if they can.\n"
        f"- Once the goal is done, or they say goodbye, say a short goodbye "
        f"and end with {HANG_UP}."
    )


# A line in a call's record for the model alone: what happened on the
# line that nobody said (the user took over, or did not come).
NOTE = "(note)"


def _ask_user_to_join(call: Call, line, user: str) -> None:
    """Ping the user to come on the call, and hold the line for them."""

    from . import notify

    who = call.who or "the other side"
    why = call.lines[-2][1] if len(call.lines) >= 2 and call.lines[-2][0] != "Flash" else ""
    line.asking_since = time.monotonic()
    line.pause(True)
    tool_call({"state": "join", "with": call.who, "why": why})
    with contextlib.suppress(Exception):
        notify.notify("Join the call", f"{who} wants to talk to you. Flash is holding the line.")


def _join_due(line) -> bool:
    since = getattr(line, "asking_since", 0.0)
    return bool(since) and not getattr(line, "taken", False) and time.monotonic() - since >= JOIN_WAIT


def _join_timed_out(call: Call, line, user: str) -> bool:
    """The user was asked to join and has not: Flash goes back on."""

    since = getattr(line, "asking_since", 0.0)
    if not since or getattr(line, "taken", False) or time.monotonic() - since < JOIN_WAIT:
        return False
    line.asking_since = 0.0
    line.pause(False)
    tool_call({"state": "join-missed"})
    first = _first_name(user) or "the user"
    call.lines.append((NOTE, f"{first} did not come to the phone. Tell them {first} can't come to the phone "
                             "right now, and offer to pass a message on."))
    return True


def _reply(call: Call, user: str, ask: Callable[[list[dict]], str],
           greeted: str = "") -> str:
    messages = [{"role": "system",
                 "content": _instructions(call, user, greeted)}]
    for who, said in call.lines:
        if who == NOTE:
            messages.append({"role": "system", "content": said})
            continue
        messages.append({"role": "assistant" if who == "Flash" else "user",
                         "content": said})
    if not call.lines or call.lines[-1][0] == "Flash":
        messages.append({"role": "user", "content": "(silence on the line)"})
    return ask(messages).strip()


def _first_words(call: Call, user: str,
                 ask: Callable[[list[dict]], str]) -> str:
    """What Flash says first on CALL: the fixed greeting, spoken as set,
    then the model carrying on from it; or, in own mode, the model's."""

    greeting = _greeting(call, user)
    if not greeting:
        return _reply(call, user, ask)
    rest = _reply(call, user, ask, greeted=greeting)
    return f"{greeting} {rest}".strip()


class _Opening:
    """Flash's first words on CALL, written and spoken on a thread of their
    own while the call is still dialling or being set up, so they are said
    the moment it is live, with no silence first."""

    def __init__(self, call: Call, user: str,
                 ask: Callable[[list[dict]], str]) -> None:
        self.got: Optional[tuple[str, bytes]] = None
        self.since = time.monotonic()
        self._thread = threading.Thread(
            target=self._ready, args=(call, user, ask), daemon=True,
            name="call opening")
        self._thread.start()

    def _ready(self, call: Call, user: str,
               ask: Callable[[list[dict]], str]) -> None:
        try:
            said = _first_words(call, user, ask)
            plain = said.replace(HANG_UP, "").replace(
                HANG_UP.title(), "").strip()
            wav = b""
            if plain:
                wav, why = voice.synthesize(voice.speakable(plain))
                if why:
                    wav = b""
            self.got = (said, wav or b"")
            _mark("first words ready", self.since)
        except Exception:  # noqa: BLE001  (talk() asks again, as before)
            self.got = None

    def get(self) -> Optional[tuple[str, bytes]]:
        self._thread.join(timeout=60)
        return self.got


def talk(call: Call, line: Line, user: str, ask: Callable[[list[dict]], str],
         stopping: Callable[[], bool] = lambda: False,
         opening: Optional[_Opening] = None) -> None:
    """The conversation: Flash and the other side in turn, until the goal
    is met, they go quiet, or it runs too long."""

    told = stopping
    halted = getattr(line, "_halted", None)
    if halted is not None:
        # Stop halts the line itself: that ends the talk too.
        def stopping() -> bool:
            return told() or halted()

    def held() -> bool:
        paused = getattr(line, "_paused", None)
        return bool(paused and paused())

    started = time.monotonic()
    # Placed, answered or joined: once live, Flash greets first, with the
    # goal, instead of waiting for a hello that may never come.
    while not stopping():
        if time.monotonic() - started > MOST_SECONDS:
            call.ended = "it ran past ten minutes"
            break
        if getattr(line, "handed_back", False):
            line.handed_back = False
            first = _first_name(user) or "the user"
            call.lines.append((NOTE, f"{first} was on the line themselves for a while and has handed the call back "
                                     "to you; what they said is not in this record. Carry on from where it is, "
                                     "briefly."))
        if held() and _join_timed_out(call, line, user):
            continue
        if held():
            # Paused, taken over, or holding for the user: still
            # listening, so what they say is kept, but Flash answers
            # nothing until it is back on.
            heard = line.hear(stopping=lambda: stopping() or not held() or (
                bool(getattr(line, "asking_since", 0.0)) and _join_due(line)))
            if heard:
                call.say(call.who or "Them", heard)
            continue
        # The first words may be ready already (see _Opening).
        ready = opening.get() if opening is not None and not call.lines \
            else None
        if opening is not None:
            _mark("talking starts", opening.since)
        opening = None
        if ready:
            said, wav = ready
        elif not call.lines:
            said, wav = _first_words(call, user, ask), b""
        else:
            said, wav = _reply(call, user, ask), b""
        done = HANG_UP in said.lower()
        wants_user = GET_USER in said.lower()
        said = said.replace(HANG_UP, "").replace(HANG_UP.title(), "")
        said = said.replace(GET_USER, "").replace(GET_USER.title(), "").strip()
        if said:
            call.say("Flash", said)
            if wav and hasattr(line, "play"):
                line.play(wav)
            else:
                line.say(said)
        if done:
            call.ended = "the call was done"
            break
        if wants_user and hasattr(line, "pause"):
            _ask_user_to_join(call, line, user)
            continue
        heard = line.hear(stopping=stopping)
        if not heard and stopping():
            break
        if not heard and held():
            continue
        if not heard:
            call.ended = "the other side went quiet"
            break
        call.say(call.who or "Them", heard)
    if stopping() and not call.ended:
        call.ended = getattr(told, "why", "") or "you stopped it"


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
          via: str = PHONE, owner: str = "",
          tell: Callable[[str], None] = lambda said: None) -> Call:
    """Call WHO and talk to them for USER, towards GOAL: on the phone, or
    with FaceTime Audio (VIA). TELL hears what it is doing, for the chat."""

    why = missing()
    if why:
        raise CallError(why)
    shown, number = _number_of(who, facetime=True)
    if "@" in number:
        # Only FaceTime calls an email address.
        via = FACETIME
    call = Call(who=shown, number=number, goal=goal, incoming=False)
    opening = _Opening(call, user, ask)
    began = opening.since
    # First words written and spoken before dialling, so they are said the
    # moment the call is picked up, not seconds into it.
    tell(f"Getting ready to call {shown}…")
    opening.get()
    if stopping():
        call.ended = "you stopped it"
        return call
    tell(f"Calling {shown}…")
    with _holding(owner, shown), _routed():
        _dial(number, via)
        _mark(f"dialled {shown}", began)
        try:
            # The Mac asks before it dials, in a notification: Flash says
            # yes, unless you click Call first.
            if not _confirmed(stopping):
                if stopping():
                    call.ended = "you stopped it"
                    return call
                raise CallError(
                    "The computer asked to confirm the call and it wasn't "
                    f"started within {CONFIRM_SECONDS:.0f} seconds. Click "
                    "Call on the notification, or give the app that runs "
                    "Flash Accessibility access (System Settings, Privacy "
                    "& Security, Accessibility) so Flash can press Call, "
                    "and try again.")
            _mark("call confirmed", began)
            _hide_call_app()
            _facetime_devices()
            _mark("devices set", began)
            if not _picked_up(call, stopping):
                return call
            _mark("picked up", began)
            with _opened() as line:
                _mark("line open", began)
                on = _StillOn(stopping)
                try:
                    talk(call, line, user, ask, on, opening)
                finally:
                    on.close()
        finally:
            _hang_up()
    return call


# Words for "whoever calls", which no call alert ever says.
ANYONE = {"", "anyone", "anybody", "any", "someone", "somebody",
          "whoever", "any caller", "everyone", "all"}


def take_next(who: str, goal: str, user: str,
              ask: Callable[[list[dict]], str], minutes: float = 30,
              stopping: Callable[[], bool] = lambda: False,
              owner: str = "") -> Optional[Call]:
    """Wait up to MINUTES for a call from WHO, answer it, and talk for
    USER, towards GOAL. None if none came."""

    why = missing()
    if why:
        raise CallError(why)
    shown = who
    number = ""
    if who.strip().lower() in ANYONE:
        # Whoever calls: no name to look for in the alert.
        want = ""
    else:
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
    opening = _Opening(call, user, ask)
    with _holding(owner, shown), _routed():
        try:
            time.sleep(1.0)
            _facetime_devices()
            with _opened() as line:
                on = _StillOn(stopping)
                try:
                    talk(call, line, user, ask, on, opening)
                finally:
                    on.close()
        finally:
            _hang_up()
    return call


def join(goal: str, user: str, ask: Callable[[list[dict]], str],
         who: str = "",
         stopping: Callable[[], bool] = lambda: False,
         owner: str = "") -> Call:
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
    opening = _Opening(call, user, ask)
    with _holding(owner, call.who):
        try:
            with _opened() as line:
                talk(call, line, user, ask, stopping, opening)
        finally:
            # Nothing was switched for it, unless something else was.
            restore()
    return call


def summary(call: Call, path: Path) -> str:
    """What happened on CALL, for the chat."""

    said = call.transcript() or "(nothing was said)"
    if call.joined:
        return (f"Flash left the call with {call.who}: {call.ended}.\n"
                f"{said}\nTranscript kept at {path}.")
    ask = ("\nTell the user, first and plainly, every message the caller "
           "asked to pass along and anything they asked for, in their "
           "words; then the transcript's path." if call.incoming else "")
    return (f"The call {'from' if call.incoming else 'to'} {call.who} "
            f"ended: {call.ended}.\n{said}\nTranscript kept at {path}.{ask}")
