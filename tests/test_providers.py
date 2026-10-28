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

    # The extension's first, then Ollama's.
    assert names == ["@echo/echo-1", "@echo/plain", "alpha3.1:latest"]


def test_extension_models_are_listed_with_ollama_down(echo, monkeypatch):
    class Down(Ollama):
        def list(self):
            raise ConnectionError("Failed to connect to Ollama")

    monkeypatch.setattr(ollama, "Client", Down)
    names = [m.model for m in providers.client("x").list().models]
    assert names == ["@echo/echo-1", "@echo/plain"]


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


class TestFallbacksAndAuto:
    def test_a_failing_model_falls_back_in_order(self, echo, monkeypatch):
        from flash import modelchain

        class Flaky(Ollama):
            def chat(self, **kwargs):
                self.asked.append(kwargs["model"])
                if kwargs["model"] != "gamma:12b":
                    raise ollama.ResponseError("model is gone", 404)
                return SimpleNamespace(
                    message=SimpleNamespace(content="from gamma"))

        monkeypatch.setattr(ollama, "Client", Flaky)
        modelchain.set_fallbacks("alpha:8b", ["beta:8b", "gamma:12b"])
        client = providers.client("x")
        reply = client.chat(model="alpha:8b", messages=[])
        assert reply.message.content == "from gamma"
        assert client._inner.asked == ["alpha:8b", "beta:8b", "gamma:12b"]
        said = modelchain.notes(client)
        assert said == ["alpha:8b (model is gone), beta:8b (model is gone) "
                        "failed, so **gamma:12b** answered."]

    def test_every_model_failing_says_each(self, monkeypatch):
        from flash import modelchain

        class Down(Ollama):
            def chat(self, **kwargs):
                raise ollama.ResponseError("down", 503)

        monkeypatch.setattr(ollama, "Client", Down)
        modelchain.set_fallbacks("*", ["beta:8b"])
        with pytest.raises(ollama.ResponseError, match="beta:8b"):
            providers.client("x").chat(model="alpha:8b", messages=[])

    def test_a_stream_falls_back_only_before_it_starts(self, monkeypatch):
        from flash import modelchain

        class Streams(Ollama):
            def chat(self, **kwargs):
                def pieces():
                    if kwargs["model"] == "alpha:8b":
                        raise ollama.ResponseError("not loaded", 500)
                    yield SimpleNamespace(message=SimpleNamespace(
                        content=kwargs["model"]))
                return pieces()

        monkeypatch.setattr(ollama, "Client", Streams)
        modelchain.set_fallbacks("alpha:8b", ["beta:8b"])
        parts = providers.client("x").chat(model="alpha:8b", messages=[],
                                           stream=True)
        assert [p.message.content for p in parts] == ["beta:8b"]

    def test_chains(self):
        from flash import modelchain

        assert modelchain.parse_chain("a > b -> c, d") == \
            ("a", ["b", "c", "d"])
        modelchain.set_fallbacks("a", ["b", "a", "b", "c"])
        assert modelchain.fallbacks("a") == ["b", "c"]
        with pytest.raises(ValueError):
            modelchain.set_fallbacks("a", ["auto"])
        modelchain.set_fallbacks("a", [])
        assert modelchain.fallbacks("a") == []

    def test_auto_picks_once_a_message(self, monkeypatch):
        from flash import modelchain, systemone, workspace

        modelchain.forget_picks()
        monkeypatch.setattr(ollama, "Client", Ollama)
        monkeypatch.setattr(workspace, "favorite_models",
                            lambda host: ["small:2b", "big:120b-cloud"])
        monkeypatch.setattr(systemone, "enabled", lambda: True)
        monkeypatch.setattr(systemone, "model", lambda: "judge")
        asked = []

        def ask(host, state, questions, name=""):
            asked.append(state)
            return {"pick": {"choice": "big:120b-cloud"}}

        monkeypatch.setattr(systemone, "ask", ask)
        client = providers.client("x")
        said = [{"role": "user", "content": "Prove this theorem"}]
        client.chat(model="auto", messages=said)
        client.chat(model="auto", messages=said + [
            {"role": "assistant", "content": "", "tool_calls": []}])
        assert [a["model"] for a in client._inner.asked] == [
            "big:120b-cloud", "big:120b-cloud"]
        assert len(asked) == 1
        assert modelchain.notes(client) == [
            "Auto picked **big:120b-cloud** (System One)."]
        assert client.show("auto").capabilities[0] == "completion"

    def test_auto_without_system_one_takes_the_first(self, monkeypatch):
        from flash import modelchain, systemone

        modelchain.forget_picks()
        monkeypatch.setattr(systemone, "enabled", lambda: False)
        monkeypatch.setenv("AUTO_MODELS", "small:2b, big:9b")
        assert modelchain.pick("x", [{"role": "user", "content": "hi"}])[0] \
            == "small:2b"
