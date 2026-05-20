# PLAN17: playwright-scenario-test v0.3.0 — pure pytest-playwright 完全移行

> ## 🚨 引継ぎメモ (2026-04-26 セッション切替時点)
>
> ### 現在の状態
>
> - **PR #56 (v0.2.5 transition) はマージ済み** (mergeCommit `d288f92`、2026-04-26T04:04:26Z)
> - 削除済 feature ブランチ: `feature/scenario-test-v0.3.0-oss-quality`
> - 現在のブランチ: **`main`** (`origin/main` と同期済み)
> - 関連 PR の経緯はすべて `plugins/ndf/CLAUDE.md` の v4.1.1 セクションに記録済み
> - 本 PLAN17 は **これから実装する** (まだコード変更ゼロ)
>
> ### 未着手のタスク (本 PLAN17 の Task 1〜8)
>
> 何も着手していない。下記「タスク分解」節を上から順に実行してください。
>
> ### 着手前にやること
>
> 1. `git -C /work/ai-plugins checkout -b feature/scenario-test-v0.3.0-pytest-native` で新ブランチ作成
> 2. `cd /work/ai-plugins/plugins/ndf/skills/playwright-scenario-test`
> 3. `uv sync` で依存解決確認 (現状 v0.2.5 = locator-first DSL 中間版)
> 4. `uv run pytest` で **126 件 全 pass** することを確認 (出発点)
>
> ### v0.2.5 から v0.3.0 への影響範囲 (削除予定の DSL 層)
>
> 以下を削除/全面書き換えすることを忘れない:
>
> - `scenario_test/testcase.py` の `Step` / `LocatorSpec` / `KNOWN_STEP_KINDS` / `discover_testcases` / `filter_testcases` / `parse_filter` (testcase.py 自体は残しても良いが、`Step` 系は削除)
> - `scenario_test/locator_steps.py` (全削除)
> - `scenario_test/runner.py` (全削除)
> - `scenario_test/cli.py` (全削除 or `pytest` invoker 薄ラッパに縮退)
> - `scenario_test/playwright_executor.py` (login 部分のみ fixture へ移植して全廃)
> - `scenario_test/report.py` (pytest plugin の `pytest_terminal_summary` hook で再実装)
> - `scripts/record_to_yaml.py` (codegen Python をそのまま test ファイルに使うため不要)
> - `scripts/generate_test_plan.py` (pytest 雛形生成版に置換)
> - `templates/testcase-*.yaml.template` 6 ファイル (pytest テスト雛形に置換)
> - `templates/config.example.yaml` (`scenario.config.yaml` ベースに変更)
> - `tests/test_step_schema.py` / `test_locator_steps.py` / `test_record_to_yaml.py` / `test_filter_and_slug.py` / `test_templates.py` (削除して新フィクスチャ向けに書き直し)
>
> ### 残す物 (carry forward, v0.2.5 から)
>
> - `scenario_test/a11y.py` (`scan_page` / `is_available` / `should_auto_scan`)
> - `scenario_test/cwv.py` (`measure_page` / `judge` / `passed`)
> - `scenario_test/evidence.py` の listener / tolerated patterns ロジック (fixture 内に再構成)
> - `scenario_test/hud.py` (HUD overlay JS 一式)
> - `scenario_test/video.py` (webm → mp4 変換)
> - `scripts/upload_evidence.py` (Drive 連携 CLI)
> - `scripts/run_a11y_scan.py` / `check_cwv.py` / `record_scenario.py` (CLI 単発)
> - `scripts/build_gdoc_with_drive_links.py`, `gdrive_upload_dir.py`, `_drive_auth.py`, `upload_md_as_gdoc.py`
> - `docs/` 配下 (方法論ドキュメント、checklists)
>
> ### バージョン bump 予定
>
> - `pyproject.toml`: `0.2.5` → **`0.3.0`**
> - `plugins/ndf/.claude-plugin/plugin.json`: `4.1.1` → **`4.2.0`**
> - `plugins/ndf/CLAUDE.md` に v4.2.0 セクションを追加
>
> ### 着手順序の推奨 (本 PLAN17 の段階的実装より具体化)
>
> Phase 1 (Task 1-2): pytest plugin 骨組み + auth fixture を作って、最小 1 テスト (`def test_login(page, ndf_role_admin): page.goto("/")`) が pytest 経由で通ることを確認
> → ここで一旦 commit (旧 DSL は残したまま新 fixture と両立)
>
> Phase 2 (Task 3-4): evidence + a11y/CWV autouse hook 追加
> → axe-core 違反を含むダミー HTML で fixture が機能することを確認
>
> Phase 3 (Task 5-6): HUD overlay + report.md + Drive 連携 を fixture/hook で実装
> → ここで end-to-end のスモークが完成
>
> Phase 4 (Task 7): templates / docs / SKILL.md / pyproject.toml / plugin.json を一気に書き直し
>
> Phase 5 (Task 8): **旧 DSL を一括削除**。最後にまとめて削除すると PR の見通しが効く
>
> ### 検証
>
> 各 Phase 完了ごとに `uv run pytest` を実行し、リグレッションがないことを確認。Phase 5 まで完了したら最終的に `100+ 件 (smoke + unit) 全 pass` を目指す。
>
> ### 関連リンク
>
> - 親 PR (v0.2.5 transition): https://github.com/takemi-ohama/ai-plugins/pull/56
> - mergeCommit: `d288f92`
>
> ---

## 関連リンク

- 親 PR (v0.2.5 transition): [#56 feat(ndf): playwright-scenario-test ...](https://github.com/takemi-ohama/pull/56)
- 関連プラン: [issues/PLAN15.md](./PLAN15.md) (v0.2.5)
- 廃止: ~~issues/PLAN16.md~~ (Codex レビューで「自前 DSL より pytest 直接利用が OSS 品質として優れる」と判断、PLAN17 に置換)

## 概要

playwright-scenario-test を **自前 YAML DSL → pure pytest-playwright** に全面移行し、OSS リリース品質に到達させる。本リポジトリで投資した locator-first DSL / runner / dispatcher は捨て、pytest エコシステムの恩恵を最大化する。

| 項目 | 由来 |
|------|------|
| `scenario_test/` の DSL 層 (testcase.py / locator_steps.py / runner.py / cli.py) を全廃 | Codex 第二意見レビュー (PR #56) |
| pytest-playwright fixture 上で利用者が直接 `def test_xxx(page): ...` を書く形に移行 | OSS quality + IDE 統合 + ecosystem |
| HUD 字幕 / 動画 / Drive / report.md / a11y / CWV / 認証 ヘルパは pytest plugin + fixture として再実装 | 既存資産の再利用 |

## 採用判断の根拠

Codex レビュー (PR #56) で指摘された **Major 7 + Minor 4** のうち、**6 件は pytest-native への移行で自動解決** する:

| Codex 指摘 | 自前 DSL | pure pytest |
|---|---|---|
| Major-3 step failure 後の続行 | `continue_on_failure` schema 拡張 | **pytest 標準: assertion で test 関数即終了** |
| Major-5 CWV silent PASS | SKIP/UNKNOWN 状態を自前で設計 | **`pytest.skip()` / `pytest.xfail()`** |
| Major-6 trace_relpath が結果モデルに無い | dataclass 拡張 + report.py 修正 | **pytest-playwright 標準 artifact** |
| Major-7 lifecycle 抱え込み | executor を pure 化する大改修 | **fixture として最初から分離** |
| Major-1 record_to_yaml 順序非保証 | `ast.NodeVisitor` で書き直し | **不要: codegen の Python そのまま使う** |
| Major-2 record_to_yaml chain 欠落 | locator chain 表現を schema 拡張 | **不要: codegen 出力をそのまま利用** |

加えて IDE 統合 (VS Code Test Explorer / JetBrains)、`pytest-html` / `allure-pytest`、`pytest-xdist`、`to_have_screenshot()` (visual regression) などのエコシステムが直接使える。

## 設計方針

### 利用者は通常の pytest テストを書く

```python
# tests/test_admin_dashboard.py
import pytest
from playwright.sync_api import Page, expect

@pytest.mark.page_role("dashboard")
@pytest.mark.role("admin")
def test_admin_kpi_view(page: Page, ndf_role_admin):
    page.goto("/admin/dashboard")
    expect(page.get_by_role("heading", name="売上サマリ")).to_be_visible()
    page.get_by_role("link", name="ユーザ管理").click()
    expect(page).to_have_url(lambda u: "/admin/users" in u)
```

NDF 提供物は (1) **fixture 群**, (2) **pytest plugin**, (3) **テンプレート**, (4) **CLI script (Drive 連携 / a11y 単発)** に縮退。

### 提供物の責務分離

| 提供物 | 役割 |
|---|---|
| `scenario_test/pytest_plugin.py` | pytest entry point (fixture / hook / report.md / Drive 連携 / a11y/CWV autouse / HUD overlay) |
| `scenario_test/fixtures/auth.py` | `ndf_config` / `ndf_role_<id>` (login 済み page を返す) |
| `scenario_test/fixtures/evidence.py` | `ndf_evidence` (HAR / trace artifact 統合) |
| `scenario_test/fixtures/a11y.py` | `ndf_a11y_scan` (axe-core, autouse condition: page_role marker) |
| `scenario_test/fixtures/cwv.py` | `ndf_cwv_measure` (Core Web Vitals, autouse condition: page_role marker) |
| `scenario_test/hud.py` | 既存維持 (HUD overlay JS) |
| `scripts/upload_evidence.py` | 既存維持 (CLI: trace/HAR/video Drive アップ) |
| `scripts/run_a11y_scan.py` | 既存維持 (CLI: 単発スキャン) |
| `scripts/check_cwv.py` | 既存維持 (CLI: 単発計測) |
| `templates/conftest.py.template` | 利用者の `tests/conftest.py` 雛形 |
| `templates/test_*.py.template` | 役割別 (auth / list / form / edit) のテスト雛形 |

## 削除する物 (v0.2.5 で投資した DSL 層)

- `scenario_test/testcase.py` の `Step` / `LocatorSpec` / `KNOWN_STEP_KINDS` / `discover_testcases` / `filter_testcases` / `parse_filter`
- `scenario_test/locator_steps.py` 全体
- `scenario_test/runner.py` 全体
- `scenario_test/cli.py` 全体 (またはごく薄い `pytest` invoker に縮退)
- `scenario_test/playwright_executor.py` 全体 (login 部分のみ fixture へ移植)
- `scenario_test/report.py` 全体 (pytest plugin の terminal_summary hook + 独自 writer に再実装)
- `scripts/record_to_yaml.py` 全体 (codegen Python をそのまま `tests/test_*.py` として保存する)
- `scripts/generate_test_plan.py` 全体 (pytest 雛形生成スクリプトに置換)
- `templates/testcase-*.yaml.template` 6 ファイル
- `templates/config.example.yaml`

## 残す物 (v0.2.5 から carry forward)

- `scenario_test/a11y.py` (`scan_page` / `is_available` / `should_auto_scan`)
- `scenario_test/cwv.py` (`measure_page` / `judge` / `passed`)
- `scenario_test/evidence.py` (listener / tolerated patterns ロジックを fixture 内に再構成)
- `scenario_test/hud.py` (HUD overlay JS 一式)
- `scenario_test/video.py` (webm → mp4 変換)
- `scripts/upload_evidence.py` (Drive 連携)
- `scripts/run_a11y_scan.py` (CLI 単発)
- `scripts/check_cwv.py` (CLI 単発)
- `scripts/record_scenario.py` (Playwright codegen 起動)
- `scripts/build_gdoc_with_drive_links.py`, `gdrive_upload_dir.py` (Drive 連携)
- `docs/` 配下 (方法論ドキュメント、checklists)

## 利用者プロジェクトの構成

```
my-e2e/
├── pyproject.toml                # [tool.pytest.ini_options] markers + ndf-config 設定
├── conftest.py                   # NDF plugin の自動 discover (`pytest -p ndf_plugin`)
├── scenario.config.yaml          # base_url / roles / a11y/CWV 設定
└── tests/
    ├── test_admin_dashboard.py
    ├── test_user_form.py
    └── ...
```

利用者が書く Python は通常の pytest テスト。NDF が提供する markers / fixtures を使って role / page_role / a11y を表現する。

## タスク分解

### Task 1: pytest plugin 骨組み

- **対象**: `scenario_test/pytest_plugin.py` (新規), `pyproject.toml` (`[project.entry-points."pytest11"]`)
- **変更内容**:
  - `pytest_addoption(parser)`: `--ndf-config` / `--ndf-out-dir` / `--ndf-no-evidence` 等
  - `pytest_configure(config)`: `ndf_config` を読み込み Session に保存
  - markers の登録: `page_role(*roles)` / `role(role_id)` / `phase(num)` / `priority(level)`
  - `pytest_runtest_setup(item)` で marker 検査

### Task 2: 認証 fixture (`fixtures/auth.py`)

- **対象**: `scenario_test/fixtures/auth.py` (新規), `scenario_test/pytest_plugin.py`
- **変更内容**:
  - `ndf_config` fixture: `Config.load(...)` を session scope で
  - 各 role に対し `ndf_role_<id>` fixture を動的生成 (login 済み storage_state を function scope で渡す)
  - storage_state の caching (1 session 内で同じ role はログインを 1 回に減らす)

### Task 3: evidence fixture + autouse hook

- **対象**: `scenario_test/fixtures/evidence.py` (新規), `scenario_test/pytest_plugin.py`
- **変更内容**:
  - `ndf_evidence` fixture: HAR / trace 設定を `browser_context_args` に inject
  - `pytest_runtest_makereport(item, call)` で FAIL 時に trace 保存パスを confirm
  - tolerated_console_errors / tolerated_page_errors を listener として attach (現 `evidence.py` ロジック流用)

### Task 4: a11y / CWV を marker autouse 化

- **対象**: `scenario_test/fixtures/a11y.py` (新規), `scenario_test/fixtures/cwv.py` (新規)
- **変更内容**:
  - `@pytest.mark.page_role("form")` が付与された test の終了直前に axe-core を自動実行
  - 同様に CWV を計測
  - 違反は `pytest.fail()` (default) または `pytest.skip()` で SKIP 扱い (config 切替)

### Task 5: HUD overlay を `browser_context_args` で注入

- **対象**: `scenario_test/pytest_plugin.py`
- **変更内容**:
  - `browser_context_args` fixture を override し、`hud.HUD_INIT_SCRIPT` を `add_init_script` で全 page に inject
  - 字幕の更新 API (`set_caption`) を fixture 経由で公開 → 利用者がオプションで叩ける
  - default は HUD なしで OK (動画必要なときだけ `--ndf-hud` で有効化)

### Task 6: report.md 生成 + Drive 連携

- **対象**: `scenario_test/pytest_plugin.py`, `scenario_test/report.py` (再実装、薄く)
- **変更内容**:
  - `pytest_terminal_summary(terminalreporter, exitstatus, config)` で全 test の result を収集
  - 既存 report.md と同等のフォーマットで `reports/<run-id>/report.md` を生成
  - `--ndf-drive-folder=<id>` 指定時は `pytest_sessionfinish` で `upload_evidence.py` の関数を直接呼び、Drive アップロード + リンク差し込み

### Task 7: テンプレート / docs / scripts 整理

- **対象**: `templates/conftest.py.template`, `templates/test_*.py.template` 4-5 ファイル, `templates/scenario.config.yaml`, `scripts/generate_test_plan.py` (pytest 雛形生成版に置換), `SKILL.md` 全面書き直し, `docs/04-playwright-mapping.md` 更新, `docs/05-bug-report.md` 更新, `pyproject.toml` (version 0.2.5 → 0.3.0), `plugins/ndf/.claude-plugin/plugin.json` (4.1.1 → 4.2.0), `plugins/ndf/CLAUDE.md` v4.2.0 セクション
- **変更内容**:
  - 利用者向け: pytest 直書き例 / `pytest -m "page_role:form"` / `pytest -n 4` / `pytest --html=` 等の標準パイプライン
  - SKILL.md は「pytest 採用 + NDF が提供する fixture/marker」を中心に書き直す
  - docs は locator-first 表現を pytest コードでそのまま例示 (DSL 言及を全削除)

### Task 8: 旧 DSL 削除 + テスト全置換

- **対象**: 上記「削除する物」リストを実行、`tests/` も新フィクスチャ向けに書き直し
- **変更内容**:
  - 単体テストは `pure 関数` (a11y.judge / cwv.judge / detect_kind / detect_mime / hud constants 等) のみ残す
  - 統合テストとして tmp http server + pytest-playwright で `tests/integration/test_smoke.py` を 1 件追加 (本来 OSS だと CI を望むため)
  - 削除対象テスト: `test_step_schema.py` / `test_locator_steps.py` / `test_record_to_yaml.py` / `test_filter_and_slug.py` (parse_filter / slugify 部分は廃止) / `test_templates.py` (templates 自体が変わるため作り直し)

## 影響範囲

- **互換性**: v0.2.5 → v0.3.0 で完全な breaking change。利用者は YAML を書き直す必要あり (誰も使っていない前提なので問題なし)
- **依存追加 (main)**: `pytest>=8.0`, `pytest-playwright>=0.5`, `pytest-xdist>=3.0`
- **依存削除**: なし (既存依存はすべて流用)
- **CI**: pytest 標準なので GitHub Actions で簡単に回せる (`uv run pytest -n auto`)

## テスト計画

- [ ] `uv run pytest tests/unit/` で pure 関数テスト (a11y.judge / cwv.judge / detect_mime / hud / video) が **全 pass**
- [ ] `uv run pytest tests/integration/test_smoke.py` で local http server (`http.server` で 1 ページ起動) に対する end-to-end (login → click → expect) が pass
- [ ] `pytest -p ndf.pytest_plugin -n 4 tests/` で並列 4 worker で smoke tests が pass
- [ ] `pytest --ndf-drive-folder=<test-folder>` でテスト後に Drive 連携が動作 (手動確認)
- [ ] `pytest -m "page_role(form)"` で form テストのみ実行
- [ ] FAIL ケースで axe-core 違反が `pytest --html` レポートにも出る
- [ ] `playwright codegen → tests/test_<scenario>.py` への手順が SKILL.md に明記、実例で動作確認

## 段階的実装

1. **Phase 1**: Task 1 + 2 (plugin 骨組み + auth fixture) — 単一 test がログイン + assertion で動くこと
2. **Phase 2**: Task 3 + 4 (evidence + a11y/CWV autouse) — failed test で trace/axe が自動保存されること
3. **Phase 3**: Task 5 (HUD) + Task 6 (report.md + Drive) — エンドツーエンドで本物のレポート生成
4. **Phase 4**: Task 7 (templates + docs + version) — リリース直前
5. **Phase 5**: Task 8 (旧 DSL 削除) — 最後にまとめて削除し、PR を分かりやすく

## v0.4.0 以降 (本 PR 範囲外)

- pytest-html / allure-pytest 連携 (PR option として)
- Visual regression (`expect(page).to_have_screenshot()`) サポート
- bug report 自動生成 (Codex 指摘の旧 PLAN16 Task 4) — pytest hook で容易に追加可能
