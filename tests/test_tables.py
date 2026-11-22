# pylint: disable=C0114,C0115,C0116

import json
import zipfile

import pytest

from flash import tables


@pytest.fixture(autouse=True)
def kept(tmp_path, monkeypatch):
    monkeypatch.setattr(tables, "db_path", lambda: tmp_path / "tables.db")


MESSY = (
    "Q3 Sales Report\n"
    "Prepared by finance,,,,\n"
    "\n"
    "Order ID,Customer,Amount,Discount,Ordered On,Paid,Region,\n"
    "00142,Acme Co,\" $1,250.00 \",10%,2026-07-03,Yes,North,\n"
    "00143,Blue Ltd,$980.50,0%,2026-07-09,No,South,\n"
    ",,,,,,,\n"
    "00144,Acme Co,\"$2,000\",5%,2026-08-01,yes,North,\n"
    "00143,Blue Ltd,$980.50,0%,2026-07-09,No,South,\n"
)


def test_a_messy_csv_becomes_a_typed_table(tmp_path):
    path = tmp_path / "Q3 sales.csv"
    path.write_text(MESSY, encoding="utf-8")
    made = tables.import_file(str(path))
    assert made["name"] == "q3_sales"  # nosec B101
    kinds = {c["name"]: c["type"] for c in made["columns"]}
    assert kinds == {"order_id": "text", "customer": "text", "amount": "money", "discount": "percent",  # nosec B101
                     "ordered_on": "date", "paid": "boolean", "region": "text"}
    assert len(made["rows"]) == 3  # nosec B101
    notes = " ".join(made["notes"])
    assert "above the header" in notes and "empty row" in notes and "duplicate" in notes  # nosec B101
    assert "empty column" in notes  # nosec B101
    # Order ids keep their leading zeros; money and percents are numbers.
    cols, rows, _ = tables.query("SELECT order_id, amount, discount, paid FROM q3_sales ORDER BY order_id")
    assert rows[0] == ("00142", 1250.0, 0.1, 1)  # nosec B101
    cols, rows, _ = tables.query("SELECT customer, SUM(amount) AS total FROM q3_sales GROUP BY customer ORDER BY total DESC")
    assert rows == [("Acme Co", 3250.0), ("Blue Ltd", 980.5)]  # nosec B101
    cols, rows, _ = tables.query("SELECT strftime('%m', ordered_on) AS m, COUNT(*) FROM q3_sales GROUP BY m")
    assert rows == [("07", 2), ("08", 1)]  # nosec B101


def test_questions_cannot_change_the_tables(tmp_path):
    (tmp_path / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    tables.import_file(str(tmp_path / "a.csv"))
    for bad in ("DELETE FROM a", "DROP TABLE a", "UPDATE a SET x = 5", "SELECT 1; DROP TABLE a"):
        with pytest.raises(tables.TableError):
            tables.query(bad)
    assert tables.query("SELECT x, y FROM a")[1] == [(1, 2)]  # nosec B101


def xlsx(path, rows, date_cells=()):
    """A real, minimal .xlsx: shared strings, numbers, and a date-formatted
    cell, as Excel writes them."""

    strings, cells_xml = [], []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(value, str):
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                style = ' s="1"' if (r, c) in date_cells else ""
                cells.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
        cells_xml.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="Notes" sheetId="1" r:id="rId1"/>'
                                      f'<sheet name="Stock" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="inlineStr">'
                                               f'<is><t>hello</t></is></c></row></sheetData></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet {ns}><sheetData>{"".join(cells_xml)}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/styles.xml", f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/>'
                                    f'</cellXfs></styleSheet>')


def test_a_workbook_sheet_is_read_with_its_dates(tmp_path):
    path = tmp_path / "inventory.xlsx"
    xlsx(path, [["Item", "Count", "Restocked"], ["Bolts", 120, 46023], ["Nuts", 75.5, 46030]],
         date_cells={(2, 2), (3, 2)})
    made = tables.import_file(str(path), sheet="stock")
    assert made["what"].startswith("sheet Stock") and "Notes" in made["what"]  # nosec B101
    kinds = {c["name"]: c["type"] for c in made["columns"]}
    assert kinds == {"item": "text", "count": "number", "restocked": "date"}  # nosec B101
    assert tables.query("SELECT item, restocked FROM inventory_stock")[1][0] == ("Bolts", "2026-01-01")  # nosec B101
    with pytest.raises(tables.TableError, match="sheets: Notes, Stock"):
        tables.import_file(str(path), sheet="Prices")


def test_json_rows_and_export_and_removal(tmp_path):
    path = tmp_path / "people.json"
    path.write_text(json.dumps({"people": [{"name": "Ana", "age": 31}, {"name": "Bo", "age": 27, "city": "Oslo"}]}),
                    encoding="utf-8")
    made = tables.import_file(str(path))
    assert [c["name"] for c in made["columns"]] == ["name", "age", "city"]  # nosec B101
    out = tables.export("people", str(tmp_path / "out" / "people.csv"))
    assert open(out, encoding="utf-8").read().splitlines() == ["name,age,city", "Ana,31,", "Bo,27,Oslo"]  # nosec B101
    assert [t["name"] for t in tables.listing()] == ["people"]  # nosec B101
    assert tables.drop("people") and tables.listing() == []  # nosec B101
    assert path.exists()  # nosec B101


def test_the_tool_imports_then_answers(tmp_path):
    from flash import tools

    path = tmp_path / "sales.csv"
    path.write_text(MESSY, encoding="utf-8")
    said = tools.table_tool("import", path=str(path), name="sales")
    assert "Imported 3 rows" in said and "amount (money" in said and "dropped 1 duplicate row" in said  # nosec B101
    answer = tools.table_tool("query", sql="SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region")
    assert "| region | total |" in answer and "| North | 3,250 |" in answer  # nosec B101
    assert "sales: 3 rows" in tools.table_tool("list")  # nosec B101
    assert "Error" in tools.table_tool("query", sql="DELETE FROM sales")  # nosec B101
    (tmp_path / "old.xls").write_bytes(b"\xd0\xcf")
    assert "old .xls" in tools.table_tool("import", path=str(tmp_path / "old.xls"))  # nosec B101
