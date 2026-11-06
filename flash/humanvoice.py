"""What makes Flash sound like a person talking, not text being read out.

In a spoken reply the model may write cues, which are said and never
shown:

- A feeling, in brackets at the start of a sentence, such as "[happy]"
  or "[sorry]": that sentence is said with it, a little quicker or
  slower, higher or lower, louder or softer (MOODS).
- A sound a person makes, such as "[laugh]", "[sigh]" or "[hm]",
  wherever it would be made (SOUNDS).

Besides, every sentence varies a touch in pace and pitch, so no two come
out stamped from one mould; text is put into words the way a person would
say it (for_ear); and there is what helps the timing of a conversation:
whether what was heard so far sounds finished (end_pause), a word said at
once to show it was heard (ack_for), and how the user sounds (hear_mood).
"""

from __future__ import annotations

import array
import math
import random
import re
import zlib
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Mood:
    pace: float = 1.0  # a length scale: under 1 is quicker
    detune: int = 0  # cents, up or down
    gain: float = 1.0


MOODS = {
    "happy": Mood(0.97, 45, 1.05),
    "excited": Mood(0.92, 90, 1.12),
    "playful": Mood(0.96, 60, 1.05),
    "warm": Mood(1.03, 15, 0.97),
    "curious": Mood(1.0, 35, 1.0),
    "surprised": Mood(0.95, 110, 1.1),
    "thoughtful": Mood(1.07, -15, 0.92),
    "calm": Mood(1.08, -20, 0.9),
    "serious": Mood(1.04, -35, 1.0),
    "sorry": Mood(1.08, -50, 0.88),
    "sad": Mood(1.12, -80, 0.82),
}

# Sounds, and how each is made when the voice has no way of its own to
# make it: a breath of air, or a word said as the sound.
SOUNDS = ("laugh", "chuckle", "sigh", "breath", "hm", "gasp")
SAID_SOUNDS = {"laugh": "Ha ha!", "chuckle": "Heh.", "hm": "Hmm."}

_CUE = re.compile(
    r"\[(" + "|".join([*MOODS, *SOUNDS]) + r")\][ \t]*", re.IGNORECASE,
)
# What the model might write for a sound it has no cue for: kept out of
# what is said, since a voice reads "(laughs)" out as words.
_STAGE = re.compile(r"\*(?:laughs?|chuckles?|sighs?|smiles?|grins?)\*"
                    r"|\((?:laughs?|chuckles?|sighs?|smiles?)\)",
                    re.IGNORECASE)


def strip(text: str) -> str:
    """TEXT with its cues taken out: as it is shown, captioned, or kept."""

    return _STAGE.sub("", _CUE.sub("", text or ""))


def pieces(sentence: str) -> tuple[str, list[tuple[str, str]]]:
    """SENTENCE as it is to be said: its feeling ("" for none) and, in
    order, ("say", words) and ("sound", name) pieces."""

    mood = ""
    out: list[tuple[str, str]] = []
    at = 0
    for found in _CUE.finditer(sentence or ""):
        before = sentence[at:found.start()]
        if before.strip():
            out.append(("say", before.strip()))
        name = found.group(1).lower()
        if name in MOODS:
            mood = mood or name
        else:
            out.append(("sound", name))
        at = found.end()
    rest = (sentence or "")[at:]
    if rest.strip():
        out.append(("say", rest.strip()))
    return mood, [(kind, _STAGE.sub("", text).strip() if kind == "say"
                   else text) for kind, text in out
                  if kind == "sound" or _STAGE.sub("", text).strip()]


VARY_CHARS = 24


def variation(text: str) -> tuple[float, int]:
    """A sentence's own small difference in pace and pitch: the same
    each time it is said, so a line said again sounds the same."""

    seed = zlib.crc32((text or "").encode("utf-8"))
    pick = random.Random(seed)
    pace = round(pick.uniform(0.98, 1.02), 3)
    cents = pick.randint(-20, 20)
    # A few words are over too soon for a difference to tell, and a voice
    # tried out by a line of them is heard as it was set.
    if len(text or "") < VARY_CHARS:
        return 1.0, 0
    return pace, cents


def delivery(mood: str, text: str, vary: bool = True) -> Mood:
    """How a sentence in MOOD is said: its feeling, and its own small
    variation on top."""

    base = MOODS.get(mood, Mood())
    if not vary:
        return base
    pace, cents = variation(text)
    return Mood(round(base.pace * pace, 3), base.detune + cents, base.gain)


# Sounds -------------------------------------------------------------------


def _noise(rate: int, seconds: float, rise: float, fall: float,
           loud: float, seed: int, low: float = 0.15,
           high: float = 0.6) -> bytes:
    """Breath: noise, softened at both ends (LOW and HIGH are how much
    of the lows and highs are kept), in and out over RISE and FALL of
    SECONDS."""

    pick = random.Random(seed)
    count = int(rate * seconds)
    out = array.array("h")
    slow = fast = 0.0
    for i in range(count):
        white = pick.uniform(-1.0, 1.0)
        slow += low * (white - slow)  # takes off the hiss
        fast += high * (slow - fast)
        t = i / rate
        if t < rise:
            shape = math.sin(math.pi / 2 * t / rise) ** 2
        else:
            shape = max(0.0, 1 - (t - rise) / max(fall, 1e-3)) ** 1.6
        out.append(int(max(-1.0, min(1.0, fast * 3.2 * shape * loud))
                       * 32767))
    return out.tobytes()


def breath(rate: int, kind: str = "breath", seed: int = 0) -> bytes:
    """A breath in (before speaking), a sigh (out, longer) or a gasp
    (in, sharp), as 16-bit audio at RATE."""

    if kind == "sigh":
        return _noise(rate, 0.85, 0.12, 0.7, 0.16, seed, low=0.1)
    if kind == "gasp":
        return _noise(rate, 0.3, 0.05, 0.22, 0.2, seed, high=0.8)
    return _noise(rate, 0.42, 0.16, 0.24, 0.07, seed)


# How long a sentence is before a breath may come ahead of it, and how
# many such sentences in ten have one.
BREATH_CHARS = 80
BREATHS_IN_TEN = 6


def breathes(sentence: str) -> bool:
    """Whether SENTENCE is said after a breath in: a long one, some of the
    time, decided by the sentence so it does not change between takes."""

    if len(sentence or "") < BREATH_CHARS:
        return False
    return zlib.crc32(sentence.encode("utf-8")) % 10 < BREATHS_IN_TEN


def louder(pcm: bytes, gain: float) -> bytes:
    """16-bit PCM made GAIN times as loud, kept from clipping."""

    if not pcm or abs(gain - 1.0) < 1e-3:
        return pcm
    samples = array.array("h", pcm[:len(pcm) - len(pcm) % 2])
    for i, value in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(value * gain)))
    return samples.tobytes()


# For the ear -----------------------------------------------------------------

_SAID = (
    (re.compile(r"\be\.g\.", re.I), "for example"),
    (re.compile(r"\bi\.e\.", re.I), "that is"),
    (re.compile(r"\betc\.", re.I), "and so on"),
    (re.compile(r"\bvs\.?(?=\s)", re.I), "versus"),
    (re.compile(r"\bw/o\b", re.I), "without"),
    (re.compile(r"\bw/(?=\s)", re.I), "with"),
    (re.compile(r"(\d)\s?%"), r"\1 percent"),
    (re.compile(r"\$(\d[\d,]*(?:\.\d+)?)"), r"\1 dollars"),
    (re.compile(r"(\d)\s?ms\b"), r"\1 milliseconds"),
    (re.compile(r"(\d)\s?(KB|MB|GB|TB)\b"),
     lambda m: f"{m.group(1)} {_BYTES[m.group(2)]}"),
)
_BYTES = {"KB": "kilobytes", "MB": "megabytes", "GB": "gigabytes",
          "TB": "terabytes"}
_TOKEN = re.compile(r"\S*/\S*")
_FILE = re.compile(
    r"\b([\w-]+)\.(py|js|ts|tsx|jsx|json|md|txt|html|css|yml|yaml|toml|"
    r"sh|rs|go|java|swift|kt|rb|php|c|h|cpp|sql|csv|env|lock|xml|pdf|png|"
    r"jpg|svg|wav|mp3|mp4)\b")


def for_ear(text: str) -> str:
    """TEXT the way a person would say it: a path by the file at its end,
    "web.py" as "web dot py", "e.g." as "for example", "40%" as "40
    percent". Cues pass through as they are."""

    for pattern, said in _SAID:
        text = pattern.sub(said, text or "")
    text = _TOKEN.sub(_path_said, text)
    return _FILE.sub(r"\1 dot \2", text)


def _path_said(found: re.Match) -> str:
    """A path as the file it ends on; anything else with a slash in it
    ("and/or", "1/2", an address) as it was."""

    token = found.group(0)
    core = token.strip(".,;:!?()\"'")
    if "://" in core or not re.search(r"[A-Za-z]", core):
        return token
    parts = [p for p in core.split("/") if p]
    last = parts[-1] if parts else ""
    pathlike = core.startswith(("~", "/", "./", "../")) or \
        core.count("/") >= 2 or bool(re.search(r"\.[A-Za-z]\w*$", last))
    if not pathlike or not last:
        return token
    return token.replace(core, last)


# Timing --------------------------------------------------------------------

# Words a sentence does not end on: heard last, the speaker is mid-thought
# and the pause is a breath, not the end of their turn.
_UNFINISHED = {
    "and", "but", "so", "or", "because", "cause", "cuz", "the", "a", "an",
    "to", "of", "with", "for", "um", "uh", "er", "erm", "like", "if",
    "then", "my", "your", "our", "their", "his", "her", "its", "is", "are",
    "was", "were", "i", "i'm", "we", "you", "it's", "that's", "in", "on",
    "at", "from", "about", "when", "where", "which", "who", "whether",
    "just", "also", "maybe", "than", "into", "onto", "by", "as", "and,",
    "this", "these", "those", "what", "how", "why", "can", "could",
    "would", "should", "will", "do", "does", "did", "not", "very", "really",
    "some", "any", "more", "most", "let", "let's", "gonna", "wanna",
}
_ASKING = {"what", "how", "why", "when", "where", "who", "which", "can",
           "could", "would", "will", "is", "are", "do", "does", "did",
           "should", "shall", "may", "have", "has"}


def end_pause(heard: str) -> float:
    """How much longer than usual to wait, after a pause, before taking
    the user to be done: more when what they said so far trails off ("and
    then I..."), less when it sounds finished, a question above all."""

    words = re.sub(r"[^a-z' ]", " ", (heard or "").lower()).split()
    if not words:
        return 1.0
    if words[-1] in _UNFINISHED or (heard or "").rstrip().endswith(","):
        return 1.8
    if len(words) >= 3 and words[0] in _ASKING:
        return 0.7
    if len(words) >= 4:
        return 0.85
    return 1.0


# A word said at once, while the answer is still on its way, so the user
# hears they were heard: picked for what they said, never one stock line.
_ACKS = {
    "bad": ("Oh no.", "Ah, okay.", "Hmm, okay."),
    "good": ("Oh nice!", "Ooh!", "Oh, love that."),
    "ask": ("Hmm.", "Ooh, okay.", "Let me see.", "Hmm, let me think."),
    "do": ("Okay.", "Sure.", "On it.", "Yep."),
}
_BAD = re.compile(r"\b(broke|broken|break(?:s|ing)?|fail(?:s|ed|ing)?|"
                  r"error|crash(?:ed|es|ing)?|bug|stuck|not working|"
                  r"doesn'?t work|won'?t|can'?t|wrong|lost|deleted)\b")
_GOOD = re.compile(r"\b(it worked|works now|finally|awesome|amazing|"
                   r"great news|yay|i did it|we did it|nailed it|passed)\b")
_DOING = re.compile(r"^(?:please |can you |could you |would you )?(open|"
                    r"make|write|find|show|create|add|fix|play|send|check|"
                    r"run|set|turn|tell|read|search|look|start|stop|build|"
                    r"put|move|delete|remove|rename|change|update|draft|"
                    r"call|book|remind|schedule|summari[sz]e)\b")


def ack_kind(heard: str) -> str:
    text = " ".join((heard or "").lower().split())
    words = text.split()
    if len(words) < 3 or re.match(r"^(thanks|thank you|bye|good ?bye|"
                                  r"hi|hey|hello|ok|okay)\b", text):
        return ""
    if _GOOD.search(text):
        return "good"
    if _BAD.search(text):
        return "bad"
    if _DOING.match(text):
        return "do"
    if words[0] in _ASKING or text.endswith("?"):
        return "ask"
    return ""


def ack_for(heard: str, seed: Optional[int] = None) -> str:
    """The word said at once to HEARD, or "" when nothing fits: a thank
    you, a hello, or something too short to need it."""

    kind = ack_kind(heard)
    if not kind:
        return ""
    choices = _ACKS[kind]
    pick = random.Random(seed if seed is not None
                         else zlib.crc32(heard.encode("utf-8")))
    return choices[pick.randrange(len(choices))]


BACKCHANNELS = ("Mm-hm.", "Yeah.", "Right.", "Mm.")
# Said while an answer takes a while: once soon after they finish, and
# once more if the work goes on long.
FILLERS = ("Hmm...", "Let me see...", "One sec...", "Still on it.")
STILL = FILLERS[-1]
# Said into a silence: after a question that needs thought, and on a
# call where the line has gone quiet a while.
QUIET = {"think": "Take your time.", "there": "You still there?"}


# Hearing how the user sounds ---------------------------------------------


@dataclass
class Voiceprint:
    """How one stretch of speech sounded: loudness, pace in words a
    second, pitch (from how often the signal crosses zero while voiced)
    and how much that pitch moved."""

    loud: float
    pace: float
    pitch: float
    lively: float


def measure(pcm: bytes, text: str, rate: int = 16000) -> Optional[Voiceprint]:
    samples = array.array("h", pcm[:len(pcm) - len(pcm) % 2])
    frame = max(1, rate // 50)  # 20 ms
    levels, crossings = [], []
    for start in range(0, len(samples) - frame + 1, frame):
        chunk = samples[start:start + frame]
        energy = math.sqrt(sum(s * s for s in chunk) / frame)
        levels.append(energy)
        crossings.append(sum(1 for a, b in zip(chunk, chunk[1:])
                             if (a < 0) != (b < 0)))
    if not levels:
        return None
    # Voiced: well above the quiet between words, where there is any; a
    # stretch of speech with none has its floor near its peak.
    floor = sorted(levels)[len(levels) // 5]
    gate = max(300.0, min(floor * 2.5, max(levels) * 0.3))
    voiced = [i for i, level in enumerate(levels) if level > gate]
    words = len((text or "").split())
    if len(voiced) < 10 or not words:
        return None
    seconds = len(voiced) * frame / rate
    pitches = [crossings[i] * rate / frame / 2 for i in voiced]
    mean = sum(pitches) / len(pitches)
    spread = math.sqrt(sum((p - mean) ** 2 for p in pitches) / len(pitches))
    return Voiceprint(
        loud=sum(levels[i] for i in voiced) / len(voiced),
        pace=words / max(seconds, 0.3),
        pitch=mean,
        lively=spread / max(mean, 1.0),
    )


_LAUGH = re.compile(r"\b(ha ?ha|haha+|hehe+|lol|lmao)\b", re.I)
_UPSET = re.compile(r"\b(ugh|damn|dammit|annoying|frustrat\w*|hate|"
                    r"seriously|again\?*|still not|why won'?t)\b", re.I)


class Ear:
    """Hears how the user sounds, against how they usually sound: kept
    for one conversation, since everyone's own voice is their baseline."""

    def __init__(self) -> None:
        self.heard: list[Voiceprint] = []

    def mood(self, pcm: bytes, text: str) -> str:
        """In a few words, how the user sounds now ("" when like they
        usually do, or before there is enough to compare against)."""

        if _LAUGH.search(text or ""):
            return "laughing"
        now = measure(pcm, text)
        if now is None:
            return ""
        before = self.heard[-8:]
        self.heard.append(now)
        if len(before) < 2:
            return "frustrated" if _UPSET.search(text or "") else ""
        usual = Voiceprint(*(sum(getattr(v, f) for v in before) / len(before)
                             for f in ("loud", "pace", "pitch", "lively")))

        def up(field: str, by: float) -> bool:
            return getattr(now, field) > getattr(usual, field) * (1 + by)

        def down(field: str, by: float) -> bool:
            return getattr(now, field) < getattr(usual, field) * (1 - by)

        if _UPSET.search(text or "") and (up("loud", 0.15) or up("pace", 0.1)):
            return "frustrated"
        brighter = up("lively", 0.1) or up("pitch", 0.08)
        if up("loud", 0.2) and up("pace", 0.12) and brighter:
            return "excited"
        if up("pitch", 0.08) and (up("lively", 0.1) or up("pace", 0.1)):
            return "upbeat"
        if down("loud", 0.2) and down("pace", 0.12):
            return "tired or down"
        if down("lively", 0.25) and not up("loud", 0.1):
            return "flat"
        return ""


# What the model is told --------------------------------------------------

SPOKEN = """
How to sound like a person on the phone, not text read aloud:
- Write for the ear: contractions, everyday words, short sentences mixed
  with longer ones. Never read out lists, symbols, paths, or code; say
  "the config file", "about three thousand", "that link I put on screen".
- Say a feeling where it changes, in brackets at the start of a sentence:
  [happy] [excited] [playful] [warm] [curious] [surprised] [thoughtful]
  [calm] [serious] [sorry] [sad]. Your voice takes it on; most sentences
  need none.
- Make the sounds people make, where they would, and sparingly (one a
  reply at most): [laugh] [chuckle] [sigh] [breath] [hm] [gasp].
- Use their name now and then, not every time. Bring back what they said
  earlier in this conversation, or before, when it matters.
- Before using a tool that takes a moment, say a few words of what you
  are doing ("Okay, let me open that."), then use it.
- Notice the time of day when it fits: a late night, an early start.
- A note in parentheses with what they said tells you how they sound,
  their time of day, or what you already said out loud; answer
  accordingly, and never read the note back."""

# Thinking aloud: how often a reply has the small "um", or the correction
# made halfway through a sentence, that people make speaking.
DISFLUENCY_SETTING = "VOICE_DISFLUENCY"
DISFLUENCIES = {
    "off": "Off: every sentence comes out whole",
    "light": "A little: an um or a second thought now and then",
    "more": "More: thinks aloud the way people do",
}
DEFAULT_DISFLUENCY = "light"
_THINKING_ALOUD = {
    "light": """
- Now and then (one reply in a few), think aloud the way people do: a
  small "um" or "uh" before a hard word, or a correction halfway through
  ("it's in the first, actually, the second tab"). Never in a sentence
  that has to be exact, like a number or a name.""",
    "more": """
- Think aloud the way people do, in most replies: an "um" or "uh"
  where you are choosing a word, a restart ("so the, the thing is"), or
  a correction halfway through ("it's in the first, actually, the second
  tab"). Never in a sentence that has to be exact, like a number or a
  name.""",
}


def disfluency() -> str:
    import os

    found = (os.environ.get(DISFLUENCY_SETTING) or "").strip().lower()
    return found if found in DISFLUENCIES else DEFAULT_DISFLUENCY


def spoken() -> str:
    """SPOKEN, with thinking aloud as the user has it set."""

    return SPOKEN + _THINKING_ALOUD.get(disfluency(), "")


def part_of_day(hour: int) -> str:
    if hour < 5:
        return "the middle of the night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "late at night"


def note_for(given) -> str:
    """The note a spoken turn's model gets with what the user said, from
    what the page knows: how they sounded ("mood"), the word already said
    to them at once ("ack"), and the line they cut off ("cut")."""

    if not isinstance(given, dict):
        return ""
    said = []
    mood = " ".join(str(given.get("mood") or "").split())[:40]
    if mood:
        said.append(f"They sound {mood}.")
    clock = " ".join(str(given.get("time") or "").split())[:20]
    hour = given.get("hour")
    if clock and isinstance(hour, int) and 0 <= hour < 24:
        said.append(f"It is {clock} for them, {part_of_day(hour)}.")
    ack = " ".join(str(given.get("ack") or "").split())[:40]
    if ack:
        said.append(f'You already said "{ack}" out loud, so carry on from '
                    "it without saying it again.")
    cut = " ".join(str(given.get("cut") or "").split())[:300]
    if cut:
        said.append(f'They talked over you while you were saying "{cut}". '
                    "Answer them; if what you were saying still matters, "
                    "come back to it briefly after.")
    return f"({' '.join(said)})" if said else ""
