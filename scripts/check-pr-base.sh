#!/usr/bin/env bash
# 既定ブランチ宛の Pull Request が、開発の起点ブランチに含まれるコミットだけを出すことを確かめる。
#
# 通すのは head が起点ブランチのときと、head が `release/v*` で先頭のコミットが `origin/<起点>` の
# 祖先のとき（承認したコミットに版上げを積み、先に起点へ取り込んだ本番の配布のブランチ）である。
#
# 配布のチャネルを分けるリポジトリでは、正式版のブランチへ直に Pull Request を出さない。
# 既定ブランチは正式版のままにするため、`gh pr create` の宛先の既定も正式版のままになる。
# 宛先の指定を忘れても気づけるよう、機械で塞ぐ。
#
# **判定は宣言に起点が書かれていて、そのブランチが origin にあるときだけ働く。** 書く前・
# 作る前は成功で通す。有効にする時期を人の手で合わせずに済ませるためである。
#
# 宛先のブランチ（環境変数 PR_BASE_REF。無ければ origin の HEAD）の `.ndf/worktree.json` の
# `branch_flow.promotion` が `selected`（選んだスプリントブランチを集める取り込み方。#3121）なら、
# 判定を次に替える（#3121 の決定 14）。宣言は Pull Request の変更で緩められないよう、宛先のブランチから読む。
#   1. head が `release/v*` である（起点ブランチからの Pull Request は通さない）
#   2. head の先頭が `origin/<宛先>` の子孫である
#   3. `origin/<宛先>..<head>` のコミットは、head の first-parent の列（リリースブランチを組むマージと
#      版上げ）か、origin のいずれかの `sprint/*` の祖先である（起点ブランチにだけあるコミットを入れない）
set -uo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "使い方: check-pr-base.sh <Pull Request の分岐元のブランチ名> [<分岐元の先頭の SHA>]" >&2
  exit 2
fi

head_ref="$1"
head_sha="${2:-}"

# 宛先のブランチの宣言が選んで集める取り込み方か。読めなければ今の判定のまま。
target="${PR_BASE_REF:-}"
if [ -z "$target" ]; then
  target=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)
  target="${target#origin/}"
fi
promotion=
if [ -n "$target" ]; then
  GIT_TERMINAL_PROMPT=0 git fetch -q origin "refs/heads/$target:refs/remotes/origin/$target" 2>/dev/null || true
  promotion=$(git show "refs/remotes/origin/$target:.ndf/worktree.json" 2>/dev/null |
    jq -r 'select(.version == 1) | .branch_flow.promotion? | select(type == "string")' 2>/dev/null)
fi

if [ "$promotion" = "selected" ]; then
  case "$head_ref" in
  release/v*) ;;
  *)
    echo "::error::${target} 宛の Pull Request は、${target} から切って承認したスプリントブランチを集めた release/v<版> からだけです（分岐元: ${head_ref}）"
    exit 1
    ;;
  esac
  if [ -z "$head_sha" ] || ! git merge-base --is-ancestor "refs/remotes/origin/$target" "$head_sha" 2>/dev/null; then
    echo "::error::${head_ref} の先頭（${head_sha:-SHA の指定なし}）が origin/${target} の子孫ではありません。${target} から切り直してください"
    exit 1
  fi
  GIT_TERMINAL_PROMPT=0 git fetch -q origin 'refs/heads/sprint/*:refs/remotes/origin/sprint/*' 2>/dev/null || true
  first=$(git rev-list --first-parent "refs/remotes/origin/$target..$head_sha")
  mapfile -t sprints < <(git for-each-ref --format='^%(refname)' 'refs/remotes/origin/sprint/')
  stray=
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    grep -qx "$c" <<<"$first" && continue
    stray="$stray ${c:0:12}"
  done < <(git rev-list "$head_sha" "^refs/remotes/origin/$target" "${sprints[@]}")
  if [ -n "$stray" ]; then
    echo "::error::${head_ref} に、スプリントブランチ（origin の sprint/*）にも組むマージと版上げにも無いコミットがあります:${stray}。承認したスプリントブランチだけを ${target} から集め直してください"
    exit 1
  fi
  exit 0
fi

dev_base=$(jq -r 'select(.version == 1) | .base_branch | select(type == "string")' \
  .ndf/worktree.json 2>/dev/null)
# 起点を宣言していないリポジトリは、チャネルを分けていない。
[ -n "$dev_base" ] || exit 0

# 起点ブランチをまだ作っていない間は、すべての Pull Request を通す。
#
# 照合先は `refs/heads/<名前>` と完全な参照名で渡す。`git ls-remote` のパターンは参照名の
# 末尾に一致するため、`develop` とだけ渡すと `refs/heads/feature/develop` にも一致し、
# 起点が未作成なのにチェックが有効になる（git 2.53.0 で実測）。返った行の参照名とも突き合わせ、
# 末尾一致で別のブランチを拾わないようにする（`wt_branch_exists` と同じ形）。
listing=$(GIT_TERMINAL_PROMPT=0 git ls-remote --heads origin "refs/heads/$dev_base" 2>/dev/null) ||
  exit 0
found=0
while IFS= read -r line; do
  case "$line" in *$'\t'"refs/heads/$dev_base") found=1; break ;; esac
done <<<"$listing"
[ "$found" -eq 1 ] || exit 0

[ "$head_ref" = "$dev_base" ] && exit 0

case "$head_ref" in
release/v*)
  if [ -n "$head_sha" ] &&
    GIT_TERMINAL_PROMPT=0 git fetch -q origin "refs/heads/$dev_base:refs/remotes/origin/$dev_base" 2>/dev/null &&
    git merge-base --is-ancestor "$head_sha" "refs/remotes/origin/$dev_base" 2>/dev/null; then
    exit 0
  fi
  echo "::error::${head_ref} の先頭（${head_sha:-SHA の指定なし}）が origin/${dev_base} に含まれていません。${dev_base} へ取り込んでから既定ブランチへ出してください"
  exit 1
  ;;
esac

echo "::error::既定ブランチ宛の Pull Request は ${dev_base} からのみです。--base ${dev_base} を付けてください（分岐元: ${head_ref}）"
exit 1
