"""Tether: a game you and Flash play together, on the same side.

You are tied together by a rope. Sparks are caught by sweeping the rope
through them (or touching them), so the two of you have to move as a pair:
one of you holds a spark's far side while the other swings round it. Drones
wander the arena; if one touches the rope it snaps, costing three sparks and a
moment to tie it again. One shared score, ninety seconds, three marks to reach.

The game runs here, in Flash, thirty times a second, so both players see one
game. You play in your browser with the keyboard. Flash plays it itself,
turn by turn, through its tether tool: it looks at the game, decides where
to go and what to say, and its player walks there while it looks again.
Nothing plays for it. The arena listens on this computer only.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

PAGE = Path(__file__).parent / "games" / "tether.html"
W, H = 960, 600  # the arena, in its own pixels
TICK = 1 / 30
SPEED = 250  # pixels a second, the same for both
DASH, DASH_FOR, DASH_EVERY = 2.3, 0.18, 2.0
RADIUS, SPARK_R, DRONE_R = 18, 10, 16
ROPE = 340  # the rope's length: pull further and it drags the other along
SPARKS, DRONES = 5, 3
ROUND = 90  # seconds
MARKS = (25, 45, 65)  # good, great, legendary
SNAP_COST, SNAP_FOR = 3, 1.5
STUN = 0.8
PLAYERS = ("you", "flash")
PRESENT = 30  # seconds Flash counts as at the table after its last move
SAY_FOR = 4.0  # seconds a line Flash says stays over its head


def seg_dist(px, py, ax, ay, bx, by) -> float:
    """How far point P is from the segment A to B."""
    dx, dy = bx - ax, by - ay
    n = dx * dx + dy * dy
    k = (
        0.0
        if n == 0
        else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / n))
    )
    return math.hypot(px - (ax + k * dx), py - (ay + k * dy))


class Game:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rng = random.Random()
        self.reset()

    def reset(self) -> None:
        self.phase = "lobby"  # lobby -> countdown -> play -> over
        self.clock = 0.0
        self.score = 0
        self.best_run = 0
        self.run = 0
        self.snapped = 0.0
        self.snaps = 0
        self.p: dict[str, dict] = {
            who: {
                "x": W * x,
                "y": H / 2,
                "keys": set(),
                "here": 0.0,
                "ready": False,
                "stun": 0.0,
                "dash": 0.0,
                "cool": 0.0,
                "goals": [],
                "dash_ask": False,
                "said": "",
                "said_until": 0.0,
                "vx": 0.0,
                "vy": 0.0,
                "away": 0.0,
            }
            for who, x in (("you", 0.38), ("flash", 0.62))
        }
        self.sparks = [self._spot() for _ in range(SPARKS)]
        self.drones = [self._drone() for _ in range(DRONES)]

    def _spot(self) -> dict:
        for _ in range(40):
            x = self.rng.uniform(50, W - 50)
            y = self.rng.uniform(50, H - 50)
            if all(
                math.hypot(x - q["x"], y - q["y"]) > 90
                for q in getattr(self, "p", {}).values()
            ):
                break
        return {"x": x, "y": y}

    def _drone(self) -> dict:
        a = self.rng.uniform(0, math.tau)
        return {
            "x": self.rng.uniform(100, W - 100),
            "y": self.rng.choice([60, H - 60]),
            "vx": math.cos(a) * 120,
            "vy": math.sin(a) * 120,
        }

    # ----- what players do -----
    def keys(self, who: str, held: list, ready: bool) -> None:
        if who not in self.p:
            return
        with self.lock:
            me = self.p[who]
            me["keys"] = {str(k) for k in held[:12]}
            me["here"] = time.monotonic()
            if ready and self.phase in ("lobby", "over"):
                me["ready"] = True
                if all(q["ready"] for q in self.p.values()):
                    kept = {k: (q["here"], q["away"]) for k, q in self.p.items()}
                    self.reset()
                    for k, q in self.p.items():
                        q["here"], q["away"] = kept[k]
                    self.phase, self.clock = "countdown", 3.0

    def act(self, who: str, goals: list, dash: bool = False,
            say: str = "", ready: bool = False) -> None:
        """A player's decision, not a key held: where to walk, in order,
        whether to dash on the way, a line to say, and ready in the lobby.
        Its player walks there at the same speed a held key moves it."""
        if who not in self.p:
            return
        now = time.monotonic()
        with self.lock:
            me = self.p[who]
            me["here"], me["away"] = now, now + PRESENT
            me["keys"] = set()
            me["goals"] = [
                (min(max(float(x), RADIUS), W - RADIUS),
                 min(max(float(y), RADIUS), H - RADIUS))
                for x, y in goals[:6]
            ]
            me["dash_ask"] = bool(dash)
            if say:
                me["said"] = " ".join(str(say).split())[:80]
                me["said_until"] = now + SAY_FOR
        if ready:
            self.keys(who, [], True)
            with self.lock:
                self.p[who]["away"] = now + PRESENT

    # ----- one step of the world -----
    def step(self, dt: float) -> None:
        with self.lock:
            if self.phase == "countdown":
                self.clock -= dt
                if self.clock <= 0:
                    self.phase, self.clock = "play", float(ROUND)
                return
            if self.phase != "play":
                return
            self.clock -= dt
            self.snapped = max(0.0, self.snapped - dt)
            for d in self.drones:
                d["x"] += d["vx"] * dt
                d["y"] += d["vy"] * dt
                if not DRONE_R <= d["x"] <= W - DRONE_R:
                    d["vx"] *= -1
                    d["x"] = min(max(d["x"], DRONE_R), W - DRONE_R)
                if not DRONE_R <= d["y"] <= H - DRONE_R:
                    d["vy"] *= -1
                    d["y"] = min(max(d["y"], DRONE_R), H - DRONE_R)
            for me in self.p.values():
                was = me["x"], me["y"]
                self._move(me, dt)
                me["_was"] = was
            self._rope()
            for me in self.p.values():
                ox, oy = me.pop("_was")
                if dt > 0:
                    me["vx"] = me["vx"] * .6 + (me["x"] - ox) / dt * .4
                    me["vy"] = me["vy"] * .6 + (me["y"] - oy) / dt * .4
            a, b = self.p["you"], self.p["flash"]
            tied = self.snapped == 0
            for i, s in enumerate(self.sparks):
                touch = any(
                    math.hypot(q["x"] - s["x"], q["y"] - s["y"])
                    < RADIUS + SPARK_R
                    for q in (a, b)
                )
                swept = (
                    tied
                    and seg_dist(
                        s["x"], s["y"], a["x"], a["y"], b["x"], b["y"]
                    )
                    < SPARK_R + 3
                )
                if touch or swept:
                    self.score += 1
                    self.run += 1
                    self.best_run = max(self.best_run, self.run)
                    self.sparks[i] = self._spot()
            for d in self.drones:
                for q in (a, b):
                    if (
                        q["stun"] == 0
                        and math.hypot(q["x"] - d["x"], q["y"] - d["y"])
                        < RADIUS + DRONE_R - 4
                    ):
                        q["stun"] = STUN
                if (
                    tied
                    and seg_dist(
                        d["x"], d["y"], a["x"], a["y"], b["x"], b["y"]
                    )
                    < DRONE_R
                ):
                    # the rope is cut: it costs sparks, and time to tie again
                    self.snapped = SNAP_FOR
                    self.snaps += 1
                    self.run = 0
                    self.score = max(0, self.score - SNAP_COST)
                    tied = False
            if self.clock <= 0:
                self.phase = "over"
                for q in self.p.values():
                    q["ready"] = False
                    q["keys"] = set()

    def _move(self, me: dict, dt: float) -> None:
        me["cool"] = max(0.0, me["cool"] - dt)
        me["dash"] = max(0.0, me["dash"] - dt)
        if me["stun"] > 0:
            me["stun"] = max(0.0, me["stun"] - dt)
            return
        k = me["keys"]
        dx = ("ArrowRight" in k or "d" in k) - ("ArrowLeft" in k or "a" in k)
        dy = ("ArrowDown" in k or "s" in k) - ("ArrowUp" in k or "w" in k)
        dashing = " " in k or "Space" in k or "Shift" in k
        # walking to where its player decided, as holding the keys would
        while not (dx or dy) and me["goals"]:
            gx, gy = me["goals"][0]
            far = math.hypot(gx - me["x"], gy - me["y"])
            if far < 4:
                me["goals"].pop(0)
                continue
            step = SPEED * (DASH if me["dash"] > 0 else 1) * dt
            if far <= step and me["dash"] == 0:
                me["x"], me["y"] = gx, gy
                me["goals"].pop(0)
                return
            dx, dy = (gx - me["x"]) / far, (gy - me["y"]) / far
            if me["dash_ask"] and me["cool"] == 0:
                dashing, me["dash_ask"] = True, False
            break
        if dashing and me["cool"] == 0 and (dx or dy):
            me["dash"], me["cool"] = DASH_FOR, DASH_EVERY
        n = math.hypot(dx, dy) or 1
        v = SPEED * (DASH if me["dash"] > 0 else 1) * dt
        me["x"] = min(max(me["x"] + dx / n * v, RADIUS), W - RADIUS)
        me["y"] = min(max(me["y"] + dy / n * v, RADIUS), H - RADIUS)

    def _rope(self) -> None:
        """Past the rope's length, each is pulled half the way back."""
        a, b = self.p["you"], self.p["flash"]
        gap = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
        if self.snapped == 0 and gap > ROPE:
            pull = (gap - ROPE) / 2
            ux, uy = (b["x"] - a["x"]) / gap, (b["y"] - a["y"]) / gap
            for q, sign in ((a, 1), (b, -1)):
                q["x"] = min(
                    max(q["x"] + ux * pull * sign, RADIUS), W - RADIUS
                )
                q["y"] = min(
                    max(q["y"] + uy * pull * sign, RADIUS), H - RADIUS
                )
        if 0 < gap < RADIUS * 2:  # bump apart
            push = (RADIUS * 2 - gap) / 2
            ux, uy = (a["x"] - b["x"]) / gap, (a["y"] - b["y"]) / gap
            a["x"] += ux * push
            a["y"] += uy * push
            b["x"] -= ux * push
            b["y"] -= uy * push

    def mark(self) -> str:
        names = ("good", "great", "legendary")
        got = [n for n, m in zip(names, MARKS) if self.score >= m]
        return got[-1] if got else ""

    def state(self) -> dict:
        now = time.monotonic()
        r = lambda v: round(v, 1)  # noqa: E731
        with self.lock:
            return {
                "phase": self.phase,
                "clock": round(max(0.0, self.clock), 2),
                "score": self.score,
                "run": self.run,
                "bestRun": self.best_run,
                "snaps": self.snaps,
                "snapped": self.snapped > 0,
                "mark": self.mark() if self.phase == "over" else "",
                "marks": MARKS,
                "rope": ROPE,
                "w": W,
                "h": H,
                "players": {
                    w: {
                        "x": r(q["x"]),
                        "y": r(q["y"]),
                        "here": now - q["here"] < 3 or now < q["away"],
                        "say": q["said"] if now < q["said_until"] else "",
                        "ready": q["ready"],
                        "stun": q["stun"] > 0,
                        "dash": q["dash"] > 0,
                    }
                    for w, q in self.p.items()
                },
                "moving": {
                    w: {"vx": r(q["vx"]), "vy": r(q["vy"])}
                    for w, q in self.p.items()
                },
                "sparks": [
                    {"x": r(s["x"]), "y": r(s["y"])} for s in self.sparks
                ],
                "drones": [
                    {"x": r(d["x"]), "y": r(d["y"]),
                     "vx": r(d["vx"]), "vy": r(d["vy"])}
                    for d in self.drones
                ],
            }


def _heading(vx: float, vy: float) -> str:
    speed = math.hypot(vx, vy)
    if speed < 15:
        return "still"
    names = ("right", "down-right", "down", "down-left", "left", "up-left",
             "up", "up-right")
    at = round(math.atan2(vy, vx) / (math.pi / 4)) % 8
    return f"{names[at]} {speed:.0f}/s"


def look(game: "Game | None" = None) -> str:
    """The game as words, for Flash to decide its next move on: where
    everyone is and is heading, what's near the rope, and the score."""
    s = (game or GAME).state()
    me, mate = s["players"]["flash"], s["players"]["you"]
    mv = s["moving"]
    lines = [
        f"{s['phase']}"
        + (f", {s['clock']:.0f}s left" if s["phase"] == "play" else "")
        + (f", starts in {s['clock']:.0f}" if s["phase"] == "countdown"
           else "")
        + f". Together {s['score']} (run {s['run']}, best {s['bestRun']}); "
        f"marks {', '.join(map(str, s['marks']))}."
        + (" The rope is CUT." if s["snapped"] else ""),
        f"You (Flash, orange) at {me['x']:.0f},{me['y']:.0f}, "
        f"{_heading(mv['flash']['vx'], mv['flash']['vy'])}"
        + (", stunned" if me["stun"] else "")
        + (", ready" if me["ready"] and s["phase"] != "play" else "") + ".",
        f"The user (blue) at {mate['x']:.0f},{mate['y']:.0f}, "
        f"{_heading(mv['you']['vx'], mv['you']['vy'])}"
        + (", stunned" if mate["stun"] else "")
        + ("" if mate["here"] else ", NOT in the game yet")
        + (", ready" if mate["ready"] and s["phase"] != "play" else "") + ".",
    ]
    gap = math.hypot(me["x"] - mate["x"], me["y"] - mate["y"])
    lines.append(f"Rope {gap:.0f} of {s['rope']} long. Arena "
                 f"{s['w']}x{s['h']}, 0,0 top left.")
    for i, sp in enumerate(s["sparks"], 1):
        line = seg_dist(sp["x"], sp["y"], me["x"], me["y"], mate["x"],
                        mate["y"])
        lines.append(
            f"Spark {i} at {sp['x']:.0f},{sp['y']:.0f}: "
            f"{math.hypot(sp['x'] - me['x'], sp['y'] - me['y']):.0f} from "
            f"you, {math.hypot(sp['x'] - mate['x'], sp['y'] - mate['y']):.0f}"
            f" from the user, {line:.0f} off the rope.")
    for i, d in enumerate(s["drones"], 1):
        line = seg_dist(d["x"], d["y"], me["x"], me["y"], mate["x"],
                        mate["y"])
        lines.append(
            f"Drone {i} at {d['x']:.0f},{d['y']:.0f}, heading "
            f"{_heading(d['vx'], d['vy'])}: {line:.0f} off the rope"
            + (" (close!)" if line < 60 else "") + ".")
    if s["phase"] == "over":
        lines.append(f"Over: {s['score']} together, mark "
                     f"{s['mark'] or 'none'}, rope cut {s['snaps']} times.")
    return "\n".join(lines)


GAME = Game()
_server: dict[str, Any] = {"httpd": None, "port": 0}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/state":
            self._send(
                200, json.dumps(GAME.state()).encode(), "application/json"
            )
        elif url.path == "/events":
            # the game as it happens: one state a tick, until the page goes
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    body = json.dumps(GAME.state()).encode()
                    self.wfile.write(b"data: " + body + b"\n\n")
                    self.wfile.flush()
                    time.sleep(TICK)
            except OSError:
                return
        else:
            self._send(404, b"not here", "text/plain")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/keys":
            self._send(404, b"not here", "text/plain")
            return
        who = (parse_qs(url.query).get("as") or [""])[0]
        if who == "flash":
            # Flash plays through its tool, never through this page.
            self._send(403, b"Flash plays through its tool", "text/plain")
            return
        size = min(int(self.headers.get("Content-Length") or 0), 4096)
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            self._send(400, b"not JSON", "text/plain")
            return
        GAME.keys(who, list(body.get("keys") or []), bool(body.get("ready")))
        self._send(204, b"", "text/plain")


def _loop() -> None:
    last = time.monotonic()
    while _server["httpd"] is not None:
        now = time.monotonic()
        GAME.step(min(0.1, now - last))
        last = now
        time.sleep(TICK)


def start(port: int = 0) -> str:
    """Start the game (once) and return its address, to open in a browser."""
    if _server["httpd"] is None:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        httpd.daemon_threads = True
        _server["httpd"], _server["port"] = httpd, httpd.server_address[1]
        threading.Thread(
            target=httpd.serve_forever, daemon=True, name="tether-http"
        ).start()
        threading.Thread(target=_loop, daemon=True, name="tether-tick").start()
    return f"http://127.0.0.1:{_server['port']}/"


def stop() -> None:
    httpd = _server["httpd"]
    _server["httpd"] = None
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()


def address() -> Optional[str]:
    return f"http://127.0.0.1:{_server['port']}/" if _server["httpd"] else None
