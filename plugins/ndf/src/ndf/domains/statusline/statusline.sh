#!/bin/bash
# ndf-statusline: managed (do not edit; auto-updated by ndf:statusline)
# NDF plugin 標準 statusline:
#   <project_dir> [<モデル名> 使用トークン <このセッションの費用> <全セッションと claude -p の直近 1 時間の費用> 7d:<7 日の上限の使用率> 5h:<5 時間の上限の使用率> │ <サブエージェントの説明> 使用トークン · ...] <接続先>
input=$(cat)

# claude root のパスを取得（project_dir を優先し、なければ current_dir を使用）
# jq 不在や無効な JSON 入力時に stderr が statusLine 描画に漏れないよう 2>/dev/null で抑制
claude_root=$(echo "$input" | jq -r '.workspace.project_dir // .workspace.current_dir // empty' 2>/dev/null)

total_input=$(echo "$input" | jq -r '.context_window.total_input_tokens // empty' 2>/dev/null)
ctx_size=$(echo "$input" | jq -r '.context_window.context_window_size // empty' 2>/dev/null)
transcript=$(echo "$input" | jq -r '.transcript_path // empty' 2>/dev/null)

# モデル表示名を取得（ラベルとして使用）。取れなければ "ctx" にフォールバック。
# 現行モデルの上限は 1M なので、"Opus 5 (1M context)" の括弧と空白は落として "Opus5" にする
model_name=$(echo "$input" | jq -r '.model.display_name // .model.id // empty' 2>/dev/null | sed 's/ *(.*)//; s/ //g')
ctx_label="${model_name:-ctx}"

# 使用量がこの値を超えたら赤で知らせる。上限は出さないため、色で危険な水準を示す
WARN_TOKENS=500000
WARN_COLOR='\033[0;31m'

NOTE_COLOR='\033[0;33m'

# 再起動の方針（#2262）。窓の大きさは statusline の入力にだけあるので、状態ディレクトリへ書いて hook に渡す。
# 適用値は SessionStart と hook が書いたファイルを読むだけにする（優先順位の解決を 2 か所に持たない）。
# 保存先の順は lib/context_decisions.py の context_state_dir と同じ
session_id=$(echo "$input" | jq -r '.session_id // empty' 2>/dev/null)
[ -n "$session_id" ] || { [ -n "$transcript" ] && session_id=$(basename "$transcript" .jsonl); }
case "$session_id" in *[!A-Za-z0-9_-]*) session_id="" ;; esac
# コンテナの状態の根。順は lib/state_home.py の container_home と同じ（#2766）
ctx_sessions="${NDF_CONTEXT_DIR:-${NDF_STATE_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}/ndf}/context}"
ctx_sessions="$ctx_sessions/sessions"
policy="" decide_from="" policy_limit="" policy_margin=""
if [ -n "$session_id" ]; then
  model_id=$(echo "$input" | jq -r '.model.id // empty' 2>/dev/null)
  window_file="$ctx_sessions/$session_id.window.json"
  if [ -n "$ctx_size" ] && [ "$ctx_size" -gt 0 ] 2>/dev/null; then
    seen=$(jq -r '"\(.window) \(.model)"' "$window_file" 2>/dev/null)
    if [ "$seen" != "$ctx_size $model_id" ] && mkdir -p "$ctx_sessions" 2>/dev/null; then
      jq -cn --argjson w "$ctx_size" --arg m "$model_id" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{window: $w, model: $m, at: $at}' > "$window_file.$$" 2>/dev/null && mv -f "$window_file.$$" "$window_file" 2>/dev/null
      rm -f "$window_file.$$" 2>/dev/null
    fi
  fi
  applied=$(jq -r 'select(.policy == "trial") | "\(.policy) \(.decide_from) \(.limit) \(.margin)"' "$ctx_sessions/$session_id.applied.json" 2>/dev/null)
  [ -n "$applied" ] && read -r policy decide_from policy_limit policy_margin <<<"$applied"
fi

# 費用（Claude Code が定価で計算した推定で、請求とずれることがある）
#   自分: このセッションの `cost.total_cost_usd` の、statusline が最初に見た時点からの増分。会話の中のサブエージェントの分を含む
#   1 時間: ジャーナル print-costs.jsonl の直近 1 時間の行の合計。行は各セッションの statusline が増えた分を足し
#           （session）、NDF が起動した `claude -p` の分は adapters/run_log.py の record_cost が足す（relay）
# 続けた会話（--resume）は前の会話の費用を引き継ぐので、最初に見た値（first）を起点にして過去の分を数えない。
# 値が前より小さくなったら 0 から数え直したとみなし、それまでの増分を base へ積む
cost_own="" cost_hour=""
cost=$(echo "$input" | jq -r '.cost.total_cost_usd | numbers' 2>/dev/null)
if [ -n "$cost" ]; then
  ctx_root="${ctx_sessions%/sessions}"
  journal="$ctx_root/print-costs.jsonl"
  mine=""
  if [ -n "$session_id" ] && mkdir -p "$ctx_sessions" 2>/dev/null; then
    cost_file="$ctx_sessions/$session_id.cost.json"
    old=$(cat "$cost_file" 2>/dev/null)
    new=$(jq -cn --argjson c "$cost" --arg old "$old" '
      ($old | try fromjson catch null) as $o
      | if ($o | type) != "object" or ($o.first | type) != "number" then {first: $c, last: $c, base: 0}
        elif $c < $o.last then {first: 0, last: $c, base: ($o.base + $o.last - $o.first)}
        else {first: $o.first, last: $c, base: $o.base} end' 2>/dev/null)
    if [ -n "$new" ] && [ "$new" != "$old" ]; then
      printf "%s" "$new" > "$cost_file.$$" 2>/dev/null && mv -f "$cost_file.$$" "$cost_file" 2>/dev/null
      rm -f "$cost_file.$$" 2>/dev/null
      # 増えた分を時刻付きで 1 行足す（初めて見たセッションは起点を決めるだけで足さない）
      jq -cn --arg old "$old" --argjson n "$new" --arg s "$session_id" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        ($old | try fromjson catch null) as $o
        | select(($o | type) == "object" and ($o.first | type) == "number")
        | (($n.base + $n.last - $n.first) - ($o.base + $o.last - $o.first)) as $d
        | select($d > 0) | {at: $at, usd: $d, session: $s}' >> "$journal" 2>/dev/null
    fi
    mine=$(echo "$new" | jq -r '.base + .last - .first' 2>/dev/null)
  fi
  cost_own=$(printf '$%.2f' "${mine:-0}")
  # 1 時間の合計は 30 秒キャッシュする（更新は 5 秒ごとで、どのセッションも同じ値を数える）
  total_cache="$ctx_root/cost-hour.txt"
  total_age=999
  [ -f "$total_cache" ] && total_age=$(( $(date +%s) - $(stat -c %Y "$total_cache" 2>/dev/null || stat -f %m "$total_cache" 2>/dev/null || echo 0) ))
  if [ "$total_age" -ge 30 ]; then
    since=$(( $(date +%s) - 3600 ))
    # 1 行ずつ読む。書きかけや壊れた行があっても、ほかの行は足す
    total=$(jq -Rn --argjson since "$since" '[inputs | fromjson? | objects | select((.at | fromdateiso8601? // 0) >= $since) | .usd | numbers] | add // 0' "$journal" 2>/dev/null)
    [ -n "$total" ] || total=0
    mkdir -p "$ctx_root" 2>/dev/null && printf "%s" "$total" > "$total_cache.$$" 2>/dev/null && mv -f "$total_cache.$$" "$total_cache" 2>/dev/null
    rm -f "$total_cache.$$" 2>/dev/null
    # ジャーナルが 5 MB を超えたら、直近 1 日の行だけを残す（表示のための記録で、入れ替えの間に足された行は落ちてよい）
    size=$(stat -c %s "$journal" 2>/dev/null || stat -f %z "$journal" 2>/dev/null || echo 0)
    if [ "$size" -gt 5000000 ] 2>/dev/null; then
      jq -Rc --argjson since "$((since - 82800))" 'fromjson? | objects | select((.at | fromdateiso8601? // 0) >= $since)' "$journal" > "$journal.$$" 2>/dev/null && mv -f "$journal.$$" "$journal" 2>/dev/null
      rm -f "$journal.$$" 2>/dev/null
    fi
  fi
  total=$(cat "$total_cache" 2>/dev/null)
  case "$total" in ''|*[!0-9.e+-]*) ;; *) cost_hour=$(printf '$%.2f/h' "$total") ;; esac
fi
# 利用上限の使用率（7 日・5 時間の順）。claude.ai のサブスクリプションのときだけ入力に載る。無い窓は出さない
# （5 時間の上限が無いプランでは 5h を出さない）。80% から黄、95% から赤
limit_part=""
limits=$(echo "$input" | jq -r '[["7d", .rate_limits.seven_day.used_percentage], ["5h", .rate_limits.five_hour.used_percentage]]
  | map(select(.[1] | type == "number")) | map("\(.[0]) \(.[1] | floor)") | .[]' 2>/dev/null)
while read -r win pct; do
  [ -n "$win" ] || continue
  color=""
  if [ "$pct" -ge 95 ] 2>/dev/null; then color=$WARN_COLOR; elif [ "$pct" -ge 80 ] 2>/dev/null; then color=$NOTE_COLOR; fi
  limit_part="$limit_part $(printf "%b%s:%s%%\033[0;36m" "$color" "$win" "$pct")"
done <<<"$limits"
cost_part=""
[ -n "$cost_own" ] && cost_part=$(printf " \033[0;32m%s%s\033[0;36m" "$cost_own" "${cost_hour:+ $cost_hour}")

ctx_info=""
if [ -n "$total_input" ]; then
  main_used="$((total_input / 1000))k"
  if [ "$policy" = trial ]; then
    # 試行方式: 判断開始の値を超えたら黄、実効の上限（運用上限か、窓 − 容量の余白の小さいほう）を超えたら赤
    eff=$policy_limit
    [ -n "$ctx_size" ] && [ $((ctx_size - policy_margin)) -lt "$eff" ] 2>/dev/null && eff=$((ctx_size - policy_margin))
    if [ "$total_input" -gt "$eff" ] 2>/dev/null; then
      main_used=$(printf "$WARN_COLOR%s\033[0;36m" "$main_used")
    elif [ "$total_input" -gt "$decide_from" ] 2>/dev/null; then
      main_used=$(printf "$NOTE_COLOR%s\033[0;36m" "$main_used")
    fi
  else
    # 現行方式: 窓に対して危険な水準を示す表示の警告（hook の止める上限 200K とは別の意味）。
    # 上限が 200K 以下のモデル（Haiku 4.5）は 150k で知らせる。モデル名ではなく入力の上限で決める
    main_limit=$WARN_TOKENS
    [ -n "$ctx_size" ] && [ "$ctx_size" -le 200000 ] 2>/dev/null && main_limit=150000
    [ "$total_input" -gt "$main_limit" ] && main_used=$(printf "$WARN_COLOR%s\033[0;36m" "$main_used")
  fi
  ctx_info=$(printf " \033[0;36m[%s %s%s%s" "$ctx_label" "$main_used" "$cost_part" "$limit_part")

  # 実行中のサブエージェントのコンテキスト使用量を並べる。statusLine の JSON は
  # メインセッションの値しか持たないため、サブエージェントの記録から読む。
  # 記録は <transcript_path から .jsonl を除いたもの>/subagents/agent-<id>.jsonl にある
  sub_dir="${transcript%.jsonl}/subagents"
  rows=""
  if [ -n "$transcript" ] && [ -d "$sub_dir" ]; then
    now=$(date +%s)
    # 直近 60 分以内に更新された記録を候補にし、実行中かどうかは記録の末尾で決める。
    # 更新の時刻では決めない。子を待つ supervisor や長いコマンドを待つ担当は、実行中でも
    # 何分も書き足さない
    # NUL 区切りで読む。空白を含むパスでも 1 ファイルとして扱う
    while IFS= read -r -d '' f; do
      # 末尾だけを読む。記録は長くなるため全体を走査しない。
      # 出力: モデル / 使用量 / 状態 (run | done | idle)
      #   最後の user か assistant の行が tool_use を含まない assistant なら応答を書き終えている。
      #   end_turn が付いていれば終了。付いていなければ tool_use の直前の text の途中かもしれない (idle)
      st=$(tail -n 50 "$f" | jq -rs '
        (map(select(.type == "assistant" or .type == "user")) | last) as $l
        | (map(select(.message.usage?)) | last) as $u
        | if $u == null then empty else
          [ ($u.message.model // ""),
            ($u.message.usage | (.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)),
            (if $l.type == "assistant" and ([$l.message.content[]?.type] | index("tool_use") | not)
             then (if $l.message.stop_reason == "end_turn" then "done" else "idle" end)
             else "run" end) ]
          | @tsv end' 2>/dev/null)
      [ -n "$st" ] || continue
      IFS=$'\t' read -r model tokens state <<<"$st"
      [ "$state" = "done" ] && continue
      if [ "$state" = idle ]; then
        # 30 秒以上書き足されていなければ終わったとみなす (GNU / BSD の stat の両方に対応)
        mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)
        [ -n "$mtime" ] && [ $((now - mtime)) -ge 30 ] && continue
      fi
      # 説明の先頭 4 文字をラベルにする。種類名は general-purpose がほとんどで見分けに使えない。
      # 空白と制御文字（\p{Cc} = U+0000-001F / U+007F-009F）を除く。ESC などを端末へ出さないため
      label=$(jq -r '.description // empty | gsub("[\\p{Cc}\\s]"; "") | .[0:4]' "${f%.jsonl}.meta.json" 2>/dev/null)
      [ -n "$label" ] || { label=$(basename "$f" .jsonl); label=${label#agent-}; label=${label:0:4}; }
      # 500k を超えたら赤で知らせる。1M 未満のモデルは Haiku（200K）だけなので、Haiku は 150k で知らせる
      limit=$WARN_TOKENS
      case "$model" in *haiku*) limit=150000 ;; esac
      warn=0
      [ "$tokens" -gt "$limit" ] && warn=1
      rows="$rows$tokens"$'\t'"$warn"$'\t'"$label"$'\n'
    done < <(find "$sub_dir" -name 'agent-*.jsonl' -mmin -60 -print0 2>/dev/null)
  fi
  # 使用量の多い順に 3 本まで並べ、残りは本数だけを出す。80 桁の端末に収めるため
  if [ -n "$rows" ]; then
    subs=""
    n=0
    while IFS=$'\t' read -r tokens warn label; do
      n=$((n + 1))
      [ "$n" -gt 3 ] && continue
      entry="$label $((tokens / 1000))k"
      [ "$warn" = 1 ] && entry=$(printf "$WARN_COLOR%s\033[0;36m" "$entry")
      subs="${subs:+$subs · }$entry"
    done < <(printf "%s" "$rows" | sort -t $'\t' -k1,1nr)
    [ "$n" -gt 3 ] && subs="$subs +$((n - 3))"
    ctx_info="$ctx_info │ $subs"
  fi
  ctx_info="$ctx_info]"
fi

# 接続先。Bedrock なら環境変数で分かる。Anthropic のアカウントは `claude auth status` の email で、
# 0.4 秒かかるので 60 秒キャッシュする（更新は 5 秒ごと）。取れなければ何も足さない
account=""
if [ -n "${CLAUDE_CODE_USE_BEDROCK:-}" ]; then
  account="bedrock${AWS_PROFILE:+:$AWS_PROFILE}"
else
  auth_cache="${NDF_STATUSLINE_AUTH_CACHE:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.ndf-statusline-auth.json}"
  cache_age=999
  if [ -f "$auth_cache" ]; then
    cache_age=$(( $(date +%s) - $(stat -c %Y "$auth_cache" 2>/dev/null || echo 0) ))
  fi
  if [ "$cache_age" -ge 60 ]; then
    auth_out=""
    if command -v timeout >/dev/null 2>&1; then
      auth_out=$(timeout 1 claude auth status 2>/dev/null)
    else
      auth_out=$(claude auth status 2>/dev/null)
    fi
    # 取れなかったときは古いバックアップを消し、次の更新で取り直す
    if [ -n "$auth_out" ]; then printf "%s" "$auth_out" > "$auth_cache" 2>/dev/null; else rm -f "$auth_cache" 2>/dev/null; fi
  fi
  # 同じメールアドレスに Team と個人のコントラクトがありうるので、コントラクトの種別（team / pro / max ...）を添える
  [ -f "$auth_cache" ] && account=$(jq -r 'select(.loggedIn == true) | select(.email != null) | .email + (if .subscriptionType then " (" + .subscriptionType + ")" else "" end)' "$auth_cache" 2>/dev/null)
fi
account_info=""
[ -n "$account" ] && account_info=$(printf " \033[2m%s\033[0m" "$account")

# コンテナ名・ホスト名は出さない。区別は端末やエディタのウィンドウタイトルに任せる
if [ -n "$claude_root" ]; then
  printf "\033[0;33m%s\033[00m%s%s" "$claude_root" "$ctx_info" "$account_info"
else
  printf "%s%s" "${ctx_info# }" "$account_info"
fi
