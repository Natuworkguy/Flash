// Tether: a reflex for Flash to start from (play_realtime runs it every frame).
// Flash is orange, its partner blue, tied by a rope. To catch a spark it goes
// to the far side of the spark from its partner, so the rope sweeps through
// it; it keeps the rope clear of drones by pulling in when one comes near.
// LEVEL "fair" looks again every fifth of a second, as a person reacts;
// "sharp" looks every frame and dashes.
const LEVEL = "fair";
const ROPE = 340;
const M = api.memory, T = document.title, sharp = LEVEL === "sharp";
const hold = (k, on) => { M.k = M.k || {}; if (on && !M.k[k]) { api.press(k); M.k[k] = 1; } if (!on && M.k[k]) { api.release(k); M.k[k] = 0; } };
const stop = () => ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].forEach(k => hold(k, false));
if (/lobby|over/.test(T)) { stop(); if (api.frame % 15 === 0) api.tap(" "); return; }
if (!/play/.test(T)) { stop(); return; }
if (!sharp && api.frame % 12 !== 0) return;   // a person's reaction time: keep the keys as they are until the next look
const solid = (b, r) => b.w > r && b.h > r && b.n > b.w * b.h * .45;
const biggest = c => api.blobs(c, 0, 0, api.width, api.height, { tol: 30 }).filter(b => solid(b, 20)).sort((a, b) => b.n - a.n)[0];
const me = biggest("#ff5a36"), mate = biggest("#2f6bff");
if (!me) { stop(); return; }
const sparks = api.blobs("#d7ff3a", 0, 0, api.width, api.height, { tol: 40 }).filter(b => b.w > 8);
const drones = api.blobs("#141210", 0, 0, api.width, api.height, { tol: 30 }).filter(b => solid(b, 18));
const clampX = x => Math.max(24, Math.min(api.width - 24, x)), clampY = y => Math.max(24, Math.min(api.height - 24, y));
const segDist = (px, py, ax, ay, bx, by) => { const dx = bx - ax, dy = by - ay, n = dx * dx + dy * dy, k = n ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / n)) : 0; return Math.hypot(px - ax - k * dx, py - ay - k * dy); };
const nearDrone = (x, y) => drones.reduce((m, d) => Math.min(m, Math.hypot(d.cx - x, d.cy - y)), 1e9);
// where to stand for each spark: past it, on the line from the partner, so the rope crosses it
let goal = null, best = 1e9;
for (const s of sparks) {
  let tx = s.cx, ty = s.cy, cost;
  if (mate) {
    const dx = s.cx - mate.cx, dy = s.cy - mate.cy, d = Math.hypot(dx, dy) || 1;
    if (d < ROPE - 40) { const reach = Math.min(ROPE - 30, d + 80); tx = clampX(mate.cx + dx / d * reach); ty = clampY(mate.cy + dy / d * reach); }
    cost = Math.hypot(tx - me.cx, ty - me.cy) + (d >= ROPE - 40 ? 150 : 0);
    // a rope that would pass a drone on the way is no good
    if (drones.some(q => segDist(q.cx, q.cy, mate.cx, mate.cy, tx, ty) < 40)) cost += 300;
  } else cost = Math.hypot(tx - me.cx, ty - me.cy);
  if (nearDrone(s.cx, s.cy) < 60) cost += 250;
  if (cost < best) { best = cost; goal = { x: tx, y: ty }; }
}
let gx = goal ? goal.x - me.cx : (mate ? mate.cx - me.cx : 0), gy = goal ? goal.y - me.cy : (mate ? mate.cy - me.cy : 0);
const gl = Math.hypot(gx, gy) || 1; gx /= gl; gy /= gl;
// keep clear of drones, and keep the rope clear: pull in towards the partner when one comes close to it
for (const d of drones) {
  const ax = me.cx - d.cx, ay = me.cy - d.cy, dist = Math.hypot(ax, ay) || 1;
  if (dist < 110) { const k = (110 - dist) / 110 * 2; gx += ax / dist * k; gy += ay / dist * k; }
  if (mate && segDist(d.cx, d.cy, mate.cx, mate.cy, me.cx, me.cy) < 55) {
    const mx = mate.cx - me.cx, my = mate.cy - me.cy, ml = Math.hypot(mx, my) || 1; gx += mx / ml * 1.6; gy += my / ml * 1.6;
  }
}
hold("ArrowRight", gx > .3); hold("ArrowLeft", gx < -.3); hold("ArrowDown", gy > .3); hold("ArrowUp", gy < -.3);
if (sharp && goal && best > 220 && nearDrone(me.cx, me.cy) > 150 && api.frame % 6 === 0) api.tap(" ");
