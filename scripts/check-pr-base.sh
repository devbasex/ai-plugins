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
#
# 照合先は `refs/heads/<名前>` と完全な参照名で渡す。`git ls-remote` のパターンは参照名の
# 末尾に一致するため、`develop` とだけ渡すと `refs/heads/feature/develop` にも一致し、
# 起点が未作成なのに検査が有効になる（git 2.53.0 で実測）。返った行の参照名とも突き合わせ、
# 末尾一致で別のブランチを拾わないようにする（`wt_branch_exists` と同じ形）。
listing=$(GIT_TERMINAL_PROMPT=0 git ls-remote --heads origin "refs/heads/$dev_base" 2>/dev/null) ||
  exit 0
found=0
while IFS= read -r line; do
  case "$line" in *$'\t'"refs/heads/$dev_base") found=1; break ;; esac
done <<<"$listing"
[ "$found" -eq 1 ] || exit 0

[ "$head_ref" = "$dev_base" ] && exit 0

echo "::error::既定ブランチ宛の Pull Request は ${dev_base} からのみです。--base ${dev_base} を付けてください（分岐元: ${head_ref}）"
exit 1
