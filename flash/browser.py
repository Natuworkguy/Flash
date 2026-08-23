"""Headless browser screenshots for Flash CLI.

Playwright drives a real Chromium so the model can look at a page it
built instead of guessing from the source. The import is deferred to the
moment a screenshot is asked for, because Playwright is slow to import
and Flash starts fine without it; a missing install turns into a tool
result the model can read and relay rather than a crash at startup.
"""

from pathlib import Path
from typing import Union
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


def _settle(page, wait_ms: int) -> None:
    """Give fonts, layout, and any intro animation time to finish."""

    # A page that keeps a socket open or ships no web fonts is still
    # worth looking at, so neither wait is allowed to fail the capture.
    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=NAVIGATION_TIMEOUT_MS,
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
