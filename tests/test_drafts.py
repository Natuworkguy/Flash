# pylint: disable=C0114,C0115,C0116
import threading
import time

import pytest

from flash import drafts, theme, tools, web

EMAIL = {"kind": "email", "to": "a@example.com", "cc": "",
         "subject": "Hi", "body": "Hello"}


# --- The draft itself ----------------------------------------------------


def test_a_draft_keeps_only_its_kinds_fields_as_text():
    kept = drafts.clean({"kind": "email", "to": " a@example.com \n",
                         "subject": "Hi\nthere", "body": "One\n\nTwo\n",
                         "evil": "x", "cc": None})

    assert kept == {"kind": "email", "to": "a@example.com", "cc": "",
                    "subject": "Hi there", "body": "One\n\nTwo"}
    assert drafts.clean({"kind": "fax", "text": "Hi"}) is None
    assert drafts.clean("To: a@example.com") is None


def test_only_a_real_change_counts_as_one():
    assert drafts.changed(EMAIL, dict(EMAIL)) is None
    assert drafts.changed(EMAIL, dict(EMAIL, body="Hello\n")) is None
    assert drafts.changed(EMAIL, None) is None
    assert drafts.changed(EMAIL, dict(EMAIL, body="Hello, Ann"))["body"] \
        == "Hello, Ann"
    # A draft of another kind is read as this one's kind.
    assert drafts.changed({"kind": "imessage", "text": "Hi"},
                          {"kind": "email", "text": "Hey"}) == {
        "kind": "imessage", "text": "Hey"}


def test_an_email_goes_to_the_editor_and_back_unchanged():
    text = drafts.as_text(dict(EMAIL, cc="c@example.com"))

    assert text.startswith("To: a@example.com\nCc: c@example.com\n")
    assert drafts.from_text(text, EMAIL) == dict(EMAIL, cc="c@example.com")


def test_an_edited_file_is_read_back_as_the_user_left_it():
    saved = ("to: b@example.com\nSubject: Lunch\n\nFriday works.\n\n"
             "To: this line is the body's now\n")

    got = drafts.from_text(saved, EMAIL)

    assert got["to"] == "b@example.com" and got["subject"] == "Lunch"
    # A header taken out keeps what it had.
    assert got["cc"] == ""
    assert got["body"] == ("Friday works.\n\n"
                           "To: this line is the body's now")


def test_an_imessage_is_its_text_alone():
    draft = {"kind": "imessage", "text": "On my way"}

    assert drafts.as_text(draft) == "On my way"
    assert drafts.from_text("Running late\n", draft)["text"] \
        == "Running late"


def test_the_editor_is_the_users_own(monkeypatch):
    monkeypatch.setenv("VISUAL", "")
    monkeypatch.setenv("EDITOR", "code --wait")
    assert drafts.editor_command() == ["code", "--wait"]


def test_editing_runs_the_editor_on_a_file_and_reads_it(monkeypatch):
    ran = []

    def editor(argv):
        ran.append(argv)
        with open(argv[-1], "w", encoding="utf-8") as file:
            file.write("To: a@example.com\nCc:\nSubject: Hi\n\nHello, Ann\n")
        return 0

    monkeypatch.setattr(drafts, "editor_command", lambda: ["ed"])
    monkeypatch.setattr(drafts.subprocess, "call", editor)

    got = drafts.edit(EMAIL)

    assert ran[0][0] == "ed" and got["body"] == "Hello, Ann"


def test_with_no_editor_it_says_how_to_set_one(monkeypatch):
    monkeypatch.setattr(drafts, "editor_command", lambda: [])
    with pytest.raises(drafts.DraftError, match="EDITOR"):
        drafts.edit(EMAIL)


# --- Asking ---------------------------------------------------------------


def test_an_answerer_sees_the_draft_and_can_send_it_changed():
    seen = []

    def answerer(question):
        seen.append(theme.offered_draft())
        theme.set_edited_draft(dict(EMAIL, body="Hello, Ann"))
        return "y"

    with theme.answer_from(answerer):
        answer, edited = tools.confirm_send("Send this email?", "To: a",
                                            EMAIL)

    assert answer == "y" and edited["body"] == "Hello, Ann"
    assert seen == [EMAIL]
    # Once asked, the draft is no longer on offer.
    assert theme.offered_draft() is None


def test_a_no_sends_nothing_back_even_with_an_edit():
    def answerer(question):
        theme.set_edited_draft(dict(EMAIL, body="Hello, Ann"))
        return "n"

    with theme.answer_from(answerer):
        assert tools.confirm_send("Send this email?", "", EMAIL) == ("n",
                                                                     None)


def test_the_terminal_opens_the_editor_on_e(monkeypatch):
    answers = iter(["e", "y"])
    monkeypatch.setattr(tools, "yes_no", lambda edit=False: next(answers))
    monkeypatch.setattr(tools.drafts, "edit",
                        lambda draft: dict(draft, subject="Lunch"))

    answer, edited = tools.confirm_send("Send this email?", "", EMAIL)

    assert answer == "y" and edited["subject"] == "Lunch"


def test_ultra_auto_does_not_ask(monkeypatch):
    monkeypatch.setattr(tools, "ULTRA_AUTO", True)
    with theme.answer_from(lambda question: pytest.fail("asked")):
        assert tools.confirm_send("Send?", "", EMAIL) == ("y", None)


# --- The web page ---------------------------------------------------------


def _ask_on_a_thread(session, chat, draft):
    got = {}
    ask = session.answerer(chat)

    def run():
        theme.offer_draft(draft)
        got["answer"] = ask("Send this email?\nTo: a@example.com")
        got["edited"] = theme.edited_draft()
        theme.offer_draft(None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not session.asks:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return next(iter(session.asks.values())), thread, got


def test_the_page_gets_the_draft_and_sends_back_the_users_version():
    session = web.Session()
    chat = session.new_chat()
    entry, thread, got = _ask_on_a_thread(session, chat, EMAIL)

    assert entry.draft == EMAIL
    assert session.answer(entry.id, "y", "",
                          dict(EMAIL, body="Hello, Ann", evil="x"))
    thread.join(5)

    assert got["answer"] == "y"
    assert got["edited"] == dict(EMAIL, body="Hello, Ann")
    asked = [e for e in chat.log if e["type"] == "ask"]
    assert asked[-1]["draft"] == EMAIL


def test_a_draft_sent_back_to_a_question_without_one_is_ignored():
    session = web.Session()
    chat = session.new_chat()
    entry, thread, got = _ask_on_a_thread(session, chat, None)

    assert entry.draft is None
    session.answer(entry.id, "y", "", dict(EMAIL, body="Sneaky"))
    thread.join(5)

    assert got == {"answer": "y", "edited": None}
