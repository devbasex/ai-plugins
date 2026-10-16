#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
: "${WITH_SECRETS:=off}"
: "${ARTIFACT_DIR:=/tmp/runtime-artifacts}"
: "${PROJECT_DIR:=/tmp/runtime-project}"

log="$ARTIFACT_DIR/authenticated-smoke.log"

if [ "$WITH_SECRETS" = off ]; then
  echo "authenticated smoke skipped: --with-secrets=off" > "$log"
  exit 0
fi

auth_ran=false

# プラグインは gcloud のアクセストークンでリモートの BigQuery MCP サーバーを呼ぶ。
# 同じトークンで list_dataset_ids を 1 回呼び、認証が通ることを確かめる。
# 入力が無ければ確かめずに戻る。入力があって失敗したときは、ほかの認証の成否に
# かかわらず bigquery_failed を立て、最後に非 0 で終える。
bigquery_failed=false
run_bigquery_secret_check() {
  [ -n "${BIGQUERY_ACCESS_TOKEN:-}" ] && [ -n "${BIGQUERY_PROJECT:-}" ] || return 0
  local body status response payload
  response="$(mktemp)"
  body="$(jq -nc --arg p "$BIGQUERY_PROJECT" \
    '{jsonrpc: "2.0", id: 1, method: "tools/call", params: {name: "list_dataset_ids", arguments: {projectId: $p}}}')"
  if ! status="$(curl -sS -m 60 -X POST https://bigquery.googleapis.com/mcp \
    -H @- \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d "$body" -o "$response" -w '%{http_code}' \
    <<<"Authorization: Bearer $BIGQUERY_ACCESS_TOKEN" 2>>"$log")"; then
    echo "bigquery remote MCP call failed: curl error" >> "$log"
    bigquery_failed=true
    rm -f "$response"
    return 0
  fi
  # 応答は JSON か SSE のどちらでもよい。SSE なら最後の data 行を JSON として読む
  payload="$(grep -q '^data:' "$response" && sed -n 's/^data: *//p' "$response" | tail -n 1 || cat "$response")"
  rm -f "$response"
  # 成功は id 1 の応答に result があり、error も isError も無いときだけ
  if [ "$status" != 200 ] || ! jq -e '.id == 1 and (.result | type == "object") and (has("error") | not) and (.result.isError != true)' \
      >/dev/null 2>&1 <<<"$payload"; then
    echo "bigquery remote MCP call failed: HTTP $status" >> "$log"
    bigquery_failed=true
    return 0
  fi
  echo "bigquery remote MCP list_dataset_ids succeeded" >> "$log"
  auth_ran=true
}

case "$runtime" in
  claude)
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
      timeout 60s claude -p "runtime smoke: reply OK only" >> "$log" 2>&1
      auth_ran=true
    fi
    run_bigquery_secret_check
    ;;
  codex)
    if [ -n "${OPENAI_API_KEY:-}" ]; then
      timeout 60s codex exec --dangerously-bypass-approvals-and-sandbox "Print OK only for runtime smoke." >> "$log" 2>&1
      auth_ran=true
    fi
    run_bigquery_secret_check
    ;;
  kiro)
    if command -v kiro-cli >/dev/null 2>&1 && [ -n "${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}" ]; then
      timeout 30s kiro-cli doctor >> "$log" 2>&1 || true
      auth_ran=true
    fi
    run_bigquery_secret_check
    ;;
  agy)
    # agy の認証は CLI が持つ。API キーの環境変数を取らないため、`agy models` の成否で
    # 認証済みかを判定する。Skill 一覧の取得は認証を要するので、ここへ置く。
    if command -v agy >/dev/null 2>&1 && timeout 60s agy models >> "$log" 2>&1; then
      timeout 120s agy --output-format text --dangerously-skip-permissions \
        -p="読み込めている Skill の名前を、1 行に 1 つだけ列挙して。説明は書かないで。" \
        >> "$log" 2>&1 || true
      auth_ran=true
    fi
    run_bigquery_secret_check
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac

if [ "$bigquery_failed" = true ]; then
  echo "bigquery authenticated smoke failed for $runtime (see $log)" >&2
  exit 1
fi

if [ "$auth_ran" = false ]; then
  if [ "$WITH_SECRETS" = required ]; then
    echo "no runtime-usable authenticated smoke secret was available for $runtime" >&2
    exit 1
  fi
  echo "authenticated smoke skipped: no runtime-usable secret for $runtime" > "$log"
fi
