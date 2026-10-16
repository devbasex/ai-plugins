# shellcheck shell=bash
# Claude Code の settings.json の実パスを返す（#1576 の決定 14）。
#
# アカウントの設定ディレクトリ（relay の登録済みアカウントの子の CLAUDE_CONFIG_DIR）では settings.json が共有の設定
# ディレクトリへの symlink になる。symlink のパスへ一時ファイルを mv すると symlink が実体に置き換わるため、書く側は
# このパスへ書き、排他・印・控えもこのパスの隣に置く。symlink でなければ今のパスのままである。
#
#   . "$SCRIPT_DIR/lib/claude-settings.sh"
#   SETTINGS="$(claude_settings_path)"          # 引数で設定ディレクトリを渡せる（既定は ${CLAUDE_CONFIG_DIR:-~/.claude}）

claude_settings_path() {
  local p="${1:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}/settings.json"
  if [ -L "$p" ]; then
    readlink -f "$p" 2>/dev/null || printf '%s\n' "$p"
  else
    printf '%s\n' "$p"
  fi
}
