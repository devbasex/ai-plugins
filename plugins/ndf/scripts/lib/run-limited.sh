#!/usr/bin/env bash
# NDF plugin: コマンドを別のプロセスグループで走らせ、期限か呼び出し元の消失で止める（#2660 の決定 7）。
#
#   run-limited.sh <秒> <猶予の秒> <呼び出し元の番号> <コマンド…>
#
# 終了コードはコマンドの値をそのまま返し、時間切れは 137 である。
#
# **ジョブ制御を張ったシェルから背景で起動する。** 起動した番号がそのままプロセスグループの
# 番号になり、コマンドも監視も同じグループに入る。`setsid` は macOS に標準で無く、`timeout` は
# ホストによって無いため、どちらも使わない。
#
#   ( set -m; bash run-limited.sh 300 5 "$$" cmd </dev/null & wait "$!" )
set -u

# 待ちの刻みをロックと共有する。
# shellcheck source=lock-common.sh
. "$(dirname "${BASH_SOURCE[0]}")/lock-common.sh" || exit 1

seconds=$1
grace=$2
caller=$3
shift 3

signal_group() { kill "-$1" -- "-$$" 2>/dev/null; }
# 呼び出し元が消えたら猶予を置かない。呼び出し元の持つ予約はすぐ捨てられるため、破棄を残さない。
check_caller() { kill -0 "$caller" 2>/dev/null || signal_group KILL; }

"$@" &
child=$!

deadline=$(($(date +%s) + seconds))
while kill -0 "$child" 2>/dev/null; do
  check_caller
  if [ "$(date +%s)" -ge "$deadline" ]; then
    # コマンドが SIGTERM で終わっても、子プロセスが残っていても、時間切れは 137 で返す。
    trap '' TERM
    signal_group TERM
    limit=$(($(date +%s) + grace))
    while kill -0 "$child" 2>/dev/null && [ "$(date +%s)" -lt "$limit" ]; do
      check_caller
      _ndf_lock_sleep
    done
    signal_group KILL
  fi
  _ndf_lock_sleep
done

wait "$child"
