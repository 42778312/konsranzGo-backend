#!/usr/bin/env bash
# Copies the timetable the app ships into data/timetable.db. The server and the app must run the SAME feed:
# trip keys come from it, and a trip the app's timetable doesn't know is shown as scheduled.
#   scripts/update-timetable.sh [path to the KonstanzGo app repo]   (default: ../KonstanzGo)
set -euo pipefail
app="${1:-$(dirname "$0")/../../KonstanzGo}"
source="$app/App/Resources/Data/timetable.db"
target="$(dirname "$0")/../data/timetable.db"
[ -f "$source" ] || { echo "no timetable at $source" >&2; exit 1; }
meta() { sqlite3 "$1" "select value from meta where key='$2'"; }
echo "server: feed $(meta "$target" feed_version), valid until $(meta "$target" feed_end)"
echo "app:    feed $(meta "$source" feed_version), valid until $(meta "$source" feed_end)"
cp "$source" "$target"
echo "copied. Release a new image (tag vX.Y.Z) together with the app update that ships this feed."
