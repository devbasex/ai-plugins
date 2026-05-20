#!/usr/bin/env bash
# PR rotation — squash + 新ブランチ + 新 PR.
#
# Usage: rotate-pr.sh <STATE_PR>
#
# 引数 STATE_PR は state.json の key (= 最初に init した PR 番号)。
# 閉じる「現在の PR」は state.json の `current_pr` を読む。
#
# 既存ブランチを squash した新ブランチを作り、旧 PR (=current_pr) を close、新 PR を作成する。
# 新 PR 番号を stdout に "NEW_PR=<番号>" / "NEW_PR_URL=<url>" 形式で出力。
#
# state.json の current_pr / pr_history 更新は `state.py set-current-pr` で別途行う。

set -euo pipefail

STATE_PR=${1:?STATE_PR required}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_tmpdir.sh
. "$SCRIPT_DIR/_tmpdir.sh"
TMP_DIR=$(tmpdir)

STATE=$TMP_DIR/cross-review-pr$STATE_PR-state.json
[ -s "$STATE" ] || { echo "state.json not found: $STATE" >&2; exit 1; }

WORKTREE=$(jq -r '.worktree_path' "$STATE")
OLD_PR=$(jq -r '.current_pr' "$STATE")
ROUND_IN_PR=$(jq --argjson p "$OLD_PR" '[.rounds[] | select(.pr == $p)] | length' "$STATE")

cd "$WORKTREE"

BRANCH=$(git branch --show-current)
BASE=$(gh pr view "$OLD_PR" --json baseRefName -q .baseRefName)
TITLE=$(gh pr view "$OLD_PR" --json title -q .title)
NEW_BRANCH="${BRANCH}-r$(date +%H%M%S)"

echo "🔄 PR #$OLD_PR rotation: $BRANCH → $NEW_BRANCH (base=$BASE)" >&2

# 1. 既存ブランチを squash して新ブランチに
git checkout -b "$NEW_BRANCH"
git reset --soft "origin/$BASE"
git commit -m "$(cat <<EOF
$TITLE

(cross-review rotation: PR #$OLD_PR を squash 統合)
EOF
)"
git push -u origin "$NEW_BRANCH"

# 2. 旧 PR を close（コメント残し）
gh pr comment "$OLD_PR" --body "🔄 cross-review ループ進行中のため、本 PR を close し新規 PR に巻き直します。 round_in_pr=$ROUND_IN_PR で長尺化を回避。"
gh pr close "$OLD_PR"

# 3. 新 PR 作成
NEW_PR_URL=$(gh pr create --base "$BASE" --title "$TITLE (rotated)" --body "$(cat <<EOF
## Summary
旧 PR #$OLD_PR をベースに、cross-review クロスレビューループの継続。
旧 PR は round_in_pr=$ROUND_IN_PR で巻き直しのため close 済み。
旧 PR の resolved スレッドは既に修正済み事項。残った指摘はこの PR で再評価する。

<!-- I want to review in Japanese. -->
EOF
)")

NEW_PR=$(echo "$NEW_PR_URL" | grep -oE '/pull/[0-9]+' | grep -oE '[0-9]+')

echo "✅ 新 PR #$NEW_PR: $NEW_PR_URL" >&2
echo "NEW_PR=$NEW_PR"
echo "NEW_PR_URL=$NEW_PR_URL"
echo "NEW_BRANCH=$NEW_BRANCH"
