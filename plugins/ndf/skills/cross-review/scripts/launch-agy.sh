#!/usr/bin/env bash
# cross-review agy launcher.
#
# Usage: launch-agy.sh <STATE_PR> <ROUND>
#
# 引数 STATE_PR は state.json の key (= 最初に init した PR 番号)。
# レビュー対象の PR は state.json の `current_pr` を読む。
#
# 注意:
#   - 起動そのものは共通層の ../../../scripts/lib/launch-cli.sh へ委譲する。作業領域の宣言・
#     プロンプトの渡し方・実行時間の上限は、そちらが 1 箇所で持つ。
#   - 完了判定は monitor.py が pidfile + result.json で多軸判定する。

set -euo pipefail

STATE_PR=${1:?STATE_PR required}
ROUND=${2:?ROUND required}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_tmpdir.sh
. "$SCRIPT_DIR/_tmpdir.sh"
TMP_DIR=$(tmpdir)

STATE=$TMP_DIR/cross-review-pr$STATE_PR-state.json
[ -s "$STATE" ] || { echo "state.json not found: $STATE" >&2; exit 1; }

WORKTREE=$(jq -r '.worktree_path' "$STATE")
REPO=$(jq -r '.repo' "$STATE")
EVENT_DOWNGRADE=$(jq -r '.event_downgrade // false' "$STATE")
EXTRA_REVIEW_INSTRUCTIONS=$(jq -r '.review_instructions // .extra_review_instructions // ""' "$STATE")
# PR (=current_pr) は gh コマンドのレビュー対象 PR 番号として使う。
# tmp パス側は STATE_PR で固定 (monitor.py / state.py との読み書き整合のため)。
PR=$(jq -r '.current_pr' "$STATE")
# head のコミットは state.json から読む（#271）。`start-round` が同期のときに解決した
# 値をラウンドへ記録しており、codex と agy が同じ値を別々に取る必要が無い。
# 前の版の state.json から再開したときだけ、従来の `gh pr view` へ落ちる。
SHA=$(jq -r '(.rounds[-1].head_sha // "")' "$STATE")
[ -n "$SHA" ] || SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)

# 前ラウンドの結果を残さない。投稿失敗などで今ラウンドの result.json が
# 書かれなかったとき、state.py read-result が**前ラウンドの結果を読んで**
# 同じ判定を繰り返す事故を防ぐ。
rm -f "$TMP_DIR/agy-review-pr$STATE_PR-result.json" \
      "$TMP_DIR/agy-review-pr$STATE_PR-round$ROUND-payload.json" \
      "$TMP_DIR/agy-review-pr$STATE_PR-round$ROUND-api-payload.json"

STEM=$TMP_DIR/agy-review-pr$STATE_PR
PROMPT=$STEM-prompt.md
# 既存コメントは **プロンプトにインライン埋め込み** する。
# tmp dir は `<worktree>/.cross_review/` を使うが、埋め込みなら読み取りの往復が
# 要らないので確実である。
EXISTING_FILE=$TMP_DIR/cross-review-pr$STATE_PR-existing-comments.txt
if [ -s "$EXISTING_FILE" ]; then
  EXISTING_INLINE=$(cat "$EXISTING_FILE")
else
  EXISTING_INLINE="(なし)"
fi
EXTRA_REVIEW_BLOCK=
if [ -n "$EXTRA_REVIEW_INSTRUCTIONS" ]; then
  EXTRA_REVIEW_BLOCK=$(cat <<EXTRA_EOF

## 追加レビュー観点
以下の観点を通常レビューに追加して重点的に確認してください。
ただし、根拠がある修正アクションだけを指摘し、重複指摘や好みの nit は避けてください。

\`\`\`
$EXTRA_REVIEW_INSTRUCTIONS
\`\`\`
EXTRA_EOF
)
fi

cat > "$PROMPT" <<EOF
# /ndf:pr-review 実行 (cross-review agy / round $ROUND)

PR #$PR を **agy の観点でレビューし、gh api で直接 PR に投稿** してください。

## 必須コンテキスト
- repo: $REPO
- PR: #$PR
- commit_id (headRefOid): $SHA
- worktree: $WORKTREE （**ファイル読み取りは必ず此処の絶対パスを使う**）
- event_downgrade: $EVENT_DOWNGRADE
  - true の場合: payload の \`event\` は \`COMMENT\` にすること。
    body 先頭 prefix の \`<event>\` は本来の intent を書く。

## 既存コメントスナップショット（重複指摘禁止）
workspace 外を読まなくて済むよう、以下にインライン展開する:

\`\`\`
$EXISTING_INLINE
\`\`\`
$EXTRA_REVIEW_BLOCK

## 出力契約
- review body の **先頭行** に必ず以下を入れる:
  \`\`\`
  ## 🤖 cross-review | round $ROUND | agy | <event(intent)>
  \`\`\`
  - \`<event>\` は **本来の intent** (REQUEST_CHANGES / APPROVE / COMMENT)

### 出力に **含めてはいけないもの**（Resolve 負荷を増やすため）
- ❌ **「良い点」/「Strengths」/「評価できる点」 section** — body にも書かない
- ❌ **対応アクションが無いインラインコメント** — 観察・感想・現状説明だけは禁止
- ❌ **nit / スタイル指摘のインライン化** — 好みの問題はコメント化しない (無視する)
- ❌ **コード引用 (\`\`\` ... \`\`\`) だけで指摘内容が無いコメント**
- ❌ **\`event=COMMENT\` での雑感投稿** — 直すべき点が無ければ \`APPROVE\` にする

### インラインコメントの書式
- \`[重要度 / カテゴリ]\` プレフィックス必須 (例: \`[major / 正確性]\`)
- 重要度は \`critical\` / \`major\` / \`minor\` のみ使う (nit はインライン化しない)
- 本文は **1 コメント = 1 修正アクション** で完結させる。1〜2 文で具体的な修正提案を書く

### body (総評) の書き方
- 設計レベル・PR 横断の **修正提案のみ** 書く
- 書くことが無ければ prefix 行 + 1 行サマリだけで良い (褒め言葉や評価文は不要)

### 進捗マーカー（監視用）
- 無言ハングと区別できるよう、作業フェーズが進むたびに
  **$STEM-progress.log** へ短い 1 行を追記すること
- 内部の推論や長い説明は書かず、以下のようなフェーズ名 + 対象だけを書く:
  - \`start: review PR #$PR round $ROUND\`
  - \`scan: diff and existing comments\`
  - \`analyze: candidate findings\`
  - \`post: submit review\`
  - \`done: result.json written\`


### インラインコメントを付けられる行（422 対策・必須）
- インラインコメントは **この PR の差分に含まれる行にしか付けられない**。差分外の行を
  指定すると GitHub が \`HTTP 422 Line could not be resolved\` を返し、**インラインだけで
  なくレビュー本体も投稿されない**（指摘が丸ごと失われる）
- 差分に無い箇所を指摘したいときは、インラインにせず **body に「ファイル名:行 + 指摘」
  の形で書く**
- それでも 422 が返ったときは、**該当インラインを body へ移して再投稿する**。
  投稿を諦めない

### 投稿できなかった場合（必須）
- gh api がエラーを返したら、err.log に詳細を残したうえで **result.json を必ず書いて
  から終了する**。\`event\` は本来の intent、\`comments_count\` は 0、
  \`"post_error"\` に失敗理由（HTTP status とメッセージ）を入れる:
  \`\`\`json
  {"event": "REQUEST_CHANGES", "posted_as": "COMMENT", "comments_count": 0,
   "review_url": "", "by_severity": {"critical": 0, "major": 0, "minor": 0, "nit": 0},
   "post_error": "422 Line could not be resolved"}
  \`\`\`
- result.json を書かずに終了すると、収束ループは**前ラウンドの結果を使うか、結果なしで
  停止する**。エラー時ほど result.json が要る

- 投稿後、サマリを **$STEM-result.json** に
  **必ず以下のキーで** 書く:
  \`\`\`json
  {
    "event": "APPROVE",
    "posted_as": "COMMENT",
    "comments_count": 3,
    "review_url": "https://github.com/.../pull/$PR#pullrequestreview-...",
    "by_severity": {"critical": 0, "major": 0, "minor": 0, "nit": 0}
  }
  \`\`\`
  - \`intent\` / \`comment_count\` 等の別名は使わないこと
  - \`event\` の値は \`APPROVE\` / \`REQUEST_CHANGES\` / \`COMMENT\` のいずれか
  - \`event_downgrade=true\` のとき \`posted_as\` は \`COMMENT\` にダウングレード可
- payload は **$STEM-round$ROUND-payload.json** に保存
  （振動検知用、\`{ "comments": [{path, line, body, severity}, ...] }\` 形式）

## 守るべきこと
- **リポジトリ編集禁止**。gh api での投稿のみ許可
- worktree 外のパスは触らない
- gh api 失敗時は err.log にエラー詳細を残し、**result.json を書いてから**終了する
EOF

# 結果ファイルの置き場所が作業ツリーの外にあるときだけ、作業領域へ足す。
# `<worktree>/.cross_review/` を使う既定の配置では足さない。
WORKTREE_ABS=$(cd "$WORKTREE" && pwd -P)
TMP_ABS=$(cd "$TMP_DIR" && pwd -P)
EXTRA_DIR=
case "$TMP_ABS/" in
  "$WORKTREE_ABS"/*) ;;
  *) EXTRA_DIR=$TMP_ABS ;;
esac

# 実行時間の上限。監視の hard timeout (既定 420 秒) より長く取り、打ち切りの判断を
# 監視の側へ一本化する。
PRINT_TIMEOUT=600

"$SCRIPT_DIR/../../../scripts/lib/launch-cli.sh" agy "$WORKTREE_ABS" "$PROMPT" "$STEM" "" \
  "$EXTRA_DIR" "$PRINT_TIMEOUT"
