#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${HOME:=/tmp/runtime-home}"

case "$runtime" in
  claude)
    find "$HOME" -path '*/.claude-plugin/plugin.json' -print | grep -q .
    find "$HOME" -path '*/skills/*/SKILL.md' -print | grep -q .
    find "$HOME" -path '*/agents/*.md' -print | grep -q .
    # 単一ディレクトリ構成では、同じ hooks/ に runtime ごとの定義が並ぶ。
    # 読む側が違うので、それぞれ自分の定義が届いていることを見る。
    find "$HOME" -path '*/hooks/claude.json' -print | grep -q .
    ;;
  codex)
    find "$HOME" -path '*/.codex-plugin/plugin.json' -print | grep -q .
    find "$HOME" -path '*/skills/*/SKILL.md' -print | grep -q .
    find "$HOME" -path '*/hooks/codex.json' -print | grep -q .
    ;;
  kiro)
    test -f "$PROJECT_DIR/.kiro/agents/ndf.json"
    # tools が未宣言だと Kiro CLI はツールなしのエージェントとして読み込み、
    # skill が SKILL.md を読むことも git / gh を実行することもできなくなる。
    python3 -c '
import json, sys
config = json.load(open(sys.argv[1]))
tools = config.get("tools")
if not tools:
    sys.exit("agent config declares no tools: " + sys.argv[1])
' "$PROJECT_DIR/.kiro/agents/ndf.json"
    find -L "$PROJECT_DIR/.kiro/skills" -path '*/SKILL.md' -print | grep -q .
    # playwright-kit は別プラグインとして導入する（NDF の manifest には含まれない）
    test -L "$PROJECT_DIR/.kiro/skills/playwright-planning"
    test -f "$PROJECT_DIR/.kiro/skills/playwright-planning/SKILL.md"
    test -f "$PROJECT_DIR/.kiro/prompts/pr.md"
    test -s "$PROJECT_DIR/.kiro/steering/ndf-policies.md"
    test -L "$PROJECT_DIR/.kiro/mcp_runtime/mcp-bigquery"
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
