"""Headless browser screenshots and page control for Flash CLI.

Playwright drives a real Chromium so the model can look at a page it
built instead of guessing from the source. The import is deferred to the
moment a browser is asked for, because Playwright is slow to import and
Flash starts fine without it; a missing install turns into a tool result
the model can read and relay rather than a crash at startup.

`capture` is the one-shot photograph. The session half of this module is
for the pages a picture cannot answer questions about: it keeps one
Chromium open across tool calls so the model can click a button, fill a
field, run JavaScript against the live DOM, and look again at what its
last action actually did.
"""

import atexit
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

PAGE_EXTENSIONS = {".html", ".htm", ".xhtml", ".svg"}

INSTALL_HINT_END = "Install it, then re-run this tool"

PACKAGE_HINT = (
    "Playwright is not installed. It can be installed with "
    "`pip install playwright` followed by `playwright install chromium`."
    + INSTALL_HINT_END
)
BROWSER_HINT = (
    "Playwright is installed but its Chromium is not. You can "
    "download it with `playwright install chromium`."
    + INSTALL_HINT_END
)

# A page that never stops loading should not hang the turn.
NAVIGATION_TIMEOUT_MS = 20000


def resolve_target(
    target: str,
) -> tuple[Optional[str], str]:
    """Turn `target` into a URL a browser can open.

    Accepts an http(s) or file URL as given, and turns a local path to a
    page into a `file://` URL. Returns `(url, "")` or `(None, reason)`.
    """

    target = target.strip()

    if not target:
        return None, "No page given to screenshot."

    scheme = urlparse(target).scheme.lower()

    if scheme in {"http", "https", "file"}:
        return target, ""

    # A bare Windows path starts with a one-letter drive scheme, so only a
    # longer scheme is a real URL Flash has to turn down.
    if len(scheme) > 1:
        return None, (
            f"Cannot open a '{scheme}:' URL. Use http, https, or a path to a "
            "local file."
        )

    path = Path(target).expanduser()

    if path.is_dir():
        return None, f"{path} is a directory. Point at the page file itself."

    if not path.is_file():
        return None, f"Page not found: {path}"

    if path.suffix.lower() not in PAGE_EXTENSIONS:
        return None, (
            f"'{path.suffix}' is not a page Flash can render. Supported: "
            + ", ".join(sorted(PAGE_EXTENSIONS))
        )

    return path.resolve().as_uri(), ""


# A headless browser draws no pointer, so the agent's is drawn into the
# page: the Flash cursor, following every move of Playwright's mouse, so
# a screenshot shows where it pointed and what it clicked. It lives in a
# closed shadow root with pointer-events off: nothing on the page can
# style it, find it, or be blocked by it.
_CURSOR_JS = """
(() => {
  if (window.__flashCursor) return;
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
    + ' viewBox="0 0 32 32" style="display:block;overflow:visible;'
    + 'filter:drop-shadow(0 1px 1.2px rgba(0,0,0,.4))"><path'
    + ' d="M4 3 23.7 18.2 13.2 20.4 6.1 28.4Z" fill="#d97757"'
    + ' stroke="#fff" stroke-width="1.8" stroke-linejoin="round"'
    + ' paint-order="stroke"/></svg>';
  let host = null, x = 0, y = 0, shown = false, pressed = false;
  const place = () => {
    if (!host) return;
    host.style.display = shown ? 'block' : 'none';
    host.style.transform = 'translate(' + (x - 3) + 'px,' + (y - 2) + 'px)'
      + (pressed ? ' scale(0.84)' : '');
  };
  const make = () => {
    const root = document.documentElement;
    if (!root || (host && host.isConnected)) return;
    host = document.createElement('flash-cursor');
    host.style.cssText = 'position:fixed;left:0;top:0;width:24px;'
      + 'height:24px;z-index:2147483647;pointer-events:none;'
      + 'transform-origin:3px 2px;display:none;';
    host.attachShadow({mode: 'closed'}).innerHTML = svg;
    root.appendChild(host);
    place();
  };
  window.__flashCursor = (nx, ny) => {
    x = nx; y = ny; shown = true; make(); place();
  };
  addEventListener('mousemove', (e) => {
    window.__flashCursor(e.clientX, e.clientY);
    if (window.__flashMoved) window.__flashMoved(e.clientX, e.clientY);
  }, true);
  addEventListener('mousedown', () => { pressed = true; place(); }, true);
  addEventListener('mouseup', () => { pressed = false; place(); }, true);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', make);
  } else {
    make();
  }
})();
"""


def _point(text: str) -> Optional[tuple[float, float]]:
    """An "x,y" the model typed, in the page's pixels, or None."""

    numbers = re.findall(r"-?\d+(?:\.\d+)?", text or "")
    if len(numbers) < 2:
        return None
    return float(numbers[0]), float(numbers[1])


def _watch(page, problems: list[str]) -> None:
    """Record the page's own errors so a broken render explains itself."""

    def on_console(message) -> None:
        if message.type == "error":
            problems.append(f"console error: {message.text}")

    page.on("console", on_console)
    page.on("pageerror", lambda exc: problems.append(f"page error: {exc}"))


def _settle(
    page,
    wait_ms: int,
    idle_timeout: int = NAVIGATION_TIMEOUT_MS,
) -> None:
    """Give fonts, layout, and any intro animation time to finish."""

    # A page that keeps a socket open or ships no web fonts is still
    # worth looking at, so neither wait is allowed to fail the capture.
    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=idle_timeout,
        )
    except Exception:  # noqa: BLE001, S110
        pass

    try:
        page.evaluate("document.fonts && document.fonts.ready")
    except Exception:  # noqa: BLE001, S110
        pass

    if wait_ms:
        page.wait_for_timeout(wait_ms)


def capture(
    url: str,
    out: Path,
    *,
    width: int,
    height: int,
    full_page: bool,
    wait_ms: int,
    mouse: Optional[tuple[float, float]] = None,
) -> tuple[list[str], str]:
    """Render `url` to `out` as a PNG.

    With `mouse`, the pointer is moved there first, so the picture shows
    what hovering does, with the Flash cursor where it rests.

    Returns `(problems, "")` on success, where `problems` are errors the
    page itself reported, or `([], reason)` when no screenshot was taken.
    """

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], PACKAGE_HINT

    problems: list[str] = []

    try:
        with sync_playwright() as driver:
            try:
                browser = driver.chromium.launch()
            except PlaywrightError as exc:
                return [], _launch_reason(exc)

            try:
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                )
                _watch(page, problems)
                page.add_init_script(_CURSOR_JS)
                page.goto(
                    url,
                    wait_until="load",
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                _settle(page, wait_ms)
                if mouse is not None:
                    page.mouse.move(*mouse)
                    # Long enough for a hover's transition to show.
                    page.wait_for_timeout(HOVER_SETTLE_MS)
                page.screenshot(path=str(out), full_page=full_page)
            finally:
                browser.close()
    except PlaywrightError as exc:
        return problems, _first_line(exc)

    if not out.is_file() or out.stat().st_size == 0:
        return problems, f"Chromium wrote no screenshot for {url}."

    return problems, ""


def _first_line(exc: Exception) -> str:
    """Playwright errors carry a long trace; the first line is the fault."""

    text = str(exc).strip()

    return text.splitlines()[0] if text else exc.__class__.__name__


def _launch_reason(exc: Exception) -> str:
    """Name the missing download when that is why Chromium did not start."""

    if "playwright install" in str(exc).lower():
        return BROWSER_HINT

    return f"Chromium would not start: {_first_line(exc)}"


# A click on a button that never appears should fail fast; only a whole
# navigation is worth waiting the longer time for.
ACTION_TIMEOUT_MS = 5000

# After the mouse arrives somewhere, before the picture: a hover's own
# transition is usually done by then.
HOVER_SETTLE_MS = 300

# How many steps a move or a drag is made in: enough that the page sees
# the pointer travel, as it would a hand's, and not jump.
MOUSE_STEPS = 12

# A page can carry hundreds of links. Enough of them to work with beats a
# list the model has to wade through.
MAX_ELEMENTS = 40

NO_PAGE = "No page is open. Open one with the open_page tool first."

# Numbering the interactive elements and stamping the number onto each
# one gives the model a selector it cannot get wrong. The stamp is a data
# attribute, so it changes nothing about how the page looks or behaves,
# and it is rewritten on every scan because the DOM moves under it.
_SCAN_JS = """
(limit) => {
  const wanted = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary',
    '[role="button"]', '[role="link"]', '[role="tab"]', '[role="checkbox"]',
    '[onclick]', '[contenteditable]',
  ].join(', ');
  const all = document.querySelectorAll(wanted);
  const items = [];
  let id = 0;
  for (const el of all) {
    const box = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    if (!box.width || !box.height) continue;
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    id += 1;
    el.setAttribute('data-flash-id', String(id));
    const label = el.getAttribute('aria-label')
      || (el.innerText || '').trim()
      || el.value
      || el.getAttribute('placeholder')
      || el.getAttribute('title')
      || el.getAttribute('name')
      || '';
    items.push({
      id: id,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      label: String(label).replace(/\\s+/g, ' ').trim().slice(0, 60),
      disabled: el.disabled === true,
    });
    if (items.length >= limit) break;
  }
  return {items: items, total: all.length};
}
"""


class PageProblem(Exception):
    """Something the model asked of the page that the page would not do."""


class _Session:
    """A Chromium that outlives the tool call which opened it."""

    def __init__(self, driver, browser) -> None:
        self.driver = driver
        self.browser = browser
        self.page: Any = None
        self.problems: list[str] = []
        # Where the agent's pointer is, in the page's pixels, once it has
        # moved: drawn again on a page it navigated to.
        self.mouse: Optional[tuple[float, float]] = None
        # What the page has fetched, and everything it has logged, since
        # it opened: kept quietly, for the devtools tool to show when it
        # is asked. Each request by its number, for a closer look.
        self.network: list[dict] = []
        self.requests: dict[int, Any] = {}
        self.console: list[dict] = []
        self.seq = 0

    def moved(self, x: float, y: float) -> None:
        self.mouse = (x, y)

    def redraw_cursor(self) -> None:
        """Put the cursor back where the pointer is, on a page that was
        loaded since it last moved and so has not drawn it yet."""

        if self.mouse is None or self.page is None:
            return
        try:
            self.page.evaluate(
                "([x, y]) => window.__flashCursor"
                " && window.__flashCursor(x, y)",
                list(self.mouse),
            )
        except Exception:  # noqa: BLE001, S110
            pass

    def drain(self) -> list[str]:
        """Hand over the errors seen since the last time we asked."""

        seen = list(self.problems)
        self.problems.clear()

        return seen

    def shut(self) -> None:
        # Shutting down is best-effort: a browser that has already
        # crashed must not stop Flash from opening the next one.
        for stop in (self.browser.close, self.driver.stop):
            try:
                stop()
            except Exception:  # noqa: BLE001, S110
                pass


_session: Optional[_Session] = None


def is_open() -> bool:
    """True while there is a live page to act on."""

    if _session is None or _session.page is None:
        return False

    try:
        return not _session.page.is_closed()
    except Exception:  # noqa: BLE001
        return False


def open_page(url: str, *, width: int, height: int, wait_ms: int) -> str:
    """Open `url` in a browser that stays open for later interaction.

    Returns `""` once the page has loaded, or the reason it has not.
    """

    global _session

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return PACKAGE_HINT

    # One page at a time: a second Chromium the model has forgotten about
    # is a leak, not a feature.
    close_session()

    try:
        driver = sync_playwright().start()
    except Exception as exc:  # noqa: BLE001
        return f"Chromium would not start: {_first_line(exc)}"

    try:
        browser = driver.chromium.launch()
    except PlaywrightError as exc:
        driver.stop()
        return _launch_reason(exc)

    session = _Session(driver, browser)

    try:
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        session.page = page
        _watch(page, session.problems)
        _record(page, session)
        page.expose_function("__flashMoved", session.moved)
        page.add_init_script(_CURSOR_JS)
        page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        _settle(page, wait_ms)
    except PlaywrightError as exc:
        session.shut()
        return _first_line(exc)

    _session = session

    return ""


def interact(
    action: str,
    *,
    selector: str = "",
    value: str = "",
    wait_ms: int = 0,
) -> tuple[str, str]:
    """Do one thing to the open page.

    Returns `(note, "")` describing what happened, or `("", reason)` when
    nothing did.
    """

    if not is_open():
        return "", NO_PAGE

    handler = _ACTIONS.get(action.strip().lower())

    if handler is None:
        return "", (
            f"Unknown action '{action}'. Use one of: "
            + ", ".join(ACTIONS)
            + "."
        )

    from playwright.sync_api import Error as PlaywrightError

    page = _session.page

    try:
        note = handler(page, selector.strip(), value)
        # The action may have navigated or started a fetch, so let the
        # page catch up before anyone photographs it.
        _settle(page, wait_ms, ACTION_TIMEOUT_MS)
    except (PageProblem, PlaywrightError) as exc:
        return "", _first_line(exc)
    finally:
        _session.redraw_cursor()

    return note, ""


def snapshot(out: Path, *, full_page: bool) -> str:
    """Photograph the open page as it stands. Returns "" or the reason."""

    if not is_open():
        return NO_PAGE

    from playwright.sync_api import Error as PlaywrightError

    try:
        _session.page.screenshot(path=str(out), full_page=full_page)
    except PlaywrightError as exc:
        return _first_line(exc)

    if not out.is_file() or out.stat().st_size == 0:
        return "Chromium wrote no screenshot of the open page."

    return ""


def elements() -> tuple[list[str], str]:
    """Number what can be clicked or typed into. Returns `(lines, why)`."""

    if not is_open():
        return [], NO_PAGE

    from playwright.sync_api import Error as PlaywrightError

    try:
        found = _session.page.evaluate(_SCAN_JS, MAX_ELEMENTS)
    except PlaywrightError as exc:
        return [], _first_line(exc)

    items = found.get("items", [])
    lines = [_describe(item) for item in items]

    hidden = found.get("total", 0) - len(items)
    if hidden > 0:
        lines.append(f"... and {hidden} more not listed")

    return lines, ""


def where() -> tuple[str, str]:
    """The open page's `(url, title)`, or `("", "")` when none is open."""

    if not is_open():
        return "", ""

    page = _session.page

    try:
        return page.url, page.title()
    except Exception:  # noqa: BLE001
        return getattr(page, "url", ""), ""


def drain_problems() -> list[str]:
    """The errors the page has thrown since the last time it was asked."""

    return [] if _session is None else _session.drain()


def close_session() -> bool:
    """Shut the open browser. True when there was one to shut."""

    global _session

    session = _session
    _session = None

    if session is None:
        return False

    session.shut()

    return True


# Chromium runs in its own process, so leaving one behind would outlive
# Flash itself.
atexit.register(close_session)


def _describe(item: dict) -> str:
    """One element as a line the model can read and then act on."""

    kind = item.get("tag", "?")
    if item.get("type"):
        kind += ":" + item["type"]

    line = f"[{item.get('id')}] {kind}"

    if item.get("label"):
        line += ' "' + item["label"] + '"'

    if item.get("disabled"):
        line += " (disabled)"

    return line


def _locate(page, selector: str):
    """Turn what the model typed into a locator for one element.

    A bare number is an id from the last element list. Anything else is
    tried as a CSS selector first and as the visible text second, because
    a model reaches for 'Sign in' far more readily than for
    'button.primary:nth-of-type(2)'.
    """

    if not selector:
        raise PageProblem(
            "That action needs a selector: an element number from the "
            "list, a CSS selector, or the text on the element."
        )

    if selector.isdigit():
        stamped = page.locator('[data-flash-id="' + selector + '"]')
        if not stamped.count():
            raise PageProblem(
                f"There is no element {selector} on this page. The numbers "
                "are handed out again after every action, so use the list "
                "that came with the most recent result."
            )

        return stamped.first

    try:
        css = page.locator(selector)
        if css.count():
            return css.first
    except Exception:  # noqa: BLE001, S110
        pass  # Not a selector Playwright understands; try it as text.

    by_text = page.get_by_text(selector)
    if by_text.count():
        return by_text.first

    quoted = json.dumps(selector)
    labelled = page.locator(
        ", ".join(
            "[" + name + "=" + quoted + "]"
            for name in ("aria-label", "placeholder", "title", "name", "value")
        )
    )
    if labelled.count():
        return labelled.first

    raise PageProblem(
        f"Nothing on the page matches '{selector}'. Check the element list "
        "in the last result and act on one of its numbers."
    )


def _act_click(page, selector: str, value: str) -> str:
    # No selector, a point: a click where the model sees something in the
    # screenshot that no selector names, like a spot on a canvas.
    if not selector:
        spot = _point(value)
        if spot is None:
            raise PageProblem(
                "click needs a selector, or a point as value: \"x,y\" in "
                "the screenshot's pixels."
            )
        page.mouse.move(*spot, steps=MOUSE_STEPS)
        page.mouse.click(*spot)

        return f"Clicked at {_spot(spot)}."

    _locate(page, selector).click(timeout=ACTION_TIMEOUT_MS)

    return f"Clicked {selector}."


def _spot(point: tuple[float, float]) -> str:
    return f"{point[0]:g},{point[1]:g}"


def _act_move(page, selector: str, value: str) -> str:
    """Move the pointer onto an element, or to a point, and leave it."""

    if selector:
        _locate(page, selector).hover(timeout=ACTION_TIMEOUT_MS)

        return f"Moved the mouse onto {selector}."

    spot = _point(value)
    if spot is None:
        raise PageProblem(
            "move needs a selector, or a point as value: \"x,y\" in the "
            "screenshot's pixels."
        )
    page.mouse.move(*spot, steps=MOUSE_STEPS)

    return f"Moved the mouse to {_spot(spot)}."


def _act_drag(page, selector: str, value: str) -> str:
    """Press at one point, move to another, let go: a slider, a canvas,
    something dragged into place."""

    numbers = re.findall(r"-?\d+(?:\.\d+)?", value or "")
    if len(numbers) < 4:
        raise PageProblem(
            "drag needs two points as value: \"x1,y1 x2,y2\" in the "
            "screenshot's pixels, from where to where."
        )
    start = (float(numbers[0]), float(numbers[1]))
    end = (float(numbers[2]), float(numbers[3]))
    page.mouse.move(*start, steps=MOUSE_STEPS)
    page.mouse.down()
    page.mouse.move(*end, steps=MOUSE_STEPS)
    page.mouse.up()

    return f"Dragged from {_spot(start)} to {_spot(end)}."


def _act_fill(page, selector: str, value: str) -> str:
    if not value:
        raise PageProblem("fill needs the text to type, in value.")

    _locate(page, selector).fill(value, timeout=ACTION_TIMEOUT_MS)

    return f"Typed '{value}' into {selector}."


def _act_press(page, selector: str, value: str) -> str:
    key = value.strip() or "Enter"

    if selector:
        _locate(page, selector).press(key, timeout=ACTION_TIMEOUT_MS)

        return f"Pressed {key} on {selector}."

    page.keyboard.press(key)

    return f"Pressed {key}."


def _act_hover(page, selector: str, value: str) -> str:
    _locate(page, selector).hover(timeout=ACTION_TIMEOUT_MS)

    return f"Hovered over {selector}."


def _act_select(page, selector: str, value: str) -> str:
    if not value:
        raise PageProblem("select needs the option to choose, in value.")

    _locate(page, selector).select_option(value, timeout=ACTION_TIMEOUT_MS)

    return f"Selected '{value}' in {selector}."


def _act_scroll(page, selector: str, value: str) -> str:
    if selector:
        _locate(page, selector).scroll_into_view_if_needed(
            timeout=ACTION_TIMEOUT_MS,
        )

        return f"Scrolled {selector} into view."

    where_to = value.strip().lower() or "bottom"

    if where_to in {"bottom", "end", "down"}:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

        return "Scrolled to the bottom of the page."

    if where_to in {"top", "start", "up"}:
        page.evaluate("window.scrollTo(0, 0)")

        return "Scrolled to the top of the page."

    try:
        pixels = int(float(where_to))
    except ValueError:
        raise PageProblem(
            "scroll takes 'top', 'bottom', or a number of pixels in value."
        ) from None

    page.evaluate("(y) => window.scrollBy(0, y)", pixels)

    return f"Scrolled {pixels} pixels down the page."


def _act_wait(page, selector: str, value: str) -> str:
    if selector:
        _locate(page, selector).wait_for(
            state="visible",
            timeout=NAVIGATION_TIMEOUT_MS,
        )

        return f"{selector} is now visible."

    try:
        pause = int(float(value.strip() or 1000))
    except ValueError:
        pause = 1000

    pause = min(pause, NAVIGATION_TIMEOUT_MS)
    page.wait_for_timeout(pause)

    return f"Waited {pause} ms."


def _act_eval(page, selector: str, value: str) -> str:
    if not value.strip():
        raise PageProblem("eval needs the JavaScript to run, in value.")

    return f"JavaScript returned: {_short(page.evaluate(value))}"


def _act_back(page, selector: str, value: str) -> str:
    if page.go_back(wait_until="load", timeout=NAVIGATION_TIMEOUT_MS) is None:
        return "There was nothing to go back to."

    return f"Went back to {page.url}."


def _act_reload(page, selector: str, value: str) -> str:
    page.reload(wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)

    return f"Reloaded {page.url}."


_ACTIONS = {
    "click": _act_click,
    "move": _act_move,
    "drag": _act_drag,
    "fill": _act_fill,
    "press": _act_press,
    "hover": _act_hover,
    "select": _act_select,
    "scroll": _act_scroll,
    "wait": _act_wait,
    "eval": _act_eval,
    "back": _act_back,
    "reload": _act_reload,
}

# Everything the model may put in the action argument. `close` is handled
# by the tool itself, since it ends the session rather than touching the
# page.
ACTIONS = (*sorted(_ACTIONS), "close")

MAX_EVAL_RESULT = 500


def _short(result) -> str:
    """A JavaScript result the model can read without drowning in it."""

    text = " ".join(str(result).split())

    if not text:
        return "nothing"

    if len(text) > MAX_EVAL_RESULT:
        return text[:MAX_EVAL_RESULT] + " ... (truncated)"

    return text


# --- Devtools ---------------------------------------------------------------
#
# What a browser's developer tools show, for the page that is open: the
# requests it made, everything it logged, JavaScript run in it with what
# that logged, and one element looked at closely. The requests and the
# log are kept from the moment the page opens; nothing is shown until
# the model asks.

MAX_NETWORK = 300
MAX_CONSOLE = 300
NETWORK_LIST = 40
CONSOLE_LIST = 60
MAX_BODY = 4000
MAX_RUN_RESULT = 4000
MAX_HTML = 2500
# What inspect reads of an element's computed style, unless asked for
# others: enough to say where it is and why it looks as it does.
STYLE_PROPERTIES = (
    "display", "position", "visibility", "opacity", "z-index", "width",
    "height", "margin", "padding", "color", "background-color",
    "font-family", "font-size", "font-weight", "line-height", "border",
    "overflow", "cursor", "pointer-events",
)


def _record(page, session: "_Session") -> None:
    """Keep what PAGE fetches and logs in SESSION, a few hundred of each."""

    by_request: dict[int, dict] = {}

    def on_request(request) -> None:
        session.seq += 1
        entry = {
            "n": session.seq, "method": request.method, "url": request.url,
            "type": request.resource_type, "status": None, "failure": "",
            "ms": None, "started": time.monotonic(),
        }
        session.network.append(entry)
        session.requests[session.seq] = request
        by_request[id(request)] = entry
        while len(session.network) > MAX_NETWORK:
            old = session.network.pop(0)
            gone = session.requests.pop(old["n"], None)
            by_request.pop(id(gone), None)

    def on_response(response) -> None:
        entry = by_request.get(id(response.request))
        if entry is not None:
            entry["status"] = response.status

    def on_done(request) -> None:
        entry = by_request.get(id(request))
        if entry is not None:
            entry["ms"] = round((time.monotonic() - entry["started"]) * 1000)

    def on_failed(request) -> None:
        entry = by_request.get(id(request))
        if entry is not None:
            entry["failure"] = str(request.failure or "failed")
            on_done(request)

    def on_console(message) -> None:
        try:
            where = message.location or {}
        except Exception:  # noqa: BLE001
            where = {}
        session.console.append({
            "type": message.type, "text": message.text, "at": time.time(),
            "where": (
                f"{where.get('url', '')}:{where.get('lineNumber', '')}"
                if where.get("url") else ""
            ),
        })
        del session.console[:-MAX_CONSOLE]

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfinished", on_done)
    page.on("requestfailed", on_failed)
    page.on("console", on_console)
    page.on("pageerror", lambda exc: session.console.append({
        "type": "pageerror", "text": str(exc), "at": time.time(), "where": "",
    }))


def _short_url(url: str, keep: int = 110) -> str:
    return url if len(url) <= keep else url[:keep - 3] + "..."


def network(
    match: str = "", failed_only: bool = False, limit: int = NETWORK_LIST,
) -> tuple[list[str], str]:
    """The requests the open page made, newest last: those whose address
    or type has MATCH in it, or that failed, if asked. `(lines, why)`."""

    if not is_open():
        return [], NO_PAGE
    match = match.strip().lower()
    found = [
        e for e in _session.network
        if (not match or match in e["url"].lower() or match == e["type"])
        and (not failed_only or e["failure"] or (e["status"] or 0) >= 400)
    ]
    failed = sum(
        1 for e in _session.network
        if e["failure"] or (e["status"] or 0) >= 400
    )
    lines = [
        f"{len(_session.network)} request"
        f"{'' if len(_session.network) == 1 else 's'} since the page opened"
        f", {failed} failed. request with a number shows one in full."
    ]
    if len(found) > limit:
        lines.append(f"(the last {limit} of {len(found)} that match)")
    for entry in found[-limit:]:
        status = (
            f"failed ({entry['failure']})" if entry["failure"]
            else str(entry["status"]) if entry["status"] is not None
            else "pending"
        )
        took = f" {entry['ms']}ms" if entry["ms"] is not None else ""
        lines.append(
            f"#{entry['n']} {entry['method']} {status} {entry['type']}"
            f"{took} {_short_url(entry['url'])}"
        )
    return lines, ""


def _headers(found: dict, keep: int = 30) -> str:
    return "\n".join(
        f"  {name}: {value[:200]}"
        for name, value in list(found.items())[:keep]
    ) or "  (none)"


def request(number: int) -> tuple[str, str]:
    """One request in full: what was sent, and what came back, with the
    start of its body. `(text, why)`."""

    if not is_open():
        return "", NO_PAGE
    found = _session.requests.get(int(number))
    entry = next((e for e in _session.network if e["n"] == int(number)), None)
    if found is None or entry is None:
        return "", (
            f"There is no request #{number}. The network list numbers them."
        )
    from playwright.sync_api import Error as PlaywrightError

    try:
        sent = found.all_headers()
        parts = [
            f"#{entry['n']} {found.method} {found.url}",
            f"Type: {found.resource_type}",
            "Request headers:\n" + _headers(sent),
        ]
        body = found.post_data
        if body:
            parts.append("Request body:\n" + body[:MAX_BODY])
        if entry["failure"]:
            parts.append(f"Failed: {entry['failure']}")
        response = found.response()
        if response is not None:
            parts.append(f"Status: {response.status} {response.status_text}")
            got = response.all_headers()
            parts.append("Response headers:\n" + _headers(got))
            kind = got.get("content-type", "")
            if any(t in kind for t in ("text", "json", "javascript", "xml",
                                       "html", "css", "svg")):
                try:
                    text = response.text()
                except PlaywrightError as exc:
                    text = f"(could not be read: {_first_line(exc)})"
                more = len(text) - MAX_BODY
                parts.append(
                    "Response body:\n" + text[:MAX_BODY]
                    + (f"\n... ({more} more characters)" if more > 0 else "")
                )
            elif kind:
                parts.append(f"Response body: {kind}, not shown as text.")
        if entry["ms"] is not None:
            parts.append(f"Took {entry['ms']} ms.")
    except PlaywrightError as exc:
        return "", _first_line(exc)
    return "\n".join(parts), ""


def console(
    level: str = "", limit: int = CONSOLE_LIST,
) -> tuple[list[str], str]:
    """What the open page logged, newest last: only LEVEL (log, info,
    warning, error) if asked. `(lines, why)`."""

    if not is_open():
        return [], NO_PAGE
    level = level.strip().lower()
    level = {"warn": "warning", "errors": "error"}.get(level, level)
    found = [
        m for m in _session.console
        if not level or m["type"] == level
        or (level == "error" and m["type"] == "pageerror")
    ]
    if not found:
        return ["The page has logged nothing" + (
            f" at the {level} level." if level else "."
        )], ""
    lines = [f"{len(found)} message{'' if len(found) == 1 else 's'}"
             + (f", the last {limit}" if len(found) > limit else "") + ":"]
    for message in found[-limit:]:
        where = f" ({message['where']})" if message["where"] else ""
        lines.append(f"[{message['type']}] {message['text'][:500]}{where}")
    return lines, ""


_RUN_JS = """
async () => {
  const logs = [];
  const kept = {};
  const shown = (x) => {
    if (typeof x === 'string') return x;
    if (x instanceof Element) return x.outerHTML.slice(0, 300);
    try { return JSON.stringify(x); } catch (_) { return String(x); }
  };
  for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
    kept[level] = console[level];
    console[level] = (...args) => {
      logs.push(level + ': ' + args.map(shown).join(' '));
      kept[level].apply(console, args);
    };
  }
  try {
    const value = await (async () => { %s })();
    let text;
    let kind = typeof value;
    if (value === undefined) {
      text = 'undefined';
    } else if (value instanceof Element) {
      kind = 'element';
      text = value.outerHTML;
    } else if (value instanceof NodeList || value instanceof HTMLCollection) {
      kind = 'elements';
      text = Array.from(value)
        .map((e) => e.outerHTML.slice(0, 200)).join('\\n');
    } else {
      try { text = JSON.stringify(value, null, 2); }
      catch (_) { text = String(value); }
    }
    if (text === undefined) text = String(value);
    return { ok: true, kind, text, logs };
  } catch (error) {
    const said = error && error.name
      ? error.name + ': ' + error.message : String(error);
    return { ok: false, kind: 'error', text: said, logs };
  } finally {
    for (const level in kept) console[level] = kept[level];
  }
}
"""


_STATEMENT_WORDS = (
    "const ", "let ", "var ", "if ", "if(", "for ", "for(", "while ",
    "while(", "return", "function ", "class ", "throw ", "try ", "try{",
    "switch", "}", "//",
)


def _ways_to_run(code: str) -> list[str]:
    """CODE as a function body, the ways a console would read it: as one
    expression; as statements whose last one is the value, as in
    `await x(); y`; and as statements that `return` what they mean to."""

    ways = [f"return ({code}\n);"]
    trimmed = code.rstrip().rstrip(";")
    cut = max(trimmed.rfind(";"), trimmed.rfind("\n"))
    if cut > 0:
        head, last = trimmed[:cut + 1], trimmed[cut + 1:].strip()
        if last and not last.startswith(_STATEMENT_WORDS):
            ways.append(f"{head}\nreturn ({last}\n);")
    ways.append(code)
    return ways


def run_script(code: str) -> tuple[str, str]:
    """Run CODE in the open page, as the console would: an expression's
    value comes back, statements can `return` one, and `await` works.
    What it logged meanwhile comes with it. `(text, why)`."""

    if not is_open():
        return "", NO_PAGE
    code = code.strip()
    if not code:
        return "", "run needs the JavaScript to run, in value."
    from playwright.sync_api import Error as PlaywrightError

    found = None
    problem = ""
    for body in _ways_to_run(code):
        try:
            found = _session.page.evaluate(_RUN_JS % body)
            break
        except PlaywrightError as exc:
            # A way the code does not parse: the next way, then.
            problem = problem or _first_line(exc)
    if found is None:
        return "", problem
    text = str(found.get("text", ""))
    if len(text) > MAX_RUN_RESULT:
        more = len(text) - MAX_RUN_RESULT
        text = text[:MAX_RUN_RESULT] + f"\n... ({more} more)"
    parts = [
        ("Returned" if found.get("ok") else "Threw")
        + f" ({found.get('kind', '')}):\n{text}"
    ]
    if found.get("logs"):
        parts.append("Logged:\n" + "\n".join(found["logs"][:50]))
    return "\n".join(parts), ""


_INSPECT_JS = """
([el, props]) => {
  const box = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  const attrs = {};
  for (const a of el.attributes) {
    if (a.name !== 'data-flash-id') attrs[a.name] = a.value.slice(0, 200);
  }
  const styles = {};
  for (const p of props) styles[p] = style.getPropertyValue(p);
  const path = [];
  let n = el;
  while (n && n.nodeType === 1 && path.length < 6) {
    let part = n.tagName.toLowerCase();
    if (n.id) part += '#' + n.id;
    else if (n.classList.length) {
      part += '.' + [...n.classList].slice(0, 2).join('.');
    }
    path.unshift(part);
    n = n.parentElement;
  }
  return {
    tag: el.tagName.toLowerCase(), path: path.join(' > '), attrs, styles,
    box: {
      x: Math.round(box.x), y: Math.round(box.y),
      width: Math.round(box.width), height: Math.round(box.height),
    },
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 300),
    children: el.children.length,
    html: el.outerHTML,
    visible: !!(box.width && box.height) && style.visibility !== 'hidden'
      && style.display !== 'none',
  };
}
"""


def inspect(target: str, properties: Optional[list] = None) -> tuple[str, str]:
    """One element of the open page, closely: where it sits in the page,
    its attributes, its box, its computed style, and its HTML. TARGET is
    an element number, a CSS selector, or its text. `(text, why)`."""

    if not is_open():
        return "", NO_PAGE
    from playwright.sync_api import Error as PlaywrightError

    wanted = [
        str(p).strip() for p in (properties or []) if str(p).strip()
    ] or list(STYLE_PROPERTIES)
    try:
        located = _locate(_session.page, target.strip())
        found = located.evaluate(
            "(el, props) => (" + _INSPECT_JS.strip() + ")([el, props])",
            wanted,
        )
    except (PageProblem, PlaywrightError) as exc:
        return "", _first_line(exc)
    box = found["box"]
    html = found["html"]
    if len(html) > MAX_HTML:
        more = len(found["html"]) - MAX_HTML
        html = html[:MAX_HTML] + f"\n... ({more} more)"
    parts = [
        f"<{found['tag']}> at {found['path']}",
        f"Box: {box['width']}x{box['height']} at ({box['x']}, {box['y']})"
        + ("" if found["visible"] else ", not visible"),
        f"Children: {found['children']}",
    ]
    if found["text"]:
        parts.append(f"Text: {found['text']}")
    if found["attrs"]:
        parts.append("Attributes:\n" + "\n".join(
            f"  {k}: {v}" for k, v in found["attrs"].items()
        ))
    parts.append("Computed style:\n" + "\n".join(
        f"  {k}: {v}" for k, v in found["styles"].items() if v != ""
    ))
    parts.append("HTML:\n" + html)
    return "\n".join(parts), ""
