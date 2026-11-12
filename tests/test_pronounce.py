"""Pronunciation judged by sound: what an acoustic model heard, among the phrase's words and their near misses."""

import pytest

from flash import pronounce, tutor


def heard(*pairs):
    return [{"word": w, "conf": c} for w, c in pairs]


def test_words_said_as_themselves_are_right():
    marks = pronounce.judge(["The", "lazy", "dog."], heard(("the", 1.0), ("lazy", 0.98), ("dog", 0.91)))
    assert [m["mark"] for m in marks] == ["right", "right", "right"]


def test_a_word_heard_as_its_neighbour_is_missed_and_says_what_it_sounded_like():
    marks = pronounce.judge(["quick", "brown", "fox"], heard(("quick", 1.0), ("brown", 1.0), ("box", 1.0)))
    assert marks[2] == {"word": "fox", "mark": "missed", "heard": "box", "conf": 1.0}


def test_an_even_split_is_one_sound_spelt_two_ways():
    marks = pronounce.judge(["quick"], heard(("quik", 0.5)))
    assert marks[0]["mark"] == "right"


def test_unsure_is_close_and_unheard_is_missed():
    marks = pronounce.judge(["the", "dog", "barks"], heard(("the", 1.0), ("dug", 0.3)))
    assert [m["mark"] for m in marks] == ["right", "close", "missed"]


def test_neighbours_are_a_sound_away_and_never_the_same_word_respelt():
    vocab = {"box", "fix", "fax", "foxe", "foxx", "fo", "for"}
    found = pronounce.neighbours("fox", lambda w: w in vocab)
    assert {"box", "fix", "fax", "fo", "for"} <= found and not {"foxe", "foxx"} & found


def test_a_say_step_is_measured_by_its_score():
    tutor.add("French", [{"id": "bonjour", "text": "Saying hello", "say": {"text": "Bonjour", "lang": "fr-FR"}}])
    assert tutor.step("French", "bonjour")["kind"] == "say"
    assert tutor.spoken("French", "bonjour", 90)["right"]
    assert not tutor.spoken("French", "bonjour", 40)["right"]
    with pytest.raises(tutor.TutorError):
        tutor.add("French", [{"id": "x", "text": "x", "say": {"lang": "fr"}}])
