"""Tests for the line that closes a turn out.

"Pondered for 1m 23s · done 7:32 PM": the state the spinner opened
with, in the past tense, how long it ran, and the time it finished.
"""

import io
import json
import re
from pathlib import Path

import pytest
from rich.console import Console

from flash import ai
from flash.stats import Turn, elapsed
from flash.theme import MIDDOT

STATES = Path(ai.__file__).parent / "thinking_states.json"


class TestTheStatesFile:
    def data(self):
        return json.loads(STATES.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("key", ["states", "image_states"])
    def test_every_state_has_both_tenses(self, key):
        for entry in self.data()[key]:
            assert entry["now"].strip(), entry
            assert entry["then"].strip(), entry

    @pytest.mark.parametrize("key", ["states", "image_states"])
    def test_the_past_tense_is_not_just_the_present(self, key):
        # A state added without a real past form would read as
        # "Pondering for 3s", which is the bug this catches.
        for entry in self.data()[key]:
            assert entry["then"] != entry["now"], entry

    @pytest.mark.parametrize("key", ["states", "image_states"])
    def test_no_state_is_left_in_the_present_participle(self, key):
        for entry in self.data()[key]:
            first = entry["then"].split()[0]
            assert not first.endswith("ing"), entry

    def test_there_are_still_plenty_to_cycle_through(self):
        assert len(self.data()["states"]) > 10
        assert len(self.data()["image_states"]) > 5


class TestStatePair:
    def test_a_full_entry_comes_through(self):
        pair = ai._state_pair({"now": "Pondering", "then": "Pondered"})

        assert pair == {"now": "Pondering", "then": "Pondered"}

    def test_a_bare_string_still_works(self):
        # Reads back as its own past tense, which is wrong but harmless
        # and better than dropping the state.
        assert ai._state_pair("Thinking") == {
            "now": "Thinking", "then": "Thinking",
        }

    def test_a_missing_past_falls_back_to_the_present(self):
        assert ai._state_pair({"now": "Thinking"})["then"] == "Thinking"

    def test_an_empty_entry_is_dropped(self):
        assert ai._state_pair("") == {}
        assert ai._state_pair({"now": "   "}) == {}

    def test_whitespace_is_trimmed(self):
        assert ai._state_pair({"now": " A ", "then": " B "}) == {
            "now": "A", "then": "B",
        }


class TestLoading:
    def test_the_real_file_loads_as_pairs(self):
        for state in ai._load_thinking_states():
            assert set(state) == {"now", "then"}

    def test_the_image_states_load_too(self):
        assert ai._load_image_thinking_states()

    def test_a_broken_file_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            ai.Path, "read_text", lambda *a, **k: "not json"
        )

        states = ai._load_thinking_states()

        assert states
        assert all(state["then"] for state in states)

    def test_the_cycle_moves_on(self):
        states = [{"now": "A", "then": "a"}, {"now": "B", "then": "b"}]
        seen = {ai._next_thinking_state(states)["now"] for _ in range(4)}

        assert seen == {"A", "B"}


class TestNoteWait:
    def test_the_first_state_is_the_one_that_sticks(self):
        turn = Turn()
        turn.note_wait("Pondered", 1.0)
        turn.note_wait("Analyzed", 2.0)

        # The user watched the first one while the wait was new.
        assert turn.state == "Pondered"

    def test_the_waits_add_up_across_rounds(self):
        turn = Turn()
        turn.note_wait("Pondered", 1.5)
        turn.note_wait("Analyzed", 2.5)

        assert turn.waited == 4.0

    def test_a_fresh_turn_has_nothing_to_say(self):
        assert Turn().state == ""
        assert Turn().waited == 0.0

    def test_a_negative_reading_is_ignored(self):
        turn = Turn()
        turn.note_wait("Pondered", -5.0)

        assert turn.waited == 0.0


class TestRenderDone:
    def render(self, turn, monkeypatch, width=100):
        console = Console(file=io.StringIO(), width=width,
                          force_terminal=False)
        monkeypatch.setattr(ai, "console", console)
        ai._render_done(turn)
        return console.file.getvalue().strip()

    def turn(self, state="Pondered", seconds=83.0):
        turn = Turn()
        turn.note_wait(state, seconds)
        return turn

    def test_it_reads_the_way_it_was_asked_for(self, monkeypatch):
        monkeypatch.setattr(ai, "_clock", lambda: "7:32 PM")

        out = self.render(self.turn(), monkeypatch)

        assert out == f"Pondered for 1m 23s {MIDDOT} done 7:32 PM"

    def test_a_short_wait_drops_the_minutes(self, monkeypatch):
        monkeypatch.setattr(ai, "_clock", lambda: "7:32 PM")

        out = self.render(self.turn(seconds=4.0), monkeypatch)

        assert "for 4s" in out

    def test_it_uses_the_state_the_spinner_opened_with(self, monkeypatch):
        monkeypatch.setattr(ai, "_clock", lambda: "7:32 PM")
        turn = self.turn("Ran the numbers")
        turn.note_wait("Refined", 1.0)

        assert self.render(turn, monkeypatch).startswith("Ran the numbers")

    def test_a_turn_that_never_waited_says_nothing(self, monkeypatch):
        assert self.render(Turn(), monkeypatch) == ""

    def test_the_elapsed_matches_the_stats_formatter(self, monkeypatch):
        monkeypatch.setattr(ai, "_clock", lambda: "7:32 PM")

        out = self.render(self.turn(seconds=125.0), monkeypatch)

        assert elapsed(125.0) in out


class TestClock:
    def test_it_reads_like_a_person_says_it(self):
        assert re.fullmatch(r"\d{1,2}:\d{2} [AP]M", ai._clock())

    def test_the_hour_carries_no_leading_zero(self):
        assert not ai._clock().startswith("0")
