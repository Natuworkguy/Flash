// A small stand-in for the browser's document, enough for the page's
// card code to draw into and for tests to read back: elements with
// classes, data, attributes and children, simple HTML, and selectors
// made of tags, ids, classes and attributes, joined by spaces or ">".

class FakeClassList {
  constructor(node) { this.node = node; }
  get list() { return this.node.className.split(/\s+/).filter(Boolean); }
  contains(c) { return this.list.includes(c); }
  add(...cs) { const l = this.list; cs.forEach((c) => { if (!l.includes(c)) l.push(c); }); this.node.className = l.join(" "); }
  remove(...cs) { this.node.className = this.list.filter((c) => !cs.includes(c)).join(" "); }
  toggle(c, on) { const want = on === undefined ? !this.contains(c) : !!on; if (want) this.add(c); else this.remove(c); return want; }
}

const VOID = new Set(["input", "br", "img", "hr"]);

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag || "").toUpperCase();
    this.className = "";
    this.dataset = {};
    this.attrs = {};
    this.children = [];
    this.parentNode = null;
    this.text = "";
    this.style = { setProperty() {} };
    this.classList = new FakeClassList(this);
    this.hidden = false;
    this.disabled = false;
    this.value = "";
  }
  get id() { return this.attrs.id || ""; }
  get childElementCount() { return this.children.filter((c) => c.tagName !== "#TEXT").length; }
  get firstChild() { return this.children[0] || null; }
  get lastElementChild() { const e = this.children.filter((c) => c.tagName !== "#TEXT"); return e[e.length - 1] || null; }
  get parentElement() { return this.parentNode; }
  get textContent() { return this.tagName === "#TEXT" ? this.text : this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children.forEach((c) => { c.parentNode = null; }); this.children = []; if (v !== "" && v != null) this._add(textNode(String(v))); }
  set innerHTML(html) { this.textContent = ""; parseInto(this, String(html)); }
  setAttribute(k, v) { if (k === "class") this.className = String(v); else this.attrs[k] = String(v); }
  getAttribute(k) { return k === "class" ? this.className : (k in this.attrs ? this.attrs[k] : null); }
  _add(child) {
    if (child.tagName === "#FRAGMENT") { [...child.children].forEach((c) => this._add(c)); return child; }
    if (child.parentNode) child.remove();
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  appendChild(child) { return this._add(child); }
  append(...kids) { kids.forEach((k) => this._add(typeof k === "string" ? textNode(k) : k)); }
  prepend(...kids) { kids.reverse().forEach((k) => this.insertBefore(k, this.children[0] || null)); }
  insertBefore(child, ref) {
    if (!ref) return this._add(child);
    if (child.parentNode) child.remove();
    child.parentNode = this;
    this.children.splice(this.children.indexOf(ref), 0, child);
    return child;
  }
  before(node) { this.parentNode.insertBefore(node, this); }
  after(node) { const p = this.parentNode, i = p.children.indexOf(this); p.insertBefore(node, p.children[i + 1] || null); }
  remove() { if (!this.parentNode) return; const p = this.parentNode; p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; }
  replaceWith(node) { this.before(node); this.remove(); }
  focus() {}
  scrollIntoView() {}
  get elements() { return this.querySelectorAll("*"); }
  descendants() { const out = []; const walk = (n) => n.children.forEach((c) => { if (c.tagName !== "#TEXT") { out.push(c); walk(c); } }); walk(this); return out; }
  querySelectorAll(sel) { return this.descendants().filter((n) => matches(n, sel, this)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  matches(sel) { return matches(this, sel, null); }
  closest(sel) { let n = this; while (n && n.tagName !== "#DOC") { if (matches(n, sel, null)) return n; n = n.parentNode; } return null; }
  click() { if (this.onclick) return this.onclick({ preventDefault() {}, stopPropagation() {} }); return undefined; }
}

function textNode(text) { const n = new FakeNode("#text"); n.text = text; return n; }

// Tags and text only: enough for the page's own snippets.
function parseInto(root, html) {
  const stack = [root];
  const re = /<\/?([a-zA-Z][\w-]*)([^>]*)>|([^<]+)/g;
  let m;
  while ((m = re.exec(html))) {
    const top = stack[stack.length - 1];
    if (m[3] !== undefined) { top._add(textNode(m[3].replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'"))); continue; }
    if (m[0].startsWith("</")) { if (stack.length > 1) stack.pop(); continue; }
    const node = new FakeNode(m[1]);
    const attr = /([\w-]+)(?:="([^"]*)")?/g;
    let a;
    while ((a = attr.exec(m[2]))) {
      const [, k, v = ""] = a;
      if (k === "class") node.className = v;
      else if (k.startsWith("data-")) node.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v;
      else node.attrs[k] = v;
      if (k === "type") node.type = v;
    }
    top._add(node);
    if (!VOID.has(m[1].toLowerCase()) && !m[0].endsWith("/>")) stack.push(node);
  }
}

function attrOf(node, key) {
  if (key === "class") return node.className;
  if (key.startsWith("data-")) {
    const name = key.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase());
    return name in node.dataset ? String(node.dataset[name]) : null;
  }
  return node.getAttribute(key);
}

function compound(node, part) {
  if (part === "*") return true;
  const re = /([.#]?)([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g;
  let m, any = false;
  while ((m = re.exec(part))) {
    any = true;
    if (m[3]) { const v = attrOf(node, m[3]); if (v === null || (m[4] !== undefined && v !== m[4])) return false; continue; }
    if (m[1] === ".") { if (!node.classList.contains(m[2])) return false; }
    else if (m[1] === "#") { if (node.id !== m[2]) return false; }
    else if (node.tagName !== m[2].toUpperCase()) return false;
  }
  return any;
}

function matches(node, sel, scope) {
  return sel.split(",").some((one) => {
    const parts = one.trim().replace(/\s*>\s*/g, " > ").split(/\s+/);
    return chain(node, parts, scope);
  });
}

function chain(node, parts, scope) {
  const last = parts[parts.length - 1];
  if (!compound(node, last)) return false;
  let rest = parts.slice(0, -1);
  if (!rest.length) return true;
  const child = rest[rest.length - 1] === ">";
  if (child) rest = rest.slice(0, -1);
  const need = rest[rest.length - 1];
  const up = (n) => n.parentNode && n.parentNode.tagName !== "#DOC" ? n.parentNode : null;
  if (need === ":scope") return child ? node.parentNode === scope : !!scope;
  if (child) { const p = up(node); return !!p && chain(p, rest, scope); }
  for (let p = up(node); p; p = up(p)) if (chain(p, rest, scope)) return true;
  return false;
}

const document = new FakeNode("#doc");
document.createElement = (tag) => new FakeNode(tag);
document.createDocumentFragment = () => new FakeNode("#fragment");
document.body = document.appendChild(new FakeNode("body"));
const $ = (sel) => document.querySelector(sel);
const CSS = { escape: (s) => String(s) };
const Node = { DOCUMENT_FRAGMENT_NODE: 11 };
const navigator = { clipboard: { writeText: async () => {} } };

// What the page's helpers do, as far as the cards need them.
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function el(tag, cls, html) { const node = document.createElement(tag); if (cls) node.className = cls; if (html !== undefined) node.innerHTML = html; return node; }
const icon = (name) => `<svg data-icon="${name}"></svg>`;
const said = { commands: [], toasts: [], failures: [] };
async function command(name, arg, _data, body) { said.commands.push({ name, arg, body: body || {} }); return said.answer ? said.answer(name, arg, body || {}) : {}; }
const toast = (t) => said.toasts.push(t);
const showFailure = (e) => said.failures.push(String(e && e.message || e));
const tcClock = (at) => `@${at}`;
const tcFetch = () => {};
const tcJump = (id) => said.commands.push({ name: "jump", arg: id });
const tcCloseThread = () => { teamChat.thread = 0; };
const sparkList = [{ id: "s1", name: "Scout", colour: "#5ab0f2" }, { id: "s2", name: "Writer", colour: "#7bd88f" }];
const tcSpark = (id) => sparkList.find((s) => s.id === id) || null;
const sparkAvatar = (spark) => el("span", "avatar", esc(spark.name[0]));
const teamChat = { id: "desk", thread: 0, byId: new Map(), typing: [], work: {}, asks: {} };
const texts = (node, sel) => node.querySelectorAll(sel).map((n) => n.textContent);
