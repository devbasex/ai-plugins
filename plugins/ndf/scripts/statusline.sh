#!/bin/bash
# ndf-statusline: managed (do not edit; auto-updated by ndf:statusline)
# NDF plugin 標準 statusline:
#   <project_dir> [<モデル名> 使用トークン │ <サブエージェントの説明> 使用トークン · ...]
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

ctx_info=""
if [ -n "$total_input" ]; then
  main_used="$((total_input / 1000))k"
  # 上限が 200K 以下のモデル（Haiku 4.5）は 150k で知らせる。モデル名ではなく入力の上限で決める
  main_limit=$WARN_TOKENS
  [ -n "$ctx_size" ] && [ "$ctx_size" -le 200000 ] 2>/dev/null && main_limit=150000
  [ "$total_input" -gt "$main_limit" ] && main_used=$(printf "$WARN_COLOR%s\033[0;36m" "$main_used")
  ctx_info=$(printf " \033[0;36m[%s %s" "$ctx_label" "$main_used")

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
      [ "$state" = done ] && continue
      if [ "$state" = idle ]; then
        # 30 秒以上書き足されていなければ終わったとみなす (GNU / BSD の stat の両方に対応)
        mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)
        [ -n "$mtime" ] && [ $((now - mtime)) -ge 30 ] && continue
      fi
      # 説明の先頭 4 文字をラベルにする。種類名は general-purpose がほとんどで見分けに使えない
      label=$(jq -r '.description // empty | gsub("\\s"; "") | .[0:4]' "${f%.jsonl}.meta.json" 2>/dev/null)
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

# コンテナ名・ホスト名は出さない。区別は端末やエディタのウィンドウタイトルに任せる
if [ -n "$claude_root" ]; then
  printf "\033[0;33m%s\033[00m%s" "$claude_root" "$ctx_info"
else
  printf "%s" "${ctx_info# }"
fi
