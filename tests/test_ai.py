# pylint: disable=C0114,C0115,C0116

import os
import time

import pytest

from flash import ai, context
from flash.ai import (
    Config,
    _direct_shell_command,
    _int_env,
    _message,
    _run_update,
    _speak_reply,
    _trim_history,
)
from flash.repl_input import WAKE
from flash.tools import trim_tool_output


def test_int_env(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "10")
    assert _int_env("TEST_VAR", 5, minimum=2) == 10  # nosec B101

    monkeypatch.setenv("TEST_VAR", "1")
    assert _int_env("TEST_VAR", 5, minimum=2) == 2  # nosec B101

    monkeypatch.setenv("TEST_VAR", "invalid")
    assert _int_env("TEST_VAR", 5, minimum=2) == 5  # nosec B101

    monkeypatch.delenv("TEST_VAR", raising=False)
    assert _int_env("TEST_VAR", 5, minimum=2) == 5  # nosec B101


@pytest.fixture
def budget(monkeypatch):
    """A small, fixed history budget, so a test is not at the mercy of
    whichever model happens to be configured."""

    monkeypatch.setattr(ai, "_history_budget", lambda: 200)


def test_trim_history_fits_the_budget(budget):
    messages = [
        {"role": "user", "content": f"message {i} " + "x" * 400}
        for i in range(10)
    ]

    _trim_history(messages)

    assert context.total_tokens(messages) <= 200  # nosec B101
    assert messages  # nosec B101


def test_trim_history_keeps_the_newest_turn(budget):
    messages = [
        {"role": "user", "content": f"message {i} " + "x" * 400}
        for i in range(10)
    ]
    newest = messages[-1]

    _trim_history(messages)

    assert newest in messages  # nosec B101


def test_trim_history_hands_back_what_it_dropped(budget):
    messages = [
        {"role": "user", "content": f"message {i} " + "x" * 400}
        for i in range(10)
    ]
    before = len(messages)

    dropped = _trim_history(messages)

    assert dropped  # nosec B101
    assert len(dropped) + len(messages) == before  # nosec B101


def test_trim_history_never_orphans_a_tool_result(budget):
    messages = []
    for index in range(8):
        messages.extend([
            {"role": "user", "content": f"q{index} " + "x" * 300},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "grep", "arguments": {}}}
            ]},
            {"role": "tool", "content": "m" * 300, "tool_name": "grep"},
            {"role": "assistant", "content": f"a{index}"},
        ])

    _trim_history(messages)

    for position, message in enumerate(messages):
        if message.get("role") == "tool":
            before = messages[position - 1] if position else {}
            assert before.get("tool_calls")  # nosec B101


def test_history_caps_are_unset_by_default(monkeypatch):
    monkeypatch.delenv("MAX_HISTORY_MESSAGES", raising=False)
    monkeypatch.delenv("MAX_HISTORY_CHARS", raising=False)
    Config.refresh()

    assert Config.max_history_messages is None  # nosec B101
    assert Config.max_history_chars is None  # nosec B101


def test_an_explicit_message_cap_is_still_honoured(monkeypatch, budget):
    monkeypatch.setenv("MAX_HISTORY_MESSAGES", "3")
    Config.refresh()
    try:
        messages = [
            {"role": "user", "content": f"m{i}"} for i in range(10)
        ]
        _trim_history(messages)

        assert len(messages) == 3  # nosec B101
    finally:
        monkeypatch.delenv("MAX_HISTORY_MESSAGES", raising=False)
        Config.refresh()


def test_an_explicit_char_cap_is_still_honoured(monkeypatch, budget):
    monkeypatch.setenv("MAX_HISTORY_CHARS", "1000")
    Config.refresh()
    try:
        messages = [
            {"role": "user", "content": "x" * 400} for _ in range(10)
        ]
        _trim_history(messages)

        total = sum(len(m["content"]) for m in messages)

        assert total <= 1000 or len(messages) == 1  # nosec B101
    finally:
        monkeypatch.delenv("MAX_HISTORY_CHARS", raising=False)
        Config.refresh()


def test_direct_shell_command():
    assert _direct_shell_command("!ls") == "ls"  # nosec B101
    assert _direct_shell_command("!echo hi") == "echo hi"  # nosec B101
    assert _direct_shell_command("!git status") == "git status"  # nosec B101
    assert _direct_shell_command("just text") is None  # nosec B101


def test_trim_tool_output():
    text = "a" * 2000
    trimmed = trim_tool_output(text)
    assert "truncated" in trimmed  # nosec B101
    assert len(trimmed) < 2000  # nosec B101

    short_text = "hello"
    assert trim_tool_output(short_text) == "hello"  # nosec B101

    empty_text = ""
    assert trim_tool_output(empty_text) == "(no output)"  # nosec B101


def test_run_update_network_failure(monkeypatch):
    monkeypatch.setattr("flash.ai.fetch_latest_version", lambda: None)
    assert _run_update() is False  # nosec B101


def test_run_update_already_up_to_date(monkeypatch):
    monkeypatch.setattr(
        "flash.ai.fetch_latest_version", lambda: "0.0.1"
    )

    def _fail_if_called(**_kwargs):
        raise AssertionError("perform_update should not run")

    monkeypatch.setattr("flash.ai.perform_update", _fail_if_called)
    assert _run_update() is True  # nosec B101


def test_run_update_declined(monkeypatch):
    monkeypatch.setattr("flash.ai.fetch_latest_version", lambda: "99.0.0")
    monkeypatch.setattr("builtins.input", lambda: "n")

    def _fail_if_called(**_kwargs):
        raise AssertionError("perform_update should not run")

    monkeypatch.setattr("flash.ai.perform_update", _fail_if_called)
    assert _run_update() is True  # nosec B101


def test_run_update_confirmed(monkeypatch):
    monkeypatch.setattr("flash.ai.fetch_latest_version", lambda: "99.0.0")
    monkeypatch.setattr("builtins.input", lambda: "y")
    monkeypatch.setattr(
        "flash.ai.perform_update", lambda **_kw: (True, "Flash updated.")
    )
    assert _run_update() is True  # nosec B101


def test_run_update_force_skips_check_and_confirmation(monkeypatch):
    monkeypatch.setattr("flash.ai.fetch_latest_version", lambda: None)

    def _fail_if_called():
        raise AssertionError("input should not be called with force")

    monkeypatch.setattr("builtins.input", _fail_if_called)
    monkeypatch.setattr(
        "flash.ai.perform_update", lambda **_kw: (True, "Flash updated.")
    )
    assert _run_update(force=True) is True  # nosec B101


def test_run_update_failure_propagates(monkeypatch):
    monkeypatch.setattr("flash.ai.fetch_latest_version", lambda: "99.0.0")
    monkeypatch.setattr("builtins.input", lambda: "y")
    monkeypatch.setattr(
        "flash.ai.perform_update", lambda **_kw: (False, "pipx not found.")
    )
    assert _run_update() is False  # nosec B101


def test_message_without_images():
    message = _message("user", "hello")
    assert message == {"role": "user", "content": "hello"}  # nosec B101
    assert "images" not in message  # nosec B101


def test_message_with_images():
    message = _message("user", "what is this", ["photo.png"])
    assert message["images"] == ["photo.png"]  # nosec B101
    assert message["content"] == "what is this"  # nosec B101


def test_speak_reply_stays_quiet_when_voice_is_off(monkeypatch):
    monkeypatch.setattr(Config, "voice", False)
    monkeypatch.setattr(ai, "speak", _refuse_to_speak)

    assert _speak_reply("all done") is False  # nosec B101


def _refuse_to_speak(_text):
    raise AssertionError("nothing should be spoken")


def test_speak_reply_takes_the_next_turn_by_voice(monkeypatch):
    said = []
    monkeypatch.setattr(Config, "voice", True)
    monkeypatch.setattr(
        ai, "speak", lambda text: (said.append(text), ("", False))[1]
    )

    assert _speak_reply("All done. See `main.py`.") is True  # nosec B101
    assert said == ["All done. See main.py."]  # nosec B101


def test_speak_reply_stays_quiet_for_a_typed_turn(monkeypatch):
    monkeypatch.setattr(Config, "voice", True)
    monkeypatch.setattr(ai, "speak", _refuse_to_speak)

    # Voice mode armed is not the user talking: typing "hi" gets a written
    # answer and the prompt back, not speech and a live microphone.
    assert _speak_reply("hi there", heard=False) is False  # nosec B101


def test_session_system_prompt_only_says_it_is_heard_when_spoken_to(
    monkeypatch,
):
    monkeypatch.setattr(Config, "voice", True)
    monkeypatch.setattr(ai, "build_system_prompt", lambda _base: "BASE")
    monkeypatch.setattr(ai, "_model_system_prompts", {"": ""})

    assert ai._session_system_prompt(False) == "BASE"  # nosec B101
    assert ai._session_system_prompt(True).startswith("BASE")  # nosec B101
    assert ai.VOICE_PROMPT in ai._session_system_prompt(True)  # nosec B101


def test_speak_reply_hands_back_the_prompt_when_it_cannot_speak(monkeypatch):
    warned = []
    monkeypatch.setattr(Config, "voice", True)
    monkeypatch.setattr(
        ai, "speak", lambda text: ("no audio device", False)
    )
    monkeypatch.setattr(ai, "warn", warned.append)

    assert _speak_reply("all done") is False  # nosec B101
    assert warned == ["no audio device"]  # nosec B101


def test_speak_reply_keeps_listening_after_a_reply_with_nothing_to_say(
    monkeypatch,
):
    monkeypatch.setattr(Config, "voice", True)
    monkeypatch.setattr(ai, "speak", _refuse_to_speak)

    # for_speech() drops an empty reply entirely; the turn still passes
    # back to the user rather than dropping out of the conversation.
    assert _speak_reply("   ") is True  # nosec B101


# --- waking up for a finished sub-agent ------------------------------------


def _script_main(monkeypatch, lines, chat_err=None):
    """Run ai.main() against scripted input and a fake model.

    Returns the user-message contents the model was sent, in order.
    """

    from flash import agent
    from flash.cli import parse_args

    feed = iter(lines)
    sent = []

    def fake_read_line(prompt, wake=None, status=None, health=None):
        try:
            line = next(feed)
        except StopIteration:
            raise EOFError from None
        return line() if callable(line) else line

    def fake_chat(console, client, messages, tools_arg, **kwargs):
        sent.append(messages[-1]["content"])
        return "reply", "", [], chat_err

    monkeypatch.setattr(ai, "parse_args", lambda: parse_args([]))
    monkeypatch.setattr(ai, "check_for_update", lambda: None)
    monkeypatch.setattr(ai, "read_line", fake_read_line)
    monkeypatch.setattr(ai, "_chat_retry_until_response", fake_chat)
    monkeypatch.setattr(ai, "_session_system_prompt", lambda heard=False: "")
    monkeypatch.setattr(ai, "notify_reply_ready", lambda: None)
    monkeypatch.setattr(ai.Config, "model", "test-model")
    monkeypatch.setattr(ai.Config, "show_stats", False)
    monkeypatch.setattr(ai.Config, "voice", False)
    monkeypatch.setattr(agent, "_agents", {})

    return agent, sent


def test_wakes_to_report_a_finished_subagent(monkeypatch):
    def finish_while_prompting():
        agent._agents["28a965"] = agent.SubAgent(
            id="28a965", task="superconductors", status=agent.DONE,
            result="LK-99 did not hold up.",
        )
        return WAKE

    agent, sent = _script_main(monkeypatch, [finish_while_prompting])

    ai.main()

    assert len(sent) == 1  # nosec B101
    assert "LK-99 did not hold up." in sent[0]  # nosec B101
    assert sent[0].endswith(ai.WAKE_NOTE)  # nosec B101
    assert agent.unseen() == []  # nosec B101


def test_finished_mid_turn_wakes_without_waiting_for_input(monkeypatch):
    agent, sent = _script_main(monkeypatch, ["research it"])
    real_chat = ai._chat_retry_until_response

    def chat_that_finishes_an_agent(*args, **kwargs):
        agent._agents["d4e5f6"] = agent.SubAgent(
            id="d4e5f6", task="t", status=agent.DONE, result="found it",
        )
        monkeypatch.setattr(ai, "_chat_retry_until_response", real_chat)
        return real_chat(*args, **kwargs)

    monkeypatch.setattr(
        ai, "_chat_retry_until_response", chat_that_finishes_an_agent
    )

    ai.main()

    assert len(sent) == 2  # nosec B101
    assert "found it" in sent[1]  # nosec B101


def test_wakes_stop_after_the_cap(monkeypatch):
    agent, sent = _script_main(monkeypatch, [], chat_err="backend down")
    agent._agents["x"] = agent.SubAgent(
        id="x", task="t", status=agent.DONE, result="answer",
    )

    ai.main()

    assert len(sent) == ai.MAX_WAKES_IN_A_ROW  # nosec B101
    assert agent.unseen()  # nosec B101  -- still waiting for the user


# --- what the user ran in VS Code's terminal -------------------------------


def _ran(*rows):
    from flash import terminal

    terminal.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with terminal.LOG_PATH.open("a") as handle:
        for code, command in rows:
            handle.write(f"{time.time()}\t{code}\t1\t/proj\t{command}\n")


def test_terminal_commands_ride_on_the_next_message_once(monkeypatch):
    from flash import terminal

    def second_message():
        # Windows' clock ticks every ~15ms, so a command logged right
        # after the first message can share its timestamp; a real one is
        # always typed later than that.
        time.sleep(0.05)
        _ran((0, "git status"))
        return "and now?"

    _, sent = _script_main(monkeypatch, ["why did that fail?", second_message,
                                         "thanks"])
    _ran((1, "npm test"))

    ai.main()

    assert sent[0].startswith(terminal.HEADER)  # nosec B101
    assert "✗ exit 1 · 1s · /proj · npm test" in sent[0]  # nosec B101
    assert sent[0].endswith("why did that fail?")  # nosec B101
    assert "npm test" not in sent[1] and "git status" in sent[1]  # nosec
    assert sent[2] == "thanks"  # nosec B101


def test_a_failed_request_keeps_the_commands_for_the_retry(monkeypatch):
    _, sent = _script_main(monkeypatch, ["first", "retry"],
                           chat_err="backend down")
    _ran((2, "make build"))

    ai.main()

    assert "make build" in sent[0] and "make build" in sent[1]  # nosec


def test_hook_command_installs_after_asking(monkeypatch, isolated_home,
                                            capsys):
    from flash import terminal

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(ai, "confirm", lambda question: True)
    _script_main(monkeypatch, ["/hook", "/hook install", "/hook",
                               "/hook remove"])

    ai.main()

    out = capsys.readouterr().out
    assert "Not set up." in out  # nosec B101
    assert "Added to" in out  # nosec B101
    assert "Set up in" in out  # nosec B101
    assert "Removed from" in out  # nosec B101
    assert terminal.BEGIN not in (isolated_home / ".zshrc").read_text()


def test_hook_command_declined_changes_nothing(monkeypatch, isolated_home):
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(ai, "confirm", lambda question: False)
    _script_main(monkeypatch, ["/hook install"])

    ai.main()

    assert not (isolated_home / ".bashrc").exists()  # nosec B101


def test_hook_command_unsupported_shell(monkeypatch, capsys):
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    _script_main(monkeypatch, ["/hook install"])

    ai.main()

    assert "supports zsh and bash" in capsys.readouterr().out  # nosec


# --- commands the user ran with ! ------------------------------------------


needs_posix = pytest.mark.skipif(os.name == "nt", reason="POSIX shell syntax")


@needs_posix
def test_a_bang_command_rides_on_the_next_message(monkeypatch):
    from flash import tools

    failing = "sh -c 'echo \"fatal: main is not a remote\" >&2; exit 128'"
    _, sent = _script_main(monkeypatch, [f"!{failing}", "why did that fail",
                                         "thanks"])

    ai.main()

    first = sent[0]
    assert first.startswith(tools.USER_RUNS_HEADER)  # nosec B101
    assert f"$ {failing}\nexit 128:" in first  # nosec B101
    assert "fatal: main is not a remote" in first  # nosec B101
    assert first.endswith("why did that fail")  # nosec B101
    assert sent[1] == "thanks"  # nosec B101


@needs_posix
def test_bang_commands_survive_a_failed_request(monkeypatch):
    _, sent = _script_main(monkeypatch, ["!echo hello", "first", "retry"],
                           chat_err="backend down")

    ai.main()

    assert "$ echo hello" in sent[0] and "$ echo hello" in sent[1]  # nosec


def test_only_the_latest_bang_commands_are_kept(monkeypatch):
    from flash import tools

    monkeypatch.setattr(tools, "_user_runs", [])
    for number in range(5):
        tools._note_user_run(f"cmd {number}", "exit 0", f"out {number}")

    block = tools.user_runs()

    assert "cmd 1" not in block and "cmd 2" in block  # nosec B101
    assert "cmd 4" in block  # nosec B101
    tools.clear_user_runs()
    assert tools.user_runs() == ""  # nosec B101


def test_a_bang_command_with_no_output_or_a_timeout(monkeypatch):
    from flash import tools

    monkeypatch.setattr(tools, "_user_runs", [])
    tools._note_user_run("touch x", "exit 0", "")
    tools._note_user_run("sleep 999", "timed out after 600s", "")

    block = tools.user_runs()

    assert "$ touch x\nexit 0\n" in block  # nosec B101
    assert "$ sleep 999\ntimed out after 600s" in block  # nosec B101
