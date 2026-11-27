# #4363: 実際に起きると確かめられた指摘だけを重い扱いのまま残す

## 関連リンク
- 要求仕様: [issue-4363-requirements.md](./issue-4363-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4363 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4363-design.md](./issue-4363-design.md)、詳細設計書: [issue-4363-design-detail.md](./issue-4363-design-detail.md)
- 実装の順: 3 課題のうち 1 番目である。この計画の単位がすべてマージされてから、[#3780 の実装計画](./issue-3780-plan.md)、次に [#2468 の実装計画](./issue-2468-plan.md) を始める。2 つはここで作る判定役の回答と関数を使う
- モード: `standard`（指摘の重さの決まりと状態の記録が変わる機能追加）

## 修正対象

詳細設計書の「地図」から引き写した。パスは `plugins/ndf/` からの相対である。

| ファイル | 変える中身 |
| --- | --- |
| `src/ndf/domains/crossreview/carry.py`（新設） | 引き継ぎの関数を `drive.py` から移す |
| `src/ndf/domains/crossreview/reviewing.py`（新設） | 相手役への指示と起動を `drive.py` から移し、出力の形に `protect` を足す |
| `src/ndf/domains/crossreview/verify_decide.py`（新設） | `select`・`instruction`・`read`・`lowers` |
| `src/ndf/domains/crossreview/verify.py`（新設） | `ask` |
| `src/ndf/domains/crossreview/criteria_decide.py` | `protect_kinds`・`track_kinds`・`protect_of`・`document_line`、`reviewer_block`・`downgraded` |
| `src/ndf/domains/crossreview/journal.py` | `protect`・`check`・`fresh`・`apply_checks` |
| `src/ndf/domains/crossreview/drive.py` | 移動と `_confirm` |
| `src/ndf/domains/crossreview/report_decide.py` | 下げた行の元の重要度と定型の理由 |
| `contracts/data/review-criteria.json` | 指摘の種類の一覧、`protect` と判定役の指示の文 |
| `contracts/limits/cross-review.remark-chars.json`・`cross-review.remarks-total.json` | 読み手のモジュール名（`ndf check` の `limit-readers` が実際の読み手と照らすため） |
| `skills/cross-review/docs/01-state-and-review.md` | 「1 ラウンド」「指摘の基準」 |
| `tests/domains/crossreview/test_crossreview_{verify（新設）,journal,report,criteria,citations,documents,prior,finish}.py` | 新しい振る舞いのテストと、移した関数の参照名 |

## タスク分解

テストは先に書く。このリポジトリはテストを CI に寄せる設定（`.ndf/project.json` の `test.ci.only`）なので、落ちることと通ることは Pull Request の CI（`gh pr checks`）で読む。静的解析の `plugins/ndf/bin/ndf check` は手元で打つ。

### Task 1: プレリファクタリング（引き継ぎを `carry.py` へ移す）
- **対象ファイル:** `carry.py`、`drive.py`、`test_crossreview_finish.py`、`test_crossreview_prior.py`
- **変更内容:** 詳細設計の「引き継ぎと相手役の起動の分離」のうち引き継ぎの側。振る舞いは変えない
- **満たす受け入れ条件:** 無し（後のタスクの場所を空ける）
- **進め方:** テスト駆動は適用しない。振る舞いを変えない移動なので、今のテストが参照名の置き換えだけで通ることで確かめる

### Task 2: プレリファクタリング（相手役への指示と起動を `reviewing.py` へ移す）
- **対象ファイル:** `reviewing.py`、`drive.py`、2 つの上限の設定の読み手、`test_crossreview_citations.py`・`test_crossreview_criteria.py`・`test_crossreview_documents.py`
- **変更内容:** 同じ節の相手役の側
- **満たす受け入れ条件:** 無し
- **進め方:** Task 1 と同じ

### Task 3: 今の下げ方を縛る退行テストを足し、指摘の種類 `protect` を受ける
- **対象ファイル:** `criteria_decide.py`、`review-criteria.json`、`reviewing.py`、`journal.py`、`test_crossreview_journal.py`・`test_crossreview_criteria.py`
- **変更内容:** 先に退行テスト（1 文のある major は重いまま、1 文の無い major は minor、MVV に反する指摘と必ず人の承認が要る操作の指摘は下げない）を置く。次に相手役の `protect` を行に残し、出し直しで足し、直した後の再発でも残す
- **満たす受け入れ条件:** 4363:5
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: 判定役の判断とジャーナルへの適用
- **対象ファイル:** `verify_decide.py`、`journal.py`、`criteria_decide.py`、`test_crossreview_verify.py`（新設）、`test_crossreview_journal.py`
- **変更内容:** `select`・`instruction`・`read`・`lowers`、`apply_checks`、下げた行の出し直し（設計の不変条件 I1・I4）、`downgraded`。外部のコマンドを打たない部分だけで、まだ駆動からは呼ばない
- **満たす受け入れ条件:** 4363:1・3・4・5
- **進め方:** 失敗するテスト（詳細設計の単体テストの観点の `select`・`read`・`lowers`・`apply_checks`・`_raised_again`・`_revive`）→ 最小実装 → 整理

### Task 5: 判定役を起動して駆動へ組み込む
- **対象ファイル:** `verify.py`、`drive.py`、`report_decide.py`、`review-criteria.json`、`test_crossreview_verify.py`、`test_crossreview_report.py`
- **変更内容:** `ask`、`_confirm`、記録のコメントの元の重要度と定型の理由（判定役が書いた文は出さない）
- **満たす受け入れ条件:** 4363:3・4
- **進め方:** 偽の起動で `ndf cross-review run` を通す失敗するテスト（成り立たない major だけなら修正担当を起動せずに収束する・判定役の失敗では下げない・渡す指摘が無ければ起動しない）→ 最小実装 → 整理

### Task 6: 手順書
- **対象ファイル:** `skills/cross-review/docs/01-state-and-review.md`
- **変更内容:** 判定役の確認のステップと、重要度が決まる 2 段階
- **満たす受け入れ条件:** 4363:1
- **進め方:** テスト駆動は適用しない（文書の文言を照合するテストは書かない）。書いた後に手順書を読み手の立場で読み直す

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 引き継ぎの分離 | Task 1 | 無し | 約 330 | |
| 相手役の起動の分離 | Task 2 | 無し | 約 320 | |
| 指摘の種類 protect | Task 3 | 4363:5 | 約 250 | |
| 判定役の判断 | Task 4 | 4363:1・3・4・5 | 約 450 | |
| 判定役の組み込み | Task 5・6 | 4363:1・3・4 | 約 400 | |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | `drive.py` から引き継ぎ（約 150 行）と相手役の指示・起動（約 140 行）を 2 つのモジュールへ移す（Task 1・2） | `drive.py` は 986 行で、構造の規則の上限は 1,000 行。この課題の追加だけで超え、#3780 と #2468 も変える。cross-review のテストは 417 関数で、移動の確かめに使える |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `journal.py` と `drive.py` | 3 課題がどれも行の形と 1 ラウンドの順に足す。`journal.py` は今 488 行 |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 判定役が正しい指摘を下げる | 保護の対象・MVV・承認の要る操作の指摘は下げない。下げた行は元の重要度と理由を残し、リリース後に全件を読む |
| 下げた行に MVV の項目を挙げた出し直しが来る場合を、設計の不変条件 I1 が定めていない | 詳細設計では固定した重さにする（受け入れ条件 5）。設計の承認で確かめる |

## 切り戻し手順
- 単位ごとの Pull Request を後ろから順に revert する。状態の記録に足した `protect`・`check` は、前のバージョンが読み飛ばす項目である

## 完了の定義
- [ ] 受け入れ条件 4363:1・3・4・5 を満たし、条件ごとに検証手段（CI のテストか設計書）と結果が対応している
- [ ] 受け入れ条件 4363:2 はリリース後テストで確かめる（10 回以上の実行の集計を #4363 に書く。判定役の起動の回数・費用・秒も併せて書く）
- [ ] Pull Request の CI と `plugins/ndf/bin/ndf check` が通る
