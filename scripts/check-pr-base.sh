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
set -uo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "使い方: check-pr-base.sh <Pull Request の分岐元のブランチ名> [<分岐元の先頭の SHA>]" >&2
  exit 2
fi

head_ref="$1"
head_sha="${2:-}"

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
