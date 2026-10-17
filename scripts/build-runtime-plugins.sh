#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK=false

usage() {
  cat <<'EOF'
Usage: bash scripts/build-runtime-plugins.sh [--check]

Generate the tracked build outputs: Codex implicit-invocation policies for
single-directory plugins, the agy skill links under dev.agy/, and the runtime
copies of MCP plugins.

Options:
  --check   Compare generated output with the working tree and fail on drift.
  -h, --help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) CHECK=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done


sync_codex_skill_policies() {
  local family="$1"
  local plugin_dir="$ROOT_DIR/plugins/$family"
  local manifest="$plugin_dir/manifests/codex-skills.txt"

  [ -f "$manifest" ] || return 0
  # frontmatter を YAML として読むため、根の uv の環境で動く生成の本体へ渡す（#1142 の D8）
  python3 "$ROOT_DIR/scripts/lib/codex_skill_policies.py" "$plugin_dir/skills" "$manifest" "$CHECK"
}

# agy は配る Skill を絞る手段を利用者側の設定にしか持たない（`plugins.json` の絞り込みは
# プラグインのディレクトリ名に掛かり、同梱の Skill には届かない）。そこで絞り込みは
# dev.agy/skills/ へ何を並べるかで表す。並べる内容は manifests/agy-skills.txt が決める。
# エージェント定義と hook の実体は 4 ランタイムで共有するため、同じ位置へ symlink を張る。
# Agent Plugins 仕様 §8.2 のクライアント拡張ディレクトリに当たる。
sync_agy_skill_links() {
  local family="$1"
  local plugin_dir="$ROOT_DIR/plugins/$family"

  [ -f "$plugin_dir/manifests/agy-skills.txt" ] || return 0
  python3 - "$plugin_dir" "$CHECK" <<'PY'
import os
import sys
from pathlib import Path

plugin_dir, check = Path(sys.argv[1]), sys.argv[2] == "true"
dev_dir = plugin_dir / "dev.agy"
links_dir = dev_dir / "skills"

published = [
    line.split("#", 1)[0].strip()
    for line in (plugin_dir / "manifests/agy-skills.txt").read_text(encoding="utf-8").splitlines()
    if line.split("#", 1)[0].strip()
]

# 生成する symlink と、その向き先。skills/ は基準が決め、agents と scripts は固定である。
expected = {links_dir / name: f"../../skills/{name}" for name in published}
expected.update({dev_dir / "agents": "../agents", dev_dir / "scripts": "../scripts"})

actual = set()
if links_dir.is_dir():
    actual |= set(links_dir.iterdir())
for name in ("agents", "scripts"):
    path = dev_dir / name
    if path.is_symlink():
        actual.add(path)
stale = sorted(actual - set(expected))

failed = False


def report(message: str) -> None:
    global failed
    print(message, file=sys.stderr)
    failed = True


for path, target in sorted(expected.items()):
    if check:
        if not path.is_symlink():
            report(f"Generated link missing: {path}")
        elif os.readlink(path) != target:
            report(f"Generated link points elsewhere: {path} -> {os.readlink(path)}")
        continue
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        path.unlink()
    path.symlink_to(target)

for path in stale:
    if check:
        report(f"Generated link is stale: {path}")
    else:
        path.unlink()

raise SystemExit(1 if failed else 0)
PY
}

# Kiro CLI にはプラグイン機構が無いため、MCP プラグインごとに installer を置く。内容は
# プラグイン名に依存しないので、雛形（scripts/templates/kiro-mcp-install.sh.in）を 1 つ持って
# 各プラグインの dev.kiro/ へ書き出す。
# Agent Plugins 仕様 §8.2 のクライアント拡張ディレクトリに当たる。
write_kiro_mcp_installer() {
  local plugin_dir="$1"
  local installer="$plugin_dir/dev.kiro/install.sh"
  local template="$ROOT_DIR/scripts/templates/kiro-mcp-install.sh.in"

  if [ "$CHECK" = true ]; then
    if [ ! -f "$installer" ] || ! diff -q "$template" "$installer" >/dev/null; then
      echo "Generated file is out of date: ${installer#$ROOT_DIR/}" >&2
      return 1
    fi
    return 0
  fi
  mkdir -p "$(dirname "$installer")"
  cp "$template" "$installer"
  chmod +x "$installer"
}

sync_mcp_plugins() {
  local plugin_dir

  for plugin_dir in "$ROOT_DIR"/plugins/mcp/*; do
    [ -f "$plugin_dir/.mcp.json" ] || continue
    write_kiro_mcp_installer "$plugin_dir"
  done
}

for dir in "$ROOT_DIR"/plugins/*; do
  [ -d "$dir/manifests" ] || continue
  family="$(basename "$dir")"
  case "$family" in
    *-shared|*-claude|*-codex|*-kiro) continue ;;
  esac
  sync_codex_skill_policies "$family"
  sync_agy_skill_links "$family"
done

sync_mcp_plugins

if [ "$CHECK" = true ]; then
  echo "runtime plugin generated files are up to date"
else
  echo "runtime plugin generated files synchronized"
fi
