#!/usr/bin/env bash
# cross-review gemini launcher (trusted directory 対策込み).
#
# Usage: launch-gemini.sh <STATE_PR> <ROUND>
#
# 引数 STATE_PR は state.json の key (= 最初に init した PR 番号)。
# レビュー対象の PR は state.json の `current_pr` を読む。
#
# 注意:
#   - worktree のような新規パスは untrusted 判定で --yolo が "default" に降格する。
#     `--skip-trust` と `GEMINI_CLI_TRUST_WORKSPACE=true` を **両方** 必須とする。
#   - 完了判定は monitor.py が pidfile + sentinel + result.json で多軸判定する。

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
# PR (=current_pr) は gh コマンドのレビュー対象 PR 番号として使う。
# tmp パス側は STATE_PR で固定 (monitor.py / state.py との読み書き整合のため)。
PR=$(jq -r '.current_pr' "$STATE")
SHA=$(gh pr view "$PR" --json headRefOid -q .headRefOid)

PROMPT=$TMP_DIR/gemini-review-pr$STATE_PR-prompt.md
# 既存コメントは **プロンプトにインライン埋め込み** する。
# tmp dir は `~/.gemini/tmp/<workspace>/` を使うようになったが、念のため
# プロンプト埋め込み方式も維持 (gemini が read_file を呼ばずに済むので確実)。
EXISTING_FILE=$TMP_DIR/cross-review-pr$STATE_PR-existing-comments.txt
if [ -s "$EXISTING_FILE" ]; then
  EXISTING_INLINE=$(cat "$EXISTING_FILE")
else
  EXISTING_INLINE="(なし)"
fi

cat > "$PROMPT" <<EOF
# /ndf:review 実行 (cross-review gemini / round $ROUND)

PR #$PR を **gemini の観点でレビューし、gh api で直接 PR に投稿** してください。

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

## 出力契約
- review body の **先頭行** に必ず以下を入れる:
  \`\`\`
  ## 🤖 cross-review | round $ROUND | gemini | <event(intent)>
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

- 投稿後、サマリを **$TMP_DIR/gemini-review-pr$STATE_PR-result.json** に書く（フォーマットは launch-codex.sh と同じ）
- payload は **$TMP_DIR/gemini-review-pr$STATE_PR-round$ROUND-payload.json** に保存

## 守るべきこと
- **リポジトリ編集禁止**。gh api での投稿のみ許可
- worktree 外のパスは触らない
- gh api 失敗時は err.log にエラー詳細を残して即時終了
EOF

cd "$WORKTREE"
# ⚠ --skip-trust と GEMINI_CLI_TRUST_WORKSPACE=true は両方必須
GEMINI_CLI_TRUST_WORKSPACE=true nohup gemini --yolo --skip-trust --output-format text \
  -p "$(cat "$PROMPT")" \
  > $TMP_DIR/gemini-review-pr$STATE_PR-stdout.log \
  2> $TMP_DIR/gemini-review-pr$STATE_PR-err.log &
echo $! > $TMP_DIR/gemini-review-pr$STATE_PR.pid
disown
echo "🚀 gemini launched (pid=$(cat $TMP_DIR/gemini-review-pr$STATE_PR.pid))" >&2
