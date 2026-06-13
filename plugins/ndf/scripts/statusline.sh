#!/bin/bash
# NDF plugin 標準 statusline:
#   <コンテナ名 or ホスト名> <project_dir> [<モデル名>: 使用トークン / 全体 (使用率%)]
input=$(cat)

# コンテナ名を取得する。コンテナでなければホスト名にフォールバック
container_name=""

# Docker/コンテナ環境かどうかを /.dockerenv で判定
if [ -f /.dockerenv ]; then
  # CONTAINER_NAME 環境変数が明示設定されていればそちらを優先
  container_name="${CONTAINER_NAME:-}"

  # Docker ソケットが使えれば docker inspect で compose 上のコンテナ名を取得
  # （/etc/hostname はコンテナIDなので、それをキーに引く）
  if [ -z "$container_name" ] && [ -S /var/run/docker.sock ] && command -v docker >/dev/null 2>&1; then
    container_id=$(cat /etc/hostname 2>/dev/null | tr -d '[:space:]')
    if [ -n "$container_id" ]; then
      container_name=$(docker inspect --format '{{.Name}}' "$container_id" 2>/dev/null | sed 's|^/||')
    fi
  fi

  # 取れなければ /etc/hostname（コンテナID）をフォールバックとして使用
  if [ -z "$container_name" ] && [ -f /etc/hostname ]; then
    container_name=$(cat /etc/hostname 2>/dev/null | tr -d '[:space:]')
  fi
fi

# コンテナ名が取れなければ hostname コマンドにフォールバック
if [ -z "$container_name" ]; then
  container_name=$(hostname -s 2>/dev/null || hostname)
fi

dir="$container_name"

# claude root のパスを取得（project_dir を優先し、なければ current_dir を使用）
# jq 不在や無効な JSON 入力時に stderr が statusLine 描画に漏れないよう 2>/dev/null で抑制
claude_root=$(echo "$input" | jq -r '.workspace.project_dir // .workspace.current_dir // empty' 2>/dev/null)

total_input=$(echo "$input" | jq -r '.context_window.total_input_tokens // empty' 2>/dev/null)
ctx_size=$(echo "$input" | jq -r '.context_window.context_window_size // empty' 2>/dev/null)
used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty' 2>/dev/null)

# モデル表示名を取得（ラベルとして使用）。取れなければ "ctx" にフォールバック
model_name=$(echo "$input" | jq -r '.model.display_name // .model.id // empty' 2>/dev/null)
ctx_label="${model_name:-ctx}"

ctx_info=""
if [ -n "$total_input" ] && [ -n "$ctx_size" ] && [ -n "$used_pct" ]; then
  total_input_k=$(awk "BEGIN { printf \"%.1f\", $total_input / 1000 }")
  ctx_size_k=$(awk "BEGIN { printf \"%.0f\", $ctx_size / 1000 }")
  ctx_info=$(printf " \033[0;36m[%s: %sk / %sk tokens (%.0f%%)]" "$ctx_label" "$total_input_k" "$ctx_size_k" "$used_pct")
fi

if [ -n "$claude_root" ]; then
  printf "\033[01;34m%s\033[00m \033[0;33m%s\033[00m%s" "$dir" "$claude_root" "$ctx_info"
else
  printf "\033[01;34m%s\033[00m%s" "$dir" "$ctx_info"
fi
