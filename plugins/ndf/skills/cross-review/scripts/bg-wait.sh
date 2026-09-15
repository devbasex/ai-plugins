#!/usr/bin/env bash
# 600 秒を超える待ちを、背景の起動と 540 秒以内に区切った待ちに分ける（#598 / #537）。
#
# Usage:
#   bg-wait.sh run  <rc ファイル> -- <コマンド> [引数…]  # 直ちに 0。出力は <rc ファイル>.log
#   bg-wait.sh wait <rc ファイル> [--max-wait <秒>]      # 既定 540、上限 540
#
# **Claude Code の Bash ツールは 1 回 600 秒で打ち切る。** 監視の上限（上限の表、レビューは
# 1200 秒）は 1 回に収まらない。`run` で監視を背景に回し、`wait` を 124 が返るあいだ
# **別の Bash の呼び出しとして** 呼び直す。終了コードは rc ファイルに残るため、待ちの
# 呼び出しをまたいでも失われない。
#
# wait の終了コード:
#   <コマンドの終了コード>  コマンドが終わった。ログの全体を標準出力へ出す
#   124                     --max-wait を過ぎてもまだ終わっていない。ログの最後の行を標準エラーへ出す
#   1                       使い方の誤り、起動していない、または背景が終了コードを残さずに終わった
#
# **背景は出力を握らない。** 標準入出力を閉じて起動しないと、呼び出し側（Bash ツール）が
# 背景の終了まで戻らない。ホストが codex / kiro / agy でも同じ書き方で動く（外部コマンドに
# 依存しない）。
#
# 置き場所は cross-review の scripts/ にする。いま使うのは cross-review だけで、
# cross-refactoring の待ちは #656 が扱う（設計の決定 13）。

set -uo pipefail

MAX_WAIT=540

usage() {
  sed -n '4,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 1
}

MODE=${1:-}
RC=${2:-}
[ -n "$MODE" ] && [ -n "$RC" ] || usage
shift 2

case "$MODE" in
  run)
    [ "${1:-}" = "--" ] || usage
    shift
    [ $# -gt 0 ] || usage
    mkdir -p "$(dirname -- "$RC")"
    # **古い rc ファイルを先に消す。** 残っていると、前の起動の終了コードを今回のものと読む。
    # **ログも消す。** 残っていると、背景が起動に失敗した回の `wait` が前回のログを出す。
    rm -f "$RC" "$RC.tmp" "$RC.pid" "$RC.log"
    # rc ファイルは一時ファイルへ書いてから置き換える（読みかけの空のファイルを返さない）。
    nohup bash -c '"${@:2}" > "$1.log" 2>&1; echo $? > "$1.tmp" && mv -f "$1.tmp" "$1"' \
      bg-wait "$RC" "$@" < /dev/null > /dev/null 2>&1 &
    echo "$!" > "$RC.pid"
    exit 0
    ;;
  wait)
    max=$MAX_WAIT
    while [ $# -gt 0 ]; do
      case "$1" in
        --max-wait) [ $# -ge 2 ] || usage; max=$2; shift 2 ;;
        *) usage ;;
      esac
    done
    case "$max" in ''|*[!0-9]*) usage ;; esac
    [ "$max" -le "$MAX_WAIT" ] || max=$MAX_WAIT
    echo "⏳ bg-wait: $RC を最大 $max 秒待ちます" >&2
    [ -e "$RC" ] || [ -e "$RC.pid" ] || {
      echo "bg-wait: 起動していません（先に run を呼ぶ）: $RC" >&2; exit 1; }

    alive() {
      local pid
      pid=$(cat "$RC.pid" 2>/dev/null) || return 1
      [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
      # 親が回収しない環境ではゾンビが残り、`kill -0` が成功し続ける。
      ! grep -q '^State:[[:space:]]*Z' "/proc/$pid/status" 2>/dev/null
    }

    deadline=$((SECONDS + max))
    while :; do
      if [ -s "$RC" ]; then
        code=$(cat "$RC")
        cat "$RC.log" 2>/dev/null
        exit "$code"
      fi
      if ! alive; then
        # 終わる直前に書かれた rc ファイルを取りこぼさない。
        [ -s "$RC" ] && continue
        echo "bg-wait: 背景のコマンドが終了コードを残さずに終わりました: $RC" >&2
        tail -n 20 "$RC.log" >&2 2>/dev/null
        exit 1
      fi
      [ "$SECONDS" -lt "$deadline" ] || break
      sleep 1
    done
    tail -n 1 "$RC.log" >&2 2>/dev/null
    exit 124
    ;;
  *)
    usage
    ;;
esac
