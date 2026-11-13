# pylint: disable=C0114,C0115,C0116

import io
import wave

import pytest
from dotenv import dotenv_values

from flash import calls, tools


@pytest.fixture(autouse=True)
def _no_timing_log(monkeypatch):
    # The real calls/timing.log is for real calls only.
    monkeypatch.setattr(calls, "_mark", lambda step, since: None)


@pytest.fixture(autouse=True)
def _own_greeting(monkeypatch):
    # Most tests here are about the talk itself, with the model's own first
    # words; the greeting tests below set the mode they need.
    monkeypatch.setenv("CALL_GREETING_MODE", "own")
    monkeypatch.delenv("CALL_GREETING", raising=False)


class FakeLine:
    def __init__(self, heard):
        self.heard = list(heard)
        self.said = []

    def hear(self, longest=calls.QUIET_SECONDS, stopping=lambda: False):
        return self.heard.pop(0) if self.heard else ""

    def say(self, text):
        self.said.append(text)


def test_a_call_flash_places_goes_to_its_goal_and_hangs_up():
    call = calls.Call(who="John Doe", number="+15550102000",
                      goal="tell him I'm running late", incoming=False)
    line = FakeLine(["Oh no worries, see you soon."])
    replies = iter([
        "Hi John, this is Flash, an automated assistant calling for Sam. "
        "Sam is running about fifteen minutes late.",
        "Thanks, I'll let Sam know. Goodbye! [hang up]",
    ])
    asked = []

    def model(messages):
        asked.append(messages)
        return next(replies)

    calls.talk(call, line, "Sam", model)

    assert [who for who, _ in call.lines] == [
        "Flash", "John Doe", "Flash"]
    assert line.said[-1] == "Thanks, I'll let Sam know. Goodbye!"
    assert call.ended == "the call was done"
    # It is told who it is, who for, and never to pass as them.
    system = asked[0][0]["content"]
    assert "automated assistant" in system and "Never claim to be Sam" \
        in system
    assert "running late" in system


def test_a_quiet_line_ends_the_call():
    call = calls.Call(who="John", number="1", goal="ask", incoming=True)
    line = FakeLine([])
    calls.talk(call, line, "Sam", lambda m: "Hi, Flash here for Sam.")
    assert call.ended == "the other side went quiet"
    assert line.said == ["Hi, Flash here for Sam."]


def test_stop_hangs_up():
    call = calls.Call(who="John", number="1", goal="ask", incoming=True)
    calls.talk(call, FakeLine(["hi"]), "Sam", lambda m: "x",
               stopping=lambda: True)
    assert call.ended == "you stopped it"


def test_calls_are_ultraauto_only(monkeypatch):
    monkeypatch.setattr(tools, "ultra_auto", lambda: False)
    assert "UltraAuto" in tools.phone_call("call", "John", "say hi")


def test_what_is_missing_is_said_with_the_command(monkeypatch):
    monkeypatch.setattr(calls.computer, "system", lambda: "mac")
    monkeypatch.setattr(calls, "_devices", lambda: set())
    assert calls.INSTALL in calls.missing()
    monkeypatch.setattr(calls.computer, "system", lambda: "linux")
    assert "only" in calls.missing()


def test_the_tool_is_offered_only_in_ultraauto_on_a_mac(monkeypatch):
    monkeypatch.setattr(tools.computer, "system", lambda: "mac")
    monkeypatch.setattr(tools, "ultra_auto", lambda: True)
    names = {t["function"]["name"] for t in tools.available_tools()}
    assert "phone_call" in names
    monkeypatch.setattr(tools, "ultra_auto", lambda: False)
    names = {t["function"]["name"] for t in tools.available_tools()}
    assert "phone_call" not in names


def test_an_email_only_contact_is_called_with_facetime(monkeypatch):
    monkeypatch.setattr(calls.computer, "recipient",
                        lambda who: ("Ann", "ann@example.com"))
    with pytest.raises(calls.CallError, match="FaceTime Audio"):
        calls._number_of("Ann")
    assert calls._number_of("Ann", facetime=True)[1] == "ann@example.com"


def test_facetime_audio_dials_its_own_way(monkeypatch):
    ran = []
    monkeypatch.setattr(calls.computer, "_run", lambda argv, **k: ran.append(
        argv) or "")
    calls._dial("+15550102000", calls.FACETIME)
    calls._dial("+15550102000", calls.PHONE)
    assert [a for a in ran if a[0] == "open"] == [
        ["open", "facetime-audio:+15550102000"],
        ["open", "tel:+15550102000"]]
    # And the app it brings up is hidden again each time.
    assert [a[0] for a in ran] == ["open", "osascript"] * 2


def test_the_transcript_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(calls, "CALLS_DIR", tmp_path)
    call = calls.Call(who="John", number="1", goal="say hi", incoming=False,
                      lines=[("Flash", "Hi"), ("John", "Hey")],
                      ended="the call was done")
    path = calls._keep(call)
    assert "Flash: Hi\nJohn: Hey" in path.read_text(encoding="utf-8")
    assert "ended: the call was done" in calls.summary(call, path)


def test_flash_waits_for_the_call_to_be_picked_up(monkeypatch):
    states = iter([{"live": True, "connected": False}] * 2
                  + [{"live": True, "connected": True}])
    monkeypatch.setattr(calls, "_state", lambda: next(states))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    call = calls.Call(who="John", number="1", goal="ask", incoming=False)
    assert calls._picked_up(call, lambda: False)


def test_a_pick_up_is_seen_when_the_ringing_banner_goes(monkeypatch):
    # No timer on some Macs: the "FaceTime Audio…" going is the answer.
    states = iter([{"live": True, "connected": False, "ringing": True}] * 2
                  + [{"live": True, "connected": False, "ringing": False}])
    monkeypatch.setattr(calls, "_state", lambda: next(states))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    call = calls.Call(who="John", number="1", goal="ask", incoming=False)
    assert calls._picked_up(call, lambda: False)


def test_a_call_nobody_picks_up_ends_without_talking(monkeypatch):
    states = iter([{"live": True, "connected": False}]
                  + [{"live": False, "connected": False}] * calls.GONE_LOOKS)
    monkeypatch.setattr(calls, "_state", lambda: next(states))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    call = calls.Call(who="John", number="1", goal="ask", incoming=False)
    assert not calls._picked_up(call, lambda: False)
    assert call.ended == "they didn't pick up"


def test_an_unconfirmed_call_says_why_and_never_talks(monkeypatch):
    monkeypatch.setattr(calls, "missing", lambda: "")
    monkeypatch.setattr(calls, "_number_of", lambda who, facetime: (
        "John", "+15550102000"))
    monkeypatch.setattr(calls, "_switch", lambda kind, name="": "")
    monkeypatch.setattr(calls, "_dial", lambda handle, via: None)
    monkeypatch.setattr(calls, "_press", lambda names, want="": "")
    monkeypatch.setattr(calls, "_state", lambda: {"live": False,
                                                  "connected": False})
    monkeypatch.setattr(calls, "_hang_up", lambda: None)
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    ticks = iter(range(0, 1000, 5))
    monkeypatch.setattr(calls.time, "monotonic", lambda: next(ticks))

    def no_line():
        raise AssertionError("it listened on a call that never started")

    monkeypatch.setattr(calls, "Line", no_line)
    with pytest.raises(calls.CallError, match="press Call"):
        calls.place("John", "say hi", "Sam", lambda m: "x")


def test_they_hanging_up_ends_the_call():
    on = calls._StillOn(lambda: False, every=0.01,
                        look=lambda: {"live": False, "connected": False})
    try:
        for _ in range(200):
            if on():
                break
            calls.time.sleep(0.01)
        call = calls.Call(who="John", number="1", goal="ask", incoming=True)
        calls.talk(call, FakeLine(["hi"]), "Sam", lambda m: "x", stopping=on)
        assert call.ended == "they hung up"
    finally:
        on.close()


class FakeStream:
    def __init__(self, levels):
        self.levels = iter(levels)

    def read(self, frames):
        import array
        level = next(self.levels, 0)
        return array.array("h", [level] * frames).tobytes(), False


def test_a_noisy_line_is_not_heard_as_endless_talk(monkeypatch):
    line = calls.Line.__new__(calls.Line)
    line.device_rate = 1000
    # Steady hiss loud enough for the fixed bar, then a voice, then hiss.
    line.stream = FakeStream([700] * 30 + [6000] * 10 + [700] * 2000)
    monkeypatch.setattr(calls.voice, "silence_threshold", lambda: 500.0)
    monkeypatch.setattr(calls.voice, "transcribe", lambda pcm: (
        f"{len(pcm)}", ""))
    # About the voice and the pause after it, not thirty seconds of hiss.
    assert int(line.hear(longest=60)) < calls.RATE * 2 * 4


def test_the_wait_for_silence_sets_where_a_turn_ends(monkeypatch):
    # A voice, a one-second pause, the voice again, then quiet.
    monkeypatch.setattr(calls.voice, "silence_threshold", lambda: 500.0)
    monkeypatch.setattr(calls.voice, "transcribe", lambda pcm: (
        f"{len(pcm)}", ""))
    blocks = int(1.0 / calls.BLOCK)
    levels = ([0] * 30 + [6000] * 10 + [0] * blocks + [6000] * 10
              + [0] * 2000)

    def heard(seconds):
        monkeypatch.setenv("CALL_SILENCE_SECONDS", seconds)
        line = calls.Line.__new__(calls.Line)
        line.device_rate = 1000
        line.stream = FakeStream(levels)
        return int(line.hear(longest=60))

    # Quick: the pause ends the turn. Patient: both halves are one turn.
    assert heard("0.5") < heard("2")


def test_the_wait_for_silence_is_kept_and_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(calls, "ENV_PATH", tmp_path / ".flash.env")
    monkeypatch.delenv("CALL_SILENCE_SECONDS", raising=False)
    assert calls.pause_seconds() == calls.PAUSE_SECONDS
    assert calls.set_pause_seconds("1.5")["silence"] == 1.5
    assert "CALL_SILENCE_SECONDS=1.5" in (tmp_path / ".flash.env").read_text(
            encoding="utf-8")
    for wrong in ("loud", "0", "10"):
        with pytest.raises(ValueError):
            calls.set_pause_seconds(wrong)
    monkeypatch.setenv("CALL_SILENCE_SECONDS", "99")
    assert calls.pause_seconds() == calls.PAUSE_MOST
    monkeypatch.setenv("CALL_SILENCE_SECONDS", "nonsense")
    assert calls.pause_seconds() == calls.PAUSE_SECONDS


def test_one_turn_is_never_listened_to_forever(monkeypatch):
    line = calls.Line.__new__(calls.Line)
    line.device_rate = 1000
    line.stream = FakeStream([6000] * 100000)
    monkeypatch.setattr(calls.voice, "silence_threshold", lambda: 500.0)
    monkeypatch.setattr(calls.voice, "transcribe", lambda pcm: (
        f"{len(pcm)}", ""))
    assert line.hear(longest=5) != ""


def test_no_accessibility_access_is_said_at_once(monkeypatch):
    def refused(argv, given="", timeout=0):
        raise calls.computer.ComputerError(
            "osascript failed: osascript is not allowed assistive access. "
            "(-25211)")

    monkeypatch.setattr(calls.computer, "_run", refused)
    with pytest.raises(calls.CallError, match="Accessibility"):
        calls._press(["Call"])


def test_device_names_come_from_settings(monkeypatch):
    monkeypatch.delenv("CALL_MIC_DEVICE", raising=False)
    monkeypatch.delenv("CALL_SPEAKER_DEVICE", raising=False)
    assert (calls.to_call(), calls.from_call()) == (
        "BlackHole 2ch", "BlackHole 16ch")
    monkeypatch.setenv("CALL_MIC_DEVICE", "Loopback Mic")
    monkeypatch.setenv("CALL_SPEAKER_DEVICE", "Loopback Speaker")
    assert (calls.to_call(), calls.from_call()) == (
        "Loopback Mic", "Loopback Speaker")


def test_joining_a_call_talks_and_never_switches_or_hangs_up(monkeypatch):
    monkeypatch.setattr(calls, "missing", lambda: "")
    line = FakeLine(["Hi Flash!"])
    line.closed = False
    line.close = lambda: setattr(line, "closed", True)
    monkeypatch.setattr(calls, "Line", lambda: line)

    def never(*a, **k):
        raise AssertionError("a joined call must stay the user's")

    monkeypatch.setattr(calls, "_switch", never)
    monkeypatch.setattr(calls, "_hang_up", never)
    replies = iter(["Hi, I'm Flash, an automated assistant for Sam.",
                    "Nice to meet you, bye! [hang up]"])
    asked = []

    def model(messages):
        asked.append(messages)
        return next(replies)

    call = calls.join("say hello", "Sam", model, who="Alex")
    assert call.joined and call.ended == "the call was done"
    assert line.said[-1] == "Nice to meet you, bye!"
    assert line.closed
    assert "brought you into a call" in asked[0][0]["content"]
    assert "left the call with Alex" in calls.summary(call, "x.md")


def test_joining_without_the_devices_says_how_to_get_them(monkeypatch):
    monkeypatch.setattr(calls, "missing", lambda: "install them")
    with pytest.raises(calls.CallError, match="install them"):
        calls.join("say hi", "Sam", lambda m: "x")


def test_the_tool_joins_without_a_name(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "ultra_auto", lambda: True)
    monkeypatch.setattr(calls, "missing", lambda: "")
    monkeypatch.setattr(calls, "CALLS_DIR", tmp_path)
    monkeypatch.setattr(tools, "_call_user", lambda: "Sam")
    seen = {}

    def join(goal, user, ask, who="", stopping=lambda: False, owner=""):
        seen.update(goal=goal, who=who)
        return calls.Call(who="the people on the call", number="",
                          goal=goal, incoming=True, joined=True,
                          ended="you stopped it")

    monkeypatch.setattr(calls, "join", join)
    result = tools.phone_call(action="join", goal="say hi", ready=True)
    assert seen == {"goal": "say hi", "who": ""}
    assert "left the call" in result


def test_each_turn_goes_to_the_live_transcript_as_it_happens(
        monkeypatch, tmp_path):
    monkeypatch.setattr(calls, "CALLS_DIR", tmp_path)
    shown = []
    monkeypatch.setattr(calls, "tool_result",
                        lambda text, style=None: shown.append(text))
    call = calls.Call(who="Alex", number="", goal="say hi",
                      incoming=False)
    call.say("Alex", "Hello?")
    call.say("Flash", "Hi Alex.")
    assert call.lines == [("Alex", "Hello?"), ("Flash", "Hi Alex.")]
    assert shown[0].endswith("Alex: Hello?")
    assert shown[1].endswith("Flash: Hi Alex.")
    kept = call.live.read_text(encoding="utf-8").splitlines()
    assert call.live.parent == tmp_path
    assert kept[0].endswith("Alex: Hello?")
    assert kept[1].endswith("Flash: Hi Alex.")


def test_the_monitor_never_feeds_the_call_back(monkeypatch):
    monkeypatch.delenv("CALL_MIC_DEVICE", raising=False)
    monkeypatch.delenv("CALL_SPEAKER_DEVICE", raising=False)
    monkeypatch.setenv("CALL_MONITOR_DEVICE", "MacBook Speakers")
    assert calls.monitor() == "MacBook Speakers"
    monkeypatch.setenv("CALL_MONITOR", "off")
    assert calls.monitor() == ""
    monkeypatch.delenv("CALL_MONITOR")
    for loop in ("BlackHole 16ch", "BlackHole 2ch"):
        monkeypatch.setenv("CALL_MONITOR_DEVICE", loop)
        assert calls.monitor() == ""
    monkeypatch.delenv("CALL_MONITOR_DEVICE")
    monkeypatch.setattr(calls, "_speakers", "Headphones")
    assert calls.monitor() == "Headphones"


def test_settings_set_the_monitor(monkeypatch):
    for name in ("CALL_MONITOR", "CALL_MONITOR_DEVICE", "CALL_MIC_DEVICE",
                 "CALL_SPEAKER_DEVICE"):
        monkeypatch.setenv(name, "")
    devices = [
        {"name": "MacBook Speakers", "max_output_channels": 2},
        {"name": "MacBook Microphone", "max_output_channels": 0},
        {"name": "BlackHole 2ch", "max_output_channels": 2},
        {"name": "BlackHole 16ch", "max_output_channels": 16},
    ]
    fake = type("SD", (), {"query_devices": staticmethod(lambda: devices)})
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake)

    shown = calls.monitor_settings()
    assert shown == {"on": True, "device": "",  # nosec B101
                     "devices": ["MacBook Speakers"]}
    assert calls.set_monitor(on=False)["on"] is False  # nosec B101
    assert calls.set_monitor(device="MacBook Speakers")[  # nosec B101
        "device"] == "MacBook Speakers"
    saved = dotenv_values(calls.ENV_PATH)
    assert saved["CALL_MONITOR"] == "off"  # nosec B101
    assert saved["CALL_MONITOR_DEVICE"] == "MacBook Speakers"  # nosec B101
    for loop in ("BlackHole 2ch", "BlackHole 16ch"):
        with pytest.raises(ValueError):
            calls.set_monitor(device=loop)
    calls.set_monitor(on=True, device="")
    assert "CALL_MONITOR_DEVICE" not in dotenv_values(  # nosec B101
        calls.ENV_PATH)
    assert calls.monitor_settings()["on"] is True  # nosec B101


def test_the_user_hears_both_sides(monkeypatch):
    class Out:
        def __init__(self):
            self.wrote = []

        def write(self, pcm):
            self.wrote.append(bytes(pcm))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    line = calls.Line.__new__(calls.Line)
    line.device_rate = 1000
    line.stream = FakeStream([6000] * 20 + [0] * 100)
    line.monitor = Out()
    call_out = Out()
    line.sd = type("SD", (), {"RawOutputStream": lambda self, **k: call_out})()
    monkeypatch.setattr(calls.voice, "silence_threshold", lambda: 500.0)
    monkeypatch.setattr(calls.voice, "transcribe", lambda pcm: ("hi", ""))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    line.hear(longest=5)
    heard = len(line.monitor.wrote)
    assert heard > 0  # the other side

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(b"\x01\x00" * 500)
    monkeypatch.setattr(calls.voice, "synthesize", lambda t: (
        buf.getvalue(), ""))
    line.say("Hello")
    # A block at a time, so Stop can cut it short.
    assert b"".join(call_out.wrote) == b"\x01\x00" * 500  # into the call
    assert b"".join(line.monitor.wrote[heard:]) == b"\x01\x00" * 500


def test_joining_asks_for_the_devices_first(monkeypatch):
    monkeypatch.setattr(tools, "ultra_auto", lambda: True)
    monkeypatch.setattr(calls, "missing", lambda: "")
    monkeypatch.setenv("CALL_MIC_DEVICE", "Loop Mic")
    monkeypatch.setenv("CALL_SPEAKER_DEVICE", "Loop Out")

    def never(*a, **k):
        raise AssertionError("nothing is joined before the user says set")

    monkeypatch.setattr(calls, "join", never)
    result = tools.phone_call(action="join", who="Alex", goal="say hi",
                              app="Meet")
    assert ("Okay. Set the mic to Loop Mic and speakers to Loop Out in "
            "Meet and tell me once it's set.") in result
    assert "ready true" in result


def _wav(frames, rate=1000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * frames)
    return buf.getvalue()


class Out:
    def __init__(self, on_write=None):
        self.wrote = []
        self.on_write = on_write

    def write(self, pcm):
        self.wrote.append(bytes(pcm))
        if self.on_write:
            self.on_write()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _bare_line():
    line = calls.Line.__new__(calls.Line)
    line.device_rate = 1000
    line.monitor = None
    line.mic = None
    line.stopped = calls.threading.Event()
    return line


def test_halting_stops_flash_mid_word(monkeypatch):
    line = _bare_line()
    out = Out(on_write=line.halt)  # Stop pressed after the first block
    line.sd = type("SD", (), {"RawOutputStream": lambda self, **k: out})()
    line.stream = FakeStream([6000] * 100)
    monkeypatch.setattr(calls.voice, "synthesize", lambda t: (
        _wav(5000), ""))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    line.say("A long sentence")
    assert len(out.wrote) == 1  # not the other forty-nine blocks
    assert line.hear(longest=5) == ""  # nor listening after
    line.say("More")  # nor speaking again
    assert len(out.wrote) == 1


def test_pausing_cuts_flash_off_mid_word_without_ending_the_call(
        monkeypatch):
    line = _bare_line()
    out = Out(on_write=lambda: line.pause(True))  # Pause after one block
    line.sd = type("SD", (), {"RawOutputStream": lambda self, **k: out})()
    line.stream = FakeStream([6000] * 100)
    monkeypatch.setattr(calls.voice, "synthesize", lambda t: (
        _wav(5000), ""))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    line.say("A long sentence")
    assert len(out.wrote) == 1  # cut within a block, about 0.1s
    assert not line._halted()  # the call goes on
    line.say("Anything else")
    assert len(out.wrote) == 1  # nothing said while paused
    line.pause(False)
    line.say("Back again")
    assert len(out.wrote) > 1


def test_a_paused_flash_listens_but_answers_only_once_resumed():
    line = FakeLine(["Hello?", "Are you there?", "Okay, bye."])
    line.paused = calls.threading.Event()
    line._paused = line.paused.is_set
    line.paused.set()
    heard = line.hear

    def hear(longest=calls.QUIET_SECONDS, stopping=lambda: False):
        said = heard()
        if said == "Are you there?":
            line.paused.clear()  # Resume pressed while they talk
        return said

    line.hear = hear
    call = calls.Call(who="Alex", number="", goal="hi", incoming=True)
    answers = []

    def model(messages):
        answers.append(messages[-1]["content"])
        return "Sorry, I'm here. [hang up]"

    calls.talk(call, line, "Sam", model)
    # Both things they said while Flash was paused were kept, and
    # Flash answered once, after the resume.
    assert [w for w, _ in call.lines] == ["Alex", "Alex", "Flash"]
    assert answers == ["Are you there?"]
    assert line.said == ["Sorry, I'm here."]


def test_a_halted_line_ends_the_talk():
    line = FakeLine(["Hello?", "Still there?"])
    line.stopped = calls.threading.Event()
    line._halted = line.stopped.is_set
    line.stopped.set()
    call = calls.Call(who="Alex", number="", goal="hi", incoming=True)
    calls.talk(call, line, "Sam", lambda m: "Hi")
    assert call.ended == "you stopped it" and line.said == []


def test_the_macs_devices_come_back_however_the_call_ends(monkeypatch):
    now = {"input": "MacBook Microphone", "output": "MacBook Speakers"}

    def switch(kind, name=""):
        was = now[kind]
        if name:
            now[kind] = name
        return was

    monkeypatch.setattr(calls, "_switch", switch)
    with pytest.raises(RuntimeError):
        with calls._routed():
            assert now == {"input": "BlackHole 2ch",
                           "output": "BlackHole 16ch"}
            raise RuntimeError("the call fell over")
    assert now == {"input": "MacBook Microphone",
                   "output": "MacBook Speakers"}
    now.update(input="Headset")
    calls.restore()  # only once: nothing to put back now
    assert now["input"] == "Headset"


def test_stop_halts_the_call_and_gives_the_devices_back(monkeypatch):
    now = {"input": "MacBook Microphone", "output": "MacBook Speakers"}

    def switch(kind, name=""):
        was = now[kind]
        if name:
            now[kind] = name
        return was

    monkeypatch.setattr(calls, "_switch", switch)
    line = _bare_line()
    monkeypatch.setattr(calls, "Line", lambda: line)
    with calls._holding("chat-1", "Alex"), calls._routed(), \
            calls._opened():
        assert calls.live()["live"]
        assert not calls.halt("another chat")  # not its call
        assert calls.halt("chat-1")
        assert line.stopped.is_set()
        assert now == {"input": "MacBook Microphone",
                       "output": "MacBook Speakers"}
    assert not calls.live()["live"]
    assert not calls.halt("chat-1")  # nothing going on now


class FakeMicSD:
    def __init__(self):
        self.out = Out()
        self.opened = []

    def query_devices(self, name=None, kind=None):
        return {"default_samplerate": 1000, "name": "MacBook Microphone"}

    def RawInputStream(self, **k):
        self.opened.append(("in", k["device"]))
        return type("In", (), {
            "start": lambda s: None, "stop": lambda s: None,
            "close": lambda s: None,
            "read": lambda s, n: (b"\x05\x00" * n, False)})()

    def RawOutputStream(self, **k):
        self.opened.append(("out", k["device"]))
        out = self.out
        out.start = out.stop = out.close = lambda: None
        return out


def test_the_users_mic_goes_into_the_call_and_out_again(monkeypatch):
    monkeypatch.delenv("CALL_MIC_DEVICE", raising=False)
    monkeypatch.delenv("CALL_SPEAKER_DEVICE", raising=False)
    monkeypatch.setenv("CALL_USER_MIC", "MacBook Microphone")
    line = _bare_line()
    line.sd = FakeMicSD()
    monkeypatch.setattr(calls, "Line", lambda: line)
    with pytest.raises(calls.CallError):
        calls.set_mic(True)  # no call going on
    with calls._holding("", "Alex"), calls._opened():
        assert calls.set_mic(True)["mic"]
        deadline = calls.time.monotonic() + 2
        while not line.sd.out.wrote and calls.time.monotonic() < deadline:
            calls.time.sleep(0.01)
        assert calls.set_mic(False)["mic"] is False
    # From the user's own mic, into what the call hears: never into
    # what Flash listens on.
    assert line.sd.opened == [("in", "MacBook Microphone"),
                              ("out", "BlackHole 2ch")]
    assert line.sd.out.wrote and line.sd.out.wrote[0].startswith(b"\x05\x00")


def test_the_users_mic_is_never_a_loopback(monkeypatch):
    monkeypatch.delenv("CALL_MIC_DEVICE", raising=False)
    monkeypatch.delenv("CALL_SPEAKER_DEVICE", raising=False)
    for loop in ("BlackHole 2ch", "BlackHole 16ch"):
        monkeypatch.setenv("CALL_USER_MIC", loop)
        with pytest.raises(calls.CallError):
            calls._user_mic()


def test_each_turn_goes_to_the_page_when_one_is_drawing(
        monkeypatch, tmp_path):
    monkeypatch.setattr(calls, "CALLS_DIR", tmp_path)
    drawn = []
    monkeypatch.setattr(calls, "tool_call",
                        lambda event: drawn.append(event) or True)
    monkeypatch.setattr(calls, "tool_result", lambda *a, **k: (
        _ for _ in ()).throw(AssertionError("printed as well")))
    call = calls.Call(who="Alex", number="", goal="hi", incoming=False)
    call.say("Alex", "Hello?")
    call.say("Flash", "Hi Alex.")
    assert [(e["who"], e["said"], e["flash"]) for e in drawn] == [
        ("Alex", "Hello?", False), ("Flash", "Hi Alex.", True)]
    assert all(e["at"] and e["with"] == "Alex" for e in drawn)


def test_flash_sees_the_call_you_confirmed_yourself(monkeypatch):
    # Flash can't press Call, but you did: the call goes live.
    states = iter([{"live": False, "connected": False}] * 2
                  + [{"live": True, "connected": False}])
    monkeypatch.setattr(calls, "_press", lambda names, want="": "")
    monkeypatch.setattr(calls, "_state", lambda: next(states))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    assert calls._confirmed(lambda: False)


def test_confirming_gives_up_in_time(monkeypatch):
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(calls.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    monkeypatch.setattr(calls, "_press", lambda names, want="": "")
    monkeypatch.setattr(calls, "_state",
                        lambda: {"live": False, "connected": False})
    assert not calls._confirmed(lambda: False)


def test_a_call_never_seen_starting_stops_and_says_why(monkeypatch):
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(calls.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    monkeypatch.setattr(calls, "_state",
                        lambda: {"live": False, "connected": False})
    call = calls.Call(who="John", number="1", goal="say hi", incoming=False)
    assert not calls._picked_up(call, lambda: False)
    assert "couldn't see the call start" in call.ended


def test_call_scans_look_in_the_phone_app_too():
    assert '"Phone"' in calls._STATE
    assert '"Phone"' in calls._PRESS


def test_no_accessibility_says_so_instead_of_never_seeing_the_call(
        monkeypatch):
    # Without it the scripts saw nothing and Flash waited out the call.
    def refused(argv, timeout=None):
        raise calls.computer.ComputerError(
            "osascript failed: execution error: Error: not allowed "
            "assistive access (-2700)")
    monkeypatch.setattr(calls.computer, "_run", refused)
    with pytest.raises(calls.CallError, match="Accessibility"):
        calls._state()
    with pytest.raises(calls.CallError, match="Accessibility"):
        calls._press(["Call"])


def test_a_slow_look_is_not_taken_for_no_accessibility(monkeypatch):
    # A timeout's message quotes the script, which names assistive access.
    def slow(argv, timeout=None):
        raise calls.computer.ComputerError(
            f"osascript could not run: Command {argv} timed out")
    monkeypatch.setattr(calls.computer, "_run", slow)
    assert calls._state() == {"live": False, "connected": False}
    assert calls._press(["Call"]) == ""


def test_scans_read_a_level_at_a_time_and_press_by_description():
    # Asked an element at a time, a look took over a minute on macOS 27;
    # its banners' buttons have a description and no name.
    for script in (calls._STATE, calls._PRESS):
        assert "uiElements" in script and "entireContents" not in script
    assert '"description"' in calls._PRESS and "AXPress" in calls._PRESS


@pytest.mark.parametrize("via", [calls.PHONE, calls.FACETIME])
@pytest.mark.parametrize("who_confirms", ["flash", "you"])
def test_both_kinds_of_call_get_confirmed_and_seen_live(monkeypatch, via,
                                                        who_confirms):
    # An iPhone call and a FaceTime Audio one: the prompt is answered,
    # by Flash pressing Call or by you, and the live call is seen.
    dialed, pressed = [], []
    monkeypatch.setattr(calls, "missing", lambda: "")
    monkeypatch.setattr(calls, "_number_of", lambda who, facetime: (
        "John", "+15550102000"))
    monkeypatch.setattr(calls, "_switch", lambda kind, name="": "")
    monkeypatch.setattr(calls, "_dial",
                        lambda handle, v: dialed.append((handle, v)))

    def press(names, want=""):
        pressed.append(names)
        return "Call" if who_confirms == "flash" and "Call" in names else ""
    monkeypatch.setattr(calls, "_press", press)
    # You clicking Call shows only as the call going live.
    ringing = [{"live": True, "connected": False}]
    states = iter(ringing * (who_confirms == "you")
                  + [{"live": True, "connected": True}] * 50)
    monkeypatch.setattr(calls, "_state", lambda: next(states))
    monkeypatch.setattr(calls, "_facetime_devices", lambda: None)
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    seen = []

    def picked_up(call, stopping):
        seen.append(calls._state()["connected"])
        call.ended = "test over"
        return False
    monkeypatch.setattr(calls, "_picked_up", picked_up)
    call = calls.place("John", "say hi", "Sam", lambda m: "x", via=via)
    assert dialed == [("+15550102000", via)]
    assert ["Call"] in pressed and seen == [True]
    assert call.ended == "test over"


def _first_turn(call):
    line = FakeLine(["Sure, go ahead.", ""])
    asked = []

    def model(messages):
        asked.append(messages)
        return "Hi, this is Flash, an automated assistant calling for Sam."

    calls.talk(call, line, "Sam", model)
    return asked[0], line


def test_a_placed_call_greets_first_with_the_goal():
    for number in ("+15550102000", "facetime-audio:+15550102000"):
        call = calls.Call(who="John", number=number,
                          goal="tell him I'm running late", incoming=False)
        first, _ = _first_turn(call)
        assert first[-1] == {"role": "user",
                             "content": "(silence on the line)"}
        assert "running late" in first[0]["content"]
        assert call.lines[0][0] == "Flash"


def test_an_answered_call_greets_first():
    call = calls.Call(who="Ann", number="+15550103000", goal="",
                      incoming=True)
    _first_turn(call)
    assert call.lines[0][0] == "Flash"


def test_a_joined_call_greets_first():
    call = calls.Call(who="the team", number="", goal="say hi",
                      incoming=True, joined=True)
    _first_turn(call)
    assert call.lines[0][0] == "Flash"


def test_the_first_words_are_ready_before_the_call_is_live(monkeypatch):
    call = calls.Call(who="John Doe", number="+15550102000",
                      goal="tell him I'm running late", incoming=False)
    monkeypatch.setattr(calls.voice, "synthesize",
                        lambda text: (b"WAV:" + text.encode(), ""))
    replies = iter(["Hi John, this is Flash, calling for Sam.",
                    "Thanks, goodbye! [hang up]"])
    asked = []

    def model(messages):
        asked.append(messages)
        return next(replies)

    # Written and spoken while dialling, before any line is open.
    opening = calls._Opening(call, "Sam", model)
    assert opening.get() == ("Hi John, this is Flash, calling for Sam.",
                             b"WAV:Hi John, this is Flash, calling for Sam.")

    class PlayLine(FakeLine):
        played = []

        def play(self, wav):
            self.played.append(wav)

    line = PlayLine(["No worries."])
    calls.talk(call, line, "Sam", model, opening=opening)

    # Played as it was made: not asked for, nor spoken, a second time.
    assert line.played == [b"WAV:Hi John, this is Flash, calling for Sam."]
    assert line.said == ["Thanks, goodbye!"]
    assert len(asked) == 2
    assert call.ended == "the call was done"


def test_answer_takes_any_caller_without_reading_a_name(monkeypatch):
    monkeypatch.setattr(calls, "missing", lambda: "")
    pressed = []
    looked = []
    monkeypatch.setattr(calls, "_number_of",
                        lambda *a, **k: looked.append(a) or ("", ""))

    def press(names, want=""):
        pressed.append(want)
        raise StopIteration
    monkeypatch.setattr(calls, "_press", press)
    try:
        calls.take_next("anyone", "say hi", "Sam", lambda m: "", minutes=1)
    except StopIteration:
        pass
    assert pressed == [""] and not looked


def test_answer_with_nothing_ringing_fails_at_once(monkeypatch):
    from flash import tools
    monkeypatch.setattr(tools, "ultra_auto", lambda: True)
    monkeypatch.setattr(calls, "missing", lambda: "")
    monkeypatch.setattr(calls, "ringing", lambda: "")
    started = []
    # Who the user is comes from a command, which on Windows reads its
    # output on threads of its own: those stay real.
    monkeypatch.setattr(tools, "_call_user", lambda: "Nathan")
    monkeypatch.setattr(tools.threading, "Thread",
                        lambda *a, **k: started.append(1))
    out = tools.phone_call("answer", "anyone", "say hi")
    assert out.startswith("Error: no call is ringing")
    assert '"call"' in out and not started
    out = tools.phone_call("answer", "John", "say hi")
    assert '"call:John"' in out


def test_the_standard_greeting_is_said_first_with_the_first_name_only(
        monkeypatch):
    monkeypatch.setenv("CALL_GREETING_MODE", "default")
    call = calls.Call(who="John", number="1", goal="say I'm late",
                      incoming=False)
    line = FakeLine([])
    asked = []

    def model(messages):
        asked.append(messages)
        return "Sam is running late."

    calls.talk(call, line, "Sam Smith", model)
    assert line.said == ["Hi, this is an automated assistant calling on "
                         "behalf of Sam. Sam is running late."]
    system = asked[0][0]["content"]
    assert "Do not greet" in system and "Smith" not in system
    assert "Flash" not in system


def test_answering_and_no_name_greetings(monkeypatch):
    monkeypatch.setenv("CALL_GREETING_MODE", "default")
    taken = calls.Call(who="Ann", number="1", goal="", incoming=True)
    assert calls._greeting(taken, "Sam Smith") == (
        "Hi, this is an automated assistant answering for Sam.")
    placed = calls.Call(who="Ann", number="1", goal="", incoming=False)
    assert calls._greeting(placed, "the user") == (
        "Hi, this is an automated assistant calling.")


def test_a_custom_greeting_is_saved_and_said(monkeypatch, tmp_path):
    monkeypatch.setattr(calls, "ENV_PATH", tmp_path / ".flash.env")
    assert calls.set_greeting("custom", "Hey, {name}'s assistant here.") \
        == {"greeting_mode": "custom",
            "greeting": "Hey, {name}'s assistant here."}
    saved = dotenv_values(calls.ENV_PATH)
    assert saved["CALL_GREETING_MODE"] == "custom"
    call = calls.Call(who="Ann", number="1", goal="", incoming=False)
    assert calls._greeting(call, "Sam Smith") == "Hey, Sam's assistant here."
    with pytest.raises(ValueError):
        calls.set_greeting("custom", "  ")
    with pytest.raises(ValueError):
        calls.set_greeting("loud")


def test_own_mode_has_no_fixed_greeting():
    call = calls.Call(who="Ann", number="1", goal="", incoming=False)
    assert calls._greeting(call, "Sam") == ""


class HoldingLine(FakeLine):
    """A line that can be paused and have the user's mic put in."""

    pause = calls.Line.pause
    _paused = calls.Line._paused

    def __init__(self, heard):
        super().__init__(heard)
        self.mic = None

    def set_mic(self, on):
        self.mic = on or None


def _on_the_line(monkeypatch, line):
    shown = []
    monkeypatch.setattr(calls, "tool_call", lambda entry: shown.append(entry) or True)
    monkeypatch.setattr(calls, "_owner", "")
    monkeypatch.setattr(calls, "_line", line)
    from flash import notify
    pinged = []
    monkeypatch.setattr(notify, "notify", lambda title, text: pinged.append((title, text)))
    return shown, pinged


def test_flash_pings_the_user_to_join_and_they_take_over_and_hand_back(monkeypatch):
    line = HoldingLine([])
    shown, pinged = _on_the_line(monkeypatch, line)
    script = iter(["Can I talk to Sam directly?", "TAKE", "So, about that invoice.", "BACK", "Great, thanks."])

    def hear(longest=calls.QUIET_SECONDS, stopping=lambda: False):
        said = next(script, "")
        if said == "TAKE":
            calls.take_over(True)
            return ""
        if said == "BACK":
            calls.take_over(False)
            return ""
        return said

    line.hear = hear
    call = calls.Call(who="Alex", number="", goal="sort out the invoice", incoming=True)
    replies = iter(["Hi Alex, Flash here for Sam.", "Sure, let me get Sam, one moment. [get the user]",
                    "Thanks Alex, Sam has it from here. Goodbye! [hang up]"])
    asked = []

    def model(messages):
        asked.append(messages)
        return next(replies)

    calls.talk(call, line, "Sam", model)
    assert pinged and pinged[0][0] == "Join the call"  # nosec B101
    assert [e.get("state") for e in shown if e.get("state")] == ["join", "taken", "back"]  # nosec B101
    # The marker is never said aloud; what they said while Sam had the call is kept.
    assert line.said[1] == "Sure, let me get Sam, one moment."  # nosec B101
    assert ("Alex", "So, about that invoice.") in call.lines  # nosec B101
    # Handed back, Flash is told Sam was on the line, and goes on from there.
    notes = [m["content"] for m in asked[-1] if m["role"] == "system"]
    assert any("handed the call back" in n for n in notes)  # nosec B101
    assert line.mic is None and not line._paused()  # nosec B101
    assert call.ended == "the call was done"  # nosec B101


def test_flash_goes_back_on_when_the_user_does_not_join(monkeypatch):
    line = HoldingLine(["Can I talk to Sam?", "Okay.", "Bye."])
    shown, _ = _on_the_line(monkeypatch, line)
    monkeypatch.setattr(calls, "JOIN_WAIT", 0.0)
    call = calls.Call(who="Alex", number="", goal="hi", incoming=True)
    replies = iter(["Hi, Flash here for Sam.", "One moment, I'll get Sam. [get the user]",
                    "Sorry, Sam can't come to the phone right now. Can I take a message? [hang up]"])
    asked = []

    def model(messages):
        asked.append(messages)
        return next(replies)

    calls.talk(call, line, "Sam", model)
    assert "join-missed" in [e.get("state") for e in shown]  # nosec B101
    assert any("can't come to the phone" in m["content"] for m in asked[-1] if m["role"] == "system")  # nosec B101
    assert line.said[-1].startswith("Sorry, Sam can't come")  # nosec B101
