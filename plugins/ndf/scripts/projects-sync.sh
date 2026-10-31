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
# `status` はボードだけに書く（「スプリントを閉じる」だけが使う）。
# **通過記録は自分では積まない（#725 の決定 1）。** `stage` / `mode` / `pace` は中で呼ぶ
# `progress-record.sh` が 1 回だけ積む。`gh` が無くても `progress-record.sh` を呼ぶため、
# ボードの宣言・`gh` の有無に関わらず通過記録が残る。1 回の実行に何件並べてもよい。
#
# **宣言（.ndf/projects.json）が無ければボードへは何もしない。** ボードを使わないと決めたリポジトリで、
# 欠けにはしない（`items` の board の要素が `not_declared`）。
#
# **結果は 1 行の結果 JSON で返す（#2227）。** 形は `lib/README.md` の「手順のスクリプトの結果」。
# 中で呼ぶ `progress-record.sh` の結果 JSON を受けて自分の `items` / `gaps` / `process` へ移し、標準出力へは
# 自分の 1 行だけを出す。本文とボードを記録した（か変える内容が無い・宣言が無い）ときだけ `ok`（0）。
# `gh` が無ければ `stopped`（3）、本文かボードのどれかを書けなければ `stopped`（6）で、書けなかったものを
# 1 件ずつ `gaps` に載せる（`reason` に `gh` の標準エラーの要旨・上限の理由）。何も書けなければ `stopped`（1）。
# **進行管理が理由で開発の工程を止めない。** 止めない判断は呼ぶ側が持ち、ここは書けなかった事実を返す。
#
# 呼び出し側の誤り（知らないキー・工程表に無い値・引数不足）は `stopped`（2）を返す。
# 黙って進むと、綴りの違う値が入るか、書き込んだつもりの値が入らない。
# **引数のチェックは何かを書く前に行う。** issue の本文だけが書かれた状態を作らない。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL=projects-sync
# shellcheck source=lib/step-result.sh
. "$SCRIPT_DIR/lib/step-result.sh" 2>/dev/null || { printf 'ERROR: lib/step-result.sh を読めない\n' >&2; exit 2; }
# shellcheck source=lib/projects-common.sh
. "$SCRIPT_DIR/lib/projects-common.sh" 2>/dev/null \
  || sr_emit --tool "$TOOL" --status stopped --summary "lib/projects-common.sh を読めない" --code 3
# 工程名・モード・進め方の判定は workflow-common.sh の 1 か所が持つ（#725 の決定 3）。
# shellcheck source=../skills/development-workflow/scripts/lib/workflow-common.sh
. "$SCRIPT_DIR/../skills/development-workflow/scripts/lib/workflow-common.sh" 2>/dev/null \
  || sr_emit --tool "$TOOL" --status stopped --summary "development-workflow の workflow-common.sh を読めない" --code 3

usage() {
  printf 'usage: projects-sync.sh <issue番号> <キー: stage|mode|pace|status|worktree|plan> <値>\n' >&2
}

# 呼び出し側の誤りを 2 で返す。
bad_args() {
  printf 'ERROR: %s\n' "$1" >&2
  [ -z "${2:-}" ] || usage
  sr_emit --tool "$TOOL" --status stopped --summary "$1" --code 2
}

ISSUE="${1:-}"
KEY="${2:-}"
VALUE="${3:-}"

if [ "$#" -ne 3 ] || [ -z "$ISSUE" ] || [ -z "$KEY" ] || [ -z "$VALUE" ]; then
  bad_args "引数が足りません" usage
fi
case "$ISSUE" in
  ''|*[!0-9]*) bad_args "issue 番号が数値ではありません: $ISSUE" ;;
esac
KIND=$(pj_key_kind "$KEY") || bad_args "知らないキーです: $KEY" usage
case "$KEY" in
  stage) wf_is_stage "$VALUE" ;;
  mode) wf_is_mode "$VALUE" ;;
  pace) wf_is_pace "$VALUE" ;;
  *) pj_is_valid_value "$KEY" "$VALUE" ;;
esac || bad_args "$KEY が取らない値です: $VALUE"

# issue の本文と通過記録。失敗してもボードの更新へ進む。
# **`gh` の有無を見る前に呼ぶ。** 通過記録は `gh` が無くても積む（#725 の決定 4）。
# `progress-record.sh` も自分で `gh` の有無を見るため、本文の振る舞いは変わらない。
BODY_STATUS=none BODY_CODE=0 SUMMARY_BODY=''
record_body() {
  local out
  out=$(bash "$SCRIPT_DIR/progress-record.sh" "$@")
  BODY_CODE=$?
  if sr_absorb "${out##*$'\n'}"; then
    BODY_STATUS=$SR_ABSORBED_STATUS
    SUMMARY_BODY=$SR_ABSORBED_SUMMARY
  else
    BODY_STATUS=stopped
    sr_gap issue-body "#$ISSUE" "progress-record.sh が結果を返さなかった（終了コード $BODY_CODE）"
  fi
}
case "$KEY" in
  stage) record_body "$ISSUE" "$VALUE" ;;
  mode|pace|worktree|plan) record_body "$ISSUE" - "--$KEY" "$VALUE" ;;
esac

# 終わり方。本文の結果とボードの結果から状態を決める。
# 本文を書けず（progress-record.sh が 1）ボードも記録しなかったときは「何も書けなかった」で 1。
BOARD_RESULT='' FIELD_NAME=''
finish() {
  local summary
  summary=$SUMMARY_BODY
  [ "$BOARD_RESULT" != recorded ] || summary="${summary:+$summary / }#$ISSUE $FIELD_NAME = $VALUE（ボード）"
  if [ "$SR_GAPS" -eq 0 ]; then
    if [ "$BODY_CODE" -eq 3 ]; then
      sr_emit --tool "$TOOL" --status stopped --summary "gh が無いため #$ISSUE の進行を書かない" --code 3
    fi
    sr_emit --tool "$TOOL" --status ok --summary "${summary:-#$ISSUE $KEY = $VALUE}"
  fi
  if [ "$BODY_CODE" -eq 1 ] && [ "$BOARD_RESULT" != recorded ]; then
    sr_emit --tool "$TOOL" --status stopped --summary "#$ISSUE の進行を何も書けなかった" --code 1
  fi
  sr_emit --tool "$TOOL" --status stopped --summary "#$ISSUE の進行の一部を書けなかった（$SR_GAPS 件）" --code 6
}

# ボードを使わない（宣言が無い・宣言にこのキーの列が無い）ときは欠けにしない。
board_not_declared() {
  BOARD_RESULT=not_declared
  sr_item board "#$ISSUE $KEY" not_declared
  finish
}

# ボードへ書けなかった理由。update の中のコマンド置換（サブシェル）からも届くようにファイルへ残す。
BOARD_ERR_FILE=$(mktemp 2>/dev/null) || BOARD_ERR_FILE=''
trap '[ -z "$BOARD_ERR_FILE" ] || rm -f "$BOARD_ERR_FILE"' EXIT
board_fail() {
  [ -z "$BOARD_ERR_FILE" ] || printf '%s' "$(sr_brief "$1")" > "$BOARD_ERR_FILE"
  return 1
}

if ! command -v gh >/dev/null 2>&1; then
  [ "$KEY" = status ] && BODY_CODE=3
  finish
fi

command -v git >/dev/null 2>&1 || { sr_gap board "#$ISSUE $KEY" "git が無いためボードの宣言を読めない"; finish; }

# 宣言はリポジトリの内容である。**いま見えている作業ツリーの最上位から読む。**
# 主ディレクトリ側を見ると、宣言を追加・変更したブランチでその変更が効かない。
top_dir=$(git rev-parse --show-toplevel 2>/dev/null) && [ -d "$top_dir" ] || board_not_declared

DECL=$(pj_declaration "$top_dir") || board_not_declared

command -v jq >/dev/null 2>&1 || { sr_gap board "#$ISSUE $KEY" "jq が無いためボードへ書けない"; finish; }

OWNER=$(pj_owner "$DECL")
NUMBER=$(pj_number "$DECL")
FIELD_NAME=$(pj_field_name "$DECL" "$KEY") || board_not_declared

# ボードから一度に読むアイテムの上限。長く使うボードほど閉じたアイテムが積み上がるため、
# 想定される総数より広く取る。上限に達したときは対象が見つからなくても黙って抜けない。
ITEM_LIMIT=1000

# 解決した識別子のキャッシュ。**記録のたびにボードの全件を読まない。**
CACHE=$(pj_cache_file "$OWNER" "$NUMBER" "$ISSUE" 2>/dev/null) || CACHE=
CACHED_PROJECT_ID='' CACHED_ITEM_ID=''
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
  url=$(gh issue view "$ISSUE" --json url -q .url 2>&1) || { board_fail "課題の URL を読めない: $url"; return 1; }
  [ -n "$url" ] || { board_fail "課題の URL が空"; return 1; }
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
    board_fail "ボードへ追加できない: $out"
    return 1
  }
  printf 'NOTE: #%s をボードへ追加しました\n' "$ISSUE" >&2
  # 出力の最後の行が識別子である。形が違えば空を返し、呼び出し側が読み直す。
  case "$out" in
    *PVTI_*) printf '%s\n' "$out" | grep -o 'PVTI_[A-Za-z0-9_-]*' | tail -1 ;;
  esac
  return 0
}

# ここから先は外部への問い合わせである。失敗したら理由を board_fail で残し、欠けとして返す。
update() {
  local project_id item_id field_id option_id total_count repo
  local project_json items_json fields_json added=0

  if [ -n "$CACHED_PROJECT_ID" ] && [ -n "$CACHED_ITEM_ID" ]; then
    project_id=$CACHED_PROJECT_ID
    item_id=$CACHED_ITEM_ID
  else
    project_json=$(gh project view "$NUMBER" --owner "$OWNER" --format json 2>&1) || {
      pj_is_rate_limited "$project_json" && printf 'NOTE: ボードを読めません（問い合わせの上限に達している可能性があります）\n' >&2
      board_fail "ボードを読めない: $project_json"
      return 1
    }
    project_id=$(printf '%s' "$project_json" | jq -r '.id // empty' 2>/dev/null) || { board_fail "ボードの応答を読めない"; return 1; }
    [ -n "$project_id" ] || { board_fail "ボードの識別子が無い"; return 1; }

  # ボードは組織単位で持てるため、同じ組織の複数のリポジトリのアイテムが並ぶ。issue 番号は
  # リポジトリごとに独立しているので、番号だけでは対象を一意に決められない。いま開いている
  # リポジトリを取得し、アイテムの所属と一致するものだけを選ぶ。取得できないときは
  # 何も更新しない。別のリポジトリのアイテムを書き換えるより、何もしないほうが安全である。
    repo=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>&1) || { board_fail "リポジトリを特定できない: $repo"; return 1; }
    [ -n "$repo" ] || { board_fail "リポジトリを特定できない"; return 1; }

    while :; do
      items_json=$(gh project item-list "$NUMBER" --owner "$OWNER" --limit "$ITEM_LIMIT" --format json 2>&1) || {
        pj_is_rate_limited "$items_json" && printf 'NOTE: ボードのアイテムを読めません（問い合わせの上限に達している可能性があります）\n' >&2
        board_fail "ボードのアイテムを読めない: $items_json"
        return 1
      }
      item_id=$(printf '%s' "$items_json" | jq -r --argjson n "$ISSUE" --arg repo "$repo" \
        'first(.items[]? | select(.content.number == $n and .content.repository == $repo) | .id) // empty' 2>/dev/null) \
        || { board_fail "ボードのアイテムの応答を読めない"; return 1; }
      [ -n "$item_id" ] && break
      # 見つからない理由は 3 つある。ボードへ登録していない、取得が上限で切れた、
      # 問い合わせが上限に達したのいずれかである。**黙って抜けると区別できない。**
      total_count=$(printf '%s' "$items_json" | jq -r '.totalCount // ((.items? // []) | length)' 2>/dev/null) || total_count=0
      case "$total_count" in ''|*[!0-9]*) total_count=0 ;; esac
      if [ "$total_count" -gt "$ITEM_LIMIT" ]; then
        printf 'NOTE: ボードのアイテムは %s 件あり、取得の上限 %s を超えています。#%s が見つからないのは取り漏れの可能性があります\n' \
          "$total_count" "$ITEM_LIMIT" "$ISSUE" >&2
        board_fail "ボードのアイテムが $total_count 件で取得の上限 $ITEM_LIMIT を超え、#$ISSUE が見つからない"
        return 1
      fi
      # 登録していないだけなら載せる。1 度だけ試す。
      [ "$added" -eq 0 ] || { board_fail "ボードへ追加した #$ISSUE が読み直しても見つからない"; return 1; }
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

  fields_json=$(gh project field-list "$NUMBER" --owner "$OWNER" --format json 2>&1) \
    || { board_fail "ボードのフィールドを読めない: $fields_json"; return 1; }

  # キャッシュの識別子が古くなることがある（ボードからアイテムを消した、ボードを作り直した）。
  # **書き込みに失敗したらキャッシュを捨て、1 度だけ全件の解決へ落ちる。** 捨てないと、
  # 以後の記録が同じ識別子を使い続けて永久に反映されない。
  if [ -n "$CACHED_ITEM_ID" ] && [ "$item_id" = "$CACHED_ITEM_ID" ]; then
    if ! write_field "$project_id" "$item_id" "$fields_json"; then
      [ -n "$CACHE" ] && rm -f "$CACHE"
      CACHED_PROJECT_ID='' CACHED_ITEM_ID=''
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
    'first(.fields[]? | select(.name == $n) | .id) // empty' 2>/dev/null) || { board_fail "ボードのフィールドの応答を読めない"; return 1; }
  [ -n "$field_id" ] || { board_fail "ボードにフィールド「$FIELD_NAME」が無い"; return 1; }

  if [ "$KIND" = "select" ]; then
    option_id=$(printf '%s' "$fields_json" | jq -r --arg n "$FIELD_NAME" --arg v "$VALUE" \
      'first(.fields[]? | select(.name == $n) | .options[]? | select(.name == $v) | .id) // empty' 2>/dev/null) \
      || { board_fail "ボードのフィールドの応答を読めない"; return 1; }
    [ -n "$option_id" ] || { board_fail "フィールド「$FIELD_NAME」に選択肢「$VALUE」が無い"; return 1; }
    sr_gh project item-edit --project-id "$project_id" --id "$item_id" \
      --field-id "$field_id" --single-select-option-id "$option_id" >/dev/null \
      || { board_fail "ボードへ書けない（gh project item-edit）: $SR_GH_ERR"; return 1; }
  else
    sr_gh project item-edit --project-id "$project_id" --id "$item_id" \
      --field-id "$field_id" --text "$VALUE" >/dev/null \
      || { board_fail "ボードへ書けない（gh project item-edit）: $SR_GH_ERR"; return 1; }
  fi
}

if update; then
  BOARD_RESULT=recorded
  sr_item board "#$ISSUE $FIELD_NAME" recorded
  sr_process "ボードの「$FIELD_NAME」へ書く" "$VALUE"
else
  reason=''
  [ -z "$BOARD_ERR_FILE" ] || reason=$(cat "$BOARD_ERR_FILE" 2>/dev/null)
  sr_gap board "#$ISSUE $FIELD_NAME" "${reason:-ボードへ書けない（理由の出力なし）}"
fi
finish
