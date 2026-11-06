"""Shared filesystem paths for Flash CLI."""

import os
import time
from pathlib import Path

ENV_PATH = str(Path.home() / ".flash.env")

# Everything Flash keeps between sessions that is too big for a
# dotfile: speech models, background scenes.
FLASH_DIR = Path.home() / ".flash"

# Voice mode's speech models are tens of megabytes each, so they live in
# a directory of their own rather than beside the dotfiles.
MODELS_DIR = Path.home() / ".flash" / "models"


def replace(source, target) -> None:
    """os.replace, made to land on Windows too: there a file another
    thread has open for a moment (reading the chat being saved) refuses
    to be replaced, so it is tried again for up to about a second."""

    for _ in range(40):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(0.025)
    os.replace(source, target)
