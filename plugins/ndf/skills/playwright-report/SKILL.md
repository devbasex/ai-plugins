---
name: playwright-report
description: "Playwright テスト結果の Markdown レポート自動生成 + Google Drive 共有。テスト結果サマリ・エビデンスリンク・失敗詳細を report.md にまとめる。"
when_to_use: "テストレポートの生成 / テスト結果の共有 / Google Drive へのエビデンスアップロードが必要なとき。Triggers: 'テストレポート', 'report.md', 'テスト結果', 'テスト報告書', 'Drive 共有', 'Drive アップロード', 'エビデンス共有'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(python *)
---

# Playwright Report (レポート生成)

テスト実行後に **Markdown レポート** を自動生成し、Google Drive で共有する。

## 自動生成

`pytest_terminal_summary` hook で `reports/<run-id>/report.md` が自動生成される。特別な設定は不要。

```bash
./scenario-test/run.sh
# → reports/<run-id>/report.md が生成される
```

## レポート内容

| セクション | 内容 |
|---|---|
| サマリ表 | nodeid, role, page_role, 結果, 実行時間, エラー数 |
| 失敗詳細 | FAIL/ERROR のテストごとの詳細情報 |
| body_check 違反 | PHP/SSR エラー検出の詳細 (URL, パターン, スニペット) |
| エビデンスリンク | video, trace, HAR, screenshot へのパス |

## レポート設定

`scenario.config.yaml` の `report` セクション:

```yaml
report:
  title: "シナリオ E2E テスト 実施報告書"
  test_plan_link: "./test-plan.md"
  phase_labels: {}
```

## Google Drive アップロード

### テスト実行時に自動アップロード

```bash
./scenario-test/run.sh --pwk-drive-folder=<FOLDER_ID>
```

`--pwk-drive-folder` を指定すると、テスト終了後に report.md + エビデンスファイルが Drive にアップロードされる。

### 手動アップロード

```bash
# エビデンスファイルを Drive にアップロード
python playwright-kit-ops/scripts/upload_evidence.py <file> --kind trace

# ディレクトリごとアップロード
python playwright-kit-ops/scripts/gdrive_upload_dir.py reports/<run-id>/ --folder-id <FOLDER_ID>

# report.md を Google Doc として変換・アップロード
python playwright-kit-ops/scripts/upload_md_as_gdoc.py reports/<run-id>/report.md

# Google Doc にエビデンスの Drive リンクを埋め込み
python playwright-kit-ops/scripts/build_gdoc_with_drive_links.py <doc-id> reports/<run-id>/
```

## 関連 Skill

- `/ndf:playwright-evidence` — 基本エビデンス収集
- `/ndf:playwright-quality` — レポートに含まれる品質計測
- `/ndf:google-drive` — Google Drive 操作全般
- `/ndf:playwright-scenario-test` — 全機能を統括したフルワークフロー
