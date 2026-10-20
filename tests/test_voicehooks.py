"""Tests for extensions in the middle of voice mode ("voice")."""

import base64
import json
import time

import pytest

from flash import extensions, voicehooks, web
from flash.extensions import ExtensionError
from tests.test_web import request, server  # noqa: F401  (a fixture)

# Writes down what it is sent, then answers with what ANSWERS (a JSON
# object of event to answer) holds for that event.
MIDDLE = """\
import json, os, sys
said = json.load(sys.stdin)
with open(os.environ["FLASH_TEST_VOICE"], "a", encoding="utf-8") as out:
    out.write(json.dumps({"by": NAME, **said}) + "\\n")
answers = ANSWERS
if said["event"] in answers:
    print(json.dumps(answers[said["event"]]))
"""


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASH_TEST_VOICE", str(tmp_path / "said.jsonl"))
    extensions.reload()
    yield
    extensions.reload()


def install(tmp_path, name, voice, script=None):
    folder = tmp_path / f"src-{name}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / extensions.MANIFEST).write_text(
        json.dumps({"name": name, "voice": voice}), encoding="utf-8",
    )
    (folder / "voice.py").write_text(
        script if script is not None else "pass\n", encoding="utf-8",
    )
    checkout = extensions.fetch(f"path@{folder}")
    try:
        installed = extensions.install(checkout, f"path@{folder}")
    finally:
        extensions.discard(checkout)
    extensions.reload()
    return installed


def middle(tmp_path, name, answers, **voice):
    script = MIDDLE.replace("NAME", repr(name)).replace(
        "ANSWERS", repr(answers))
    return install(tmp_path, name, {"run": ["python", "./voice.py"], **voice},
                   script)


def said(tmp_path, count=0, timeout=10):
    out = tmp_path / "said.jsonl"
    deadline = time.monotonic() + timeout
    while True:
        lines = out.read_text().splitlines() if out.exists() else []
        if len(lines) >= count or time.monotonic() > deadline:
            return [json.loads(line) for line in lines]
        time.sleep(0.05)


class TestManifest:
    def test_every_moment_by_default_and_listed(self, tmp_path):
        added = install(tmp_path, "mid", {"run": ["python", "./voice.py"]})

        assert added.voice.on == voicehooks.EVENTS  # nosec B101
        assert added.voice.timeout == voicehooks.DEFAULT_TIMEOUT  # nosec
        assert any(  # nosec B101
            line.startswith("in the middle of voice mode")
            for line in added.contents()
        )

    def test_someone_waiting_is_never_kept_long(self, tmp_path):
        added = install(tmp_path, "mid", {
            "run": ["python", "./voice.py"], "timeout": 300,
        })

        assert added.voice.timeout == 30  # nosec B101

    @pytest.mark.parametrize("bad", [
        {"run": ["python", "./voice.py"], "on": ["lunch"]},
        {"run": ["python", "./voice.py"], "on": []},
        {"on": ["heard"]},
        "voice.py",
    ])
    def test_bad_ones_are_refused(self, tmp_path, bad):
        with pytest.raises(ExtensionError):
            install(tmp_path, "mid", bad)


class TestAsked:
    def test_nothing_installed_changes_nothing(self):
        assert voicehooks.ask("heard", "c", text="hi") == {  # nosec B101
            "text": "hi", "reply": None, "end": False,
        }
        assert voicehooks.wanted() is False  # nosec B101

    def test_what_was_heard_passes_through_each_in_turn(self, tmp_path):
        middle(tmp_path, "a", {"heard": {"text": "what is the weather"}})
        middle(tmp_path, "b", {})

        got = voicehooks.ask("heard", "c1", text="whats the wether")

        assert got["text"] == "what is the weather"  # nosec B101
        seen = said(tmp_path, 2)
        assert [(s["by"], s["text"]) for s in seen] == [  # nosec B101
            ("a", "whats the wether"), ("b", "what is the weather"),
        ]
        assert seen[0]["conversation"] == "c1"  # nosec B101
        assert seen[0]["source"] == "web"  # nosec B101

    def test_one_that_answers_is_the_last_asked(self, tmp_path):
        middle(tmp_path, "a", {"heard": {"reply": "It is 4pm."}})
        middle(tmp_path, "b", {"heard": {"text": "never asked"}})

        got = voicehooks.ask("heard", "c", text="what time is it")

        assert got == {  # nosec B101
            "text": "what time is it", "reply": "It is 4pm.", "end": False,
        }
        assert [s["by"] for s in said(tmp_path, 1)] == ["a"]  # nosec B101

    def test_it_can_end_the_conversation(self, tmp_path):
        middle(tmp_path, "a", {"heard": {"end": True}})

        assert voicehooks.ask("heard", "c", text="bye")["end"] is True

    def test_what_flash_says_can_be_changed(self, tmp_path):
        middle(tmp_path, "a", {"reply": {"text": "Sunny, 21 degrees."}})

        got = voicehooks.ask("reply", "c", text="It is sunny and 21.")

        assert got == {"text": "Sunny, 21 degrees."}  # nosec B101

    def test_hey_flash_can_be_ignored(self, tmp_path):
        middle(tmp_path, "a", {"wake": {"ignore": True}})

        assert voicehooks.ask("wake")["ignore"] is True  # nosec B101

    @pytest.mark.parametrize("script", [
        "print('not json')\n",
        "import sys; print('{\"text\": \"x\"}'); sys.exit(3)\n",
        "import time; time.sleep(5)\n",
        "print('[1, 2]')\n",
    ])
    def test_a_program_that_misbehaves_changes_nothing(self, tmp_path,
                                                       script):
        install(tmp_path, "bad",
                {"run": ["python", "./voice.py"], "timeout": 1}, script)

        got = voicehooks.ask("heard", "c", text="hello")

        assert got == {  # nosec B101
            "text": "hello", "reply": None, "end": False,
        }

    def test_only_what_it_asked_for_reaches_it(self, tmp_path):
        middle(tmp_path, "a", {"heard": {"text": "changed"}}, on=["reply"])

        assert voicehooks.ask("heard", "c", text="same")["text"] == "same"


class TestTold:
    def test_moments_go_out_in_order(self, tmp_path):
        middle(tmp_path, "a", {})

        voicehooks.tell("start", "c", how="wake")
        voicehooks.tell("state", "c", state="listening")
        voicehooks.tell("words", "c", text="whats the")
        voicehooks.tell("state", "c", state="banana")
        voicehooks.tell("end", "c")

        seen = said(tmp_path, 5)
        assert [s["event"] for s in seen] == [  # nosec B101
            "start", "state", "words", "state", "end",
        ]
        assert seen[0]["how"] == "wake"  # nosec B101
        assert seen[2]["text"] == "whats the"  # nosec B101
        # Something it does not know is told as listening.
        assert seen[3]["state"] == "listening"  # nosec B101

    def test_asked_and_told_are_kept_apart(self):
        with pytest.raises(ValueError):
            voicehooks.tell("heard", "c", text="x")
        with pytest.raises(ValueError):
            voicehooks.ask("words", "c", text="x")


class TestThePage:
    def test_the_page_asks_and_tells(self, tmp_path, server):  # noqa: F811
        middle(tmp_path, "a", {"heard": {"reply": "Done."}})

        status, body = request(server, "POST", "/api/voice/hook", {
            "event": "heard", "conversation": "k1", "text": "lights off",
        })
        assert status == 200  # nosec B101
        assert json.loads(body)["reply"] == "Done."  # nosec B101

        status, body = request(server, "POST", "/api/voice/hook", {
            "event": "words", "conversation": "k1", "text": "lights",
        })
        assert status == 200 and json.loads(body) == {}  # nosec B101
        assert said(tmp_path, 2)[1]["event"] == "words"  # nosec B101

        assert request(server, "POST", "/api/voice/hook",
                       {"event": "lunch"})[0] == 400  # nosec B101

    def test_the_page_knows_whether_anyone_listens(self, tmp_path):
        assert web.Session().state(lite=True)["status"][  # nosec B101
            "voice_hooks"] is False
        middle(tmp_path, "a", {})
        assert web.Session().state(lite=True)["status"][  # nosec B101
            "voice_hooks"] is True

    def test_an_ignored_hey_flash_wakes_nothing(
            self, tmp_path, server, monkeypatch):  # noqa: F811
        monkeypatch.setattr(web.voice, "hear_wake",
                            lambda stream, pcm, end=False: (True, ""))
        middle(tmp_path, "a", {"wake": {"ignore": True}})

        status, body = request(server, "POST", "/api/voice/wake", {
            "id": "w", "data": base64.b64encode(b"\0\0").decode(),
        })

        assert status == 200 and json.loads(body) == {"woke": False}
