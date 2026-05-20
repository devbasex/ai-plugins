#!/bin/bash
# NDF Plugin Installer for Kiro CLI
# Usage: bash scripts/install-kiro.sh [--with-slack] [--with-codex]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_DIR="$PROJECT_ROOT/plugins/ndf"
KIRO_DIR="$PROJECT_ROOT/.kiro"
SKILLS_DIR="$KIRO_DIR/skills"
AGENT_FILE="$KIRO_DIR/agents/default.json"
PLUGIN_JSON="$PLUGIN_DIR/.claude-plugin/plugin.json"

# Parse options
WITH_SLACK=false
WITH_CODEX=false
for arg in "$@"; do
  case "$arg" in
    --with-slack) WITH_SLACK=true ;;
    --with-codex) WITH_CODEX=true ;;
    --help|-h)
      echo "Usage: bash scripts/install-kiro.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --with-slack   stopフックにSlack通知を追加"
      echo "  --with-codex   Codex CLI MCPサーバーを追加"
      echo "  -h, --help     このヘルプを表示"
      exit 0
      ;;
  esac
done

echo "=== NDF Plugin Installer for Kiro CLI ==="

# Validate plugin.json exists
if [ ! -f "$PLUGIN_JSON" ]; then
  echo "ERROR: $PLUGIN_JSON が見つかりません" >&2
  exit 1
fi

# --- Step 1: Create symlinks in .kiro/skills/ ---
mkdir -p "$SKILLS_DIR"
echo "Skills シンボリックリンクを作成中..."
SKILL_COUNT=0
while IFS= read -r skill_path; do
  skill_path="${skill_path#./}"
  skill_name=$(basename "$skill_path")
  src_dir="$PLUGIN_DIR/$skill_path"

  if [ ! -f "$src_dir/SKILL.md" ]; then
    echo "  SKIP: $skill_name (SKILL.mdなし)"
    continue
  fi

  # Relative symlink from .kiro/skills/ to plugins/ndf/skills/
  ln -sfn "../../plugins/ndf/$skill_path" "$SKILLS_DIR/$skill_name"
  echo "  linked: $skill_name"
  SKILL_COUNT=$((SKILL_COUNT + 1))
done < <(grep -oP '"\.\/skills\/[^"]+' "$PLUGIN_JSON" | sed 's/"//g')

# --- Step 2: Create prompts in .kiro/prompts/ for workflow skills ---
PROMPTS_DIR="$KIRO_DIR/prompts"
mkdir -p "$PROMPTS_DIR"
echo "ワークフロープロンプトを作成中..."

declare -A PROMPT_DESCS=(
  [pr]="commit, push, PR作成を一括実行してください。"
  [pr-tests]="PRのTest Planを自動実行し、結果をPRコメントに反映してください。"
  [fix]="PRのレビューコメントを確認し、修正対応を実行してください。"
  [review]="PRを専門家としてレビューし、Approve/Request Changesを判定してください。"
  [merged]="PRマージ後のクリーンアップを実行してください（main更新、ブランチ削除）。"
  [clean]="mainマージ済みブランチをローカル/リモート一括削除してください。"
)

for name in "${!PROMPT_DESCS[@]}"; do
  cat > "$PROMPTS_DIR/$name.md" << PROMPT_EOF
${PROMPT_DESCS[$name]}

${name}スキルの手順に従って実行してください。引数があればそのまま使用します。
PROMPT_EOF
  echo "  prompt: $name"
done

# --- Step 3: Generate agent config ---
mkdir -p "$KIRO_DIR/agents"

if [ "$WITH_SLACK" = true ]; then echo "Slack通知: 有効"; else echo "Slack通知: 無効 (--with-slack で有効化)"; fi
if [ "$WITH_CODEX" = true ]; then echo "Codex MCP: 有効"; else echo "Codex MCP: 無効 (--with-codex で有効化)"; fi

# Backup existing config
if [ -f "$AGENT_FILE" ]; then
  cp "$AGENT_FILE" "${AGENT_FILE}.bak"
  echo "既存設定をバックアップ: ${AGENT_FILE}.bak"
fi

# Write agent JSON
python3 -c "
import json, sys
config = {
    'name': 'default',
    'description': 'NDF統合開発エージェント（Kiro CLI用）',
    'resources': [
        'file://AGENTS.md',
        'file://README.md',
        'file://.kiro/skills/ndf-policies/SKILL.md',
        'skill://.kiro/skills/**/SKILL.md'
    ],
    'hooks': {
        'agentSpawn': [{'command': 'if [ -f \"\${PWD}/CLAUDE.ndf.md\" ] || [ -f \"\$HOME/.claude/CLAUDE.ndf.md\" ]; then echo \"[NDF] CLAUDE.ndf.md が検出されました。廃止済みです。cleanup を実行して削除してください。\"; fi'}]
    }
}
if sys.argv[1] == 'true':
    config['hooks']['stop'] = [{'command': 'node plugins/ndf/scripts/slack-notify.js session_end', 'timeout_ms': 70000}]
if sys.argv[2] == 'true':
    config['mcpServers'] = {'codex': {'command': 'codex', 'args': ['mcp-server'], 'env': {}}}
json.dump(config, open(sys.argv[3], 'w'), indent=2, ensure_ascii=False)
" "$WITH_SLACK" "$WITH_CODEX" "$AGENT_FILE"

echo ""
echo "=== インストール完了 ==="
echo "  エージェント設定: $AGENT_FILE"
echo "  Skills数: $SKILL_COUNT (シンボリックリンク: .kiro/skills/)"
echo ""
echo "Kiro CLIを起動して動作確認してください:"
echo "  kiro-cli chat"
