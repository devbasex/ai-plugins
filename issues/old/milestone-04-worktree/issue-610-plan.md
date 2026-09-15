# #610: 開始時の hook の追従を既定で止め、`follow_branch: true` で有効にする（実装計画）

## 関連リンク

- 要求と受け入れ条件: [issue-495-610-requirements.md](issue-495-610-requirements.md)（#610 は AC1〜AC10、共通は AC30〜AC31）
- 設計文書: [issue-495-610-design.md](issue-495-610-design.md)（決定 1〜4。Pull Request 1 の範囲）
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/634 （マージ済み）
- 課題: #610。#495（Pull Request 2）はこの後に別の担当が載せる

## モード

`standard`。hook の振る舞い（主ディレクトリの HEAD を動かすかどうか）と宣言の形（`follow_branch`）と
8 か所の文書が変わるため（親が判定済み）。

## 目的と非目的

達成したい状態:

- `follow_branch` を書かない宣言では、開始時の hook が主ディレクトリの HEAD を動かさず、origin へも
  通信しない（決定 2）
- `follow_branch: true` を書いたときだけ、現行の追従（`wt_follow_target` と `follow_to`）がそのまま動く
  （決定 3）
- 未コミット変更の提示は `follow_branch` の値によらず変わらない
- 文書 8 か所が「既定で追従する」前提を書かなくなる

やらないこと:

- 追従の判定規則（`wt_follow_target`）の変更
- 既に detached HEAD のまま残っている主ディレクトリへの案内（決定 4）
- 個人の宣言 `.ndf/worktree.local.json` の重ね合わせ（#495 が持つ）
- `worktree-common.sh` の `wt_follow_enabled` 以外の変更（並行する担当 B が `wt_extract_write_target` 周辺を触る）
- `CHANGELOG.md` と版数（配布の工程が書く）

## 前提

- 前提 1: 新しいファイルは作らない。変更は設計文書の「変わるファイル」の Pull Request 1 の 12 ファイルと
  生成物に収まる
- 前提 2: 既存の追従のテスト（`test_session.py` の 8 件）は、宣言に `follow_branch: true` を足すだけで
  期待値を変えずに通す（AC7）
- 前提 3: このリポジトリの `.ndf/worktree.json` には `follow_branch` を書かない（要求の前提 4）
- 前提 4: `wt_follow_enabled` は git を呼ばず、宣言の JSON だけで判定する（`wt_follow_target` と同じ置き方）

## 受け入れ条件

**AC1〜AC10 と AC30〜AC31 は [issue-495-610-requirements.md](issue-495-610-requirements.md) の
「受け入れ条件（#610）」と「受け入れ条件（両方）」にある。** ここでは写さない。タスクごとに、その
タスクが満たす条件を指す。

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 宣言の形 | 最上位に `follow_branch`（boolean、既定 `false`）を足す | 追加のみ。`version` は上げない |
| 開始時の hook の出力 | 追従しないとき、追従の案内が出なくなる | 出力の形（JSON / 平文 / injectSteps）は変えない |
| `wt_follow_target` / `follow_to` | 変えない | — |

## 修正対象

- `plugins/ndf/scripts/lib/worktree-common.sh`（`wt_follow_enabled` の新設だけ）
- `plugins/ndf/scripts/worktree-session.sh`（追従を有効なときだけ判定する）
- `plugins/ndf/scripts/worktree-setup.sh`（`init` の完了の文言）
- `plugins/ndf/skills/worktree/SKILL.md`（手順 0 の完了の説明、「主ディレクトリのブランチ」）
- `plugins/ndf/skills/worktree/references/declaration.md`（`follow_branch` の節）
- `plugins/ndf/skills/worktree/schemas/worktree.schema.json`（`follow_branch` と `base_branch` の説明）
- `plugins/ndf/skills/worktree/tests/test_session.py`
- `plugins/ndf/skills/worktree/tests/test_follow_target.py`（`wt_follow_enabled` の単体）
- `plugins/ndf/skills/development-workflow/SKILL.md`（`base_branch` の説明の「追従先」）
- `plugins/ndf/README.md`（hook の表）
- `AGENTS.md`（Git 運用ルールの「主ディレクトリの追従先」）
- `KIRO.md`（`agentSpawn` の表）
- 生成物（`bash scripts/build-runtime-plugins.sh` で同期する各ランタイムの写し）

## タスク分解

### Task 1: `wt_follow_enabled` を共通層へ足す

- **対象ファイル:** `plugins/ndf/scripts/lib/worktree-common.sh`、`plugins/ndf/skills/worktree/tests/test_follow_target.py`
- **変更内容:** 宣言の JSON を引数に取り、`.follow_branch == true` なら 0、それ以外（`false` / `"true"` /
  `1` / `null` / 項目なし / 空の入力）は 1 を返す関数を `wt_follow_target` の隣に置く。git を呼ばない
- **満たす受け入れ条件:** AC5（判定の側）
- **進め方:** 6 値でパラメータ化した失敗するテスト → jq 1 回の判定 → 整理。jq の `== true` が
  `"true"` / `1` に対して false になることを先に実行して確かめる

### Task 2: 開始時の hook が既定で HEAD を動かさない

- **対象ファイル:** `plugins/ndf/scripts/worktree-session.sh`、`plugins/ndf/skills/worktree/tests/test_session.py`
- **変更内容:** `AT_START` の分岐で未コミット変更を数えた後、`wt_follow_enabled "$DECLARATION"` が
  0 のときだけ `wt_dev_worktrees` と `wt_follow_target` を呼ぶ。無効なら `DECISION` を空のままにし、
  `case` はどの枝にも入らない（設計の「処理の流れ」）。冒頭の注記と未コミット変更の案内の文
  「変更がある間は稼働中の作業ツリーへ追従しません」を、既定で追従しないことと矛盾しない形へ直す
- **満たす受け入れ条件:** AC1・AC2・AC3・AC4・AC5・AC6・AC8・AC9
- **進め方:** 失敗するテスト（AC1〜AC6 は変更前に落ちる。AC8・AC9 は既存の形を `follow_branch` の
  有無でパラメータ化）→ 分岐の追加 → 整理。AC6 は origin の URL を到達できないパスへ変えた木で
  `.git/FETCH_HEAD` が無いことを見る。AC3 は 1 つのテストで作業ツリーを 4 通りに変えて 4 回実行し、
  `git reflog` の `checkout:` の行数の増分が 0 であることを見る

### Task 3: `follow_branch: true` で現行の追従が変わらない

- **対象ファイル:** `plugins/ndf/skills/worktree/tests/test_session.py`
- **変更内容:** 既存の追従のテスト 8 件（1 個へ detach、2 個で起点へ、未コミット変更で止まる、
  レビュー用を数えない、起点の取得 4 件）の宣言に `follow_branch: true` を足す。期待値は変えない
- **満たす受け入れ条件:** AC7
- **進め方:** Task 2 の後に落ちている 8 件を、宣言だけを変えて通す。`declared()` に引数を足して
  `follow_branch` を書き分ける

### Task 4: 文書 8 か所を「既定で追従しない」へ直す

- **対象ファイル:** 修正対象の文書 8 ファイル（`worktree-setup.sh` の `init` の完了の文言を含む）
- **変更内容:** 「既定と有効にする方法」の 6 か所は、既定で追従しないことと `follow_branch: true` で
  有効にすることを書く。「追従先の説明」の 2 か所（`AGENTS.md`、`development-workflow`）は、
  `base_branch` が追従先になるのを `follow_branch: true` のときに限る。スキーマに `follow_branch`
  を足す
- **満たす受け入れ条件:** AC10
- **進め方:** 8 ファイルを並べた `grep -L follow_branch` と `grep -n "ブランチ追従"` が何も出力しない
  ことを確かめる（手動）。テスト駆動は適用しない（文書の中身はレビューで見る）

### Task 5: 全体テストと生成物の同期

- **対象ファイル:** 生成物
- **変更内容:** `bash scripts/build-runtime-plugins.sh` で各ランタイムの写しを同期する
- **満たす受け入れ条件:** AC30・AC31
- **進め方:** `uv run --with pytest pytest scripts/tests plugins/ndf -q`、
  `bash scripts/build-runtime-plugins.sh --check`、`claude plugin validate .`

## 影響範囲

- 追従を前提にしていた利用者: 既定で追従しなくなる。使うには `follow_branch: true` を書く
- 開始時の hook の実行時間: 既定では作業ツリーの一覧・checkout・origin への問い合わせが消える
- `worktree-guard.sh` / `localenv` / `testenv` / `check-pr-base.sh`: 変わらない（`follow_branch` を読まない）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `worktree-session.sh` は入口で、判定が共通層に無い分岐（`case "$DECISION"`）を持つ。追従の分岐を足すと入口が肥大する | 判定は `wt_follow_enabled` へ置き、入口は真偽で `DECISION` を作るかどうかだけを分ける。触る範囲が 1 か所に閉じるため、実装の後の構造改善（cross-refactoring）で足りる |
| `worktree-common.sh` を並行する担当 B が触る | 触るのは `wt_follow_enabled` の追加だけ。`wt_follow_target` の直後へ置き、取り込み時の衝突を位置だけに留める |
| 未コミット変更の案内の文が「追従しません」と書いており、既定で追従しないと文が空回りする | 案内の文を追従の有無に依存しない形へ直す。AC8 のテストは件数と一覧だけを見る |

## 切り戻し手順

- 12 ファイルと生成物の変更を revert する。永続データも型も持たないため、他に戻すものは無い。
  revert すると追従が既定で再び動く

## 完了の定義

- [ ] AC1〜AC10・AC30・AC31 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `claude plugin validate .` が終了コード 0 で終わる
- [ ] `bash scripts/build-runtime-plugins.sh --check` が差分なしで終わる
- [ ] cross-refactoring が収束し、最後の cross-review が `APPROVE` になる
