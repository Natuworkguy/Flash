"""Tests for reading aloud, and for sound files shown in the chat."""

import io
import json
import shutil
import wave
from types import SimpleNamespace

import pytest

from flash import readaloud, tools, voice, web, workspace
from flash.theme import capture_tool_output


def tiny_wav(text: str) -> tuple[bytes, str]:
    """What voice.synthesize gives: a WAV, here a tenth of a second a word."""

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x01\x00" * 800 * max(1, len(text.split())))
    return out.getvalue(), ""


class TestTheWords:
    def test_made_fit_to_hear(self):
        said = readaloud.speakable(
            "# A title\n\nSee [the docs](https://x.io/docs) or "
            "https://example.com now.\n- **one** point\n\n\n`code` too")
        assert said == ("A title\n\nSee the docs or a link now. one point"
                        "\n\ncode too")

    def test_long_paragraphs_go_by_sentence(self):
        long = " ".join(f"Sentence number {n} is here." for n in range(60))
        parts = readaloud.pieces(f"Short one.\n\n{long}")
        assert parts[0] == "Short one."
        assert all(len(p) <= readaloud.PIECE_CHARS + 40 for p in parts)
        assert " ".join(parts[1:]) == long

    def test_too_long_is_cut_at_a_sentence_and_empty_is_refused(self):
        said, cut = readaloud.prepare("A sentence here. " * 5000)
        assert cut and said.endswith(".") and len(said) <= readaloud.MAX_CHARS
        assert readaloud.prepare("Short.") == ("Short.", False)
        with pytest.raises(readaloud.ReadError, match="nothing in it"):
            readaloud.prepare("  \n\n ")

    def test_a_page_is_read_from_its_article(self):
        page = ("<nav>Home About</nav><article><p>Small</p></article>"
                "<article><h1>Big story</h1><p>All of it.</p></article>")
        assert "Big story" in readaloud.article(page)
        assert "Home" not in readaloud.article(page)
        assert readaloud.article("<p>No article</p>") == "<p>No article</p>"


class TestRecording:
    def test_a_wav_with_a_breath_between_pieces(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voice, "synthesize", tiny_wav)
        made = readaloud.record("One two.\n\nThree.", tmp_path / "r.wav")
        with wave.open(str(made["path"])) as w:
            assert w.getframerate() == 8000
            frames = w.getnframes()
        assert frames == 800 * 2 + 800 + 2 * int(8000 * 0.35)
        assert made["seconds"] == pytest.approx(frames / 8000)

    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
    def test_an_m4a_when_ffmpeg_is_here(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voice, "synthesize", tiny_wav)
        made = readaloud.record("Hello there.", tmp_path / "r.m4a")
        assert made["path"].suffix == ".m4a" and made["path"].stat().st_size

    def test_no_voice_says_why(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voice, "synthesize",
                            lambda text: (b"", "no voice installed"))
        with pytest.raises(readaloud.ReadError, match="no voice"):
            readaloud.record("Hi.", tmp_path / "r.wav")


def page_from(html: str):
    class Response:
        headers = SimpleNamespace(
            get=lambda k, d="": "text/html; charset=utf-8")

        def read(self, n):
            return html.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda request, timeout=0: Response()


class TestTheTool:
    def test_the_terminal_reads_it_through_the_speakers(self, monkeypatch):
        heard = []
        monkeypatch.setattr(voice, "speak",
                            lambda text: (heard.append(text) or ("", False)))
        said = tools.read_aloud(text="Hello there.\n\nGoodbye.")
        assert heard == ["Hello there.", "Goodbye."]
        assert "Read aloud: 3 words" in said

    def test_stopped_part_way(self, monkeypatch):
        monkeypatch.setattr(voice, "speak", lambda text: ("", True))
        said = tools.read_aloud(text="One.\n\nTwo.")
        assert "stopped the reading after part 1 of 2" in said

    def test_an_article_from_a_page(self, monkeypatch):
        heard = []
        monkeypatch.setattr(voice, "speak",
                            lambda text: (heard.append(text) or ("", False)))
        monkeypatch.setattr(tools.urllib.request, "urlopen", page_from(
            "<title>Tides</title><nav>Menu Login</nav><article><p>The moon "
            "pulls the sea.</p></article>"))
        tools.read_aloud(url="https://example.com/tides")
        assert " ".join(heard) == "Tides. The moon pulls the sea."

    def test_the_browser_gets_a_player_that_starts(self, monkeypatch):
        monkeypatch.setattr(voice, "synthesize", tiny_wav)
        sent = []
        with capture_tool_output(lambda kind, text, style: sent.append(
                (kind, text))):
            said = tools.read_aloud(text="Read this to me please.")
        audio = [json.loads(t) for k, t in sent if k == "audio"]
        assert audio and audio[0]["play"] is True
        assert audio[0]["path"].endswith((".m4a", ".wav"))
        assert "started playing" in said and "Do not repeat" in said

    def test_bad_input_is_an_error(self, tmp_path):
        assert tools.read_aloud(path=str(tmp_path / "gone.txt")).startswith(
            "Error: could not read it aloud: no file")
        assert "only http" in tools.read_aloud(url="ftp://x")


class TestSoundInTheChat:
    def test_sound_files_are_kept_as_audio(self, tmp_path):
        song = tmp_path / "song.mp3"
        song.write_bytes(b"ID3" + b"\0" * 64)
        kept = workspace.keep_file(str(song))
        assert kept["kind"] == "audio" and kept["mime"] == "audio/mpeg"

    def test_send_audio(self, tmp_path):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(tiny_wav("a b")[0])
        sent = []
        with capture_tool_output(lambda kind, text, style: sent.append(
                (kind, text))):
            said = tools.send_audio(str(clip), play=True)
        assert ("audio", json.dumps({"path": str(clip), "play": True})) \
            in sent
        assert "Sent clip.wav" in said
        assert tools.send_audio(str(tmp_path / "x.txt")).startswith("Error")

    def test_the_page_gets_a_file_that_plays_once(self, tmp_path):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(tiny_wav("a")[0])
        session = web.Session()
        chat = session.new_chat()
        web._sink(session, chat)("audio", json.dumps(
            {"path": str(clip), "play": True}), "")
        shown = [e for e in chat.log if e["type"] == "file"][-1]
        assert shown["kind"] == "audio" and shown["play"] is True

    def test_make_sound_shows_its_sound(self, tmp_path):
        sent = []
        with capture_tool_output(lambda kind, text, style: sent.append(kind)):
            said = tools.make_sound("C4 E4", str(tmp_path / "s.wav"))
        assert "audio" in sent and "in the chat in a player" in said

    def test_on_the_page(self):
        page = web.PAGE.read_text()
        assert "function audioPlayer(entry)" in page
        assert 'entry.kind === "audio" ? audioPlayer(entry)' in page
