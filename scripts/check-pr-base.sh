#!/usr/bin/env bash
# 既定ブランチ宛の Pull Request が、開発の起点ブランチから出ていることを確かめる。
#
# 配布のチャネルを分けるリポジトリでは、正式版のブランチへ直に Pull Request を出さない。
# 既定ブランチは正式版のままにするため、`gh pr create` の宛先の既定も正式版のままになる。
# 宛先の指定を忘れても気づけるよう、機械で塞ぐ。
#
# **判定は宣言に起点が書かれていて、そのブランチが origin にあるときだけ働く。** 書く前・
# 作る前は成功で通す。有効にする時期を人の手で合わせずに済ませるためである。
set -uo pipefail

if [ "$#" -ne 1 ]; then
  echo "使い方: check-pr-base.sh <Pull Request の分岐元のブランチ名>" >&2
  exit 2
fi

head_ref="$1"

dev_base=$(jq -r 'select(.version == 1) | .base_branch | select(type == "string")' \
  .ndf/worktree.json 2>/dev/null)
# 起点を宣言していないリポジトリは、チャネルを分けていない。
[ -n "$dev_base" ] || exit 0

# 起点ブランチをまだ作っていない間は、すべての Pull Request を通す。
git ls-remote --exit-code --heads origin "$dev_base" >/dev/null 2>&1 || exit 0

[ "$head_ref" = "$dev_base" ] && exit 0

echo "::error::既定ブランチ宛の Pull Request は ${dev_base} からのみです。--base ${dev_base} を付けてください（分岐元: ${head_ref}）"
exit 1
