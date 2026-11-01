# pylint: disable=C0114,C0115,C0116

import pytest

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
