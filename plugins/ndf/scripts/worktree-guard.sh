#!/usr/bin/env bash
# NDF plugin: 主ディレクトリを編集しようとしたときに、作業ツリーで作業する旨を伝える。
#
# 結線先は 3 つある。
#   - tool 実行前の hook (Claude Code / Codex CLI): 編集先のパスを見た案内を
#     additionalContext でモデルへ渡す
#   - プロンプト送信時の hook (Kiro CLI): パスを見ない案内を標準出力へ書く
#   - tool 実行前の hook (agy): 案内をセッションの控えへ積み、次のモデル呼び出しの前に
#     worktree-session.sh が injectSteps で渡す。agy がこの事象でモデルへ文言を返す口は
#     拒否のときにしか働かないためである (詳細設計 #215 の決定 4)
#
# このスクリプトは 4 ランタイム分の入力を 1 つの判定へ正規化する。実際に hook として
# 結ばれているランタイムは hooks/*.json と dev.kiro/install.sh と dev.agy/hooks.json の
# 側が決める。
#
# 拒否の判定は返さない (詳細設計 06 の決定 1)。判定はすべて共通ライブラリが持ち、
# この入口は入力の受け取りと出力の整形だけを行う (同 決定 8)。
#
# 依存コマンドが無い場合や入力が読めない場合は、何も出力せず終了コード 0 で抜ける。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/worktree-common.sh
. "$SCRIPT_DIR/lib/worktree-common.sh" 2>/dev/null || exit 0

command -v jq >/dev/null 2>&1 || exit 0
command -v git >/dev/null 2>&1 || exit 0

PAYLOAD=$(cat 2>/dev/null || true)
[ -n "$PAYLOAD" ] || exit 0
printf '%s' "$PAYLOAD" | jq -e . >/dev/null 2>&1 || exit 0

jq_get() { printf '%s' "$PAYLOAD" | jq -r "$1" 2>/dev/null; }

EVENT=$(jq_get '.hook_event_name // empty')
TOOL=$(jq_get '.tool_name // empty')
CWD=$(jq_get '.cwd // empty')
SESSION=$(jq_get '.session_id // empty')

# agy は事象名を持たず、tool の名前と引数を toolCall へ入れる。作業ディレクトリは
# workspacePaths の先頭、セッションの識別子は conversationId にある。項目の名前が
# 他の 3 ランタイムと重ならないため、tool の名前がどちらに入っているかで判別できる。
AGY=0
if [ -z "$TOOL" ]; then
  agy_tool=$(jq_get '.toolCall.name // empty')
  if [ -n "$agy_tool" ]; then
    AGY=1
    TOOL="$agy_tool"
    [ -n "$CWD" ] || CWD=$(jq_get '.workspacePaths[0] // empty')
  fi
fi
[ -n "$SESSION" ] || SESSION=$(jq_get '.conversationId // empty')
[ -n "$SESSION" ] || SESSION="${KIRO_SESSION_ID:-}"

if [ -n "$CWD" ] && [ -d "$CWD" ]; then
  cd "$CWD" 2>/dev/null || true
fi
CWD_NOW=$(pwd -P)

# --- セッション状態の控え ---------------------------------------------------
# tool 実行のたびに git を起動しないための控え。解決したときの作業ディレクトリを
# 併せて記録し、一致するときだけ再利用する。

STATE_FILE=""
if [ -n "$SESSION" ]; then
  STATE_FILE="${TMPDIR:-/tmp}/ndf-worktree-$(printf '%s' "$SESSION" | tr -c 'A-Za-z0-9._-' '_').json"
fi

MAIN_DIR=""
IN_WORKTREE=1
HAS_DECLARATION=0
ALLOW_PATHS=()

load_state() {
  [ -n "$STATE_FILE" ] && [ -f "$STATE_FILE" ] || return 1
  local cached_cwd cached_stamp
  cached_cwd=$(jq -r '.resolved_from // empty' "$STATE_FILE" 2>/dev/null) || return 1
  [ "$cached_cwd" = "$CWD_NOW" ] || return 1
  MAIN_DIR=$(jq -r '.main_dir // empty' "$STATE_FILE" 2>/dev/null)
  [ -n "$MAIN_DIR" ] || return 1
  # 宣言ファイルは後から作られる（`worktree-setup.sh init`）。控えたままにすると、
  # 作った直後のセッションで案内が出ない。印が変わっていたら作り直す。
  cached_stamp=$(jq -r '.declaration_stamp // ""' "$STATE_FILE" 2>/dev/null)
  [ "$cached_stamp" = "$(wt_declaration_stamp "$MAIN_DIR")" ] || return 1
  IN_WORKTREE=$(jq -r 'if .in_worktree then 0 else 1 end' "$STATE_FILE" 2>/dev/null)
  HAS_DECLARATION=$(jq -r 'if .has_declaration then 1 else 0 end' "$STATE_FILE" 2>/dev/null)
  _wt_read_lines < <(jq -r '(.allow_paths // []) | .[]' "$STATE_FILE" 2>/dev/null)
  ALLOW_PATHS=("${WT_LINES[@]+"${WT_LINES[@]}"}")
  [ -n "$MAIN_DIR" ]
}

compute_state() {
  MAIN_DIR=$(wt_main_dir) || return 1
  if wt_in_worktree; then IN_WORKTREE=0; else IN_WORKTREE=1; fi
  local decl
  if decl=$(wt_declaration "$MAIN_DIR"); then
    HAS_DECLARATION=1
  else
    HAS_DECLARATION=0
    decl=""
  fi
  _wt_read_lines < <(wt_allow_paths "$decl")
  ALLOW_PATHS=("${WT_LINES[@]+"${WT_LINES[@]}"}")
  return 0
}

save_state() {
  [ -n "$STATE_FILE" ] || return 0
  local tmp
  tmp=$(mktemp "${STATE_FILE}.XXXXXX" 2>/dev/null) || return 0
  jq -n \
    --arg main_dir "$MAIN_DIR" \
    --arg resolved_from "$CWD_NOW" \
    --arg declaration_stamp "$(wt_declaration_stamp "$MAIN_DIR")" \
    --argjson in_worktree "$([ "$IN_WORKTREE" = 0 ] && echo true || echo false)" \
    --argjson has_declaration "$([ "$HAS_DECLARATION" = 1 ] && echo true || echo false)" \
    --argjson allow_paths "$(printf '%s\n' "${ALLOW_PATHS[@]+"${ALLOW_PATHS[@]}"}" | jq -R . | jq -s 'map(select(. != ""))')" \
    '{main_dir: $main_dir, resolved_from: $resolved_from, in_worktree: $in_worktree,
      has_declaration: $has_declaration, declaration_stamp: $declaration_stamp,
      allow_paths: $allow_paths, notified: [], pending: [], computed_at: (now | todate)}' >"$tmp" 2>/dev/null || { rm -f "$tmp"; return 0; }
  mv "$tmp" "$STATE_FILE" 2>/dev/null || rm -f "$tmp"
}

if ! load_state; then
  compute_state || exit 0
  save_state
fi

# 宣言が無いリポジトリでは何もしない (詳細設計 06 の決定 9)。
[ "$HAS_DECLARATION" = 1 ] || exit 0
# 作業ツリーの中では案内を出さない。
[ "$IN_WORKTREE" = 1 ] || exit 0

# --- プロンプト送信時 (Kiro CLI) --------------------------------------------

case "$EVENT" in
  userPromptSubmit|UserPromptSubmit)
    cat <<EOS
[ndf:worktree] 現在の作業ディレクトリは、リポジトリを clone した主ディレクトリです。
開発の変更は ${WT_WORKTREE_DIR}/<ブランチ名> の作業ツリーの中で行ってください。
作業ツリーの用意は /ndf:worktree の手順に従います。
issues/ や docs/ など、知識と設定の更新はこのままで構いません。
EOS
    exit 0
    ;;
esac

# --- tool 実行前 ------------------------------------------------------------

# ツール名はランタイムごとに違う。判定を 1 つに保つため、ここで種別へ正規化する。
# 対象の一覧は共通ライブラリの WT_EDIT_TOOLS / WT_PATCH_TOOLS / WT_SHELL_TOOLS が
# 持ち、hook の matcher も同じ一覧から作る。
targets=()
# 相対パスの起点。tool が実行ディレクトリを指定していればそちらを使う。
BASE_DIR="$CWD_NOW"
if [[ "$TOOL" =~ ^($WT_PATCH_TOOLS)$ ]]; then
  # Codex CLI はファイルの編集をパッチ本文で渡す。パスは本文の中にある。
  patch_text=$(
    jq_get 'if (.tool_input.command | type) == "string" then .tool_input.command
            elif (.tool_input.patch | type) == "string" then .tool_input.patch
            elif (.tool_input.input | type) == "string" then .tool_input.input
            else empty end'
  )
  [ -n "$patch_text" ] || exit 0
  _wt_read_lines < <(wt_extract_patch_target "$patch_text")
  targets=("${WT_LINES[@]+"${WT_LINES[@]}"}")
elif [[ "$TOOL" =~ ^($WT_EDIT_TOOLS)$ ]]; then
  _wt_read_lines < <(
    jq_get '[.tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
             (.tool_input.edits[]?.file_path?), (.tool_input.operations[]?.path?),
             .toolCall.args.TargetFile?]
            | map(select(type == "string" and . != "")) | unique | .[]'
  )
  targets=("${WT_LINES[@]+"${WT_LINES[@]}"}")
elif [[ "$TOOL" =~ ^($WT_SHELL_TOOLS)$ ]]; then
  command_text=$(
    jq_get 'if (.tool_input.command | type) == "array" then (.tool_input.command | join(" "))
            elif (.tool_input.command | type) == "string" then .tool_input.command
            elif (.toolCall.args.CommandLine | type) == "string" then .toolCall.args.CommandLine
            else empty end'
  )
  [ -n "$command_text" ] || exit 0
  # コマンドの実行ディレクトリを別に指定できるランタイムがある
  # （`run_shell_command` は tool_input.dir_path で渡す）。
  # 指定があれば、相対パスの起点をそちらへ合わせる。
  command_cwd=$(
    jq_get 'if (.tool_input.dir_path | type) == "string" then .tool_input.dir_path
            elif (.tool_input.cwd | type) == "string" then .tool_input.cwd
            elif (.tool_input.workdir | type) == "string" then .tool_input.workdir
            elif (.toolCall.args.Cwd | type) == "string" then .toolCall.args.Cwd
            else empty end'
  )
  if [ -n "$command_cwd" ]; then
    BASE_DIR=$(wt_normalize_path "$command_cwd" "$CWD_NOW")
  fi
  # 起点を渡す。渡さないと、同じコマンドの中の `cd` が反映されず、作業ツリーへ
  # 移ってからの相対パスが移動前の位置を指す。返る値は絶対パスになる。
  _wt_read_lines < <(wt_extract_write_target "$command_text" "$BASE_DIR")
  targets=("${WT_LINES[@]+"${WT_LINES[@]}"}")
else
  exit 0
fi

[ "${#targets[@]}" -gt 0 ] || exit 0

# 主ディレクトリの保護対象にあたるものだけを残す。
flagged=()
for raw in "${targets[@]}"; do
  [ -n "$raw" ] || continue
  abs=$(wt_normalize_path "$raw" "$BASE_DIR") || continue
  rel=$(wt_relative_to_main "$abs" "$MAIN_DIR") || continue
  [ "$rel" = "." ] && continue
  # 作業ツリーそのものへの書き込みは対象外。
  case "$rel" in "$WT_WORKTREE_DIR"/*) continue ;; esac
  if wt_is_allowed_path "$rel" "${ALLOW_PATHS[@]+"${ALLOW_PATHS[@]}"}"; then
    continue
  fi
  flagged+=("$rel")
done

[ "${#flagged[@]}" -gt 0 ] || exit 0

# 同じパスへの案内をこのセッションで繰り返さない。
if [ -n "$STATE_FILE" ] && [ -f "$STATE_FILE" ]; then
  fresh=()
  for rel in "${flagged[@]}"; do
    if jq -e --arg p "$rel" '(.notified // []) | index($p)' "$STATE_FILE" >/dev/null 2>&1; then
      continue
    fi
    fresh+=("$rel")
  done
  flagged=("${fresh[@]+"${fresh[@]}"}")
  [ "${#flagged[@]}" -gt 0 ] || exit 0

  tmp=$(mktemp "${STATE_FILE}.XXXXXX" 2>/dev/null)
  if [ -n "$tmp" ]; then
    if jq --argjson add "$(printf '%s\n' "${flagged[@]}" | jq -R . | jq -s .)" \
         '.notified = ((.notified // []) + $add | unique)' "$STATE_FILE" >"$tmp" 2>/dev/null; then
      mv "$tmp" "$STATE_FILE" 2>/dev/null || rm -f "$tmp"
    else
      rm -f "$tmp"
    fi
  fi
fi

list=$(printf '%s\n' "${flagged[@]}" | sed 's/^/  - /')
summary="主ディレクトリを編集しています。開発は ${WT_WORKTREE_DIR}/<ブランチ名> で行ってください（/ndf:worktree）"
context=$(cat <<EOS
この編集先は、リポジトリを clone した主ディレクトリです。

$list

開発の変更は作業ツリーの中で行います。まだ用意していなければ /ndf:worktree の手順で
${WT_WORKTREE_DIR}/<ブランチ名> に作り、そこへ移ってから編集してください。既に主ディレクトリで
変更を加えている場合は、同じ手順の移送で作業ツリー側へ移せます。

この編集を止めてはいません。意図した操作であればそのまま続けてください。
EOS
)

# agy はこの事象でモデルへ文言を返せない。案内は控えへ積み、次のモデル呼び出しの前に
# worktree-session.sh が取り出して渡す。ここで返すのは操作を止めない判定だけである。
# 控えを作れないとき (セッションの識別子が無いとき) は案内を落とす。案内が届かなくても
# 操作は成立し、逸脱は開始時の提示と作業ツリーの手順の側で拾える。
if [ "$AGY" = 1 ]; then
  if [ -n "$STATE_FILE" ] && [ -f "$STATE_FILE" ]; then
    tmp=$(mktemp "${STATE_FILE}.XXXXXX" 2>/dev/null)
    if [ -n "$tmp" ]; then
      if jq --arg add "$context" '.pending = ((.pending // []) + [$add])' "$STATE_FILE" >"$tmp" 2>/dev/null; then
        mv "$tmp" "$STATE_FILE" 2>/dev/null || rm -f "$tmp"
      else
        rm -f "$tmp"
      fi
    fi
  fi
  printf '%s\n' '{"decision": "allow"}'
  exit 0
fi

jq -n --arg summary "$summary" --arg context "$context" \
  '{systemMessage: $summary,
    hookSpecificOutput: {hookEventName: "PreToolUse", additionalContext: $context}}'
exit 0
