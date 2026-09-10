#!/usr/bin/env bash
# cross-review 反証の起動（#156 の 3 本目）。
#
# Usage: critique.sh <runtime> <STATE_PR> <ROUND>
#
#   runtime      claude | codex | agy | kiro
#
# **提案者以外の担当が、各指摘へ 1 つの値を返す。** 値は support / refute /
# insufficient_evidence / duplicate / out_of_scope の 5 つで、`state.py
# collect-critiques` が `finding_id` で突き合わせて指摘へ結ぶ。
#
# **自分が出した指摘は渡さない。** 自己支持を数に入れると、1 者が出した指摘が常に
# 1 票を持つ。統合された指摘では `origin_runtimes` に載る担当すべてが提案者である。
#
# **同じラウンドの 2 段目として回す。** 新しいラウンドを足すと、収束の上限（12）の
# 意味が変わる。
#
# 状態ファイル: $TMP_DIR/<runtime>-critique-pr<STATE_PR>-round<ROUND>.json

set -euo pipefail

RUNTIME=${1:?runtime required}
STATE_PR=${2:?STATE_PR required}
ROUND=${3:?ROUND required}
case "$RUNTIME" in
  claude|codex|agy|kiro) ;;
  *) echo "未知のランタイムです: $RUNTIME" >&2; exit 1 ;;
esac

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_tmpdir.sh
. "$SCRIPT_DIR/_tmpdir.sh"
TMP_DIR=$(tmpdir)

STATE=$TMP_DIR/cross-review-pr$STATE_PR-state.json
[ -s "$STATE" ] || { echo "state.json not found: $STATE" >&2; exit 1; }

WORKTREE=$(jq -r '.worktree_path' "$STATE")
PR=$(jq -r '.current_pr' "$STATE")

# **この担当が提案者でない指摘だけを渡す。** origin_runtimes に載る担当は
# すべて提案者である（統合した組を含む）。束ねられた側も渡さない。
TARGETS=$(jq -r --arg agent "$RUNTIME" --argjson round "$ROUND" '
  [ (.review_findings // [])[]
    | select(.round == $round)
    | select(has("merged_into") | not)
    | select(((.origin_runtimes // [.agent]) | index($agent)) == null)
    | {finding_id, path, line, severity, body, evidence, falsification,
       suggested_check} ]' "$STATE")

if [ "$(printf '%s' "$TARGETS" | jq 'length')" = "0" ]; then
  echo "⏭ $RUNTIME: 反証の対象がありません（すべて自分の指摘）"
  exit 0
fi

OUT=$TMP_DIR/$RUNTIME-critique-pr$STATE_PR-round$ROUND.json
rm -f "$OUT"

STEM=$TMP_DIR/$RUNTIME-critique-pr$STATE_PR
PROMPT=$STEM-prompt.md
cat > "$PROMPT" <<EOF
# 反証: PR #$PR round $ROUND

**他の担当が出した指摘を読み、1 件ずつ賛否を返してください。** 新しい指摘は出しません。

## 返す値

| 値 | いつ使うか |
| --- | --- |
| \`support\` | 根拠を**独立に確認できた**。同じ結論へ自分でも到達できる |
| \`refute\` | 反例・仕様・コードから**誤りを示せる**。何がそう言えるかを reason へ書く |
| \`insufficient_evidence\` | 可能性はあるが立証できない |
| \`duplicate\` | 別の指摘と同一。相手の \`finding_id\` を \`duplicate_of\` へ書く |
| \`out_of_scope\` | この Pull Request の範囲・目的から外れる |

**\`refute\` の代わりに \`duplicate\` や \`out_of_scope\` を使わないでください。**
どちらも「指摘が誤っている」という主張ではありません。範囲外の指摘は、この Pull Request で
直さないだけで、課題としては残ります。

**\`support\` は「確かにそう見える」では足りません。** 自分でコードを読み、同じ経路へ
到達できたときだけ使ってください。到達できなければ \`insufficient_evidence\` です。

## 対象の指摘

\`\`\`json
$TARGETS
\`\`\`

## 作業

1. \`cd $WORKTREE\` で作業する
2. 各指摘の \`evidence\`（根拠）と \`falsification\`（反証条件）を読み、コードで確かめる
3. **リポジトリを編集しない。** 読むだけである

## 書き出すもの

**$OUT** へ次の形で書く。

\`\`\`json
{"critiques": [
  {"finding_id": "<対象の finding_id>", "verdict": "<5 つのいずれか>",
   "reason": "<なぜその値か。1〜3 文>"}
]}
\`\`\`

- \`duplicate\` のときは \`duplicate_of\` に相手の \`finding_id\` を足す
- **対象の全件へ 1 つずつ返す。** 判断できないものは \`insufficient_evidence\` にする
- 投稿は行わない。ファイルを書くだけである
EOF

# 実行時間の上限。監視の hard timeout より長く取り、打ち切りの判断を監視の側へ一本化する。
PRINT_TIMEOUT=${NDF_CRITIQUE_PRINT_TIMEOUT:-1800}

"$SCRIPT_DIR/../../../scripts/lib/launch-cli.sh" "$RUNTIME" "$WORKTREE" "$PROMPT" \
  "$RUNTIME-critique-pr$STATE_PR" "" "$TMP_DIR" "$PRINT_TIMEOUT"
