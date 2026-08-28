#!/bin/bash

set -euo pipefail

uninstall() {
    echo "Uninstalling flash..."
    if command -v flash >/dev/null 2>&1; then
        echo "Removing the flash:// URL handler..."
        flash --unregister-url-scheme || true
    fi
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
TEMP_DIR="$(mktemp -d)"
REPO_DIR="$TEMP_DIR/flash"


git clone "$REPO_URL" "$REPO_DIR"  || {
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
    rm -rf "$TEMP_DIR"
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

# Install Ollama only when it is missing. Reinstalling over an existing
# install wipes the models and data already on the machine, so an existing
# ollama on PATH is always left exactly as it is.
install_ollama() {
    if command -v ollama >/dev/null 2>&1; then
        echo "Ollama is already installed."
        return 0
    fi

    echo "Ollama is not installed. Installing Ollama..."
    case "$(uname -s)" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                brew install ollama || {
                    echo "Failed to install Ollama via brew."
                    echo "Install it manually: https://ollama.com/download"
                    return 1
                }
            else
                echo "brew is not installed, so Ollama cannot be installed automatically."
                echo "Install it manually: https://ollama.com/download"
                return 1
            fi
            ;;
        Linux)
            if command -v curl >/dev/null 2>&1; then
                curl -fsSL https://ollama.com/install.sh | sh || {
                    echo "Failed to install Ollama."
                    echo "Install it manually: https://ollama.com/download"
                    return 1
                }
            else
                echo "curl is not installed, so Ollama cannot be installed automatically."
                echo "Install it manually: https://ollama.com/download"
                return 1
            fi
            ;;
        *)
            echo "Unrecognized platform. Install Ollama manually: https://ollama.com/download"
            return 1
            ;;
    esac
}

# Flash can also talk to a remote Ollama server, so never fail the install
# over a missing local one.
install_ollama || echo "Flash needs an Ollama server. Continuing."

if ! command -v pipx >/dev/null 2>&1; then
    echo "pipx is not installed. Installing pipx..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update || true
        if ! sudo apt-get install -y pipx; then
            echo "Failed to install pipx via apt."
            exit 1
        fi
    elif command -v brew >/dev/null 2>&1; then
        if ! brew install -y pipx; then
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
        echo "If you are on MacOS, ensure brew is installed."
        echo "Install it manually: https://pipx.pypa.io/latest/how-to/install-pipx.html"
        exit 1
    fi
fi

pipx ensurepath --force

# Install (or reinstall, if already present) flash from this repo
pipx install --force "$REPO_DIR"

PIPX_BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "$HOME/.local/bin")"

PIPX_VENVS="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || echo "")"
VENV_PYTHON="$PIPX_VENVS/flash/bin/python"

echo ""
echo "Downloading the headless browser used for page screenshots..."
if [ -x "$VENV_PYTHON" ]; then
    "$VENV_PYTHON" -m playwright install chromium || \
        echo "Download failed. Screenshots stay unavailable until it succeeds."
else
    echo "Could not find the flash environment. Screenshots need chromium."
fi

# Register the flash:// URL handler. Unsupported on macOS, and harmless to
# skip anywhere else, so never fail the install over it.
FLASH_BIN="$PIPX_BIN_DIR/flash"
if [ ! -x "$FLASH_BIN" ]; then
    FLASH_BIN="flash"
fi

echo ""
echo "Registering the flash:// URL handler..."
"$FLASH_BIN" --register-url-scheme || \
    echo "Unable to install flash:// URL handler. Continuing."
FLASH_VERSION="$(flash --version)"

echo ""
echo "=== $FLASH_VERSION installed via pipx. ==="

case ":$PATH:" in
    *":$PIPX_BIN_DIR:"*)
        ;;
    *)
        echo "$PIPX_BIN_DIR is not on your PATH yet."
        echo "Run this in your current shell, or open a new terminal:"
        echo "    export PATH=\"$PIPX_BIN_DIR:\$PATH\""
        ;;
esac

