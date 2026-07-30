#!/bin/bash

set -euo pipefail

uninstall() {
    echo "Uninstalling flash..."
    if command -v pipx >/dev/null 2>&1; then
        echo "Uninstalling flash via pipx..."
        pipx uninstall flash || echo "flash is not installed via pipx."
    else
        echo "pipx is not installed. Cannot uninstall flash via pipx."
    fi
}


if [ "${1:-}" == "--uninstall" ]; then
    uninstall

    exit 0
fi

REPO_URL="https://github.com/Natuworkguy/Flash"
REPO_DIR="/tmp/flash-install"

if [ -d "$REPO_DIR" ]; then
    echo "Removing existing directory $REPO_DIR"
    rm -rf "$REPO_DIR"
else
    echo "Directory $REPO_DIR does not exist, proceeding with clone."
fi

git clone $REPO_URL "$REPO_DIR"  || {
    echo "Failed to clone repository. Please check your internet connection and try again.";
    exit 1;
}

if [ ! -d "$REPO_DIR" ]; then
    echo "Repository directory $REPO_DIR does not exist after cloning."
    exit 1
fi

cd "$REPO_DIR"


cleanup() {
    echo "Cleaning up..."
    rm -rf "$REPO_DIR"
}

trap cleanup EXIT INT TERM

detect_python() {
    if command -v python3 >/dev/null 2>&1; then
        echo "Detected python command: python3"
        PYTHON=python3
    elif command -v python >/dev/null 2>&1; then
        echo "Detected python command: python"
        PYTHON=python
    else
        echo "Python is not installed. Please install Python 3."
        exit 1
    fi
}

detect_python

if ! command -v pipx >/dev/null 2>&1; then
    echo "pipx is not installed. Installing pipx..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update || true
        if ! sudo apt-get install -y pipx; then
            echo "Failed to install pipx via apt."
            exit 1
        fi
    elif command -v brew >/dev/null 2>&1; then
        if ! brew install pipx; then
            echo "Failed to install pipx via brew."
            exit 1
        fi
    elif $PYTHON -m pip --version >/dev/null 2>&1; then
        if ! $PYTHON -m pip install --user pipx; then
            echo "Failed to install pipx via pip."
            exit 1
        fi
    else
        echo "Could not install pipx automatically."
        echo "Install it manually: https://pipx.pypa.io/latest/how-to/install-pipx.html"
        exit 1
    fi
fi

pipx ensurepath --force

# Install (or reinstall, if already present) flash from this repo
pipx install --force "$REPO_DIR"

PIPX_BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "$HOME/.local/bin")"

echo ""
echo "=== flash installed via pipx. ==="

case ":$PATH:" in
    *":$PIPX_BIN_DIR:"*)
        ;;
    *)
        echo "Refreshing this shell so $PIPX_BIN_DIR is on PATH..."
        exec "${SHELL:-bash}" -i
        ;;
esac

