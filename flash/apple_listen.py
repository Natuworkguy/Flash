"""Hearing with this Mac's own speech recognition: the engine behind dictation and Siri, on the Mac itself where it
can (macOS 13 and later, for the languages it has downloaded), with punctuation.

macOS lets a program use it only if it is an app that says why it wants to listen, so Flash builds a small one
(apple_listen.swift, with Xcode's command line tools), signs it for this Mac alone, and keeps it in Flash's folder.
Each time something is said, the recording goes to it in a file and it is opened with `open`, as an app of its own:
macOS then asks about the app, once, rather than about whatever terminal Flash runs in.

A listening model named "apple" picks it in Settings. It hears what was said once the speaker stops, so there are
no words on screen while they talk.
"""

from __future__ import annotations

import hashlib
import json
import platform
import plistlib
import shutil
import subprocess  # nosec B404 -- our own helper and macOS's own tools, fixed arguments
import tempfile
import threading
import wave
from pathlib import Path

from . import siri

NAME = "apple"
LABEL = "Apple, built into this Mac: on the Mac itself, with punctuation"
SOURCE = Path(__file__).with_name("apple_listen.swift")
BUNDLE_ID = "local.flash.listen"
HEAR_SECONDS = 100
BUILD_SECONDS = 300

_lock = threading.Lock()


class AppleListenError(Exception):
    pass


def is_apple(name: str) -> bool:
    return str(name or "") == NAME


def available() -> bool:
    """On a Mac with the command line tools to build the helper."""

    return platform.system() == "Darwin" and siri.tools_installed()


def _home() -> Path:
    from .paths import FLASH_DIR

    return Path(FLASH_DIR) / "AppleListen.app"


def _source_tag() -> str:
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:16]


def app() -> Path:
    """The helper app, built (or built again, after the source changed) first."""

    if not available():
        raise AppleListenError(siri.NO_TOOLS if platform.system() == "Darwin"
                               else "This Mac's speech recognition is only on a Mac.")
    home = _home()
    exe = home / "Contents" / "MacOS" / "AppleListen"
    stamp = home / "Contents" / "Resources" / "source"
    with _lock:
        if exe.is_file() and stamp.is_file() and stamp.read_text() == _source_tag():
            return home
        shutil.rmtree(home, ignore_errors=True)
        (home / "Contents" / "MacOS").mkdir(parents=True)
        (home / "Contents" / "Resources").mkdir(parents=True)
        info = {
            "CFBundleIdentifier": BUNDLE_ID, "CFBundleName": "Flash Listening",
            "CFBundleExecutable": "AppleListen", "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0", "LSUIElement": True,
            "NSSpeechRecognitionUsageDescription":
                "Flash hears what you say to it with this Mac's own speech recognition.",
        }
        (home / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
        swiftc = shutil.which("swiftc") or "swiftc"
        built = subprocess.run(  # nosec B603 -- our own source
            [swiftc, "-O", str(SOURCE), "-o", str(exe)], capture_output=True, text=True,
            timeout=BUILD_SECONDS, check=False)
        if built.returncode:
            shutil.rmtree(home, ignore_errors=True)
            said = (built.stderr or built.stdout).strip().splitlines()
            raise AppleListenError("Could not build the listening helper"
                                   + (f": {said[-1]}" if said else "."))
        # signed for this Mac alone, so macOS can remember what it was allowed
        subprocess.run(["codesign", "--force", "--sign", "-", str(home)],  # nosec B603 B607
                       capture_output=True, timeout=60, check=False)
        stamp.write_text(_source_tag())
        return home


def transcribe(pcm: bytes, rate: int = 16000, language: str = "en-US") -> str:
    """What was said in PCM (mono 16-bit at RATE)."""

    if not pcm:
        return ""
    helper = app()
    with tempfile.TemporaryDirectory(prefix="flash-listen-") as tmp:
        wav, out = Path(tmp) / "said.wav", Path(tmp) / "heard.json"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        try:
            subprocess.run(  # nosec B603 B607 -- macOS's own open, our own app
                ["open", "-n", "-W", "-g", "-j", "-a", str(helper), "--args", str(wav), str(out), language],
                capture_output=True, timeout=HEAR_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            raise AppleListenError("This Mac's speech recognition took too long.") from None
        try:
            said = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AppleListenError("The listening helper gave no answer.") from None
    if not said.get("ok"):
        raise AppleListenError(str(said.get("error") or "This Mac could not hear it."))
    return str(said.get("text") or "")


class AppleHearing:
    """Hearing with the Mac's own recognizer: the audio kept as it comes and heard whole once the speaker stops."""

    def __init__(self, language: str = "en-US") -> None:
        self._pcm = bytearray()
        self.language = language
        self.error = ""

    def feed(self, pcm: bytes) -> str:
        self._pcm += pcm
        return ""

    def words(self) -> str:
        return ""

    def pieces(self) -> list[str]:
        return []

    def ended(self) -> list[str]:
        return [self.final()]

    def final(self) -> str:
        try:
            return transcribe(bytes(self._pcm), language=self.language)
        except AppleListenError as exc:
            self.error = str(exc)
            return ""
        finally:
            self._pcm = bytearray()

    def close(self) -> None:
        self._pcm = bytearray()
