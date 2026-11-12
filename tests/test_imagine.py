"""Pictures: made by an image model in Ollama, edited, cut out and made bigger."""

import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from flash import imagine, tools


def png(w=8, h=8, colour=(200, 40, 40)):
    out = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(out, "PNG")
    return out.getvalue()


class Ollama:
    def __init__(self, names=("x/z-image-turbo:latest", "gemma3:12b")):
        self.names, self.asked = names, []

    def list(self):
        return SimpleNamespace(models=[SimpleNamespace(model=n) for n in self.names])

    def generate(self, **kw):
        self.asked.append(kw)
        yield SimpleNamespace(completed=4, total=8, image=None)
        yield SimpleNamespace(completed=8, total=8, image=base64.b64encode(png()).decode())


@pytest.fixture(autouse=True)
def where(monkeypatch, tmp_path):
    monkeypatch.setattr(imagine, "folder", lambda: tmp_path)
    return tmp_path


def test_a_picture_is_made_by_the_image_model_and_saved():
    client = Ollama()
    made = imagine.make("A red fox in snow at dawn", "portrait", client=client, count=2)
    assert len(made) == 2 and made[0] != made[1] and made[0].name.startswith("a-red-fox-in-snow-at-dawn")
    assert client.asked[0]["model"] == "x/z-image-turbo:latest" and (client.asked[0]["width"], client.asked[0]["height"]) == (832, 1216)
    assert Image.open(made[0]).size == (8, 8)


def test_an_edit_sends_the_picture_along(where):
    client = Ollama()
    src = where / "fox.png"
    src.write_bytes(png())
    imagine.make("make it night", edit=str(src), client=client)
    assert client.asked[0]["images"] == [str(src)]


def test_no_image_model_says_how_to_get_one():
    with pytest.raises(imagine.ImageError, match="ollama pull"):
        imagine.make("a fox", client=Ollama(names=("gemma3:12b",)))


def test_sizes():
    assert imagine.size_of("wide") == (1344, 768)
    assert imagine.size_of("1000x700") == (992, 688)
    with pytest.raises(imagine.ImageError):
        imagine.size_of("huge")


def test_upscale_makes_it_bigger(where):
    src = where / "small.png"
    src.write_bytes(png(10, 6))
    assert Image.open(imagine.upscale(str(src), 3)).size == (30, 18)


def test_the_tool_says_errors_plainly():
    assert tools.image_tool("edit", prompt="night").startswith("Error: edit needs the picture's path")
    assert tools.image_tool("paint").startswith("Error: action is")
