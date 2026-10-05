---
name: playwright-evidence
description: "Generate the Playwright test report and collect its evidence files under reports/<run-id>/. Use when summarizing or sharing E2E results（テスト報告書・エビデンス・trace・動画）."
allowed-tools:
  - Read
  - Bash(python *)
  - Bash(uv *)
  - Bash(pytest *)
---

# Playwright 証跡とレポート

テスト実行後に Markdown レポートを生成し、エビデンス一式を `reports/<run-id>/` にまとめる。
共有の手段（チャット・ストレージ・課題への添付など）は利用者が選ぶ。

## 前提条件

- テスト実行済みで `reports/<run-id>/` にエビデンスが存在すること (`/playwright-kit:playwright-authoring`)

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

## エビデンスと共有するときの注意

| ファイル | 種別 | セキュリティ考慮 |
|---|---|---|
| `video.mp4` | テスト動画 | 画面に表示された情報が含まれる |
| `trace.zip` | Playwright Trace | DOM snapshot + 操作ログ + Cookie/localStorage |
| `request.har` | ネットワーク通信ログ | HTTP request/response body を含む場合あり |
| `report.md` | テスト結果サマリ | URL + テスト名程度 |
| `body_check.jsonl` | PHP/SSR エラー詳細 | ソースコード片を含む場合あり |
| `screenshot-*.png` | 失敗時スクリーンショット | 画面に表示された情報 |

**セキュリティ注意**: trace.zip / HAR には認証情報 (Cookie, localStorage, Basic Auth) や
入力内容が含まれる可能性がある。共有する先は **非公開の置き場所** と **信頼できる共有相手** に限定すること。

## 推奨ワークフロー

```
[テスト実行]      ./scenario-test/run.sh --pwk-overlay
      ▼
[ローカル確認]    reports/<run-id>/report.md で結果確認
      ▼
[共有]            reports/<run-id>/ を、利用者が選んだ手段で共有する
```

## Trace Viewer

trace.zip はローカルの Playwright Trace Viewer で開ける。

```bash
uv run playwright show-trace reports/<run-id>/<case>/trace.zip
```

## 関連 Skill

- `/playwright-kit:playwright-authoring` — スクリプト作成と実行 (前段)
- `/playwright-kit:playwright-planning` — テスト計画
- `/playwright-kit:playwright-kit-ops` — 実行環境の運用
