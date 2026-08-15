#!/usr/bin/env bash
# cross-refactoring: フェーズごとのプロンプトを組み立てて CLI を起動する。
#
# Usage: launch-cli.sh <runtime> <phase> <ID> [ROUND]
#
#   runtime  claude | codex | gemini | kiro
#   phase    propose | apply | review | fix
#   ID       状態ファイルの鍵（最初に初期化した Pull Request 番号）
#   ROUND    propose 以外で必須
#
# **ホストか否かで分岐しない。** ランタイム名だけで分岐する。ホストと同じランタイムが
# 実装担当になるラウンドでも、ホストのサブエージェント機能は使わず別プロセスの CLI と
# して起動する。起動そのものは共通層の [lib/launch-cli.sh] に委譲する。

set -euo pipefail

RUNTIME=${1:?runtime required}
PHASE=${2:?phase required}
ID=${3:?ID required}
ROUND=${4:-}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LIB=$(cd -- "$SCRIPT_DIR/../../cross-review/scripts/lib" && pwd)
PROMPTS=$SCRIPT_DIR/../prompts

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
MAX_ITEMS=$(jq -r '.max_items_per_round' "$STATE")

# ラウンド番号は表示と項目の絞り込みに使う。未指定なら開いている最新ラウンドを採る。
[ -n "$ROUND" ] || ROUND=$(jq -r '.rounds | length' "$STATE")

case "$PHASE" in
  propose)
    STEM=$TMP_DIR/$RUNTIME-propose-rf$ID
    WORKDIR=$ROOT/$RUNTIME
    ;;
  apply|fix)
    [ "$ROUND" -ge 1 ] 2>/dev/null || { echo "$PHASE には ROUND が必要です" >&2; exit 1; }
    STEM=$TMP_DIR/$RUNTIME-$PHASE-r$ROUND
    # 適用と修正は常に work/ の中だけで行う。並列適用はしない。
    WORKDIR=$WORK
    ;;
  review)
    [ "$ROUND" -ge 1 ] 2>/dev/null || { echo "review には ROUND が必要です" >&2; exit 1; }
    STEM=$TMP_DIR/$RUNTIME-review-r$ROUND
    WORKDIR=$ROOT/$RUNTIME
    ;;
  *)
    echo "未知のフェーズです: $PHASE" >&2
    exit 1
    ;;
esac

# Skill の配置先はランタイムで違う。**プロンプトに明示パスを必ず書く**ため、
# ここで解決して雛形へ渡す。kiro は配置しただけでは SKILL.md 本文を読まない。
# `set -u` 下で未定義参照にならないよう、分岐の前に必ず初期化する。
SKILL_BASE=
case "$RUNTIME" in
  claude) SKILL_BASE=.claude/skills ;;
  codex)  SKILL_BASE=.agents/skills ;;
  kiro)   SKILL_BASE=.kiro/skills ;;
  # gemini は NDF の配布先ではないが、**語彙を読ませないと提案が全件降格する**ため
  # 同じ形の場所へ配置し、明示パスで読ませる。
  gemini) SKILL_BASE=.gemini/skills ;;
  *)      echo "未知のランタイムです: $RUNTIME" >&2; exit 1 ;;
esac

PROMPT=$STEM-prompt.md
TEMPLATE=$PROMPTS/$PHASE.md
[ -f "$TEMPLATE" ] || { echo "プロンプト雛形がありません: $TEMPLATE" >&2; exit 1; }

# 採用済みの改善項目（適用 / レビュー / 修正で使う）。提案の段階ではまだ無い。
ITEMS_JSON='[]'
if [ "$PHASE" != "propose" ]; then
  ITEMS_JSON=$(jq --argjson r "$ROUND" '[.items[] | select(.round == $r)]' "$STATE")
fi
# 見送った提案は「対象外」として渡し、毎ラウンド同じ提案が出続けるのを防ぐ。
EXCLUDED=$(jq -r '[.deferred_items[] | "- \(.path)#\(.symbol) （\(.smell)）: \(.defer_reason // "見送り")"] | join("\n")' "$STATE")
[ -n "$EXCLUDED" ] || EXCLUDED="（なし）"

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

export RF_REPO=$REPO RF_PR=$PR RF_ROUND=${ROUND:-} RF_RUNTIME=$RUNTIME
export RF_MODEL=${MODEL:-default} RF_WORKDIR=$WORKDIR RF_STEM=$STEM
export RF_SCOPE=$SCOPE RF_HEAD_BRANCH=$HEAD_BRANCH RF_BASE_BRANCH=$BASE_BRANCH
export RF_BASELINE_TEST=$BASELINE_TEST RF_MAX_ITEMS=$MAX_ITEMS
export RF_SKILL_BLOCK=$SKILL_BLOCK RF_EXCLUDED=$EXCLUDED
export RF_ITEMS=$ITEMS_JSON RF_TMP_DIR=$TMP_DIR

# 雛形は `${RF_*}` を展開するだけの素の Markdown。コマンド置換は展開しない
# （プロンプト本文に `$(...)` や backtick が現れても実行させないため）。
python3 - "$TEMPLATE" > "$PROMPT" <<'PY'
import os
import string
import sys

template = string.Template(open(sys.argv[1], encoding="utf-8").read())
sys.stdout.write(template.safe_substitute(os.environ))
PY

# gemini は作業領域の外への書き込みが拒否される。結果ファイルの置き場所は
# 全ランタイム共通の一時ディレクトリなので、作業領域へ明示的に追加する。
EXTRA_DIR=
[ "$RUNTIME" = "gemini" ] && EXTRA_DIR=$TMP_DIR

"$LIB/launch-cli.sh" "$RUNTIME" "$WORKDIR" "$PROMPT" "$STEM" "$MODEL" "$EXTRA_DIR"
