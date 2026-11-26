# #4580: 開発の流れをプロジェクトの設定で選べるようにする

## 関連リンク

- 要求仕様: [issue-4580-requirements.md](./issue-4580-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4580 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4580-design.md](./issue-4580-design.md)、詳細設計書: [issue-4580-design-detail.md](./issue-4580-design-detail.md)
- モード: `standard`（機能追加で、公開インタフェース（`release-steps`・`records close-check`・`progress new` の引数と結果）と設定のスキーマを変えるため）

## 出す順序

設計書の決定 9 のとおり、2 回に分けてリリースする。守る順序は、公開の順（2 回目は 1 回目の後に出す）と、共有するファイルを main へ入れる順の 2 つだけで、着手はこの順を待たない。1 回目の承認や CI を待つ間も、2 回目の単位の実装と検証を進めるためである（Value 5 の「並べられる作業は並べて進め」）。

| リリース | 含む単位 | 満たす受け入れ条件 |
| --- | --- | --- |
| 1 回目 | U1〜U5 | 1・2・6・7・8・9・13 |
| 2 回目 | U6〜U8 | 3・4・5・10・11・12 |

単位ごとの着手の前提と、main へ入れる前提は次のとおりである。#4513 は https://github.com/devbasex/ai-plugins/issues/4513 （バージョン番号の無いリポジトリでデプロイの工程が終わらない不具合）である。

| 単位 | 着手の前提 | main へ入れる前提 | 理由 |
| --- | --- | --- | --- |
| U1 | 無い | 無い | #4513 が触る記録のスキーマ（`release-verification.schema.json`）と、`release`・`release-verification` の手順書を触らない |
| U2〜U5 | #4513 の直しがリリースされている | 前の単位が main に入っている（依存の向きは「レビューの単位」） | #4513 と同じ記録のスキーマと手順書を触る |
| U6 | U1 が main に入っている | 1 回目のリリースが公開されている | U1 の `progress/flow.py` と `project-workflow.schema.json` に足す。U2〜U5 と重なるのは、U4 が配布先の `paths`・`observe` を足す設定のスキーマ 2 つ（`ndf-settings/project.schema.json` と写し）だけで、触る位置（`workflow` と `release.deliveries`）が違う。そのため、1 回目の承認や CI を待つ間に実装とテストを進め、main へ入れる前に、1 回目を出した後の main へ取り込み直して CI を通す |
| U7・U8 | 前の単位の作業ブランチ（U6、U7） | 前の単位が main に入っている | 同じ `flow.py` と流れの文書の正本を順に足す |

1 回目のリリースの後に、要求仕様の「リリース後の確認」のとおり、今あるリリースの設定だけを使って受け入れ条件 2・9 を確かめる。2 回目では `workflow.kind` を書いたときの振る舞いを確かめる。

## 修正対象

詳細設計書の「地図」の表のとおりである。単位ごとの対象は各タスクの「対象ファイル」に引き写す。手順書と文書（`development-workflow`・`progress-tracking`・`release`・`release-verification` の SKILL.md と references、用語集）は設計書の「設計の結果」の触るファイルから引き写す。

## タスク分解

### 1 回目のリリース

#### Task 1: 流れの設定が無いときの振る舞いを縛る退行テスト

- **対象ファイル:** `plugins/ndf/tests/domains/records/test_close.py`、`plugins/ndf/tests/domains/records/test_cli.py`、`plugins/ndf/tests/domains/release/test_release_record.py`
- **変更内容:** 流れの設定が無いリポジトリで、`close-check` の判定（`--require` の有無・`--verification` の不合格と保留）と、バージョン付きの `release-steps record`・`records`・`verification-record` の結果が今と同じであることを縛る。工程の要否の退行テストは 2 回目の Task 9 に置く（1 回目は `progress new` を触らない）
- **満たす受け入れ条件:** 1
- **進め方:** 今の振る舞いで通るテストを先に書く（機能追加の最初のタスク）。以降のタスクで落ちたら退行である

#### Task 2: 閉じる条件を理由つきで外す

- **対象ファイル:** `plugins/ndf/src/ndf/progress/flow.py`（新設。`Flow`・`CloseRule`・`read_flow`・`close_rules` だけ）、`plugins/ndf/contracts/schemas/project-workflow.schema.json`、`plugins/ndf/contracts/schemas/ndf-settings/project.schema.json`、`plugins/ndf/skills/development-workflow/schemas/project.schema.json`、`plugins/ndf/src/ndf/domains/records/close_decide.py`（`Verdict`・`verdict`・`conditions_of`）、`plugins/ndf/src/ndf/domains/records/cli.py`（`_close_check`）、`plugins/ndf/contracts/schemas/io/records.result.schema.json`、`plugins/ndf/tests/progress/test_flow.py`（新設）、`test_close.py`、`test_cli.py`
- **変更内容:** `workflow.close` の形（理由の要る `required: false`）と、`close-check` の `outputs.conditions`・`verified`。外しても渡した記録の不合格では閉じない
- **満たす受け入れ条件:** 6・7・8（`--verification` の経路）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

#### Task 3: 流れの設定を NDF が書き換えない

- **対象ファイル:** `plugins/ndf/tests/domains/project/test_project_cli.py` ほか、`project write` と `branch-strategy` の書き出しのテスト
- **変更内容:** `close` を持つ `workflow` を、`project write` と `branch-strategy` の書き出しが保持することを縛る。落ちれば書き出しの側を直す。2 回目の `kind`・`modes` は Task 9 で同じテストに足す
- **満たす受け入れ条件:** 13
- **進め方:** 失敗するテスト → 通す最小実装

#### Task 4: 閉じる手順の読み取り（権限を照会できない書き手で止まる）

- **対象ファイル:** `plugins/ndf/src/ndf/adapters/github.py`（`Authored.comment_id`・`PermissionUnknown`・`permission_strict`・`trusts_strict`）、`plugins/ndf/src/ndf/domains/release/record_decide.py`（`Record` の項目・`parse_record`・`Correction`・`render_correction`・`parse_correction`・`authoritative_targets`・`representative`・`decisive_row`・`to_test`・`complete_of`）、`plugins/ndf/src/ndf/domains/release/record.py`（`Selection`・`_candidates`・`read_release`・`pr_body_only`）、`plugins/ndf/src/ndf/domains/release/cli.py`（`cmd_records`）、`plugins/ndf/contracts/schemas/io/release-steps.args.schema.json`（`records` の引数）、`plugins/ndf/tests/adapters/test_github.py`、`test_release_record.py`
- **変更内容:** 設計書の「閉じる手順の読み取り」の手順 1〜6 と、`--version`・`--release`・`--unit`・`--bodies`・`--issues`・`--pr-body-only`
- **満たす受け入れ条件:** 9（読む側）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

#### Task 5: バージョンの無いリリース記録の確かめ（純粋な判定）

- **対象ファイル:** `plugins/ndf/src/ndf/domains/release/record_decide.py`（`Push`・`value_matches`・`production_deliveries`・`glob_matches`・`targets_of`・`push_of`・`value_commit_conflict`・`value_commit`・`has_unversioned`・`confirm`・`json_pointer`）、`test_release_record.py`
- **変更内容:** 確かめ 2・3・5・6・8 の判定と、`push` の決め方の手順 2。GitHub・git・HTTP を呼ばない
- **満たす受け入れ条件:** 2・9（書く側の判定）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

#### Task 6: バージョンの無いリリース記録を書く・訂正する

- **対象ファイル:** `plugins/ndf/src/ndf/adapters/github.py`（`branch_activity`・`workflow_runs`・`delete_comment`）、`plugins/ndf/src/ndf/domains/release/record.py`（`Unversioned`・`Outcome`・`write_unversioned`・`correct_targets`・`_landed_commit`・`_changed_paths`・`_find_push`・`_confirm_delivery`・`_command_result`・`_observe`・`_write_one`）、`plugins/ndf/src/ndf/domains/release/cli.py`（`cmd_record`・`_record_args`）、`plugins/ndf/contracts/schemas/io/release-steps.args.schema.json`（`record` の引数）、`plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` と写し（配布先の `paths`・`observe`）、`test_github.py`、`test_release_record.py`
- **変更内容:** 確かめ 0〜8・書く順の手順 1〜4・`record` の終わり方の表・対象の一覧の訂正
- **満たす受け入れ条件:** 2・9（書く側）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。並行した書き込みは偽の GitHub でコメントの順を作って確かめ、実際の待ちと子プロセスを使わない（`tdd-cycle`）

#### Task 7: テストの記録を配布先ごとに書き、`close-check --records` で閉じる

- **対象ファイル:** `plugins/ndf/contracts/schemas/release-verification.schema.json`、`plugins/ndf/src/ndf/domains/release/record.py`（`write_verification`）、`plugins/ndf/src/ndf/domains/release/cli.py`（`cmd_verification_record`）、`plugins/ndf/src/ndf/domains/records/close_decide.py`（`records_verdict`）、`plugins/ndf/src/ndf/domains/records/cli.py`（`_records_view`・`_close_check`・`_add_close_check`）、`plugins/ndf/contracts/schemas/io/records.result.schema.json`、`plugins/ndf/contracts/workflow/context.schema.json`、`test_release_record.py`、`test_close.py`、`test_cli.py`
- **変更内容:** `environments`・`unit`・`retest_reason` を持つテストの記録と、`close-check` の `--records`・`release.complete`・`missing`・`unconfirmed`。`none` で `--verification` を断る
- **満たす受け入れ条件:** 6・8（`--records` の経路）・9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

#### Task 8: 1 回目の手順書

- **対象ファイル:** `plugins/ndf/skills/progress-tracking/SKILL.md`（「閉じる条件」と「スプリントを閉じる」の手順 1〜3）、`plugins/ndf/skills/release/SKILL.md`、`plugins/ndf/skills/release/references/release-steps.md`、`plugins/ndf/skills/release-verification/SKILL.md`、`plugins/ndf/skills/development-workflow/references/parallel.md`
- **変更内容:** 閉じる手順を `records --bodies` と `close-check --records` に替え、外した条件を閉じる報告に転記する。バージョンの無いリポジトリで `record` を配布先ごとに打つ時点、テストの担当と引き継ぎ、`to_test` の引き方、オーガナイザーと conductor の分担を書く
- **満たす受け入れ条件:** 2・6・7・9
- **進め方:** 手順書の変更なのでテスト駆動を適用しない（文言を照合するテストは書かない）。書いたコマンドは手元の偽のリポジトリで 1 回打って結果を確かめる

### 2 回目のリリース

#### Task 9: 種類の既定とモードごとの要否でスプリントを作る

- **対象ファイル:** `plugins/ndf/contracts/data/workflow-kinds.json`（新設）、3 つのスキーマ（`kind`・`modes`）、`plugins/ndf/src/ndf/progress/flow.py`（`FlowItem`・`kind_defaults`・`merged_stages`・`creation_errors`・`input_missing`）、`plugins/ndf/src/ndf/progress/command.py`（`_flow_declared`・`_prepare_new`、`_declared` の削除）、`test_flow.py`、`plugins/ndf/tests/progress/test_command.py`、Task 3 のテスト
- **変更内容:** 3 層を重ねて `resolve_enabled` へ渡す。知らない種類・無い名前・承認ゲートの無効化・入力を作る工程の無効化でステートマシンを作らない。最初に、流れの設定が無いときの全モードの enablement が今と同じことを縛る退行テストを書く
- **満たす受け入れ条件:** 1（工程の要否）・3・4・5・11・13
- **進め方:** 退行テスト → 失敗するテスト → 通す最小実装 → 整理

#### Task 10: 設定の確かめで食い違いを示す

- **対象ファイル:** `plugins/ndf/src/ndf/progress/flow.py`（`conflicts`）、`plugins/ndf/src/ndf/domains/project/cli.py`（`_flow_items`・`_check`）、`plugins/ndf/contracts/schemas/io/project.result.schema.json`、`plugins/ndf/skills/development-workflow/SKILL.md`（起動時に食い違いを利用者へ伝える）、`plugins/ndf/tests/domains/project/test_project_cli.py`
- **変更内容:** `flow_close_conflict`・`flow_version_conflict` と、作成を止める誤りを `items` に並べる。終了コードは変えない
- **満たす受け入れ条件:** 10
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

#### Task 11: 種類ごとの流れの文書を生成する

- **対象ファイル:** `scripts/contracts_lib/project_flow_doc.py`（新設）、`scripts/check-ndf-contracts.py`、`scripts/tests/test_check_ndf_contracts.py`、`plugins/ndf/skills/development-workflow/references/project-flow.md`（新設）、`plugins/ndf/skills/progress-tracking/SKILL.md`（「閉じる条件」に外せる 2 つの条件を書き足す）、`docs/glossary/glossary.json`・`docs/glossary.md`・`docs/glossary/ndf-workflow.md`（「流れの設定」）
- **変更内容:** 決定 10 の 4 つの表と「閉じる条件」の抜き出しを生成し、`--write` で書き出す。引数なしで違いを違反にする
- **満たす受け入れ条件:** 12
- **進め方:** 失敗するテスト → 通す最小実装 → 生成した文書をコミット

## レビューの単位

行数は追加と削除の和で、`git diff --shortstat <ベース>...HEAD -- . ':(exclude)uv.lock'` で数える。生成した `project-flow.md` の生成の範囲は数えない（U8 で `':(exclude)plugins/ndf/skills/development-workflow/references/project-flow.md'` を足す）。

| 単位 | リリース | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- | --- |
| U1 閉じる条件の除外 | 1 回目 | Task 1・2・3 | 1・6・7・8・13 | 約 450 | （実装の後に書き足す） |
| U2 閉じる手順の読み取り | 1 回目 | Task 4 | 9 | 約 600 | |
| U3 リリース記録の判定 | 1 回目 | Task 5 | 2・9 | 約 450 | |
| U4 リリース記録の書き込みと訂正 | 1 回目 | Task 6 | 2・9 | 約 800 | |
| U5 テストの記録と閉じる判定・手順書 | 1 回目 | Task 7・8 | 2・6・7・8・9 | 約 650 | |
| U6 種類の既定とモードごとの要否 | 2 回目 | Task 9 | 1・3・4・5・11・13 | 約 550 | |
| U7 設定の確かめ | 2 回目 | Task 10 | 10 | 約 300 | |
| U8 流れの文書 | 2 回目 | Task 11 | 12 | 約 350 | |

依存の向きは U1 → U2 → U3 → U4 → U5（U2 の `Record` の項目と `decisive_row` を U3〜U5 が使う）、U1 → U6 → U7 → U8（U6 は U1 の `flow.py` に足す）である。どの単位も単独でテストが通り、終えた後も今の振る舞いを保つ（足した引数と項目は任意）。U4 は 400 行の目安を超えるが、確かめ 0〜8 と書く順は 1 つの `record` の呼び出しの中で互いを前提にしており、途中で切ると単位が単独で読めないため分けない。実装の途中で 1,000 行を超えそうなら、訂正（`correct_targets`）を別の単位へ分ける。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るファイルの行数は `record.py` 323・`record_decide.py` 234・`close_decide.py` 72・`records/cli.py` 549・`release/cli.py` 466・`progress/command.py` 987・`adapters/github.py` 1000。同じファイルを触るタスクの割合は、最多の `record.py` で 11 タスク中 3。テストは `test_release_record.py` 27 件・`test_close.py` 9 件・`test_command.py` 84 件・`test_github.py` 70 件。大きい 2 つ（`command.py`・`github.py`）は 1 つか 2 つのタスクが関数を足すだけで、中の構造を変えない |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `record.py` の読み方が 2 つ（今の `read` と `read_release`）並ぶこと、`record.py` の大きさ（見込み約 800 行）、`close_decide.verdict` と `records_verdict` の判定の重なり | U2・U4・U5 が同じファイルを触り、同じ形の処理（信用する記録の選び方・行の判定）が単位をまたいで増えるため。1 回目と 2 回目のそれぞれの終わりに候補として渡す |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #4513 の直しより先に着手すると、記録のスキーマと手順書が衝突する | U2 の着手の前に #4513 のリリースを確かめる。まだなら U1 と、U1 の後の U6 を進め、U2〜U5 は待つ（「出す順序」の単位ごとの前提） |
| GitHub のアクティビティに push が載るまでの遅れの大きさが分からない（設計書の「未確認のまま残ること」と同じ） | `record` は止まって打ち直しを示す。1 回目のリリースの後の確認で遅れを測り、遅すぎれば待ちを足す課題を起票する |
| 配布先の宣言の `result.command`（コマンドで CD の結果を読む形）の配布先が、記録できずに課題を閉じられなくなる | U4 で `result.workflow` と同じく `result.command` も読む（設計書の「CD の結果の照会」。今の契約の cd-setup.md の終了コードのまま）。1 回目のリリースの条件に、`result.command` の配布先で `cd` の記録が書けることを入れ、Task 6 のテストで偽のコマンドの終了コード 0・75・1 を確かめる |
| 閉じる手順の変更で、今動いているスプリントの閉じ方が変わる | 流れの設定が無くバージョンを持つリポジトリの判定は Task 1 の退行テストで縛る |

## 切り戻し手順

- 単位ごとに revert できる。足した引数と記録の項目は任意で、今の記録を書き換えないため、revert しても既にある記録は読める
- 1 回目のリリースの後に revert すると、バージョンの無いリポジトリで書いた記録（`targets` を持つ）は、前の版の `records` が読み飛ばす。課題は人が確かめて手で閉じる

## 完了の定義

- [ ] 受け入れ条件 1〜13 をすべて満たし、条件ごとに検証手段（設計書の「テスト設計」の行と、要求仕様の「リリース後の確認」）と結果が対応している
- [ ] 1 回目と 2 回目のそれぞれで、`standard` の検証の段階（`quality-gates`）を通している
- [ ] 「レビューの単位」の実際の行数を書き足している
