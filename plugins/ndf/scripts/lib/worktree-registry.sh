#!/usr/bin/env bash
# NDF plugin: テスト環境の採番と台帳・排他・スロット。
#
# `worktree-common.sh` が source する。呼び出し側はこのファイルを直に source せず、
# `worktree-common.sh` だけを source する。

# --- テスト環境の採番と台帳 --------------------------------------------------

# 共通の git ディレクトリの絶対パスを返す。
# `rev-parse --path-format=absolute` は git 2.31 以降にしかない。素の
# `--git-common-dir` は呼び出し元からの相対パスを返すことがあるため、そこで
# 解決する。要求する git の版を上げずに同じ結果を得る。
wt_common_git_dir() {
  local dir="${1:-}" common
  [ -n "$dir" ] || return 1
  common=$(git -C "$dir" rev-parse --git-common-dir 2>/dev/null) || return 1
  _wt_abs_in "$dir" "$common"
}

# 台帳の位置。共通の git ディレクトリ配下へ置く。作業ツリーの中に置くと、その
# 作業ツリーを削除した時点で割り当ての記録が消える (詳細設計 06 の決定 7)。
wt_registry_path() {
  local main_dir="${1:-}" common
  common=$(wt_common_git_dir "$main_dir") || return 1
  printf '%s/ndf/worktree-registry.json\n' "$common"
}

# 割り当てを解放しても行を消さないため、解放済みの行は増え続ける。
# 1 年を超えた解放済みの行は読み取り時に無視する。**削除はしない。**
WT_REGISTRY_KEEP_DAYS=365

# 空きスロットの上限。0 から数えるため 64 個。
WT_SLOT_MAX=63

# 環境名を作る。`<リポジトリ>-wt-<ブランチ>-<要約値 6 桁>` を小文字英数と `-` に
# 揃え、40 文字で切る。同じ作業ツリーには常に同じ値が返る。
# 名前は 40 文字で切る。**要約値は必ず残す。** 単純に末尾を落とすと、先頭が
# 同じ長いブランチ名どうしで同じ名前になり、テスト環境が混ざる。
WT_ENV_NAME_MAX=40
WT_ENV_DIGEST_LEN=6

_wt_slug() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9-]/-/g' -e 's/--*/-/g' -e 's/^-//' -e 's/-$//'
}

wt_env_name() {
  local main_dir="${1:-}" branch="${2:-}" repo digest head room name
  [ -n "$main_dir" ] && [ -n "$branch" ] || return 1
  digest=$(printf '%s' "$branch" | (sha1sum 2>/dev/null || shasum 2>/dev/null) | cut -c"1-$WT_ENV_DIGEST_LEN")
  [ -n "$digest" ] || return 1

  repo=$(_wt_slug "$(basename "$main_dir")")
  branch=$(_wt_slug "$branch")

  # 要約値と区切りに WT_ENV_DIGEST_LEN + 1 文字を残し、その手前を切る。
  room=$((WT_ENV_NAME_MAX - (WT_ENV_DIGEST_LEN + 1)))
  head=$(printf '%s-wt-%s' "$repo" "$branch" | cut -c "1-$room")
  head=${head%-}
  name=$(printf '%s-%s' "$head" "$digest")
  printf '%s\n' "$(_wt_slug "$name")"
}

# ポート番号を返す。`<帯の下限> + スロット*20 + 役割番号`。
# 判定だけを行い、宣言の読み取りは呼び出し側が持つ（テストのため）。
wt_port_for() {
  local band_low="${1:-}" slot="${2:-}" role_number="${3:-}"
  case "$band_low$slot$role_number" in
    *[!0-9]*|"") return 1 ;;
  esac
  printf '%s\n' "$((band_low + slot * 20 + role_number))"
}

# 期間の表記を秒へ直す。`90` / `90s` / `45m` / `2h` / `1d` を受ける。
wt_duration_seconds() {
  local value="${1:-}" number unit
  [ -n "$value" ] || return 1
  number=${value%[smhd]}
  case "$number" in ""|*[!0-9]*) return 1 ;; esac
  unit=${value#"$number"}
  case "$unit" in
    ""|s) printf '%s\n' "$number" ;;
    m) printf '%s\n' "$((number * 60))" ;;
    h) printf '%s\n' "$((number * 3600))" ;;
    d) printf '%s\n' "$((number * 86400))" ;;
    *) return 1 ;;
  esac
}

# 台帳を読む。無ければ空の台帳を返す。
wt_registry_read() {
  local path="${1:-}"
  if [ -s "$path" ] && jq -e . "$path" >/dev/null 2>&1; then
    cat "$path"
    return 0
  fi
  printf '{"version":1,"assignments":[]}\n'
}

# 読み取り時に無視する行を落とした台帳を返す。
# 解放から WT_REGISTRY_KEEP_DAYS を超えた行は数にも一覧にも入れない。
# **ファイルからは消さない。**
wt_registry_visible() {
  local path="${1:-}"
  wt_registry_read "$path" | jq --argjson keep "$WT_REGISTRY_KEEP_DAYS" '
    .assignments |= map(
      select(.released_at == null
             or ((.released_at | fromdateiso8601) > (now - ($keep * 86400))))
    )' 2>/dev/null
}

_wt_registry_write() {
  local path="$1" content="$2" tmp
  mkdir -p "$(dirname "$path")" 2>/dev/null
  tmp=$(mktemp "${path}.XXXXXX" 2>/dev/null) || return 1
  printf '%s\n' "$content" >"$tmp" || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$path" 2>/dev/null || { rm -f "$tmp"; return 1; }
}

# --- 排他 -------------------------------------------------------------------
#
# **実装は `lock-common.sh` の 1 箇所にある**（#293）。ここに置くのは、既存の名前で
# 呼べるようにするための委譲だけである。`flock` を使わない理由・関門が 2 段である
# 理由・陳腐化の判定・`set -C` を部分シェルの中だけで張る理由は、いずれも共通ファイルの
# 同じ節にある。
#
# **共通ファイルは自分の位置からの相対で指す。** `cd` で戻ってから `pwd` を取る形は
# 採らない。`cd` は `..` を論理パスに対して字句で畳むため、Skill だけを複製する
# Kiro CLI の配置では symlink の手前へ戻り、プラグインルートを外す（#293 の実測）。
#
# **読み込めないときは、常に取得できないものとして定義する。** 排他を取れないことと、
# 排他なしで共有ファイルへ書くことは別である。呼び出し元はいずれも「取得できなかった」
# ときの分岐を持ち、そこでは共有ファイルへ書かない。
# shellcheck source=lock-common.sh
if ! . "$(dirname "${BASH_SOURCE[0]}")/lock-common.sh" 2>/dev/null; then
  ndf_lock_acquire() { return 1; }
  ndf_lock_release() { [ -n "${1:-}" ] || return 0; rm -rf "$1" 2>/dev/null; return 0; }
  ndf_lock_is_held() { return 1; }
fi

# 捨ててよいと見なすまでの分数。共通ファイルの値を、既存の名前でも引けるようにする。
WT_LOCK_STALE_MINUTES="${NDF_LOCK_STALE_MINUTES:-5}"

# 待ちの上限の既定は 5 秒である。**この既定に環境変数の上書きは置かない。**
# 上書きは通過記録の側（`NDF_STAGE_LOCK_TIMEOUT`）だけが持つ。
wt_lock_acquire() {
  local dir="${1:-}" timeout="${2:-5}"
  ndf_lock_acquire "$dir" "$timeout"
}

wt_lock_release() {
  ndf_lock_release "$@"
}

wt_lock_is_held() {
  ndf_lock_is_held "$@"
}

_wt_lock_discard() {
  _ndf_lock_discard "$@"
}

_wt_lock_is_stale() {
  _ndf_lock_is_stale "$@"
}

_wt_registry_apply() {
  local content updated
  content=$(wt_registry_read "$_WT_REGISTRY_TARGET")
  updated=$(printf '%s' "$content" | jq "${_WT_REGISTRY_ARGS[@]+"${_WT_REGISTRY_ARGS[@]}"}" \
    "$_WT_REGISTRY_PROGRAM" 2>/dev/null) || return 1
  _wt_registry_write "$_WT_REGISTRY_TARGET" "$updated"
}

# 台帳の更新を排他のもとで行う。**読み込み・変更・書き出しを 1 つのロックの中にまとめる。**
# 判定も jq のプログラムの中で行うこと。区間の外で読んで中で書くと、同時に走った
# 別のプロセスと同じ番号を割り当ててしまう。
#
# 使い方: wt_registry_update <台帳> <jq プログラム> [jq の引数...]
# 値は jq の引数として渡す。プログラムの文字列へ埋め込むと、引用符を含む
# ブランチ名やパスで壊れる。
wt_registry_update() {
  _WT_REGISTRY_TARGET="${1:-}"
  _WT_REGISTRY_PROGRAM="${2:-}"
  [ -n "$_WT_REGISTRY_TARGET" ] && [ -n "$_WT_REGISTRY_PROGRAM" ] || return 1
  shift 2
  _WT_REGISTRY_ARGS=("$@")
  mkdir -p "$(dirname "$_WT_REGISTRY_TARGET")" 2>/dev/null

  # 排他の手段は 1 つに揃える。`flock` の有無で使うロックが分かれると、
  # 同じ台帳を別の場所から同時に触ったときに互いを見落とす。
  local lock="${_WT_REGISTRY_TARGET}.lockdir" rc
  wt_lock_acquire "$lock" 5 || return 1
  _wt_registry_apply
  rc=$?
  wt_lock_release "$lock"
  return "$rc"
}

# 作業ツリーへ割り当てられているスロットを返す。無ければ 1 を返す。
wt_slot_of() {
  local main_dir="${1:-}" worktree="${2:-}" path slot
  path=$(wt_registry_path "$main_dir") || return 1
  slot=$(wt_registry_visible "$path" \
    | jq -r --arg wt "$worktree" \
      '[.assignments[] | select(.released_at == null and .worktree == $wt)] | last | .slot // empty' 2>/dev/null)
  [ -n "$slot" ] || return 1
  printf '%s\n' "$slot"
}

# 作業ツリーへスロットを割り当てる。既に割り当てがあれば同じ番号を返す。
# 空きが無ければ 1 を返す。
#
# **空きの判定と行の追加を 1 つの jq プログラムで行う。** 排他区間の外で空きを
# 読むと、同時に走った別のプロセスと同じ番号を掴む。
wt_slot_acquire() {
  local main_dir="${1:-}" worktree="${2:-}" branch="${3:-}" environment="${4:-}"
  local path
  path=$(wt_registry_path "$main_dir") || return 1

  wt_registry_update "$path" '
    ([.assignments[] | select(.released_at == null)]) as $active
    | if ($active | map(select(.worktree == $wt)) | length) > 0 then .
      else
        ([$active[] | .slot]) as $used
        | ([range(0; $max + 1)] - $used | first) as $slot
        | if $slot == null then .
          else
            .assignments += [{
              id: $id, worktree: $wt, branch: $branch, environment: $environment,
              slot: $slot, ports: {}, assigned_at: (now | todate),
              last_used_at: (now | todate), released_at: null, expose: null
            }]
          end
      end'     --arg wt "$worktree" --arg branch "$branch" --arg environment "$environment"     --arg id "$(date -u +%Y%m%dT%H%M%SZ)-$$" --argjson max "$WT_SLOT_MAX" || return 1

  wt_slot_of "$main_dir" "$worktree"
}

# 割り当ての未解放行を対象に、指定した代入式でフィールドを更新する。
_wt_slot_set_field() {
  local main_dir="${1:-}" worktree="${2:-}" assignment="${3:-}"
  if [ "$#" -ge 3 ]; then
    shift 3
  else
    shift "$#"
  fi
  local path
  path=$(wt_registry_path "$main_dir") || return 1
  wt_registry_update "$path" "
    .assignments |= map(
      if .worktree == \$wt and .released_at == null then ${assignment} else . end
    )" --arg wt "$worktree" "$@"
}

# 割り当てを解放する。**行は消さず、解放の時刻を書き込む。**
wt_slot_release() {
  local main_dir="${1:-}" worktree="${2:-}"
  _wt_slot_set_field "$main_dir" "$worktree" '.released_at = (now | todate)'
}

# 割り当てへポートを記録する。
wt_slot_set_ports() {
  local main_dir="${1:-}" worktree="${2:-}" ports_json="${3:-}"
  _wt_slot_set_field "$main_dir" "$worktree" '.ports = $ports' --argjson ports "$ports_json"
}

# 最後に使った時刻を記録する。reap の判定が読む。
wt_slot_touch() {
  local main_dir="${1:-}" worktree="${2:-}"
  _wt_slot_set_field "$main_dir" "$worktree" '.last_used_at = (now | todate)'
}
