---
name: google-drive
description: "Google Drive / Google Docs API でファイルのエクスポート・ダウンロード・アップロード (公開共有リンク付与) を行う。認証は ndf:google-auth に委譲。"
when_to_use: "Google Drive / Docs のファイル操作が必要なとき。Triggers: 'Google Drive', 'Google Docs', 'drive.file', 'ファイルエクスポート', 'ダウンロード', 'アップロード', '公開共有リンク'"
allowed-tools:
  - Read
  - Bash(python *)
  - Bash(uv *)
---

# Google Drive アクセス

## 概要

Google Drive / Google Docs のファイルを CLI 環境から操作する。
認証は `ndf:google-auth` スキルの共通モジュール (`get_credentials()`) を使用。

## 提供物

```
google-drive/
├── SKILL.md                  ← このファイル
├── pyproject.toml            ← uv プロジェクト (Drive API 依存)
└── scripts/
    └── gdrive_fetch.py       ← CLI: エクスポート / ダウンロード / アップロード
```

`gdrive_fetch.py` は実行時に `ndf:google-auth` スキルの `google_auth.py` を sys.path に追加して
`get_credentials()` を呼ぶ。`google-auth` 側で OAuth2 トークンを取得済みであれば追加の認証は不要。

## 前提条件

| 項目 | 値 |
|---|---|
| 認証 | `ndf:google-auth` スキル (共通 OAuth2 モジュール) |
| Python 実行 | `uv run --project ${CLAUDE_SKILL_DIR} python ...` または `uv run --with ...` |
| client_secret.json | `ndf:google-auth` の手順で配置済み |
| 既存トークン | `~/.config/gcloud/google_token.json` (`ndf:google-auth` で取得済み) |

## クイックスタート

```bash
SKILL_DIR=${CLAUDE_SKILL_DIR}
SCRIPT=$SKILL_DIR/scripts/gdrive_fetch.py

# Google Doc をテキストでエクスポート
uv run --project $SKILL_DIR python $SCRIPT --id FILE_ID

# HTML 形式
uv run --project $SKILL_DIR python $SCRIPT --id FILE_ID --mime text/html -o /tmp/doc.html

# PDF 形式
uv run --project $SKILL_DIR python $SCRIPT --id FILE_ID --mime application/pdf -o /tmp/doc.pdf

# バイナリファイル (画像、PDF 等) をダウンロード
uv run --project $SKILL_DIR python $SCRIPT --id FILE_ID --download -o /tmp/file.png

# ファイルをアップロード (公開共有リンク付与)
uv run --project $SKILL_DIR python $SCRIPT --upload /path/to/file.png
```

`--port N` を渡すとローカルサーバ方式の OAuth で再認証 (初回かつスコープ追加時のみ必要)。

## Google Doc ID の取得

URL から ID を抽出する:

```
https://docs.google.com/document/d/【ここが Doc ID】/edit
```

例: `https://docs.google.com/document/d/1vwDAZJLlLjtjFFITB23qTF8ldWBgRZg8MPUO4kUt-Ys/edit`
→ ID: `1vwDAZJLlLjtjFFITB23qTF8ldWBgRZg8MPUO4kUt-Ys`

## エクスポート形式 (`--mime`)

| mimeType | 形式 |
|---|---|
| `text/plain` | プレーンテキスト |
| `text/html` | HTML |
| `application/pdf` | PDF |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | DOCX |

Sheets は `text/csv` / `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (xlsx) も対応。
Slides は `application/pdf` / `application/vnd.openxmlformats-officedocument.presentationml.presentation` (pptx)。

## トラブルシューティング

### 403 Insufficient scopes

スコープ不足の場合は `ndf:google-auth` で必要なスコープ付きで再認証:

```bash
python ${CLAUDE_SKILLS_DIR:-${CLAUDE_PROJECT_DIR}/.claude/skills}/google-auth/scripts/google_auth.py --clear
! python ${CLAUDE_SKILLS_DIR:-${CLAUDE_PROJECT_DIR}/.claude/skills}/google-auth/scripts/google_auth.py drive.file
```

### 404 File not found

サービスアカウントにはユーザのファイルへのアクセス権がない。本スキルは **ユーザの OAuth 認証** (`ndf:google-auth` 経由) を使う前提。

### `GOOGLE_APPLICATION_CREDENTIALS` の干渉

```bash
GOOGLE_APPLICATION_CREDENTIALS="" uv run --project $SKILL_DIR python $SCRIPT --id FILE_ID
```

### gcloud CLI での認証 (非推奨)

gcloud CLI の OAuth はインタラクティブ入力の制約で多くのエージェント環境で動かない。
本スキルの依存である `ndf:google-auth` を使うこと。
