# pylint: disable=C0114,C0115,C0116

import subprocess  # nosec B404

from flash import images, tools
from flash.tools import (
    glob_tool,
    grep_tool,
    shell_tool,
    take_pending_images,
    view_image,
)


def test_shell_tool_timeout(monkeypatch):
    # This might be tricky to test without actually waiting 15 seconds
    # but we can mock subprocess.run to raise TimeoutExpired
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 15)

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = shell_tool("long_command")
    assert "Command timed out" in result  # nosec B101
    assert "non-interactively" in result  # nosec B101


def test_shell_tool_success(monkeypatch):
    class MockResult:
        stdout = "success"
        stderr = ""
        returncode = 0

    def mock_run(*_, **__):
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = shell_tool("echo success")
    assert result == "success"  # nosec B101


def test_glob_tool_finds_matching_files(tmp_path):
    (tmp_path / "a.py").write_text("print(1)")
    (tmp_path / "b.txt").write_text("not python")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("print(2)")

    result = glob_tool("*.py", str(tmp_path))
    assert "a.py" in result  # nosec B101
    assert "sub/c.py" in result  # nosec B101
    assert "b.txt" not in result  # nosec B101


def test_glob_tool_missing_path():
    result = glob_tool("*.py", "/no/such/directory")
    assert "not found" in result  # nosec B101


def test_grep_tool_finds_matches(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n")

    result = grep_tool("def foo", str(tmp_path))
    assert "a.py:1:" in result  # nosec B101
    assert "b.py" not in result  # nosec B101


def test_grep_tool_glob_filter(tmp_path):
    (tmp_path / "a.py").write_text("target\n")
    (tmp_path / "a.txt").write_text("target\n")

    result = grep_tool("target", str(tmp_path), glob_filter="*.py")
    assert "a.py:1:" in result  # nosec B101
    assert "a.txt" not in result  # nosec B101


def test_grep_tool_case_insensitive(tmp_path):
    (tmp_path / "a.py").write_text("HELLO world\n")

    assert "No matches" in grep_tool("hello", str(tmp_path))  # nosec B101
    result = grep_tool("hello", str(tmp_path), case_insensitive=True)
    assert "HELLO world" in result  # nosec B101


def test_grep_tool_invalid_regex():
    result = grep_tool("(", ".")
    assert "invalid regex" in result  # nosec B101


def test_grep_tool_missing_path():
    result = grep_tool("x", "/no/such/directory")
    assert "not found" in result  # nosec B101


def test_view_image_queues_the_bytes(tmp_path, monkeypatch):
    take_pending_images()
    # No model name means no /api/show call, keeping the test offline.
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    image = tmp_path / "shot.png"
    data = b"not really a png, but the tool only checks the file"
    image.write_bytes(data)

    result = view_image(str(image))
    assert "Attached shot.png" in result  # nosec B101
    assert take_pending_images() == [data]  # nosec B101
    assert take_pending_images() == []  # nosec B101


def test_view_image_missing_file(tmp_path):
    take_pending_images()

    result = view_image(str(tmp_path / "nope.png"))
    assert "Image not found" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101


def test_view_image_unsupported_type(tmp_path):
    take_pending_images()
    text_file = tmp_path / "notes.txt"
    text_file.write_text("hello")

    result = view_image(str(text_file))
    assert "Unsupported image type" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101


def test_view_image_too_large(tmp_path, monkeypatch):
    take_pending_images()
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 10)
    image = tmp_path / "big.png"
    image.write_bytes(b"x" * 100)

    result = view_image(str(image))
    assert "too large" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101


def test_view_image_refuses_a_model_without_vision(tmp_path, monkeypatch):
    take_pending_images()
    monkeypatch.setattr(tools, "MODEL_NAME", "text-only")
    monkeypatch.setattr(tools, "model_sees_images", lambda *_: False)
    image = tmp_path / "shot.png"
    image.write_bytes(b"data")

    result = view_image(str(image))
    assert "no vision support" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101
