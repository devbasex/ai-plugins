#!/usr/bin/env bash
# NDF Plugin Installer for Kiro CLI
# Usage: bash plugins/ndf-kiro/install.sh [--with-slack] [--with-codex] [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLUGIN_DIR="$SCRIPT_DIR"
KIRO_DIR="$PROJECT_ROOT/.kiro"
SKILLS_DIR="$KIRO_DIR/skills"
PROMPTS_DIR="$KIRO_DIR/prompts"
AGENT_FILE="$KIRO_DIR/agents/default.json"
TEMPLATE_FILE="$PLUGIN_DIR/agents/default.json.template"
PLUGIN_SKILLS_DIR="$PLUGIN_DIR/skills"
PLUGIN_PROMPTS_DIR="$PLUGIN_DIR/prompts"

# Parse options
WITH_SLACK=false
WITH_CODEX=false
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --with-slack) WITH_SLACK=true ;;
    --with-codex) WITH_CODEX=true ;;
    --dry-run) DRY_RUN=true ;;
    --help|-h)
      echo "Usage: bash plugins/ndf-kiro/install.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --with-slack   stopフックにSlack通知を追加"
      echo "  --with-codex   Codex CLI直接実行用プロンプトを追加"
      echo "  --dry-run      書き込みを行わず実行内容を表示"
      echo "  -h, --help     このヘルプを表示"
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

echo "=== NDF Plugin Installer for Kiro CLI ==="

if [ ! -d "$PLUGIN_SKILLS_DIR" ]; then
  echo "ERROR: $PLUGIN_SKILLS_DIR が見つかりません。先に scripts/build-runtime-plugins.sh を実行してください。" >&2
  exit 1
fi
if [ ! -f "$TEMPLATE_FILE" ]; then
  echo "ERROR: $TEMPLATE_FILE が見つかりません" >&2
  exit 1
fi

# --- Step 1: Create symlinks in .kiro/skills/ ---
echo "Skills シンボリックリンクを作成中..."
SKILL_COUNT=0
if [ "$DRY_RUN" = false ]; then
  mkdir -p "$SKILLS_DIR"
  find "$SKILLS_DIR" -mindepth 1 -maxdepth 1 -type l -exec rm -f {} +
fi

while IFS= read -r src_dir; do
  skill_name="$(basename "$src_dir")"

  if [ ! -f "$src_dir/SKILL.md" ]; then
    echo "  SKIP: $skill_name (SKILL.mdなし)"
    continue
  fi

  if [ "$DRY_RUN" = false ]; then
    ln -sfn "../../plugins/ndf-kiro/skills/$skill_name" "$SKILLS_DIR/$skill_name"
  fi
  echo "  linked: $skill_name"
  SKILL_COUNT=$((SKILL_COUNT + 1))
done < <(find "$PLUGIN_SKILLS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)

# --- Step 2: Create prompts in .kiro/prompts/ for workflow skills ---
echo "ワークフロープロンプトを作成中..."
if [ "$DRY_RUN" = false ]; then
  mkdir -p "$PROMPTS_DIR"
fi

while IFS= read -r prompt_file; do
  prompt_name="$(basename "$prompt_file")"
  [ "$prompt_name" = "codex.md" ] && [ "$WITH_CODEX" = false ] && continue
  if [ "$DRY_RUN" = false ]; then
    cp "$prompt_file" "$PROMPTS_DIR/$prompt_name"
  fi
  echo "  prompt: ${prompt_name%.md}"
done < <(find "$PLUGIN_PROMPTS_DIR" -maxdepth 1 -type f -name '*.md' | sort)

if [ "$WITH_CODEX" = true ] && [ ! -f "$PLUGIN_PROMPTS_DIR/codex.md" ]; then
  echo "ERROR: $PLUGIN_PROMPTS_DIR/codex.md が見つかりません" >&2
  exit 1
fi

if [ "$WITH_SLACK" = true ]; then echo "Slack通知: 有効"; else echo "Slack通知: 無効 (--with-slack で有効化)"; fi
if [ "$WITH_CODEX" = true ]; then echo "Codex CLI連携: 有効"; else echo "Codex CLI連携: 無効 (--with-codex で有効化)"; fi

if [ "$DRY_RUN" = true ]; then
  echo ""
  echo "DRY RUN: 書き込みは行いませんでした"
  echo "  エージェント設定: $AGENT_FILE"
  echo "  Skills数: $SKILL_COUNT"
  exit 0
fi

# --- Step 3: Generate agent config ---
mkdir -p "$KIRO_DIR/agents"

if [ -f "$AGENT_FILE" ]; then
  cp "$AGENT_FILE" "${AGENT_FILE}.bak"
  echo "既存設定をバックアップ: ${AGENT_FILE}.bak"
fi

python3 - "$TEMPLATE_FILE" "$WITH_SLACK" "$WITH_CODEX" "$AGENT_FILE" <<'PY'
import json
import sys

template_file, with_slack, with_codex, agent_file = sys.argv[1:5]
with open(template_file, encoding="utf-8") as f:
    config = json.load(f)

hooks = config.setdefault("hooks", {})
if with_slack == "true":
    hooks["stop"] = [
        {
            "command": "node plugins/ndf-kiro/scripts/slack-notify.js session_end",
            "timeout_ms": 70000,
        }
    ]
else:
    hooks.pop("stop", None)

if with_codex == "true":
    config["mcpServers"] = {
        "codex": {
            "command": "codex",
            "args": ["mcp-server"],
            "env": {},
        }
    }
else:
    config.pop("mcpServers", None)

with open(agent_file, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

echo ""
echo "=== インストール完了 ==="
echo "  エージェント設定: $AGENT_FILE"
echo "  Skills数: $SKILL_COUNT (シンボリックリンク: .kiro/skills/)"
echo ""
echo "Kiro CLIを起動して動作確認してください:"
echo "  kiro-cli chat"
