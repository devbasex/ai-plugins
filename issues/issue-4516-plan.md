# #4516: 読み手の無い値の測定をやめ、解析の記録を追跡しないファイルへ分ける

## 関連リンク

- 要求仕様: [issue-4516-requirements.md](./issue-4516-requirements.md)（正は #4516 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4516-design.md](./issue-4516-design.md)・詳細設計書 [issue-4516-design-detail.md](./issue-4516-design-detail.md)
- モード: `standard`（設定のファイルの形と公開の契約を変えるため。conductor の判定）

## 修正対象

詳細設計書の「地図」の表のとおり。単位ごとに受け持つ行は下の「タスク分解」の対象ファイルにある。

## タスク分解

### Task 1: 読み手の無い値を測るのをやめる

- **対象ファイル:** `measure_ci.py`・`measure_repo.py`・`cli.py`（`_ci_items`）・`write.py`（`p_number`・`_carry_walls` とその呼び出し）・`check.py`（`ANALYZER` を 4 へ）・設定のスキーマ（契約とそのコピー）・`docs/specifications/project-decl-ci-representative-run.md` と `docs/specifications/README.md` の行・`project-analysis.md`（測るものと書き出しの規則の節）・`test_project_measure.py`・`test_project_cli.py`
- **変更内容:** 詳細設計書の「測定」の節のとおり。`ci` は `required_checks` だけ、言語の行に `files` を書かず名前の順に並べる。GitHub の API は `rules/branches`・`pulls`・`issues` だけを呼ぶ。スキーマは、この単位では `Language.files` と `Ci.provider`・`Ci.workflows` を必須から外すだけにする（追跡している `.ndf/project.json` に残る旧い値を、単位 2 で移すまで受け付けるため。消すのは単位 2）。`project-analysis.md` は、この単位で測らなくなる値を案内しないように直す。例の手順 2 から CI の JUnit の合計とワークフローを外し、CI の実行時間を測る実行の選び方の段落を、測るものが必須のチェックだけであることと、テストの走らせ方を回答で決めることの説明に置き換え、「書き出しの規則」から実行時間を引き継ぐ行を消す
- **満たす受け入れ条件:** 4（3 のうち測定の側）
- **進め方:** 失敗するテスト（`gh` の呼び出しのパスが 3 つだけ・言語の行にファイル数が無く名前の順）→ 消す・直す → 整理

### Task 2: 解析の記録を `.ndf/analysis/project.json` へ分ける

- **対象ファイル:** `analysis_record.py`（新設）・`items.py`・`check.py`（`load`・`judge`）・`write.py`（`Inputs`・`merge_item`・`merge_project`・`_replace_ours`・`_declare_ci_tests`・`File`・`plan`、`_same_but_time` の削除）・`cli.py`（`_inputs` の記録の読み込み・`_print_write` の要約）・設定のスキーマ（旧い形のキーを消す）と `internal/project-analysis.schema.json`（新設）・`locations.json`・`ndf-files.json`・生成する `ndf-settings.md`・`files.md`・追跡している `.ndf/project.json`・`project-analysis.md`（記録の置き場に関わる記述）・`test_analysis_record.py`（新設）・`test_project_cli.py`
- **変更内容:** 詳細設計書の「解析の記録」「書き出し」「判定と読み込み」の節のとおり。`.gitignore` の作成・途中の印・`written_digest` を含む。`project-analysis.md` には、設定に指紋が残ること・「`.ndf/` の追跡」の表の解析の記録の行・移行の 1 回目に出る差分をコミットする手順を書く。追跡している `.ndf/project.json` は、設計書の決定 4 の移し方（`strip_legacy` と指紋の書き直し）で、スキーマの変更と同じコミットで移す
- **満たす受け入れ条件:** 1・2・3・5・5a・6・8・9・10・11・12・13・14・15・16
- **進め方:** 失敗するテスト（2 回目の書き出しで何も書かない・旧い形を「古い」と判定・移行で持ち主の見分けが変わらない）→ 実装 → 整理

### Task 3: 記録が git に入らないことを確かめ、書き出しをロックで守る

- **対象ファイル:** `cli.py`（`_write` の `git ls-files`・`git check-ignore`・ロック）・`check.py`（`repo_dirs` の `RepoDirs`）・`strategy_write.py`（`lock_file`）・`strategy_cli.py`・`contracts/limits/branch-strategy.write-lock.json`・`project-analysis.md`（ロックと、記録が git に無視されることの確認に関わる記述）・`test_project_cli.py`
- **変更内容:** 詳細設計書の `_write` の手順・異常系の表・`lock_file`・`repo_dirs` のとおり。`project-analysis.md` には、書き出しがロックを待つことと、記録が git に無視されない状態では書かずに止まることを書く。測定の記述は単位 1、記録の置き場の記述は単位 2 で直す
- **満たす受け入れ条件:** 5b・7・17
- **進め方:** 失敗するテスト（追跡済みの記録・否定のパターン・`git` の 128・ロックの上限 0 秒）→ 実装 → 整理

## レビューの単位

依存の向き（測定 → 記録 → 守り）で分ける。どの単位も単独で CI が通り、終えた後も書き出しが動く。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 1 測定を減らす | Task 1 | 4516:4 | 約 700（大半は削除） | 792（+211・-581。実装計画とレビューの直しを含む） |
| 2 解析の記録を分ける | Task 2 | 4516:1・2・3・5・5a・6・8・9・10・11・12・13・14・15・16 | 約 900 | 1046（+689・-357。レビューの直しを含む） |
| 3 書き出しを守る | Task 3 | 4516:5b・7・17 | 約 400 | 268（+242・-26。手順書の直しの多くは単位 2 のレビューで先に入った） |

数えないパス: 生成物の `plugins/ndf/docs/contracts/ndf-settings.md`・`files.md`、スキーマのコピー `plugins/ndf/skills/development-workflow/schemas/project.schema.json`。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るファイルは最大 545 行（`measure_repo.py`）で、`write.py` 357 行・`cli.py` 271 行。単位 2 が足す `analysis_record.py` が置き場を分けるので、先に整える箇所が無い |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `cli._write` と `write.plan`（単位 2 と 3 がともに触る）、`strategy_write` と `cli` のロックの取り方 | 2 つの単位が同じ関数を順に足すので、分け方と名前をまとめて見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 単位 2 が入ると、メインディレクトリの未コミットの `.ndf/project.json` と取り込む変更がぶつかる | 設計書の決定 4 のとおり、片付けは開発者が行う。この計画では触らない |
| 手元でテストを打たない設定（`test.ci.only`）のため、失敗が CI で初めて分かる | 単位ごとに Draft の PR を早く出し、CI の結果を監視して直す |

## 完了の定義

- [ ] 受け入れ条件 1〜17（5a・5b を含む）をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] 単位ごとの PR の CI（`pytest`・`lint`・`script-structure-check` など必須のチェック）が通り、`cross-review` が収束している
