"""Speech recognition and speech synthesis for voice mode.

Both models run locally and are downloaded on first use: Vosk for
listening (a small English acoustic model) and Piper for speaking (an
ONNX voice). Nothing here prints; the caller is handed progress and
state through callbacks so the CLI keeps one voice for its output.
"""

import array
import json
import math
import os
import re
import sys
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .paths import MODELS_DIR


def _install_command() -> str:
    """How to add the voice packages to this particular install.

    pipx keeps Flash in an environment of its own, and pip cannot reach
    into it, so a pipx install has to inject them instead.
    """

    if "pipx" in sys.prefix.lower():
        return "pipx inject flash vosk piper-tts sounddevice"

    return 'pip install "flash[voice]"'


INSTALL_HINT = (
    "Voice mode needs the vosk, piper-tts, and sounddevice packages. "
    f"Install them with `{_install_command()}`."
)

MIC_HINT = (
    "No microphone is available. Check that one is connected and that "
    "this terminal is allowed to use it."
)


def _setting(name: str, default: str) -> str:
    """Read a setting when it is used, never when this module is imported.

    Flash loads ~/.flash.env after importing its modules, so anything
    read at import time would only ever see the real environment, and
    /set VOICE_... would look like it did nothing.
    """

    value = (os.getenv(name) or "").strip()

    return value or default


def _number(name: str, default: float, *, minimum: float) -> float:
    """A numeric setting that falls back rather than crashing on nonsense.

    A typo in the env file must not take the whole CLI down with it, so
    an unreadable value is treated as if it were absent.
    """

    try:
        return max(minimum, float(_setting(name, str(default))))
    except ValueError:
        return default


def vosk_model() -> str:
    """Name of the Vosk model used for listening."""

    return _setting("VOICE_VOSK_MODEL", DEFAULT_VOSK_MODEL)


def piper_voice() -> str:
    """Name of the Piper voice used for speaking."""

    return _setting("VOICE_PIPER_VOICE", DEFAULT_PIPER_VOICE)


def silence_seconds() -> float:
    """How long a pause has to be before a spoken turn counts as over."""

    return _number("VOICE_SILENCE_SECONDS", 1.2, minimum=0.2)


def no_speech_seconds() -> float:
    """How long a listening turn waits for someone to start speaking.

    Voice mode listens again after every reply, so this is also what
    ends a hands-free conversation.
    """

    return _number("VOICE_NO_SPEECH_SECONDS", 8.0, minimum=1.0)


def silence_threshold() -> float:
    """Level (of 32768) above which a block is speech, not room noise."""

    return _number("VOICE_SILENCE_THRESHOLD", 500.0, minimum=0.0)


def max_speech_chars() -> int:
    """Longest reply read aloud before it is cut at a sentence."""

    return int(_number("VOICE_MAX_CHARS", 700.0, minimum=80.0))


def interrupt_word() -> str:
    """The word that stops a reply when said over the top of it."""

    return _setting("VOICE_INTERRUPT_WORD", "interrupt").lower()


# Vosk ships one archive per model; the small English one is accurate
# enough for dictation and small enough to download once.
DEFAULT_VOSK_MODEL = "vosk-model-small-en-us-0.15"
VOSK_BASE = "https://alphacephei.com/vosk/models"

# Piper voices are a pair of files: the ONNX network and its config.
DEFAULT_PIPER_VOICE = "en_US-amy-medium"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Vosk is trained on 16 kHz mono audio and resamples nothing itself.
SAMPLE_RATE = 16000
BLOCK_FRAMES = 1600  # 100 ms of audio per read

MAX_TURN_SECONDS = 120.0

DOWNLOAD_TIMEOUT = 30
DOWNLOAD_CHUNK = 1 << 16

Progress = Callable[[str, int], None]
State = Callable[[str], None]


def vosk_model_dir() -> Path:
    """Where the unpacked Vosk model lives once downloaded."""

    return MODELS_DIR / vosk_model()


def piper_paths() -> tuple[Path, Path]:
    """The Piper voice's network and its config file."""

    onnx = MODELS_DIR / f"{piper_voice()}.onnx"

    return onnx, onnx.with_suffix(".onnx.json")


def _piper_urls() -> tuple[str, str]:
    """The download addresses for the configured Piper voice.

    A voice is named locale-speaker-quality ("en_US-amy-medium"), and
    the files sit under language/locale/speaker/quality, so every part
    of the name has to come from the name itself. Raises ValueError on a
    name that is not shaped like a voice.
    """

    name = piper_voice()
    locale, speaker, quality = name.split("-", 2)
    language = locale.split("_")[0].lower()
    base = f"{PIPER_BASE}/{language}/{locale}/{speaker}/{quality}/{name}.onnx"

    return base, f"{base}.json"


def models_present() -> bool:
    """True when both models are already downloaded."""

    onnx, config = piper_paths()

    return (
        (vosk_model_dir() / "am").is_dir()
        and onnx.is_file()
        and config.is_file()
    )


def missing_packages() -> list[str]:
    """The voice packages that are not installed, in install order."""

    missing = []

    for module, package in (
        ("vosk", "vosk"),
        ("piper", "piper-tts"),
        ("sounddevice", "sounddevice"),
    ):
        try:
            __import__(module)
        except Exception:  # noqa: BLE001
            # A package can also fail on its own native library, which
            # leaves voice mode just as unusable as a missing one.
            missing.append(package)

    return missing


def _download(url: str, out: Path, label: str, on_progress: Progress) -> str:
    """Stream `url` to `out`, reporting percent complete as it goes."""

    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(out.suffix + ".part")
    request = Request(url, headers={"User-Agent": "FlashCLI"})  # nosec B310

    try:
        opened = urlopen(request, timeout=DOWNLOAD_TIMEOUT)  # nosec B310

        with opened as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0

            with part.open("wb") as handle:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break

                    handle.write(chunk)
                    done += len(chunk)
                    percent = int(done * 100 / total) if total else 0
                    on_progress(label, percent)

        part.replace(out)
    except (URLError, OSError, ValueError) as exc:
        part.unlink(missing_ok=True)
        return f"could not download {label}: {exc}"

    return ""


def _unpack(archive: Path, into: Path) -> str:
    """Extract a downloaded model archive, refusing unsafe entries."""

    try:
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                target = (into / name).resolve()
                if not str(target).startswith(str(into.resolve())):
                    return f"{archive.name} contains an unsafe path: {name}"

            bundle.extractall(into)  # nosec B202
    except (zipfile.BadZipFile, OSError) as exc:
        return f"could not unpack {archive.name}: {exc}"
    finally:
        archive.unlink(missing_ok=True)

    return ""


def ensure_models(on_progress: Progress) -> str:
    """Download whatever voice mode is missing. Returns "" when ready.

    Both models are large enough that the download is worth showing, so
    `on_progress` is called with a label and a percentage as each file
    arrives.
    """

    if models_present():
        return ""

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if not (vosk_model_dir() / "am").is_dir():
        name = vosk_model()
        archive = MODELS_DIR / f"{name}.zip"
        why = _download(
            f"{VOSK_BASE}/{name}.zip",
            archive,
            "listening model",
            on_progress,
        )
        if why:
            return why

        why = _unpack(archive, MODELS_DIR)
        if why:
            return why

        if not (vosk_model_dir() / "am").is_dir():
            return (
                f"{name} did not unpack into "
                f"{vosk_model_dir()}. Check the model name in "
                "VOICE_VOSK_MODEL."
            )

    onnx, config = piper_paths()

    if not onnx.is_file() or not config.is_file():
        try:
            onnx_url, config_url = _piper_urls()
        except ValueError:
            return (
                f"'{piper_voice()}' is not a Piper voice name. Use "
                "one shaped like en_US-amy-medium."
            )

        why = _download(onnx_url, onnx, "voice", on_progress)
        if why:
            return why

        why = _download(config_url, config, "voice settings", on_progress)
        if why:
            return why

    return ""


_listener = None
_speaker = None


def _level(block: bytes) -> float:
    """The root-mean-square loudness of one block of 16-bit audio."""

    samples = array.array("h")
    samples.frombytes(block[:len(block) - len(block) % 2])

    if not samples:
        return 0.0

    return math.sqrt(sum(sample * sample for sample in samples)
                     / len(samples))


# Speech recognition hands over bare words, so a spoken "/voice off" can
# never arrive. These are what "stop the conversation" sounds like
# instead; leaving voice mode for good stays a typed command.
EXIT_PHRASES = {
    "voice off",
    "voice ah",
    "voice mode off",
    "turn off voice",
    "turn off voice mode",
    "turn voice off",
    "turn voice mode off",
    "exit voice",
    "exit voice mode",
    "quit voice",
    "quit voice mode",
    "stop voice",
    "stop voice mode",
    "stop listening",
    "disable voice",
    "disable voice mode",
}


def is_exit_phrase(text: str) -> bool:
    """True when what was said asks Flash to stop listening.

    Matched whole, never as a substring: a sentence that happens to
    mention voice mode is a message for the model, not a command.
    """

    said = re.sub(r"[^a-z ]", "", (text or "").lower())

    return " ".join(said.split()) in EXIT_PHRASES


def _load_listener():
    """Load and cache the Vosk model, which takes a moment to read.

    Cached against the directory it came from, so changing
    VOICE_VOSK_MODEL mid-session loads the new model instead of quietly
    keeping the old one.
    """

    global _listener

    directory = vosk_model_dir()

    if _listener is None or _listener[0] != directory:
        import vosk

        # Vosk logs its whole model layout to stderr at load.
        vosk.SetLogLevel(-1)
        _listener = (directory, vosk.Model(str(directory)))

    return _listener[1]


def listen(on_state: State) -> tuple[str, str]:
    """Record until the speaker stops, and return what was said.

    Returns `(text, "")`, where `text` is empty when nothing was heard,
    or `("", reason)` when the microphone or the model was not usable.
    """

    try:
        import sounddevice
        import vosk
    except Exception:  # noqa: BLE001
        return "", INSTALL_HINT

    try:
        model = _load_listener()
    except Exception as exc:  # noqa: BLE001
        return "", f"could not load the listening model: {exc}"

    recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE)
    heard = False
    quiet = 0.0
    elapsed = 0.0
    step = BLOCK_FRAMES / SAMPLE_RATE

    # Read once per turn so a /set during the session takes effect.
    threshold = silence_threshold()
    pause = silence_seconds()
    patience = no_speech_seconds()

    try:
        with sounddevice.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_FRAMES,
            dtype="int16",
            channels=1,
        ) as stream:
            on_state("listening")

            while elapsed < MAX_TURN_SECONDS:
                block, _overflowed = stream.read(BLOCK_FRAMES)
                block = bytes(block)
                recognizer.AcceptWaveform(block)
                elapsed += step

                if _level(block) >= threshold:
                    heard = True
                    quiet = 0.0
                    continue

                quiet += step

                if heard and quiet >= pause:
                    break

                if not heard and elapsed >= patience:
                    return "", ""
    except KeyboardInterrupt:
        # Cancelling a turn is not a microphone fault, and the caller
        # says so differently, so it goes straight up.
        raise
    except Exception as exc:  # noqa: BLE001
        return "", f"{MIC_HINT} ({exc})"

    on_state("transcribing")

    try:
        result = json.loads(recognizer.FinalResult())
    except ValueError:
        return "", "the listening model returned nothing usable."

    return str(result.get("text") or "").strip(), ""


# Said while Flash is talking, the interrupt word cuts the reply off. It
# is matched as a whole word, so "interrupted" does not trigger it.
INTERRUPT_PHRASES = {"stop talking", "be quiet", "shut up"}


def _heard_interruption(text: str) -> bool:
    """True when what came through the microphone asks Flash to stop."""

    said = re.sub(r"[^a-z ]", "", (text or "").lower())
    said = " ".join(said.split())

    if not said:
        return False

    word = interrupt_word()

    # A one-word setting has to match a whole word, so "interrupted"
    # does not trigger it; a phrase is matched as the phrase.
    if word in said if " " in word else word in said.split():
        return True

    return any(phrase in said for phrase in INTERRUPT_PHRASES)


class _Barge:
    """Listens for an interruption while a reply is being read aloud.

    Recognition runs on a thread of its own so playback is never held up
    waiting for it, and every failure here is silent: a machine that
    cannot open the microphone can still be listened to, it just cannot
    be talked over.
    """

    def __init__(self) -> None:
        self.heard = threading.Event()
        self._done = threading.Event()
        self._thread = None

    def __enter__(self):
        try:
            import sounddevice
            import vosk

            model = _load_listener()
        except Exception:  # noqa: BLE001
            return self

        self._thread = threading.Thread(
            target=self._watch,
            args=(sounddevice, vosk, model),
            daemon=True,
        )
        self._thread.start()

        return self

    def __exit__(self, *_) -> bool:
        self._done.set()

        if self._thread is not None:
            self._thread.join(timeout=1.0)

        return False

    def _watch(self, sounddevice, vosk, model) -> None:
        recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE)

        try:
            with sounddevice.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_FRAMES,
                dtype="int16",
                channels=1,
            ) as stream:
                while not self._done.is_set():
                    block, _overflowed = stream.read(BLOCK_FRAMES)

                    # Partial results are what make this feel immediate:
                    # the word lands before the sentence is finished.
                    if recognizer.AcceptWaveform(bytes(block)):
                        said = json.loads(recognizer.Result()).get("text")
                    else:
                        said = json.loads(
                            recognizer.PartialResult()
                        ).get("partial")

                    if _heard_interruption(said):
                        self.heard.set()
                        return
        except Exception:  # noqa: BLE001, S110  # nosec B110
            # No microphone here just means the reply cannot be cut off.
            pass


def _load_speaker():
    """Load and cache the Piper voice and the sample rate it speaks at.

    Cached against the voice file, so a new VOICE_PIPER_VOICE is heard
    rather than ignored in favour of the one already loaded.
    """

    global _speaker

    onnx, config = piper_paths()

    if _speaker is None or _speaker[0] != onnx:
        from piper import PiperVoice

        settings = json.loads(config.read_text(encoding="utf-8"))
        rate = int(settings.get("audio", {}).get("sample_rate", 22050))
        _speaker = (onnx, PiperVoice.load(str(onnx)), rate)

    return _speaker[1], _speaker[2]


def _pcm_chunks(voice, text: str):
    """Yield raw 16-bit audio for `text`, across Piper's two APIs."""

    if hasattr(voice, "synthesize_stream_raw"):
        yield from voice.synthesize_stream_raw(text)
        return

    for chunk in voice.synthesize(text):
        audio = getattr(chunk, "audio_int16_bytes", None)
        yield audio if audio is not None else bytes(chunk)


def speak(text: str) -> tuple[str, bool]:
    """Read `text` aloud, stopping if it is interrupted.

    Returns `(reason, interrupted)`: `reason` is "" unless the reply
    could not be played at all, and `interrupted` says whether saying
    "interrupt" (or Ctrl+C) cut it short.
    """

    text = text.strip()

    if not text:
        return "", False

    try:
        import sounddevice
    except Exception:  # noqa: BLE001
        return INSTALL_HINT, False

    try:
        voice, rate = _load_speaker()
    except Exception as exc:  # noqa: BLE001
        return f"could not load the voice: {exc}", False

    # Audio is written a tenth of a second at a time so an interruption
    # lands on the word being said, not at the end of the sentence.
    slice_bytes = max(2, (rate // 10) * 2)
    interrupted = False

    try:
        with _Barge() as barge, sounddevice.RawOutputStream(
            samplerate=rate,
            dtype="int16",
            channels=1,
        ) as stream:
            for chunk in _pcm_chunks(voice, text):
                for start in range(0, len(chunk or b""), slice_bytes):
                    if barge.heard.is_set():
                        interrupted = True
                        break

                    stream.write(chunk[start:start + slice_bytes])

                if interrupted:
                    # Drop whatever is still queued, rather than letting
                    # the device finish the buffered sentence.
                    _abort(stream)
                    break
    except KeyboardInterrupt:
        # Cutting the reply short is a normal way to use voice mode, not
        # a failure worth reporting.
        return "", True
    except Exception as exc:  # noqa: BLE001
        return f"could not play the reply: {exc}", False

    return "", interrupted


def _abort(stream) -> None:
    """Discard audio the device has already buffered, if it can."""

    try:
        stream.abort()
    except Exception:  # noqa: BLE001, S110  # nosec B110
        # Stopping mid-reply is best effort; closing the stream ends it
        # either way.
        pass


CODE_ONLY = "That reply is code. It is on screen."
CUT_SHORT = "The rest is on screen."

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"<?https?://\S+>?")
_TABLE = re.compile(r"^\s*\|.*$", re.MULTILINE)
_RULE = re.compile(r"^\s*([-*_]\s*){3,}$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_QUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_INLINE_CODE = re.compile(r"`+([^`]*)`+")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)")
_BLANKS = re.compile(r"\n{2,}")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def for_speech(text: str, limit: int = 0) -> str:
    """Turn a Markdown reply into something worth hearing.

    Code blocks, tables, and URLs are on screen already and are painful
    read aloud, so they come out; the prose that is left is cut at a
    sentence once it runs past `limit`.
    """

    # A spoken reply that runs on for minutes is worse than a short one
    # the reader can finish on screen.
    limit = limit or max_speech_chars()

    stripped = _FENCE.sub(" ", text or "")
    stripped = _IMAGE.sub(" ", stripped)
    stripped = _LINK.sub(r"\1", stripped)
    stripped = _URL.sub("a link", stripped)
    stripped = _TABLE.sub(" ", stripped)
    stripped = _RULE.sub(" ", stripped)
    stripped = _HEADING.sub("", stripped)
    stripped = _BULLET.sub("", stripped)
    stripped = _QUOTE.sub("", stripped)
    stripped = _INLINE_CODE.sub(r"\1", stripped)
    stripped = _EMPHASIS.sub("", stripped)
    stripped = _BLANKS.sub("\n", stripped).strip()

    if not stripped:
        return CODE_ONLY if text.strip() else ""

    return _shorten(stripped, limit)


def _shorten(text: str, limit: int) -> str:
    """Cut `text` at the last sentence that fits, saying that it was cut."""

    if len(text) <= limit:
        return text

    ends = [match.end() for match in _SENTENCE_END.finditer(text[:limit])]
    cut = ends[-1] if ends else text[:limit].rfind(" ")

    if cut <= 0:
        cut = limit

    return f"{text[:cut].strip()} {CUT_SHORT}"
