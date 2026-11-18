#!/usr/bin/env bash
# 10 系の最後のタグ（ndf--v10.27.77）のプラグインを取り出し、その検査スクリプトを打つ。
#
#   bash scripts/ndf10-run.sh <10 系のプラグインの scripts/ からの相対パス> [引数...]
#   例: bash scripts/ndf10-run.sh instructions-check.py --root .
#
# 11.0.0 の入れ替え（#3304）で 10 系の scripts/ は無くなった。11 の副命令（ndf check instructions など）へ
# 移すまで（#3791）、CI の検査と指示書の手順はこの包みを通して 10 系のスクリプトを打つ（#3304 の S2-0 の判断 (B)）。
# #3791 で移したら、使い手を 11 の副命令へ書き換えてこのファイルを消す。
#
# 取り出し先は ${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ndf10-<タグ>。在ればそのまま使う。タグが手元に無ければ origin から取る。
# 終了コードは打ったスクリプトのもの。取り出せなければ 2。
set -euo pipefail

TAG="ndf--v10.27.77"
PLUGIN_DIR="plugins/ndf"

if [ "$#" -lt 1 ]; then
  echo "使い方: bash scripts/ndf10-run.sh <scripts/ からの相対パス> [引数...]" >&2
  exit 2
fi

root=$(git rev-parse --show-toplevel)
dest="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ndf10-$TAG"
if [ ! -d "$dest/$PLUGIN_DIR/scripts" ]; then
  if ! git -C "$root" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    git -C "$root" fetch -q --no-tags --depth=1 origin "refs/tags/$TAG:refs/tags/$TAG" || {
      echo "タグ $TAG を取れない" >&2
      exit 2
    }
  fi
  tmp=$(mktemp -d "${dest}.XXXXXX")
  git -C "$root" archive "$TAG" "$PLUGIN_DIR" | tar -x -C "$tmp" || {
    rm -rf "$tmp"
    echo "タグ $TAG から $PLUGIN_DIR を取り出せない" >&2
    exit 2
  }
  rm -rf "$dest"
  mv "$tmp" "$dest"
fi

script="$dest/$PLUGIN_DIR/scripts/$1"
shift
case "$script" in
  *.sh) exec bash "$script" "$@" ;;
  *) exec python3 "$script" "$@" ;;
esac
