#!/usr/bin/env bash
# コンテナ内から SSH 経由でホスト (macOS / Linux) の Chrome を
# リモートデバッグモードで起動するスクリプト。
#
# 背景:
#   Docker コンテナはホストとプロセス空間が分離されているため、
#   コンテナから直接ホストのプロセスを起動できない。
#   ホストの sshd (macOS: システム設定 > 共有 > リモートログイン) に接続し、
#   起動コマンドを実行することで間接的にホスト Chrome を起動する。
#
#   SSH が使えない場合 (鍵未登録・リモートログイン無効など) は、
#   ホスト側で手動実行する起動コマンドを案内し、起動されるまで待機する
#   フォールバックに切り替わる。
#
# 前提 (SSH 自動起動を使う場合のみ・ホスト側で一度だけ設定):
#   1) リモートログインを有効化  (macOS: sudo systemsetup -setremotelogin on)
#   2) コンテナの公開鍵を ~/.ssh/authorized_keys に登録 (パスワードレス実行)
#   3) コンソールにログイン中の本人ユーザーで SSH すること
#      (GUI アプリは WindowServer 接続のためコンソールセッション所有者が必要)
#
# 使い方 (コンテナ内):
#   # SSH 自動起動
#   HOST_SSH_USER=<macのユーザー名> ./scripts/start-host-chrome.sh
#   # SSH を使わず手動起動の案内のみ
#   ./scripts/start-host-chrome.sh
#
# 環境変数:
#   HOST_SSH_USER         ホストのログインユーザー名。未設定なら手動フォールバック
#   HOST_SSH_HOST         SSH 接続先 (default: host.docker.internal)
#   CDP_HOST              CDP 疎通確認先ホスト (default: HOST_SSH_HOST)
#   CDP_PORT              リモートデバッグポート (default: 9222)
#   CDP_BIND_ADDRESS      Chrome の listen address。空 (default) なら付与せず
#                         Chrome 既定の loopback bind (macOS Docker Desktop は
#                         host.docker.internal がホスト loopback に到達するため
#                         これで動作する)。Linux/WSL2 等で loopback bind だと
#                         コンテナから到達できない場合のみ 0.0.0.0 等を指定する。
#                         0.0.0.0 は全インターフェース公開のためセキュリティ注意。
#   CHROME_USER_DATA_DIR  起動プロファイル (default: /tmp/chrome-debug)
#                         空にするとデフォルトプロファイル (ログイン済み Session) を使用
#   CHROME_BIN            Chrome バイナリパス
#                         (default: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome)
#   STARTUP_TIMEOUT       SSH 起動後の待機タイムアウト秒 (default: 30)
#   MANUAL_WAIT           手動フォールバック時の待機秒。0 で待たず即終了 (default: 120)
set -euo pipefail

HOST_SSH_USER="${HOST_SSH_USER:-}"
HOST_SSH_HOST="${HOST_SSH_HOST:-host.docker.internal}"
CDP_HOST="${CDP_HOST:-$HOST_SSH_HOST}"
CDP_PORT="${CDP_PORT:-9222}"
CDP_BIND_ADDRESS="${CDP_BIND_ADDRESS:-}"
CHROME_USER_DATA_DIR="${CHROME_USER_DATA_DIR-/tmp/chrome-debug}"
CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-30}"
MANUAL_WAIT="${MANUAL_WAIT:-120}"

# curl は冪等チェック・起動待機・手動フォールバックの全経路で CDP 疎通確認に
# 使う必須コマンド。無いと終了コード 127 で常に未起動扱いとなり、起動成功時でも
# タイムアウトしてしまうため、ここでフェイルファストする。
if ! command -v curl >/dev/null 2>&1; then
  echo "✗ curl が見つかりません。CDP 疎通確認に必須です。" >&2
  echo "  コンテナに curl をインストールしてから再実行してください" >&2
  echo "  (例: apt-get install -y curl / apk add curl)。" >&2
  exit 1
fi

cdp_up() {
  # --max-time でネットワークハング時も待機ループ周期が壊れないようにする
  curl -sf --max-time 2 "http://${CDP_HOST}:${CDP_PORT}/json/version" >/dev/null 2>&1
}

# --user-data-dir は空文字なら付与しない (デフォルトプロファイル使用)
userdata_arg=""
if [ -n "${CHROME_USER_DATA_DIR}" ]; then
  userdata_arg="--user-data-dir='${CHROME_USER_DATA_DIR}'"
fi

# --remote-debugging-address は CDP_BIND_ADDRESS が空なら付与しない。
#   既定 (空) では Chrome は loopback (127.0.0.1) のみ listen する。macOS
#   Docker Desktop は host.docker.internal がホストの loopback に到達するため
#   これで動作する (ユーザー実証済み・0.0.0.0 不要)。
#   Linux/WSL2 等で loopback bind だとコンテナから到達できない場合のみ
#   CDP_BIND_ADDRESS=0.0.0.0 等を指定する。0.0.0.0 は CDP を全インターフェース
#   へ公開し、CDP は認証なしでブラウザ操作できるためセキュリティに注意すること。
bind_arg=""
if [ -n "${CDP_BIND_ADDRESS}" ]; then
  bind_arg="--remote-debugging-address=${CDP_BIND_ADDRESS} "
fi

# ホスト側で実行する Chrome 起動コマンド (人間がコピペできる体裁)
host_launch_cmd() {
  printf '"%s" --remote-debugging-port=%s %s%s --remote-allow-origins=* --disable-features=DialMediaRouteProvider' \
    "${CHROME_BIN}" "${CDP_PORT}" "${bind_arg}" "${userdata_arg}"
}

# 手動フォールバック: ホストで実行するコマンドを案内し、起動を待機する
manual_fallback() {
  local reason="$1"
  echo "" >&2
  echo "──────────────────────────────────────────────────────────────" >&2
  echo "⚠ SSH 自動起動を利用できません (${reason})。" >&2
  echo "  ホスト (mac) 側のターミナルで以下を実行してください:" >&2
  echo "" >&2
  echo "    $(host_launch_cmd)" >&2
  echo "" >&2
  echo "──────────────────────────────────────────────────────────────" >&2

  if [ "${MANUAL_WAIT}" -le 0 ]; then
    echo "✗ Chrome 未起動のまま終了します (MANUAL_WAIT=0)。" >&2
    exit 1
  fi

  echo "→ ホストでの起動を待機中 (最大 ${MANUAL_WAIT}s, Ctrl-C で中断)..." >&2
  # seq に依存せず bash 組み込みの算術 for ループを使う (最小コンテナ対応)
  for ((i = 0; i < MANUAL_WAIT; i++)); do
    if cdp_up; then
      echo "✓ Chrome 起動を検知しました: http://${CDP_HOST}:${CDP_PORT}"
      exit 0
    fi
    sleep 1
  done
  echo "✗ 待機タイムアウト。手動起動後に再実行してください。" >&2
  exit 1
}

# 1) 既に起動済みなら何もしない (冪等)
if cdp_up; then
  echo "✓ Chrome は既に CDP http://${CDP_HOST}:${CDP_PORT} で起動済みです"
  exit 0
fi

# 2) HOST_SSH_USER 未設定 → 手動フォールバック
if [ -z "${HOST_SSH_USER}" ]; then
  manual_fallback "HOST_SSH_USER が未設定"
fi

# 3) ssh コマンドが無い → 手動フォールバック
if ! command -v ssh >/dev/null 2>&1; then
  manual_fallback "コンテナに ssh クライアントが無い"
fi

# 4) SSH でホストに接続し、Chrome をバックグラウンド起動
echo "→ ${HOST_SSH_USER}@${HOST_SSH_HOST} で Chrome を起動します..."
# </dev/null で stdin を切り離し、SSH 非対話セッションのハングを防ぐ
remote_cmd="nohup $(host_launch_cmd) </dev/null >/tmp/chrome-debug.log 2>&1 &"
if ! ssh -o StrictHostKeyChecking=accept-new \
         -o BatchMode=yes \
         -o ConnectTimeout=10 \
         "${HOST_SSH_USER}@${HOST_SSH_HOST}" \
         "${remote_cmd}"; then
  # SSH 失敗 (鍵未登録・リモートログイン無効・到達不可など) → 手動フォールバック
  manual_fallback "SSH 接続/実行に失敗"
fi

# 5) CDP が応答するまで待機
echo "→ CDP エンドポイントの起動を待機中 (最大 ${STARTUP_TIMEOUT}s)..."
# seq に依存せず bash 組み込みの算術 for ループを使う (最小コンテナ対応)
for ((i = 0; i < STARTUP_TIMEOUT; i++)); do
  if cdp_up; then
    echo "✓ Chrome 起動完了: http://${CDP_HOST}:${CDP_PORT}"
    exit 0
  fi
  sleep 1
done

echo "✗ 起動タイムアウト。ホストの /tmp/chrome-debug.log を確認してください。" >&2
exit 1
