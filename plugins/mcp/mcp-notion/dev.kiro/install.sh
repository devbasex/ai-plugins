#!/usr/bin/env bash
# MCP Plugin Installer for Kiro CLI
# Usage: bash plugins/mcp/<プラグイン名>/dev.kiro/install.sh [--project PATH] [--dry-run]
#
# scripts/build-runtime-plugins.sh が生成します。直接編集しないでください。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_NAME="$(basename "$PLUGIN_DIR")"
PROJECT_ROOT="$(pwd)"
KIRO_DIR="$PROJECT_ROOT/.kiro"
KIRO_AGENT_FILE="$KIRO_DIR/agents/default.json"
KIRO_PLUGIN_LINK="$KIRO_DIR/mcp_runtime/$PLUGIN_NAME"
KIRO_SKILLS_DIR="$KIRO_DIR/skills"
TARGET_MCP="$PROJECT_ROOT/.mcp.json"
SOURCE_MCP="$PLUGIN_DIR/.mcp.json"
SOURCE_HOOKS="$PLUGIN_DIR/hooks/hooks.json"
SOURCE_SKILLS="$PLUGIN_DIR/skills"
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: bash install.sh [--project PATH] [--dry-run]

Install this MCP plugin into the current project's Kiro settings.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --project)
      [ "$#" -ge 2 ] || { echo "ERROR: --project requires a path" >&2; exit 2; }
      PROJECT_ROOT="$(cd "$2" && pwd)"
      KIRO_DIR="$PROJECT_ROOT/.kiro"
      KIRO_AGENT_FILE="$KIRO_DIR/agents/default.json"
      KIRO_PLUGIN_LINK="$KIRO_DIR/mcp_runtime/$PLUGIN_NAME"
      KIRO_SKILLS_DIR="$KIRO_DIR/skills"
      TARGET_MCP="$PROJECT_ROOT/.mcp.json"
      shift
      ;;
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
  echo "Would link plugin runtime into $KIRO_PLUGIN_LINK"
  if [ -d "$SOURCE_SKILLS" ]; then
    echo "Would link skills from $SOURCE_SKILLS into $KIRO_SKILLS_DIR"
  fi
  if [ -f "$SOURCE_HOOKS" ]; then
    echo "Would merge hooks from $SOURCE_HOOKS into $KIRO_AGENT_FILE"
  fi
  exit 0
fi

mkdir -p "$KIRO_DIR/mcp_runtime" "$KIRO_SKILLS_DIR" "$(dirname "$KIRO_AGENT_FILE")"
if [ -e "$KIRO_PLUGIN_LINK" ] && [ ! -L "$KIRO_PLUGIN_LINK" ]; then
  echo "ERROR: $KIRO_PLUGIN_LINK already exists and is not a symlink" >&2
  exit 1
fi
ln -sfn "$PLUGIN_DIR" "$KIRO_PLUGIN_LINK"

if [ -d "$SOURCE_SKILLS" ]; then
  find "$SOURCE_SKILLS" -mindepth 1 -maxdepth 1 -type d | sort | while IFS= read -r skill_dir; do
    skill_name="$(basename "$skill_dir")"
    skill_link="$KIRO_SKILLS_DIR/$skill_name"
    [ -f "$skill_dir/SKILL.md" ] || continue
    if [ -e "$skill_link" ] && [ ! -L "$skill_link" ]; then
      echo "ERROR: $skill_link already exists and is not a symlink" >&2
      exit 1
    fi
    ln -sfn "$skill_dir" "$skill_link"
    echo "Linked Kiro skill: $skill_name"
  done
fi

python3 - "$SOURCE_MCP" "$TARGET_MCP" "$SOURCE_HOOKS" "$KIRO_AGENT_FILE" "$KIRO_PLUGIN_LINK" <<'PY'
import json
import sys
from pathlib import Path

EVENT_MAP = {
    "SessionStart": "agentSpawn",
    "Stop": "stop",
}
# Kiro にはプラグインルートを示す環境変数が無い。hook の command が使う
# プレースホルダを、installer が張った symlink の絶対パスへ置き換える。
PLACEHOLDER = "${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}}"


def to_entry(hook, plugin_link):
    command = hook.get("command")
    if not command:
        return None
    entry = {"command": command.replace(PLACEHOLDER, plugin_link)}
    if "timeout_ms" in hook:
        entry["timeout_ms"] = hook["timeout_ms"]
    return entry


def merge_hooks(agent, source_hooks, plugin_link):
    target_hooks = agent.setdefault("hooks", {})
    for source_event, groups in source_hooks.items():
        target_event = EVENT_MAP.get(source_event, source_event)
        target_list = target_hooks.setdefault(target_event, [])
        existing = {
            entry.get("command")
            for entry in target_list
            if isinstance(entry, dict)
        }
        entries = (to_entry(hook, plugin_link) for group in groups for hook in group.get("hooks", []))
        for entry in entries:
            if entry is None or entry["command"] in existing:
                continue
            target_list.append(entry)
            existing.add(entry["command"])


source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
hooks_path = Path(sys.argv[3])
agent_path = Path(sys.argv[4])
plugin_link = sys.argv[5]
source = json.loads(source_path.read_text())
target = json.loads(target_path.read_text()) if target_path.exists() else {}
source_servers = source.get("mcpServers", {})
target.setdefault("mcpServers", {})
target["mcpServers"].update(source_servers)
target_path.write_text(json.dumps(target, indent=2, ensure_ascii=False) + "\n")

if agent_path.exists():
    agent = json.loads(agent_path.read_text())
else:
    agent = {
        "name": "default",
        "resources": ["skill://.kiro/skills/**/SKILL.md"],
    }

# Kiro は tools に挙がったツールだけをモデルへ見せる。欄が無いと MCP のツールを
# 呼べない（#1873）。利用者が書いた tools は上書きしない。
agent.setdefault("tools", ["*"])
agent.setdefault("mcpServers", {})
agent["mcpServers"].update(source_servers)

if hooks_path.exists():
    source_hooks = json.loads(hooks_path.read_text()).get("hooks", {})
    merge_hooks(agent, source_hooks, plugin_link)

agent_path.write_text(json.dumps(agent, indent=2, ensure_ascii=False) + "\n")

print(f"Installed MCP servers: {', '.join(sorted(source_servers))}")
print(f"Updated: {target_path}")
print(f"Updated: {agent_path}")
PY
