// A colour picker of its own in place of the system's: any <input type="color"> on the page opens it,
// so the code that reads the input's value and listens for input/change keeps working as it is.
// A square for shade, a strip for hue, a hex field and a row of colours to start from.
(() => {
  if (window.ColourPicker) return;
  const STARTERS = ["#e5484d", "#f76b15", "#f2b64c", "#62c073", "#2bb3a3", "#3fa9ff", "#6e7bf2", "#a66cf6", "#e5609b", "#ffffff", "#8f8c84", "#1d1d1b"];
  const css = `
  .cpick { position: fixed; z-index: 10000; width: 248px; padding: 12px; border-radius: 14px; background: var(--surface, #30302e);
    border: 1px solid var(--border, rgba(222,220,209,.13)); box-shadow: 0 16px 40px rgba(0,0,0,.4); color: var(--text, #f5f4ef);
    font: 13px var(--sans, ui-sans-serif, system-ui, sans-serif); user-select: none; -webkit-user-select: none; }
  .cpick[hidden] { display: none; }
  .cpick .sv { position: relative; height: 150px; border-radius: 9px; cursor: crosshair; touch-action: none;
    background: linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, transparent), var(--hue-c, red); }
  .cpick .hue { position: relative; height: 14px; margin: 12px 0; border-radius: 7px; cursor: pointer; touch-action: none;
    background: linear-gradient(to right, #f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00); }
  .cpick .knob { position: absolute; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%; border: 2px solid #fff;
    box-shadow: 0 0 0 1px rgba(0,0,0,.35), 0 1px 4px rgba(0,0,0,.4); pointer-events: none; }
  .cpick .hue .knob { top: 50%; background: var(--hue-c, red); }
  .cpick .row { display: flex; align-items: center; gap: 8px; }
  .cpick .now { width: 30px; height: 30px; flex: none; border-radius: 8px; border: 1px solid var(--border, rgba(222,220,209,.13)); }
  .cpick input.hex { flex: 1; min-width: 0; height: 30px; padding: 0 10px; border-radius: 8px; font: inherit; font-variant-numeric: tabular-nums;
    text-transform: uppercase; color: inherit; background: var(--bg, #262624); border: 1px solid var(--border, rgba(222,220,209,.13)); outline: none; }
  .cpick input.hex:focus { border-color: var(--accent, #d97757); }
  .cpick .starts { display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px; margin-top: 12px; }
  .cpick .starts button { height: 24px; padding: 0; border-radius: 6px; border: 1px solid var(--border, rgba(128,128,128,.3)); cursor: pointer; }
  .cpick .starts button:focus-visible, .cpick .done:focus-visible { outline: 2px solid var(--accent, #d97757); outline-offset: 2px; }
  .cpick .done { height: 30px; padding: 0 14px; border-radius: 8px; border: 0; cursor: pointer; font: inherit; font-weight: 500;
    background: var(--accent, #d97757); color: var(--accent-text, #fff); }`;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const box = document.createElement("div");
  box.className = "cpick";
  box.hidden = true;
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-label", "Pick a colour");
  box.innerHTML = `<div class="sv"><span class="knob"></span></div><div class="hue"><span class="knob"></span></div>
    <div class="row"><span class="now"></span><input class="hex" maxlength="7" spellcheck="false" aria-label="Hex code"><button type="button" class="done">Done</button></div>
    <div class="starts"></div>`;
  const [sv, hue] = [box.querySelector(".sv"), box.querySelector(".hue")];
  const [svKnob, hueKnob] = [sv.querySelector(".knob"), hue.querySelector(".knob")];
  const hex = box.querySelector(".hex"), now = box.querySelector(".now");
  for (const c of STARTERS) {
    const b = document.createElement("button");
    b.type = "button";
    b.style.background = c;
    b.setAttribute("aria-label", c);
    b.onclick = () => { set(fromHex(c)); };
    box.querySelector(".starts").appendChild(b);
  }
  const mount = () => { if (!box.isConnected) document.body.appendChild(box); };

  // h 0..360, s and v 0..1
  let hsv = { h: 0, s: 1, v: 1 }, input = null, was = "";
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  function toHex({ h, s, v }) {
    const f = (n) => { const k = (n + h / 60) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)); };
    return "#" + [f(5), f(3), f(1)].map((x) => Math.round(x * 255).toString(16).padStart(2, "0")).join("");
  }
  function fromHex(c) {
    const m = /^#?([0-9a-f]{6})$/i.exec(c || "") || /^#?([0-9a-f]{3})$/i.exec(c || "");
    if (!m) return null;
    const x = m[1].length === 3 ? m[1].replace(/./g, "$&$&") : m[1];
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(x.slice(i, i + 2), 16) / 255);
    const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
    let h = 0;
    if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return { h: (h * 60 + 360) % 360, s: max ? d / max : 0, v: max };
  }
  function draw(typing) {
    const c = toHex(hsv);
    box.style.setProperty("--hue-c", toHex({ h: hsv.h, s: 1, v: 1 }));
    svKnob.style.left = hsv.s * 100 + "%";
    svKnob.style.top = (1 - hsv.v) * 100 + "%";
    svKnob.style.background = c;
    hueKnob.style.left = (hsv.h / 360) * 100 + "%";
    now.style.background = c;
    if (!typing) hex.value = c;
    return c;
  }
  function set(next, typing) {
    if (!next) return;
    hsv = next;
    const c = draw(typing);
    if (input && input.value !== c) {
      input.value = c;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }
  function drag(el, onAt) {
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId);
      const at = (ev) => { const r = el.getBoundingClientRect(); onAt(clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1)); };
      at(e);
      el.onpointermove = at;
      el.onpointerup = el.onpointercancel = () => { el.onpointermove = null; };
    });
  }
  drag(sv, (x, y) => set({ ...hsv, s: x, v: 1 - y }));
  drag(hue, (x) => set({ ...hsv, h: Math.min(359.9, x * 360) }));
  hex.addEventListener("input", () => set(fromHex(hex.value.trim()), true));
  hex.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); close(true); } });
  box.querySelector(".done").onclick = () => close(true);

  function open(el) {
    mount();
    if (input && input !== el) close(true);
    input = el;
    was = el.value;
    hsv = fromHex(el.value) || { h: 20, s: .6, v: .85 };
    draw();
    box.hidden = false;
    const r = el.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight, gap = 8;
    let top = r.bottom + gap;
    if (top + h > innerHeight - 8 && r.top - gap - h > 8) top = r.top - gap - h;
    box.style.top = clamp(top, 8, Math.max(8, innerHeight - h - 8)) + "px";
    box.style.left = clamp(r.left, 8, Math.max(8, innerWidth - w - 8)) + "px";
    hex.focus({ preventScroll: true });
    hex.select();
  }
  // KEEP: false puts back the colour it had when opened (Escape).
  function close(keep) {
    if (!input) return;
    const el = input;
    input = null;
    box.hidden = true;
    if (!keep && el.value !== was) {
      el.value = was;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (el.value !== was) el.dispatchEvent(new Event("change", { bubbles: true }));
    if (el.isConnected) el.focus({ preventScroll: true });
  }
  const isColour = (t) => t instanceof HTMLInputElement && t.type === "color" && !t.disabled;
  // The system's picker never opens: a click (or a label's) on a colour input opens this one.
  document.addEventListener("click", (e) => {
    if (isColour(e.target)) { e.preventDefault(); open(e.target); return; }
    if (input && !box.contains(e.target)) close(true);
  }, true);
  document.addEventListener("keydown", (e) => {
    if (isColour(e.target) && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); open(e.target); }
    else if (input && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); }
  }, true);
  addEventListener("resize", () => close(true));
  window.ColourPicker = { open, close };
})();
