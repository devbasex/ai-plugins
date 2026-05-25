#!/usr/bin/env bash
# Usage: fetch-pr-comments.sh <owner/repo> <pr_number>
# 3 ソース (インラインコメント / レビュー body / PR レベルコメント) を一括取得し、
# タグ付き行単位で stdout に出力する。
set -uo pipefail

REPO="$1"
PR="$2"

# 1. インラインコメント (diff の特定行に紐づく)
gh api "repos/${REPO}/pulls/${PR}/comments" --paginate --jq \
  '.[] | "\(.path // "?"):\(.line // .original_line // "?") [\(.user.login)] \(.body // "" | split("\n")[0])"' \
  || true

# 2. レビュー body (CHANGES_REQUESTED / COMMENTED 等の総評)
gh api "repos/${REPO}/pulls/${PR}/reviews" --paginate --jq \
  '.[] | select(.body != null and .body != "") | "[REVIEW-BODY] [\(.user.login)] state=\(.state) \(.body | split("\n")[0])"' \
  || true

# 3. PR レベルコメント (Conversation タブの通常コメント)
gh api "repos/${REPO}/issues/${PR}/comments" --paginate --jq \
  '.[] | "[PR-COMMENT] [\(.user.login)] \(.body // "" | split("\n")[0])"' \
  || true
