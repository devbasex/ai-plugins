#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK=false

usage() {
  cat <<'EOF'
Usage: bash scripts/build-runtime-plugins.sh [--check]

Generate the tracked build outputs: Codex implicit-invocation policies for
single-directory plugins.

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

# 配る family は manifests/ があり marketplace.json に登録されたもの（scripts/lib/plugin_families.py）
FAMILIES="$(python3 "$ROOT_DIR/scripts/lib/plugin_families.py" "$ROOT_DIR")"
while IFS= read -r family; do
  [ -n "$family" ] || continue
  sync_codex_skill_policies "$family"
done <<< "$FAMILIES"

if [ "$CHECK" = true ]; then
  echo "runtime plugin generated files are up to date"
else
  echo "runtime plugin generated files synchronized"
fi
