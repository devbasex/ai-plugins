#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(pwd)"
TARGET_MCP="$PROJECT_ROOT/.mcp.json"
SOURCE_MCP="$SCRIPT_DIR/.mcp.json"
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: bash install.sh [--dry-run]

Install this MCP plugin into the current project's .mcp.json.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ ! -f "$SOURCE_MCP" ]; then
  echo "ERROR: source .mcp.json not found: $SOURCE_MCP" >&2
  exit 1
fi

if [ "$DRY_RUN" = true ]; then
  echo "Would merge $SOURCE_MCP into $TARGET_MCP"
  exit 0
fi

python3 - "$SOURCE_MCP" "$TARGET_MCP" <<'PY'
import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
source = json.loads(source_path.read_text())
target = json.loads(target_path.read_text()) if target_path.exists() else {}
source_servers = source.get("mcpServers", {})
target.setdefault("mcpServers", {})
target["mcpServers"].update(source_servers)
target_path.write_text(json.dumps(target, indent=2, ensure_ascii=False) + "\n")
print(f"Installed MCP servers: {', '.join(sorted(source_servers))}")
print(f"Updated: {target_path}")
PY
