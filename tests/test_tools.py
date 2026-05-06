import subprocess
from shellmind.tools import shell_tool

def test_shell_tool_timeout(monkeypatch):
    # This might be tricky to test without actually waiting 15 seconds
    # but we can mock subprocess.run to raise TimeoutExpired
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 15)

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = shell_tool("long_command")
    assert "Command timed out" in result
    assert "non-interactively" in result

def test_shell_tool_success(monkeypatch):
    class MockResult:
        stdout = "success"
        stderr = ""
        returncode = 0

    def mock_run(*args, **kwargs):
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = shell_tool("echo success")
    assert result == "success"
