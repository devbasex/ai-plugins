#!/usr/bin/env bash
# NDF plugin: Skill が使うスクリプトの置き場所を 1 つに決めて、絶対パスを標準出力へ書く。
#
# 使い方:
#   resolve.sh root              プラグインルート
#   resolve.sh scripts           プラグインルート直下の scripts/
#   resolve.sh scripts <Skill名> skills/<Skill名>/scripts/
#   resolve.sh skill <Skill名>   skills/<Skill名>/
#
# 見つからなければ理由を標準エラーへ書き、終了コード 3 で終わる。引数の誤りは 2。
#
# 候補の順序の正本は skills/development-workflow/references/scripts-lookup.md である。
#   1. 開発中のリポジトリ（現在地の git のトップの plugins/ndf）
#   2. Claude Code が読み込んだプラグイン
#   3. Kiro CLI の .kiro/skills/<Skill名> の symlink が指すプラグイン
#   4. Codex のマーケットプレイスの控え
#   5. agy が複製した実体
#   6. 現在地からの相対（plugins/ndf）
# どれも当たらなければ、この入口自身が置かれたプラグインを採る。
#
# この入口はプラグインルート直下の scripts/ に置く。Skill の下に置くと、その Skill を
# 配らない配布先（agy）で届かない（scripts/lib/README.md の「プラグインルート直下に置く理由」）。
set -uo pipefail

MARKER=scripts/projects-sync.sh

die() { echo "resolve.sh: $*" >&2; exit 3; }

usage() {
  echo "使い方: resolve.sh <root|scripts [Skill名]|skill <Skill名>>" >&2
  exit 2
}

# 候補がプラグインルートなら、物理的な絶対パスを書いて 0 を返す。
# `cd -P` にするのは、Kiro CLI の symlink の下の `..` を字句で畳ませないためである。
emit_if_plugin() {
  [ -n "${1:-}" ] || return 1
  [ -f "$1/$MARKER" ] || return 1
  (cd -P -- "$1" 2>/dev/null && pwd -P)
}

# この入口が置かれたプラグインルート。呼び出し側がたどった道（symlink を含む）を
# 保ったままの字句のパスと、物理的なパスの両方を持つ。
SELF_DIR_LOGICAL=$(dirname -- "${BASH_SOURCE[0]}")/..
SELF_ROOT=$(cd -P -- "$SELF_DIR_LOGICAL" 2>/dev/null && pwd -P) || SELF_ROOT=

# 2 の手がかり。Claude Code の中でだけ見る。
#   - 環境変数 CLAUDE_PLUGIN_ROOT（hook から呼ばれたとき）
#   - 入口が配布物の置き場（~/.codex / ~/.gemini / .kiro/skills / ~/.claude/plugins/cache）を
#     通らずに届いたなら、SKILL.md の ${CLAUDE_PLUGIN_ROOT} の置き換えで届いた実体である
#     （`claude --plugin-dir <パス>` で読み込んだとき）
#   - ~/.claude/plugins/installed_plugins.json に記録された ndf の installPath
# 参照ファイルの bash は置き換わらないため、入口が Codex の控えや cache の古い版から届く
# ことがある（#590）。その控えを採らず、Claude Code の導入の記録へ戻る。
claude_candidates() {
  [ -n "${CLAUDECODE:-}" ] || return 0
  case "${CLAUDE_PLUGIN_ROOT:-}" in /*) echo "$CLAUDE_PLUGIN_ROOT" ;; esac
  case "${BASH_SOURCE[0]}" in
    */.codex/*|*/.gemini/*|*.kiro/skills/*|*/.claude/plugins/cache/*) ;;
    *) echo "$SELF_ROOT" ;;
  esac
  local record="${HOME:-}/.claude/plugins/installed_plugins.json"
  [ -f "$record" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  python3 - "$record" <<'PY' 2>/dev/null || true
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
plugins = data.get("plugins", data) if isinstance(data, dict) else {}
for key, entries in plugins.items():
    if key.split("@", 1)[0] != "ndf":
        continue
    for entry in entries if isinstance(entries, list) else [entries]:
        path = entry.get("installPath") if isinstance(entry, dict) else None
        if path:
            print(path)
PY
}

# 3 の手がかり。`.kiro/skills` は実体のディレクトリで、Skill ごとの symlink の指す先の
# 2 つ上がプラグインルートになる。別のプラグインのリンクが並ぶため、目印があるまで調べる。
# `readlink -f` は BSD 系で意味が違うため使わない。
kiro_candidates() {
  local link target
  for link in .kiro/skills/*/ "${HOME:-}/.kiro/skills/"*/; do
    [ -L "${link%/}" ] || continue
    target=$(readlink "${link%/}") || continue
    case "$target" in
      /*) ;;
      *) target="$(dirname "${link%/}")/$target" ;;
    esac
    echo "$target/../.."
  done
}

# 1 を除く候補を順序どおりに 1 行ずつ書く（順序はヘッダの 2〜6 と最後の入口自身）。
# 1 は find_root が先に試す。最頻の経路で python3 などを起動しないためである。
candidates() {
  claude_candidates
  kiro_candidates
  printf '%s\n' \
    "${HOME:-}/.codex/.tmp/marketplaces/"*/plugins/ndf \
    "${HOME:-}/.codex/marketplaces/"*/plugins/ndf \
    "${HOME:-}/.gemini/config/plugins/ndf" \
    "plugins/ndf" \
    "$SELF_ROOT"
}

find_root() {
  local candidate top
  if top=$(git rev-parse --show-toplevel 2>/dev/null); then
    emit_if_plugin "$top/plugins/ndf" && return 0
  fi
  while IFS= read -r candidate; do
    emit_if_plugin "$candidate" && return 0
  done < <(candidates)
  return 1
}

[ "$#" -ge 1 ] || usage
KIND=$1
NAME=${2:-}
case "$KIND" in
  root|scripts) [ "$#" -le 2 ] || usage ;;
  skill) [ "$#" -eq 2 ] && [ -n "$NAME" ] || usage ;;
  *) usage ;;
esac
case "$NAME" in */*|.|..) usage ;; esac

ROOT=$(find_root) || die "NDF のプラグインルートが見つからない（$MARKER を持つ候補が無い。候補の順序は skills/development-workflow/references/scripts-lookup.md）"

case "$KIND" in
  root) echo "$ROOT" ;;
  scripts)
    if [ -z "$NAME" ]; then
      echo "$ROOT/scripts"
    else
      [ -d "$ROOT/skills/$NAME/scripts" ] || die "$ROOT/skills/$NAME/scripts が無い（この配布先は Skill $NAME を持たない）"
      echo "$ROOT/skills/$NAME/scripts"
    fi
    ;;
  skill)
    [ -d "$ROOT/skills/$NAME" ] || die "$ROOT/skills/$NAME が無い（この配布先は Skill $NAME を持たない）"
    echo "$ROOT/skills/$NAME"
    ;;
esac
