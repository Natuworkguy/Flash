"""Shared filesystem paths for Flash CLI."""

from pathlib import Path

ENV_PATH = str(Path.home() / ".flash.env")

# Everything Flash keeps between sessions that is too big for a
# dotfile: speech models, background scenes.
FLASH_DIR = Path.home() / ".flash"

# Voice mode's speech models are tens of megabytes each, so they live in
# a directory of their own rather than beside the dotfiles.
MODELS_DIR = Path.home() / ".flash" / "models"
