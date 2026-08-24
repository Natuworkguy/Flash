"""Shared image handling for Flash CLI.

One place for what counts as an image and for the checks a file has to
pass before it is attached to a message, so `/image` and the view_image
tool accept exactly the same files and reject them for the same reasons.
"""

from pathlib import Path
from typing import Union

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# Ollama base64-encodes the whole file into the request body, so an
# oversized image costs a lot of memory and time for no extra detail.
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def resolve_image_path(
    path: str,
) -> tuple[Union[Path, None], str]:  # noqa: UP007, RUF100
    """Expand and validate `path` as a local image file.

    Returns `(path, "")` when it can be sent to the model, or
    `(None, reason)` explaining why it cannot.
    """

    image_path = Path(path).expanduser()

    if image_path.is_dir():
        return None, f"{image_path} is a directory, not an image file."

    if not image_path.is_file():
        return None, f"Image not found: {image_path}"

    if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None, (
            f"Unsupported image type '{image_path.suffix}'. Supported: "
            + ", ".join(sorted(IMAGE_EXTENSIONS))
        )

    size = image_path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        return None, (
            f"Image is too large ({size / 1048576:.1f} MB). "
            f"The limit is {MAX_IMAGE_BYTES // 1048576} MB."
        )

    return image_path, ""
