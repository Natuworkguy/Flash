from shellmind.ai import (
    _int_env,
    _trim_history,
    _direct_shell_command,
    _trim_tool_output,
    config
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
    messages = [{"role": "user", "parts": [{"text": "hello"}]}] * 10
    # config.max_history_messages is 6 by default
    _trim_history(messages)
    assert len(messages) <= config.max_history_messages  # nosec B101


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
