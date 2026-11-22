"""Xcode, simulators and devices, on a Mac: what the xcode tool does.

Flash builds and tests an app with xcodebuild, runs it in a simulator
(simctl) or on a phone, watch or tablet plugged in or paired over the
network (devicectl, what Xcode's device window uses), and debugs it the
way a developer would: the app's own console output while it runs, the
system log for its process, a screenshot of the simulator, the newest
crash report with the crashed thread first, and a backtrace of every
thread of a running app (lldb) when it hangs.

Every command's output is cut down to what matters: a build's errors and
warnings with their file and line, a test run's failures, a crash's
reason and frames, never thousands of lines of compiler chatter.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import subprocess  # nosec B404 -- Apple's own developer tools, fixed argv
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

ACTIONS = ("status", "schemes", "build", "test", "boot", "run", "stop",
           "logs", "screenshot", "crashes", "backtrace", "open_url",
           "appearance", "devices_window", "show", "open_app")

# The apps every iPhone has, by the names people call them: open_app
# opens one by name on a simulator or a device.
APPS = {
    "wallet": "com.apple.Passbook", "apple pay": "com.apple.Passbook",
    "settings": "com.apple.Preferences", "safari": "com.apple.mobilesafari",
    "photos": "com.apple.mobileslideshow", "camera": "com.apple.camera",
    "maps": "com.apple.Maps", "messages": "com.apple.MobileSMS",
    "phone": "com.apple.mobilephone", "contacts": "com.apple.MobileAddressBook",
    "mail": "com.apple.mobilemail", "calendar": "com.apple.mobilecal",
    "notes": "com.apple.mobilenotes", "reminders": "com.apple.reminders",
    "files": "com.apple.DocumentsApp", "app store": "com.apple.AppStore",
    "shortcuts": "com.apple.shortcuts", "clock": "com.apple.mobiletimer",
    "weather": "com.apple.weather", "music": "com.apple.Music",
    "health": "com.apple.Health", "fitness": "com.apple.Fitness",
    "find my": "com.apple.findmy", "home": "com.apple.Home",
    "watch": "com.apple.Bridge", "news": "com.apple.news",
    "podcasts": "com.apple.podcasts", "books": "com.apple.iBooks",
    "translate": "com.apple.Translate", "passwords": "com.apple.Passwords",
}

# The same apps on Apple Watch, which has its own builds of them.
WATCH_APPS = {
    "wallet": "com.apple.NanoPassbook", "apple pay": "com.apple.NanoPassbook",
    "workout": "com.apple.SessionTrackerApp", "heart rate": "com.apple.HeartRate",
    "music": "com.apple.NanoMusic", "maps": "com.apple.NanoMaps",
    "weather": "com.apple.NanoWeather", "messages": "com.apple.MobileSMS",
    "calendar": "com.apple.NanoCalendar", "reminders": "com.apple.NanoReminders",
    "settings": "com.apple.NanoSettings", "camera": "com.apple.NanoCamera",
    "camera remote": "com.apple.NanoCamera", "alarms": "com.apple.NanoAlarm",
    "timers": "com.apple.NanoTimer", "stopwatch": "com.apple.NanoStopwatch",
    "compass": "com.apple.NanoCompass", "photos": "com.apple.NanoPhotos",
    "find my": "com.apple.findmy", "home": "com.apple.Home",
}

# What a person calls a kind of device, to find theirs: "my watch". Said
# alone, it means their real one before a simulator of that name.
GENERIC = {"watch", "apple watch", "iphone", "phone", "ipad", "vision pro"}
FAMILIES = (("watch", "watchOS"), ("ipad", "iPadOS"), ("iphone", "iOS"),
            ("phone", "iOS"), ("vision", "xrOS"), ("tv", "tvOS"))


def is_watch(where: dict) -> bool:
    said = " ".join(str(where.get(k, "")) for k in ("platform", "type", "os", "runtime", "model", "name"))
    return "watch" in said.lower()


# What Flash may show on a device but never does itself: the user's own
# money and identity. It stops at that step and says what the user does.
HANDS_OFF = ("Never pay, add or remove a card, confirm Apple Pay, send money, "
             "or sign in: at that step, stop and tell the user what to do "
             "themselves (for a payment: the side button twice, Face ID or "
             "the passcode, then the top of the phone held near the reader).")


def bundle_for(app: str, watch: bool = False) -> str:
    """The bundle id APP means: a name from APPS (WATCH_APPS for an Apple
    Watch), or a bundle id as is."""

    name = " ".join(str(app or "").lower().split()).removesuffix(" app")
    if watch and name in WATCH_APPS:
        return WATCH_APPS[name]
    if name in APPS:
        return APPS[name]
    if "." in str(app):
        return str(app).strip()
    raise XcodeError(f"No built-in app called {app!r}: give its bundle id. "
                     f"Known: {', '.join(sorted(set(APPS) - {'apple pay'}))}.")


def device_hub() -> str:
    """Where Device Hub is (Xcode 27 and later), or "": the app that shows
    simulators and plugged-in devices, which replaced Simulator."""

    dev = os.environ.get("DEVELOPER_DIR", "")
    if not dev:
        try:
            dev = subprocess.run(["xcode-select", "-p"], capture_output=True,  # nosec B603 B607
                                 text=True, timeout=10, check=False).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            dev = ""
    if dev:
        app = Path(dev.removesuffix("/Contents/Developer")) / "Contents" / "Applications" / "DeviceHub.app"
        if app.is_dir():
            return str(app)
    try:
        found = subprocess.run(["mdfind", "kMDItemFSName == 'DeviceHub.app'"],  # nosec B603 B607
                               capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        found = ""
    return next((ln for ln in found.splitlines() if ln.strip()), "")
BUILD_SECONDS = 1200
QUICK_SECONDS = 60
DERIVED = Path.home() / ".flash" / "xcode" / "DerivedData"
MAX_ISSUES = 40


class XcodeError(RuntimeError):
    pass


# ----- running Apple's tools -----
# One place runs every command, so tests can stand in for it, and the
# agent's Stop can end a long build (the caller passes its own runner).
Runner = Callable[[list[str], int], tuple[str, str, int]]


TIMED_OUT = 124


def _plain_run(argv: list[str], seconds: int) -> tuple[str, str, int]:
    """Run ARGV for at most SECONDS. Past that it is ended and what it
    printed so far is kept (exit TIMED_OUT): how an app's console is
    watched for a while."""

    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,  # nosec B603
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=seconds)
        return out or "", err or "", proc.returncode
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); out, err = proc.communicate()
        return out or "", err or "", TIMED_OUT


def available() -> bool:
    """On a Mac with Xcode (not only the command line tools: xcodebuild
    needs the full Xcode)."""

    if sys.platform != "darwin" or not shutil.which("xcrun"):
        return False
    try:
        done = subprocess.run(["xcode-select", "-p"], capture_output=True,  # nosec B603 B607
                              text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and "CommandLineTools" not in done.stdout


# ----- reading what the tools say -----
ISSUE = re.compile(r"^(?P<file>/[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
                   r"(?P<kind>error|warning):\s*(?P<text>.+)$", re.M)
PLAIN_ERROR = re.compile(r"^(?:xcodebuild: )?error:\s*(?P<text>.+)$", re.M)
TEST_FAIL = re.compile(r"^(?P<where>.+?):(?P<line>\d+): error: -?\[?(?P<test>[^\]]+?)\]? : (?P<text>.+)$", re.M)
SWIFT_TEST_FAIL = re.compile(r"^.*Test (?P<test>[^ ]+) .*(?:failed|recorded an issue).*$", re.M)


def issues(log: str) -> dict:
    """A build log's errors and warnings, each once, with file and line;
    and errors xcodebuild gives without a file (a missing scheme, a
    signing problem)."""

    seen, errors, warnings = set(), [], []
    for m in ISSUE.finditer(log):
        key = (m["file"], m["line"], m["text"])
        if key in seen:
            continue
        seen.add(key)
        entry = f"{m['file']}:{m['line']}: {m['text'].strip()}"
        (errors if m["kind"] == "error" else warnings).append(entry)
    for m in PLAIN_ERROR.finditer(log):
        text = m["text"].strip()
        if text not in seen and not any(text in e for e in errors):
            seen.add(text)
            errors.append(text)
    return {"errors": errors[:MAX_ISSUES], "warnings": warnings[:MAX_ISSUES],
            "more_errors": max(0, len(errors) - MAX_ISSUES),
            "more_warnings": max(0, len(warnings) - MAX_ISSUES)}


def test_failures(log: str) -> list[str]:
    out = [f"{m['test'].strip()}: {m['text'].strip()} ({m['where']}:{m['line']})" for m in TEST_FAIL.finditer(log)]
    out += [m.group(0).strip() for m in SWIFT_TEST_FAIL.finditer(log)]
    return list(dict.fromkeys(out))[:MAX_ISSUES]


def simulators(listing: str) -> list[dict]:
    """simctl list devices available -j, flattened: name, udid, state,
    and the runtime ("iOS 26.0")."""

    data = json.loads(listing or "{}")
    out = []
    for runtime, devs in (data.get("devices") or {}).items():
        label = runtime.rsplit(".", 1)[-1].replace("-", " ", 1).replace("-", ".")
        for d in devs:
            if d.get("isAvailable", True):
                out.append({"name": d.get("name", ""), "udid": d.get("udid", ""),
                            "state": d.get("state", ""), "runtime": label})
    return out


def devices(listing: str) -> list[dict]:
    """devicectl list devices --json-output, flattened: name, id, model,
    OS, and how it is connected."""

    data = json.loads(listing or "{}")
    out = []
    for d in ((data.get("result") or {}).get("devices") or []):
        props, hw = d.get("deviceProperties") or {}, d.get("hardwareProperties") or {}
        conn = d.get("connectionProperties") or {}
        out.append({"name": props.get("name", ""), "id": d.get("identifier", ""),
                    "model": hw.get("marketingName") or hw.get("productType", ""),
                    "os": f"{hw.get('platform', '')} {props.get('osVersionNumber', '')}".strip(),
                    "platform": hw.get("platform", ""),
                    "type": hw.get("deviceType", ""),
                    "connected": conn.get("tunnelState", "") or conn.get("transportType", "")})
    return out


def crash_summary(text: str) -> str:
    """A crash report (.ips: a JSON header line, then JSON) cut to what
    finds the bug: the app, the exception and its reason, and the
    crashed thread's frames."""

    lines = text.split("\n", 1)
    try:
        head = json.loads(lines[0])
        body = json.loads(lines[1]) if len(lines) > 1 else {}
    except (ValueError, IndexError):
        return text[:3000]
    exc = body.get("exception") or {}
    out = [f"{head.get('app_name') or body.get('procName', '?')} {head.get('app_version', '')} "
           f"crashed {head.get('timestamp', '')}",
           f"Exception: {exc.get('type', '')} {exc.get('signal', '')} {exc.get('subtype', '')}".strip()]
    term = body.get("termination") or {}
    if term.get("indicator"):
        out.append(f"Termination: {term['indicator']}")
    for k in ("asi", "lastExceptionBacktrace"):
        if k == "asi" and body.get("asi"):
            for who, msgs in body["asi"].items():
                out.append(f"{who}: " + " ".join(msgs)[:600])
    images = body.get("usedImages") or []
    threads = body.get("threads") or []
    crashed = next((t for t in threads if t.get("triggered")), threads[0] if threads else None)
    if crashed:
        out.append(f"Crashed thread {threads.index(crashed)}" + (f" ({crashed['name']})" if crashed.get("name") else "") + ":")
        for i, f in enumerate((crashed.get("frames") or [])[:25]):
            img = images[f["imageIndex"]] if 0 <= f.get("imageIndex", -1) < len(images) else {}
            where = f.get("symbol") or f"+{f.get('imageOffset', 0)}"
            src = f" ({Path(f['sourceFile']).name}:{f.get('sourceLine', '')})" if f.get("sourceFile") else ""
            out.append(f"  {i:2} {img.get('name', '?')}  {where}{src}")
    return "\n".join(out)


# ----- the tool -----
class Xcode:
    def __init__(self, run: Optional[Runner] = None):
        self.run = run or _plain_run

    def _out(self, argv: list[str], seconds: int = QUICK_SECONDS) -> str:
        out, err, code = self.run(argv, seconds)
        if code:
            raise XcodeError((err or out).strip()[-1500:] or f"{argv[1] if len(argv) > 1 else argv[0]} failed ({code})")
        return out

    # what there is
    def sims(self) -> list[dict]:
        return simulators(self._out(["xcrun", "simctl", "list", "devices", "available", "-j"]))

    def phones(self) -> list[dict]:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            self.run(["xcrun", "devicectl", "list", "devices", "--json-output", path, "--quiet"], QUICK_SECONDS)
            return devices(Path(path).read_text() if os.path.exists(path) else "{}")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)

    def target(self, name: str = "") -> dict:
        """The simulator or device NAME means (a name, a UDID or id, or
        part of a name); with none, the booted simulator, else the newest
        iPhone one."""

        sims, want = self.sims(), (name or "").strip().lower()
        if want:
            phones = self.phones()
            for d in phones:
                if want in (d["id"].lower(), d["name"].lower()) or want in d["name"].lower():
                    return {"kind": "device", **d}
            plain = re.sub(r"^(?:my|the)\s+", "", want)
            hits = [s for s in sims if want in (s["udid"].lower(), s["name"].lower())] or \
                [s for s in sims if want in s["name"].lower()]
            if not hits or plain in GENERIC:
                mine = self._own(phones, want)
                if mine:
                    return mine
            if not hits:
                raise XcodeError(f"No simulator or device called {name!r}. Ask for status to see them.")
            hits.sort(key=lambda s: (s["state"] != "Booted", s["runtime"]), reverse=False)
            return {"kind": "simulator", **hits[0]}
        booted = [s for s in sims if s["state"] == "Booted"]
        if booted:
            return {"kind": "simulator", **booted[0]}
        phones = [s for s in sims if s["name"].startswith("iPhone")]
        if not phones:
            raise XcodeError("No simulators are installed: add one in Xcode (Window > Devices and Simulators).")
        phones.sort(key=lambda s: (s["runtime"], s["name"]))
        return {"kind": "simulator", **phones[-1]}

    @staticmethod
    def _own(phones: list[dict], want: str) -> Optional[dict]:
        """The user's own device of the kind WANT names ("my apple watch",
        "my phone"), a connected one first, or None."""

        for word, platform in FAMILIES:
            if word not in want:
                continue
            mine = [d for d in phones if platform.lower() in (d.get("platform") or d.get("os", "")).lower()
                    or (word == "watch" and is_watch(d))]
            if word in ("iphone", "phone"):
                mine = [d for d in mine if not is_watch(d) and "ipad" not in d.get("model", "").lower()]
            mine.sort(key=lambda d: d.get("connected") not in ("connected", "wired", "localNetwork"))
            return {"kind": "device", **mine[0]} if mine else None
        return None

    def status(self) -> str:
        path = self._out(["xcode-select", "-p"]).strip()
        version = " ".join(self._out(["xcodebuild", "-version"]).split())
        sims = self.sims()
        booted = [s for s in sims if s["state"] == "Booted"]
        lines = [f"{version} at {path}",
                 "Booted simulators: " + (", ".join(f"{s['name']} ({s['runtime']}, {s['udid']})" for s in booted) or "none")]
        names = sorted({f"{s['name']} ({s['runtime']})" for s in sims})
        lines.append(f"Simulators ({len(sims)}): " + ", ".join(names[:30]) + (" ..." if len(names) > 30 else ""))
        ph = self.phones()
        lines.append("Devices: " + (", ".join(f"{d['name']} ({d['model']}, {d['os']}, {d['connected'] or 'not connected'}, id {d['id']})" for d in ph) or "none paired"))
        return "\n".join(lines)

    # the project
    @staticmethod
    def _project_args(project: str) -> list[str]:
        p = Path(project).expanduser()
        if p.is_dir() and p.suffix not in (".xcodeproj", ".xcworkspace"):
            found = sorted(p.glob("*.xcworkspace")) or sorted(p.glob("*.xcodeproj"))
            if not found:
                if (p / "Package.swift").exists():
                    return []           # a Swift package: xcodebuild runs in its folder
                raise XcodeError(f"No .xcodeproj, .xcworkspace or Package.swift in {p}.")
            p = found[0]
        if p.suffix == ".xcworkspace":
            return ["-workspace", str(p)]
        if p.suffix == ".xcodeproj":
            return ["-project", str(p)]
        raise XcodeError(f"{project} is not an Xcode project, workspace or folder holding one.")

    def schemes(self, project: str) -> str:
        data = json.loads(self._out(["xcodebuild", "-list", "-json", *self._project_args(project)], 120) or "{}")
        info = data.get("workspace") or data.get("project") or {}
        return (f"Schemes: {', '.join(info.get('schemes') or []) or 'none'}\n"
                f"Targets: {', '.join(info.get('targets') or []) or '-'}\n"
                f"Configurations: {', '.join(info.get('configurations') or []) or '-'}")

    def _destination(self, where: dict) -> str:
        if where["kind"] == "device":
            return f"id={where['id']}"
        os_name = (where.get("runtime") or "iOS").split()[0]
        os_name = {"xrOS": "visionOS"}.get(os_name, os_name)
        return f"platform={os_name} Simulator,id={where['udid']}"

    def build(self, project: str, scheme: str, where: dict, configuration: str = "Debug", test: bool = False) -> dict:
        args = ["xcodebuild", *self._project_args(project), "-scheme", scheme, "-configuration", configuration,
                "-destination", self._destination(where), "-derivedDataPath", str(DERIVED / scheme),
                "test" if test else "build"]
        if where["kind"] == "device":
            args.append("-allowProvisioningUpdates")
        started = time.time()
        out, err, code = self.run(args, BUILD_SECONDS)
        log = out + "\n" + err
        found = issues(log)
        result = {"ok": code == 0, "seconds": round(time.time() - started), **found}
        if test:
            result["failures"] = test_failures(log)
            m = re.search(r"Executed (\d+) tests?, with (\d+) failures?", log)
            if m:
                result["tests"] = f"{m[1]} run, {m[2]} failed"
        if code == 0 and not test:
            result["app"] = self.app_path(project, scheme, where, configuration)
        if code and not found["errors"] and not result.get("failures"):
            result["tail"] = "\n".join(log.strip().splitlines()[-25:])
        return result

    def app_path(self, project: str, scheme: str, where: dict, configuration: str = "Debug") -> str:
        sdk = "iphoneos" if where["kind"] == "device" else "iphonesimulator"
        hits = glob.glob(str(DERIVED / scheme / "Build" / "Products" / f"{configuration}-{sdk}" / "*.app"))
        if not hits:
            hits = glob.glob(str(DERIVED / scheme / "Build" / "Products" / f"{configuration}-*" / "*.app"))
        return max(hits, key=os.path.getmtime) if hits else ""

    @staticmethod
    def bundle_id(app: str) -> str:
        plist = Path(app) / "Info.plist"
        if not plist.exists():
            return ""
        import plistlib
        with plist.open("rb") as f:
            return plistlib.load(f).get("CFBundleIdentifier", "")

    # the simulator
    def boot(self, where: dict) -> str:
        if where["kind"] != "simulator":
            return f"{where['name']} is a device: nothing to boot."
        if where["state"] != "Booted":
            out, err, code = self.run(["xcrun", "simctl", "boot", where["udid"]], 180)
            if code and "current state: Booted" not in err:
                raise XcodeError(err.strip()[-800:])
            self.run(["xcrun", "simctl", "bootstatus", where["udid"], "-b"], 300)
        shown = self.show(where)
        return f"{where['name']} ({where['runtime']}) is booted. {shown}"

    def install_and_launch(self, where: dict, app: str, bundle: str, console_seconds: int = 0, args: Optional[list] = None) -> str:
        args = [str(a) for a in (args or [])]
        if where["kind"] == "simulator":
            self.boot(where)
            if app:
                self._out(["xcrun", "simctl", "install", where["udid"], app], 300)
            if console_seconds:
                # the app's own stdout and stderr (print, NSLog) for a while, then it keeps running
                out, err, _ = self.run(["xcrun", "simctl", "launch", "--console-pty", "--terminate-running-process",
                                        where["udid"], bundle, *args], console_seconds)
                return (out + err).strip()
            return self._out(["xcrun", "simctl", "launch", "--terminate-running-process", where["udid"], bundle, *args]).strip()
        if app:
            self._out(["xcrun", "devicectl", "device", "install", "app", "--device", where["id"], app], 600)
        cmd = ["xcrun", "devicectl", "device", "process", "launch", "--device", where["id"], "--terminate-existing"]
        if console_seconds:
            out, err, _ = self.run(cmd + ["--console", bundle, *args], console_seconds)
            return (out + err).strip()
        return self._out(cmd + [bundle, *args], 120).strip()

    def stop(self, where: dict, bundle: str) -> str:
        if where["kind"] == "simulator":
            self.run(["xcrun", "simctl", "terminate", where["udid"], bundle], QUICK_SECONDS)
            return f"Stopped {bundle} on {where['name']}."
        out = self._out(["xcrun", "devicectl", "device", "info", "processes", "--device", where["id"]], QUICK_SECONDS)
        pid = next((ln.split()[0] for ln in out.splitlines() if bundle.split(".")[-1].lower() in ln.lower() and ln.split()[:1] and ln.split()[0].isdigit()), "")
        if not pid:
            return f"{bundle} is not running on {where['name']}."
        self._out(["xcrun", "devicectl", "device", "process", "terminate", "--device", where["id"], "--pid", pid])
        return f"Stopped {bundle} on {where['name']}."

    def logs(self, where: dict, process: str, minutes: float = 2, contains: str = "") -> str:
        if where["kind"] != "simulator":
            raise XcodeError("The system log is read from simulators; on a device, run the app with console_seconds to see its output.")
        pred = f'process == "{process}"' + (f' AND eventMessage CONTAINS[c] "{contains}"' if contains else "")
        out = self._out(["xcrun", "simctl", "spawn", where["udid"], "log", "show", "--style", "compact",
                         "--last", f"{max(1, int(minutes * 60))}s", "--predicate", pred], 120)
        lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("Timestamp")]
        return "\n".join(lines[-200:]) or f"Nothing logged by {process} in the last {minutes:g} min."

    def show(self, where: dict) -> str:
        """WHERE on the Mac's screen, to watch and work: in Device Hub,
        else (an older Xcode) Simulator or Xcode's device window."""

        ident = where.get("udid") or where.get("id", "")
        hub = self.hub()
        if hub:
            self.run(["open", hub], QUICK_SECONDS)
            self.run(["open", f"devices://device/open?id={ident}"], QUICK_SECONDS)
            return f"{where['name']} is showing in Device Hub."
        if where["kind"] == "simulator":
            self.run(["open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", ident], QUICK_SECONDS)
            return f"{where['name']} is showing in Simulator."
        self.devices_window()
        return f"{where['name']} is in Xcode's device window."

    hub = staticmethod(device_hub)

    def open_app(self, where: dict, app: str) -> str:
        """A built-in app (or any installed one, by bundle id) opened on
        WHERE, as it is, without closing it first."""

        bundle = bundle_for(app, watch=is_watch(where))
        if where["kind"] == "simulator":
            self.boot(where)
            self._out(["xcrun", "simctl", "launch", where["udid"], bundle])
        else:
            self._out(["xcrun", "devicectl", "device", "process", "launch", "--device", where["id"], bundle], 120)
            self.show(where)
        return f"Opened {app} ({bundle}) on {where['name']}."

    def _window_box(self) -> str:
        """Where Device Hub's front window is on screen, as screencapture's
        -R takes it, or "" when it has none."""

        out, _err, code = self.run(["osascript", "-e",
                                    'tell application "System Events" to tell (first process whose name is '
                                    '"DeviceHub") to get {position, size} of front window'], QUICK_SECONDS)
        nums = re.findall(r"-?\d+", out)
        return ",".join(nums[:4]) if code == 0 and len(nums) >= 4 else ""

    def screenshot(self, where: dict, path: str) -> str:
        if where["kind"] == "simulator":
            self._out(["xcrun", "simctl", "io", where["udid"], "screenshot", path], QUICK_SECONDS)
            return path
        # A device: its screen as Device Hub shows it.
        if not self.hub():
            raise XcodeError("A device's screen is seen through Device Hub (Xcode 27 or later); "
                             "with this Xcode, screenshots are of simulators.")
        self.show(where)
        time.sleep(1.0)
        box = self._window_box()
        if not box:
            raise XcodeError("Device Hub has no window to see: give Flash Screen Recording and "
                             "Accessibility in System Settings, Privacy and Security.")
        self._out(["screencapture", "-x", "-R", box, path], QUICK_SECONDS)
        return path

    def open_url(self, where: dict, url: str) -> str:
        if where["kind"] != "simulator":
            raise XcodeError("Links are opened in simulators.")
        self._out(["xcrun", "simctl", "openurl", where["udid"], url])
        return f"Opened {url} in {where['name']}."

    def appearance(self, where: dict, mode: str) -> str:
        if mode not in ("light", "dark"):
            raise XcodeError("appearance is light or dark.")
        self._out(["xcrun", "simctl", "ui", where["udid"], "appearance", mode])
        return f"{where['name']} is in {mode} mode."

    @staticmethod
    def crashes(process: str = "", newest: int = 1) -> str:
        roots = [Path.home() / "Library/Logs/DiagnosticReports", Path("/Library/Logs/DiagnosticReports")]
        found = []
        for root in roots:
            found += [p for p in root.glob("*.ips") if not process or p.name.lower().startswith(process.lower())]
        if not found:
            return "No crash reports" + (f" for {process}" if process else "") + "."
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return "\n\n".join(f"{p}\n" + crash_summary(p.read_text(errors="replace")) for p in found[:max(1, newest)])

    def backtrace(self, process: str) -> str:
        """Every thread's stack of a running app (a hang, a deadlock), by
        attaching lldb for a moment and letting it go."""

        out, err, code = self.run(["xcrun", "lldb", "--batch", "-o", f"process attach --name {process}",
                                   "-o", "thread backtrace all", "-o", "process detach"], 120)
        text = (out + err).strip()
        if code and "error:" in text:
            raise XcodeError(text[-1200:])
        keep = [ln for ln in text.splitlines() if ln.lstrip().startswith(("* thread", "thread #", "frame #", "  * thread"))]
        return "\n".join(keep[:300]) or text[-3000:]

    def devices_window(self) -> str:
        self.run(["open", "-a", "Xcode"], QUICK_SECONDS)
        self.run(["osascript", "-e", 'tell application "System Events" to keystroke "2" using {command down, shift down}'], QUICK_SECONDS)
        return "Opened Xcode's device window (Window > Devices and Simulators)."


def describe(result: dict) -> str:
    """A build or test result, said briefly."""

    out = [("Built" if result.get("ok") else "Build failed") + f" in {result.get('seconds', 0)} s."]
    if result.get("tests"):
        out[0] = f"Tests: {result['tests']}." if result.get("ok") else f"Tests failed: {result['tests']}."
    if result.get("errors"):
        out.append("Errors:\n" + "\n".join(f"  {e}" for e in result["errors"]) + (f"\n  ...and {result['more_errors']} more" if result.get("more_errors") else ""))
    if result.get("failures"):
        out.append("Failing tests:\n" + "\n".join(f"  {e}" for e in result["failures"]))
    if result.get("warnings"):
        out.append(f"{len(result['warnings'])} warning(s), first: {result['warnings'][0]}")
    if result.get("app"):
        out.append(f"App: {result['app']}")
    if result.get("tail"):
        out.append("Last lines:\n" + result["tail"])
    return "\n".join(out)
