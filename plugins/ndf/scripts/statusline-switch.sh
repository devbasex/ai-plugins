#!/usr/bin/env bash
# NDF plugin: statusline の導入・切替・復元を行う。
#
# Usage: statusline-switch.sh <ensure|set|restore|status>
#   ensure  : SessionStart hook 用。statusLine 未設定の場合のみ NDF 版を設定する
#             (既存設定があれば何もしない)。NDF 版利用中はスクリプト更新のみ追従。
#   set     : 既存の statusLine 設定をバックアップした上で NDF 版に切り替える。
#   restore : バックアップから元の statusLine 設定を復元する
#             (バックアップが無ければ statusLine 設定を削除しデフォルト表示に戻す)。
#   status  : 現在の statusLine 設定とバックアップの有無を表示する。
set -euo pipefail

CMD="${1:-status}"
SETTINGS="$HOME/.claude/settings.json"
TARGET="$HOME/.claude/ndf-statusline.sh"
BACKUP="$HOME/.claude/.ndf-statusline-backup.json"
LOCK="$HOME/.claude/.ndf-statusline.lock"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/statusline.sh"
NDF_COMMAND="bash ~/.claude/ndf-statusline.sh"

# jq 必須
if ! command -v jq >/dev/null 2>&1; then
  echo "[ndf:statusline] jq が見つからないため処理をスキップしました" >&2
  exit 0
fi

mkdir -p "$HOME/.claude"
if [ ! -f "$SETTINGS" ]; then
  echo "{}" > "$SETTINGS"
fi

# プラグイン同梱の statusline.sh を ~/.claude/ndf-statusline.sh に配置 (差分時のみ)
deploy_script() {
  if [ ! -f "$TARGET" ] || ! cmp -s "$SRC" "$TARGET"; then
    cp "$SRC" "$TARGET"
    chmod +x "$TARGET"
  fi
}

current_command() {
  jq -r '.statusLine.command // empty' "$SETTINGS" 2>/dev/null || true
}

is_ndf_statusline() {
  case "$(current_command)" in
    *ndf-statusline.sh*) return 0 ;;
    *) return 1 ;;
  esac
}

# settings.json を atomic に書き換える。引数: jq フィルタ
update_settings() {
  local tmp
  tmp="$(mktemp)"
  if jq "$@" "$SETTINGS" > "$tmp"; then
    mv "$tmp" "$SETTINGS"
  else
    rm -f "$tmp"
    return 1
  fi
}

set_ndf_statusline() {
  update_settings --arg cmd "$NDF_COMMAND" \
    '.statusLine = {type: "command", command: $cmd}'
}

cmd_ensure() {
  deploy_script
  # 既に statusLine が設定されていればそちらを優先 (何もしない)
  if [ -n "$(jq -r '.statusLine // empty' "$SETTINGS" 2>/dev/null)" ]; then
    return 0
  fi
  set_ndf_statusline
  echo "[ndf] statusLine を NDF 標準 statusline に設定しました (~/.claude/settings.json)"
}

cmd_set() {
  deploy_script
  if is_ndf_statusline; then
    echo "[ndf:statusline] 既に NDF 標準 statusline が設定されています"
    return 0
  fi
  # 既存設定があればバックアップしてから切り替え
  local existing
  existing=$(jq -c '.statusLine // empty' "$SETTINGS" 2>/dev/null || true)
  if [ -n "$existing" ]; then
    printf '%s\n' "$existing" > "$BACKUP"
    echo "[ndf:statusline] 既存設定をバックアップしました: $BACKUP"
  fi
  set_ndf_statusline
  echo "[ndf:statusline] NDF 標準 statusline に切り替えました"
}

cmd_restore() {
  if [ -f "$BACKUP" ]; then
    update_settings --slurpfile bk "$BACKUP" '.statusLine = $bk[0]'
    rm -f "$BACKUP"
    echo "[ndf:statusline] バックアップから statusLine 設定を復元しました"
  elif is_ndf_statusline; then
    update_settings 'del(.statusLine)'
    echo "[ndf:statusline] バックアップが無いため statusLine 設定を削除しました (デフォルト表示に戻ります)"
  else
    echo "[ndf:statusline] 復元対象がありません (NDF statusline は未使用、バックアップ無し)"
  fi
}

cmd_status() {
  local cur
  cur=$(current_command)
  if [ -z "$cur" ]; then
    echo "statusLine: 未設定 (デフォルト表示)"
  elif is_ndf_statusline; then
    echo "statusLine: NDF 標準 statusline ($cur)"
  else
    echo "statusLine: カスタム設定 ($cur)"
  fi
  if [ -f "$BACKUP" ]; then
    echo "バックアップ: あり ($(cat "$BACKUP"))"
  else
    echo "バックアップ: なし"
  fi
}

run_cmd() {
  case "$CMD" in
    ensure)  cmd_ensure ;;
    set)     cmd_set ;;
    restore) cmd_restore ;;
    status)  cmd_status ;;
    *)
      echo "Usage: $(basename "$0") <ensure|set|restore|status>" >&2
      exit 1
      ;;
  esac
}

# 複数セッション同時起動時の race condition を flock で回避 (flock 不在時は atomic rename のみ)
if command -v flock >/dev/null 2>&1; then
  (
    flock -x -w 5 200 || exit 0
    run_cmd
  ) 200>"$LOCK"
else
  run_cmd
fi
