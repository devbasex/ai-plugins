# scenario-test ランタイム

このディレクトリは利用者プロジェクトに埋め込まれた **playwright_kit** ベースの
Web シナリオ E2E テストランタイムです。Skill の有無に関係なく、このディレクトリ
**単体で動作**します。

## クイックスタート

```bash
# 1) このディレクトリ (scenario-test/) で実行
./run.sh

# 2) プロジェクトルートから実行
./scenario-test/run.sh

# Windows
run.bat
```

初回は `uv sync` と `playwright install chromium` が自動実行されます (数分)。
2 回目以降はキャッシュ済 `.venv` を使うので即起動します。

## ディレクトリ構成

```
scenario-test/
├── playwright_kit/         ← Python パッケージ本体
├── scripts/                ← 補助 CLI (classify_page_role / accessibility / web_vitals / drive 等)
├── tests/                  ← 利用者が書く pytest テスト
│   ├── conftest.py
│   └── test_*.py
├── reports/                ← 実行結果 (gitignore 推奨)
├── scenario.config.yaml    ← base_url / roles / accessibility / web_vitals 等
├── run.sh                  ← Linux/Mac 用ワンコマンドランチャ
├── run.bat                 ← Windows 用ワンコマンドランチャ
├── pyproject.toml          ← runtime 用 (testpaths=tests)
├── uv.lock                 ← 依存 lockfile (commit して再現性を確保)
└── README.md               ← 本ファイル
```

## 設定

`scenario.config.yaml` で以下を編集します。

- `target.base_url`: テスト対象 URL
- `target.basic_auth`: Basic 認証 (省略可)
- `roles`: ログインロール定義 (id / login.path / login.fields)
- `playwright`: viewport / step delay / overlay / trace / HAR mode
- `accessibility`: axe-core 自動スキャン (page_role による起動条件)
- `web_vitals`: Core Web Vitals 自動計測 (page_role による起動条件)
- `body_check`: PHP / SSR エラー文字列検出 (default 有効)

## テストの書き方

`tests/test_*.py` で以下のように pytest-playwright スタイルで書きます。

```python
import pytest
from playwright.sync_api import Page, expect


@pytest.mark.page_role("dashboard")
@pytest.mark.role("admin")
def test_admin_dashboard(page: Page, pwk_role_admin, pwk_config):
    page.goto(f"{pwk_config.base_url}/admin", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="ダッシュボード")).to_be_visible()
```

`@pytest.mark.page_role(...)` を付与すると accessibility (axe-core) と
web vitals (LCP/CLS/TTFB) が **autouse で自動実行** されます。

## 主要 fixture

- `pwk_config`: `scenario.config.yaml` をロードした `Config` オブジェクト
- `pwk_role_<id>`: 定義済 role でログイン済の `storage_state` を inject
- `pwk_evidence`: HAR / trace / console / pageerror collector
- `pwk_accessibility_scan()`: 明示的に axe-core を 1 回実行
- `pwk_web_vitals_measure()`: 明示的に Web Vitals を 1 回計測
- `pwk_body_check_scan()`: 明示的に body_check を 1 回実行

## 主要 CLI option

```bash
./run.sh \
  --pwk-config=./scenario.config.yaml \
  --pwk-out-dir=./reports/manual-run/ \
  --pwk-overlay \
  --pwk-drive-folder=<DRIVE_FOLDER_ID> \
  -k test_admin -m page_role
```

- `--pwk-config <path>`: 設定ファイル (env `PWK_CONFIG` でも上書き可)
- `--pwk-out-dir <path>`: 成果物出力先 (default: `./reports/<run-id>/`)
- `--pwk-no-evidence`: HAR / trace / 動画を OFF
- `--pwk-har-mode {minimal,full,none}`: HAR 録画モード
- `--pwk-overlay`: 動画に赤丸カーソル + 字幕を焼き込む
- `--pwk-drive-folder <id>`: 終了後に成果物を Google Drive にアップロード

Drive 連携は optional dependency です。`google-auth` skill はどのランタイムの配布物にも
同梱していないため、`--pwk-drive-folder` や Drive 系スクリプトを使う場合はリポジトリ
[devbasex/ai-plugins](https://github.com/devbasex/ai-plugins) を clone し、事前に
`GOOGLE_AUTH_SCRIPTS` をその clone 先の `google-auth/scripts` へ設定してから
Drive extra を同期してください。

```bash
# <ai-plugins のパス> を clone 先の実パスに置き換える
export GOOGLE_AUTH_SCRIPTS=<ai-plugins のパス>/plugins/ndf-shared/skills/google-auth/scripts
uv sync --extra drive
```

## 補助スクリプト

`scripts/` 配下に CLI ヘルパが置かれています。

- `scripts/classify_page_role.py <url>`: accessibility tree から page role を推定
- `scripts/run_a11y_scan.py <url>`: axe-core で違反を検出
- `scripts/check_cwv.py <url>`: Core Web Vitals を計測
- `scripts/upload_evidence.py <path>`: trace / HAR / 動画を Drive にアップ

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| `uv が見つかりません` | https://docs.astral.sh/uv/ を参照してインストール |
| `playwright install chromium` が失敗 | オフライン環境なら `PLAYWRIGHT_BROWSERS_PATH` を共有キャッシュへ向ける |
| pytest plugin が discover されない | `cd scenario-test && uv sync` を再実行。または `tests/conftest.py` に `pytest_plugins = ["playwright_kit.pytest_plugin"]` を追加 |
| 成果物 `reports/` の場所が想定と違う | `--pwk-out-dir` で明示的に指定する |

## ドキュメント

詳細な使い方は (このディレクトリの元になった) Skill ディレクトリの
`SKILL.md` / `docs/` を参照してください。Skill が利用者環境にない場合は
ソース管理リポジトリで参照可能です。
