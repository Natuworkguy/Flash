"""Timers and alarms: lengths and times read as people say them, timers that
pause and ring, alarms that come round on their days, snoozing and stopping."""

from datetime import datetime

import pytest

from flash import clock, paths


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "FLASH_DIR", tmp_path)
    monkeypatch.setattr(clock, "_ensure", lambda: None)
    monkeypatch.setattr(clock, "_announce", lambda it: None)
    monkeypatch.setattr(clock, "_ring_sound", lambda: None)
    yield tmp_path


@pytest.mark.parametrize("said,secs", [("10m", 600), ("1h30m", 5400), ("90s", 90), ("10 minutes", 600),
                                       ("1:30", 90), ("1:02:00", 3720), ("5", 300), ("1 hour and 5 min", 3900)])
def test_lengths(said, secs):
    assert clock.parse_length(said) == secs


@pytest.mark.parametrize("said,hm", [("7", (7, 0)), ("7:30", (7, 30)), ("7.30am", (7, 30)), ("6:45 pm", (18, 45)),
                                     ("18:00", (18, 0)), ("12am", (0, 0)), ("noon", (12, 0))])
def test_times(said, hm):
    assert clock.parse_time_of_day(said) == hm


def test_bad_input_says_why():
    for bad in ("soon", ""):
        with pytest.raises(clock.ClockError):
            clock.parse_length(bad)
    with pytest.raises(clock.ClockError):
        clock.parse_time_of_day("25:00")


def test_days():
    assert clock.parse_days("weekdays") == ["mon", "tue", "wed", "thu", "fri"]
    assert clock.parse_days("Tuesdays and Thursdays") == ["tue", "thu"]
    assert clock.parse_days("") == []


def test_timer_rings_then_stops():
    t = clock.add_timer("1m", "Tea", now=1000.0)
    assert clock.tick(now=1030.0) == []
    st = clock.state(now=1030.0)
    assert st["items"][0]["progress"] == pytest.approx(.5)
    rang = clock.tick(now=1061.0)
    assert [r["id"] for r in rang] == [t["id"]]
    assert clock.state(now=1062.0)["ringing"]
    clock.stop(t["id"])
    assert clock.state()["items"] == []


def test_pause_resume_and_more_time(monkeypatch):
    t = clock.add_timer("2m", now=0.0)
    monkeypatch.setattr(clock.time, "time", lambda: 30.0)
    clock.pause(t["id"])
    assert clock.state(now=500.0)["items"][0]["remaining"] == pytest.approx(90)
    assert clock.tick(now=500.0) == []
    monkeypatch.setattr(clock.time, "time", lambda: 500.0)
    clock.resume(t["id"])
    clock.add_time(t["id"], 60)
    assert clock.state(now=500.0)["items"][0]["remaining"] == pytest.approx(150)


def test_alarm_comes_round_on_its_days():
    monday_6 = datetime(2026, 11, 23, 6, 0).timestamp()          # a Monday
    a = clock.add_alarm("6:45", "Gym", "weekdays", now=monday_6)
    assert datetime.fromtimestamp(a["next"]).strftime("%a %H:%M") == "Mon 06:45"
    friday_7 = datetime(2026, 11, 27, 7, 0).timestamp()
    nxt = clock._next_alarm(6, 45, a["days"], friday_7)
    assert datetime.fromtimestamp(nxt).strftime("%a %H:%M") == "Mon 06:45"


def test_snooze_then_ring_again(monkeypatch):
    now = datetime(2026, 11, 23, 6, 0).timestamp()
    a = clock.add_alarm("6:01", now=now)
    assert clock.tick(now=now + 61)
    monkeypatch.setattr(clock.time, "time", lambda: now + 70)
    clock.snooze(a["id"], 9)
    assert clock.tick(now=now + 70 + 8 * 60) == []
    assert clock.tick(now=now + 70 + 9 * 60 + 1)
    clock.stop(a["id"])
    assert clock.state()["items"][0]["on"] is False      # it rang once, so it is done


def test_left_ringing_too_long_is_missed():
    t = clock.add_timer("10s", now=0.0)
    clock.tick(now=11.0)
    clock.tick(now=11.0 + clock.RING_LIMIT + 1)
    assert all(i["id"] != t["id"] for i in clock.state(now=1000.0)["items"])


def test_tool_words():
    out = clock.tool("timer", length="5m", label="Pasta")
    assert out.startswith("Timer set.") and "Pasta" in out
    assert "Alarm set." in clock.tool("alarm", at="7:30", days="weekdays")
    assert "Couldn't" in clock.tool("timer", length="soon")
    listed = clock.tool("list")
    assert "Pasta" in listed and "07:30" in listed
