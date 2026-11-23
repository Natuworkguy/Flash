# The setup window, for an installer in bash: sourced, then
#     window_open PRODUCT ACCENT PYTHON "Step one" "Step two" ...   opens it (returns 1 when it can't)
#     window_set INDEX STATE [NOTE]      a step, from 0: wait, run, ok, warn, ask (needs the terminal) or fail
#     window_done NOTE COMMAND...        all done, and what its Open button starts
#     window_fail WHY                    it stopped
# The installer app (installer/mac: a native window around index.html; installer/windows: a native form) sets
# SETUP_WINDOW_DIR and reads the status.json these write there. Without it (the installer run in a terminal),
# nothing here does anything and the steps show in the terminal. WINDOW is set while the app is showing them.

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
    # Only for the installer app (installer/mac, installer/windows), which shows the steps in a window of its own
    # and says where to write them in SETUP_WINDOW_DIR. Run in a terminal, the installer shows its steps there.
    local product="$1" accent="$2"
    shift 3
    [ -n "${SETUP_WINDOW_DIR:-}" ] || return 1
    [ -z "${XN_NO_WINDOW:-}" ] || return 1
    mkdir -p "$SETUP_WINDOW_DIR" 2>/dev/null || return 1
    WINDOW="$SETUP_WINDOW_DIR"
    WINDOW_PRODUCT="$product"; WINDOW_ACCENT="$accent"
    WINDOW_TITLES=("$@"); WINDOW_STATES=(); WINDOW_NOTES=()
    local i
    for i in "${!WINDOW_TITLES[@]}"; do WINDOW_STATES[$i]="wait"; WINDOW_NOTES[$i]=""; done
    _window_write
    return 0
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
