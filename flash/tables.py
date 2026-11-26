"""Spreadsheets brought into Flash as tables it can answer questions from.

A CSV, TSV, Excel workbook (.xlsx) or JSON list the user drops in is read,
tidied and kept as a table in ~/.flash/tables.db: the header row found
under any title rows above it, cells trimmed, empty rows and columns and
exact duplicates dropped, and each column given the type its values have
(whole numbers, numbers, money, percents, dates, yes or no, or text) so
"total revenue by month" sums numbers rather than strings. The file itself
is never changed. Questions are answered with read-only SQL over the kept
tables, and a table can be written back out as a clean CSV.

Standard library only: an .xlsx is a zip of XML, read here directly.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree as ET

MAX_ROWS = 1_000_000
SHOW_ROWS = 50
TYPES = ("integer", "number", "money", "percent", "date", "boolean", "text")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_TRUE = {"yes", "y", "true", "t", "1"}
_FALSE = {"no", "n", "false", "f", "0"}


class TableError(ValueError):
    pass


def db_path() -> Path:
    from . import paths

    return paths.FLASH_DIR / "tables.db"


# ----- reading files -----

def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-16" and "\x00" in text:
            continue
        return text
    raise TableError("could not read the file's text")


def read_delimited(text: str) -> list[list[str]]:
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    n = 0
    for ch in letters.group(0) if letters else "A":
        n = n * 26 + ord(ch) - 64
    return n - 1


def _excel_date(serial: float) -> str:
    when = dt.datetime(1899, 12, 30) + dt.timedelta(days=serial)
    return when.date().isoformat() if when.time() == dt.time() else when.isoformat(sep=" ", timespec="minutes")


def read_xlsx(path: Path, sheet: str = "") -> tuple[list[list[str]], str, list[str]]:
    """The rows of SHEET (the first by default), its name, and every
    sheet's name."""

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        date_styles: set[int] = set()
        if "xl/styles.xml" in names:
            styles = ET.fromstring(z.read("xl/styles.xml"))
            custom = {int(f.get("numFmtId", 0)): f.get("formatCode", "")
                      for f in styles.iter(f"{{{_NS['m']}}}numFmt")}
            xfs = styles.find("m:cellXfs", _NS)
            for i, xf in enumerate(xfs.findall("m:xf", _NS) if xfs is not None else []):
                fid = int(xf.get("numFmtId", 0))
                code = custom.get(fid, "").lower()
                if 14 <= fid <= 22 or 45 <= fid <= 47 or (code and re.search(r"[dy]", re.sub(r'"[^"]*"', "", code))):
                    date_styles.add(i)
        book = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target", "") for r in rels}
        sheets = [(s.get("name", ""), targets.get(s.get(f"{{{_NS['r']}}}id"), ""))
                  for s in book.iter(f"{{{_NS['m']}}}sheet")]
        if not sheets:
            raise TableError("that workbook has no sheets")
        chosen = next((s for s in sheets if s[0].lower() == sheet.lower()), None) if sheet else sheets[0]
        if chosen is None:
            raise TableError(f"no sheet called {sheet!r}; its sheets: {', '.join(s[0] for s in sheets)}")
        target = chosen[1].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        rows: list[list[str]] = []
        for row in ET.fromstring(z.read(target)).iter(f"{{{_NS['m']}}}row"):
            out: list[str] = []
            for c in row.findall("m:c", _NS):
                at = _col_index(c.get("r", "A"))
                while len(out) < at:
                    out.append("")
                kind, style = c.get("t", ""), int(c.get("s", 0))
                v = c.find("m:v", _NS)
                text = v.text if v is not None and v.text is not None else ""
                if kind == "s" and text:
                    text = shared[int(text)]
                elif kind == "inlineStr":
                    text = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                elif kind == "b":
                    text = "TRUE" if text == "1" else "FALSE"
                elif text and style in date_styles and kind in ("", "n"):
                    try:
                        text = _excel_date(float(text))
                    except (ValueError, OverflowError):
                        pass
                out.append(text)
            rows.append(out)
            if len(rows) > MAX_ROWS:
                break
        return rows, chosen[0], [s[0] for s in sheets]


def read_json(text: str) -> list[list[str]]:
    data = json.loads(text)
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if lists else [data]
    if not isinstance(data, list) or not data:
        raise TableError("the JSON has no list of rows in it")
    if all(isinstance(r, dict) for r in data):
        keys: list[str] = []
        for r in data:
            keys += [k for k in r if k not in keys]
        return [keys] + [["" if r.get(k) is None else
                          (json.dumps(r[k]) if isinstance(r.get(k), (dict, list)) else str(r[k]))
                          for k in keys] for r in data]
    return [[str(v) for v in (r if isinstance(r, list) else [r])] for r in data]


def read_file(path: str, sheet: str = "") -> tuple[list[list[str]], str]:
    """The rows in the file at PATH, and what they are (the sheet's name
    for a workbook)."""

    p = Path(path).expanduser()
    if not p.is_file():
        raise TableError(f"no file at {path}")
    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        rows, name, all_sheets = read_xlsx(p, sheet)
        more = f" (sheets: {', '.join(all_sheets)})" if len(all_sheets) > 1 else ""
        return rows, f"sheet {name}{more}"
    if suffix == ".xls":
        raise TableError("that is the old .xls format: save it as .xlsx or CSV first")
    text = _read_text(p)
    if suffix == ".json":
        return read_json(text), "JSON"
    return read_delimited(text), "CSV" if suffix != ".tsv" else "TSV"


# ----- tidying -----

def _blank(row: list[str]) -> bool:
    return not any(str(c).strip() for c in row)


def find_header(rows: list[list[str]]) -> int:
    """Which row is the header: among the first ten, the first that is as
    wide as the table and all text, under any title rows above it."""

    width = max((sum(1 for c in r if str(c).strip()) for r in rows[:50]), default=0)
    for i, row in enumerate(rows[:10]):
        cells = [str(c).strip() for c in row if str(c).strip()]
        if len(cells) >= max(1, width * 0.6) and all(_kind(c) == "text" for c in cells):
            return i
    return next((i for i, r in enumerate(rows) if not _blank(r)), 0)


def column_name(text: str, taken: set[str], n: int) -> str:
    name = re.sub(r"[^0-9a-z]+", "_", str(text).strip().lower()).strip("_") or f"column_{n + 1}"
    if name[0].isdigit():
        name = "c_" + name
    base, k = name, 2
    while name in taken:
        name, k = f"{base}_{k}", k + 1
    taken.add(name)
    return name


_MONEY = re.compile(r"^\(?-?\s*[$€£¥₹]\s*-?[\d,]*\.?\d+\)?$|^-?[\d,]*\.?\d+\s*[$€£¥₹]$")
_NUMBER = re.compile(r"^\(?-?[\d,]*\.?\d+(?:[eE][-+]?\d+)?\)?$")
_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$|"
                   r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$|^[A-Za-z]{3,9}\.? \d{1,2},? \d{4}$|^\d{1,2} [A-Za-z]{3,9}\.? \d{4}$")


def _kind(v: str) -> str:
    v = v.strip()
    if not v:
        return ""
    low = v.lower()
    if low in ("true", "false", "yes", "no"):
        return "boolean"
    if _MONEY.match(v):
        return "money"
    if v.endswith("%") and _NUMBER.match(v[:-1].strip()):
        return "percent"
    if _NUMBER.match(v) and not re.match(r"^0\d", v.replace(",", "")):  # a leading zero is a code, not a number
        return "integer" if re.fullmatch(r"\(?-?[\d,]+\)?", v) and (v.count(",") == 0 or re.fullmatch(
            r"\(?-?\d{1,3}(,\d{3})+\)?", v)) else "number"
    if _DATE.match(v):
        return "date"
    return "text"


def column_type(values: list[str]) -> str:
    kinds = [k for k in (_kind(v) for v in values) if k]
    if not kinds:
        return "text"
    found = set(kinds)
    if found == {"integer"}:
        return "integer"
    if found <= {"integer", "number"}:
        return "number"
    if found <= {"money", "integer", "number"} and "money" in found:
        return "money"
    if found <= {"percent"}:
        return "percent"
    if found <= {"date"}:
        return "date"
    if found <= {"boolean"}:
        return "boolean"
    return "text"


def _num(v: str) -> Optional[float]:
    s = re.sub(r"[$€£¥₹,%\s]", "", v)
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negative else n


def _date(v: str) -> Optional[str]:
    v = v.strip()
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", v):
        try:
            parts = re.split(r"[ T]", v, maxsplit=1)
            day = dt.date(*[int(x) for x in parts[0].split("-")]).isoformat()
            return day + (" " + parts[1] if len(parts) > 1 else "")
        except ValueError:
            return None
    try:
        from dateutil import parser
    except ImportError:
        return None
    try:
        return parser.parse(v, dayfirst=False).date().isoformat()
    except (ValueError, OverflowError):
        return None


def convert(v: str, kind: str) -> Any:
    v = str(v).strip()
    if not v:
        return None
    if kind in ("integer", "number", "money"):
        n = _num(v)
        if n is None:
            return v
        return int(n) if kind == "integer" and n.is_integer() else n
    if kind == "percent":
        n = _num(v)
        return None if n is None else n / 100
    if kind == "date":
        return _date(v) or v
    if kind == "boolean":
        return 1 if v.lower() in _TRUE else 0 if v.lower() in _FALSE else v
    return v


def tidy(rows: list[list[str]]) -> dict:
    """Header, columns with their types, rows of typed values, and what
    was done to get there."""

    if not rows or all(_blank(r) for r in rows):
        raise TableError("the file has no rows in it")
    notes: list[str] = []
    at = find_header(rows)
    if at:
        notes.append(f"skipped {at} row{'s' if at > 1 else ''} above the header")
    header, body = rows[at], rows[at + 1:]
    width = max([len(header)] + [len(r) for r in body])
    body = [[str(c).strip() for c in r] + [""] * (width - len(r)) for r in body]
    header = [str(c).strip() for c in header] + [""] * (width - len(header))
    blank = len(body)
    body = [r for r in body if not _blank(r)]
    if blank - len(body):
        notes.append(f"dropped {blank - len(body)} empty row{'s' if blank - len(body) != 1 else ''}")
    keep = [i for i in range(width) if header[i] or any(r[i] for r in body)]
    if len(keep) < width:
        notes.append(f"dropped {width - len(keep)} empty column{'s' if width - len(keep) != 1 else ''}")
    header = [header[i] for i in keep]
    body = [[r[i] for i in keep] for r in body]
    seen: set[tuple] = set()
    unique = []
    for r in body:
        key = tuple(r)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    if len(unique) < len(body):
        notes.append(f"dropped {len(body) - len(unique)} duplicate row{'s' if len(body) - len(unique) != 1 else ''}")
    body = unique[:MAX_ROWS]
    taken: set[str] = set()
    names = [column_name(h, taken, i) for i, h in enumerate(header)]
    kinds = [column_type([r[i] for r in body]) for i in range(len(names))]
    typed = [[convert(r[i], kinds[i]) for i in range(len(names))] for r in body]
    columns = [{"name": n, "was": h, "type": k} for n, h, k in zip(names, header, kinds)]
    return {"columns": columns, "rows": typed, "notes": notes}


# ----- keeping and asking -----

_SQL_TYPE = {"integer": "INTEGER", "number": "REAL", "money": "REAL", "percent": "REAL",
             "boolean": "INTEGER", "date": "TEXT", "text": "TEXT"}


def _connect(read_only: bool = False) -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        if not path.exists():
            raise TableError("no tables yet: import a spreadsheet first")
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS _tables (name TEXT PRIMARY KEY, source TEXT, what TEXT,"
                " imported TEXT, columns TEXT, rows INTEGER, notes TEXT)")
    return con


def table_name(text: str) -> str:
    name = re.sub(r"[^0-9a-z]+", "_", str(text).lower()).strip("_")[:40] or "table"
    return ("t_" + name) if name[0].isdigit() or name.startswith("_") or name.startswith("sqlite") else name


def import_file(path: str, name: str = "", sheet: str = "", replace: bool = True) -> dict:
    rows, what = read_file(path, sheet)
    made = tidy(rows)
    tname = table_name(name or (Path(path).stem + (f"_{sheet}" if sheet else "")))
    con = _connect()
    try:
        if not replace and con.execute("SELECT 1 FROM _tables WHERE name=?", (tname,)).fetchone():
            raise TableError(f"there is already a table called {tname}")
        cols = made["columns"]
        con.execute(f'DROP TABLE IF EXISTS "{tname}"')
        con.execute(f'CREATE TABLE "{tname}" (' + ", ".join(
            f'"{c["name"]}" {_SQL_TYPE[c["type"]]}' for c in cols) + ")")
        con.executemany(f'INSERT INTO "{tname}" VALUES (' + ",".join("?" * len(cols)) + ")", made["rows"])
        con.execute("INSERT OR REPLACE INTO _tables VALUES (?,?,?,?,?,?,?)",
                    (tname, str(Path(path).expanduser().resolve()), what,
                     dt.datetime.now().isoformat(timespec="seconds"), json.dumps(cols),
                     len(made["rows"]), json.dumps(made["notes"])))
        con.commit()
    finally:
        con.close()
    return {"name": tname, "what": what, **made}


def listing() -> list[dict]:
    if not db_path().exists():
        return []
    con = _connect(read_only=True)
    try:
        found = con.execute("SELECT name, source, what, imported, columns, rows FROM _tables ORDER BY imported DESC")
        return [{"name": n, "source": s, "what": w, "imported": i, "columns": json.loads(c), "rows": r}
                for n, s, w, i, c, r in found.fetchall()]
    finally:
        con.close()


def query(sql: str, limit: int = SHOW_ROWS) -> tuple[list[str], list[tuple], bool]:
    """Columns, rows (at most LIMIT) and whether there were more, for SQL
    run read-only: it can look at the tables but never change them."""

    sql = str(sql or "").strip().rstrip(";")
    if not sql:
        raise TableError("query needs sql")
    if ";" in sql:
        raise TableError("one statement at a time")
    con = _connect(read_only=True)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(limit + 1)
    except sqlite3.Error as exc:
        raise TableError(f"that query failed: {exc}") from None
    finally:
        con.close()
    return cols, rows[:limit], len(rows) > limit


def export(name: str, path: str) -> str:
    cols, rows, _more = query(f'SELECT * FROM "{table_name(name)}"', limit=MAX_ROWS)
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(["" if v is None else v for v in r] for r in rows)
    return str(out)


def drop(name: str) -> bool:
    tname = table_name(name)
    con = _connect()
    try:
        had = con.execute("SELECT 1 FROM _tables WHERE name=?", (tname,)).fetchone() is not None
        con.execute(f'DROP TABLE IF EXISTS "{tname}"')
        con.execute("DELETE FROM _tables WHERE name=?", (tname,))
        con.commit()
        return had
    finally:
        con.close()


# ----- said to the model -----

def markdown(cols: list[str], rows: list, more: bool = False) -> str:
    def cell(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1e15 else str(v)
        return str(v).replace("|", "\\|").replace("\n", " ")[:120]

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    if more:
        lines.append(f"(more rows than these {len(rows)}: add LIMIT, or aggregate)")
    return "\n".join(lines)


def describe_import(made: dict) -> str:
    cols = made["columns"]
    lines = [f"Imported {len(made['rows']):,} rows from the {made['what']} as table {made['name']}."]
    if made["notes"]:
        lines.append("Tidied: " + "; ".join(made["notes"]) + ".")
    lines.append("Columns: " + ", ".join(
        f"{c['name']} ({c['type']}{', was ' + repr(c['was']) if c['was'] and c['was'] != c['name'] else ''})"
        for c in cols) + ".")
    lines.append("Money and numbers are stored as numbers, percents as fractions (12% is 0.12), dates as "
                 "YYYY-MM-DD, yes/no as 1/0. Ask with action query and SQLite SQL.")
    sample = made["rows"][:5]
    if sample:
        lines.append(markdown([c["name"] for c in cols], sample))
    return "\n".join(lines)
