# pylint: disable=C0114,C0115,C0116

import json
import struct
import zlib

import pytest

from flash import model3d, textures


def _png_pixels(data):
    """A PNG this module wrote, as its size and rows of RGB tuples."""

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    at, idat = 8, b""
    while at < len(data):
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        if kind == b"IDAT":
            idat += data[at + 8:at + 8 + size]
        at += 12 + size
    raw = zlib.decompress(idat)
    stride = 1 + width * 3
    rows = []
    for y in range(height):
        line = raw[y * stride + 1:(y + 1) * stride]
        rows.append([tuple(line[x * 3:x * 3 + 3]) for x in range(width)])
    return width, height, rows


def _glb(parts):
    data = model3d.to_glb(model3d.build_parts(parts))
    size = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + size])


@pytest.mark.parametrize("name", textures.PATTERNS)
def test_every_pattern_is_a_square_png(name):
    color = (0.6, 0.4, 0.2)
    width, height, rows = _png_pixels(
        textures.draw(name, color, textures.second_color(name, color))
    )

    assert width == height == textures.SIZE
    # Two colours make some difference across the tile.
    assert len({p for row in rows for p in row}) > 1


@pytest.mark.parametrize("name", ["wood", "marble", "noise", "grass"])
def test_noisy_patterns_tile_without_a_seam(name):
    color = (0.6, 0.4, 0.2)
    _, _, rows = _png_pixels(
        textures.draw(name, color, textures.second_color(name, color))
    )

    def gap(a, b):
        return sum(abs(x - y) for x, y in zip(a, b))

    # The jump from the last column to the first, where the tile repeats,
    # is no bigger than an ordinary step between neighbours.
    seam = sum(gap(row[-1], row[0]) for row in rows) / len(rows)
    step = sum(gap(row[10], row[11]) for row in rows) / len(rows)
    assert seam < step * 3 + 6


def test_an_unknown_pattern_says_which_there_are():
    with pytest.raises(textures.TextureError, match="brick"):
        textures.draw("plaid", (1, 1, 1), (0, 0, 0))


def test_images_must_be_png_or_jpeg(tmp_path):
    picture = tmp_path / "logo.png"
    picture.write_bytes(textures.png(1, 1, b"\x00\xff\x00\x00"))
    assert textures.load_image(picture)[1] == "image/png"

    fake = tmp_path / "logo.jpg"
    fake.write_text("not a picture")
    with pytest.raises(textures.TextureError, match="not a PNG"):
        textures.load_image(fake)
    with pytest.raises(textures.TextureError, match="not a file"):
        textures.load_image(tmp_path / "missing.png")


# --- On a model --------------------------------------------------------------


def test_a_pattern_paints_the_part():
    document = _glb([
        {"shape": "box", "size": [2, 1, 1], "color": "#a0632f",
         "texture": "wood"},
    ])

    primitive = document["meshes"][0]["primitives"][0]
    assert "TEXCOORD_0" in primitive["attributes"]
    material = document["materials"][0]["pbrMetallicRoughness"]
    assert material["baseColorTexture"] == {"index": 0}
    # The pattern carries the colour; the factor does not darken it again.
    assert material["baseColorFactor"][:3] == [1.0, 1.0, 1.0]
    assert document["images"][0]["mimeType"] == "image/png"
    assert document["samplers"][0]["wrapS"] == 10497
    assert "target" not in document["bufferViews"][
        document["images"][0]["bufferView"]
    ]


def test_parts_without_a_texture_are_as_before():
    document = _glb([{"shape": "box"}])

    assert "TEXCOORD_0" not in document["meshes"][0]["primitives"][0][
        "attributes"
    ]
    assert "images" not in document and "textures" not in document


def test_one_texture_is_shared_by_parts_that_match():
    leg = {"shape": "cylinder", "radius": 0.03, "color": "#a0632f",
           "texture": "wood"}
    document = _glb([leg, leg, {**leg, "texture": "brick"}])

    assert len(document["images"]) == 2
    assert len(document["materials"]) == 2


def test_a_pattern_takes_a_second_colour_and_a_scale():
    built = model3d.build_parts([{
        "shape": "box", "size": [2, 2, 2], "color": "#9c3b26",
        "texture": {"pattern": "brick", "color2": "white", "scale": 0.5},
    }])

    texture = built[0].texture
    assert texture.scale == 0.5
    us = [u for u, _ in built[0].texture_coordinates()]
    # Two metres over half a metre a tile: four repeats across.
    assert max(us) - min(us) == pytest.approx(4)


def test_a_picture_fits_the_largest_face(tmp_path):
    picture = tmp_path / "poster.png"
    picture.write_bytes(textures.png(1, 1, b"\x00\xff\x00\x00"))
    built = model3d.build_parts([{
        "shape": "box", "size": [1.6, 0.9, 0.02], "texture": str(picture),
    }])

    coords = built[0].texture_coordinates()
    assert min(u for u, _ in coords) == pytest.approx(0)
    assert max(u for u, _ in coords) == pytest.approx(1)
    assert max(v for _, v in coords) == pytest.approx(1)
    # Shown as it is: no colour was named to tint it.
    assert not built[0].texture.tinted


def test_a_face_reads_the_right_way_up():
    # On the front, u runs right and v runs down, as an image's rows do.
    assert model3d._box_uv((-1, 1, 0), (0, 0, 1)) == (-1, -1)
    assert model3d._box_uv((1, -1, 0), (0, 0, 1)) == (1, 1)


@pytest.mark.parametrize("texture, message", [
    ("plaid", "not one of"),
    ({"pattern": "wood", "scale": -1}, "scale"),
    ("/no/such/picture.png", "not a file"),
    (7, "pattern's name"),
])
def test_bad_textures_say_why(texture, message):
    with pytest.raises(model3d.ModelError, match=message):
        model3d.build_parts([{"shape": "box", "texture": texture}])


@pytest.mark.parametrize("shape", [
    {"shape": "sphere"}, {"shape": "cylinder"}, {"shape": "torus"},
    {"shape": "lathe", "points": [[0, 0], [0.3, 0.2], [0.1, 0.6]]},
    {"shape": "extrude", "points": [[0, 0], [1, 0], [0, 1]]},
    {"shape": "text", "text": "HI"},
])
def test_every_shape_takes_a_texture(shape):
    document = _glb([{**shape, "texture": "checker"}])

    accessors = document["accessors"]
    attributes = document["meshes"][0]["primitives"][0]["attributes"]
    assert accessors[attributes["TEXCOORD_0"]]["count"] == \
        accessors[attributes["POSITION"]]["count"]
