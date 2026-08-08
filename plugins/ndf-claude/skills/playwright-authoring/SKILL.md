---
name: playwright-authoring
description: "Write Playwright E2E test scripts and run them with video / trace evidence, or check a page over browser MCP. Use when writing or running E2E tests, doing a browser smoke check, or connecting to Chrome over CDP. Triggers: 'playwright codegen', 'axe-core', 'connectOverCDP', 'ブラウザ動作確認'"
argument-hint: "[url]"
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash
  - mcp__playwright__browser_navigate
  - mcp__playwright__browser_snapshot
  - mcp__playwright__browser_click
  - mcp__playwright__browser_fill_form
  - mcp__playwright__browser_take_screenshot
  - mcp__playwright__browser_type
  - mcp__playwright__browser_evaluate
  - mcp__playwright__browser_console_messages
  - mcp__playwright__browser_wait_for
  - mcp__playwright__browser_tabs
  - mcp__playwright__browser_navigate_back
  - mcp__playwright__browser_close
  - mcp__playwright__browser_resize
  - mcp__playwright__browser_handle_dialog
  - mcp__playwright__browser_press_key
  - mcp__playwright__browser_hover
  - mcp__playwright__browser_select_option
  - mcp__playwright__browser_drag
  - mcp__playwright__browser_network_requests
  - mcp__playwright__browser_file_upload
  - mcp__playwright__browser_install
  - mcp__chrome-devtools__navigate_page
  - mcp__chrome-devtools__take_snapshot
  - mcp__chrome-devtools__click
  - mcp__chrome-devtools__fill_form
  - mcp__chrome-devtools__take_screenshot
  - mcp__chrome-devtools__type
  - mcp__chrome-devtools__evaluate_script
  - mcp__chrome-devtools__list_console_messages
  - mcp__chrome-devtools__wait_for
  - mcp__chrome-devtools__list_pages
  - mcp__chrome-devtools__new_page
  - mcp__chrome-devtools__select_page
  - mcp__chrome-devtools__close_page
  - mcp__chrome-devtools__navigate_page_history
  - mcp__chrome-devtools__resize_page
  - mcp__chrome-devtools__handle_dialog
  - mcp__chrome-devtools__hover
  - mcp__chrome-devtools__drag
  - mcp__chrome-devtools__list_network_requests
  - mcp__chrome-devtools__get_network_request
  - mcp__chrome-devtools__upload_file
  - mcp__chrome-devtools__emulate_network
  - mcp__chrome-devtools__emulate_cpu
  - mcp__chrome-devtools__performance_start_trace
  - mcp__chrome-devtools__performance_stop_trace
  - mcp__chrome-devtools__performance_analyze_insight
---

# Playwright スクリプト作成と実行

再現可能なテストスクリプトを作成し、レビューを経てから実行してエビデンスを収集する。
スクリプトを介さない単発のブラウザ動作確認は「MCP でのブラウザ動作確認」節で行う。

## 大原則

1. **テストスクリプトを実装してからテストを実施する。** レビューを通るまで実行フェーズに進まない
2. **エビデンス動画はデフォルト ON。** 明示的にスキップする場合のみ `--pwk-no-video` を指定する
3. **`scenario-test/` は ndf plugin 非依存。** プラグイン未インストール環境でも単体で動く

## 前提条件

- テスト計画が完了していること (`/ndf:playwright-planning`)
- `init_project.sh` でプロジェクトが初期化済みであること (`/ndf:playwright-kit-ops`)
- `scenario.config.yaml` が設定済みであること

## ワークフロー

```
[A] テスト計画の確認 (チェックリスト / page role / テスト技法)
      ▼
[B] テンプレート選択      tests/ 配下の test_*.py を起点にする
      ▼
[C] テストコード実装      codegen で記録 → expect() ベースの assertion を追加
      ▼
[D] 再現可能性レビュー    下記チェックリストを全項目確認
      ▼
[E] テスト実行 + エビデンス収集   ./scenario-test/run.sh
      ▼
[F] レポートと証跡へ → /ndf:playwright-evidence
```

## テストスクリプト作成

### テンプレートを起点にする

`init_project.sh` で以下のテンプレートが `tests/` に配置済み。プロジェクト固有の URL やセレクタを書き換えて使う。

| テンプレート | page role | 内容 |
|---|---|---|
| `test_auth.py` | auth | ログイン / ログアウトフロー |
| `test_list.py` | list | 一覧ページネーション / ソート |
| `test_form.py` | form | 入力 → 送信 → 結果検証 |
| `test_dashboard.py` | dashboard | KPI / リンク遷移 |

→ コード例は `playwright-kit-ops/templates/test_*.py.template` を参照。

### playwright codegen での操作記録

`uv run playwright codegen <URL>` で操作を記録し、生成コードをテスト関数にコピーする。
コピー後に `@pytest.mark.page_role()`, `@pytest.mark.role()`, `expect()` assertion, `pwk_config.base_url` を追加する。

### fixture / marker

完全な一覧は `playwright_kit/pytest_plugin.py` の `_PWK_MARKERS` 定義と `playwright_kit/fixtures/` 配下を参照。

- 主な fixture: `pwk_config`, `pwk_role_<id>`, `pwk_evidence`, `pwk_accessibility_scan()`, `pwk_web_vitals_measure()`
- 主な marker: `@pytest.mark.page_role()`, `@pytest.mark.role()`, `@pytest.mark.phase()`, `@pytest.mark.priority()`, `@pytest.mark.no_body_check`

overlay API (`set_caption`, `flash_click`, `hide_cursor`) の使用例は `playwright_kit/overlay.py` を参照。

### 再現可能性レビューチェックリスト

スクリプト完成後、以下を全項目確認してからテスト実行に進む。

- [ ] **再現可能性**: 同じ環境で同じ結果が得られるか (ランダム値・タイムスタンプに依存していないか)
- [ ] **テストデータ独立性**: 外部の状態に依存せず、テスト単体で成立するか
- [ ] **marker 付与**: `@pytest.mark.page_role()` が全テスト関数に付与されているか
- [ ] **role marker**: 認証が必要なテストに `@pytest.mark.role()` + `pwk_role_<id>` fixture があるか
- [ ] **assertion 網羅性**: 正常系 + 少なくとも 1 つの異常系 (バリデーション等) が含まれるか
- [ ] **URL 構築**: ハードコードされた URL ではなく `pwk_config.base_url` を使用しているか
- [ ] **wait 戦略**: `wait_until="domcontentloaded"` 等の明示的な待機指定があるか
- [ ] **ndf plugin 非依存**: `scenario-test/` ディレクトリ単体で実行可能か

## テスト実行

```bash
./scenario-test/run.sh                            # 全テスト (動画 ON)
./scenario-test/run.sh -k test_admin              # フィルタ
./scenario-test/run.sh --pwk-overlay              # 字幕 + カーソル付き動画
./scenario-test/run.sh --pwk-no-video             # 動画のみ OFF
./scenario-test/run.sh --pwk-no-evidence          # 全エビデンス OFF (HAR/trace/動画)
```

### CLI options

| option | 役割 |
|---|---|
| `--pwk-config <path>` | `scenario.config.yaml` のパス |
| `--pwk-out-dir <path>` | 成果物出力先 (default: `reports/<run-id>/`) |
| `--pwk-no-video` | 動画収集を OFF (デフォルトは ON) |
| `--pwk-no-evidence` | HAR / trace / video の収集を全て OFF |
| `--pwk-har-mode {minimal,full,none}` | HAR 録画モード (default: minimal) |
| `--pwk-overlay` | overlay (赤丸カーソル + 字幕) を ON |
| `--pwk-drive-folder=<ID>` | 実行後に Drive へ自動アップロード (→ `/ndf:playwright-evidence`) |

### エビデンス種別と成果物

| 種別 | デフォルト | OFF フラグ | 説明 |
|---|---|---|---|
| video | **ON** | `--pwk-no-video` | 全テストの動画を取得 |
| trace | ON (retain-on-failure) | `--pwk-no-evidence` | Playwright Trace (DOM + 操作ログ) |
| HAR | ON (minimal) | `--pwk-har-mode none` | ネットワーク通信ログ |
| screenshot | ON (only-on-failure) | `--pwk-no-evidence` | 失敗時スクリーンショット |

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

### 品質計測

いずれも `scenario.config.yaml` で制御する。設定例は `playwright-kit-ops/templates/scenario.config.yaml` を参照。

| 計測 | 発動条件 | 設定セクション |
|---|---|---|
| accessibility (axe-core) | `@pytest.mark.page_role` が auto_roles にマッチ | `accessibility:` |
| Core Web Vitals (LCP/CLS/TTFB/longest_task) | 同上 | `web_vitals:` |
| body_check (PHP/SSR エラー検出) | 常時有効。`@pytest.mark.no_body_check` で opt-out | `body_check:` |

body_check は `page.on("response")` で全 HTML レスポンスを監視し、`Fatal error` 等を検出する。

## ブラウザ接続

| モード | scenario.config.yaml | 接続先 | 用途 |
|---|---|---|---|
| `local` | `browser.mode: local` | コンテナ内 Chromium | CI / ヘッドレス実行 (デフォルト) |
| `cdp-remote` | `browser.mode: cdp-remote` | リモート Chrome (CDP) | GUI 操作・ログイン済み Session 再利用 |

```yaml
browser:
  # local: playwright install chromium でインストールしたローカルブラウザ (デフォルト)
  # cdp-remote: Chrome DevTools Protocol 経由でリモートブラウザに接続
  mode: local
  # cdp-remote 時のみ有効
  cdp_endpoint: ${CDP_ENDPOINT:-http://localhost:9222}
```

WSL2 / macOS / Linux ホストの Chrome へ CDP 接続する手順、`scripts/start-host-chrome.sh` によるホスト
Chrome の起動、`conftest.py` への統合、ネットワーク到達性の確保、トラブルシュートは
[references/browser-connection.md](references/browser-connection.md) を参照。

## MCP でのブラウザ動作確認

テストスクリプトを書かずに、現在のブランチの実装をブラウザで確認する手順。Playwright MCP または
Chrome DevTools MCP の利用可能な方を自動選択する。どちらも使えない環境では手動確認手順を案内する。

```
/ndf:playwright-authoring                       # 現在のブランチの実装を確認
/ndf:playwright-authoring http://localhost:8080 # 特定 URL を確認
```

| MCP | 特徴 | 前提 |
|---|---|---|
| Playwright MCP | 自動でブラウザを起動。Chromium/Firefox/WebKit 対応。利用可能なら第一選択 | Playwright インストール |
| Chrome DevTools MCP | 既に開いている Chrome を操作。DevTools 統合でパフォーマンス分析可能 | Chrome をデバッグモードで起動 (`--remote-debugging-port=9222`) |

### 手順

1. **アプリケーション起動確認**: `docker compose ps` や `curl -fsS http://localhost:<port>/health` で確認する。起動していなければ起動手順を案内する
2. **アクセスと認証**: 指定 URL (または `/`) にアクセスし、必要ならログインする。資格情報はプロジェクト固有で、`.env.example` / README から確認し機密情報として扱う
3. **機能画面への遷移**: 実装された機能に応じた画面へ遷移する
4. **動作確認**: フォーム入力・ボタンクリック・データ表示・コンソールエラー・ネットワークリクエストを確認する。スクリーンショットは明示的に指示されたときのみ取得する
5. **結果報告**: 実施項目 / 確認事項 (コンソールエラー・ネットワークエラー・期待結果との一致) / 気になる点 を Markdown で報告する

継続的に回すべき確認は、この手順で得た操作列をテストスクリプトへ落とし込む (本 Skill の前半)。

## ndf plugin 非依存

`init_project.sh` で埋め込まれた `scenario-test/` は `playwright_kit/` パッケージ本体を含み、
`pyproject.toml` で pytest11 entry-point を定義し、`run.sh` でワンコマンド実行できる。
→ ndf plugin 未インストール環境でも `./scenario-test/run.sh` で動作する。

## 関連 Skill

- `/ndf:playwright-planning` — テスト計画 (前段)
- `/ndf:playwright-evidence` — 証跡とレポート (後段)
- `/ndf:playwright-kit-ops` — 実行環境の運用 (init_project / codegen / スキャン)
- `/ndf:docker-container-access` — Docker コンテナアクセス一般
- `/ndf:review --branch` — 変更差分のコードレビュー
- `/ndf:pr-tests` — PR Test Plan の自動実行

> `playwright-planning` / `playwright-evidence` / `playwright-kit-ops` は Codex 公開セットにのみ同梱される。
> Claude Code / Kiro CLI のプラグインには含まれないため、必要な場合はリポジトリ
> [devbasex/ai-plugins](https://github.com/devbasex/ai-plugins) の `plugins/ndf-shared/skills/` を参照する。
