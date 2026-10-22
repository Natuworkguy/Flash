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
        {"shape": "text", "text": "Hi!"},
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


def _pixels(char):
    return sum(row.count("#") for row in model3d.FONT[char])


def test_text_is_one_closed_block_to_a_pixel_of_the_font():
    part = model3d.Part(0, {"shape": "text", "text": "minecraft",
                            "height": 7, "depth": 2})
    blocks = sum(_pixels(c) for c in "MINECRAFT")

    # Closed and facing out, whatever blocks touch.
    assert _volume(part.geometry) == pytest.approx(blocks * 2)
    low = [min(p[i] for p in part.geometry.positions) for i in range(3)]
    high = [max(p[i] for p in part.geometry.positions) for i in range(3)]
    # Nine letters, a block of space between each; centred.
    widths = sum(len(model3d.FONT[c][0]) for c in "MINECRAFT") + 8
    assert high[0] - low[0] == pytest.approx(widths)
    assert high[1] - low[1] == pytest.approx(7)
    assert low[0] == pytest.approx(-high[0])
    assert (low[2], high[2]) == pytest.approx((-1, 1))


def test_touching_blocks_share_no_hidden_faces():
    part = model3d.Part(0, {"shape": "text", "text": "-"})

    # Three blocks in a row: each keeps its front, back, top and bottom
    # (4 faces, 8 triangles), and only the two ends of the run show.
    assert part.geometry.triangles == 3 * 4 + 3 * 4 + 2 * 2


def test_text_lines_stack_centred():
    one = model3d.Part(0, {"shape": "text", "text": "AB", "height": 7})
    two = model3d.Part(0, {"shape": "text", "text": "AB\nC", "height": 7})

    tall = max(p[1] for p in two.geometry.positions) - \
        min(p[1] for p in two.geometry.positions)
    assert tall == pytest.approx(7 + 2 + 7)
    assert _volume(two.geometry) == pytest.approx(
        _volume(one.geometry) + _pixels("C") * 1,
    )


def test_text_says_which_letters_the_font_lacks():
    with pytest.raises(model3d.ModelError, match=r"no '#' '@'"):
        model3d.Part(0, {"shape": "text", "text": "a@b#"})
    with pytest.raises(model3d.ModelError, match="needs text"):
        model3d.Part(0, {"shape": "text", "text": "  "})


def test_every_glyph_is_seven_rows_of_one_width():
    for char, rows in model3d.FONT.items():
        assert len(rows) == model3d.FONT_ROWS, char
        assert len({len(r) for r in rows}) == 1, char
        assert set("".join(rows)) <= {"#", "."}, char


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


# --- Motion ---------------------------------------------------------------


def _document(parts):
    return _read_glb(model3d.to_glb(model3d.build_parts(parts)))


def test_a_still_model_has_no_animations_and_one_node_a_part():
    document = _document([{"shape": "box"}, {"shape": "sphere"}])

    assert "animations" not in document  # nosec B101
    assert document["scenes"][0]["nodes"] == [0, 1]  # nosec B101


def test_a_moving_part_hangs_from_its_pivot():
    document = _document([{
        "shape": "box", "size": [0.1, 2, 1], "position": [1, 1, 0],
        "rotation": [0, 30, 0],
        "animate": {"swing": {"axis": "y", "degrees": 80},
                    "pivot": [0, 0, -0.5]},
    }])
    shape, holder = document["nodes"]

    assert document["scenes"][0]["nodes"] == [1]  # nosec B101
    assert holder["children"] == [0]  # nosec B101
    assert holder["translation"] == [1, 1, -0.5]  # nosec B101
    # At rest, the shape is where it was put: the pivot's offset undone.
    assert shape["translation"] == [0, 0, 0.5]  # nosec B101
    assert "rotation" in shape  # nosec B101
    (animation,) = document["animations"]
    assert [c["target"] for c in animation["channels"]] == [  # nosec B101
        {"node": 1, "path": "rotation"},
    ]


def test_spin_turns_a_quarter_in_a_quarter_of_its_time():
    motion = model3d.Motion({"spin": {"axis": "y", "seconds": 4}})
    _, turn, _ = motion.at(1)

    assert turn == pytest.approx(  # nosec B101
        (0, math.sin(math.pi / 4), 0, math.cos(math.pi / 4))
    )
    # A whole turn later it is back where it began (a quaternion and its
    # negative are the same turn).
    same = sum(a * b for a, b in zip(motion.at(4)[1], motion.at(0)[1]))
    assert abs(same) == pytest.approx(1)  # nosec B101


def test_steady_motions_fit_the_loop_a_whole_number_of_times():
    motion = model3d.Motion({"spin": {"seconds": 4}, "bob": {"seconds": 3}})

    assert motion.duration == 4  # nosec B101
    # Bobbing once in 4 s, not 1.33 times, so it loops without a jump.
    assert motion.at(0)[0] == pytest.approx(motion.at(4)[0])  # nosec B101


def test_keyframes_move_and_ease_from_rest():
    motion = model3d.Motion({
        "keyframes": [
            {"time": 0, "move": [0, 0, 0]},
            {"time": 2, "move": [1, 0, 0], "scale": 2},
            {"time": 4, "move": [0, 0, 0], "scale": 1},
        ],
        "ease": "linear",
    })

    assert motion.duration == 4  # nosec B101
    assert motion.at(1)[0] == pytest.approx((0.5, 0, 0))  # nosec B101
    # Scale is only given from 2 s on: before that it holds the first.
    assert motion.at(1)[2] == pytest.approx((2, 2, 2))  # nosec B101
    assert motion.at(3)[2] == pytest.approx((1.5, 1.5, 1.5))  # nosec B101
    assert (motion.moves, motion.turns, motion.scales) == (  # nosec B101
        True, False, True,
    )


def test_delay_sets_parts_out_of_step():
    left = model3d.Motion({"swing": True})
    right = model3d.Motion({"swing": True, "delay": 1})

    assert right.at(1.5)[1] == pytest.approx(left.at(0.5)[1])  # nosec B101


def test_samples_cover_the_loop_and_every_keyframe():
    motion = model3d.Motion({"keyframes": [
        {"time": 0, "turn": [0, 0, 0]}, {"time": 1.01, "turn": [0, 90, 0]},
        {"time": 2, "turn": [0, 0, 0]},
    ]})
    times = motion.times(4)

    assert times[0] == 0 and times[-1] == 2  # nosec B101
    assert 1.01 in times  # nosec B101


def test_the_glb_animation_reads_back():
    document = _document([{
        "shape": "sphere", "position": [0, 1, 0],
        "animate": {"bob": True, "pulse": True},
    }])
    (animation,) = document["animations"]
    paths = [c["target"]["path"] for c in animation["channels"]]
    clock = document["accessors"][animation["samplers"][0]["input"]]

    assert paths == ["translation", "scale"]  # nosec B101
    assert clock["min"] == [0] and clock["max"] == [2]  # nosec B101


@pytest.mark.parametrize("animate, problem", [
    ("spin", "must be an object"),
    ({"ease": "linear"}, "needs one of"),
    ({"wobble": True}, "has no wobble"),
    ({"spin": {"axis": "w"}}, "axis must be"),
    ({"spin": {"seconds": 0}}, "more than 0"),
    ({"keyframes": [{"time": 0, "move": [0, 0, 0]}]}, "at least two"),
    ({"keyframes": [{"time": 0}, {"time": 1, "move": 1}]}, "needs a move"),
    ({"spin": {"seconds": 90}}, "at most 60"),
    ({"spin": True, "ease": "bouncy"}, "ease must be"),
])
def test_bad_motion_says_what_is_wrong(animate, problem):
    with pytest.raises(model3d.ModelError, match=problem):
        model3d.build_parts([{"shape": "box", "animate": animate}])


def test_the_result_names_what_moves():
    built = model3d.build_parts([
        {"shape": "box", "name": "base"},
        {"shape": "cylinder", "name": "blade", "animate": {"spin": 2}},
    ])

    assert "1 part moves: blade (spin, looping every 2 s)" in (  # nosec B101
        model3d.describe(built)
    )
