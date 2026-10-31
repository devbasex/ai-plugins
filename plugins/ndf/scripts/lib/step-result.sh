#!/usr/bin/env bash
# NDF plugin: シェルの手順のスクリプトが、Python と同じ形の結果 JSON を 1 行出すための関数（#2227）。
#
#   . "$SCRIPT_DIR/lib/step-result.sh"
#   sr_gh issue view "$ISSUE" --json body -q .body > "$f" || sr_gap issue-body "#$ISSUE" "$SR_GH_ERR"
#   sr_item issue-body "#$ISSUE" recorded
#   sr_emit --tool progress-record --status ok --summary "#$ISSUE 進行 = 設計"
#
# **JSON をシェルで組まない。** 形の検査とエスケープは `step_result.py` の `emit` の 1 か所が持つ
# （結果の形の正本は同じディレクトリの README.md の「手順のスクリプトの結果」）。ここは値を 1 つずつ
# 別の引数として溜め、`python3 step_result.py emit` へ渡すだけである。
#
# 溜める値（`sr_emit` が `emit` の引数の後ろへ足す）:
#   SR_ARGS   `--item` / `--gap` / `--process` / `--decision` / `--metric` の引数の並び
#   SR_GAPS   溜めた欠けの件数（呼ぶ側が状態と終了コードを決めるのに使う）
#   SR_GH_ERR 直前の `sr_gh` が失敗したときの標準エラーの要旨（改行を空白にし先頭 300 文字）

SR_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SR_ARGS=()
SR_GAPS=0
SR_GH_ERR=''

sr_item() { SR_ARGS+=(--item "$1" "$2" "$3" "${4:-}"); }
sr_gap() { SR_ARGS+=(--gap "$1" "$2" "${3:-理由が分からない}"); SR_GAPS=$((SR_GAPS + 1)); }
sr_process() { SR_ARGS+=(--process "$1" "$2"); }
sr_decision() { SR_ARGS+=(--decision "$1" "$2"); }
sr_metric() { SR_ARGS+=(--metric "$1" "$2"); }

# 文を 1 行の要旨にする（改行を空白に、前後の空白を落とし、先頭 300 文字。`step_result.gh_json` と同じ長さ）。
sr_brief() {
  local s="${1//$'\r'/ }"
  s="${s//$'\n'/ }"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "${s:0:300}"
}

# gh を走らせる。標準出力はそのまま通し、失敗したら標準エラーの要旨を SR_GH_ERR に残して gh の終了コードを返す。
# gh が無ければ SR_GH_ERR に「gh が無い」を入れて 127 を返す。コマンド置換（`$(sr_gh ...)`）の中で呼ぶと
# SR_GH_ERR は呼ぶ側へ届かない。そのときは `2>&1` で出力ごと受け、`sr_brief` で要旨にする。
sr_gh() {
  local errf rc err
  SR_GH_ERR=''
  if ! command -v gh >/dev/null 2>&1; then
    SR_GH_ERR='gh が無い'
    return 127
  fi
  errf=$(mktemp) || { gh "$@"; return $?; }
  gh "$@" 2>"$errf"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    err=$(cat "$errf" 2>/dev/null)
    SR_GH_ERR=$(sr_brief "$err")
    [ -n "$SR_GH_ERR" ] || SR_GH_ERR="gh が終了コード $rc で失敗した（標準エラーは空）"
  fi
  rm -f "$errf"
  return "$rc"
}

# 別の手順のスクリプトの結果 JSON（1 行）の items / gaps / process / decisions を SR_ARGS へ取り込む。
# 取り込んだ結果の status と summary を SR_ABSORBED_STATUS / SR_ABSORBED_SUMMARY に残す。結果 JSON でない・形が誤っていれば 1 を返し、何も足さない。
# shellcheck disable=SC2034  # source した側が読む
SR_ABSORBED_STATUS='' SR_ABSORBED_SUMMARY=''
sr_absorb() {
  local line
  local -a got=()
  SR_ABSORBED_STATUS='' SR_ABSORBED_SUMMARY=''
  line="${1:-}"
  [ -n "$line" ] || return 1
  # NUL 区切りの値はコマンド置換で落ちるため、プロセス置換から直に配列へ読む。読めなければ値が出ない
  mapfile -t -d '' got < <(printf '%s' "$line" | python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1])
from step_result import RECORD_KEYS, validate_result
try:
    obj = json.loads(sys.stdin.read())
except ValueError:
    sys.exit(1)
if validate_result(obj):
    sys.exit(1)
out = [obj["status"], obj["summary"]]
for it in obj["items"]:
    out += ["--item", str(it.get("kind", "")), str(it.get("name", "")), str(it.get("result", "")), str(it.get("reason", ""))]
flag = {"gaps": "--gap", "process": "--process", "decisions": "--decision"}
for field, keys in RECORD_KEYS.items():
    for it in obj.get(field, []):
        out += [flag[field], *(it[k] for k in keys)]
sys.stdout.write("\0".join(out) + "\0")
' "$SR_LIB_DIR" 2>/dev/null)
  [ "${#got[@]}" -gt 1 ] || return 1
  # shellcheck disable=SC2034  # source した側が読む
  SR_ABSORBED_STATUS=${got[0]} SR_ABSORBED_SUMMARY=${got[1]}
  local i=2
  while [ "$i" -lt "${#got[@]}" ]; do
    [ "${got[$i]}" = "--gap" ] && SR_GAPS=$((SR_GAPS + 1))
    SR_ARGS+=("${got[$i]}")
    i=$((i + 1))
  done
  return 0
}

# 呼び出し側の誤りを標準エラーへ 1 行出し、stopped / 2 の結果で終える。第 3 引数に関数名を渡すと、先にそれ（usage）を呼ぶ。
sr_bad_args() {
  printf 'ERROR: %s\n' "$2" >&2
  [ -z "${3:-}" ] || "$3"
  sr_emit --tool "$1" --status stopped --summary "$2" --code 2
}

# 結果 JSON を 1 行出し、その終了コードでシェルを終える。引数は `step_result.py emit` のもの
# （--tool / --status / --summary / --code ほか）。溜めた SR_ARGS を後ろへ足す。
# 形が誤っていれば何も出さず 2 で終わる。python3 が無ければ理由を標準エラーへ出して 2 で終わる。
sr_emit() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'ERROR: python3 が無いため結果を出せない\n' >&2
    exit 2
  fi
  python3 "$SR_LIB_DIR/step_result.py" emit "$@" "${SR_ARGS[@]+"${SR_ARGS[@]}"}"
  exit $?
}
