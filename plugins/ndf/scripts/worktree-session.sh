#!/usr/bin/env bash
# NDF plugin: セッション開始時に、主ディレクトリの逸脱を提示してブランチを追従させる。
#
# 結線先はセッション開始時の hook (Claude Code / Codex CLI の SessionStart、
# Kiro CLI の agentSpawn)。判定はすべて共通ライブラリが持ち、この入口は入力の
# 受け取りと出力の整形だけを行う (詳細設計 06 の決定 8)。
#
# 追従は detached HEAD で行う (同 決定 4)。同じブランチを 2 つの作業ディレクトリへ
# checkout できないためで、detached HEAD ではコミットしてもブランチが動かない。
#
# 追従に失敗しても作業を止めない。依存コマンドが無い場合や入力が読めない場合も
# 含め、常に終了コード 0 で終わる。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/worktree-common.sh
. "$SCRIPT_DIR/lib/worktree-common.sh" 2>/dev/null || exit 0

command -v git >/dev/null 2>&1 || exit 0

PAYLOAD=$(cat 2>/dev/null || true)
CWD=""
EVENT=""
if [ -n "$PAYLOAD" ] && command -v jq >/dev/null 2>&1; then
  if printf '%s' "$PAYLOAD" | jq -e . >/dev/null 2>&1; then
    CWD=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
    EVENT=$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // empty' 2>/dev/null)
  fi
fi
if [ -n "$CWD" ] && [ -d "$CWD" ]; then
  cd "$CWD" 2>/dev/null || true
fi

MAIN_DIR=$(wt_main_dir) || exit 0
# 作業ツリーの中では何もしない。逸脱も追従も主ディレクトリの話である。
wt_in_worktree && exit 0
# 宣言が無いリポジトリでは何もしない (詳細設計 06 の決定 9)。
DECLARATION=$(wt_declaration "$MAIN_DIR") || exit 0

DIRTY=$(wt_dirty_paths "$MAIN_DIR")
DIRTY_COUNT=0
[ -n "$DIRTY" ] && DIRTY_COUNT=$(printf '%s\n' "$DIRTY" | grep -c '^' )

LISTING=$(wt_dev_worktrees "$MAIN_DIR")
if [ "$DIRTY_COUNT" -gt 0 ]; then
  DECISION=$(wt_follow_target "$LISTING" 1)
else
  DECISION=$(wt_follow_target "$LISTING" 0)
fi

MESSAGES=()

# --- 逸脱検知 ---------------------------------------------------------------

if [ "$DIRTY_COUNT" -gt 0 ]; then
  # 変更が多いときに一覧をそのまま渡すと、引数の長さの上限に触れ、文脈も
  # 埋めてしまう。先頭だけを見せて残りは件数で丸める。
  list=$(printf '%s\n' "$DIRTY" | head -n "$WT_DIRTY_LIST_MAX" | sed 's/^/  /')
  if [ "$DIRTY_COUNT" -gt "$WT_DIRTY_LIST_MAX" ]; then
    list="$list
  ... 他 $((DIRTY_COUNT - WT_DIRTY_LIST_MAX)) 件"
  fi
  MESSAGES+=("主ディレクトリに追跡対象の未コミット変更が ${DIRTY_COUNT} 件あります。

$list

開発の変更であれば、作業ツリーへ移してください。手順は /ndf:worktree の
「主ディレクトリに残った変更を移す」にあります。主ディレクトリのブランチは、
変更がある間は稼働中の作業ツリーへ追従しません。")
fi

# --- ブランチ追従 -----------------------------------------------------------

follow_to() {
  local target="$1" label="$2"
  if git -C "$MAIN_DIR" checkout -q --detach "$target" 2>/dev/null; then
    MESSAGES+=("主ディレクトリを ${label} が指すコミットへ detached HEAD で合わせました。")
  else
    MESSAGES+=("主ディレクトリを ${label} へ合わせられませんでした。作業は続けられます。")
  fi
}

case "$DECISION" in
  "detach "*)
    branch=${DECISION#detach }
    current=$(git -C "$MAIN_DIR" rev-parse HEAD 2>/dev/null)
    wanted=$(git -C "$MAIN_DIR" rev-parse "$branch" 2>/dev/null)
    if [ -n "$wanted" ] && [ "$current" != "$wanted" ]; then
      follow_to "$branch" "作業ツリーのブランチ $branch"
    fi
    ;;
  default)
    default_branch=$(wt_default_branch "$MAIN_DIR") || default_branch=""
    if [ -n "$default_branch" ]; then
      current_branch=$(git -C "$MAIN_DIR" symbolic-ref --short -q HEAD 2>/dev/null)
      if [ "$current_branch" != "$default_branch" ]; then
        current=$(git -C "$MAIN_DIR" rev-parse HEAD 2>/dev/null)
        wanted=$(git -C "$MAIN_DIR" rev-parse "$default_branch" 2>/dev/null)
        if [ -n "$wanted" ] && [ "$current" != "$wanted" ]; then
          follow_to "$default_branch" "既定ブランチ $default_branch"
        fi
      fi
    fi
    ;;
esac

[ "${#MESSAGES[@]}" -gt 0 ] || exit 0

CONTEXT=$(printf '%s\n\n' "${MESSAGES[@]}")

# 出力の形は事象で選ぶ。**平文と JSON を同時に書かない。**
# 両方を書くと標準出力全体が JSON として読めなくなり、Claude Code は平文と
# JSON の文字列表現をまとめて 1 つの本文として文脈へ積む（設計 05 は双方へ
# 書くとしていたが、実装時にこの重複が分かったため事象で分ける形へ改めた）。
#
#   SessionStart (Claude Code / Codex CLI) — JSON の additionalContext で渡す
#   agentSpawn (Kiro CLI) / 事象が読めない場合 — 標準出力へ平文で書く
case "$EVENT" in
  SessionStart|session_start)
    if command -v jq >/dev/null 2>&1; then
      jq -n --arg context "$CONTEXT" \
        '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $context}}'
    else
      printf '%s\n' "$CONTEXT"
    fi
    ;;
  *)
    printf '%s\n' "$CONTEXT"
    ;;
esac
exit 0
