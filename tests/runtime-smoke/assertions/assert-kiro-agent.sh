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

# ndf-policies は steering として配置する。Skill としても置くと Kiro 組み込みルールの
# Skill 読み込みと steering 読み込みで文脈へ二重注入される。
if [ -e "$KIRO_DIR/skills/ndf-policies" ]; then
  echo "ndf-policies must be delivered via steering only: $KIRO_DIR/skills/ndf-policies" >&2
  exit 1
fi

# エージェント定義と、起動時に読み込まれる文脈量を検査する。
# 上限は 2026-08-08 / kiro-cli 2.16.1 でのこのフィクスチャの実測 112,404 文字に対する余裕分。
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

# 利用者が $AGENT_FILE へ写した設定（MCP プラグインの mcpServers など）が、
# installer の再実行で失われないことを検査する。kiro-cli には依存しない。
python3 - "$AGENT_FILE" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
config.setdefault("mcpServers", {})["smoke-user-mcp"] = {
    "command": "echo",
    "args": ["user-managed"],
}
config.setdefault("hooks", {})["userPromptSubmit"] = [{"command": "true"}]
config["smokeUserKey"] = "keep-me"
path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
bash "$REPO_ROOT/plugins/ndf-kiro/install.sh" --project "$PROJECT_DIR" --with-slack >> "$LOG" 2>&1
python3 - "$AGENT_FILE" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
if "smoke-user-mcp" not in config.get("mcpServers", {}):
    raise SystemExit(f"reinstall dropped a user-managed mcpServers entry: {path}")
if "userPromptSubmit" not in config.get("hooks", {}):
    raise SystemExit(f"reinstall dropped a user-managed hook: {path}")
if config.get("smokeUserKey") != "keep-me":
    raise SystemExit(f"reinstall dropped a user-managed key: {path}")
# installer 管理のキーはテンプレートから再生成されている
if config.get("name") != "ndf" or not config.get("hooks", {}).get("agentSpawn"):
    raise SystemExit(f"reinstall did not regenerate installer-managed keys: {path}")
if not config.get("hooks", {}).get("stop"):
    raise SystemExit(f"--with-slack did not regenerate the stop hook: {path}")
PY
# 検査用に注入した設定を取り除き、以降の検査へ持ち越さない
python3 - "$AGENT_FILE" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
config.get("mcpServers", {}).pop("smoke-user-mcp", None)
if not config.get("mcpServers"):
    config.pop("mcpServers", None)
config.get("hooks", {}).pop("userPromptSubmit", None)
config.pop("smokeUserKey", None)
path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
echo "reinstall preserved user-managed agent settings" >> "$LOG"

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

# kiro-cli の既定エージェントは ~/.local/share/kiro-cli/data.sqlite3 に保存されるマシン全体の
# 設定であり、この検査は必ず元へ戻す必要がある。set-default は agent list と同じく workspace
# エージェントを cwd 配下からしか検出せず、しかも未検出でも終了コード 0 を返すため、
# agent_list と同じ PROJECT_DIR から実行し、戻ったことを agent list で検証する。
# 途中の検査が落ちても復旧するよう trap で実行する。
restore_default() {
  [ -n "$before_default" ] || return 0
  [ "$before_default" != "$AGENT_NAME" ] || return 0
  (cd "$PROJECT_DIR" && kiro-cli agent set-default "$before_default") >> "$LOG" 2>&1 || true
  restored_default="$(current_default || true)"
  echo "default agent restored: ${restored_default:-unknown}" >> "$LOG"
  if [ "$restored_default" != "$before_default" ]; then
    echo "failed to restore the default agent: ${restored_default:-unknown} (expected $before_default)" >&2
    return 1
  fi
}
trap 'rc=$?; restore_default || rc=1; exit $rc' EXIT

# kiro-cli は workspace エージェントを cwd 配下からのみ検出する。--project で別ディレクトリへ
# 導入したときに --set-default が効くことを検査するため、PROJECT_DIR 以外の cwd から実行する。
(cd "$ARTIFACT_DIR" && bash "$REPO_ROOT/plugins/ndf-kiro/install.sh" --project "$PROJECT_DIR" --with-slack --set-default --yes) >> "$LOG" 2>&1
after_default="$(current_default)"
echo "default agent after: ${after_default:-unknown}" >> "$LOG"
if [ "$after_default" != "$AGENT_NAME" ]; then
  echo "--set-default did not switch the default agent: ${after_default:-unknown}" >&2
  exit 1
fi
