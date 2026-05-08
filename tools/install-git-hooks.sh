#!/usr/bin/env bash
# One-shot installer for the repo's git hooks. Run from any directory
# inside the repo. Idempotent — safe to re-run.
set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
SRC="$REPO_ROOT/tools/git-pre-commit"
DST="$REPO_ROOT/.git/hooks/pre-commit"

if [ ! -f "$SRC" ]; then
    echo "install-git-hooks: source missing: $SRC" >&2
    exit 1
fi

cp "$SRC" "$DST"
chmod +x "$DST"
echo "Installed $DST"
