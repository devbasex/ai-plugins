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
  agy)
    # agy は導入時にプラグインのディレクトリ全体を複製する。symlink は実体へ解決されるため、
    # 導入先では独立したファイルになる。
    plugin_dir="$HOME/.gemini/config/plugins/ndf"
    test -f "$plugin_dir/plugin.json"
    test -f "$plugin_dir/hooks.json"
    find "$plugin_dir/skills" -path '*/SKILL.md' -print | grep -q .
    find "$plugin_dir/agents" -name '*.md' -print | grep -q .
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
