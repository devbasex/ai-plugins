# PLAN19: playwright-scenario-test の Skill 非依存化（プロジェクト自己完結化）

- 起票日: 2026-04-29
- 対象 Skill: `ndf:playwright-scenario-test` (現行 v0.4.0)
- 対象パス: `plugins/ndf/skills/playwright-scenario-test/`
- 関連: PLAN17 (pure pytest-playwright 完全移行) / PLAN18 (body_check 復活)

## 背景・課題

現行 v0.4.0 の構造では、**テスト実行は Skill ディレクトリ配下の uv プロジェクトに依存**している。
具体的には:

- `scenario_test/` パッケージ本体（pytest plugin / fixtures / a11y / cwv / body_check / report、
  v0.5.0 で `playwright_kit` にリネーム予定）は Skill ディレクトリにのみ存在
- `pyproject.toml` / `uv.lock` も Skill ディレクトリ側にあり、`[project.entry-points."pytest11"]`
  で pytest plugin を登録している
- 利用者は SKILL.md の手順に従い、**Skill ディレクトリで `uv sync` してから利用者プロジェクトで
  pytest を呼ぶ**運用 — 結果として:
    1. Skill が消えるとテストが動かない（CI / 別マシン / Skill 非導入のメンバー環境で破綻）
    2. uv プロジェクトが Skill 側にあるため、利用者プロジェクトの依存と分離されており再現性が低い
    3. Drive 連携 / a11y / CWV 等の補助スクリプトもパス参照が Skill ディレクトリ前提

これにより、テスト**作成時**だけでなく**実行時・メンテ時**まで Skill の存在に縛られている。
本来 Skill は「テストを書き始めるためのスキャフォルダ」であり、初期化後は利用者プロジェクト
単体で動作するべき。

## ゴール

1. **必要なスクリプト・実行環境（uv）をすべて利用者プロジェクトにコピー**して自己完結させる
2. **実行用バッチファイル（sh / bat）を 1 本実行するだけ**でシナリオテストが走る形にする
3. Skill が削除された環境（マシン移行 / CI / 別開発者環境）でも継続して動作する

## 設計方針

### 1. プロジェクト配置構造（init 後）

利用者プロジェクト直下に `scenario-test/` ディレクトリ（**default 名、変更可能**）を 1 つだけ
作成し、**テスト・設定・レポート・ランチャを含むすべてをその中に集約**する。利用者プロジェクト
直下を汚さない（ルートに散らばらない）。

```
your-app/                         ← 利用者プロジェクト（ルートに余計なファイルを置かない）
└── scenario-test/                ← all-in-one ランタイム（名前変更可）
    ├── playwright_kit/           ← Python パッケージ本体（旧 scenario_test、リネーム）
    │   ├── pytest_plugin.py
    │   ├── fixtures/
    │   ├── accessibility.py      ← 旧 a11y.py（axe-core ランナー）
    │   ├── web_vitals.py         ← 旧 cwv.py（LCP/CLS/TTFB 計測）
    │   ├── body_check.py         ← PHP/SSR エラー検出
    │   ├── overlay.py            ← 旧 hud.py（赤丸カーソル+字幕）
    │   ├── pytest_report.py
    │   └── ...
    ├── scripts/                  ← CLI 補助スクリプト（classify_page_role / a11y / cwv / drive 等）
    ├── tests/                    ← 利用者が書く pytest テスト
    │   ├── conftest.py
    │   └── test_*.py
    ├── reports/                  ← 実行結果（gitignore 推奨）
    ├── scenario.config.yaml      ← 利用者の設定（base_url / roles / a11y / CWV 等）
    ├── run.sh                    ← Linux/Mac 用ワンコマンドランチャ
    ├── run.bat                   ← Windows 用ワンコマンドランチャ
    ├── pyproject.toml            ← runtime 用に整形済（testpaths=tests）
    ├── uv.lock                   ← lock 同梱で再現性確保
    ├── README.md                 ← init 後の使い方（Skill なしでも読める）
    └── .gitignore                ← .venv / __pycache__ / reports/
```

#### 1.2 package 名 / fixture prefix の整理 (v0.5.0 で実施・破壊的変更)

**目的**: 本改修の主眼は **NDF Skill から切り離してプロジェクト単独で動作させること**。
これに合わせて、NDF 由来の名前空間 (`scenario_test` 由来 / `ndf_*` 由来) を**汎用的な
`playwright_kit` (`pwk_*`) 系**に置き換える。

**Layer 別 rename 表**:

| Layer | 旧 | 新 |
|---|---|---|
| Skill 名 | `playwright-scenario-test` | (変更なし) |
| ランタイムディレクトリ名 (default) | `scenario-test/` | (変更なし、名前変更可) |
| Python パッケージ名 | `scenario_test` | **`playwright_kit`** |
| pytest entry-point ID | `ndf-scenario-test` | **`playwright-kit`** |
| import path | `from scenario_test.X import Y` | `from playwright_kit.X import Y` |
| pytest_plugins 列挙 | `"scenario_test.fixtures.auth"` etc | `"playwright_kit.fixtures.auth"` etc |

**fixture / CLI option / env var の rename 表** (`ndf_*` / `--ndf-*` / `NDF_*` を除去):

| 種別 | 旧 | 新 |
|---|---|---|
| session fixture | `ndf_config` | `pwk_config` |
| dynamic fixture | `ndf_role_<id>` | `pwk_role_<id>` |
| function fixture | `ndf_evidence` | `pwk_evidence` |
| function fixture | `ndf_out_dir` | `pwk_out_dir` |
| helper fixture | `ndf_a11y_scan` | `pwk_accessibility_scan` (Phase 0a で `a11y` → `accessibility`) |
| helper fixture | `ndf_cwv_measure` | `pwk_web_vitals_measure` (Phase 0a で `cwv` → `web_vitals`) |
| helper fixture | `ndf_body_check_scan` | `pwk_body_check_scan` |
| CLI option | `--ndf-config` | `--pwk-config` |
| CLI option | `--ndf-out-dir` | `--pwk-out-dir` |
| CLI option | `--ndf-no-evidence` | `--pwk-no-evidence` |
| CLI option | `--ndf-hud` | `--pwk-overlay` (Phase 0a で `hud` → `overlay`) |
| CLI option | `--ndf-drive-folder` | `--pwk-drive-folder` |
| pytest group | `parser.getgroup("ndf", ...)` | `parser.getgroup("pwk", ...)` |
| env var | `NDF_CONFIG` | `PWK_CONFIG` |
| 内部クラス | `NdfTestEntry` | `PwkTestEntry` |

**変更しないもの** (汎用名 / pytest 標準寄りのもの):

- `@pytest.mark.page_role` / `role` / `phase` / `priority` / `no_body_check` markers
  — 既に NDF 中立。プロジェクト固有名ではないのでそのまま
- `scenario.config.yaml` ファイル名 — 利用者が編集する設定ファイル
- `pyproject.toml` の `[tool.pytest.ini_options]` 設定群

採用理由:

- **all-in-one**: 1 ディレクトリ移動でシナリオテスト一式が完結。利用者プロジェクトのルートを
  汚染しない。複数のテストランタイムを別名 (`e2e/` / `regression/` 等) で同居させやすい
- **コピーで持ち込む**（git submodule / pip install パッケージは採用しない）
  - Skill 非依存・オフライン動作・後から手動編集可能
- **`pyproject.toml` を runtime 内に置く**ことで利用者の Python プロジェクトの依存と分離
  - `cd scenario-test && uv sync` で独立した `.venv` が得られる
  - `[tool.pytest.ini_options].testpaths = ["tests"]` を維持し、相対パス `./tests` がそのまま効く
- pytest plugin は **runtime/.venv 内に editable install** で entry-point を効かせる
  （現行 v0.4.0 と挙動完全一致）
- 利用者の通常の pytest ランナー（IDE 統合等）からも `cd scenario-test && pytest` で動く

#### 1.1 ランタイム実行とディレクトリ名のカスタマイズ

利用者は以下のいずれかで実行する:

```bash
# A) ランタイムディレクトリに入って実行
cd scenario-test && ./run.sh

# B) プロジェクトルートから相対パスで実行（推奨：CWD = ランタイム内に統一される）
./scenario-test/run.sh
```

ランチャは `$(dirname "${BASH_SOURCE[0]}")` で自身の位置を解決するため、どちらでも
CWD を `scenario-test/` 内に固定して pytest を起動する → `tests/` / `reports/` /
`scenario.config.yaml` がすべて相対パスで参照可能。

ランタイムディレクトリ名は **init 時の `--runtime-dir <name>` で変更可能**:

```bash
# default 名で init
scripts/init_project.sh /path/to/your-app
# → your-app/scenario-test/ 一式

# 任意名で init（既存ディレクトリと衝突回避 / 複数ランタイム共存）
scripts/init_project.sh /path/to/your-app --runtime-dir e2e
# → your-app/e2e/ 一式 (中の run.sh / pyproject.toml は同じ)

# 複数ランタイム共存例
scripts/init_project.sh /path/to/your-app --runtime-dir e2e-prod
scripts/init_project.sh /path/to/your-app --runtime-dir e2e-staging
# → your-app/e2e-prod/run.sh / your-app/e2e-staging/run.sh をそれぞれ独立に実行
```

ランチャ自身は **ディレクトリ名に依存しない**（自分の位置から相対で動く）ため、
リネームや複数共存に追加の設定は不要。env による上書きや `.scenario-test.env` のような
中央設定ファイルは all-in-one 構成では不要なので**削除**する。

### 2. ワンコマンドランチャ（run.sh / run.bat）

ランタイムディレクトリ内に置かれる `run.sh` は以下を順に実行する。

```bash
#!/usr/bin/env bash
set -euo pipefail

# このスクリプト自身が置かれているディレクトリ = ランタイムルート
RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1) uv の存在確認
command -v uv >/dev/null 2>&1 || {
  echo "uv が見つかりません。https://docs.astral.sh/uv/ を参照してインストールしてください。"
  exit 1
}

# 2) ランタイムの依存セットアップ（.venv が無ければ作る）
if [[ ! -d "$RUNTIME_DIR/.venv" ]]; then
  echo "[init] uv sync ($RUNTIME_DIR) ..."
  (cd "$RUNTIME_DIR" && uv sync)
  echo "[init] playwright install chromium ..."
  (cd "$RUNTIME_DIR" && uv run playwright install chromium)
fi

# 3) pytest 実行（CWD = ランタイム内、tests/ / reports/ / scenario.config.yaml を相対参照）
cd "$RUNTIME_DIR"
uv run pytest \
  --pwk-config="${PWK_CONFIG:-./scenario.config.yaml}" \
  "$@"
```

ポイント:

- **CWD をランタイム内に固定**するため、利用者プロジェクトのどこから呼んでも挙動が同じ
  （`./scenario-test/run.sh` でも `cd scenario-test && ./run.sh` でも結果一致）
- 初回のみ `uv sync` + `playwright install chromium` を自動実行
- 出力先 `reports/` はランタイム内に作られる（pytest plugin の `--ndf-out-dir` default を活かす）
- `$@` で追加引数（`-k name` / `-m page_role` / `--ndf-drive-folder=...` 等）を素通し
- `.bat` 版も同等の処理を Windows シェルで提供（`%~dp0` でスクリプト自身の位置を解決）

### 3. 初期化スクリプト（Skill 内 `scripts/init_project.sh`）

Skill 側に初期化スクリプトを 1 本だけ追加し、Claude Code セッション中で
`./init_project.sh /path/to/your-app [--runtime-dir <name>]` を呼ぶことで上記構造を作る。

```bash
#!/usr/bin/env bash
# 使い方: scripts/init_project.sh <PROJECT_ROOT> [--runtime-dir <name>] [--dry-run]
set -euo pipefail

PROJECT_ROOT=""
RUNTIME_DIR_NAME="scenario-test"   # default
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime-dir) RUNTIME_DIR_NAME="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -*) echo "unknown option: $1"; exit 1 ;;
    *)  PROJECT_ROOT="$1"; shift ;;
  esac
done
[[ -n "$PROJECT_ROOT" ]] || { echo "PROJECT_ROOT は必須です"; exit 1; }

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/$RUNTIME_DIR_NAME"

mkdir -p "$RUNTIME_DIR/tests"

# 1) ランタイム本体をコピー（.venv / .pytest_cache / reports は除外、tests は雛形を別途）
rsync -a \
  --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='reports' --exclude='tests' \
  "$SKILL_DIR/playwright_kit" "$SKILL_DIR/scripts" \
  "$SKILL_DIR/uv.lock" \
  "$RUNTIME_DIR/"

# 2) runtime 用 pyproject / ランチャ / .gitignore / README をコピー
cp "$SKILL_DIR/templates/pyproject.toml.runtime"     "$RUNTIME_DIR/pyproject.toml"
cp "$SKILL_DIR/templates/run.sh"                     "$RUNTIME_DIR/run.sh"
cp "$SKILL_DIR/templates/run.bat"                    "$RUNTIME_DIR/run.bat"
cp "$SKILL_DIR/templates/runtime-gitignore"          "$RUNTIME_DIR/.gitignore"
cp "$SKILL_DIR/templates/runtime-README.md"          "$RUNTIME_DIR/README.md"
chmod +x "$RUNTIME_DIR/run.sh"

# 3) 利用者編集物（既存があれば上書きしない）
[[ -f "$RUNTIME_DIR/scenario.config.yaml" ]] || \
  cp "$SKILL_DIR/templates/scenario.config.yaml" "$RUNTIME_DIR/scenario.config.yaml"
[[ -f "$RUNTIME_DIR/tests/conftest.py" ]] || \
  cp "$SKILL_DIR/templates/conftest.py.template" "$RUNTIME_DIR/tests/conftest.py"
# テスト雛形（初回のみコピー）
for tmpl in test_auth test_list test_form test_dashboard; do
  [[ -f "$RUNTIME_DIR/tests/${tmpl}.py" ]] || \
    cp "$SKILL_DIR/templates/${tmpl}.py.template" "$RUNTIME_DIR/tests/${tmpl}.py"
done

# 4) 初回 uv sync + playwright install
(cd "$RUNTIME_DIR" && uv sync && uv run playwright install chromium)

echo "[done] init 完了。次回以降は ./$RUNTIME_DIR_NAME/run.sh で実行できます。"
```

`init_project.bat` も同等処理（`xcopy` ベース、`--runtime-dir` 引数対応）で用意。

### 4. `pyproject.toml` の整形（コピー版）

現行 `pyproject.toml` から **Skill 開発時のみ必要な要素を排除**してコピー版を生成する。

- `[tool.pytest.ini_options].testpaths = ["tests"]` は **そのまま維持**
  → all-in-one 構成では `tests/` がランタイム直下にあるため相対パスがそのまま効く
- `[tool.pytest.ini_options].addopts = "-q"` 等の Skill 開発用設定は利用者向けに調整
  （`-q` は維持してよい）
- `name = "playwright-scenario-test"` を `name = "playwright_kit"` へ rename
  （Python パッケージ名と一致させ、利用者プロジェクトと混同しないため）
- `[project.optional-dependencies].dev` セクションを削除（Skill 開発時のみ必要）
- hatch wheel 設定 を `[tool.hatch.build.targets.wheel] packages = ["playwright_kit"]` に更新
- `[project.entry-points."pytest11"]` を `playwright-kit = "playwright_kit.pytest_plugin"` に更新
  → `uv sync` 後の editable install で plugin が自動 discover される

Skill リポジトリに **`templates/pyproject.toml.runtime`** として整形済みコピーを置き、
init 時はそれを `<runtime-dir>/pyproject.toml` として配置する（現行 `pyproject.toml` は Skill 開発用
として残す）。

### 5. SKILL.md の改訂

現行 SKILL.md は「Skill ディレクトリで uv sync」前提の手順だが、以下に改める:

```markdown
## クイックスタート

1) このディレクトリで `scripts/init_project.sh /path/to/your-app` を実行
   （Windows は `scripts\init_project.bat`）
   オプション: `--runtime-dir <name>` で配置先ディレクトリ名を変更可能
2) `your-app/scenario-test/scenario.config.yaml` の base_url / roles を編集
3) `your-app/scenario-test/tests/test_*.py` を書く（init 時に雛形が配置される）
4) `your-app/scenario-test/run.sh` を実行（プロジェクトルートからは `./scenario-test/run.sh`）

→ 以降このディレクトリ（Skill）は不要。`your-app/scenario-test/` 単体で完結する。
```

「Skill 単体で uv sync するモード」は **Skill 自身の開発・テスト用**として残し、
利用者向けセクションからは削除する。

## 実装タスク

### Phase 0a: ドメイン用語の整理（cryptic な略語を平易な英語に）

現状、モジュール名 / fixture / config / CLI に略語が無造作に混じっており、ドメイン
非専門家には意味が伝わらない。以下方針で整理する。

**整理対象と新名**:

| 現名 | 新名 | 理由 |
|---|---|---|
| `scenario_test/a11y.py` | `accessibility.py` | a11y は WCAG/axe ドメイン業界用語 |
| `scenario_test/cwv.py` | `web_vitals.py` | CWV (Core Web Vitals) を平易に |
| `scenario_test/hud.py` | `overlay.py` | HUD = Heads-Up Display は造語的 |
| config key `a11y:` | `accessibility:` | YAML を読む利用者向け |
| config key `cwv:` | `web_vitals:` | 同上 |
| fixture `pwk_a11y_scan` | `pwk_accessibility_scan` | コードリーダ向け |
| fixture `pwk_cwv_measure` | `pwk_web_vitals_measure` | 同上 |
| CLI `--pwk-hud` | `--pwk-overlay` | 同上 |

**保持するもの** (W3C / 業界標準の正式略号):

- `LCP` (Largest Contentful Paint)
- `CLS` (Cumulative Layout Shift)
- `TTFB` (Time To First Byte)
- `longest_task` (Long Tasks API)
- `HAR` (HTTP Archive、ファイル形式名)
- `axe-core` (a11y 検査ライブラリの実名)

これらは **データフィールド名・外部仕様名としてそのまま**使う。ただし初出箇所の docstring と
SKILL.md の用語集で**正式名称と意味を必ず併記**する。

**用語集の追加** (`SKILL.md` 上部に新規セクション):

```markdown
## 用語集

| 略語 / 用語 | 正式名 / 意味 |
|---|---|
| accessibility (旧 a11y) | Web アクセシビリティ。WCAG 準拠を axe-core で機械検査 |
| web vitals (旧 CWV) | Google が定義する「ユーザ体感パフォーマンス指標」群 |
| LCP | Largest Contentful Paint — 最大コンテンツ描画時間 (体感ロード速度) |
| CLS | Cumulative Layout Shift — 累積レイアウトずれ量 (視覚的安定性) |
| TTFB | Time To First Byte — 初バイト到達時間 (サーバ応答速さ) |
| longest_task | Long Tasks API で観測した最長タスクのミリ秒値 |
| HAR | HTTP Archive — ネットワーク通信ログのファイル形式 |
| trace | Playwright Trace — DOM スナップショット + 操作ログ + 動画の zip |
| overlay (旧 HUD) | テスト中に画面に重ねる赤丸カーソル + 字幕表示 |
| body_check | サーバが HTML 本文に出力した PHP/SSR エラー文字列の検出 |
```

**実装タスク**:

- [ ] `a11y.py` → `accessibility.py`、`cwv.py` → `web_vitals.py`、`hud.py` → `overlay.py`
      ファイルリネーム
- [ ] パッケージ内・テスト内・docs 内の import / 言及を一括置換
- [ ] config schema (`scenario.config.yaml`) の `a11y:` → `accessibility:`、`cwv:` → `web_vitals:`
      に変更
- [ ] config dataclass の属性名 (`Config.a11y`, `Config.cwv`) を `accessibility` / `web_vitals` に
- [ ] fixture `pwk_a11y_scan` / `pwk_cwv_measure` を `pwk_accessibility_scan` /
      `pwk_web_vitals_measure` に rename (Phase 0 の `pwk_*` 化と同タイミング)
- [ ] CLI `--pwk-hud` を `--pwk-overlay` に rename
- [ ] templates/scenario.config.yaml のキー名 / コメントを新名に更新
- [ ] SKILL.md 上部に「用語集」セクションを追加 (上記表)
- [ ] 各 fixture / モジュールの docstring に正式名 (Largest Contentful Paint 等) を併記
- [ ] docs/01-methodology.md などで「a11y」「CWV」と書いていた箇所を新名 + 略号併記に統一

### Phase 0: パッケージ・名前空間のリネーム (`scenario_test` → `playwright_kit`, `ndf_*` → `pwk_*`)
**Python パッケージ rename**:
- [ ] `plugins/ndf/skills/playwright-scenario-test/scenario_test/` を `playwright_kit/` にリネーム
- [ ] パッケージ内 `from scenario_test.X import Y` を `from playwright_kit.X import Y` へ全置換
- [ ] `pytest_plugin.py` の `pytest_plugins = ["scenario_test.fixtures.X", ...]` を
      `["playwright_kit.fixtures.X", ...]` へ更新
- [ ] `pyproject.toml` (Skill 開発用) の以下を更新:
    - `[project.entry-points."pytest11"] playwright-kit = "playwright_kit.pytest_plugin"`
    - `[tool.hatch.build.targets.wheel] packages = ["playwright_kit"]`
    - `name = "playwright_kit"`
- [ ] Skill 自身の `tests/` 配下の import 修正 (`from scenario_test.X` → `from playwright_kit.X`)

**fixture / CLI option / env / クラス名 rename** (`ndf_*` 系を全て `pwk_*` に):
- [ ] fixture 名: `ndf_config` / `ndf_role_<id>` / `ndf_evidence` / `ndf_out_dir` /
      `ndf_a11y_scan` / `ndf_cwv_measure` / `ndf_body_check_scan` を `pwk_*` に rename
- [ ] CLI option: `--ndf-config` / `--ndf-out-dir` / `--ndf-no-evidence` / `--ndf-hud` /
      `--ndf-drive-folder` を `--pwk-*` に rename
- [ ] pytest group 名: `parser.getgroup("ndf", ...)` → `parser.getgroup("pwk", ...)`
- [ ] env var: `NDF_CONFIG` を `PWK_CONFIG` に rename (`run.sh` のフォールバックも)
- [ ] 内部クラス: `NdfTestEntry` → `PwkTestEntry`
- [ ] templates 内のテスト雛形 (`test_auth.py.template` 等) で fixture 名を新名に更新
- [ ] `templates/conftest.py.template` 内の例コメントを新名に更新

**ドキュメント更新**:
- [ ] docs/ 配下の言及（`scenario_test/` / `ndf_*` 表記）を新名に更新
- [ ] SKILL.md の構造図 / fixture 表 / CLI option 表 / 全コード例を新名に更新
- [ ] `uv sync` で 147 件の既存テストが pass することを確認

### Phase 1: ランタイムコピー版の整形
- [ ] `templates/pyproject.toml.runtime` を新規作成（name=`playwright_kit`、entry-point /
      hatch wheel 設定を `playwright_kit` に、dev 用設定削除、
      `[tool.pytest.ini_options].testpaths = ["tests"]` 維持）
- [ ] `templates/runtime-gitignore` を新規作成（`.venv` / `__pycache__` / `reports/`）
- [ ] `templates/runtime-README.md` を新規作成（Skill 無し環境向け最低限の使い方）

### Phase 2: ランチャテンプレート
- [ ] `templates/run.sh` を新規作成（自身の位置から RUNTIME_DIR を解決 → uv sync → pytest）
- [ ] `templates/run.bat` を新規作成（Windows 同等処理、`%~dp0` でスクリプト位置解決）
- [ ] 両ランチャに `--help` を実装し追加引数の使い方を表示
- [ ] `cd <runtime-dir> && ./run.sh` でも `./<runtime-dir>/run.sh` でも同じ挙動になる確認

### Phase 3: 初期化スクリプト
- [ ] `scripts/init_project.sh` を新規作成（rsync ベース、all-in-one 配置）
- [ ] `scripts/init_project.bat` を新規作成（Windows / xcopy ベース）
- [ ] `--runtime-dir <name>` 引数でランタイムディレクトリ名を変更可能に
- [ ] init スクリプトの dry-run モード（`--dry-run` で予定差分のみ表示）
- [ ] init スクリプトの冪等性確認（既存 `scenario.config.yaml` / `tests/conftest.py` /
      `tests/test_*.py` を上書きしない）
- [ ] 同一プロジェクトに複数ランタイム名で init された場合も独立して動く確認
      （`scenario-test/` と `e2e/` を共存）

### Phase 4: SKILL.md 改訂
- [ ] クイックスタートを「init → run-scenario-test.sh」フローに書き換え
- [ ] 「Skill ディレクトリで uv sync する旧運用」節を削除（または開発者向け節に縮小）
- [ ] 「init 後は Skill 不要」を明示
- [ ] templates/ 配置物の説明を更新（runtime テンプレート群の追加）

### Phase 5: docs/ 更新
- [ ] `docs/06-pytest-playwright.md` の「実行方法」節を `run-scenario-test.sh` ベースに変更
- [ ] `docs/README.md` のディレクトリ図を更新（init 後構造）

### Phase 6: 検証
- [ ] **Skill が無い擬似環境**で動作検証:
    1. 一時ディレクトリに init 実行（default 名 + 任意名 `--runtime-dir e2e`）
    2. Skill ディレクトリ自体を別名にリネーム or 削除
    3. `./scenario-test/run.sh` および `./e2e/run.sh` が完走することを確認
- [ ] init 後に出力される `reports/` がランタイム内 (`scenario-test/reports/`) に作られることを確認
- [ ] templates/test_*.py.template 由来のテストで動画 / a11y / CWV / body_check が正常に出力されることを確認
- [ ] `--ndf-drive-folder=<id>` の Drive アップロードが scripts/_drive_auth.py /
      gdrive_upload_dir.py 経由で機能することを確認
- [ ] 複数ランタイム共存（`scenario-test/` + `e2e/`）が干渉せず動く確認
- [ ] Windows (WSL 以外) で `.bat` ランチャの動作確認

### Phase 7: バージョン更新
- [ ] `playwright-scenario-test/pyproject.toml` を `version = "0.5.0"` に
- [ ] `plugins/ndf/.claude-plugin/plugin.json` を `4.4.0` に
- [ ] `plugins/ndf/CLAUDE.md` の開発履歴に v4.4.0 / v0.5.0 の節を追加（破壊的変更を明記）

## 互換性方針

本 Skill は開発中につき**後方互換は考慮しない**。旧 API (`scenario_test` / `ndf_*` /
`--ndf-*` / `NDF_*` / 旧 Skill ディレクトリで `uv sync` する運用) は **完全に削除** する。
旧バージョンを利用していたコードがあれば手動で書き換える。

## リスクと対策

| リスク | 対策 |
|---|---|
| 利用者プロジェクトに `scenario-test/` を git 管理させると差分が肥大化 | `scenario-test/.venv` / `__pycache__` を gitignore。コアコードは数 KB 程度なので commit してよい設計（再現性のため逆に推奨）|
| Skill 更新時にコピー済みランタイムが取り残される | 開発中につき再 init で上書き運用。安定後にあらためて update 戦略を検討 |
| pytest plugin の entry-point 解決が editable install 依存 | 初回 `uv sync` 完了で editable install が効く構成（現行と同じ）。万一壊れたら `tests/conftest.py` に `pytest_plugins = ["playwright_kit.pytest_plugin"]` を追加するフォールバックを SKILL.md に明記 |
| `playwright install chromium` がオフライン環境で失敗 | init 時にエラーログを表示しつつ続行。利用者は事前に `PLAYWRIGHT_BROWSERS_PATH` を共有キャッシュへ向ける運用 |
| Windows `.bat` ランチャの差異 | WSL 推奨を SKILL.md に明記。最低限 `.bat` で uv 経由 pytest が回る程度に留める |

## 完了の定義

- [ ] **Skill ディレクトリを `mv` で隠した状態**でも `./scenario-test/run.sh` が完走する
- [ ] init 後、利用者プロジェクトには **`scenario-test/` ディレクトリが 1 つ追加されるだけ**
      で他のルートファイルが増えない（all-in-one が成立）
- [ ] `scenario-test/` の中に `playwright_kit/` / `scripts/` / `tests/` / `reports/` /
      `scenario.config.yaml` / `run.sh` / `run.bat` / `pyproject.toml` / `uv.lock` が揃っている
- [ ] `--runtime-dir e2e` で init した場合、`your-app/e2e/` 配下に同じ構造が作られ
      `./e2e/run.sh` で実行できる
- [ ] SKILL.md / docs/ が新フローに沿って更新されている
- [ ] バージョンが `v0.5.0` / プラグイン `v4.4.0` に上がっている
- [ ] 既存テストスイート（147 件）が引き続き pass する

## 参考

- 現行構造: `plugins/ndf/skills/playwright-scenario-test/`
- 関連 Plan: PLAN17 (v0.3.0 pure pytest 化) / PLAN18 (v0.4.0 body_check 復活)
- pytest entry-point: `pyproject.toml` `[project.entry-points."pytest11"]`
- uv project: https://docs.astral.sh/uv/concepts/projects/
