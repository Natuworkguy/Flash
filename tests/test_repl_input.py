# pylint: disable=C0114,C0115,C0116

import threading

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from flash import repl_input
from flash.repl_input import SlashCommandCompleter, _mention_before


def _completions(text, tmp_path, monkeypatch):
    """What the dropdown offers for TEXT, typed from inside TMP_PATH."""

    monkeypatch.chdir(tmp_path)
    document = Document(text, cursor_position=len(text))

    return [
        completion.text
        for completion in SlashCommandCompleter().get_completions(
            document, CompleteEvent()
        )
    ]


def _tree(tmp_path):
    (tmp_path / "notes.md").write_text("hi", encoding="utf-8")
    (tmp_path / "main.py").write_text("hi", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep.py").write_text("hi", encoding="utf-8")


def test_mention_before_starts_at_a_bare_at():
    assert _mention_before("@") == ""  # nosec B101
    assert _mention_before("read @src/mo") == "src/mo"  # nosec B101
    assert _mention_before("") is None  # nosec B101
    assert _mention_before("nothing here") is None  # nosec B101


def test_mention_before_ignores_an_at_inside_a_word():
    assert _mention_before("mail me@example.com") is None  # nosec B101
    assert _mention_before("a@b") is None  # nosec B101


def test_at_lists_the_working_directory(tmp_path, monkeypatch):
    _tree(tmp_path)

    assert sorted(  # nosec B101
        _completions("@", tmp_path, monkeypatch)
    ) == ["main.py", "notes.md", "src"]


def test_at_narrows_as_the_path_is_typed(tmp_path, monkeypatch):
    _tree(tmp_path)

    assert _completions(  # nosec B101
        "@no", tmp_path, monkeypatch
    ) == ["tes.md"]


def test_at_walks_into_a_directory(tmp_path, monkeypatch):
    _tree(tmp_path)

    assert _completions(  # nosec B101
        "@src/", tmp_path, monkeypatch
    ) == ["deep.py"]


def test_at_completes_mid_sentence(tmp_path, monkeypatch):
    _tree(tmp_path)

    assert _completions(  # nosec B101
        "what does @main", tmp_path, monkeypatch
    ) == [".py"]


def test_at_stops_at_the_space_after_a_path(tmp_path, monkeypatch):
    _tree(tmp_path)

    assert _completions(  # nosec B101
        "@main.py do what", tmp_path, monkeypatch
    ) == []


def test_at_quotes_a_path_with_a_space(tmp_path, monkeypatch):
    (tmp_path / "my notes.md").write_text("hi", encoding="utf-8")

    # The whole mention is replaced, not appended to, so the quotes end
    # up around the path rather than in the middle of it.
    assert _completions(  # nosec B101
        "@my", tmp_path, monkeypatch
    ) == ['"my notes.md"']


def test_slash_commands_still_complete(tmp_path, monkeypatch):
    assert "/model" in _completions(  # nosec B101
        "/mod", tmp_path, monkeypatch
    )


def test_an_email_address_does_not_open_the_dropdown(tmp_path, monkeypatch):
    _tree(tmp_path)

    assert _completions(  # nosec B101
        "write to me@example.com", tmp_path, monkeypatch
    ) == []


def test_at_hides_dot_entries_until_one_is_asked_for(tmp_path, monkeypatch):
    _tree(tmp_path)
    (tmp_path / ".env").write_text("hi", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    listed = _completions("@", tmp_path, monkeypatch)

    assert sorted(listed) == ["main.py", "notes.md", "src"]  # nosec B101
    assert _completions(  # nosec B101
        "@.e", tmp_path, monkeypatch
    ) == ["nv"]


# --- waking the prompt -----------------------------------------------------

def _piped_session(monkeypatch, pipe):
    monkeypatch.setattr(repl_input, "WAKE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(
        repl_input, "_session",
        PromptSession(input=pipe, output=DummyOutput()),
    )


def test_read_line_gives_way_to_wake_on_an_empty_line(monkeypatch):
    with create_pipe_input() as pipe:
        _piped_session(monkeypatch, pipe)
        result = repl_input.read_line("> ", wake=lambda: True)
    assert result == repl_input.WAKE  # nosec B101


def test_read_line_never_wakes_over_typed_text(monkeypatch):
    with create_pipe_input() as pipe:
        _piped_session(monkeypatch, pipe)
        pipe.send_text("half a thought")
        # Submit only after the poller has had many chances to wake.
        threading.Timer(0.3, lambda: pipe.send_text("\r")).start()
        result = repl_input.read_line("> ", wake=lambda: True)
    assert result == "half a thought"  # nosec B101


def test_read_line_without_wake_reads_normally(monkeypatch):
    with create_pipe_input() as pipe:
        _piped_session(monkeypatch, pipe)
        pipe.send_text("hello\r")
        assert repl_input.read_line("> ") == "hello"  # nosec B101
