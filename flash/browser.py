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
from pathlib import Path
from typing import Any, Union
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
) -> tuple[Union[str, None], str]:  # noqa: UP007, RUF100
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
) -> tuple[list[str], str]:
    """Render `url` to `out` as a PNG.

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
                page.goto(
                    url,
                    wait_until="load",
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                _settle(page, wait_ms)
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


_session: Union[_Session, None] = None  # noqa: UP007, RUF100


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
    _locate(page, selector).click(timeout=ACTION_TIMEOUT_MS)

    return f"Clicked {selector}."


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
