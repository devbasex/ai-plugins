# shellcheck shell=bash
# 収束ループ共通: gemini CLI を非対話で走らせるための環境整備。
#
# Usage:
#   . "$LIB/_gemini-env.sh"
#   gemini_sanitize_settings "$WORKTREE" "$BACKUP_PATH" "$SANITIZED_PATH"
#   GEMINI_CLI_TRUST_WORKSPACE=true gemini --yolo --skip-trust ...
#   gemini_restore_settings
#
# 扱う固有事情は 2 つ。
#
# 1. **信頼済みディレクトリ**: 作業ディレクトリのような新規パスは untrusted と判定され、
#    `--yolo` が "default" へ降格する。`--skip-trust` と `GEMINI_CLI_TRUST_WORKSPACE=true`
#    の **両方** が必須で、片方だけでは降格を防げない。
# 2. **設定ファイルの無害化**: 最近の gemini-cli は `mcpServers` エントリの `disabled` キーを
#    Unrecognized 扱いし、起動時に `Error in: mcpServers.<name>` を標準エラー出力へ出す。
#    監視側の早期エラー検知が誤爆する原因になるため、起動時だけ `disabled` を落とした
#    設定を差し込み、読み終わったら元へ戻す。

# 復元対象を保持する。gemini_sanitize_settings が設定し、gemini_restore_settings が消す。
GEMINI_SETTINGS_PATH=
GEMINI_SETTINGS_BACKUP=

# gemini に必ず渡す環境変数。`--skip-trust` と併用して初めて降格を防げる。
gemini_trust_env() {
  echo "GEMINI_CLI_TRUST_WORKSPACE=true"
}

# 設定ファイルから `disabled` キーを再帰的に削除した版を差し込む。
# jq が無い / 失敗する環境では何もせず、元の設定のまま起動させる。
gemini_sanitize_settings() {
  local worktree=${1:?worktree required}
  local backup=${2:?backup path required}
  local sanitized=${3:?sanitized path required}

  GEMINI_SETTINGS_PATH=$worktree/.gemini/settings.json
  GEMINI_SETTINGS_BACKUP=

  [ -f "$GEMINI_SETTINGS_PATH" ] || return 0
  command -v jq >/dev/null 2>&1 || return 0

  cp "$GEMINI_SETTINGS_PATH" "$backup"
  if jq 'walk(if type == "object" then del(.disabled) else . end)' "$backup" > "$sanitized" 2>/dev/null; then
    cp "$sanitized" "$GEMINI_SETTINGS_PATH"
    GEMINI_SETTINGS_BACKUP=$backup
  else
    # jq が失敗したら無害化を諦め、バックアップも破棄して元のまま起動する
    rm -f "$backup"
  fi
}

# 無害化した設定を元へ戻す。冪等なので trap から多重に呼んでも安全。
gemini_restore_settings() {
  if [ -n "${GEMINI_SETTINGS_BACKUP:-}" ] && [ -f "$GEMINI_SETTINGS_BACKUP" ]; then
    mv -f "$GEMINI_SETTINGS_BACKUP" "$GEMINI_SETTINGS_PATH" 2>/dev/null || true
    GEMINI_SETTINGS_BACKUP=
  fi
}
