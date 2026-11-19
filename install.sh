#!/bin/bash
# Flash's installer for macOS and Linux:
#     curl -fsSL https://raw.githubusercontent.com/Natuworkguy/Flash/main/install.sh | bash
# Options: --dev (the dev branch), --uninstall (add --purge to delete ~/.flash too).

set -euo pipefail

# --- How it looks -------------------------------------------------------------
# Colour and a spinner in a terminal; plain lines anywhere else (a log, CI).

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
    FANCY=1
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
    ORANGE=$'\033[38;5;173m'; GREEN=$'\033[38;5;71m'; YELLOW=$'\033[38;5;179m'; RED=$'\033[38;5;167m'
else
    FANCY=""
    BOLD=""; DIM=""; RESET=""; ORANGE=""; GREEN=""; YELLOW=""; RED=""
fi

LOG="$(mktemp "${TMPDIR:-/tmp}/flash-install.XXXXXX")"
STEP=0
STEPS=8
NOTES=()   # what didn't work but needn't stop it, said again at the end

banner() {
    echo ""
    echo "  ${ORANGE}${BOLD}Flash${RESET}  ${DIM}installer${RESET}"
    echo "  ${DIM}────────────────────${RESET}"
    echo ""
}

# A step: "  ·    Installing Flash  5/8", its first mark a spinner while its
# command runs (its output kept in the log), then a tick, or a "!" and why.
step() {
    STEP=$((STEP + 1))
    CURRENT="$1"
    if [ -n "$FANCY" ]; then
        printf "  ${DIM}·${RESET}    %s  ${DIM}%d/%d${RESET}" "$CURRENT" "$STEP" "$STEPS"
    else
        printf "  %d/%d  %s" "$STEP" "$STEPS" "$CURRENT"
    fi
    echo "" >> "$LOG"
    echo "== $CURRENT" >> "$LOG"
}

done_ok() {
    local said="${1:-}"
    if [ -n "$FANCY" ]; then
        printf "\r\033[K  ${GREEN}✓${RESET}    %s" "$CURRENT"
    else
        printf " ... done"
    fi
    [ -n "$said" ] && printf "  ${DIM}%s${RESET}" "$said"
    echo ""
}

done_warn() {
    if [ -n "$FANCY" ]; then
        printf "\r\033[K  ${YELLOW}!${RESET}    %s  ${DIM}%s${RESET}\n" "$CURRENT" "$1"
    else
        printf " ... %s\n" "$1"
    fi
    NOTES+=("$1")
}

fail() {
    if [ -n "$FANCY" ]; then
        printf "\r\033[K  ${RED}✗${RESET}    %s\n" "$CURRENT"
    else
        printf " ... failed\n"
    fi
    echo ""
    echo "  ${RED}${BOLD}$1${RESET}"
    [ -n "${2:-}" ] && echo "  $2"
    if [ -s "$LOG" ]; then
        echo ""
        echo "  ${DIM}The last of what happened:${RESET}"
        tail -n 12 "$LOG" | sed "s/^/    ${DIM}/;s/$/${RESET}/"
        echo ""
        echo "  ${DIM}All of it: $LOG${RESET}"
    fi
    KEEP_LOG=1
    exit 1
}

# Runs a command with its output in the log and a spinner while it goes.
quietly() {
    if [ -z "$FANCY" ]; then
        "$@" >> "$LOG" 2>&1
        return
    fi
    "$@" >> "$LOG" 2>&1 &
    local pid=$! i=0
    local frames=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r  ${ORANGE}%s${RESET}" "${frames[i++ % 10]}"
        sleep 0.08
    done
    wait "$pid"
}

# A command that may ask for a password (sudo, Ollama's own installer): no
# spinner over the prompt, and its output kept in the log.
asking() {
    # The step's line keeps a quiet dot, not a frozen spinner, above any prompt.
    [ -n "$FANCY" ] && printf "\r  ${DIM}·${RESET}"
    echo ""
    "$@" >> "$LOG" 2>&1
}

uninstall() {
    banner
    echo "  Uninstalling Flash..."
    # flash uninstall turns off its login services, removes the flash://
    # handler and shortcut, then the package. --purge also deletes ~/.flash.
    if command -v flash >/dev/null 2>&1 && \
        flash uninstall --yes $PURGE_FLAG; then
        return
    fi
    if command -v flash >/dev/null 2>&1; then
        echo "  Removing the flash:// URL handler..."
        flash --unregister-url-scheme || true
        echo "  Removing the Flash shortcut..."
        flash --shortcut remove || true
    fi
    if command -v pipx >/dev/null 2>&1; then
        echo "  Uninstalling flash via pipx..."
        pipx uninstall flash || echo "  flash is not installed via pipx."
    else
        echo "  pipx is not installed. Cannot uninstall flash via pipx."
    fi
    if [ -n "$PURGE_FLAG" ]; then
        rm -rf "$HOME/.flash" && echo "  Deleted $HOME/.flash."
    fi
}


# --dev installs the dev branch, where changes land before a release.
# --uninstall removes Flash; add --purge to delete ~/.flash as well.
BRANCH="main"
UNINSTALL=""
PURGE_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=1 ;;
        --purge) PURGE_FLAG="--purge" ;;
        --dev) BRANCH="dev" ;;
        *) echo "Unknown option: $arg (use --dev, --uninstall or --purge)"; exit 1 ;;
    esac
done
if [ -n "$UNINSTALL" ]; then
    uninstall
    rm -f "$LOG"
    exit 0
fi

REPO_URL="https://github.com/Natuworkguy/Flash"
TEMP_DIR="$(mktemp -d)"
REPO_DIR="$TEMP_DIR/flash"
KEEP_LOG=""

cleanup() {
    rm -rf "$TEMP_DIR"
    # Kept only when something went wrong, to look at.
    if [ -z "$KEEP_LOG" ] && [ ${#NOTES[@]} -eq 0 ]; then rm -f "$LOG"; fi
}

trap cleanup EXIT INT TERM

banner
if [ "$BRANCH" = "dev" ]; then
    echo "  ${YELLOW}The dev version${RESET}: changes land here before a release."
    echo ""
fi

# --- 1. Flash's code -------------------------------------------------------------

step "Getting Flash"
quietly git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$REPO_DIR" || \
    fail "Couldn't download Flash." "Check your internet connection and try again."
[ -d "$REPO_DIR" ] || fail "Flash's download is missing after cloning."
cd "$REPO_DIR"
done_ok "$BRANCH"

# --- 2. Python -----------------------------------------------------------------------

step "Finding Python"
if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    fail "Python 3 isn't installed." "Install it from https://www.python.org/downloads/ and run this again."
fi
done_ok "$($PYTHON --version 2>&1)"

# --- 3. Ollama -------------------------------------------------------------------------

install_ollama_curl() {
    if command -v curl >/dev/null 2>&1; then
        asking sh -c 'curl -fsSL https://ollama.com/install.sh | sh'
    else
        return 1
    fi
}

# Install Ollama only when it is missing. Reinstalling over an existing
# install wipes the models and data already on the machine, so an existing
# ollama on PATH is always left exactly as it is.
step "Ollama, for models on this computer"
if command -v ollama >/dev/null 2>&1; then
    done_ok "already here"
else
    ollama_ok=""
    case "$(uname -s)" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                quietly brew install ollama && ollama_ok=1 || { install_ollama_curl && ollama_ok=1; } || true
            fi
            ;;
        Linux)
            install_ollama_curl && ollama_ok=1 || true
            ;;
    esac
    # Flash can also use a remote Ollama server or hosted models, so a
    # missing local one never stops the install.
    if [ -n "$ollama_ok" ]; then
        done_ok "installed"
    else
        done_warn "Not installed: get it from https://ollama.com/download, or use hosted models."
    fi
fi

# --- 4. pipx -------------------------------------------------------------------------------

step "pipx, to keep Flash in its own space"
if command -v pipx >/dev/null 2>&1; then
    quietly pipx ensurepath --force || true
    done_ok "already here"
else
    if command -v apt-get >/dev/null 2>&1; then
        asking sudo apt-get update || true
        asking sudo apt-get install -y pipx || fail "Couldn't install pipx with apt."
    elif command -v brew >/dev/null 2>&1; then
        quietly brew install pipx || fail "Couldn't install pipx with Homebrew."
    elif $PYTHON -m pip --version >/dev/null 2>&1; then
        quietly $PYTHON -m pip install --user pipx || fail "Couldn't install pipx with pip."
    else
        fail "Couldn't install pipx." \
            "On a Mac, install Homebrew first (https://brew.sh); or see https://pipx.pypa.io/latest/how-to/install-pipx.html"
    fi
    quietly pipx ensurepath --force || true
    done_ok "installed"
fi

# --- 5. Flash itself ------------------------------------------------------------------------

step "Installing Flash"
# Install (or reinstall, if already present) flash from this repo
quietly pipx install --force "$REPO_DIR" || fail "Couldn't install Flash." "The details are below."
PIPX_BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "$HOME/.local/bin")"
PIPX_VENVS="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || echo "")"
VENV_PYTHON="$PIPX_VENVS/flash/bin/python"
FLASH_BIN="$PIPX_BIN_DIR/flash"
if [ ! -x "$FLASH_BIN" ]; then
    FLASH_BIN="flash"
fi
FLASH_VERSION="$("$FLASH_BIN" --version 2>/dev/null || echo "Flash")"
# Which branch and commit this is, so `flash --update` keeps to the same branch.
mkdir -p "$HOME/.flash"
printf '{"branch": "%s", "commit": "%s"}\n' "$BRANCH" "$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null)" \
    > "$HOME/.flash/install.json" 2>/dev/null || true
done_ok "$FLASH_VERSION"

# --- 6. The browser for screenshots ------------------------------------------------------------

step "A browser for page screenshots"
if [ -x "$VENV_PYTHON" ] && quietly "$VENV_PYTHON" -m playwright install chromium; then
    done_ok
else
    done_warn "Screenshots stay off until it downloads (run: flash --update)."
fi

# --- 7. Voice ------------------------------------------------------------------------------------

step "Voice mode"
NEEDED_VOICE_PACKAGES="sounddevice vosk piper-tts"
# `inject` leaves a package that is already in the venv at whatever
# version it was, so the forced reinstall behind it is what actually
# moves one. `runpip` hands its arguments straight to pip, so the
# subcommand here is `install`, not `pip install`.
# shellcheck disable=SC2086
if quietly pipx inject flash $NEEDED_VOICE_PACKAGES &&
    quietly pipx runpip flash install --upgrade --force-reinstall \
        $NEEDED_VOICE_PACKAGES; then
    voice_ok=1
else
    voice_ok=""
fi
# sounddevice bundles PortAudio on macOS and Windows, but takes it from
# the system on Linux.
if [ "$(uname -s)" = "Linux" ] && command -v apt-get >/dev/null 2>&1; then
    asking sudo apt-get install -y libportaudio2 || \
        NOTES+=("Install libportaudio2 yourself if voice mode finds no microphone.")
fi
if [ -n "$voice_ok" ]; then
    done_ok "speech models download the first time you use it"
else
    done_warn "Voice mode stays off until its packages install."
fi

# --- 8. Links and the shortcut ----------------------------------------------------------------------

step "flash:// links and a Flash shortcut"
# The flash:// URL handler (unsupported on macOS, and harmless to skip
# anywhere else), and a Flash shortcut (an app in ~/Applications on a Mac,
# an applications menu entry on Linux): the web UI in the browser, started
# if it is not running. Neither ever fails the install.
links_ok=1
quietly "$FLASH_BIN" --register-url-scheme || links_ok=""
quietly "$FLASH_BIN" --shortcut || links_ok=""
if [ -n "$links_ok" ]; then
    done_ok
else
    done_warn "Not all set up; Flash works without them."
fi

# --- Done -----------------------------------------------------------------------------------------------

echo ""
echo "  ${GREEN}${BOLD}$FLASH_VERSION is installed.${RESET}"
echo ""
echo "  ${BOLD}Start it${RESET}        flash            ${DIM}in a terminal${RESET}"
echo "                  flash --web      ${DIM}in your browser${RESET}"
echo "  ${BOLD}Talk to it${RESET}      /voice on        ${DIM}inside Flash${RESET}"

case ":$PATH:" in
    *":$PIPX_BIN_DIR:"*)
        ;;
    *)
        echo ""
        echo "  ${YELLOW}Open a new terminal first${RESET}, or run this to use it in this one:"
        echo "      export PATH=\"$PIPX_BIN_DIR:\$PATH\""
        ;;
esac

if [ ${#NOTES[@]} -gt 0 ]; then
    echo ""
    echo "  ${DIM}Worth knowing:${RESET}"
    for note in "${NOTES[@]}"; do
        echo "  ${YELLOW}·${RESET} $note"
    done
    echo "  ${DIM}Details: $LOG${RESET}"
fi
echo ""
