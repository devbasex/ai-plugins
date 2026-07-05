"""Google API 共通 OAuth2 認証モジュール

CLI とライブラリの両方として使える:

  # ライブラリ用途 (他スキルから import)
  from google_auth import get_credentials
  creds = get_credentials(['drive.file'])  # 既存スコープに追加して再認証

  # CLI: 初回認証 (デフォルトはローカルサーバ port 9123)
  python google_auth.py spreadsheets drive

  # CLI: 手動 copy-paste フロー (ポート不要、Bash 環境で確実)
  python google_auth.py --manual spreadsheets

  # CLI: トークン情報表示 / 削除
  python google_auth.py --show
  python google_auth.py --clear

特徴:
  - トークンは `~/.config/gcloud/google_token.json` に永続化 (`GOOGLE_TOKEN_FILE` で上書き可)
  - 期限切れは `refresh_token` で自動更新
  - 既存トークンのスコープに不足があれば、要求スコープと既存スコープをマージして再認証
  - `--manual` で手動 copy-paste フロー (ローカルサーバ不要、空きポート動的割当)
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

DEFAULT_TOKEN_FILE = Path.home() / ".config" / "gcloud" / "google_token.json"
DEFAULT_PORT = 9123

# よく使うスコープのショートハンド
SCOPE_SHORTCUTS = {
    'spreadsheets':              'https://www.googleapis.com/auth/spreadsheets',
    'spreadsheets.readonly':     'https://www.googleapis.com/auth/spreadsheets.readonly',
    'drive':                     'https://www.googleapis.com/auth/drive',
    'drive.readonly':            'https://www.googleapis.com/auth/drive.readonly',
    'drive.file':                'https://www.googleapis.com/auth/drive.file',
    'script.projects':           'https://www.googleapis.com/auth/script.projects',
    'script.external_request':   'https://www.googleapis.com/auth/script.external_request',
    'chat.messages':             'https://www.googleapis.com/auth/chat.messages',
    'chat.messages.readonly':    'https://www.googleapis.com/auth/chat.messages.readonly',
    'chat.spaces.readonly':      'https://www.googleapis.com/auth/chat.spaces.readonly',
    'calendar':                  'https://www.googleapis.com/auth/calendar',
    'calendar.readonly':         'https://www.googleapis.com/auth/calendar.readonly',
}

DEFAULT_SCOPE = 'https://www.googleapis.com/auth/spreadsheets.readonly'


def _expand_scopes(scopes: list[str]) -> list[str]:
    """ショートハンド or フル URL の混在リストをすべて URL に展開する。"""
    out: list[str] = []
    for s in scopes:
        if s.startswith('https://'):
            out.append(s)
        elif s in SCOPE_SHORTCUTS:
            out.append(SCOPE_SHORTCUTS[s])
        else:
            out.append(f'https://www.googleapis.com/auth/{s}')
    return out


def _resolve_token_file(token_file: str | os.PathLike | None) -> Path:
    if token_file:
        return Path(token_file).expanduser()
    env = os.environ.get('GOOGLE_TOKEN_FILE')
    if env:
        return Path(env).expanduser()
    return DEFAULT_TOKEN_FILE


def _resolve_client_secret(client_secret: str | os.PathLike | None) -> Path:
    """`--client-secret` arg → env `GOOGLE_CLIENT_SECRET` → `<SKILL_DIR>/client_secret.json`
    → `<CWD>/client_secret.json` の順で探す。"""
    candidates: list[Path] = []
    if client_secret:
        candidates.append(Path(client_secret).expanduser())
    env = os.environ.get('GOOGLE_CLIENT_SECRET')
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(SKILL_DIR / 'client_secret.json')
    candidates.append(Path.cwd() / 'client_secret.json')
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "client_secret.json が見つかりません。下記いずれかに配置してください:\n"
        + "\n".join(f"  - {c}" for c in candidates)
        + "\n\nGCP コンソールで OAuth 2.0 クライアント ID (デスクトップアプリ) を作成し、"
          "ダウンロードした JSON を `client_secret.json` として配置:\n"
          "  https://console.cloud.google.com/apis/credentials"
    )


def _save_token(creds: Credentials, token_file: Path) -> None:
    """OAuth トークンを `0600` permission で保存する。

    トークンは access_token / refresh_token を含むため、他ユーザから読まれない
    よう所有者のみ read/write 可とする (CWE-732 対策)。親ディレクトリも `0700` で作成。
    """
    token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file.write_text(creds.to_json())
    token_file.chmod(0o600)
    print(f'Token saved: {token_file} (mode 0600)', file=sys.stderr)


def _pick_free_port() -> int:
    """空きポートを取得 (redirect_uri のダミーとして使う)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _authorize_manual(flow: InstalledAppFlow) -> Credentials:
    """ローカルサーバを立てない手動 copy-paste OAuth フロー。

    redirect_uri は `http://127.0.0.1:<空きポート>/` で動的割当 (どのポートにもサーバを
    立てないので競合しない)。ブラウザは「このサイトにアクセスできません」になるが、
    アドレスバーの URL をユーザに貼り付けてもらえば認可コードを取り出せる。

    `OAUTHLIB_INSECURE_TRANSPORT` は localhost redirect_uri (loopback IP) のための
    一時許可。プロセス全体に影響しないよう try/finally で元の値に復元する (CWE-319 対策)。
    """
    old_insecure = os.environ.get('OAUTHLIB_INSECURE_TRANSPORT')
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    try:
        flow.redirect_uri = f'http://127.0.0.1:{_pick_free_port()}/'
        auth_url, _ = flow.authorization_url(
            access_type='offline', prompt='consent', include_granted_scopes='true'
        )
        print('', file=sys.stderr)
        print('=== 手動認証フロー ===', file=sys.stderr)
        print('1. 以下の URL をブラウザで開いて承認してください:', file=sys.stderr)
        print('', file=sys.stderr)
        print(auth_url, file=sys.stderr)
        print('', file=sys.stderr)
        print(f'2. 承認後、ブラウザは {flow.redirect_uri}?code=... へリダイレクトします', file=sys.stderr)
        print('   (ローカルにサーバを立てていないため接続失敗ページになりますが、', file=sys.stderr)
        print('    アドレスバーの URL をそのままコピーしてください)', file=sys.stderr)
        print('', file=sys.stderr)
        response_url = input('3. その URL をここに貼り付けて Enter: ').strip()
        flow.fetch_token(authorization_response=response_url)
        return flow.credentials
    finally:
        if old_insecure is None:
            os.environ.pop('OAUTHLIB_INSECURE_TRANSPORT', None)
        else:
            os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = old_insecure


def get_credentials(
    scopes: list[str] | None = None,
    *,
    port: int | None = DEFAULT_PORT,
    manual: bool = False,
    client_secret: str | os.PathLike | None = None,
    token_file: str | os.PathLike | None = None,
) -> Credentials:
    """OAuth2 認証情報を取得する。

    Args:
        scopes: 追加スコープ (ショートハンドまたはフル URL)。指定がなければデフォルトの
                spreadsheets.readonly。既存トークンに含まれるスコープは自動的にマージされる。
        port: ローカルコールバックサーバのポート (デフォルト 9123)。`manual=True` の場合は無視。
        manual: True なら手動 copy-paste フローを使う (ポート競合や Bash 環境で有用)。
        client_secret: client_secret.json のパス。省略時は env GOOGLE_CLIENT_SECRET → SKILL_DIR
                       → CWD の順で探す。
        token_file: トークン保存先。省略時は env GOOGLE_TOKEN_FILE →
                    ~/.config/gcloud/google_token.json。
    """
    requested = _expand_scopes(scopes) if scopes else []
    token_path = _resolve_token_file(token_file)

    creds: Credentials | None = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path))
        except Exception as exc:
            print(f'既存トークン読み込み失敗: {exc}', file=sys.stderr)
            creds = None

    existing_scopes = set(creds.scopes or []) if creds else set()
    needed = set(requested) if requested else {DEFAULT_SCOPE}

    # 既存トークンが有効 + 要求スコープを満たしていればそのまま返す
    if creds and creds.valid and needed.issubset(existing_scopes):
        return creds

    # 期限切れだけならリフレッシュ
    if (
        creds and creds.expired and creds.refresh_token
        and needed.issubset(existing_scopes)
    ):
        try:
            creds.refresh(Request())
            _save_token(creds, token_path)
            return creds
        except Exception as exc:
            print(f'トークンリフレッシュ失敗、再認証します: {exc}', file=sys.stderr)
            creds = None

    # 再認証: 既存スコープと要求スコープをマージ
    merged = list(existing_scopes | needed)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(_resolve_client_secret(client_secret)), merged,
    )
    if manual or port is None:
        creds = _authorize_manual(flow)
    else:
        creds = flow.run_local_server(port=port, open_browser=False)
    _save_token(creds, token_path)
    return creds


# --- CLI ---------------------------------------------------------

def _show_token(token_file: Path) -> int:
    if not token_file.exists():
        print(f'トークン未作成: {token_file}')
        return 1
    try:
        data = json.loads(token_file.read_text())
    except Exception as exc:
        print(f'読み取りエラー: {exc}')
        return 1
    print(token_file)
    print('  scopes:')
    for s in data.get('scopes', []):
        print(f'    - {s}')
    if data.get('expiry'):
        print(f"  expiry: {data['expiry']}")
    return 0


def _clear_token(token_file: Path) -> int:
    if token_file.exists():
        token_file.unlink()
        print(f'削除: {token_file}')
        return 0
    print(f'トークンなし: {token_file}')
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Google API 共通 OAuth2 認証')
    p.add_argument('scopes', nargs='*',
                   help='追加スコープ (ショートハンドまたはフル URL)。既存トークンと自動マージ')
    p.add_argument('--client-secret', default=None,
                   help='client_secret.json のパス (env GOOGLE_CLIENT_SECRET または SKILL_DIR/CWD の同名ファイルでも可)')
    p.add_argument('--token-file', default=None,
                   help=f'トークン保存先 (default: {DEFAULT_TOKEN_FILE})')
    p.add_argument('--port', type=int, default=DEFAULT_PORT,
                   help=f'ローカルコールバックサーバのポート (default: {DEFAULT_PORT})')
    p.add_argument('--manual', action='store_true',
                   help='手動 copy-paste フローを使う (ポート競合や Bash 環境で有用)')
    p.add_argument('--show', action='store_true', help='現在のトークン情報を表示して終了')
    p.add_argument('--clear', action='store_true', help='トークンを削除して終了')
    return p


def main() -> int:
    args = _build_parser().parse_args()
    token_path = _resolve_token_file(args.token_file)

    if args.show:
        return _show_token(token_path)
    if args.clear:
        return _clear_token(token_path)

    creds = get_credentials(
        args.scopes or None,
        port=args.port, manual=args.manual,
        client_secret=args.client_secret, token_file=token_path,
    )
    print('認証成功')
    print('スコープ:')
    for s in creds.scopes or []:
        print(f'  - {s}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
