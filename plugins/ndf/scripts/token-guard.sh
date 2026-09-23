#!/usr/bin/env bash
# NDF plugin: 待ちの呼び出しと、文脈が上限を超えた conductor の工程の起動を止める
# PreToolUse hook（#829 / #830）。Claude Code にだけ登録する。
#
# | tool_name          | 判定                                                       |
# | ------------------ | ---------------------------------------------------------- |
# | Bash               | 前景の `sleep` で待つ（ループの本体にあるか、上限を超える）  |
# | Read               | 変わらないファイルの同じ範囲を続けて読み直す                 |
# | Skill / Agent・Task | 文脈が上限を超えた conductor が工程へ入る                   |
#
# **拒否は `permissionDecision: deny` で返し、終了コードは常に 0 にする。** 通すときは何も
# 出さない。判定が失敗したとき（入力が読めない・jq が無い・控えを書けない・記録を読めない・
# ロックを 1 秒で取れない）は通す。hook の失敗でツールの実行を止めないためである。
#
# 規約は skills/development-workflow/references/waiting.md（待ち方）と
# context-window.md（会話を切る）にある。

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || exit 0
WAITING_DOC="development-workflow/references/waiting.md"
CONTEXT_DOC="development-workflow/references/context-window.md"

command -v jq >/dev/null 2>&1 || exit 0
INPUT=$(cat) || exit 0
TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
[ -n "$TOOL" ] || exit 0

field() {
  printf '%s' "$INPUT" | jq -r "$1 // empty | tostring" 2>/dev/null
}

deny() {
  jq -cn --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",
    permissionDecision:"deny", permissionDecisionReason:$r}}'
  exit 0
}

# 控えの置き場所。順は workflow-common.sh の wf_state_dir と同じ（あちらは stages/、
# こちらは guards/ を置く）。workflow-common.sh は通信の層まで読み込むため、毎回の hook
# では読み込まない。
guards_dir() {
  local base fallback="${TMPDIR:-/tmp}/ndf-guards"
  if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    base="$CLAUDE_PLUGIN_DATA/guards"
  elif [ -n "${XDG_STATE_HOME:-}" ]; then
    base="$XDG_STATE_HOME/ndf/guards"
  elif [ -n "${HOME:-}" ]; then
    base="$HOME/.local/state/ndf/guards"
  else
    base="$fallback"
  fi
  if mkdir -p "$base" 2>/dev/null && [ -w "$base" ]; then
    printf '%s\n' "$base"
    return 0
  fi
  mkdir -p "$fallback" 2>/dev/null && [ -w "$fallback" ] || return 1
  printf '%s\n' "$fallback"
}

# session ごとのロックを取る。取れなければ 1（呼び出し側は判定せずに通す）。
LOCK=
take_lock() {
  local dir="$1" sid="$2"
  # shellcheck source=lib/lock-common.sh
  . "$HERE/lib/lock-common.sh" 2>/dev/null || return 1
  LOCK="$dir/$sid.lock"
  ndf_lock_acquire "$LOCK" 1 || { LOCK=; return 1; }
  trap 'ndf_lock_release "$LOCK"' EXIT
}

# 置き換えで書く。途中で落ちても壊れた JSON を残さない。
write_json() {
  local path="$1" body="$2" tmp
  tmp="$path.$$.tmp"
  printf '%s\n' "$body" >"$tmp" 2>/dev/null && mv -f "$tmp" "$path" 2>/dev/null
  find "$(dirname "$path")" -maxdepth 1 -type f -name '*.json' -mtime +7 -delete 2>/dev/null
  return 0
}

guard_sleep() {
  [ "${NDF_SLEEP_GUARD:-1}" = 0 ] && exit 0
  [ "$(field '.tool_input.run_in_background')" = true ] && exit 0
  local cmd max
  cmd=$(field '.tool_input.command')
  [ -n "$cmd" ] || exit 0
  case "$cmd" in *sleep*) ;; *) exit 0 ;; esac
  command -v python3 >/dev/null 2>&1 || exit 0
  max=${NDF_SLEEP_MAX_SEC:-5}
  printf '%s' "$cmd" | python3 "$HERE/lib/token_guard_sleep.py" "$max" >/dev/null 2>&1 && exit 0
  deny "前景で sleep を使って待つと、待つ呼び出しのたびに会話の文脈の全体を読み直す（ループの本体の sleep と、${max} 秒を超える sleep を止めている）。同じ条件の until ループ（例: until [ -s <ファイル> ]; do sleep 5; done）を Bash の run_in_background: true で起動し、完了通知を待つ（通知は 1 回で、待つ間は呼び出しが増えない）。出来事を 1 つずつ受けるなら Monitor を使う。規約: ${WAITING_DOC}（止めるなら NDF_SLEEP_GUARD=0）"
}

file_stat() {
  # 大きさ・更新時刻（ナノ秒）・inode。無いファイルは -1。
  stat -c '%s %.9Y %i' "$1" 2>/dev/null || stat -f '%z %Fm %i' "$1" 2>/dev/null || echo "-1 -1 -1"
}

guard_read() {
  [ "${NDF_READ_REPEAT_GUARD:-1}" = 0 ] && exit 0
  local sid path key limit dir st size mtime inode prev count state
  sid=$(field '.session_id')
  path=$(field '.tool_input.file_path')
  [ -n "$sid" ] && [ -n "$path" ] || exit 0
  key="$path"$'\t'"$(field '.tool_input.offset')"$'\t'"$(field '.tool_input.limit')"
  limit=${NDF_READ_REPEAT_LIMIT:-3}
  dir=$(guards_dir) || exit 0
  take_lock "$dir" "$sid" || exit 0
  read -r size mtime inode <<<"$(file_stat "$path")"
  state="$dir/read-$sid.json"
  prev=$(jq -r --arg k "$key" --arg s "$size" --arg m "$mtime" --arg i "$inode" \
    'if .key == $k and (.size|tostring) == $s and .mtime == $m and (.inode|tostring) == $i
     then .count else 0 end' "$state" 2>/dev/null) || prev=0
  count=$(( ${prev:-0} + 1 ))
  write_json "$state" "$(jq -cn --arg k "$key" --argjson s "$size" --arg m "$mtime" \
    --argjson i "$inode" --argjson c "$count" \
    '{key:$k, size:$s, mtime:$m, inode:$i, count:$c}')"
  [ "$count" -ge "$limit" ] || exit 0
  deny "同じファイルの同じ範囲を、変わらないまま ${count} 回続けて読もうとした（${path}）。書き終わりを待つなら until [ -s <ファイル> ]; do sleep 1; done を Bash の run_in_background: true で起動して完了通知を待つか、背景の処理そのものの完了通知を待つ。サブエージェントの tasks/*.output は読まずに完了通知を待つ。規約: ${WAITING_DOC}（止めるなら NDF_READ_REPEAT_GUARD=0）"
}

# 最後の assistant 行の usage から文脈量を読む。末尾だけを読むのは大きな記録でも速く終えるため。
context_tokens() {
  tail -n 200 "$1" 2>/dev/null | jq -rs '
    [ .[] | select(.type == "assistant" and (.message.usage | type) == "object")
      | .message.usage
      | (.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0)
    ] | last // empty' 2>/dev/null
}

guard_context() {
  [ "${NDF_CONTEXT_GUARD:-1}" = 0 ] && exit 0
  # サブエージェントの中の起動は見ない。agent_id はサブエージェントの中でだけ付く
  # （Claude Code 2.1.280 で実測。サブエージェントの transcript_path は親の記録を指す）
  [ -n "$(field '.agent_id')" ] && exit 0
  local tp sid key words total limit dir mark issues skill
  tp=$(field '.transcript_path')
  case "$tp" in */subagents/*) exit 0 ;; esac
  sid=$(field '.session_id')
  [ -n "$tp" ] && [ -n "$sid" ] || exit 0
  if [ "$TOOL" = Skill ]; then
    skill=$(field '.tool_input.skill')
    skill=${skill#ndf:}
    grep -qxF "$skill" "$HERE/lib/token-guard-stages.txt" 2>/dev/null || exit 0
    words=$(field '.tool_input.args')
    key="skill"$'\t'"$skill"$'\t'"$words"
  else
    words=$(field '.tool_input.description')
    case "${words%%:*}" in 設計|実装|検査|取り込み|仕上げ) ;; *) exit 0 ;; esac
    case "$words" in *:*) ;; *) exit 0 ;; esac
    key="agent"$'\t'"$words"
  fi
  total=$(context_tokens "$tp")
  case "$total" in ''|*[!0-9]*) exit 0 ;; esac
  limit=${NDF_CONTEXT_LIMIT:-200000}
  dir=$(guards_dir) || exit 0
  take_lock "$dir" "$sid" || exit 0
  mark="$dir/context-$sid.json"
  if [ "$(jq -r '.key // empty' "$mark" 2>/dev/null)" = "$key" ]; then
    rm -f "$mark" 2>/dev/null
    exit 0
  fi
  [ "$total" -gt "$limit" ] || exit 0
  write_json "$mark" "$(jq -cn --arg k "$key" '{key:$k}')"
  # 日付・版数・小数（2026-09-23 / v10.16.1 / 2.0.3）は課題番号ではないので先に取り除く。
  # 範囲（#829-830 / 829-830）は残し、#829 #830 として案内する
  issues=$(printf '%s\n' "$words" | sed -E 's/[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}//g; s/[0-9]+(\.[0-9]+)+//g' | grep -oE '(^|[^0-9A-Za-z_/])#?[0-9]+\b' \
    | grep -oE '[0-9]+' | sed 's/^/#/' | tr '\n' ' ')
  issues=${issues% }
  deny "会話の文脈が ${total} トークンで、上限 ${limit} を超えた。この工程は新しい会話で始める。利用者へ次の 1 行を示して応答を終える: /ndf:development-workflow ${issues:-<課題番号>}（3 層で進めているなら、新しい会話で /goal に同じ 1 行を渡す）。<課題番号> のままなら、進めている課題の番号を補って示す。このまま続けると利用者が決めたら、同じ起動をもう一度行うと 1 度だけ通る。規約: ${CONTEXT_DOC}（止めるなら NDF_CONTEXT_GUARD=0、上限は NDF_CONTEXT_LIMIT）"
}

case "$TOOL" in
  Bash) guard_sleep ;;
  Read) guard_read ;;
  Skill|Agent|Task) guard_context ;;
esac
exit 0
