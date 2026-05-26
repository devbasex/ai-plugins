# PLAN23: playwright-scenario-test を 5 機能 skill + 統括 skill に再構成

**Issue**: https://github.com/devbasex/ai-plugins/issues/17
**Status**: 実装済み

## Context

`playwright-scenario-test` は 73 ファイル / 760KB の大型 skill。初回は 6 skill + orchestrator に分割したが、以下の大原則に基づいて 5 skill + orchestrator に再構成した。

### 大原則

1. 再現可能なテストスクリプトを実装してからテストを実施する
2. テストスクリプトは ndf plugin 非依存でプロジェクトフォルダに設置する
3. テスト実行はエビデンス動画を常に取得する (オプションでスキップ可能)

## 実施内容

### 5 skill + orchestrator 構成

| # | Skill 名 | 責務 |
|---|---|---|
| 1 | `playwright-test-planning` | テスト計画 (HTSM/ISTQB/FEW HICCUPPS) |
| 2 | `playwright-script-creation` | テストスクリプト作成 (テンプレート→実装→レビュー) |
| 3 | `playwright-execution` | テスト実行 + エビデンス収集 (video/trace/overlay/quality 統合) |
| 4 | `playwright-report` | レポート生成 |
| 5 | `playwright-kit-ops` | ツール群 (init_project/スキャン/アップロード) |

### 廃止 skill

- `playwright-evidence` → `playwright-execution` に統合
- `playwright-overlay` → `playwright-execution` に統合
- `playwright-quality` → `playwright-execution` に統合

### コード変更

- `pytest_plugin.py`: `--pwk-no-video` オプション追加、動画デフォルト ON
- `run.sh`: `--video=on` フォールバック追加
- `conftest.py.template`: テストスクリプト存在チェック追加
- `plugin.json`: v4.8.0 → v4.9.0 (45 → 44 skills)

## 設計書

`docs/superpowers/specs/2026-05-25-playwright-skill-restructure-design.md`
