// A reflex for Flash Dash, as the agent wrote it: run every frame by
// play_realtime. It reads only the screen: spikes (red) along the ground,
// bars (violet) hanging low, and the square (yellow). It works out how
// fast the level moves from how far things moved since the last frame.
// Then, for the next cluster of hazards (things less than 40 pixels
// apart, cleared with one jump), it finds the coming frames a jump could
// start on and land past the cluster, and jumps in the middle of them, so
// a frame early or late is still safe.
const GROUND = 240, SIZE = 30, PX = 150, GRAVITY = 2400, JUMP = 640;
const SPIKE = 30, BAR = GROUND - SIZE - 45, FRAME = 1 / 60, SUB = 4, STEP = FRAME / SUB;
const m = api.memory;

// What is on screen, by where it is from the square's left edge.
const spikes = [];
for (const [a, b] of api.runs("#ff3b5c", GROUND - 4, PX - 40, api.width, 40)) {
  // Triangles cut four pixels above their base: each about 26 wide.
  const n = Math.max(1, Math.round((b - a + 5) / SPIKE));
  for (let k = 0; k < n; k++) spikes.push(a - 2 + k * SPIKE - PX);
}
const bars = api.runs("#7c5cff", BAR - 3, 0, api.width, 40).map(([a, b]) => [a - PX, b + 1 - PX]);

// How fast: the middle of what the nearest spike moved, frame to frame.
m.seen = m.seen || [];
const mark = spikes.length ? spikes[0] : null;
if (m.mark != null && mark != null && api.t > m.t) {
  const v = (m.mark - mark) / (api.t - m.t);
  if (v > 200 && v < 700) { m.seen.push(v); if (m.seen.length > 15) m.seen.shift(); }
}
m.mark = mark; m.t = api.t;
const sorted = [...m.seen].sort((a, b) => a - b);
const speed = sorted.length ? sorted[sorted.length >> 1] : 360;

// On the ground: the yellow starts just where a square standing there would.
const top = api.findDown("#ffd23f", PX + 5, 0, GROUND, 30);
const grounded = top >= GROUND - SIZE - 1;

function hits(x, y) {
  const left = x, right = x + SIZE, bottom = GROUND + y;
  for (const s of spikes) if (right > s + 8 && left < s + SPIKE - 8 && bottom > GROUND - SPIKE + 10) return true;
  for (const [a, b] of bars) if (right > a && left < b && bottom - SIZE < BAR) return true;
  return false;
}

// The next cluster: from the nearest thing still ahead of the square's
// back, on while the next starts less than 40 pixels after.
const things = [...spikes.map((s) => [s, s + SPIKE]), ...bars].filter(([, b]) => b > 0).sort((p, q) => p[0] - q[0]);
let end = null;
if (things.length) {
  end = things[0][1];
  for (const [a, b] of things.slice(1)) { if (a - end < 40) end = Math.max(end, b); else break; }
}

// A jump starting with the square's left edge at X: lands clear of it all,
// and past the cluster?
function clears(x) {
  let y = 0, vy = -JUMP;
  for (;;) {
    x += speed * STEP; vy += GRAVITY * STEP; y += vy * STEP;
    if (y >= 0) return !hits(x, 0) && x > end;
    if (hits(x, y)) return false;
  }
}

if (grounded && end !== null) {
  let x = 0, first = -1, last = -1;
  for (let k = 0; k < 60; k++) {
    for (let i = 0; i < SUB; i++) x += speed * STEP;
    if (hits(x, 0)) break;
    const ok = clears(x);
    if (ok && first < 0) first = k;
    if (!ok && first >= 0) { last = k - 1; break; }
  }
  if (first >= 0 && last < 0) last = first;
  // Seen whole while it is ahead, the window's width is kept; once it
  // starts now, jump when half of it is left.
  // Landing with the window already open: it is measured from here.
  if (first > 0 || (first === 0 && m.width === undefined)) m.width = last - first;
  if (first === 0 && last <= Math.floor(m.width / 2)) {
    api.tap(" ");
    api.log("jump at", speed.toFixed(0), "px/s, window", m.width + 1, "frames");
    m.width = undefined;
  }
}
