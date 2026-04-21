"""
Google OAuth2 認証スクリプト

Google API（Sheets, Drive, Apps Script等）のOAuth2トークンを取得する。
ポート9123でローカル認証サーバーを起動し、ブラウザ認証後にトークンを保存する。

前提:
  - client_secret.json がカレントディレクトリに配置されていること
  - google-auth-oauthlib がインストールされていること
    uv pip install -r pyproject.toml  (スキルディレクトリのpyproject.toml)
    または
    pip install google-auth-oauthlib

使い方:
  python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py [スコープ...]

  # デフォルト（spreadsheets.readonly）
  python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py

  # スコープ指定
  python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py spreadsheets script.projects

  # フルURL指定も可能
  python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py https://www.googleapis.com/auth/spreadsheets
"""

import argparse
import json
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))

DEFAULT_CLIENT_SECRET_PATH = os.path.join(os.getcwd(), 'client_secret.json')
TOKEN_OUTPUT_PATH = '/tmp/google_token.json'
AUTH_PORT = 9123

# よく使うスコープのショートハンド
SCOPE_SHORTCUTS = {
    'spreadsheets': 'https://www.googleapis.com/auth/spreadsheets',
    'spreadsheets.readonly': 'https://www.googleapis.com/auth/spreadsheets.readonly',
    'drive': 'https://www.googleapis.com/auth/drive',
    'drive.readonly': 'https://www.googleapis.com/auth/drive.readonly',
    'script.projects': 'https://www.googleapis.com/auth/script.projects',
    'script.external_request': 'https://www.googleapis.com/auth/script.external_request',
}

DEFAULT_SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']


def resolve_scopes(args):
    """引数をGoogle APIスコープURLに変換する"""
    if not args:
        return DEFAULT_SCOPES

    scopes = []
    for arg in args:
        if arg.startswith('https://'):
            scopes.append(arg)
        elif arg in SCOPE_SHORTCUTS:
            scopes.append(SCOPE_SHORTCUTS[arg])
        else:
            scopes.append(f'https://www.googleapis.com/auth/{arg}')
    return scopes


def parse_args():
    parser = argparse.ArgumentParser(description='Google OAuth2 認証トークンを取得する')
    parser.add_argument(
        '--client-secret',
        default=DEFAULT_CLIENT_SECRET_PATH,
        help=f'client_secret.json のパス（デフォルト: カレントディレクトリの client_secret.json）',
    )
    parser.add_argument(
        'scopes',
        nargs='*',
        help='Google APIスコープ（ショートハンドまたはフルURL）',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    scopes = resolve_scopes(args.scopes)
    client_secret_path = args.client_secret

    if not os.path.exists(client_secret_path):
        print(f'エラー: {client_secret_path} が見つかりません。')
        print()
        print('client_secret.json の取得手順:')
        print('  1. GCPコンソールを開く: https://console.cloud.google.com/apis/credentials')
        print('  2.「認証情報を作成」→「OAuthクライアントID」→ アプリケーションの種類「デスクトップアプリ」')
        print('  3. 作成後、JSONをダウンロード')
        print(f'  4. ダウンロードしたファイルを {DEFAULT_CLIENT_SECRET_PATH} に配置')
        print()
        print('別の場所にある場合は --client-secret オプションでパスを指定:')
        print(f'  python {__file__} --client-secret /path/to/client_secret.json [スコープ...]')
        sys.exit(1)

    print(f'スコープ: {scopes}')
    print(f'ポート{AUTH_PORT}で認証サーバーを起動します...')
    print('表示されるURLをブラウザで開いてください。')
    print('認証後、自動的にトークンが保存されます。')
    print()

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, scopes)
    creds = flow.run_local_server(port=AUTH_PORT, open_browser=False)

    token_data = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': list(creds.scopes or []),
    }

    with open(TOKEN_OUTPUT_PATH, 'w') as f:
        json.dump(token_data, f, indent=2)

    print(f'認証成功！トークンを {TOKEN_OUTPUT_PATH} に保存しました。')


if __name__ == '__main__':
    main()
