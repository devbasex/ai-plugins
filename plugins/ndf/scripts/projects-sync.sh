#!/usr/bin/env bash
# NDF plugin: issue に対応する Projects のアイテムのフィールドを更新する。
#
#   projects-sync.sh <issue番号> <キー> <値>
#     キー: stage | mode | status | worktree | plan
#
# **宣言（.ndf/projects.json）が無ければ何も出力せず終了コード 0 で抜ける。**
# gh が無い場合と、盤面の更新に失敗した場合も同じである。進行管理が理由で
# 開発の工程が止まってはいけない。
#
# 呼び出し側の誤り（知らないキー・工程表に無い値・引数不足）だけは 2 を返す。
# 黙って進むと、綴りの違う値が盤面へ入るか、書き込んだつもりの値が入らない。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/projects-common.sh
. "$SCRIPT_DIR/lib/projects-common.sh" 2>/dev/null || exit 0

command -v git >/dev/null 2>&1 || exit 0

# 宣言はリポジトリの内容である。**いま見えている作業ツリーの最上位から読む。**
# 主ディレクトリ側を見ると、宣言を追加・変更したブランチでその変更が効かない。
top_dir=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -d "$top_dir" ] || exit 0

DECL=$(pj_declaration "$top_dir") || exit 0

usage() {
  printf 'usage: projects-sync.sh <issue番号> <キー: stage|mode|status|worktree|plan> <値>\n' >&2
}

ISSUE="${1:-}"
KEY="${2:-}"
VALUE="${3:-}"

if [ "$#" -ne 3 ] || [ -z "$ISSUE" ] || [ -z "$KEY" ] || [ -z "$VALUE" ]; then
  printf 'ERROR: 引数が足りません\n' >&2
  usage
  exit 2
fi
case "$ISSUE" in
  ''|*[!0-9]*) printf 'ERROR: issue 番号が数値ではありません: %s\n' "$ISSUE" >&2; exit 2 ;;
esac
if ! KIND=$(pj_key_kind "$KEY"); then
  printf 'ERROR: 知らないキーです: %s\n' "$KEY" >&2
  usage
  exit 2
fi
if ! pj_is_valid_value "$KEY" "$VALUE"; then
  printf 'ERROR: %s が取らない値です: %s\n' "$KEY" "$VALUE" >&2
  exit 2
fi

command -v gh >/dev/null 2>&1 || exit 0
command -v jq >/dev/null 2>&1 || exit 0

OWNER=$(pj_owner "$DECL")
NUMBER=$(pj_number "$DECL")
FIELD_NAME=$(pj_field_name "$DECL" "$KEY") || exit 0

# 盤面から一度に読むアイテムの上限。長く使う盤面ほど閉じたアイテムが積み上がるため、
# 想定される総数より広く取る。上限に達したときは対象が見つからなくても黙って抜けない。
ITEM_LIMIT=1000

# ここから先は外部への問い合わせである。どこで失敗しても 0 で抜ける。
update() {
  local project_id item_id field_id option_id item_count
  local project_json items_json fields_json

  project_json=$(gh project view "$NUMBER" --owner "$OWNER" --format json 2>/dev/null) || return 1
  project_id=$(printf '%s' "$project_json" | jq -r '.id // empty' 2>/dev/null) || return 1
  [ -n "$project_id" ] || return 1

  items_json=$(gh project item-list "$NUMBER" --owner "$OWNER" --limit "$ITEM_LIMIT" --format json 2>/dev/null) || return 1
  item_id=$(printf '%s' "$items_json" | jq -r --argjson n "$ISSUE" \
    'first(.items[]? | select(.content.number == $n) | .id) // empty' 2>/dev/null) || return 1
  if [ -z "$item_id" ]; then
    # 見つからない理由は 2 つある。盤面へ登録していないか、取得が上限で切れたかである。
    # 黙って抜けると両者を区別できないため、上限に達したときだけ知らせる。
    item_count=$(printf '%s' "$items_json" | jq -r '(.items? // []) | length' 2>/dev/null) || item_count=0
    if [ "$item_count" = "$ITEM_LIMIT" ]; then
      printf 'NOTE: 盤面のアイテムの取得が上限 %s に達しました。#%s が見つからないのは取り漏れの可能性があります\n' \
        "$ITEM_LIMIT" "$ISSUE" >&2
    fi
    return 1
  fi

  fields_json=$(gh project field-list "$NUMBER" --owner "$OWNER" --format json 2>/dev/null) || return 1
  field_id=$(printf '%s' "$fields_json" | jq -r --arg n "$FIELD_NAME" \
    'first(.fields[]? | select(.name == $n) | .id) // empty' 2>/dev/null) || return 1
  [ -n "$field_id" ] || return 1

  if [ "$KIND" = "select" ]; then
    option_id=$(printf '%s' "$fields_json" | jq -r --arg n "$FIELD_NAME" --arg v "$VALUE" \
      'first(.fields[]? | select(.name == $n) | .options[]? | select(.name == $v) | .id) // empty' 2>/dev/null) || return 1
    [ -n "$option_id" ] || return 1
    gh project item-edit --project-id "$project_id" --id "$item_id" \
      --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null 2>&1 || return 1
  else
    gh project item-edit --project-id "$project_id" --id "$item_id" \
      --field-id "$field_id" --text "$VALUE" >/dev/null 2>&1 || return 1
  fi
}

update || exit 0
printf '#%s %s = %s\n' "$ISSUE" "$FIELD_NAME" "$VALUE"
exit 0
