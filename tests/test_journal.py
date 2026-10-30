"""Tests for the journal: what a day held, writing it up, the rollups,
once a day from the keeper, and reading it back."""

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from flash import ai, journal, sessions, tools, web, workspace

DAY = date(2026, 10, 27)  # a Tuesday


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour).astimezone() \
        .timestamp()


def web_chat(chat_id, title, turns):
    """A saved browser chat: TURNS is (when, asked, answered)."""

    log = []
    for when, asked, answered in turns:
        log += [{"type": "user", "text": asked, "turn": "t"},
                {"type": "assistant", "text": answered, "thinking": ""},
                {"type": "stats", "at": int(when), "model": "m"}]
    workspace.save_chat({
        "id": chat_id, "title": title, "messages": [], "log": log,
        "created": turns[0][0], "updated": turns[-1][0],
    })


class Writer:
    """A model that answers every write-up with BULLETS, and keeps what
    it was asked."""

    def __init__(self, bullets="- You did a thing."):
        self.bullets = bullets
        self.asked = []

    def chat(self, **kwargs):
        self.asked.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=self.bullets))


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv(journal.SETTING, "1")


class TestKeys:
    def test_days_weeks_and_months(self):
        assert journal.heading("2026-10-27") == "Tuesday 27 October 2026"
        assert journal.week_key(DAY) == "2026-W44"
        assert journal.heading("2026-W44") == (
            "Week 44, 2026 (26 October to 1 November)")
        assert journal.heading("2026-10") == "October 2026"
        assert journal.path_of("2026-W44").parent.name == "weeks"
        with pytest.raises(ValueError, match="not a day"):
            journal.path_of("../secrets")


class TestWhatADayHeld:
    def test_browser_chats_by_the_time_of_each_reply(self):
        web_chat("aaaa0001", "Lisbon trip", [
            (at(DAY, 9), "Plan a trip to Lisbon", "Here is a plan."),
            (at(date(2026, 10, 28), 9), "And Porto?", "Porto too."),
        ])

        text, _ = journal.gather(DAY)

        assert "Chat: Lisbon trip" in text
        assert "User: Plan a trip to Lisbon" in text
        assert "Porto" not in text
        assert journal.gather(date(2026, 10, 26))[0] == ""

    def test_terminal_sessions_by_their_last_change_and_only_once(self):
        session = sessions.Session(title="Fix the parser")
        sessions.save(session, [
            {"role": "user", "content": "Why does parse_date fail?"},
            {"role": "assistant", "content": "It does not strip spaces."},
        ])
        # Last changed on DAY.
        kept = sessions._path(session.id)
        raw = json.loads(kept.read_text())
        raw["updated"] = at(DAY, 15)
        kept.write_text(json.dumps(raw))

        text, reached = journal.gather(DAY)
        assert "User: Why does parse_date fail?" in text
        assert reached == {session.id: 2}

        journal.write_day(Writer(), "m", DAY)
        assert journal.gather(DAY)[0] == ""


class TestWritingItUp:
    def test_a_day_with_chats(self):
        web_chat("aaaa0002", "Taxes", [(at(DAY), "File my taxes", "Done.")])
        model = Writer("Here is your entry:\n\n- You filed your taxes.\n"
                       "* You found a missing receipt.\nThanks!")

        path = journal.write_day(model, "m", DAY)

        assert path.read_text() == (
            "# Tuesday 27 October 2026\n\n- You filed your taxes.\n"
            "- You found a missing receipt.\n")
        asked = model.asked[0]["messages"]
        assert "Tuesday 27 October 2026" in asked[0]["content"]
        assert "File my taxes" in asked[1]["content"]

    def test_a_day_without_any_is_not_written(self):
        assert journal.write_day(Writer(), "m", DAY) is None

    def test_catch_up_rolls_up_finished_weeks_and_months(self):
        for n, day in enumerate([date(2026, 10, 27), date(2026, 10, 29),
                                 date(2026, 11, 2)]):
            web_chat(f"bbbb000{n}", "Work", [(at(day), f"Task {n}", "Ok")])
        model = Writer()

        written = journal.catch_up(model, "m", today=date(2026, 11, 3))

        names = sorted(p.name for p in written)
        assert names == ["2026-10-27.md", "2026-10-29.md", "2026-10.md",
                         "2026-11-02.md", "2026-W44.md"]
        # The week of 2 November is not over, nor is November.
        assert not journal.path_of("2026-W45").exists()
        assert not journal.path_of("2026-11").exists()
        # Days with nothing are remembered, and not asked about again.
        assert "2026-10-28" in json.loads(
            journal._state_file().read_text())["quiet"]
        before = len(model.asked)
        assert journal.catch_up(model, "m", today=date(2026, 11, 3)) == []
        assert len(model.asked) == before

    def test_once_a_day_on_only_and_with_a_model(self, monkeypatch):
        web_chat("cccc0001", "x", [(at(DAY), "hi", "hello")])
        made = []

        def client():
            made.append(1)
            return Writer()

        today = date(2026, 10, 28)
        assert journal.tick(client, "", today=today) == []
        monkeypatch.setenv(journal.SETTING, "0")
        assert journal.tick(client, "m", today=today) == []
        monkeypatch.setenv(journal.SETTING, "1")
        assert len(journal.tick(client, "m", today=today)) == 1
        assert journal.tick(client, "m", today=today) == []
        assert made == [1]


class TestReadingItBack:
    def write(self):
        journal._write("2026-10-27", "- You fixed the login bug.\n"
                       "- You booked flights to Lisbon.")
        journal._write("2026-10-28", "- You wrote tests for the parser.")
        journal._write("2026-10", "- A month of fixes and travel plans.")

    def test_search_and_read(self):
        self.write()

        found = journal.search("login BUG")
        assert found == [("2026-10-27", ["- You fixed the login bug."])]
        assert "Wednesday 28 October 2026" in journal.answer(
            "read", "2026-10-28")
        assert "A month of fixes" in journal.answer("read", "2026-10")
        # A week not rolled up yet reads as its days.
        assert "login bug" in journal.answer("read", "2026-W44")
        assert journal.answer("read", "2026-09-01").startswith(
            "There is no journal entry")
        assert journal.answer("read", "nope").startswith("Error:")
        assert "2026-10-28: Wednesday" in journal.answer("list")

    def test_the_tool(self):
        self.write()
        assert "journal" in tools.FUNCTIONS
        said = tools.journal_tool("search", query="Lisbon")
        assert "booked flights to Lisbon" in said

    def test_the_terminal(self, capsys, monkeypatch):
        self.write()
        ai._journal_command("2026-10-27", None)
        ai._journal_command("parser", None)
        out = capsys.readouterr().out
        assert "login bug" in out and "tests for the parser" in out

        ai._journal_command("off", None)
        assert not journal.enabled()

        web_chat("dddd0001", "x", [(at(date(2026, 10, 29)), "hi", "yo")])
        monkeypatch.setattr(ai.Config, "model", "m")
        ai._journal_command("now", Writer())
        assert journal.path_of("2026-10-29").exists() or \
            journal._today() <= date(2026, 10, 29)

    def test_the_page(self):
        self.write()
        session = web.Session()

        listed = web.command(session, {"name": "journal"})
        assert listed["on"] is True
        assert [e["key"] for e in listed["entries"]] == [
            "2026-10-28", "2026-10-27", "2026-10"]
        assert listed["entries"][2]["kind"] == "month"
        found = web.command(session, {"name": "journal-search",
                                      "arg": "parser"})["found"]
        assert found[0]["heading"] == "Wednesday 28 October 2026"
        assert web.command(session, {"name": "journal-setting",
                                     "arg": "off"}) == {"on": False}
        assert not journal.enabled()
        page = web.PAGE.read_text()
        assert 'id="journal-btn"' in page and "drawJournalPage" in page
        assert web.PAGE_PATHS.match("/journal")
