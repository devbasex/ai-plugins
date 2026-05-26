# Playwright スキル再構成設計書

## 背景

PR #18 で `playwright-scenario-test` を 6 スキルに分割したが、以下の大原則を満たすために再構成が必要:

1. **再現可能なテストスクリプトを実装してからテストを実施する**
2. **テストスクリプトは ndf plugin がインストールされていなくても動作するようプロジェクトフォルダに設置する**
3. **テスト実行はエビデンス動画を常に取得できるようにしておく** (オプションで明示的にスキップ可能)

## スキル構成

### 変更前 (6 スキル + orchestrator)

| スキル | 責務 |
|---|---|
| playwright-test-planning | テスト計画 |
| playwright-evidence | エビデンス収集 |
| playwright-overlay | 動画装飾 |
| playwright-quality | 品質計測 |
| playwright-report | レポート + Drive 共有 |
| playwright-kit-ops | ツール群 |
| playwright-scenario-test | orchestrator |

### 変更後 (5 スキル + orchestrator)

| # | スキル | 責務 | 元スキル |
|---|---|---|---|
| 1 | `playwright-test-planning` | テスト計画 (HTSM/page role/チェックリスト) | 既存改修 |
| 2 | `playwright-script-creation` | テストスクリプト作成 (テンプレート→実装→レビュー) | **新規** |
| 3 | `playwright-execution` | テスト実行+エビデンス収集 (video/trace/overlay/quality) | evidence + overlay + quality 統合 |
| 4 | `playwright-report` | レポート生成 (Drive 共有削除) | 既存改修 |
| 5 | `playwright-kit-ops` | ツール群 (init_project/スキャン/アップロード) | 維持 |
| -- | `playwright-scenario-test` | orchestrator (5 スキルへの案内) | 既存改修 |

### 廃止スキル

- `playwright-evidence` → `playwright-execution` に統合
- `playwright-overlay` → `playwright-execution` に統合
- `playwright-quality` → `playwright-execution` に統合

## ワークフロー (大原則の反映)

```
[Phase 1] テスト計画 (/ndf:playwright-test-planning)
    │  page role 判定 → チェックリスト → テスト技法確定
    ▼
[Phase 2] スクリプト作成 (/ndf:playwright-script-creation)
    │  テンプレート選択 → テストコード実装 → 再現可能性レビュー
    │  ※ スクリプトが完成するまでテスト実行に進まない
    ▼
[Phase 3] テスト実行+エビデンス収集 (/ndf:playwright-execution)
    │  動画デフォルトON → trace/HAR/overlay/a11y/CWV/body_check
    │  ※ --pwk-no-video で動画のみスキップ可
    ▼
[Phase 4] レポート生成 (/ndf:playwright-report)
    │  report.md 自動生成
    ▼
[任意] ツール群 (/ndf:playwright-kit-ops)
       init_project / スキャン / アップロード (任意タイミング)
```

## コード変更

### A. pytest_plugin.py: 動画デフォルト ON

`--video=on` を pytest_configure でデフォルト注入する。ユーザーが `--video` を明示指定した場合はそちらを優先。

```python
# 新規 CLI オプション
group.addoption(
    "--pwk-no-video",
    action="store_true",
    default=False,
    help="動画収集を明示的に OFF にする",
)
```

```python
def pytest_configure(config):
    # ... 既存の marker 登録 ...

    # 動画デフォルト ON: ユーザーが --video を明示指定していない場合のみ
    video_opt = config.getoption("video", default=None)
    no_video = config.getoption("pwk_no_video", default=False)
    if video_opt is None and not no_video:
        config.option.video = "on"
    elif no_video:
        config.option.video = "off"
```

### B. run.sh: --video=on のフォールバック追加

pytest_plugin 側で制御するため run.sh は補助的な変更のみ:

```bash
# --pwk-no-video が引数に含まれていなければ --video=on を追加
VIDEO_FLAG="--video=on"
for arg in "$@"; do
  case "$arg" in
    --pwk-no-video) VIDEO_FLAG="" ;;
  esac
done

exec uv run pytest \
  --pwk-config="${PWK_CONFIG:-./scenario.config.yaml}" \
  $VIDEO_FLAG \
  "$@"
```

### C. conftest.py テンプレート: テストスクリプト存在チェック

`conftest.py.template` にテストスクリプトの存在チェックを追加:

```python
def pytest_collection_modifyitems(session, config, items):
    """テストスクリプトが存在しない場合に警告を出す。"""
    if not items:
        import warnings
        warnings.warn(
            "[pwk] tests/ ディレクトリにテストスクリプトが見つかりません。"
            "playwright-script-creation スキルでテストスクリプトを作成してください。",
            stacklevel=1,
        )
```

### D. plugin.json

- 廃止: `playwright-evidence`, `playwright-overlay`, `playwright-quality` (3 スキル削除)
- 追加: `playwright-script-creation`, `playwright-execution` (2 スキル追加)
- skills 数: 45 → 44

### E. SKILL.md ファイル操作

| ファイル | 操作 |
|---|---|
| `playwright-script-creation/SKILL.md` | 新規作成 |
| `playwright-execution/SKILL.md` | 新規作成 |
| `playwright-test-planning/SKILL.md` | 改修 (次フェーズ導線追加) |
| `playwright-report/SKILL.md` | 改修 (Drive 共有削除) |
| `playwright-scenario-test/SKILL.md` | 改修 (5 スキル案内テーブル更新) |
| `playwright-evidence/SKILL.md` | 削除 (ディレクトリごと) |
| `playwright-overlay/SKILL.md` | 削除 (ディレクトリごと) |
| `playwright-quality/SKILL.md` | 削除 (ディレクトリごと) |

## 各スキル SKILL.md の要件

### playwright-test-planning (改修)

- 既存の内容を維持
- ワークフロー末尾に「次は `/ndf:playwright-script-creation` でスクリプトを作成」を追加
- 「テスト計画が完了するまでスクリプト作成に進まない」を明記

### playwright-script-creation (新規)

- テンプレート（test_*.py.template）を起点にテストスクリプトを作成するガイド
- `playwright codegen` での操作記録 → テストコード化の手順
- スクリプト完成後のレビューチェックリスト:
  - 再現可能性の確認 (同じ環境で同じ結果が得られるか)
  - テストデータの独立性 (外部依存の排除)
  - page_role / role marker の付与確認
  - assert / expect の網羅性
- 「スクリプトが完成・レビューを経てから `/ndf:playwright-execution` に進む」を明記
- ndf plugin 非依存で動作することの説明 (init_project.sh で埋め込み済みの場合)

### playwright-execution (新規: 3 スキル統合)

- エビデンス収集設定 (video/trace/screenshot/HAR) — 旧 playwright-evidence
- overlay (赤丸カーソル + 字幕) — 旧 playwright-overlay
- 品質計測 (axe-core/Web Vitals/body_check) — 旧 playwright-quality
- **動画はデフォルト ON** であることを明記
- `--pwk-no-video` で動画のみスキップ可能
- `--pwk-no-evidence` で HAR/trace も含めて全エビデンス OFF
- 実行コマンド例、成果物ディレクトリ構造の説明

### playwright-report (改修)

- 既存の Markdown レポート自動生成を維持
- Drive 共有関連のセクション・コマンド例を削除
- (Drive アップロードが必要な場合は kit-ops を案内)

### playwright-scenario-test (orchestrator 改修)

- 5 スキルへの案内テーブルを更新
- フェーズ順序 (計画→スクリプト→実行→レポート) を明記
- 大原則 3 つを冒頭に記載

## 実装上の注意点

- `pytest_configure` での `config.option.video` 直接設定は pytest-playwright の内部実装に依存する。実装時に pytest-playwright の `pytest_configure` フックとの実行順序を検証し、必要に応じて `tryfirst=True` や `browser_context_args` fixture 経由での制御に切り替える。
- `--pwk-no-video` と `--pwk-no-evidence` の関係: `--pwk-no-evidence` は既存の HAR/trace OFF フラグ。`--pwk-no-video` は動画のみの独立制御。両方指定した場合は全エビデンス OFF。
- 旧スキルディレクトリ (`playwright-evidence/`, `playwright-overlay/`, `playwright-quality/`) は SKILL.md のみ含むため、ディレクトリごと削除可能。

## ndf plugin 非依存の保証

`init_project.sh` で埋め込まれた `scenario-test/` ランタイムは:

1. `playwright_kit/` パッケージ本体を含む
2. `pyproject.toml` で pytest11 entry-point を定義
3. `run.sh` でワンコマンド実行可能
4. テストスクリプト (`tests/test_*.py`) はプロジェクトフォルダに配置

→ ndf plugin がインストールされていない環境でも `./scenario-test/run.sh` で動作する。
