# #3907: 学習に使う設定を Off へ書き換えてから claude・codex を起動する（実装計画）

## 関連リンク

- 要求仕様: [issue-3907-requirements.md](./issue-3907-requirements.md)（正は #3907 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3907-design.md](./issue-3907-design.md)、詳細設計書: [issue-3907-design-detail.md](./issue-3907-design-detail.md)
- モード: `standard`（公開の結果 JSON の項目を足し、既定で働く起動の口の振る舞いを変える機能追加。計画 p8-5 のレーン ltrain の判定）

## 修正対象

詳細設計書の「地図」から引き写す。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/adapters/training.py` | `Patch`・`settle`・`_send_off`・`_values`・`_auth`・`_claude_headers`・`codex_values`・`_grove_values`・`_settle_lock`・`_summary` を新設。`Endpoint`・`_read_over_http`・`read_claude`・`read_codex`・`codex_training`・`_read_now`・`ensure`・`check` を変える。`_grove_enabled` を消す。冒頭の docstring を書き換える振る舞いへ直す |
| `plugins/ndf/src/ndf/adapters/training_cache.py` | `Rewrite`・`off_by_hand`・`MANUAL_OFF` を新設。`Reading`（`rewrites` を既定値つきで足す）・`Reading.item`・`save`（保存の排他）・`_cached_verdict`（学習に使う設定の断りに記録の `reason` と手で直す場所）を変える |
| `plugins/ndf/contracts/schemas/io/training.result.schema.json` | `items` の要素に任意の `rewrites` を足す |
| `plugins/ndf/contracts/limits.json` | `codex-training.budget` を消し、`training.save-lock`（2 秒）を足す |
| `plugins/ndf/docs/contracts/commands.md` | `ndf check --write-spec` で作り直す |
| `plugins/ndf/README.md` | 「学習の設定を確かめる」の節を書き換える振る舞いへ直す |
| `plugins/ndf/tests/adapters/test_training_optout.py`（新設） | 受け入れ条件 1〜6・混在の `summary`・並行の保存と確かめ |
| `plugins/ndf/tests/cli/test_every_output.py` | `test_training_check_rewrite_output` を足す |

## タスク分解

機能追加なので、最初のタスクは今の振る舞いを縛る退行テストにする。今の `test_training.py`・`test_training_ensure.py` がその役を持つので、変えずに通し続けることを全タスクの終わりの条件にする（受け入れ条件 7）。

### Task 1: 書き換えの記録の形（`Rewrite`・`Reading.rewrites`・スキーマ）

- **対象ファイル:** `training_cache.py`、`training.result.schema.json`、`test_training_optout.py`
- **変更内容:** `Rewrite` と `Reading` の 4 つ目の項目、`item()` が `rewrites` のあるときだけ載せる。スキーマに閉じた要素の `rewrites` を足す
- **満たす受け入れ条件:** 1・2（記録と出力の形）、7
- **進め方:** 失敗するテスト（`item()` の 5 項目・スキーマが足りない要素を落とす）→ 実装 → 整理

### Task 2: claude の書き換え（`settle` の骨格）

- **対象ファイル:** `training.py`、`test_training_optout.py`
- **変更内容:** `Endpoint` を `values`・`place`・`patch` へ替え、`_values`・`_auth`・`_claude_headers`・`_send_off`・`settle` を作る。`read_claude`・`read_codex` は `_read_over_http` 経由で読むだけのまま
- **満たす受け入れ条件:** 1・5・6
- **進め方:** 失敗するテスト（true → 202 → false で `rewrites`・送った本文と要求ヘッダ、false では `GET` 1 回）→ 実装 → 整理

### Task 3: codex の書き換えと失敗の扱い

- **対象ファイル:** `training.py`、`training_cache.py`（`MANUAL_OFF`・`off_by_hand`・`_cached_verdict`）、`test_training_optout.py`
- **変更内容:** `codex_values` と true のキーだけの `PATCH`、1 キーの失敗でも残りを送る。403・通信の失敗・読み直しても true・読み直しが 500・キーが消えた場合を `training: True` と理由にする。起動の口の断りに記録の `reason` と手で直す場所を載せる
- **満たす受け入れ条件:** 2・4・6
- **進め方:** 失敗するテスト → 実装 → 整理

### Task 4: `check` と起動の口（`_read_now`・`ensure`）を `settle` へつなぐ

- **対象ファイル:** `training.py`、`test_training_optout.py`、`tests/cli/test_every_output.py`
- **変更内容:** `check` と `_read_now` が `settle` を呼ぶ。`_summary` で混在の `summary` を組む。CLI から書き換えを通すケースを足す
- **満たす受け入れ条件:** 1・2・3・4
- **進め方:** 失敗するテスト（`check` の `ok`/`stopped`・混在の `summary`・差し替えない `_read_now` での起動）→ 実装 → 整理

### Task 5: 排他（保存と同じ担当の確かめ）

- **対象ファイル:** `training.py`（`_settle_lock`・`ensure`・`check`）、`training_cache.py`（`save`）、`limits.json`、`test_training_optout.py`
- **変更内容:** `training.json.lock` の中で保存し、`training.<担当>.lock` の中で確かめて保存する。`ensure` は排他の後に記録を読み直す。期限切れと保存の失敗を担当ごとに受ける。`codex-training.budget` を消し `training.save-lock` を足す
- **満たす受け入れ条件:** 3・4（決定 11）
- **進め方:** 失敗するテスト（2 スレッドの同時保存・同じ担当の 2 つの `ensure`・排他の期限切れ・保存の失敗）→ 実装 → 整理

### Task 6: 文書（docstring・README・commands.md）

- **対象ファイル:** `training.py` の冒頭、`README.md`、`commands.md`
- **変更内容:** 「読むだけ」を書き換える振る舞いへ直す。`commands.md` は `ndf check --write-spec` で作り直す
- **満たす受け入れ条件:** —（設計の「設計の結果」の触るファイル）
- **進め方:** テスト駆動を適用しない（文書。`commands.md` は生成と `check-ndf-contracts.py` で確かめる）

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| #3907 の 5（PR 1 本） | Task 1〜6 | 3907:1〜7 | 約 700（数えない: `plugins/ndf/docs/contracts/commands.md`） | （実装の後に書き足す） |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `training.py` は 288 行、`training_cache.py` は 200 行で、今のテストが 2 ファイル 355 行ある。読み手の切り出し（`_auth`・`_values`）は Task 2 の中で詳細設計どおりに行う |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | しない | 単位が 1 つで、単位をまたぐ候補が無い |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 今のテストが `Reading` の 3 引数・`_read_now` の名前・`ensure` の `read` に依存している | 形を変えない。毎タスクの後に 2 つのファイルを打つ |
| 本物のアカウントへ書き込みを送る | テストは httpx の `MockTransport` だけを使う。実際の書き換えはリリースの前に利用者が確かめる（要求仕様の「検証手段」） |

## 完了の定義

- [ ] 受け入れ条件 1〜7 をすべて満たし、条件ごとに縛るテストが対応している
- [ ] 足した・触ったテストのファイルが手元で通る。テストの suite 全体は PR の CI で決める（`test.ci.only`）
- [ ] `bash scripts/check-lint.sh`・`python3 scripts/check-ndf-contracts.py`・`python3 scripts/check-script-structure.py` が通る
