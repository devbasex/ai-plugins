#!/usr/bin/env bash
# NDF plugin: issue の本文へ工程の進行を記録する。
#
#   progress-record.sh <issue番号> <工程名> [--mode M] [--pace normal|fast|auto] [--worktree P] [--plan P]
#                      [--repo <所有者>/<リポジトリ>] [--note TEXT]
#
# 工程名に `-` を渡すと、チェックリストを変えずに見出し行（モード・作業ツリー・実装計画のファイル）
# だけを更新する。`projects-sync.sh` の `mode` / `worktree` / `plan` がこの形で呼ぶ（#828）。
#
# **ボードの宣言が無いリポジトリでも進行が残る。** 記録先は issue の本文の `## 進行` の節で、
# 節の外は書き換えない（更新のたびに本文を取得し、その節だけを差し替える）。人が本文へ
# 書いた内容を消さないためである。差し替えは `lib/gh_sections.py` の `replace_section` が行い、
# 節の終わりへ目印 `<!-- ndf:section-end -->` を置く。目印より後ろ（振り返りが本文末尾へ足す
# 1 行など）は節の外として残る（#659）。
#
# **通過記録へ積むのもこのスクリプトである（#725）。** 引数のチェックを通った呼び出しごとに、
# `--mode` / `--pace` / 工程名（`-` でないとき）を 1 件ずつ、本文を書く前に積む。本文の書き換えが
# 失敗しても（`gh` が無い・issue を取得できない）積む。積めない（リポジトリを特定できない・`jq` が
# 無い・排他を取れない）ときは標準エラーへ `NOTE:` を残して続ける。鍵は `--repo` があればそのリポジトリ、
# 無ければ作業ディレクトリの origin である。`--worktree` / `--plan` / `--note` は積まない。
# 工程名が `配布` で、記録の無い必須の工程があれば、工程ごとに `items` の `kind: "stage-missing"` の要素
# （`result` は missing か conditional、`reason` に記録し直すコマンド）として返す。
#
# **結果は 1 行の結果 JSON で返す（#2227）。** 形は `lib/README.md` の「手順のスクリプトの結果」で、
# `lib/step-result.sh` の `sr_emit` が出す。記録した・変える内容が無かったときだけ `ok`（0）。
# `gh` が無ければ `stopped`（3）、本文を書けなかった（`gh` の失敗・錠を取れない）ときは `stopped`（6）で、
# `gaps` に `gh` の標準エラーの要旨を載せる。本文も通過記録も書けなければ `stopped`（1）。
# **進行管理が理由で開発の工程を止めない。** 止めない判断は呼ぶ側が持ち、ここは書けなかった事実を返す。
#
# **本文の読みから書きまでは課題ごとの錠の中で行う（#1241 の I10）。** 錠は `lib/gh_parts.py body-lock` が取り、
# その中でこのスクリプトをもう 1 度走らせる（`NDF_PROGRESS_LOCKED=1`。通過記録は外側だけが積む）。外側は
# 内側の結果 JSON を受けてまとめ、標準出力へは自分の 1 行だけを出す。錠を 30 秒（`NDF_PROGRESS_LOCK_TIMEOUT`）で
# 取れなければ、`## 進行` を書かずに欠けとして返す。
#
# 呼び出し側の誤り（引数不足・知らない工程名）は `stopped`（2）を返す。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL=progress-record
# shellcheck source=lib/step-result.sh
. "$SCRIPT_DIR/lib/step-result.sh" 2>/dev/null || { printf 'ERROR: lib/step-result.sh を読めない\n' >&2; exit 2; }
# shellcheck source=lib/projects-common.sh
. "$SCRIPT_DIR/lib/projects-common.sh" 2>/dev/null \
  || sr_emit --tool "$TOOL" --status stopped --summary "lib/projects-common.sh を読めない" --code 3
# 工程名・モード・進め方の一覧と通過記録は development-workflow のライブラリが持つ（#725 の決定 2・3）。
# **同じプラグインの根の相対で辿る。** `cd -P` で実体へ解決しない。agy の配置（dev.agy/scripts と
# dev.agy/skills/development-workflow がどちらも symlink）でも、`scripts/` の 1 つ上に `skills/` がある。
# shellcheck source=../skills/development-workflow/scripts/lib/workflow-common.sh
. "$SCRIPT_DIR/../skills/development-workflow/scripts/lib/workflow-common.sh" 2>/dev/null \
  || sr_emit --tool "$TOOL" --status stopped --summary "development-workflow の workflow-common.sh を読めない" --code 3

SECTION_HEADING="## 進行"

# どのモードで対象外（`-`）かを「モード<タブ>工程」の 1 行 1 件で、全モードの分を返す。
# 工程の記録のたびに呼ばれるため、表を 1 回だけ走査し、外部コマンドを起動しない
# （モード×工程ごとに wf_stage_class を呼ぶと列の解決のたびに tr が起動する。#1904）。
# 呼ぶのはこのスクリプトだけで、workflow-common.sh の行数の上限（例外リストの 900 行）を超えないためここに置く。
wf_skipped_stages() {
  local line name i
  local -a modes cells
  IFS=$'\t' read -r -a modes <<<"$WF_MODES"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    name="${line%%$'\t'*}"
    IFS=$'\t' read -r -a cells <<<"${line#*$'\t'}"
    for i in "${!modes[@]}"; do
      [ "${cells[$i]:-}" = "-" ] && printf '%s\t%s\n' "${modes[$i]}" "$name"
    done
  done <<<"$WF_STAGE_MATRIX"
  return 0
}

usage() {
  printf 'usage: progress-record.sh <issue番号> <工程名> [--mode M] [--pace P] [--worktree P] [--plan P] [--repo R] [--note TEXT]\n' >&2
}

# 呼び出し側の誤りを 2 で返す。
bad_args() {
  printf 'ERROR: %s\n' "$1" >&2
  [ -z "${2:-}" ] || usage
  sr_emit --tool "$TOOL" --status stopped --summary "$1" --code 2
}

ARGS=("$@")
ISSUE="${1:-}"
STAGE="${2:-}"
shift 2 2>/dev/null || true

MODE='' PACE='' WORKTREE='' PLAN='' REPO='' NOTE=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --pace) PACE="${2:-}"; shift 2 ;;
    --worktree) WORKTREE="${2:-}"; shift 2 ;;
    --plan) PLAN="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --note) NOTE="${2:-}"; shift 2 ;;
    *) bad_args "知らない引数です: $1" usage ;;
  esac
done

[ -n "$ISSUE" ] && [ -n "$STAGE" ] || bad_args "引数が足りません" usage
case "$ISSUE" in
  ''|*[!0-9]*) bad_args "issue 番号が数値ではありません: $ISSUE" ;;
esac
[ "$STAGE" = "-" ] || wf_is_stage "$STAGE" || bad_args "工程表に無い工程名です: $STAGE"
[ -z "$MODE" ] || wf_is_mode "$MODE" || bad_args "知らないモードです: $MODE"
[ -z "$PACE" ] || wf_is_pace "$PACE" || bad_args "知らない進め方です: $PACE"

# `配布` の記録で wf_record_progress が出す案内（`  記録なし: A / B`・`  条件付き: C`）を、工程ごとの
# `stage-missing` の要素へ移す。標準出力へは出さない（結果 JSON の 1 行を守る）。
absorb_stage_report() {
  local line label result rest stage
  local -a stages
  while IFS= read -r line; do
    case "$line" in
      *'記録なし: '*) label='記録なし: ' result=missing ;;
      *'条件付き: '*) label='条件付き: ' result=conditional ;;
      *) continue ;;
    esac
    rest=${line#*"$label"}
    IFS='/' read -r -a stages <<<"$rest"
    for stage in "${stages[@]}"; do
      stage=$(sr_brief "$stage")
      [ -n "$stage" ] || continue
      sr_item stage-missing "$stage" "$result" "bash \"\$SCRIPTS/projects-sync.sh\" $ISSUE stage \"$stage\""
    done
  done <<<"$1"
}

# 通過記録へ積む。**本文の書き換えより前に置く**（#725 の決定 4）。後ろの早い終わり方のどれを
# 通っても、引数のチェックを通った呼び出しは 1 回だけ積まれる。
if [ -n "$REPO" ]; then
  SLUG=$REPO
else
  SLUG=$(wf_repo_slug ".") || SLUG=''
fi
RECORD_STAGE=$STAGE
[ "$RECORD_STAGE" != "-" ] || RECORD_STAGE=''
PASSED=0
if [ -z "${NDF_PROGRESS_LOCKED:-}" ] && [ -n "$RECORD_STAGE$MODE$PACE" ]; then
  STAGE_REPORT=$(wf_record_progress "$SLUG" "$ISSUE" "$RECORD_STAGE" "$MODE" "$PACE")
  if [ -n "$SLUG" ] && command -v jq >/dev/null 2>&1; then
    PASSED=1
    sr_process "通過記録へ積む（#$ISSUE）" "積んだ"
  else
    sr_process "通過記録へ積む（#$ISSUE）" "積めない（リポジトリを特定できないか jq が無い）"
  fi
  absorb_stage_report "$STAGE_REPORT"
fi

if ! command -v gh >/dev/null 2>&1; then
  sr_emit --tool "$TOOL" --status stopped --summary "gh が無いため #$ISSUE の本文へ進行を書かない" --code 3
fi

# 外側: 錠の中で自分を走らせ、内側の結果 JSON を自分の結果へまとめる。
if [ -z "${NDF_PROGRESS_LOCKED:-}" ]; then
  LOCK_ERR=$(mktemp 2>/dev/null) || LOCK_ERR=/dev/null
  INNER=$(NDF_PROGRESS_LOCKED=1 python3 "$SCRIPT_DIR/lib/gh_parts.py" body-lock --issue "$ISSUE" ${REPO:+--repo "$REPO"} \
    --timeout "${NDF_PROGRESS_LOCK_TIMEOUT:-30}" -- bash "${BASH_SOURCE[0]}" "${ARGS[@]}" 2>"$LOCK_ERR")
  RC=$?
  ERR_TEXT=''
  if [ "$LOCK_ERR" != /dev/null ]; then
    ERR_TEXT=$(cat "$LOCK_ERR" 2>/dev/null)
    [ -z "$ERR_TEXT" ] || printf '%s\n' "$ERR_TEXT" >&2
    rm -f "$LOCK_ERR"
  fi
  SUMMARY=''
  if sr_absorb "${INNER##*$'\n'}"; then
    SUMMARY=$SR_ABSORBED_SUMMARY
  else
    printf 'NOTE: #%s の本文の錠を取れない（または記録が失敗した）。進行を書かずに続ける\n' "$ISSUE" >&2
    sr_gap issue-body "#$ISSUE" "本文の錠を取れないか、記録が結果を返さなかった（終了コード $RC）: $(sr_brief "${ERR_TEXT:-理由の出力なし}")"
  fi
  [ "$SR_GAPS" -eq 0 ] && sr_emit --tool "$TOOL" --status ok --summary "$SUMMARY"
  if [ "$PASSED" -eq 1 ]; then
    sr_emit --tool "$TOOL" --status stopped --summary "#$ISSUE の本文へ進行を書けなかった（通過記録は積んだ）" --code 6
  fi
  sr_emit --tool "$TOOL" --status stopped --summary "#$ISSUE の進行を何も書けなかった" --code 1
fi

# 内側: 本文を書けなかったことを欠けとして返す。
body_gap() {
  sr_gap issue-body "#$ISSUE" "$1"
  sr_emit --tool "$TOOL" --status stopped --summary "#$ISSUE の本文へ進行を書けなかった" --code 6
}

BODY_FILE=$(mktemp) || body_gap "一時ファイルを作れない"
NEW_FILE=$(mktemp) || { rm -f "$BODY_FILE"; body_gap "一時ファイルを作れない"; }
trap 'rm -f "$BODY_FILE" "$NEW_FILE"' EXIT

sr_gh issue view "$ISSUE" ${REPO:+--repo "$REPO"} --json body -q .body > "$BODY_FILE" \
  || body_gap "本文を読めない（gh issue view）: $SR_GH_ERR"

# 節の中身を組み立てる。**目印を付けるのは、この呼び出しが記録する工程までである。**
# 一覧の残りは空欄のまま残し、飛ばした工程がチェックの穴として見えるようにする。
STAMP=$(date '+%Y-%m-%d %H:%M')
STAGES=$(wf_stages)
# モードで通らない工程（分類が `-`）の行を書かないため、「モード<タブ>工程」を改行区切りで渡す（#1683 の決定 6）。
# モードは本文から読み直すことがあるため、全モードの分を渡す。分類は上の wf_skipped_stages が持つ。
SKIP_STAGES=$(wf_skipped_stages)
export SKIP_STAGES
export STAGES SECTION_HEADING STAGE STAMP MODE PACE WORKTREE PLAN NOTE SCRIPT_DIR
python3 - "$BODY_FILE" > "$NEW_FILE" <<'PY' || body_gap "本文の節を組めない"
import os
import re
import sys

# 節の差し替えは共通の部品が持つ（#659・#849）。書いた節の終わりへ目印を置き、目印より後ろ
# （振り返りが末尾へ足す 1 行など）を節の外として残す。
sys.path.insert(0, os.path.join(os.environ["SCRIPT_DIR"], "lib"))
from gh_sections import SECTION_END, get_section, replace_section  # noqa: E402

body = open(sys.argv[1], encoding="utf-8").read()
heading = os.environ["SECTION_HEADING"]
stages = os.environ["STAGES"].split("\n")
stage = os.environ["STAGE"]
stamp = os.environ["STAMP"]

# **目印の無い節（目印を置く前に書いた本文）は、工程の行の並びの後ろで閉じる。** 節が本文の最後に
# あると「次の見出しまで」は末尾までになり、チェックリストの後ろへ人や振り返りが足した行も節に入る。
# 見出し・モードの行・チェックリストの行の最後の行の後ろへ目印を置き、残りを節の外へ出す。
lines = body.replace("\r\n", "\n").split("\n")
head_at = next((i for i, x in enumerate(lines) if x.rstrip() == heading), None)
if head_at is not None:
    nxt = next((j for j in range(head_at + 1, len(lines)) if re.match(r"^#{1,2}[ \t]+\S", lines[j])), len(lines))
    own = [k for k in range(head_at + 1, nxt) if re.match(r"^(- \[[ x]\] |モード: )", lines[k].strip())]
    has_marker = any(lines[k].strip() == SECTION_END for k in range(head_at + 1, nxt))
    if own and not has_marker and any(lines[k].strip() for k in range(own[-1] + 1, nxt)):
        lines.insert(own[-1] + 1, SECTION_END)
        body = "\n".join(lines)

# すでにある節から、済んだ工程とその記録を読み取る。**書き直すのは節だけである。**
done: dict[str, str] = {}
mode = os.environ.get("MODE", "")
pace = os.environ.get("PACE", "")
worktree = os.environ.get("WORKTREE", "")
plan = os.environ.get("PLAN", "")
section = get_section(body, heading)
if section is not None:
    for line in section.split("\n"):
        hit = re.match(r"- \[x\] (.+?)(?: — (.*))?$", line.strip())
        if hit:
            done[hit.group(1)] = hit.group(2) or ""
    meta = re.search(r"^モード: (\S+)(?: / 進め方: (\S+))?(?: / 作業ツリー: `(.+?)`)?(?: / 計画: `(.+?)`)?$",
                     section, re.M)
    if meta:
        mode = mode or (meta.group(1) if meta.group(1) != "—" else "")
        pace = pace or (meta.group(2) or "")
        worktree = worktree or (meta.group(3) or "")
        plan = plan or (meta.group(4) or "")

# **記録するのは「工程に入った時点」である。** 同じ工程を再び呼んでも時刻を書き換えない。
# 書き換えると、途中で止まった実行を再開したときに最初に入った時刻が失われる。
# 付随情報（`--note`）を新しく渡したときだけ、その分を足す。
note = os.environ.get("NOTE", "")
if stage == "-":
    # 見出し行だけを更新する。チェックリストには目印を足さない。
    pass
elif stage in done and done[stage]:
    if note and note not in done[stage]:
        done[stage] = f"{done[stage]} / {note}"
else:
    done[stage] = " / ".join([stamp] + ([note] if note else []))

meta_parts = [f"モード: {mode or '—'}"]
if pace in ("fast", "auto"):  # 既定（normal）は書かない
    meta_parts.append(f"進め方: {pace}")
if worktree:
    meta_parts.append(f"作業ツリー: `{worktree}`")
if plan:
    meta_parts.append(f"計画: `{plan}`")

# **モードで通らない工程（`SKIP_STAGES` のそのモードの行）は、記録が無ければ書かない（#1683）。**
# 記録の済んだ行は列に関わらず残す（モードを途中で変えた課題）。モードが `—` か知らない値なら全工程を書く。
skipped = {s for m, _, s in (row.partition("\t") for row in os.environ.get("SKIP_STAGES", "").split("\n") if row) if m == mode}

lines = [" / ".join(meta_parts), ""]
for name in stages:
    if name in skipped and name not in done:
        continue
    if name in done:
        lines.append(f"- [x] {name} — {done[name]}" if done[name] else f"- [x] {name}")
    else:
        lines.append(f"- [ ] {name}")
sys.stdout.write(replace_section(body, heading, "\n".join(lines)))
PY

[ -s "$NEW_FILE" ] || body_gap "本文の節を組めない（出力が空）"
if [ "$STAGE" = "-" ]; then
  DONE_TEXT="#$ISSUE 進行の見出し = $(sed -n '/^## 進行[[:space:]]*$/,/^## /{/^モード: /p;}' "$NEW_FILE" | head -1)"
else
  DONE_TEXT="#$ISSUE 進行 = $STAGE"
fi
if cmp -s "$BODY_FILE" "$NEW_FILE"; then
  # 変える内容が無いときは書き込まない。同じ操作を 2 度行っても結果が変わらない。
  sr_item issue-body "#$ISSUE" unchanged
  sr_emit --tool "$TOOL" --status ok --summary "$DONE_TEXT（変える内容が無い）"
fi
sr_gh issue edit "$ISSUE" ${REPO:+--repo "$REPO"} --body-file "$NEW_FILE" >/dev/null \
  || body_gap "本文を書けない（gh issue edit）: $SR_GH_ERR"
sr_item issue-body "#$ISSUE" recorded
sr_emit --tool "$TOOL" --status ok --summary "$DONE_TEXT"
