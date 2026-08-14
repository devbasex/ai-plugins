---
name: playwright-evidence
description: "Generate the Playwright test report and store its evidence on Google Drive. Use when sharing E2E results（テスト報告書・エビデンスをDriveへ保管）."
allowed-tools:
  - Read
  - Bash(python *)
  - Bash(uv *)
  - Bash(pytest *)
---

# Playwright 証跡とレポート

テスト実行後に Markdown レポートを生成し、エビデンス一式を Google Drive に保管して共有可能にする。

## 前提条件

- テスト実行済みで `reports/<run-id>/` にエビデンスが存在すること (`/ndf:playwright-authoring`)
- Drive へ保管する場合のみ、`google-auth` skill で OAuth2 認証が完了していること (drive.file スコープ)。
  同 skill は既定の配布セットに含まれないため、`plugins/ndf-shared/skills/google-auth/` を利用先へ
  導入するか、`GOOGLE_AUTH_SCRIPTS` 環境変数で認証スクリプトの場所を指す

## レポート生成

`pytest_terminal_summary` hook で `reports/<run-id>/report.md` が自動生成される。特別な設定は不要。

```bash
./scenario-test/run.sh
# → reports/<run-id>/report.md が生成される
```

| セクション | 内容 |
|---|---|
| サマリ表 | nodeid, role, page_role, 結果, 実行時間, エラー数 |
| 失敗詳細 | FAIL/ERROR のテストごとの詳細情報 |
| body_check 違反 | PHP/SSR エラー検出の詳細 (URL, パターン, スニペット) |
| エビデンスリンク | video, trace, HAR, screenshot へのパス |

`scenario.config.yaml` の `report` セクションで表題等を制御する。

```yaml
report:
  title: "シナリオ E2E テスト 実施報告書"
  test_plan_link: "./test-plan.md"
  phase_labels: {}
```

## アップロード対象とセキュリティ

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

`report.md` と各テストの `trace.zip` / `*.har` / `*.mp4` / `body_check.jsonl` が、
すべて非公開 (private) でアップロードされる。

## 方法 2: 手動アップロード (scripts)

スクリプトは `playwright-kit-ops/scripts/` に配置されている。

```bash
cd scenario-test

# 単一ファイル
uv run python scripts/upload_evidence.py reports/<run-id>/test_login/trace.zip \
  --kind trace --parent-folder-id <FOLDER_ID>

# ディレクトリ一括 (構造を保ったまま Drive にミラー。サブフォルダも再帰作成)
uv run python scripts/gdrive_upload_dir.py --local reports/<run-id>/ --parent <FOLDER_ID>

# report.md → Google Docs 変換 (表・リスト・見出しを Docs ネイティブ形式へ)
uv run python scripts/upload_md_as_gdoc.py --md reports/<run-id>/report.md \
  --parent <FOLDER_ID> --name "E2E テスト報告書"

# Google Docs にエビデンスの Drive リンクを埋め込み
uv run python scripts/build_gdoc_with_drive_links.py --md reports/<run-id>/report.md \
  --folder <DRIVE_FOLDER_ID> --run-id <run-id> --name "E2E テスト報告書"
```

`upload_evidence.py` の主なオプション:

- `--kind {trace|har|video|any}` — ファイル種別 (拡張子から自動判定も可)
- `--parent-folder-id <FOLDER_ID>` — Drive 上のアップロード先フォルダ ID
- `--public` — anyone/read 権限を付与 (trace viewer URL 生成に必要)

`build_gdoc_with_drive_links.py` は、Drive 上の `<run-id>` フォルダのファイル一覧を取得し、
`report.md` 内の相対パスリンク (`./TC-XX/trace.zip`) を Drive URL に書き換えてから Docs 化する。
チームメンバーは Docs 上で report を読みながらエビデンスへのリンクをクリックできる。

## 推奨ワークフロー

```
[テスト実行]      ./scenario-test/run.sh --pwk-overlay
      ▼
[ローカル確認]    reports/<run-id>/report.md で結果確認
      ▼
[Drive 一括保管]  gdrive_upload_dir.py --local reports/<run-id>/ --parent <ID>
      ▼
[Docs 化 + リンク埋め込み]  build_gdoc_with_drive_links.py ...
      ▼
[共有]            Docs URL をチーム (Slack / Google Chat) に共有
```

ワンコマンドで済ませる場合: `./scenario-test/run.sh --pwk-drive-folder=<FOLDER_ID> --pwk-overlay`

### Drive フォルダ構成の推奨

```
E2E テスト証跡/                              ← 共有ドライブ or チームフォルダ
├── <プロジェクト名>/
│   ├── 20260526-134500/                     ← run-id (自動生成)
│   │   ├── report.md
│   │   └── test_login/
│   │       ├── video.mp4
│   │       ├── trace.zip
│   │       └── request.har
│   └── report (Google Docs)                 ← build_gdoc_with_drive_links で生成
```

## Trace Viewer 連携

`--public` でアップロードした trace.zip は Playwright Trace Viewer で直接開ける。

```bash
uv run python scripts/upload_evidence.py reports/.../trace.zip \
  --kind trace --public --parent-folder-id <FOLDER_ID>
# → playwright_trace_viewer: https://trace.playwright.dev/?trace=...
```

この URL を共有すると、インストール不要でブラウザ上から trace を再生できる。

**注意**: `--public` は anyone/read を付与するため、trace 内の機密情報 (Cookie, 入力値) が
公開される。社内限定の場合は private のまま `playwright show-trace` をローカルで使うこと。

## 環境変数

| 変数 | 用途 | 例 |
|---|---|---|
| `GOOGLE_AUTH_SCRIPTS` | google-auth skill の scripts/ パス | `~/.claude/skills/google-auth/scripts` |
| `PWK_DRIVE_FOLDER` | デフォルトの Drive アップロード先 (将来対応予定) | `1ABCxyz...` |

## トラブルシュート

| 症状 | 原因 | 対策 |
|---|---|---|
| `Google Drive 連携には optional skill google-auth が必要です` | `google-auth` 未導入 | `GOOGLE_AUTH_SCRIPTS` を `google-auth/scripts` へ設定するか、同 skill を利用先へ導入する |
| `HttpError 403: insufficient permissions` | drive.file スコープ不足 | `google-auth` skill で再認証 (`drive.file` スコープ指定) |
| `HttpError 404: File not found` | FOLDER_ID が間違っている / アクセス権なし | Drive で共有フォルダ ID を確認 |
| `resumable upload failed` | ファイルサイズが大きい / ネットワーク不安定 | 再試行。動画は mp4 (H.264) で容量を抑える |
| pytest 後に自動アップロードされない | `--pwk-drive-folder` 未指定 | CLI 引数を確認 |

## 関連 Skill

- `/ndf:playwright-authoring` — スクリプト作成と実行 (前段)
- `/ndf:playwright-planning` — テスト計画
- `/ndf:playwright-kit-ops` — 実行環境の運用 (アップロードスクリプトの配置元)
- `google-auth` / `google-drive` — Google API の認証と Drive 操作。どちらも既定の配布セットには
  含まれない（`plugins/ndf-shared/skills/` にはあるが manifest には載せていない）。Drive 連携を
  使う場合だけ利用先へ導入する
