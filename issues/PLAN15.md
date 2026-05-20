# PLAN15: playwright-scenario-test v0.3.0 — OSS リリース品質化

## 関連リンク

- 親 PR: [#55 feat(ndf): playwright-scenario-test ... v4.1.0](https://github.com/takemi-ohama/ai-plugins/pull/55)
- 親 PR の自己レビュー: [comment #3142670254](https://github.com/takemi-ohama/ai-plugins/pull/55#issuecomment-3142670254)

## 概要

PR #55 で残った以下を 1 PR で全対応し、playwright-scenario-test を **OSS リリース品質** まで引き上げる。

| 項目 | 由来 |
|------|------|
| Maj-7: `playwright_executor.py` 責務分離 | 自己レビュー Major |
| Min-2: `_slugify` 衝突 | 自己レビュー Minor |
| Min-4: HAR upload 用スクリプト不在 | 自己レビュー Minor |
| pytest-playwright + locator-first 移行 | PR #55 description (v0.3.0) |
| web-first assertion (`expect`) 統一 | PR #55 description (v0.3.0) |
| docs の "v0.3.0 以降" 記述解消 | PR #55 description |
| 録画→YAML 自動変換 | PR #55 description (v0.4.0 → 前倒し) |
| a11y / CWV を runner に組込 | PR #55 description (v0.4.0 → 前倒し) |

**後方互換は不要** (誰も使っていない開発中段階)。古い実装は躊躇なく削除し、最終形のみを残す。

## 設計方針 (no back-compat)

1. **testcase YAML スキーマを最終形へ刷新**: 旧 `path` ベース step を削除し、`goto` / `click` / `fill` / `expect` の **明示的 step kind** に統一
2. **runner は pytest-playwright ベース**: `scenario-test` CLI は pytest を呼ぶ薄いラッパに縮退
3. **assertion は `expect()` のみ**: 文字列 match / `body_check` の正規表現は `expect(locator).not_to_contain_text(...)` などへ移行
4. **evidence (trace / HAR / video) 収集は単一モジュール**: `evidence.py` で集中管理、scripts は `upload_evidence.py` に統合
5. **deprecated/dead code は削除**: `trace_link.py` shim は残さず削除、過去の互換層も持たない
6. **a11y / CWV 自動付加**: page_role に応じて axe-core / Core Web Vitals を runner が自動実行し report に含める

## 修正対象 (主要ファイル)

### 削除

- `scripts/trace_link.py` (`upload_evidence.py` に吸収)
- `scenario_test/nav_helpers.py` の旧 `path` ベース helper (`navigate_post`, `find_click_target`, `detect_body_errors`, `slug_for`) — locator + expect で置換
- `scenario_test/curl_executor.py` の `_curl()` で使う path-only step (curl タイプは残すが step 表現を新スキーマに合わせる)

### 新規

- `scenario_test/evidence.py` — `EvidenceCollectors` dataclass + listener / tracing / HAR 集中管理
- `scenario_test/locator_steps.py` — YAML step kind → Playwright Locator 操作の dispatcher
- `scenario_test/a11y.py` — axe-core 実行 + 結果集計 (page_role 自動判定)
- `scenario_test/cwv.py` — Core Web Vitals 計測ラッパ (LCP/CLS/TTFB/INP-proxy)
- `scripts/upload_evidence.py` — trace/HAR/video 共通アップロード
- `scripts/record_to_yaml.py` — Playwright codegen 出力を YAML testcase へ変換
- `tests/test_evidence.py`
- `tests/test_locator_steps.py`
- `tests/test_a11y.py`
- `tests/test_cwv.py`
- `tests/test_upload_evidence.py`
- `tests/test_record_to_yaml.py`
- `tests/test_default_test_id.py`

### 大幅改訂

- `scenario_test/playwright_executor.py` (678 → 約 350 行目標。pytest-playwright 連携 + locator dispatcher 委譲)
- `scenario_test/testcase.py` (新 step kind スキーマ + バリデーション)
- `scripts/generate_test_plan.py` (`_default_test_id` で URL path 全体 + sha1[:6] 衝突回避、`_yaml_dump` 撤去して `pyyaml` の `safe_dump` を直接利用)
- `templates/testcase-*.yaml.template` (全 6 ファイル新スキーマで書き直し)
- `templates/config.example.yaml` (a11y/CWV セクション追加)
- `SKILL.md` (新ランナー / 新 step kind / 新 scripts へ全面刷新)
- `docs/01-methodology.md` 〜 `05-bug-report.md` (旧 helper 言及を locator-first へ更新)
- `docs/04-playwright-mapping.md` (`expect_aria_snapshot` を実装済みとして書き直し)
- `pyproject.toml` (version 0.2.0 → 0.3.0、`pytest-playwright` を dev に追加)
- `plugins/ndf/.claude-plugin/plugin.json` (4.1.0 → 4.2.0)
- `plugins/ndf/CLAUDE.md` (v4.2.0 開発履歴追記)

## タスク分解

### Task 1: 新スキーマ確立 (testcase.py + locator_steps.py)

- `TestCase.steps` を `list[Step]` (union of `GotoStep / ClickStep / FillStep / ExpectStep / ExtractStep`) に変更
- `locator_steps.py` で各 step kind を Locator 操作に dispatch (例: `ClickStep(role="button", name="保存")` → `page.get_by_role("button", name="保存").click()`)
- 旧 `NavStep` (`path` only) は削除。step 不正時は ValueError でエラー
- `KNOWN_PAGE_ROLES` 検証は維持
- `tests/test_locator_steps.py` で各 step → Locator 構築を smoke (Playwright 不要なテスト含む)

### Task 2: evidence.py へ集中管理

- `EvidenceCollectors` dataclass: `trace_path`, `har_path`, `video_path`, `console_errors`, `page_errors`, `axe_violations`, `cwv_metrics`
- `EvidenceCollectors.attach(context, page, config, tc, log_lines)` で listener / tracing / HAR を一括 setup
- `EvidenceCollectors.finalize()` で trace.stop / a11y / CWV 実行
- `playwright_executor` から evidence 関連コードを除去 (約 200 行削減)

### Task 3: a11y.py / cwv.py 実装

- `a11y.py`: `axe-playwright-python` を呼び出し WCAG 2.0/2.1/2.2 違反を集計
  - page_role が `lp / list / form / dashboard / cart / checkout / settings` のとき自動実行
  - violations を `EvidenceCollectors.axe_violations` に格納し、report に表示
- `cwv.py`: `check_cwv.py` (既存) のロジックを module 化し runner から呼び出せるように
  - LCP / CLS / TTFB / longest_task を計測
  - page_role が `lp / list / dashboard` のとき自動実行
- どちらも threshold を `config.yaml` で上書き可能

### Task 4: pytest-playwright ベース runner

- `playwright_executor.py` を pytest-playwright fixture (`page`, `browser_context_args`) に再構築
- `scenario-test` CLI は `pytest` を `subprocess.run` で呼ぶ薄いラッパ
- 並列は `pytest-xdist` (`-n {workers}`) で実装
- 録画 / trace は `browser_context_args` で設定
- `expect()` のみを assertion に使用、自前 `body_check` は `expect_no_text` step kind で代替

### Task 5: scripts 整理

- `upload_evidence.py` (新規): `--kind {trace,har,video,any}` を受ける統合 uploader
- `trace_link.py` 削除
- `record_to_yaml.py` (新規): Playwright codegen の Python 出力を読み、新 step kind YAML へ変換
- `record_scenario.py` は `record_to_yaml.py` 呼び出しのみに簡略化

### Task 6: テンプレート + docs 全面更新

- `templates/testcase-*.yaml.template` を 6 ファイル全部新スキーマで書き直し
- `templates/config.example.yaml` に `a11y`, `cwv` セクション追加
- `SKILL.md` を新ランナー前提でクイックスタートから書き直し
- `docs/01〜05` の旧 helper 言及を locator-first へ更新
- `docs/04-playwright-mapping.md` の `aria_snapshot` を実装済みとして書き直し
- `docs/05-bug-report.md` の `generate_bug_report.py` を実装済みとして書き直す (Task 5 にスクリプト追加)

### Task 7: バージョン bump + 開発履歴

- `pyproject.toml`: `version = "0.3.0"` + `dev` extras に `pytest-playwright>=0.5`, `pytest-xdist>=3.0`
- `plugins/ndf/.claude-plugin/plugin.json`: `4.1.0` → `4.2.0`
- `plugins/ndf/CLAUDE.md` の開発履歴に v4.2.0 セクションを追加 (本 PR の概要)
- 古い `i09.md` 等の参照ノートで古くなった部分があれば追記

## 影響範囲

- **互換性破壊**: あり (testcase YAML スキーマ変更、`trace_link.py` 削除、`scenario-test` CLI の内部実装が pytest 委譲)
- **依存追加**: `pytest-playwright>=0.5` (dev), `pytest-xdist>=3.0` (dev), `axe-playwright-python>=0.1.4` (a11y extras はすでに追加済み)
- **ユーザ影響**: 利用者ゼロ (本人検証段階) のため気にしない

## テスト計画

- [ ] `uv run pytest` (新規 + 既存) **全 pass** (目標 100+ ケース)
- [ ] `uv run scenario-test --help` smoke
- [ ] 新スキーマの testcase YAML を最低 3 件 (auth / form / list role) 用意し、テスト用ローカル HTTP サーバ (Python `http.server`) に対して実行成功
- [ ] a11y 違反を意図的に持つ HTML を fixture 化し、`a11y.py` が拾うことを確認
- [ ] CWV しきい値を超える遅延 fixture で `cwv.py` が FAIL を返すことを確認
- [ ] `upload_evidence.py --kind any` が拡張子から正しく分岐
- [ ] `record_to_yaml.py` で Playwright codegen の Python 出力 → YAML 変換 round-trip
- [ ] `playwright_executor.py` 行数 678 → 約 350 行を確認
- [ ] `grep -r "v0.3.0 以降\|v0.4.0 以降\|TODO\|FIXME" plugins/ndf/skills/playwright-scenario-test/` で 0 hit (リリース品質チェック)
- [ ] `docs/` の旧 helper 言及 (`navigate_post`, `find_click_target`, `detect_body_errors`) が 0 hit

## 進め方

タスクは依存順に Task 1 → 7 で進める。Task 4 (pytest-playwright runner) は最大規模なので、Task 1〜3 完了後に着手する。

各タスク完了ごとに `uv run pytest` を回し、リグレッションがないことを確認する。
