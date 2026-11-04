// A reflex for Flash Dash, as the agent wrote it: run every frame by
// play_realtime. It reads only the screen: spikes (red) on the ground,
// blocks and pillars (blue), pads (orange), rings (cyan), the square or
// ship (yellow, the ship with a pink trim). It measures how fast the
// level moves by tracking what it sees from frame to frame.
//
// As a square it simulates the game ahead: would running on die, and if
// so, which of the coming frames could a jump start on and land safe? It
// jumps in the middle of that window, so a frame early or late is still
// fine. In the air on a ring, it taps only if not tapping would die. As a
// ship it steers for the middle of the next gap between the pillars.
const GROUND = 240, CEIL = 40, SIZE = 30, PX = 150, SPIKE = 30;
const GRAVITY = 2400, JUMP = 640, PAD = 900, ORB = 680;
const FRAME = 1 / 60, SUB = 4, STEP = FRAME / SUB;
const m = api.memory;
if (m.attempt !== api.attempt) { m.attempt = api.attempt; m.prev = null; m.seen = []; m.width = undefined; m.py = []; m.sy = []; }

// ---- What is on screen ----
const spikes = [];
for (const [a, b] of api.runs("#ff3b5c", GROUND - 4, 0, api.width, 40)) {
  const n = Math.max(1, Math.round((b - a + 5) / SPIKE));
  for (let k = 0; k < n; k++) spikes.push(a - 2 + k * SPIKE);
}
const blue = api.blobs("#39a0ff", 0, CEIL, api.width, GROUND, { step: 2, min: 3 });
const blocks = blue.filter((b) => b.y + b.h >= GROUND - 2 && b.y > CEIL + 4).map((b) => ({ a: b.x, b: b.x + b.w, top: b.y }));
const pads = api.runs("#ff8a00", GROUND - 3, 0, api.width, 40).map(([a]) => a);
const orbs = api.blobs("#22d3ee", 0, 0, api.width, GROUND, { step: 2, min: 3 }).map((o) => ({ x: o.cx, y: o.cy }));
// The player's top, down the column at its back edge: a square's top, or
// the ship's back corner six pixels below its top. The ship has a pink
// trim along its underside.
const top = api.findDown("#ffd23f", PX + 1, 0, GROUND + 1, 30);
const ship = top >= 0 && api.findDown("#ff4fd8", PX + 5, top, top + 36, 45) >= 0;

// ---- How fast it moves: what was seen last frame, found again ----
const marks = [...spikes, ...blue.map((b) => b.x), ...pads, ...orbs.map((o) => o.x)].filter((x) => x > 20 && x < api.width - 20);
if (m.prev && api.dt > 0) {
  const moved = [];
  for (const x of marks) {
    let best = null;
    for (const p of m.prev) { const d = p - x; if (d > 1 && d < 16 && (best === null || Math.abs(d - 6.5) < Math.abs(best - 6.5))) best = d; }
    if (best !== null) moved.push(best / api.dt);
  }
  if (moved.length) { moved.sort((a, b) => a - b); m.seen.push(moved[moved.length >> 1]); if (m.seen.length > 7) m.seen.shift(); }
}
m.prev = marks;
const sorted = [...m.seen].sort((a, b) => a - b);
const speed = sorted.length ? Math.max(250, Math.min(520, sorted[sorted.length >> 1])) : 360;

// ---- The square: the game's own rules, run ahead ----
function hitsAt(left, bottom) {
  const right = left + SIZE, topY = bottom - SIZE;
  for (const s of spikes) if (right > s + 8 && left < s + SPIKE - 8 && bottom > GROUND - SPIKE + 10) return true;
  for (const k of blocks) if (right > k.a + 2 && left < k.b - 2 && bottom > k.top + 2 && topY < GROUND) return true;
  return false;
}
function floorUnder(left, bottom) {
  let f = GROUND;
  for (const k of blocks) if (left + SIZE > k.a + 1 && left < k.b - 1 && k.top >= bottom - 6) f = Math.min(f, k.top);
  return f;
}
// From the square's state now, with a tap at frame JUMP_AT (or never) and
// one at ORB_NOW (on a ring, now): does it live for FRAMES frames, or, if
// it jumps, till it has landed and run on a little?
// A plan that jumps also taps a ring the first frame it touches one.
const touches = (left, bottom) => orbs.some((o) => Math.hypot(Math.max(left, Math.min(o.x, left + SIZE)) - o.x, Math.max(bottom - SIZE, Math.min(o.y, bottom)) - o.y) < 16);
function lives(state, jumpAt, orbNow, frames) {
  let { bottom, vy, ground } = state, s = 0, landed = -1, ringed = orbNow;
  for (let f = 0; f < frames; f++) {
    for (let i = 0; i < SUB; i++) {
      s += speed * STEP;
      const left = PX + s;
      if (i === 0 && f === 0 && orbNow && !ground) vy = -ORB;
      if (i === 0 && f === jumpAt && ground) { vy = -JUMP; ground = false; }
      if (i === 0 && jumpAt !== null && f > jumpAt && !ground && !ringed && touches(left, bottom)) { vy = -ORB; ringed = true; }
      if (ground && bottom >= GROUND - 1) for (const p of pads) if (left + SIZE > p && left < p + 30) { vy = -PAD; ground = false; }
      const under = floorUnder(left, bottom);
      if (ground && under > bottom + .5) ground = false;
      if (!ground) {
        const was = bottom;
        vy += GRAVITY * STEP; bottom += vy * STEP;
        const land = floorUnder(left, was);
        if (vy >= 0 && bottom >= land) { bottom = land; vy = 0; ground = true; if (landed < 0 && (jumpAt !== null || orbNow)) landed = f; }
      }
      if (hitsAt(left, bottom)) return false;
    }
    if (landed >= 0 && f - landed > 6) return true;
  }
  return true;
}

if (!ship && top >= 0) {
  const bottom = top + SIZE;
  m.sy = [];
  m.py.push(bottom); if (m.py.length > 3) m.py.shift();
  const vy = m.py.length > 1 && api.dt > 0 ? (m.py[m.py.length - 1] - m.py[m.py.length - 2]) / api.dt : 0;
  const ground = Math.abs(bottom - floorUnder(PX, bottom)) < 1.5 && Math.abs(vy) < 30;
  const now = { bottom, vy: ground ? 0 : vy, ground };
  if (ground) {
    if (!lives(now, null, false, 50)) {
      let first = -1, last = -1;
      for (let d = 0; d < 45; d++) {
        const ok = lives(now, d, false, 70);
        if (ok && first < 0) first = d;
        if (!ok && first >= 0) { last = d - 1; break; }
      }
      if (first >= 0 && last < 0) last = first;
      if (first > 0 || (first === 0 && m.width === undefined)) m.width = last - first;
      if (first === 0 && last <= Math.floor((m.width || 0) / 2)) {
        api.tap(" "); api.log("jump at", Math.round(speed), "px/s, window", (m.width || 0) + 1, "frames");
        m.width = undefined;
      }
      if (first < 0 && !m.warned) { m.warned = true; api.log("no safe jump seen ahead"); }
    } else m.width = undefined;
  } else {
    // A ring within reach: tap only if falling on without it dies.
    const onRing = touches(PX, bottom);
    if (onRing && !lives(now, null, false, 60) && lives(now, null, true, 70)) { api.tap(" "); api.log("ring jump"); }
  }
}

// ---- The ship: aim for the middle of the next gap ----
if (ship && top >= 0) {
  const shipTop = top - 6, centre = shipTop + SIZE / 2;
  // its speed up or down, over the last tenth of a second, by the clock
  m.py = [];
  m.sy.push([api.t, centre]); while (m.sy.length > 2 && api.t - m.sy[0][0] > .04) m.sy.shift();
  const span = m.sy.length > 1 ? api.t - m.sy[0][0] : 0;
  const vy = span > .015 ? (centre - m.sy[0][1]) / span : 0;
  // Pillars come in pairs at one x: one down from the ceiling, one up from the ground.
  const cols = [];
  for (const b of blue) {
    let c = cols.find((q) => Math.abs(q.x - b.x) < 6);
    if (!c) cols.push(c = { x: b.x, r: b.x + b.w, gapTop: CEIL, gapBottom: GROUND });
    if (b.y <= CEIL + 3) c.gapTop = Math.max(c.gapTop, b.y + b.h);
    if (b.y + b.h >= GROUND - 3) c.gapBottom = Math.min(c.gapBottom, b.y);
  }
  const ahead = cols.filter((c) => c.r > PX - 4).sort((a, b) => a.x - b.x);
  const mid = (c) => (c.gapTop + c.gapBottom) / 2;
  let target = ahead.length ? mid(ahead[0]) : 140;
  // Inside a gap: head for the next one already, but never out of this one.
  if (ahead.length && ahead[0].x < PX + SIZE) {
    const lo = ahead[0].gapTop + SIZE / 2 + 5, hi = ahead[0].gapBottom - SIZE / 2 - 5;
    target = Math.max(lo, Math.min(hi, ahead[1] ? mid(ahead[1]) : target));
  }
  if (m.debug) api.log("ship", Math.round(centre), Math.round(vy), Math.round(target), ahead.map((c) => `${c.x}:${c.gapTop}-${c.gapBottom}`).join(" "));
  // hold to climb when it will be below the target a moment from now
  if (centre + vy * .12 > target) api.down(PX, 150); else api.up(PX, 150);
} else api.up(PX, 150);
