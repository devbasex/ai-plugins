#!/usr/bin/env bash
# NDF plugin: 工程の飛ばしの検知（#221）と、設計 Pull Request のマージの判定（#266）。
#
# **判定はすべてこのライブラリが持つ。** 入口のスクリプト（workflow-guard.sh /
# stage-check.sh）は入出力の整形だけを行う。worktree の共通ライブラリと同じ構造である。
#
# 2 つの機能で、判定できないときの倒し方が逆になる。
#
#   - #221 の報告は**通す側へ倒す**。記録の遅れで正当な操作が止まってはいけない
#   - #266 のマージは**拒否する側へ倒す**。`curl` の REST は `gh` が無くても成立するため、
#     判定できないまま通すとマージが動く。マージは取り消せない

# --- 工程の分類 -------------------------------------------------------------
# 並びと分類は SKILL.md の「モードごとに起動する Skill」の表から導ける。
#   R = 必須 / C = 条件付き / - = 対象外
# 表のセルが `—` なら対象外、`任意` か丸括弧で条件を添えたものなら条件付き、
# それ以外は必須である。表と食い違わないことはレビューで見る（#885）。
WF_MODES=$'light\toperation\tlegacy-refactor\tstandard\tdocumentation'
WF_STAGE_MATRIX=$'要求と受け入れ条件\tR\tR\t-\tR\tR
作業場所の用意\tC\tC\tR\tR\tR
設計\tC\tC\tR\tR\tR
素材の収集と出典の確定\t-\t-\t-\t-\tR
ドキュメント再構成\t-\t-\tC\tR\tR
ドキュメントレビュー\t-\t-\tC\tR\tR
計画\t-\tR\tR\tR\tC
実装\tR\tR\tR\tR\tR
構造改善\t-\t-\tR\tR\t-
実装レビュー\tR\tR\tR\tR\tR
完了判定\tR\tR\tR\tR\tR
Pull Request\tR\tR\tR\tR\tR
確定仕様化\t-\tC\t-\tR\tC
後片付け\tR\tR\tR\tR\tR
配布\tR\tR\tR\tR\tR
体裁レビュー\t-\t-\t-\t-\tR
リリース後テスト\t-\tC\tR\tR\tC
振り返り\t-\tC\tR\tR\tR'

# モードの高さ。**列の位置からは導かない**（決定 2-b）。`WF_MODES` の並びをそのまま
# 高さにすると読みやすさのための並びが高さの根拠として読まれる。母集合が変わっても、
# 列とは別に持てば高さの定義を直さずに済む。
#
# **`documentation` の高さの根拠は工程の数ではない**（マイルストーン 10 の決定 2）。この表で
# `R` を数えると `standard` が 16 個、`documentation` は 14 個であり、最多ではない。根拠は
# **混在が分割し損ねた状態であること**にある。`documentation` と他のモードが混ざる Pull Request
# は分けると定めており、`documentation` の列にしかない必須の工程（素材の収集と出典の確定 /
# 体裁レビュー）は、`standard` の側で検査すると一度も求められない。混ざっていること自体が
# 表に出ないため、`documentation` の側で検査する。
# **列の並びは高さと同じ順である**（決定 10）。表を軽い順に読めるようにするためであって、
# 導出の根拠ではない。**判定の順序とも別である。** `operation` は判定では 1 番に来るが、
# 高さは `light` の 1 つ上に置く（工程の重さが `light` と `legacy-refactor` の間にある）。
WF_MODE_HEIGHT=$'light\t1
operation\t2
legacy-refactor\t3
standard\t4
documentation\t5'

# 進め方（#1078）。`fast` では次の工程を課題ごとに求めない。トリガーの工程は検査のトリガーが
# 立ったときに前回の検査からの差分へ通し、まとめる工程はミッションの終わりに 1 回通す。
# 並びは SKILL.md の「進め方」の表と同じである。
WF_PACES=$'normal\tfast'
WF_FAST_TRIGGER_STAGES=$'構造改善\n実装レビュー'
WF_FAST_DEFERRED_STAGES=$'確定仕様化\n振り返り'

# 報告の引き金になる工程。ここへ進んだ時点で、記録の無い必須の工程を案内する。
WF_REPORT_STAGE='配布'

# Pull Request を作る時点の検査で、終点にする工程。この工程より前の必須の工程を見る。
WF_PR_STAGE='Pull Request'
# その検査から外す工程。**`cross-review` は Pull Request が無いと回せない**（決定 7）。
# 求めれば毎回欠落として出て、案内が読まれなくなる。
WF_PR_EXEMPT_STAGE='実装レビュー'
# 承認の印。**この名前のラベルがリポジトリに定義されていること自体が有効化の宣言になる**。
WF_APPROVAL_LABEL='design-approved'
# 設計 Pull Request を見分ける head のブランチ名の接頭辞。
WF_DESIGN_PREFIX='design/'

# 工程の並びを 1 行 1 件で返す。
#
# **パイプを使わない。** 呼び出し側は `pipefail` を立てており、読む側が先に終わると
# 書く側が SIGPIPE で落ちて、一致していてもパイプライン全体が失敗になる。
wf_stages() {
  local line
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    printf '%s\n' "${line%%$'\t'*}"
  done <<<"$WF_STAGE_MATRIX"
}

wf_is_stage() {
  local want="${1:-}" stage
  [ -n "$want" ] || return 1
  while IFS= read -r stage; do
    [ "$stage" = "$want" ] && return 0
  done < <(wf_stages)
  return 1
}

wf_is_pace() {
  local want="${1:-}"
  [ -n "$want" ] || return 1
  case $'\t'"$WF_PACES"$'\t' in
    *$'\t'"$want"$'\t'*) return 0 ;;
  esac
  return 1
}

# fast のときの工程の区分を返す（trigger / deferred）。当たらなければ 1 を返す。
wf_fast_class() {
  local stage="${1:-}" line
  while IFS= read -r line; do
    [ "$line" = "$stage" ] && { printf 'trigger\n'; return 0; }
  done <<<"$WF_FAST_TRIGGER_STAGES"
  while IFS= read -r line; do
    [ "$line" = "$stage" ] && { printf 'deferred\n'; return 0; }
  done <<<"$WF_FAST_DEFERRED_STAGES"
  return 1
}

wf_is_mode() {
  local want="${1:-}"
  [ -n "$want" ] || return 1
  case $'\t'"$WF_MODES"$'\t' in
    *$'\t'"$want"$'\t'*) return 0 ;;
  esac
  return 1
}

# モードの列番号（1 始まり）を返す。
_wf_mode_column() {
  local mode="${1:-}" i=0 name
  while IFS= read -r name; do
    i=$((i + 1))
    [ "$name" = "$mode" ] && { printf '%s\n' "$i"; return 0; }
  done < <(printf '%s\n' "$WF_MODES" | tr '\t' '\n')
  return 1
}

# ある工程がそのモードで必須か（R / C / -）を返す。
wf_stage_class() {
  local mode="${1:-}" stage="${2:-}" column line name
  column=$(_wf_mode_column "$mode") || return 1
  while IFS= read -r line; do
    IFS=$'\t' read -r name c1 c2 c3 c4 c5 <<<"$line"
    [ "$name" = "$stage" ] || continue
    case "$column" in
      1) printf '%s\n' "$c1" ;; 2) printf '%s\n' "$c2" ;;
      3) printf '%s\n' "$c3" ;; 4) printf '%s\n' "$c4" ;;
      5) printf '%s\n' "$c5" ;;
    esac
    return 0
  done <<<"$WF_STAGE_MATRIX"
  return 1
}

# モードの高さを数で返す。知らないモードは 0 を返す。
wf_mode_height() {
  local want="${1:-}" line name value
  [ -n "$want" ] || { printf '0\n'; return 1; }
  while IFS= read -r line; do
    IFS=$'\t' read -r name value <<<"$line"
    if [ "$name" = "$want" ]; then printf '%s\n' "$value"; return 0; fi
  done <<<"$WF_MODE_HEIGHT"
  printf '0\n'
  return 1
}

# 2 つのモードのうち高い方を返す。高さが同じなら先に与えた方を返す。
wf_higher_mode() {
  local a="${1:-}" b="${2:-}" ha hb
  [ -n "$a" ] || { printf '%s\n' "$b"; return 0; }
  [ -n "$b" ] || { printf '%s\n' "$a"; return 0; }
  ha=$(wf_mode_height "$a") || true
  hb=$(wf_mode_height "$b") || true
  if [ "$hb" -gt "$ha" ]; then printf '%s\n' "$b"; else printf '%s\n' "$a"; fi
}

# Pull Request を作る時点で求める工程を 1 行 1 件返す。
#
# **終点は工程表が持つ並びで決める。** 記録済みの最も先の工程（`_wf_frontier`）に置くと、
# Pull Request を作る時点では常にその手前であるため、欠落が 1 件も出ない（決定 7）。
wf_stages_before_pr() {
  local stage
  while IFS= read -r stage; do
    [ "$stage" = "$WF_PR_STAGE" ] && return 0
    [ "$stage" = "$WF_PR_EXEMPT_STAGE" ] && continue
    printf '%s\n' "$stage"
  done < <(wf_stages)
}

# --- 引用符を解いた語の分割 --------------------------------------------------
# **展開はしない。** コマンドの本文を判定するだけで、実行はこの hook の役目ではない。
# `$SCRIPTS` のような未展開の変数はそのままの文字列として残る。
#
# **bash の文字取り出しでは書かない。** tool 実行のたびに走るため、長い本文で費用が
# 効く。実測では 27KB の本文に 2.4 秒かかり、hook の制限時間に近づいた。同じ処理を
# awk に置くと 0.02 秒で終わる。
#
# **区切りは NUL である。** 引用符の中の改行は語の一部として残るため、行で区切ると
# 1 つの語が複数に割れる。`pr` が必須と定めるヒアドキュメントの本文はこの形になり、
# 行区切りで読むと 1 行目だけを本文として扱ってしまう（#427 のレビュー）。
# 読む側は `read -r -d ""` で受ける。
#
# **コマンドの境目は空の語で表す（#565）。** 引用の外の `;` `|` `(` `)` `&` と、本文の
# 途中の改行で出す。記録の値に `"設計";` のように演算子が密着すると、区切りが無ければ
# 値の一部として読まれ、工程名ではないとして黙って捨てられていた。空の語は `""` を解いた
# 結果として出ることがないため、実在の語と衝突しない。読む側は `[ -z "$tok" ]` で見分ける。
#
#   - `&` は直前が `>` `<` か直後が `>` のときリダイレクトの一部として残す（`2>&1` / `&>`）
#   - 改行の区切りは次の行の頭で出す。awk は次の行を読むまで、行末が本文の途中だったかを
#     知らない。空の行では出さずに持ち越すため、本文の末尾の改行（here-string が足すもの
#     を含む）では出ない
#   - 行末の `\` は継続として捨て、区切りを出さない
wf_split() {
  awk '
    function flush() { if (out != "") { printf "%s%c", out, 0; out = "" } }
    function mark() { flush(); printf "%s%c", "", 0 }
    {
      n = length($0)
      if (pending && n > 0) { printf "%s%c", "", 0; pending = 0 }
      cont = 0
      for (i = 1; i <= n; i++) {
        ch = substr($0, i, 1)
        if (quote != "") {
          if (ch == quote) { quote = "" } else { out = out ch }
          continue
        }
        if (ch == "\"" || ch == "'"'"'") { quote = ch; continue }
        if (ch == " " || ch == "\t") { flush(); continue }
        if (ch == ";" || ch == "|" || ch == "(" || ch == ")") { mark(); continue }
        if (ch == "&") {
          prev = substr($0, i - 1, 1)
          if (prev != ">" && prev != "<" && substr($0, i + 1, 1) != ">") { mark(); continue }
        }
        if (ch == "\\" && i == n) { cont = 1; continue }
        out = out ch
      }
      if (quote != "") { out = out "\n" }
      else { flush(); pending = !cont }
    }
    END { flush() }
  ' <<<"${1:-}"
}

# 判定の対象になりうる本文かを、走査の前に安く見分ける。
# **当たらない本文では語の分割そのものを行わない。**
#
# **行末の `\` による継続は空白へ畳んでから見る。** `gh pr \⏎merge 268` は行単位の grep では
# `pr` と `merge` が別の行に分かれ、読み手の判定まで届かない（#565）。
wf_is_candidate() {
  local text="${1:-}"
  grep -qE 'projects-sync\.sh|pr[[:space:]]+merge|pulls/[0-9]+/merge|pr[[:space:]]+create' \
    <<<"${text//$'\\\n'/ }"
}

_wf_seek_gh_verb() {
  local state="${1:-0}" tok="${2:-}" verb="${3:-}"
  case "$state" in
    0) [ "$tok" = "gh" ] && printf '1\n' || printf '0\n' ;;
    1)
      case "$tok" in
        pr) printf '2\n' ;;
        gh) printf '1\n' ;;
        -R|--repo) printf '4\n' ;;
        -*) printf '1\n' ;;
        *) printf '0\n' ;;
      esac
      ;;
    2) [ "$tok" = "$verb" ] && printf '3\n' || printf '0\n' ;;
    4) printf '1\n' ;;
    *) printf '%s\n' "$state" ;;
  esac
}

# `gh pr <verb>` を探しながら語を読み、空でない語ごとに `<each> <語>` を呼ぶ。
# 見つけたら 0、見つからなければ 1 を返す。
#
# 区切りに来たら、見つける前なら `gh` の探索をやり直し、見つけた後なら読むのを止める。
# 越えて読むと `gh pr merge; echo 268` の 268 を拾う。
#
# `<each>` は `state`（3 が見つけた状態）と `found`（0 が見つけた）を読み書きできる。
# 呼ぶ時点で、その語までの探索は済んでいる。
_wf_scan_gh_verb() {
  local cmd="${1:-}" verb="${2:-}" each="${3:-}" tok state=0 found=1
  while IFS= read -r -d '' tok; do
    if [ -z "$tok" ]; then
      [ "$found" -ne 0 ] || break
      state=0
      continue
    fi
    state=$(_wf_seek_gh_verb "$state" "$tok" "$verb")
    [ "$state" = "3" ] && found=0
    "$each" "$tok"
  done < <(wf_split "$cmd")
  [ "$found" -eq 0 ]
}

# 進行の記録のコマンドなら、課題番号・キー・値をタブ区切りで出す。
#
# 見分けは `projects-sync.sh` で終わる語である。呼び出し側は `$SCRIPTS` を展開してから
# 実行するが、hook が受け取るのは書かれたままの本文なので、どちらの形でも当たる。
#
# **1 つ目の記録のコマンドだけを読む。** 見つけた後の区切りか 3 語目で止める。区切りを
# 越えて読むと、`stage; echo 設計` の `echo` を値として読む。
wf_parse_sync() {
  local cmd="${1:-}" tok found=1
  local -a args=()
  while IFS= read -r -d '' tok; do
    if [ "$found" -ne 0 ]; then
      case "$tok" in *projects-sync.sh) found=0 ;; esac
      continue
    fi
    [ -n "$tok" ] || break
    args+=("$tok")
    [ "${#args[@]}" -lt 3 ] || break
  done < <(wf_split "$cmd")
  [ "$found" -eq 0 ] || return 1
  [ "${#args[@]}" -ge 3 ] || return 1
  printf '%s\t%s\t%s\n' "${args[0]}" "${args[1]}" "${args[2]}"
}

# --- Pull Request の作成の観測（#424） ---------------------------------------

# 閉じる語の読み取りの実体。**写しは持たない**（決定 3）。`merged` と gate の両方が
# 同じファイルを `bash` の副プロセスとして起動する。
#
# **`cd` では解決しない。** Skill だけを複製する Kiro CLI の配置では symlink の手前へ
# 戻り、プラグインルートを外す。4 階層の相対で指す形は #293 で契約として固定した。
WF_CLOSING_ISSUES="$(dirname "${BASH_SOURCE[0]}")/../../../../scripts/lib/closing-issues.sh"

_wf_read_file() {
  local file="${1:-}"
  if [ -f "$file" ]; then
    cat -- "$file" 2>/dev/null || true
  fi
}

# `gh pr create` の本文を取り出す。取れなければ 1 を返す。
#
# 本文の渡し方は 2 つある（`--body` と `--body-file`）。**短い形も見る**（`-b` / `-F`）。
_wf_pr_create_body() {
  local cmd="${1:-}" want="" body=""
  _wf_scan_gh_verb "$cmd" "create" _wf_pr_create_body_token || return 1
  [ -n "$body" ] || return 1
  printf '%s\n' "$body"
}

# `_wf_pr_create_body` の 1 語分。`want` と `body` は呼び出し元のものを書き換える。
_wf_pr_create_body_token() {
  local tok="${1:-}"
  if [ -n "$want" ]; then
    case "$want" in
      text) body="$tok" ;;
      file) body=$(_wf_read_file "$tok") ;;
    esac
    want=""
    return 0
  fi
  [ "$found" -eq 0 ] || return 0
  case "$tok" in
    --body|-b) want=text ;;
    --body-file|-F) want=file ;;
    --body=*) body="${tok#--body=}" ;;
    --body-file=*)
      body=$(_wf_read_file "${tok#--body-file=}")
      ;;
  esac
}

# `gh pr create` の本文から、閉じる語が指す `<所有者>/<リポジトリ>` と `<番号>` の組を
# タブ区切りで出す。取れなければ 1 を返す（**何も出さずに通す**）。
wf_parse_pr_create() {
  local cmd="${1:-}" body slug out
  [ -x "$WF_CLOSING_ISSUES" ] || [ -f "$WF_CLOSING_ISSUES" ] || return 1
  body=$(_wf_pr_create_body "$cmd") || return 1
  slug=$(wf_repo_slug ".") || return 1
  out=$(printf '%s\n' "$body" | bash "$WF_CLOSING_ISSUES" --repo "$slug" 2>/dev/null) || return 1
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}

# 控えに記録の無い、Pull Request の作成までに求める工程を 1 行 1 件返す。
_wf_missing_before_pr() {
  local mode="${1:-}" content="${2:-}" stage class pace
  local -a recorded=()
  pace=$(_wf_read_pace "$content")
  while IFS= read -r stage; do
    recorded+=("$stage")
  done < <(_wf_recorded_lines "$content")
  while IFS= read -r stage; do
    _wf_contains "$stage" ${recorded[@]+"${recorded[@]}"} && continue
    # fast のトリガー・まとめる工程は、Pull Request の作成の時点で求めない
    if [ "$pace" = "fast" ] && wf_fast_class "$stage" >/dev/null; then continue; fi
    class=$(wf_stage_class "$mode" "$stage") || continue
    [ "$class" = "R" ] || continue
    printf '%s\n' "$stage"
  done < <(wf_stages_before_pr)
}

_wf_parse_targets() {
  local line repo issue
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    IFS=$'\t' read -r repo issue <<<"$line"
    [ -n "$repo" ] && [ -n "$issue" ] || continue
    printf '%s\t%s\n' "$repo" "$issue"
  done
}

# 控えが存在すれば本文とモードをタブ区切り 1 行で返す。控えが無ければ 1 を返す。
_wf_load_state_mode() {
  local repo="${1:-}" issue="${2:-}" file content mode
  file=$(wf_state_file "$repo" "$issue") || return 1
  [ -f "$file" ] || return 1
  content=$(wf_state_read "$file")
  mode=$(_wf_read_mode "$content")
  printf '%s\t%s\n' "$content" "$mode"
}

_wf_collect_target_modes() {
  local line repo issue state_mode content mode
  local -a raw_targets=() modes=()
  local effective=""

  while IFS= read -r line; do
    [ -n "$line" ] || continue
    raw_targets+=("$line")
    IFS=$'\t' read -r repo issue <<<"$line"
    state_mode=$(_wf_load_state_mode "$repo" "$issue") || continue
    IFS=$'\t' read -r content mode <<<"$state_mode"
    [ -n "$mode" ] || continue
    _wf_contains "$mode" ${modes[@]+"${modes[@]}"} || modes+=("$mode")
    effective=$(wf_higher_mode "$effective" "$mode")
  done
  [ "${#raw_targets[@]}" -gt 0 ] || return 1

  printf '%s\n' "$effective"
  printf '%s\n' "${modes[*]}"
  for line in "${raw_targets[@]}"; do
    printf '%s\n' "$line"
  done
}

# 先頭の行を引数で名指しした変数へ 1 行ずつ読み、残りの空でない行を _WF_PACKED_REST へ読む。
# bash は配列を戻せないため、標準出力へ積んだスカラと行の並びをこの形で解く。
_wf_read_packed() {
  local _wf_pk_name _wf_pk_line
  _WF_PACKED_REST=()
  for _wf_pk_name in "$@"; do
    IFS= read -r _wf_pk_line
    printf -v "$_wf_pk_name" '%s' "$_wf_pk_line"
  done
  while IFS= read -r _wf_pk_line; do
    [ -n "$_wf_pk_line" ] && _WF_PACKED_REST+=("$_wf_pk_line")
  done
  return 0
}

_wf_unpack_collected() {
  local _wf_uc_targets_name="${1:-}" _wf_uc_modes_name="${2:-}" _wf_uc_modes_str_name="${3:-}"
  local _wf_uc_line _wf_uc_index=0
  shift 3

  _wf_read_packed "$@"
  for _wf_uc_line in ${_WF_PACKED_REST[@]+"${_WF_PACKED_REST[@]}"}; do
    printf -v "${_wf_uc_targets_name}[$_wf_uc_index]" '%s' "$_wf_uc_line"
    _wf_uc_index=$((_wf_uc_index + 1))
  done
  read -r -a "$_wf_uc_modes_name" <<<"${!_wf_uc_modes_str_name}"
}

_wf_collect_targets() {
  local parsed collected effective modes_str line conflict=0
  local -a raw_targets=() modes=()

  parsed=$(_wf_parse_targets) || return 1
  [ -n "$parsed" ] || return 1
  collected=$(printf '%s\n' "$parsed" | _wf_collect_target_modes) || return 1
  _wf_unpack_collected raw_targets modes modes_str effective modes_str <<<"$collected"
  [ "${#modes[@]}" -gt 1 ] && conflict=1

  printf '%s\n' "$effective"
  printf '%s\n' "$conflict"
  printf '%s\n' "$modes_str"
  for line in "${raw_targets[@]}"; do
    printf '%s\n' "$line"
  done
  return 0
}

# content（通過工程の記録）と effective モードから、PR 前に記録が無い必須工程の案内文だけを
# 組み立てて返す。欠落が無ければ何も出力しない。状態ファイルやモードの診断は行わない。
_wf_missing_stage_note() {
  local repo="${1:-}" issue="${2:-}" effective="${3:-}" content="${4:-}"
  local stage missing=""
  local -a missing_stages=()
  while IFS= read -r stage; do
    [ -n "$stage" ] || continue
    missing_stages+=("$stage")
  done < <(_wf_missing_before_pr "$effective" "$content")
  missing=$(wf_join ${missing_stages[@]+"${missing_stages[@]}"})
  [ -n "$missing" ] && printf '  #%s (%s): 記録なし: %s\n' "$issue" "$repo" "$missing"
  return 0
}

_wf_target_note() {
  local repo="${1:-}" issue="${2:-}" effective="${3:-}"
  local state_mode content mode
  if ! state_mode=$(_wf_load_state_mode "$repo" "$issue"); then
    printf '  #%s (%s): 進行の記録がありません（モードの記録も、通過工程の記録もありません）\n' "$issue" "$repo"
    return 0
  fi
  IFS=$'\t' read -r content mode <<<"$state_mode"
  if [ -z "$mode" ]; then
    printf '  #%s (%s): モードの記録がありません\n' "$issue" "$repo"
    [ -n "$effective" ] || return 0
  fi
  _wf_missing_stage_note "$repo" "$issue" "$effective" "$content"
  return 0
}

_wf_compose_evidence_body() {
  local conflict="${1:-0}" effective="${2:-}" modes_str="${3:-}"
  shift 3
  local -a notes=("$@")
  local body line

  [ "${#notes[@]}" -gt 0 ] || [ "$conflict" -eq 1 ] || return 1

  body='Pull Request を作る前に、進行の記録を確かめてください。'
  for line in ${notes[@]+"${notes[@]}"}; do
    body="$body"$'\n'"$line"
  done
  if [ "$conflict" -eq 1 ]; then
    body="$body"$'\n'"モードの記録が課題ごとに食い違います（$modes_str）。最も高い $effective を基準に見ています。"
    body="$body"$'\n'"1 つの Pull Request に対しモードは 1 つです。閉じる課題すべての控えへ同じ値を書いてください。"
  fi
  body="$body"$'\n'"記録が無いことは、その工程を通っていないことと同じではありません。記録の側が遅れているだけのこともあります。"
  body="$body"$'\n'"記録するには: bash \"\$SCRIPTS/projects-sync.sh\" <課題番号> stage \"<工程名>\""
  printf '%s\n' "$body"
}

# リポジトリと番号の組（タブ区切り、1 行 1 件）を標準入力から受け、案内を 1 つの文字列で
# 返す。**言うことが何も無ければ 1 を返す。**
#
# **拒否はしない。** 記録が無いことは、その工程を通っていないことと同じではない。
wf_evidence_report() {
  local collected effective conflict modes_str line repo issue note
  local -a targets=() notes=() modes=()

  command -v jq >/dev/null 2>&1 || return 1

  collected=$(_wf_collect_targets) || return 1
  _wf_unpack_collected targets modes modes_str effective conflict modes_str <<<"$collected"

  for line in "${targets[@]}"; do
    IFS=$'\t' read -r repo issue <<<"$line"
    while IFS= read -r note; do
      [ -n "$note" ] && notes+=("$note")
    done < <(_wf_target_note "$repo" "$issue" "$effective")
  done

  _wf_compose_evidence_body "$conflict" "$effective" "$(wf_join ${modes[@]+"${modes[@]}"})" ${notes[@]+"${notes[@]}"}
}

# --- JSON の組み立て --------------------------------------------------------
# jq が無くても出力できるようにする。#266 は jq が無いときも拒否を返すため、
# 出力の側が jq に依存すると、拒否そのものを届けられない。
wf_json_escape() {
  local s="${1:-}"
  # **`\` を最初に置き換える。** 後にすると、`\"` や `\n` として足したほうの `\` まで
  # 二重にしてしまう。以降の 4 つは互いに重ならないため順序を問わない。
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\t'/\\t}
  s=${s//$'\r'/\\r}
  printf '%s' "$s"
}

wf_emit_deny() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny",'
  printf '"permissionDecisionReason":"%s"}}\n' "$(wf_json_escape "${1:-}")"
}

wf_emit_context() {
  local text
  text=$(wf_json_escape "${1:-}")
  printf '{"systemMessage":"%s",' "$text"
  printf '"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$text"
}

# --- リポジトリと控えの置き場所 ---------------------------------------------

# `<所有者>/<リポジトリ>` を返す。**通信しない。**
# 畳む規則は projects-common.sh の pj_repo_slug の 1 箇所にある（#435）。
# 読み込めていれば委譲し、できていなければ従来どおりの規則をここで実行する。
wf_repo_slug() {
  local dir="${1:-.}"
  if command -v pj_repo_slug >/dev/null 2>&1 && [ "$(type -t pj_repo_slug)" = "function" ]; then
    pj_repo_slug "$dir"
    return
  fi
  local url slug repo owner
  command -v git >/dev/null 2>&1 || return 1
  url=$(git -C "$dir" config --get remote.origin.url 2>/dev/null) || return 1
  [ -n "$url" ] || return 1
  slug=${url%.git}
  slug=${slug%/}
  slug=${slug##*:}          # git@github.com:owner/repo
  case "$slug" in */*) ;; *) return 1 ;; esac
  repo=${slug##*/}
  owner=${slug%/*}
  owner=${owner##*/}
  [ -n "$owner" ] && [ -n "$repo" ] || return 1
  printf '%s/%s\n' "$owner" "$repo"
}

# 控えの置き場所。リポジトリの中には置かない（変更として Pull Request に載るため）。
wf_state_dir() {
  local base fallback="${TMPDIR:-/tmp}/ndf-stages"
  if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    base="$CLAUDE_PLUGIN_DATA/stages"
  elif [ -n "${XDG_STATE_HOME:-}" ]; then
    base="$XDG_STATE_HOME/ndf/stages"
  elif [ -n "${HOME:-}" ]; then
    base="$HOME/.local/state/ndf/stages"
  else
    base="$fallback"
  fi
  if mkdir -p "$base" 2>/dev/null && [ -w "$base" ]; then
    printf '%s\n' "$base"
    return 0
  fi
  base="$fallback"
  mkdir -p "$base" 2>/dev/null || return 1
  printf '%s\n' "$base"
}

wf_state_file() {
  local slug="${1:-}" issue="${2:-}" dir name
  [ -n "$slug" ] && [ -n "$issue" ] || return 1
  dir=$(wf_state_dir) || return 1
  name=$(printf '%s__%s' "${slug//\//__}" "$issue" | tr -c 'A-Za-z0-9._-' '_')
  printf '%s/%s.json\n' "$dir" "$name"
}

# --- 排他 -------------------------------------------------------------------
#
# **実装は `<プラグインルート>/scripts/lib/lock-common.sh` の 1 箇所にある**（#293）。
# ここに置くのは、既存の名前で呼べるようにするための委譲と、待ちの上限の既定の上書き
# だけである。`flock` を使わない理由・関門が 2 段である理由・陳腐化の判定は、いずれも
# 共通ファイルの同じ節にある。
#
# **共通ファイルは自分の位置からの相対で指す。** 4 つの配布先ランタイムのすべてで、
# Skill のディレクトリからプラグインルートの `scripts/` へ 4 階層で戻れる（#293 の実測）。
# `cd` で戻ってから `pwd` を取る形は採らない。Skill だけを複製する Kiro CLI の配置では
# symlink の手前へ戻り、プラグインルートを外す。
#
# **読み込めないときは、常に取得できないものとして定義する。** 控えへ 1 件積む処理は、
# 取得できないとき書き込みそのものを行わず、終了コード 0 で工程を続ける。
# shellcheck source=../../../../scripts/lib/lock-common.sh
if ! . "$(dirname "${BASH_SOURCE[0]}")/../../../../scripts/lib/lock-common.sh" 2>/dev/null; then
  ndf_lock_acquire() { return 1; }
  ndf_lock_release() { [ -n "${1:-}" ] || return 0; rm -rf "$1" 2>/dev/null; return 0; }
fi

# slug を畳む規則の実体は projects-common.sh の pj_repo_slug にある（#435）。
# 読み込めなければ wf_repo_slug 側の従来の規則で処理するため、失敗は握りつぶす。
# shellcheck source=../../../../scripts/lib/projects-common.sh
. "$(dirname "${BASH_SOURCE[0]}")/../../../../scripts/lib/projects-common.sh" 2>/dev/null || true

# 捨ててよいと見なすまでの分数。共通ファイルの値を、既存の名前でも引けるようにする。
WF_LOCK_STALE_MINUTES="${NDF_LOCK_STALE_MINUTES:-5}"

# **待ちの上限の上書きは、この側だけが持つ。** `development-workflow` のテストが待ち
# 時間を 1 秒へ縮めるために使う。台帳の側へ広げると、名前が指す対象と実際に効く範囲が
# 食い違う。
WF_LOCK_TIMEOUT="${NDF_STAGE_LOCK_TIMEOUT:-5}"

wf_lock_acquire() {
  local dir="${1:-}" timeout="${2:-$WF_LOCK_TIMEOUT}"
  ndf_lock_acquire "$dir" "$timeout"
}

wf_lock_release() {
  ndf_lock_release "$@"
}

_wf_lock_discard() {
  _ndf_lock_discard "$@"
}

_wf_lock_is_stale() {
  _ndf_lock_is_stale "$@"
}

# --- 通過工程の控え ---------------------------------------------------------

# 控えを読む。`version` が 1 以外・読めない・壊れているものは記録が無いものとして扱う。
wf_state_read() {
  local file="${1:-}" content
  if [ -n "$file" ] && [ -f "$file" ]; then
    content=$(jq -c 'select(.version == 1)' "$file" 2>/dev/null) || content=""
    [ -n "$content" ] && { printf '%s\n' "$content"; return 0; }
  fi
  printf '{"version":1,"stages":[]}\n'
}

# 控えから記録されたモードを取り出す。
_wf_read_mode() {
  jq -r '.mode // empty' <<<"${1:-}" 2>/dev/null
}

# 控えから記録された進め方を取り出す。記録が無ければ normal と読む。
_wf_read_pace() {
  local pace
  pace=$(jq -r '.pace // empty' <<<"${1:-}" 2>/dev/null)
  printf '%s\n' "${pace:-normal}"
}

# 控えへ 1 件積む。**排他を取れないときは書き込みそのものを行わない。**
# 飛ばしても終了コード 0 で返って工程は続き、飛ばした工程は報告の「記録なし」に含まれる。
wf_record() {
  local slug="${1:-}" issue="${2:-}" key="${3:-}" value="${4:-}"
  local file lock content updated now
  command -v jq >/dev/null 2>&1 || return 0
  file=$(wf_state_file "$slug" "$issue") || return 0
  lock="$file.lockdir"
  if ! wf_lock_acquire "$lock" "$WF_LOCK_TIMEOUT"; then
    printf 'NOTE: #%s の進行の控えが使用中のため、この記録は残しません\n' "$issue" >&2
    return 0
  fi
  content=$(wf_state_read "$file")
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) || now=""
  case "$key" in
    stage) updated=$(printf '%s' "$content" \
      | jq --arg r "$slug" --argjson i "$issue" --arg v "$value" --arg t "$now" \
        '.repo = $r | .issue = $i | .stages = ((.stages // []) + [$v]) | .updated_at = $t' 2>/dev/null) ;;
    mode) updated=$(printf '%s' "$content" \
      | jq --arg r "$slug" --argjson i "$issue" --arg v "$value" --arg t "$now" \
        '.repo = $r | .issue = $i | .mode = $v | .stages = (.stages // []) | .updated_at = $t' 2>/dev/null) ;;
    pace) updated=$(printf '%s' "$content" \
      | jq --arg r "$slug" --argjson i "$issue" --arg v "$value" --arg t "$now" \
        '.repo = $r | .issue = $i | .pace = $v | .stages = (.stages // []) | .updated_at = $t' 2>/dev/null) ;;
    *) wf_lock_release "$lock"; return 0 ;;
  esac
  if [ -n "$updated" ]; then
    if printf '%s\n' "$updated" >"$file.tmp.$$" 2>/dev/null; then
      mv "$file.tmp.$$" "$file" 2>/dev/null || rm -f "$file.tmp.$$" 2>/dev/null
    fi
  fi
  wf_lock_release "$lock"
  return 0
}

# 記録された工程を、控えに書かれた順で 1 行 1 件返す。
_wf_recorded() {
  jq -r '(.stages // []) | .[]' <<<"$1" 2>/dev/null
}

# _wf_recorded の出力から空行を除き、記録済み工程を 1 行 1 件返す。
# bash は配列を戻せないため行で返す。
_wf_recorded_lines() {
  _wf_recorded "${1:-}" | while IFS= read -r s; do
    [ -n "$s" ] && printf '%s\n' "$s"
  done
}

# 与えた値が並びの中にあれば 0 を返す。
_wf_contains() {
  local want="$1" item
  shift
  for item in "$@"; do
    [ "$item" = "$want" ] && return 0
  done
  return 1
}

# いちばん先まで進んだ記録の位置を、工程表の並びで測って返す。
# **まだ来ていない工程は欠落ではない。** ここまでを見る範囲とする。
_wf_frontier() {
  local stage index=0 frontier=0
  while IFS= read -r stage; do
    index=$((index + 1))
    _wf_contains "$stage" "$@" && frontier=$index
  done < <(wf_stages)
  printf '%s\n' "$frontier"
}

wf_join() {
  local out="" item
  for item in "$@"; do
    [ -n "$out" ] && out="$out / "
    out="$out$item"
  done
  printf '%s' "$out"
}

# 工程の分類。_wf_classify_stages が書き、wf_report が読む。
WF_CLASS_PRESENT='present'         # 記録あり
WF_CLASS_MISSING='missing'         # 必須で記録なし
WF_CLASS_CONDITIONAL='conditional' # 条件付き
WF_CLASS_TRIGGER='trigger'         # fast: トリガーで通す（記録を求めない）
WF_CLASS_DEFERRED='deferred'       # fast: ミッションの終わりにまとめる（記録を求めない）

# frontier までの各工程を分類し、'class<TAB>stage' を 1 行 1 件で返す。
# class は WF_CLASS_* のいずれか。
# recorded 配列・mode・frontier を引数で受け取る。
# pace が fast なら、必須か条件付きのトリガー・まとめる工程を WF_CLASS_TRIGGER / WF_CLASS_DEFERRED にする。
# まとめる工程は、先へ進んだ記録（frontier）より後にあっても出す（ミッションの終わりに通すため）。
_wf_classify_stages() {
  local mode="$1" frontier="$2" pace="$3"
  shift 3
  local -a recorded=("$@")
  local stage class index=0 fast
  while IFS= read -r stage; do
    index=$((index + 1))
    if _wf_contains "$stage" ${recorded[@]+"${recorded[@]}"}; then
      [ "$index" -le "$frontier" ] && printf '%s\t%s\n' "$WF_CLASS_PRESENT" "$stage"
      continue
    fi
    [ -n "$mode" ] || continue
    class=$(wf_stage_class "$mode" "$stage") || continue
    if [ "$pace" = "fast" ] && fast=$(wf_fast_class "$stage") && { [ "$class" = "R" ] || [ "$class" = "C" ]; }; then
      case "$fast" in
        trigger) [ "$index" -le "$frontier" ] && printf '%s\t%s\n' "$WF_CLASS_TRIGGER" "$stage" ;;
        deferred) printf '%s\t%s\n' "$WF_CLASS_DEFERRED" "$stage" ;;
      esac
      continue
    fi
    [ "$index" -le "$frontier" ] || continue
    case "$class" in
      R) printf '%s\t%s\n' "$WF_CLASS_MISSING" "$stage" ;;
      C) printf '%s\t%s\n' "$WF_CLASS_CONDITIONAL" "$stage" ;;
    esac
  done < <(wf_stages)
}

# 通過工程を報告する。**終了コードで工程を止めない。**
wf_report() {
  local slug="${1:-}" issue="${2:-}" file content mode pace stage class frontier
  local -a recorded=() present=() missing=() conditional=() trigger=() deferred=()

  command -v jq >/dev/null 2>&1 || { wf_report_empty "$issue"; return 0; }
  file=$(wf_state_file "$slug" "$issue") || { wf_report_empty "$issue"; return 0; }
  content=$(wf_state_read "$file")
  while IFS= read -r stage; do
    recorded+=("$stage")
  done < <(_wf_recorded_lines "$content")
  if [ "${#recorded[@]}" -eq 0 ]; then
    wf_report_empty "$issue"
    return 0
  fi
  mode=$(_wf_read_mode "$content")
  pace=$(_wf_read_pace "$content")
  frontier=$(_wf_frontier "${recorded[@]}")

  while IFS=$'\t' read -r class stage; do
    case "$class" in
      "$WF_CLASS_PRESENT") present+=("$stage") ;;
      "$WF_CLASS_MISSING") missing+=("$stage") ;;
      "$WF_CLASS_CONDITIONAL") conditional+=("$stage") ;;
      "$WF_CLASS_TRIGGER") trigger+=("$stage") ;;
      "$WF_CLASS_DEFERRED") deferred+=("$stage") ;;
    esac
  done < <(_wf_classify_stages "$mode" "$frontier" "$pace" "${recorded[@]}")

  if [ -n "$mode" ] && [ "$pace" = "fast" ]; then
    printf '#%s の通過工程（%s・進め方 fast）\n' "$issue" "$mode"
  elif [ -n "$mode" ]; then
    printf '#%s の通過工程（%s）\n' "$issue" "$mode"
  else
    printf '#%s の通過工程（モード不明）\n' "$issue"
  fi
  printf '  記録あり: %s\n' "$(wf_join "${present[@]+"${present[@]}"}")"
  [ "${#missing[@]}" -gt 0 ] && printf '  記録なし: %s\n' "$(wf_join "${missing[@]}")"
  [ "${#conditional[@]}" -gt 0 ] && printf '  条件付き: %s\n' "$(wf_join "${conditional[@]}")"
  [ "${#trigger[@]}" -gt 0 ] && printf '  トリガー: %s（検査のトリガーが立ったときに前回の検査からの差分へ通す）\n' \
    "$(wf_join "${trigger[@]}")"
  [ "${#deferred[@]}" -gt 0 ] && printf '  まとめる: %s（ミッションの終わりに 1 回通す）\n' "$(wf_join "${deferred[@]}")"
  if [ -z "$mode" ]; then
    printf 'モードの記録が無いため、必須の工程は判定しません。\n'
  elif [ "${#missing[@]}" -eq 0 ] && [ "${#conditional[@]}" -eq 0 ]; then
    printf '記録の無い必須の工程はありません。\n'
  else
    printf '実施済みであれば、記録してから先へ進んでください。\n'
    for stage in "${missing[@]+"${missing[@]}"}" "${conditional[@]+"${conditional[@]}"}"; do
      printf '  bash "$SCRIPTS/projects-sync.sh" %s stage "%s"\n' "$issue" "$stage"
    done
  fi
  return 0
}

wf_report_empty() {
  printf '#%s の進行の記録がありません。\n' "${1:-}"
}

# 設計 Pull Request のマージの判定は、通信を行う唯一の層として別のファイルへ置く。
# shellcheck source=workflow-merge.sh
. "$(dirname "${BASH_SOURCE[0]}")/workflow-merge.sh"
