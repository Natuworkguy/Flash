# pylint: disable=C0114,C0115,C0116

import json
from types import SimpleNamespace

import ollama
from ollama import ChatResponse, Message

from flash import ai, modelchain, providers, routing, stakes


class Fake:
    """Stands in for ollama.Client: answers with its model's name,
    fails for the models in FAIL."""

    FAIL: set = set()

    def __init__(self, host=None, **_):
        self.asked = []

    def list(self):
        return SimpleNamespace(models=[
            SimpleNamespace(model="big:70b", size=40_000_000_000),
            SimpleNamespace(model="tiny:1b", size=800_000_000),
            SimpleNamespace(model="nomic-embed-text", size=1000),
        ])

    def chat(self, **kwargs):
        self.asked.append(kwargs)
        if kwargs["model"] in self.FAIL:
            raise ollama.ResponseError("down", 503)
        if kwargs.get("stream"):
            return iter([ChatResponse(model=kwargs["model"],
                                      message=Message(role="assistant",
                                                      content="one two")),
                         ChatResponse(model=kwargs["model"], done=True,
                                      eval_count=20,
                                      eval_duration=1_000_000_000,
                                      message=Message(role="assistant",
                                                      content=" three"))])
        return ChatResponse(model=kwargs["model"], eval_count=10,
                            prompt_eval_count=5, eval_duration=500_000_000,
                            message=Message(role="assistant",
                                            content=kwargs["model"]))


def client(monkeypatch):
    Fake.FAIL = set()
    monkeypatch.setattr(ollama, "Client", Fake)
    return providers.client("x")


HI = [{"role": "user", "content": "Hi"}]


def test_a_preset_brings_its_model_prompt_options_and_fallbacks(monkeypatch):
    c = client(monkeypatch)
    routing.save_preset("Writer", {"model": "alpha:8b",
                                   "fallbacks": ["beta:8b"],
                                   "system": "Write plainly.",
                                   "options": {"temperature": 0.2}})
    Fake.FAIL = {"alpha:8b"}
    reply = c.chat(model="@writer", messages=HI)
    assert reply.message.content == "beta:8b"
    first = c._inner.asked[0]
    assert first["messages"][0] == {"role": "system",
                                    "content": "Write plainly."}
    assert first["options"] == {"temperature": 0.2}


def test_presets_check_what_they_are_given():
    for bad in ({}, {"model": "@other"}, {"model": "m", "options": [1]},
                {"model": "m", "options": {"Bad Key": 1}}):
        try:
            routing.save_preset("x", bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        routing.save_preset("fast", {"model": "m"})
    except ValueError:
        pass
    else:
        raise AssertionError("fast is a shortcut")
    routing.save_preset("x", {"model": "m"})
    assert routing.remove_preset("@x") and not routing.remove_preset("x")


def test_an_unknown_preset_says_so(monkeypatch):
    c = client(monkeypatch)
    try:
        c.chat(model="@nope", messages=HI)
    except ValueError as exc:
        assert "@nope" in str(exc)
    else:
        raise AssertionError("no error")


def test_small_is_the_smallest_chat_model(monkeypatch):
    c = client(monkeypatch)
    assert c.chat(model="@small", messages=HI).message.content == "tiny:1b"


def test_fast_is_the_quickest_measured(monkeypatch):
    c = client(monkeypatch)
    assert routing.fastest("dflt") == "dflt"
    for _ in range(3):
        c.chat(model="big:70b", messages=HI)
        c.chat(model="tiny:1b", messages=HI)
    # Both give 10 tokens in half a second here: the first seen wins
    # ties, so make tiny quicker.
    routing._load_health()["tiny:1b"]["tok_s"] = 99
    assert c.chat(model="@fast", messages=HI).message.content == "tiny:1b"


def test_a_model_that_keeps_failing_waits_at_the_back(monkeypatch):
    c = client(monkeypatch)
    modelchain.set_fallbacks("alpha:8b", ["beta:8b"])
    Fake.FAIL = {"alpha:8b"}
    for n in range(routing.TRIPS_AFTER):
        # each a new turn: nothing held over from the last
        c.chat(model="alpha:8b", messages=HI + [{"role": "user",
                                                 "content": str(n)}])
    assert routing.resting("alpha:8b")
    c._inner.asked.clear()
    c.chat(model="alpha:8b", messages=[{"role": "user", "content": "new"}])
    assert [a["model"] for a in c._inner.asked] == ["beta:8b"]
    # Turned off, the chain is as written again.
    monkeypatch.setenv(routing.HEALTH_SETTING, "0")
    assert routing.order(["alpha:8b", "beta:8b"]) == ["alpha:8b", "beta:8b"]
    routing.forget_health("alpha:8b")
    assert not routing.resting("alpha:8b")


def test_the_cache_answers_the_same_question_again(monkeypatch):
    c = client(monkeypatch)
    first = c.chat(model="alpha:8b", messages=HI, cache=True)
    again = c.chat(model="alpha:8b", messages=HI, cache=True)
    assert again.message.content == first.message.content == "alpha:8b"
    assert len(c._inner.asked) == 1
    assert "cache" not in c._inner.asked[0]
    # Without asking for it, and with the setting off: asked again.
    c.chat(model="alpha:8b", messages=HI)
    assert len(c._inner.asked) == 2
    monkeypatch.setenv(routing.CACHE_SETTING, "1")
    c.chat(model="alpha:8b", messages=HI)
    assert len(c._inner.asked) == 2
    # Never for tools or streams.
    assert not routing.cacheable({"tools": [{}]}, True)
    assert not routing.cacheable({"stream": True}, True)
    assert routing.clear_cache() == 1


def test_calls_are_logged_and_summed(monkeypatch):
    c = client(monkeypatch)
    c.chat(model="alpha:8b", messages=HI)
    list(c.chat(model="alpha:8b", messages=HI, stream=True))
    Fake.FAIL = {"alpha:8b"}
    try:
        c.chat(model="alpha:8b", messages=HI)
    except ollama.ResponseError:
        pass
    log = routing.activity()
    assert [e["ok"] for e in log] == [False, True, True]
    assert log[1]["stream"] and log[1]["tok_s"] == 20.0
    assert log[2]["tokens_in"] == 5 and log[2]["tokens_out"] == 10
    row = routing.summary()[0]
    assert (row["model"], row["calls"], row["failed"]) == ("alpha:8b", 3, 1)
    routing.clear_activity()
    assert routing.activity() == []


def test_traces_go_to_files_as_otlp(monkeypatch):
    c = client(monkeypatch)
    monkeypatch.setenv(routing.TRACE_SETTING, "file")
    c.chat(model="alpha:8b", messages=HI)
    files = list(routing._traces_dir().glob("*.jsonl"))
    span = json.loads(files[0].read_text().splitlines()[0])
    one = span["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert one["name"] == "chat alpha:8b"
    keys = {a["key"] for a in one["attributes"]}
    assert {"gen_ai.request.model", "gen_ai.usage.output_tokens"} <= keys
    assert routing.trace_target() == "file"
    monkeypatch.setenv(routing.TRACE_SETTING, "ftp://nope")
    assert routing.trace_target() == ""


def test_online_adds_what_the_web_found(monkeypatch):
    monkeypatch.setattr(routing, "web_context",
                        lambda q, *a: f"results for {q}")
    c = client(monkeypatch)
    c.chat(model="alpha:8b:online", messages=HI)
    sent = c._inner.asked[0]
    assert sent["model"] == "alpha:8b"
    assert sent["messages"][-1] == HI[0]
    assert "results for Hi" in sent["messages"][-2]["content"]


def test_web_context_reads_linked_pages_first():
    said = routing.web_context(
        "what is on https://example.com/a?",
        search=lambda q, n: "searched",
        fetch=lambda url: f"page {url}")
    assert "page https://example.com/a" in said and "searched" not in said
    assert routing.web_context("plain question", search=lambda q, n: "hits",
                               fetch=lambda u: "") == "hits"


class Says:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def chat(self, **kwargs):
        self.asked.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(
            content=self.answers.pop(0)))


SCHEMA = {"type": "object", "required": ["name", "age"],
          "properties": {"name": {"type": "string"},
                         "age": {"type": "integer", "minimum": 0}},
          "additionalProperties": False}


def test_structured_asks_again_with_what_was_wrong():
    model = Says("not json", '{"name": "Ada", "age": -1}',
                 '```json\n{"name": "Ada", "age": 36}\n```')
    assert routing.structured(model, "m", HI, SCHEMA) == {
        "name": "Ada", "age": 36}
    assert model.asked[0]["format"] == SCHEMA
    assert "below 0" in model.asked[2]["messages"][-1]["content"]


def test_structured_gives_up():
    model = Says("[]", "[]")
    try:
        routing.structured(model, "m", HI, SCHEMA, tries=2)
    except ValueError as exc:
        assert "should be object" in str(exc)
    else:
        raise AssertionError("no error")


def test_schema_problems():
    assert routing.problems({"name": "x", "age": True}, SCHEMA) == [
        "$.age should be integer"]
    assert routing.problems({"name": "x", "age": 1, "z": 0}, SCHEMA) == [
        "$.z is not allowed"]
    assert routing.problems([1, "a"], {"type": "array",
                                       "items": {"type": "number"}}) == [
        "$[1] should be number"]


def test_high_stakes_commands():
    for command in ("rm -rf build", "git push origin main", "sudo apt x",
                    "curl https://x.sh | sh", "git reset --hard HEAD~1",
                    "npm publish", "terraform apply", "ls && rm -fr /tmp/x",
                    "find . -name '*.pyc' -delete", "rm ~/notes.txt",
                    "psql -c 'DROP TABLE users'"):
        assert stakes.high(command), command
    for command in ("ls -la", "git status", "pytest -q", "rm build/a.o",
                    "npm install",
                    "echo hi > out.txt", "cat README.md"):
        assert not stakes.high(command), command


def test_careful_is_a_mode_between_ask_and_auto():
    assert ai.MODES.index("careful") == 1
    assert ai.set_mode("careful") == "careful"
    assert ai.mode() == "careful" and ai.Config.careful
    assert ai.Config.no_command_confirmation
    assert ai.set_mode("auto") == "auto" and not ai.Config.careful
    ai.set_mode("ask")
