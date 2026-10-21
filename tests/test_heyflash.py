"""Tests for "Hey Flash" heard with no page open (heyflash)."""

import json
import threading
import time
from dataclasses import dataclass, field

import pytest
from dotenv import dotenv_values

from flash import heyflash, voice, web
from tests.test_voicehooks import middle, said
from tests.test_web import request, server  # noqa: F401  (a fixture)


@dataclass
class FakeChat:
    id: str
    log: list = field(default_factory=list)
    heard: bool = False
    busy: bool = False
    queued: bool = False


class FakeHub:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class FakeSession:
    """Answers each message with ANSWER(text), or holds it on an ask."""

    def __init__(self, answer=lambda text: f"You said {text}.", ask=False):
        self.hub = FakeHub()
        self.chats = {}
        self.asks = {}
        self.sent = []
        self._answer = answer
        self._ask = ask

    def new_chat(self):
        chat = FakeChat(id=f"c{len(self.chats)}")
        self.chats[chat.id] = chat
        return chat

    def send(self, chat, text, heard=False):
        self.sent.append((chat.id, text, heard))
        chat.log.append({"type": "user", "text": text})
        if self._ask:
            self.asks["a"] = web.Ask(id="a", chat=chat.id, question="Run it?")
            chat.busy = True
            return {}
        chat.log.append({"type": "assistant", "text": self._answer(text)})
        return {}


class Microphone:
    """Silence, a block at a time, counted."""

    def __init__(self):
        self.reads = 0
        self.opened = 0

    def __call__(self):
        self.opened += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, frames):
        self.reads += 1
        time.sleep(0.005)
        return b"\0\0" * frames, False


@pytest.fixture
def wakes(monkeypatch):
    """Hey Flash is heard on the third block after each WAKE.set()."""

    wake = threading.Event()
    blocks = {"n": 0}

    def hear_wake(stream, pcm, end=False):
        if end or not wake.is_set():
            return False, ""
        blocks["n"] += 1
        if blocks["n"] >= 3:
            wake.clear()
            blocks["n"] = 0
            return True, ""
        return False, ""

    monkeypatch.setattr(voice, "hear_wake", hear_wake)
    monkeypatch.setattr(voice, "listening_installed", lambda name: True)
    monkeypatch.setattr(heyflash, "POLL_SECONDS", 0.01)
    return wake


def listener(session, heard=(), mic=None):
    """A listener that hears each of HEARD in turn after waking, then
    silence, and keeps what it says."""

    queue = list(heard)
    spoken = []

    def listen(on_state, on_words=None, settle=False, limit=0):
        if not queue:
            return "", ""
        text = queue.pop(0)
        if on_words is not None:
            on_words(text.split()[0])
            on_words(text)
        return text, ""

    def speak(text):
        spoken.append(text)
        return "", False

    chimes = []
    found = heyflash.Listener(
        session, listen=listen, speak=speak, microphone=mic or Microphone(),
        chime=lambda rising: chimes.append("up" if rising else "down"),
        ping=lambda: chimes.append("ping"),
    )
    found.chimes = chimes
    return found, spoken


def until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


class TestSwitch:
    def test_off_unless_switched_on_and_kept(self, monkeypatch):
        assert heyflash.enabled() is False  # nosec B101

        heyflash.set_enabled(True)

        assert heyflash.enabled() is True  # nosec B101
        saved = dotenv_values(heyflash.ENV_PATH)
        assert saved[heyflash.SETTING] == "1"  # nosec B101

    def test_off_it_never_opens_the_microphone(self, wakes):
        mic = Microphone()
        found, _ = listener(FakeSession(), mic=mic)
        found.start()
        time.sleep(0.1)
        found.stop()

        assert mic.opened == 0  # nosec B101
        assert found.snapshot()["listening"] is False  # nosec B101


class TestConversation:
    def test_hey_flash_holds_a_spoken_conversation(self, wakes):
        heyflash.set_enabled(True)
        session = FakeSession()
        found, spoken = listener(
            session, heard=["what time is it", "thanks"],
        )
        found.start()
        until(lambda: found.listening)
        wakes.set()
        until(lambda: len(spoken) == 2)
        until(lambda: not found.talking)
        # It goes back to listening for the next "Hey Flash": looked at
        # before it is stopped, since a stopped one never listens.
        until(lambda: found.listening)
        found.stop()

        assert spoken == [  # nosec B101
            "You said what time is it.", "You said thanks.",
        ]
        # A chime as it starts to listen, a ping as each request is
        # heard, and a chime once it stops.
        assert found.chimes == ["up", "ping", "ping", "down"]  # nosec B101
        # One chat for the conversation, every message marked as heard.
        assert [s[0] for s in session.sent] == ["c0", "c0"]  # nosec B101
        assert all(s[2] for s in session.sent)  # nosec B101

    def test_stop_listening_ends_it_unanswered(self, wakes):
        heyflash.set_enabled(True)
        session = FakeSession()
        found, spoken = listener(session, heard=["stop listening"])
        found.start()
        until(lambda: found.listening)
        wakes.set()
        until(lambda: not wakes.is_set() and not found.talking)
        time.sleep(0.05)
        found.stop()

        assert spoken == []  # nosec B101
        assert session.sent == []  # nosec B101

    def test_waiting_on_an_answer_says_so_and_ends(self, wakes):
        heyflash.set_enabled(True)
        session = FakeSession(ask=True)
        found, spoken = listener(session, heard=["delete it", "more"])
        found.start()
        until(lambda: found.listening)
        wakes.set()
        until(lambda: spoken)
        until(lambda: not found.talking)
        found.stop()

        assert spoken == [heyflash.WAITING]  # nosec B101
        assert len(session.sent) == 1  # nosec B101

    def test_a_page_listening_has_the_microphone(self, wakes):
        heyflash.set_enabled(True)
        session = FakeSession()
        found, spoken = listener(session, heard=["hello"])
        found.page_listening()
        found.start()
        until(lambda: found.listening)
        wakes.set()
        time.sleep(0.2)

        assert spoken == [] and wakes.is_set()  # nosec B101

        # So does a page's voice mode, waiting on what it heard.
        found._page = -heyflash.PAGE_SECONDS
        chat = session.new_chat()
        chat.heard = chat.busy = True
        time.sleep(0.2)
        assert spoken == []  # nosec B101

        chat.busy = False
        until(lambda: spoken == ["You said hello."])
        found.stop()

    def test_extensions_are_in_the_middle(self, wakes, tmp_path,
                                          monkeypatch):
        monkeypatch.setenv("FLASH_TEST_VOICE", str(tmp_path / "said.jsonl"))
        middle(tmp_path, "a", {"heard": {"reply": "It is noon."},
                               "reply": {"text": "IT IS NOON."}})
        heyflash.set_enabled(True)
        session = FakeSession()
        found, spoken = listener(session, heard=["what time is it"])
        found.start()
        until(lambda: found.listening)
        wakes.set()
        until(lambda: spoken)
        until(lambda: not found.talking)
        found.stop()

        assert spoken == ["IT IS NOON."]  # nosec B101
        assert session.sent == []  # nosec B101
        seen = said(tmp_path, 12)
        events = [s["event"] for s in seen]
        assert events[0] == "wake" and "start" in events  # nosec B101
        assert events[-1] == "end"  # nosec B101
        assert {s["source"] for s in seen} == {"server"}  # nosec B101

    def test_no_microphone_is_said_once_and_tried_again(self, wakes):
        heyflash.set_enabled(True)

        def broken():
            raise OSError("no input device")

        found, _ = listener(FakeSession(), mic=broken)
        found.start()
        until(lambda: found.problem)
        found.stop()

        assert "no input device" in found.problem  # nosec B101
        assert found.listening is False  # nosec B101


class Noisy(Microphone):
    def __init__(self):
        super().__init__()
        self.sound = False

    def read(self, frames):
        block, flag = super().read(frames)
        return (b"\x10\x00" * frames if self.sound else block), flag


class TestSilence:
    def test_a_microphone_sending_only_silence_is_said(
            self, wakes, monkeypatch):
        monkeypatch.setattr(heyflash, "SILENT_BLOCKS", 5)
        heyflash.set_enabled(True)
        mic = Noisy()
        found, _ = listener(FakeSession(), mic=mic)
        found.start()
        until(lambda: found.problem == heyflash.SILENT)

        assert found.listening  # nosec B101

        mic.sound = True
        until(lambda: found.problem == "")
        found.stop()


class TestThePage:
    def test_the_switch_and_how_it_stands(self, server):  # noqa: F811
        status, body = request(server, "POST", "/api/command",
                               {"name": "wake-always", "arg": "on"})
        assert status == 200  # nosec B101
        assert json.loads(body)["on"] is True  # nosec B101
        assert heyflash.enabled()  # nosec B101

        state = server.session.state(lite=True)["status"]
        assert state["wake_always"]["on"] is True  # nosec B101

        status, body = request(server, "POST", "/api/command",
                               {"name": "wake-always", "arg": "lunch"})
        assert status == 400  # nosec B101

    def test_a_page_listening_is_noticed(self, server,  # noqa: F811
                                         monkeypatch):
        monkeypatch.setattr(web.voice, "hear_wake",
                            lambda stream, pcm, end=False: (False, ""))
        assert not server.session.always.page_has_microphone()  # nosec

        request(server, "POST", "/api/voice/wake", {"id": "w", "data": ""})

        assert server.session.always.page_has_microphone()  # nosec B101

    def test_the_sensitivity_slider(self, server):  # noqa: F811
        status, body = request(server, "POST", "/api/command",
                               {"name": "wake-sensitivity", "arg": "25"})
        assert status == 200  # nosec B101
        assert json.loads(body)["sensitivity"] == 25  # nosec B101
        assert voice.wake_sensitivity() == 25  # nosec B101
        assert dotenv_values(heyflash.ENV_PATH)[  # nosec B101
            heyflash.SENSITIVITY_SETTING] == "25"
        state = server.session.state(lite=True)["status"]
        assert state["wake_sensitivity"] == 25  # nosec B101

        for bad in ("150", "loud"):
            status, _ = request(server, "POST", "/api/command",
                                {"name": "wake-sensitivity", "arg": bad})
            assert status == 400  # nosec B101


def share(**shared):
    path = heyflash._shared_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(shared), encoding="utf-8")


class TestAcrossFlashes:
    @pytest.fixture(autouse=True)
    def quick(self, monkeypatch):
        monkeypatch.setattr(heyflash, "BEAT_SECONDS", 0.05)
        monkeypatch.setattr(heyflash, "CHECK_SECONDS", 0.05)

    def test_the_first_listener_still_going_has_the_microphone(self):
        import os

        now = time.time()
        other = os.getppid()
        shared = {"listeners": {
            str(os.getpid()): {"since": now, "at": now},
            str(other): {"since": now - 60, "at": now},
            "999999": {"since": now - 600, "at": now},  # long gone
            "3": {"since": now - 900, "at": now - 60},  # gone quiet
        }}

        assert heyflash.first_listener(shared) == other  # nosec B101

    def test_another_flash_listening_first_keeps_this_one_out(self, wakes):
        import os

        now = time.time()
        share(listeners={str(os.getppid()): {"since": now - 60, "at": now}})
        mic = Microphone()
        found, _ = listener(FakeSession(), mic=mic)
        found.always = True
        found.start()
        time.sleep(0.3)

        assert mic.opened == 0 and not found.listening  # nosec B101

        share()  # it stopped
        until(lambda: found.listening)
        # And now this one says it is listening, for the others.
        until(lambda: str(os.getpid()) in (
            heyflash._read_shared().get("listeners") or {}))
        found.stop()
        assert str(os.getpid()) not in (  # nosec B101
            heyflash._read_shared().get("listeners") or {})

    def test_a_page_listening_in_another_flash_keeps_it_out(self, wakes):
        share(page=time.time() + 60)
        mic = Microphone()
        found, _ = listener(FakeSession(), mic=mic)
        found.always = True
        found.start()
        time.sleep(0.3)
        found.stop()

        assert mic.opened == 0  # nosec B101

    def test_a_page_here_is_told_to_the_others(self, wakes):
        found, _ = listener(FakeSession())
        found.page_listening()
        found.start()
        until(lambda: heyflash.page_elsewhere())
        found.stop()

    def test_flash_listen_goes_when_its_service_does(self, monkeypatch):
        started = []

        class Quiet:
            problem = ""

            def __init__(self, session, **_):
                started.append(_)

            def start(self):
                pass

            def stop(self):
                started.append("stopped")

        monkeypatch.setattr(heyflash, "Listener", Quiet)
        monkeypatch.setattr(heyflash, "WANTED_SECONDS", 0)
        monkeypatch.setattr(heyflash, "REPORT_SECONDS", 0.01)
        asked = []

        heyflash.serve(wanted=lambda: asked.append(1) or len(asked) < 3)

        # The session makes one of its own, switch and all: the service's
        # is the one that always listens.
        assert {"always": True, "prepare": None} in started  # nosec B101
        assert started[-1] == "stopped" and len(asked) == 3  # nosec B101


def test_the_ping_rings_and_fades():
    sound = heyflash.ping_sound()
    rate = heyflash.CHIME_RATE

    def loudest(start, end):
        return max(abs(v) for v in sound[int(rate * start):int(rate * end)])

    assert 0 < loudest(0.0, 0.05) <= 32767  # nosec B101
    # Rung out by the end, not cut off.
    assert loudest(0.0, 0.05) > 20 * loudest(0.5, 0.57)  # nosec B101


def test_flash_can_end_the_conversation_itself(wakes):
    heyflash.set_enabled(True)

    class Ending(FakeSession):
        def send(self, chat, text, heard=False):
            chat.voice_ended = "bye" in text
            return super().send(chat, text, heard)

    session = Ending()
    found, spoken = listener(session, heard=["ok bye", "never heard"])
    found.start()
    until(lambda: found.listening)
    wakes.set()
    until(lambda: spoken)
    until(lambda: not found.talking)
    found.stop()

    # It said its goodbye, and listened no more.
    assert spoken == ["You said ok bye."]  # nosec B101
    assert len(session.sent) == 1  # nosec B101
