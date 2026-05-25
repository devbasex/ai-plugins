---
name: playwright-scenario-test
description: "pytest-playwright ベースのフル E2E テストフレームワーク統括。テスト計画・スクリプト作成・エビデンス付きテスト実行・レポート生成の 4 フェーズを組み合わせた包括的なテストワークフローを提供する。個別機能のみ必要な場合は各専門 skill を直接参照。"
when_to_use: "フル E2E テストワークフロー (計画→スクリプト→実行→レポート) を一貫して行うとき / pytest-playwright 拡張 fixture (pwk_*) の全体像を把握したいとき / init_project.sh でプロジェクトをセットアップするとき。Triggers: 'pytest-playwright', 'pwk_role', 'pwk_evidence', 'init_project', 'シナリオテスト一式', 'フル E2E'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(playwright *)
  - Bash(python *)
---

# Playwright シナリオテスト Skill (v0.6.0)

Web アプリの E2E シナリオを **理論ベース** で計画し、**再現可能なテストスクリプトを実装してから**、**pytest-playwright** 上でエビデンス動画付きで実行、Markdown レポートを自動生成する一式の Skill。

## 大原則

1. **再現可能なテストスクリプトを実装してからテストを実施する**
2. **テストスクリプトは ndf plugin 非依存でプロジェクトフォルダに設置する**
3. **テスト実行はエビデンス動画を常に取得する** (オプションで明示的にスキップ可能)

## フェーズ別 Skill

| Phase | Skill | 機能 |
|---|---|---|
| 1 | `/ndf:playwright-test-planning` | テスト計画 (HTSM / page role / チェックリスト) |
| 2 | `/ndf:playwright-script-creation` | テストスクリプト作成 (テンプレート→実装→レビュー) |
| 3 | `/ndf:playwright-execution` | テスト実行 + エビデンス収集 (video/trace/overlay/quality) |
| 4 | `/ndf:playwright-report` | レポート生成 (Markdown) |
| -- | `/ndf:playwright-kit-ops` | ツール群 (init_project / スキャン / アップロード) |

## 標準ワークフロー

```
[Phase 1] テスト計画 (/ndf:playwright-test-planning)
    │  対象 URL → page role 判定 → チェックリスト → テスト技法確定
    ▼
[Phase 2] スクリプト作成 (/ndf:playwright-script-creation)
    │  テンプレート選択 → テストコード実装 → 再現可能性レビュー
    │  ※ スクリプトが完成するまでテスト実行に進まない
    ▼
[Phase 3] テスト実行 + エビデンス収集 (/ndf:playwright-execution)
    │  動画デフォルト ON → trace/HAR/overlay/a11y/CWV/body_check
    │  ※ --pwk-no-video で動画のみスキップ可
    ▼
[Phase 4] レポート生成 (/ndf:playwright-report)
    │  reports/<run-id>/report.md 自動生成
    ▼
[任意] ツール群 (/ndf:playwright-kit-ops)
       init_project / スキャン / アップロード (任意タイミング)
```

## クイックスタート

1. プロジェクト初期化: `./scripts/init_project.sh /path/to/your-app`
2. 設定編集: `scenario-test/scenario.config.yaml`
3. テストスクリプト作成: `scenario-test/tests/test_*.py` (→ `/ndf:playwright-script-creation`)
4. テスト実行 (動画デフォルト ON): `./scenario-test/run.sh`
5. 動画スキップ: `./scenario-test/run.sh --pwk-no-video`

→ `your-app/scenario-test/` は ndf plugin 非依存。単体で完結する。

## 用語集・制約

用語集 (accessibility, web vitals, LCP, CLS, TTFB, HAR, trace, overlay, body_check, page role, pwk) と制約/注意事項は `playwright_kit/` パッケージの README (`templates/runtime-README.md`) および `pyproject.toml` (`templates/pyproject.toml.runtime`) を参照。
