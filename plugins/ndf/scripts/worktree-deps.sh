#!/usr/bin/env bash
# NDF plugin: worktree の依存の用意（#1337）。
#
#   prepare <worktree> [--if-unprepared]
#       共有の宣言（.ndf/worktree.json）の `deps` 節に従って、依存物を worktree で使える状態にする。
#       copy_from_main（ハードリンクの複製）・copy_as_real（実体の複製）・run（コマンド）の順に行う。
#       --if-unprepared は、今の HEAD と宣言で用意した印があれば何もしない（使い回す worktree で使う）。
#       印が別の HEAD・宣言のもの（前に用意した worktree を同期した後）なら、既にある複製の宛先は
#       そのまま使い、無い宛先の複製と run だけをやり直す
#
# 終了コード:
#   0  用意した / 宣言が無い / --if-unprepared で今の HEAD と宣言の印があった
#   1  用意が失敗した（複製元が無い・複製できない・書き込み先が外・コマンドが 0 以外・
#      git の状態が変わった・copy_from_main のハードリンクがその場で書き換えられた）
#   3  宣言が壊れている（手順を 1 つも実行しない）
#
# **標準出力には何も出さない。** 報告は標準エラーの 1 行（`依存の用意: 済み（N 件・S 秒）` か
# `依存の用意: 失敗（<手順>・S 秒）` の後に出力の末尾 20 行）である。宣言が無いときは何も出さない。
#
# 用意の印は `git -C <worktree> rev-parse --absolute-git-dir` の `ndf-deps` に書く。worktree の中には
# 書かないため、git の状態を変えず、`git worktree remove` で一緒に消える。
# **`deps` は共有の宣言からだけ読む。** 個人の宣言の `deps` は反映しない。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/worktree-common.sh
. "$SCRIPT_DIR/lib/worktree-common.sh" 2>/dev/null || {
  printf '%s\n' "共通ライブラリを読み込めません" >&2
  exit 1
}
# shellcheck source=lib/worktree-copy.sh
. "$SCRIPT_DIR/lib/worktree-copy.sh" || exit 1

# 失敗の後に出す、手順の出力の末尾の行数。
DEPS_TAIL_LINES=20

usage() {
  printf '%s\n' "使い方: worktree-deps.sh prepare <worktree> [--if-unprepared]" >&2
  exit 1
}

SUBCOMMAND="${1:-}"
[ "$SUBCOMMAND" = prepare ] || usage
shift
TARGET=
IF_UNPREPARED=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --if-unprepared) IF_UNPREPARED=1 ;;
    -*) usage ;;
    *) [ -z "$TARGET" ] || usage; TARGET="$1" ;;
  esac
  shift
done
[ -n "$TARGET" ] || usage

TARGET_ARG=$TARGET
TARGET=$(cd "$TARGET" 2>/dev/null && pwd -P) || {
  printf '作業ツリーがありません: %s\n' "$TARGET_ARG" >&2
  exit 1
}
MAIN_DIR=$(wt_main_dir "$TARGET") || {
  printf 'git の作業ツリーではありません: %s\n' "$TARGET" >&2
  exit 1
}
DECL_FILE="$MAIN_DIR/$WT_DECLARATION_FILE"

GIT_DIR_ABS=$(git -C "$TARGET" rev-parse --absolute-git-dir 2>/dev/null) || {
  printf 'git の作業ツリーではありません: %s\n' "$TARGET" >&2
  exit 1
}
MARK="$GIT_DIR_ABS/ndf-deps"

# 印の中身。用意した時の HEAD と宣言の中身を持ち、どちらかが変われば印は効かない
# （同期で lockfile や package.json が変わった worktree を、古い依存物のまま使わない）。
deps_key() {
  local head decl=none
  head=$(git -C "$TARGET" rev-parse HEAD 2>/dev/null) || head=none
  [ -f "$DECL_FILE" ] && decl=$(cksum < "$DECL_FILE")
  printf 'head=%s decl=%s' "$head" "$decl"
}
KEY=$(deps_key)

# --if-unprepared で印が食い違うときは、前に用意した worktree のやり直しである。既にある複製の宛先は
# run（`npm ci` など）が入れ替えたり、メインディレクトリ側が更新されたりして食い違いうるため、
# 食い違いで失敗にせず、そのまま使う。
REDO=0
if [ "$IF_UNPREPARED" = 1 ] && [ -f "$MARK" ]; then
  [ "$(cat "$MARK" 2>/dev/null)" = "$KEY" ] && exit 0
  REDO=1
fi

# --- 宣言を読む ---------------------------------------------------------------

[ -f "$DECL_FILE" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

# 壊れている箇所を 1 つ返す（無ければ空）。JSON として読めなければ jq の誤りの文を返す。
broken_part() {
  local out
  if ! out=$(jq -r '
    def strings_at(k):
      (.deps[k]) as $v
      | if $v == null then empty
        elif ($v | type) == "array" and all($v[]; type == "string") then empty
        else "deps.\(k) が文字列の配列ではありません" end;
    if type != "object" then empty
    elif (.deps == null) then empty
    elif (.deps | type) != "object" then "deps が object ではありません"
    else ([strings_at("copy_from_main"), strings_at("copy_as_real"), strings_at("run")] | first // empty)
    end' "$DECL_FILE" 2>&1); then
    printf '%s\n' "$out" | head -n 1
    return 0
  fi
  printf '%s' "$out"
}

BROKEN=$(broken_part)
if [ -n "$BROKEN" ]; then
  printf '依存の用意: 宣言が壊れています（%s: %s）\n' "$DECL_FILE" "$BROKEN" >&2
  exit 3
fi

_wt_read_lines < <(jq -r 'if type == "object" then (.deps.copy_from_main // [])[] else empty end' "$DECL_FILE")
COPY_FROM_MAIN=("${WT_LINES[@]+"${WT_LINES[@]}"}")
_wt_read_lines < <(jq -r 'if type == "object" then (.deps.copy_as_real // [])[] else empty end' "$DECL_FILE")
COPY_AS_REAL=("${WT_LINES[@]+"${WT_LINES[@]}"}")
# コマンドは改行を含みうるため、1 件ずつ JSON の文字列で受けてから戻す。
_wt_read_lines < <(jq -c 'if type == "object" then (.deps.run // [])[] else empty end' "$DECL_FILE")
RUN_JSON=("${WT_LINES[@]+"${WT_LINES[@]}"}")

STEPS=$(( ${#COPY_FROM_MAIN[@]} + ${#COPY_AS_REAL[@]} + ${#RUN_JSON[@]} ))
[ "$STEPS" -gt 0 ] || exit 0

# --- 用意する -----------------------------------------------------------------

START=$SECONDS
WORK=$(mktemp -d "${TMPDIR:-/tmp}/ndf-deps.XXXXXX") || exit 1
trap 'rm -rf "$WORK"' EXIT
LOG="$WORK/log"
STAMP="$WORK/stamp"
: > "$LOG"

# 引数なしの prepare は印を見ずにやり直す。失敗の後に効く印が残らないよう、先に消す。
# やり直しでは、前に用意したことだけを残す（どの KEY とも一致しない中身にする）。
if [ "$REDO" = 1 ]; then
  printf '%s\n' redo-pending > "$MARK" 2>/dev/null || rm -f "$MARK"
else
  rm -f "$MARK"
fi

fail() {
  local step="$1"
  printf '依存の用意: 失敗（%s・%s 秒）\n' "$step" "$(( SECONDS - START ))" >&2
  tail -n "$DEPS_TAIL_LINES" "$LOG" >&2
  exit 1
}

BEFORE=$(git -C "$TARGET" status --porcelain 2>/dev/null)
touch "$STAMP"

i=0
for rel in "${COPY_FROM_MAIN[@]+"${COPY_FROM_MAIN[@]}"}"; do
  step="copy_from_main[$i] $rel"
  i=$((i + 1))
  if ! wt_is_safe_relative "$rel"; then
    printf '%s は作業ツリーの外を指します\n' "$rel" >> "$LOG"
    fail "$step"
  fi
  if [ ! -e "$MAIN_DIR/$rel" ]; then
    printf '複製元がありません: %s\n' "$MAIN_DIR/$rel" >> "$LOG"
    fail "$step"
  fi
  [ "$REDO" = 1 ] && [ -e "$TARGET/$rel" ] && ! [ -L "$TARGET/$rel" ] && continue
  copy_one "$rel" >> "$LOG" 2>&1 || fail "$step"
done

i=0
for rel in "${COPY_AS_REAL[@]+"${COPY_AS_REAL[@]}"}"; do
  step="copy_as_real[$i] $rel"
  i=$((i + 1))
  if ! wt_is_safe_relative "$rel"; then
    printf '%s は作業ツリーの外を指します\n' "$rel" >> "$LOG"
    fail "$step"
  fi
  if [ ! -e "$MAIN_DIR/$rel" ]; then
    printf '複製元がありません: %s\n' "$MAIN_DIR/$rel" >> "$LOG"
    fail "$step"
  fi
  [ "$REDO" = 1 ] && [ -e "$TARGET/$rel" ] && ! [ -L "$TARGET/$rel" ] && continue
  replace_with_real_copy "$rel" >> "$LOG" 2>&1 || fail "$step"
done

i=0
for json in "${RUN_JSON[@]+"${RUN_JSON[@]}"}"; do
  command=$(printf '%s' "$json" | jq -r '.')
  step="run[$i] $command"
  i=$((i + 1))
  (cd "$TARGET" && sh -c "$command") >> "$LOG" 2>&1 < /dev/null || fail "$step"
done

# ハードリンクの複製はメインディレクトリと同じ実体を指す。複製の後にその場で書き換えられた
# ファイルは、メインディレクトリの依存物も書き換えている。
for rel in "${COPY_FROM_MAIN[@]+"${COPY_FROM_MAIN[@]}"}"; do
  [ -e "$TARGET/$rel" ] || continue
  rewritten=$(find -P "$TARGET/$rel" -type f -links +1 -newer "$STAMP" 2>/dev/null | head -n "$DEPS_TAIL_LINES")
  if [ -n "$rewritten" ]; then
    {
      printf '%s\n' "ハードリンクで複製したファイルが、用意の中でその場で書き換えられました（メインディレクトリの同じファイルも変わっています）。書き換えるパスは copy_as_real に置いてください:"
      printf '%s\n' "$rewritten"
    } >> "$LOG"
    fail "copy_from_main の書き換え $rel"
  fi
done

AFTER=$(git -C "$TARGET" status --porcelain 2>/dev/null)
if [ "$BEFORE" != "$AFTER" ]; then
  {
    printf '%s\n' "用意の前後で git の状態が変わりました。依存物は git が無視するパスに置いてください:"
    diff <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$AFTER") | sed -n 's/^> //p'
  } >> "$LOG"
  fail "git の状態"
fi

printf '%s\n' "$KEY" > "$MARK" 2>/dev/null || {
  printf '用意の印を書けません: %s\n' "$MARK" >> "$LOG"
  fail "用意の印"
}
printf '依存の用意: 済み（%s 件・%s 秒）\n' "$STEPS" "$(( SECONDS - START ))" >&2
exit 0
