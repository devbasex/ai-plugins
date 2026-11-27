# #4295: レーンの触る場所をファイル単位にし、実際に変えたファイルの重なりを知らせる

## 関連リンク
- 要求仕様: [issue-4295-requirements.md](./issue-4295-requirements.md)（正は [#4295](https://github.com/devbasex/ai-plugins/issues/4295) の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4295-2037-design.md](./issue-4295-2037-design.md)、詳細設計書: [issue-4295-2037-design-detail.md](./issue-4295-2037-design-detail.md)（#2037 と共通。不変条件と決定の番号はこの設計書の番号）
- 同じスプリントの実装計画: [issue-2037-plan.md](./issue-2037-plan.md)。設計の結果の読み手と、設計 PR の番号の記録（`design_pr`）はこの計画で先に作る
- モード: `standard`。スプリントの課題が 2 つで、公開インタフェースも変わるので、実装計画を省かない

## 修正対象

詳細設計書の「地図」から引き写す。関数ごとの仕様は詳細設計書の各セクションにある。設計の結果の表で #4295 の記載にある `stages.py`・`definition.py` の `outputs_as`・`progress/run.py`・`document-review.json`・`ndf.schema.json`・`stage-snapshot.schema.json` は、[issue-2037-plan.md](./issue-2037-plan.md) の Task 4 で作る。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/progress/lane/types.py` | 行の触る場所と比較の状態の型、`mode_of` |
| `plugins/ndf/src/ndf/progress/lane/fold.py` | 事象 `touches_found`・`touches_compared`・`touches_retry` の畳み込み |
| `plugins/ndf/src/ndf/progress/lane/rules.py` | I13 を行の単位で判定し、重なりの組と待ちを求める。`plan_complete` に I6 |
| `plugins/ndf/src/ndf/progress/lane/__init__.py` | 工程 `document-review` を持つモードの名前を `PlanView` に載せる |
| `plugins/ndf/src/ndf/progress/lane/machine.py` | `other_views` |
| `plugins/ndf/src/ndf/progress/lane/launch.py` | `DEFAULT_MODE` を `types` から読む |
| `plugins/ndf/src/ndf/progress/definition.py` | `modes_with_stage` |
| `plugins/ndf/src/ndf/progress/plan/__init__.py` | レーンの印の記録、`record_touches`、`complete_plan` |
| `plugins/ndf/src/ndf/progress/plan/touches.py`（新設） | 観測の 1 周（GitHub とステートマシンの読み書き） |
| `plugins/ndf/src/ndf/progress/plan/touches_decide.py`（新設） | 観測の判断（DB とプロセスを読まない） |
| `plugins/ndf/src/ndf/progress/plan/run.py`・`run_decide.py` | 観測の手と、耐久ステップ `observe_touches` |
| `plugins/ndf/src/ndf/core/durable.py` | `FORMAT_SOURCE_HASH` を書き直す |
| `plugins/ndf/src/ndf/contracts/limits.py`、`plugins/ndf/contracts/schemas/limits.schema.json`、`plugins/ndf/contracts/limits/parallel-run.touches.json`（新設） | 観測の間隔・取得の上限・失敗の上限 |
| `plugins/ndf/contracts/workflow/progress.json` | 事象 3 件を足す |
| `plugins/ndf/contracts/workflow/parallel-plan.schema.json` | レーンの任意の真偽値 `touches_from_design` |
| `plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md` | `touches_from_design` と `after` の書き方 |
| `plugins/ndf/skills/development-workflow/references/parallel-checks.md`・`parallel.md` | I13 の対象、知らせの表の `touches_overlap` |
| `plugins/ndf/tests/progress/test_lane.py`・`test_lane_machine.py`・`test_plan_run.py`・`test_definition.py`、`plugins/ndf/tests/progress/test_plan_touches.py`（新設） | 詳細設計書の「単体テストの観点」と、設計書の「テスト設計」 |

## タスク分解

変更の種類は機能追加なので、最初のタスクは今の振る舞いを縛る退行テストにする。テストは `uv run --frozen --project . --all-extras pytest <対象> -q` で、対象のファイルに絞って流す。

### Task 1: 今の I13 と計画の完了を退行テストで縛る
- **対象ファイル:** `plugins/ndf/tests/progress/test_lane.py`、`plugins/ndf/tests/progress/test_plan_run.py`
- **変更内容:** `test_touch_overlap_errors_as_they_are_now` が通ることを確かめる。印の無い計画で、終わった行だけになれば計画が完了することを縛る（足りない場合だけ足す）
- **満たす受け入れ条件:** 4295:3
- **進め方:** 退行テストなので、今のコードで通るテストを先に置く

### Task 2: 新しい事象を足し、行の触る場所を畳み込んで、I13 を行の単位で判定する
- **対象ファイル:** `plugins/ndf/contracts/workflow/progress.json`、`plugins/ndf/contracts/workflow/parallel-plan.schema.json`、`progress/lane/types.py`・`fold.py`・`rules.py`・`__init__.py`・`launch.py`、`progress/definition.py`、`progress/plan/__init__.py`（`lane_payload`）、`tests/progress/test_lane.py`・`test_definition.py`（パスは `plugins/ndf/src/ndf/` と `plugins/ndf/` からの相対）
- **変更内容:** 詳細設計書の「事象のコントラクトと行の触る場所の畳み込み」と、「計画の決まり」のうち `designing`・`row_scope`・`_touch_hits`・`_touch_errors`。印の無いレーンの判定は変えない
- **満たす受け入れ条件:** 4295:3
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。設計書のテスト設計の「I1（設計中の行と I19）」の (a)〜(c) と「#4295 の 3・I1」を縛る

### Task 3: 観測の 1 周を耐久ワークフローに足し、設計の触る場所を記録する
- **対象ファイル:** `progress/plan/touches.py`・`touches_decide.py`・`run.py`・`run_decide.py`・`__init__.py`、`core/durable.py`、`contracts/limits.py`（以上 `plugins/ndf/src/ndf/` からの相対）、`plugins/ndf/contracts/limits/parallel-run.touches.json`、`plugins/ndf/contracts/schemas/limits.schema.json`、`plugins/ndf/tests/progress/test_plan_touches.py`・`test_plan_run.py`
- **変更内容:** 詳細設計書の「触る場所の観測」のうち、耐久ワークフローへの組み込み、`observe_cycle`・`record_designs`・`record_touches`・`gh_api`・`ApiDocs`・`counted`。`observe_cycle` が呼ぶのはこの時点では設計の記録だけにする。`gh api` と `gh repo view` の呼び出しは、書く前に手元で打って、出力の形と失敗の終了コードを確かめる
- **満たす受け入れ条件:** 4295:1、4295:4
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。設計書のテスト設計の「#4295 の 1」「#4295 の 4・I3」「手順 1・2」「手順 3」「手順 1〜3・I9」の行を縛る。`test_durable_format.py` を流し、`FORMAT_SOURCE_HASH` を書き直す

### Task 4: 単位の PR の変えたファイルを行へ記録し、最後の観測で閉じる
- **対象ファイル:** `progress/plan/touches.py`・`touches_decide.py`・`run.py`・`__init__.py`、`progress/lane/rules.py`（`plan_complete`）、`core/durable.py`（以上 `plugins/ndf/src/ndf/` からの相対）、`plugins/ndf/tests/progress/test_plan_touches.py`・`test_plan_run.py`・`test_lane.py`
- **変更内容:** 詳細設計書の `record_diffs`・`assign`・`pick`・`is_final`・`Memory` の読み残しと一覧の読み取り済み時刻・`snapshot_of`・`observe`・`complete`・`complete_plan` の `require_final`。`gh api -i` の `Date` ヘッダと `--paginate` の出力は、書く前に手元で打って確かめる
- **満たす受け入れ条件:** 4295:5、4295:8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。設計書のテスト設計の「#4295 の 5・I2」「決定 6」「I6」の行を縛る。触る場所の外の変更を断る分岐を足さない

### Task 5: 重なりの知らせを書き、設計の重なりを実装の入口の待ちにする
- **対象ファイル:** `progress/lane/rules.py`・`machine.py`、`progress/plan/touches.py`・`touches_decide.py`（以上 `plugins/ndf/src/ndf/` からの相対）、`plugins/ndf/tests/progress/test_lane.py`・`test_lane_machine.py`・`test_plan_touches.py`
- **変更内容:** 詳細設計書の `overlap_pairs`・`ordered_since`・`touches_wait`・`other_views`・`compare`・`notices`。#4050 が作る `rules.implementation_wait` の戻り値へ、`touches_wait` の戻り値を足す 1 行を加える
- **満たす受け入れ条件:** 4295:2、4295:6
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。設計書のテスト設計の「#4295 の 2」「#4295 の 6」「I4」の 4 行・「決定 3」「決定 7」「I7」「I8」「I1・I3」「組の規則」の行を縛る

### Task 6: 計画の書き方とオーガナイザーの手順を直す
- **対象ファイル:** `plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md`、`plugins/ndf/skills/development-workflow/references/parallel-checks.md`・`parallel.md`
- **変更内容:** 詳細設計書の「計画の書き方・規約と、オーガナイザーの手順」の表のとおり。書くコマンドは、書く前に打って確かめる
- **満たす受け入れ条件:** 4295:2、4295:6、4295:8
- **進め方:** テスト駆動を適用しない（手順書の文だけの変更で、文言を照合するテストは書かない）。`ndf check` で参照切れを確かめる

## レビューの単位

#4295 の見込みの差分が 1,000 行を超えるので、依存の向きで 4 つに分ける。どの単位も単独でテストが通る。印 `touches_from_design` を付けると書く手順書は最後の単位に置くので、途中の単位をマージしても、印の無い今の計画の I13 は変わらない。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 4295-U1 | Task 1・2 | 4295:3 | 約 600 | |
| 4295-U2 | Task 3 | 4295:1・4295:4 | 約 750 | |
| 4295-U3 | Task 4 | 4295:5・4295:8 | 約 650 | |
| 4295-U4 | Task 5・6 | 4295:2・4295:6 | 約 950 | |

- 順序は、[issue-2037-plan.md](./issue-2037-plan.md) の 2037-U1・2037-U2 → 4295-U1 → U2 → U3 → U4 にする。4295-U2 は 2037 の読み手と `design_pr` の記録を使う
- 受け入れ条件 7 は、設計で採らないと決めた形（決定 4）の条件なので、どの単位にも入れない
- どの単位も見込みが目安の 400 行を超える。実装の途中で 1,000 行を超えそうなら分け直す（4295-U4 は、Task 6 を別の単位にできる）

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る中で大きいのは `plan/run_decide.py` 602 行・`plan/__init__.py` 481 行・`plan/run.py` 458 行で、新しい読み書きと判断は新設の `touches.py`・`touches_decide.py` に分ける。`lane/rules.py` は 254 行で 6 タスク中 3 タスクが触るが、`test_lane.py` 22 件と `test_lane_machine.py` 31 件がある。`test_plan_run.py` は 47 件ある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `progress/plan/touches.py` の `ApiDocs`・`gh_api` と `core/design_results.py` の `GitDocs`・`merge_commit` | 4295 と 2037 の単位が、同じ版の文書と `gh` を別々の場所で読む。失敗の扱いが同じ形になるので、重複を見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 設計の決定 3 は [#4050](https://github.com/devbasex/ai-plugins/issues/4050)（開発計画を 7 つのフェーズに分ける課題）の実装の入口の待ちを前提にする。2026-11-26 の時点で #4050 は開いていて、`plugins/ndf/src` に `implementation_wait` が無い | 設計の「影響と危険」のとおり #4050 を先に実装し、#4295 の単位はその後に始める。#2037 の 2 単位は #4050 の待ちを使わないので、先に進められる |
| #4050 も `progress/plan/run.py` と `progress/lane/*.py` を変える | 後に入る #4295 の側が base-sync で直す。#4050 の実装の形が詳細設計の前提と違えば、Task 5 の前に設計へ戻す |
| 前の版が始めた耐久ワークフローは `touches_every` を持たない | 0 として読み、観測せず、完了に最後の観測を求めない。Task 3・4 のテストで縛る |
| 3000 件を超えるファイルを変えた単位の PR は、後ろのファイルが記録されない | 設計書の「未確認のまま残ること」のとおり、実機の観測で見る |

## 切り戻し手順
- 単位の Pull Request の revert で戻せる。テーブルの形は変えず、移行は無い
- 戻す前に記録した事象 3 件は、計画のステートマシンに残る。戻した版の畳み込み（`lane/fold.py` の `_fold_plan_event`）は、知らない種類の事象を読み飛ばす作りである。事象の定義の検査まで通るかは確かめていないので、戻すときに、新しい事象を持つ計画を戻した版で読めることを確かめる
- 戻す前に開いた `touches_overlap` のリクエストは、`ndf lanes request close` で閉じる

## 完了の定義
- [ ] 受け入れ条件 1〜6・8 をすべて満たし、条件ごとに検証手段と結果が対応している（7 は採らないので対象外）
- [ ] `standard` の検証の段階を通る（`ndf loop design-tests --issue 4295` を含む）
- [ ] `ndf check` と CI の検査が通る
