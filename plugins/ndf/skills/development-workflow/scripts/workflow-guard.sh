#!/usr/bin/env bash
# NDF plugin: tool 実行前の hook。工程の飛ばしを検知し、設計 Pull Request のマージを
# 承認の印に縛る（#221 / #266）。
#
# `development-workflow` の frontmatter が、この Skill を呼んだ会話の単位へ登録する。
# 判定はすべて lib/ が持ち、この入口は入力の受け取りと出力の整形だけを行う。
#
# 出力は 3 通りである。いずれも終了コード 0 で返す。
#   - 拒否（permissionDecision: deny）— 設計 Pull Request のマージだけ
#   - 案内（additionalContext）— 記録の無い必須の工程
#   - 何も出力しない — 判定の対象でないとき、条件を満たしたとき
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/workflow-common.sh
. "$SCRIPT_DIR/lib/workflow-common.sh" 2>/dev/null || exit 0

PAYLOAD=$(cat 2>/dev/null || true)
[ -n "$PAYLOAD" ] || exit 0

# **jq や awk が無くても、マージらしい本文は止める。** 入力を読み解けないことを通す
# 理由にしない（決定 8）。#221 の報告はここで諦める（通す側へ倒す）。
#
# awk を条件へ入れるのは、`wf_split` が語の分割を awk で行うためである。awk が無いと
# 分割の結果が空になり、`wf_merge_target` は何も見つけられないまま 1 を返す。呼び出し元の
# `wf_check_merge` はそれを「マージではない」と読んで 0（許可）を返すため、拒否の判定へ
# 一度も入らない。ここで grep による粗い見分けへ倒し、fail-closed を保つ。
if ! command -v jq >/dev/null 2>&1 || ! command -v awk >/dev/null 2>&1; then
  if wf_looks_like_merge_text "$PAYLOAD"; then
    reason=$(wf_deny_undetermined "" '承認の印（判定に要る jq または awk が無い）')
    wf_emit_deny "$reason"
  fi
  exit 0
fi

jq -e . >/dev/null 2>&1 <<<"$PAYLOAD" || exit 0
jq_get() { jq -r "$1" 2>/dev/null <<<"$PAYLOAD"; }

[ "$(jq_get '.hook_event_name // empty')" = "PreToolUse" ] || exit 0
[ "$(jq_get '.tool_name // empty')" = "Bash" ] || exit 0

COMMAND=$(jq_get '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# **判定の対象になりうる本文だけを走査する。** この hook は tool 実行のたびに走るため、
# 当たらない本文で語の分割まで進むと、無関係なコマンドの費用になる。
wf_is_candidate "$COMMAND" || exit 0

CWD=$(jq_get '.cwd // empty')
if [ -n "$CWD" ] && [ -d "$CWD" ]; then
  cd "$CWD" 2>/dev/null || true
fi

# --- #266 設計 Pull Request のマージ ----------------------------------------
if ! REASON=$(wf_check_merge "$COMMAND"); then
  wf_emit_deny "$REASON"
  exit 0
fi

# --- #221 進行の記録を観測して積む ------------------------------------------
SYNC=$(wf_parse_sync "$COMMAND") || exit 0
IFS=$'\t' read -r ISSUE KEY VALUE <<<"$SYNC"
case "$ISSUE" in ''|*[!0-9]*) exit 0 ;; esac
case "$KEY" in stage|mode) ;; *) exit 0 ;; esac
[ -n "$VALUE" ] || exit 0
if [ "$KEY" = "stage" ]; then wf_is_stage "$VALUE" || exit 0; fi
if [ "$KEY" = "mode" ]; then wf_is_mode "$VALUE" || exit 0; fi

SLUG=$(wf_repo_slug ".") || exit 0
wf_record "$SLUG" "$ISSUE" "$KEY" "$VALUE" 2>/dev/null

# 配布の記録へ進んだときだけ、記録の無い必須の工程を案内する。
[ "$KEY" = "stage" ] && [ "$VALUE" = "$WF_REPORT_STAGE" ] || exit 0
REPORT=$(wf_report "$SLUG" "$ISSUE")
case "$REPORT" in
  *'記録なし:'*|*'条件付き:'*) wf_emit_context "$REPORT" ;;
esac
exit 0
