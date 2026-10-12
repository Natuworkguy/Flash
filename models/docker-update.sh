#!/bin/sh
# Pull the newest Flash Onyx 2.6 and build it inside a Docker container
# that runs Ollama. Safe to run on a timer: the base model is pulled
# again (a no-op when it is current) and the model is rebuilt from the
# newest Modelfile on the branch, which reuses every layer it already
# has.
#
#   models/docker-update.sh              # 12b, in the container "ollama"
#   models/docker-update.sh 12b 31b      # both sizes
#
#   OLLAMA_CONTAINER  the container's name (default: ollama)
#   FLASH_BRANCH      the branch to take the Modelfile from (default: dev)
#
# Every night at 4, from cron:
#   0 4 * * * /path/to/Flash/models/docker-update.sh >> ~/onyx-update.log 2>&1

set -eu

CONTAINER="${OLLAMA_CONTAINER:-ollama}"
BRANCH="${FLASH_BRANCH:-dev}"
NAME="flash-onyx-2.6"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ $# -gt 0 ] || set -- 12b

docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null \
    | grep -q true || { echo "container $CONTAINER is not running" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The newest Modelfile, without touching the checkout.
git -C "$REPO" fetch --quiet origin "$BRANCH"
git -C "$REPO" show "origin/$BRANCH:models/$NAME.Modelfile" \
    > "$WORK/$NAME.Modelfile"

for size in "$@"; do
    tag="$NAME:$size"
    python3 "$REPO/models/build.py" "$WORK/$NAME.Modelfile" \
        --print --size "$size" > "$WORK/$size.Modelfile"
    base="$(sed -n 's/^FROM[[:space:]]\{1,\}//p' "$WORK/$size.Modelfile")"

    echo "$(date '+%F %T') $tag: pulling $base"
    docker exec "$CONTAINER" ollama pull "$base"

    echo "$(date '+%F %T') $tag: building"
    docker cp "$WORK/$size.Modelfile" "$CONTAINER:/tmp/$NAME-$size.Modelfile"
    docker exec "$CONTAINER" ollama create "$tag" \
        -f "/tmp/$NAME-$size.Modelfile"
    docker exec "$CONTAINER" rm -f "/tmp/$NAME-$size.Modelfile"
    echo "$(date '+%F %T') $tag: up to date"
done
