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


def test_an_email_only_contact_cannot_be_called(monkeypatch):
    monkeypatch.setattr(calls.computer, "recipient",
                        lambda who: ("Ann", "ann@example.com"))
    with pytest.raises(calls.CallError, match="phone number"):
        calls._number_of("Ann")


def test_the_transcript_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(calls, "CALLS_DIR", tmp_path)
    call = calls.Call(who="John", number="1", goal="say hi", incoming=False,
                      lines=[("Flash", "Hi"), ("John", "Hey")],
                      ended="the call was done")
    path = calls._keep(call)
    assert "Flash: Hi\nJohn: Hey" in path.read_text()
    assert "ended: the call was done" in calls.summary(call, path)
