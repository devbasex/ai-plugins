#!/usr/bin/env bash
# NDF plugin: Pull Request の本文から、閉じる語が指す issue の番号を取り出す。
#
#   closing-issues.sh [本文のファイル]
#     引数が無ければ標準入力から読む。番号を 1 行 1 件で、現れた順に出す。
#
# **閉じる語は番号ごとに要る。** GitHub の公式ドキュメントが "you must use the keyword
# before each issue you reference" と定めており、`Fixes #12, #13` は 12 だけを閉じる。
# 既定ブランチへマージしたときの GitHub の振る舞いと同じ結果になるようにする。
#
# 起点ブランチが既定ブランチでないリポジトリでは、マージしても自動クローズが働かない。
# 後片付けの工程がこの一覧を読み、まだ OPEN のものだけを閉じる。
set -uo pipefail

body=$(cat -- "${1:--}")

# 大文字と小文字を区別しない。語と番号の間は空白と `:` を許す（GitHub と同じ）。
#
# **1 件も見つからないことは正常な結果である。** `grep` の終了コードをそのまま返すと、
# 閉じる語の無い Pull Request で失敗したように見える。
matches=$(
  printf '%s' "$body" \
    | grep -oiE '\b(close[sd]?|fix(e[sd])?|resolve[sd]?)[[:space:]]*:?[[:space:]]*#[0-9]+' \
    || true
)
[ -n "$matches" ] || exit 0

printf '%s\n' "$matches" | grep -oE '[0-9]+' | awk '!seen[$0]++'
