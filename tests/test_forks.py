# pylint: disable=C0114,C0115,C0116

import pytest

from flash import web


def a_chat(session):
    """A chat of three turns, as the page has it and the model saw it."""

    chat = session.new_chat()
    for n in range(3):
        chat.log += [
            {"type": "user", "text": f"question {n}", "turn": f"t{n}"},
            {"type": "assistant", "text": f"answer {n}"},
            {"type": "stats", "tokens": 1},
        ]
        chat.messages += [
            {"role": "user", "content": f"question {n}", "turn": f"t{n}"},
            {"role": "assistant", "content": f"answer {n}"},
        ]
    return chat


def test_a_fork_goes_on_from_a_reply_in_the_same_chat():
    session = web.Session()
    chat = a_chat(session)

    said = web.command(session, {"name": "fork", "arg": "1",
                                 "chat": chat.id})

    # Kept up to the end of the first turn, its stats and all.
    assert [e["type"] for e in chat.log] == ["user", "assistant", "stats"]
    assert chat.messages[-1]["content"] == "answer 0"  # nosec B101
    assert chat.fork == said["fork"] and chat.summary()["forks"] == 2
    tree = {f["id"]: f for f in said["forks"]}
    assert tree["main"]["messages"] == 6  # nosec B101
    assert tree[said["fork"]]["parent"] == "main"  # nosec B101
    assert tree[said["fork"]]["label"] == "answer 0"  # nosec B101
    assert tree[said["fork"]]["current"]  # nosec B101


def test_forks_of_forks_and_going_back():
    session = web.Session()
    chat = a_chat(session)
    first = session.fork(chat, 4)            # after the second turn
    chat.log.append({"type": "user", "text": "another way"})
    chat.messages.append({"role": "user", "content": "another way"})
    second = session.fork(chat, 1)           # a fork of the fork

    tree = {f["id"]: f for f in session.fork_tree(chat)}
    assert tree[second]["parent"] == first  # nosec B101
    assert tree[first]["parent"] == "main"  # nosec B101

    session.switch_fork(chat, "main")
    assert len(chat.log) == 9 and len(chat.messages) == 6  # nosec B101
    session.switch_fork(chat, first)
    assert chat.log[-1]["text"] == "another way"  # nosec B101

    # Saved and read back, every fork comes too.
    back = web.Chat.restore(chat.saved())
    assert set(back.forks) == {"main", first, second}  # nosec B101
    assert back.fork == first  # nosec B101

    with pytest.raises(ValueError, match="not in this chat"):
        session.switch_fork(chat, "nope")


def test_a_busy_chat_is_not_forked():
    session = web.Session()
    chat = a_chat(session)
    chat.busy = True
    with pytest.raises(ValueError, match="Wait"):
        session.fork(chat, 1)


def test_deleting_a_fork_moves_its_forks_up_and_shows_where_it_came_from():
    session = web.Session()
    chat = a_chat(session)
    first = session.fork(chat, 4)
    chat.log.append({"type": "user", "text": "another way"})
    chat.messages.append({"role": "user", "content": "another way"})
    second = session.fork(chat, 1)
    session.switch_fork(chat, first)

    said = web.command(session, {"name": "fork-delete", "arg": first,
                                 "chat": chat.id})

    # The one showing went: the chat is back on main, as main had it.
    tree = {f["id"]: f for f in said["forks"]}
    assert set(tree) == {"main", second}  # nosec B101
    assert tree[second]["parent"] == "main"  # nosec B101
    assert chat.fork == "main" and len(chat.log) == 9  # nosec B101
    assert len(chat.messages) == 6  # nosec B101

    # With one fork left, the chat has none: the Forks button goes.
    session.delete_fork(chat, second)
    assert chat.forks == {} and chat.fork == ""  # nosec B101
    assert chat.summary()["forks"] == 0  # nosec B101
    assert len(chat.log) == 9  # nosec B101
    with pytest.raises(ValueError, match="not in this chat"):
        session.delete_fork(chat, second)


def test_deleting_main_keeps_the_rest_under_the_oldest():
    session = web.Session()
    chat = a_chat(session)
    first = session.fork(chat, 1)
    session.switch_fork(chat, "main")
    second = session.fork(chat, 4)

    session.delete_fork(chat, "main")

    tree = {f["id"]: f for f in session.fork_tree(chat)}
    assert tree[first]["parent"] == ""  # nosec B101
    assert tree[second]["parent"] == first  # nosec B101
    assert chat.fork == second  # nosec B101

    chat.busy = True
    with pytest.raises(ValueError, match="Wait"):
        session.delete_fork(chat, first)
