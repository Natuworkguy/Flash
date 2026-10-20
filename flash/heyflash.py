"""Hey Flash with no page open: Flash listens on its own microphone.

With the switch on (Settings, Voice, or VOICE_WAKE_ALWAYS=1), the web
server listens for "Hey Flash" on the microphone of the computer it runs
on, for as long as it runs, whether or not a page is open. Said, it
holds a spoken conversation there and then: it listens, answers through
that computer's speakers, and listens again, until nobody speaks or
they say goodbye, as voice mode does in the page. The conversation is
kept as a chat, and extensions in the middle of voice mode (voicehooks)
take part in it as in the page's, told it comes from the "server".

A page that listens for "Hey Flash" itself, or holds voice mode, has the
microphone while it does: this stands aside, so one "Hey Flash" never
starts two conversations. Only the wake phrases are listened for, and
nothing heard is kept, as in the page.
"""

import array
import math
import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Optional

from . import voice, voicehooks
from .paths import ENV_PATH

SETTING = "VOICE_WAKE_ALWAYS"
SOURCE = "server"
# The wake recognizer's stream, beside the pages' own.
STREAM = "always"

# How often a listener that is switched off looks at the switch again,
# and how long it waits after the microphone could not be opened.
CHECK_SECONDS = 2.0
RETRY_SECONDS = 30.0
# A page heard from this recently, listening or in voice mode, has the
# microphone.
PAGE_SECONDS = 8.0
# The longest a conversation waits for Flash's answer, and the longest
# one turn of it listens.
REPLY_SECONDS = 600.0
TURN_SECONDS = 30.0
POLL_SECONDS = 0.25

WOKE = "woke"
# A microphone the system will not let Flash use opens all the same, on a
# Mac, and hears nothing at all: not even a quiet room is that quiet.
SILENT_BLOCKS = 50
SILENT = (
    "The microphone is only sending silence. On a Mac, allow the "
    "microphone for the app Flash runs in (System Settings, Privacy & "
    "Security, Microphone), then start Flash again."
)
WAITING = (
    "I need your answer before I can go on. It's waiting for you in the "
    "chat."
)
SLOW = "That is taking a while, so I'll leave the answer in the chat."
NOTHING = "I don't have anything to say to that."


def enabled() -> bool:
    return (os.getenv(SETTING) or "").strip().lower() in {
        "1", "on", "yes", "true",
    }


def set_enabled(on: bool) -> None:
    from .envfile import set_env_var

    os.environ[SETTING] = "1" if on else "0"
    set_env_var(ENV_PATH, SETTING, "1" if on else "0")


class Listener:
    """Listens for "Hey Flash" on this computer's microphone, on a
    thread of its own, and holds the conversation it starts.

    LISTEN, SPEAK, MICROPHONE and CHIME stand in for voice.listen,
    voice.speak, sounddevice's input stream and the chime, for tests.
    """

    def __init__(
        self,
        session: Any,
        listen: Optional[Callable] = None,
        speak: Optional[Callable] = None,
        microphone: Optional[Callable] = None,
        chime: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.session = session
        self._listen = listen or voice.listen
        self._speak = speak or voice.speak
        self._microphone = microphone or _microphone
        self._chime = chime or _chime
        self.listening = False
        self.talking = False
        self.problem = ""
        self._page = -PAGE_SECONDS
        self._stop = threading.Event()
        self._changed = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # From the server ------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._changed.set()

    def switched(self) -> None:
        """The switch was turned on or off: look at it now."""

        self._changed.set()

    def page_listening(self) -> None:
        """A page is listening for "Hey Flash", or in voice mode."""

        self._page = time.monotonic()

    def page_has_microphone(self) -> bool:
        """A page listening, or in voice mode: speaking, listening, or
        waiting on the answer to what it heard."""

        if time.monotonic() - self._page < PAGE_SECONDS:
            return True
        return any(chat.heard and (chat.busy or chat.queued)
                   for chat in list(self.session.chats.values()))

    def snapshot(self) -> dict:
        return {
            "on": enabled(), "listening": self.listening,
            "talking": self.talking, "problem": self.problem,
        }

    # Listening -------------------------------------------------------

    def _set(self, **changes: Any) -> None:
        before = self.snapshot()
        for name, value in changes.items():
            setattr(self, name, value)
        if self.snapshot() != before:
            self.session.hub.publish({"type": "status"})

    def _wait(self, seconds: float) -> None:
        self._changed.wait(seconds)
        self._changed.clear()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not enabled():
                self._set(problem="")
                self._wait(CHECK_SECONDS)
                continue
            why = self._hear_wake()
            if why == WOKE:
                self._converse()
            elif why:
                self._set(problem=why)
                self._wait(RETRY_SECONDS)

    def _hear_wake(self) -> str:
        """Listen until "Hey Flash": WOKE, or why it could not listen, or
        "" once switched off or stopped."""

        if not voice.listening_installed(voice.vosk_model()):
            return (
                "The listening model is not here yet. Use voice mode once, "
                "or download it under Voice models."
            )
        try:
            with self._microphone() as mic:
                self._set(listening=True, problem="")
                silent = 0
                while not self._stop.is_set() and enabled():
                    block = bytes(mic.read(voice.BLOCK_FRAMES)[0])
                    if voice._level(block) < 1:
                        silent += 1
                        if silent == SILENT_BLOCKS:
                            self._set(problem=SILENT)
                    elif silent:
                        silent = 0
                        if self.problem == SILENT:
                            self._set(problem="")
                    if self.page_has_microphone():
                        continue
                    woke, why = voice.hear_wake(STREAM, block)
                    if why:
                        return why
                    if woke and not voicehooks.ask(
                        "wake", source=SOURCE,
                    )["ignore"]:
                        return WOKE
                return ""
        except ImportError:
            return voice.INSTALL_HINT
        except Exception as exc:  # noqa: BLE001
            return f"{voice.MIC_HINT} ({exc})"
        finally:
            voice.hear_wake(STREAM, b"", end=True)
            self._set(listening=False)

    # The conversation -----------------------------------------------

    def _converse(self) -> None:
        talk = uuid.uuid4().hex[:10]

        def tell(event: str, **data: Any) -> None:
            voicehooks.tell(event, talk, source=SOURCE, **data)

        self._set(talking=True)
        tell("start", how="wake")
        # Nothing on a screen says it heard: a chime does, rising as it
        # starts to listen and falling once it stops.
        self._chime(True)
        chat = None
        try:
            while not self._stop.is_set():
                tell("state", state="listening")
                heard = {"words": ""}

                def words(text: str) -> None:
                    if not heard["words"]:
                        tell("state", state="hearing")
                    heard["words"] = text
                    tell("words", text=text)

                # A room that is never quiet (a video playing) would keep
                # the turn open: it ends once the words stop changing too.
                text, why = self._listen(lambda _state: None, words,
                                         settle=True, limit=TURN_SECONDS)
                if why:
                    self._set(problem=why)
                    return
                if not text or voice.is_exit_phrase(text):
                    return
                middle = voicehooks.ask("heard", talk, text=text,
                                        source=SOURCE)
                if middle["end"]:
                    return
                tell("state", state="thinking")
                last = False
                if middle["reply"] is not None:
                    reply = middle["reply"]
                else:
                    if chat is None:
                        chat = self.session.new_chat()
                    reply, last = self._answer(chat, middle["text"])
                reply = voicehooks.ask(
                    "reply", talk, text=voice.speakable(reply),
                    source=SOURCE,
                )["text"]
                tell("state", state="speaking")
                why, _interrupted = self._speak(reply)
                if why:
                    self._set(problem=why)
                    return
                if last:
                    return
        finally:
            self._chime(False)
            tell("end")
            self._set(talking=False)

    def _answer(self, chat: Any, text: str) -> tuple[str, bool]:
        """What Flash says to TEXT in CHAT, and whether the conversation
        ends there: Flash is waiting on an answer only the page can
        give, or took too long."""

        session = self.session
        before = len(chat.log)
        session.send(chat, text, heard=True)
        deadline = time.monotonic() + REPLY_SECONDS
        while time.monotonic() < deadline and not self._stop.is_set():
            time.sleep(POLL_SECONDS)
            if any(ask.chat == chat.id and not ask.done.is_set()
                   for ask in list(session.asks.values())):
                return WAITING, True
            if chat.busy or chat.queued:
                continue
            replies = [
                str(event.get("text") or "") for event in chat.log[before:]
                if event.get("type") == "assistant"
                and str(event.get("text") or "").strip()
            ]
            return (replies[-1], False) if replies else (NOTHING, False)
        return SLOW, True


def _microphone():
    import sounddevice

    return sounddevice.RawInputStream(
        samplerate=voice.SAMPLE_RATE,
        blocksize=voice.BLOCK_FRAMES,
        dtype="int16",
        channels=1,
    )


CHIME_RATE = 24000


def _chime(rising: bool) -> None:
    """Two soft notes through the speakers: up to start listening, down
    once done. Quietly nothing where there is nothing to play them on."""

    notes = (660.0, 880.0) if rising else (880.0, 660.0)
    pcm = array.array("h")
    length = int(CHIME_RATE * 0.09)
    for pitch in notes:
        for i in range(length):
            # Faded in and out, so neither note clicks.
            fade = min(1.0, i / 240, (length - i) / 240)
            pcm.append(int(6000 * fade * math.sin(
                2 * math.pi * pitch * i / CHIME_RATE)))
    try:
        import sounddevice

        with sounddevice.RawOutputStream(
            samplerate=CHIME_RATE, dtype="int16", channels=1,
        ) as out:
            out.write(pcm.tobytes())
    except Exception:  # noqa: BLE001
        # Only a nicety: the conversation goes on without it.
        return
