#!/usr/bin/env bash
# NDF plugin: Pull Request の本文から、閉じる語が指す issue を取り出す。
#
#   closing-issues.sh [--repo <所有者>/<リポジトリ>] [本文のファイル]
#     引数が無ければ標準入力から読む。`<所有者>/<リポジトリ>` と `<番号>` をタブで
#     区切り、現れた順に 1 行 1 件で出す。
#
# **閉じる語は番号ごとに要る。** GitHub の公式ドキュメントが "you must use the keyword
# before each issue you reference" と定めており、`Fixes #12, #13` は 12 だけを閉じる。
# 既定ブランチへマージしたときの GitHub の振る舞いと同じ結果になるようにする。
#
# **閉じる先はリポジトリまで含めて取り出す**（#229）。開発の対象が別のリポジトリのとき、
# そこへ起票した issue は番号だけでは指せない。`gh issue close` の位置引数は
# `{<number> | <url>}` で `owner/repo#番号` を受け取らないため、リポジトリと番号に
# 分けて出し、呼び出し側が `--repo` へ渡す。
#
# 起点ブランチが既定ブランチでないリポジトリでは、マージしても自動クローズが働かない。
# 後片付けの工程がこの一覧を読み、まだ OPEN のものだけを閉じる。
set -uo pipefail

DEFAULT_REPO=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) DEFAULT_REPO="${2:-}"; shift 2 ;;
    --repo=*) DEFAULT_REPO="${1#--repo=}"; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done

# 番号だけの形は、実行したリポジトリの issue を指す。**通信しない。**
if [ -z "$DEFAULT_REPO" ] && command -v git >/dev/null 2>&1; then
  url=$(git config --get remote.origin.url 2>/dev/null) || url=""
  if [ -n "$url" ]; then
    slug=${url%.git}
    slug=${slug%/}
    slug=${slug##*:}
    case "$slug" in
      */*)
        repo_name=${slug##*/}
        owner_name=${slug%/*}
        owner_name=${owner_name##*/}
        [ -n "$owner_name" ] && [ -n "$repo_name" ] && DEFAULT_REPO="$owner_name/$repo_name"
        ;;
    esac
  fi
fi

body=$(cat -- "${1:--}")

# 大文字と小文字を区別しない。語と番号の間は空白と `:` を許す（GitHub と同じ）。
# 指し方は 3 つある。issue の URL・`<所有者>/<リポジトリ>#<番号>`・`#<番号>` である。
# **Pull Request の URL は拾わない。** 閉じる対象は issue である。
NAME='[A-Za-z0-9._-]+'
REF="(https?://github\.com/$NAME/$NAME/issues/[0-9]+|$NAME/$NAME#[0-9]+|#[0-9]+)"
KEYWORD='\b(close[sd]?|fix(e[sd])?|resolve[sd]?)[[:space:]]*:?[[:space:]]*'

# **1 件も見つからないことは正常な結果である。** `grep` の終了コードをそのまま返すと、
# 閉じる語の無い Pull Request で失敗したように見える。
matches=$(printf '%s' "$body" | grep -oiE "$KEYWORD$REF" || true)
[ -n "$matches" ] || exit 0

while IFS= read -r match; do
  [ -n "$match" ] || continue
  # 一致は閉じる語で始まり、指し先で終わる。末尾に錨を置いて指し先だけを取り出す。
  ref=$(printf '%s' "$match" | grep -oE "$REF\$") || continue
  case "$ref" in
    *://*)
      rest=${ref#*github.com/}
      repo=${rest%%/issues/*}
      number=${rest##*/}
      ;;
    '#'*)
      repo="$DEFAULT_REPO"
      number=${ref#\#}
      ;;
    *)
      repo=${ref%#*}
      number=${ref##*#}
      ;;
  esac
  # リポジトリを決められないときは落とす。番号だけでは閉じる先が定まらない。
  [ -n "$repo" ] || continue
  printf '%s\t%s\n' "$repo" "$number"
done <<<"$matches" | awk '!seen[$0]++'
