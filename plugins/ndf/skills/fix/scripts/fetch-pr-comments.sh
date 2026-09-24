#!/usr/bin/env bash
# Usage: fetch-pr-comments.sh [--strict] <owner/repo> <pr_number>
# 3 ソース (インラインコメント / レビュー body / PR レベルコメント) を一括取得し、
# タグ付き行単位で stdout に出力する。
# 全ソース取得失敗時は非 0 で終了する（0件取得と取得失敗を区別）。
# --strict を付けると、3 ソースのどれか 1 つでも失敗すれば非 0 で終了する。
# cross-review の控えの取り直しが使う（一部だけの控えで前の控えを上書きしないため。#542）。
set -uo pipefail

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
  shift
fi

if [[ $# -lt 2 ]] || [[ -z "${1:-}" ]] || [[ -z "${2:-}" ]]; then
  echo "ERROR: 引数が不足しています。Usage: $0 [--strict] <owner/repo> <pr_number>" >&2
  exit 1
fi

REPO="$1"
PR="$2"

FAIL_COUNT=0

# endpoint / jq 式 / 警告ラベル。本文の改行は \n へエスケープし、1 行で出力する。
# gh api --jq は内部で jq -r 相当だが、環境差を吸収するため jq -r を明示する。
COMMENT_SOURCES=(
  "repos/${REPO}/pulls/${PR}/comments"$'\034''.[] | "\(.path // "?"):\(.line // .original_line // "?") [\(.user.login)] \(.body // "" | gsub("\n"; "\\n") | gsub("```"; "` ` `"))"'$'\034''インラインコメント'
  "repos/${REPO}/pulls/${PR}/reviews"$'\034''.[] | select(.body != null and .body != "") | "[REVIEW-BODY] [\(.user.login)] state=\(.state) \(.body | gsub("\n"; "\\n") | gsub("```"; "` ` `"))"'$'\034''レビュー body'
  "repos/${REPO}/issues/${PR}/comments"$'\034''.[] | "[PR-COMMENT] [\(.user.login)] \(.body // "" | gsub("\n"; "\\n") | gsub("```"; "` ` `"))"'$'\034''PR レベルコメント'
)

for source in "${COMMENT_SOURCES[@]}"; do
  IFS=$'\034' read -r endpoint jq_expr warning_label <<< "$source"
  if ! gh api "$endpoint" --paginate | jq -r "$jq_expr"; then
    echo "WARNING: ${warning_label}の取得に失敗しました (${endpoint})" >&2
    (( FAIL_COUNT += 1 )) || true
  fi
done

# 全ソース失敗時のみ非 0 で終了（認証切れ等の検出）
if (( FAIL_COUNT >= 3 )); then
  echo "ERROR: 全 3 ソースの取得に失敗しました" >&2
  exit 1
fi
if (( STRICT == 1 && FAIL_COUNT > 0 )); then
  echo "ERROR: ${FAIL_COUNT} 件のソースの取得に失敗しました（--strict）" >&2
  exit 1
fi
