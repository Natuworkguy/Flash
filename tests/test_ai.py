from shellmind.ai import _int_env, _trim_history, _direct_shell_command, _trim_tool_output, config

def test_int_env(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "10")
    assert _int_env("TEST_VAR", 5, minimum=2) == 10

    monkeypatch.setenv("TEST_VAR", "1")
    assert _int_env("TEST_VAR", 5, minimum=2) == 2

    monkeypatch.setenv("TEST_VAR", "invalid")
    assert _int_env("TEST_VAR", 5, minimum=2) == 5

    monkeypatch.delenv("TEST_VAR", raising=False)
    assert _int_env("TEST_VAR", 5, minimum=2) == 5

def test_trim_history():
    messages = [{"role": "user", "parts": [{"text": "hello"}]}] * 10
    # config.max_history_messages is 6 by default
    _trim_history(messages)
    assert len(messages) <= config.max_history_messages

def test_direct_shell_command():
    assert _direct_shell_command("!ls") == "ls"
    assert _direct_shell_command("/shell echo hi") == "echo hi"
    assert _direct_shell_command("/run git status") == "git status"
    assert _direct_shell_command("just text") is None

def test_trim_tool_output():
    text = "a" * 2000
    trimmed = _trim_tool_output(text)
    assert "truncated" in trimmed
    assert len(trimmed) < 2000

    short_text = "hello"
    assert _trim_tool_output(short_text) == "hello"

    empty_text = ""
    assert _trim_tool_output(empty_text) == "(no output)"
