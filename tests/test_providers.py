# pylint: disable=C0114,C0116

import base64
import shutil
from pathlib import Path
from types import SimpleNamespace

import ollama
import pytest

from flash import ai, extensions, models, providers, showcase, sysprompt

EXAMPLE = Path(__file__).parent / "data" / "echo-provider"


@pytest.fixture
def echo():
    target = extensions.extensions_dir() / "echo-provider"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(EXAMPLE, target)
    extensions.reload()
    providers.forget_models()
    yield
    providers.forget_models()


class Ollama:
    """Stands in for ollama.Client: one model of its own."""

    def __init__(self, host=None, **_):
        self.asked = []

    def list(self):
        return SimpleNamespace(models=[SimpleNamespace(
            model="alpha3.1:latest", size=1, details=None, modified_at=None,
        )])

    def chat(self, **kwargs):
        self.asked.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content="from ollama"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ollama, "Client", Ollama)
    return providers.client("http://localhost:11434")


def test_the_manifest_declares_providers(echo):
    extension = extensions.find("echo-provider")

    assert [p.name for p in extension.providers] == ["echo"]
    assert extension.providers[0].timeout == 20
    assert "model providers: @echo" in extension.contents()


def test_a_provider_name_belongs_to_one_extension(echo, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(EXAMPLE, other)
    manifest = other / "flash-extension.json"
    manifest.write_text(manifest.read_text().replace(
        '"echo-provider"', '"other"'))

    assert extensions.clashes(
        extensions.load(other), frozenset(), frozenset(),
    ) == ["provider @echo belongs to echo-provider"]


@pytest.mark.parametrize("name, parts", [
    ("@echo/echo-1", ("echo", "echo-1")),
    ("@Echo/org/model:tag", ("echo", "org/model:tag")),
])
def test_a_provider_model_is_named_by_its_provider(name, parts):
    assert providers.routed(name)
    assert providers.split(name) == parts


def test_ollama_names_are_never_routed():
    assert not providers.routed("Natuworkguy/flash-onyx-2.6:31b")
    with pytest.raises(ollama.ResponseError):
        providers.split("@echo")


def test_its_models_are_listed_beside_ollamas(echo, client):
    names = [m.model for m in client.list().models]

    assert names == ["alpha3.1:latest", "@echo/echo-1", "@echo/plain"]


def test_a_chat_goes_to_the_provider_and_comes_back_as_ollamas(
    echo, client,
):
    reply = client.chat(model="@echo/echo-1",
                        messages=[{"role": "user", "content": "hi"}])

    assert reply.message.content == "echo-1 heard: hi"
    assert reply["message"]["content"] == "echo-1 heard: hi"
    assert reply.prompt_eval_count == 10 and reply.eval_count == 4
    # Anything else is still Ollama's.
    assert client.chat(model="alpha3.1", messages=[]).message.content \
        == "from ollama"


def test_tool_calls_come_back_with_their_arguments_parsed(echo, client):
    reply = client.chat(
        model="@echo/echo-1",
        messages=[{"role": "user", "content": "call tool"}],
        tools=[{"type": "function", "function": {"name": "get_date"}}],
    )

    call = reply.message.tool_calls[0]
    assert call.function.name == "get_date"
    assert call.function.arguments == {"when": "now"}


def test_a_streamed_answer_arrives_in_pieces(echo, client):
    pieces = list(client.chat(
        model="@echo/echo-1",
        messages=[{"role": "user", "content": "hi"}], stream=True,
    ))

    assert "".join(p.message.content for p in pieces) == "Hello from echo"
    assert pieces[-1].done and not pieces[0].done


def test_images_are_sent_as_base64(echo, client, tmp_path):
    picture = tmp_path / "dot.png"
    picture.write_bytes(b"\x89PNG fake")

    reply = client.chat(model="@echo/plain", messages=[{
        "role": "user", "content": "look", "images": [str(picture)],
    }])

    assert reply.message.content == "plain heard: look and 1 image"
    assert providers._images([str(picture)]) == [
        base64.b64encode(b"\x89PNG fake").decode()
    ]


@pytest.mark.parametrize("said, why", [
    ("fail", "@echo: out of credit"),
    ("crash", "exit 2\\): it broke"),
])
def test_a_failure_is_ollamas_kind_of_error(echo, client, said, why):
    with pytest.raises(ollama.ResponseError, match=why):
        client.chat(model="@echo/echo-1",
                    messages=[{"role": "user", "content": said}])


def test_an_unknown_provider_says_so(client):
    with pytest.raises(ollama.ResponseError, match="No provider called @x"):
        client.chat(model="@x/y", messages=[])


def test_what_a_provider_model_can_do_comes_from_its_listing(echo):
    host = "http://localhost:11434"

    assert sysprompt.get_context_limit(host, "@echo/echo-1") == 8192
    assert sysprompt.get_context_ceiling(host, "@echo/echo-1") == 8192
    assert sysprompt.is_remote(host, "@echo/echo-1")
    assert not sysprompt.model_sees_images(host, "@echo/echo-1")
    # Unlisted capabilities: completion and tools.
    assert sysprompt.get_context_limit(host, "@echo/plain") is None


def test_nothing_is_ever_downloaded_for_one(echo, client, monkeypatch):
    monkeypatch.setattr(models, "confirm", lambda q: pytest.fail(q))

    assert models.full_name("@echo/echo-1") == "@echo/echo-1"
    assert models.fetch_if_missing(client, "@echo/unlisted")
    with pytest.raises(ollama.ResponseError, match="nothing to download"):
        client.pull("@echo/echo-1")


def test_a_provider_model_is_never_counted_private():
    assert not showcase.is_private("http://localhost:11434", "@echo/echo-1")


def test_the_terminal_chats_with_one(echo, client, monkeypatch):
    monkeypatch.setattr(ai.Config, "model", "@echo/echo-1")

    reply = ai._chat(ai._client(),
                     [{"role": "user", "content": "from the terminal"}])

    assert reply.message.content == "echo-1 heard: from the terminal"


def test_the_picker_shows_them_as_they_are(echo, client):
    rows = models.installed_models(client, "@echo/echo-1")

    first = rows[0]
    assert first.name == "@echo/echo-1" and first.note == "active"
    assert "@echo provider" in first.summary and first.size == ""
