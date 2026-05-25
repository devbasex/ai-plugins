---
name: playwright-scenario-test
description: "pytest-playwright ベースのフル E2E テストフレームワーク統括。テスト計画・エビデンス収集・動画装飾・品質計測・レポート生成の 5 機能を組み合わせた包括的なテストワークフローを提供する。個別機能のみ必要な場合は各専門 skill を直接参照。"
when_to_use: "フル E2E テストワークフロー (計画→実行→エビデンス→品質→レポート) を一貫して行うとき / pytest-playwright 拡張 fixture (pwk_*) の全体像を把握したいとき / init_project.sh でプロジェクトをセットアップするとき。Triggers: 'pytest-playwright', 'pwk_role', 'pwk_evidence', 'init_project', 'シナリオテスト一式', 'フル E2E'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(playwright *)
  - Bash(python *)
---

# Playwright シナリオテスト Skill (v0.5.0)

Web アプリの E2E シナリオを **理論ベース** で計画し、**pytest-playwright** 上で実行、**動画 + Markdown レポート + accessibility / web vitals** を自動収集する一式の Skill。

## 機能別 Skill

個別機能のみ必要な場合は、以下の専門 skill を直接参照:

| Skill | 機能 |
|---|---|
| `/ndf:playwright-test-planning` | テスト計画 (HTSM / page role / チェックリスト) |
| `/ndf:playwright-evidence` | エビデンス収集 (video / trace / screenshot) |
| `/ndf:playwright-overlay` | 動画装飾 (字幕 + カーソル) |
| `/ndf:playwright-quality` | 品質計測 (accessibility / Web Vitals / body_check) |
| `/ndf:playwright-report` | レポート生成 + Drive 共有 |
| `/ndf:playwright-kit-ops` | スクリプト実行 (init_project / スキャン / アップロード) |

以下は全機能を組み合わせたフルワークフローの案内。

**v0.5.0 の方針**: 本 Skill は「テストを書き始めるためのスキャフォルダ」であり、`scripts/init_project.sh` で利用者プロジェクトに **all-in-one ディレクトリ** を埋め込んだ後は、Skill ディレクトリの存在に依存せず単独で動作する (CI / 別マシン / Skill 非導入のメンバー環境でも完結)。

## 用語集

ドメイン略語と正式名・意味の対応表。

| 略語 / 用語 | 正式名 / 意味 |
|---|---|
| accessibility (旧 a11y) | Web アクセシビリティ。WCAG 準拠を axe-core で機械検査 |
| web vitals (旧 CWV) | Google が定義する「ユーザ体感パフォーマンス指標」群 |
| LCP | Largest Contentful Paint — 最大コンテンツ描画時間 (体感ロード速度) |
| CLS | Cumulative Layout Shift — 累積レイアウトずれ量 (視覚的安定性) |
| TTFB | Time To First Byte — 初バイト到達時間 (サーバ応答速さ) |
| longest_task | Long Tasks API で観測した最長タスクのミリ秒値 (応答性代理指標) |
| HAR | HTTP Archive — ネットワーク通信ログのファイル形式 |
| trace | Playwright Trace — DOM スナップショット + 操作ログ + 動画の zip |
| overlay (旧 HUD) | テスト中に画面に重ねる赤丸カーソル + 字幕表示 |
| body_check | サーバが HTML 本文に出力した PHP/SSR エラー文字列の検出 |
| page role | LP / 一覧 / 詳細 / フォーム 等のページ種別。a11y / web vitals 自動実行の判定材料 |
| pwk | playwright_kit の略。fixture (`pwk_*`) / CLI option (`--pwk-*`) / env (`PWK_*`) の prefix |

## 提供物

```
playwright-scenario-test/   ← Skill 自体 (この Skill ディレクトリ)
├── SKILL.md                ← このファイル
├── pyproject.toml          ← Skill 開発用 (pytest entry-point 含む)
├── playwright_kit/         ← Python パッケージ本体 (旧 scenario_test)
│   ├── pytest_plugin.py    ← pytest11 entry-point (addoption / markers / hooks)
│   ├── pytest_report.py    ← report.md 生成
│   ├── fixtures/           ← pytest fixtures
│   │   ├── auth.py         — pwk_config / pwk_role_<id>
│   │   ├── evidence.py     — pwk_evidence (HAR / trace / console / pageerror)
│   │   ├── accessibility.py — page_role marker autouse で axe-core
│   │   ├── web_vitals.py   — page_role marker autouse で Core Web Vitals
│   │   └── body_check.py   — page.on("response") で本文エラー文字列を検出
│   ├── accessibility.py / web_vitals.py — axe-core / Web Vitals ランナー (純関数)
│   ├── overlay.py          ← 赤丸カーソル + 字幕 (旧 HUD) JS
│   ├── video.py            ← webm → mp4 変換
│   └── config.py           ← scenario.config.yaml ローダ
├── docs/                   ← テスト方法論 (HTSM / ISTQB / FEW HICCUPPS)
├── (scripts/ は playwright-kit-ops/ skill に移動)
│   └── → /ndf:playwright-kit-ops を参照
└── templates/              ← 利用者プロジェクト用雛形
    ├── pyproject.toml.runtime  — runtime 用 pyproject (dev 用 deps を排除)
    ├── runtime-gitignore       — .venv / __pycache__ / reports/
    ├── runtime-README.md       — Skill 無し環境向けの最低限の使い方
    ├── run.sh / run.bat        — ワンコマンドランチャ
    ├── scenario.config.yaml    — base_url / roles / accessibility / web_vitals 設定
    ├── conftest.py.template    — 利用者の conftest.py 雛形
    └── test_*.py.template      — auth / list / form / dashboard 雛形
```

init 後の利用者プロジェクト側 (Skill 非依存):

```
your-app/
└── scenario-test/                 ← all-in-one ランタイム (--runtime-dir で名前変更可)
    ├── playwright_kit/            ← Python パッケージ本体
    ├── scripts/                   ← 補助 CLI
    ├── tests/                     ← 利用者の pytest テスト
    │   ├── conftest.py
    │   └── test_*.py
    ├── reports/                   ← 実行結果 (.gitignore 推奨)
    ├── scenario.config.yaml       ← 利用者の設定
    ├── run.sh / run.bat           ← ワンコマンドランチャ
    ├── pyproject.toml             ← runtime 用 (testpaths=["tests"])
    ├── uv.lock                    ← 再現性のため commit 推奨
    └── README.md                  ← runtime-README.md コピー
```

## クイックスタート

```bash
# 1) このディレクトリ (Skill) で利用者プロジェクトを初期化
cd .claude/plugins/ndf/skills/playwright-scenario-test   # Skill のパスは環境による
./scripts/init_project.sh /path/to/your-app
# →  /path/to/your-app/scenario-test/ 一式が作成され、uv sync + chromium install
#    まで完了する

# オプション: 配置先ディレクトリ名をカスタマイズ
./scripts/init_project.sh /path/to/your-app --runtime-dir e2e
# → /path/to/your-app/e2e/

# Windows
scripts\init_project.bat C:\path\to\your-app

# 2) base_url / roles を編集
$EDITOR /path/to/your-app/scenario-test/scenario.config.yaml

# 3) tests/ にテストを追加 (init 時に test_auth/list/form/dashboard 雛形は配置済)
$EDITOR /path/to/your-app/scenario-test/tests/test_admin.py

# 4) 実行
cd /path/to/your-app
./scenario-test/run.sh                            # 全テスト
./scenario-test/run.sh -k test_admin              # nodeid フィルタ
./scenario-test/run.sh --pwk-overlay              # 動画に赤丸カーソル + 字幕
./scenario-test/run.sh --pwk-drive-folder=<ID>    # Drive 自動アップロード
```

→ 以降このディレクトリ (Skill) は不要。`your-app/scenario-test/` 単体で完結する。

### 複数ランタイム共存

```bash
./scripts/init_project.sh your-app --runtime-dir e2e-prod
./scripts/init_project.sh your-app --runtime-dir e2e-staging
# → your-app/e2e-prod/run.sh と your-app/e2e-staging/run.sh が独立に動く
```

## 利用者は通常の pytest テストを書く

```python
# tests/test_admin_dashboard.py
import pytest
from playwright.sync_api import Page, expect

@pytest.mark.page_role("dashboard")
@pytest.mark.role("admin")
def test_admin_kpi_view(page: Page, pwk_role_admin):
    page.goto("/admin/dashboard")
    expect(page.get_by_role("heading", name="売上サマリ")).to_be_visible()
    page.get_by_role("link", name="ユーザ管理").click()
    expect(page).to_have_url(lambda u: "/admin/users" in u)
```

提供 fixture / marker:

| 提供 | 種別 | 役割 |
|---|---|---|
| `pwk_config` | session fixture | `scenario.config.yaml` をロード (Config dataclass) |
| `pwk_role_<id>` | function fixture (動的) | 該当 role で login 済の storage_state を context に注入 |
| `pwk_evidence` | function fixture | HAR / trace / console.error / pageerror / body_check の集中管理 |
| `pwk_accessibility_scan()` | helper | 任意のタイミングで axe-core を 1 回実行 |
| `pwk_web_vitals_measure()` | helper | 任意のタイミングで Web Vitals を 1 回計測 |
| `pwk_body_check_scan()` | helper | 任意のタイミングで現在の page 本文を 1 回 body_check |
| `@pytest.mark.page_role("form")` | marker | accessibility / web_vitals autouse の判定 (auto_roles 設定に従う) |
| `@pytest.mark.role("admin")` | marker | report.md 集計用 (login 自体は `pwk_role_<id>` fixture) |
| `@pytest.mark.phase(1)` | marker | report.md フェーズ集計 |
| `@pytest.mark.priority("high")` | marker | report.md ソート |
| `@pytest.mark.no_body_check` | marker | body_check autouse をこの test では skip |

### body_check (PHP / SSR エラー検出, v0.4.0+)

PHP / SSR が HTML 本文に直接出力した `Fatal error` / `Warning:` / `STRICT:` 等の
エラー文字列は console.error / pageerror では拾えない。`body_check` は
`page.on("response")` で全 HTML レスポンスを監視し、文字列パターンとの substring
一致で violation を記録する。**default で enabled + PHP 系パターン内蔵** なので、
config を書かなくても PHP プロジェクトでまず動く。

パターンを上書きしたい場合のみ `scenario.config.yaml` で明示する:

```yaml
# scenario.config.yaml (省略可)
body_check:
  enabled: true
  fatal_patterns: ["Fatal error", "Uncaught", "Parse error"]
  warning_patterns: ["STRICT:", "Warning:", "Notice:", "Deprecated:"]
  warning_head_chars: 300          # warning_patterns は本文先頭 N 文字のみ走査 (旧名 warning_head_bytes も alias)
  not_found_patterns: ["File not found"]
  fail_on_match: true              # false で情報収集モード (PASS のまま report に記録)
```

- 違反は `case_dir/body_check.jsonl` に 1 violation = 1 行で出力 (`jq`/`grep`
  で集計しやすいよう flat structure にしている)
- `report.md` のサマリ表に `body_check` カラムが、件数 > 0 の場合は詳細セクション
  (URL / pattern / snippet) が出力される
- 機能ごと無効化したい場合は `body_check.enabled: false` を明示
- 個別カテゴリのみ無効化したい場合は `fatal_patterns: []` のように明示空指定
- 個別 test で skip したい場合は `@pytest.mark.no_body_check` を付与
- 非 PHP プロジェクトでは default の `Notice:` / `Warning:` 等が誤検出になる
  場合あり。その場合は `warning_patterns: []` で warning カテゴリだけ無効化するか
  `enabled: false` で機能ごと off にする

## CLI options

ランチャ経由 (`./run.sh` / `run.bat`) でも、`uv run pytest` を直接呼んでも、同じ option がそのまま効く。

| option | 役割 |
|---|---|
| `--pwk-config <path>` | `scenario.config.yaml` のパス。env `PWK_CONFIG` / CWD の同名ファイルでも可 |
| `--pwk-out-dir <path>` | 成果物出力先 (default: `reports/<run-id>/`) |
| `--pwk-no-evidence` | HAR / trace / video の収集を OFF |
| `--pwk-har-mode {minimal,full,none}` | HAR 録画モード (default: minimal、Issue #62) |
| `--pwk-overlay` | overlay (赤丸カーソル + 字幕、旧名 HUD) を全 page に inject |
| `--pwk-drive-folder <id>` | session 終了時に report.md と evidence を Drive アップロード |

pytest 標準と組み合わせて使える:

```bash
# page_role marker が付いたテストだけ実行
./scenario-test/run.sh -m "page_role"

# 4 worker で並列実行
./scenario-test/run.sh -n 4

# 動画レポートを Drive へ自動アップロード
./scenario-test/run.sh --pwk-drive-folder=<FOLDER_ID>

# pytest-html を組み合わせて HTML report も
./scenario-test/run.sh --html=reports/index.html --self-contained-html
```

## 標準ワークフロー (理論ベース計画)

```
[A] 対象 URL を渡される
       │
[B] page role を判定                       playwright-kit-ops/scripts/classify_page_role.py --url <URL>
       ▼
[C] 該当 checklist を開く                   docs/checklists/checklist-{role}.md
       │     全項目を「適用」or「不適用 (理由付き)」で判定
       ▼
[D] 必須技法を確定                          docs/03-test-techniques.md § 11
       ▼
[E] pytest テストを書く                     templates/test_<role>.py.template を起点に
       │     `playwright codegen` で操作 → そのまま test 関数に貼る or 整形
       ▼
[F] 実行                                   ./scenario-test/run.sh
       │     trace.zip / video / HAR / console / accessibility / web_vitals を自動収集
       ▼
[G] レポート確認                            scenario-test/reports/<run-id>/report.md
       │     --pwk-drive-folder 指定で Drive にアップロード + viewer URL 化
       ▼
[H] 不具合発見 → bug report                docs/05-bug-report.md
             FEW HICCUPPS の oracle 軸を必ず付与
```

## 単発 CLI ツール (補助)

| Script | 用途 |
|---|---|
| → `/ndf:playwright-kit-ops` | スクリプト群は playwright-kit-ops skill に移動。詳細はそちらを参照 |

## docs/ 配下 (理論ベース知識)

| ファイル | 内容 |
|---|---|
| `docs/01-methodology.md` | 総論: HTSM / FEW HICCUPPS / ISO 29119-3 の位置付け |
| `docs/02-page-roles.md` | page role 分類 (lp/list/item/edit/form/search/...) |
| `docs/03-test-techniques.md` | テスト技法 (EP / BVA / Decision Table / Pairwise) と role 必須マッピング |
| `docs/04-playwright-mapping.md` | Playwright API → role / 観点 マッピング |
| `docs/05-bug-report.md` | bug report 仕様 (ISO 29119-3 + FEW HICCUPPS) |
| `docs/06-pytest-playwright.md` | pytest-playwright fixture / CLI option / playwright_kit 拡張との対応関係 |
| `docs/checklists/checklist-<role>.md` | role 別チェックリスト (lp/list/item/edit/form/search/dashboard/auth/cart-checkout/modal-wizard/common) |

## テスト雛形 (templates/)

利用者は role に応じて `test_<role>.py.template` をコピーして編集する (init 時に
4 ファイルが配置済)。各テンプレートには:

- 該当 `page_role` marker
- 該当 `pwk_role_<id>` fixture
- `expect()` ベースの web-first assertion
- 正常系 + 1 件以上の異常系

が含まれる。

## 開発者向け: Skill 単体で動かす旧運用

Skill 自身の改修・テスト時のみ、Skill ディレクトリで直接 pytest を回す:

```bash
cd .claude/plugins/ndf/skills/playwright-scenario-test
uv sync
uv run pytest -q                # 159 件 pure 関数テスト
```

利用者環境向け (`init_project.sh` 経由) と Skill 開発用は **別の uv プロジェクト**
として分離される (利用者側は `templates/pyproject.toml.runtime` を用い、開発側は
リポジトリルートの `pyproject.toml` を用いる)。

## 制約 / 注意

- **依存**: `pytest>=8.0`, `pytest-playwright>=0.5`, `pytest-xdist>=3.0`, `playwright>=1.50`
- **認証情報は YAML に直書きしない**: `scenario.config.yaml` の `fields.Password` 等は `${ENV_VAR}` で参照し、実値は環境変数 (`.env` / `direnv` / shell export) で管理してください。リポジトリに認証情報をコミットしないこと
- **トレース / HAR / 動画は機微情報を含む**:
  - HAR には URL のクエリ文字列・Cookie・Authorization ヘッダ等が記録されます
  - trace.zip には localStorage / 操作履歴 / DOM スナップショットが含まれます
  - `--pwk-drive-folder=<id>` は **private folder** を指定し、共有相手を限定してください
  - `upload_evidence.py --public` を付けない限り Drive にも非公開でアップ (既定)
- **CI**: GitHub Actions では `cd scenario-test && uv run pytest -n auto` でそのまま回せる
- **Skill 更新時の追従**: 開発中につきコピー済ランタイムは再 `init_project.sh` で
  上書き運用 (`scenario.config.yaml` / `tests/conftest.py` / `tests/test_*.py` は
  既存があれば skip するため利用者編集物は保護される)

## 関連ドキュメント

- `docs/README.md` — 知識マップ
- `templates/scenario.config.yaml` — 設定例
- `templates/runtime-README.md` — init 後の `your-app/scenario-test/README.md` 元
