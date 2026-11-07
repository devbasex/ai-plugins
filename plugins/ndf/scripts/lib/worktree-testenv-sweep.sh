#!/usr/bin/env bash
# NDF plugin: テスト環境の片付け（使われていない環境の停止と、残った割り当ての回収）。
#
# `worktree-testenv.sh` が source する。単独では実行しない。割り当ての持ち主であることは
# 変えず、宣言・台帳の読み取りと compose の呼び出しは読み込む側の関数を使う。

# 回収の全体の上限（秒）。テスト環境の起動の上限（`container_reach.UP_TIMEOUT`）と同じ値である。
RECLAIM_SECONDS=300
# 打ち切った破棄の、SIGTERM から SIGKILL までの猶予（秒）。
RECLAIM_GRACE=5
# 回収の予約を持ち続ける上限（秒）。`container_reach.RECLAIM_LIMIT` と同じ値で、破棄が止まる
# 開始から 305 秒より後に切れる（#2660 の決定 4・決定 7）。
RECLAIM_HOLD=330

# --- reap -------------------------------------------------------------------

# 実行中ロックや稼働状態を判定し、個別環境を停止する。
reap_environment() {
  local worktree="$1"
  local environment="$2"
  local slot="$3"
  local lock

  TARGET="$worktree"
  ENVIRONMENT="$environment"
  # `compose_env` はスロットも渡す。読まずに `compose` を呼ぶと、未定義の
  # 変数を参照した時点で終了し（`set -u`）、コンテナが動いたまま残る。
  # shellcheck disable=SC2034  # 読むのは読み込む側の compose_env である
  SLOT="$slot"

  # 実行中の作業ツリーはロックを握っている。握られていれば対象から外す。
  lock=$(inuse_lock "$environment")
  if wt_lock_is_held "$lock"; then
    return 0
  fi

  # 起動していないものは止める必要がない。
  if [ -z "$("$(docker_command)" ps -q --filter "label=com.docker.compose.project=$environment" 2>/dev/null)" ]; then
    return 0
  fi

  printf '停止します: %s（%s）\n' "$environment" "$worktree"
  compose stop
}

do_reap() {
  local idle_seconds
  idle_seconds=$(wt_duration_seconds "$1") || {
    printf '%s\n' '--idle に期間を指定してください（例: 45m）' >&2
    return 1
  }
  has_docker || return 0

  local worktree environment slot
  while IFS=$'\t' read -r worktree environment slot; do
    [ -n "$worktree" ] || continue
    reap_environment "$worktree" "$environment" "$slot"
  done < <(wt_registry_visible "$(registry)" \
    | jq -r --argjson idle "$idle_seconds" '
      [.assignments[]
       | select(.released_at == null)
       | select(((.last_used_at // .assigned_at) | fromdateiso8601) < (now - $idle))]
      | group_by(.worktree) | map(last) | .[]
      | "\(.worktree)\t\(.environment)\t\(.slot)"')
}

# --- 回収の予約 ---------------------------------------------------------------

# 割り当ての追加と使い始めは、回収の予約を短く取って通る。回収が持つ間は割り当てない（決定 4）。
_reclaim_reserve() {
  local lock
  lock=$(reclaim_lock "$1")
  mkdir -p "$(dirname "$lock")" 2>/dev/null
  wt_lock_acquire "$lock" 5 "$RECLAIM_HOLD" && return 0
  printf '%s\n' "同じ環境名のテスト環境を回収しています: $1" >&2
  return 3
}

pass_reclaim_lock() {
  _reclaim_reserve "$1" || return
  wt_lock_release "$(reclaim_lock "$1")"
}

# 回収の予約を持ったまま行を足し、スロットを返す。予約を取れなければ 3、空きが無ければ 1 を返す。
take_slot() {
  local branch=$1 environment=$2 rc
  _reclaim_reserve "$environment" || return
  wt_slot_acquire "$MAIN_DIR" "$TARGET" "$branch" "$environment"
  rc=$?
  wt_lock_release "$(reclaim_lock "$environment")"
  return "$rc"
}

# 排他を取れずに失敗したときを除くため、未解放の行の数で枯渇を確かめる。
slots_exhausted() {
  [ "$(wt_registry_visible "$(registry)" | jq '[.assignments[] | select(.released_at == null)] | length')" -gt "$WT_SLOT_MAX" ]
}

# --- 回収の部品 ---------------------------------------------------------------

_reclaim_left() { printf '%s\n' "$((RECLAIM_DEADLINE - $(date +%s)))"; }

# コマンドを別のプロセスグループで、回収の残りの秒まで走らせる。時間切れは 137 を返す。
# 使い方: _reclaim_limited <標準出力の先> <標準エラーの先> <コマンド…>
_reclaim_limited() {
  local out=$1 err=$2
  shift 2
  ( set -m
    bash "$SCRIPT_DIR/lib/run-limited.sh" "$(_reclaim_left)" "$RECLAIM_GRACE" "$$" "$@" </dev/null >"$out" 2>"$err" &
    wait "$!" ) 2>/dev/null
}

_reclaim_error_line() { tail -n 1 "$1" 2>/dev/null | cut -c1-300; }

# 結果の 1 行を足す。使い方: _reclaim_item <結果> <環境名> <worktree> [<項目> <値>]...
_reclaim_item() {
  local item
  item=$(jq -nc --arg result "$1" --arg name "$2" --arg worktree "$3" \
    '{kind: "testenv", name: $name, result: $result, worktree: $worktree}')
  shift 3
  while [ "$#" -gt 0 ]; do
    item=$(printf '%s' "$item" | jq -c --arg key "$1" --arg value "$2" '.[$key] = $value')
    shift 2
  done
  RECLAIM_ITEMS="$RECLAIM_ITEMS$item"$'\n'
}

# 結果の JSON を出す。`failed` の行があれば 1 を返す。
_reclaim_report() {
  local report
  report=$(printf '%s' "$RECLAIM_ITEMS" | jq -sc '
    def count($r): map(select(.result == $r)) | length;
    {reclaimed: count("reclaimed"), released: count("released"), failed: count("failed"), deferred: count("deferred"),
     exposed: count("exposed"), unseen: count("unseen"), foreign: count("foreign")} as $m
    | {tool: "testenv-reclaim", status: (if $m.failed > 0 then "stopped" else "ok" end),
       summary: "テスト環境 \($m.reclaimed) 件を回収した（失敗 \($m.failed) 件・残した \($m.deferred) 件）",
       items: ., metrics: $m}')
  printf '%s\n' "$report"
  [ "$(printf '%s' "$report" | jq '.metrics.failed')" = 0 ]
}

_reclaim_not_run() {
  jq -nc --arg name "$MAIN_DIR" --arg reason "$1" '
    {tool: "testenv-reclaim", status: "stopped", summary: $reason,
     items: [{kind: "testenv-reclaim", name: $name, result: "not_run", reason: $reason}],
     metrics: {reclaimed: 0, released: 0, failed: 0, deferred: 0, exposed: 0, unseen: 0, foreign: 0}}'
  return 1
}

_reclaim_file_system() { stat -c %d "$1" 2>/dev/null || stat -f %d "$1" 2>/dev/null; }

# 回収を打った位置から置き場を確かめられるかを返す。別のコンテナの `/tmp` にある生きた worktree は
# 親のディレクトリが見えてもファイルシステムが違う。見えないものを無いと判定しない（決定 3）。
_reclaim_location_seen() {
  case "$1" in "$MAIN_DIR"/*) return 0 ;; esac
  local parent device
  parent=$(dirname "$1")
  [ -d "$parent" ] || return 1
  device=$(_reclaim_file_system "$parent")
  [ -n "$device" ] && [ "$device" = "$(_reclaim_file_system "$MAIN_DIR")" ]
}

# 行の分類を RECLAIM_CLASS へ入れる。live / unseen / stale のどれかである。
_reclaim_classify() {
  RECLAIM_CLASS=live
  [ ! -d "$1" ] || return 0
  case $'\n'"$RECLAIM_LOCKED"$'\n' in *$'\n'"$1"$'\n'*) return 0 ;; esac
  RECLAIM_CLASS=unseen
  _reclaim_location_seen "$1" || return 0
  RECLAIM_CLASS=stale
}

_reclaim_locked_paths() {
  local porcelain
  porcelain=$(git -C "$MAIN_DIR" worktree list --porcelain 2>/dev/null) || return 1
  printf '%s\n' "$porcelain" | awk '/^worktree /{ path = substr($0, 10) } /^locked( |$)/{ print path }'
}

# 同じ環境名の行の worktree を 1 行ずつ出す。使い方: _reclaim_rows <環境名> <行の条件>
_reclaim_rows() {
  wt_registry_read "$(registry)" \
    | jq -r --arg environment "$1" "[.assignments[] | select(.environment == \$environment and ($2)) | .worktree] | unique | .[]"
}

# コンテナの持ち主を確かめ、RECLAIM_OWNER へ none / own / foreign / failed を、RECLAIM_WHY へ理由を入れる。
# 別の複製のテスト環境を消さないため、コンテナの作業ディレクトリが同じ環境名の行のどれかの worktree か
# その下であることを見る。解放済みの行を含めるのは、先に解放した行から起動したコンテナが残るためである（決定 11）。
_reclaim_check_owner() {
  local environment=$1 out="$RECLAIM_TMP/ps" err="$RECLAIM_TMP/ps.err" path line matched rc
  local -a paths
  _reclaim_limited "$out" "$err" "$RECLAIM_DOCKER" ps -a --filter "label=com.docker.compose.project=$environment" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}'
  rc=$?
  if [ "$rc" != 0 ]; then
    RECLAIM_OWNER=failed
    RECLAIM_WHY="コンテナの持ち主を確かめられない: $(_reclaim_error_line "$err")"
    [ "$rc" != 137 ] || RECLAIM_WHY="コンテナの持ち主の確認が回収の上限で終わらなかった"
    return 0
  fi
  # 値の空のコンテナは空の行で出る。コマンド置換は末尾の改行を落とすため、ファイルの大きさで見る。
  if [ ! -s "$out" ]; then
    RECLAIM_OWNER=none
    return 0
  fi
  _wt_read_lines < <(_reclaim_rows "$environment" true)
  paths=("${WT_LINES[@]+"${WT_LINES[@]}"}")
  while IFS= read -r line || [ -n "$line" ]; do
    matched=0
    for path in "${paths[@]+"${paths[@]}"}"; do
      case "$line" in "$path" | "$path"/*) matched=1 ;; esac
    done
    if [ "$matched" = 0 ]; then
      RECLAIM_OWNER=foreign
      RECLAIM_WHY="別の位置から起動されたコンテナがある: ${line:-（作業ディレクトリの値が空）}"
      return 0
    fi
  done <"$out"
  RECLAIM_OWNER=own
}

# 定義を読まずにプロジェクトを破棄する。定義の無い `/` から `COMPOSE_FILE` を外して打つ（決定 2）。
_reclaim_discard() {
  local environment=$1 volumes=$2
  local -a flags=()
  [ "$volumes" = 0 ] || flags=(--volumes)
  ( cd / && _reclaim_limited /dev/null "$RECLAIM_TMP/down.err" \
      env -u COMPOSE_FILE -u COMPOSE_PROFILES "$RECLAIM_DOCKER" compose -p "$environment" down "${flags[@]+"${flags[@]}"}" )
}

_reclaim_release_rows() {
  local worktree
  for worktree in "$@"; do
    wt_slot_release "$MAIN_DIR" "$worktree" || return 1
  done
}

# 破棄して解放する。予約とロックの中で呼ぶ。stale は読み直した残った割り当ての worktree である。
_reclaim_destroy() {
  local environment=$1 shown=$2 volumes=1 worktree rc
  shift 2
  # shellcheck disable=SC2034  # 読むのは読み込む側の drop_golden_tag である
  ENVIRONMENT=$environment
  _reclaim_check_owner "$environment"
  case "$RECLAIM_OWNER" in
    failed) _reclaim_item failed "$environment" "$shown" reason "$RECLAIM_WHY"; return 0 ;;
    foreign) _reclaim_item foreign "$environment" "$shown" reason "$RECLAIM_WHY"; return 0 ;;
  esac

  # 中身を確かめられない環境に基準のタグを残さない（`do_down` と同じ）。
  for worktree in "$@"; do
    TARGET=$worktree
    drop_golden_tag 2>/dev/null || {
      _reclaim_item failed "$environment" "$shown" reason "基準のタグを台帳から消せなかった"
      return 0
    }
  done

  if [ "$(_reclaim_left)" -le 0 ]; then
    _reclaim_item deferred "$environment" "$shown" reason "回収の上限に達した"
    return 0
  fi

  [ "$WITH_VOLUMES" = 1 ] || wt_discards_volumes "$DECLARATION" || volumes=0
  # ボリュームのラベルには作業ディレクトリが無く、コンテナが無いと持ち主を確かめられない。
  local why=""
  if [ "$RECLAIM_OWNER" = none ]; then
    [ "$volumes" = 0 ] || why="コンテナが無く持ち主を確かめられないため、ボリュームは消していない"
    volumes=0
  fi

  _reclaim_discard "$environment" "$volumes"
  rc=$?
  if [ "$rc" = 137 ]; then
    _reclaim_item failed "$environment" "$shown" reason "破棄が回収の上限で終わらなかった"
    return 0
  fi
  if [ "$rc" != 0 ]; then
    _reclaim_item failed "$environment" "$shown" reason "破棄に失敗した: $(_reclaim_error_line "$RECLAIM_TMP/down.err")"
    return 0
  fi
  _reclaim_release_rows "$@" || {
    _reclaim_item failed "$environment" "$shown" reason "破棄したが解放を記録できなかった。回収を打ち直す"
    return 0
  }

  local kept=discarded
  [ "$volumes" = 1 ] || kept=kept
  RECLAIM_FREED=1
  _reclaim_item reclaimed "$environment" "$shown" volumes "$kept" ${why:+reason "$why"}
}

# 予約とロックを取った後に読み直し、1 つの環境を処理する。別の担当がすでに片付けていれば何も足さない。
_reclaim_locked() {
  local environment=$1 worktree live=0 shown
  local -a stale=()
  _wt_read_lines < <(_reclaim_rows "$environment" '.released_at == null')
  for worktree in "${WT_LINES[@]+"${WT_LINES[@]}"}"; do
    _reclaim_classify "$worktree"
    if [ "$RECLAIM_CLASS" = stale ]; then
      stale+=("$worktree")
    else
      live=1
    fi
  done
  [ "${#stale[@]}" -gt 0 ] || return 0
  shown=$(printf '%s, ' "${stale[@]}")
  shown=${shown%, }

  if [ "$live" = 1 ]; then
    if _reclaim_release_rows "${stale[@]}"; then
      RECLAIM_FREED=1
      _reclaim_item released "$environment" "$shown"
    else
      _reclaim_item failed "$environment" "$shown" reason "解放を記録できなかった"
    fi
    return 0
  fi
  _reclaim_destroy "$environment" "$shown" "${stale[@]}"
}

# 環境ごとの予約と実行中のロックを、待たずに取る。取れなければ別の回収かテストが処理中である（決定 4）。
_reclaim_environment() {
  local environment=$1 shown=$2 reservation inuse
  reservation=$(reclaim_lock "$environment")
  inuse=$(inuse_lock "$environment")
  if ! wt_lock_acquire "$reservation" 0 "$RECLAIM_HOLD"; then
    _reclaim_item deferred "$environment" "$shown" reason "別の回収が同じ環境を処理している"
    return 0
  fi
  if wt_lock_acquire "$inuse" 0 "$RECLAIM_HOLD"; then
    _reclaim_locked "$environment"
    wt_lock_release "$inuse"
  else
    _reclaim_item deferred "$environment" "$shown" reason "同じ環境で別の実行が動いている"
  fi
  wt_lock_release "$reservation"
}

# --- 回収の本体 ---------------------------------------------------------------

# 残った割り当てを回収し、結果の JSON を標準出力へ出す。第 1 引数が 1 なら、1 環境を空けたら止める。
# `TARGET` などの変数を書き換えるため、**部分シェルの中で呼ぶ。**
reclaim_run() {
  local one_only=${1:-0} path snapshot worktree missing=0 line environment url shown
  local -a rows plan stale=()
  RECLAIM_ITEMS=""
  RECLAIM_FREED=0
  RECLAIM_DEADLINE=$(($(date +%s) + ${WT_RECLAIM_SECONDS:-$RECLAIM_SECONDS}))

  path=$(registry) || { _reclaim_not_run "worktree レジストリの位置を読めない"; return 1; }
  if [ -s "$path" ] && ! jq -e . "$path" >/dev/null 2>&1; then
    _reclaim_not_run "worktree レジストリを読めない: $path"
    return 1
  fi
  snapshot=$(wt_registry_visible "$path")
  _wt_read_lines < <(printf '%s' "$snapshot" \
    | jq -r '[.assignments[] | select(.released_at == null) | .worktree] | unique | .[]')
  rows=("${WT_LINES[@]+"${WT_LINES[@]}"}")
  for worktree in "${rows[@]+"${rows[@]}"}"; do
    [ -d "$worktree" ] || missing=1
  done
  if [ "$missing" = 0 ]; then
    _reclaim_report
    return
  fi

  # 接続できない Docker で行を解放しない（決定 10）。`compose ls` は Compose と接続を 1 回で確かめる。
  has_docker || { _reclaim_not_run "コンテナ実行系が見つかりません"; return 1; }
  RECLAIM_DOCKER=$(command -v "$(docker_command)")
  # 標準エラーの控えは台帳の隣に置き、回収の終わりに消す。
  RECLAIM_TMP="$(dirname "$path")/reclaim-$$-${RANDOM:-0}.d"
  mkdir "$RECLAIM_TMP" 2>/dev/null || { _reclaim_not_run "控えのディレクトリを作れない: $RECLAIM_TMP"; return 1; }
  trap 'rm -rf "$RECLAIM_TMP"' EXIT
  RECLAIM_LOCKED=$(_reclaim_locked_paths) || { _reclaim_not_run "worktree の一覧を読めない"; return 1; }
  _reclaim_limited /dev/null "$RECLAIM_TMP/ls.err" "$RECLAIM_DOCKER" compose ls -q || {
    _reclaim_not_run "コンテナ実行系へ繋がらない: $(_reclaim_error_line "$RECLAIM_TMP/ls.err")"
    return 1
  }

  for worktree in "${rows[@]+"${rows[@]}"}"; do
    _reclaim_classify "$worktree"
    case "$RECLAIM_CLASS" in
      stale) stale+=("$worktree") ;;
      unseen)
        _reclaim_item unseen "$(printf '%s' "$snapshot" | jq -r --arg wt "$worktree" \
          '[.assignments[] | select(.released_at == null and .worktree == $wt)] | last | .environment')" "$worktree" \
          reason "回収を打った位置から worktree の置き場を確かめられない"
        ;;
    esac
  done

  # 公開は解放の後にも閉じられる。解放済みの行の公開が開いていても破棄しない。
  _wt_read_lines < <(printf '%s' "$snapshot" | jq -r --argjson stale \
    "$(printf '%s\n' "${stale[@]+"${stale[@]}"}" | jq -Rsc 'split("\n") | map(select(length > 0))')" '
    .assignments as $all
    | [$all[] | . as $row | select(.released_at == null and any($stale[]; . == $row.worktree))]
    | group_by(.environment)[]
    | .[0].environment as $e
    | ([$all[] | select(.environment == $e and .expose != null and .expose.closed_at == null) | .expose.url] | last // "") as $url
    | "\($e)\t\(map(.worktree) | unique | join(", "))\t\($url)"')
  plan=("${WT_LINES[@]+"${WT_LINES[@]}"}")

  for line in "${plan[@]+"${plan[@]}"}"; do
    IFS=$'\t' read -r environment shown url <<<"$line"
    if [ -n "$url" ]; then
      _reclaim_item exposed "$environment" "$shown" url "$url"
    elif [ "$(_reclaim_left)" -le 0 ]; then
      _reclaim_item deferred "$environment" "$shown" reason "回収の上限に達した"
    else
      _reclaim_environment "$environment" "$shown"
    fi
    [ "$one_only" = 1 ] && [ "$RECLAIM_FREED" = 1 ] && break
  done
  _reclaim_report
}

do_reclaim() { ( reclaim_run 0 ); }

# スロットが尽きたときに 1 環境だけ空ける。結果は標準出力へ出さない（`env` の出力は割り当ての JSON だけである）。
reclaim_for_slot() {
  local report freed
  report=$( ( reclaim_run 1 ) 2>/dev/null )
  freed=$(printf '%s' "$report" | jq -r '.metrics.reclaimed + .metrics.released' 2>/dev/null)
  [ "${freed:-0}" -gt 0 ] || return 0
  printf '残った割り当てを回収しました: %s 件\n' "$freed" >&2
}
