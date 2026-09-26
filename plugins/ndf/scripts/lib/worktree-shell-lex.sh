#!/usr/bin/env bash
# NDF plugin: 書き込み先の推定に使うシェルコマンドの字句解析。
#
# `worktree-common.sh` が source する。呼び出し側はこのファイルを直に source せず、
# `worktree-common.sh` だけを source する。

# --- シェルコマンドからの書き込み先の推定 -----------------------------------

# case の現在の段が見出しを待っていれば 0 を返す。
_wt_tok_in_pattern() {
  local case_depth="${1:-0}" case_state="${2:-}"
  [ "$case_depth" -gt 0 ] || return 1
  [ "$case_state" = want_pattern ]
}

# 語を出力したときの case の深さと状態スタックを返す。
# 状態は空白を含まないためカンマ区切りで受け渡す。結果は
# _WT_TOK_CASE_DEPTH / _WT_TOK_CASE_STATES に入る。
_wt_tok_emit() {
  local word="${1:-}" prev="${2:-}" case_depth="${3:-0}" case_states="${4:-}"
  local case_state="${case_states##*,}"
  _WT_TOK_CASE_DEPTH=$case_depth
  _WT_TOK_CASE_STATES=$case_states
  case "$word" in
    case|esac)
      case "$prev" in
        ""|__WT_SEP__|__WT_CASE_FALL__|__WT_SUBSHELL_END__|__WT_CASE_END__|"|"|"|&"|"&"\
        |"&&"|"||"|"("|"{"|if|elif|then|else|while|until|do|"!"|time)
          if [ "$word" = case ]; then
            if [ -n "$case_states" ]; then
              _WT_TOK_CASE_STATES="$case_states,want_in"
            else
              _WT_TOK_CASE_STATES=want_in
            fi
            _WT_TOK_CASE_DEPTH=$((case_depth + 1))
          elif [ "$case_depth" -gt 0 ]; then
            if [ "$case_depth" -gt 1 ]; then
              _WT_TOK_CASE_STATES=${case_states%,*}
            else
              _WT_TOK_CASE_STATES=""
            fi
            _WT_TOK_CASE_DEPTH=$((case_depth - 1))
          fi
          ;;
      esac
      ;;
    in)
      if [ "$case_depth" -gt 0 ] && [ "$case_state" = want_in ]; then
        if [ "$case_depth" -gt 1 ]; then
          _WT_TOK_CASE_STATES="${case_states%,*},want_pattern"
        else
          _WT_TOK_CASE_STATES=want_pattern
        fi
      fi
      ;;
  esac
}

# エスケープを消費した結果を _WT_TOK_TEXT と _WT_TOK_ADVANCE に返す。
# シングルクォート内など、対象でなければ 1 を返す。
_wt_tok_consume_escape() {
  local s="${1:-}" i="${2:-0}" quote="${3:-}" c esc
  c=${s:i:1}
  [ "$c" = '\' ] && [ "$quote" != "'" ] || return 1
  esc=${s:i+1:1}
  _WT_TOK_TEXT=""
  _WT_TOK_ADVANCE=0
  if [ -z "$esc" ]; then _WT_TOK_TEXT=$c; return 0; fi
  if [ "$esc" = $'\n' ]; then _WT_TOK_ADVANCE=1; return 0; fi
  if [ -n "$quote" ]; then
    case "$esc" in
      '$'|'`'|'"'|'\') _WT_TOK_TEXT=$esc ;;
      *) _WT_TOK_TEXT="$c$esc" ;;
    esac
  else
    _WT_TOK_TEXT=$esc
  fi
  _WT_TOK_ADVANCE=1
}

# 区切りの種類、読み飛ばす文字数、case の次の状態を返す。
_wt_tok_separator() {
  local c="${1:-}" following="${2:-}" case_depth="${3:-0}"
  _WT_TOK_TOKEN=__WT_SEP__
  _WT_TOK_ADVANCE=0
  _WT_TOK_CASE_STATE=""
  if [ "$c" = ";" ] && [ "$case_depth" -gt 0 ]; then
    case "$following" in
      ";&") _WT_TOK_TOKEN=__WT_CASE_FALL__; _WT_TOK_ADVANCE=2; _WT_TOK_CASE_STATE=want_pattern ;;
      "&"*) _WT_TOK_TOKEN=__WT_CASE_FALL__; _WT_TOK_ADVANCE=1; _WT_TOK_CASE_STATE=want_pattern ;;
      ";"*) _WT_TOK_CASE_STATE=want_pattern ;;
    esac
  fi
}

# `|` / `&` を語への追記か演算子に分類し、結果を返す。
_wt_tok_operator() {
  local c="${1:-}" pair="${2:-}" cur="${3:-}" last="${4:-}"
  _WT_TOK_TEXT=""
  _WT_TOK_TOKEN=""
  _WT_TOK_ADVANCE=0
  if [ "$c" = "&" ] && { [ "${cur: -1}" = "<" ] ||
    { [ -z "$cur" ] &&
      { [ "$last" = "__WT_REDIR__" ] || [ "$last" = "__WT_APPEND__" ]; }; }; }; then
    _WT_TOK_TEXT=$c
    return 0
  fi
  _WT_TOK_TOKEN=$c
  case "$pair" in
    "&&"|"||"|"|&") _WT_TOK_TOKEN=$pair ;;
  esac
  _WT_TOK_ADVANCE=$((${#_WT_TOK_TOKEN} - 1))
}

# 開き丸括弧を語中括弧、case 見出し、関数定義、部分シェルへ分類する。
_wt_tok_open_parenthesis() {
  local cur="${1:-}" inword="${2:-0}" in_pattern="${3:-0}" rest="${4:-}"
  if [ -n "$cur" ] || [ "$inword" -gt 0 ]; then _WT_TOK_KIND=inword
  elif [ "$in_pattern" -eq 1 ]; then _WT_TOK_KIND=pattern
  elif [ "${rest:0:1}" = ")" ]; then _WT_TOK_KIND=inword
  else _WT_TOK_KIND=subshell
  fi
}

# 閉じ丸括弧を語中括弧、case 見出し、部分シェルへ分類する。
_wt_tok_close_parenthesis() {
  local inword="${1:-0}" in_pattern="${2:-0}" subshells="${3:-0}"
  if [ "$inword" -gt 0 ]; then _WT_TOK_KIND=inword
  elif [ "$in_pattern" -eq 1 ]; then _WT_TOK_KIND=pattern
  elif [ "$subshells" -gt 0 ]; then _WT_TOK_KIND=subshell
  else _WT_TOK_KIND=word
  fi
}

# シェルの語分割を、引用符を解釈しながら行う。1 行 1 語で出力する。
# `sed -i 's/a b/c/' f` のように引用符の中へ空白を含む形を 1 語として扱うため、
# 単純な空白区切りでは足りない。
_wt_tokenize() {
  local s="${1:-}" n i c quote="" cur="" last rest in_pattern prev_out
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
  local case_depth=0 case_states=""
  # 語の頭の `~` が展開されるのは、`~` から最初の引用されていない `/` までに
  # 引用もエスケープも無いときだけである（`""~/x` と `~"/x"` は字面の `~/x`、
  # `~/"x"` は展開される）。語を出すと引用の有無が消えるため、その範囲で引用か
  # エスケープを見たか（`tilde_quoted`）と、引用されていない `/` を見たか
  # （`bare_slash`）を語ごとに持ち、確定するときに `./` を前へ足して現在地から
  # の相対パスとして残す。
  local tilde_quoted=0 bare_slash=0
  # 見出しを閉じる語の直前で `cur` を 1 語として確定する。`_wt_tok_emit` は
  # 直前の語（`prev_out`）を見て case の深さと状態を更新するため、確定と同時に
  # その同期も行う。`out` / `cur` / `case_depth` / `case_states` は動的スコープで
  # 共有する（末尾で unset -f する）。`cur` が空なら何もしない。
  _wt_tok_flush_word() {
    _wt_tok_mark_literal_tilde
    [ -n "$cur" ] || return 0
    prev_out=""; ((${#out[@]} > 0)) && prev_out=${out[${#out[@]} - 1]}
    _wt_tok_emit "$cur" "$prev_out" "$case_depth" "$case_states"
    case_depth=$_WT_TOK_CASE_DEPTH; case_states=$_WT_TOK_CASE_STATES
    out+=("$cur"); cur=""
  }
  # 展開されない語頭の `~` に `./` を足し、語ごとの記録を戻す。
  _wt_tok_mark_literal_tilde() {
    [ "$tilde_quoted" -eq 1 ] && [ "${cur:0:1}" = "~" ] && cur="./$cur"
    tilde_quoted=0 bare_slash=0
  }
  for ((i = 0; i < n; i++)); do
    c=${s:i:1}
    # `\` は次の 1 文字をエスケープする。**シングルクォートの中を除く。** 中では
    # `\` も字面で、閉じる `'` を隠さない (`'a\'` は `a\`)。実測で確かめた。
    #
    # 見なければ `"` の中の `\"` を閉じ引用符と読み、残りをまるごと 1 語へ吸い
    # 込む（検知漏れ）。引用符の外では `\ ` を区切り、`\)` を部分シェルの終わり
    # と読む（語の取り違えと誤検知）。
    if _wt_tok_consume_escape "$s" "$i" "$quote"; then
      [ -n "$_WT_TOK_TEXT" ] && [ "$bare_slash" -eq 0 ] && tilde_quoted=1
      cur+="$_WT_TOK_TEXT"
      i=$((i + _WT_TOK_ADVANCE))
      continue
    fi
    if [ -n "$quote" ]; then
      if [ "$c" = "$quote" ]; then quote=""; else cur+="$c"; fi
      continue
    fi
    case "$c" in
      "'"|'"') quote="$c"; [ "$bare_slash" -eq 0 ] && tilde_quoted=1 ;;
      # 改行と `;` はコマンドの区切りである。空白として捨てると、次の行の語を
      # 前のコマンドの対象と取り違える（`cp a b` の次の行の `echo c` の `c` を
      # 複製先として拾うなど）。区切りの目印を独立した語として出す。
      $'\n'|";")
        # `;;` `;&` `;;&` は `case` の枝の終わりで、次に来るのは見出しである。
        #
        # **`;&` と `;;&` は 1 つの目印にする。** どちらも次の枝の本体を前の枝の
        # 出口から始めるが、`__WT_SEP__` と `&` の 2 語へ割ると、後者が背景実行の
        # 演算子として読まれて現在地がグループの入口へ戻る。走査の側でフォール
        # スルーと背景実行を見分けられるよう、専用の目印を出す。
        _wt_tok_flush_word
        _wt_tok_separator "$c" "${s:i+1:2}" "$case_depth"
        if [ -n "$_WT_TOK_CASE_STATE" ]; then
          if [ "$case_depth" -gt 1 ]; then case_states="${case_states%,*},$_WT_TOK_CASE_STATE"
          else case_states=$_WT_TOK_CASE_STATE
          fi
        fi
        out+=("$_WT_TOK_TOKEN")
        i=$((i + _WT_TOK_ADVANCE))
        ;;
      " "|$'\t')
        # `>& file` の `&` は、標準出力と標準エラーをまとめて 1 つのファイルへ
        # 向ける形の一部で、後ろの語がそのファイルになる。ここで区切ると `&` が
        # 単独の語になり、背景実行の演算子として読まれる（現在地がグループの
        # 入口へ戻る）。次の語まで繋げて 1 語にする。
        if [ "$cur" = "&" ]; then
          last=""
          ((${#out[@]} > 0)) && last=${out[${#out[@]} - 1]}
          case "$last" in
            __WT_REDIR__|__WT_APPEND__) continue ;;
          esac
        fi
        _wt_tok_flush_word
        ;;
      # 演算子は空白で囲まれているとは限らない。切り出さないと
      # `cp a b||echo c` の `b||echo` が 1 語になり、区切りとして見えない
      # （次のコマンドの `c` を複製先として拾う）。`>` と `>>` は呼び出し側が
      # 目印へ置き換えるため、ここには現れない。
      "|"|"&")
        # `>&2` `2>&1` `3<&0` の `&` はファイル記述子の複製であって、背景実行の
        # 演算子ではない。切ると後続が別のコマンドに見え、`cd` の効果を落とす。
        # 直前が `<` か、`>` の置き換えの目印のときは字面のまま繋げる。
        # 長い演算子を先に見る。`&&` を `&` 2 つに割ると、同じシェルで続く並びが
        # 背景実行 2 つになって意味が変わる。
        last=""
        ((${#out[@]} > 0)) && last=${out[${#out[@]} - 1]}
        _wt_tok_operator "$c" "${s:i:2}" "$cur" "$last"
        if [ -n "$_WT_TOK_TEXT" ]; then
          cur+="$_WT_TOK_TEXT"
        else
          _wt_tok_flush_word
          out+=("$_WT_TOK_TOKEN")
          i=$((i + _WT_TOK_ADVANCE))
        fi
        ;;
      # `(` は部分シェルを開く。語の頭にあるときだけ入口として切り出す。
      # 途中に現れる `(` は展開・関数定義・配列の代入の一部で、部分シェルでは
      # ない。字面のまま語へ残し、対応する `)` も切り出さないよう数える。
      "(")
        # 見出しの位置の `(` は飾りで、部分シェルの入口ではない
        # （`case $x in (a) ...`）。語の一部として残す。
        # 中身の無い `()` は関数定義の目印で、部分シェルの入口ではない。`f ()`
        # のように空白を挟む書き方があるため、語の途中かどうかでは見分けられない。
        in_pattern=0
        _wt_tok_in_pattern "$case_depth" "${case_states##*,}" && in_pattern=1
        rest=${s:i+1}
        rest=${rest#"${rest%%[!$' \t']*}"}
        _wt_tok_open_parenthesis "$cur" "$inword" "$in_pattern" "$rest"
        case "$_WT_TOK_KIND" in
          inword) inword=$((inword + 1)); cur+="$c" ;;
          pattern) cur+="$c" ;;
          subshell) out+=("$c"); subshells=$((subshells + 1)) ;;
        esac
        ;;
      # `)` は、切り出した `(` が残っているときだけ部分シェルの終わりである。
      # `case` の見出し (`a)`) のように対応する `(` が無いものは語の一部で、
      # 切り出すと存在しない位置を書き込み先として示すことになる。
      ")")
        # 見出しを閉じる `)`。枝の本体が始まることを目印で伝える。見出しの語と
        # くっついているか (`a)`) 離れているか (`a )`) で扱いを変えない。
        in_pattern=0
        _wt_tok_in_pattern "$case_depth" "${case_states##*,}" && in_pattern=1
        _wt_tok_close_parenthesis "$inword" "$in_pattern" "$subshells"
        case "$_WT_TOK_KIND" in
          inword) inword=$((inword - 1)); cur+="$c" ;;
          pattern)
            _wt_tok_flush_word
            if [ "$case_depth" -gt 1 ]; then case_states="${case_states%,*},body"
            else case_states=body
            fi
            out+=("__WT_CASE_END__")
            ;;
          subshell)
            _wt_tok_flush_word
            out+=("__WT_SUBSHELL_END__")
            subshells=$((subshells - 1))
            ;;
          word) cur+="$c" ;;
        esac
        ;;
      /) cur+="$c"; bare_slash=1 ;;
      *) cur+="$c" ;;
    esac
  done
  _wt_tok_mark_literal_tilde
  if [ -n "$cur" ]; then
    prev_out=""; ((${#out[@]} > 0)) && prev_out=${out[${#out[@]} - 1]}
    _wt_tok_emit "$cur" "$prev_out" "$case_depth" "$case_states"
    out+=("$cur")
  fi
  unset -f _wt_tok_flush_word _wt_tok_mark_literal_tilde
  printf '%s\n' "${out[@]+"${out[@]}"}"
}
