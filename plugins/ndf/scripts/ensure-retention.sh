#!/usr/bin/env bash
# NDF plugin: ~/.claude/settings.json の cleanupPeriodDays を最低 90 日に保つ。
# - 既存値 >= 90 なら何もしない
# - 既存値 < 90 or 未設定 なら 90 に更新
# - 前回チェックから 7 日経っていなければスキップ (多重実行防止)
# - 複数セッション同時起動時の race condition を flock で回避 (flock 不在時は atomic rename のみ)
set -euo pipefail

MIN_DAYS=90
GUARD_DAYS=7
SETTINGS="$HOME/.claude/settings.json"
FLAG="$HOME/.claude/.ndf-retention-checked"
LOCK="$HOME/.claude/.ndf-retention.lock"

# jq 必須
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# 7 日以内に実行済みならスキップ
if [ -f "$FLAG" ]; then
  if find "$FLAG" -mtime "-${GUARD_DAYS}" -print -quit 2>/dev/null | grep -q .; then
    exit 0
  fi
fi

# settings.json が存在しない場合は新規作成
mkdir -p "$(dirname "$SETTINGS")"
if [ ! -f "$SETTINGS" ]; then
  echo "{}" > "$SETTINGS"
fi

# 更新処理をひとつの関数にまとめ、ロック内で呼び出す
update_retention() {
  # ロック取得後に再チェック (並行セッションが先に更新済みの可能性)
  local current tmp
  current=$(jq -r '.cleanupPeriodDays // 0' "$SETTINGS" 2>/dev/null || echo 0)
  case "$current" in
    ''|*[!0-9]*) current=0 ;;
  esac
  if [ "$current" -ge "$MIN_DAYS" ]; then
    return 0
  fi
  tmp="$(mktemp)"
  if jq --argjson v "$MIN_DAYS" '.cleanupPeriodDays = $v' "$SETTINGS" > "$tmp"; then
    mv "$tmp" "$SETTINGS"
    echo "[ndf] cleanupPeriodDays を ${current} → ${MIN_DAYS} に更新しました (~/.claude/settings.json)"
  else
    rm -f "$tmp"
    return 1
  fi
}

# ロック取得: flock があればアトミック更新、無ければ atomic rename のみに依存
if command -v flock >/dev/null 2>&1; then
  (
    # 最大 5 秒待機。取得できなければ他プロセスが更新中なので何もしない
    flock -x -w 5 200 || exit 0
    update_retention || true
  ) 200>"$LOCK"
else
  # flock 不在環境 (macOS 標準など): mv 自体は POSIX で atomic なので大きな破損は起きない
  update_retention || true
fi

touch "$FLAG"
