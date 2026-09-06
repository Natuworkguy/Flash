"""What a turn cost: how fast the model answered, and how full its
context got.

Ollama reports these counters on every chat response and they are worth
one dim line, because on local hardware they change what the user does
next. Forty tokens a second is a reply you wait for. Nine is a reply you
walk away from, and knowing which one you are getting is the difference
between waiting and wasting the wait.
"""

from typing import Union

from rich.text import Text

from .theme import DIM

NS_PER_SECOND = 1_000_000_000

# Under a percent, the number says nothing a reader can act on, so the
# line says so in words instead of rounding it away to 0%.
MIN_SHOWN_PERCENT = 1.0


def _count(response, name: str) -> int:
    """One counter off a chat response, whether typed or a plain dict.

    Ollama leaves these unset on some responses (a cache hit reports no
    eval duration at all), so a missing counter reads as zero rather
    than breaking the line.
    """

    value = getattr(response, name, None)

    if value is None and isinstance(response, dict):
        value = response.get(name)

    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class Turn:
    """Generation counters summed across every call one turn makes.

    A turn that calls tools asks the model several times, and the user
    sat through all of it, so the counts and the clock add up. Context
    fill is the high-water mark instead, since that is the prompt that
    came closest to the window.
    """

    def __init__(self) -> None:
        self.generated = 0
        self.eval_nanoseconds = 0
        self.total_nanoseconds = 0
        self.prompt_tokens = 0

    def add(self, response) -> None:
        """Fold one chat response into the running totals."""

        self.generated += _count(response, "eval_count")
        self.eval_nanoseconds += _count(response, "eval_duration")
        self.total_nanoseconds += _count(response, "total_duration")
        self.prompt_tokens = max(
            self.prompt_tokens, _count(response, "prompt_eval_count")
        )

    @property
    def tokens(self) -> int:
        """Everything the turn spent: the prompt it sent and the reply it
        generated. The prompt is re-sent on every tool round, so it counts
        once, at its high-water mark, rather than once per round."""

        return self.prompt_tokens + self.generated

    @property
    def seconds(self) -> float:
        """Wall time the user waited, prefill and model loading included."""

        return self.total_nanoseconds / NS_PER_SECOND

    @property
    def rate(self) -> Union[float, None]:  # noqa: UP007, RUF100
        """Tokens per second generated, or None if Ollama did not say.

        Generation only. Prefill runs an order of magnitude faster on the
        same hardware, and averaging the two hides the number that
        predicts how long the next reply takes.
        """

        if self.generated <= 0 or self.eval_nanoseconds <= 0:
            return None

        return self.generated / (self.eval_nanoseconds / NS_PER_SECOND)


def _window(limit: int) -> str:
    """A context size the way people say it: 65536 -> '64K'."""

    return f"{limit // 1024}K" if limit >= 1024 else str(limit)


def _elapsed(seconds: float) -> str:
    """A short duration: 93.4 -> '1m 33s'."""

    whole = int(seconds)

    if whole < 60:
        return f"{whole}s"

    return f"{whole // 60}m {whole % 60:02d}s"


def summary(
    turn: Turn,
    limit: Union[int, None] = None,  # noqa: UP007, RUF100
) -> Union[Text, None]:  # noqa: UP007, RUF100
    """The one-line cost of a finished turn, or None if there is nothing
    to say. Every part is dropped independently, so a backend that
    reports half the counters still gets half a line."""

    if turn.generated <= 0:
        return None

    line = Text(f"  {turn.tokens:,} tokens", style=DIM)

    if turn.seconds >= 1:
        line.append(f" in {_elapsed(turn.seconds)}")

    rate = turn.rate

    if rate is not None:
        line.append(f"   {rate:.1f} tok/s")

    if limit and turn.prompt_tokens > 0:
        percent = turn.prompt_tokens / limit * 100
        shown = (
            f"{percent:.0f}%"
            if percent >= MIN_SHOWN_PERCENT
            else f"under {MIN_SHOWN_PERCENT:.0f}%"
        )
        line.append(f"   context {shown} of {_window(limit)}")

    return line
