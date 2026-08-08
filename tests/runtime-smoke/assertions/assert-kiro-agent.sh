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
# INSTALL_ARGS: この scope の導入先を再現する installer 引数
case "$scope" in
  workspace)
    KIRO_DIR="$PROJECT_DIR/.kiro"
    INSTALL_ARGS=(--project "$PROJECT_DIR")
    ;;
  global)
    KIRO_DIR="$HOME/.kiro"
    INSTALL_ARGS=(--scope global)
    ;;
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

# Kiro 配布物は plugin.json を持たないため、版数は VERSION ファイルと導入後の
# エージェント description でしか確認できない。両者が一致することを検査する。
VERSION_FILE="$REPO_ROOT/plugins/ndf-kiro/VERSION"
test -s "$VERSION_FILE"
ndf_version="$(tr -d '[:space:]' < "$VERSION_FILE")"
# grep だと 5.0.0 の . が任意文字に一致するため、JSON を読んで厳密に照合する。
python3 - "$AGENT_FILE" "$ndf_version" >> "$LOG" <<'PY'
import json
import sys
from pathlib import Path

agent_file, version = sys.argv[1:3]
description = json.loads(Path(agent_file).read_text(encoding="utf-8")).get("description", "")
expected = f"NDF統合開発エージェント（Kiro CLI用 / v{version}）"
if description != expected:
    raise SystemExit(
        f"installed agent description must be {expected!r}, got {description!r}"
    )
print(f"kiro version surfaced: v{version}")
PY

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

# 旧 installer が別 checkout から張った .kiro/skills/ndf-policies symlink は、現在の
# プラグイン配下を指さないため「自分が張ったリンクだけ消す」掃除に掛からない。旧導入済み
# プロジェクトでも steering との二重注入が解消されることを検査する。
STALE_ROOT="$ARTIFACT_DIR/stale-checkout-$scope/skills/ndf-policies"
mkdir -p "$STALE_ROOT"
echo "stale" > "$STALE_ROOT/SKILL.md"
ln -sfn "$STALE_ROOT" "$KIRO_DIR/skills/ndf-policies"
bash "$REPO_ROOT/plugins/ndf-kiro/install.sh" "${INSTALL_ARGS[@]}" --with-slack >> "$LOG" 2>&1
if [ -e "$KIRO_DIR/skills/ndf-policies" ] || [ -L "$KIRO_DIR/skills/ndf-policies" ]; then
  echo "installer left a stale ndf-policies skill link: $KIRO_DIR/skills/ndf-policies" >&2
  exit 1
fi
test -f "$STALE_ROOT/SKILL.md"  # リンク先の実体まで消していないこと
echo "installer removed a stale ndf-policies skill link" >> "$LOG"

# --- workspace 限定の検査（ここから fi まで。heredoc の終端子の都合でインデントしない） ---
if [ "$scope" = workspace ]; then
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

# 旧 .kiro/agents/default.json からの自動移行を検査する。kiro-cli には依存しない。
MIGRATION_ROOT="$ARTIFACT_DIR/kiro-legacy-migration"
rm -rf "$MIGRATION_ROOT"

# $1: プロジェクト, $2: ndf ならば旧 installer の生成物そのままの fixture、
# それ以外なら NDF 生成物と判定されない fixture を書く。
#
# installer の判定は「旧テンプレート固有の description の完全一致」+「旧 resources
# の skill:// 指定 または agentSpawn フックの CLAUDE.ndf.md 検査」なので、
# 自動移行ケースの fixture は旧 default.json.template と同じ値を持たせる。
write_legacy_agent() {
  mkdir -p "$1/.kiro/agents"
  if [ "$2" = ndf ]; then
    cat > "$1/.kiro/agents/default.json" <<'JSON'
{
  "name": "default",
  "description": "NDF統合開発エージェント（Kiro CLI用）",
  "tools": ["*"],
  "resources": [
    "file://AGENTS.md",
    "file://README.md",
    "file://.kiro/skills/ndf-policies/SKILL.md",
    "skill://.kiro/skills/**/SKILL.md"
  ],
  "hooks": {
    "agentSpawn": [
      { "command": "if [ -f \"${PWD}/CLAUDE.ndf.md\" ]; then echo \"[NDF] CLAUDE.ndf.md\"; fi" }
    ]
  },
  "mcpServers": { "legacy-user-mcp": { "command": "echo", "args": ["legacy"] } }
}
JSON
  else
    cat > "$1/.kiro/agents/default.json" <<'JSON'
{
  "name": "default",
  "description": "my own agent",
  "resources": ["file://AGENTS.md"],
  "mcpServers": { "legacy-user-mcp": { "command": "echo", "args": ["legacy"] } }
}
JSON
  fi
}
install_into() {
  bash "$REPO_ROOT/plugins/ndf-kiro/install.sh" --project "$1" "${@:2}" >> "$LOG" 2>&1
}

# 1. NDF 生成物 + ndf.json なし → 自動移行し、利用者の mcpServers を引き継ぐ
case_ndf="$MIGRATION_ROOT/ndf-generated"
write_legacy_agent "$case_ndf" ndf
install_into "$case_ndf"
test ! -e "$case_ndf/.kiro/agents/default.json"
test -f "$case_ndf/.kiro/agents/default.json.bak"
python3 - "$case_ndf/.kiro/agents/ndf.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
if config.get("name") != "ndf":
    raise SystemExit(f"legacy migration did not refresh installer-managed keys: {path}")
if "legacy-user-mcp" not in config.get("mcpServers", {}):
    raise SystemExit(f"legacy migration dropped a user-managed mcpServers entry: {path}")
PY

# 2. NDF 生成物と判定できない default.json → 移行せず元のまま残す
case_user="$MIGRATION_ROOT/user-owned"
write_legacy_agent "$case_user" user
install_into "$case_user"
test -f "$case_user/.kiro/agents/default.json"
test -f "$case_user/.kiro/agents/default.json.bak"
python3 - "$case_user/.kiro/agents/ndf.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
if "legacy-user-mcp" in config.get("mcpServers", {}):
    raise SystemExit(f"a non-NDF default.json must not be migrated automatically: {path}")
PY

# 3. default.json と ndf.json の両方がある → 移行せず既存の ndf.json を尊重する
case_both="$MIGRATION_ROOT/both"
mkdir -p "$case_both"
install_into "$case_both"
python3 - "$case_both/.kiro/agents/ndf.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
config["smokeExistingKey"] = "keep-me"
path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
write_legacy_agent "$case_both" ndf
install_into "$case_both"
test -f "$case_both/.kiro/agents/default.json"
python3 - "$case_both/.kiro/agents/ndf.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
if config.get("smokeExistingKey") != "keep-me":
    raise SystemExit(f"legacy migration overwrote an existing ndf.json: {path}")
if "legacy-user-mcp" in config.get("mcpServers", {}):
    raise SystemExit(f"legacy migration overwrote an existing ndf.json: {path}")
PY

# 4. --dry-run → 移行を含め一切書き込まない
case_dry="$MIGRATION_ROOT/dry-run"
write_legacy_agent "$case_dry" ndf
dry_state() { (cd "$case_dry" && find . | sort && find . -type f -exec sha256sum {} + | sort); }
before_dry="$(dry_state)"
install_into "$case_dry" --dry-run
if [ "$(dry_state)" != "$before_dry" ]; then
  echo "--dry-run modified the project: $case_dry" >&2
  exit 1
fi
echo "installer migrated a legacy default.json only when it is safe" >> "$LOG"
fi
# --- workspace 限定の検査ここまで ---

if ! command -v kiro-cli >/dev/null 2>&1; then
  echo "kiro-cli agent checks skipped: kiro-cli is not available" >> "$LOG"
  exit 0
fi

# kiro-cli は workspace エージェントを cwd 配下の .kiro/agents からしか検出しない。
# scope ごとに、生成した $AGENT_FILE を検出できる cwd と installer の再実行引数を選ぶ。
case "$scope" in
  workspace) KIRO_CWD="$PROJECT_DIR" ;;
  # global エージェントはどこからでも解決できるはずなので、.kiro を持たない中立の
  # ディレクトリを cwd にする。$HOME を使うと $HOME/.kiro が workspace 扱いにもなり、
  # 「Global として見えている」ことの検査にならない。
  global) KIRO_CWD="$ARTIFACT_DIR" ;;
esac
if [ "$scope" = global ] && [ -e "$KIRO_CWD/.kiro" ]; then
  echo "global agent checks need a cwd without .kiro: $KIRO_CWD" >&2
  exit 1
fi

esc="$(printf '\033')"
agent_list() {
  # kiro-cli 2.16.1 の agent list は一覧を標準エラー出力へ書く
  (cd "$KIRO_CWD" && kiro-cli agent list 2>&1) | sed -e "s/${esc}\\[[0-9;]*m//g"
}
current_default() {
  agent_list | awk '/^\*/ { print $2; exit }'
}

if ! agent_list > "$ARTIFACT_DIR/kiro-agent-list-$scope.txt"; then
  echo "kiro-cli agent checks skipped: agent list failed" >> "$LOG"
  exit 0
fi
# global scope の $KIRO_CWD には .kiro がないため、ここに $AGENT_NAME が出ること自体が
# 「Global: ~/.kiro/agents 経由でどこからでも解決できる」ことの検査になる。
if ! awk '{ print $1, $2 }' "$ARTIFACT_DIR/kiro-agent-list-$scope.txt" | grep -qw "$AGENT_NAME"; then
  echo "agent list ($scope) does not contain $AGENT_NAME" >&2
  cat "$ARTIFACT_DIR/kiro-agent-list-$scope.txt" >&2
  exit 1
fi

before_default="$(current_default)"
echo "default agent before: ${before_default:-unknown}" >> "$LOG"

# kiro-cli の既定エージェントは ~/.local/share/kiro-cli/data.sqlite3 に保存されるマシン全体の
# 設定であり、この検査は必ず元へ戻す必要がある。set-default は agent list と同じく workspace
# エージェントを cwd 配下からしか検出せず、しかも未検出でも終了コード 0 を返すため、
# agent_list と同じ $KIRO_CWD から実行し、戻ったことを agent list で検証する。
# 途中の検査が落ちても復旧するよう trap で実行する。
restore_default() {
  [ -n "$before_default" ] || return 0
  [ "$before_default" != "$AGENT_NAME" ] || return 0
  (cd "$KIRO_CWD" && kiro-cli agent set-default "$before_default") >> "$LOG" 2>&1 || true
  restored_default="$(current_default || true)"
  echo "default agent restored: ${restored_default:-unknown}" >> "$LOG"
  if [ "$restored_default" != "$before_default" ]; then
    echo "failed to restore the default agent: ${restored_default:-unknown} (expected $before_default)" >&2
    return 1
  fi
}
trap 'rc=$?; restore_default || rc=1; exit $rc' EXIT

# kiro-cli はエージェントを cwd / $HOME 配下からのみ検出する。workspace では --project で
# 別ディレクトリへ導入したときに --set-default が効くことを検査するため、PROJECT_DIR 以外の
# cwd から実行する。global でも同様に $HOME 以外の cwd から実行して既定切替を検査する。
(cd "$ARTIFACT_DIR" && bash "$REPO_ROOT/plugins/ndf-kiro/install.sh" "${INSTALL_ARGS[@]}" --with-slack --set-default --yes) >> "$LOG" 2>&1
after_default="$(current_default)"
echo "default agent after: ${after_default:-unknown}" >> "$LOG"
if [ "$after_default" != "$AGENT_NAME" ]; then
  echo "--set-default did not switch the default agent ($scope): ${after_default:-unknown}" >&2
  exit 1
fi
