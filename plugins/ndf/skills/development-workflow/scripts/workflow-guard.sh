#!/usr/bin/env bash
# NDF plugin: tool 実行前の hook。設計 Pull Request のマージを承認ラベルに縛り（#266）、
# Pull Request の作成の時点で実行証跡を案内する（#424）。
#
# `development-workflow` の frontmatter が、この Skill を呼んだ会話の単位へ登録する。
# 判定はすべて lib/ が持ち、この入口は入力の受け取りと出力の整形だけを行う。
#
# **進捗記録は観測しない（#725）。** 通過記録へ積むのは記録のスクリプト（progress-record.sh）自身である。
# この hook が見るのは、外部のコマンドを観測するしかない判定（マージと Pull Request の作成）だけである。
#
# 出力は 3 通りである。いずれも終了コード 0 で返す。
#   - 拒否（permissionDecision: deny）— 設計 Pull Request のマージだけ
#   - 案内（additionalContext）— Pull Request の作成の時点の、記録の無い必須の工程
#   - 何も出力しない — 判定の対象でないとき、条件を満たしたとき
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/workflow-common.sh
. "$SCRIPT_DIR/lib/workflow-common.sh" 2>/dev/null || exit 0

PAYLOAD=$(cat 2>/dev/null || true)
[ -n "$PAYLOAD" ] || exit 0

# **jq や hook の環境が無くても、マージらしい本文は止める。** 入力を読み解けないことを通す
# 理由にしない（決定 8）。#424 の案内はここで諦める（通す側へ倒す）。
#
# hook の環境（SessionStart が用意した python。`wf_hook_python`）を条件へ入れるのは、`wf_split` が語の分割を
# その python で行うためである（#1142 の決定 20）。環境が無いと分割の結果が空になり、`wf_merge_target` は
# 何も見つけられないまま 1 を返す。呼び出し元の `wf_check_merge` はそれを「マージではない」と読んで
# 0（許可）を返すため、拒否の判定へ一度も入らない。ここで grep による粗い見分けへ倒し、fail-closed を保つ。
if ! command -v jq >/dev/null 2>&1 || ! wf_hook_python >/dev/null; then
  if wf_looks_like_merge_text "$PAYLOAD"; then
    reason=$(wf_deny_undetermined "" '承認ラベル（判定に要る jq または hook の環境が無い）')
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

# --- #424 Pull Request の作成の時点で実行証跡を見る --------------------------
#
# **拒否はしない。** 判定できないときは何も出さずに通す（決定 4）。
ENTRIES=$(wf_parse_pr_create "$COMMAND") || exit 0
EVIDENCE=$(wf_evidence_report <<<"$ENTRIES") || exit 0
[ -n "$EVIDENCE" ] && wf_emit_context "$EVIDENCE"
exit 0
