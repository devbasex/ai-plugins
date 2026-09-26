#!/usr/bin/env bash
# 局所変数は `_wt_extract_scan` が宣言し、2 本の関数が動的スコープで読み書きする。
# shellcheck disable=SC2034,SC2154
# NDF plugin: 書き込み先の推定の走査の本体と、書き込み先を出す関数。
#
# `worktree-common.sh` が source する。呼び出し側はこのファイルを直に source せず、
# `worktree-common.sh` だけを source する。
#
# 現在地と入れ子の追跡は `worktree-write-target-track.sh` が持つ。
#
# ここの関数は `_wt_extract_scan` の局所変数（`words` `n` `i` `w` `cwd` ほか）を動的スコープで
# 読み書きする。`_wt_extract_scan` の中からだけ呼ぶ。

# 語列を 1 度だけ読み通す走査。第 1 引数は相対パスの起点、残りが語である。
# 書き込み先を 1 件でも出せば 0、出せなければ 1 を返す。
#
# 語ごとの処理は 3 つに分かれる。
#
# | 段 | 関数 | 何を決めるか |
# | --- | --- | --- |
# | 命令の位置 | `_wt_scan_command_position` | その語が命令名か、関数定義の途中か |
# | 段 3: 追跡 | `_wt_scan_track_word` | 現在地と複合構文の入れ子。当たれば次の語へ進む |
# | 段 4: 抽出 | `_wt_scan_emit_word` | 書き込み先の語を出す |
#
# 状態は走査の全体で共有するため、各段はこの関数の局所変数を直接読み書きする。
_wt_extract_scan() {
  local base="$1"
  shift
  local -a words=()
  words=("$@")

  # `cd` を追った現在地と、それが確かかどうか。起点を渡されない限り使わない。
  local cwd="$base" cwd_known=1
  # `cd` の効果が及ぶ範囲は、それが動くシェルの中に限られる。パイプの各区画と
  # 背景実行は部分シェルで動くため、後続のコマンドは移動前の位置のままになる。
  # 引き継いでしまうと、主ディレクトリへの書き込みを作業ツリー側と取り違えて
  # 案内を出さない。区画の入口の位置を 2 段で控え、`|` / `|&` ではパイプの入口へ、
  # `&` では処理のグループの入口へ戻す。
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
  # `{` で開くグループは必ず走るため、この積み上げの対象にしない。
  local -a block_cds=()
  local block_depth=0 cds=0
  # `&` の復元先は、背景実行にまとめられるひとつのグループの入口である。グループの
  # 境目は `;` / 改行 / `&` だが、複合コマンド (`{ }` / `( )` / `if` / `while` …)
  # の**中**の `;` は、外側のグループを切らない。切ると `{ cd x; } & …` の `&` の
  # 復元先が `x` になり、後続の相対パスを作業ツリー側と取り違えて案内を出さない
  # （検知漏れになる）。入口を入れ子の段ごとに積み、閉じるときに戻す。
  local -a group_cwd=() group_known=()
  local group_depth=0
  # `case` の枝どうしは排他で、見出し (`a)`) の間に走る命令は無い。枝の入口の
  # 位置は `case` を開いた時点の位置そのものである。次の見出しで戻さないと、
  # 前の枝の `cd` を引きずり、走っていない移動を書き込み先の起点に使う。
  # 見出しを命令の位置として数えるかどうかの判定にも使うため、深さを持つ。
  # ただし `;&` `;;&` で落ちた枝は例外で、前の枝の出口から始まる。落ちてきたか
  # どうかを段ごとに控え、次の見出しで入口へ戻すかどうかを決める。
  local -a case_cwd=() case_known=() case_fall=()
  local case_depth=0
  # 関数定義の本体は、定義した時点では走らない。中の `cd` を数えると、後続の
  # 相対パスの起点が動く。本体は部分シェルと同じ隔離で扱い、中の書き込み先は
  # これまでどおり出したまま、移動だけを外へ漏らさない。
  #
  # `func_stage` は定義の見分けの途中経過を持つ。`name` は `function` の次の語を
  # 待っている状態、`paren` は命令の位置の語の次が `()` かを待っている状態、
  # `body` は本体を開く `{` か `(` を待っている状態である。
  local func_stage="" func_name=""
  # 本体を開いた複合コマンドの深さ・入口の `cd` の回数・定義した名前と、その本体を
  # `{` で開いたか。閉じる語がどれかを深さで見分け、`{` の本体だけ出口で
  # `_wt_scan_pop_subshell` を呼ぶ（`(` の本体は既存の部分シェルの経路が戻す）。
  local -a func_group=() func_cds=() func_names=() func_brace=()
  local func_depth=0
  # 本体で移動した関数の名前。呼び出しの時点の現在地は字面から決められない
  # ため、命令の位置にこの名前が現れたら相対パスを出さない（決定 3）。
  local func_moving="|"

  local n=${#words[@]} i j w tw target found=0 prev="" at_cmd=0 dest="" k cmd_prefix=0 cd_end_of_options=0
  # `command` / `builtin` の被演算子を命令の位置として数えている間だけ 1。
  local cmd_wrapper=0 or_next=""
  # `||` の右辺のブレースグループが必ず後続へ進まないと判ったときに積む。グループ
  # を閉じる `}` の添字と、そこで戻す位置を持つ。入れ子は内側が先に閉じるため、
  # 積んだ順にそのまま取り出せる。
  local -a or_end=() or_cwd=() or_known=()
  local or_depth=0
  # `_wt_scan_or_group_exits` が返す、グループを閉じる `}` の添字。
  local _WT_GROUP_END=0
  # 移動前の位置で解決し終えたリダイレクトの、走査が届いた語の位置。`cd` の枝と
  # `||` の右辺の枝が書き込む。ここより手前の `__WT_REDIR__` / `__WT_APPEND__` を
  # 二度拾わない。
  local resolved_redir_end=0
  # 命令の位置で読んだリダイレクトが、被演算子を読み終えた次の語の位置。シェルは
  # リダイレクトを開いてから命令を実行するため、その次の語が命令名である。控え
  # ないと `>/dev/null cd ../..` の `cd` を被演算子として読み飛ばし、移動が
  # 起点へ反映されない。変数代入と `command` / `builtin` が命令の位置を延ばすのと
  # 同じ形で、判定の場所は 1 か所のままにする。
  local cmd_after_redir=-1
  # `_wt_scan_redir_target` の結果。書き込み先の語と、読み進めた最後の語の位置。
  local _WT_REDIR_DEST="" _WT_REDIR_END=0
  # `_wt_scan_redir_span` の結果。語の中の `<` より前にあった被演算子（無ければ空）。
  local _WT_REDIR_HEAD=""

  # 入口は各段を語ごとに順へ接続するだけにする。
  for ((i = 0; i < n; i++)); do
    w=${words[i]}
    _wt_scan_command_position
    if _wt_scan_track_word; then continue; fi
    _wt_scan_emit_word
  done

  [ "$found" = 1 ] || return 1
}

# 目印 (`__WT_REDIR__` / `__WT_APPEND__`) の後ろの語から、実際に開かれる
# ファイルを決める。`>&` には用法が 2 つある。
#
# - `2>&1` `>&2` `>&-` はファイル記述子の複製と閉鎖で、ファイルは開かない
# - `>& file` `>&file` は `&>` と同義で、後ろの語がファイルになる
#
# 目印の後ろが `&` に続く数字か `-` だけなら前者、それ以外なら後者である。
# 記述子を指定した `2>&file` は bash が ambiguous redirect として拒む（実測）
# ため実際には開かれないが、目印へ置き換えた後は `echo 2 >& file` と字面が
# 同じになり区別できない。案内が多めに出る側へ倒し、書き込み先として扱う。
_wt_scan_redir_target() {
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
# 被演算子を走査する枝（`sed` / `cp`・`mv` / `tee`）が、添字の語をリダイレクトとして
# 読み飛ばすかを決める。当たれば 0 を返し、リダイレクトの被演算子を読み終えた最後の
# 語の位置を `_WT_REDIR_END` に置く。書き込み先は出さない。出力側の目印の書き込み先は
# 主の走査の目印の枝が出すため、ここで出すと二度出る。
#
# bash はリダイレクトを引数の並びから取り除いてから命令を実行する。読み飛ばさないと、
# 出力側の目印を区切りと読んで後ろの被演算子を落とす（`sed -i s/a/b/ x.md >log y.md`
# の `y.md`）。
#
# 入力側（`<` `<<<` `<<` `<<-` `<&` `<>`）は目印へ置き換わらず語のまま残るため、語の中の
# 最初の `<` で見分ける。**語の頭ではなく中を見る**のは、字句解析が `b<in` を 1 語の
# まま渡すのに対し、bash は `b` と `in` に分けるためである。`<` より前を
# `_WT_REDIR_HEAD` に置き、枝はそれを今の語として扱う。前が数字だけなら記述子の番号
# （`2<in`）で、被演算子ではない。プロセス置換 `<(` は `/dev/fd/N` へ展開される
# 被演算子で、bash も並びに残すため読み飛ばさない。
_wt_scan_redir_span() {
  local w2=${words[$1]} head rest
  _WT_REDIR_HEAD=""
  case "$w2" in
    __WT_REDIR__|__WT_APPEND__) _wt_scan_redir_target "$1"; return 0 ;;
    *"<"*) ;;
    *) return 1 ;;
  esac
  head=${w2%%<*}
  rest=${w2#"$head"}
  case "$rest" in
    "<("*) return 1 ;;
    "<<<"*) rest=${rest#<<<} ;;
    "<<-"*) rest=${rest#<<-} ;;
    "<<"*) rest=${rest#<<} ;;
    "<&"*) rest=${rest#<&} ;;
    *) rest=${rest#<} ;;
  esac
  case "$head" in
    *[!0-9]*) _WT_REDIR_HEAD=$head ;;
  esac
  if [ -n "$rest" ]; then
    # `<in` `<<<word` のように被演算子が同じ語にある。
    _WT_REDIR_END=$1
  else
    # 被演算子は次の語にある。`<>rw` は目印への置き換えで `<` と目印と `rw` に
    # 分かれるため、目印なら `_wt_scan_redir_target` にその先を読ませる。
    case "${words[$1 + 1]:-}" in
      __WT_REDIR__|__WT_APPEND__) _wt_scan_redir_target "$(($1 + 1))" ;;
      *) _WT_REDIR_END=$(($1 + 1)) ;;
    esac
  fi
  return 0
}
_wt_take_redirect_operand() {
  local index_name=$1 word_name=$2 operand_index
  eval "operand_index=\${$index_name}"
  _wt_scan_redir_span "$operand_index" || return 0
  printf -v "$index_name" '%s' "$_WT_REDIR_END"
  [ -n "$_WT_REDIR_HEAD" ] || return 1
  printf -v "$word_name" '%s' "$_WT_REDIR_HEAD"
}
_wt_scan_emit() {
  # 目印の置換は引用符を見ずに行うため、引用符の中の `>` まで目印へ変わる。
  # `cp a "b>c"` の `b>c` は書き込み先そのもので、`>` は字面である。
  # **目印のまま出すと、案内の文字列へ内部の目印が漏れる。** 字面へ戻す。
  #
  # 引用符の外の `>` は語として切り出されるため、ここへは届かない。届くのは
  # 引用符の中に収まったものだけである。置換は目印の両側へ空白を足すため、
  # 空白ごと戻す。元からあった空白は残る（`"b > c"` は `b > c` のまま）。
  local _emit_word=$1
  _emit_word=${_emit_word// __WT_APPEND__ />>}
  _emit_word=${_emit_word// __WT_REDIR__ />}
  _emit_word=${_emit_word// __WT_ANDAND__ /&&}
  _emit_word=${_emit_word//__WT_APPEND__/>>}
  _emit_word=${_emit_word//__WT_REDIR__/>}
  _emit_word=${_emit_word//__WT_ANDAND__/&&}
  set -- "$_emit_word"
  _wt_is_not_target "$1" && return
  if [ -z "$base" ]; then
    printf '%s\n' "$1"
    found=1
    return
  fi
  # 先頭の `~` は bash が $HOME へ展開する。起点へ継ぎ足すとリポジトリの外への
  # 書き込みを主ディレクトリの編集として案内する。`~user` は決められないため出さない。
  case "$1" in
    "~") set -- "$HOME" ;;
    "~/"*) set -- "$HOME/${1#"~/"}" ;;
    "~"*) return ;;
  esac
  case "$1" in
    /*) ;;
    # 現在地が定まらない間の相対パスは、どこを指すか決められない。
    *) [ "$cwd_known" = 1 ] || return ;;
  esac
  printf '%s\n' "$(wt_normalize_path "$1" "$cwd")"
  found=1
}
# sed の被演算子から、in-place で書き換えられるファイルをすべて拾う。引数は
# `sed` の語の添字。`-i` / `--in-place` があるときだけ書き込み先として出す。
# `-e` / `-f` が現れなければ、最初の被演算子がスクリプトで残りがファイルである。
_wt_extract_sed_targets() {
  local start=$1 j2 w2 target
  local has_inplace=0 seen_script=0 skip_next=0
  local -a files=()
  for ((j2 = start + 1; j2 < n; j2++)); do
    w2=${words[j2]}
    # リダイレクトはオプションの引数にならない（`sed -e >log s/a/b/` の `-e` は
    # `s/a/b/` を受け取る）ため、`skip_next` より先に読み飛ばす。
    _wt_take_redirect_operand j2 w2 || continue
    if [ "$skip_next" = 1 ]; then skip_next=0; continue; fi
    if _wt_is_separator "$w2"; then break; fi
    case "$w2" in
      --in-place|--in-place=*) has_inplace=1 ;;
      -e|-f|--expression|--file) seen_script=1; skip_next=1 ;;
      --expression=*|--file=*) seen_script=1 ;;
      # `-es/a/b/` のように空白を挟まずスクリプトを続ける形もある。
      # 見落とすと、最初のファイルをスクリプトと取り違える。
      -e*|-f*) seen_script=1 ;;
      --) ;;
      -*)
        if [[ $w2 =~ ^-[a-zA-Z]*i([a-zA-Z]*|\..*)$ ]]; then
          has_inplace=1
        fi
        ;;
      *)
        if [ "$seen_script" = 0 ]; then
          seen_script=1
        else
          files+=("$w2")
        fi
        ;;
    esac
  done
  if [ "$has_inplace" = 1 ]; then
    for target in "${files[@]+"${files[@]}"}"; do
      _wt_scan_emit "$target"
    done
  fi
}
# cp / mv の被演算子から宛先を拾う。引数は `cp` / `mv` の語の添字。既定では
# 最後の被演算子が宛先だが、`-t <ディレクトリ>` を付けると宛先が先に来て、
# 後ろの被演算子はすべて複製元になる。
_wt_extract_cp_mv_target() {
  local start=$1 j2 w2
  local dest="" target_dir="" take_next=0
  for ((j2 = start + 1; j2 < n; j2++)); do
    w2=${words[j2]}
    # `-t >log dir` の `-t` は `dir` を受け取るため、`take_next` より先に読み飛ばす。
    _wt_take_redirect_operand j2 w2 || continue
    if [ "$take_next" = 1 ]; then
      target_dir=$w2
      take_next=0
      continue
    fi
    if _wt_is_separator "$w2"; then break; fi
    case "$w2" in
      -t|--target-directory) take_next=1 ;;
      --target-directory=*) target_dir=${w2#--target-directory=} ;;
      # `-t<ディレクトリ>` のように空白を挟まない形もある。
      -t*) target_dir=${w2#-t} ;;
      -*) continue ;;
      *) dest=$w2 ;;
    esac
  done
  [ -n "$target_dir" ] && dest=$target_dir
  _wt_scan_emit "$dest"
}

# 今の語が命令の位置にあるかを決め、関数定義の見分けを進める。結果は `at_cmd` と
# `cmd_prefix` / `cmd_wrapper` / `func_stage` / `prev` に置く。書き込み先は出さない。
_wt_scan_command_position() {
  # コマンドの位置にある語だけを命令として扱う。`echo cd > f` の `cd` を
  # 移動として数えると、書き込み先の起点がずれる。
  at_cmd=0
  if [ "$i" = 0 ]; then
    at_cmd=1
  else
    case "$prev" in
      __WT_SEP__|__WT_CASE_FALL__|"&&"|"||"|"|"|"|&"|"&") at_cmd=1 ;;
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
      # として渡す。ここでは目印だけを見る。
      __WT_CASE_END__)
        if [ "$case_depth" -gt 0 ]; then
          at_cmd=1
          # 枝の入口は `case` を開いた位置である。前の枝の `cd` は走っていない。
          # **`;&` `;;&` で落ちてきたときだけは、前の枝の出口が入口になる。**
          # 戻すと、前の枝で作業ツリーへ移った後の書き込みを主ディレクトリ側の
          # ものとして案内する（誤検知になる）。
          if [ "${case_fall[case_depth - 1]}" = 1 ]; then
            case_fall[case_depth - 1]=0
          else
            cwd="${case_cwd[case_depth - 1]}"
            cwd_known="${case_known[case_depth - 1]}"
          fi
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
  # 命令名より前に置いたリダイレクトを読み終えた次の語も、命令の位置である。
  if [ "$at_cmd" = 0 ] && [ "$i" = "$cmd_after_redir" ]; then at_cmd=1; fi
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
  # 関数定義を見分ける（決定 2）。字句解析は `f()` を 1 語に、`f ()` を `f` と
  # `()` の 2 語にまとめるため、直前の語だけで足りる。
  case "$func_stage" in
    # `function` の次の語が名前である。`function f()` は字句解析が 1 語に
    # まとめるため、末尾の `()` を落として名前だけを控える。付けたままだと
    # 呼び出しの語 (`f`) と照合できず、決定 3 の隔離が働かない。
    name) func_name=${w%"()"}; func_stage=body ;;
    # 命令の位置の語の次が `()` なら定義である。そうでなければ普通の命令だった。
    paren) if [ "$w" = "()" ]; then func_stage=body; else func_stage=""; fi ;;
    # 本体を開く語を待つ。改行を挟む書き方 (`f ()` の次の行に `{`) がある。
    # `function f () { ...; }` の `()` は名前と本体の間に挟まるため読み飛ばす。
    body) case "$w" in "{"|"("|"()"|__WT_SEP__) ;; *) func_stage="" ;; esac ;;
  esac
  if [ -z "$func_stage" ] && [ "$at_cmd" = 1 ]; then
    case "$w" in
      function) func_stage=name; func_name="" ;;
      # 演算子と目印は名前ではない。`()` だけの語も定義の目印であって名前ではない。
      "()"|__WT_*|"|"|"|&"|"&"|"&&"|"||"|"{"|"("|"}") ;;
      *"()") func_stage=body; func_name=${w%"()"} ;;
      *) func_stage=paren; func_name="$w" ;;
    esac
  fi
  # 本体で移動する関数を呼んだ後の現在地は、字面から決められない（決定 3）。
  if [ "$at_cmd" = 1 ] && [ -n "$base" ]; then
    case "$func_moving" in *"|$w|"*) cwd_known=0 ;; esac
  fi
  prev="$w"
}

# 段 4: 書き込み先の抽出。段 3 が扱わなかった語だけが渡る。出力は `_wt_scan_emit` が行い、
# 相対パスはそこで現在地から解決される。
_wt_scan_emit_word() {
  case "$w" in
    __WT_REDIR__|__WT_APPEND__)
      # 移動前の位置で解決済みのリダイレクトは、その枝が拾い終えている。
      if [ "$i" -lt "$resolved_redir_end" ]; then return 0; fi
      _wt_scan_redir_target "$i"
      _wt_scan_emit "$_WT_REDIR_DEST"
      # 命令の位置で読んだなら、被演算子の次に命令名が続く。
      [ "$at_cmd" = 1 ] && cmd_after_redir=$((_WT_REDIR_END + 1))
      ;;
    tee)
      # tee は並べたファイルすべてへ書き込む。1 件目で止めない。
      for ((j = i + 1; j < n; j++)); do
        tw=${words[j]}
        _wt_take_redirect_operand j tw || continue
        if _wt_is_separator "$tw"; then break; fi
        case "$tw" in
          -*) continue ;;
          *) _wt_scan_emit "$tw" ;;
        esac
      done
      ;;
    sed)
      # in-place の指定があるとき、操作対象のファイルをすべて拾う。
      _wt_extract_sed_targets "$i"
      ;;
    cp|mv)
      # 既定では最後の被演算子が宛先だが、`-t <ディレクトリ>` を付けると
      # 宛先が先に来て、後ろの被演算子はすべて複製元になる。
      _wt_extract_cp_mv_target "$i"
      ;;
  esac
}
