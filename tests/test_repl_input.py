# pylint: disable=C0114,C0115,C0116

import threading
import time

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


# --- keys ------------------------------------------------------------------

ALT_ENTER = "\x1b\r"
SHIFT_TAB = "\x1b[Z"
CTRL_O = "\x0f"


def _keyed(monkeypatch, pipe, tmp_path):
    monkeypatch.setattr(repl_input, "_carried", "")
    monkeypatch.setattr(
        repl_input, "_session",
        PromptSession(
            input=pipe,
            output=DummyOutput(),
            key_bindings=repl_input.key_bindings(),
            history=repl_input.SessionHistory(str(tmp_path / "history")),
        ),
    )


def _typed(monkeypatch, tmp_path, keys, history=0):
    """KEYS at a fresh prompt. With HISTORY, not before that many earlier
    lines have loaded: prompt_toolkit loads them in the background once
    the prompt starts, and a slow machine was still loading when an Up
    arrow sent straight away arrived."""

    with create_pipe_input() as pipe:
        _keyed(monkeypatch, pipe, tmp_path)

        def send():
            buffer = repl_input._session.default_buffer
            deadline = time.monotonic() + 10
            # The line being typed is the last working line; the loaded
            # history comes before it.
            while (len(buffer._working_lines) <= history
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            pipe.send_text(keys)

        if history:
            threading.Thread(target=send, daemon=True).start()
        else:
            pipe.send_text(keys)
        return repl_input.read_line("> ")


def test_alt_enter_starts_a_new_line(monkeypatch, tmp_path):
    assert _typed(  # nosec B101
        monkeypatch, tmp_path, f"one{ALT_ENTER}two\r"
    ) == "one\ntwo"


def test_a_backslash_before_enter_starts_a_new_line(monkeypatch, tmp_path):
    assert _typed(  # nosec B101
        monkeypatch, tmp_path, "one\\\rtwo\r"
    ) == "one\ntwo"


def test_enter_and_ctrl_j_still_send(monkeypatch, tmp_path):
    # WSL sends Ctrl+J for Enter, so it has to keep sending.
    assert _typed(monkeypatch, tmp_path, "hi\r") == "hi"  # nosec B101
    assert _typed(monkeypatch, tmp_path, "hi\n") == "hi"  # nosec B101


def test_shift_tab_stands_down_and_keeps_the_line(monkeypatch, tmp_path):
    result = _typed(monkeypatch, tmp_path, f"half{SHIFT_TAB}")

    assert result == repl_input.TOGGLE_AUTO  # nosec B101
    assert repl_input._take_carried() == "half"  # nosec B101


def test_ctrl_o_stands_down_for_expand(monkeypatch, tmp_path):
    assert _typed(  # nosec B101
        monkeypatch, tmp_path, CTRL_O
    ) == repl_input.EXPAND


def test_up_arrow_brings_back_the_last_line(monkeypatch, tmp_path):
    _typed(monkeypatch, tmp_path, "first\r")

    # A fresh session, as the next run of flash would make.
    assert _typed(  # nosec B101
        monkeypatch, tmp_path, "\x1b[A\r", history=1
    ) == "first"


# --- history ---------------------------------------------------------------

def test_history_survives_and_keeps_secrets_out(tmp_path):
    path = tmp_path / "nested" / "history"
    history = repl_input.SessionHistory(str(path))

    history.append_string("explain this repo")
    history.append_string("/set API_KEY sk-secret")

    reread = list(repl_input.SessionHistory(str(path)).load_history_strings())

    assert reread == ["explain this repo"]  # nosec B101
    assert "sk-secret" not in path.read_text()  # nosec B101


def test_a_broken_history_file_costs_only_the_history(tmp_path):
    # A directory where the file should be: every read and write fails.
    history = repl_input.SessionHistory(str(tmp_path))

    history.append_string("still works")

    assert list(history.load_history_strings()) == []  # nosec B101


def test_ctrl_r_searches_history(monkeypatch, tmp_path):
    _typed(monkeypatch, tmp_path, "first message\r")
    _typed(monkeypatch, tmp_path, "second message\r")

    # Ctrl+R, a few letters, Enter to take the match, Enter to send it.
    assert _typed(  # nosec B101
        monkeypatch, tmp_path, "\x12fir\r\r", history=2
    ) == "first message"


# --- shell mode --------------------------------------------------------------

def test_a_shell_line_wears_a_bang_in_the_prompts_colour():
    from flash.theme import CHEVRON

    accent, reset = "\x1b[38;5;209m", "\x1b[0m"
    plain = f"{accent}{CHEVRON} {reset}"
    remote = f"\x1b[2mgpu{reset} {plain}"
    bang = f"{accent}! {reset}"

    assert repl_input.shell_prompt(plain) == bang  # nosec B101
    assert repl_input.shell_prompt(remote).endswith(bang)  # nosec B101
    assert repl_input.shell_prompt(remote).startswith(  # nosec B101
        "\x1b[2mgpu"
    )


def _shown(text):
    from types import SimpleNamespace

    from prompt_toolkit.document import Document

    ti = SimpleNamespace(
        lineno=0, document=Document(text), fragments=[("", text)],
    )
    done = repl_input.HideShellMark().apply_transformation(ti)
    return "".join(f[1] for f in done.fragments), done


def test_the_bang_is_kept_out_of_sight_and_in_the_text():
    shown, done = _shown("!ls -la")

    assert shown == "ls -la"  # nosec B101
    # The cursor after the ! sits at the start of what is shown.
    assert done.source_to_display(1) == 0  # nosec B101
    assert done.display_to_source(0) == 1  # nosec B101
    assert _shown("ls -la")[0] == "ls -la"  # nosec B101
    assert _shown("why is it! broken")[0] == "why is it! broken"  # nosec B101


def test_a_shell_line_is_read_with_its_bang(monkeypatch, tmp_path):
    assert _typed(  # nosec B101
        monkeypatch, tmp_path, "!git status\r"
    ) == "!git status"


def test_a_shell_line_comes_back_from_history_as_one(monkeypatch, tmp_path):
    _typed(monkeypatch, tmp_path, "!ls\r")

    assert _typed(  # nosec B101
        monkeypatch, tmp_path, "\x1b[A\r", history=1
    ) == "!ls"
