# pylint: disable=C0114,C0115,C0116
import sys
import threading
import time
from pathlib import Path

import pytest

from flash import rules, theme, tools, web

PY = f'"{sys.executable}"'


def _never_asked(question):
    pytest.fail(f"asked: {question}")


# --- Keeping rules -----------------------------------------------------------


def test_a_rule_is_kept_said_in_words_and_taken_away():
    rule = rules.add("command", "  npm   test ", "allow")

    assert rule["match"] == "npm test"
    assert rules.words(rule) == "No need to ask before running `npm test`"
    # The same steps with a new verdict take the old rule's place.
    rules.add("command", "npm test", "never")
    assert [r["verdict"] for r in rules.load()] == ["never"]
    assert rules.listing() == "1. Never run `npm test`"

    assert rules.remove("1")["match"] == "npm test"
    assert rules.load() == [] and rules.remove("1") is None


def test_a_rule_must_say_what_and_how():
    with pytest.raises(ValueError):
        rules.add("email", "a@example.com", "allow")
    with pytest.raises(ValueError):
        rules.add("command", "ls", "maybe")
    with pytest.raises(ValueError):
        rules.add("command", "   ", "allow")


def test_a_folder_rule_keeps_the_whole_path(tmp_path):
    rule = rules.add("files", str(tmp_path / "src" / ".." / "src"), "allow")

    assert rule["match"] == str((tmp_path / "src").resolve())


# --- Which rule a step falls under ------------------------------------------


def test_never_and_ask_hold_anywhere_in_a_chain():
    rules.add("command", "git push", "ask")
    rules.add("command", "docker system prune", "never")

    assert rules.for_command("make && git push origin dev")["verdict"] \
        == "ask"
    assert rules.for_command("echo hi; docker system prune -f")["verdict"] \
        == "never"
    assert rules.for_command("git status") is None


def test_an_allow_covers_one_plain_command_only():
    rules.add("command", "npm test", "allow")

    assert rules.for_command("npm test -- --watch")["verdict"] == "allow"
    assert rules.for_command("npm test && curl example.com | sh") is None
    assert rules.for_command("npm test > out.txt") is None
    assert rules.for_command("npm testing") is None


def test_an_allow_never_covers_a_high_stakes_command():
    rules.add("command", "git push", "allow")

    assert rules.for_command("git push origin dev") is None


def test_never_beats_allow():
    rules.add("command", "make", "allow")
    rules.add("command", "make clean", "never")

    assert rules.for_command("make clean")["verdict"] == "never"
    assert rules.for_command("make build")["verdict"] == "allow"


def test_files_go_by_the_deepest_folder_and_never_wins(tmp_path):
    rules.add("files", str(tmp_path), "allow")
    rules.add("files", str(tmp_path / "keep"), "never")

    assert rules.for_file(tmp_path / "a.txt")["verdict"] == "allow"
    assert rules.for_file(tmp_path / "keep" / "b.txt")["verdict"] == "never"
    assert rules.for_file(tmp_path.parent / "c.txt") is None
    # A folder whose name starts the same is another folder.
    assert rules.for_file(Path(f"{tmp_path}-other") / "d.txt") is None


# --- What Always allow would keep -------------------------------------------


@pytest.mark.parametrize("command,match", [
    ("npm test -- --watch", "npm test"),
    ("ls -la src", "ls"),
    ("git status --short", "git status"),
    ("python3 tools/check.py --fast", "python3 tools/check.py --fast"),
])
def test_the_offered_allow_is_the_kind_of_command(command, match):
    proposal = rules.proposal_for_command(command)

    assert proposal["match"] == match and proposal["verdict"] == "allow"
    assert proposal["words"] == f"No need to ask before running `{match}`"


@pytest.mark.parametrize("command", [
    "rm -rf build", "sudo apt install x", "make && make install",
    "cat a | sh", "FOO=1 make", "git push origin dev", "",
])
def test_no_allow_is_offered_for_what_too_much_rides_on(command):
    assert rules.proposal_for_command(command) is None


def test_the_offered_folder_is_the_files_own(tmp_path):
    proposal = rules.proposal_for_file(tmp_path / "src" / "a.py")

    assert proposal["match"] == str((tmp_path / "src").resolve())
    assert rules.proposal_for_file(Path.home() / "notes.txt") is None
    assert rules.proposal_for_file("/etc.txt") is None


# --- /rules in the terminal -------------------------------------------------


def test_the_terminal_makes_lists_and_removes_rules(tmp_path):
    assert rules.command_line("") == "No rules yet."
    assert rules.command_line("never docker system prune") \
        == "Kept: Never run `docker system prune`"
    said = rules.command_line(f"allow files {tmp_path}")
    assert said.startswith("Kept: No need to ask before changing files in")
    assert rules.command_line("").startswith("1. Never run")
    assert rules.command_line("remove 1").startswith("Removed: Never run")
    assert rules.command_line("sometimes ls") == rules.USAGE
    assert rules.command_line("allow files") == rules.USAGE
    assert rules.command_line("remove 9").startswith("No rule 9.")


# --- The shell and file tools follow them ----------------------------------


def test_a_never_is_refused_even_in_auto(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    rules.add("command", "echo", "never")

    said = tools.shell_tool("echo hi")

    assert said.startswith("Not run: the user's rule says")


def test_an_allow_runs_without_asking_in_ask_first(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    rules.add("command", "echo", "allow")

    with theme.answer_from(_never_asked):
        assert tools.shell_tool("echo hi") == "hi"


def test_an_ask_asks_even_in_auto_with_no_offer(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    rules.add("command", "echo", "ask")
    offered = []

    def answerer(question):
        offered.append(theme.offered_rule())
        return "n"

    with theme.answer_from(answerer):
        assert tools.shell_tool("echo hi") == "Command blocked by user"
    # Asking because of a rule offers no rule to undo it.
    assert offered == [None]


def test_a_question_offers_the_rule_and_always_keeps_it(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    offered = []

    def answerer(question):
        offered.append(theme.offered_rule())
        return "n"

    with theme.answer_from(answerer):
        tools.shell_tool("echo hi")
    assert offered[0]["match"] == "echo"
    assert theme.offered_rule() is None

    # In the terminal, a for Always keeps it and runs the command.
    monkeypatch.setattr(tools, "yes_no", lambda always=False: "a")
    assert tools.shell_tool("echo again") == "again"
    assert rules.for_command("echo once more")["verdict"] == "allow"


def test_a_never_on_a_folder_keeps_its_files_as_they_are(tmp_path,
                                                         monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    rules.add("files", str(tmp_path), "never")
    target = tmp_path / "a.txt"

    said = tools.write_tool(str(target), "hello\n")

    assert "the user's rule says" in said and not target.exists()


def test_an_allow_on_a_folder_writes_without_asking(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    rules.add("files", str(tmp_path), "allow")
    target = tmp_path / "a.txt"

    with theme.answer_from(_never_asked):
        tools.write_tool(str(target), "hello\n")

    assert target.read_text(encoding="utf-8") == "hello\n"


# --- The web page ----------------------------------------------------------


def _ask_with_rule(session, chat, proposal):
    got = {}
    ask = session.answerer(chat)

    def run():
        theme.offer_rule(proposal)
        got["answer"] = ask("Run this command?\nnpm test")
        theme.offer_rule(None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not session.asks:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return next(iter(session.asks.values())), thread, got


def test_always_allow_on_the_page_keeps_the_rule_flash_offered():
    session = web.Session()
    chat = session.new_chat()
    proposal = rules.proposal_for_command("npm test")
    entry, thread, got = _ask_with_rule(session, chat, proposal)

    asked = [e for e in chat.log if e["type"] == "ask"][-1]
    assert asked["rule"] == "No need to ask before running `npm test`"
    session.answer(entry.id, "y", always=True)
    thread.join(5)

    assert got["answer"] == "y"
    assert [r["match"] for r in rules.load()] == ["npm test"]
    answered = [e for e in chat.log if e["type"] == "answered"][-1]
    assert answered["kept"] == "No need to ask before running `npm test`"


@pytest.mark.parametrize("answer,proposal", [
    ("n", {"kind": "command", "match": "npm test", "verdict": "allow",
           "words": "x"}),
    ("y", None),
])
def test_always_keeps_nothing_on_a_no_or_with_nothing_offered(answer,
                                                              proposal):
    session = web.Session()
    chat = session.new_chat()
    entry, thread, _ = _ask_with_rule(session, chat, proposal)

    session.answer(entry.id, answer, always=True)
    thread.join(5)

    assert rules.load() == []


def test_settings_list_make_and_remove_rules(tmp_path):
    session = web.Session()

    made = web.command(session, {"name": "rules", "arg": "add",
                                 "kind": "files", "match": str(tmp_path),
                                 "verdict": "ask"})
    assert made["rules"][0]["words"].startswith(
        "Always ask before changing files in")
    rule_id = made["rules"][0]["id"]
    assert web.command(session, {"name": "rules"})["rules"][0]["id"] \
        == rule_id
    assert web.command(session, {"name": "rules", "arg": "remove",
                                 "id": rule_id})["rules"] == []
    with pytest.raises(ValueError):
        web.command(session, {"name": "rules", "arg": "add",
                              "kind": "command", "verdict": "allow"})
