"""A spark's handle is its own: a name whose handle is taken is said at
once, in the terminal as in the web UI's form."""

from flash import ai, sparks


def test_a_taken_name_is_asked_for_again(monkeypatch):
    sparks.create("Scout", "Watch the issues.")
    answers = iter(["scout", "Ranger", ""])
    asked = []
    warned = []

    def ask(question):
        asked.append(question)
        return next(answers)

    monkeypatch.setattr(ai, "_ask_line", ask)
    monkeypatch.setattr(ai, "warn", warned.append)
    monkeypatch.setattr(ai.console, "print", lambda *a, **k: None)

    ai._new_spark()

    assert asked[:3] == ["Name it:", "Name it:", "What should it keep doing?"]
    assert warned == [
        "  Scout already has @scout-spark. Pick another name.",
    ]
    assert [s.name for s in sparks.all_sparks()] == ["Scout"]
