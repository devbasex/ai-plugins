---
name: playwright-report
description: "Playwright テスト結果の Markdown レポート自動生成。テスト結果サマリ・エビデンスリンク・失敗詳細を report.md にまとめる。"
when_to_use: "テストレポートの生成 / テスト結果の共有が必要なとき。Triggers: 'テストレポート', 'report.md', 'テスト結果', 'テスト報告書', 'レポート生成', 'テスト結果まとめ'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(python *)
---

# Playwright Report (レポート生成)

テスト実行後に **Markdown レポート** を自動生成する。

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

## Google Drive での共有

レポート + エビデンスを Drive にアップロードしてチーム共有する場合は
`/ndf:playwright-evidence-drive` を参照。

## 関連 Skill

- `/ndf:playwright-execution` — テスト実行 + エビデンス収集
- `/ndf:playwright-evidence-drive` — エビデンス Google Drive 保管・共有
- `/ndf:playwright-kit-ops` — エビデンスアップロードツール (スクリプト群)
- `/ndf:playwright-scenario-test` — 全機能を統括したフルワークフロー
