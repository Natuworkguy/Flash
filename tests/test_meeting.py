# pylint: disable=C0114,C0115,C0116

import array

import pytest

from flash import ai, meeting, voice, web, workspace


def _pcm(seconds: float) -> bytes:
    return array.array(
        "h", [500] * int(seconds * voice.SAMPLE_RATE)
    ).tobytes()


class _Hearing:
    """Hears a new stretch of speech for every second fed to it, the
    last one still being heard, and changes its mind about it once."""

    said = ["hello everyone", "let's ship friday", "Sam writes the notes"]

    def __init__(self):
        self.fed = 0
        self.closed = False

    def feed(self, pcm):
        self.fed += len(pcm) // 2

    def pieces(self):
        if not self.fed:
            return []
        heard = self.said[:self.fed // voice.SAMPLE_RATE + 1]
        if self.fed < voice.SAMPLE_RATE // 2:
            heard = ["hello every"]
        return heard

    def ended(self):
        return self.said[:self.fed // voice.SAMPLE_RATE + 1]

    def close(self):
        self.closed = True


@pytest.fixture
def ready(monkeypatch):
    made = []

    def hearing(live=True):
        made.append(_Hearing())
        return made[-1], ""

    monkeypatch.setattr(voice, "listening_missing", lambda: [])
    monkeypatch.setattr(voice, "listening_installed", lambda name: True)
    monkeypatch.setattr(voice, "_hearing", hearing)
    return made


def post(session, step, **body):
    return web._meeting_post(session, f"/api/meeting/{step}", body)


def test_a_meeting_is_written_down_as_it_is_said(ready):
    session = web.Session()
    started = post(session, "start")

    first = post(session, "hear", id=started["id"], data="", count=0)
    assert first["lines"] == []  # nosec B101

    import base64

    def hear(seconds, count):
        return post(session, "hear", id=started["id"], count=count,
                    data=base64.b64encode(_pcm(seconds)).decode())

    got = hear(0.2, 0)
    assert got["lines"] == [{"at": 0.0, "text": "hello every"}]  # nosec
    got = hear(1.0, 1)
    # The line still being heard is sent again, now it reads better.
    assert got["from"] == 0  # nosec B101
    assert [line["text"] for line in got["lines"]] == [  # nosec B101
        "hello everyone", "let's ship friday",
    ]
    assert got["lines"][1]["at"] == pytest.approx(1.0, abs=0.11)  # nosec
    assert got["seconds"] == pytest.approx(1.2)  # nosec B101


def test_ending_it_starts_the_write_up(ready, monkeypatch):
    session = web.Session()
    sent = []
    monkeypatch.setattr(session, "send",
                        lambda chat, text, files=None: sent.append(
                            (chat.id, text, files)))
    started = post(session, "start")
    import base64
    post(session, "hear", id=started["id"], count=0,
         data=base64.b64encode(_pcm(2.5)).decode())

    ended = post(session, "stop", id=started["id"])

    chat_id, text, files = sent[0]
    assert chat_id == ended["chat"] and text == ""  # nosec B101
    info = workspace.upload_info(files[0])
    assert info["meeting"]["seconds"] == 2  # nosec B101
    assert info["name"].startswith("Meeting ")  # nosec B101
    # The model is asked for the write-up, the transcript in hand.
    said, _ = web.outgoing(ai, "", [{"id": files[0]}])
    assert said.startswith(meeting.PROMPT)  # nosec B101
    assert "[0:00] hello everyone" in said  # nosec B101
    assert "[2:00]" not in said and "Sam writes the notes" in said  # nosec
    # And kept for good, besides.
    kept = list(meeting.folder().glob("*.md"))
    assert len(kept) == 1 and "let's ship friday" in kept[0].read_text()
    with pytest.raises(ValueError, match="not being recorded"):
        post(session, "hear", id=started["id"], data="", count=0)


def test_a_long_meeting_is_read_from_its_file(ready, monkeypatch, tmp_path):
    monkeypatch.setattr(meeting, "INLINE_CHARS", 50)
    path = tmp_path / "Meeting.md"
    path.write_text("[0:00] " + "words " * 40 + "\n")

    said = meeting.note({"path": str(path)})

    assert str(path) in said and "read tool" in said  # nosec B101
    assert "words words" not in said  # nosec B101


def test_nothing_heard_keeps_nothing(ready, monkeypatch):
    session = web.Session()
    monkeypatch.setattr(_Hearing, "said", [""])
    started = post(session, "start")

    with pytest.raises(ValueError, match="Nothing was heard"):
        post(session, "stop", id=started["id"])


def test_discarding_keeps_nothing(ready):
    session = web.Session()
    started = post(session, "start")
    import base64
    found = session.meetings.get(started["id"])
    found._saved = -1e9
    post(session, "hear", id=started["id"], count=0,
         data=base64.b64encode(_pcm(0.5)).decode())
    assert list(meeting.folder().glob("*.md"))  # nosec B101  (saved)

    post(session, "discard", id=started["id"])

    assert not list(meeting.folder().glob("*.md"))  # nosec B101
    assert ready[0].closed  # nosec B101


def test_it_says_what_it_needs_first(monkeypatch):
    monkeypatch.setattr(voice, "listening_missing", lambda: ["vosk"])
    with pytest.raises(ValueError, match="needs vosk"):
        post(web.Session(), "start")

    monkeypatch.setattr(voice, "listening_missing", lambda: [])
    monkeypatch.setattr(voice, "listening_installed", lambda name: False)
    with pytest.raises(ValueError, match="listening model"):
        post(web.Session(), "start")


def test_a_meeting_has_a_longest(ready, monkeypatch):
    monkeypatch.setattr(meeting, "LONGEST_SECONDS", 1)
    session = web.Session()
    started = post(session, "start")
    import base64
    data = base64.b64encode(_pcm(1.0)).decode()
    post(session, "hear", id=started["id"], count=0, data=data)

    with pytest.raises(ValueError, match="at most"):
        post(session, "hear", id=started["id"], count=0, data=data)


def test_clock():
    assert meeting.clock(0) == "0:00"  # nosec B101
    assert meeting.clock(754) == "12:34"  # nosec B101
    assert meeting.clock(3725) == "1:02:05"  # nosec B101
