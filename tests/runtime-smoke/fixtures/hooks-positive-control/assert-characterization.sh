#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:?}"
base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

mkdir -p "$base/bin" "$base/cwd"
cat >"$base/bin/codex" <<'PY'
#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == 2:
        print(json.dumps({"id": 2, "error": {"message": "fixture error"}}), flush=True)
        break
PY
chmod +x "$base/bin/codex"

rc=0
PATH="$base/bin:$PATH" python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" \
  --cwd "$base/cwd" >"$base/hooks-list.stdout" 2>"$base/hooks-list.stderr" || rc=$?
if [ "$rc" -ne 1 ] || ! grep -Fq "hooks/list returned an error" "$base/hooks-list.stderr"; then
  echo "codex-hooks-list did not report the hooks/list error (exit $rc)" >&2
  cat "$base/hooks-list.stderr" >&2
  exit 1
fi

cat >"$base/bin/codex" <<'SH'
#!/usr/bin/env bash
touch "${RUNTIME_CALLED:?}"
exit 99
SH
chmod +x "$base/bin/codex"

rc=0
RUNTIME_CALLED="$base/runtime-called" \
REPO_ROOT="$REPO_ROOT/tests/runtime-smoke/fixtures/hooks-positive-control/no-hooks" \
ARTIFACT_DIR="$base/artifacts" HOME="$base/home" PATH="$base/bin:$PATH" \
  "$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-definitions.sh" codex \
  >"$base/no-hooks.stdout" 2>"$base/no-hooks.stderr" || rc=$?
if [ "$rc" -ne 1 ] || ! grep -Fq "no plugin with hooks definitions found" "$base/no-hooks.stderr"; then
  echo "assert-hook-definitions did not reject a marketplace without hooks (exit $rc)" >&2
  cat "$base/no-hooks.stderr" >&2
  exit 1
fi
if [ -e "$base/runtime-called" ]; then
  echo "assert-hook-definitions started codex before rejecting a marketplace without hooks" >&2
  exit 1
fi

echo "hooks characterization tests: pass"
