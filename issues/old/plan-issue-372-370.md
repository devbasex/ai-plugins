# 372 / 370: ラウンド内のレビューを軽くし、構造改善の既定を移す

## 関連リンク

- 設計: [parallel-batch-08/01-issue-372-370.md](parallel-batch-08/01-issue-372-370.md)
- 全体の境界と順序: [parallel-batch-08/00-overview.md](parallel-batch-08/00-overview.md)
- 課題: #372 / #370

## モード

`architecture`。工程表（公開インタフェースにあたる工程の定義）を変更し、
`cross-refactoring` / `cross-review` / `development-workflow` の複数 Skill にまたがる。

## 目的と非目的

達成したい状態:

- 修正のたびに 2 者を起動し直す形をやめ、変更要求を出した担当だけが再レビューする
- 工程表の「構造改善」が `cross-refactoring` を指し、モードごとの要否と退避先が決まっている

やらないこと:

- レビュー担当の人数を 1 者へ減らす（設計の決定 1）
- ラウンド内のレビューを廃す（決定 2）
- 重さを選ぶ引数を増やす（決定 3）
- `--max-fix-rounds` の既定を変える（決定 5）
- 工程表の行を増やす（決定 7）

## 前提

- 前提 1: レビュー結果のファイルはラウンド番号でパスが決まり、再レビューで起動しなかった
  担当のファイルはその場に残る。**消さなければ引き継ぎが成立する**
- 前提 2: 担当 B（#371）が `check_auth` を共通層へ移すが、この担当のマージが先なら既存の
  位置のままでよい

## 受け入れ条件

- [ ] 1. 再レビューで起動する担当が、変更要求を出した担当だけになる
      （`review-targets` の単体テスト）
- [ ] 2. 起動しなかった担当の判定を前回の結果から引き継ぎ、`judge` が矛盾なく動く
      （承認済みの結果ファイルを残したまま `judge-review` が `approved` を返す単体テスト）
- [ ] 3. 担当が 0 人になる状態で進まない（`review-targets` が中断の終了コード `ABORT` を返す単体テスト）
- [ ] 4. 差し戻し（`invalid`）は 2 者へ戻る（単体テスト）
- [ ] 5. `fix_reviewers` を持たない既存の状態ファイルを初回として読める（単体テスト）
- [ ] 6. 削減量が `docs/02-apply-and-review.md` の「なぜラウンド単位か」の表と同じ形で書かれている
- [ ] 7. 取り消しの単位が保たれている（`test_abandon_items.py` が変わらず通る）
- [ ] 8. 工程表の「構造改善」が `cross-refactoring` を指し、モードごとの要否が書かれている
- [ ] 9. 使わない条件と退避先が `workflow-modes.md` に書かれている
- [ ] 10. `refactoring` と `cross-refactoring` の関係が両方の `SKILL.md` で一致している
- [ ] 11. `projects-tracking.md` の対応表と工程表が食い違わない（#231 の検査が通る）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `refactor.py` の副コマンド | `review-targets` を追加 | 追加のみ。既存の呼び出し側は変わらない |
| 状態ファイル | `fix_reviewers` と `carried_from_round_fix` を追加 | 無いときは初回として読む |
| 工程表 | 「構造改善」の値を変える | 手順の変更。利用者が呼ぶコマンドが変わる |

## 修正対象

- `plugins/ndf/skills/cross-refactoring/scripts/refactor.py`
- `plugins/ndf/skills/cross-refactoring/docs/02-apply-and-review.md`
- `plugins/ndf/skills/cross-refactoring/SKILL.md`
- `plugins/ndf/skills/refactoring/SKILL.md`
- `plugins/ndf/skills/development-workflow/SKILL.md`
- `plugins/ndf/skills/development-workflow/references/workflow-modes.md`
- `plugins/ndf/skills/development-workflow/references/projects-tracking.md`
- `plugins/ndf/skills/cross-refactoring/tests/test_review_targets.py`（新規）

## タスク分解

### Task 1: 再レビューの対象を返す副コマンドを作る

- **対象ファイル:** `refactor.py`、`tests/test_review_targets.py`（新規）
- **変更内容:** 判定が変更要求のときに `entry["fix_reviewers"]` へ担当を記録し、
  `review-targets` がラウンドの状態から次に起動する担当を返す
- **満たす受け入れ条件:** 1 / 2 / 3 / 4 / 5 / 7
- **進め方:** 失敗するテスト → 最小実装 → 整理

**投稿の確認を起動した担当だけに絞る変更は行わない。** 受け入れ条件に紐づかず、設計の
構成要素にも無い。起動しなかった担当の判定は結果ファイルがその場に残ることで引き継がれ、
確認をもう一度通っても結果は変わらない。

### Task 2: 手順書を書き換える

- **対象ファイル:** `docs/02-apply-and-review.md`、`SKILL.md`（cross-refactoring）
- **変更内容:** Step 5 の起動を `review-targets` 経由にし、削減量の表と、軽くしたことで
  失う検出をどこで補うかを書く。Step 7 の `cross-review` へ渡す観点を定型として置く
- **満たす受け入れ条件:** 6
- **進め方:** 文書のみ。テスト駆動を適用しない

### Task 3: 工程表と退避先を書く

- **対象ファイル:** `development-workflow/SKILL.md`、`references/workflow-modes.md`、
  `references/projects-tracking.md`
- **変更内容:** 「構造改善」の行を `cross-refactoring`（`standard` は `refactoring`）にし、
  「Pull Request」の行へ注記を置く。退避先の 3 条件を `workflow-modes.md` へ書く
- **満たす受け入れ条件:** 8 / 9 / 11
- **進め方:** 文書のみ。#231 の検査で確かめる

### Task 4: 2 つの Skill の関係を揃える

- **対象ファイル:** `refactoring/SKILL.md`、`cross-refactoring/SKILL.md`
- **変更内容:** `refactoring` から `cross-refactoring` への案内を足し、両方の記述を揃える
- **満たす受け入れ条件:** 10
- **進め方:** 文書のみ

## 影響範囲

- `cross-refactoring` を呼ぶ利用者の手順（Step 5 の起動が変わる）
- 工程表を読むすべてのモード（`standard` は既定が変わらない）
- 盤面の対応表（値そのものは変えない）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 起動しなかった担当の結果ファイルが消えて引き継ぎが切れる | 消さないことをテストで固定する（受け入れ条件 2） |
| 差し戻しが 1 者だけへ戻り、形式の誤りを見落とす | `invalid` を絞らない（受け入れ条件 4） |
| 工程表の行の変更が 4 箇所の突き合わせを壊す | 値を変えず、指す Skill 名だけを変える |

## 切り戻し手順

- 変更はすべて 1 つの Pull Request に載る。取り消しは revert で足りる
- 状態ファイルへ足すキーは追加のみで、古い版が読んでも無視される

## 完了の定義

- [ ] 受け入れ条件 11 件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] リポジトリの根から `pytest` が通る
- [ ] `bash scripts/validate-runtime-plugins.sh` が通る
