---
name: playwright-script-creation
description: "再現可能なE2Eテストスクリプトを作成する。"
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
