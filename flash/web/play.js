// The reflex a model writes for a real-time game, run in the page every
// frame (browser.play). Called with [policy, seconds, until, canvas,
// start keys]; says how the run went.
(async ([policySrc, seconds, untilSrc, canvasSel, startKeys]) => {
  const canvas = canvasSel ? document.querySelector(canvasSel) : document.querySelector("canvas");
  const c2d = canvas ? canvas.getContext("2d", { willReadFrequently: true }) : null;
  const logged = [];
  let actions = 0;
  const target = () => document.activeElement && document.activeElement !== document.body ? document.activeElement : (canvas || document.body);
  const codeOf = (k) => k === " " ? "Space" : k.length === 1 ? (/[a-z]/i.test(k) ? "Key" + k.toUpperCase() : /\d/.test(k) ? "Digit" + k : k) : k;
  const key = (k, type) => {
    const ev = new KeyboardEvent(type, { key: k, code: codeOf(k), bubbles: true, cancelable: true });
    target().dispatchEvent(ev);
  };
  const box = () => canvas ? canvas.getBoundingClientRect() : { left: 0, top: 0, width: innerWidth, height: innerHeight };
  const mouse = (type, x, y) => {
    const b = box(), sx = canvas ? b.width / canvas.width : 1, sy = canvas ? b.height / canvas.height : 1;
    const ev = new MouseEvent(type, { clientX: b.left + x * sx, clientY: b.top + y * sy, bubbles: true, cancelable: true, button: 0 });
    (canvas || document.elementFromPoint(b.left + x * sx, b.top + y * sy) || document.body).dispatchEvent(ev);
  };
  const near = (a, b, tol) => Math.abs(a[0] - b[0]) <= tol && Math.abs(a[1] - b[1]) <= tol && Math.abs(a[2] - b[2]) <= tol;
  const hex = (c) => typeof c === "string" ? [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)) : c;
  const api = {
    width: canvas ? canvas.width : innerWidth, height: canvas ? canvas.height : innerHeight,
    memory: {}, frame: 0, t: 0,
    px: (x, y) => { if (!c2d) return [0, 0, 0]; const d = c2d.getImageData(Math.round(x), Math.round(y), 1, 1).data; return [d[0], d[1], d[2]]; },
    // The first x from X0 to X1 on row Y whose colour is within TOL of COLOUR; -1 if none.
    find: (colour, y, x0, x1, tol = 40) => {
      if (!c2d) return -1;
      const want = hex(colour), lo = Math.max(0, Math.round(Math.min(x0, x1))), hi = Math.min(api.width, Math.round(Math.max(x0, x1)));
      if (hi <= lo) return -1;
      const d = c2d.getImageData(lo, Math.round(y), hi - lo, 1).data;
      for (let i = 0; i < hi - lo; i++) if (near([d[i * 4], d[i * 4 + 1], d[i * 4 + 2]], want, tol)) return lo + i;
      return -1;
    },
    // Every run of COLOUR on row Y from X0 to X1, as [first x, last x].
    runs: (colour, y, x0, x1, tol = 40) => {
      if (!c2d) return [];
      const want = hex(colour), lo = Math.max(0, Math.round(Math.min(x0, x1))), hi = Math.min(api.width, Math.round(Math.max(x0, x1)));
      if (hi <= lo) return [];
      const d = c2d.getImageData(lo, Math.round(y), hi - lo, 1).data;
      const out = [];
      let from = -1;
      for (let i = 0; i <= hi - lo; i++) {
        const on = i < hi - lo && near([d[i * 4], d[i * 4 + 1], d[i * 4 + 2]], want, tol);
        if (on && from < 0) from = i;
        if (!on && from >= 0) { out.push([lo + from, lo + i - 1]); from = -1; }
      }
      return out;
    },
    // The first y from Y0 to Y1 down column X whose colour is within TOL.
    findDown: (colour, x, y0, y1, tol = 40) => {
      if (!c2d) return -1;
      const want = hex(colour), lo = Math.max(0, Math.round(y0)), hi = Math.min(api.height, Math.round(y1));
      if (hi <= lo) return -1;
      const d = c2d.getImageData(Math.round(x), lo, 1, hi - lo).data;
      for (let i = 0; i < hi - lo; i++) if (near([d[i * 4], d[i * 4 + 1], d[i * 4 + 2]], want, tol)) return lo + i;
      return -1;
    },
    press: (k) => { key(k, "keydown"); actions++; },
    release: (k) => key(k, "keyup"),
    tap: (k) => { key(k, "keydown"); key(k, "keyup"); actions++; },
    click: (x, y) => { mouse("mousedown", x, y); mouse("mouseup", x, y); mouse("click", x, y); actions++; },
    move: (x, y) => mouse("mousemove", x, y),
    log: (...said) => { logged.push(`${api.frame}: ${said.join(" ")}`); if (logged.length > 40) logged.shift(); },
  };
  for (const k of startKeys) api.tap(k);
  actions = 0;
  let policy, until;
  try {
    policy = new Function("api", policySrc);
    until = untilSrc ? new Function("api", "return (" + untilSrc + ");") : null;
  } catch (error) { return { error: "The policy does not parse: " + error.message }; }
  const start = performance.now();
  let stopped = "", error = "";
  await new Promise((done) => {
    const tick = () => {
      api.frame++;
      api.t = (performance.now() - start) / 1000;
      try { policy(api); } catch (e) { error = String(e && e.stack || e).split("\n")[0]; return done(); }
      if (until) { let u; try { u = until(api); } catch (e) { u = "until threw: " + e; } if (u) { stopped = String(u === true ? "until was true" : u); return done(); } }
      if (api.t >= seconds) return done();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  return { frames: api.frame, seconds: +api.t.toFixed(2), actions, stopped, error, title: document.title, log: logged, canvas: !!canvas };
})
