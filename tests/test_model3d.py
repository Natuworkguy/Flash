# pylint: disable=C0114,C0115,C0116

import json
import math
import struct

import pytest

from flash import model3d, tools
from flash.tools import make_3d_model, send_3d_model, take_pending_images


def _volume(geo):
    """Signed volume: positive only when every face points outwards."""

    total = 0.0
    points = geo.positions
    for i in range(0, len(geo.indices), 3):
        a, b, c = (points[j] for j in geo.indices[i:i + 3])
        total += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
    return total / 6


def _read_glb(data):
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert (magic, version, length) == (b"glTF", 2, len(data))
    size, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    document = json.loads(data[20:20 + size])
    size_bin, kind_bin = struct.unpack_from("<I4s", data, 20 + size)
    assert kind_bin == b"BIN\x00"
    assert size_bin == document["buffers"][0]["byteLength"]
    return document


# --- Shapes ---------------------------------------------------------------


@pytest.mark.parametrize("spec, volume", [
    ({"shape": "box", "size": [1, 2, 3]}, 6),
    ({"shape": "sphere", "radius": 1, "segments": 96}, 4 / 3 * math.pi),
    ({"shape": "cylinder", "radius": 1, "height": 2, "segments": 96},
     2 * math.pi),
    ({"shape": "cone", "radius": 1, "height": 3, "segments": 96}, math.pi),
    ({"shape": "torus", "radius": 2, "tube": 0.5, "segments": 128},
     2 * math.pi ** 2 * 2 * 0.25),
    ({"shape": "lathe", "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
      "segments": 96}, math.pi),
    # The same lathe, written top to bottom, still faces out.
    ({"shape": "lathe", "points": [[0, 1], [1, 1], [1, 0], [0, 0]],
      "segments": 96}, math.pi),
    # An L, both ways round.
    ({"shape": "extrude", "height": 1,
      "points": [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]]}, 3),
    ({"shape": "extrude", "height": 1,
      "points": [[0, 0], [0, 2], [1, 2], [1, 1], [2, 1], [2, 0]]}, 3),
])
def test_closed_shapes_face_outwards_and_have_the_right_volume(spec, volume):
    part = model3d.Part(0, spec)

    assert _volume(part.geometry) == pytest.approx(volume, rel=0.01)


def test_normals_agree_with_the_faces_they_light():
    for spec in (
        {"shape": "box"}, {"shape": "sphere"}, {"shape": "cylinder"},
        {"shape": "cone"}, {"shape": "torus"},
        {"shape": "lathe", "points": [[0.2, 0], [0.5, 0.5], [0.1, 1]]},
    ):
        geo = model3d.Part(0, spec).geometry
        for i in range(0, len(geo.indices), 3):
            a, b, c = geo.indices[i:i + 3]
            face = model3d._cross(
                model3d._sub(geo.positions[b], geo.positions[a]),
                model3d._sub(geo.positions[c], geo.positions[a]),
            )
            normal = geo.normals[a]
            assert sum(f * n for f, n in zip(face, normal)) >= 0, spec


def test_a_mesh_takes_polygons_and_checks_its_indices():
    square = {
        "shape": "mesh",
        "vertices": [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
        "faces": [[0, 1, 2, 3]],
    }
    assert model3d.Part(0, square).geometry.triangles == 2

    square["faces"] = [[0, 1, 9]]
    with pytest.raises(model3d.ModelError, match="not in vertices"):
        model3d.Part(0, square)


@pytest.mark.parametrize("spec, message", [
    ({"shape": "pyramid"}, "shape must be one of"),
    ({"shape": "box", "size": [1, -1, 1]}, "more than 0"),
    ({"shape": "box", "size": [1, 2]}, r"\[x, y, z\]"),
    ({"shape": "sphere", "radius": "big"}, "must be a number"),
    ({"shape": "box", "color": "sort of blue"}, "hex code"),
    ({"shape": "extrude", "points": [[0, 0], [1, 1]]}, "at least 3"),
    ({"shape": "lathe", "points": [[1, 0]]}, "at least 2"),
])
def test_bad_parts_say_which_part_and_why(spec, message):
    with pytest.raises(model3d.ModelError, match=message) as caught:
        model3d.build_parts([{"shape": "box"}, spec])

    assert str(caught.value).startswith("part 2")


def test_parts_can_arrive_as_a_json_string():
    built = model3d.build_parts('[{"shape": "box"}, {"shape": "sphere"}]')

    assert [p.shape for p in built] == ["box", "sphere"]


def test_colors_take_names_and_short_hex():
    assert model3d.parse_color("#f80") == pytest.approx((1, 0x88 / 255, 0))
    assert model3d.parse_color("White") == (1, 1, 1)


# --- The file -------------------------------------------------------------


def test_glb_is_well_formed_and_places_every_part():
    built = model3d.build_parts([
        {"shape": "box", "name": "seat", "position": [0, 0.5, 0],
         "color": "red"},
        {"shape": "sphere", "position": [1, 2, 3], "rotation": [0, 90, 0],
         "scale": [1, 2, 1], "color": "red"},
        {"shape": "cone", "color": "#00f", "opacity": 0.5,
         "emissive": "#ff0"},
    ])

    data = model3d.to_glb(built, "Chair")
    document = _read_glb(data)

    assert len(data) % 4 == 0
    assert document["scenes"][0]["name"] == "Chair"
    assert [n["name"] for n in document["nodes"]] == [
        "seat", "sphere-2", "cone-3",
    ]
    assert document["nodes"][0]["translation"] == [0, 0.5, 0]
    turned = document["nodes"][1]["rotation"]
    assert turned == pytest.approx([0, math.sqrt(0.5), 0, math.sqrt(0.5)])
    assert document["nodes"][1]["scale"] == [1, 2, 1]
    # The two red parts share one material.
    assert len(document["materials"]) == 2
    see_through = document["materials"][1]
    assert see_through["alphaMode"] == "BLEND"
    assert see_through["emissiveFactor"] == pytest.approx([1, 1, 0])
    for view in document["bufferViews"]:
        assert view["byteOffset"] % 4 == 0


def test_describe_gives_the_size_and_notices_a_floating_model():
    built = model3d.build_parts([
        {"shape": "box", "size": [2, 1, 1], "position": [0, 0.5, 0]},
    ])
    text = model3d.describe(built)
    assert "1 part, 12 triangles" in text
    assert "Overall size (x, y, z): 2, 1, 1" in text
    assert "does not sit on the ground" not in text

    built = model3d.build_parts([{"shape": "box", "position": [0, 3, 0]}])
    assert "does not sit on the ground" in model3d.describe(built)


def test_the_bounds_follow_the_turn():
    built = model3d.build_parts([
        {"shape": "box", "size": [2, 1, 1], "rotation": [0, 0, 90]},
    ])

    low, high = model3d.bounds(built)

    assert high[0] - low[0] == pytest.approx(1)
    assert high[1] - low[1] == pytest.approx(2)


# --- The tools ------------------------------------------------------------


def _no_vision(monkeypatch):
    monkeypatch.setattr(tools, "model_sees_images", lambda *_: False)
    monkeypatch.setattr(tools, "_open_with_spinner", lambda path: "")


def test_make_3d_model_writes_a_glb(tmp_path, monkeypatch):
    _no_vision(monkeypatch)
    target = tmp_path / "out" / "lamp"

    result = make_3d_model(str(target), [
        {"shape": "cylinder", "radius": 0.1, "height": 0.4,
         "position": [0, 0.2, 0]},
    ], title="Lamp")

    written = tmp_path / "out" / "lamp.glb"
    assert _read_glb(written.read_bytes())["scenes"][0]["name"] == "Lamp"
    assert f"Saved {written}" in result
    assert "Overall size" in result
    assert "no vision" in result


def test_make_3d_model_writes_nothing_for_a_bad_part(tmp_path, monkeypatch):
    _no_vision(monkeypatch)
    target = tmp_path / "bad.glb"

    result = make_3d_model(str(target), [{"shape": "blob"}])

    assert result.startswith("Error: part 1")
    assert not target.exists()


def test_make_3d_model_leaves_a_file_that_is_not_a_model(
    tmp_path, monkeypatch
):
    _no_vision(monkeypatch)
    precious = tmp_path / "notes.glb"
    precious.write_text("my notes")

    result = make_3d_model(str(precious), [{"shape": "box"}])

    assert "left alone" in result
    assert precious.read_text() == "my notes"


def test_make_3d_model_attaches_a_picture_for_a_model_that_sees(
    tmp_path, monkeypatch
):
    take_pending_images()
    monkeypatch.setattr(tools, "model_sees_images", lambda *_: True)
    monkeypatch.setattr(tools, "_open_with_spinner", lambda path: "")
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    pages = []

    def capture(url, out, **_):
        pages.append(url)
        out.write_bytes(b"png bytes")
        return [], ""

    monkeypatch.setattr(tools, "capture", capture)

    result = make_3d_model(str(tmp_path / "cube.glb"), [{"shape": "box"}])

    assert "picture of it" in result
    assert take_pending_images() == [b"png bytes"]
    # The page draws the model with the web UI's own viewer.
    page = (tmp_path / "model-1.html").read_text()
    assert "FlashModelView.show" in page and "window.FlashThree" in page
    assert pages == [(tmp_path / "model-1.html").resolve().as_uri()]


def test_send_3d_model_checks_what_it_is_given(tmp_path, monkeypatch):
    _no_vision(monkeypatch)
    fake = tmp_path / "fake.glb"
    fake.write_text("not a model")
    wrong = tmp_path / "model.fbx"
    wrong.write_bytes(b"x")
    stl = tmp_path / "part.stl"
    stl.write_bytes(b"solid part\nendsolid part\n")

    assert "does not start like" in send_3d_model(str(fake))
    assert "not a .glb, .stl, or .obj" in send_3d_model(str(wrong))
    assert "no file" in send_3d_model(str(tmp_path / "gone.glb"))
    assert send_3d_model(str(stl)).startswith("Sent part.stl")


def test_the_model_tools_are_offered():
    names = {t["function"]["name"] for t in tools.tools}

    assert {"make_3d_model", "send_3d_model"} <= names
    assert tools.FUNCTIONS["make_3d_model"] is make_3d_model
    assert tools.FUNCTIONS["send_3d_model"] is send_3d_model
