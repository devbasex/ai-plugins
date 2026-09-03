#!/usr/bin/env bash
# NDF plugin: セッション開始時に、主ディレクトリの逸脱を提示してブランチを追従させる。
#
# 結線先はセッション開始時の hook (Claude Code / Codex CLI の SessionStart、
# Kiro CLI の agentSpawn) と、agy のモデル呼び出し前の hook (PreInvocation)。判定は
# すべて共通ライブラリが持ち、この入口は入力の受け取りと出力の整形だけを行う
# (詳細設計 06 の決定 8)。
#
# **agy にはセッション開始時にあたる事象が無い。** PreInvocation はモデル呼び出しの
# たびに発火するため、通し番号 (invocationNum) が 0 のときだけ開始時の処理を行う
# (詳細設計 #215 の決定 5)。あわせて、tool 実行前の hook が控えへ積んだ案内をここで
# 取り出して渡す。agy はその事象でモデルへ文言を返せないためである (同 決定 4)。
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
AGY=0
INVOCATION=""
SESSION=""
if [ -n "$PAYLOAD" ] && command -v jq >/dev/null 2>&1; then
  if printf '%s' "$PAYLOAD" | jq -e . >/dev/null 2>&1; then
    CWD=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
    EVENT=$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // empty' 2>/dev/null)
    # agy は事象名を持たない。通し番号があることで PreInvocation だと判別できる。
    INVOCATION=$(printf '%s' "$PAYLOAD" \
      | jq -r 'if (.invocationNum | type) == "number" then .invocationNum else empty end' 2>/dev/null)
    if [ -n "$INVOCATION" ]; then
      AGY=1
      SESSION=$(printf '%s' "$PAYLOAD" | jq -r '.conversationId // empty' 2>/dev/null)
      [ -n "$CWD" ] || CWD=$(printf '%s' "$PAYLOAD" | jq -r '.workspacePaths[0] // empty' 2>/dev/null)
    fi
  fi
fi
if [ -n "$CWD" ] && [ -d "$CWD" ]; then
  cd "$CWD" 2>/dev/null || true
fi

# 案内が無いときの出口。agy は返す形が決まっているため空の JSON を書く。
# **宣言を持たないリポジトリでは何も書かない** (詳細設計 06 の決定 9)。
emit_nothing() {
  [ "$AGY" = 1 ] && [ "${DECLARED:-0}" = 1 ] && printf '%s\n' '{}'
  exit 0
}

DECLARED=0
MAIN_DIR=$(wt_main_dir) || emit_nothing
# 宣言が無いリポジトリでは何もしない (詳細設計 06 の決定 9)。
DECLARATION=$(wt_declaration "$MAIN_DIR") || emit_nothing
DECLARED=1
# 作業ツリーの中では何もしない。逸脱も追従も主ディレクトリの話である。
wt_in_worktree && emit_nothing

# tool 実行前の hook が積んだ案内を取り出し、控えを空にする。取り出した案内を残すと、
# モデル呼び出しのたびに同じ案内を渡すことになる。
STATE_FILE=""
if [ "$AGY" = 1 ] && [ -n "$SESSION" ]; then
  STATE_FILE="${TMPDIR:-/tmp}/ndf-worktree-$(printf '%s' "$SESSION" | tr -c 'A-Za-z0-9._-' '_').json"
fi

drain_pending() {
  [ -n "$STATE_FILE" ] && [ -f "$STATE_FILE" ] || return 0
  local text tmp
  text=$(jq -r '(.pending // []) | join("\n\n")' "$STATE_FILE" 2>/dev/null) || return 0
  [ -n "$text" ] || return 0
  tmp=$(mktemp "${STATE_FILE}.XXXXXX" 2>/dev/null)
  if [ -n "$tmp" ]; then
    if jq '.pending = []' "$STATE_FILE" >"$tmp" 2>/dev/null; then
      mv "$tmp" "$STATE_FILE" 2>/dev/null || rm -f "$tmp"
    else
      rm -f "$tmp"
    fi
  fi
  printf '%s' "$text"
}

# 開始時にあたるかどうか。agy はモデル呼び出しのたびに発火するため、通し番号が 0 の
# ときだけ開始時の処理を行う。他の 3 ランタイムは開始時にしか発火しない。
AT_START=1
[ "$AGY" = 1 ] && [ "$INVOCATION" != 0 ] && AT_START=0

DIRTY_COUNT=0
DIRTY=""
DECISION=""
if [ "$AT_START" = 1 ]; then
  DIRTY=$(wt_dirty_paths "$MAIN_DIR")
  [ -n "$DIRTY" ] && DIRTY_COUNT=$(printf '%s\n' "$DIRTY" | grep -c '^' )

  LISTING=$(wt_dev_worktrees "$MAIN_DIR")
  if [ "$DIRTY_COUNT" -gt 0 ]; then
    DECISION=$(wt_follow_target "$LISTING" 1)
  else
    DECISION=$(wt_follow_target "$LISTING" 0)
  fi
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

# ブランチ名が指すコミットを出力する。解決できなければ 1 を返す。
#
# **名前をそのまま `git rev-parse` へ渡さない。** 解決の順序に
# `refs/remotes/origin/<名前>` は含まれないため、ローカルに同名のブランチが無いと
# 名前だけでは解決できない。しかもそのとき `rev-parse` は渡した名前を標準出力へ
# そのまま書いて失敗するため、出力が空かどうかでは失敗を拾えない。参照名を組み立て、
# ローカル → 取得済みの追跡参照の順に確かめる。
resolve_commit() {
  local name="$1" ref sha
  for ref in "refs/heads/$name" "refs/remotes/origin/$name"; do
    sha=$(git -C "$MAIN_DIR" rev-parse --verify --quiet "$ref^{commit}" 2>/dev/null) || continue
    [ -n "$sha" ] || continue
    printf '%s\n' "$sha"
    return 0
  done
  return 1
}

# 起点が指すコミットを出力する。取得済みの参照で解決できないときは origin から取る。
#
# `wt_base_branch` は origin にあるだけで取得していないブランチも起点として返す。
# 起点を移した直後の主ディレクトリがこの状態になり、取得済みの参照だけを見ていると
# 追従が黙って起きない。この経路は `wt_base_branch` が既に origin へ問い合わせて
# いるため、通信が増えるのは元から通信していた場合だけである。取得できないときは
# 追従しない（案内は編集時の hook と作業ツリーの手順の側で出す）。
resolve_base_commit() {
  local name="$1" sha
  sha=$(resolve_commit "$name") && { printf '%s\n' "$sha"; return 0; }
  GIT_TERMINAL_PROMPT=0 git -C "$MAIN_DIR" fetch -q --no-tags origin \
    "refs/heads/$name" >/dev/null 2>&1 || return 1
  sha=$(git -C "$MAIN_DIR" rev-parse --verify --quiet 'FETCH_HEAD^{commit}' 2>/dev/null) || return 1
  [ -n "$sha" ] || return 1
  printf '%s\n' "$sha"
}

case "$DECISION" in
  "detach "*)
    branch=${DECISION#detach }
    current=$(git -C "$MAIN_DIR" rev-parse HEAD 2>/dev/null)
    wanted=$(resolve_commit "$branch") || wanted=""
    if [ -n "$wanted" ] && [ "$current" != "$wanted" ]; then
      follow_to "$wanted" "作業ツリーのブランチ $branch"
    fi
    ;;
  default)
    # 合わせる先は開発の起点であって、既定ブランチとは限らない。解決できない
    # ときは追従しない。案内はセッション開始時の出力へ混ぜず、編集時の hook と
    # 作業ツリーの手順の側で出す。
    base_branch=$(wt_base_branch "$MAIN_DIR" 2>/dev/null) || base_branch=""
    if [ -n "$base_branch" ]; then
      current_branch=$(git -C "$MAIN_DIR" symbolic-ref --short -q HEAD 2>/dev/null)
      if [ "$current_branch" != "$base_branch" ]; then
        current=$(git -C "$MAIN_DIR" rev-parse HEAD 2>/dev/null)
        wanted=$(resolve_base_commit "$base_branch") || wanted=""
        if [ -n "$wanted" ] && [ "$current" != "$wanted" ]; then
          follow_to "$wanted" "起点ブランチ $base_branch"
        fi
      fi
    fi
    ;;
esac

# 控えへ積まれた案内を最後に足す。開始時の提示より後ろへ置くのは、直前の操作への案内が
# 読み手にとって新しい情報だからである。
if [ "$AGY" = 1 ]; then
  PENDING=$(drain_pending)
  [ -n "$PENDING" ] && MESSAGES+=("$PENDING")
fi

[ "${#MESSAGES[@]}" -gt 0 ] || emit_nothing

CONTEXT=$(printf '%s\n\n' "${MESSAGES[@]}")

# 出力の形は事象で選ぶ。**平文と JSON を同時に書かない。**
# 両方を書くと標準出力全体が JSON として読めなくなり、Claude Code は平文と
# JSON の文字列表現をまとめて 1 つの本文として文脈へ積む。
#
#   SessionStart (Claude Code / Codex CLI) — JSON の additionalContext で渡す
#   agentSpawn (Kiro CLI) / 事象が読めない場合 — 標準出力へ平文で書く
#   PreInvocation (agy) — injectSteps の userMessage で渡す
if [ "$AGY" = 1 ]; then
  jq -n --arg context "$CONTEXT" '{injectSteps: [{userMessage: $context}]}'
  exit 0
fi

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
