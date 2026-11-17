#!/usr/bin/env bash
# The app keeps a copy of KGLiveAPI (Packages/KGLiveAPI). This repo's copy is the original: change it here first,
# then copy it over. Fails when the two differ.
#   scripts/check-api.sh [path to the KonstanzGo app repo]   (default: ../KonstanzGo)
set -euo pipefail
app="${1:-$(dirname "$0")/../../KonstanzGo}"
here="$(dirname "$0")/../KGLiveAPI"
if diff -r -x .build -x .swiftpm -x .DS_Store "$here" "$app/Packages/KGLiveAPI"; then
  echo "KGLiveAPI is the same in both repos."
else
  echo "KGLiveAPI differs. Copy it to the app: rsync -a --delete --exclude .build --exclude .swiftpm $here/ $app/Packages/KGLiveAPI/" >&2
  exit 1
fi
