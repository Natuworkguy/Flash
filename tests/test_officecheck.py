"""Tests for checking an office file before it is handed over."""

import os
import shutil
import subprocess  # nosec B404
import zipfile
from pathlib import Path

import pytest

from flash import officecheck, tools


def has_writer_and_calc():
    """LibreOffice that can open documents and sheets, not only its
    core, and poppler to read the pages."""

    soffice = shutil.which("soffice")
    if not soffice or not shutil.which("pdftotext"):
        return False
    program = Path(os.path.realpath(soffice)).parent
    return all((program / lib).exists() for lib in ("libswlo.so",
                                                    "libsclo.so"))


needs_office = pytest.mark.skipif(not has_writer_and_calc(),
                                  reason="needs LibreOffice Writer and Calc")


def deck(tmp_path, slides):
    """A .pptx with just the parts that say what is on each slide."""

    path = tmp_path / "deck.pptx"
    with zipfile.ZipFile(path, "w") as z:
        ids = "".join(f'<p:sldId id="{256 + n}" r:id="rId{n}"/>'
                      for n in range(1, len(slides) + 1))
        z.writestr("ppt/presentation.xml",
                   f"<p:presentation><p:sldIdLst>{ids}</p:sldIdLst>"
                   "</p:presentation>")
        rels = "".join(
            f'<Relationship Target="slides/slide{n}.xml" Id="rId{n}"/>'
            for n in range(1, len(slides) + 1))
        z.writestr("ppt/_rels/presentation.xml.rels",
                   f"<Relationships>{rels}</Relationships>")
        for n, body in enumerate(slides, start=1):
            z.writestr(f"ppt/slides/slide{n}.xml",
                       f"<p:sld><p:cSld>{body}</p:cSld></p:sld>")
    return path


def test_empty_slides_are_found(tmp_path):
    path = deck(tmp_path, [
        "<p:sp><a:t>Title</a:t></p:sp>",
        "<p:sp><a:t> </a:t></p:sp>",
        '<p:pic><a:blip r:embed="rId2"/></p:pic>',
        "",
    ])
    assert officecheck.empty_slides(path) == [2, 4]


def test_sheet_errors_name_the_cells(tmp_path):
    path = tmp_path / "book.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml",
                   '<workbook><sheets><sheet sheetId="1" r:id="rId1" '
                   'name="Costs"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships><Relationship Id="rId1" '
                   'Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml",
                   '<sheetData><row><c r="A1" t="s"><v>0</v></c>'
                   '<c r="B2" t="e"><f>B1/0</f><v>#DIV/0!</v></c>'
                   '<c t="e" r="C2"><v>#REF!</v></c>'
                   '<c r="D2"><v>4</v></c></row></sheetData>')
    assert officecheck.sheet_errors(path) == [
        "Costs!B2 shows #DIV/0!", "Costs!C2 shows #REF!"]


def test_pages_to_show_keep_the_ends():
    assert officecheck._which_pages(3) == [1, 2, 3]
    shown = officecheck._which_pages(40, 5)
    assert shown[0] == 1 and shown[-1] == 40 and len(shown) == 5


def test_what_is_not_an_office_file_is_refused(tmp_path):
    with pytest.raises(officecheck.CheckError, match="no file"):
        officecheck.check(tmp_path / "x.docx")
    (tmp_path / "x.txt").write_text("hi")
    with pytest.raises(officecheck.CheckError, match="not an office file"):
        officecheck.check(tmp_path / "x.txt")


@needs_office
def test_a_document_and_a_sheet_as_they_open(tmp_path, monkeypatch):
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Intro. TODO: the numbers.")
    doc.add_page_break()
    doc.add_page_break()
    doc.add_paragraph("The end.")
    doc.save(tmp_path / "report.docx")
    report = officecheck.check(tmp_path / "report.docx")
    assert report.pages == 3
    assert report.findings == ["page 2 is blank",
                               'placeholder text "TODO" on page 1']
    assert len(report.images) == 3 and report.images[0][:4] == b"\x89PNG"

    (tmp_path / "costs.csv").write_text("A,10,=B1/B2\nB,0,=B1*2\n")
    subprocess.run(  # nosec B603
        [shutil.which("soffice"),
         f"-env:UserInstallation={(tmp_path / 'p').as_uri()}",
         "--headless", "--convert-to", "xlsx", "--outdir", str(tmp_path),
         str(tmp_path / "costs.csv")],
        capture_output=True, timeout=180, check=True)
    monkeypatch.setattr(tools, "model_sees_images", lambda host, model: True)
    tools.take_pending_images()
    said = tools.check_office_file(str(tmp_path / "costs.xlsx"))
    assert "- costs!C1 shows #DIV/0!" in said
    assert "The images with this result are page 1 as they print" in said
    assert len(tools.take_pending_images()) == 1
