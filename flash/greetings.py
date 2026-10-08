"""What Flash says hello with, in the terminal's welcome box and on a new
chat in the web UI: one of a few for the time of day, and in the daytime
the day of the week.

The web UI picks from the same lists by the browser's own clock, so the
two read alike wherever Flash is opened. {day} is the day's name.
"""

from __future__ import annotations

import random
import time
from typing import Optional

POOLS = {
    "late": ["Up late?", "Burning the midnight oil?", "Hello, night owl",
             "Still at it?"],
    "morning": ["Good morning", "Coffee and Flash time?",
                "What's on the list today?"],
    "afternoon": ["Good afternoon", "How's the day going?"],
    "evening": ["Good evening", "How was your day?",
                "What's on your mind tonight?"],
    "night": ["Good evening", "Winding down?",
              "What's on your mind tonight?"],
    # Any time in the daytime, beside the ones for its part of the day.
    "any": ["Back at it", "What's next?", "Hey there", "Happy {day}"],
}

# The parts of the day that also draw from "any".
DAYTIME = ("morning", "afternoon", "evening")


def part_of_day(hour: int) -> str:
    if hour < 5:
        return "late"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening" if hour < 22 else "night"


def pick(
    now: Optional[time.struct_time] = None,
    rng: Optional[random.Random] = None,
) -> str:
    """A greeting for NOW, the local time unless given."""

    now = now or time.localtime()
    part = part_of_day(now.tm_hour)
    pool = list(POOLS[part])
    if part in DAYTIME:
        pool += POOLS["any"]
    chosen = (rng or random).choice(pool)  # nosec B311 -- not a secret
    return chosen.replace("{day}", time.strftime("%A", now))
