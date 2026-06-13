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

# jq 必須 (ensure は SessionStart hook から毎回呼ばれるためノイズ防止で黙ってスキップ)
if ! command -v jq >/dev/null 2>&1; then
  if [ "$CMD" != "ensure" ]; then
    echo "[ndf:statusline] jq が見つからないため処理をスキップしました" >&2
  fi
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

# current_command から実行スクリプトのパスを抽出し、先頭の ~ を $HOME に展開する。
# 例: "bash ~/.claude/statusline-command.sh" -> "/home/user/.claude/statusline-command.sh"
current_script_path() {
  local cmd path
  cmd="$(current_command)"
  [ -z "$cmd" ] && return 0
  # 末尾の *.sh トークンを実行対象スクリプトとみなす。
  # grep -o は GNU 拡張のため、POSIX 準拠かつ BusyBox でも動く sed で抽出する。
  # NDF が配置するコマンドは "bash ~/.claude/<name>.sh" 形式 (パスにスペースを含まない) を想定。
  path="$(printf '%s\n' "$cmd" | sed -n 's/.*[[:space:]]\([^[:space:]]*\.sh\).*/\1/p; s/^\([^[:space:]]*\.sh\)$/\1/p' | tail -n1)"
  [ -z "$path" ] && return 0
  case "$path" in
    "~/"*) path="$HOME/${path#\~/}" ;;
    "~")   path="$HOME" ;;
  esac
  printf '%s\n' "$path"
}

# 指定スクリプトが NDF 由来 (マーカー付き、または既知のレガシーコピー) か判定する。
# ユーザー独自の statusline を誤って上書きしないため、判定は厳格に行う。
is_ndf_managed_copy() {
  local path="$1"
  [ -f "$path" ] || return 1
  # ① マーカーがあれば NDF 管理コピー確定 (今後配置される全コピーが該当)
  if grep -Fq 'ndf-statusline: managed' "$path" 2>/dev/null; then
    return 0
  fi
  # ② レガシー救済: マーカー導入前の既知の旧コピー名で、かつ NDF statusline 特有の
  #    ロジック (ctx ラベル + コンテナ名取得) を両方含む場合のみ移行対象とする
  case "$(basename "$path")" in
    statusline-command.sh)
      if grep -Fq '[ctx:' "$path" 2>/dev/null && grep -Fq 'container_name' "$path" 2>/dev/null; then
        return 0
      fi
      ;;
  esac
  return 1
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

# NDF 由来の旧 statusline を検出した際に、既存設定をバックアップした上で
# 正規パス (~/.claude/ndf-statusline.sh) 参照へ移行する。
migrate_to_ndf_statusline() {
  local existing
  existing=$(jq -c '.statusLine // empty' "$SETTINGS" 2>/dev/null || true)
  if [ -n "$existing" ] && [ ! -f "$BACKUP" ]; then
    printf '%s\n' "$existing" > "$BACKUP"
  fi
  set_ndf_statusline
  echo "[ndf:statusline] NDF 由来の旧 statusline を検出したため正規パス (~/.claude/ndf-statusline.sh) へ移行しました (旧設定は $BACKUP に退避)"
}

cmd_ensure() {
  deploy_script
  # 既に statusLine が設定されている場合
  if [ -n "$(jq -r '.statusLine // empty' "$SETTINGS" 2>/dev/null)" ]; then
    # 正規パスを指していれば deploy_script で本体が追従済み (何もしない)
    if is_ndf_statusline; then
      return 0
    fi
    # NDF が過去に配置したコピー (マーカー付き or レガシー statusline-command.sh) を
    # 指している場合のみ、正規パス参照へ移行してバージョンアップ追従を回復する
    local cur_path
    cur_path="$(current_script_path)"
    if [ -n "$cur_path" ] && is_ndf_managed_copy "$cur_path"; then
      migrate_to_ndf_statusline
      return 0
    fi
    # それ以外はユーザー独自設定として尊重し、何もしない
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
