# Playwright スキル再構成 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PR #18 の 6 スキル分割を、大原則 (再現可能テストスクリプト先行 / plugin 非依存 / エビデンス動画デフォルト ON) に基づいて 5 スキル + orchestrator に再構成する。

**Architecture:** 旧 evidence/overlay/quality の 3 スキルを `playwright-execution` に統合し、新規 `playwright-script-creation` を追加。`pytest_plugin.py` に `--pwk-no-video` オプションを追加し動画デフォルト ON を実現。`run.sh` にも `--video=on` フォールバックを追加。

**Tech Stack:** Python (pytest / pytest-playwright), Bash, YAML, Markdown (SKILL.md)

**Spec:** `docs/superpowers/specs/2026-05-25-playwright-skill-restructure-design.md`

---

## ファイル構成

### 新規作成

| ファイル | 責務 |
|---|---|
| `plugins/ndf/skills/playwright-script-creation/SKILL.md` | Phase 2 スキル: テストスクリプト作成ガイド |
| `plugins/ndf/skills/playwright-execution/SKILL.md` | Phase 3 スキル: テスト実行+エビデンス収集 (3 スキル統合) |
| `plugins/ndf/skills/playwright-kit-ops/tests/test_video_default.py` | `--pwk-no-video` オプションと動画デフォルト ON のテスト |

### 変更

| ファイル | 変更内容 |
|---|---|
| `plugins/ndf/skills/playwright-kit-ops/playwright_kit/pytest_plugin.py` | `--pwk-no-video` CLI オプション追加 + `pytest_configure` で動画デフォルト ON |
| `plugins/ndf/skills/playwright-kit-ops/templates/run.sh` | `--video=on` フォールバック追加 |
| `plugins/ndf/skills/playwright-kit-ops/templates/conftest.py.template` | テストスクリプト存在チェック追加 |
| `plugins/ndf/skills/playwright-test-planning/SKILL.md` | 次フェーズ導線追加 |
| `plugins/ndf/skills/playwright-report/SKILL.md` | Drive 共有セクション削除 |
| `plugins/ndf/skills/playwright-scenario-test/SKILL.md` | 5 スキル案内テーブル更新 + 大原則記載 |
| `plugins/ndf/.claude-plugin/plugin.json` | skills 配列更新 (3 削除 + 2 追加) |

### 削除

| ディレクトリ | 理由 |
|---|---|
| `plugins/ndf/skills/playwright-evidence/` | `playwright-execution` に統合 |
| `plugins/ndf/skills/playwright-overlay/` | `playwright-execution` に統合 |
| `plugins/ndf/skills/playwright-quality/` | `playwright-execution` に統合 |

---

## Task 1: `--pwk-no-video` CLI オプション追加 + 動画デフォルト ON のテスト

**Files:**
- Create: `plugins/ndf/skills/playwright-kit-ops/tests/test_video_default.py`

- [ ] **Step 1: テストファイルを作成**

`plugins/ndf/skills/playwright-kit-ops/tests/test_video_default.py` に以下を書く:

```python
"""--pwk-no-video オプションと動画デフォルト ON の検証。

pytest-playwright の --video オプションが playwright_kit plugin 経由で
デフォルト 'on' に設定されること、および --pwk-no-video で 'off' に
切り替わることを pytester 経由で検証する。
"""

from __future__ import annotations

import textwrap


def test_pwk_no_video_option_registered(pytester):
    """--pwk-no-video が pytest -h に出ること。"""
    pytester.makepyfile("def test_dummy(): pass\n")
    res = pytester.runpytest("--help")
    out = res.stdout.str()
    assert "--pwk-no-video" in out


def test_video_default_on(pytester):
    """--video 未指定時、playwright_kit が video='on' をデフォルト設定すること。"""
    pytester.makepyfile(
        textwrap.dedent(
            """
            def test_video_config(pytestconfig):
                video = pytestconfig.getoption("video", default=None)
                assert video == "on", f"expected 'on', got {video!r}"
            """
        )
    )
    res = pytester.runpytest("-q")
    res.assert_outcomes(passed=1)


def test_pwk_no_video_sets_off(pytester):
    """--pwk-no-video 指定時、video='off' になること。"""
    pytester.makepyfile(
        textwrap.dedent(
            """
            def test_video_config(pytestconfig):
                video = pytestconfig.getoption("video", default=None)
                assert video == "off", f"expected 'off', got {video!r}"
            """
        )
    )
    res = pytester.runpytest("-q", "--pwk-no-video")
    res.assert_outcomes(passed=1)


def test_explicit_video_flag_takes_precedence(pytester):
    """--video=retain-on-failure を明示指定した場合、pwk が上書きしないこと。"""
    pytester.makepyfile(
        textwrap.dedent(
            """
            def test_video_config(pytestconfig):
                video = pytestconfig.getoption("video", default=None)
                assert video == "retain-on-failure", f"expected 'retain-on-failure', got {video!r}"
            """
        )
    )
    res = pytester.runpytest("-q", "--video=retain-on-failure")
    res.assert_outcomes(passed=1)
```

- [ ] **Step 2: テストを実行して FAIL を確認**

Run: `cd /work/ai-plugins/plugins/ndf/skills/playwright-kit-ops && uv run pytest tests/test_video_default.py -v`

Expected: `test_pwk_no_video_option_registered` → FAIL (`--pwk-no-video` がまだ登録されていない)。`test_video_default_on` → FAIL (デフォルトが `on` ではない)。`test_pwk_no_video_sets_off` → FAIL。`test_explicit_video_flag_takes_precedence` → 結果は pytest-playwright の状態に依存。

- [ ] **Step 3: コミット**

```bash
git add plugins/ndf/skills/playwright-kit-ops/tests/test_video_default.py
git commit -m "test: --pwk-no-video オプションと動画デフォルト ON の failing tests 追加"
```

---

## Task 2: `pytest_plugin.py` に `--pwk-no-video` を実装して動画デフォルト ON にする

**Files:**
- Modify: `plugins/ndf/skills/playwright-kit-ops/playwright_kit/pytest_plugin.py:44-93` (pytest_addoption)
- Modify: `plugins/ndf/skills/playwright-kit-ops/playwright_kit/pytest_plugin.py:110-137` (pytest_configure)

- [ ] **Step 1: `pytest_addoption` に `--pwk-no-video` を追加**

`plugins/ndf/skills/playwright-kit-ops/playwright_kit/pytest_plugin.py` の `pytest_addoption` 関数内、`--pwk-overlay` の直前に追加する:

```python
    group.addoption(
        "--pwk-no-video",
        action="store_true",
        default=False,
        help="動画収集を明示的に OFF にする (デフォルトは全テストで動画 ON)",
    )
```

- [ ] **Step 2: `pytest_configure` に動画デフォルト ON ロジックを追加**

`pytest_configure` 関数内の既存コードの末尾（`config._pwk_config = cfg` 行の後、関数末尾）に以下を追加する:

```python
    # 動画デフォルト ON (大原則: エビデンス動画を常に取得)
    # ユーザーが --video を CLI で明示指定した場合はそちらを優先する。
    # --pwk-no-video 指定時は video='off' に設定する。
    # --pwk-no-evidence 指定時も video='off' に設定する (全エビデンス OFF)。
    try:
        video_opt = config.getoption("video", default=None)
        no_video = config.getoption("pwk_no_video", default=False)
        no_evidence = config.getoption("pwk_no_evidence", default=False)
        if video_opt is None:
            if no_video or no_evidence:
                config.option.video = "off"
            else:
                config.option.video = "on"
    except (ValueError, AttributeError):
        pass
```

- [ ] **Step 3: テストを実行して PASS を確認**

Run: `cd /work/ai-plugins/plugins/ndf/skills/playwright-kit-ops && uv run pytest tests/test_video_default.py -v`

Expected: 4 テスト全て PASS。

- [ ] **Step 4: 既存テストが壊れていないことを確認**

Run: `cd /work/ai-plugins/plugins/ndf/skills/playwright-kit-ops && uv run pytest -q`

Expected: 全テスト PASS (テスト数は変動する可能性あり)。

- [ ] **Step 5: コミット**

```bash
git add plugins/ndf/skills/playwright-kit-ops/playwright_kit/pytest_plugin.py
git commit -m "feat: --pwk-no-video オプション追加 + 動画デフォルト ON"
```

---

## Task 3: `run.sh` テンプレートに `--video=on` フォールバックを追加

**Files:**
- Modify: `plugins/ndf/skills/playwright-kit-ops/templates/run.sh:80-89` (pytest 実行部分)

- [ ] **Step 1: `run.sh` の pytest 実行部分を変更**

`plugins/ndf/skills/playwright-kit-ops/templates/run.sh` の末尾 pytest 実行部分を以下に置換する:

```bash
# --- 3) pytest 実行 ------------------------------------------------
cd "$RUNTIME_DIR"

# --pwk-no-video が引数に含まれていなければ --video=on をデフォルト追加。
# pytest_plugin.py 側でもデフォルト注入するが、run.sh 経由の場合は
# 明示的に渡すことで --video の優先度を確保する。
VIDEO_FLAG="--video=on"
for arg in "$@"; do
  case "$arg" in
    --pwk-no-video|--pwk-no-evidence) VIDEO_FLAG="" ;;
  esac
done

exec uv run pytest \
  --pwk-config="${PWK_CONFIG:-./scenario.config.yaml}" \
  $VIDEO_FLAG \
  "$@"
```

- [ ] **Step 2: help テキストに `--pwk-no-video` を追加**

`run.sh` の `--help` セクション (`cat <<HELP` ブロック内)、`--pwk-no-evidence` の次の行に以下を追加:

```
  --pwk-no-video               動画収集を OFF (デフォルトは全テストで動画 ON)
```

- [ ] **Step 3: コミット**

```bash
git add plugins/ndf/skills/playwright-kit-ops/templates/run.sh
git commit -m "feat: run.sh に --video=on デフォルト + --pwk-no-video 対応追加"
```

---

## Task 4: conftest.py テンプレートにテストスクリプト存在チェックを追加

**Files:**
- Modify: `plugins/ndf/skills/playwright-kit-ops/templates/conftest.py.template`

- [ ] **Step 1: テストスクリプト存在チェックを追加**

`plugins/ndf/skills/playwright-kit-ops/templates/conftest.py.template` の末尾に以下を追加する:

```python


def pytest_collection_modifyitems(session, config, items):
    if not items:
        import warnings

        warnings.warn(
            "[pwk] tests/ にテストスクリプトが見つかりません。"
            "テストスクリプトを作成してから実行してください。",
            stacklevel=1,
        )
```

- [ ] **Step 2: コミット**

```bash
git add plugins/ndf/skills/playwright-kit-ops/templates/conftest.py.template
git commit -m "feat: conftest テンプレートにテストスクリプト存在チェック追加"
```

---

## Task 5: 旧スキルディレクトリを削除

**Files:**
- Delete: `plugins/ndf/skills/playwright-evidence/` (ディレクトリごと)
- Delete: `plugins/ndf/skills/playwright-overlay/` (ディレクトリごと)
- Delete: `plugins/ndf/skills/playwright-quality/` (ディレクトリごと)

- [ ] **Step 1: 3 ディレクトリを削除**

```bash
git rm -r plugins/ndf/skills/playwright-evidence
git rm -r plugins/ndf/skills/playwright-overlay
git rm -r plugins/ndf/skills/playwright-quality
```

- [ ] **Step 2: コミット**

```bash
git commit -m "refactor: playwright-evidence/overlay/quality を削除 (playwright-execution に統合)"
```

---

## Task 6: `playwright-execution/SKILL.md` を新規作成

**Files:**
- Create: `plugins/ndf/skills/playwright-execution/SKILL.md`

- [ ] **Step 1: SKILL.md を作成**

`plugins/ndf/skills/playwright-execution/SKILL.md`:

```markdown
---
name: playwright-execution
description: "Playwright E2E テストの実行 + エビデンス収集 (video/trace/screenshot/HAR) + overlay (赤丸カーソル+字幕) + 品質計測 (axe-core/Web Vitals/body_check) を統合した実行フェーズスキル。動画はデフォルト ON。"
when_to_use: "E2E テストの実行 / エビデンス収集 / 動画エビデンス / accessibility チェック / Core Web Vitals 計測が必要なとき。テストスクリプト作成済みであることが前提。Triggers: 'E2E テスト実行', 'テスト実行', '動画エビデンス', 'エビデンス収集', 'テスト証跡', 'a11y テスト', 'accessibility テスト', 'axe-core', 'WCAG', 'Core Web Vitals', 'Web Vitals', 'LCP', 'CLS', 'body_check', 'overlay', '字幕', 'カーソル'"
allowed-tools:
  - Read
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(npx *)
  - Bash(playwright *)
  - Bash(python *)
---

# Playwright Execution (テスト実行 + エビデンス収集)

テストスクリプト作成済みの状態で E2E テストを実行し、エビデンスを収集する。

## 前提条件

- テストスクリプトが `tests/` に作成済みであること (`/ndf:playwright-script-creation` で作成)
- `scenario.config.yaml` が設定済みであること

## 大原則

**エビデンス動画はデフォルト ON**。全テストで常に動画を取得する。
明示的にスキップする場合のみ `--pwk-no-video` を指定する。

## 実行コマンド

```bash
./scenario-test/run.sh                            # 全テスト (動画 ON)
./scenario-test/run.sh -k test_admin              # フィルタ
./scenario-test/run.sh --pwk-overlay              # 字幕 + カーソル付き動画
./scenario-test/run.sh --pwk-no-video             # 動画のみ OFF
./scenario-test/run.sh --pwk-no-evidence          # 全エビデンス OFF (HAR/trace/動画)
```

## エビデンス種別

| 種別 | デフォルト | OFF フラグ | 説明 |
|---|---|---|---|
| video | **ON** | `--pwk-no-video` | 全テストの動画を取得 |
| trace | ON (retain-on-failure) | `--pwk-no-evidence` | Playwright Trace (DOM + 操作ログ) |
| HAR | ON (minimal) | `--pwk-har-mode none` | ネットワーク通信ログ |
| screenshot | ON (only-on-failure) | `--pwk-no-evidence` | 失敗時スクリーンショット |

## overlay (赤丸カーソル + 字幕)

`--pwk-overlay` フラグで全テストの動画にオーバーレイが適用される。

API 詳細・使用例は `playwright_kit/overlay.py` を参照。主要関数: `set_caption()`, `flash_click()`, `hide_cursor()`。

## 品質計測

### accessibility (axe-core)

`@pytest.mark.page_role` marker が付いたテストで auto_roles にマッチする場合に自動実行。
設定は `scenario.config.yaml` の `accessibility:` セクションで制御。→ 設定例は `templates/scenario.config.yaml` を参照。

### Core Web Vitals

`@pytest.mark.page_role` marker + auto_roles マッチで LCP/CLS/TTFB/longest_task を自動計測。
設定は `scenario.config.yaml` の `web_vitals:` セクションで制御。→ 設定例は `templates/scenario.config.yaml` を参照。

### body_check (PHP/SSR エラー検出)

`page.on("response")` で全 HTML レスポンスを監視し、`Fatal error` 等を検出。デフォルト有効。
`@pytest.mark.no_body_check` で個別 opt-out 可能。→ 設定例は `templates/scenario.config.yaml` の `body_check:` セクションを参照。

## 成果物

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

## CLI options

| option | 役割 |
|---|---|
| `--pwk-config <path>` | `scenario.config.yaml` のパス |
| `--pwk-out-dir <path>` | 成果物出力先 (default: `reports/<run-id>/`) |
| `--pwk-no-video` | 動画収集を OFF (デフォルトは ON) |
| `--pwk-no-evidence` | HAR / trace / video の収集を全て OFF |
| `--pwk-har-mode {minimal,full,none}` | HAR 録画モード (default: minimal) |
| `--pwk-overlay` | overlay (赤丸カーソル + 字幕) を ON |

## 関連 Skill

- `/ndf:playwright-script-creation` — テストスクリプト作成 (実行の前段)
- `/ndf:playwright-report` — Markdown レポート生成
- `/ndf:playwright-kit-ops` — スクリプト実行 (init_project / スキャン)
- `/ndf:playwright-scenario-test` — 全機能統括
```

- [ ] **Step 2: コミット**

```bash
git add plugins/ndf/skills/playwright-execution/SKILL.md
git commit -m "feat: playwright-execution スキル追加 (evidence+overlay+quality 統合)"
```

---

## Task 7: `playwright-script-creation/SKILL.md` を新規作成

**Files:**
- Create: `plugins/ndf/skills/playwright-script-creation/SKILL.md`

- [ ] **Step 1: SKILL.md を作成**

`plugins/ndf/skills/playwright-script-creation/SKILL.md`:

```markdown
---
name: playwright-script-creation
description: "再現可能な E2E テストスクリプトを作成するガイド。テンプレートを起点にテストコードを実装し、再現可能性レビューを経てからテスト実行フェーズに進む。ndf plugin 非依存で動作する。"
when_to_use: "E2E テストスクリプトの作成 / テストコードの実装 / テストテンプレートからのスクリプト生成が必要なとき。Triggers: 'テストスクリプト作成', 'テストコード作成', 'テスト実装', 'テストを書く', 'シナリオ作成', 'codegen', 'テンプレートからテスト', 'playwright codegen'"
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash(uv *)
  - Bash(playwright *)
  - Bash(python *)
---

# Playwright Script Creation (テストスクリプト作成)

再現可能なテストスクリプトを作成し、レビューを経てからテスト実行に進む。

## 大原則

**テストスクリプトを実装してからテストを実施する。**
スクリプトが完成・レビューを経るまで `/ndf:playwright-execution` に進まない。

## 前提条件

- テスト計画が完了していること (`/ndf:playwright-test-planning` で計画済み)
- `init_project.sh` でプロジェクトが初期化済みであること (`/ndf:playwright-kit-ops`)

## ワークフロー

```
[A] テスト計画の確認 (チェックリスト / page role / テスト技法)
      │
[B] テンプレート選択
      │  tests/ 配下の test_*.py.template を起点にする
      ▼
[C] テストコード実装
      │  playwright codegen で操作を記録 → テスト関数に組み込む
      │  または手動で expect() ベースの assertion を書く
      ▼
[D] 再現可能性レビュー (下記チェックリスト)
      │
[E] テスト実行へ → /ndf:playwright-execution
```

## テンプレート一覧

`init_project.sh` で以下のテンプレートが `tests/` に配置済み:

| テンプレート | page role | 内容 |
|---|---|---|
| `test_auth.py` | auth | ログイン / ログアウトフロー |
| `test_list.py` | list | 一覧ページネーション / ソート |
| `test_form.py` | form | 入力 → 送信 → 結果検証 |
| `test_dashboard.py` | dashboard | KPI / リンク遷移 |

## テストコードの書き方

### テンプレートを起点にする

各 page role のテンプレートが `templates/test_*.py.template` に用意されている。
`init_project.sh` 実行時に `tests/` へコピーされるので、プロジェクト固有の URL やセレクタを書き換えて使う。

→ コード例: `templates/test_form.py.template`, `templates/test_auth.py.template` 等を参照

### playwright codegen での操作記録

`uv run playwright codegen <URL>` で操作を記録し、生成コードをテスト関数にコピーする。
コピー後に `@pytest.mark.page_role()`, `@pytest.mark.role()`, `expect()` assertion, `pwk_config.base_url` を追加する。

### overlay 付きテスト

overlay API (`set_caption`, `flash_click`) の使用例は `playwright_kit/overlay.py` を参照。

## fixture / marker 一覧

fixture / marker の完全な一覧は `playwright_kit/pytest_plugin.py` の `_PWK_MARKERS` 定義と `playwright_kit/fixtures/` 配下の各モジュールを参照。

主な fixture: `pwk_config`, `pwk_role_<id>`, `pwk_evidence`, `pwk_accessibility_scan()`, `pwk_web_vitals_measure()`
主な marker: `@pytest.mark.page_role()`, `@pytest.mark.role()`, `@pytest.mark.phase()`, `@pytest.mark.priority()`, `@pytest.mark.no_body_check`

## 再現可能性レビューチェックリスト

スクリプト完成後、以下を全項目確認してからテスト実行に進む:

- [ ] **再現可能性**: 同じ環境で同じ結果が得られるか (ランダム値・タイムスタンプに依存していないか)
- [ ] **テストデータ独立性**: 外部の状態に依存せず、テスト単体で成立するか
- [ ] **marker 付与**: `@pytest.mark.page_role()` が全テスト関数に付与されているか
- [ ] **role marker**: 認証が必要なテストに `@pytest.mark.role()` + `pwk_role_<id>` fixture があるか
- [ ] **assertion 網羅性**: 正常系 + 少なくとも 1 つの異常系 (バリデーション等) が含まれるか
- [ ] **URL 構築**: ハードコードされた URL ではなく `pwk_config.base_url` を使用しているか
- [ ] **wait 戦略**: `wait_until="domcontentloaded"` 等の明示的な待機指定があるか
- [ ] **ndf plugin 非依存**: `scenario-test/` ディレクトリ単体で実行可能か

## ndf plugin 非依存

`init_project.sh` で埋め込まれた `scenario-test/` は:
- `playwright_kit/` パッケージ本体を含む
- `pyproject.toml` で pytest11 entry-point を定義
- `run.sh` でワンコマンド実行可能

→ ndf plugin 未インストール環境でも `./scenario-test/run.sh` で動作する。

## 関連 Skill

- `/ndf:playwright-test-planning` — テスト計画 (前段)
- `/ndf:playwright-execution` — テスト実行 + エビデンス収集 (後段)
- `/ndf:playwright-kit-ops` — init_project / codegen 等のツール群
- `/ndf:playwright-scenario-test` — 全機能統括
```

- [ ] **Step 2: コミット**

```bash
git add plugins/ndf/skills/playwright-script-creation/SKILL.md
git commit -m "feat: playwright-script-creation スキル追加 (テストスクリプト作成ガイド)"
```

---

## Task 8: `playwright-test-planning/SKILL.md` を改修

**Files:**
- Modify: `plugins/ndf/skills/playwright-test-planning/SKILL.md`

- [ ] **Step 1: ワークフロー末尾に次フェーズ導線を追加**

`plugins/ndf/skills/playwright-test-planning/SKILL.md` のワークフロー `[E]` の後に以下を追加する:

```markdown
      ▼
[F] スクリプト作成へ → /ndf:playwright-script-creation
      テスト計画が確定したら、テストスクリプトの作成に進む。
      テスト計画が完了するまでスクリプト作成には進まない。
```

- [ ] **Step 2: 関連 Skill セクションを更新**

既存の関連 Skill セクションを以下に置換する:

```markdown
## 関連 Skill

- `/ndf:playwright-script-creation` — テストスクリプト作成 (次のフェーズ)
- `/ndf:playwright-execution` — テスト実行 + エビデンス収集
- `/ndf:playwright-scenario-test` — 全機能を統括したフルワークフロー
```

- [ ] **Step 3: コミット**

```bash
git add plugins/ndf/skills/playwright-test-planning/SKILL.md
git commit -m "Update: playwright-test-planning にスクリプト作成フェーズへの導線追加"
```

---

## Task 9: `playwright-report/SKILL.md` から Drive 共有を削除

**Files:**
- Modify: `plugins/ndf/skills/playwright-report/SKILL.md`

- [ ] **Step 1: Drive 関連セクションを削除**

`plugins/ndf/skills/playwright-report/SKILL.md` から以下のセクションを削除する:
- `## Google Drive アップロード` セクション全体 (「### テスト実行時に自動アップロード」と「### 手動アップロード」を含む)

- [ ] **Step 2: 関連 Skill セクションを更新**

既存の関連 Skill セクションを以下に置換する:

```markdown
## 関連 Skill

- `/ndf:playwright-execution` — テスト実行 + エビデンス収集
- `/ndf:playwright-kit-ops` — エビデンスアップロードツール (Drive 連携が必要な場合)
- `/ndf:playwright-scenario-test` — 全機能を統括したフルワークフロー
```

- [ ] **Step 3: コミット**

```bash
git add plugins/ndf/skills/playwright-report/SKILL.md
git commit -m "Update: playwright-report から Drive 共有セクションを削除"
```

---

## Task 10: `playwright-scenario-test/SKILL.md` (orchestrator) を改修

**Files:**
- Modify: `plugins/ndf/skills/playwright-scenario-test/SKILL.md`

- [ ] **Step 1: SKILL.md を全面改修**

`plugins/ndf/skills/playwright-scenario-test/SKILL.md` の内容を以下に置換する (frontmatter を含めて全体を差し替え):

```markdown
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
```

- [ ] **Step 2: コミット**

```bash
git add plugins/ndf/skills/playwright-scenario-test/SKILL.md
git commit -m "Update: playwright-scenario-test orchestrator を 5 スキル構成に改修"
```

---

## Task 11: `plugin.json` を更新

**Files:**
- Modify: `plugins/ndf/.claude-plugin/plugin.json`

- [ ] **Step 1: skills 配列を更新**

`plugins/ndf/.claude-plugin/plugin.json` の `skills` 配列から以下を削除:
```json
    "./skills/playwright-evidence",
    "./skills/playwright-overlay",
    "./skills/playwright-quality",
```

同じ配列の `"./skills/playwright-test-planning"` の後に以下を追加:
```json
    "./skills/playwright-script-creation",
    "./skills/playwright-execution",
```

- [ ] **Step 2: description を更新**

`description` フィールドを以下に変更:

```
"Integrated plugin with 8 specialized agents (model-tiered: opus/sonnet/haiku), 44 skills including official mcp-builder, on-demand loader for Anthropic official skills, generic workflow/principle skills, skill usage statistics, pytest-playwright E2E testing split into 5 focused skills (test-planning, script-creation, execution, report, kit-ops) + orchestrator with video-by-default evidence, Google Drive/Chat integration, and Codex CLI integration via /ndf:codex skill. Transcript retention is automatically kept at >= 90 days. Serena MCP is a separate plugin (mcp-serena)."
```

- [ ] **Step 3: version を 4.9.0 に更新**

```json
"version": "4.9.0",
```

- [ ] **Step 4: コミット**

```bash
git add plugins/ndf/.claude-plugin/plugin.json
git commit -m "Update: plugin.json を 5 スキル構成に更新 (v4.9.0)"
```

---

## Task 12: `claude plugin validate` で検証

**Files:** (なし — 検証のみ)

- [ ] **Step 1: プラグイン検証を実行**

Run: `cd /work/ai-plugins && claude plugin validate`

Expected: 検証が通ること。エラーがあればその場で修正する。

- [ ] **Step 2: 全テストを実行**

Run: `cd /work/ai-plugins/plugins/ndf/skills/playwright-kit-ops && uv run pytest -q`

Expected: 全テスト PASS。

---

## Task 13: 実装プラン `issues/PLAN23_playwright-skill-split.md` を更新

**Files:**
- Modify: `issues/PLAN23_playwright-skill-split.md`

- [ ] **Step 1: ステータスと内容を更新**

`issues/PLAN23_playwright-skill-split.md` のステータスを更新し、再構成の概要を追記する:

```markdown
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
```

- [ ] **Step 2: コミット**

```bash
git add issues/PLAN23_playwright-skill-split.md
git commit -m "Docs: PLAN23 を 5 スキル再構成に更新"
```
