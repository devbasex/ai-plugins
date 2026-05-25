---
name: playwright-evidence
description: "Playwright E2E テスト実行時のエビデンス収集 (video / trace / screenshot / HAR) を標準化する軽量スキル。pytest-playwright / Playwright Test いずれの環境でも対応。"
when_to_use: "E2E テストの実行 / エビデンス収集 / 動画・トレース・スクリーンショットの保存が必要なとき。Triggers: 'E2E テスト', 'シナリオテスト', '動画エビデンス', 'Playwright', 'テスト実行', 'リリース前確認', '回帰テスト', 'エビデンス収集', 'テスト証跡'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(npx *)
  - Bash(playwright *)
---

# Playwright Evidence (エビデンス収集)

E2E テスト実行時に **video / trace / screenshot / HAR** を確実に収集するための標準設定ガイド。

## エビデンスデフォルト設定

| 種別 | 推奨設定 | 説明 |
|---|---|---|
| video | `retain-on-failure` | 失敗テストの動画を保持 (全テスト: `on`) |
| trace | `retain-on-failure` | 失敗テストの Playwright Trace を保持 |
| screenshot | `only-on-failure` | 失敗時のスクリーンショットを自動取得 |

## pytest-playwright 環境

### セットアップ済みプロジェクト (playwright_kit 使用)

`scenario.config.yaml` のエビデンス関連設定:

```yaml
playwright:
  headless: true
  viewport: { width: 1280, height: 720 }
  video_size: { width: 1280, height: 720 }
  enable_trace: true
  video_format: mp4
```

実行:

```bash
./scenario-test/run.sh                       # 全テスト
./scenario-test/run.sh -k test_login         # 特定テスト
./scenario-test/run.sh --pwk-no-evidence     # エビデンス収集 OFF
```

### 素の pytest-playwright

```bash
uv run pytest \
  --video=retain-on-failure \
  --tracing=retain-on-failure \
  --screenshot=only-on-failure \
  --output=reports/
```

## Playwright Test (TypeScript) 環境

`playwright.config.ts`:

```typescript
export default defineConfig({
  use: {
    video: 'retain-on-failure',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  outputDir: 'test-results/',
  reporter: [['html', { open: 'never' }]],
});
```

実行:

```bash
npx playwright test                          # 全テスト
npx playwright test --ui                     # UI モード
npx playwright show-trace test-results/*/trace.zip  # Trace Viewer
```

## 成果物

```
reports/<run-id>/                # pytest-playwright (playwright_kit)
├── report.md                   # Markdown テスト結果サマリ
├── <test-name>/
│   ├── video.mp4               # テスト動画
│   ├── trace.zip               # Playwright Trace
│   ├── har.json                # ネットワーク通信ログ
│   ├── console.log             # console.error / console.warn
│   └── screenshot-*.png        # スクリーンショット

test-results/                    # Playwright Test (TypeScript)
├── <test-name>/
│   ├── video.webm              # テスト動画
│   └── trace.zip               # Playwright Trace
```

## 関連 Skill

- `/ndf:playwright-overlay` — 動画に字幕 + カーソルを追加
- `/ndf:playwright-quality` — accessibility / Web Vitals 自動計測
- `/ndf:playwright-report` — Markdown レポート生成 + Drive 共有
- `/ndf:playwright-scenario-test` — 全機能を統括したフルワークフロー
