#!/usr/bin/env bash
# hooks 定義をランタイム自身に読ませ、読み込みの報告（警告・誤り）が 1 件でもあれば落とす。
#
# 受け取るキーの一覧はこちらで持たない。判定はランタイムの報告に従う（#571 の設計の決定 1）。
#   claude: `claude --debug-file <log> --plugin-dir ... plugin list` のデバッグログ
#   codex : `codex app-server` の `hooks/list` の応答（lib/codex-hooks-list.py）
#
# 本物の判定は「報告 0 件」で通る。報告の形が変わった版や報告を出さない古い像では何もチェック
# しないまま通るため、先に陽性対照（fixtures/hooks-positive-control）を読ませ、報告を受け
# 取れることを確かめてから本物を判定する（決定 4）。
#
# 読ませるたびに空の設定ディレクトリを作る。アダプタが導入済みの設定へ重ねると導入済みの
# 定義も読まれて数が混ざり、他のチェックが見る設定にも書き込むことになる（決定 5）。
set -euo pipefail

runtime="${1:-}"
case "$runtime" in
  claude|codex) ;;
  *) echo "usage: $0 <claude|codex>" >&2; exit 2 ;;
esac

: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${ARTIFACT_DIR:=/tmp/runtime-artifacts}"
: "${HOME:=/tmp/runtime-home}"

FIXTURE_ROOT="$REPO_ROOT/tests/runtime-smoke/fixtures/hooks-positive-control"
HOOKS_LIST="$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py"
OUT_DIR="$ARTIFACT_DIR/hook-definitions"
mkdir -p "$OUT_DIR"

WORK_ROOT="$(mktemp -d)"
trap 'rm -rf "$WORK_ROOT"' EXIT

# マーケットプレイス定義から、そのランタイムの hooks 定義を持つプラグインを見つける（決定 6）。
# 1 行に 1 プラグインを「マーケットプレイス上の名前 TAB plugin.json の名前 TAB ディレクトリ」で書く。
discover_targets() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

root, runtime = Path(sys.argv[1]), sys.argv[2]
manifest_dir = {"claude": ".claude-plugin", "codex": ".codex-plugin"}[runtime]
marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
for entry in marketplace.get("plugins", []):
    plugin_dir = (root / entry["source"]).resolve()
    manifest_path = plugin_dir / manifest_dir / "plugin.json"
    if not manifest_path.is_file():
        continue
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if "hooks" in manifest or (plugin_dir / "hooks" / "hooks.json").is_file():
        print(f"{entry['name']}\t{manifest.get('name', entry['name'])}\t{plugin_dir}")
PY
}

marketplace_name() {
  jq -r '.name' "$1/.claude-plugin/marketplace.json"
}

# ---- Claude Code -------------------------------------------------------------

# parse_claude_debug_log <log> <reports_file> <loaded_file>
parse_claude_debug_log() {
  local log="$1" reports_file="$2" loaded_file="$3"
  # 報告は hooks に触れる警告と誤りのすべてとする。未知キーの `[WARN] Plugin <名前>: hooks.json: ...`、
  # JSON の誤りの `[ERROR] Failed to load hooks for <名前>`、マニフェストが指す先が無いときの
  # `[ERROR] Hooks file ... not found` がこの形で出る（最後のものは hooks.json が別にあると
  # 読み込みの行も出るため、「読まれていない」では拾えない）。
  # タグは大文字だけに一致させ（`-i` を付けると小文字の `[error]` にも当たる）、`hook` /
  # `hooks` / `Hooks` は前後が英数字でない語として一致させる（部分一致では `webhook` のような
  # hooks と無関係の誤りまで報告に数える）。`broken-hooks` のように記号で区切られた名前は拾う。
  grep -E '\[(WARN|ERROR)\] (.*[^[:alnum:]])?[Hh]ooks?([^[:alnum:]]|$)' "$log" >"$reports_file" || true
  sed -nE 's/.*Read (hooks\.json|manifest hooks) for plugin ([^ ]+) \(.*/\2/p' "$log" \
    | sort -u >"$loaded_file"
}

# claude_load <label> <plugin dir>...
# 報告の行を $OUT_DIR/claude-<label>.reports に、読まれたプラグイン名を .loaded に書く。
claude_load() {
  local label="$1"; shift
  local log="$OUT_DIR/claude-$label.log" config args=() dir rc=0
  config="$(mktemp -d "$WORK_ROOT/claude-config.XXXXXX")"
  for dir in "$@"; do args+=(--plugin-dir "$dir"); done
  rm -f "$log"
  CLAUDE_CONFIG_DIR="$config" claude --debug-file "$log" "${args[@]}" plugin list \
    </dev/null >"$OUT_DIR/claude-$label.stdout" 2>&1 || rc=$?
  # 読み込みに失敗しても plugin list は 0 を返す（実測 #5）。0 以外は起動そのものの失敗である。
  if [ "$rc" -ne 0 ]; then
    echo "claude plugin list failed while loading the $label hooks (exit $rc):" >&2
    cat "$OUT_DIR/claude-$label.stdout" >&2
    exit 1
  fi
  [ -f "$log" ] || { echo "claude did not write the debug log: $log" >&2; exit 1; }
  parse_claude_debug_log "$log" "$OUT_DIR/claude-$label.reports" "$OUT_DIR/claude-$label.loaded"
}

# ---- Codex -------------------------------------------------------------------

# codex_install <label> <home> <marketplace root> <marketplace 上の名前>...
# 隔離した CODEX_HOME へ marketplace を登録し、指定のプラグインを導入する。
codex_install() {
  local label="$1" home="$2" root="$3"; shift 3
  local name mname install_log="$OUT_DIR/codex-$label.install.log"
  mname="$(marketplace_name "$root")"
  : >"$install_log"
  CODEX_HOME="$home" codex plugin marketplace add "$root" >>"$install_log" 2>&1 \
    || { echo "codex plugin marketplace add failed for the $label hooks:" >&2; cat "$install_log" >&2; exit 1; }
  for name in "$@"; do
    CODEX_HOME="$home" codex plugin add "$name@$mname" >>"$install_log" 2>&1 \
      || { echo "codex plugin add $name@$mname failed:" >&2; cat "$install_log" >&2; exit 1; }
  done
}

# parse_codex_hooks_list <response> <reports_file> <loaded_file>
# hooks/list の応答から報告（警告・誤り）と読まれたプラグイン名を書き出す。
parse_codex_hooks_list() {
  local response="$1" reports_file="$2" loaded_file="$3"
  jq -r '.data[] | (.warnings // [])[], (.errors // [])[] | if type == "string" then . else tojson end' \
    "$response" >"$reports_file"
  # 読まれた = hooks が 1 つ以上登録された（空の定義は報告なしで一覧から消える: 実測 #15）
  jq -r '.data[].hooks[]?.pluginId | split("@")[0]' "$response" \
    | sort -u >"$loaded_file"
}

# codex_load <label> <marketplace root> <marketplace 上の名前>...
codex_load() {
  local label="$1" root="$2"; shift 2
  local home workdir
  local response="$OUT_DIR/codex-$label.json"
  home="$(mktemp -d "$WORK_ROOT/codex-home.XXXXXX")"
  workdir="$(mktemp -d "$WORK_ROOT/codex-cwd.XXXXXX")"
  codex_install "$label" "$home" "$root" "$@"
  CODEX_HOME="$home" python3 "$HOOKS_LIST" --cwd "$workdir" >"$response" \
    || { echo "could not read hooks/list from codex app-server for the $label hooks" >&2; exit 1; }
  parse_codex_hooks_list "$response" "$OUT_DIR/codex-$label.reports" "$OUT_DIR/codex-$label.loaded"
}

# ---- 判定 --------------------------------------------------------------------

runtime_version() {
  "$runtime" --version 2>/dev/null | tail -n 1
}

mapfile -t targets < <(discover_targets "$REPO_ROOT" "$runtime")
if [ "${#targets[@]}" -eq 0 ]; then
  echo "no plugin with hooks definitions found" >&2
  exit 1
fi

entry_names=() manifest_names=() dirs=()
for line in "${targets[@]}"; do
  IFS=$'\t' read -r entry manifest dir <<<"$line"
  entry_names+=("$entry"); manifest_names+=("$manifest"); dirs+=("$dir")
done
echo "==> hooks definitions ($runtime): ${manifest_names[*]}"

mapfile -t control < <(discover_targets "$FIXTURE_ROOT" "$runtime")
IFS=$'\t' read -r control_entry _ control_dir <<<"${control[0]:?positive control fixture has no plugin for $runtime}"

# 陽性対照を先に読ませ、報告を受け取れることを確かめてから本物を読ませる
case "$runtime" in
  claude) claude_load positive-control "$control_dir" ;;
  codex) codex_load positive-control "$FIXTURE_ROOT" "$control_entry" ;;
esac
if [ ! -s "$OUT_DIR/$runtime-positive-control.reports" ]; then
  echo "$runtime $(runtime_version) did not report the broken hooks fixture; the report format may have changed or the image is stale" >&2
  exit 1
fi
echo "positive control reported: $(head -n 1 "$OUT_DIR/$runtime-positive-control.reports")"

# Claude Code のログは plugin.json の名前を、Codex の pluginId はマーケットプレイス上の名前を書く
case "$runtime" in
  claude)
    claude_load plugins "${dirs[@]}"
    expected=("${manifest_names[@]}")
    ;;
  codex)
    codex_load plugins "$REPO_ROOT" "${entry_names[@]}"
    expected=("${entry_names[@]}")
    ;;
esac

status=0
if [ -s "$OUT_DIR/$runtime-plugins.reports" ]; then
  echo "$runtime reported hooks definitions:" >&2
  cat "$OUT_DIR/$runtime-plugins.reports" >&2
  status=1
fi

missing=()
for name in "${expected[@]}"; do
  grep -Fxq "$name" "$OUT_DIR/$runtime-plugins.loaded" || missing+=("$name")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "$runtime did not load hooks for: ${missing[*]}" >&2
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "hooks definitions ($runtime): no report, all ${#expected[@]} plugins loaded"
fi
exit "$status"
