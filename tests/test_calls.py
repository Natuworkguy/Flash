# pylint: disable=C0114,C0115,C0116

import io
import wave

import pytest
from dotenv import dotenv_values

from flash import calls, tools


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
    line = FakeLine(["Hello?", "Oh no worries, see you soon."])
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
        "John Doe", "Flash", "John Doe", "Flash"]
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
    assert ran == [["open", "facetime-audio:+15550102000"],
                   ["open", "tel:+15550102000"]]


def test_the_transcript_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(calls, "CALLS_DIR", tmp_path)
    call = calls.Call(who="John", number="1", goal="say hi", incoming=False,
                      lines=[("Flash", "Hi"), ("John", "Hey")],
                      ended="the call was done")
    path = calls._keep(call)
    assert "Flash: Hi\nJohn: Hey" in path.read_text()
    assert "ended: the call was done" in calls.summary(call, path)


def test_flash_waits_for_the_call_to_be_picked_up(monkeypatch):
    states = iter([{"live": True, "connected": False}] * 2
                  + [{"live": True, "connected": True}])
    monkeypatch.setattr(calls, "_state", lambda: next(states))
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    call = calls.Call(who="John", number="1", goal="ask", incoming=False)
    assert calls._picked_up(call, lambda: False)


def test_a_call_nobody_picks_up_ends_without_talking(monkeypatch):
    states = iter([{"live": True, "connected": False},
                   {"live": False, "connected": False}])
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

    def join(goal, user, ask, who="", stopping=lambda: False):
        seen.update(goal=goal, who=who)
        return calls.Call(who="the people on the call", number="",
                          goal=goal, incoming=True, joined=True,
                          ended="you stopped it")

    monkeypatch.setattr(calls, "join", join)
    result = tools.phone_call(action="join", goal="say hi")
    assert seen == {"goal": "say hi", "who": ""}
    assert "left the call" in result


def test_each_turn_goes_to_the_live_transcript_as_it_happens(monkeypatch,
                                                            tmp_path):
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
    assert call_out.wrote == [b"\x01\x00" * 500]  # into the call
    assert line.monitor.wrote[heard:] == [b"\x01\x00" * 500]  # and to you
