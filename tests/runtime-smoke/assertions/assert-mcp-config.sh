#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
config="${2:?config path is required}"

python3 - "$runtime" "$config" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

runtime = sys.argv[1]
path = Path(sys.argv[2])
data = json.loads(path.read_text(encoding="utf-8"))
text = json.dumps(data, ensure_ascii=False)
server = data.get("mcpServers", {}).get("bigquery")
if not server:
    raise SystemExit(f"{runtime}: bigquery MCP config not found in {path}")
if server.get("url") != "https://bigquery.googleapis.com/mcp":
    raise SystemExit(f"{runtime}: bigquery MCP must point to the remote endpoint in {path}")
# Claude Code は headersHelper、Codex は http_headers_helper でトークンを取り直す
for key in ("headersHelper", "http_headers_helper"):
    if "gcloud auth print-access-token" not in server.get(key, ""):
        raise SystemExit(f"{runtime}: {key} must fetch the token with gcloud in {path}")
# Kiro CLI はヘルパーを持たないため、環境変数の placeholder を展開して送る
if server.get("headers", {}).get("Authorization") != "Bearer ${BIGQUERY_ACCESS_TOKEN}":
    raise SystemExit(f"{runtime}: expected Bearer ${{BIGQUERY_ACCESS_TOKEN}} placeholder in {path}")
token = os.environ.get("BIGQUERY_ACCESS_TOKEN")
if (token and len(token) >= 8 and token in text) or re.search(r"Bearer ya29\.", text):
    raise SystemExit(f"{runtime}: access token leaked into {path}")
if "/tmp/runtime-secrets" in text:
    raise SystemExit(f"{runtime}: runtime secret path leaked into {path}")
PY
