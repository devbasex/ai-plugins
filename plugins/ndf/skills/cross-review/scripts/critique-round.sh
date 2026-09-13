#!/usr/bin/env bash
# cross-review 反証の 1 ラウンド（#549 レビュー対応）。
#
# Usage: critique-round.sh <STATE_PR> <ROUND> <担当> [<担当>...]
#
# 起動（`critique.sh`）→ 監視（`monitor.py`）→ 取り込み（`state.py collect-critiques`）
# を 1 つにまとめる。**手順書の骨組みには置かない。** 骨組みは実行して確かめられず、
# 30 秒の停止も再取得の分岐もテストで固定できない。
#
# **監視へ渡すのは pid ファイルができた担当だけである。** `critique.sh` は反証の対象が
# 無い担当（自分の指摘だけ、または指摘 0 件）で `launch-cli.sh` を呼ばずに終わるため
# `<stem>.pid` を作らない。全員を渡すと `monitor.py` が未起動の担当の pid ファイルを
# 30 秒ポーリングしたうえで `PIDFILE_BAD`（終了コード 6）を返す。**両者とも指摘 0 件で
# 収束するラウンドでも毎回 30 秒止まる。**
#
# **取り直しは同じラウンドで 1 度だけである。** `collect-critiques` は有効な反証が
# 揃わないと終了コード 7 と `CRITIQUE_RETRY_AGENTS` を返す。その担当だけを起動し直して
# 取り込みをやり直す。2 度目も揃わなければ印を付けないまま進む（そのラウンドは全件を
# 数えるため、未検証のまま収束することはない）。

set -uo pipefail

STATE_PR=${1:?STATE_PR required}
ROUND=${2:?ROUND required}
shift 2
[ $# -gt 0 ] || { echo "担当を 1 つ以上渡してください" >&2; exit 1; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_tmpdir.sh
. "$SCRIPT_DIR/_tmpdir.sh"
TMP_DIR=$(tmpdir)

TARGETS=("$@")

for _attempt in 1 2; do
  LAUNCHED=()
  for r in "${TARGETS[@]}"; do
    # 1 者の起動の失敗でラウンドを止めない。届かなかったことは取り込みの側が数える。
    "$SCRIPT_DIR/critique.sh" "$r" "$STATE_PR" "$ROUND" || true
    [ -f "$TMP_DIR/$r-critique-pr$STATE_PR.pid" ] && LAUNCHED+=("$r")
  done

  if [ ${#LAUNCHED[@]} -gt 0 ]; then
    # 反証の結果は `<stem>-result.json` ではないため `--no-require-result` で待つ。
    "$SCRIPT_DIR/monitor.py" "$STATE_PR" \
      --agents "$(IFS=,; echo "${LAUNCHED[*]}")" \
      --stem-template '{agent}-critique-pr{id}' --no-require-result || true
  fi

  # 置換の終了コードは変数で受けてから読む（`eval` が潰すため）。
  COLLECT_OUT=$("$SCRIPT_DIR/state.py" collect-critiques "$STATE_PR")
  COLLECT_RC=$?
  [ "$COLLECT_RC" -eq 7 ] || exit "$COLLECT_RC"

  RETRY_LINE=$(printf '%s\n' "$COLLECT_OUT" | grep '^CRITIQUE_RETRY_AGENTS=') || {
    echo "反証が揃わないのに取り直す担当が返りません" >&2
    exit 1
  }
  eval "$RETRY_LINE"
  read -r -a TARGETS <<<"$CRITIQUE_RETRY_AGENTS"
done

exit 0
