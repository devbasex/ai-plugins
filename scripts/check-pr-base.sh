#!/usr/bin/env bash
# 既定ブランチ宛の Pull Request が、宛先のブランチのブランチ戦略の宣言に合う分岐元から出ていることを確かめる。
#
# 宣言は宛先のブランチ（環境変数 PR_BASE_REF。無ければ origin の HEAD）の `.ndf/project.json` のセクション
# `branch_strategy` から、`plugins/ndf/scripts/lib/branch_strategy_cli.py get --ref` で読む（#3672 の D27）。
# Pull Request の変更で緩められないよう、head 側の宣言は読まない。セクションが無い・壊れている・旧いキーが
# 残るときは、読みの部品が出す理由（ファイル・キーの位置・打つコマンド）を添えて落とす。既定の判定へ倒さない。
#
# 判定は `release_strategy` の値で分ける。
#   trunk:             ベースブランチと本番ブランチが同じなので、既定ブランチ宛でも通す
#   merge_base_branch: 既定ブランチへ直に出さない。通すのは head がベースブランチ（`branches.base`）のときと、
#                      head が `release/v*` で先頭のコミットが `origin/<ベース>` の祖先のとき（承認したコミットに
#                      版上げを積み、先にベースへ取り込んだ本番の配布のブランチ）である。ベースブランチを origin に
#                      まだ作っていない間は通す
#   release_branch:    選んだスプリントブランチを集める取り込み方。次をすべて満たすときだけ通す
#     1. head が `release/v*` である（ベースブランチからの Pull Request は通さない）
#     2. head の先頭が `origin/<宛先>` の子孫である
#     3. `origin/<宛先>..<head>` のコミットは、head の first-parent の列（リリースブランチを組むマージと
#        版上げ）か、origin のいずれかの `sprint/*` の祖先である（ベースブランチにだけあるコミットを入れない）
set -uo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "使い方: check-pr-base.sh <Pull Request の分岐元のブランチ名> [<分岐元の先頭の SHA>]" >&2
  exit 2
fi

head_ref="$1"
head_sha="${2:-}"

STRATEGY_CLI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/plugins/ndf/scripts/lib/branch_strategy_cli.py"

target="${PR_BASE_REF:-}"
if [ -z "$target" ]; then
  target=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)
  target="${target#origin/}"
fi
if [ -z "$target" ]; then
  echo "::error::宛先のブランチが分かりません。PR_BASE_REF を渡すか、origin の HEAD を設定してください"
  exit 1
fi
GIT_TERMINAL_PROMPT=0 git fetch -q origin "refs/heads/$target:refs/remotes/origin/$target" 2>/dev/null || true

# 宛先のブランチのセクションの値を 1 つ読む。読めなければ理由を出して落とす。
# 標準エラーは分けて受ける（uv が環境を作るときの行を値に混ぜない）。
# 理由はコマンド置換に吸われないよう、控えた元の標準出力（fd 3）へ書く。
exec 3>&1
strategy_value() {
  local out err
  err=$(mktemp)
  if ! out=$(python3 "$STRATEGY_CLI" get --ref "refs/remotes/origin/$target" "$1" 2>"$err"); then
    echo "::error::${target} のブランチ戦略の宣言を読めません: $(tr '\n' ' ' <"$err")" >&3
    rm -f "$err"
    return 1
  fi
  rm -f "$err"
  printf '%s\n' "$out"
}

release_strategy=$(strategy_value /release_strategy) || exit 1

case "$release_strategy" in
trunk) exit 0 ;;
release_branch | merge_base_branch) ;;
*)
  echo "::error::${target} の宣言の release_strategy が分かりません: ${release_strategy}"
  exit 1
  ;;
esac

if [ "$release_strategy" = "release_branch" ]; then
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

# merge_base_branch: ベースブランチからだけ通す
dev_base=$(strategy_value /branches/base) || exit 1

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
