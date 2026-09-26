#!/usr/bin/env bash
# NDF plugin: シェルコマンドとパッチ本文からの書き込み先の推定のエントリポイントと前処理。
#
# `worktree-common.sh` が source する。呼び出し側はこのファイルを直に source せず、
# `worktree-common.sh` だけを source する。
#
# 語列の走査は `worktree-write-target-scan.sh` と `worktree-write-target-track.sh` が持つ。

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

# ヒアドキュメントの開始記号 `<<` の直後（位置 $2）から、終端の語を読む。
# 結果は呼び出し側が `local` で宣言した変数へ書く。
#   _WT_DELIM     終端の語（空なら開始記号ではない）
#   _WT_STRIP     `<<-` なら 1（本文の行頭のタブを落とす）
#   _WT_EXPAND    本文が展開されるなら 1
#   _WT_DELIM_END 読み終えた位置（区切りの文字、または行末）
_wt_heredoc_parse_delim() {
  local line="${1:-}" i="${2:-0}" n c delim="" quoted=0 dq="" strip=0
  n=${#line}
  if [ "${line:i:1}" = "-" ]; then strip=1; i=$((i + 1)); fi
  while [ "${line:i:1}" = " " ] || [ "${line:i:1}" = $'\t' ]; do i=$((i + 1)); done
  # 終端の語。引用符は書き方の違いで、語そのものには含まれない。
  # 引用符を 1 つでも使えば、本文は展開されない。
  # **引用符の中では区切りで切らない。** `<<"EOF X"` のように空白や記号を
  # 含む語を、途中で切ると終端を見つけられない。
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
  _WT_DELIM="$delim"
  _WT_STRIP="$strip"
  _WT_EXPAND=$((1 - quoted))
  _WT_DELIM_END="$i"
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
  local line candidate out="" n i c
  # 終端の語の解析結果。_wt_heredoc_parse_delim が書き込む。
  local _WT_DELIM="" _WT_STRIP=0 _WT_EXPAND=1 _WT_DELIM_END=0
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
      _wt_heredoc_parse_delim "$line" "$((i + 2))"
      i=$_WT_DELIM_END
      if [ -n "$_WT_DELIM" ]; then
        delims+=("$_WT_DELIM")
        strips+=("$_WT_STRIP")
        expands+=("$_WT_EXPAND")
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

_wt_extract_preprocess_cmd() {
  local cmd="${1:-}"
  # ヒアドキュメントの本文を先に落とす。落とす前に目印を挟むと、本文の中の `>` が
  # 出力の付け替えとして数えられる。
  cmd=$(_wt_strip_heredocs "$cmd")

  # `>path` のように空白の無い形を語へ分けるため、先に目印を挟む。
  #
  # `&>` と `&>>` は、標準出力と標準エラーをまとめて 1 つのファイルへ向ける形
  # である（`&>>` は追記）。`&` は背景実行の演算子ではない。`>` だけを置き換えると
  # `&` が演算子として残り、現在地が処理のグループの入口へ戻る
  # （`cd a && echo hi &> f` の `f` を移動前の位置で解決してしまう）。
  # `>` より先に、まとめて 1 つの目印へ置き換える。
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
  printf '%s\n' "$spaced"
}

# 正規化済みコマンド文字列からトークン列を生成し、1 行 1 トークンで出力する。
# 字句解析を行い、リダイレクトの目印の直前にあるファイル記述子番号（すべて数字の語）
# を並びから外す。
_wt_extract_tokenize() {
  local spaced="${1:-}"
  local -a raw_words=()
  _wt_read_lines < <(_wt_tokenize "$spaced")
  raw_words=("${WT_LINES[@]+"${WT_LINES[@]}"}")

  # リダイレクトの目印の直前にある、すべて数字の語を並びから外す。`2>&1` の `2` は
  # ファイル記述子の番号であって、開かれるファイルの名前ではない。残すと番号を
  # 書き込み先として案内するうえ、`cp` / `mv` は最後の被演算子を宛先とするため、
  # 本来の宛先がその位置を奪われて出なくなる。
  #
  # **落とすのは語へ切り分けた後である。** 目印への置き換えは引用符を見ないため、
  # その前に数字を落とすと引用符の中の字面まで書き換わる（`cp a "x 2>&1"` の
  # 宛先は `x 2>&1` という名前のファイルで、番号は名前の一部である）。
  #
  # すべて数字の語だけを落とす。`cat file2>log` の `file2` は語であって番号では
  # なく、bash も `log` だけを開く（実測）。逆に `cp a 2 >f` の `2` は名前が
  # すべて数字のファイルだが、目印への置き換えが番号と `>` の間の空白を消すため
  # 見分けられない。取り逃がす側へ倒す。
  local -a kept=()
  local wi
  for ((wi = 0; wi < ${#raw_words[@]}; wi++)); do
    case "${raw_words[wi]}" in
      *[!0-9]*|"") kept+=("${raw_words[wi]}"); continue ;;
    esac
    case "${raw_words[wi + 1]:-}" in
      __WT_REDIR__|__WT_APPEND__) continue ;;
    esac
    kept+=("${raw_words[wi]}")
  done
  ((${#kept[@]} > 0)) && printf '%s\n' "${kept[@]}"
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

  # 段 1: コマンド文字列の前処理（ヒアドキュメント除去とリダイレクト記号化）
  local spaced
  spaced=$(_wt_extract_preprocess_cmd "$cmd")

  # 段 2: 字句化とファイル記述子番号の除去
  local -a words=()
  _wt_read_lines < <(_wt_extract_tokenize "$spaced")
  words=("${WT_LINES[@]+"${WT_LINES[@]}"}")

  # 段 3・段 4: 語列を 1 度読み通し、現在地を追いながら書き込み先を出す。
  _wt_extract_scan "$base" ${words[@]+"${words[@]}"}
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
