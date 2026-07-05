# 06. pytest-playwright リファレンス

`pytest-playwright` は Playwright 公式の pytest plugin。本 Skill はこの plugin の上に NDF 固有の fixture / marker / hook を追加する形で構築されている。
本書では「pytest-playwright が提供する物」と「NDF が追加する物」を整理し、利用者が両者を混乱なく組み合わせられるようにする。

## 1. インストールと前提

```bash
uv add pytest-playwright pytest-xdist        # main 依存に追加 (本 Skill では同梱済)
playwright install chromium                  # ブラウザバイナリ
```

`pytest-playwright>=0.5` が必要。CLAUDE.md / pyproject.toml の依存と一致。

## 2. 標準 fixture (pytest-playwright 提供)

| fixture | scope | 用途 |
|---|---|---|
| `playwright` | session | `Playwright` インスタンス |
| `browser_type` | session | 現在の `BrowserType` (chromium/firefox/webkit) |
| `browser` | session | session 中で 1 回だけ launch される `Browser` |
| `browser_name` / `browser_channel` | session | 文字列 (条件分岐用) |
| `is_chromium` / `is_firefox` / `is_webkit` | session | 真偽値 |
| `context` | function | 各 test 専用の `BrowserContext` (毎回新規) |
| `page` | function | `context.new_page()` 済の `Page` |
| `new_context` | function | test 内で別 context を追加で作る関数 |
| `browser_type_launch_args` | session | `BrowserType.launch()` の引数 (override 可能) |
| `browser_context_args` | function | `Browser.new_context()` の引数 (override 可能) |
| `connect_options` | session | 既存ブラウザに WS で接続する場合の dict |

**重要**: `context` / `page` は **function scope** なので test ごとに完全分離される。テスト間で状態漏れが起きない代わりに、認証は毎回 storage_state を inject する必要がある (NDF では `pwk_role_<id>` fixture が担当)。

## 3. CLI options (pytest-playwright)

| option | デフォルト | 用途 |
|---|---|---|
| `--browser <name>` | chromium | 複数指定可: `--browser firefox --browser webkit` |
| `--browser-channel <name>` | (なし) | `chrome` / `msedge` 等 |
| `--headed` | off | ヘッドフルで実行 (debug) |
| `--slowmo <ms>` | 0 | 各操作を ms 単位で遅延 (debug) |
| `--device <name>` | (なし) | `iPhone 13` 等のデバイスエミュ |
| `--output <dir>` | `test-results` | 全 artifact の出力先 |
| `--tracing <mode>` | off | `on` / `off` / `retain-on-failure` |
| `--video <mode>` | off | 同上 |
| `--screenshot <mode>` | off | `on` / `off` / `only-on-failure` |
| `--full-page-screenshot` | off | screenshot 有効時に全 page を撮る |

`pytest.ini` / `pyproject.toml` で固定化できる:

```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = "--headed --browser firefox --tracing retain-on-failure"
```

## 4. NDF 提供の追加 fixture / marker / option

| 種別 | 名前 | 由来 | scope | 用途 |
|---|---|---|---|---|
| fixture | `pwk_config` | NDF | session | `scenario.config.yaml` をロード |
| fixture | `pwk_role_<id>` (動的生成) | NDF | function | 該当 role で login 済 storage_state を context に inject |
| fixture | `pwk_evidence` | NDF | function | HAR / trace / console / pageerror の集中管理 |
| fixture | `pwk_accessibility_scan` | NDF | function | 任意のタイミングで axe-core を 1 回実行 |
| fixture | `pwk_web_vitals_measure` | NDF | function | 任意のタイミングで Core Web Vitals を 1 回計測 |
| fixture | `browser_context_args` (override) | NDF | function | HAR `record_har_path` を inject (上書きしないこと) |
| marker | `@pytest.mark.page_role(...)` | NDF | — | accessibility / web_vitals autouse の判定 (auto_roles 設定に従う) |
| marker | `@pytest.mark.role(role_id)` | NDF | — | report.md 集計用 (login は `pwk_role_<id>` 側で行う) |
| marker | `@pytest.mark.phase(num)` / `priority(level)` | NDF | — | report.md ソート / フェーズ集計 |
| CLI | `--pwk-config <path>` | NDF | — | `scenario.config.yaml` パス |
| CLI | `--pwk-out-dir <path>` | NDF | — | 成果物出力先 (default: `reports/<run-id>/`) |
| CLI | `--pwk-no-evidence` | NDF | — | HAR / trace / video の収集を OFF |
| CLI | `--pwk-overlay` | NDF | — | overlay (旧名 HUD) を inject (録画用) |
| CLI | `--pwk-drive-folder <id>` | NDF | — | session 終了時に Drive へアップ |

## 5. fixture override パターン

### 5.1 全テスト共通の context オプション

利用者プロジェクトの `tests/conftest.py` で:

```python
import pytest

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 1440, "height": 900},
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
    }
```

NDF の `browser_context_args` (HAR `record_har_path` を注入する関数 scope) を併用する場合、**NDF の override が優先される**。利用者は `viewport` / `locale` 等の追加情報のみを混ぜる形にする。

### 5.2 1 件のテストだけ context を変える

```python
@pytest.mark.browser_context_args(timezone_id="Europe/Berlin", locale="en-GB")
def test_german_user(page):
    assert page.evaluate("window.navigator.languages") == ["de-DE"]
```

`browser_context_args` marker は pytest-playwright 標準。NDF marker と独立して動く。

### 5.3 複数 role を session 内で再利用

NDF では `pwk_role_<id>` fixture が **session 内で login を 1 回だけ実行** し storage_state を cache する。同じ role を使う test は何件あっても再ログインしない。
利用者プロジェクト側で同様の最適化を自前で書く必要はない。

## 6. 並列実行 (`pytest-xdist`)

```bash
pytest -n auto                           # CPU 数に応じて自動
pytest -n 4                              # worker 4 並列
pytest -n 4 --dist=loadgroup             # @pytest.mark.xdist_group で同じ context を寄せる
```

注意点:

- `pwk_role_<id>` の storage_state cache は **worker 内** で共有される。worker をまたぐとログインが N 回走る (ヘビーな環境では `auth.json` を pre-build しておく方法を検討)
- `pwk_evidence` の出力先 (`reports/<run-id>/<test-id>/`) は test 名から sub-dir を切るため worker 競合は起きない
- `pytest_terminal_summary` で集約される `report.md` は xdist でも 1 ファイルで出る

## 7. 複数ブラウザ / parametrize

```bash
pytest --browser chromium --browser firefox --browser webkit
```

各 test は browser 数だけ実行される (test ID の末尾に `[chromium]` 等が付く)。
特定 test のみ skip / only したい場合:

```python
def test_only_in_chromium(page, browser_name):
    if browser_name != "chromium":
        pytest.skip("chromium only")
    ...
```

## 8. Visual Regression

```python
expect(page).to_have_screenshot("dashboard.png", max_diff_pixel_ratio=0.01)
```

- 初回実行で baseline が生成される (`<test>__<browser>.png`)
- `--update-snapshots` で baseline を更新
- 差分は `test-results/` に PNG で保存される

NDF では現状 visual regression は autouse 化していない。必要な test に手動で `expect(...).to_have_screenshot()` を書く。

## 9. 実行方法 (v0.5.0 自己完結ランタイム)

v0.5.0 から、利用者プロジェクトに `init_project.sh` で埋め込んだ `scenario-test/`
ディレクトリ単体で実行する形が推奨。Skill ディレクトリの存在に依存しない。

```bash
# 1) 初期化 (Skill ディレクトリ内で 1 度だけ)
# Claude Code/Kiro: <claude-plugin-root>/skills/playwright-kit-ops
# Codex: <codex-plugin-root>/skills-codex/playwright-kit-ops
cd <ndf-plugin-skill-dir>/playwright-kit-ops
./scripts/init_project.sh /path/to/your-app
# → /path/to/your-app/scenario-test/ 一式が作成される
#   (--runtime-dir e2e で配置先名カスタマイズ可)

# 2) 通常の実行
cd /path/to/your-app
./scenario-test/run.sh                                # 全テスト
./scenario-test/run.sh -k test_admin                  # nodeid フィルタ
./scenario-test/run.sh -m "page_role"                 # marker フィルタ
./scenario-test/run.sh --pwk-overlay                  # 動画に赤丸カーソル + 字幕
./scenario-test/run.sh --pwk-drive-folder=<ID>        # Drive 自動アップロード
./scenario-test/run.sh -n 4                           # 並列実行
```

ランチャ (`run.sh` / `run.bat`) は自身の位置から RUNTIME_DIR を解決し、CWD を
ランタイム内 (`scenario-test/`) に固定して `uv run pytest` を起動する。利用者
プロジェクトのどこから呼んでも挙動は同じ。

`scenario-test/` の中身:

```
scenario-test/
├── playwright_kit/        ← Python パッケージ本体
├── scripts/               ← 補助 CLI
├── tests/                 ← 利用者の pytest テスト
├── reports/               ← 実行結果 (.gitignore 推奨)
├── scenario.config.yaml   ← 利用者の設定
├── pyproject.toml         ← runtime 用 (testpaths=["tests"])
├── uv.lock                ← 再現性のため commit 推奨
├── run.sh / run.bat       ← ランチャ
└── README.md              ← 最低限の使い方
```

直接 pytest を呼ぶ場合 (CI / IDE 統合):

```bash
cd /path/to/your-app/scenario-test
uv sync                                # 初回のみ
uv run playwright install chromium     # 初回のみ
uv run pytest --pwk-config=./scenario.config.yaml
```

### 旧運用との互換性

旧 v0.4.0 までは Skill ディレクトリで `uv sync` する運用だったが、v0.5.0 では
非推奨。理由は (1) Skill が消えるとテストが動かない (CI / 別マシン破綻)、
(2) Skill 側の uv プロジェクトが利用者プロジェクトの依存と分離されておらず
再現性が低い。本 Skill は開発中につき後方互換は重視せず、旧運用は廃止する。

## 10. デバッグ

| 目的 | 方法 |
|---|---|
| ヘッドフルで動かす | `pytest --headed` |
| ステップバイステップ | `pytest --headed --slowmo 500` |
| trace を必ず採る | `pytest --tracing on` (本 Skill は pwk_evidence でも採取) |
| trace viewer で再生 | `playwright show-trace test-results/.../trace.zip` |
| インスペクタで一時停止 | `page.pause()` を test 内に挿入 + `--headed` |

`page.pause()` は **テストを書く時の手探り** に有効。完成したテストには残さない。

## 11. NDF 移行時の注意

| pytest-playwright 標準 | NDF 上での扱い |
|---|---|
| `--video on` | NDF では `pwk_evidence` が webm を採り `video.py` で mp4 化する。`--video` は併用しないこと (重複) |
| `--tracing on` | 同上。`pwk_evidence` 側で取るので NDF 経由が推奨 |
| `--screenshot only-on-failure` | NDF 標準では設定していない。利用者プロジェクトで必要なら有効化可 |
| `browser_context_args` override | NDF 側 override をベースに、利用者は `viewport` 等の追加のみ |

## 12. autouse fixture の落とし穴

NDF の accessibility / web_vitals autouse fixture は **`page` を直接 fixture 引数に取らない**。理由:

- pytest-playwright が `page` を function scope で要求するため、autouse fixture が `page` を要求すると **全 test が browser parametrize される**
- 結果として「page_role marker 無し」の純 unit test まで browser を立ち上げてしまう

NDF では `request.fixturenames` ガード + `request.getfixturevalue("page")` の遅延取得でこれを回避している。利用者プロジェクトで autouse fixture を書く場合も同じパターンを使うこと。

## 参考文献

- pytest-playwright Test Runners, https://playwright.dev/python/docs/test-runners
- pytest-playwright API, https://playwright.dev/python/docs/api/class-fixtures
- pytest-xdist, https://pytest-xdist.readthedocs.io/
- Playwright Trace Viewer, https://playwright.dev/python/docs/trace-viewer
- Visual Comparisons, https://playwright.dev/python/docs/test-snapshots
