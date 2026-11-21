# The setup window, for an installer in bash: sourced, then
#     window_open PRODUCT ACCENT PYTHON "Step one" "Step two" ...   opens it (returns 1 when it can't)
#     window_set INDEX STATE [NOTE]      a step, from 0: wait, run, ok, warn, ask (needs the terminal) or fail
#     window_done NOTE COMMAND...        all done, and what its Open button starts
#     window_fail WHY                    it stopped
# The page (index.html beside this file) reads status.json, which these write; serve.py serves both on this
# computer only. It opens as a window of its own in Chrome, Edge, Brave or Chromium (app mode), else in the
# default browser. Nothing here ever stops an install: when the window can't open, the installer carries on
# in the terminal. WINDOW is set while the window is open.

WINDOW=""
WINDOW_PRODUCT=""
WINDOW_ACCENT=""
WINDOW_TITLES=()
WINDOW_STATES=()
WINDOW_NOTES=()
WINDOW_DONE=""
WINDOW_DONE_NOTE=""
WINDOW_FAILED=""

_window_json() {  # a JSON string
    local s="$1"
    s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/ }"; s="${s//$'\t'/ }"
    printf '"%s"' "$s"
}

_window_write() {
    [ -n "$WINDOW" ] || return 0
    local i out
    out="{\"product\": $(_window_json "$WINDOW_PRODUCT"), \"accent\": $(_window_json "$WINDOW_ACCENT"), \"steps\": ["
    for i in "${!WINDOW_TITLES[@]}"; do
        [ "$i" -gt 0 ] && out+=", "
        out+="{\"title\": $(_window_json "${WINDOW_TITLES[$i]}"), \"state\": $(_window_json "${WINDOW_STATES[$i]}"), \"note\": $(_window_json "${WINDOW_NOTES[$i]}")}"
    done
    out+="], \"done\": ${WINDOW_DONE:-false}, \"doneNote\": $(_window_json "$WINDOW_DONE_NOTE")"
    [ -n "$WINDOW_FAILED" ] && out+=", \"failed\": $(_window_json "$WINDOW_FAILED")"
    out+="}"
    printf '%s\n' "$out" > "$WINDOW/status.tmp" && mv -f "$WINDOW/status.tmp" "$WINDOW/status.json"
}

window_open() {
    local product="$1" accent="$2" python="$3" kit
    shift 3
    kit="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [ -t 1 ] || return 1
    [ -z "${XN_NO_WINDOW:-}" ] || return 1
    case "$(uname -s)" in
        Darwin) ;;
        *) [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || return 1 ;;
    esac
    [ -f "$kit/index.html" ] || return 1
    WINDOW="$(mktemp -d "${TMPDIR:-/tmp}/setup-window.XXXXXX")" || { WINDOW=""; return 1; }
    cp "$kit/index.html" "$kit/serve.py" "$kit/film.mp4" "$WINDOW/" 2>/dev/null || { WINDOW=""; return 1; }
    WINDOW_PRODUCT="$product"; WINDOW_ACCENT="$accent"
    WINDOW_TITLES=("$@"); WINDOW_STATES=(); WINDOW_NOTES=()
    local i
    for i in "${!WINDOW_TITLES[@]}"; do WINDOW_STATES[$i]="wait"; WINDOW_NOTES[$i]=""; done
    _window_write
    # The server outlives the installer, so the page can still say it's done, and start the app.
    nohup "$python" "$WINDOW/serve.py" "$WINDOW" > "$WINDOW/port" 2>/dev/null &
    local tries=0 port=""
    while [ $tries -lt 40 ]; do
        port="$(head -n 1 "$WINDOW/port" 2>/dev/null || true)"
        [ -n "$port" ] && break
        sleep 0.1; tries=$((tries + 1))
    done
    [ -n "$port" ] || { WINDOW=""; return 1; }
    local url="http://127.0.0.1:$port/" app flags
    flags=(--app="$url" --window-size=980,640 --user-data-dir="$WINDOW/profile" --no-first-run --no-default-browser-check)
    case "$(uname -s)" in
        Darwin)
            for app in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
                if [ -d "/Applications/$app.app" ] || [ -d "$HOME/Applications/$app.app" ]; then
                    open -na "$app" --args "${flags[@]}" >/dev/null 2>&1 && return 0
                fi
            done
            open "$url" >/dev/null 2>&1 && return 0
            ;;
        *)
            for app in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge brave-browser; do
                if command -v "$app" >/dev/null 2>&1; then
                    nohup "$app" "${flags[@]}" >/dev/null 2>&1 &
                    return 0
                fi
            done
            command -v xdg-open >/dev/null 2>&1 && xdg-open "$url" >/dev/null 2>&1 && return 0
            ;;
    esac
    WINDOW=""
    return 1
}

window_set() {
    WINDOW_STATES[$1]="$2"
    WINDOW_NOTES[$1]="${3:-}"
    _window_write
}

window_done() {
    WINDOW_DONE_NOTE="$1"
    shift
    if [ -n "$WINDOW" ]; then
        local arg list=""
        for arg in "$@"; do list+="${list:+, }$(_window_json "$arg")"; done
        printf '{"cmd": [%s]}\n' "$list" > "$WINDOW/open.json"
    fi
    WINDOW_DONE=true
    _window_write
}

window_fail() {
    WINDOW_FAILED="$1"
    _window_write
}
