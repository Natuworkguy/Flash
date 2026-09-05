# pylint: disable=C0114,C0115,C0116

from flash import ai
from flash.ai import (
    Config,
    _direct_shell_command,
    _int_env,
    _message,
    _run_update,
    _speak_reply,
    _trim_history,
    _trim_tool_output,
)


def test_int_env(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "10")
    assert _int_env("TEST_VAR", 5, minimum=2) == 10  # nosec B101

    monkeypatch.setenv("TEST_VAR", "1")
    assert _int_env("TEST_VAR", 5, minimum=2) == 2  # nosec B101

    monkeypatch.setenv("TEST_VAR", "invalid")
    assert _int_env("TEST_VAR", 5, minimum=2) == 5  # nosec B101

    monkeypatch.delenv("TEST_VAR", raising=False)
    assert _int_env("TEST_VAR", 5, minimum=2) == 5  # nosec B101


def test_trim_history():
    messages = [{"role": "user", "content": "hello"}] * 10
    # config.max_history_messages is 6 by default
    _trim_history(messages)
    assert len(messages) <= Config.max_history_messages  # nosec B101


def test_direct_shell_command():
    assert _direct_shell_command("!ls") == "ls"  # nosec B101
    assert _direct_shell_command("!echo hi") == "echo hi"  # nosec B101
    assert _direct_shell_command("!git status") == "git status"  # nosec B101
    assert _direct_shell_command("just text") is None  # nosec B101


def test_trim_tool_output():
    text = "a" * 2000
    trimmed = _trim_tool_output(text)
    assert "truncated" in trimmed  # nosec B101
    assert len(trimmed) < 2000  # nosec B101

    short_text = "hello"
    assert _trim_tool_output(short_text) == "hello"  # nosec B101

    empty_text = ""
    assert _trim_tool_output(empty_text) == "(no output)"  # nosec B101


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
