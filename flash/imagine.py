"""Pictures made and changed on this computer.

Ollama runs image models as it runs chat models (Z-Image Turbo, FLUX.2
Klein and the like, pulled with `ollama pull x/z-image-turbo`): a prompt
in, a picture out, and with a picture given as well, that picture changed
as the prompt says ("make it night", "the same character, now waving").
Two everyday jobs need no model at all: cutting the background away
(rembg, when installed) and making a picture bigger (resampled with
Pillow, sharpened a little).

Every picture is saved under ~/.flash/images with a name from its prompt,
and never over an earlier one.
"""

from __future__ import annotations

import base64
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

SIZES = {"square": (1024, 1024), "portrait": (832, 1216), "landscape": (1216, 832), "wide": (1344, 768),
         "story": (768, 1344), "banner": (1536, 640)}
KNOWN = ("z-image", "flux", "sdxl", "stable-diffusion", "qwen-image", "hidream", "image-turbo", "sana")
SETTING = "FLASH_IMAGE_MODEL"
MOST = 4


class ImageError(Exception):
    pass


def folder() -> Path:
    from . import paths

    d = paths.FLASH_DIR / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _name(prompt: str, suffix: str = ".png") -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:48] or "image"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder() / f"{slug}-{stamp}{suffix}"
    n = 2
    while path.exists():
        path = folder() / f"{slug}-{stamp}-{n}{suffix}"
        n += 1
    return path


def size_of(size: str) -> tuple[int, int]:
    """'square', 'portrait', ... or '1024x768', as (width, height), each a multiple of 16 from 256 to 2048."""

    size = str(size or "square").strip().lower()
    if size in SIZES:
        return SIZES[size]
    m = re.fullmatch(r"(\d{3,4})\s*[x×*]\s*(\d{3,4})", size)
    if not m:
        raise ImageError("size is square, portrait, landscape, wide, story, banner, or WIDTHxHEIGHT")
    fit = lambda v: max(256, min(2048, int(v) // 16 * 16))  # noqa: E731
    return fit(m.group(1)), fit(m.group(2))


def models(client: Any) -> list[str]:
    """The image models Ollama has: the one set in FLASH_IMAGE_MODEL first."""

    try:
        listed = client.list()
        names = [getattr(m, "model", None) or m.get("model") or m.get("name") for m in (getattr(listed, "models", None) or listed.get("models", []))]
    except Exception:  # noqa: BLE001  (no server, or an old one)
        names = []
    found = [n for n in names if n and any(k in n.lower() for k in KNOWN)]
    chosen = os.environ.get(SETTING, "").strip()
    if chosen:
        found = [chosen] + [n for n in found if n != chosen]
    return found


def make(prompt: str, size: str = "square", steps: Optional[int] = None, model: str = "", edit: str = "",
         count: int = 1, client: Any = None, progress: Optional[Callable[[str], None]] = None) -> list[Path]:
    """COUNT pictures of PROMPT (or EDIT, a picture's path, changed as PROMPT says), saved; their paths."""

    prompt = " ".join(str(prompt or "").split())
    if not prompt:
        raise ImageError("say what to make")
    if client is None:
        from . import ai

        client = ai._client()  # pylint: disable=W0212
    model = model or next(iter(models(client)), "")
    if not model:
        raise ImageError("no image model in Ollama yet: pull one, e.g. `ollama pull x/z-image-turbo` (fast) or "
                         "`ollama pull x/flux2-klein` (edits pictures too), then ask again")
    width, height = size_of(size)
    source = None
    if edit:
        source = Path(os.path.expanduser(edit))
        if not source.is_file():
            raise ImageError(f"no picture at {source}")
    say = progress or (lambda _: None)
    made: list[Path] = []
    for n in range(max(1, min(int(count or 1), MOST))):
        image = ""
        try:
            parts = client.generate(model=model, prompt=prompt, width=width, height=height, steps=steps,
                                    images=[str(source)] if source else None, stream=True)
            for part in parts:
                done, total = getattr(part, "completed", None), getattr(part, "total", None)
                if done and total and (done == total or done % max(1, total // 4) == 0):
                    say(f"picture {n + 1}: step {done} of {total}")
                image = getattr(part, "image", None) or image
        except Exception as exc:  # noqa: BLE001
            raise ImageError(f"{model} could not make it: {exc}") from None
        if not image:
            raise ImageError(f"{model} sent no picture back; is it an image model?")
        path = _name(prompt)
        path.write_bytes(base64.b64decode(image))
        made.append(path)
    return made


def remove_background(path: str) -> Path:
    """PATH with its background cut away, as a PNG with transparency."""

    source = Path(os.path.expanduser(path))
    if not source.is_file():
        raise ImageError(f"no picture at {source}")
    try:
        from rembg import remove  # type: ignore
    except ImportError:
        raise ImageError("cutting out backgrounds needs rembg: pip install rembg (about 170 MB of model, once)") from None
    out = _name(source.stem + " cut out")
    out.write_bytes(remove(source.read_bytes()))
    return out


def upscale(path: str, factor: int = 2) -> Path:
    """PATH made FACTOR times bigger (2 to 4), resampled and sharpened a little."""

    from PIL import Image, ImageFilter

    source = Path(os.path.expanduser(path))
    if not source.is_file():
        raise ImageError(f"no picture at {source}")
    factor = max(2, min(int(factor or 2), 4))
    with Image.open(source) as im:
        big = im.resize((im.width * factor, im.height * factor), Image.Resampling.LANCZOS)
        big = big.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
        out = _name(f"{source.stem} x{factor}")
        big.save(out)
    return out
