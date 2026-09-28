#!/usr/bin/env bash
# NDF plugin: メインディレクトリから worktree へ 1 つのパスを複製する手続き（#1337）。
#
# `worktree-localenv.sh setup` と `worktree-deps.sh prepare` が source する。呼び出し側は
# `MAIN_DIR`（複製元のメインディレクトリ）と `TARGET`（書き込み先の worktree）を実体の絶対パスで
# 設定してから関数を呼ぶ。
#
# 書き込み先は worktree の中だけである。途中の symlink をたどって外へ書かない
# （`destination_is_safe`）。symlink は張らない。

# 書き込み先が本当に作業ツリーの中かを、実体で確かめる。
# 字面のチェックだけでは、途中に置かれた symlink をたどって外へ書き込めてしまう。
destination_is_safe() {
  local rel="$1" to="$TARGET/$1" parent resolved

  # 宛先そのものが symlink のときは、たどらずに断る。
  if [ -L "$to" ]; then
    printf '中断: %s は symlink です。たどらずに終わります\n' "$rel" >&2
    return 1
  fi

  # 実在する最も近い上位ディレクトリを実体解決し、作業ツリーの中かを見る。
  parent=$(dirname "$to")
  while [ ! -d "$parent" ] && [ "$parent" != "/" ] && [ -n "$parent" ]; do
    parent=$(dirname "$parent")
  done
  resolved=$(cd "$parent" 2>/dev/null && pwd -P) || {
    printf '中断: %s の置き場所を解決できません\n' "$rel" >&2
    return 1
  }
  case "$resolved" in
    "$TARGET" | "$TARGET"/*) return 0 ;;
  esac
  printf '中断: %s の書き込み先が作業ツリーの外（%s）を指します\n' "$rel" "$resolved" >&2
  return 1
}

# 既にあるパスの内容が主ディレクトリと食い違うかを見る。
# 食い違うときは上書きせず中断する。
differs_from_main() {
  local rel="$1"
  [ -e "$TARGET/$rel" ] || return 1
  if diff -rq "$MAIN_DIR/$rel" "$TARGET/$rel" >/dev/null 2>&1; then
    return 1
  fi
  printf '中断: %s の内容が主ディレクトリと異なります。手で確かめてください\n' "$rel" >&2
  return 0
}

# 1 つのパスを主ディレクトリから作業ツリーへ複製する。
# ハードリンクを試し、使えない配置ではファイル複製へ退避する。
# 複製元が無いパスは何もせず 0 を返す（無いことを失敗にするかは呼び出し側が決める）。
copy_one() {
  local rel="$1" from="$MAIN_DIR/$1" to="$TARGET/$1"
  [ -e "$from" ] || return 0
  destination_is_safe "$rel" || return 1

  if [ -e "$to" ]; then
    if differs_from_main "$rel"; then
      return 1
    fi
    return 0
  fi

  mkdir -p "$(dirname "$to")" 2>/dev/null
  # `cp -al` はハードリンクでの複製。テストから差し替えられるようにしておく。
  if [ -n "${WT_LINK_COMMAND:-}" ]; then
    if "$WT_LINK_COMMAND" "$from" "$to" 2>/dev/null; then
      return 0
    fi
  elif cp -al "$from" "$to" 2>/dev/null; then
    return 0
  fi
  # ハードリンクが途中で失敗すると、作りかけのディレクトリが残る。そのまま
  # `cp -a` すると、上書きではなくその中へ複製されて階層が二重になる。
  rm -rf "$to" 2>/dev/null
  cp -a "$from" "$to" 2>/dev/null || {
    printf '複製できませんでした: %s\n' "$rel" >&2
    return 1
  }
}

# 書き換えられるパスは、ハードリンクを外して実体で置き換える。
# 複製元が無いパスは何もせず 0 を返す。
replace_with_real_copy() {
  local rel="$1" from="$MAIN_DIR/$1" to="$TARGET/$1"
  [ -e "$from" ] || return 0
  destination_is_safe "$rel" || return 1
  # 作業ツリー側で書き換えられていたら、置き換えずに中断する。
  if [ -e "$to" ] && differs_from_main "$rel"; then
    return 1
  fi
  rm -rf "$to" 2>/dev/null
  mkdir -p "$(dirname "$to")" 2>/dev/null
  cp -a "$from" "$to" 2>/dev/null || {
    printf '複製できませんでした: %s\n' "$rel" >&2
    return 1
  }
}
