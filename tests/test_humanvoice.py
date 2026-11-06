"""Tests for what makes Flash's voice sound like a person (humanvoice)."""

import array
import math

from flash import ai, humanvoice, teamchat, voice, web
from tests.test_web import (
    FakeClient,
    offline,
    part,
    run,
)

# The web tests' fixture that keeps every turn offline, used here too.
FIXTURES = (offline,)
# Before that fixture stands in for it.
SYSTEM_PROMPT = ai._session_system_prompt


class TestCues:
    def test_cues_are_said_and_never_shown(self):
        text = "[happy] Oh nice! [laugh] That worked.\n\n    keep  this"

        assert humanvoice.strip(text) == (
            "Oh nice! That worked.\n\n    keep  this")

    def test_a_sentence_has_its_feeling_and_its_sounds_in_order(self):
        mood, pieces = humanvoice.pieces("[excited] No way, [laugh] it works?")

        assert mood == "excited"
        assert pieces == [
            ("say", "No way,"), ("sound", "laugh"), ("say", "it works?"),
        ]

    def test_stage_directions_are_not_read_out(self):
        assert humanvoice.strip("Sure *laughs* why not") == (
            "Sure  why not")

    def test_a_feeling_changes_how_it_is_said(self):
        happy = humanvoice.delivery("happy", "x", vary=False)
        sorry = humanvoice.delivery("sorry", "x", vary=False)

        assert happy.detune > 0 > sorry.detune
        assert happy.pace < 1 < sorry.pace
        assert sorry.gain < 1 < happy.gain

    def test_each_sentence_varies_but_the_same_one_never_does(self):
        long = "This sentence is long enough to be said a little its own way."
        other = "And this one, also long enough, is said its own way too."

        assert humanvoice.variation(long) == humanvoice.variation(long)
        assert humanvoice.variation(long) != humanvoice.variation(other)
        assert humanvoice.variation("Hi there.") == (1.0, 0)


class TestForTheEar:
    def test_paths_files_and_marks_are_said_as_a_person_would(self):
        said = humanvoice.for_ear(
            "Open src/flash/web.py, e.g. at 40% for $5 in 300ms.")

        assert said == (
            "Open web dot py, for example at 40 percent for 5 dollars in "
            "300 milliseconds.")

    def test_slashes_that_are_not_paths_stay(self):
        for text in ("and/or", "1/2 cup", "open 24/7", "https://x.com/a/b"):
            assert humanvoice.for_ear(text) == text


class TestTiming:
    def test_a_thought_left_open_waits_longer(self):
        assert humanvoice.end_pause("so I went and then I") > 1
        assert humanvoice.end_pause("open the notes for,") > 1

    def test_a_whole_question_is_answered_sooner(self):
        assert humanvoice.end_pause("what time is it") < 1

    def test_the_word_said_at_once_fits_what_was_said(self):
        assert humanvoice.ack_kind("the build failed again") == "bad"
        assert humanvoice.ack_kind("it worked finally") == "good"
        assert humanvoice.ack_kind("open my notes please") == "do"
        assert humanvoice.ack_kind("what is on my calendar") == "ask"
        assert humanvoice.ack_for("thanks so much") == ""
        assert humanvoice.ack_for("hi") == ""
        said = humanvoice.ack_for("the build failed again")
        assert said in humanvoice._ACKS["bad"]

    def test_breaths_come_before_some_long_sentences_only(self):
        long = [f"Sentence number {n}, which goes on long enough that a "
                "speaker would take a breath before it." for n in range(20)]

        assert not humanvoice.breathes("Short one.")
        assert 0 < sum(map(humanvoice.breathes, long)) < 20
        assert humanvoice.breathes(long[0]) == humanvoice.breathes(long[0])


def _speech(seconds, loud, pitch, rate=16000):
    """A voiced tone, the stand-in for speech: LOUD and at PITCH."""

    return array.array("h", (
        int(loud * math.sin(2 * math.pi * pitch * (i / rate)
                            + 0.4 * math.sin(i / 900)))
        for i in range(int(rate * seconds)))).tobytes()


class TestHearingHowTheySound:
    def test_laughing_is_heard_in_what_they_said(self):
        assert humanvoice.Ear().mood(b"", "haha no way") == "laughing"

    def test_nothing_is_said_before_there_is_a_usual_to_compare(self):
        ear = humanvoice.Ear()

        assert ear.mood(_speech(2, 4000, 150), "one two three four") == ""

    def test_louder_and_quicker_than_usual_is_excited(self):
        ear = humanvoice.Ear()
        for _ in range(3):
            ear.mood(_speech(2, 3000, 140), "one two three four five")

        now = ear.mood(_speech(1.2, 9000, 200),
                       "one two three four five six seven eight")

        assert now in ("excited", "upbeat")

    def test_quieter_and_slower_than_usual_is_tired(self):
        ear = humanvoice.Ear()
        for _ in range(3):
            ear.mood(_speech(2, 8000, 160), "one two three four five six")

        assert ear.mood(_speech(3, 2500, 160), "one two three") == (
            "tired or down")


class TestWhatTheModelIsTold:
    def test_the_note_says_how_they_sound_their_time_and_what_was_said(self):
        note = humanvoice.note_for({
            "mood": "upbeat", "time": "11:40 PM", "hour": 23,
            "ack": "Okay.", "cut": "So the plan is",
        })

        assert note.startswith("(They sound upbeat.")
        assert "11:40 PM for them, late at night" in note
        assert '"Okay."' in note and '"So the plan is"' in note
        assert humanvoice.note_for({}) == ""
        assert humanvoice.note_for("not a dict") == ""

    def test_thinking_aloud_follows_the_setting(self, monkeypatch):
        monkeypatch.setenv(humanvoice.DISFLUENCY_SETTING, "off")
        assert "um" not in humanvoice.spoken().split('"')
        assert "actually" not in humanvoice.spoken()

        monkeypatch.setenv(humanvoice.DISFLUENCY_SETTING, "more")
        assert "in most replies" in humanvoice.spoken()

        monkeypatch.delenv(humanvoice.DISFLUENCY_SETTING)
        assert humanvoice.disfluency() == "light"

    def test_spoken_turns_get_the_guidance(self):
        assert "[laugh]" in SYSTEM_PROMPT(heard=True)
        assert "[laugh]" not in SYSTEM_PROMPT()


class _Voice:
    """A Kokoro-shaped voice that keeps what it was asked to say."""

    def __init__(self):
        self.said = []

    def pcm(self, text, pace=1.0):
        self.said.append((text, pace))
        return b"\x10\x00" * 50


class TestSaying:
    def test_cues_are_said_not_read(self, monkeypatch):
        monkeypatch.setattr(voice, "_KokoroVoice", _Voice)
        speaker = _Voice()

        pcm = b"".join(voice._pcm_chunks(
            speaker, "[sorry] I lost it. [sigh] [laugh] Found it!", 100))

        said = [text for text, _ in speaker.said]
        assert "[" not in "".join(said)
        assert humanvoice.SAID_SOUNDS["laugh"] in said
        assert any(b != 0 for b in pcm)
        # Said sorry: slower than the voice's own pace for the line.
        assert speaker.said[0][1] > 1

    def test_a_line_s_pitch_is_its_feeling_s(self, monkeypatch):
        monkeypatch.setenv("VOICE_STYLE", "warm")

        assert voice.pitch_of("[excited] Oh wow, that is great!") > 50
        assert voice.pitch_of("[sad] Oh no, I am so sorry about that.") < -40

        monkeypatch.setenv("VOICE_STYLE", "plain")
        assert voice.pitch_of("[excited] Oh wow, that is great!") == 0


class TestTheWebPage:
    def test_how_they_sounded_goes_to_the_model_and_not_the_chat(self):
        FakeClient.scripts = [[part("Hey."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()
        session.voice_notes[chat.id] = humanvoice.note_for({"mood": "tired"})

        session.send(chat, "long day", heard=True)
        run(session, chat, "")

        asked = FakeClient.requests[0]["messages"][-1]["content"]
        assert asked.endswith("(They sound tired.)")
        assert [e["text"] for e in chat.log if e["type"] == "user"] == [
            "long day"]
        assert chat.id not in session.voice_notes

    def test_a_typed_turn_gets_no_note(self):
        FakeClient.scripts = [[part("Hey."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()
        session.voice_notes[chat.id] = "(They sound tired.)"

        run(session, chat, "typed")

        assert "tired" not in FakeClient.requests[0]["messages"][-1][
            "content"]

    def test_thinking_aloud_is_set_from_settings(self):
        session = web.Session()

        found = web.command(session, {"name": "voice-disfluency",
                                      "arg": "more"})

        picked = [d["name"] for d in found["disfluency"] if d["current"]]
        assert picked == ["more"]


def test_a_call_hears_how_the_user_sounded(monkeypatch):
    monkeypatch.setattr(teamchat, "_team", lambda key: type(
        "Team", (), {"id": "t1"})())
    monkeypatch.setattr(teamchat, "say", lambda *a, **k: {"id": 1})
    monkeypatch.setattr(teamchat, "answer", lambda *a, **k: [])

    teamchat.send("t1", "hi", call="c1", note="(They sound upbeat.)")

    assert teamchat._call_notes["t1"] == "(They sound upbeat.)"
