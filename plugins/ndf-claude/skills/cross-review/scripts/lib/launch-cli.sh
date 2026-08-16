#!/usr/bin/env bash
# 収束ループ共通: CLI をランタイム名で分岐して非対話・背景起動する。
#
# Usage:
#   launch-cli.sh <runtime> <workdir> <prompt-file> <stem> [model] [extra-dir]
#
#   runtime      claude | codex | gemini | kiro
#   workdir      CLI の作業ディレクトリ。**ここから外は触らせない**
#   prompt-file  プロンプト。**必ず標準入力から渡す**（下の注意を参照）
#   stem         出力ファイルのパス接頭辞。`<stem>.pid` / `<stem>-stdout.log` /
#                `<stem>-err.log` を作る。監視スクリプトの `--stem-template` と揃える
#   model        省略可。空文字なら CLI の既定モデルへ委ねる
#   extra-dir    省略可。gemini のときだけ作業領域へ追加する（結果ファイルの置き場所が
#                作業ディレクトリの外にある場合。gemini は領域外への書き込みを拒否する）
#
# **ホストか否かで分岐してはいけない。** ランタイム名だけで分岐する。ホストと同じ
# ランタイムが実装担当になるラウンドでも、ホストのサブエージェント機能は使わず
# 別プロセスの CLI として起動する。これにより「実装した者と評価する者が別」という
# 構造と、ホストセッションの作業文脈を汚さない性質の両方が保たれる。
#
# 注意（実測に基づく）:
#   - codex はコマンド引数でプロンプトを渡すと、標準入力が開いている限り
#     `Reading additional input from stdin...` を表示したまま待ち続ける（600 秒でも
#     終了しない）。必ず標準入力から渡す。
#   - claude の `bypassPermissions` は root 実行で拒否される。継続的インテグレーションや
#     コンテナは root 実行が多いため、`acceptEdits` と `--allowed-tools` の明示を既定にする。
#   - kiro の終了コードは成否を表さない（ツール拒否でもシェルの失敗でも 0）。
#     完了判定は監視スクリプトの結果ファイルと標準エラー出力の照合に委ねる。
#   - kiro のツール絞り込みは防御にならない（シェル実行を許可した時点で迂回できる）。
#     防御は作業ディレクトリの隔離に一本化し、`--trust-all-tools` を使う。

set -euo pipefail

RUNTIME=${1:?runtime required}
WORKDIR=${2:?workdir required}
PROMPT=${3:?prompt file required}
STEM=${4:?output stem required}
MODEL=${5:-}
EXTRA_DIR=${6:-}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

[ -d "$WORKDIR" ] || { echo "作業ディレクトリがありません: $WORKDIR" >&2; exit 1; }
[ -s "$PROMPT" ] || { echo "プロンプトが空です: $PROMPT" >&2; exit 1; }
mkdir -p "$(dirname "$STEM")"

STDOUT_LOG=$STEM-stdout.log
ERR_LOG=$STEM-err.log
PID_FILE=$STEM.pid

# 前回実行の残骸を消してから起動する。残っていると監視側が古い結果を拾う。
rm -f "$STDOUT_LOG" "$ERR_LOG" "$PID_FILE" "$STEM-result.json" "$STEM-progress.log"

# モデル指定。全 4 CLI が `--model` を受ける。空なら CLI の既定へ委ねる。
MODEL_ARGS=()
[ -n "$MODEL" ] && MODEL_ARGS=(--model "$MODEL")

# claude の事前承認ツール。root 実行でも通る組み合わせにする。
CLAUDE_ALLOWED_TOOLS=${NDF_CLAUDE_ALLOWED_TOOLS:-Bash,Read,Write,Edit,Glob,Grep}

cd "$WORKDIR"

case "$RUNTIME" in
  codex)
    nohup codex exec --dangerously-bypass-approvals-and-sandbox \
      --config reasoning.effort=medium -C "$WORKDIR" "${MODEL_ARGS[@]}" \
      < "$PROMPT" > "$STDOUT_LOG" 2> "$ERR_LOG" &
    PID=$!
    ;;
  gemini)
    # shellcheck source=_gemini-env.sh
    . "$SCRIPT_DIR/_gemini-env.sh"
    trap gemini_restore_settings EXIT INT TERM HUP
    gemini_sanitize_settings "$WORKDIR" "$STEM-settings-backup.json" \
      "$STEM-settings-sanitized.json"
    # 結果ファイルの置き場所が作業ディレクトリの外にあるときは、作業領域へ追加する。
    # 追加しないと `write_file` が拒否され、結果ファイルが 1 つも残らない。
    INCLUDE_ARGS=()
    [ -n "$EXTRA_DIR" ] && INCLUDE_ARGS=(--include-directories "$EXTRA_DIR")
    # ⚠ --skip-trust と GEMINI_CLI_TRUST_WORKSPACE=true は両方必須。
    # 片方だけでは新規パスが untrusted と判定され --yolo が "default" へ降格する。
    GEMINI_CLI_TRUST_WORKSPACE=true nohup gemini --yolo --skip-trust \
      --output-format text "${MODEL_ARGS[@]}" "${INCLUDE_ARGS[@]}" -p "" \
      < "$PROMPT" > "$STDOUT_LOG" 2> "$ERR_LOG" &
    PID=$!
    ;;
  claude)
    nohup claude -p \
      --permission-mode acceptEdits \
      --allowed-tools "$CLAUDE_ALLOWED_TOOLS" \
      --output-format json "${MODEL_ARGS[@]}" \
      < "$PROMPT" > "$STDOUT_LOG" 2> "$ERR_LOG" &
    PID=$!
    ;;
  kiro)
    nohup kiro-cli chat --no-interactive --trust-all-tools "${MODEL_ARGS[@]}" \
      < "$PROMPT" > "$STDOUT_LOG" 2> "$ERR_LOG" &
    PID=$!
    ;;
  *)
    echo "未知のランタイムです: $RUNTIME" >&2
    exit 1
    ;;
esac

echo "$PID" > "$PID_FILE"
disown 2>/dev/null || true

if [ "$RUNTIME" = "gemini" ]; then
  # gemini は起動時に 1 度だけ設定ファイルを読む。読み終わってから元へ戻し、
  # 作業ディレクトリを汚れたままにしない。
  if [ -n "${GEMINI_SETTINGS_BACKUP:-}" ] && [ -f "$GEMINI_SETTINGS_BACKUP" ]; then
    sleep 2
    gemini_restore_settings
  fi
fi

echo "🚀 $RUNTIME launched (pid=$PID, model=${MODEL:-default})" >&2
