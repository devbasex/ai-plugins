#!/usr/bin/env bash
# 検査のコマンド（#1323）。CI（lint.yml）と .githooks/pre-push も同じものを呼ぶ。
# ruff format --check・ruff check・shellcheck -S warning を、git が追跡するファイルへ掛ける。
# ツールは根の uv.lock が固定した版を `uv run --only-group lint` から起動する（決定 6）。
#
# 使い方: bash scripts/check-lint.sh [--fix] [--] [<パス>...]
#   --fix   段の前に ruff check --fix と ruff format を Python の対象へ掛ける（sh は直さない）
#   <パス>  根からの相対パス。渡すと、そのうち git が追跡し作業ディレクトリに残るファイルだけを対象にする
#           （範囲テスト。#464）。渡さなければ追跡されているファイルすべて。`--` 以降はすべてパスとして読む
# 終了コード: 0 違反なし / 1 違反あり / 2 検査の仕組みが落ちた（git の外・uv が無い・引数の誤り・ツールの異常）
set -uo pipefail

usage() {
  echo "使い方: bash scripts/check-lint.sh [--fix] [--] [<パス>...]" >&2
}

FIX=0
PATHS=()
ONLY_PATHS=0
for arg in "$@"; do
  if [ "$ONLY_PATHS" -eq 1 ]; then
    PATHS+=("$arg")
    continue
  fi
  case "$arg" in
    --) ONLY_PATHS=1 ;;
    --fix) FIX=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      usage
      echo "check-lint: 知らない引数: $arg" >&2
      exit 2
      ;;
    *) PATHS+=("$arg") ;;
  esac
done

if ! ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "check-lint: git の worktree の外で起動された" >&2
  exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "check-lint: uv が見つからない（https://docs.astral.sh/uv/ から入れる）" >&2
  exit 2
fi
cd "$ROOT_DIR" || exit 2

# 対象は git が追跡するファイルだけにする（I2）。消したがまだ index にあるものは除く。
# パスを渡したときは、その配下の追跡ファイルに絞る（振り分けの規則は引数なしと同じ）
list_tracked() {
  if [ "${#PATHS[@]}" -gt 0 ]; then
    git --literal-pathspecs ls-files -z -- "${PATHS[@]}"
  else
    git ls-files -z
  fi
}

PY_FILES=()
SH_FILES=()
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  case "${f##*/}" in
    *.py) PY_FILES+=("$f") ;;
    *.sh) SH_FILES+=("$f") ;;
    *.*) ;;
    *)
      # 拡張子の無いファイルは、1 行目が sh・bash の shebang のものだけを対象にする
      IFS= read -r first <"$f" 2>/dev/null || first=""
      if [[ "$first" =~ ^#![^[:space:]]*/(env[[:space:]]+)?(ba)?sh([[:space:]]|$) ]]; then
        SH_FILES+=("$f")
      fi
      ;;
  esac
done < <(list_tracked)

tool() {
  uv run --frozen --project "$ROOT_DIR" --only-group lint "$@"
}

RC=0
FAILED=()
BROKEN=()

# 段の終了コードを集める。1 は違反、2 以上は仕組みの異常（ruff も shellcheck も同じ意味で返す）
record() {
  local name="$1" code="$2"
  if [ "$code" -eq 1 ]; then
    FAILED+=("$name")
  elif [ "$code" -ge 2 ]; then
    BROKEN+=("$name（終了コード $code）")
  fi
  if [ "$code" -gt "$RC" ]; then
    RC="$code"
  fi
}

if [ "$FIX" -eq 1 ] && [ "${#PY_FILES[@]}" -gt 0 ]; then
  # 直せない違反が残ると 1 を返すが、それは後の段が出す。ここでは異常（2 以上）だけを拾う
  tool ruff check --fix --quiet "${PY_FILES[@]}"
  code=$?
  [ "$code" -ge 2 ] && record "ruff check --fix" "$code"
  tool ruff format --quiet "${PY_FILES[@]}"
  code=$?
  [ "$code" -ge 2 ] && record "ruff format" "$code"
fi

if [ "${#PY_FILES[@]}" -gt 0 ]; then
  tool ruff format --check --output-format concise "${PY_FILES[@]}"
  record "ruff format --check" "$?"
  tool ruff check --output-format concise "${PY_FILES[@]}"
  record "ruff check" "$?"
fi

if [ "${#SH_FILES[@]}" -gt 0 ]; then
  tool shellcheck -S warning -f gcc "${SH_FILES[@]}"
  record "shellcheck" "$?"
fi

if [ "$RC" -gt 2 ]; then
  RC=2
fi
if [ "${#BROKEN[@]}" -gt 0 ]; then
  echo "check-lint: 検査の仕組みが落ちた: ${BROKEN[*]}" >&2
elif [ "${#FAILED[@]}" -gt 0 ]; then
  echo "check-lint: 違反あり: ${FAILED[*]}" >&2
else
  echo "check-lint: 違反なし（Python ${#PY_FILES[@]} 本・sh ${#SH_FILES[@]} 本）" >&2
fi
exit "$RC"
