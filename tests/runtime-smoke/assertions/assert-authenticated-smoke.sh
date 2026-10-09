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
run_bigquery_secret_check() {
  [ -n "${BIGQUERY_ACCESS_TOKEN:-}" ] || return 1
  [ -n "${BIGQUERY_PROJECT:-}" ] || return 1
  local body status
  body="$(jq -nc --arg p "$BIGQUERY_PROJECT" \
    '{jsonrpc: "2.0", id: 1, method: "tools/call", params: {name: "list_dataset_ids", arguments: {projectId: $p}}}')"
  # 呼び出し元が `|| true` で包むため set -e は効かない。失敗は return 1 で返す
  status="$(curl -sS -m 60 -X POST https://bigquery.googleapis.com/mcp \
    -H @- \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d "$body" -o /tmp/bigquery-mcp-response.json -w '%{http_code}' \
    <<<"Authorization: Bearer $BIGQUERY_ACCESS_TOKEN")" || return 1
  if [ "$status" != 200 ] || jq -e '.result.isError == true' /tmp/bigquery-mcp-response.json >/dev/null; then
    echo "bigquery remote MCP call failed: HTTP $status" >> "$log"
    return 1
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
    run_bigquery_secret_check || true
    ;;
  codex)
    if [ -n "${OPENAI_API_KEY:-}" ]; then
      timeout 60s codex exec --dangerously-bypass-approvals-and-sandbox "Print OK only for runtime smoke." >> "$log" 2>&1
      auth_ran=true
    fi
    run_bigquery_secret_check || true
    ;;
  kiro)
    if command -v kiro-cli >/dev/null 2>&1 && [ -n "${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}" ]; then
      timeout 30s kiro-cli doctor >> "$log" 2>&1 || true
      auth_ran=true
    fi
    run_bigquery_secret_check || true
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
    run_bigquery_secret_check || true
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac

if [ "$auth_ran" = false ]; then
  if [ "$WITH_SECRETS" = required ]; then
    echo "no runtime-usable authenticated smoke secret was available for $runtime" >&2
    exit 1
  fi
  echo "authenticated smoke skipped: no runtime-usable secret for $runtime" > "$log"
fi
