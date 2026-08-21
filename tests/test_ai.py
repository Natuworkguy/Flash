# pylint: disable=C0114,C0115,C0116

from flash.ai import (
    Config,
    _direct_shell_command,
    _int_env,
    _run_update,
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
        "flash.ai.perform_update", lambda: (True, "Flash updated.")
    )
    assert _run_update() is True  # nosec B101


def test_run_update_force_skips_check_and_confirmation(monkeypatch):
    monkeypatch.setattr("flash.ai.fetch_latest_version", lambda: None)

    def _fail_if_called():
        raise AssertionError("input should not be called with force")

    monkeypatch.setattr("builtins.input", _fail_if_called)
    monkeypatch.setattr(
        "flash.ai.perform_update", lambda: (True, "Flash updated.")
    )
    assert _run_update(force=True) is True  # nosec B101


def test_run_update_failure_propagates(monkeypatch):
    monkeypatch.setattr("flash.ai.fetch_latest_version", lambda: "99.0.0")
    monkeypatch.setattr("builtins.input", lambda: "y")
    monkeypatch.setattr(
        "flash.ai.perform_update", lambda: (False, "pipx not found.")
    )
    assert _run_update() is False  # nosec B101
