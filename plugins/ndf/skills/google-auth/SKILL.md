---
name: google-auth
description: "Google API (Sheets, Drive, Apps Script, Chat, Calendar 等) の OAuth2 認証ヘルパ。単一トークンファイルで複数 API のスコープを一元管理し、CLI / Python ライブラリ両方として使える。"
when_to_use: "Google API の OAuth2 認証が必要なときに自動参照。Triggers: 'Google認証', 'OAuth', 'google_token', 'spreadsheets', 'Google API', 'client_secret'"
allowed-tools:
  - Read
  - Bash(python *)
  - Bash(uv *)
---

# Google OAuth2 認証ガイド

Google API を使う操作 (スプレッドシート読み書き、GAS 実行、Drive アップロード、Chat メッセージ取得 等) で
認証が必要な場合に参照する。`${CLAUDE_SKILL_DIR}/scripts/google_auth.py` が CLI と Python ライブラリの両方として使える。

## 提供機能

| 機能 | 概要 |
|---|---|
| 単一トークン管理 | `~/.config/gcloud/google_token.json` (永続) に全スコープを保存 |
| 自動スコープマージ | `--scopes drive.file` 等で追加した分を既存スコープと自動マージして再認証 |
| 自動リフレッシュ | 期限切れは `refresh_token` で透過更新 |
| 手動 copy-paste フロー | `--manual` でローカルサーバ不要 (ポート競合・コンテナ環境で有用) |
| `--show` / `--clear` | トークン情報の表示・削除 |
| Python ライブラリ | `from google_auth import get_credentials` で他スキルから import 可能 |

## 前提条件

1. **`client_secret.json` の準備** (初回のみ):
   - [GCP コンソール](https://console.cloud.google.com/apis/credentials) を開く
   - 「認証情報を作成」→「OAuth クライアント ID」→ アプリケーションの種類「デスクトップアプリ」
   - 作成後、JSON をダウンロードして以下のいずれかに `client_secret.json` として配置:
     - `${CLAUDE_SKILL_DIR}/client_secret.json` (推奨、skill ローカル)
     - 環境変数 `GOOGLE_CLIENT_SECRET` で示すパス
     - 実行時のカレントディレクトリ
   - **注意**: `.gitignore` に追加し、絶対にコミットしないこと

2. **依存パッケージのインストール** (skill ローカルの `pyproject.toml` を使う):

   ```bash
   uv pip install -r ${CLAUDE_SKILL_DIR}/pyproject.toml
   # もしくは: uv run --project ${CLAUDE_SKILL_DIR} python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py ...
   ```

## CLI 使用法

認証はインタラクティブなブラウザ操作が必要なので、**ユーザに `!` プレフィックスで実行を案内する**。
エージェントが Bash で直接呼び出すとサーバ待ちでハングする。

```bash
# デフォルト (ローカルサーバ port 9123、スコープ spreadsheets.readonly)
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py

# スコープ指定 (ショートハンド)
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py spreadsheets drive

# スコープ指定 (フル URL)
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py https://www.googleapis.com/auth/script.projects

# 手動 copy-paste フロー (ローカルサーバ不要、ポート競合・コンテナ環境で確実)
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py --manual drive.file

# client_secret.json のパスを明示
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py --client-secret /path/to/client_secret.json drive

# トークン情報を表示 (現在保存されているスコープ等)
python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py --show

# トークン削除 (再認証用)
python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py --clear
```

### 利用可能なスコープショートハンド

| ショートハンド | フル URL |
|---|---|
| `spreadsheets` / `spreadsheets.readonly` | `https://www.googleapis.com/auth/spreadsheets[.readonly]` |
| `drive` / `drive.readonly` / `drive.file` | `https://www.googleapis.com/auth/drive[.readonly|.file]` |
| `script.projects` / `script.external_request` | `https://www.googleapis.com/auth/script.*` |
| `chat.messages` / `chat.messages.readonly` / `chat.spaces.readonly` | `https://www.googleapis.com/auth/chat.*` |
| `calendar` / `calendar.readonly` | `https://www.googleapis.com/auth/calendar[.readonly]` |

未定義のショートハンドは `https://www.googleapis.com/auth/<arg>` として展開される。フル URL も直接渡せる。

### 認証フロー

#### 既定: ローカルサーバ方式

1. スクリプトがポート 9123 (`--port` で変更可) でローカルサーバを起動
2. ユーザが表示された URL をブラウザで開いて承認
3. ブラウザ → ローカルサーバへの自動リダイレクトでトークン取得
4. `~/.config/gcloud/google_token.json` に保存

#### `--manual`: 手動 copy-paste 方式

1. スクリプトが空きポートを動的に確保し、それを redirect_uri に指定 (サーバは立てない)
2. 表示された URL をブラウザで開いて承認
3. リダイレクト先は接続失敗するが、アドレスバーの URL をターミナルにコピー貼り付け
4. スクリプトがその URL から認可コードを取り出してトークン化
5. `~/.config/gcloud/google_token.json` に保存

ポートが空いていないコンテナ・サーバでも確実に動く。

## Python ライブラリ用法

他のスキルやスクリプトから import:

```python
import sys
sys.path.insert(0, '${CLAUDE_SKILL_DIR}/scripts')   # 実際は実行時に解決
from google_auth import get_credentials

# 既存トークン (有効) があればそのまま、期限切れなら自動リフレッシュ
creds = get_credentials()

# 追加スコープが必要な場合のみ指定 (既存スコープと自動マージして再認証)
creds = get_credentials(['drive.file'])

# Bash 環境で確実に動かしたい場合
creds = get_credentials(['drive.file'], manual=True)
```

引数:

| 引数 | デフォルト | 説明 |
|---|---|---|
| `scopes` | `None` (= spreadsheets.readonly) | 追加スコープのリスト |
| `port` | `9123` | ローカルサーバのポート (manual=True 時は無視) |
| `manual` | `False` | True で手動 copy-paste フロー |
| `client_secret` | env `GOOGLE_CLIENT_SECRET` または `${CLAUDE_SKILL_DIR}/client_secret.json` または CWD | client_secret.json のパス |
| `token_file` | env `GOOGLE_TOKEN_FILE` または `~/.config/gcloud/google_token.json` | トークン保存先 |

## トークン管理

- **永続ファイル**: `~/.config/gcloud/google_token.json` (コンテナ再起動でも残る)
- **単一ファイルで全スコープ管理**: `--scopes` で追加するたびに既存スコープと merge して再認証
- **自動リフレッシュ**: 期限切れで `refresh_token` を使って透過更新
- **スコープ不足検出**: 既存トークンに要求スコープが含まれていなければ自動的に再認証フローへ

## トラブルシューティング

### 403 Insufficient scopes

スコープ不足の場合は `--clear` してから必要なスコープ付きで再認証:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py --clear
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py drive.file spreadsheets
```

### 404 File not found / 権限エラー

サービスアカウントにはユーザのファイルへのアクセス権がない。本スキルは **ユーザの OAuth 認証** を使う前提。
`GOOGLE_APPLICATION_CREDENTIALS` 環境変数が設定されていると干渉するので、明示的にクリア:

```bash
GOOGLE_APPLICATION_CREDENTIALS="" python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py drive
```

### ポート競合

ポート 9123 が使用中の場合:

```bash
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py --port 9124 drive
# または手動フロー (ポート不要)
! python ${CLAUDE_SKILL_DIR}/scripts/google_auth.py --manual drive
```

### gcloud CLI での認証 (非推奨)

gcloud CLI の OAuth 認証はインタラクティブ入力の制約で多くのエージェント環境では動作しない。
本スキルの `google_auth.py` を使うこと。
