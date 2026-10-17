// Put into a design while the web UI's side panel shows it (the server
// adds it to the page it sends there, never to the file). The panel
// talks to it by postMessage: it picks the part of the page a comment
// is about, lets the words be edited in place, and sets the page's
// custom properties as the sliders move. It changes nothing on its own.
(() => {
  const PARENT = window.parent;
  if (PARENT === window) return;
  const say = (message) => PARENT.postMessage({ flashDesign: true, ...message }, "*");
  let mode = "view";
  let hovered = null;
  const edits = new Map();   // element -> the words it had at first

  const style = document.createElement("style");
  style.id = "flash-design-style";
  style.textContent = `
    [data-flash-hover] { outline: 2px solid #d97757 !important; outline-offset: 2px !important; cursor: pointer !important; }
    [data-flash-noted] { outline: 2px dashed #d97757 !important; outline-offset: 2px !important; }
    [data-flash-editable]:hover { outline: 1px dashed rgba(217, 119, 87, 0.7) !important; outline-offset: 2px !important; cursor: text !important; }
    [contenteditable="true"] { outline: 2px solid #d97757 !important; outline-offset: 2px !important; }
  `;
  document.head.appendChild(style);

  // Where an element is, as a path a comment can name and find again.
  function pathOf(el) {
    const parts = [];
    for (let node = el; node && node !== document.body && node.parentElement; node = node.parentElement) {
      const same = [...node.parentElement.children].filter((c) => c.tagName === node.tagName);
      const tag = node.tagName.toLowerCase();
      parts.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(node) + 1})` : tag);
      if (node.id) { parts[0] = `${tag}#${CSS.escape(node.id)}`; break; }
    }
    return parts.join(" > ");
  }

  function labelOf(el) {
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.getAttribute("alt") || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
    const names = { h1: "Heading", h2: "Heading", h3: "Heading", h4: "Heading", p: "Paragraph", a: "Link", button: "Button", img: "Image", nav: "Navigation", header: "Header", footer: "Footer", section: "Section", li: "List item", input: "Field", form: "Form", ul: "List", ol: "List" };
    return { kind: names[tag] || tag, text: text.slice(0, 120) };
  }

  // Words alone, with no element inside: the part that can be edited in
  // place and written back to the file exactly.
  const wordsOnly = (el) => el.children.length === 0 && (el.textContent || "").trim().length > 0 && !["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT"].includes(el.tagName);

  function setHover(el) {
    if (hovered) hovered.removeAttribute("data-flash-hover");
    hovered = el;
    if (el) el.setAttribute("data-flash-hover", "");
  }

  function setMode(next) {
    mode = next;
    setHover(null);
    document.querySelectorAll("[data-flash-editable]").forEach((el) => el.removeAttribute("data-flash-editable"));
    if (mode === "edit") document.querySelectorAll("body *").forEach((el) => { if (wordsOnly(el)) el.setAttribute("data-flash-editable", ""); });
    document.querySelectorAll('[contenteditable="true"]').forEach(finishEdit);
  }

  function finishEdit(el) {
    el.removeAttribute("contenteditable");
    const before = edits.get(el);
    const now = el.textContent.trim();
    if (before !== undefined && before !== now) say({ type: "edit", path: pathOf(el), old: before, new: now, count: [...edits].filter(([e, was]) => e.textContent.trim() !== was).length });
  }

  document.addEventListener("mouseover", (e) => {
    if (mode !== "comment") return;
    const el = e.target.closest("body *");
    if (el && el !== hovered) setHover(el);
  }, true);
  document.addEventListener("mouseleave", () => setHover(null));

  document.addEventListener("click", (e) => {
    if (mode === "view") return;
    const el = e.target.closest("body *");
    if (!el) return;
    if (mode === "comment") {
      e.preventDefault();
      e.stopPropagation();
      const r = el.getBoundingClientRect();
      say({ type: "pick", path: pathOf(el), ...labelOf(el), rect: { x: r.left, y: r.top, w: r.width, h: r.height } });
      return;
    }
    if (mode === "edit" && el.hasAttribute("data-flash-editable")) {
      e.preventDefault();
      e.stopPropagation();
      if (!edits.has(el)) edits.set(el, el.textContent.trim());
      el.setAttribute("contenteditable", "true");
      el.focus();
    }
  }, true);

  document.addEventListener("focusout", (e) => { if (e.target.getAttribute && e.target.getAttribute("contenteditable") === "true") finishEdit(e.target); }, true);
  document.addEventListener("keydown", (e) => {
    const el = e.target;
    if (el.getAttribute && el.getAttribute("contenteditable") === "true" && (e.key === "Escape" || (e.key === "Enter" && !e.shiftKey))) {
      e.preventDefault();
      el.blur();
    }
  }, true);

  // The values a design offers to tune: its list in #tweaks, or failing
  // that every custom property set on :root.
  function tweaks() {
    const root = getComputedStyle(document.documentElement);
    let listed = [];
    try { listed = JSON.parse((document.getElementById("tweaks") || {}).textContent || "[]"); } catch (_) { listed = []; }
    if (!Array.isArray(listed) || !listed.length) {
      const names = new Set();
      for (const sheet of document.styleSheets) {
        let rules;
        try { rules = sheet.cssRules; } catch (_) { continue; }
        for (const rule of rules) {
          if (!rule.style || !/^(:root|html)$/.test((rule.selectorText || "").trim())) continue;
          for (const name of rule.style) if (name.startsWith("--")) names.add(name);
        }
      }
      listed = [...names].map((name) => ({ var: name }));
    }
    return listed.filter((t) => t && typeof t.var === "string" && t.var.startsWith("--"))
      .map((t) => ({ ...t, value: root.getPropertyValue(t.var).trim() }));
  }

  window.addEventListener("message", (e) => {
    if (e.source !== PARENT || !e.data || !e.data.flashDesign) return;
    const m = e.data;
    if (m.type === "mode") setMode(m.mode);
    if (m.type === "vars") for (const [name, value] of Object.entries(m.vars || {})) document.documentElement.style.setProperty(name, value);
    if (m.type === "mark") {
      document.querySelectorAll("[data-flash-noted]").forEach((el) => el.removeAttribute("data-flash-noted"));
      for (const path of m.paths || []) { try { const el = document.querySelector(path); if (el) el.setAttribute("data-flash-noted", ""); } catch (_) { /* a path from an older version */ } }
    }
  });

  const ready = () => say({ type: "ready", tweaks: tweaks(), slides: document.querySelectorAll("section.slide").length, title: document.title });
  if (document.readyState === "complete") ready(); else window.addEventListener("load", ready);
})();
