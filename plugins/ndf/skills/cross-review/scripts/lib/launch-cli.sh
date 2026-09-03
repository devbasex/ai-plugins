#!/usr/bin/env bash
# 収束ループ共通: CLI をランタイム名で分岐して非対話・背景起動する。
#
# Usage:
#   launch-cli.sh <runtime> <workdir> <prompt-file> <stem> [model] [extra-dir] [timeout]
#
#   runtime      claude | codex | agy | kiro
#   workdir      CLI の作業ディレクトリ。**ここから外は触らせない**
#   prompt-file  プロンプト。渡し方は CLI で違う（下の注意を参照）
#   stem         出力ファイルのパス接頭辞。`<stem>.pid` / `<stem>-stdout.log` /
#                `<stem>-err.log` を作る。監視スクリプトの `--stem-template` と揃える
#   model        省略可。空文字なら CLI の既定モデルへ委ねる
#   extra-dir    省略可。agy のときだけ作業領域へ追加する（結果ファイルの置き場所が
#                作業ディレクトリの外にある場合）
#   timeout      省略可。agy の `--print-timeout` へ渡す秒数。**フェーズの監視の上限
#                以上**にする。CLI が先に打ち切ると結果ファイルが残らず、監視からは
#                起動できなかった場合と区別が付かない
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
#   - agy は逆で、標準入力からプロンプトを受け取らない（`empty prompt` で落ちる）。
#     `-p=<本文>` の形で渡し、**`-p` より後ろにフラグを置かない**（後ろの引数が
#     プロンプトとして読まれる）。
#   - agy は現在地を作業領域にしない。`--add-dir` で宣言しないと、利用者の見えない
#     場所で作業する。渡す範囲は作業ディレクトリと結果ファイルの置き場所に限る。
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
# 既定は、いちばん長いフェーズ（適用・修正の 3600 秒）を覆う値にする。
PRINT_TIMEOUT=${7:-3600}

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
  agy)
    # 作業領域は作業ディレクトリと、結果ファイルの置き場所だけを宣言する。
    ADD_DIR_ARGS=(--add-dir "$WORKDIR")
    [ -n "$EXTRA_DIR" ] && ADD_DIR_ARGS+=(--add-dir "$EXTRA_DIR")
    # `--print-timeout` は単位付きの時間を取る（数字だけでは `missing unit` で落ちる）。
    nohup agy --dangerously-skip-permissions --output-format text \
      --print-timeout "${PRINT_TIMEOUT}s" "${MODEL_ARGS[@]}" "${ADD_DIR_ARGS[@]}" \
      -p="$(cat "$PROMPT")" \
      < /dev/null > "$STDOUT_LOG" 2> "$ERR_LOG" &
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

echo "🚀 $RUNTIME launched (pid=$PID, model=${MODEL:-default})" >&2
