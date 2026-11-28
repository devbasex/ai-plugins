# 4735: 要求仕様書を要求の正にし、Issue の本文を報告のまま残す

## 関連リンク

- 要求仕様: [issue-4735-requirements.md](./issue-4735-requirements.md)。受け入れ条件は番号で指す
- 設計書: [issue-4735-design.md](./issue-4735-design.md)
- 詳細設計書: [issue-4735-design-detail.md](./issue-4735-design-detail.md)。関数の仕様・異常系・単体テストの観点はこの文書を引く
- モード: `standard`（公開インタフェースが変わる機能追加）

## 修正対象

パスの起点は、断りが無ければ `plugins/ndf/src/ndf/` である。`*` は新設を表す。関数ごとの中身は詳細設計書の「地図」と各節にある。

| ファイル | 変える中身 | Task |
| --- | --- | --- |
| `core/issue_body.py` *、`progress/records.py`、`domains/records/cleanup_stage.py` | Issue の本文の書き換えを共通にする | 1 |
| `core/issue_docs.py`、`core/requirements_decide.py` *、`domains/docrebuild/drive.py` | 要求仕様書の名前・形・解析 | 2 |
| `adapters/git.py`、`core/requirements_history.py` * | 履歴から最終版を読む | 3 |
| `contracts/worker_paths.py`、`core/requirements_docs.py` * | 読み先を順にたどる読み出し | 4 |
| `domains/records/requirements_cmd.py` *、`domains/records/cli.py`、`core/plan/review_units_decide.py` | `ndf records requirements show`・`stamp` | 5 |
| `domains/materials/collect.py`、`domains/materials/cli.py` | 種類と影響を要求仕様書から読む | 6 |
| `progress/requirements_stage.py` *、`progress/command.py`、`contracts/internal.py` | 実行の印と要求の工程の出力 | 7 |
| `progress/requirements_stage.py`、`contracts/worker_paths.py` | 要求仕様書の移送 | 8 |
| `core/plan/inputs_decide.py`、`domains/loop/acceptance_decide.py` | 実行と要求仕様書の組ごとの確認の表 | 9 |
| `domains/loop/acceptance.py`、`domains/loop/cli.py`、`core/worker.py` | 確認の記録を要求仕様書と実行の印の形にする | 10 |
| `core/plan/review_units_decide.py`、`core/route/__init__.py`、`progress/units_invoke.py`、`progress/invoke.py` | 実装の単位の条件の版と突き合わせ | 11 |
| `progress/issue_values.py`、`domains/records/requirements_cmd.py`、`core/route/__init__.py`、`plugins/ndf/contracts/templates/` | PR の本文を要求仕様書の参照にする | 12 |
| `core/run_trailer_decide.py` *、`progress/records.py` | スプリント PR の実行の行 | 13 |
| `domains/release/acceptance_record_decide.py` *、`acceptance_record.py` *、`record.py`、`record_decide.py`、`record_unversioned.py`、`cli.py` | リリースの対応の記録を書く | 14 |
| `core/requirements_history.py`、`adapters/git.py`、`domains/release/record_read.py`、`domains/loop/acceptance_release.py` *、`domains/records/requirements_cmd.py` | 配布した版の読み出し | 15 |
| `domains/loop/acceptance_release.py`、`domains/records/close_decide.py` | 確認の表の整理と、版を見る閉じる判定 | 16 |
| `domains/records/close_release.py` *、`domains/records/cli.py` | `ndf records close-release` | 17 |
| `plugins/ndf/skills/`、`docs/glossary*`、`plugins/ndf/docs/contracts/`、`plugins/ndf/contracts/worker/` | 手順書・用語集・コントラクトの説明 | 18 |

## タスク分解

どのタスクにも当たる決まりは次のとおりである。

- コマンド・引数・結果 JSON を変えるタスクは、同じタスクで `plugins/ndf/contracts/schemas/` の対応するスキーマと `plugins/ndf/docs/contracts/commands.md` を直す
- 保存先やファイルを足すタスクは、同じタスクで `plugins/ndf/contracts/domains/` と `plugins/ndf/docs/contracts/files.md` を直す
- テストは設計書の「テスト設計」と詳細設計書の「単体テストの観点」から書く。テストのパスは `plugins/ndf/tests/` の下である。設計書の触るファイルに無い既存のテストファイルは、変える関数の観点を足すために触る
- 1 ファイルの上限は 1,000 行（`checks/rules.json` の `structure.max_lines`）、関数は 60 行である
- 各タスクの終わりに、変えた範囲のテストと `ndf check` を通す。手順書の行が原因で `ndf check` が落ちるときは、その行を同じタスクで直す

### Task 1: Issue の本文の書き換えを共通にする

- **対象ファイル:** `core/issue_body.py`（新設）、`progress/records.py`、`domains/records/cleanup_stage.py`、`domains/loop/acceptance.py`（ロックのパスの移動）、テスト `core/test_issue_body.py`（新設）
- **変更内容:** 先に、進行とクリーンアップの記録の今の振る舞いを退行テストで固定する。次に `BodyStore`・`GitHubBodies`・`lock_path`・`rewrite` を `core` に置き、本文を書く 3 つの処理をそこへ寄せる。ロックのパスは変えない
- **満たす受け入れ条件:** 10
- **進め方:** 退行テスト → 失敗するテスト → 通す最小実装 → 整理

### Task 2: 要求仕様書の名前・形・解析

- **対象ファイル:** `core/issue_docs.py`、`core/requirements_decide.py`（新設）、`domains/docrebuild/drive.py`、テスト `core/test_issue_docs.py`・`core/test_requirements_docs.py`（新設）・`domains/docrebuild/test_docrebuild_run.py`
- **変更内容:** パスの形、元の Issue の行、受け入れ条件の版、型と例外を作る。ドキュメント再構成は、番号を複数持つ要求仕様書を設計書と組にする
- **満たす受け入れ条件:** 2・9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: 履歴から要求仕様書の最終版を読む

- **対象ファイル:** `adapters/git.py`（`name_status_log`）、`core/requirements_history.py`（新設。`Touch`・`Deleted`・`final_version`・`branch_deleted`）、テスト `core/test_requirements_history.py`（新設）・`adapters/test_git.py`
- **変更内容:** 消えた要求仕様書の最終版を、`git log` 1 回の結果から選ぶ。`release_docs` は Task 15 で作る
- **満たす受け入れ条件:** 7・8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。`git log` の引数は、書く前に実行して出力の形を確かめる

### Task 4: 読み先を順にたどる読み出し

- **対象ファイル:** `contracts/worker_paths.py`（`read_bytes`・`has_acl`）、`adapters/git.py`（`tree_entries`）、`core/requirements_docs.py`（新設）、テスト `core/test_requirements_docs.py`・`contracts/test_worker_paths.py`
- **変更内容:** 作業場所 → このブランチで消した版 → 起点コミットの順に読む `lookup`・`find` を作る
- **満たす受け入れ条件:** 7・8・9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 5: 要求仕様書を読むコマンドと版を書くコマンド

- **対象ファイル:** `domains/records/requirements_cmd.py`（新設）、`domains/records/cli.py`、`core/plan/review_units_decide.py`（`criteria_versions`・`stamped`）、`contracts/worker_paths.py`（`owner_only`）、テスト `domains/records/test_requirements_cmd.py`（新設）・`core/plan/test_review_units.py`
- **変更内容:** `ndf records requirements show`（`--ref`・`--out` まで）と `stamp` を足す。`show --release` は Task 15 で足す
- **満たす受け入れ条件:** 7・8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 6: モードの判定の材料を要求仕様書から読む

- **対象ファイル:** `domains/materials/collect.py`、`domains/materials/cli.py`、テスト `domains/materials/test_materials_cli.py`
- **変更内容:** 種類と公開インタフェースを要求仕様書から読み、無ければ `記載なし` にする。候補は報告の本文と要求仕様書の両方から拾う
- **満たす受け入れ条件:** 6・8・9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 7: 実行の印と要求の工程の出力

- **対象ファイル:** `progress/requirements_stage.py`（新設）、`progress/command.py`、`contracts/internal.py`、`plugins/ndf/contracts/workflow/`（`context.schema.json`・`stages/requirements.json`・`progress.json`）、`plugins/ndf/contracts/schemas/internal/common.schema.json`、テスト `progress/test_requirements_stage.py`（新設）・`progress/test_definition_migration.py`
- **変更内容:** `run_mark`・`context_run`・`recorded_docs`・`creation_finish`・`finish_requirements`・`_snapshot_v2` を作る。`progress/command.py` は 1,000 行に達しているので、`_requirements` を移した分より多くの行を足さない
- **満たす受け入れ条件:** 2・7
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 8: 要求仕様書の移送

- **対象ファイル:** `progress/requirements_stage.py`、`progress/command.py`（サブコマンドの登録）、`contracts/worker_paths.py`（`create_file`）、`plugins/ndf/contracts/workflow/stages/worktree.json`・`progress.json`、テスト `progress/test_requirements_stage.py`
- **変更内容:** `ndf progress move-requirements` を足す。中断からの再開は詳細設計の `resume_step` の表のとおりにする
- **満たす受け入れ条件:** 2
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 9: 実行と要求仕様書の組ごとの確認の表

- **対象ファイル:** `core/plan/inputs_decide.py`、`domains/loop/acceptance_decide.py`、テスト `core/plan/test_inputs_check_rows.py`・`domains/loop/test_loop_acceptance.py`
- **変更内容:** `TableRow`・`RunTable`・`read_tables`・`counted`・`table_text`・`table_digest`・`without_tables` を足す。今の関数は残し、呼び出し元を移す Task 10 で消す
- **満たす受け入れ条件:** 7・9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 10: 確認の記録のコマンドを新しい形にする

- **対象ファイル:** `domains/loop/acceptance.py`、`acceptance_decide.py`、`cli.py`、`core/worker.py`（`ACCEPTANCE_RULES`）、テスト `domains/loop/test_loop_acceptance.py`
- **変更内容:** `record`・`show`・`check` が条件を要求仕様書から読み、`--run` と条件一覧の版を受ける。`new-run` を足し、今の表の関数を消す
- **満たす受け入れ条件:** 7・9・10
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 11: 実装の単位の条件の版と突き合わせ

- **対象ファイル:** `core/plan/review_units_decide.py`、`core/route/__init__.py`、`progress/units_invoke.py`、`progress/invoke.py`、テスト `core/plan/test_review_units.py`・`progress/test_units_run.py`・`progress/test_units_invoke.py`
- **変更内容:** 条件の数と版を要求仕様書から作り、単位の `check` に `--run`・`--mode`・版を渡す。スプリント PR の前に全条件を照らす。`core/route/__init__.py` は 999 行なので、足して上限を超える場合は受け入れ条件の引数を扱う関数を同じディレクトリの別モジュールへ移す（上限を守るために足すファイル）
- **満たす受け入れ条件:** 7・8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。Task 10 と同じ単位で出す（`check` の引数と呼び出し元がそろわないと自動の実装が止まる）

### Task 12: PR の本文を要求仕様書の参照にする

- **対象ファイル:** `progress/issue_values.py`、`domains/records/requirements_cmd.py`（`pr-body`）、`core/route/__init__.py`（`_by_body_from`）、`plugins/ndf/contracts/templates/`（`params.json`・`design.json`・`impl.json`）、テスト `domains/records/test_requirements_cmd.py`・`progress/test_sprint_templates.py`・`progress/test_invoke_issue_copies.py`
- **変更内容:** `pr` のステップの直前に、push した head の要求仕様書から本文を作り直す。行数の上限の扱いは Task 11 と同じ
- **満たす受け入れ条件:** 8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 13: スプリント PR に実行の行を足す

- **対象ファイル:** `core/run_trailer_decide.py`（新設）、`progress/records.py`、テスト `core/test_run_trailer.py`（新設。行の形を書く側と読む側の往復を確かめるために足す）
- **変更内容:** 「通した工程」に対象の Issue・確認の表・実行・モードの行を足し、読み戻す `parse_trailer` を作る
- **満たす受け入れ条件:** 7・9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 14: リリースの対応の記録を書く

- **対象ファイル:** `domains/release/acceptance_record_decide.py`（新設）、`acceptance_record.py`（新設）、`record.py`、`record_decide.py`、`record_unversioned.py`、`cli.py`、`plugins/ndf/contracts/schemas/release-verification.schema.json`、テスト `domains/release/test_acceptance_record.py`（新設）
- **変更内容:** 対応の記録の組み立て・確認対象の版・有効な記録の選び方を作り、リリース記録に `acceptance` を入れる。`record-acceptance` を足す（`--reason` の訂正で版を比べる部分は Task 15 の `release_docs` の後）
- **満たす受け入れ条件:** 7・9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 15: 配布した版を読み出す

- **対象ファイル:** `core/requirements_history.py`（`release_docs`）、`adapters/git.py`（`missing_objects`・`fetch_commits`）、`domains/release/record_read.py`、`acceptance_record.py`（訂正）、`domains/loop/acceptance_release.py`（新設。`show_release`）、`domains/loop/acceptance.py`（`show --release` の引数）、`domains/records/requirements_cmd.py`（`show --release`）、テスト `core/test_requirements_history.py`・`domains/release/test_acceptance_record.py`・`domains/loop/test_acceptance_release.py`（新設）
- **変更内容:** 確定仕様化とマージの後も、対応の記録が指すコミットと PR の head から要求と区分を読む
- **満たす受け入れ条件:** 7・8・9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 16: 確認の表の整理と、版を見る閉じる判定

- **対象ファイル:** `domains/loop/acceptance_release.py`（`prune`・`_prune_one`）、`domains/loop/acceptance.py`（`prune` の登録）、`domains/records/close_decide.py`（`current_rows`・`records_verdict`）、`domains/records/cli.py`（`--skipped-deploy`）、`domains/release/record.py`・`cli.py`（`verification-record --records`）、テスト `domains/loop/test_acceptance_release.py`・`domains/records/test_close.py`・`domains/release/test_release_record.py`
- **変更内容:** テストの行に確認対象の版を書き、版が今と同じ行だけで合否を出す。閉じた Issue の確認の表を退避して消す
- **満たす受け入れ条件:** 7・8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 17: 閉じて照合し直すコマンド

- **対象ファイル:** `domains/records/close_release.py`（新設）、`domains/records/cli.py`、`domains/records/close_decide.py`（`ClosedEntry`・`close_verdict`・`after_close`）、`domains/release/record_read.py`（`released_at`）、`plugins/ndf/contracts/domains/locations.json`・`ndf-files.json`、テスト `domains/records/test_close_release.py`（新設。GitHub を差し替えて中断と打ち直しを確かめるために足す）
- **変更内容:** `ndf records close-release` を足す。順は設計書の「閉じた後の照合し直し」のとおりにする
- **満たす受け入れ条件:** 8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 18: 手順書・起動指示・用語集を直す

- **対象ファイル:** 詳細設計の「手順書と用語集」と「worker の起動指示」の表にあるファイル（`plugins/ndf/skills/`、`plugins/ndf/contracts/worker/`、`docs/glossary*`、`plugins/ndf/docs/contracts/`）
- **変更内容:** 要求を Issue の本文へ書く手順と、一致を確かめる手順を消す。「進行中の作業」の手順を置く。用語集は `ndf records glossary render` で作り直す。Issue 4543 が先にマージされていれば、`requirements-design`・`design` の差分を取り込む
- **満たす受け入れ条件:** 1・2・3・4・5・6
- **進め方:** テスト駆動を適用しない（文書の変更で、`.md` の文言を照合するテストは書かない）。`python3 scripts/check-skill-frontmatter.py`・`ndf check`・`ndf records glossary meanings` で確かめる

## レビューの単位

表の順に実行する。後の単位は前の単位の成果を使う。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| body-rewrite | Task 1 | 無し | 約 350 | |
| doc-parse | Task 2 | 無し | 約 400 | |
| doc-history | Task 3 | 無し | 約 450 | |
| doc-lookup | Task 4 | 無し | 約 550 | |
| doc-commands | Task 5 | 無し | 約 500 | |
| materials | Task 6 | 無し | 約 200 | |
| run-output | Task 7 | 無し | 約 500 | |
| move | Task 8 | 無し | 約 600 | |
| check-table | Task 9 | 無し | 約 350 | |
| check-switch | Task 10・11 | 無し | 約 950 | |
| pr-body | Task 12・13 | 無し | 約 650 | |
| release-write | Task 14 | 無し | 約 900 | |
| release-read | Task 15 | 無し | 約 750 | |
| close-judge | Task 16 | 無し | 約 650 | |
| close-release | Task 17 | 無し | 約 600 | |
| handbooks | Task 18 | 無し | 約 600 | |

「満たす受け入れ条件」の欄をすべて「無し」にするのは、この実装を流す NDF が受け入れ条件を Issue の本文から数えるためである。この Issue の受け入れ条件は[要求仕様書](./issue-4735-requirements.md)だけが持ち、本文へはコピーしない。単位ごとの確認は流れず、10 件は完了判定で要求仕様書と照らす。単位と受け入れ条件の番号の対応は次のとおりである: body-rewrite=10、doc-parse=2,9、doc-history=7,8、doc-lookup=7〜9、doc-commands=7,8、materials=6,8,9、run-output=2,7、move=2、check-table=7,9、check-switch=7〜10、pr-body=7〜9、release-write=7,9、release-read=7〜9、close-judge=7,8、close-release=8、handbooks=1〜6。

行数は `git diff --shortstat <ベース>...HEAD` で数える。見込みが 400 行を超える単位は、1 つのモジュールや 1 つのコマンドをテストと一緒に作るので、目安を超える。`check-switch` は、分けるとコマンドと呼び出し元の引数がそろわない。実装の途中で 1,000 行を超えそうなら、その単位をタスクの中の関数の依存の向きで分け直す。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `progress/command.py` は 1000 行、`core/route/__init__.py` は 999 行、`core/worker.py` は 980 行だが、触るのは 18 タスク中それぞれ 2・2・1 タスクである。行の移し先は Task 7・11 の中で決まる。テストは `plugins/ndf/tests/progress/` と `core/` にある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | Issue の本文をまとめて読む照会（`progress/units_invoke.py` と `domains/loop/acceptance.py`）、信用する書き手の記録の読み出し（`domains/release/acceptance_record.py`・`record_read.py`・`domains/loop/acceptance_release.py`） | 層の向きと単位の分け方で、同じ形の処理が複数の単位にできる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| このスプリントを動かす導入済みの NDF（11.0.51）は、受け入れ条件を Issue の本文の `## 受け入れ条件` から読む。Issue 4735 の本文にその見出しは無く、単位の表の条件の番号が 0 件の本文に照らされて止まる | 実装の工程を始める前に、conductor が進め方を決める。本文は書き換えない前提で、要求仕様書の番号は変えない |
| 途中の単位では、手順書が古いコマンドの形のまま残る | 手順書は Task 18 でまとめて直す。利用者へ届くのはスプリントブランチをまとめてマージした後である |
| 行数の上限に近いファイルへの追加で `ndf check` が落ちる | Task 7・11・12 の変更内容のとおりに扱う |
| Issue 4543 が `requirements-design`・`design` の手順書を触る | 後になる側が差分を取り込む（Task 18） |

## 切り戻し手順

- コードと手順書は、スプリントのマージを revert すれば戻る
- 新しい版で書いた記録（Issue の本文の確認の表、リリース記録の `acceptance`）は残る。戻した版は読まないので、戻した後の確認の記録は書き直す
- 移行するデータは無い（過去の Issue は移行しない）

## 完了の定義

- [ ] 受け入れ条件 1〜10 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] 変えたモジュールの範囲テストと、`python3 scripts/check-skill-frontmatter.py`・`ndf check`・`ndf records glossary meanings` が通る
- [ ] 全体テスト（CI）が通る
- [ ] 要求仕様書の「リリース後の確認の計画」の条件 1・6・7 を、配ったバージョンで確かめる項目として残している
