#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$ROOT_DIR/.git" ] && ! git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not a git repository: $ROOT_DIR" >&2
  exit 1
fi

git -C "$ROOT_DIR" config core.hooksPath .githooks
echo "Installed development hooks: core.hooksPath=.githooks"
