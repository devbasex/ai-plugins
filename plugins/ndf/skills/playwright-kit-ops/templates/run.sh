#!/usr/bin/env bash
# scenario-test ランタイムのワンコマンドランチャ。
#
# 使い方:
#   ./run.sh                                    # 全テスト実行
#   ./run.sh -k test_login                      # nodeid フィルタ
#   ./run.sh -m "page_role and role"            # marker フィルタ
#   ./run.sh --pwk-overlay --pwk-drive-folder=<id>
#   ./run.sh --help                             # この help を表示
#
# 動作:
#   1) 自身が置かれているディレクトリを RUNTIME_DIR とし、CWD をそこに固定
#   2) `.venv/` が無ければ `uv sync` + `playwright install chromium` を実行
#   3) `uv run pytest --pwk-config=$PWK_CONFIG` を引数素通しで起動
#
# このスクリプトはディレクトリ名に依存しないため、
# `--runtime-dir e2e` で任意名にリネームしてもそのまま動く。
set -euo pipefail

# このスクリプト自身が置かれているディレクトリ = ランタイムルート
RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- help ----------------------------------------------------------
case "${1:-}" in
  -h|--help)
    cat <<HELP
scenario-test ランタイムランチャ

使い方:
  $(basename "$0") [pytest 引数...]

主な追加引数 (pytest にそのまま転送):
  --pwk-config <path>          scenario.config.yaml のパス (env PWK_CONFIG でも可)
  --pwk-out-dir <path>         成果物出力先 (default: ./reports/<run-id>/)
  --pwk-no-evidence            HAR / trace / 動画 を OFF
  --pwk-no-video               動画収集を OFF (デフォルトは全テストで動画 ON)
  --pwk-har-mode {minimal,full,none}
                               HAR 録画モード (default: minimal)
  --pwk-overlay                動画に赤丸カーソル + 字幕 (旧名 HUD) を焼き込む
  --pwk-drive-folder <id>      終了後に成果物を Google Drive にアップロード
  -k <expr>                    nodeid 部分一致フィルタ
  -m <expr>                    marker フィルタ (page_role / role / phase 等)
  --headed                     ブラウザを画面表示 (debug 用)

環境変数:
  PWK_CONFIG     scenario.config.yaml のパス。--pwk-config 未指定時に参照。

例:
  $(basename "$0") -k test_login --pwk-overlay
  $(basename "$0") -m "page_role" --pwk-drive-folder=ABCDEF
  PWK_CONFIG=./alt.yaml $(basename "$0")
HELP
    exit 0
    ;;
esac

# --- 1) uv の存在確認 ---------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<EOF
[run.sh] uv が見つかりません。
https://docs.astral.sh/uv/getting-started/installation/ を参照して
インストールしてから再実行してください。
EOF
  exit 1
fi

# --- 2) 初回のみ uv sync + playwright install ---------------------
if [[ ! -d "$RUNTIME_DIR/.venv" ]]; then
  echo "[run.sh] 初回セットアップ: uv sync ($RUNTIME_DIR)"
  # サブシェル内の `set -euo pipefail` は外側に伝播しないため、
  # uv sync の終了コードを明示的に拾って失敗時に abort する。
  (cd "$RUNTIME_DIR" && uv sync) || {
    echo "[run.sh] ERROR: uv sync に失敗しました。" >&2
    echo "[run.sh]        ネットワーク / pyproject.toml / uv.lock を確認してください。" >&2
    exit 1
  }
  echo "[run.sh] 初回セットアップ: playwright install chromium"
  (cd "$RUNTIME_DIR" && uv run playwright install chromium) || {
    echo "[run.sh] WARN: playwright install chromium に失敗しました。"
    echo "[run.sh]       オフライン環境では PLAYWRIGHT_BROWSERS_PATH を共有"
    echo "[run.sh]       キャッシュへ向ける運用を検討してください。"
  }
fi

# --- 3) pytest 実行 ------------------------------------------------
cd "$RUNTIME_DIR"

# --pwk-no-video / --pwk-no-evidence / --video=* が引数に含まれていなければ
# --video=on をデフォルト追加。pytest_plugin.py 側でもデフォルト注入するが、
# run.sh 経由の場合は明示的に渡すことで --video の優先度を確保する。
VIDEO_FLAG="--video=on"
for arg in "$@"; do
  case "$arg" in
    --pwk-no-video|--pwk-no-evidence|--video|--video=*) VIDEO_FLAG="" ;;
  esac
done

exec uv run pytest \
  --pwk-config="${PWK_CONFIG:-./scenario.config.yaml}" \
  $VIDEO_FLAG \
  "$@"
