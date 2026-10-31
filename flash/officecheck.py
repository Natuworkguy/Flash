"""Checking an office file before it is handed over.

A spreadsheet, deck or document Flash made is opened the way the user
will open it: LibreOffice turns it into pages (recalculating every
formula on the way), and the pages become pictures the model looks at
for what reading the file's code cannot show, like text running out of
its box or a table cut off at the page's edge. Alongside, what can be
found for certain is listed: formulas that end in an error, blank
pages, empty slides, and placeholder text left in.
"""

import re
import shutil
import subprocess  # nosec B404 -- LibreOffice and poppler, with arguments
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

KINDS = (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".doc", ".xls",
         ".ppt", ".pdf")
SHEETS = (".xlsx", ".xls", ".ods")
DECKS = (".pptx", ".ppt", ".odp")
MAX_PAGES = 12
RESOLUTION = 70
TIMEOUT = 180
MAX_FINDINGS = 30

PLACEHOLDER = re.compile(
    r"lorem ipsum|\bTODO\b|\bTBD\b|\bFIXME\b|\[insert[^\]]*\]|"
    r"\{\{[^}]*\}\}|\bXXX+\b|click to add (?:title|text)",
    re.I,
)
ERROR_VALUE = re.compile(r"^(#(?:REF!|DIV/0!|NAME\?|VALUE!|N/A|NUM!|NULL!)|"
                         r"Err:\d+)")


class CheckError(Exception):
    """The file could not be opened to check."""


@dataclass
class Report:
    name: str
    pages: int = 0
    findings: list = field(default_factory=list)
    images: list = field(default_factory=list)
    shown: list = field(default_factory=list)  # page numbers of IMAGES

    def text(self) -> str:
        lines = [f"Checked {self.name}: {self.pages} page"
                 f"{'' if self.pages == 1 else 's'}."]
        if self.findings:
            lines.append("Found:")
            lines += [f"- {f}" for f in self.findings[:MAX_FINDINGS]]
            more = len(self.findings) - MAX_FINDINGS
            if more > 0:
                lines.append(f"- and {more} more")
        else:
            lines.append("No formula errors, blank pages, empty slides or "
                         "placeholder text.")
        if self.images:
            lines.append(
                "The images with this result are "
                + ("page " if len(self.shown) == 1 else "pages ")
                + ", ".join(map(str, self.shown))
                + " as they print. Look at each for what the checks "
                "cannot see: text running off the page or out of its box, "
                "overlapping shapes, a table or chart cut off, ##### in "
                "a column too narrow, unreadable contrast, a slide with "
                "too much on it.")
        lines.append("Fix what is wrong in the code that made the file, "
                     "make it again and check again. Hand it over only "
                     "once it is clean, saying it was checked.")
        return "\n".join(lines)


def _soffice() -> str:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if not found:
        raise CheckError(
            "checking an office file needs LibreOffice: install it "
            "(brew install --cask libreoffice, or apt install "
            "libreoffice)")
    return found


def convert(path: Path, out: Path, to: str) -> Path:
    """PATH converted by LibreOffice TO a format (pdf, xlsx), in OUT."""

    profile = out / "profile"
    done = subprocess.run(  # nosec B603
        [_soffice(), f"-env:UserInstallation={profile.as_uri()}",
         "--headless", "--norestore", "--convert-to", to, "--outdir",
         str(out), str(path)],
        capture_output=True, text=True, timeout=TIMEOUT, check=False,
    )
    made = out / f"{path.stem}.{to.split(':')[0]}"
    if not made.is_file():
        raise CheckError("LibreOffice could not open it: "
                         + (done.stderr.strip() or done.stdout.strip()
                            or "no output")[-300:])
    return made


def page_texts(pdf: Path) -> list[str]:
    tool = shutil.which("pdftotext")
    if not tool:
        return []
    done = subprocess.run(  # nosec B603
        [tool, "-layout", str(pdf), "-"], capture_output=True, text=True,
        timeout=TIMEOUT, check=False, errors="replace",
    )
    texts = done.stdout.split("\f")
    return texts[:-1] if texts and not texts[-1].strip() else texts


def page_images(pdf: Path, out: Path, pages: list[int]) -> list[bytes]:
    tool = shutil.which("pdftoppm")
    if not tool:
        return []
    images = []
    for page in pages:
        stem = out / f"page-{page}"
        subprocess.run(  # nosec B603
            [tool, "-png", "-r", str(RESOLUTION), "-f", str(page), "-l",
             str(page), "-singlefile", str(pdf), str(stem)],
            capture_output=True, timeout=TIMEOUT, check=False,
        )
        made = stem.with_suffix(".png")
        if made.is_file():
            images.append(made.read_bytes())
    return images


def _which_pages(count: int, most: int = MAX_PAGES) -> list[int]:
    if count <= most:
        return list(range(1, count + 1))
    keep = {1, count}
    n = 1
    while len(keep) < most:
        keep.add(1 + round(n * (count - 1) / (most - 1)))
        n += 1
    return sorted(keep)


def _read(book: zipfile.ZipFile, name: str) -> str:
    try:
        return book.read(name).decode("utf-8", "replace")
    except KeyError:
        return ""


def _attrs(xml: str, tag: str, *names: str) -> list[tuple]:
    """Each TAG element's NAMES attributes, in whatever order written."""

    found = []
    for attrs in re.findall(rf"<{tag}\b([^>]*)>", xml):
        values = [re.search(rf'(?:^|\s){re.escape(n)}="([^"]*)"', attrs)
                  for n in names]
        if all(values):
            found.append(tuple(v.group(1) for v in values))
    return found


def _targets(book: zipfile.ZipFile, rels: str) -> dict:
    return dict(_attrs(_read(book, rels), "Relationship", "Id", "Target"))


def sheet_errors(xlsx: Path) -> list[str]:
    """The cells whose formulas end in an error, as Sheet!A1 = #REF!,
    from a workbook LibreOffice has just recalculated."""

    found = []
    with zipfile.ZipFile(xlsx) as book:
        rels = _targets(book, "xl/_rels/workbook.xml.rels")
        for name, rid in _attrs(_read(book, "xl/workbook.xml"), "sheet",
                                "name", "r:id"):
            target = rels.get(rid, "").lstrip("/")
            xml = _read(book, target if target.startswith("xl/")
                        else f"xl/{target}")
            for attrs, body in re.findall(r"<c\b([^>]*)>(.*?)</c>", xml,
                                          re.S):
                if 't="e"' not in attrs:
                    continue
                ref = re.search(r'r="([A-Z]+\d+)"', attrs)
                value = re.search(r"<v>([^<]*)</v>", body)
                if ref and value and ERROR_VALUE.match(value.group(1)):
                    found.append(f"{name}!{ref.group(1)} shows "
                                 f"{value.group(1)}")
    return found


def empty_slides(pptx: Path) -> list[int]:
    """The slides with nothing on them: no words, no picture, no chart
    or table."""

    empty = []
    with zipfile.ZipFile(pptx) as deck:
        rels = _targets(deck, "ppt/_rels/presentation.xml.rels")
        order = _attrs(_read(deck, "ppt/presentation.xml"), "p:sldId",
                       "r:id")
        for number, (rid,) in enumerate(order, start=1):
            xml = _read(deck, "ppt/" + rels.get(rid, "").lstrip("/"))
            words = "".join(re.findall(r"<a:t>([^<]*)</a:t>", xml)).strip()
            if not words and not re.search(r"<p:pic\b|<p:graphicFrame\b",
                                           xml):
                empty.append(number)
    return empty


def check(path: Path, images: bool = True) -> Report:
    """Open PATH as the user will, and say what is wrong with it."""

    path = Path(path).expanduser()
    if not path.is_file():
        raise CheckError(f"no file at {path}")
    suffix = path.suffix.lower()
    if suffix not in KINDS:
        raise CheckError(f"{path.name} is not an office file: use one of "
                         + ", ".join(KINDS))
    report = Report(path.name)
    with tempfile.TemporaryDirectory(prefix="flash-check-") as folder:
        out = Path(folder)
        pdf = path if suffix == ".pdf" else convert(path, out, "pdf")
        texts = page_texts(pdf)
        report.pages = len(texts)
        if suffix in SHEETS:
            # Recalculated: a file written by code often carries its
            # formulas without their values.
            book = convert(path, out / "recalculated", "xlsx")
            report.findings += sheet_errors(book)
        if suffix == ".pptx":
            report.findings += [f"slide {n} is empty"
                                for n in empty_slides(path)]
        elif suffix not in SHEETS + DECKS:
            report.findings += [f"page {n} is blank"
                                for n, text in enumerate(texts, start=1)
                                if not text.strip()]
        for n, text in enumerate(texts, start=1):
            for match in sorted({m.group(0) for m in
                                 PLACEHOLDER.finditer(text)}):
                where = "slide" if suffix in DECKS else "page"
                report.findings.append(
                    f'placeholder text "{match}" on {where} {n}')
        if images and report.pages:
            report.shown = _which_pages(report.pages)
            report.images = page_images(pdf, out, report.shown)
            if not report.images:
                report.shown = []
    return report
