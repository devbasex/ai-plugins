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
# それ以外は必須である。食い違いは tests/test_workflow_stage_matrix.py が拾う。
WF_MODES=$'light\tstandard\tarchitecture\tlegacy-refactor'
WF_STAGE_MATRIX=$'作業場所の用意\tC\tR\tR\tR
要求と受け入れ条件\t-\tR\tR\t-
設計\t-\tR\tR\tR
設計レビュー\t-\tR\tR\tC
計画\t-\tR\tR\tR
実装\tR\tR\tR\tR
構造改善\t-\tR\tR\tR
レビュー\t-\tR\tR\tR
完了判定\tR\tR\tR\tR
Pull Request\tR\tR\tR\tR
確定仕様化\t-\tC\tR\t-
後片付け\tR\tR\tR\tR
配布\tR\tR\tR\tR
リリース後テスト\t-\tC\tR\tR
振り返り\t-\tR\tR\tR'

# 報告の引き金になる工程。ここへ進んだ時点で、記録の無い必須の工程を案内する。
WF_REPORT_STAGE='配布'
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
    IFS=$'\t' read -r name c1 c2 c3 c4 <<<"$line"
    [ "$name" = "$stage" ] || continue
    case "$column" in
      1) printf '%s\n' "$c1" ;; 2) printf '%s\n' "$c2" ;;
      3) printf '%s\n' "$c3" ;; 4) printf '%s\n' "$c4" ;;
    esac
    return 0
  done <<<"$WF_STAGE_MATRIX"
  return 1
}

# --- 引用符を解いた語の分割 --------------------------------------------------
# **展開はしない。** コマンドの本文を判定するだけで、実行はこの hook の役目ではない。
# `$SCRIPTS` のような未展開の変数はそのままの文字列として残る。
#
# **bash の文字取り出しでは書かない。** tool 実行のたびに走るため、長い本文で費用が
# 効く。実測では 27KB の本文に 2.4 秒かかり、hook の制限時間に近づいた。同じ処理を
# awk に置くと 0.02 秒で終わる。
wf_split() {
  awk '
    {
      n = length($0)
      for (i = 1; i <= n; i++) {
        ch = substr($0, i, 1)
        if (quote != "") {
          if (ch == quote) { quote = "" } else { out = out ch }
          continue
        }
        if (ch == "\"" || ch == "'"'"'") { quote = ch; continue }
        if (ch == " " || ch == "\t") {
          if (out != "") { print out; out = "" }
          continue
        }
        out = out ch
      }
      if (quote != "") { out = out "\n" }
      else if (out != "") { print out; out = "" }
    }
    END { if (out != "") print out }
  ' <<<"${1:-}"
}

# 判定の対象になりうる本文かを、走査の前に安く見分ける。
# **当たらない本文では語の分割そのものを行わない。**
wf_is_candidate() {
  grep -qE 'projects-sync\.sh|pr[[:space:]]+merge|pulls/[0-9]+/merge' <<<"${1:-}"
}

# 進行の記録のコマンドなら、課題番号・キー・値をタブ区切りで出す。
#
# 見分けは `projects-sync.sh` で終わる語である。呼び出し側は `$SCRIPTS` を展開してから
# 実行するが、hook が受け取るのは書かれたままの本文なので、どちらの形でも当たる。
wf_parse_sync() {
  local cmd="${1:-}" tok found=1
  local -a args=()
  while IFS= read -r tok; do
    if [ "$found" -ne 0 ]; then
      case "$tok" in *projects-sync.sh) found=0 ;; esac
      continue
    fi
    args+=("$tok")
  done < <(wf_split "$cmd")
  [ "$found" -eq 0 ] || return 1
  [ "${#args[@]}" -ge 3 ] || return 1
  printf '%s\t%s\t%s\n' "${args[0]}" "${args[1]}" "${args[2]}"
}

# --- JSON の組み立て --------------------------------------------------------
# jq が無くても出力できるようにする。#266 は jq が無いときも拒否を返すため、
# 出力の側が jq に依存すると、拒否そのものを届けられない。
wf_json_escape() {
  local s="${1:-}" out="" ch i len=0
  len=${#s}
  for (( i = 0; i < len; i++ )); do
    ch=${s:i:1}
    case "$ch" in
      '\') out+='\\' ;;
      '"') out+='\"' ;;
      $'\n') out+='\n' ;;
      $'\t') out+='\t' ;;
      $'\r') out+='\r' ;;
      *) out+="$ch" ;;
    esac
  done
  printf '%s' "$out"
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
wf_repo_slug() {
  local dir="${1:-.}" url slug
  command -v git >/dev/null 2>&1 || return 1
  url=$(git -C "$dir" config --get remote.origin.url 2>/dev/null) || return 1
  [ -n "$url" ] || return 1
  slug=${url%.git}
  slug=${slug%/}
  slug=${slug##*:}          # git@github.com:owner/repo
  case "$slug" in */*) ;; *) return 1 ;; esac
  local repo owner
  repo=${slug##*/}
  owner=${slug%/*}
  owner=${owner##*/}
  [ -n "$owner" ] && [ -n "$repo" ] || return 1
  printf '%s/%s\n' "$owner" "$repo"
}

# 控えの置き場所。リポジトリの中には置かない（変更として Pull Request に載るため）。
wf_state_dir() {
  local base
  if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    base="$CLAUDE_PLUGIN_DATA/stages"
  elif [ -n "${XDG_STATE_HOME:-}" ]; then
    base="$XDG_STATE_HOME/ndf/stages"
  elif [ -n "${HOME:-}" ]; then
    base="$HOME/.local/state/ndf/stages"
  else
    base="${TMPDIR:-/tmp}/ndf-stages"
  fi
  if mkdir -p "$base" 2>/dev/null && [ -w "$base" ]; then
    printf '%s\n' "$base"
    return 0
  fi
  base="${TMPDIR:-/tmp}/ndf-stages"
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

# 通過工程を報告する。**終了コードで工程を止めない。**
wf_report() {
  local slug="${1:-}" issue="${2:-}" file content mode stage class frontier index=0
  local -a recorded=() present=() missing=() conditional=()

  command -v jq >/dev/null 2>&1 || { wf_report_empty "$issue"; return 0; }
  file=$(wf_state_file "$slug" "$issue") || { wf_report_empty "$issue"; return 0; }
  content=$(wf_state_read "$file")
  while IFS= read -r stage; do
    [ -n "$stage" ] && recorded+=("$stage")
  done < <(_wf_recorded "$content")
  if [ "${#recorded[@]}" -eq 0 ]; then
    wf_report_empty "$issue"
    return 0
  fi
  mode=$(jq -r '.mode // empty' <<<"$content" 2>/dev/null)
  frontier=$(_wf_frontier "${recorded[@]}")

  while IFS= read -r stage; do
    index=$((index + 1))
    [ "$index" -le "$frontier" ] || break
    if _wf_contains "$stage" "${recorded[@]}"; then
      present+=("$stage")
      continue
    fi
    [ -n "$mode" ] || continue
    class=$(wf_stage_class "$mode" "$stage") || continue
    case "$class" in
      R) missing+=("$stage") ;;
      C) conditional+=("$stage") ;;
    esac
  done < <(wf_stages)

  if [ -n "$mode" ]; then
    printf '#%s の通過工程（%s）\n' "$issue" "$mode"
  else
    printf '#%s の通過工程（モード不明）\n' "$issue"
  fi
  printf '  記録あり: %s\n' "$(wf_join "${present[@]+"${present[@]}"}")"
  [ "${#missing[@]}" -gt 0 ] && printf '  記録なし: %s\n' "$(wf_join "${missing[@]}")"
  [ "${#conditional[@]}" -gt 0 ] && printf '  条件付き: %s\n' "$(wf_join "${conditional[@]}")"
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
