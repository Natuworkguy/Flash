# pylint: disable=C0114,C0115,C0116

import subprocess  # nosec B404

import pytest

from flash import browser, images, tools
from flash.tools import (
    glob_tool,
    grep_tool,
    interact,
    open_page,
    read_tool,
    screenshot,
    shell_tool,
    take_pending_images,
    view_image,
    write_tool,
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


def _fake_capture(png=b"png bytes", problems=(), error=""):
    """Stand in for a real Chromium run, writing the file it promises."""

    def capture(_url, out, **_kwargs):
        if not error:
            out.write_bytes(png)

        return list(problems), error

    return capture


def test_screenshot_queues_the_rendered_page(tmp_path, monkeypatch):
    take_pending_images()
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr(tools, "capture", _fake_capture())
    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>")

    result = screenshot(str(page))
    assert "Rendered file://" in result  # nosec B101
    assert take_pending_images() == [b"png bytes"]  # nosec B101


def test_screenshot_reports_page_errors(tmp_path, monkeypatch):
    take_pending_images()
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr(
        tools,
        "capture",
        _fake_capture(problems=["page error: boom is not defined"]),
    )
    page = tmp_path / "broken.html"
    page.write_text("<script>boom()</script>")

    result = screenshot(str(page))
    assert "boom is not defined" in result  # nosec B101
    assert take_pending_images() == [b"png bytes"]  # nosec B101


def test_screenshot_clamps_an_absurd_viewport(tmp_path, monkeypatch):
    take_pending_images()
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    seen = {}

    def capture(_url, out, **kwargs):
        seen.update(kwargs)
        out.write_bytes(b"png")
        return [], ""

    monkeypatch.setattr(tools, "capture", capture)
    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>")

    screenshot(str(page), width=999999, height=0, wait_ms="soon")
    assert seen["width"] == tools.MAX_SCREENSHOT_SIDE  # nosec B101
    assert seen["height"] == tools.MIN_SCREENSHOT_SIDE  # nosec B101
    assert seen["wait_ms"] == tools.DEFAULT_SCREENSHOT_WAIT_MS  # nosec B101


def test_screenshot_surfaces_a_capture_failure(tmp_path, monkeypatch):
    take_pending_images()
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr(
        tools,
        "capture",
        _fake_capture(error=browser.BROWSER_HINT),
    )
    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>")

    result = screenshot(str(page))
    assert "playwright install chromium" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101


def test_screenshot_refuses_a_model_without_vision(tmp_path, monkeypatch):
    take_pending_images()
    monkeypatch.setattr(tools, "MODEL_NAME", "text-only")
    monkeypatch.setattr(tools, "model_sees_images", lambda *_: False)
    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>")

    result = screenshot(str(page))
    assert "no vision support" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101


def test_resolve_target_accepts_urls_and_local_pages(tmp_path):
    assert browser.resolve_target("https://example.com") == (  # nosec B101
        "https://example.com",
        "",
    )

    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>")
    url, why = browser.resolve_target(str(page))
    assert why == ""  # nosec B101
    assert url is not None  # nosec B101
    assert url.startswith("file://")  # nosec B101


def test_resolve_target_rejects_what_it_cannot_render(tmp_path):
    _, why = browser.resolve_target(str(tmp_path / "missing.html"))
    assert "Page not found" in why  # nosec B101

    _, why = browser.resolve_target(str(tmp_path))
    assert "is a directory" in why  # nosec B101

    script = tmp_path / "app.py"
    script.write_text("print('hi')")
    _, why = browser.resolve_target(str(script))
    assert "not a page Flash can render" in why  # nosec B101

    _, why = browser.resolve_target("ftp://example.com/page.html")
    assert "Cannot open a 'ftp:' URL" in why  # nosec B101


def test_read_tool_numbers_lines(tmp_path):
    target = tmp_path / "renderer.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 21)))

    result = read_tool(str(target), offset=2, limit=19)

    assert "     2\tline 2" in result  # nosec B101
    assert "    20\tline 20" in result  # nosec B101
    assert "line 1\n" not in result  # nosec B101


def test_read_tool_reports_remaining_lines(tmp_path):
    target = tmp_path / "long.txt"
    target.write_text("\n".join(str(i) for i in range(1, 11)))

    result = read_tool(str(target), limit=4)

    assert "6 more lines" in result  # nosec B101
    assert "offset=5" in result  # nosec B101


def test_read_tool_offset_past_end(tmp_path):
    target = tmp_path / "short.txt"
    target.write_text("only one line")

    result = read_tool(str(target), offset=99)

    assert "past the end" in result  # nosec B101


def test_read_tool_missing_and_directory(tmp_path):
    assert "not found" in read_tool(str(tmp_path / "nope.txt"))  # nosec B101
    assert "is a directory" in read_tool(str(tmp_path))  # nosec B101


def test_read_tool_extracts_docx_text(tmp_path):
    import docx

    target = tmp_path / "notes.docx"
    document = docx.Document()
    document.add_paragraph("Hello from docx.")
    document.save(target)

    result = read_tool(str(target))

    assert "Hello from docx." in result  # nosec B101


def test_read_tool_reports_legacy_doc(tmp_path):
    target = tmp_path / "old.doc"
    target.write_bytes(b"not a real .doc file")

    result = read_tool(str(target))

    assert "Save it as .docx" in result  # nosec B101


def test_write_tool_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    target = tmp_path / "new" / "file.py"

    result = write_tool(str(target), "print(1)\nprint(2)\n")

    assert target.read_text() == "print(1)\nprint(2)\n"  # nosec B101
    assert "Created 2 lines" in result  # nosec B101


def test_write_tool_preserves_newlines_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    target = tmp_path / "verbatim.txt"

    write_tool(str(target), "a\nb\n")

    assert target.read_bytes() == b"a\nb\n"  # nosec B101


def test_write_tool_blocked_leaves_file_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr("builtins.input", lambda: "n")
    target = tmp_path / "keep.txt"
    target.write_text("original")

    result = write_tool(str(target), "replaced")

    assert target.read_text() == "original"  # nosec B101
    assert "blocked by user" in result  # nosec B101


def test_diff_preview_counts_changes():
    preview, omitted, additions, removals = tools._diff_preview(
        "a\nb\nc", "a\nB\nc", "f.txt"
    )

    assert additions == 1  # nosec B101
    assert removals == 1  # nosec B101
    assert omitted == 0  # nosec B101
    assert "-b" in preview  # nosec B101
    assert "+B" in preview  # nosec B101


def _fake_page(monkeypatch, tmp_path, *, elements=(), problems=()):
    """Stand in for a live Chromium, writing the screenshot it promises."""

    monkeypatch.setattr(tools, "MODEL_NAME", "")
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr(tools, "model_sees_images", lambda *_: True)
    monkeypatch.setattr(tools, "page_is_open", lambda: True)
    monkeypatch.setattr(
        tools, "page_where", lambda: ("http://localhost/page", "Demo")
    )
    monkeypatch.setattr(tools, "page_elements", lambda: (list(elements), ""))
    monkeypatch.setattr(tools, "page_problems", lambda: list(problems))

    def snapshot(out, **_kwargs):
        out.write_bytes(b"png bytes")

        return ""

    monkeypatch.setattr(tools, "page_snapshot", snapshot)


def test_open_page_shows_the_page_and_what_it_can_click(tmp_path, monkeypatch):
    take_pending_images()
    _fake_page(
        monkeypatch,
        tmp_path,
        elements=['[1] button "Add one"'],
        problems=["page error: boom is not defined"],
    )
    monkeypatch.setattr(tools, "browser_open", lambda *_a, **_k: "")
    page = tmp_path / "index.html"
    page.write_text("<button>Add one</button>")

    result = open_page(str(page))

    assert "Opened file://" in result  # nosec B101
    assert '[1] button "Add one"' in result  # nosec B101
    assert "boom is not defined" in result  # nosec B101
    assert take_pending_images() == [b"png bytes"]  # nosec B101


def test_open_page_surfaces_a_browser_that_will_not_start(
    tmp_path, monkeypatch
):
    take_pending_images()
    _fake_page(monkeypatch, tmp_path)
    monkeypatch.setattr(
        tools, "browser_open", lambda *_a, **_k: browser.BROWSER_HINT
    )
    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>")

    result = open_page(str(page))

    assert "playwright install chromium" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101


def test_interact_needs_a_page_to_be_open(tmp_path, monkeypatch):
    take_pending_images()
    _fake_page(monkeypatch, tmp_path)
    monkeypatch.setattr(tools, "page_is_open", lambda: False)

    result = interact("click", selector="1")

    assert "No page is open" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101


def test_interact_shows_the_page_after_every_action(tmp_path, monkeypatch):
    take_pending_images()
    _fake_page(monkeypatch, tmp_path, elements=['[1] button "Add one"'])
    monkeypatch.setattr(
        tools, "browser_interact", lambda *_a, **_k: ("Clicked 1.", "")
    )

    result = interact("click", selector="1")

    assert "Clicked 1." in result  # nosec B101
    assert '[1] button "Add one"' in result  # nosec B101
    assert take_pending_images() == [b"png bytes"]  # nosec B101


def test_interact_still_shows_the_page_when_the_action_fails(
    tmp_path, monkeypatch
):
    take_pending_images()
    _fake_page(monkeypatch, tmp_path, elements=['[1] button "Add one"'])
    monkeypatch.setattr(
        tools,
        "browser_interact",
        lambda *_a, **_k: ("", "Nothing on the page matches 'ghost'."),
    )

    result = interact("click", selector="ghost")

    assert "did not work" in result  # nosec B101
    assert '[1] button "Add one"' in result  # nosec B101
    assert take_pending_images() == [b"png bytes"]  # nosec B101


def test_interact_close_shuts_the_browser(tmp_path, monkeypatch):
    take_pending_images()
    _fake_page(monkeypatch, tmp_path)
    closed = []
    monkeypatch.setattr(tools, "close_session", lambda: closed.append(1) or 1)

    result = interact("close")

    assert closed == [1]  # nosec B101
    assert "Closed the browser" in result  # nosec B101
    assert take_pending_images() == []  # nosec B101


class _FakeLocator:
    """The little of Playwright's locator that `_locate` leans on."""

    def __init__(self, count):
        self._count = count
        self.first = self

    def count(self):
        return self._count


class _FakePage:
    """A page where only the given selectors match anything."""

    def __init__(self, matches):
        self.matches = matches
        self.asked = []

    def locator(self, selector):
        self.asked.append(selector)

        return _FakeLocator(self.matches.get(selector, 0))

    def get_by_text(self, text):
        return _FakeLocator(self.matches.get(f"text={text}", 0))


def test_locate_takes_an_element_number():
    page = _FakePage({'[data-flash-id="3"]': 1})

    assert browser._locate(page, "3") is not None  # nosec B101


def test_locate_falls_back_from_a_selector_to_the_visible_text():
    page = _FakePage({"text=Sign in": 1})

    assert browser._locate(page, "Sign in") is not None  # nosec B101
    assert "Sign in" in page.asked  # nosec B101


def test_locate_explains_a_stale_element_number():
    page = _FakePage({})

    with pytest.raises(browser.PageProblem) as caught:
        browser._locate(page, "9")

    assert "no element 9" in str(caught.value)  # nosec B101


def test_interact_turns_down_an_action_it_does_not_have(monkeypatch):
    monkeypatch.setattr(browser, "is_open", lambda: True)

    _, why = browser.interact("frobnicate")

    assert "Unknown action" in why  # nosec B101
