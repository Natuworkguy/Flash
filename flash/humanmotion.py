"""The mouse and keyboard as a person works them, for Computer Use.

A person does not jump the pointer to a button. They move it along a gentle curve, quick in the middle and slowing
as it arrives, taking longer the farther and smaller the target is (Fitts's law); a long reach often overshoots a
little and comes back. The hand shakes a little the whole way. They stop on the target for a moment before
pressing, hold the button down for a tenth of a second or so, and double-click with a short gap. They type in an
uneven rhythm: quicker through common pairs of letters, slower after a space, slower still after the end of a
sentence, and now and then pausing as if to think. A wheel turns in notches.

Everything here only plans: each action becomes a list of steps, which the computer's own driver plays.

    {"m": [x, y]}       move the pointer there
    {"w": seconds}      wait
    {"d": button}       press a button ("left", "right", "middle"); "n" the click's count, 2 for a double-click's second
    {"u": button}       let it go
    {"t": text}         type these characters
    {"s": [up, side]}   turn the wheel by notches

Steps are made from a seed, so a plan can be checked; in use each plan gets a fresh one.
"""

from __future__ import annotations

import math
import random
from typing import Optional

STEP = 1 / 125                 # how often the pointer is moved along its way: 125 times a second
FITTS_A, FITTS_B = .1, .11     # seconds: a person's start-up time, and per bit of difficulty
TARGET = 24.0                  # a target's size when nobody said, in points

# Pairs of letters typed quickest, being common and split across the hands.
_QUICK = {"th", "he", "in", "er", "an", "re", "on", "at", "en", "nd", "ti", "es", "or", "te", "of", "ed", "is", "it",
          "al", "ar", "st", "to", "nt", "ng", "se", "ha", "as", "ou", "io", "le", "ve", "co", "me", "de", "hi", "ri"}


def _min_jerk(t: float) -> float:
    """How far along a smooth reach is at T (0..1): slow off, quick through the middle, slow in."""

    return t * t * t * (10 - 15 * t + 6 * t * t)


def duration(distance: float, width: float = TARGET) -> float:
    """How long a person takes to reach a target WIDTH across, DISTANCE away."""

    return FITTS_A + FITTS_B * math.log2(distance / max(width, 4.0) + 1)


def _reach(start, end, seconds: float, rng: random.Random, bend: Optional[float] = None) -> list:
    """Points along one curved reach from START to END over SECONDS, with tremor."""

    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1:
        return []
    # bowed to one side by up to a sixth of the way, as a wrist turning does
    nx, ny = -dy / dist, dx / dist
    bow = (bend if bend is not None else rng.uniform(-.16, .16)) * dist
    mx, my = x0 + dx * .5 + nx * bow, y0 + dy * .5 + ny * bow
    n = max(2, int(seconds / STEP))
    # tremor: a slow wander a point or so wide, fading as the hand settles on the target
    ph1, ph2 = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
    amp = min(1.6, .4 + dist / 600)
    out = []
    for i in range(1, n + 1):
        s = _min_jerk(i / n)
        x = (1 - s) ** 2 * x0 + 2 * (1 - s) * s * mx + s * s * x1
        y = (1 - s) ** 2 * y0 + 2 * (1 - s) * s * my + s * s * y1
        fade = 1 - s
        x += amp * fade * math.sin(i * .21 + ph1)
        y += amp * fade * math.sin(i * .17 + ph2)
        out.append({"m": [round(x, 1), round(y, 1)]})
        out.append({"w": round(STEP, 4)})
    out[-2] = {"m": [round(x1, 1), round(y1, 1)]}
    return out


def move(start, end, rng: random.Random, width: float = TARGET) -> list:
    """The pointer taken from START to END as a hand takes it."""

    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if dist < 2:
        return [{"m": [round(end[0], 1), round(end[1], 1)]}]
    seconds = duration(dist, width) * rng.uniform(.85, 1.2)
    # a long reach overshoots now and then, then corrects in a short second movement
    if dist > 180 and rng.random() < .4:
        over = rng.uniform(.03, .08) * dist
        ux, uy = (end[0] - start[0]) / dist, (end[1] - start[1]) / dist
        past = (end[0] + ux * over + rng.uniform(-3, 3), end[1] + uy * over + rng.uniform(-3, 3))
        steps = _reach(start, past, seconds * .85, rng)
        steps.append({"w": round(rng.uniform(.04, .1), 3)})
        steps += _reach(past, end, duration(over, width) * .8, rng, bend=rng.uniform(-.05, .05))
        return steps
    return _reach(start, end, seconds, rng)


def click(start, at, rng: random.Random, button: str = "left", count: int = 1) -> list:
    steps = move(start, at, rng)
    steps.append({"w": round(rng.uniform(.06, .18), 3)})          # settling on it, then pressing
    for k in range(count):
        if k:
            steps.append({"w": round(rng.uniform(.07, .12), 3)})   # a double-click's gap
        steps += [{"d": button, "n": k + 1}, {"w": round(rng.uniform(.065, .12), 3)}, {"u": button, "n": k + 1}]
    return steps


def drag(start, frm, to, rng: random.Random) -> list:
    steps = move(start, frm, rng)
    steps += [{"w": round(rng.uniform(.08, .16), 3)}, {"d": "left"}, {"w": round(rng.uniform(.1, .2), 3)}]
    dist = math.hypot(to[0] - frm[0], to[1] - frm[1])
    # carrying something, people move more slowly and more carefully
    steps += _reach(frm, to, duration(dist) * rng.uniform(1.25, 1.6), rng, bend=rng.uniform(-.08, .08))
    steps += [{"w": round(rng.uniform(.1, .2), 3)}, {"u": "left"}]
    return steps


def scroll(start, at, direction: str, amount: int, rng: random.Random) -> list:
    steps = move(start, at, rng)
    up, side = {"up": (1, 0), "down": (-1, 0), "left": (0, 1), "right": (0, -1)}[direction]
    for k in range(amount):
        steps.append({"w": round(rng.uniform(.03, .09) if k else rng.uniform(.08, .2), 3)})
        steps.append({"s": [up, side]})
    return steps


def type_text(text: str, rng: random.Random, wpm: Optional[float] = None) -> list:
    """TEXT typed a key at a time, in a person's rhythm (about 55 to 85 words a minute)."""

    wpm = wpm or rng.uniform(55, 85)
    per_key = 60.0 / (wpm * 5)
    steps = []
    prev = ""
    for ch in text:
        gap = rng.lognormvariate(math.log(per_key), .32)
        pair = (prev + ch).lower()
        if pair in _QUICK:
            gap *= .72
        if prev == " ":
            gap *= 1.35
        if prev in ".!?" and ch == " ":
            gap += rng.uniform(.25, .6)
        if prev == "\n":
            gap += rng.uniform(.2, .5)
        if ch.isupper() and prev and not prev.isupper():
            gap *= 1.25                                              # reaching for Shift
        if rng.random() < .015:
            gap += rng.uniform(.3, .8)                               # a moment's thought
        steps.append({"w": round(min(gap, 1.5), 3)})
        steps.append({"t": ch})
        prev = ch
    return steps


def total(steps: list) -> float:
    return sum(s.get("w", 0.0) for s in steps)


def batches(steps: list, seconds: float = 1.5) -> list:
    """STEPS in pieces of about SECONDS each, so the user can take the computer back between them."""

    out, cur, t = [], [], 0.0
    for s in steps:
        cur.append(s)
        t += s.get("w", 0.0)
        # never between a press and its release
        held = sum(1 for c in cur if "d" in c) > sum(1 for c in cur if "u" in c)
        if t >= seconds and not held:
            out.append(cur)
            cur, t = [], 0.0
    if cur:
        out.append(cur)
    return out
