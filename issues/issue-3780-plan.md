# #3780: 2 ラウンド目以降は直した箇所を確かめて終わる

## 関連リンク
- 要求仕様: [issue-3780-requirements.md](./issue-3780-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/3780 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3780-design.md](./issue-3780-design.md)、詳細設計書: [issue-3780-design-detail.md](./issue-3780-design-detail.md)
- 実装の順: 3 課題のうち 2 番目である。[#4363 の実装計画](./issue-4363-plan.md) の単位がすべてマージされた後に始める。この計画がマージされてから [#2468 の実装計画](./issue-2468-plan.md) を始める
- モード: `standard`（2 ラウンド目以降の範囲と収束の条件が変わる機能追加）

## 修正対象

詳細設計書の「地図」から引き写した。パスは `plugins/ndf/` からの相対である。

| ファイル | 変える中身 |
| --- | --- |
| `src/ndf/domains/crossreview/local_scope.py`（新設） | `review-scope` の範囲の判断を `cli.py` から移す |
| `src/ndf/domains/crossreview/leftover_decide.py`（新設） | 線引き・残し方・起票の文と照合 |
| `src/ndf/domains/crossreview/leftover.py`（新設） | 起票と起票の記録 |
| `src/ndf/domains/crossreview/scope_decide.py`・`round_scope.py`・`changes.py` | 起点の条件、新しく PR の変更になったファイル、渡す差分、変えた行 |
| `src/ndf/domains/crossreview/journal.py`・`prior_decide.py` | `parked` と起票済みの引き継ぎ |
| `src/ndf/domains/crossreview/finish_decide.py`・`report_decide.py`・`threads.py` | 欠け・表・一覧・スレッドから残した指摘を扱う |
| `src/ndf/domains/crossreview/drive.py`・`cli.py`・`settings.py` | 範囲と線引きの組み込み、起票の公開、上限の既定 |
| `contracts/data/review-criteria.json` | 起票する指摘の種類 |
| `contracts/limits/cross-review.max-rounds.json`・`cross-review.max-rounds.design.json`（新設）・`github.list-pages.json` | 上限の既定と、読み手の追加 |
| `contracts/schemas/io/cross-review.result.schema.json`・`cross-review.args.schema.json`、`docs/contracts/commands.md` | `disposition` と上限の既定（args と commands は `ndf check --write-spec` の生成物） |
| `skills/cross-review/SKILL.md`・`docs/01-state-and-review.md`・`docs/02-fix-and-rotation.md` | 範囲・判定・上限 |
| `tests/domains/crossreview/test_crossreview_{leftover（新設）,scope,run,finish,report,prior}.py`・`conftest.py` | 新しい振る舞いのテストと、起票先を差し替える偽物 |

詳細設計で設計の触るファイルに足したのは、`prior_decide.py`、`review-criteria.json`、`github.list-pages.json`、`docs/contracts/commands.md`、`test_crossreview_prior.py`、`conftest.py` である（理由は詳細設計の地図）。

## タスク分解

テストは先に書き、合否は Pull Request の CI で読む（`.ndf/project.json` の `test.ci.only`）。`plugins/ndf/bin/ndf check` は手元で打つ。時間の経過を実際に待つテストは書かない。

### Task 1: プレリファクタリング（`review-scope` の判断を `local_scope.py` へ移す）
- **対象ファイル:** `local_scope.py`、`cli.py`
- **変更内容:** `_LocalBase`・`_local_base`・`_local_mvv`・`_local_sections`・`_local_scope` を振る舞いを変えずに移す。`_local_scope` は `decide` に名前を変える
- **満たす受け入れ条件:** 無し
- **進め方:** テスト駆動は適用しない。今の `test_crossreview_scope.py` がそのまま通ることで確かめる

### Task 2: 今の振る舞いを縛る退行テスト
- **対象ファイル:** `test_crossreview_scope.py`、`test_crossreview_run.py`
- **変更内容:** 1 ラウンド目は全差分、設計の PR の範囲は今のまま、`--max-rounds` を渡せばその値、必ず人の承認が要る操作の指摘は人の判断を待つ、前回の収束の使い回しの条件は今のまま
- **満たす受け入れ条件:** 3780:4
- **進め方:** 今のコードで通るテストを足す（退行テスト）

### Task 3: 上限の既定を目的で分ける
- **対象ファイル:** `settings.py`、`cli.py`、2 つの上限の設定、`cross-review.args.schema.json`、`docs/contracts/commands.md`
- **変更内容:** `default_max_rounds`。`--max-rounds` の argparse の既定を None にする。`cli.py` の説明の `[--max-rounds 3]` も直す
- **満たす受け入れ条件:** 3780:3
- **進め方:** 失敗するテスト（省けば設計の PR は 3、ほかは 4）→ 最小実装 → `ndf check --write-spec` で生成物を書く

### Task 4: 起点の条件と、新しく PR の変更になったファイル
- **対象ファイル:** `scope_decide.py`、`round_scope.py`、`changes.py`、`test_crossreview_scope.py`
- **変更内容:** `origin_record`・`origin`・`line_origin`・`new_in_pr`・`since_diff`
- **満たす受け入れ条件:** 3780:1
- **進め方:** 一時のリポジトリで失敗するテスト（詳細設計の `new_in_pr` の観点。比較先の変更、競合の解消、実行権限だけの変更、同じ文の追加）→ 最小実装 → 整理

### Task 5: 範囲を駆動と `review-scope` へ組み込む
- **対象ファイル:** `drive.py`、`local_scope.py`、`test_crossreview_run.py`、`test_crossreview_scope.py`
- **変更内容:** `_ranges` と `_prepare_round`。今の「比較基点が違えば全差分」の期待は、実装の PR では差分になるように直す
- **満たす受け入れ条件:** 3780:1・4
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 6: 線引きの判断と、残した指摘のジャーナル
- **対象ファイル:** `leftover_decide.py`、`changes.py`（`touched`）、`journal.py`、`review-criteria.json`、`test_crossreview_leftover.py`（新設）
- **変更内容:** `normalized`・`proceeds`・`kinds_of`・`touched`・`sort`、`enter` の `raised_ids`・`returned`、`_classify` が `parked` を外す。**必ず人の承認が要る操作の指摘は直さずに残さないことを確かめるテストを最初に書く。** 変えていない行の major で起票する種類を持つ場合、以前の確認で指摘の種類（`check.tracks`）が付いた行に後から承認の要る操作が指摘された場合、直さずに残した行（`parked`）に後から指摘された場合の 3 つで、`parked` が付かないか外れ、起票されず、人の判断を待つ
- **満たす受け入れ条件:** 3780:5
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 7: 線引きを駆動・終わり方・スレッドへ組み込む
- **対象ファイル:** `drive.py`、`finish_decide.py`、`threads.py`、`test_crossreview_run.py`、`test_crossreview_finish.py`
- **変更内容:** `_sort_leftovers`、`parked` を欠けとスレッドから除く
- **満たす受け入れ条件:** 3780:2・4・5
- **進め方:** 失敗するテスト（2 ラウンド目に変えていない行の major と minor だけなら収束する・1 ラウンド目は線引きしない・設計の PR は線引きしない）→ 最小実装 → 整理

### Task 8: 起票と起票の記録
- **対象ファイル:** `leftover.py`（新設）、`leftover_decide.py`、`github.list-pages.json`、`test_crossreview_leftover.py`
- **変更内容:** `files_issue`・`identity`・`issue_parts`・`trusted`・`matches`・起票の記録・`file_issues`・`primary_of`。**指摘の全文の秘密を伏せてから題を切り出す。** 題の切り出しの長さの境目をまたぐ偽の秘密を持つ指摘を起票し、題にも本文にも断片が無いことを確かめるテストを置く。偽の秘密はテストの中で文字列を連結して作り、`scripts/check-repo-secrets.py` に当たらないようにする
- **満たす受け入れ条件:** 3780:2
- **進め方:** GitHub を差し替える偽物で失敗するテスト → 最小実装 → 整理

### Task 9: 記録のコメント・結果・引き継ぎへの組み込み
- **対象ファイル:** `report_decide.py`、`prior_decide.py`、`journal.py`（`carry`）、`threads.py`（`viewer`）、`cli.py`、`cross-review.result.schema.json`、`conftest.py`、`test_crossreview_report.py`、`test_crossreview_prior.py`
- **変更内容:** 表の残し方と URL、一覧の `parked`・`issue`、`Hub.leftovers`、`_github_hub`・`_publish`・`_post_report`・`_report`・`cmd_report`・`_with_leftovers`
- **満たす受け入れ条件:** 3780:2
- **進め方:** 失敗するテスト（結果の `items` に `disposition` と `url`、起票の失敗の後の打ち直しで 1 回だけ起票、別の保存先は残した指摘を引き継がず URL を戻す）→ 最小実装 → 整理

### Task 10: 手順書
- **対象ファイル:** `skills/cross-review/` の 3 つの文書
- **変更内容:** 詳細設計の「手順書」
- **満たす受け入れ条件:** 3780:1・2・3
- **進め方:** テスト駆動は適用しない。読み手の立場で読み直す

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 範囲の判断の分離 | Task 1 | 無し | 約 220 | |
| 退行テストと上限の既定 | Task 2・3 | 3780:3・4 | 約 200 | |
| 起点と新しい変更 | Task 4 | 3780:1 | 約 400 | |
| 範囲の組み込み | Task 5 | 3780:1・4 | 約 300 | |
| 線引きの判断 | Task 6 | 3780:5 | 約 400 | |
| 線引きの組み込み | Task 7 | 3780:2・4・5 | 約 300 | |
| 起票 | Task 8 | 3780:2 | 約 600 | |
| 記録と手順書 | Task 9・10 | 3780:1・2・3 | 約 500 | |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | `cli.py` の `review-scope` の範囲の判断を `local_scope.py` へ移す（Task 1） | `cli.py` は今 848 行で、この課題の起票の組み込みと #2468 の引数が足され、1,000 行の上限に近づく。範囲の判断はこの課題で変えるので、移した先で変える。`drive.py`（今 986 行）の分離は #4363 の Task 1・2 が先に行う |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | 記録の公開（`cli.py` の `_publish`・`_report`）と、HTML のコメントに JSON を埋める 2 つの形（記録のコメントの一覧と起票の記録） | スレッド・起票・記録の 3 つが同じ公開の処理に並ぶ。埋め込みの形が 2 か所になる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 読んでいない相手役を足して全差分になったラウンドでも線引きするため（設計の決定 2）、その相手役の変えていない行の major は直さずに残る | 残した指摘は記録のコメントと結果に必ず出る。要求仕様の受け入れ条件 1・2 の読み合わせを設計の承認で確かめる |
| 起票が公開のリポジトリに秘密を出す | 全文を伏せてから切り出す順をテストで固定する（Task 8） |
| 起票が重複する | 起票の記録を作成の前に書き、打ち直しの確かめをテストする |

## 切り戻し手順
- 単位ごとの Pull Request を後ろから順に revert する。起票した Issue と起票の記録のコメントは残る（消さない）
- 前のバージョンは行の `parked` を知らないので、同じ状態の保存先で打ち直すと、残した指摘が未解決の指摘として修正担当へ渡る

## 完了の定義
- [ ] 受け入れ条件 3780:1〜5 をすべて満たし、条件ごとに CI のテストと対応している
- [ ] Pull Request の CI と `plugins/ndf/bin/ndf check` が通る
