---
name: google-chat
description: "Google Chat API でスペースのメッセージ読み取り・スペース一覧を取得する。WebFetch は認証付き Chat ページに非対応のため、Chat API + OAuth2 ユーザー認証で取得する (認証は ndf:google-auth に委譲)。"
when_to_use: "Google Chat スペースのメッセージ取得・スペース一覧が必要なとき。Triggers: 'Google Chat', 'chat.spaces', 'chat.messages', 'Chatスペース', 'メッセージ取得', 'チャット履歴'"
allowed-tools:
  - Read
  - Bash(python *)
  - Bash(uv *)
---

# Google Chat アクセス

## 概要

Google Chat API (Python) を使ってチャットスペースのメッセージを取得する。
認証は `ndf:google-auth` スキルの共通モジュール (`get_credentials()`) を使用。

## 提供物

```
google-chat/
├── SKILL.md                  ← このファイル
├── pyproject.toml            ← uv プロジェクト (Chat API 依存)
└── scripts/
    └── gchat_read.py         ← CLI: メッセージ一覧 / スペース一覧
```

`gchat_read.py` は実行時に `ndf:google-auth` スキルの `google_auth.py` を sys.path に追加して
`get_credentials()` を呼ぶ。`google-auth` 側で OAuth2 トークンを取得済みであれば追加の認証は不要。

## 前提条件

| 項目 | 値 |
|---|---|
| 認証 | `ndf:google-auth` スキル (共通 OAuth2 モジュール) |
| Python 実行 | `uv run --project ${CLAUDE_SKILL_DIR} python ...` または `uv run --with ...` |
| client_secret.json | `ndf:google-auth` の手順で配置済み |
| 既存トークン | `~/.config/gcloud/google_token.json` (`ndf:google-auth` で取得済み) |
| Cloud Console | **Google Chat API の有効化**が必要 |
| アカウント | Google Workspace (Business / Enterprise)。無料 Gmail では利用不可 |

## URL から Space ID を取得

Google Chat URL の末尾がそのまま Space ID になる。

```
https://mail.google.com/mail/u/0/#chat/space/AAQA6AWG1iE
                                               ^^^^^^^^^^^
                                               これが Space ID
```

API 呼び出し時は `spaces/AAQA6AWG1iE` の形式で指定する (スクリプトは ID のみ受け取る)。

## クイックスタート

```bash
SKILL_DIR=${CLAUDE_SKILL_DIR}
SCRIPT=$SKILL_DIR/scripts/gchat_read.py

# スペース一覧を表示
uv run --project $SKILL_DIR python $SCRIPT --list-spaces

# メッセージ一覧 (デフォルト Space ID)
uv run --project $SKILL_DIR python $SCRIPT

# Space ID を指定
uv run --project $SKILL_DIR python $SCRIPT --space AAQA6AWG1iE

# 日付フィルタ (RFC-3339 形式)
uv run --project $SKILL_DIR python $SCRIPT --space AAQA6AWG1iE \
  --after "2024-01-01T00:00:00+09:00"

# 出力先を変更
uv run --project $SKILL_DIR python $SCRIPT --space AAQA6AWG1iE --output /tmp/my_chat.json
```

### 出力

- `--output` で指定した JSON ファイル (デフォルト: `/tmp/gchat_messages.json`)
- 標準出力に直近 5 件のプレビュー

## API パラメータリファレンス

### ListMessagesRequest

| パラメータ | 型 | 説明 |
|---|---|---|
| `parent` | string | `spaces/{space_id}` 形式 (必須) |
| `page_size` | int | 最大取得件数 (デフォルト 25、最大 1,000) |
| `page_token` | string | ページネーション用トークン |
| `filter` | string | `createTime` や `thread.name` でフィルタ |
| `order_by` | string | `createTime ASC` または `createTime DESC` |
| `show_deleted` | bool | 削除済みメッセージを含めるか |

### フィルタ構文

```
# 特定日時以降
createTime > "2024-01-01T00:00:00+09:00"

# 日付範囲
createTime > "2024-03-01T00:00:00+09:00" AND createTime < "2024-04-01T00:00:00+09:00"

# スレッド指定
thread.name = "spaces/AAQA6AWG1iE/threads/THREAD_ID"
```

### 必要な OAuth スコープ

| スコープ | 用途 |
|---|---|
| `chat.spaces.readonly` | スペース一覧取得 (読み取り専用) |
| `chat.messages.readonly` | メッセージ一覧取得 (読み取り専用) |
| `chat.messages` | メッセージ読み書き (送信が必要な場合) |

`ndf:google-auth` でこれらのスコープを取得しておく:

```bash
! python ${CLAUDE_SKILLS_DIR:-${CLAUDE_PROJECT_DIR}/.claude/skills}/google-auth/scripts/google_auth.py \
  chat.messages.readonly chat.spaces.readonly
```

## トラブルシューティング

### 403 PERMISSION_DENIED

- Google Cloud Console で Chat API が有効化されているか確認
- OAuth クライアント ID が Desktop app 用か確認
- Google Workspace (Business / Enterprise) アカウントでログインしているか確認
- 無料 Gmail アカウントでは利用不可

### 403 Insufficient scopes

スコープ変更時はトークンを削除して再認証 (`ndf:google-auth` の `--clear` を使う):

```bash
python ${CLAUDE_SKILLS_DIR:-${CLAUDE_PROJECT_DIR}/.claude/skills}/google-auth/scripts/google_auth.py --clear
! python ${CLAUDE_SKILLS_DIR:-${CLAUDE_PROJECT_DIR}/.claude/skills}/google-auth/scripts/google_auth.py \
  chat.messages.readonly chat.spaces.readonly
```

### INVALID_ARGUMENT (filter)

- 日付は RFC-3339 形式: `"2024-01-01T00:00:00+09:00"`
- スレッド名はフルパス: `spaces/SPACE_ID/threads/THREAD_ID`

### `GOOGLE_APPLICATION_CREDENTIALS` の干渉

サービスアカウントを指している場合は明示的にクリアする:

```bash
GOOGLE_APPLICATION_CREDENTIALS="" uv run --project $SKILL_DIR python $SCRIPT --list-spaces
```
