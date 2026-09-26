#!/usr/bin/env bash
# 対象のアプリケーションが HTTP で応答するまで待つ。テスト実行の前に起動を確かめる。
#
# 2xx / 3xx / 4xx の応答を「起動している」とみなす（ログインへのリダイレクトや 401 も
# サーバは動いている）。5xx と接続できない状態は起動していないとみなし、時間切れまで繰り返す。
#
# 標準出力は 1 行の JSON: {"url": ..., "ready": true|false, "status": <HTTP コード|null>, "attempts": N, "elapsed_s": N}
#
# 終了コード:
#   0  起動している
#   1  時間切れ（最後の応答を status に返す。接続できなければ null）
#   2  引数の誤り・curl が無い
#
# Usage:
#   scripts/app_ready.sh http://localhost:8080/ [--timeout 60] [--interval 1]
set -u

usage_error() {
  printf '{"url": %s, "ready": false, "status": null, "error": "%s"}\n' "${1:-null}" "$2"
  exit 2
}

json_str() {
  local s=${1//\\/\\\\}
  s=${s//\"/\\\"}
  printf '"%s"' "$s"
}

url=""
timeout=60
interval=1
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout) timeout=${2:-}; shift 2 || usage_error null "--timeout に値が要る" ;;
    --interval) interval=${2:-}; shift 2 || usage_error null "--interval に値が要る" ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    -*) usage_error null "未知のオプション: $1" ;;
    *) [ -z "$url" ] || usage_error "$(json_str "$1")" "URL は 1 つだけ渡す"; url=$1; shift ;;
  esac
done

[ -n "$url" ] || usage_error null "URL が要る"
[[ "$timeout" =~ ^[0-9]+$ ]] || usage_error "$(json_str "$url")" "--timeout は秒の整数"
[[ "$interval" =~ ^[0-9]+(\.[0-9]+)?$ ]] || usage_error "$(json_str "$url")" "--interval は秒の数値"
command -v curl >/dev/null 2>&1 || usage_error "$(json_str "$url")" "curl が無い"

start=$(date +%s)
deadline=$((start + timeout))
attempts=0
status=null
per_request=$(( timeout < 5 ? (timeout > 0 ? timeout : 1) : 5 ))

while :; do
  attempts=$((attempts + 1))
  code=$(curl -k -s -o /dev/null -w '%{http_code}' --max-time "$per_request" "$url" 2>/dev/null)
  if [[ "$code" =~ ^[1-9][0-9][0-9]$ ]]; then
    status=$code
    if [ "$code" -lt 500 ]; then
      printf '{"url": %s, "ready": true, "status": %s, "attempts": %d, "elapsed_s": %d}\n' \
        "$(json_str "$url")" "$status" "$attempts" "$(( $(date +%s) - start ))"
      exit 0
    fi
  else
    status=null
  fi
  [ "$(date +%s)" -lt "$deadline" ] || break
  sleep "$interval"
  [ "$(date +%s)" -lt "$deadline" ] || break
done

printf '{"url": %s, "ready": false, "status": %s, "attempts": %d, "elapsed_s": %d}\n' \
  "$(json_str "$url")" "$status" "$attempts" "$(( $(date +%s) - start ))"
exit 1
