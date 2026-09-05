# pylint: disable=C0114,C0115,C0116

from datetime import datetime, timedelta, timezone

import httpx
from ollama import ResponseError
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from flash import models
from flash.models import (
    Model,
    choose,
    download,
    fetch_if_missing,
    human_size,
    installed_models,
    installed_names,
    is_installed,
    pick_model,
)

DOWN = "\x1b[B"
UP = "\x1b[A"
ENTER = "\r"
CTRL_C = "\x03"
BACKSPACE = "\x7f"


class _Details:
    def __init__(self, family="gemma3", parameter_size="12.2B",
                 quantization_level="Q4_K_M"):
        self.family = family
        self.parameter_size = parameter_size
        self.quantization_level = quantization_level


class _Entry:
    def __init__(self, name, size=7_600_000_000, modified_at=None,
                 details=None):
        self.model = name
        self.size = size
        self.modified_at = modified_at
        self.details = details or _Details()


class _Listing:
    def __init__(self, entries):
        self.models = entries


class _Update:
    def __init__(self, status="", digest="", completed=0, total=0):
        self.status = status
        self.digest = digest
        self.completed = completed
        self.total = total


class FakeClient:
    """Stands in for ollama.Client: answers list() and streams pull()."""

    def __init__(self, names=(), updates=(), raises=None, listing=None):
        self.entries = [
            name if isinstance(name, _Entry) else _Entry(name)
            for name in names
        ]
        self.updates = list(updates)
        self.raises = raises
        self.listing = listing
        self.pulled = []

    def list(self):
        if self.listing is not None:
            raise self.listing
        return _Listing(self.entries)

    def pull(self, model, stream=False):
        self.pulled.append((model, stream))
        if self.raises is not None:
            raise self.raises
        return iter(self.updates)


def _fake_terminal(monkeypatch):
    """Let the picker's terminal check pass under pytest."""

    monkeypatch.setattr(models.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        type(models.console), "is_terminal", property(lambda _self: True)
    )


def _picker(text, rows=None):
    """Run the picker over ROWS, typing TEXT into it."""

    rows = rows if rows is not None else [
        Model("gemma4:12b", "gemma3, 12.2B", "7.6 GB", "active"),
        Model("llama3.1:8b", "llama, 8.0B", "4.9 GB"),
        Model("qwen3:8b", "qwen3, 8.2B", "5.2 GB"),
    ]

    with create_pipe_input() as inp:
        inp.send_text(text)
        with create_app_session(input=inp, output=DummyOutput()):
            return choose(rows)


def test_tagged_fills_in_latest():
    assert models._tagged("gemma4") == "gemma4:latest"  # nosec B101
    assert models._tagged("gemma4:12b") == "gemma4:12b"  # nosec B101
    assert (  # nosec B101
        models._tagged("natuworkguy/flash-onyx-1")
        == "natuworkguy/flash-onyx-1:latest"
    )


def test_tagged_ignores_a_port_in_the_host():
    assert (  # nosec B101
        models._tagged("localhost:5000/mine") == "localhost:5000/mine:latest"
    )


def test_installed_names_tags_what_ollama_reports():
    client = FakeClient(names=["gemma4:12b", "llama3.2:3b"])

    assert installed_names(client) == {  # nosec B101
        "gemma4:12b",
        "llama3.2:3b",
    }


def test_installed_names_is_none_when_ollama_is_down():
    client = FakeClient(listing=ConnectionError("no ollama"))

    assert installed_names(client) is None  # nosec B101


def test_is_installed_matches_an_untagged_name():
    client = FakeClient(names=["gemma4:latest"])

    assert is_installed(client, "gemma4") is True  # nosec B101
    assert is_installed(client, "gemma4:12b") is False  # nosec B101


def test_is_installed_says_nothing_when_ollama_is_down():
    client = FakeClient(listing=ConnectionError("no ollama"))

    assert is_installed(client, "gemma4") is None  # nosec B101


def test_installed_models_puts_the_active_one_first():
    client = FakeClient(names=["qwen3:8b", "gemma4:12b", "llama3.1:8b"])

    rows = installed_models(client, "llama3.1:8b")

    assert [row.name for row in rows] == [  # nosec B101
        "llama3.1:8b",
        "gemma4:12b",
        "qwen3:8b",
    ]
    assert rows[0].note == "active"  # nosec B101
    assert not any(row.note for row in rows[1:])  # nosec B101


def test_installed_models_describes_each_row():
    client = FakeClient(names=[
        _Entry(
            "gemma4:12b",
            size=7_600_000_000,
            modified_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
    ])

    row = installed_models(client)[0]

    assert row.size == "7.6 GB"  # nosec B101
    assert "gemma3" in row.summary  # nosec B101
    assert "12.2B" in row.summary  # nosec B101
    assert "Q4_K_M" in row.summary  # nosec B101
    assert "pulled 3 days ago" in row.summary  # nosec B101


def test_installed_models_is_none_when_ollama_is_down():
    client = FakeClient(listing=ConnectionError("no ollama"))

    assert installed_models(client) is None  # nosec B101


def test_installed_models_is_empty_on_a_fresh_install():
    assert installed_models(FakeClient()) == []  # nosec B101


def test_ago_reads_in_whole_units():
    now = datetime.now(timezone.utc)

    assert models._ago(None) == ""  # nosec B101
    assert models._ago(now) == "pulled just now"  # nosec B101
    assert (  # nosec B101
        models._ago(now - timedelta(hours=1)) == "pulled 1 hour ago"
    )
    assert (  # nosec B101
        models._ago(now - timedelta(days=1)) == "pulled 1 day ago"
    )
    assert (  # nosec B101
        models._ago(now - timedelta(days=9)) == "pulled 9 days ago"
    )


def test_matching_filters_on_name_and_summary():
    rows = [
        Model("gemma4:12b", "gemma3, 12.2B"),
        Model("llama3.1:8b", "llama, 8.0B"),
    ]

    assert len(models._matching(rows, "")) == 2  # nosec B101
    assert (  # nosec B101
        [row.name for row in models._matching(rows, "GEMMA")]
        == ["gemma4:12b"]
    )
    assert (  # nosec B101
        [row.name for row in models._matching(rows, "8.0B")]
        == ["llama3.1:8b"]
    )
    assert models._matching(rows, "nothing") == []  # nosec B101


def test_human_size_uses_decimal_units():
    assert human_size(512) == "512 B"  # nosec B101
    assert human_size(7_600_000_000) == "7.6 GB"  # nosec B101
    assert human_size(2_000_000) == "2.0 MB"  # nosec B101


def test_picker_returns_the_row_under_the_cursor():
    assert _picker(ENTER) == "gemma4:12b"  # nosec B101


def test_picker_moves_with_the_arrow_keys():
    assert _picker(DOWN + ENTER) == "llama3.1:8b"  # nosec B101
    assert _picker(DOWN + DOWN + ENTER) == "qwen3:8b"  # nosec B101
    assert _picker(UP + ENTER) == "qwen3:8b"  # nosec B101


def test_picker_filters_as_you_type():
    assert _picker("qwen" + ENTER) == "qwen3:8b"  # nosec B101
    assert (  # nosec B101
        _picker("qwen" + BACKSPACE * 4 + ENTER) == "gemma4:12b"
    )


def test_picker_hands_back_a_name_that_is_not_here():
    assert _picker("mistral:7b" + ENTER) == "mistral:7b"  # nosec B101


def test_picker_takes_a_name_with_nothing_installed():
    assert _picker("mistral:7b" + ENTER, rows=[]) == "mistral:7b"  # nosec B101


def test_picker_cancels_without_choosing():
    assert _picker(CTRL_C) is None  # nosec B101


def test_picker_ignores_enter_with_nothing_to_take():
    # No row under the cursor and nothing typed: Enter does nothing, so
    # an empty list cannot be dismissed into a model that is not there.
    assert _picker(ENTER + CTRL_C, rows=[]) is None  # nosec B101


def test_download_streams_every_update():
    client = FakeClient(updates=[
        _Update(status="pulling manifest"),
        _Update(digest="sha256:aa", completed=0, total=1_000_000),
        _Update(digest="sha256:aa", completed=1_000_000, total=1_000_000),
        _Update(status="success"),
    ])

    assert download(client, "gemma4:12b") is True  # nosec B101
    assert client.pulled == [("gemma4:12b", True)]  # nosec B101


def test_download_reports_a_backend_error():
    client = FakeClient(raises=ResponseError("model not found", 404))

    assert download(client, "nope:1b") is False  # nosec B101


def test_download_reports_an_unreachable_ollama():
    client = FakeClient(raises=ConnectionError("no ollama"))

    assert download(client, "gemma4:12b") is False  # nosec B101


def test_download_survives_an_interrupt():
    client = FakeClient(raises=KeyboardInterrupt())

    assert download(client, "gemma4:12b") is False  # nosec B101


def test_layers_count_only_the_bytes_that_moved():
    layers = models._Layers()

    # A layer already on disk reports itself finished on sight.
    layers.update("sha256:aa", 1_000_000, 1_000_000)
    assert layers.downloaded == 0  # nosec B101
    assert layers.size == 1_000_000  # nosec B101

    layers.update("sha256:bb", 0, 4_000_000)
    layers.update("sha256:bb", 3_000_000, 4_000_000)
    assert layers.downloaded == 3_000_000  # nosec B101
    assert layers.completed == 4_000_000  # nosec B101
    assert layers.size == 5_000_000  # nosec B101


def test_fetch_if_missing_leaves_an_installed_model_alone():
    client = FakeClient(names=["gemma4:12b"])

    assert fetch_if_missing(client, "gemma4:12b") is True  # nosec B101
    assert client.pulled == []  # nosec B101


def test_fetch_if_missing_trusts_the_name_when_ollama_is_down():
    client = FakeClient(listing=ConnectionError("no ollama"))

    assert fetch_if_missing(client, "gemma4:12b") is True  # nosec B101
    assert client.pulled == []  # nosec B101


def test_fetch_if_missing_downloads_once_confirmed(monkeypatch):
    monkeypatch.setattr(models, "confirm", lambda _question: True)
    client = FakeClient(updates=[_Update(status="success")])

    assert fetch_if_missing(client, "mistral:7b") is True  # nosec B101
    assert client.pulled == [("mistral:7b", True)]  # nosec B101


def test_fetch_if_missing_respects_a_no(monkeypatch):
    monkeypatch.setattr(models, "confirm", lambda _question: False)
    client = FakeClient()

    assert fetch_if_missing(client, "mistral:7b") is False  # nosec B101
    assert client.pulled == []  # nosec B101


def test_pick_model_returns_what_was_picked(monkeypatch):
    _fake_terminal(monkeypatch)
    monkeypatch.setattr(models, "choose", lambda _rows: "llama3.1:8b")
    client = FakeClient(names=["gemma4:12b", "llama3.1:8b"])

    assert pick_model(client, "gemma4:12b") == "llama3.1:8b"  # nosec B101
    assert client.pulled == []  # nosec B101


def test_pick_model_downloads_a_name_that_is_not_here(monkeypatch):
    _fake_terminal(monkeypatch)
    monkeypatch.setattr(models, "choose", lambda _rows: "mistral:7b")
    monkeypatch.setattr(models, "confirm", lambda _question: True)
    client = FakeClient(
        names=["gemma4:12b"], updates=[_Update(status="success")]
    )

    assert pick_model(client) == "mistral:7b"  # nosec B101
    assert client.pulled == [("mistral:7b", True)]  # nosec B101


def test_pick_model_switches_to_nothing_when_the_download_is_declined(
    monkeypatch,
):
    _fake_terminal(monkeypatch)
    monkeypatch.setattr(models, "choose", lambda _rows: "mistral:7b")
    monkeypatch.setattr(models, "confirm", lambda _question: False)
    client = FakeClient(names=["gemma4:12b"])

    assert pick_model(client) is None  # nosec B101


def test_pick_model_returns_nothing_when_cancelled(monkeypatch):
    _fake_terminal(monkeypatch)
    monkeypatch.setattr(models, "choose", lambda _rows: None)
    client = FakeClient(names=["gemma4:12b"])

    assert pick_model(client) is None  # nosec B101


def test_pick_model_needs_a_terminal(monkeypatch):
    monkeypatch.setattr(models.sys.stdin, "isatty", lambda: False)
    client = FakeClient(names=["gemma4:12b"])

    assert pick_model(client) is None  # nosec B101


def test_pick_model_gives_up_when_ollama_is_down(monkeypatch):
    _fake_terminal(monkeypatch)
    client = FakeClient(listing=ConnectionError("no ollama"))

    assert pick_model(client) is None  # nosec B101


def test_download_reports_a_transport_failure():
    # A streaming pull skips ollama's error wrapping, so httpx's own
    # errors reach the caller unconverted.
    client = FakeClient(raises=httpx.ConnectError("connection refused"))

    assert download(client, "gemma4:12b") is False  # nosec B101


def test_picker_takes_a_pasted_name():
    assert _picker(  # nosec B101
        "\x1b[200~mistral:7b\x1b[201~" + ENTER
    ) == "mistral:7b"
