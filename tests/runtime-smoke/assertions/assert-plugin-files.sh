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
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
