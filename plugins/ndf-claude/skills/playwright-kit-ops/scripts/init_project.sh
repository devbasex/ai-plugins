#!/usr/bin/env bash
# scenario-test ランタイムを利用者プロジェクトに埋め込む初期化スクリプト。
#
# 使い方:
#   scripts/init_project.sh <PROJECT_ROOT> [--runtime-dir <name>] [--dry-run]
#
# 例:
#   scripts/init_project.sh /path/to/your-app
#       → /path/to/your-app/scenario-test/ 一式を作成
#
#   scripts/init_project.sh /path/to/your-app --runtime-dir e2e
#       → /path/to/your-app/e2e/ 一式 (中身は同じ、ディレクトリ名のみ変更)
#
#   scripts/init_project.sh /path/to/your-app --dry-run
#       → 実際にはコピーせず、配置予定のファイル一覧のみ表示
#
# 動作:
#   1) <PROJECT_ROOT>/<runtime-dir>/ を作成
#   2) Skill 側の playwright_kit/ / scripts/ / uv.lock / 各テンプレートをコピー
#   3) scenario.config.yaml / tests/conftest.py / tests/test_*.py.template は
#      既に存在する場合は上書きしない (利用者の編集物保護)
#   4) 初回 uv sync + playwright install chromium を実行
#
# このスクリプト完了後、利用者プロジェクトは Skill ディレクトリの存在に依存
# しなくなる (all-in-one)。
set -euo pipefail

# ---------- 引数パース ----------
PROJECT_ROOT=""
RUNTIME_DIR_NAME="scenario-test"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime-dir)
      [[ $# -ge 2 ]] || { echo "[init] --runtime-dir に値が必要です" >&2; exit 1; }
      RUNTIME_DIR_NAME="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "[init] unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -n "$PROJECT_ROOT" ]]; then
        echo "[init] PROJECT_ROOT は 1 つだけ指定してください (既: $PROJECT_ROOT, 追加: $1)" >&2
        exit 1
      fi
      PROJECT_ROOT="$1"
      shift
      ;;
  esac
done

[[ -n "$PROJECT_ROOT" ]] || { echo "[init] PROJECT_ROOT は必須です" >&2; exit 1; }
# `.` / `..` を弾いた上で英数字 + `.` `_` `-` のみ許可。
# (先頭 `.` は許可するが、`.` 単体・`..` 単体・パス区切りはブロック)
if [[ "$RUNTIME_DIR_NAME" == "." || "$RUNTIME_DIR_NAME" == ".." ]]; then
  echo "[init] --runtime-dir に '.' / '..' は指定できません: '$RUNTIME_DIR_NAME'" >&2
  exit 1
fi
if [[ ! "$RUNTIME_DIR_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "[init] --runtime-dir は英数字 / . / _ / - のみ使用可能です: '$RUNTIME_DIR_NAME'" >&2
  exit 1
fi

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# bash 3.2 (macOS default) ではサブシェル失敗時の `||` 右辺が動かないケースがあるため、
# 明示的な `-d` チェックを先に行う。
if [[ ! -d "$PROJECT_ROOT" ]]; then
  echo "[init] PROJECT_ROOT が存在しません: $PROJECT_ROOT" >&2
  exit 1
fi
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/$RUNTIME_DIR_NAME"

echo "[init] Skill ディレクトリ : $SKILL_DIR"
echo "[init] プロジェクトルート : $PROJECT_ROOT"
echo "[init] ランタイム配置先   : $RUNTIME_DIR"
[[ $DRY_RUN -eq 1 ]] && echo "[init] (dry-run モード: 実際にはコピーしません)"

# ---------- 必要コマンドチェック ----------
for cmd in rsync; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "[init] 必要なコマンドが見つかりません: $cmd" >&2
    exit 1
  }
done

# ---------- helper ----------
copy() {
  local src="$1" dst="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "    cp $src -> $dst"
    return 0
  fi
  cp "$src" "$dst"
}

copy_if_absent() {
  local src="$1" dst="$2"
  if [[ -e "$dst" ]]; then
    echo "    skip (exists): $dst"
    return 0
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "    cp (new): $src -> $dst"
    return 0
  fi
  cp "$src" "$dst"
}

# ---------- 0) 配置先準備 ----------
if [[ $DRY_RUN -eq 0 ]]; then
  mkdir -p "$RUNTIME_DIR/tests"
fi

# ---------- 1) パッケージ本体 + scripts + uv.lock を rsync ----------
echo "[init] [1/4] playwright_kit / scripts / uv.lock をコピー"
# dry-run 時は rsync を起動しない (宛先 RUNTIME_DIR が未作成だと
# rsync -n でも failed to read directory で abort するため)。
if [[ $DRY_RUN -eq 1 ]]; then
  echo "    rsync $SKILL_DIR/playwright_kit -> $RUNTIME_DIR/playwright_kit"
  echo "    rsync $SKILL_DIR/scripts          -> $RUNTIME_DIR/scripts"
  echo "    cp    $SKILL_DIR/uv.lock        -> $RUNTIME_DIR/uv.lock"
else
  RSYNC_OPTS=(-a
    --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache'
    --exclude='reports' --exclude='*.egg-info'
  )
  rsync "${RSYNC_OPTS[@]}" "$SKILL_DIR/playwright_kit" "$RUNTIME_DIR/"
  rsync "${RSYNC_OPTS[@]}" "$SKILL_DIR/scripts"          "$RUNTIME_DIR/"
  cp "$SKILL_DIR/uv.lock" "$RUNTIME_DIR/uv.lock"
fi

# ---------- 2) ランタイム用テンプレートをコピー (上書き) ----------
echo "[init] [2/4] runtime テンプレート (pyproject.toml / run.sh / run.bat / .gitignore / README.md)"
copy "$SKILL_DIR/templates/pyproject.toml.runtime" "$RUNTIME_DIR/pyproject.toml"
copy "$SKILL_DIR/templates/run.sh"                  "$RUNTIME_DIR/run.sh"
copy "$SKILL_DIR/templates/run.bat"                 "$RUNTIME_DIR/run.bat"
copy "$SKILL_DIR/templates/runtime-gitignore"       "$RUNTIME_DIR/.gitignore"
copy "$SKILL_DIR/templates/runtime-README.md"       "$RUNTIME_DIR/README.md"
[[ $DRY_RUN -eq 0 ]] && chmod +x "$RUNTIME_DIR/run.sh"

# ---------- 3) 利用者編集物 (上書きしない) ----------
echo "[init] [3/4] 利用者編集物 (scenario.config.yaml / tests/) — 既存があれば skip"
copy_if_absent "$SKILL_DIR/templates/scenario.config.yaml" "$RUNTIME_DIR/scenario.config.yaml"
copy_if_absent "$SKILL_DIR/templates/conftest.py.template" "$RUNTIME_DIR/tests/conftest.py"
for t in test_auth test_list test_form test_dashboard; do
  copy_if_absent "$SKILL_DIR/templates/${t}.py.template" "$RUNTIME_DIR/tests/${t}.py"
done

# ---------- 4) 初回 uv sync + playwright install ----------
if [[ $DRY_RUN -eq 1 ]]; then
  echo "[init] [4/4] dry-run のため uv sync / playwright install はスキップ"
  echo "[init] dry-run 完了"
  exit 0
fi

echo "[init] [4/4] uv sync + playwright install chromium ($RUNTIME_DIR)"
if ! command -v uv >/dev/null 2>&1; then
  echo "[init] WARN: uv が見つかりません。https://docs.astral.sh/uv/ を参照してインストールしてください。"
  echo "[init]       インストール後、$(printf '%q' "$RUNTIME_DIR")/run.sh が初回 uv sync を自動実行します。"
else
  (cd "$RUNTIME_DIR" && uv sync)
  (cd "$RUNTIME_DIR" && uv run playwright install chromium) || {
    echo "[init] WARN: playwright install chromium に失敗しました。"
    echo "[init]       オフライン環境なら PLAYWRIGHT_BROWSERS_PATH を設定してください。"
  }
fi

echo
echo "[done] 初期化完了。次回以降は以下のコマンドで実行できます:"
echo "       cd $(printf '%q' "$RUNTIME_DIR") && ./run.sh"
echo "  または:"
echo "       (cd $(printf '%q' "$PROJECT_ROOT") && ./$RUNTIME_DIR_NAME/run.sh)"
