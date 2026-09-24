#!/usr/bin/env bash
# cross-refactoring: フェーズごとのプロンプトを組み立てて CLI を起動する（#933）。
#
# Usage: launch-cli.sh <runtime> <phase> <ID>
#
#   runtime  claude | codex | agy | kiro
#   phase    propose | plan | add-tests | implement | fix | judge-test-changes | final-fix
#   ID       状態ファイルの鍵（最初に初期化した Pull Request 番号）
#
# **フェーズの名前は状態・履歴・`limits.py`・雛形で同じ語を使う。** 提案だけが参加者の
# 全員、残りは実装担当 1 者が担う（決定 1）。
#
# **ホストか否かで分岐しない。** ランタイム名だけで分岐する。ホストと同じランタイムが
# 実装担当になるときでも、ホストのサブエージェント機能は使わず別プロセスの CLI として
# 起動する。起動そのものは共通層の [../../../scripts/lib/launch-cli.sh] に委譲する。

set -euo pipefail

RUNTIME=${1:?runtime required}
PHASE=${2:?phase required}
ID=${3:?ID required}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# **`cd` で登らない。** `cd` は `..` を字句で畳むため、Kiro CLI が `.kiro/skills/` へ張った
# symlink の手前へ戻る。文字列のまま渡してカーネルに解決させる（バッチ 06 の契約）。
LIB=$SCRIPT_DIR/../../../scripts/lib
PROMPTS=$SCRIPT_DIR/../prompts

load_common_state() {
  command -v jq >/dev/null 2>&1 || { echo "jq が必要です" >&2; exit 1; }

  TMP_DIR=${CROSS_REFACTORING_TMP_DIR:?CROSS_REFACTORING_TMP_DIR を export してください}
  STATE=$TMP_DIR/cross-refactoring-rf$ID-state.json
  [ -s "$STATE" ] || { echo "状態ファイルがありません: $STATE" >&2; exit 1; }

  REPO=$(jq -r '.repo' "$STATE")
  PR=$(jq -r '.current_pr' "$STATE")
  ROOT=$(jq -r '.worktree_root' "$STATE")
  WORK=$(jq -r '.worktrees.work' "$STATE")
  HEAD_BRANCH=$(jq -r '.head_branch' "$STATE")
  BASE_BRANCH=$(jq -r '.base_branch' "$STATE")
  SCOPE=$(jq -r '.target_scope | join(" ")' "$STATE")
  MODEL=$(jq -r --arg rt "$RUNTIME" '.models[$rt] // ""' "$STATE")
  BASELINE_TEST=$(jq -r '.baseline_test.command // ""' "$STATE")
  # 範囲のテスト。省いた実行では空で、項目の検証は全体のテストから組み立てた語の並びを使う。
  ROUND_TEST=$(jq -r '.round_test.command // ""' "$STATE")
}

load_common_state

# CLI 側の実行時間の上限。**`start-phase` が予算から導いた上限があればその秒数を渡す**
# （テストの追加と実装。締め切りより先に CLI が止まらないため）。無ければ工程名を渡し、
# 共通層が上限の表（`lib/limits.py`）から「監視の上限 + 120 秒」を導く（#598 / #537）。
resolve_print_timeout() {
  local override
  override=$(jq -r --arg p "$PHASE" '.phases[$p].timeout // empty' "$STATE")
  if [ -n "$override" ]; then
    PRINT_TIMEOUT=$((override + 120))
  else
    PRINT_TIMEOUT=$PHASE
  fi
}

configure_phase() {
case "$PHASE" in
  propose|plan)
    # 提案と計画は読むだけ。**読み取り用の作業ディレクトリ**（`prepare-worktrees.sh` が
    # 担当ごとに detach で用意し、HEAD へ同期する）で行う。
    STEM=$TMP_DIR/$RUNTIME-$PHASE-rf$ID
    WORKDIR=$ROOT/$RUNTIME
    ;;
  add-tests|implement|fix|judge-test-changes)
    # 書き換えるフェーズは常に work/ の中だけで行う。並列には起動しない。
    STEM=$TMP_DIR/$RUNTIME-$PHASE-rf$ID
    WORKDIR=$WORK
    ;;
  final-fix)
    # **実行の番号を名前に入れない**（今の名前のまま）。取り込み側（`merge-final-fix`）も
    # この名前で探す。
    STEM=$TMP_DIR/$RUNTIME-final-fix
    WORKDIR=$WORK
    ;;
  *)
    echo "未知のフェーズです: $PHASE" >&2
    exit 1
    ;;
esac
}

configure_phase
resolve_print_timeout

# Skill の配置先はランタイムで違う。**プロンプトに明示パスを必ず書く**ため、
# ここで解決して雛形へ渡す。kiro は配置しただけでは SKILL.md 本文を読まない。
resolve_skill_base() {
SKILL_BASE=
case "$RUNTIME" in
  claude) SKILL_BASE=.claude/skills ;;
  codex)  SKILL_BASE=.agents/skills ;;
  kiro)   SKILL_BASE=.kiro/skills ;;
  # agy は codex と同じ `.agents/skills` を読む。
  agy)    SKILL_BASE=.agents/skills ;;
  *)      echo "未知のランタイムです: $RUNTIME" >&2; exit 1 ;;
esac
}

resolve_skill_base

PROMPT=$STEM-prompt.md
TEMPLATE=$PROMPTS/$PHASE.md
[ -f "$TEMPLATE" ] || { echo "プロンプト雛形がありません: $TEMPLATE" >&2; exit 1; }

# フェーズごとに渡す項目。**締め切りは項目ごとの時刻で渡す**（AC12）。
collect_items() {
case "$PHASE" in
  plan)
    # 候補の全件。`key` は計画の結果が候補を指す鍵（`path#symbol#smell`）。
    ITEMS_JSON=$(jq '[.candidates[] | {key: "\(.path)#\(.symbol)#\(.smell)", path, symbol,
      smell, technique, severity, rationale, plan, test_gap, agreed_by: (.proposed_by | length)}]' "$STATE")
    ;;
  add-tests)
    ITEMS_JSON=$(jq '[.items[] | select(.status == "planned" and ((.tests // []) | length) > 0)
      | {item_id: .id, path, symbol, smell, technique, tests, test_targets,
         start_deadline: .test_start_deadline, estimate_minutes: .estimate.test}]' "$STATE")
    ;;
  implement)
    ITEMS_JSON=$(jq '[.items[] | select(.status == "planned" or .status == "tested")
      | {item_id: .id, rank, path, symbol, smell, technique, rationale, plan, tests,
         start_deadline, estimate_minutes: .estimate.implement,
         test_command: (.command | join(" "))}]' "$STATE")
    ;;
  fix)
    # 落ちた項目だけ。**同じ語の並びを共有した項目はまとめて 1 つの修正の対象**になる。
    ITEMS_JSON=$(jq '[.items[] | select(.status == "failing")
      | {item_id: .id, path, symbol, technique, plan, fix_count,
         test_command: (.command | join(" ")), test_log: .last_log}]' "$STATE")
    ;;
  *)
    ITEMS_JSON='[]'
    ;;
esac
}

build_skill_block() {
SKILL_BLOCK="（この実行では手順書を配置していません）"
if [ -n "$SKILL_BASE" ]; then
  SKILL_BLOCK=$(cat <<SKILL_EOF
まず次のファイルを **順に読み**、その手順に従うこと。読まずに進めてはならない。

- \`$SKILL_BASE/refactoring/SKILL.md\` — 手順の本体（スメル語彙 / 手法カタログ / 現状固定テスト / 表現の判断）
- \`$SKILL_BASE/tdd-cycle/SKILL.md\` — テストが乏しい経路で現状固定テストを先に書く手順
- \`$SKILL_BASE/quality-gates/SKILL.md\` — 「直し終わった」と言える条件

スメルと手法の語彙は \`$SKILL_BASE/refactoring/references/code-smells.md\` と
\`$SKILL_BASE/refactoring/references/refactoring-catalog.md\` に限定する。
SKILL_EOF
)
fi
}

# 語彙の許容値。**手順書を読ませるだけでは足りない。** 検証側が持つ集合を状態ファイル
# 経由で受け取り、**許容値をそのまま列挙する**。観点（AC5）も同じ経路で列挙する。
collect_refactoring_vocabulary() {
VOCAB_SMELLS=$(jq -r '(.vocabulary.smells // {}) | to_entries[] | "- `\(.key)` — \(.value)"' "$STATE")
VOCAB_TECHNIQUES=$(jq -r '(.vocabulary.techniques // {}) | to_entries[] | "- `\(.key)` — \(.value)"' "$STATE")
VOCAB_SEVERITIES=$(jq -r '(.vocabulary.severities // []) | map("`" + . + "`") | join(" / ")' "$STATE")
VOCAB_VIEWPOINTS=$(jq -r '(.vocabulary.viewpoints // {}) | to_entries[] | "- `\(.key)` — \(.value)"' "$STATE")
[ -n "$VOCAB_SMELLS" ] || VOCAB_SMELLS="（状態ファイルに語彙がありません。手順書の語彙に従うこと）"
[ -n "$VOCAB_TECHNIQUES" ] || VOCAB_TECHNIQUES="（同上）"
[ -n "$VOCAB_SEVERITIES" ] || VOCAB_SEVERITIES="\`critical\` / \`major\` / \`minor\`"
[ -n "$VOCAB_VIEWPOINTS" ] || VOCAB_VIEWPOINTS="（同上）"
}

export_prompt_env() {
BUDGET_MINUTES=$(jq -r '.budget_minutes' "$STATE")
END_AT=$(jq -r '.plan.end_at // ""' "$STATE")
export RF_REPO=$REPO RF_PR=$PR RF_RUNTIME=$RUNTIME RF_PHASE=$PHASE
export RF_MODEL=${MODEL:-default} RF_WORKDIR=$WORKDIR RF_STEM=$STEM
export RF_SCOPE=$SCOPE RF_HEAD_BRANCH=$HEAD_BRANCH RF_BASE_BRANCH=$BASE_BRANCH
export RF_BASELINE_TEST=$BASELINE_TEST RF_ROUND_TEST=${ROUND_TEST:-（無し。全体のテストから項目ごとに組み立てる）}
export RF_SKILL_BLOCK=$SKILL_BLOCK RF_SKILL_BASE=$SKILL_BASE
export RF_ITEMS=$ITEMS_JSON RF_TMP_DIR=$TMP_DIR
export RF_BUDGET_MINUTES=$BUDGET_MINUTES RF_END_AT=$END_AT
export RF_VOCAB_SMELLS=$VOCAB_SMELLS RF_VOCAB_TECHNIQUES=$VOCAB_TECHNIQUES
export RF_VOCAB_SEVERITIES=$VOCAB_SEVERITIES RF_VOCAB_VIEWPOINTS=$VOCAB_VIEWPOINTS
export RF_TEST_DIFF_PATH=$TMP_DIR/test-diff-rf$ID.diff
}

collect_items
build_skill_block
collect_refactoring_vocabulary
export_prompt_env

# 雛形は `${RF_*}` を展開するだけの素の Markdown。コマンド置換は展開しない
# （プロンプト本文に `$(...)` や backtick が現れても実行させないため）。
render_prompt() {
python3 - "$TEMPLATE" > "$PROMPT" <<'PY'
import os
import string
import sys

template = string.Template(open(sys.argv[1], encoding="utf-8").read())
sys.stdout.write(template.safe_substitute(os.environ))
PY
}

render_prompt

# agy は現在地を作業領域にしない。結果ファイルの置き場所は全ランタイム共通の
# 一時ディレクトリなので、作業領域へ明示的に追加する。
launch_cli() {
EXTRA_DIR=
[ "$RUNTIME" = "agy" ] && EXTRA_DIR=$TMP_DIR

"$LIB/launch-cli.sh" "$RUNTIME" "$WORKDIR" "$PROMPT" "$STEM" "$MODEL" "$EXTRA_DIR" \
  "$PRINT_TIMEOUT"
}

launch_cli
