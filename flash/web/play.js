// The reflex a model writes for a real-time game, run in the page every
// frame (browser.play). Called with [policy, seconds, until, canvas,
// start keys, record, attempts, died, restart keys, progress]; says how
// the run went, and with record, hands back a WebM of the canvas (of the
// best attempt) as a data URL so the user can watch it.
//
// With attempts, it plays like a person beating a hard level: when DIED
// is true it presses the restart keys and goes again, the policy's
// api.memory kept from try to try so it can learn where it died, until
// UNTIL is true or the attempts run out.
(async ([policySrc, seconds, untilSrc, canvasSel, startKeys, record, attempts, diedSrc, restartKeys, progressSrc]) => {
  attempts = Math.max(1, Math.min(50, +attempts || 1));
  const canvas = canvasSel ? document.querySelector(canvasSel) : document.querySelector("canvas");
  // Pixels: a 2D canvas is read as it is; a WebGL one (most game engines)
  // is copied into a 2D one once a frame, in the same frame it was drawn,
  // before the browser clears it.
  let own2d = null;
  try { own2d = canvas ? canvas.getContext("2d", { willReadFrequently: true }) : null; } catch { own2d = null; }
  const copy = canvas && !own2d ? Object.assign(document.createElement("canvas"), { width: canvas.width, height: canvas.height }) : null;
  const copy2d = copy ? copy.getContext("2d", { willReadFrequently: true }) : null;
  let copiedAt = -1;
  const c2d = () => {
    if (own2d) return own2d;
    if (!copy2d) return null;
    if (copiedAt !== api.frame) {
      if (copy.width !== canvas.width || copy.height !== canvas.height) { copy.width = canvas.width; copy.height = canvas.height; }
      try { copy2d.drawImage(canvas, 0, 0); } catch { return null; }
      copiedAt = api.frame;
    }
    return copy2d;
  };
  const logged = [];
  let actions = 0;
  const target = () => document.activeElement && document.activeElement !== document.body ? document.activeElement : (canvas || document.body);
  const codeOf = (k) => k === " " ? "Space" : k.length === 1 ? (/[a-z]/i.test(k) ? "Key" + k.toUpperCase() : /\d/.test(k) ? "Digit" + k : k) : k;
  const key = (k, type) => {
    const ev = new KeyboardEvent(type, { key: k, code: codeOf(k), bubbles: true, cancelable: true });
    target().dispatchEvent(ev);
  };
  const box = () => canvas ? canvas.getBoundingClientRect() : { left: 0, top: 0, width: innerWidth, height: innerHeight };
  const at = (x, y) => { const b = box(), sx = canvas ? b.width / canvas.width : 1, sy = canvas ? b.height / canvas.height : 1; return [b.left + x * sx, b.top + y * sy]; };
  // A mouse event, and the pointer and touch-free pointer event engines
  // listen for instead.
  const mouse = (type, x, y) => {
    const [cx, cy] = at(x, y);
    const into = canvas || document.elementFromPoint(cx, cy) || document.body;
    const pointer = { mousedown: "pointerdown", mouseup: "pointerup", mousemove: "pointermove" }[type];
    if (pointer) into.dispatchEvent(new PointerEvent(pointer, { clientX: cx, clientY: cy, bubbles: true, cancelable: true, button: 0, buttons: type === "mouseup" ? 0 : 1, pointerId: 1, pointerType: "mouse", isPrimary: true }));
    into.dispatchEvent(new MouseEvent(type, { clientX: cx, clientY: cy, bubbles: true, cancelable: true, button: 0, buttons: type === "mouseup" ? 0 : 1 }));
  };
  const near = (a, b, tol) => Math.abs(a[0] - b[0]) <= tol && Math.abs(a[1] - b[1]) <= tol && Math.abs(a[2] - b[2]) <= tol;
  const hex = (c) => typeof c === "string" ? [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)) : c;
  const clampX = (x) => Math.max(0, Math.min(api.width, Math.round(x)));
  const clampY = (y) => Math.max(0, Math.min(api.height, Math.round(y)));
  const rowOf = (y, x0, x1) => {
    const g = c2d(); if (!g) return null;
    const lo = clampX(Math.min(x0, x1)), hi = clampX(Math.max(x0, x1));
    if (hi <= lo || y < 0 || y >= api.height) return null;
    return { lo, hi, d: g.getImageData(lo, Math.round(y), hi - lo, 1).data };
  };
  let lastBand = null, speeds = [];
  const timers = [];
  let mouseHeld = false;
  const api = {
    width: canvas ? canvas.width : innerWidth, height: canvas ? canvas.height : innerHeight,
    memory: {}, frame: 0, t: 0, dt: 0, attempt: 1, deaths: [],
    px: (x, y) => { const g = c2d(); if (!g) return [0, 0, 0]; const d = g.getImageData(clampX(x), clampY(y), 1, 1).data; return [d[0], d[1], d[2]]; },
    // The first x from X0 to X1 on row Y whose colour is within TOL of COLOUR; -1 if none.
    find: (colour, y, x0, x1, tol = 40) => {
      const r = rowOf(y, x0, x1); if (!r) return -1;
      const want = hex(colour);
      for (let i = 0; i < r.hi - r.lo; i++) if (near([r.d[i * 4], r.d[i * 4 + 1], r.d[i * 4 + 2]], want, tol)) return r.lo + i;
      return -1;
    },
    // Every run of COLOUR on row Y from X0 to X1, as [first x, last x].
    runs: (colour, y, x0, x1, tol = 40) => {
      const r = rowOf(y, x0, x1); if (!r) return [];
      const want = hex(colour), out = [];
      let from = -1;
      for (let i = 0; i <= r.hi - r.lo; i++) {
        const on = i < r.hi - r.lo && near([r.d[i * 4], r.d[i * 4 + 1], r.d[i * 4 + 2]], want, tol);
        if (on && from < 0) from = i;
        if (!on && from >= 0) { out.push([r.lo + from, r.lo + i - 1]); from = -1; }
      }
      return out;
    },
    // The first y from Y0 to Y1 down column X whose colour is within TOL.
    findDown: (colour, x, y0, y1, tol = 40) => {
      const g = c2d(); if (!g) return -1;
      const want = hex(colour), lo = clampY(y0), hi = clampY(y1);
      if (hi <= lo) return -1;
      const d = g.getImageData(clampX(x), lo, 1, hi - lo).data;
      for (let i = 0; i < hi - lo; i++) if (near([d[i * 4], d[i * 4 + 1], d[i * 4 + 2]], want, tol)) return lo + i;
      return -1;
    },
    // Every patch of COLOUR in the box X0,Y0 to X1,Y1, as {x, y, w, h, cx,
    // cy, n}: its bounding box, centre and size in pixels. Looked at every
    // STEP pixels (2 by default), so it is quick enough for every frame.
    blobs: (colour, x0 = 0, y0 = 0, x1 = api.width, y1 = api.height, { tol = 40, step = 2, min = 2 } = {}) => {
      const g = c2d(); if (!g) return [];
      const lo = clampX(Math.min(x0, x1)), hi = clampX(Math.max(x0, x1)), top = clampY(Math.min(y0, y1)), bot = clampY(Math.max(y0, y1));
      if (hi <= lo || bot <= top) return [];
      const d = g.getImageData(lo, top, hi - lo, bot - top).data, w = hi - lo;
      const want = hex(colour), cols = Math.ceil(w / step), rows = Math.ceil((bot - top) / step);
      const hit = new Uint8Array(cols * rows), seen = new Uint8Array(cols * rows);
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const i = ((r * step) * w + c * step) * 4;
        if (near([d[i], d[i + 1], d[i + 2]], want, tol)) hit[r * cols + c] = 1;
      }
      const out = [];
      for (let s = 0; s < hit.length; s++) {
        if (!hit[s] || seen[s]) continue;
        let minc = 1e9, maxc = -1, minr = 1e9, maxr = -1, n = 0;
        const stack = [s]; seen[s] = 1;
        while (stack.length) {
          const q = stack.pop(), r = (q / cols) | 0, c = q % cols;
          n++; if (c < minc) minc = c; if (c > maxc) maxc = c; if (r < minr) minr = r; if (r > maxr) maxr = r;
          for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
            const rr = r + dr, cc = c + dc;
            if (rr < 0 || cc < 0 || rr >= rows || cc >= cols) continue;
            const k = rr * cols + cc;
            if (hit[k] && !seen[k]) { seen[k] = 1; stack.push(k); }
          }
        }
        if (n < min) continue;
        const x = lo + minc * step, y = top + minr * step, bw = (maxc - minc + 1) * step, bh = (maxr - minr + 1) * step;
        out.push({ x, y, w: bw, h: bh, cx: x + bw / 2, cy: y + bh / 2, n: n * step * step });
      }
      return out.sort((a, b) => a.x - b.x);
    },
    // How fast the picture scrolls sideways, in pixels a second (positive:
    // the world moves left, as when a runner runs right), measured from how
    // far a band of rows from Y0 to Y1 moved since the last frame. The
    // middle of the last few, so one odd frame does not throw it.
    speed: (y0 = api.height * .6, y1 = api.height * .85) => {
      const g = c2d(); if (!g) return 0;
      const top = clampY(Math.min(y0, y1)), h = Math.max(1, clampY(Math.max(y0, y1)) - top);
      const d = g.getImageData(0, top, api.width, h).data, W = api.width;
      const col = new Float32Array(W);
      for (let x = 0; x < W; x++) { let s = 0; for (let r = 0; r < h; r++) { const i = (r * W + x) * 4; s += d[i] * .3 + d[i + 1] * .59 + d[i + 2] * .11; } col[x] = s / h; }
      if (lastBand && api.dt > 0) {
        let best = 0, bestErr = Infinity, texture = 0;
        for (let x = 1; x < W; x++) texture += Math.abs(col[x] - col[x - 1]);
        for (let shift = 0; shift <= 40; shift++) {
          let err = 0, n = 0;
          for (let x = 0; x + shift < W; x += 2) { err += Math.abs(lastBand[x + shift] - col[x]); n++; }
          err /= n; if (err < bestErr) { bestErr = err; best = shift; }
        }
        if (texture > W * .5 && best > 0) { speeds.push(best / api.dt); if (speeds.length > 9) speeds.shift(); }
      }
      lastBand = col;
      const sorted = [...speeds].sort((a, b) => a - b);
      return sorted.length ? sorted[sorted.length >> 1] : 0;
    },
    press: (k) => { key(k, "keydown"); actions++; },
    release: (k) => key(k, "keyup"),
    tap: (k) => { key(k, "keydown"); key(k, "keyup"); actions++; },
    click: (x, y) => { mouse("mousedown", x, y); mouse("mouseup", x, y); mouse("click", x, y); actions++; },
    move: (x, y) => mouse("mousemove", x, y),
    // Hold the mouse (or a finger) down at X,Y until up(): a ship, a jetpack.
    down: (x = api.width / 2, y = api.height / 2) => { if (!mouseHeld) { mouse("mousedown", x, y); mouseHeld = true; actions++; } },
    up: (x = api.width / 2, y = api.height / 2) => { if (mouseHeld) { mouse("mouseup", x, y); mouseHeld = false; } },
    held: () => mouseHeld,
    // Do FN in MS milliseconds, between frames, for timing finer than one.
    later: (ms, fn) => { timers.push(setTimeout(() => { try { fn(api); } catch (e) { api.log("later threw: " + e); } }, Math.max(0, +ms || 0))); },
    log: (...said) => { logged.push(`${api.attempt > 1 ? `#${api.attempt} ` : ""}${api.frame}: ${said.join(" ")}`); if (logged.length > 60) logged.shift(); },
  };
  let policy, until, died, progress;
  try {
    policy = new Function("api", policySrc);
    until = untilSrc ? new Function("api", "return (" + untilSrc + ");") : null;
    died = diedSrc ? new Function("api", "return (" + diedSrc + ");") : null;
    progress = progressSrc ? new Function("api", "return (" + progressSrc + ");") : null;
  } catch (error) { return { error: "The policy does not parse: " + error.message }; }

  const types = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  const canRecord = record && canvas && canvas.captureStream && window.MediaRecorder;
  const startRecording = () => {
    if (!canRecord) return null;
    try {
      const type = types.find((t) => MediaRecorder.isTypeSupported(t)) || "";
      const chunks = [], recorder = new MediaRecorder(canvas.captureStream(30), type ? { mimeType: type } : {});
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.start(1000);
      return { recorder, chunks };
    } catch { return null; }
  };
  const stopRecording = async (rec) => {
    if (!rec) return null;
    try { await new Promise((done) => { rec.recorder.onstop = done; rec.recorder.stop(); }); return new Blob(rec.chunks, { type: "video/webm" }); } catch { return null; }
  };
  const progressOf = () => { if (!progress) return null; try { const p = Number(progress(api)); return isFinite(p) ? p : null; } catch { return null; } };

  for (const k of startKeys) api.tap(k);
  actions = 0;
  const begun = performance.now();
  const tries = [];
  let stopped = "", error = "", best = null, bestVideo = null;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    api.attempt = attempt;
    if (attempt > 1) {
      if (mouseHeld) api.up();
      for (const k of restartKeys) api.tap(k);
      await new Promise((r) => setTimeout(r, 250));
    }
    const rec = startRecording();
    const start = performance.now();
    let last = start, ended = "";
    lastBand = null; speeds = [];
    await new Promise((done) => {
      const tick = () => {
        const now = performance.now();
        api.frame++;
        api.dt = (now - last) / 1000; last = now;
        api.t = (now - start) / 1000;
        try { policy(api); } catch (e) { error = String(e && e.stack || e).split("\n")[0]; ended = "error"; return done(); }
        if (until) { let u; try { u = until(api); } catch (e) { u = "until threw: " + e; } if (u) { stopped = String(u === true ? "until was true" : u); ended = "won"; return done(); } }
        if (died) { let x; try { x = died(api); } catch { x = false; } if (x) { ended = "died"; return done(); } }
        if ((now - begun) / 1000 >= seconds) { ended = "time"; return done(); }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    for (const t of timers.splice(0)) clearTimeout(t);
    const reached = progressOf();
    const result = { attempt, seconds: +api.t.toFixed(2), ended, progress: reached };
    tries.push(result);
    if (ended === "died") api.deaths.push({ attempt, t: +api.t.toFixed(3), progress: reached });
    const video = await stopRecording(rec);
    const better = !best || ended === "won" || (reached ?? result.seconds) > (best.progress ?? best.seconds);
    if (better) { best = result; if (video) bestVideo = video; }
    if (ended !== "died") break;
  }
  if (mouseHeld) api.up();
  let video = "";
  if (record && bestVideo) {
    video = await new Promise((done) => { const r = new FileReader(); r.onload = () => done(String(r.result)); r.onerror = () => done(""); r.readAsDataURL(bestVideo); });
  }
  return { video, frames: api.frame, seconds: +((performance.now() - begun) / 1000).toFixed(2), actions, stopped, error, title: document.title,
    log: logged, canvas: !!canvas, webgl: !!(canvas && !own2d), attempts: tries, best };
})
