---
name: playwright-execution
description: "Playwright E2E テストの実行 + エビデンス収集 (video/trace/screenshot/HAR) + overlay (赤丸カーソル+字幕) + 品質計測 (axe-core/Web Vitals/body_check) を統合した実行フェーズスキル。動画はデフォルト ON。"
when_to_use: "E2E テストの実行 / エビデンス収集 / 動画エビデンス / accessibility チェック / Core Web Vitals 計測が必要なとき。テストスクリプト作成済みであることが前提。Triggers: 'E2E テスト実行', 'テスト実行', '動画エビデンス', 'エビデンス収集', 'テスト証跡', 'a11y テスト', 'accessibility テスト', 'axe-core', 'WCAG', 'Core Web Vitals', 'Web Vitals', 'LCP', 'CLS', 'body_check', 'overlay', '字幕', 'カーソル'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(npx *)
  - Bash(playwright *)
  - Bash(python *)
---

# Playwright Execution (テスト実行 + エビデンス収集)

テストスクリプト作成済みの状態で E2E テストを実行し、エビデンスを収集する。

## 前提条件

- テストスクリプトが `tests/` に作成済みであること (`/ndf:playwright-script-creation` で作成)
- `scenario.config.yaml` が設定済みであること

## 大原則

**エビデンス動画はデフォルト ON**。全テストで常に動画を取得する。
明示的にスキップする場合のみ `--pwk-no-video` を指定する。

## 実行コマンド

```bash
./scenario-test/run.sh                            # 全テスト (動画 ON)
./scenario-test/run.sh -k test_admin              # フィルタ
./scenario-test/run.sh --pwk-overlay              # 字幕 + カーソル付き動画
./scenario-test/run.sh --pwk-no-video             # 動画のみ OFF
./scenario-test/run.sh --pwk-no-evidence          # 全エビデンス OFF (HAR/trace/動画)
```

## エビデンス種別

| 種別 | デフォルト | OFF フラグ | 説明 |
|---|---|---|---|
| video | **ON** | `--pwk-no-video` | 全テストの動画を取得 |
| trace | ON (retain-on-failure) | `--pwk-no-evidence` | Playwright Trace (DOM + 操作ログ) |
| HAR | ON (minimal) | `--pwk-har-mode none` | ネットワーク通信ログ |
| screenshot | ON (only-on-failure) | `--pwk-no-evidence` | 失敗時スクリーンショット |

## overlay (赤丸カーソル + 字幕)

`--pwk-overlay` フラグで全テストの動画にオーバーレイが適用される。

API 詳細・使用例は `playwright_kit/overlay.py` を参照。主要関数: `set_caption()`, `flash_click()`, `hide_cursor()`。

## 品質計測

### accessibility (axe-core)

`@pytest.mark.page_role` marker が付いたテストで auto_roles にマッチする場合に自動実行。
設定は `scenario.config.yaml` の `accessibility:` セクションで制御。→ 設定例は `templates/scenario.config.yaml` を参照。

### Core Web Vitals

`@pytest.mark.page_role` marker + auto_roles マッチで LCP/CLS/TTFB/longest_task を自動計測。
設定は `scenario.config.yaml` の `web_vitals:` セクションで制御。→ 設定例は `templates/scenario.config.yaml` を参照。

### body_check (PHP/SSR エラー検出)

`page.on("response")` で全 HTML レスポンスを監視し、`Fatal error` 等を検出。デフォルト有効。
`@pytest.mark.no_body_check` で個別 opt-out 可能。→ 設定例は `templates/scenario.config.yaml` の `body_check:` セクションを参照。

## 成果物

```
reports/<run-id>/
├── report.md                   # テスト結果サマリ
├── <test-name>/
│   ├── video.mp4               # テスト動画 (デフォルト ON)
│   ├── trace.zip               # Playwright Trace
│   ├── request.har             # ネットワーク通信ログ
│   ├── body_check.jsonl        # body_check 違反詳細
│   └── screenshot-*.png        # スクリーンショット
```

## CLI options

| option | 役割 |
|---|---|
| `--pwk-config <path>` | `scenario.config.yaml` のパス |
| `--pwk-out-dir <path>` | 成果物出力先 (default: `reports/<run-id>/`) |
| `--pwk-no-video` | 動画収集を OFF (デフォルトは ON) |
| `--pwk-no-evidence` | HAR / trace / video の収集を全て OFF |
| `--pwk-har-mode {minimal,full,none}` | HAR 録画モード (default: minimal) |
| `--pwk-overlay` | overlay (赤丸カーソル + 字幕) を ON |

## 関連 Skill

- `/ndf:playwright-script-creation` — テストスクリプト作成 (実行の前段)
- `/ndf:playwright-report` — Markdown レポート生成
- `/ndf:playwright-kit-ops` — スクリプト実行 (init_project / スキャン)
- `/ndf:playwright-scenario-test` — 全機能統括
