---
name: playwright-evidence-drive
description: "Upload Playwright evidence to Google Drive."
when_to_use: "テストエビデンスを Google Drive に保管・共有したいとき / テスト結果を Google Docs としてチームに配布したいとき / Drive 上のエビデンスリンクを report に埋め込みたいとき。Triggers: 'Drive にアップロード', 'Drive 共有', 'エビデンス保管', 'evidence drive', 'pwk-drive-folder', 'テスト結果共有', 'Google Drive エビデンス', 'trace アップロード', '動画アップロード', 'report を Docs に', 'エビデンス配布'"
allowed-tools:
  - Read
  - Bash(python *)
  - Bash(uv *)
---

# Playwright Evidence → Google Drive 保管

テスト実行後のエビデンス一式を Google Drive に保管し、共有可能にする。

## 前提条件

- `/ndf:google-auth` で OAuth2 認証が完了していること (drive.file スコープ)
- テスト実行済みで `reports/<run-id>/` にエビデンスが存在すること

## アップロード対象

| ファイル | 種別 | セキュリティ考慮 |
|---|---|---|
| `video.mp4` | テスト動画 | 画面に表示された情報が含まれる |
| `trace.zip` | Playwright Trace | DOM snapshot + 操作ログ + Cookie/localStorage |
| `request.har` | ネットワーク通信ログ | HTTP request/response body を含む場合あり |
| `report.md` | テスト結果サマリ | URL + テスト名程度 |
| `body_check.jsonl` | PHP/SSR エラー詳細 | ソースコード片を含む場合あり |
| `screenshot-*.png` | 失敗時スクリーンショット | 画面に表示された情報 |

**セキュリティ注意**: trace.zip / HAR には認証情報 (Cookie, localStorage, Basic Auth) や
入力内容が含まれる可能性がある。アップロード先は **private folder** + **信頼できる共有相手** に限定すること。

## 方法 1: テスト実行時の自動アップロード

`--pwk-drive-folder` を指定すると `pytest_sessionfinish` hook で自動アップロードされる。

```bash
./scenario-test/run.sh --pwk-drive-folder=<FOLDER_ID>
```

動作:
1. テスト終了後に `pytest_sessionfinish` が発火
2. `report.md` をアップロード
3. 各テストの `trace.zip` / `*.har` / `*.mp4` / `body_check.jsonl` をアップロード
4. 全ファイルは非公開 (private) でアップロードされる

## 方法 2: 手動アップロード (scripts)

テスト実行後に個別にアップロードする場合。スクリプトは `playwright-kit-ops/scripts/` に配置。

### 単一ファイルアップロード

```bash
cd scenario-test
uv run python scripts/upload_evidence.py reports/<run-id>/test_login/trace.zip \
  --kind trace \
  --parent-folder-id <FOLDER_ID>
```

オプション:
- `--kind {trace|har|video|any}` — ファイル種別 (拡張子から自動判定も可)
- `--parent-folder-id <FOLDER_ID>` — Drive 上のアップロード先フォルダ ID
- `--public` — anyone/read 権限を付与 (trace viewer URL 生成に必要)

### ディレクトリ一括アップロード

```bash
uv run python scripts/gdrive_upload_dir.py \
  --local reports/<run-id>/ \
  --parent <FOLDER_ID>
```

ディレクトリ構造を保ったまま Drive にミラーする。
サブフォルダも再帰的に作成される。

### report.md → Google Docs 変換

```bash
uv run python scripts/upload_md_as_gdoc.py \
  --md reports/<run-id>/report.md \
  --parent <FOLDER_ID> \
  --name "E2E テスト報告書 2026-05-26"
```

Markdown を Google Docs 形式に変換してアップロード。
テーブル・リスト・見出しが Docs のネイティブ形式に変換される。

### Google Docs にエビデンス Drive リンクを埋め込み

```bash
uv run python scripts/build_gdoc_with_drive_links.py \
  --md reports/<run-id>/report.md \
  --folder <DRIVE_FOLDER_ID> \
  --run-id <run-id> \
  --name "E2E テスト報告書 2026-05-26"
```

1. Drive 上の `<DRIVE_FOLDER_ID>` 配下の `<run-id>` フォルダからファイル一覧を取得
2. `report.md` 内の相対パスリンク (`./TC-XX/trace.zip`) を Drive URL に書き換え
3. 書き換え済み Markdown を Google Docs としてアップロード

→ チームメンバーが Docs 上で report を読みながら、エビデンスへの Drive リンクをクリックして確認できる。

## 推奨ワークフロー

```
[テスト実行]
  ./scenario-test/run.sh --pwk-overlay
      ↓
[ローカル確認]
  reports/<run-id>/report.md で結果確認
      ↓
[Drive 一括アップロード]
  uv run python scripts/gdrive_upload_dir.py --local reports/<run-id>/ --parent <ID>
      ↓
[Docs 変換 + リンク埋め込み]
  uv run python scripts/build_gdoc_with_drive_links.py --md reports/<run-id>/report.md --folder <ID> --run-id <run-id> --name "報告書"
      ↓
[共有]
  Docs URL をチーム (Slack / Google Chat) に共有
```

ワンコマンドで全てを行う場合:

```bash
./scenario-test/run.sh --pwk-drive-folder=<FOLDER_ID> --pwk-overlay
```

## Drive フォルダ構成の推奨

```
E2E テスト証跡/                              ← 共有ドライブ or チームフォルダ
├── <プロジェクト名>/
│   ├── 20260526-134500/                     ← run-id (自動生成)
│   │   ├── report.md
│   │   ├── test_login/
│   │   │   ├── video.mp4
│   │   │   ├── trace.zip
│   │   │   └── request.har
│   │   └── test_admin_dashboard/
│   │       ├── video.mp4
│   │       └── trace.zip
│   └── report (Google Docs)                 ← build_gdoc_with_drive_links で生成
```

## Trace Viewer 連携

`--public` でアップロードした trace.zip は Playwright Trace Viewer で直接開ける:

```bash
uv run python scripts/upload_evidence.py reports/.../trace.zip \
  --kind trace --public --parent-folder-id <FOLDER_ID>
```

出力例:
```
playwright_trace_viewer: https://trace.playwright.dev/?trace=https%3A%2F%2Fdrive.google.com%2Fuc%3Fexport%3Ddownload%26id%3D...
```

この URL を共有すると、インストール不要でブラウザ上から trace を再生できる。

**注意**: `--public` は anyone/read を付与するため、trace 内の機密情報 (Cookie, 入力値) が
公開される。社内限定の場合は private のまま `playwright show-trace` をローカルで使うこと。

## トラブルシュート

| 症状 | 原因 | 対策 |
|---|---|---|
| `google_auth スキルが見つかりません` | google-auth スキル未インストール | `GOOGLE_AUTH_SCRIPTS` env を設定、または `/ndf:google-auth` で認証セットアップ |
| `HttpError 403: insufficient permissions` | drive.file スコープ不足 | `/ndf:google-auth` で再認証 (`drive.file` スコープ指定) |
| `HttpError 404: File not found` | FOLDER_ID が間違っている / アクセス権なし | Drive で共有フォルダ ID を確認 |
| `resumable upload failed` | ファイルサイズが大きい / ネットワーク不安定 | 再試行。動画は mp4 (H.264) で容量を抑える |
| pytest 後に自動アップロードされない | `--pwk-drive-folder` 未指定 | CLI 引数を確認 |

## 環境変数

| 変数 | 用途 | 例 |
|---|---|---|
| `GOOGLE_AUTH_SCRIPTS` | google-auth スキルの scripts/ パス | `~/.claude/skills/google-auth/scripts` |
| `PWK_DRIVE_FOLDER` | デフォルトの Drive アップロード先 (将来対応予定) | `1ABCxyz...` |

## 関連 Skill

- `/ndf:playwright-execution` — テスト実行 + エビデンス収集 (Drive アップロードの前段)
- `/ndf:playwright-report` — Markdown レポート生成
- `/ndf:playwright-kit-ops` — スクリプト実行 (upload_evidence 等のスクリプトはここに配置)
- `/ndf:google-auth` — Google API OAuth2 認証
- `/ndf:google-drive` — Google Drive 汎用操作
- `/ndf:playwright-scenario-test` — 全機能統括
