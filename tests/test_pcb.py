"""Tests for the boards Flash designs (pcb)."""

# pylint: disable=C0116

import io
import json
import zipfile

import pytest

from flash import pcb
from tests.test_web import request
from tests.test_web import server as web_server

SMALL = {
    "name": "Blinky",
    "board": {"shape": "rounded", "width": 20, "height": 14, "layers": 2},
    "parts": [
        {"ref": "U1", "package": "SOT-23-5", "value": "LDO",
         "pins": {"1": "VIN", "2": "GND", "3": "VIN", "5": "3V3"}},
        {"ref": "C1", "package": "0402", "value": "1uF",
         "pins": ["VIN", "GND"]},
        {"ref": "C2", "package": "0402", "value": "1uF",
         "pins": ["3V3", "GND"]},
        {"ref": "R1", "package": "0402", "value": "330",
         "pins": ["3V3", "LED_A"]},
        {"ref": "D1", "package": "LED-0603", "value": "green",
         "pins": {"A": "LED_A", "K": "GND"}},
        {"ref": "J1", "package": "HDR-1x2", "value": "power",
         "pins": ["VIN", "GND"]},
    ],
}


@pytest.fixture(scope="module")
def board():
    return pcb.design(SMALL)


def test_footprints_match_real_parts():
    assert len(pcb.footprint("QFN-48-6x6").pads) == 49   # and its EP
    assert len(pcb.footprint("WLCSP-49-0.4").pads) == 49
    assert {p.name for p in pcb.footprint("LED-0603").pads} == {"A", "K"}
    with pytest.raises(pcb.PcbError, match="would overlap"):
        pcb.footprint("QFN-48-6x6-0.5")
    with pytest.raises(pcb.PcbError, match="There are"):
        pcb.footprint("NOPE-3")


def test_a_wrong_pin_names_the_ones_there_are():
    bad = json.loads(json.dumps(SMALL))
    bad["parts"][0]["pins"] = {"9": "VIN"}
    with pytest.raises(pcb.PcbError, match="its pins are 1, 2, 3, 4, 5"):
        pcb.read(bad)


def test_a_small_board_is_placed_routed_and_clean(board):
    stats = board["stats"]
    assert stats["routed"] == stats["nets"]
    assert stats["errors"] == 0, board["problems"]
    assert board["pours"]["1"]    # ground poured on the bottom
    assert "clean" in pcb.summary(board)


def test_the_files_a_board_house_takes(board):
    zipped = zipfile.ZipFile(io.BytesIO(pcb.fab_zip(board)))
    names = zipped.namelist()
    for part in ("F_Cu.gbr", "B_Cu.gbr", "F_Mask.gbr", "Edge_Cuts.gbr",
                 "PTH.drl", "BOM.csv", "CPL.csv", ".kicad_pcb"):
        assert any(n.endswith(part) for n in names), part
    copper = zipped.read(next(n for n in names if n.endswith("F_Cu.gbr")))
    assert copper.startswith(b"%TF.GenerationSoftware")
    assert copper.rstrip().endswith(b"M02*")
    placements = next(n for n in names if n.endswith("CPL.csv"))
    assert b"U1" in zipped.read(placements)


def test_the_board_in_3d(board):
    assert pcb.glb(board)[:4] == b"glTF"


# --- The tool, the file, and the page's downloads --------------------------


def test_the_tool_writes_the_board_and_its_files(tmp_path, monkeypatch):
    from flash import tools

    shown = []
    monkeypatch.setattr(tools, "tool_file", lambda path: shown.append(path)
                        or True)

    said = tools.design_pcb(str(tmp_path / "blinky"), SMALL, "a blinky")

    assert shown == [str(tmp_path / "blinky.fpcb")]
    assert (tmp_path / "blinky-fab.zip").exists()
    assert (tmp_path / "blinky.kicad_pcb").read_text().startswith(
        "(kicad_pcb")
    assert pcb.load((tmp_path / "blinky.fpcb").read_text())["name"] == \
        "Blinky"
    assert "Design rule check: clean" in said
    assert "call design_pcb again" in said


def test_the_tool_says_what_is_wrong_and_writes_nothing(tmp_path):
    from flash import tools

    bad = {**SMALL, "parts": [{"ref": "U1", "package": "QFP-9000",
                               "pins": []}]}

    said = tools.design_pcb(str(tmp_path / "x.fpcb"), bad)

    assert said.startswith("Error:") and "no package" in said
    assert not (tmp_path / "x.fpcb").exists()


def test_a_board_is_kept_as_a_board(tmp_path, board):
    from flash import workspace

    path = tmp_path / "blinky.fpcb"
    path.write_text(json.dumps(board), encoding="utf-8")

    kept = workspace.keep_file(str(path))

    assert kept["kind"] == "pcb" and kept["mime"] == "application/json"


# The web tests' running server, for the page's downloads.
FIXTURES = (web_server,)


def test_the_page_downloads_its_fab_files_kicad_and_3d(
        tmp_path, board, web_server):
    from flash import workspace

    path = tmp_path / "blinky.fpcb"
    path.write_text(json.dumps(board), encoding="utf-8")
    kept = workspace.keep_file(str(path))
    base = f"/api/files/{kept['id']}/board"

    server = web_server
    status, body = request(server, "GET", base + "?format=fab")
    assert status == 200 and body[:2] == b"PK"
    status, body = request(server, "GET", base + "?format=kicad")
    assert status == 200 and body.startswith(b"(kicad_pcb")
    status, body = request(server, "GET", base + "?format=glb")
    assert status == 200 and body[:4] == b"glTF"
    assert request(server, "GET", base + "?format=exe")[0] == 400
    assert request(server, "GET", base + "?format=fab",
                   token=False)[0] in (401, 403)
