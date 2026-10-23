# pylint: disable=C0114,C0115,C0116

import pytest

from flash import ai, sessions


def chat(turns=3):
    said = []
    for n in range(turns):
        said += [
            {"role": "user", "content": f"question {n}"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "read",
                                          "arguments": {"path": "x"}}}]},
            {"role": "tool", "content": "file", "tool_name": "read"},
            {"role": "assistant", "content": f"answer {n}"},
        ]
    return said


def test_kept_and_listed_the_latest_first():
    first = sessions.save(sessions.Session(), [
        {"role": "user", "content": "look at this", "images": [b"\x89PNG"]},
        {"role": "assistant", "content": "A cat."},
    ])
    second = sessions.save(sessions.Session(), chat(2))

    assert [s["id"] for s in sessions.listing()] == [second.id, first.id]
    back = sessions.load(first.id)
    assert back.title == "look at this"
    assert back.messages[0] == {
        "role": "user", "content": f"look at this\n{sessions.IMAGE_NOTE}"}
    assert sessions.listing()[0]["turns"] == 2
    with pytest.raises(sessions.SessionError):
        sessions.load("../../etc")


def test_forks_go_back_switch_and_are_let_go():
    session = sessions.Session()
    messages = chat(3)
    made, kept = sessions.fork(session, messages, back=2)

    # Kept up to the start of the second turn.
    assert [m["content"] for m in kept if m["role"] == "user"] == [
        "question 0"]
    assert session.fork == made and session.forks[made]["parent"] == "main"
    assert session.forks[made]["label"] == "answer 0"

    kept.append({"role": "user", "content": "another way"})
    deeper, _ = sessions.fork(session, kept)
    rows = sessions.tree(session, kept)
    assert [(r["id"], r["depth"]) for r in rows] == [
        ("main", 0), (made, 1), (deeper, 2)]
    assert rows[1]["lead"] == "└─ "
    assert rows[2]["lead"] == "   └─ " and rows[2]["current"]

    back = sessions.switch(session, kept, "main")
    assert len(back) == len(messages)
    again = sessions.switch(session, back, made)
    assert again[-1]["content"] == "another way"

    # The one showing goes, and so its forks move up to main.
    now = sessions.delete_fork(session, again, made)
    assert session.fork == "main" and len(now) == len(messages)
    assert session.forks[deeper]["parent"] == "main"
    sessions.delete_fork(session, now, deeper)
    assert session.forks == {} and session.fork == ""

    with pytest.raises(sessions.SessionError, match="only 3 turns"):
        sessions.fork(session, messages, back=4)


def test_the_terminal_keeps_resumes_and_forks(monkeypatch):
    printed = []
    monkeypatch.setattr(ai.console, "print",
                        lambda *a, **kw: printed.append(
                            "".join(str(x) for x in a)))
    monkeypatch.setattr(ai.model_picker, "can_pick", lambda: False)

    old = chat(2)
    ai._keep_session(old)
    kept_id = ai._session.id
    ai._start_session([])

    messages = [{"role": "user", "content": "something else"}]
    ai._keep_session(messages)
    ai._resume_command("question", messages)

    assert ai._session.id == kept_id
    assert messages == sessions.load(kept_id).messages
    assert any("Picked up: question 0" in p for p in printed)

    ai._fork_command("1", messages)
    assert [m["content"] for m in messages if m["role"] == "user"] == [
        "question 0"]
    printed.clear()
    ai._forks_command("", messages)
    assert any("main" in p for p in printed)
    ai._forks_command("main", messages)
    assert len(messages) == len(old)
