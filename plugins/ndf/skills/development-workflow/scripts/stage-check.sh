#!/usr/bin/env bash
# NDF plugin: 通過工程の控えを記録し、報告する（#221）。
#
#   stage-check.sh record <課題番号> <stage|mode> <値>
#   stage-check.sh report <課題番号>
#
# **終了コードで工程を止めない。** 呼び出し側の誤りだけを 2 で返す。`projects-sync.sh`
# が採っている扱いと同じである。排他を取れなかったときは標準エラーへ 1 行だけ残し、
# 書き込みを行わずに 0 で終わる。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/workflow-common.sh
. "$SCRIPT_DIR/lib/workflow-common.sh" 2>/dev/null || exit 0

usage() {
  printf 'usage: stage-check.sh record <課題番号> <キー: stage|mode> <値>\n' >&2
  printf '       stage-check.sh report <課題番号>\n' >&2
}

SUB="${1:-}"
ISSUE="${2:-}"

case "$SUB" in
  record|report) ;;
  *) printf 'ERROR: 知らない副命令です: %s\n' "${SUB:-（無し）}" >&2; usage; exit 2 ;;
esac
case "$ISSUE" in
  ''|*[!0-9]*) printf 'ERROR: 課題番号が数値ではありません: %s\n' "${ISSUE:-（無し）}" >&2; usage; exit 2 ;;
esac

# リポジトリを特定できないときは何もしない。控えは課題番号だけでは一意にならない。
SLUG=$(wf_repo_slug ".") || {
  printf 'NOTE: origin の URL を取れないため、進行の控えは扱いません\n' >&2
  exit 0
}

if [ "$SUB" = "report" ]; then
  [ "$#" -eq 2 ] || { printf 'ERROR: 引数が多すぎます\n' >&2; usage; exit 2; }
  wf_report "$SLUG" "$ISSUE"
  exit 0
fi

KEY="${3:-}"
VALUE="${4:-}"
if [ "$#" -ne 4 ] || [ -z "$KEY" ] || [ -z "$VALUE" ]; then
  printf 'ERROR: 引数が足りません\n' >&2
  usage
  exit 2
fi
case "$KEY" in
  stage)
    wf_is_stage "$VALUE" || {
      printf 'ERROR: 工程表に無い工程です: %s\n' "$VALUE" >&2; exit 2; }
    ;;
  mode)
    wf_is_mode "$VALUE" || {
      printf 'ERROR: 知らないモードです: %s\n' "$VALUE" >&2; exit 2; }
    ;;
  *)
    printf 'ERROR: 知らないキーです: %s\n' "$KEY" >&2
    usage
    exit 2
    ;;
esac

wf_record "$SLUG" "$ISSUE" "$KEY" "$VALUE"
exit 0
