#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${HOME:=/tmp/runtime-home}"

# `find ... | grep -q .` は使わない。`grep -q` は最初の一致で終わるため、`find` がまだ
# 書いている間にパイプが閉じ、`find` が SIGPIPE で死ぬ。`set -o pipefail` のもとでは
# パイプライン全体が 141 を返し、**見つかっているのに失敗になる**。走査するファイルが
# 増えるほど当たりやすい（配布 Skill を 5 個足した時点で継続的統合が落ちた）。
# 最初の 1 件で `find` 自身を止め、結果は変数で受ける。
require_path() {
  local root="$1" pattern="$2" hit
  hit="$(find "$root" -path "$pattern" -print -quit)"
  [ -n "$hit" ] || { echo "not found: $pattern under $root" >&2; exit 1; }
}

# symlink をたどる版。Kiro CLI は `.kiro/skills/` へ symlink を張る。
require_path_follow() {
  local root="$1" pattern="$2" hit
  hit="$(find -L "$root" -path "$pattern" -print -quit)"
  [ -n "$hit" ] || { echo "not found: $pattern under $root" >&2; exit 1; }
}

require_name() {
  local root="$1" pattern="$2" hit
  hit="$(find "$root" -name "$pattern" -print -quit)"
  [ -n "$hit" ] || { echo "not found: $pattern under $root" >&2; exit 1; }
}

case "$runtime" in
  claude)
    require_path "$HOME" '*/.claude-plugin/plugin.json'
    require_path "$HOME" '*/skills/*/SKILL.md'
    require_path "$HOME" '*/agents/*.md'
    # 単一ディレクトリ構成では、同じ hooks/ に runtime ごとの定義が並ぶ。
    # 読む側が違うので、それぞれ自分の定義が届いていることを見る。
    require_path "$HOME" '*/hooks/claude.json'
    ;;
  codex)
    require_path "$HOME" '*/.codex-plugin/plugin.json'
    require_path "$HOME" '*/skills/*/SKILL.md'
    require_path "$HOME" '*/hooks/codex.json'
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
    require_path_follow "$PROJECT_DIR/.kiro/skills" '*/SKILL.md'
    # playwright-kit は別プラグインとして導入する（NDF の manifest には含まれない）
    test -L "$PROJECT_DIR/.kiro/skills/playwright-planning"
    test -f "$PROJECT_DIR/.kiro/skills/playwright-planning/SKILL.md"
    test -f "$PROJECT_DIR/.kiro/prompts/pr.md"
    test -s "$PROJECT_DIR/.kiro/steering/ndf-policies.md"
    test -L "$PROJECT_DIR/.kiro/mcp_runtime/mcp-bigquery"
    ;;
  agy)
    # agy は導入時にプラグインのディレクトリ全体を複製する。symlink は実体へ解決されるため、
    # 導入先では独立したファイルになる。
    plugin_dir="$HOME/.gemini/config/plugins/ndf"
    test -f "$plugin_dir/plugin.json"
    test -f "$plugin_dir/hooks.json"
    require_path "$plugin_dir/skills" '*/SKILL.md'
    require_name "$plugin_dir/agents" '*.md'
    test -f "$plugin_dir/scripts/worktree-guard.sh"
    # 配る Skill の基準は manifest だけが持つ。導入先の並びが基準と一致することを見る。
    python3 - "$REPO_ROOT/plugins/ndf/manifests/agy-skills.txt" "$plugin_dir/skills" <<'AGYPY'
import sys
from pathlib import Path

manifest, installed = Path(sys.argv[1]), Path(sys.argv[2])
expected = sorted(
    line.split("#", 1)[0].strip()
    for line in manifest.read_text(encoding="utf-8").splitlines()
    if line.split("#", 1)[0].strip()
)
present = sorted(d.name for d in installed.iterdir() if (d / "SKILL.md").is_file())
if expected != present:
    sys.exit(
        "installed agy skills differ from the manifest: "
        f"missing={sorted(set(expected) - set(present))} "
        f"extra={sorted(set(present) - set(expected))}"
    )
print(f"agy skills installed: {len(present)}")
AGYPY
    # ファイルの存在だけでは、agy がどの要素を取り込んだかを確かめられない。取り込みの
    # 記録を読み、並びに依存せず 3 つが揃っていることを見る。
    agy plugin list | python3 -c '
import json, sys
imports = json.load(sys.stdin).get("imports", [])
entry = next((i for i in imports if i.get("name") == "ndf"), None)
if entry is None:
    sys.exit("agy plugin list does not contain ndf")
components = sorted(entry.get("components", []))
if components != ["agents", "hooks", "skills"]:
    sys.exit(f"agy imported unexpected components: {components}")
print("agy components: " + ", ".join(components))
'
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
