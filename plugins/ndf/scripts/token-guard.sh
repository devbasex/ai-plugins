#!/usr/bin/env bash
# NDF plugin: 待ちの呼び出しと、文脈が上限を超えた conductor の工程の起動を止める
# PreToolUse hook（#829 / #830）。Claude Code にだけ登録する。
#
# | tool_name          | 判定                                                       |
# | ------------------ | ---------------------------------------------------------- |
# | Bash               | 前景の `sleep` で待つ（ループの本体にあるか、上限を超える）  |
# | Bash               | 文脈が上限を超えた conductor が supervise.py queue / run でプランを起こす |
# | Read               | 変わらないファイルの同じ範囲を続けて読み直す                 |
# | Skill / Agent・Task | 文脈が上限を超えた conductor が工程へ入る                   |
# | Agent・Task        | supervisor の起動に、プランで流せることを案内する（止めない） |
# | Skill              | 寿命 5 分の supervisor が文脈を伸ばしたまま収束ループを始める |
#
# **拒否は `permissionDecision: deny` で返し、終了コードは常に 0 にする。** 案内は
# `additionalContext` で返す。通すときは何も出さない。判定が失敗したとき（入力が読めない・jq が無い・記録を書けない・記録を読めない・
# ロックを待ちの上限の内に取れない）は通す。hook の失敗でツールの実行を止めないためである。
# 待ちの上限は `NDF_TOKEN_GUARD_LOCK_WAIT`（秒・0 以上の整数。既定 1）で変えられる。
# 本番は既定の 1 秒のままにする。延ばすのは負荷の高い環境で並列の試験を動かすときである（#950）。
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

# 記録の置き場所。順は workflow-common.sh の wf_state_dir と同じ（あちらは stages/、
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
  local wait="${NDF_TOKEN_GUARD_LOCK_WAIT:-1}"
  case "$wait" in '' | *[!0-9]*) wait=1 ;; esac
  LOCK="$dir/$sid.lock"
  ndf_lock_acquire "$LOCK" "$wait" || { LOCK=; return 1; }
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
  deny "前景で sleep を使って待つと、待つ呼び出しのたびに会話の文脈の全体を読み直す（ループの本体の sleep と、${max} 秒を超える sleep を止めている）。待つ相手に NDF のスクリプトがあればそれを Bash の run_in_background: true で起動し、完了通知を待つ（通知は 1 回で、待つ間は呼び出しが増えない）。queue の終わり: python3 $HERE/supervise.py wait <done のパス>（途中の知らせで終了コード 20 で返るので、中身を読んで待ち直す）。PR の CI を待ってマージ（マージの承認を得た後に限る。緑ならそのまま --admin でマージする）: python3 $HERE/merged-steps.py merge-when-green <PR 番号>。CI を待つだけなら gh pr checks <PR 番号> --watch を同じく背景で起動する。どちらでもなければ同じ条件の until ループ（例: until [ -s <ファイル> ]; do sleep 5; done）を同じく背景で起動する。出来事を 1 つずつ受けるなら Monitor を使う。規約: ${WAITING_DOC}（止めるなら NDF_SLEEP_GUARD=0）"
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
  deny "同じファイルの同じ範囲を、変わらないまま ${count} 回続けて読もうとした（${path}）。queue の終わりを待つなら python3 $HERE/supervise.py wait <done のパス> を、それ以外の書き終わりを待つなら until [ -s <ファイル> ]; do sleep 1; done を Bash の run_in_background: true で起動して完了通知を待つか、背景の処理そのものの完了通知を待つ。サブエージェントの tasks/*.output は読まずに完了通知を待つ。規約: ${WAITING_DOC}（止めるなら NDF_READ_REPEAT_GUARD=0）"
}

# 最後の assistant 行の usage から文脈量を読む。末尾だけを読むのは大きな記録でも速く終えるため。
context_tokens() {
  tail -n 200 "$1" 2>/dev/null | jq -rs '
    [ .[] | select(.type == "assistant" and (.message.usage | type) == "object")
      | .message.usage
      | (.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0)
    ] | last // empty' 2>/dev/null
}

# ラッパーの判定と告知の文面を relay.py notice の 1 回の起動で得る（1 行目が判定、2 行目が告知。#980）。
# ラッパーの下なら告知を出して 0、外なら 1 を返す
relay_notice() {
  local out
  out=$(python3 "$HERE/relay.py" notice 2>/dev/null) || return 1
  [ "${out%%$'\n'*}" = relay ] || return 1
  printf '%s\n' "$out" | sed -n 2p
}

# 日付・版数・小数（2026-09-23 / v10.16.1 / 2.0.3）は課題番号ではないので先に取り除く。
# 範囲（#829-830 / 829-830）は残し、#829 #830 として案内する
issue_refs() {
  local refs
  refs=$(printf '%s\n' "$1" | sed -E 's/[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}//g; s/[0-9]+(\.[0-9]+)+//g' | grep -oE '(^|[^0-9A-Za-z_/])#?[0-9]+\b' \
    | grep -oE '[0-9]+' | sed 's/^/#/' | tr '\n' ' ')
  printf '%s' "${refs% }"
}

# Bash のコマンドが supervise.py の queue か run（プランを起こす副命令）を起動するなら 0。
# 区切り（; & | 括弧 改行）で分けた各コマンドの先頭で起動しているものだけを見る。
# echo の引数やコメントの中の文字列はコマンドの先頭に来ないので拾わない
plan_command() {
  local line re='^[[:space:]]*(env[[:space:]]+|nohup[[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*([^[:space:]]*python3?[[:space:]]+)?[^[:space:]]*supervise\.py['"'"'"]?[[:space:]]+(queue|run)([[:space:]]|$)'
  while IFS= read -r line; do
    [[ "$line" =~ $re ]] && return 0
  done <<< "${1//[;&|()]/$'\n'}"
  return 1
}

guard_context() {
  [ "${NDF_CONTEXT_GUARD:-1}" = 0 ] && exit 0
  # サブエージェントの中の起動は見ない。agent_id はサブエージェントの中でだけ付く
  # （Claude Code 2.1.280 で実測。サブエージェントの transcript_path は親の記録を指す）
  [ -n "$(field '.agent_id')" ] && exit 0
  local tp sid key words total limit dir mark issues skill relayed notice
  tp=$(field '.transcript_path')
  case "$tp" in */subagents/*) exit 0 ;; esac
  sid=$(field '.session_id')
  [ -n "$tp" ] && [ -n "$sid" ] || exit 0
  if [ "$TOOL" = Bash ]; then
    # プランを起こす副命令（queue / run）だけを見る。背景での起動も同じに扱う
    words=$(field '.tool_input.command')
    plan_command "$words" || exit 0
    key="plan"$'\t'"$words"
    words=
  elif [ "$TOOL" = Skill ]; then
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
  # ラッパー（relay.py）の直接の子の conductor では 1 度の通しをやめ、上限を超えている限り止め
  # 続ける。人が居ない前提で LLM が「続ける」と決めて上限を超えたまま進むことを止める（#895）
  relayed=0
  notice=
  if [ -n "${NDF_RELAY_DIR:-}" ] && [ "$total" -gt "$limit" ] && command -v python3 >/dev/null 2>&1; then
    if notice=$(relay_notice); then relayed=1; fi
  fi
  if [ "$relayed" = 0 ] && [ "$(jq -r '.key // empty' "$mark" 2>/dev/null)" = "$key" ]; then
    rm -f "$mark" 2>/dev/null
    exit 0
  fi
  [ "$total" -gt "$limit" ] || exit 0
  write_json "$mark" "$(jq -cn --arg k "$key" '{key:$k}')"
  issues=$(issue_refs "$words")
  local next="/ndf:development-workflow ${issues:-<課題番号>}"
  if [ "$relayed" = 1 ]; then
    deny "会話の文脈が ${total} トークンで、上限 ${limit} を超えた。ラッパーの下なので、上限を超えている限りこの起動を止め続ける。新しいフェーズ（プランを含む）を起動せず、動いているプランと supervisor の報告を待ってから、引継ぎ文書（/goal の指示が名指ししたもの。無ければ書かない）を更新し、次のコマンドを情報文字列 ndf-next の囲みのコードブロック 1 つで出して応答を終える（中身: /goal ${next}、名指しの引継ぎ文書があれば「<文書> の続きから」）。<課題番号> のままなら、進めている課題の番号を補う。ブロックの直前に次の 1 文をそのまま書き、承認や確認を挟まずに出して終える: ${notice}。規約: ${CONTEXT_DOC}（止めるなら NDF_CONTEXT_GUARD=0、上限は NDF_CONTEXT_LIMIT）"
  fi
  deny "会話の文脈が ${total} トークンで、上限 ${limit} を超えた。この工程（プランを含む）は新しい会話で始める。次のコマンドを情報文字列 ndf-next の囲みのコードブロック 1 つで示して応答を終える。中身: ${next}（今の区間を /goal で始めていたなら /goal ${next}）。<課題番号> のままなら、進めている課題の番号を補って示す。このまま続けると利用者が決めたら、同じ起動をもう一度行うと 1 度だけ通る。規約: ${CONTEXT_DOC}（止めるなら NDF_CONTEXT_GUARD=0、上限は NDF_CONTEXT_LIMIT）"
}

# 記録の先頭から最初の assistant 行を探し、その文脈量を読む（スイッチポイントの判定の P）。見つけた時点で読むのをやめる。
first_context_tokens() {
  jq -rn 'first(inputs | select(.type == "assistant" and (.message.usage | type) == "object")
      | .message.usage
      | (.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0))
    // empty' "$1" 2>/dev/null
}

# 寿命 5 分の supervisor（ndf:supervisor）が、文脈を伸ばしたまま収束ループの Skill を始めるのを止める
# （#954）。止める条件は「今の文脈 C ≥ 比 × 最初の呼び出しの文脈 P」。止めないときは戻り、
# 呼び出し側が次の判定へ進む。判定は安い順に行い、外れた時点で戻る。
guard_supervisor_cut() {
  [ "${NDF_SUPERVISOR_CUT_GUARD:-1}" = 0 ] && return 0
  local skill aid own tp first last ratio stage
  skill=$(field '.tool_input.skill')
  skill=${skill#ndf:}
  case "$skill" in
    cross-refactoring) stage="構造改善" ;;
    cross-review) stage="<実装レビューかドキュメントレビューのうち、始めようとした工程>" ;;
    *) return 0 ;;
  esac
  aid=$(field '.agent_id')
  [ -n "$aid" ] || return 0
  # 定義の名前はサブエージェントの中の入力にだけ付く。寿命 1 時間の ndf:supervisor-waits は替えない
  [ "$(field '.agent_type')" = "ndf:supervisor" ] || return 0
  # transcript_path はサブエージェントの中でも親の記録を指す。supervisor 自身の記録を組み立てて読む
  tp=$(field '.transcript_path')
  [ -n "$tp" ] || return 0
  own="${tp%.jsonl}/subagents/agent-${aid}.jsonl"
  [ -r "$own" ] || return 0
  first=$(first_context_tokens "$own")
  last=$(context_tokens "$own")
  case "$first" in ''|*[!0-9]*|0) return 0 ;; esac
  case "$last" in ''|*[!0-9]*) return 0 ;; esac
  ratio=${NDF_SUPERVISOR_CUT_RATIO:-1.5}
  awk -v c="$last" -v p="$first" -v r="$ratio" 'BEGIN { exit !(r + 0 > 0 && c >= r * p) }' || return 0
  # 1 度だけ通すことはしない。やり直すだけで越えられると、スイッチポイントが LLM の裁量に戻る
  deny "この supervisor の文脈が ${last} トークンで、最初の呼び出し（${first}）の ${ratio} 倍以上ある。寿命 5 分のまま収束ループ（${skill}）を始めると、待ちの後のたびに文脈の全体を書き直す。同じ起動をやり直さずに、Pull Request を出す・進行を記録するなど起動の前に済ませることを済ませてから、フェーズの報告を「結果: スイッチポイント」「次のフェーズ: <今と同じフェーズ>」「次の工程: ${stage}」で返す（規則 12。conductor が寿命 1 時間の supervisor で続ける）。規約: ${CONTEXT_DOC}（止めるなら NDF_SUPERVISOR_CUT_GUARD=0、比は NDF_SUPERVISOR_CUT_RATIO）"
}

# conductor が Agent で supervisor を起こすとき、リポジトリに .ndf/ の宣言があれば、プランで
# 流せることを案内する（#1191）。止めない
plan_hint() {
  [ "${NDF_PLAN_HINT:-1}" = 0 ] && exit 0
  [ -n "$(field '.agent_id')" ] && exit 0
  case "$(field '.transcript_path')" in */subagents/*) exit 0 ;; esac
  case "$(field '.tool_input.subagent_type')" in ndf:supervisor|ndf:supervisor-waits) ;; *) exit 0 ;; esac
  local cwd root
  cwd=$(field '.cwd')
  root=$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null) || exit 0
  [ -f "$root/.ndf/supervise.json" ] || [ -f "$root/.ndf/worktree.json" ] || exit 0
  jq -cn --arg c "このリポジトリには .ndf/ の宣言がある。このフェーズは python3 $HERE/supervise.py new <種別>（mission / impl / check / release）のプランで作り、supervise.py queue で流せる。Agent の supervisor に落とすのはプランの雛形が無いときだけ（development-workflow の references/agent-layers.md のフェーズの表）。この案内を止めるなら NDF_PLAN_HINT=0" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse", additionalContext:$c}}'
  exit 0
}

# guard_context を子のシェルで判定し、拒否したらそれを出して終える（通すときは次の判定へ進む）
context_first() {
  local out
  out=$(guard_context)
  [ -n "$out" ] || return 0
  printf '%s\n' "$out"
  exit 0
}

case "$TOOL" in
  Bash) context_first; guard_sleep ;;
  Read) guard_read ;;
  Skill) guard_supervisor_cut; guard_context ;;
  Agent|Task) context_first; plan_hint ;;
esac
exit 0
