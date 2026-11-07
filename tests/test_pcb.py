"""Tests for the boards Flash designs (pcb)."""

# pylint: disable=C0116

import io
import json
import zipfile

import pytest

from flash import pcb

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
