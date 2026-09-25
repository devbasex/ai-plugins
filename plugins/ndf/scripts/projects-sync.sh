#!/usr/bin/env bash
# NDF plugin: 進行の記録の入口。issue の本文の `## 進行` と、ボードのアイテムのフィールドを
# 1 回の呼び出しで更新する（#828）。
#
#   projects-sync.sh <issue番号> <キー> <値>
#     キー: stage | mode | pace | status | worktree | plan
#
# `stage` / `mode` / `pace` / `worktree` / `plan` は、ボードの宣言の有無にかかわらず先に
# `progress-record.sh` を呼んで issue の本文を更新し、その後でボードを更新する。`pace`（normal / fast）は
# ボードのフィールドを持たず、本文の見出し行（`進め方: fast`）にだけ書く。
# `status` はボードだけに書く（「ミッションを閉じる」だけが使う）。
# 通過記録はこのコマンドを観測して積むため、入口はこのスクリプトのままにする。
#
# **宣言（.ndf/projects.json）が無ければボードへは何もしない。** gh が無い場合と、
# 記録に失敗した場合も終了コード 0 で抜ける。進行管理が理由で開発の工程が止まってはいけない。
#
# 呼び出し側の誤り（知らないキー・工程表に無い値・引数不足）だけは 2 を返す。
# 黙って進むと、綴りの違う値が入るか、書き込んだつもりの値が入らない。
# **引数の検査は何かを書く前に行う。** issue の本文だけが書かれた状態を作らない。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/projects-common.sh
. "$SCRIPT_DIR/lib/projects-common.sh" 2>/dev/null || exit 0

usage() {
  printf 'usage: projects-sync.sh <issue番号> <キー: stage|mode|pace|status|worktree|plan> <値>\n' >&2
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

# issue の本文。失敗してもボードの更新へ進む（終了コードの契約は誤りだけ 2）。
case "$KEY" in
  stage) bash "$SCRIPT_DIR/progress-record.sh" "$ISSUE" "$VALUE" || : ;;
  mode|pace|worktree|plan) bash "$SCRIPT_DIR/progress-record.sh" "$ISSUE" - "--$KEY" "$VALUE" || : ;;
esac

command -v git >/dev/null 2>&1 || exit 0

# 宣言はリポジトリの内容である。**いま見えている作業ツリーの最上位から読む。**
# 主ディレクトリ側を見ると、宣言を追加・変更したブランチでその変更が効かない。
top_dir=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -d "$top_dir" ] || exit 0

DECL=$(pj_declaration "$top_dir") || exit 0

command -v jq >/dev/null 2>&1 || exit 0

OWNER=$(pj_owner "$DECL")
NUMBER=$(pj_number "$DECL")
FIELD_NAME=$(pj_field_name "$DECL" "$KEY") || exit 0

# ボードから一度に読むアイテムの上限。長く使うボードほど閉じたアイテムが積み上がるため、
# 想定される総数より広く取る。上限に達したときは対象が見つからなくても黙って抜けない。
ITEM_LIMIT=1000

# 解決した識別子のキャッシュ。**記録のたびにボードの全件を読まない。**
CACHE=$(pj_cache_file "$OWNER" "$NUMBER" "$ISSUE" 2>/dev/null) || CACHE=
CACHED_PROJECT_ID= CACHED_ITEM_ID=
if [ -n "$CACHE" ] && [ -f "$CACHE" ]; then
  # キャッシュは KEY=VALUE の 2 行だけである。**キー名を絞ってから読み込む。**
  # 値が古くなっていた場合は、書き込みに失敗した時点でキャッシュを捨てる（下記）。
  while IFS='=' read -r k v; do
    case "$k" in
      project_id) CACHED_PROJECT_ID=$v ;;
      item_id) CACHED_ITEM_ID=$v ;;
    esac
  done < "$CACHE"
fi

# ボードへアイテムを追加する。**追加は 1 つの issue を 1 つのボードへ載せる操作で、
# 取り消しも 1 コマンドである。** ボードそのものの作成とは扱いを分ける。
add_item() {
  local url out
  url=$(gh issue view "$ISSUE" --json url -q .url 2>/dev/null) || return 1
  [ -n "$url" ] || return 1
  # **戻り値のアイテムの識別子をそのまま使う。** 追加した直後に全件を読み直すと、
  # この PR が減らそうとした問い合わせをそこで使う。索引の反映が遅れていると、
  # 追加したばかりのアイテムが見つからずに記録が飛ぶ余地も残る。
  out=$(gh project item-add "$NUMBER" --owner "$OWNER" --url "$url" 2>&1) || {
    if pj_is_rate_limited "$out"; then
      printf 'NOTE: #%s をボードへ追加できません（問い合わせの上限に達している可能性があります）: %s\n' \
        "$ISSUE" "$out" >&2
    else
      printf 'NOTE: #%s をボードへ追加できませんでした: %s\n' "$ISSUE" "$out" >&2
    fi
    return 1
  }
  printf 'NOTE: #%s をボードへ追加しました\n' "$ISSUE" >&2
  # 出力の最後の行が識別子である。形が違えば空を返し、呼び出し側が読み直す。
  case "$out" in
    *PVTI_*) printf '%s\n' "$out" | grep -o 'PVTI_[A-Za-z0-9_-]*' | tail -1 ;;
  esac
  return 0
}

# ここから先は外部への問い合わせである。どこで失敗しても 0 で抜ける。
update() {
  local project_id item_id field_id option_id total_count repo
  local project_json items_json fields_json added=0

  if [ -n "$CACHED_PROJECT_ID" ] && [ -n "$CACHED_ITEM_ID" ]; then
    project_id=$CACHED_PROJECT_ID
    item_id=$CACHED_ITEM_ID
  else
    project_json=$(gh project view "$NUMBER" --owner "$OWNER" --format json 2>&1) || {
      pj_is_rate_limited "$project_json" && printf 'NOTE: ボードを読めません（問い合わせの上限に達している可能性があります）\n' >&2
      return 1
    }
    project_id=$(printf '%s' "$project_json" | jq -r '.id // empty' 2>/dev/null) || return 1
    [ -n "$project_id" ] || return 1

  # ボードは組織単位で持てるため、同じ組織の複数のリポジトリのアイテムが並ぶ。issue 番号は
  # リポジトリごとに独立しているので、番号だけでは対象を一意に決められない。いま開いている
  # リポジトリを取得し、アイテムの所属と一致するものだけを選ぶ。取得できないときは
  # 何も更新しない。別のリポジトリのアイテムを書き換えるより、何もしないほうが安全である。
    repo=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null) || return 1
    [ -n "$repo" ] || return 1

    while :; do
      items_json=$(gh project item-list "$NUMBER" --owner "$OWNER" --limit "$ITEM_LIMIT" --format json 2>&1) || {
        pj_is_rate_limited "$items_json" && printf 'NOTE: ボードのアイテムを読めません（問い合わせの上限に達している可能性があります）\n' >&2
        return 1
      }
      item_id=$(printf '%s' "$items_json" | jq -r --argjson n "$ISSUE" --arg repo "$repo" \
        'first(.items[]? | select(.content.number == $n and .content.repository == $repo) | .id) // empty' 2>/dev/null) || return 1
      [ -n "$item_id" ] && break
      # 見つからない理由は 3 つある。ボードへ登録していない、取得が上限で切れた、
      # 問い合わせが上限に達したのいずれかである。**黙って抜けると区別できない。**
      total_count=$(printf '%s' "$items_json" | jq -r '.totalCount // ((.items? // []) | length)' 2>/dev/null) || total_count=0
      case "$total_count" in ''|*[!0-9]*) total_count=0 ;; esac
      if [ "$total_count" -gt "$ITEM_LIMIT" ]; then
        printf 'NOTE: ボードのアイテムは %s 件あり、取得の上限 %s を超えています。#%s が見つからないのは取り漏れの可能性があります\n' \
          "$total_count" "$ITEM_LIMIT" "$ISSUE" >&2
        return 1
      fi
      # 登録していないだけなら載せる。1 度だけ試す。
      [ "$added" -eq 0 ] || return 1
      added=1
      item_id=$(add_item) || return 1
      # 識別子を受け取れたらそのまま使い、読み直さない。取れなければ次の回で読む。
      [ -n "$item_id" ] && break
    done
  fi

  # キャッシュへ残す。次回からは `item-edit` だけで済む。
  if [ -n "$CACHE" ]; then
    printf 'project_id=%s\nitem_id=%s\n' "$project_id" "$item_id" > "$CACHE" 2>/dev/null || :
  fi

  fields_json=$(gh project field-list "$NUMBER" --owner "$OWNER" --format json 2>/dev/null) || return 1

  # キャッシュの識別子が古くなることがある（ボードからアイテムを消した、ボードを作り直した）。
  # **書き込みに失敗したらキャッシュを捨て、1 度だけ全件の解決へ落ちる。** 捨てないと、
  # 以後の記録が同じ識別子を使い続けて永久に反映されない。
  if [ -n "$CACHED_ITEM_ID" ] && [ "$item_id" = "$CACHED_ITEM_ID" ]; then
    if ! write_field "$project_id" "$item_id" "$fields_json"; then
      [ -n "$CACHE" ] && rm -f "$CACHE"
      CACHED_PROJECT_ID= CACHED_ITEM_ID=
      printf 'NOTE: キャッシュの識別子で書き込めませんでした。ボードを読み直します\n' >&2
      update
      return $?
    fi
    return 0
  fi
  write_field "$project_id" "$item_id" "$fields_json"
}

# フィールドへ値を書く。単一選択は選択肢の識別子へ、文字列はそのまま。
write_field() {
  local project_id="$1" item_id="$2" fields_json="$3" field_id option_id
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
