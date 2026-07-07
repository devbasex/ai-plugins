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
if "bigquery" not in text:
    raise SystemExit(f"{runtime}: bigquery MCP config not found in {path}")
for name in ("BIGQUERY_PROJECT", "BIGQUERY_LOCATION", "BIGQUERY_DATASET", "BIGQUERY_KEY_FILE"):
    if name not in text:
        raise SystemExit(f"{runtime}: missing env placeholder {name}")
    value = os.environ.get(name)
    if value and len(value) >= 8 and value in text:
        raise SystemExit(f"{runtime}: secret value leaked into {path}")
if "/tmp/runtime-secrets" in text:
    raise SystemExit(f"{runtime}: runtime secret path leaked into {path}")
if not re.search(r"\$\{BIGQUERY_[A-Z_]+\}", text):
    raise SystemExit(f"{runtime}: expected ${...} placeholders in {path}")
PY
