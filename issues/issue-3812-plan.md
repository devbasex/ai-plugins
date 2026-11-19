# #3812: 中継のラッパーが最初のセグメントの前とカットポイントでプラグインを更新する

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/3812 （#3813・#3816 を統合）
- 要求: [issue-3812-requirements.md](./issue-3812-requirements.md)
- 設計: [issue-3812-design.md](./issue-3812-design.md)（設計 PR https://github.com/devbasex/ai-plugins/pull/3844 で承認済み）
- 詳細設計: [issue-3812-design-detail.md](./issue-3812-design-detail.md)

## モード

standard（pace normal）。複数のモジュールと契約（`limits.json`・`locations.json`）と手順書にまたがり、設計承認ゲートを通した。

## 目的と非目的

達成したい状態:
- リリースの後のカットポイントで、ラッパーが次のセグメントを新しいバージョンの NDF で始める

やらないこと:
- ラッパー自身の入れ替え（10 系の reexec と `handover.json`）
- 手順書の「上限」（#3815）・「承認ゲートを越えない守り」の本体（#3814）・リレーハブ（#3818）
- 子の claude の起動の例外の扱い（#3842）・`--trim` の不具合（#3843）

## 受け入れ条件

要求の受け入れ条件 1〜11（[issue-3812-requirements.md](./issue-3812-requirements.md) の「受け入れ条件」）をそのまま使う。検証手段は設計文書の「テスト設計」の表にある。

## 不変条件

設計文書の I1〜I8 を使う。

## 修正対象

詳細設計の「ファイル配置」の表から引き写す（パスは `plugins/ndf/` から）。

- `src/ndf/domains/relay/plugin_update.py`（新設）
- `src/ndf/adapters/claude.py`
- `src/ndf/domains/relay/wrapper.py`
- `src/ndf/domains/relay/cli.py`
- `src/ndf/domains/relay/session_env.py`
- `src/ndf/domains/relay/hub_decide.py`
- `contracts/limits.json`
- `contracts/domains/locations.json`
- `skills/development-workflow/references/relay.md`
- `skills/development-workflow/references/relay-accounts.md`
- `tests/domains/relay/test_relay_plugin_update.py`（新設）
- `tests/domains/relay/test_relay_wrapper.py`
- `tests/domains/relay/test_relay_decide.py`

## タスク分解

### Task 0: 新しいバージョンへ上げたときの `updateOutcome` を実測する

- **対象ファイル:** 無し（結果を Task 2 のテストの入力にする）
- **変更内容:** 別の `CLAUDE_CONFIG_DIR` に古いバージョンの NDF を入れてから更新し、`claude plugin update ... --json` の 1 行を記録する（詳細設計の「処理の手順」の `<上げたときの値>`）
- **満たす受け入れ条件:** 1・3 の前提
- **進め方:** 実測（テスト駆動を当てない。外部コマンドの挙動を確かめる作業）
- **結果（2026-11-19、Claude Code 2.1.328）:** 別の `CLAUDE_CONFIG_DIR` にローカルのマーケットプレイスを置き、1.0.0 を入れてから 1.0.1 へ上げて `marketplace update` → `update -y --json --scope user` を打った。終了コード 0 で、最後の行は次のとおり。`<上げたときの値>` は `updated` である

  ```json
  {"command":"update","outcome":"ok","plugin":"ndf@mtest","pluginId":"ndf@mtest","scope":"user","message":"Plugin \"ndf\" updated from 1.0.0 to 1.0.1 for scope user. Restart to apply changes.","updateOutcome":"updated","oldVersion":"1.0.0","newVersion":"1.0.1"}
  ```

  更新の後の `claude plugin list --json` の行は `version: "1.0.1"` と `installPath: <設定の保存先>/plugins/cache/mtest/ndf/1.0.1` を持つ

### Task 1: バージョンの混ざりをメジャーバージョンで判定する

- **対象ファイル:** `src/ndf/domains/relay/hub_decide.py`・`tests/domains/relay/test_relay_decide.py`
- **変更内容:** `mixed` が旧い NDF とメジャーバージョンの違うラッパーだけを断る。`_major` を足す
- **満たす受け入れ条件:** 5
- **進め方:** 失敗するテスト → 通す最小実装

### Task 2: 導入元の選び方と更新の結果の読み取り（純粋な関数）とアダプター

- **対象ファイル:** `src/ndf/domains/relay/plugin_update.py`・`src/ndf/adapters/claude.py`・`tests/domains/relay/test_relay_plugin_update.py`
- **変更内容:** `Ran`・`run_plugin`・`list_plugins(env=...)`、`Origin`・`Target`・`Updated`・`find_origin`・`find_target`・`read_update`・`major_changed`
- **満たす受け入れ条件:** 3・6・11（判定の部分）
- **進め方:** 失敗するテスト → 通す最小実装

### Task 3: 錠の中で 2 つのコマンドを打つ `Updater` と、その組み立て

- **対象ファイル:** `src/ndf/domains/relay/plugin_update.py`・`src/ndf/domains/relay/session_env.py`・`src/ndf/domains/relay/cli.py`・`contracts/limits.json`・`contracts/domains/locations.json`・`tests/domains/relay/test_relay_plugin_update.py`
- **変更内容:** `Updater.__call__`・`Updater.confirm`、`tool_env`、`wrapper_settings` が `Settings.update`・`confirm` を渡す。上限 `relay.plugin-update` と錠の保存先 `relay-plugin-update` を足し、`relay.handover-prepare`・`relay.runtime-install` を消す
- **満たす受け入れ条件:** 1・3・6・7（上限の削除）
- **進め方:** 失敗するテスト → 通す最小実装

### Task 4: ラッパーが更新・確認を呼び、メジャーバージョンの違いで止まる

- **対象ファイル:** `src/ndf/domains/relay/wrapper.py`・`tests/domains/relay/test_relay_wrapper.py`
- **変更内容:** `Settings.update`・`confirm`、`_update_plugin`・`_confirm_plugin`・`_stop_for_major`、`_loop`・`_on_switch`・`_on_resume` の順の変更
- **満たす受け入れ条件:** 1・2・3・4・11
- **進め方:** 失敗するテスト → 通す最小実装

### Task 5: 手順書を 11 系の振る舞いへ直す

- **対象ファイル:** `skills/development-workflow/references/relay.md`・`relay-accounts.md`
- **変更内容:** 冒頭の例・「好きな時点で切り替える」・「ラッパーの入れ替え」・「承認ゲートを越えない守り」の最後の箇条・「落ちたときの続け方」・「記録の読み方」
- **満たす受け入れ条件:** 7・8・9
- **進め方:** 文書のためテスト駆動を当てない（`.md` の文言テストは書かない）。`jq` の例は手元の `log.jsonl` に打って確かめる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `wrapper.py`（427 行）の `_replace` を `_on_switch`・`_on_resume` で止めると起動の 2 つに分ける | タスクごとに触ったテストファイルを通す。構造の循環・責務の混在は見当たらないので先に整えない |
| 手元で全体のテストを打てない（CI テストの設定） | 触ったテストファイルだけを手元で打ち、全体は PR の CI で見る |

## 切り戻し手順

- PR を revert すれば戻る。データの移行は無い（`log.jsonl` の行が増えるだけ）

## 完了の定義

- [ ] 受け入れ条件 1〜11 を満たし、条件ごとに検証手段と結果が対応している
- [ ] PR の CI のテストのチェックが通る
