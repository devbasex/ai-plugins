#!/usr/bin/env bash
# NDF plugin: 作業ツリー運用の判定を集めた共通ライブラリ。
#
# 入口のスクリプト (worktree-guard.sh / worktree-session.sh など) は入力の受け取りと
# 出力の整形だけを行い、判定はすべてこのファイルの関数が持つ。同じ判定を 3 ランタイム
# 分の入口へ書くと片方だけが古くなるため、テストもこの層に対して書く。
#
# このファイルは source して使う。単体で実行しても何も起きない。
# 依存は bash と git、宣言ファイルを読むときだけ jq。無い場合は各関数が 1 を返す。

# 主ディレクトリで編集しても案内を出さないパスの既定。
# 宣言ファイルの guard.allow_paths が指定されていればそちらが優先する。
# 末尾が `/` の項目は前方一致、それ以外は完全一致とその配下を許可する。
WT_DEFAULT_ALLOW_PATHS=(
  "issues/"
  "docs/"
  ".claude/"
  ".codex/"
  ".kiro/"
  ".agents/"
  ".gemini/"
  ".serena/"
  ".ndf/"
  ".gitignore"
)

# 読み取れる宣言ファイルの版。知らない版は読まずに終わる。
WT_DECLARATION_VERSION=1

# 開発用の作業ツリーを置くディレクトリ (主ディレクトリからの相対)。
WT_WORKTREE_DIR=".worktrees"

# 逸脱検知でパスを並べる上限。超えた分は件数へ丸める。
WT_DIRTY_LIST_MAX=20

# 誘導の対象になる tool 名。ランタイムごとに名乗りが違うため、ここで 1 箇所に
# まとめる。hook の matcher もこの一覧から作る（両方に書くと片方が古くなる）。
#   編集系 — Claude Code は Edit / Write、Kiro CLI は fs_write、Gemini CLI は replace
#   パッチ系 — Codex CLI はパッチ本文で編集先を渡す
#   シェル系 — 書き込みを伴うコマンドの形から編集先を推定する
WT_EDIT_TOOLS="Edit|MultiEdit|Write|NotebookEdit|fs_write|edit_file|write_file|str_replace_editor|replace"
WT_PATCH_TOOLS="apply_patch"
WT_SHELL_TOOLS="Bash|shell|execute_bash|local_shell|run_command|run_shell_command"

# hook の matcher に書く正規表現を出力する。
wt_tool_matcher() {
  printf '%s|%s|%s\n' "$WT_EDIT_TOOLS" "$WT_PATCH_TOOLS" "$WT_SHELL_TOOLS"
}

# --- 補助 -------------------------------------------------------------------

# 標準入力を 1 行 1 要素で配列 WT_LINES へ読み込む。
# `mapfile` / `readarray` は bash 4 以降にしかない。macOS が標準で持つ bash は
# 3.2 で、そこで呼ぶと 127 を返して読み込みが空になる。hook は失敗しても黙って
# 終わるため、案内が出ない形で壊れる。
#
# 使い方: _wt_read_lines < <(コマンド); arr=("${WT_LINES[@]+"${WT_LINES[@]}"}")
_wt_read_lines() {
  WT_LINES=()
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    WT_LINES+=("$line")
  done
}

# --- 位置の解決 -------------------------------------------------------------

# `base` を起点に相対パスを実体の絶対パスへ直す。存在しなければ 1 を返す。
# `git -C <dir> rev-parse` が返すパスは <dir> からの相対になるため、現在地を
# 起点にすると解決できない。
_wt_abs_in() {
  local base="${1:-}" path="${2:-}"
  [ -n "$path" ] || return 1
  (cd "$base" 2>/dev/null && cd "$path" 2>/dev/null && pwd -P) || return 1
}

# 相対パスを実体の絶対パスへ直す。存在しなければ 1 を返す。
_wt_abs() {
  _wt_abs_in "$PWD" "${1:-}"
}

# git への問い合わせを、同じディレクトリについて 1 回に留めるための控え。
# 呼び出しは `git rev-parse --git-dir --git-common-dir` と
# `git rev-parse --show-superproject-working-tree` の 2 回まで。
# 引数を省くと現在地を解決する。控えは解決したディレクトリを鍵にする。
_wt_resolve() {
  local dir="${1:-$PWD}"
  [ "${_WT_RESOLVED_DIR:-}" = "$dir" ] && return "${_WT_RESOLVE_RC:-0}"
  _WT_RESOLVED_DIR="$dir"
  _WT_RESOLVE_RC=1
  _WT_MAIN_DIR=""
  _WT_IN_WORKTREE=1

  local dirs git_dir git_common super
  dirs=$(git -C "$dir" rev-parse --git-dir --git-common-dir 2>/dev/null) || return 1
  git_dir=$(printf '%s\n' "$dirs" | sed -n '1p')
  git_common=$(printf '%s\n' "$dirs" | sed -n '2p')
  git_dir=$(_wt_abs_in "$dir" "$git_dir") || return 1
  git_common=$(_wt_abs_in "$dir" "$git_common") || return 1

  # サブモジュールの中でも 2 つの git ディレクトリは異なる。作業ツリーと
  # 取り違えないよう、上位リポジトリを持つ場合は通常のリポジトリとして扱う。
  super=$(git -C "$dir" rev-parse --show-superproject-working-tree 2>/dev/null)
  if [ -n "$super" ]; then
    _WT_MAIN_DIR=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || return 1
    _WT_MAIN_DIR=$(_wt_abs "$_WT_MAIN_DIR") || return 1
    _WT_IN_WORKTREE=1
    _WT_RESOLVE_RC=0
    return 0
  fi

  if [ "$git_dir" != "$git_common" ]; then
    _WT_IN_WORKTREE=0
  fi
  _WT_MAIN_DIR=$(dirname "$git_common")
  _WT_RESOLVE_RC=0
  return 0
}

# 主ディレクトリの絶対パスを出力する。リポジトリの外では 1 を返す。
# 引数を省くと現在地から解決する。**対象を引数で受けるコマンドは対象を渡す。**
# 現在地から解決すると、別のリポジトリから実行したときに、対象とは違う
# リポジトリの宣言ファイル・台帳・ポートの帯で動く。
wt_main_dir() {
  _wt_resolve "${1:-$PWD}" || return 1
  [ -n "$_WT_MAIN_DIR" ] || return 1
  printf '%s\n' "$_WT_MAIN_DIR"
}

# 作業ツリーの中なら 0、主ディレクトリとサブモジュールの中なら 1 を返す。
# 引数を省くと現在地から解決する。
wt_in_worktree() {
  _wt_resolve "${1:-$PWD}" || return 1
  return "$_WT_IN_WORKTREE"
}

# --- 宣言ファイル -----------------------------------------------------------

# 主ディレクトリの .ndf/worktree.json を 1 行の JSON として出力する。
# ファイルが無い / JSON として読めない / 版が未対応のいずれでも、何も出力せず 1 を返す。
wt_declaration() {
  local main_dir="${1:-}" file json version
  [ -n "$main_dir" ] || return 1
  file="$main_dir/.ndf/worktree.json"
  [ -f "$file" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  json=$(jq -c '.' "$file" 2>/dev/null) || return 1
  [ -n "$json" ] || return 1
  version=$(printf '%s' "$json" | jq -r 'if (.version|type) == "number" then .version else empty end' 2>/dev/null)
  [ "$version" = "$WT_DECLARATION_VERSION" ] || return 1
  printf '%s\n' "$json"
}

# 宣言ファイルの状態を表す印を返す。存在しなければ空文字。
# 控えの作り直しが要るかを、git を呼ばずに判定するために使う。
#
# **内容から作る。** 更新時刻は秒までしか持たない実装があり、同じ秒のうちに
# 書き換えると印が変わらない。長さの変わらない書き換え（許可パスの入れ替えなど）は
# 大きさでも捉えられない。`cksum` は POSIX にあり、実測で 1 ミリ秒未満で終わる。
wt_declaration_stamp() {
  local main_dir="${1:-}" file
  [ -n "$main_dir" ] || return 1
  file="$main_dir/.ndf/worktree.json"
  [ -e "$file" ] || { printf '\n'; return 0; }

  if command -v cksum >/dev/null 2>&1; then
    cksum <"$file" 2>/dev/null && return 0
  fi
  # `cksum` が無い場合の退避。取り方は実装で分かれる。
  stat -c '%.9Y %s' "$file" 2>/dev/null && return 0
  stat -f '%Fm %z' "$file" 2>/dev/null && return 0
  stat -c '%Y %s' "$file" 2>/dev/null && return 0
  stat -f '%m %z' "$file" 2>/dev/null && return 0
  # どれも使えないときは毎回作り直す側へ倒す。
  printf 'unknown-%s\n' "$(date +%s%N 2>/dev/null || date +%s)"
}

# 案内を出さないパスを 1 行 1 件で出力する。
# 引数は wt_declaration の出力。空や未指定なら既定を返す。
wt_allow_paths() {
  local decl="${1:-}"
  # 空の配列は「何も許可しない」という指定である。出力が空であることと
  # 項目が無いことを区別するため、既定へ戻すかは配列の有無で決める。
  if [ -n "$decl" ] && command -v jq >/dev/null 2>&1 &&
     printf '%s' "$decl" | jq -e '(.guard.allow_paths | type) == "array"' >/dev/null 2>&1; then
    printf '%s' "$decl" | jq -r '.guard.allow_paths | .[]' 2>/dev/null
    return 0
  fi
  printf '%s\n' "${WT_DEFAULT_ALLOW_PATHS[@]}"
}

# --- パスの判定 -------------------------------------------------------------

# 主ディレクトリからの相対パスが許可一覧に該当すれば 0 を返す。
# 使い方: wt_is_allowed_path <相対パス> <許可項目>...
wt_is_allowed_path() {
  local rel="${1:-}" entry
  [ -n "$rel" ] || return 1
  shift || true
  for entry in "$@"; do
    [ -n "$entry" ] || continue
    case "$entry" in
      */)
        # ディレクトリそのものを指す形も許可する。`cp x docs/` の書き込み先は
        # 正規化の途中で末尾のスラッシュが落ち、`docs` として渡ってくる。
        [ "$rel" = "${entry%/}" ] && return 0
        case "$rel" in "$entry"*) return 0 ;; esac
        ;;
      *)
        [ "$rel" = "$entry" ] && return 0
        case "$rel" in "$entry"/*) return 0 ;; esac
        ;;
    esac
  done
  return 1
}

# 宣言に書かれた相対パスが、主ディレクトリと作業ツリーの中に収まるかを見る。
# 宣言の誤りで外側を読み書きしないよう、絶対パスと上位への移動を弾く。
wt_is_safe_relative() {
  case "${1:-}" in
    "" | /*) return 1 ;;
    "." | "..") return 1 ;;
    ../* | */.. | */../*) return 1 ;;
    "~"*) return 1 ;;
  esac
  return 0
}

# Compose のプロジェクト名を作る。実行系は名前を小文字へ揃え、`a-z0-9_-` 以外を
# 落としてから使う。基のディレクトリ名をそのまま渡すと、大文字や記号を含むとき
# 稼働中のコンテナを見つけられない。
wt_compose_project() {
  local name="${1:-}"
  [ -n "$name" ] || return 1
  name=$(printf '%s' "$name" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9_-]//g' -e 's/^[-_]*//')
  [ -n "$name" ] || return 1
  printf '%s\n' "$name"
}

# 絶対パスを主ディレクトリからの相対パスへ直す。外を指すなら 1 を返す。
wt_relative_to_main() {
  local path="${1:-}" main_dir="${2:-}"
  [ -n "$path" ] && [ -n "$main_dir" ] || return 1
  case "$path" in
    "$main_dir") printf '.\n'; return 0 ;;
    "$main_dir"/*) printf '%s\n' "${path#"$main_dir"/}"; return 0 ;;
    *) return 1 ;;
  esac
}

# --- シェルコマンドからの書き込み先の推定 -----------------------------------

# シェルの語分割を、引用符を解釈しながら行う。1 行 1 語で出力する。
# `sed -i 's/a b/c/' f` のように引用符の中へ空白を含む形を 1 語として扱うため、
# 単純な空白区切りでは足りない。
_wt_tokenize() {
  local s="${1:-}" n i c quote="" cur="" op last rest esc
  n=${#s}
  local -a out=()
  # 部分シェルの入口として切り出した `(` のうち、まだ閉じていない数。
  local subshells=0
  # 語の途中に現れた `(` のうち、まだ閉じていない数。`$(` の展開・`$((` の
  # 算術・関数定義の `f()`・配列の代入 `a=(` はいずれも部分シェルの入口では
  # ない。ここで数えておかないと、その閉じ括弧を部分シェルの終わりとして
  # 切り出してしまい、後続の相対パスの起点が入口の位置へ戻る。
  local inword=0
  # `case ... in` から `esac` までの入れ子の数と、その段が見出しを待っているか。
  # 見出しを閉じる `)` は部分シェルの終わりではない。数だけで決めると、部分
  # シェルの中の見出し (`( case $x in a) ... )`) で親の段を戻してしまう。
  local case_depth=0
  local -a case_state=()
  # 見出しの位置にいるか。`)` と `(` の役割はここで決まる。
  _tok_in_pattern() {
    [ "$case_depth" -gt 0 ] || return 1
    [ "${case_state[case_depth - 1]}" = want_pattern ] || return 1
    return 0
  }
  # 語を 1 つ出力し、`case` の段を進める。`case` と `esac` を数えるのは命令の
  # 位置にあるときだけで、`echo case` の `case` は入口として数えない。
  #
  # 命令の位置の一覧には**予約語の後ろも入れる**。`then` / `else` / `do` の直後に
  # `case` を置く形は普通にあり、数えないと見出しを閉じた `)` が
  # `__WT_CASE_END__` にならず、枝の中の `cd` が追跡から漏れる。呼び出し側
  # (`wt_extract_write_target`) の `at_cmd` が同じ予約語を挙げているのと揃える。
  _tok_emit() {
    local w="$1" prev_out=""
    ((${#out[@]} > 0)) && prev_out=${out[${#out[@]} - 1]}
    case "$w" in
      case|esac)
        case "$prev_out" in
          ""|__WT_SEP__|__WT_SUBSHELL_END__|__WT_CASE_END__|"|"|"|&"|"&"|"&&"|"||"|"("|"{"\
          |if|elif|then|else|while|until|do|"!"|time)
            if [ "$w" = case ]; then
              case_state[case_depth]=want_in
              case_depth=$((case_depth + 1))
            elif [ "$case_depth" -gt 0 ]; then
              case_depth=$((case_depth - 1))
            fi
            ;;
        esac
        ;;
      in)
        # `case` の対象の後ろの `in` だけが見出しの位置を開く。枝の中の
        # `for x in ...` の `in` は、その段が既に見出しを通っているため効かない。
        if [ "$case_depth" -gt 0 ] && [ "${case_state[case_depth - 1]}" = want_in ]; then
          case_state[case_depth - 1]=want_pattern
        fi
        ;;
    esac
    out+=("$w")
  }
  for ((i = 0; i < n; i++)); do
    c=${s:i:1}
    # `\` は次の 1 文字をエスケープする。**シングルクォートの中を除く。** 中では
    # `\` も字面で、閉じる `'` を隠さない (`'a\'` は `a\`)。実測で確かめた。
    #
    # 見なければ `"` の中の `\"` を閉じ引用符と読み、残りをまるごと 1 語へ吸い
    # 込む（検知漏れ）。引用符の外では `\ ` を区切り、`\)` を部分シェルの終わり
    # と読む（語の取り違えと誤検知）。
    if [ "$c" = '\' ] && [ "$quote" != "'" ]; then
      esc=${s:i+1:1}
      # 文字列の末尾の `\` はエスケープする相手がいない。字面のまま残す。
      if [ -z "$esc" ]; then cur+="$c"; continue; fi
      # `\` + 改行は行継続で、両方が消える。命令の区切りにもならない。
      if [ "$esc" = $'\n' ]; then i=$((i + 1)); continue; fi
      if [ -n "$quote" ]; then
        # **`"` の中で `\` がエスケープとして働く相手は限られる。** `$` `` ` ``
        # `"` `\` と改行だけで、それ以外の前では `\` が文字として残る
        # (`"a\nb"` は `a\nb`、`"a\\b"` は `a\b`。実測で確かめた)。語の
        # 区切りは変わらないが、語そのものが書き込み先のパスになるため、
        # 落とす `\` と残す `\` を分けないと実在しない位置を案内する。
        case "$esc" in
          '$'|'`'|'"'|'\') cur+="$esc" ;;
          *) cur+="$c$esc" ;;
        esac
      else
        # 引用符の外では次の 1 文字がそのまま語の一部になる。`\ ` の空白は
        # 区切りにならず、`\(` `\)` は部分シェルの入口・終わりにならない。
        cur+="$esc"
      fi
      i=$((i + 1))
      continue
    fi
    if [ -n "$quote" ]; then
      if [ "$c" = "$quote" ]; then quote=""; else cur+="$c"; fi
      continue
    fi
    case "$c" in
      "'"|'"') quote="$c" ;;
      # 改行と `;` はコマンドの区切りである。空白として捨てると、次の行の語を
      # 前のコマンドの対象と取り違える（`cp a b` の次の行の `echo c` の `c` を
      # 複製先として拾うなど）。区切りの印を独立した語として出す。
      $'\n'|";")
        if [ -n "$cur" ]; then _tok_emit "$cur"; cur=""; fi
        # `;;` `;&` `;;&` は `case` の枝の終わりで、次に来るのは見出しである。
        if [ "$c" = ";" ] && [ "$case_depth" -gt 0 ]; then
          case "${s:i+1:1}" in
            ";"|"&") case_state[case_depth - 1]=want_pattern ;;
          esac
        fi
        out+=("__WT_SEP__")
        ;;
      " "|$'\t')
        # `>& file` の `&` は、標準出力と標準エラーをまとめて 1 つのファイルへ
        # 向ける形の一部で、後ろの語がそのファイルになる。ここで区切ると `&` が
        # 単独の語になり、背景実行の演算子として読まれる（現在地がまとまりの
        # 入口へ戻る）。次の語まで繋げて 1 語にする。
        if [ "$cur" = "&" ]; then
          last=""
          ((${#out[@]} > 0)) && last=${out[${#out[@]} - 1]}
          case "$last" in
            __WT_REDIR__|__WT_APPEND__) continue ;;
          esac
        fi
        if [ -n "$cur" ]; then _tok_emit "$cur"; cur=""; fi
        ;;
      # 演算子は空白で囲まれているとは限らない。切り出さないと
      # `cp a b||echo c` の `b||echo` が 1 語になり、区切りとして見えない
      # （次のコマンドの `c` を複製先として拾う）。`>` と `>>` は呼び出し側が
      # 印へ置き換えるため、ここには現れない。
      "|"|"&")
        last=""
        ((${#out[@]} > 0)) && last=${out[${#out[@]} - 1]}
        # `>&2` `2>&1` `3<&0` の `&` はファイル記述子の複製であって、背景実行の
        # 演算子ではない。切ると後続が別のコマンドに見え、`cd` の効果を落とす。
        # 直前が `<` か、`>` の置き換えの印のときは字面のまま繋げる。
        if [ "$c" = "&" ] && { [ "${cur: -1}" = "<" ] ||
          { [ -z "$cur" ] &&
            { [ "$last" = "__WT_REDIR__" ] || [ "$last" = "__WT_APPEND__" ]; }; }; }; then
          cur+="$c"
          continue
        fi
        # 長い演算子を先に見る。`&&` を `&` 2 つに割ると、同じシェルで続く並びが
        # 背景実行 2 つになって意味が変わる。
        op="$c"
        case "${s:i:2}" in
          "&&"|"||"|"|&") op=${s:i:2} ;;
        esac
        if [ -n "$cur" ]; then _tok_emit "$cur"; cur=""; fi
        out+=("$op")
        i=$((i + ${#op} - 1))
        ;;
      # `(` は部分シェルを開く。語の頭にあるときだけ入口として切り出す。
      # 途中に現れる `(` は展開・関数定義・配列の代入の一部で、部分シェルでは
      # ない。字面のまま語へ残し、対応する `)` も切り出さないよう数える。
      "(")
        if [ -n "$cur" ] || [ "$inword" -gt 0 ]; then
          inword=$((inword + 1))
          cur+="$c"
          continue
        fi
        # 見出しの位置の `(` は飾りで、部分シェルの入口ではない
        # （`case $x in (a) ...`）。語の一部として残す。
        if _tok_in_pattern; then cur+="$c"; continue; fi
        # 中身の無い `()` は関数定義の目印で、部分シェルの入口ではない。`f ()`
        # のように空白を挟む書き方があるため、語の途中かどうかでは見分けられない。
        rest=${s:i+1}
        rest=${rest#"${rest%%[!$' \t']*}"}
        if [ "${rest:0:1}" = ")" ]; then
          inword=$((inword + 1))
          cur+="$c"
          continue
        fi
        out+=("$c")
        subshells=$((subshells + 1))
        ;;
      # `)` は、切り出した `(` が残っているときだけ部分シェルの終わりである。
      # `case` の見出し (`a)`) のように対応する `(` が無いものは語の一部で、
      # 切り出すと存在しない位置を書き込み先として示すことになる。
      ")")
        if [ "$inword" -gt 0 ]; then
          inword=$((inword - 1))
          cur+="$c"
          continue
        fi
        # 見出しを閉じる `)`。枝の本体が始まることを印で伝える。見出しの語と
        # くっついているか (`a)`) 離れているか (`a )`) で扱いを変えない。
        if _tok_in_pattern; then
          if [ -n "$cur" ]; then _tok_emit "$cur"; cur=""; fi
          case_state[case_depth - 1]=body
          out+=("__WT_CASE_END__")
          continue
        fi
        if [ "$subshells" -le 0 ]; then
          cur+="$c"
          continue
        fi
        if [ -n "$cur" ]; then _tok_emit "$cur"; cur=""; fi
        out+=("__WT_SUBSHELL_END__")
        subshells=$((subshells - 1))
        ;;
      *) cur+="$c" ;;
    esac
  done
  [ -n "$cur" ] && _tok_emit "$cur"
  unset -f _tok_in_pattern _tok_emit
  printf '%s\n' "${out[@]+"${out[@]}"}"
}

# 展開されるヒアドキュメントの本文を 1 行走査し、実行される断片だけを取り出す。
# 結果は _WT_SANITIZED に入り、コマンド置換の外は空白へ置き換わる。置換の境界
# （`$(` と対応する `)`、backtick）も空白にするため、閉じ括弧が書き込み先の語へ
# くっつかない。走査した行が置換に掛かっていれば _WT_OPENED を 1 にする。
#
# 状態は _WT_SUBST（`$(` の深さ）・_WT_BACKTICK・_WT_ARITH・_WT_QUOTE・_WT_QSTACK で
# 持ち回る。呼び出し側 (`_wt_strip_heredocs`) がこれらを `local` で宣言するため、
# グローバルへは残らない。
#
# **引用符は置換の中でだけ効く。** 本文そのものでは `$`・backtick・`\` だけが特別で、
# `'` と `"` は字面である。本文の `it's` を引用符の始まりとして数えると、そこから
# 後ろの `$(` を見落とす。逆に置換の中では引用符が効くため、`$(echo "a )" > f)` の
# 引用符に囲まれた `)` を閉じ括弧として数えると、置換がそこで終わったことになる。
#
# `$((...))` は算術展開で、コマンドは動かない。中の `>` は比較であって出力の
# 付け替えではないため、閉じるまで空白へ置き換える。
_wt_scan_expanded_line() {
  local line="${1:-}" n j c
  n=${#line}
  j=0
  _WT_OPENED=0
  _WT_SANITIZED=""
  if [ "$_WT_SUBST" -gt 0 ] || [ "$_WT_BACKTICK" = 1 ]; then _WT_OPENED=1; fi

  while [ "$j" -lt "$n" ]; do
    c=${line:j:1}

    if [ "$_WT_ARITH" -gt 0 ]; then
      case "$c" in
        '(') _WT_ARITH=$((_WT_ARITH + 1)) ;;
        ')') _WT_ARITH=$((_WT_ARITH - 1)) ;;
      esac
      _WT_SANITIZED+=" "
      j=$((j + 1))
      continue
    fi
    if [ "${line:j:3}" = '$((' ]; then
      _WT_ARITH=2
      _WT_SANITIZED+="   "
      j=$((j + 3))
      continue
    fi

    # 本文そのもの。実行されないため、字面は残さない。
    if [ "$_WT_SUBST" = 0 ] && [ "$_WT_BACKTICK" = 0 ]; then
      case "$c" in
        '\') _WT_SANITIZED+="  "; j=$((j + 2)); continue ;;
        '`') _WT_BACKTICK=1; _WT_OPENED=1; _WT_SANITIZED+=" "; j=$((j + 1)); continue ;;
      esac
      if [ "${line:j:2}" = '$(' ]; then
        _WT_QSTACK[$_WT_SUBST]="$_WT_QUOTE"
        _WT_SUBST=1
        _WT_QUOTE=""
        _WT_OPENED=1
        _WT_SANITIZED+="  "
        j=$((j + 2))
        continue
      fi
      _WT_SANITIZED+=" "
      j=$((j + 1))
      continue
    fi

    # 置換の中。シェルの引用符が効く。実行される部分なので字面を残す。
    if [ "$_WT_QUOTE" = "'" ]; then
      [ "$c" = "'" ] && _WT_QUOTE=""
      _WT_SANITIZED+="$c"
      j=$((j + 1))
      continue
    fi
    if [ "$c" = '\' ]; then
      _WT_SANITIZED+="${line:j:2}"
      j=$((j + 2))
      continue
    fi
    if [ "${line:j:2}" = '$(' ]; then
      _WT_QSTACK[$_WT_SUBST]="$_WT_QUOTE"
      _WT_SUBST=$((_WT_SUBST + 1))
      _WT_QUOTE=""
      _WT_SANITIZED+="  "
      j=$((j + 2))
      continue
    fi
    if [ "$_WT_QUOTE" = '"' ]; then
      [ "$c" = '"' ] && _WT_QUOTE=""
      _WT_SANITIZED+="$c"
      j=$((j + 1))
      continue
    fi
    case "$c" in
      "'"|'"') _WT_QUOTE="$c"; _WT_SANITIZED+="$c" ;;
      ')')
        if [ "$_WT_SUBST" -gt 0 ]; then
          _WT_SUBST=$((_WT_SUBST - 1))
          _WT_QUOTE="${_WT_QSTACK[$_WT_SUBST]:-}"
          _WT_SANITIZED+=" "
        else
          _WT_SANITIZED+="$c"
        fi
        ;;
      '`')
        if [ "$_WT_BACKTICK" = 1 ]; then _WT_BACKTICK=0; else _WT_BACKTICK=1; fi
        _WT_SANITIZED+=" "
        ;;
      *) _WT_SANITIZED+="$c" ;;
    esac
    j=$((j + 1))
  done
}

# ヒアドキュメントの本文を落とす。本文はコマンドとして実行される部分ではないため、
# 中の `>` や語を書き込み先として拾わない。引用符の中の `<<` と、行の入力を渡す
# `<<<` は本文の始まりとして扱わない。
#
# **終端の語を引用符で囲まない形は本文を落とさない。** この形の本文はシェルが展開し、
# 中のコマンド置換が実行される（`$(echo data > out.txt)` は out.txt を作る）。
# 展開される本文のうち、コマンド置換を含む行だけを残す。
_wt_strip_heredocs() {
  local text="${1:-}"
  local -a lines=() delims=() strips=() expands=()
  local line candidate out="" n i c delim strip quoted dq
  # 展開される本文の中で、コマンド置換が続いているかを行をまたいで持つ。
  # 走査は _wt_scan_expanded_line が行う。`local` で宣言すると、bash の動的
  # スコープにより呼び出し先からも読み書きできる。グローバルへは残らない。
  local _WT_SUBST=0 _WT_BACKTICK=0 _WT_QUOTE="" _WT_OPENED=0 _WT_ARITH=0
  local _WT_SANITIZED=""
  local -a _WT_QSTACK=()

  _wt_read_lines <<<"$text"
  lines=("${WT_LINES[@]+"${WT_LINES[@]}"}")

  # 読み終えた終端の語は、配列から外さずに添字で進める。空になった配列の
  # 展開は bash の版で扱いが分かれる。
  local head=0
  # 引用符の状態は行をまたいで続く。行ごとに初期化すると、複数行にわたる
  # 引用符の中の `<<` を本文の始まりとして数えてしまう。
  local quote=""
  for line in "${lines[@]+"${lines[@]}"}"; do
    # 本文の中では、終端の語が現れるまで読み飛ばす。
    if [ "$head" -lt "${#delims[@]}" ]; then
      candidate="$line"
      if [ "${strips[head]}" = 1 ]; then
        while [ "${candidate#	}" != "$candidate" ]; do candidate="${candidate#	}"; done
      fi
      if [ "$candidate" = "${delims[head]}" ]; then
        head=$((head + 1))
        _WT_SUBST=0
        _WT_BACKTICK=0
        _WT_ARITH=0
        _WT_QUOTE=""
        _WT_QSTACK=()
        continue
      fi
      # 展開される本文のコマンド置換は実行される。書き込みを見落とさないよう、
      # 置換の始まりから終わりまでを残す。置換は複数行にまたがることがあるため、
      # 深さを行をまたいで持ち越す。
      if [ "${expands[head]}" = 1 ]; then
        _wt_scan_expanded_line "$line"
        if [ "$_WT_OPENED" = 1 ]; then out+="$_WT_SANITIZED"$'\n'; fi
      fi
      continue
    fi

    n=${#line}
    i=0
    while [ "$i" -lt "$n" ]; do
      c=${line:i:1}
      # `\` は次の 1 文字をエスケープする。**シングルクォートの中を除く。**
      # 引用符の中でも効くため、`quote` を見る前に読み飛ばす。見なければ
      # `"` の中の `\"` を閉じ引用符と読み、その後の `<<` を本文の始まりとして
      # 数えない（本文が残り、実行されない行の語を書き込み先として拾う）。
      #
      # ここは位置だけを見る走査なので、`"` の中でエスケープが効く相手が
      # `$` `` ` `` `"` `\` と改行に限られることは結果を変えない。隠れる 1 文字が
      # 引用符でも `<` でもなければ、読み飛ばしても読み進めても同じである。
      if [ "$c" = '\' ] && [ "$quote" != "'" ]; then i=$((i + 2)); continue; fi
      if [ -n "$quote" ]; then
        [ "$c" = "$quote" ] && quote=""
        i=$((i + 1))
        continue
      fi
      case "$c" in
        "'"|'"') quote="$c"; i=$((i + 1)); continue ;;
      esac
      if [ "${line:i:3}" = "<<<" ]; then
        i=$((i + 3))
        continue
      fi
      if [ "${line:i:2}" != "<<" ]; then
        i=$((i + 1))
        continue
      fi
      i=$((i + 2))
      strip=0
      if [ "${line:i:1}" = "-" ]; then strip=1; i=$((i + 1)); fi
      while [ "${line:i:1}" = " " ] || [ "${line:i:1}" = $'\t' ]; do i=$((i + 1)); done
      # 終端の語。引用符は書き方の違いで、語そのものには含まれない。
      # 引用符を 1 つでも使えば、本文は展開されない。
      # **引用符の中では区切りで切らない。** `<<"EOF X"` のように空白や記号を
      # 含む語を、途中で切ると終端を見つけられない。
      delim=""
      quoted=0
      dq=""
      while [ "$i" -lt "$n" ]; do
        c=${line:i:1}
        if [ -n "$dq" ]; then
          # `"` の中の `\"` は引用を閉じない。閉じたと読むと終端の語を取り違え、
          # 本文の終わりを見つけられない（後続の命令まで本文として落とす）。
          # 落とす `\` と残す `\` の別は `_wt_tokenize` と同じ。
          # `'` の中では `\` は字面で、エスケープにならない。
          if [ "$dq" = '"' ] && [ "$c" = '\' ] && [ -n "${line:i+1:1}" ]; then
            case "${line:i+1:1}" in
              '$'|'`'|'"'|'\') delim+="${line:i+1:1}" ;;
              *) delim+="$c${line:i+1:1}" ;;
            esac
            i=$((i + 2))
            continue
          fi
          if [ "$c" = "$dq" ]; then dq=""; else delim+="$c"; fi
          i=$((i + 1))
          continue
        fi
        case "$c" in
          " "|$'\t'|";"|"|"|"&"|">"|"<") break ;;
          "'"|'"') dq="$c"; quoted=1 ;;
          # 引用符の外の `\` は次の 1 文字を字面にする。終端の語には `\` を
          # 含めない (`<<E\OF` の終端は `EOF`)。展開は止まるため `quoted` を立てる。
          '\')
            quoted=1
            if [ -n "${line:i+1:1}" ]; then delim+="${line:i+1:1}"; i=$((i + 1)); fi
            ;;
          *) delim+="$c" ;;
        esac
        i=$((i + 1))
      done
      if [ -n "$delim" ]; then
        delims+=("$delim")
        strips+=("$strip")
        expands+=("$((1 - quoted))")
      fi
    done
    out+="$line"$'\n'
  done

  printf '%s' "$out"
}

# コマンドの区切りにあたる語かを判定する。被演算子の走査は、ここで止める。
# 跨いで走査すると、次のコマンドの語を書き込み先と取り違える
# （`cp a b || echo c` の `c` を複製先として拾うなど）。
# `|&` は標準エラー出力も渡すパイプで、これも区切りにあたる。
# `;` と改行は字句解析が `__WT_SEP__` へ置き換えるため、この一覧には現れない。
# `)` は部分シェルの終わりで、ここでも命令が切れる。区切りとして扱わないと
# `( sed -i 's/a/b/' x.md )` の `)` を書き込み先として拾い、実在しない位置を示す。
_wt_is_separator() {
  case "$1" in
    __WT_*|"|"|"|&"|"&&"|"||"|"&"|")") return 0 ;;
  esac
  return 1
}

# 書き込み先として採らない語かを判定する。
_wt_is_not_target() {
  local s="$1"
  case "$s" in
    ""|"&"*|"|"*|"&&"|";"|"/dev/null"|"/dev/stdout"|"/dev/stderr") return 0 ;;
    __WT_*) return 0 ;;
    # 展開前の変数を含む語は、どのパスを指すかを決められない。字面のまま案内
    # すると、実在しない位置を書き込み先として示すことになる。
    *'$'*) return 0 ;;
  esac
  return 1
}

# シェルコマンドの文字列から書き込み先を 1 行 1 件で出力する。
# 対象は直接の書き換え (sed -i)・出力の付け替え (> / >>)・標準入力からの
# 書き出し (tee)・複製と移動 (cp / mv) の 4 形式に限る。推定できなければ 1 を返す。
#
# 第 2 引数に相対パスの起点を渡すと、出力は絶対パスになる。**同じコマンドの中で
# 先に実行される `cd` を反映する。** 反映しないと、作業ツリーへ移ってから相対パスで
# 書き換えたときに、移動前の位置を指した案内が出る。移動先を決められない形
# (`cd` 単独・`cd -`・展開前の変数) と、`||` の左辺のどこで失敗したのかを
# 決められない形では、相対パスの書き込み先を出さない。
# 字面のまま起点へ継ぎ足すと、実際には触っていない位置を案内することになる。
# 起点を渡さない呼び方では字面のまま返す。
wt_extract_write_target() {
  local cmd="${1:-}" base="${2:-}"
  [ -n "$cmd" ] || return 1

  # `cd` を追った現在地と、それが確かかどうか。起点を渡されない限り使わない。
  local cwd="$base" cwd_known=1
  # `cd` の効果が及ぶ範囲は、それが動くシェルの中に限られる。パイプの各区画と
  # 背景実行は部分シェルで動くため、後続のコマンドは移動前の位置のままになる。
  # 引き継いでしまうと、主ディレクトリへの書き込みを作業ツリー側と取り違えて
  # 案内を出さない。区画の入口の位置を 2 段で控え、`|` / `|&` ではパイプの入口へ、
  # `&` では処理のまとまりの入口へ戻す。
  local pipe_cwd="$base" pipe_known=1 pipe_cds=0 job_cwd="$base" job_known=1
  # `||` の右辺は**左辺が失敗したときに**走るため、直前の `cd` は効いていない。
  # 控えた位置へ戻す仕組みはパイプ・背景実行と同じだが、戻してよいかどうかの
  # 条件が要る。左辺のどこで失敗したかによって位置が変わるためである。
  # 判定に使う値を and-or リスト (`;` / 改行 / `&` で切れる並び) ごとに控える。
  local list_cwd="$base" list_known=1 list_cds=0 list_or=0 cd_is_last=0
  # `&&` の右辺の `cd` は、左辺が成功したときだけ走る。左辺の最後が `cd` なら
  # 走ったものとして扱えるが (`cd a && cd b`)、そうでなければ (`cmd && cd b`)
  # リストを抜けた後の現在地を決められない。跨いだかどうかを控える。
  local list_and_uncertain=0 list_cond_cd=0
  # 部分シェル (`( ... )`) の中の `cd` は、**その中の相対パスには効くが**、閉じた
  # 後の親のシェルの位置は変えない。中で解決しないと、作業ツリーへ移ってから
  # 書き換えたものを主ディレクトリへの書き込みとして案内する（誤検知になる）。
  # 逆に親へ漏らすと、抜けた後の主ディレクトリへの書き込みを作業ツリー側と
  # 取り違えて案内を出さない（検知漏れになる）。入口で追跡の状態を積み、出口で
  # 戻すことで、両方を満たす。入れ子にも耐えるよう深さごとに積む。
  local -a sub_cwd=() sub_known=() sub_cds=() sub_list_cds=() sub_cd_is_last=()
  local -a sub_list_or=() sub_and_unc=() sub_cond_cd=()
  local -a sub_list_cwd=() sub_list_known=()
  local -a sub_pipe_cwd=() sub_pipe_known=() sub_pipe_cds=()
  local subshell_depth=0
  # 条件分岐 (`if`) と繰り返し (`while` / `until` / `for` / `select`) の本体は、
  # 走るかどうかが実行時に決まる。中で `cd` を追ったときは、閉じた後の現在地を
  # 字面からは決められない。開いた時点の `cd` の回数を積んでおき、閉じるときに
  # 比べる。`else` / `elif` の手前でも比べる（条件の中の `cd` が失敗したときに
  # そちらへ来るため、その `cd` は効いていない）。
  # `{` で開くまとまりは必ず走るため、この積み上げの対象にしない。
  local -a block_cds=()
  local block_depth=0 cds=0
  # `&` の復元先は、背景実行にまとめられるひとまとまりの入口である。まとまりの
  # 境目は `;` / 改行 / `&` だが、複合コマンド (`{ }` / `( )` / `if` / `while` …)
  # の**中**の `;` は、外側のまとまりを切らない。切ると `{ cd x; } & …` の `&` の
  # 復元先が `x` になり、後続の相対パスを作業ツリー側と取り違えて案内を出さない
  # （検知漏れになる）。入口を入れ子の段ごとに積み、閉じるときに戻す。
  local -a group_cwd=() group_known=()
  local group_depth=0
  # `case` の枝どうしは排他で、見出し (`a)`) の間に走る命令は無い。枝の入口の
  # 位置は `case` を開いた時点の位置そのものである。次の見出しで戻さないと、
  # 前の枝の `cd` を引きずり、走っていない移動を書き込み先の起点に使う。
  # 見出しを命令の位置として数えるかどうかの判定にも使うため、深さを持つ。
  local -a case_cwd=() case_known=()
  local case_depth=0

  # ヒアドキュメントの本文を先に落とす。落とす前に印を挟むと、本文の中の `>` が
  # 出力の付け替えとして数えられる。
  cmd=$(_wt_strip_heredocs "$cmd")

  # `>path` のように空白の無い形を語へ分けるため、先に印を挟む。
  #
  # `&>` と `&>>` は、標準出力と標準エラーをまとめて 1 つのファイルへ向ける形
  # である（`&>>` は追記）。`&` は背景実行の演算子ではない。`>` だけを置き換えると
  # `&` が演算子として残り、現在地が処理のまとまりの入口へ戻る
  # （`cd a && echo hi &> f` の `f` を移動前の位置で解決してしまう）。
  # `>` より先に、まとめて 1 つの印へ置き換える。
  #
  # 置き換えの前に `&&` を退避する。`cmd&&>f` は `&&` と `>` だが、字面では `&`
  # と `>` が隣り合うため、退避しないと `&>` として拾い、残った `&` が背景実行の
  # 演算子になる。退避は字面をそのまま戻すため、引用符の中の語も変わらない。
  local spaced=${cmd//&&/__WT_ANDAND__}
  spaced=${spaced//&>>/ __WT_APPEND__ }
  spaced=${spaced//&>/ __WT_REDIR__ }
  # 戻すときは置換の字面を引用符で囲む。bash 5.2 以降は置換文字列の裸の `&` が
  # 「一致した部分」を指すため、囲まないと `&&` が `__WT_ANDAND__` 2 つへ戻る。
  spaced=${spaced//__WT_ANDAND__/"&&"}
  spaced=${spaced//>>/ __WT_APPEND__ }
  spaced=${spaced//>/ __WT_REDIR__ }

  local -a words=()
  _wt_read_lines < <(_wt_tokenize "$spaced")
  words=("${WT_LINES[@]+"${WT_LINES[@]}"}")

  local n=${#words[@]} i j w target found=0 prev="" at_cmd=0 dest="" k cmd_prefix=0 cd_end_of_options=0
  # `command` / `builtin` の被演算子を命令の位置として数えている間だけ 1。
  local cmd_wrapper=0 or_next=""
  # `||` の右辺のブレースグループが必ず後続へ進まないと判ったときに積む。まとまり
  # を閉じる `}` の添字と、そこで戻す位置を持つ。入れ子は内側が先に閉じるため、
  # 積んだ順にそのまま取り出せる。
  local -a or_end=() or_cwd=() or_known=()
  local or_depth=0
  # `_or_group_exits` が返す、まとまりを閉じる `}` の添字。
  local _WT_GROUP_END=0
  # 移動前の位置で解決し終えたリダイレクトの、走査が届いた語の位置。`cd` の枝と
  # `||` の右辺の枝が書き込む。ここより手前の `__WT_REDIR__` / `__WT_APPEND__` を
  # 二度拾わない。
  local resolved_redir_end=0
  # `_redir_target` の結果。書き込み先の語と、読み進めた最後の語の位置。
  local _WT_REDIR_DEST="" _WT_REDIR_END=0
  # 印 (`__WT_REDIR__` / `__WT_APPEND__`) の後ろの語から、実際に開かれる
  # ファイルを決める。`>&` には用法が 2 つある。
  #
  # - `2>&1` `>&2` `>&-` はファイル記述子の複製と閉鎖で、ファイルは開かない
  # - `>& file` `>&file` は `&>` と同義で、後ろの語がファイルになる
  #
  # 印の後ろが `&` に続く数字か `-` だけなら前者、それ以外なら後者である。
  # 記述子を指定した `2>&file` は bash が ambiguous redirect として拒む（実測）
  # ため実際には開かれないが、印へ置き換えた後は `echo 2 >& file` と字面が
  # 同じになり区別できない。案内が多めに出る側へ倒し、書き込み先として扱う。
  _redir_target() {
    local p=$(($1 + 1)) nx rest
    _WT_REDIR_DEST=""
    _WT_REDIR_END=$p
    nx=${words[p]:-}
    case "$nx" in
      # `>& file` の `&`。語として切れているときはファイル名が次の語にある。
      "&") _WT_REDIR_END=$((p + 1)); _WT_REDIR_DEST=${words[p + 1]:-} ;;
      "&"*)
        rest=${nx#&}
        case "$rest" in
          # `>&-` は記述子を閉じる。
          "-") ;;
          # 数字以外を含むならファイル名である。
          *[!0-9]*) _WT_REDIR_DEST=$rest ;;
          # 数字だけなら記述子の複製である。
          *) ;;
        esac
        ;;
      *) _WT_REDIR_DEST=$nx ;;
    esac
  }
  # 複合コマンドの入口で `&` の復元先を積み、出口で戻す。
  _push_group() {
    group_cwd[group_depth]="$job_cwd"
    group_known[group_depth]="$job_known"
    group_depth=$((group_depth + 1))
    job_cwd="$cwd"; job_known="$cwd_known"
  }
  _pop_group() {
    [ "$group_depth" -gt 0 ] || return 0
    group_depth=$((group_depth - 1))
    job_cwd="${group_cwd[group_depth]}"
    job_known="${group_known[group_depth]}"
  }
  # 部分シェルの入口で親の追跡状態を積み、中は新しいコマンドの並びとして始める。
  _push_subshell() {
    sub_cwd[subshell_depth]="$cwd"
    sub_known[subshell_depth]="$cwd_known"
    sub_cds[subshell_depth]="$cds"
    sub_list_cds[subshell_depth]="$list_cds"
    sub_cd_is_last[subshell_depth]="$cd_is_last"
    sub_list_or[subshell_depth]="$list_or"
    sub_and_unc[subshell_depth]="$list_and_uncertain"
    sub_cond_cd[subshell_depth]="$list_cond_cd"
    sub_list_cwd[subshell_depth]="$list_cwd"
    sub_list_known[subshell_depth]="$list_known"
    sub_pipe_cwd[subshell_depth]="$pipe_cwd"
    sub_pipe_known[subshell_depth]="$pipe_known"
    sub_pipe_cds[subshell_depth]="$pipe_cds"
    subshell_depth=$((subshell_depth + 1))
    list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0; list_or=0
    list_and_uncertain=0; list_cond_cd=0; cd_is_last=0
    pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
  }
  # 出口で親の追跡状態へ戻す。中で数えた `cd` も戻すため、部分シェルは外側の
  # 判定（`||` の右辺・複合コマンドを閉じるときの比較）から見えなくなる。
  _pop_subshell() {
    [ "$subshell_depth" -gt 0 ] || return 0
    subshell_depth=$((subshell_depth - 1))
    cwd="${sub_cwd[subshell_depth]}"
    cwd_known="${sub_known[subshell_depth]}"
    cds="${sub_cds[subshell_depth]}"
    list_cds="${sub_list_cds[subshell_depth]}"
    cd_is_last="${sub_cd_is_last[subshell_depth]}"
    list_or="${sub_list_or[subshell_depth]}"
    list_and_uncertain="${sub_and_unc[subshell_depth]}"
    list_cond_cd="${sub_cond_cd[subshell_depth]}"
    list_cwd="${sub_list_cwd[subshell_depth]}"
    list_known="${sub_list_known[subshell_depth]}"
    pipe_cwd="${sub_pipe_cwd[subshell_depth]}"
    pipe_known="${sub_pipe_known[subshell_depth]}"
    pipe_cds="${sub_pipe_cds[subshell_depth]}"
  }
  # `||` の右辺のブレースグループ (`{ ... }`) が、必ず後続へ進まないかを見る。
  # まとまりは同じシェルで走るため、その中の `exit` はスクリプトを終える。
  # 引数はまとまりを開く `{` の添字で、閉じる `}` の添字を `_WT_GROUP_END` に置く。
  #
  # 数えるのは**まとまりの直下にある、条件の付かない非継続命令**だけである。
  # `{ [ -n "$x" ] && exit; }` や `{ if ...; then exit; fi; }` は、通ったかどうかが
  # 実行時に決まる。字面では抜けたと言い切れないため数えない。候補にするのは
  # `depth` が 1 の位置にあり、直前が `{` か `__WT_SEP__` の語だけである。
  #
  # 候補は、その命令が終わるところまで見てから確定する (`pend`)。`{ exit 1 & }` の
  # 背景実行とパイプの区画は部分シェルで走り、親のシェルは続くためである。引数と
  # リダイレクトは命令の一部なので跨ぎ、`&` `|` `|&` で取り消し、`;`（改行）・
  # `&&` `||`・閉じる `}` で確定する。
  #
  # 予約語として深さを動かすのは命令の位置にある語だけである。`echo done` の
  # `done` を数えると深さが狂い、後ろの非継続命令が候補から外れる。それでも数え
  # 損ねたときは、抜けると言い切れない側（従来どおりの抑止）へ倒れる。
  _or_group_exits() {
    local p="$1" depth=0 pw="" tw exits=0 pend=0 at_pos j
    _WT_GROUP_END=0
    for ((j = p; j < n; j++)); do
      tw=${words[j]}
      if [ "$pend" = 1 ]; then
        case "$tw" in
          "&"|"|"|"|&") pend=0 ;;
          __WT_SEP__|"}"|"&&"|"||") exits=1; pend=0 ;;
        esac
      fi
      at_pos=0
      case "$pw" in
        ""|"{"|"("|__WT_SEP__|__WT_CASE_END__|__WT_SUBSHELL_END__|"&&"|"||"|"|"|"|&"|"&"\
        |if|elif|then|else|while|until|do|"!"|time) at_pos=1 ;;
      esac
      case "$tw" in
        "{"|"(") depth=$((depth + 1)) ;;
        "}"|__WT_SUBSHELL_END__)
          depth=$((depth - 1))
          if [ "$depth" -le 0 ]; then
            _WT_GROUP_END=$j
            [ "$exits" = 1 ] && return 0
            return 1
          fi
          ;;
        if|while|until|for|select|case)
          if [ "$at_pos" = 1 ]; then depth=$((depth + 1)); fi
          ;;
        fi|done|esac)
          if [ "$at_pos" = 1 ]; then depth=$((depth - 1)); fi
          ;;
        exit|return|break|continue)
          if [ "$depth" = 1 ]; then
            case "$pw" in "{"|__WT_SEP__) pend=1 ;; esac
          fi
          ;;
      esac
      pw="$tw"
    done
    return 1
  }
  # `||` の右辺の非継続命令 (`exit` / `return` / `break` / `continue`) に付いた
  # リダイレクトを、**左辺が失敗した位置**で解決する。右辺へ到達したのは左辺が
  # 失敗したときだけで、そのとき `cd` は効いていない。移動した後の位置で解決すると
  # 主ディレクトリ側への書き込みを作業ツリー側と取り違えて案内を出さない
  # （検知漏れになる）。`cd` 自身に付いたリダイレクトと同じ考え方である。
  #
  # 失敗した命令を字面から特定できるのは、リストの中の `cd` がちょうど 1 つで、
  # それが直前の命令のときだけである。特定できない形では相対パスを出さない。
  # 判定は下の失敗時の扱いと同じで、あちらは右辺そのものが走る位置を決めるのに
  # 対し、ここはリダイレクトが開かれる位置を決める（どちらも同じ位置になる）。
  _or_exit_redirs() {
    local p="$1" j2 saved_cwd="$cwd" saved_known="$cwd_known"
    if [ "$list_cds" = 1 ] && [ "$cd_is_last" = 1 ]; then
      cwd="$list_cwd"; cwd_known="$list_known"
    elif [ "$list_cds" != 0 ]; then
      cwd_known=0
    fi
    # リダイレクトの印は `_wt_is_separator` にも当たるため、先に見る。
    for ((j2 = p; j2 < n; j2++)); do
      case "${words[j2]}" in
        __WT_REDIR__|__WT_APPEND__)
          _redir_target "$j2"
          _emit "$_WT_REDIR_DEST"
          j2=$_WT_REDIR_END
          continue
          ;;
      esac
      if _wt_is_separator "${words[j2]}"; then break; fi
    done
    # 走査が届いた位置を控える。`__WT_REDIR__` の枝が同じ語を二度拾わない。
    resolved_redir_end=$j2
    cwd="$saved_cwd"; cwd_known="$saved_known"
  }
  _emit() {
    _wt_is_not_target "$1" && return
    if [ -z "$base" ]; then
      printf '%s\n' "$1"
      found=1
      return
    fi
    case "$1" in
      /*) ;;
      # 現在地が定まらない間の相対パスは、どこを指すか決められない。
      *) [ "$cwd_known" = 1 ] || return ;;
    esac
    printf '%s\n' "$(wt_normalize_path "$1" "$cwd")"
    found=1
  }

  for ((i = 0; i < n; i++)); do
    w=${words[i]}
    # コマンドの位置にある語だけを命令として扱う。`echo cd > f` の `cd` を
    # 移動として数えると、書き込み先の起点がずれる。
    at_cmd=0
    if [ "$i" = 0 ]; then
      at_cmd=1
    else
      case "$prev" in
        __WT_SEP__|"&&"|"||"|"|"|"|&"|"&") at_cmd=1 ;;
        # 予約語の後ろも命令の位置である。ここに挙げるのは、続きを**同じシェル**
        # で走らせる語だけである。中の `cd` の効果は後続へ残るため、書き込み先の
        # 起点に反映しなければ移動前の位置を指した案内が出る。
        if|elif|then|else|while|until|do|"{"|"!"|time) at_cmd=1 ;;
        # `(` は部分シェルを開く。中も命令の位置ではあるが、そこの `cd` は親の
        # 位置を変えないため、深さ (`subshell_depth`) で別に抑える。命令の位置
        # として数えるのは、入れ子の `( ( ... ) )` で内側の `(` を見落とさない
        # ためである。**`coproc` は意図して外す。** 同じく部分シェルを開くが、
        # 語として切れる形が一定でないため深さを数えられない。
        "(") at_cmd=1 ;;
        # `case` の見出し (`a)` `*)` `"a")` `(a)`) の後ろは、その枝の本体が始まる
        # 位置である。数えないと枝の中の `cd` が追跡から漏れ、作業ツリーへ移って
        # から書き換えたものを主ディレクトリへの書き込みとして案内する（誤検知に
        # なる）。見出しを閉じた `)` かどうかは字句解析が決め、`__WT_CASE_END__`
        # として渡す。ここでは印だけを見る。
        __WT_CASE_END__)
          if [ "$case_depth" -gt 0 ]; then
            at_cmd=1
            # 枝の入口は `case` を開いた位置である。前の枝の `cd` は走っていない。
            cwd="${case_cwd[case_depth - 1]}"
            cwd_known="${case_known[case_depth - 1]}"
            pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
            job_cwd="$cwd"; job_known="$cwd_known"
            list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0; list_or=0
            list_and_uncertain=0; list_cond_cd=0
          fi
          ;;
        # `fi` / `done` / `esac` / `}` / `__WT_SUBSHELL_END__` は複合コマンドの
        # 終わりで、後ろに続くのは区切りであって命令ではない。`for` / `select` /
        # `case` / `in` の後ろは名前や語の並びで、いずれも `cd` を置ける位置では
        # ない。命令の位置として数えない。
      esac
    fi
    # 命令の前には変数代入を並べられる。`FOO=bar cd x` の `cd` を命令として
    # 数えないと、移動が書き込み先の起点へ反映されない。`echo a=b cd x` の
    # `cd` は単なる引数なので、命令の位置から代入だけが途切れずに続いている
    # 間だけ、次の語も命令の位置とみなす。`FOO+=bar` も代入である。
    #
    # `command` と `builtin` も後ろの語を命令として走らせる。どちらも `cd` を
    # 現在のシェルで動かすため、被演算子として読み飛ばすと移動が起点へ反映
    # されない。オプションが挟まる形 (`command -p cd`) も追う。
    if [ "$at_cmd" = 0 ] &&
      { [ "$cmd_prefix" = 1 ] || [ "$cmd_wrapper" = 1 ]; }; then at_cmd=1; fi
    if [ "$at_cmd" = 1 ] && [[ $w =~ ^[A-Za-z_][A-Za-z0-9_]*\+?= ]]; then
      cmd_prefix=1
    else
      cmd_prefix=0
    fi
    if [ "$at_cmd" = 1 ]; then
      case "$w" in
        command|builtin) cmd_wrapper=1 ;;
        # `command` のオプションのうち、`-p` と `--` は後ろに命令が続く。
        # `-v` / `-V` は名前を表示するだけで走らせないため、ここで打ち切る
        # （`*` の枝が 0 へ戻す）。`builtin` は `--` だけを受け取る。
        -p|--) ;;
        *) cmd_wrapper=0 ;;
      esac
    fi
    if [ "$at_cmd" = 1 ]; then
      case "$w" in
        "|"|"|&"|"&"|"&&"|"||"|__WT_SEP__) ;;
        *) cd_is_last=0 ;;
      esac
    fi
    prev="$w"
    case "$w" in
      "|"|"|&")
        # パイプの各区画は部分シェルで動く。入口の位置へ戻す。
        cwd="$pipe_cwd"; cwd_known="$pipe_known"
        # 区画の中の `cd` は親の位置を変えない。`||` の判定に使う回数も戻す。
        list_cds="$pipe_cds"; cd_is_last=0
        continue
        ;;
      "&")
        # 背景実行は処理のまとまりごと部分シェルへ入る。入口の位置へ戻す。
        cwd="$job_cwd"; cwd_known="$job_known"
        pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
        # `&` は and-or リストの終わりでもある。入口を引き直す。
        list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0; list_or=0
        list_and_uncertain=0; list_cond_cd=0
        cd_is_last=0
        continue
        ;;
      "&&")
        # 左辺が成功したときに走る。移動の効果は残る。パイプの入口だけ引き直す。
        # 左辺の最後が `cd` でなければ、右辺が走ったかどうかを字面から決められない。
        [ "$cd_is_last" = 1 ] || list_and_uncertain=1
        # `||` を跨いだリストに `cd` があると、左右どちらの経路を通ったかで現在地
        # が変わる。右辺はどちらの経路からも走るため、位置を決められない
        # （`cd a || cd b && echo hi > f` の `f` は `a` 側にも `b` 側にもなる）。
        # `__WT_SEP__` の枝と同じ判定である。`list_cond_cd` を見ないのは、`&&` の
        # 右辺は左辺が成功したときにだけ走るためで、跨いだ先の `cd` は走った
        # ことが確かである。
        if [ "$list_or" = 1 ] && [ "$list_cds" != 0 ]; then cwd_known=0; fi
        pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds="$list_cds"
        continue
        ;;
      "||")
        # 右辺が**後続へ進まない命令**なら、そこを過ぎた時点で左辺の成功が確定
        # している。`cd /main || exit` は主ディレクトリへ移る定番の形で、右辺へ
        # 到達したときはそのまま終わるため、続きが走るのは移動した後の位置だけ
        # である。ここを一律に「決められない」と扱うと、正しく移った後の相対
        # パスの書き込みを案内できない。
        #
        # 数えるのは `exit` / `return` / `break` / `continue` の 4 つで、bash の
        # 実測で確かめた。`exit` は無条件にシェルを終える。`return` は関数と
        # source した本文の中で、`break` / `continue` はループの中で、それぞれ
        # 残りの命令へ進まない（外側で使うと bash がエラーを出すため、その形は
        # そもそも成立しない）。`exec` は外す。命令を伴えば置き換わるが、
        # `exec 2>log` のようにリダイレクトだけなら後続へ進む。
        #
        # 先行する `||` で経路が分かれている (`list_or` が 1) ときは使えない。
        # 左辺のどちらを通ったかで位置が変わり、右辺の `exit` では絞れない。
        # 部分シェルの `(exit)` も対象外で、字句解析が `(` を別の語として渡す。
        or_next=""
        ((i + 1 < n)) && or_next=${words[i + 1]}
        if [ "$list_or" = 0 ]; then
          case "$or_next" in
            exit|return|break|continue)
              # 右辺に付いたリダイレクトは、左辺が失敗した位置で開かれる。
              # 位置を持ち替える前に解決する。
              _or_exit_redirs "$((i + 1))"
              # 左辺が成功した位置をそのまま持つ。判定に使う値は引き直す。
              list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0
              list_and_uncertain=0; list_cond_cd=0; cd_is_last=0
              pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
              continue
              ;;
            "{")
              # `cd dir || { echo ...; exit 1; }` は `|| exit` より広く使われる。
              # まとまりの中に条件の付かない非継続命令があれば、抜けた先へは
              # 進まないため、続きが走るのは移動した後の位置だけである。
              #
              # ただし**まとまりの中は左辺が失敗した位置で走る**（`cd` は効いて
              # いない）。ここで位置を戻すと中の書き込み先を取り違えるため、
              # 移動した後の位置は閉じる `}` まで預け、下の失敗時の扱いへ落とす。
              if _or_group_exits "$((i + 1))"; then
                or_end[or_depth]=$_WT_GROUP_END
                or_cwd[or_depth]="$cwd"
                or_known[or_depth]="$cwd_known"
                or_depth=$((or_depth + 1))
              fi
              ;;
          esac
        fi
        # 左辺が失敗したときに走る。失敗した命令を字面から特定できるのは、
        # リストの中の `cd` がちょうど 1 つで、それが直前の命令のときだけである。
        # このとき失敗したのはその `cd` なので、右辺はリストの入口の位置で走る。
        # 位置を捨てず案内を出せるほうを選び、特定できない形だけ抑止する。
        if [ "$list_cds" = 1 ] && [ "$cd_is_last" = 1 ]; then
          cwd="$list_cwd"; cwd_known="$list_known"
        elif [ "$list_cds" != 0 ]; then
          # `cd a && cd b || x` のように左辺に命令が複数あると、どこで失敗した
          # かで位置が変わる。決められないものとして相対パスを抑止する。
          cwd_known=0
        fi
        list_or=1
        pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds="$list_cds"
        continue
        ;;
      __WT_SEP__)
        # `;` と改行でも同じシェルが続く。両方の入口を引き直す。
        # ただし `||` を跨いだリストに `cd` があると、それが効いたかどうかは
        # 左辺の成否で決まる。リストを抜けた後の位置も決められない。
        # `&&` を跨いだ先に `cd` があるときも同じで、左辺の成否で位置が変わる。
        if { [ "$list_or" = 1 ] && [ "$list_cds" != 0 ]; } ||
          [ "$list_cond_cd" = 1 ]; then
          cwd_known=0
        fi
        pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
        job_cwd="$cwd"; job_known="$cwd_known"
        list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0; list_or=0
        list_and_uncertain=0; list_cond_cd=0
        cd_is_last=0
        continue
        ;;
      if|while|until|for|select|case)
        # 複合コマンドの入口。中で `cd` を追ったかを、閉じるときに比べるため控える。
        if [ "$at_cmd" = 1 ]; then
          block_cds[block_depth]=$cds
          block_depth=$((block_depth + 1))
          _push_group
          if [ "$w" = case ]; then
            case_cwd[case_depth]="$cwd"
            case_known[case_depth]="$cwd_known"
            case_depth=$((case_depth + 1))
          fi
        fi
        continue
        ;;
      "{"|"(")
        # 部分シェル (`(`) と、同じシェルで走るまとまり (`{`) の入口。どちらも
        # 中の `;` が外側のまとまりを切らないため、`&` の復元先を積む。
        if [ "$at_cmd" = 1 ]; then
          _push_group
          [ "$w" = "(" ] && _push_subshell
        fi
        continue
        ;;
      "}")
        # `}` は予約語で、命令の位置にしか置けない。`echo }` の `}` は語である。
        if [ "$at_cmd" = 1 ]; then
          _pop_group
          # 必ず抜けるまとまりを閉じた。ここへ来た経路は左辺が成功した側だけで、
          # 位置は `||` で預けた移動後のものへ戻る。判定に使う値も引き直す。
          if [ "$or_depth" -gt 0 ] && [ "$i" = "${or_end[or_depth - 1]}" ]; then
            or_depth=$((or_depth - 1))
            cwd="${or_cwd[or_depth]}"; cwd_known="${or_known[or_depth]}"
            list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0; list_or=0
            list_and_uncertain=0; list_cond_cd=0; cd_is_last=0
            pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
          fi
        fi
        continue
        ;;
      __WT_SUBSHELL_END__)
        # 部分シェルの終わり。字句解析が切り出した `(` に対応するものだけが
        # この印になる。配列の代入 `a=( 1 )`・`case` の見出し・関数定義の `()`
        # の `)` は語の一部で、ここへは来ない（来ると親の段まで戻ってしまう）。
        # 対応する `(` を数え損ねていても、深さは 0 で止める（`_pop_subshell` が
        # 深さ 0 で何もしない）。戻さなければ入口の位置のままで、案内が多めに
        # 出る側へ倒れる。
        _pop_group
        _pop_subshell
        continue
        ;;
      else|elif)
        # 条件が偽のときに走る。条件の中の `cd` は効いていない。
        if [ "$at_cmd" = 1 ] && [ "$block_depth" -gt 0 ] &&
          [ "$cds" -gt "${block_cds[block_depth - 1]}" ]; then
          cwd_known=0
        fi
        continue
        ;;
      fi|done|esac)
        # 本体が走ったかどうかは実行時に決まる。中で移動していたなら、閉じた後の
        # 現在地を決められない。
        if [ "$at_cmd" = 1 ] && [ "$block_depth" -gt 0 ]; then
          block_depth=$((block_depth - 1))
          [ "$cds" -gt "${block_cds[block_depth]}" ] && cwd_known=0
          _pop_group
          if [ "$w" = esac ] && [ "$case_depth" -gt 0 ]; then
            case_depth=$((case_depth - 1))
          fi
        fi
        continue
        ;;
      cd)
        [ "$at_cmd" = 1 ] && [ -n "$base" ] || continue
        # 部分シェルの中でも移動は追う。中の相対パスはここで解決する。親の位置は
        # `)` で `_pop_subshell` が戻すため、この移動は外へ漏れない。
        # 移動先の語と、この `cd` に付いたリダイレクトを 1 回の走査で拾う。
        # **リダイレクト先は移動する前の位置で開かれる。** シェルはリダイレクトを
        # 開いてから命令を実行するためである。移動後の位置で解決すると、主
        # ディレクトリ側への書き込みを作業ツリー側と取り違えて案内を出さない
        # （検知漏れになる）。まだ `cwd` を更新していないここで解決する。
        dest=""
        cd_end_of_options=0
        for ((k = i + 1; k < n; k++)); do
          case "${words[k]}" in
            __WT_REDIR__|__WT_APPEND__)
              _redir_target "$k"
              _emit "$_WT_REDIR_DEST"
              k=$_WT_REDIR_END
              continue
              ;;
          esac
          if _wt_is_separator "${words[k]}"; then break; fi
          case "${words[k]}" in
            # `--` 以降はオプションの解釈を止める。`cd -- -dir` の `-dir` は
            # 移動先であって `cd -` ではない。止めないと読み飛ばして、後続の
            # 相対パスを抑止する。
            --) [ "$cd_end_of_options" = 1 ] || { cd_end_of_options=1; continue; } ;;
            # **`-` だけは `--` の後でも直前の位置を指す。** bash では `-` が
            # オプションではなく被演算子の綴りとして扱われるためで、`-` という
            # 名前のディレクトリがあっても `$OLDPWD` へ移る（実測で確認）。
            # 字面からは追えないため、移動先を決めない。
            -) continue ;;
            # `cd -` と同じく、オプションは移動先ではない。
            -*) [ "$cd_end_of_options" = 1 ] || continue ;;
          esac
          # 移動先は最初の被演算子である。リダイレクトを拾い切るため、
          # 見つけても区切りまで走査を続ける。
          [ -n "$dest" ] || dest=${words[k]}
        done
        # 走査が届いた位置を控える。`__WT_REDIR__` の枝が同じ語を二度拾わない。
        resolved_redir_end=$k
        # `||` の右辺で戻せるかどうかの判定に使う。
        list_cds=$((list_cds + 1)); cd_is_last=1
        # `&&` を跨いだ先の `cd` は、走ったかどうかが左辺の成否で決まる。
        [ "$list_and_uncertain" = 0 ] || list_cond_cd=1
        # 複合コマンドを閉じるときの比較に使う。
        cds=$((cds + 1))
        case "$dest" in
          # 引数なし (ホーム)・`cd -`・展開前の変数・チルダ展開。いずれも
          # コマンドの字面からは移動先を決められない。
          ""|*'$'*|"~"*) cwd_known=0 ;;
          /*) cwd=$(wt_normalize_path "$dest" "/"); cwd_known=1 ;;
          *) [ "$cwd_known" = 1 ] && cwd=$(wt_normalize_path "$dest" "$cwd") ;;
        esac
        ;;
      __WT_REDIR__|__WT_APPEND__)
        # 移動前の位置で解決済みのリダイレクトは、その枝が拾い終えている。
        if [ "$i" -lt "$resolved_redir_end" ]; then continue; fi
        _redir_target "$i"
        _emit "$_WT_REDIR_DEST"
        ;;
      tee)
        # tee は並べたファイルすべてへ書き込む。1 件目で止めない。
        for ((j = i + 1; j < n; j++)); do
          if _wt_is_separator "${words[j]}"; then break; fi
          case "${words[j]}" in
            -*) continue ;;
            *) _emit "${words[j]}" ;;
          esac
        done
        ;;
      sed)
        # in-place の指定があるとき、操作対象のファイルをすべて拾う。
        # `-e` / `-f` が現れなければ、最初の被演算子がスクリプトで残りがファイル。
        local has_inplace=0 seen_script=0 skip_next=0
        local -a files=()
        for ((j = i + 1; j < n; j++)); do
          if [ "$skip_next" = 1 ]; then skip_next=0; continue; fi
          if _wt_is_separator "${words[j]}"; then break; fi
          case "${words[j]}" in
            --in-place|--in-place=*) has_inplace=1 ;;
            -e|-f|--expression|--file) seen_script=1; skip_next=1 ;;
            --expression=*|--file=*) seen_script=1 ;;
            # `-es/a/b/` のように空白を挟まずスクリプトを続ける形もある。
            # 見落とすと、最初のファイルをスクリプトと取り違える。
            -e*|-f*) seen_script=1 ;;
            --) ;;
            -*)
              if [[ ${words[j]} =~ ^-[a-zA-Z]*i([a-zA-Z]*|\..*)$ ]]; then
                has_inplace=1
              fi
              ;;
            *)
              if [ "$seen_script" = 0 ]; then
                seen_script=1
              else
                files+=("${words[j]}")
              fi
              ;;
          esac
        done
        if [ "$has_inplace" = 1 ]; then
          for target in "${files[@]+"${files[@]}"}"; do
            _emit "$target"
          done
        fi
        ;;
      cp|mv)
        # 既定では最後の被演算子が宛先だが、`-t <ディレクトリ>` を付けると
        # 宛先が先に来て、後ろの被演算子はすべて複製元になる。
        local dest="" target_dir="" take_next=0
        for ((j = i + 1; j < n; j++)); do
          if [ "$take_next" = 1 ]; then
            target_dir=${words[j]}
            take_next=0
            continue
          fi
          if _wt_is_separator "${words[j]}"; then break; fi
          case "${words[j]}" in
            -t|--target-directory) take_next=1 ;;
            --target-directory=*) target_dir=${words[j]#--target-directory=} ;;
            # `-t<ディレクトリ>` のように空白を挟まない形もある。
            -t*) target_dir=${words[j]#-t} ;;
            -*) continue ;;
            *) dest=${words[j]} ;;
          esac
        done
        [ -n "$target_dir" ] && dest=$target_dir
        _emit "$dest"
        ;;
    esac
  done

  unset -f _emit _push_group _pop_group _push_subshell _pop_subshell _or_group_exits _or_exit_redirs
  [ "$found" = 1 ] || return 1
}

# --- パスの正規化 -----------------------------------------------------------

# tool から渡されたパスを絶対パスへ直す。まだ存在しないパスでも、実在する
# 最も近い上位ディレクトリまでを実体解決してから残りを継ぎ足す。
# `.` と `..` を字面で畳む。実体解決は上位ディレクトリの存在を要するため、
# 存在しないパスでは `..` が残ってしまう。残ると、前方一致での「配下か」の
# 判定をすり抜ける（`<対象>/a/../../外` が `<対象>/` で始まって見える）。
_wt_lexical_normalize() {
  local path="$1" part out="" glob_was_off=0
  # 分割のための展開でパス名展開が走らないようにする。`*` や `?` を含む
  # パスが、実在するファイルの名前へ化けてしまう。
  case "$-" in *f*) glob_was_off=1 ;; esac
  set -f
  local IFS=/
  # shellcheck disable=SC2086
  set -- $path
  [ "$glob_was_off" = 1 ] || set +f
  for part in "$@"; do
    case "$part" in
      ""|.) continue ;;
      ..) out=${out%/*} ;;
      *) out="$out/$part" ;;
    esac
  done
  printf '%s\n' "${out:-/}"
}

wt_normalize_path() {
  local path="${1:-}" cwd="${2:-$PWD}" suffix="" dir abs
  [ -n "$path" ] || return 1
  case "$path" in /*) ;; *) path="$cwd/$path" ;; esac
  path=$(_wt_lexical_normalize "$path")
  dir="$path"
  while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if abs=$(_wt_abs "$dir"); then
      if [ -n "$suffix" ]; then
        printf '%s/%s\n' "$abs" "$suffix"
      else
        printf '%s\n' "$abs"
      fi
      return 0
    fi
    if [ -n "$suffix" ]; then
      suffix="$(basename "$dir")/$suffix"
    else
      suffix="$(basename "$dir")"
    fi
    dir=$(dirname "$dir")
  done
  printf '%s\n' "$path"
}

# --- 作業ツリーの一覧と追従先の判定 -----------------------------------------

# 開発用の作業ツリーを `<パス><タブ><ブランチ名>` の形で 1 行 1 件で出力する。
# 対象は主ディレクトリ直下の .worktrees/ 配下に限る。レビュー用の作業ツリーは
# 非永続領域に置かれるため、この一覧には入らない。
wt_dev_worktrees() {
  local main_dir="${1:-}" prefix path branch
  [ -n "$main_dir" ] || return 1
  prefix="$main_dir/$WT_WORKTREE_DIR/"
  path=""
  branch=""
  while IFS= read -r line; do
    case "$line" in
      "worktree "*)
        path=${line#worktree }
        branch=""
        ;;
      "branch "*)
        branch=${line#branch }
        branch=${branch#refs/heads/}
        ;;
      "")
        case "$path" in
          "$prefix"*) printf '%s\t%s\n' "$path" "$branch" ;;
        esac
        path=""
        branch=""
        ;;
    esac
  done < <(git -C "$main_dir" worktree list --porcelain 2>/dev/null)
  # 最後の項目は空行で終わらないことがある。
  case "$path" in
    "$prefix"*) printf '%s\t%s\n' "$path" "$branch" ;;
  esac
}

# 主ディレクトリの追従先を決める。git は呼ばず、引数だけで判定する。
# 使い方: wt_follow_target "<一覧>" "<未コミット変更があれば 1>"
# 出力: `detach <ブランチ名>` / `default` / `skip`
wt_follow_target() {
  local listing="${1:-}" dirty="${2:-0}" line branch count=0 single=""
  if [ "$dirty" = "1" ]; then
    printf 'skip\n'
    return 0
  fi
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    branch=${line#*$'\t'}
    # ブランチを持たない作業ツリー (detached) は追従先にしない。
    [ -n "$branch" ] || continue
    count=$((count + 1))
    single=$branch
  done <<<"$listing"

  if [ "$count" = 1 ]; then
    printf 'detach %s\n' "$single"
  else
    printf 'default\n'
  fi
}

# 主ディレクトリの既定ブランチ名を出力する。origin の HEAD が指す先を優先し、
# 取れなければ main / master の順で存在するものを返す。
wt_default_branch() {
  local main_dir="${1:-}" ref candidate
  [ -n "$main_dir" ] || return 1
  ref=$(git -C "$main_dir" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)
  if [ -n "$ref" ]; then
    printf '%s\n' "${ref#origin/}"
    return 0
  fi
  for candidate in main master; do
    if git -C "$main_dir" show-ref --verify --quiet "refs/heads/$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# 主ディレクトリの追跡対象の未コミット変更を `<状態> <パス>` で 1 行 1 件出力する。
# 追跡されていないファイルは含めない。
wt_dirty_paths() {
  local main_dir="${1:-}"
  [ -n "$main_dir" ] || return 1
  git -C "$main_dir" status --porcelain --untracked-files=no 2>/dev/null
}

# --- パッチ本文からの書き込み先の推定 ---------------------------------------

# `apply_patch` の本文から書き込み先を 1 行 1 件で出力する。
# Codex CLI はファイルの編集をこの形で渡し、パスは tool_input.command の中の
# `*** Update File: <パス>` などの行に入る。推定できなければ 1 を返す。
wt_extract_patch_target() {
  local patch="${1:-}" line target found=0
  [ -n "$patch" ] || return 1
  while IFS= read -r line; do
    case "$line" in
      '*** Update File: '*) target=${line#'*** Update File: '} ;;
      '*** Add File: '*) target=${line#'*** Add File: '} ;;
      '*** Delete File: '*) target=${line#'*** Delete File: '} ;;
      '*** Move to: '*) target=${line#'*** Move to: '} ;;
      *) continue ;;
    esac
    # 前後の空白を落とす。
    target=${target#"${target%%[![:space:]]*}"}
    target=${target%"${target##*[![:space:]]}"}
    if [ -n "$target" ]; then
      printf '%s\n' "$target"
      found=1
    fi
  done <<<"$patch"
  [ "$found" = 1 ] || return 1
}

# --- テスト環境の採番と台帳 --------------------------------------------------

# 共通の git ディレクトリの絶対パスを返す。
# `rev-parse --path-format=absolute` は git 2.31 以降にしかない。素の
# `--git-common-dir` は呼び出し元からの相対パスを返すことがあるため、そこで
# 解決する。要求する git の版を上げずに同じ結果を得る。
wt_common_git_dir() {
  local dir="${1:-}" common
  [ -n "$dir" ] || return 1
  common=$(git -C "$dir" rev-parse --git-common-dir 2>/dev/null) || return 1
  _wt_abs_in "$dir" "$common"
}

# 台帳の位置。共通の git ディレクトリ配下へ置く。作業ツリーの中に置くと、その
# 作業ツリーを削除した時点で割り当ての記録が消える (詳細設計 06 の決定 7)。
wt_registry_path() {
  local main_dir="${1:-}" common
  common=$(wt_common_git_dir "$main_dir") || return 1
  printf '%s/ndf/worktree-registry.json\n' "$common"
}

# 割り当てを解放しても行を消さないため、解放済みの行は増え続ける。
# 1 年を超えた解放済みの行は読み取り時に無視する。**削除はしない。**
WT_REGISTRY_KEEP_DAYS=365

# 空きスロットの上限。0 から数えるため 64 個。
WT_SLOT_MAX=63

# 環境名を作る。`<リポジトリ>-wt-<ブランチ>-<要約値 6 桁>` を小文字英数と `-` に
# 揃え、40 文字で切る。同じ作業ツリーには常に同じ値が返る。
# 名前は 40 文字で切る。**要約値は必ず残す。** 単純に末尾を落とすと、先頭が
# 同じ長いブランチ名どうしで同じ名前になり、テスト環境が混ざる。
WT_ENV_NAME_MAX=40

_wt_slug() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9-]/-/g' -e 's/--*/-/g' -e 's/^-//' -e 's/-$//'
}

wt_env_name() {
  local main_dir="${1:-}" branch="${2:-}" repo digest head room name
  [ -n "$main_dir" ] && [ -n "$branch" ] || return 1
  digest=$(printf '%s' "$branch" | (sha1sum 2>/dev/null || shasum 2>/dev/null) | cut -c1-6)
  [ -n "$digest" ] || return 1

  repo=$(_wt_slug "$(basename "$main_dir")")
  branch=$(_wt_slug "$branch")

  # 要約値と区切りに 7 文字を残し、その手前を切る。
  room=$((WT_ENV_NAME_MAX - 7))
  head=$(printf '%s-wt-%s' "$repo" "$branch" | cut -c "1-$room")
  head=${head%-}
  name=$(printf '%s-%s' "$head" "$digest")
  printf '%s\n' "$(_wt_slug "$name")"
}

# ポート番号を返す。`<帯の下限> + スロット*20 + 役割番号`。
# 判定だけを行い、宣言の読み取りは呼び出し側が持つ（テストのため）。
wt_port_for() {
  local band_low="${1:-}" slot="${2:-}" role_number="${3:-}"
  case "$band_low$slot$role_number" in
    *[!0-9]*|"") return 1 ;;
  esac
  printf '%s\n' "$((band_low + slot * 20 + role_number))"
}

# 期間の表記を秒へ直す。`90` / `90s` / `45m` / `2h` / `1d` を受ける。
wt_duration_seconds() {
  local value="${1:-}" number unit
  [ -n "$value" ] || return 1
  number=${value%[smhd]}
  case "$number" in ""|*[!0-9]*) return 1 ;; esac
  unit=${value#"$number"}
  case "$unit" in
    ""|s) printf '%s\n' "$number" ;;
    m) printf '%s\n' "$((number * 60))" ;;
    h) printf '%s\n' "$((number * 3600))" ;;
    d) printf '%s\n' "$((number * 86400))" ;;
    *) return 1 ;;
  esac
}

# 台帳を読む。無ければ空の台帳を返す。
wt_registry_read() {
  local path="${1:-}"
  if [ -s "$path" ] && jq -e . "$path" >/dev/null 2>&1; then
    cat "$path"
    return 0
  fi
  printf '{"version":1,"assignments":[]}\n'
}

# 読み取り時に無視する行を落とした台帳を返す。
# 解放から WT_REGISTRY_KEEP_DAYS を超えた行は数にも一覧にも入れない。
# **ファイルからは消さない。**
wt_registry_visible() {
  local path="${1:-}"
  wt_registry_read "$path" | jq --argjson keep "$WT_REGISTRY_KEEP_DAYS" '
    .assignments |= map(
      select(.released_at == null
             or ((.released_at | fromdateiso8601) > (now - ($keep * 86400))))
    )' 2>/dev/null
}

_wt_registry_write() {
  local path="$1" content="$2" tmp
  mkdir -p "$(dirname "$path")" 2>/dev/null
  tmp=$(mktemp "${path}.XXXXXX" 2>/dev/null) || return 1
  printf '%s\n' "$content" >"$tmp" || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$path" 2>/dev/null || { rm -f "$tmp"; return 1; }
}

# --- 排他 -------------------------------------------------------------------
#
# `flock` を持たないホストがある。`mkdir` は同じ名前で同時に成功するのが 1 つだけなので、
# ディレクトリの作成そのものを排他の手段として使う。

# 待ちの刻み。小数を受けない sleep のホストでは 1 秒へ落ちる。
_wt_lock_sleep() {
  sleep 0.1 2>/dev/null || sleep 1
}

# 持ち主が消えたまま残るロックを捨ててよいと見なすまでの分数。
# `mkdir` に成功した直後、印を書く前に落ちた持ち主を救うために使う。
WT_LOCK_STALE_MINUTES=5

# 判定したものと同じロックであることを確かめてから取り除く。
# 名前の付け替えは 1 つのプロセスだけが成功するため、これを関門に使う。
# 単に `rm -rf` すると、先に捨てて取り直した別のプロセスのロックを壊す。
_wt_lock_discard() {
  local dir="$1" seen="$2" token="$3" stale="$1.stale.$3"
  mv "$dir" "$stale" 2>/dev/null || return 1
  if [ "$(cat "$stale/token" 2>/dev/null)" = "$seen" ]; then
    rm -rf "$stale" 2>/dev/null
    return 0
  fi
  # 別物だった。戻せなければ、取り直した側が既に持っているので捨てる。
  mv "$stale" "$dir" 2>/dev/null || rm -rf "$stale" 2>/dev/null
  return 1
}

# ロックが捨ててよい状態かを見る。
_wt_lock_is_stale() {
  local dir="$1" owner
  owner=$(cat "$dir/pid" 2>/dev/null)
  if [ -n "$owner" ]; then
    kill -0 "$owner" 2>/dev/null && return 1
    return 0
  fi
  # 印が無いロックは、作った直後に落ちた可能性がある。古ければ捨ててよい。
  find "$dir" -maxdepth 0 -mmin "+$WT_LOCK_STALE_MINUTES" 2>/dev/null | grep -q . && return 0
  return 1
}

# ロックを取る。取れなければ 1 を返す。
#
# `flock` は使わない。使えるホストと使えないホストが混じると、同じ資源に対して
# 別々の仕組みが動き、互いを見落とす。どこでも同じ `mkdir` に揃える。
wt_lock_acquire() {
  local dir="${1:-}" timeout="${2:-5}" token seen
  [ -n "$dir" ] || return 1
  # ロックの位置にディレクトリ以外があれば、ロックとして成立しない。取り除く。
  if [ -e "$dir" ] && [ ! -d "$dir" ]; then
    rm -f "$dir" 2>/dev/null
  fi
  token="$$-$(date +%s 2>/dev/null)-${RANDOM:-0}"
  # 上限は実時間で測る。刻みが 0.1 秒か 1 秒かで待ち時間が 10 倍変わるため、
  # 回数で数えない。
  local deadline
  deadline=$(( $(date +%s) + timeout ))
  while ! mkdir "$dir" 2>/dev/null; do
    seen=$(cat "$dir/token" 2>/dev/null)
    if _wt_lock_is_stale "$dir"; then
      _wt_lock_discard "$dir" "$seen" "$token"
      continue
    fi
    [ "$(date +%s)" -ge "$deadline" ] && return 1
    _wt_lock_sleep
  done
  printf '%s\n' "$token" >"$dir/token" 2>/dev/null
  printf '%s\n' "$$" >"$dir/pid" 2>/dev/null
  return 0
}

wt_lock_release() {
  [ -n "${1:-}" ] || return 0
  rm -rf "$1" 2>/dev/null
  return 0
}

# 生きている持ち主がロックを握っていれば 0 を返す。
wt_lock_is_held() {
  local dir="${1:-}" owner
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  owner=$(cat "$dir/pid" 2>/dev/null)
  [ -n "$owner" ] || return 0
  kill -0 "$owner" 2>/dev/null
}

_wt_registry_apply() {
  local content updated
  content=$(wt_registry_read "$_WT_REGISTRY_TARGET")
  updated=$(printf '%s' "$content" | jq "${_WT_REGISTRY_ARGS[@]+"${_WT_REGISTRY_ARGS[@]}"}" \
    "$_WT_REGISTRY_PROGRAM" 2>/dev/null) || return 1
  _wt_registry_write "$_WT_REGISTRY_TARGET" "$updated"
}

# 台帳の更新を排他のもとで行う。**読み込み・変更・書き出しを 1 つの区間にまとめる。**
# 判定も jq のプログラムの中で行うこと。区間の外で読んで中で書くと、同時に走った
# 別のプロセスと同じ番号を割り当ててしまう。
#
# 使い方: wt_registry_update <台帳> <jq プログラム> [jq の引数...]
# 値は jq の引数として渡す。プログラムの文字列へ埋め込むと、引用符を含む
# ブランチ名やパスで壊れる。
wt_registry_update() {
  _WT_REGISTRY_TARGET="${1:-}"
  _WT_REGISTRY_PROGRAM="${2:-}"
  [ -n "$_WT_REGISTRY_TARGET" ] && [ -n "$_WT_REGISTRY_PROGRAM" ] || return 1
  shift 2
  _WT_REGISTRY_ARGS=("$@")
  mkdir -p "$(dirname "$_WT_REGISTRY_TARGET")" 2>/dev/null

  # 排他の手段は 1 つに揃える。`flock` の有無で使うロックが分かれると、
  # 同じ台帳を別の場所から同時に触ったときに互いを見落とす。
  local lock="${_WT_REGISTRY_TARGET}.lockdir" rc
  wt_lock_acquire "$lock" 5 || return 1
  _wt_registry_apply
  rc=$?
  wt_lock_release "$lock"
  return "$rc"
}

# 作業ツリーへ割り当てられているスロットを返す。無ければ 1 を返す。
wt_slot_of() {
  local main_dir="${1:-}" worktree="${2:-}" path slot
  path=$(wt_registry_path "$main_dir") || return 1
  slot=$(wt_registry_visible "$path" \
    | jq -r --arg wt "$worktree" \
      '[.assignments[] | select(.released_at == null and .worktree == $wt)] | last | .slot // empty' 2>/dev/null)
  [ -n "$slot" ] || return 1
  printf '%s\n' "$slot"
}

# 作業ツリーへスロットを割り当てる。既に割り当てがあれば同じ番号を返す。
# 空きが無ければ 1 を返す。
#
# **空きの判定と行の追加を 1 つの jq プログラムで行う。** 排他区間の外で空きを
# 読むと、同時に走った別のプロセスと同じ番号を掴む。
wt_slot_acquire() {
  local main_dir="${1:-}" worktree="${2:-}" branch="${3:-}" environment="${4:-}"
  local path
  path=$(wt_registry_path "$main_dir") || return 1

  wt_registry_update "$path" '
    ([.assignments[] | select(.released_at == null)]) as $active
    | if ($active | map(select(.worktree == $wt)) | length) > 0 then .
      else
        ([$active[] | .slot]) as $used
        | ([range(0; $max + 1)] - $used | first) as $slot
        | if $slot == null then .
          else
            .assignments += [{
              id: $id, worktree: $wt, branch: $branch, environment: $environment,
              slot: $slot, ports: {}, assigned_at: (now | todate),
              last_used_at: (now | todate), released_at: null, expose: null
            }]
          end
      end'     --arg wt "$worktree" --arg branch "$branch" --arg environment "$environment"     --arg id "$(date -u +%Y%m%dT%H%M%SZ)-$$" --argjson max "$WT_SLOT_MAX" || return 1

  wt_slot_of "$main_dir" "$worktree"
}

# 割り当てを解放する。**行は消さず、解放の時刻を書き込む。**
wt_slot_release() {
  local main_dir="${1:-}" worktree="${2:-}" path
  path=$(wt_registry_path "$main_dir") || return 1
  wt_registry_update "$path" '
    .assignments |= map(
      if .worktree == $wt and .released_at == null then .released_at = (now | todate) else . end
    )' --arg wt "$worktree"
}

# 割り当てへポートを記録する。
wt_slot_set_ports() {
  local main_dir="${1:-}" worktree="${2:-}" ports_json="${3:-}" path
  path=$(wt_registry_path "$main_dir") || return 1
  wt_registry_update "$path" '
    .assignments |= map(
      if .worktree == $wt and .released_at == null then .ports = $ports else . end
    )' --arg wt "$worktree" --argjson ports "$ports_json"
}

# 最後に使った時刻を記録する。reap の判定が読む。
wt_slot_touch() {
  local main_dir="${1:-}" worktree="${2:-}" path
  path=$(wt_registry_path "$main_dir") || return 1
  wt_registry_update "$path" '
    .assignments |= map(
      if .worktree == $wt and .released_at == null then .last_used_at = (now | todate) else . end
    )' --arg wt "$worktree"
}
