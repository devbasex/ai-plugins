#!/usr/bin/env bash
# Kiro の ndf エージェントが「選択できる状態」で導入されたことを検査する。
# 旧実装は .kiro/agents/default.json を生成していたが、Kiro の既定は組み込みの
# kiro_default であり、生成したエージェントは選択されないままだった。
#
# Usage: assert-kiro-agent.sh [workspace|global]
set -euo pipefail

scope="${1:-workspace}"
: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${ARTIFACT_DIR:=/tmp/runtime-artifacts}"
: "${HOME:=/tmp/runtime-home}"

AGENT_NAME="ndf"
case "$scope" in
  workspace) KIRO_DIR="$PROJECT_DIR/.kiro" ;;
  global) KIRO_DIR="$HOME/.kiro" ;;
  *) echo "unknown scope: $scope" >&2; exit 2 ;;
esac

AGENT_FILE="$KIRO_DIR/agents/$AGENT_NAME.json"
STEERING_FILE="$KIRO_DIR/steering/ndf-policies.md"
LOG="$ARTIFACT_DIR/kiro-agent-$scope.log"

mkdir -p "$ARTIFACT_DIR"
: > "$LOG"

test -f "$AGENT_FILE"
test -s "$STEERING_FILE"
find -L "$KIRO_DIR/skills" -path '*/SKILL.md' -print | grep -q .

# エージェント定義と、起動時に読み込まれる文脈量を検査する。
# 上限は 2026-08-07 / kiro-cli 2.16.1 の実測 112,621 文字に対する余裕分。
python3 - "$AGENT_FILE" "$KIRO_DIR" "$PROJECT_DIR" "$STEERING_FILE" 200000 >> "$LOG" <<'PY'
import json
import sys
from pathlib import Path

agent_file, kiro_dir, project_dir, steering_file, budget = sys.argv[1:6]
budget = int(budget)
config = json.loads(Path(agent_file).read_text(encoding="utf-8"))

if config.get("name") != "ndf":
    raise SystemExit(f"agent name must be 'ndf': {config.get('name')!r}")
if not config.get("tools"):
    raise SystemExit(f"agent config declares no tools: {agent_file}")

resources = config.get("resources", [])
for entry in resources:
    if entry.startswith("skill://"):
        raise SystemExit(f"skill:// resource duplicates the built-in rule: {entry}")
    if "ndf-policies" in entry:
        raise SystemExit(f"ndf-policies must be delivered via steering, not resources: {entry}")
if len(resources) != len(set(resources)):
    raise SystemExit(f"duplicated resources in {agent_file}")

context_files = sorted(Path(kiro_dir).glob("skills/*/SKILL.md"))
project = Path(project_dir)
context_files += [p for p in (project / "AGENTS.md", project / "README.md") if p.is_file()]
context_files.append(Path(steering_file))
total = sum(len(p.read_text(encoding="utf-8")) for p in context_files)
print(f"context files: {len(context_files)} / chars: {total} / budget: {budget}")
if total > budget:
    raise SystemExit(f"context files exceed the budget: {total} > {budget}")
PY

[ "$scope" = workspace ] || exit 0

if ! command -v kiro-cli >/dev/null 2>&1; then
  echo "kiro-cli agent checks skipped: kiro-cli is not available" >> "$LOG"
  exit 0
fi

esc="$(printf '\033')"
agent_list() {
  # kiro-cli 2.16.1 の agent list は一覧を標準エラー出力へ書く
  (cd "$PROJECT_DIR" && kiro-cli agent list 2>&1) | sed -e "s/${esc}\\[[0-9;]*m//g"
}
current_default() {
  agent_list | awk '/^\*/ { print $2; exit }'
}

if ! agent_list > "$ARTIFACT_DIR/kiro-agent-list.txt"; then
  echo "kiro-cli agent checks skipped: agent list failed" >> "$LOG"
  exit 0
fi
if ! awk '{ print $1, $2 }' "$ARTIFACT_DIR/kiro-agent-list.txt" | grep -qw "$AGENT_NAME"; then
  echo "agent list does not contain $AGENT_NAME" >&2
  cat "$ARTIFACT_DIR/kiro-agent-list.txt" >&2
  exit 1
fi

before_default="$(current_default)"
echo "default agent before: ${before_default:-unknown}" >> "$LOG"
bash "$REPO_ROOT/plugins/ndf-kiro/install.sh" --project "$PROJECT_DIR" --with-slack --set-default --yes >> "$LOG" 2>&1
after_default="$(current_default)"
echo "default agent after: ${after_default:-unknown}" >> "$LOG"
if [ "$after_default" != "$AGENT_NAME" ]; then
  echo "--set-default did not switch the default agent: ${after_default:-unknown}" >&2
  exit 1
fi
if [ -n "$before_default" ] && [ "$before_default" != "$AGENT_NAME" ]; then
  kiro-cli agent set-default "$before_default" >> "$LOG" 2>&1
  echo "default agent restored: $before_default" >> "$LOG"
fi
