#!/usr/bin/env bash
# 局所変数は `_wt_extract_scan` が宣言し、2 本の関数が動的スコープで読み書きする。
# shellcheck disable=SC2034,SC2154
# NDF plugin: 書き込み先の推定の走査が使う、現在地と複合構文の入れ子の追跡。
#
# `worktree-common.sh` が source する。呼び出し側はこのファイルを直に source せず、
# `worktree-common.sh` だけを source する。
#
# ここの関数は `_wt_extract_scan` の局所変数（`words` `n` `i` `w` `cwd` ほか）を動的スコープで
# 読み書きする。`_wt_extract_scan` の中からだけ呼ぶ。

# 段 3: 現在地と複合構文の追跡。今の語が区切り・複合構文・`cd` のいずれかであれば
# 走査の状態を更新して 0 を返す（呼び出し側はその語で次へ進む）。当たらなければ 1 を
# 返し、段 4 が書き込み先を見る。
_wt_scan_track_word() {
  case "$w" in
    "|"|"|&")
      # パイプの各区画は部分シェルで動く。入口の位置へ戻す。
      cwd="$pipe_cwd"; cwd_known="$pipe_known"
      # 区画の中の `cd` は親の位置を変えない。`||` の判定に使う回数も戻す。
      list_cds="$pipe_cds"; cd_is_last=0
      return 0
      ;;
    "&")
      # 背景実行は処理のグループごと部分シェルへ入る。入口の位置へ戻す。
      cwd="$job_cwd"; cwd_known="$job_known"
      pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
      # `&` は and-or リストの終わりでもある。入口を引き直す。
      list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0; list_or=0
      list_and_uncertain=0; list_cond_cd=0
      cd_is_last=0
      return 0
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
      return 0
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
            _wt_scan_or_exit_redirs "$((i + 1))"
            # 左辺が成功した位置をそのまま持つ。判定に使う値は引き直す。
            list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0
            list_and_uncertain=0; list_cond_cd=0; cd_is_last=0
            pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
            return 0
            ;;
          "{")
            # `cd dir || { echo ...; exit 1; }` は `|| exit` より広く使われる。
            # グループの中に条件の付かない非継続命令があれば、抜けた先へは
            # 進まないため、続きが走るのは移動した後の位置だけである。
            #
            # ただし**グループの中は左辺が失敗した位置で走る**（`cd` は効いて
            # いない）。ここで位置を戻すと中の書き込み先を取り違えるため、
            # 移動した後の位置は閉じる `}` まで預け、下の失敗時の扱いへ落とす。
            if _wt_scan_or_group_exits "$((i + 1))"; then
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
      return 0
      ;;
    __WT_SEP__|__WT_CASE_FALL__)
      # `;` と改行でも同じシェルが続く。両方の入口を引き直す。
      # ただし `||` を跨いだリストに `cd` があると、それが効いたかどうかは
      # 左辺の成否で決まる。リストを抜けた後の位置も決められない。
      # `&&` を跨いだ先に `cd` があるときも同じで、左辺の成否で位置が変わる。
      #
      # `;&` `;;&` (`__WT_CASE_FALL__`) は `case` の枝の終わりだが、**現在地は
      # そのまま次の枝の本体へ引き継がれる**。and-or リストとパイプの入口を
      # 引き直す後始末は `;` と同じで、加えて落ちてきたことを段へ控える。次の
      # 見出しがこれを見て、枝の入口へ戻すかどうかを決める。
      if [ "$w" = __WT_CASE_FALL__ ] && [ "$case_depth" -gt 0 ]; then
        case_fall[case_depth - 1]=1
      fi
      if { [ "$list_or" = 1 ] && [ "$list_cds" != 0 ]; } ||
        [ "$list_cond_cd" = 1 ]; then
        cwd_known=0
      fi
      pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
      job_cwd="$cwd"; job_known="$cwd_known"
      list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0; list_or=0
      list_and_uncertain=0; list_cond_cd=0
      cd_is_last=0
      return 0
      ;;
    if|while|until|for|select|case)
      # 複合コマンドの入口。中で `cd` を追ったかを、閉じるときに比べるため控える。
      if [ "$at_cmd" = 1 ]; then
        block_cds[block_depth]=$cds
        block_depth=$((block_depth + 1))
        _wt_scan_push_group
        if [ "$w" = case ]; then
          case_cwd[case_depth]="$cwd"
          case_known[case_depth]="$cwd_known"
          case_fall[case_depth]=0
          case_depth=$((case_depth + 1))
        fi
      fi
      return 0
      ;;
    "{"|"(")
      # 部分シェル (`(`) と、同じシェルで走るグループ (`{`) の入口。どちらも
      # 中の `;` が外側のグループを切らないため、`&` の復元先を積む。
      #
      # 関数定義の本体は、命令の位置に無くてもここで開く（直前は `()` か
      # 定義した名前である）。本体の `cd` を外へ漏らさないよう、`{` で開いた
      # ものも部分シェルと同じく追跡の状態を積む。
      if [ "$at_cmd" = 1 ] || [ "$func_stage" = body ]; then
        _wt_scan_push_group
        [ "$w" = "(" ] && _wt_scan_push_subshell
        if [ "$func_stage" = body ]; then
          if [ "$w" = "{" ]; then
            _wt_scan_push_subshell
            func_brace[func_depth]=1
          else
            func_brace[func_depth]=0
          fi
          func_group[func_depth]="$group_depth"
          func_cds[func_depth]="$cds"
          func_names[func_depth]="$func_name"
          func_depth=$((func_depth + 1))
          func_stage=""
        fi
      fi
      return 0
      ;;
    "}")
      # `}` は予約語で、命令の位置にしか置けない。`echo }` の `}` は語である。
      if [ "$at_cmd" = 1 ]; then
        _wt_scan_close_function_body
        _wt_scan_pop_group
        # 必ず抜けるグループを閉じた。ここへ来た経路は左辺が成功した側だけで、
        # 位置は `||` で預けた移動後のものへ戻る。判定に使う値も引き直す。
        if [ "$or_depth" -gt 0 ] && [ "$i" = "${or_end[or_depth - 1]}" ]; then
          or_depth=$((or_depth - 1))
          cwd="${or_cwd[or_depth]}"; cwd_known="${or_known[or_depth]}"
          list_cwd="$cwd"; list_known="$cwd_known"; list_cds=0; list_or=0
          list_and_uncertain=0; list_cond_cd=0; cd_is_last=0
          pipe_cwd="$cwd"; pipe_known="$cwd_known"; pipe_cds=0
        fi
      fi
      return 0
      ;;
    __WT_SUBSHELL_END__)
      # 部分シェルの終わり。字句解析が切り出した `(` に対応するものだけが
      # この目印になる。配列の代入 `a=( 1 )`・`case` の見出し・関数定義の `()`
      # の `)` は語の一部で、ここへは来ない（来ると親の段まで戻ってしまう）。
      # 対応する `(` を数え損ねていても、深さは 0 で止める（`_wt_scan_pop_subshell` が
      # 深さ 0 で何もしない）。戻さなければ入口の位置のままで、案内が多めに
      # 出る側へ倒れる。
      _wt_scan_close_function_body
      _wt_scan_pop_group
      _wt_scan_pop_subshell
      return 0
      ;;
    else|elif)
      # 条件が偽のときに走る。条件の中の `cd` は効いていない。
      if [ "$at_cmd" = 1 ] && [ "$block_depth" -gt 0 ] &&
        [ "$cds" -gt "${block_cds[block_depth - 1]}" ]; then
        cwd_known=0
      fi
      return 0
      ;;
    fi|done|esac)
      # 本体が走ったかどうかは実行時に決まる。中で移動していたなら、閉じた後の
      # 現在地を決められない。
      if [ "$at_cmd" = 1 ] && [ "$block_depth" -gt 0 ]; then
        block_depth=$((block_depth - 1))
        [ "$cds" -gt "${block_cds[block_depth]}" ] && cwd_known=0
        _wt_scan_pop_group
        if [ "$w" = esac ] && [ "$case_depth" -gt 0 ]; then
          case_depth=$((case_depth - 1))
        fi
      fi
      return 0
      ;;
    cd) _wt_scan_cd; return 0 ;;
  esac
  return 1
}

# `cd` の走査。移動先を現在地へ反映し、同じ命令に付いたリダイレクトを移動する前の
# 位置で解決する。命令の位置に無い `cd` と、起点を渡されない呼び方では何もしない。
_wt_scan_cd() {
  [ "$at_cmd" = 1 ] && [ -n "$base" ] || return 0
  # 部分シェルの中でも移動は追う。中の相対パスはここで解決する。親の位置は
  # `)` で `_wt_scan_pop_subshell` が戻すため、この移動は外へ漏れない。
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
        _wt_scan_redir_target "$k"
        _wt_scan_emit "$_WT_REDIR_DEST"
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
}

# 複合コマンドの入口で `&` の復元先を積み、出口で戻す。
_wt_scan_push_group() {
  group_cwd[group_depth]="$job_cwd"
  group_known[group_depth]="$job_known"
  group_depth=$((group_depth + 1))
  job_cwd="$cwd"; job_known="$cwd_known"
}
_wt_scan_pop_group() {
  [ "$group_depth" -gt 0 ] || return 0
  group_depth=$((group_depth - 1))
  job_cwd="${group_cwd[group_depth]}"
  job_known="${group_known[group_depth]}"
}
# 部分シェルの入口で親の追跡状態を積み、中は新しいコマンドの並びとして始める。
_wt_scan_push_subshell() {
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
_wt_scan_pop_subshell() {
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
# 関数定義の本体を閉じる。閉じる語が本体のものかは複合コマンドの深さで見分ける。
# 中で `cd` を追っていたら、その名前を控える（決定 3）。比べるのは追跡の状態を
# 戻す前で、`_wt_scan_pop_subshell` が `cds` を入口の値へ戻してしまうためである。
_wt_scan_close_function_body() {
  [ "$func_depth" -gt 0 ] || return 0
  [ "${func_group[func_depth - 1]}" = "$group_depth" ] || return 0
  func_depth=$((func_depth - 1))
  if [ "$cds" -gt "${func_cds[func_depth]}" ] && [ -n "${func_names[func_depth]}" ]; then
    func_moving="${func_moving}${func_names[func_depth]}|"
  fi
  # `(` で開いた本体は、部分シェルの終わりの目印が既に戻す。
  [ "${func_brace[func_depth]}" = 1 ] && _wt_scan_pop_subshell
  return 0
}
# `||` の右辺のブレースグループ (`{ ... }`) が、必ず後続へ進まないかを見る。
# グループは同じシェルで走るため、その中の `exit` はスクリプトを終える。
# 引数はグループを開く `{` の添字で、閉じる `}` の添字を `_WT_GROUP_END` に置く。
#
# 数えるのは**グループの直下にある、条件の付かない非継続命令**だけである。
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
_wt_scan_or_group_exits() {
  local p="$1" depth=0 pw="" tw exits=0 pend=0 at_pos j
  _WT_GROUP_END=0
  for ((j = p; j < n; j++)); do
    tw=${words[j]}
    if [ "$pend" = 1 ]; then
      case "$tw" in
        "&"|"|"|"|&") pend=0 ;;
        __WT_SEP__|__WT_CASE_FALL__|"}"|"&&"|"||") exits=1; pend=0 ;;
      esac
    fi
    at_pos=0
    case "$pw" in
      ""|"{"|"("|__WT_SEP__|__WT_CASE_FALL__|__WT_CASE_END__|__WT_SUBSHELL_END__|"&&"\
      |"||"|"|"|"|&"|"&"|if|elif|then|else|while|until|do|"!"|time) at_pos=1 ;;
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
_wt_scan_or_exit_redirs() {
  local p="$1" j2 saved_cwd="$cwd" saved_known="$cwd_known"
  if [ "$list_cds" = 1 ] && [ "$cd_is_last" = 1 ]; then
    cwd="$list_cwd"; cwd_known="$list_known"
  elif [ "$list_cds" != 0 ]; then
    cwd_known=0
  fi
  # リダイレクトの目印は `_wt_is_separator` にも当たるため、先に見る。
  for ((j2 = p; j2 < n; j2++)); do
    case "${words[j2]}" in
      __WT_REDIR__|__WT_APPEND__)
        _wt_scan_redir_target "$j2"
        _wt_scan_emit "$_WT_REDIR_DEST"
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
