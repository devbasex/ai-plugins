# PLAN23: playwright-scenario-test を 6 機能 skill + 統括 skill に分割

**Issue**: https://github.com/devbasex/ai-plugins/issues/17
**Status**: 実装済み (未コミット)

## Context

`playwright-scenario-test` は 73 ファイル / 760KB の大型 skill。SKILL.md (315行, ~5,000 tokens) が skill 発動時にコンテキストに全量ロードされ、エージェントのコンテキストを圧迫していた。

## 実施内容

機能を 6 つの独立 skill に分割し、`playwright-scenario-test` を統括オーケストレータに改修。

### 新規 6 skill

| # | Skill 名 | 責務 |
|---|---|---|
| 1 | `playwright-test-planning` | テスト計画 (HTSM/ISTQB/FEW HICCUPPS、page role、チェックリスト) |
| 2 | `playwright-evidence` | エビデンス収集 (video/trace/screenshot/HAR) |
| 3 | `playwright-overlay` | 動画装飾 (赤丸カーソル + 字幕) |
| 4 | `playwright-quality` | 品質計測 (accessibility/Web Vitals/body_check) |
| 5 | `playwright-report` | Markdown レポート + Drive 共有 |
| 6 | `playwright-kit-ops` | スクリプト実行 (init_project/スキャン/アップロード) |

### 統括 skill (既存改修)

`playwright-scenario-test` → トリガーを絞り、冒頭に 6 skill への案内テーブルを追加。

### スクリプト移動

`playwright-scenario-test/scripts/` → `playwright-kit-ops/scripts/` に全スクリプトを移動。
`init_project.sh` は `SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"` で自身の親ディレクトリ (`playwright-kit-ops/`) を解決するため、移動先でそのまま動作する。

### plugin.json

- skills 配列に 6 skill を追加 (計 45 skills)
- version: 4.7.5 → 4.8.0

## 変更ファイル一覧

- 新規 6 ファイル: `skills/playwright-{test-planning,evidence,overlay,quality,report,kit-ops}/SKILL.md`
- 移動 11 ファイル: `scripts/*.py`, `scripts/init_project.{sh,bat}` → `playwright-kit-ops/scripts/`
- 修正 2 ファイル: `playwright-scenario-test/SKILL.md`, `.claude-plugin/plugin.json`

## 将来の対応 (別 issue)

- `playwright_kit` Python パッケージの内部サブディレクトリ化
