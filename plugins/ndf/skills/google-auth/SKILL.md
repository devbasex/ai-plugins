---
name: google-auth
description: |
  Google API（Sheets, Drive, Apps Script等）のOAuth2認証が必要な操作を行う際に自動参照。
  トークン取得手順、スコープ指定、認証済みトークンの利用方法を扱う。

  Triggers: "Google認証", "OAuth", "google_token", "spreadsheets", "Google API", "client_secret"
allowed-tools:
  - Read
  - Bash(python *)
  - Bash(uv *)
  - Bash(pip *)
---

# Google OAuth2 認証ガイド

Google API を使用する操作（スプレッドシート読み書き、GAS実行等）で認証が必要な場合に参照する。

## 認証スクリプト

`${CLAUDE_SKILL_DIR}/scripts/google_auth.py` を使用する。

### 前提条件

1. **`client_secret.json` の準備**（初回のみ）:
   - [GCPコンソール](https://console.cloud.google.com/apis/credentials) を開く
   - 「認証情報を作成」→「OAuthクライアントID」→ アプリケーションの種類「デスクトップアプリ」
   - 作成後、JSONをダウンロードしてプロジェクトルートに `client_secret.json` として配置
   - **注意**: `.gitignore` に追加し、絶対にコミットしないこと

   **client_secret.jsonが見つからない場合の案内**: 上記手順をユーザーに伝え、配置後に再実行を依頼する。

2. 依存パッケージのインストール:

```bash
# uv環境の場合（推奨）
uv pip install -r ${CLAUDE_SKILL_DIR}/pyproject.toml

# pip の場合
pip install google-auth-oauthlib
```

### 実行方法

ユーザーに以下のコマンドを案内し、`!` プレフィックスで実行してもらう（インタラクティブな認証のため）:

```bash
# デフォルト（spreadsheets.readonly）
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py

# スコープ指定（ショートハンド）
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py spreadsheets drive

# スコープ指定（フルURL）
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py https://www.googleapis.com/auth/script.projects

# client_secret.json のパスを明示的に指定
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py --client-secret /path/to/client_secret.json spreadsheets
```

### 利用可能なスコープショートハンド

| ショートハンド | フルスコープ |
|--------------|------------|
| `spreadsheets` | `https://www.googleapis.com/auth/spreadsheets` |
| `spreadsheets.readonly` | `https://www.googleapis.com/auth/spreadsheets.readonly` |
| `drive` | `https://www.googleapis.com/auth/drive` |
| `drive.readonly` | `https://www.googleapis.com/auth/drive.readonly` |
| `script.projects` | `https://www.googleapis.com/auth/script.projects` |
| `script.external_request` | `https://www.googleapis.com/auth/script.external_request` |

### 認証フロー

1. スクリプトがポート9123でローカルサーバーを起動
2. ユーザーが表示されたURLをブラウザで開いて認証
3. トークンが `/tmp/google_token.json` に保存される

## 認証済みトークンの利用

認証後、`/tmp/google_token.json` にトークンが保存される。Python から利用する場合:

```python
import json
from google.oauth2.credentials import Credentials

with open('/tmp/google_token.json') as f:
    token_data = json.load(f)

creds = Credentials(
    token=token_data['token'],
    refresh_token=token_data['refresh_token'],
    token_uri=token_data['token_uri'],
    client_id=token_data['client_id'],
    client_secret=token_data['client_secret'],
    scopes=token_data['scopes'],
)
```

## 注意事項

- 認証はインタラクティブなブラウザ操作が必要なため、**エージェントが直接実行することはできない**
- ユーザーに `! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py [スコープ]` の実行を案内すること
- トークンは `/tmp/google_token.json` に保存されるため、コンテナ再起動で消える
- `client_secret.json` は `.gitignore` に含まれており、リポジトリにコミットされない
