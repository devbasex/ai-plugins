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
#                `<stem>-err.log` / `<stem>-launch.json`（起動の記録）を作る。
#                監視スクリプトの `--stem-template` と揃える
#   model        省略可。空文字なら CLI の既定モデルへ委ねる
#   extra-dir    省略可。agy のときだけ作業領域へ追加する（結果ファイルの置き場所が
#                作業ディレクトリの外にある場合）
#   timeout      省略可。agy の `--print-timeout`（CLI の上限）。次のどれかを渡す
#                  工程名  上限の表（`limits.py`）の工程。`limits.py cli-timeout <工程> <runtime>`
#                          の値（環境変数で解決した監視の上限 + 120 秒）を使う
#                  数字    その秒数（従来どおり）
#                  空      `implement` の工程名と同じ（いちばん長い工程を覆う）
#                **CLI の上限は監視の上限より長くする。** CLI が先に打ち切ると結果ファイルが
#                残らず、監視からは起動できなかった場合と区別が付かない（#598 / #537）
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

# 包みが作業ディレクトリへ `cd` した後に記録の部品を呼ぶため、絶対パスで控える（`pwd` は symlink を畳まない）。
LIB_DIR=$(cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

RUNTIME=${1:?runtime required}
WORKDIR=${2:?workdir required}
PROMPT=${3:?prompt file required}
STEM=${4:?output stem required}
MODEL=${5:-}
EXTRA_DIR=${6:-}
# 空なら、いちばん長い工程（適用・修正）を覆う値にする。
PRINT_TIMEOUT=${7:-implement}

[ -d "$WORKDIR" ] || { echo "作業ディレクトリがありません: $WORKDIR" >&2; exit 1; }
[ -s "$PROMPT" ] || { echo "プロンプトが空です: $PROMPT" >&2; exit 1; }
mkdir -p "$(dirname "$STEM")"

# **宣言の外のランタイムは起動しない（#1598 の I5）。** どの経路から呼ばれても、作業ディレクトリの
# リポジトリの `.ndf/runtimes.json` と照らし、外か宣言が壊れていれば起動せずに終了コード 3 で終える。
python3 "$(dirname -- "${BASH_SOURCE[0]}")/runtime_policy.py" check "$RUNTIME" --root "$WORKDIR" || {
  echo "宣言により起動しません: $RUNTIME" >&2; exit 3; }

# 工程名を CLI の上限の秒数へ解決する。**表に無い名前は起動せずに終える。**
# 上限の表は `limits.py` だけが持つ（値を使うのは agy だけだが、名前のチェックは全ランタイムで行う）。
# **`cd` で登らない。** 表の位置は文字列のまま渡す（Kiro CLI の symlink を字句で畳まない）。
resolve_print_timeout() {
  case "$PRINT_TIMEOUT" in
    ''|*[!0-9]*)
      # **工程名を先に控える。** 代入に使うと、コマンド置換が失敗した時点で
      # `PRINT_TIMEOUT` が空になり、案内が渡された工程名ではなく既定の `implement` を出す。
      local limits phase=${PRINT_TIMEOUT:-implement}
      limits=$(dirname -- "${BASH_SOURCE[0]}")/limits.py
      PRINT_TIMEOUT=$(python3 "$limits" cli-timeout "$phase" "$RUNTIME") || {
        echo "CLI の上限を決められません（工程: ${phase}）" >&2; exit 1; }
      ;;
  esac
}

# stem に属する出力パスを決め、前回実行の残骸を消す。残っていると監視側が古い結果を拾う。
# **監視の結果ファイル（`<stem>-monitor.json`）も消す。** 読む側は stem から 1 つに引くため、
# 残っていると前の起動の理由を今回のものと読む。追記だけの記録
# （`monitor-outcomes.jsonl`）は過去を残すためのものなので消さない（#662）。
prepare_artifacts() {
  STDOUT_LOG=$STEM-stdout.log
  ERR_LOG=$STEM-err.log
  # 包みは作業ディレクトリへ `cd` した後に記録を書くため、stem を絶対パスで控える。
  STEM_ABS=$(cd "$(dirname "$STEM")" && pwd)/$(basename "$STEM")
  PID_FILE=$STEM.pid
  rm -f "$STDOUT_LOG" "$ERR_LOG" "$PID_FILE" "$STEM-result.json" "$STEM-progress.log" \
    "$STEM-monitor.json" "$STEM-launch.json"
  # 出力のファイルは起動から戻る時点で在るようにする（包みのサブシェルが開くより先に読まれても欠けない）。
  : > "$STDOUT_LOG"
  : > "$ERR_LOG"
}

# 起動の記録（`<stem>-launch.json`）を書く（#759）。実際に動いたモデルを取る側
# （`models.observed_model`）が、開始の時刻と作業ディレクトリでランタイムの記録を結びつける。
# **`cd` の前に書く。** 後で書くと、相対の stem が作業ディレクトリの下に落ちる。
# 時刻は秒まで（BSD の `date` でも同じ形）。切り捨てるため、実際の起動より前か同じになる。
# python3 を使わない（上限を数字で渡せば python3 の無い環境でも起動まで進む）。JSON の文字列に要る
# 逆斜線と二重引用符だけを逃がす。
write_launch_record() {
  local real_workdir started_at
  real_workdir=$(cd "$WORKDIR" && pwd -P)
  real_workdir=${real_workdir//\\/\\\\}
  real_workdir=${real_workdir//\"/\\\"}
  started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"runtime": "%s", "workdir": "%s", "started_at": "%s"}\n' \
    "$RUNTIME" "$real_workdir" "$started_at" > "$STEM-launch.json"
}

# ランタイム名で分岐して CLI を前景で走らせる（包みのサブシェルの中で呼ぶ）。
# **ホストか否かで分岐してはいけない。** ランタイム名だけで分岐する。ホストと同じ
# ランタイムが実装担当になるラウンドでも、ホストのサブエージェント機能は使わず
# 別プロセスの CLI として起動する。これにより「実装した者と評価する者が別」という
# 構造と、ホストセッションの作業文脈を汚さない性質の両方が保たれる。
run_runtime() {
  case "$RUNTIME" in
    codex)
      codex exec --dangerously-bypass-approvals-and-sandbox \
        --config model_reasoning_effort=medium -C "$WORKDIR" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
        < "$PROMPT" > "$STDOUT_LOG" 2> "$ERR_LOG"
      ;;
    agy)
      # 作業領域は作業ディレクトリと、結果ファイルの置き場所だけを宣言する。
      local add_dir_args=(--add-dir "$WORKDIR")
      [ -n "$EXTRA_DIR" ] && add_dir_args+=(--add-dir "$EXTRA_DIR")
      # `--print-timeout` は単位付きの時間を取る（数字だけでは `missing unit` で落ちる）。
      # 使用量は JSON の出力にしか無い（#1953 の決定 3）。本文は `response` の鍵にある。
      agy --dangerously-skip-permissions --output-format json \
        --print-timeout "${PRINT_TIMEOUT}s" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} "${add_dir_args[@]}" \
        -p="$(cat "$PROMPT")" \
        < /dev/null > "$STDOUT_LOG" 2> "$ERR_LOG"
      ;;
    claude)
      # `--allowed-tools` は許可を絞るだけで、Tool の定義・MCP・Skill の一覧は文脈に残る。#827 の最小構成と
      # 同じく `--tools` を許可と同じ値にし、MCP とスラッシュコマンドを読まない（Skill はプロンプトがパスで渡す）。
      claude -p ${CLAUDE_SETTINGS_ARGS[@]+"${CLAUDE_SETTINGS_ARGS[@]}"} \
        --permission-mode acceptEdits \
        --tools "$CLAUDE_ALLOWED_TOOLS" \
        --allowed-tools "$CLAUDE_ALLOWED_TOOLS" \
        --strict-mcp-config --disable-slash-commands \
        --output-format json ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
        < "$PROMPT" > "$STDOUT_LOG" 2> "$ERR_LOG"
      ;;
    kiro)
      kiro-cli chat --no-interactive --trust-all-tools ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
        < "$PROMPT" > "$STDOUT_LOG" 2> "$ERR_LOG"
      ;;
  esac
}

# 包みのサブシェル（#1953 の決定 6）。CLI を前景で走らせ、終わった後に使用量の帳簿へ 1 行を書く。
# `nohup` と同じく HUP を無視する（無視は CLI へ継がれる）。止める側の TERM はプロセスグループの全員へ届き、
# CLI が終わった後で trap が `killed` を控える。帳簿への記録の失敗は終了コードに出さない（I5）。
run_wrapped() {
  trap '' HUP
  local rc=0 status=
  trap 'status=killed' TERM INT
  run_runtime || rc=$?
  # 記録の間は TERM / INT を無視する（無視は python3 へ継がれる）。監視は CLI が終わった後も包みのグループへ
  # TERM を送るため、既定の扱いのままでは記録が殺されて行が残らない。上限は猶予の後の KILL が保つ。
  trap '' TERM INT
  python3 "$LIB_DIR/usage_capture.py" record-cli --stem "$STEM_ABS" --runtime "$RUNTIME" --rc "$rc" \
    ${status:+--status "$status"} --model "$MODEL" < /dev/null > /dev/null 2>> "$ERR_LOG" || true
  return "$rc"
}

# 包みを背景で起動し、PID を `PID` へ入れる。`<stem>.pid` は CLI ではなく包みを指す。包みの起動の引数は
# この script と同じで、ランタイム名を含む（監視の起動の引数の照合はそのまま通る）。
launch_runtime() {
  case "$RUNTIME" in
    codex | agy | claude | kiro) ;;
    *)
      echo "未知のランタイムです: $RUNTIME" >&2
      exit 1
      ;;
  esac
  run_wrapped < /dev/null > /dev/null 2>&1 &
  PID=$!
}

resolve_print_timeout

prepare_artifacts

# モデル指定。全 4 CLI が `--model` を受ける。空なら CLI の既定へ委ねる。
MODEL_ARGS=()
[ -n "$MODEL" ] && MODEL_ARGS=(--model "$MODEL")

# claude の事前承認ツール。root 実行でも通る組み合わせにする。
CLAUDE_ALLOWED_TOOLS=${NDF_CLAUDE_ALLOWED_TOOLS:-Bash,Read,Write,Edit,Glob,Grep}

# 従量の接続の区間から起動する claude にも、宣言の変数を `--settings` の env で渡す（#1543）。
# 利用者の settings.json の env は環境変数より優先されるため、環境変数だけでは宣言が負ける。
# 組み立ては relay・supervise.py と同じ `claude_settings.py` が持つ。組めなければ今までどおり起動する。
CLAUDE_SETTINGS_ARGS=()
if [ "$RUNTIME" = claude ] && [ "${NDF_CLAUDE_ACCOUNT:-}" = metered ]; then
  CLAUDE_SETTINGS=$(python3 "$(dirname -- "${BASH_SOURCE[0]}")/claude_settings.py") || {
    echo "従量の接続の宣言を --settings へ組めません（環境変数だけで起動します）" >&2; CLAUDE_SETTINGS=; }
  if [ -n "$CLAUDE_SETTINGS" ]; then CLAUDE_SETTINGS_ARGS=(--settings "$CLAUDE_SETTINGS"); fi
fi

write_launch_record

cd "$WORKDIR"

# **CLI を独立したプロセスグループで起動する（#584 / #729 の決定 10）。** ジョブ制御を
# 有効にして背景起動すると、包みの pid がそのままプロセスグループの番号になり、前景で走る CLI も
# 同じグループに入る（サブシェルの中ではジョブ制御が切れる）。監視は止めるときにグループへ
# シグナルを送るので、CLI の子プロセスが止めた後に結果ファイルを書かない。`setsid` は macOS に標準で無いため使わない。起動の直後に戻し、この script の
# 残りはジョブ制御の影響を受けない。`set -m` は bash 3.2（macOS）にもある（bash.1 の
# 「Monitor mode. Job control is enabled.」）。
set -m
launch_runtime
set +m

echo "$PID" > "$PID_FILE"
disown 2>/dev/null || true

echo "🚀 $RUNTIME launched (pid=$PID, model=${MODEL:-default})" >&2
