#!/usr/bin/env bash
# NDF plugin: 主ディレクトリを編集しようとしたときに、作業ツリーで作業する旨を伝える。
#
# 結線先は 2 つある。
#   - tool 実行前の hook (Claude Code / Codex CLI): 編集先のパスを見た案内を
#     additionalContext でモデルへ渡す
#   - プロンプト送信時の hook (Kiro CLI): パスを見ない案内を標準出力へ書く
#
# このスクリプトは 3 ランタイム分の入力を 1 つの判定へ正規化する。実際に hook として
# 結ばれているランタイムは hooks/*.json と dev.kiro/install.sh の側が決める。
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
  local cached_cwd
  cached_cwd=$(jq -r '.resolved_from // empty' "$STATE_FILE" 2>/dev/null) || return 1
  [ "$cached_cwd" = "$CWD_NOW" ] || return 1
  MAIN_DIR=$(jq -r '.main_dir // empty' "$STATE_FILE" 2>/dev/null)
  IN_WORKTREE=$(jq -r 'if .in_worktree then 0 else 1 end' "$STATE_FILE" 2>/dev/null)
  HAS_DECLARATION=$(jq -r 'if .has_declaration then 1 else 0 end' "$STATE_FILE" 2>/dev/null)
  mapfile -t ALLOW_PATHS < <(jq -r '(.allow_paths // []) | .[]' "$STATE_FILE" 2>/dev/null)
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
  mapfile -t ALLOW_PATHS < <(wt_allow_paths "$decl")
  return 0
}

save_state() {
  [ -n "$STATE_FILE" ] || return 0
  local tmp
  tmp=$(mktemp "${STATE_FILE}.XXXXXX" 2>/dev/null) || return 0
  jq -n \
    --arg main_dir "$MAIN_DIR" \
    --arg resolved_from "$CWD_NOW" \
    --argjson in_worktree "$([ "$IN_WORKTREE" = 0 ] && echo true || echo false)" \
    --argjson has_declaration "$([ "$HAS_DECLARATION" = 1 ] && echo true || echo false)" \
    --argjson allow_paths "$(printf '%s\n' "${ALLOW_PATHS[@]+"${ALLOW_PATHS[@]}"}" | jq -R . | jq -s 'map(select(. != ""))')" \
    '{main_dir: $main_dir, resolved_from: $resolved_from, in_worktree: $in_worktree,
      has_declaration: $has_declaration, allow_paths: $allow_paths, notified: [],
      computed_at: (now | todate)}' >"$tmp" 2>/dev/null || { rm -f "$tmp"; return 0; }
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
# 結線しているランタイム (Claude Code / Codex CLI / Kiro CLI) の名前に加えて、
# 委譲先として動きうる CLI の名前も併記する。取りこぼすと案内が出ないだけで、
# 余分に並べても該当しなければ何も起きない。
targets=()
case "$TOOL" in
  Edit|MultiEdit|Write|NotebookEdit|fs_write|edit_file|write_file|apply_patch|str_replace_editor|replace)
    mapfile -t targets < <(
      jq_get '[.tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
               (.tool_input.edits[]?.file_path?), (.tool_input.operations[]?.path?)]
              | map(select(type == "string" and . != "")) | unique | .[]'
    )
    ;;
  Bash|shell|execute_bash|local_shell|run_command|run_shell_command)
    command_text=$(
      jq_get 'if (.tool_input.command | type) == "array" then (.tool_input.command | join(" "))
              elif (.tool_input.command | type) == "string" then .tool_input.command
              else empty end'
    )
    [ -n "$command_text" ] || exit 0
    mapfile -t targets < <(wt_extract_write_target "$command_text")
    ;;
  *)
    exit 0
    ;;
esac

[ "${#targets[@]}" -gt 0 ] || exit 0

# 主ディレクトリの保護対象にあたるものだけを残す。
flagged=()
for raw in "${targets[@]}"; do
  [ -n "$raw" ] || continue
  abs=$(wt_normalize_path "$raw" "$CWD_NOW") || continue
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

jq -n --arg summary "$summary" --arg context "$context" \
  '{systemMessage: $summary,
    hookSpecificOutput: {hookEventName: "PreToolUse", additionalContext: $context}}'
exit 0
