# #3879: 工程表のレビューの単位とリファクタリングの時機

## 関連リンク
- 要求仕様: [#3879](https://github.com/devbasex/ai-plugins/issues/3879)（写しは [issue-3879-requirements.md](./issue-3879-requirements.md)）。受け入れ条件は番号で指す
- 設計書: [issue-3879-design.md](./issue-3879-design.md)、詳細設計書: [issue-3879-design-detail.md](./issue-3879-design-detail.md)（設計 PR [#3883](https://github.com/devbasex/ai-plugins/pull/3883)、main へマージ済み）
- 前提の課題: [#3868](https://github.com/devbasex/ai-plugins/issues/3868)（実装 PR [#3882](https://github.com/devbasex/ai-plugins/pull/3882)、未マージ）。実装計画の「レビューの単位」「リファクタリングの時機」の欄と `document-roles.md` を置く
- モード: `standard`（工程の定義の語彙・テンプレートのステップ・コマンドの引数という公開インタフェースと、実装のフェーズのレビューの振る舞いを変えるため）

## 修正対象
（詳細設計書の「地図」と構成要素 1〜11 から引き写す。#3882 の欄は #3882 の重なりを示す）

| ファイル | 変える中身 | 構成要素 | #3882 と重なる |
| --- | --- | --- | --- |
| `plugins/ndf/contracts/templates/impl.json` | `base-fetch`・`review`・`push-reviewed`・`review-head`・`head-reviewed`・`review-delta` を足し、`acceptance` を差し込み `{{acceptance}}` にする | 1 | 無し |
| `plugins/ndf/contracts/templates/params.json` | `acceptance`（`list: true`）を足す | 1 | 無し |
| `plugins/ndf/src/ndf/core/route/__init__.py` | `REVIEW_STATE`・`REVIEW_WITH_PR`・`review_launch` を移し、`launch_values`・`check_acceptance` を新設、`_write_plan` で通す | 4 | 無し |
| `plugins/ndf/src/ndf/ci_cli.py` | `merge --reviewed <sha>` | 8 | 無し |
| `plugins/ndf/src/ndf/domains/loop/acceptance.py` | `check` の `--issue` の繰り返しと `--criteria`、`record` のロックと読み直し | 7 | 無し |
| `plugins/ndf/contracts/workflow/context.schema.json` | `gathered` を足す | 2 | 無し |
| `plugins/ndf/contracts/workflow/stages/implement.json` | `outputs` に `gathered` | 2 | 無し |
| `plugins/ndf/contracts/workflow/stages/code-review.json` | `optional_inputs: [gathered]`・`when: code_in_unreviewed_diff`・範囲 `unreviewed_diff` | 2 | 無し |
| `plugins/ndf/contracts/workflow/stages/refactor.json` | `inputs` に `issues` | 2 | 無し |
| `plugins/ndf/contracts/workflow/ndf.schema.json` | `range` に `unreviewed_diff`、`when` に `code_in_unreviewed_diff`、`optional_inputs` | 2 | 無し |
| `plugins/ndf/src/ndf/progress/stages.py` | `DIFF_RANGES`・I9 の文言・I8a の照らし | 6 | 無し |
| `plugins/ndf/src/ndf/progress/definition.py` | `optional_inputs` の読み込み、`_context_key_errors` の照らす欄 | 6 | 無し |
| `plugins/ndf/src/ndf/progress/command.py` | `unreviewed_range` 新設・`facts_of` に `code_in_unreviewed_diff`、`new --carry` と `_prepare_new` | 3・9 | 無し |
| `plugins/ndf/src/ndf/progress/invoke.py` | `template_values` で `route.launch_values`・`check_acceptance` を通す、`resolve_inputs` の `optional_inputs`、`_fill_review` の `gathered`、`_fill_refactor` 新設 | 4・5 | 無し |
| `plugins/ndf/src/ndf/domains/crossrefactor/cli.py`・`drive.py`・`prompt_decide.py` | `--candidates`、`RunConfig.candidates`、`args()` の `--candidates-sha256`、`propose` の候補の節 | 10 | 無し |
| `plugins/ndf/tests/progress/test_invoke.py`・`test_command.py`・`test_definition.py`、`tests/domains/loop/test_loop_acceptance.py`、`tests/route/test_templates.py`・`test_cli.py`・`test_flows.py`、`tests/adapters/test_ci_cli.py`、`tests/domains/crossrefactor/test_prompt_decide.py`・`test_drive.py` | 構成要素ごとの「単体テストの観点」 | 1〜10 | 無し |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 「工程が動く単位」の表、「工程ごとの決まり」のリファクタリング・コードレビュー・文書を作る 4 行・完了判定の行 | 11 | **有り** |
| `plugins/ndf/skills/development-workflow/references/stage-notes.md` | リファクタリングの 3 つの時機と候補の渡し方、つなぎ目のレビュー | 11 | **有り** |
| `plugins/ndf/skills/development-workflow/references/parallel-work.md` | 「工程が動く単位」の表、「課題の PR はコードレビューを通さない」と `fast` の列を消す | 11 | 無し |
| `plugins/ndf/skills/development-workflow/references/workflow-modes.md` | 「標準フロー」の図 | 11 | 無し |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | フェーズの表の実装・検査の行、単位ごとのコマンド、`ndf ci merge --reviewed`、`gathered` | 11 | 無し |
| `plugins/ndf/skills/quality-gates/references/design-match.md` | #3882 が置く「設計書を省いた変更」の節に、流し直しを始める判定 | 11 | **有り** |
| `plugins/ndf/skills/implementation-plan/SKILL.md` | 単位の欄の行が実行計画の行と実装のプランになること、`acceptance` と `--candidates` への渡り | 11 | **有り** |
| `plugins/ndf/skills/issue-plan-strategy/references/execution-plan.md` | 実装の行の作り方（仮の行 → 単位の行） | 11 | **有り** |
| `docs/glossary/glossary.json`・`docs/glossary/ndf-branch-strategy.md`・`docs/glossary.md` | 4 語の反映と `ndf records glossary render` | 11 | **有り**（`glossary.json`） |

設計書の「設計の結果」の表に無く詳細設計書にある `quality-gates/references/design-match.md`・`crossrefactor/drive.py`・`tests/domains/crossrefactor/test_drive.py` は、詳細設計書の構成要素 10・11 に従って含める。

## タスク分解

変更の種類は機能追加なので、各単位の最初のタスクを今の振る舞いを縛る退行テストにする（既存のテストが既に縛っている所は足さない）。テストの合否は PR の CI で見る（`.ndf/project.json` の `test.ci.only`）。

### Task 1: 受け入れ条件の確かめの今の振る舞いを縛る
- **対象ファイル:** `plugins/ndf/tests/domains/loop/test_loop_acceptance.py`
- **変更内容:** `--issue` 1 つ・`--criteria` なしの `check` の結果（欠けの `name` が条件の番号だけ）と、`record` が既存の記録を残すことを縛る
- **満たす受け入れ条件:** 1（退行しないこと）
- **進め方:** 今のコードで通るテストを足す（縛るためのテストなので、先に落ちるテストにはならない）

### Task 2: `check` を単位の分に絞る（構成要素 7）
- **対象ファイル:** `plugins/ndf/src/ndf/domains/loop/acceptance.py`、`plugins/ndf/tests/domains/loop/test_loop_acceptance.py`
- **変更内容:** `--issue` を `action="append"` に、`--criteria <課題>:<条件>` を足す。課題ごとに照らしてまとめ、欠けの `name` は `<課題>:<条件>`。渡した課題に無い番号・本文に無い条件は `invalid`
- **満たす受け入れ条件:** 1
- **進め方:** 詳細設計の観点（`--criteria 1:1 --criteria 1:2` で `ok`・`--issue 1 --issue 2` で `2:<番号>`・`--criteria 1:9` で `invalid`）を失敗するテストで書く → 最小実装 → 整理

### Task 3: `record` を課題ごとのロックと読み直しで直列にする（構成要素 7）
- **対象ファイル:** `plugins/ndf/src/ndf/domains/loop/acceptance.py`、`plugins/ndf/tests/domains/loop/test_loop_acceptance.py`
- **変更内容:** `<git-common-dir>/ndf/acceptance-<課題>.lock` の `FileLock`（待ちの上限 `limits.seconds("locks.append")`）の中で読む・書く・読み直す。そろわなければ当て直し、3 回で `conflict`
- **満たす受け入れ条件:** 1
- **進め方:** 偽の GitHub で 2 つの `record` を割り込ませるテスト・戻し続けるテストを先に書く → 実装

### Task 4: 実装のプランとレビューの起動の今の組み方を縛る
- **対象ファイル:** `plugins/ndf/tests/progress/test_invoke.py`、`plugins/ndf/tests/route/test_cli.py`
- **変更内容:** 工程から組む経路の `{{review}}` の既定の並び（`review_launch`）と、`ndf route render implement` が今組むプランの `acceptance` のステップを縛る
- **満たす受け入れ条件:** 1（退行しないこと）
- **進め方:** 今のコードで通るテストを足す

### Task 5: 差し込みの補いを `core.route` に置き、2 つの経路で共有する（構成要素 4・5 の一部）
- **対象ファイル:** `plugins/ndf/src/ndf/core/route/__init__.py`、`plugins/ndf/src/ndf/progress/invoke.py`、`plugins/ndf/tests/route/test_cli.py`、`plugins/ndf/tests/progress/test_invoke.py`
- **変更内容:** 先に `REVIEW_STATE`・`REVIEW_WITH_PR`・`review_launch` を `invoke.py` から `core.route` へ移す（振る舞いを変えない。Task 4 のテストがそのまま通る）。続けて `launch_values`・`check_acceptance` を新設し、`_write_plan` と `invoke.template_values` から通す
- **満たす受け入れ条件:** 1
- **進め方:** 移す → Task 4 のテストが通ることを確かめる → 詳細設計の観点（決定 1 のコマンドそのままで `--base origin/sprint/x`、`--issue` なしの `acceptance` で `RouteError`、`impl` の `{{review}}` が両経路で同じ値）を失敗するテストで書く → 実装

### Task 6: 実装のテンプレートに単位の PR のレビューを足す（構成要素 1）
- **対象ファイル:** `plugins/ndf/contracts/templates/impl.json`、`plugins/ndf/contracts/templates/params.json`、`plugins/ndf/tests/route/test_templates.py`、`plugins/ndf/tests/route/test_flows.py`
- **変更内容:** `pr → conflict-check → base-fetch → review → push-reviewed → review-head → ci → head-reviewed → end` と `review-delta` の周回、`acceptance` の差し込み
- **満たす受け入れ条件:** 1
- **進め方:** ステップの順と流れのテスト（`ci-fix` の後に `review-delta` が `--base <reviewed-head>` で打たれる）を先に書く → テンプレートを直す。設計書の「未確認のまま残ること」の `--base <リモート>/<base>` の振る舞いも、このタスクのテストで確かめる

### Task 7: レビューした HEAD でだけマージする（構成要素 8）
- **対象ファイル:** `plugins/ndf/src/ndf/ci_cli.py`、`plugins/ndf/tests/adapters/test_ci_cli.py`
- **変更内容:** `merge --reviewed <sha>`。HEAD と違えば `refused`
- **満たす受け入れ条件:** 1
- **進め方:** 3 つの観点（同じ・違う・なし）を失敗するテストで書く → 実装

### Task 8: つなぎ目に関わる今の振る舞いを縛る
- **対象ファイル:** `plugins/ndf/tests/progress/test_invoke.py`、`plugins/ndf/tests/progress/test_command.py`、`plugins/ndf/tests/progress/test_definition.py`
- **変更内容:** `gathered` が無いとき工程「コードレビュー」のプランの `--base` がベースブランチであること、文書だけの差分でも工程が起動すること、今の I8a・I9 の断り方を縛る
- **満たす受け入れ条件:** 3（走り中のスプリントが今のとおり動くこと）
- **進め方:** 今のコードで通るテストを足す

### Task 9: 工程の定義と語彙に `gathered`・`unreviewed_diff`・`optional_inputs` を足す（構成要素 2・6）
- **対象ファイル:** `plugins/ndf/contracts/workflow/context.schema.json`、`stages/implement.json`、`stages/code-review.json`、`ndf.schema.json`、`plugins/ndf/src/ndf/progress/stages.py`、`plugins/ndf/src/ndf/progress/definition.py`、`plugins/ndf/tests/progress/test_definition.py`
- **変更内容:** 詳細設計の構成要素 2（`refactor.json` を除く）と 6。`optional_inputs` の誤記を読み込みで断る
- **満たす受け入れ条件:** 3・5
- **進め方:** I8a で後ろへ動かした定義を断るテスト・`optional_inputs: ["gathred"]` を断るテストを先に書く → 定義とコードを直す

### Task 10: つなぎ目の範囲と工程の条件（構成要素 3）
- **対象ファイル:** `plugins/ndf/src/ndf/progress/command.py`、`plugins/ndf/tests/progress/test_command.py`
- **変更内容:** `unreviewed_range` を新設し、`facts_of` に `code_in_unreviewed_diff` を足す。`gathered` が読めない・祖先でない・ブランチが違うときは `FactUnknown`
- **満たす受け入れ条件:** 3
- **進め方:** 詳細設計の 8 つの観点を一時のリポジトリで失敗するテストに書く → 実装

### Task 11: 工程「コードレビュー」の起点を `gathered` にする（構成要素 5 の `resolve_inputs`・`_fill_review`）
- **対象ファイル:** `plugins/ndf/src/ndf/progress/invoke.py`、`plugins/ndf/tests/progress/test_invoke.py`
- **変更内容:** `resolve_inputs` が `optional_inputs` を文脈にあるときだけ取る。`_fill_review` が `unreviewed_range` で確かめた `gathered` を `--base` にする（設定の起動の並びには後ろへ足す）
- **満たす受け入れ条件:** 2・3
- **進め方:** 詳細設計の観点（`gathered` ありで `--base <sha>`、無しで今のまま、設定の並びの後ろに足す）を先に書く → 実装

### Task 12: cross-refactoring の今の引数とプロンプトを縛る
- **対象ファイル:** `plugins/ndf/tests/domains/crossrefactor/test_drive.py`、`plugins/ndf/tests/domains/crossrefactor/test_prompt_decide.py`
- **変更内容:** `--candidates` なしの `args()` の並びと `propose` の文字列を縛る
- **満たす受け入れ条件:** 2（退行しないこと）
- **進め方:** 今のコードで通るテストを足す

### Task 13: cross-refactoring が候補を受ける（構成要素 10）
- **対象ファイル:** `plugins/ndf/src/ndf/domains/crossrefactor/cli.py`、`drive.py`、`prompt_decide.py`、`plugins/ndf/tests/domains/crossrefactor/test_drive.py`、`test_prompt_decide.py`
- **変更内容:** `--candidates <ファイル>`、`RunConfig.candidates`、`args()` に `--candidates-sha256`、`propose` に候補の節。候補の中身が変われば再利用しない
- **満たす受け入れ条件:** 2
- **進め方:** 詳細設計の `test_drive.py` の 5 つの観点を先に書く → 実装

### Task 14: 工程「リファクタリング」が実装計画の候補を渡す（構成要素 2 の `refactor.json`・構成要素 5 の `_fill_refactor`）
- **対象ファイル:** `plugins/ndf/contracts/workflow/stages/refactor.json`、`plugins/ndf/src/ndf/progress/invoke.py`、`plugins/ndf/tests/progress/test_invoke.py`、`plugins/ndf/tests/progress/test_definition.py`
- **変更内容:** `refactor.json` の `inputs` に `issues`。`_fill_refactor` が `<worktree>/issues/issue-<番号>-plan.md` の「リファクタリングの時機」の表から「スプリントの終わり」の行を集めて `refactor-candidates.md` に書き、`--candidates` を足す。表の形は #3882 の `implementation-plan` の SKILL.md の「リファクタリングの時機」に合わせ、テストの実装計画もその形で作る
- **満たす受け入れ条件:** 2
- **進め方:** 実際の `refactor.json` を `definition` で読んで組むテストを先に書く → 実装

### Task 15: ステートマシンの作成の今の振る舞いを縛る
- **対象ファイル:** `plugins/ndf/tests/progress/test_command.py`
- **変更内容:** 出力を持たない工程だけを `--disable` した `ndf progress new` が今のとおり作れることを縛る
- **満たす受け入れ条件:** 4（退行しないこと）
- **進め方:** 今のコードで通るテストを足す

### Task 16: 流し直しで作業場所を引き継ぐ `--carry`（構成要素 9）
- **対象ファイル:** `plugins/ndf/src/ndf/progress/command.py`、`plugins/ndf/tests/progress/test_command.py`
- **変更内容:** `new --carry <ID>`。無効にした工程の `outputs` のキーを中止したステートマシンの文脈から写す。詳細設計の 5 つの異常系で断る
- **満たす受け入れ条件:** 4
- **進め方:** CLI の流れ（`new s1` → `finish` → `cancel` → `new s1-redesign --carry`）と異常系を先に書く → 実装

### Task 17: 工程表と工程の手順書を新しい工程表にそろえる（構成要素 11）
- **対象ファイル:** `plugins/ndf/skills/development-workflow/SKILL.md`、`references/parallel-work.md`、`references/stage-notes.md`、`references/workflow-modes.md`、`references/agent-layers.md`
- **変更内容:** 詳細設計の構成要素 11 の各行。文書を作る 4 つの行は #3882 の `requirements-design/references/document-roles.md` を指し、条件を写さない。#3882 が工程「詳細設計」の行に置いた中身を新しい「工程ごとの決まり」へ移す
- **満たす受け入れ条件:** 2・4・5
- **進め方:** テスト駆動を適用しない（`.md` の文言のテストは書かない）。`test_stage_table.py` と参照の検査（`ndf check`）で確かめる

### Task 18: 流し直しを始める判定を置く（構成要素 11 の `design-match.md`）
- **対象ファイル:** `plugins/ndf/skills/quality-gates/references/design-match.md`
- **変更内容:** #3882 が置く「設計書を省いた変更」の節に、数え直しの手順 (1)〜(4) を足す。条件は節の既存の文を指し、2 か所に書かない
- **満たす受け入れ条件:** 4
- **進め方:** テスト駆動を適用しない（Markdown）。参照の検査で確かめる

### Task 19: 実装計画と実行計画の手順書に単位の行き先を書く（構成要素 11）
- **対象ファイル:** `plugins/ndf/skills/implementation-plan/SKILL.md`、`plugins/ndf/skills/issue-plan-strategy/references/execution-plan.md`
- **変更内容:** #3882 の「レビューの単位」の節の後に、欄の行が実行計画の行と実装のプランになることと `acceptance`・`--candidates` への渡りを書く。実行計画の実装の行を「仮の行 → 単位の行」にする
- **満たす受け入れ条件:** 1・2
- **進め方:** テスト駆動を適用しない（Markdown）

### Task 20: 用語集に 4 語を反映する（構成要素 11）
- **対象ファイル:** `docs/glossary/glossary.json`、`docs/glossary/ndf-branch-strategy.md`、`docs/glossary.md`
- **変更内容:** 「レビューの単位」の意味の置き換えと、「単位の PR」「つなぎ目」「集めた時点」の追加。`ndf records glossary render` で作り直す
- **満たす受け入れ条件:** 5
- **進め方:** テスト駆動を適用しない。用語集の検査で確かめる

## レビューの単位

単位ごとにブランチ（`feature/issue-3879-u<N>`）と PR を分け、その PR で cross-review を収束まで回す。#3882 の前に着手できるかは、単位が #3882 の触るファイル（`development-workflow` の SKILL.md・`stage-notes.md`・`approval-request.md`・`implementation-plan` の SKILL.md・`execution-plan.md`・`parallel-plan.md`・`design-match.md`・`document-roles.md`・用語集など 39 ファイル）を触るか、#3882 が置く文書と欄を前提にするかで決めた。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 着手の条件 | #3882 の前に着手できるか | 実際の行数 |
| --- | --- | --- | --- | --- | --- | --- |
| U1 受け入れ条件の確かめを単位の分に絞る | Task 1・2・3 | 1 | 約 350 | 無し | できる | 467 |
| U2 単位の PR のレビューとレビューした HEAD でのマージ | Task 4・5・6・7 | 1 | 約 450 | 無し | できる | 582 |
| U3 つなぎ目のレビュー | Task 8・9・10・11 | 2・3・5 | 約 450 | U2 のマージ後（`invoke.py` の補いを U2 が `core.route` へ移すため） | できる | 776 |
| U4 スプリントの終わりの候補 | Task 12・13・14 | 2 | 約 300 | U2 のマージ後（`invoke.template_values` を U2 が変えるため） | できる。ただし Task 14 の表の形は #3882 の欄に合わせ、#3882 のレビューで欄が変われば追う | 301 |
| U5 流し直しの `--carry` | Task 15・16 | 4 | 約 250 | 無し | できる | 228 |
| U6 手順書と用語集 | Task 17・18・19・20 | 1・2・4・5 | 約 350（`docs/glossary.md` は再生成なので数えない） | #3882 のマージ後、かつ U1〜U5 のマージ後（手順書が引くコマンドと引数がそろってから） | できない（#3882 と同じファイルを触り、#3882 が置く `document-roles.md` と `design-match.md` の節を指す） | 322 |

行数は `git diff --shortstat <ベース>...HEAD -- . ':(exclude)docs/glossary.md'` で数える。

- **U2 と U3 は目安の 400 行を少し超える見込みである。** U2 は補いの移設（構成要素 4）とテンプレート（構成要素 1）が同じ受け入れ条件 1 の流れで、片方だけではレビューのステップが組めない。U3 は工程の定義と、それを読むコードを分けると、定義だけの単位が範囲の語の検査（I9）で落ちる。どちらも上限の 1,000 行は超えない。実装で 600 行を超えたら、U2 は Task 7 を、U3 は Task 11 を別の単位へ分け直す
- **U3 は 600 行を超えた（776 行）。** 超えた主な分はテスト（464 行。U4 のテストとの重なりで `test_invoke.py` から分けた `test_invoke_refactor.py` の 115 行を含む）と、レビューで足した `ndf progress gathered`（`command.py`）で、上限の 1,000 行は超えない
- **着手の順:** U1・U2・U5 を並列に始め、U2 のマージ後に U3・U4 を並列に始める。U6 は #3882 と U1〜U5 のマージを待つ
- **設計書の「実装は #3868 の実装が入った後のベースブランチから切る」との違い:** U1〜U5 は #3882 の触るファイルを 1 つも触らないので、#3882 の前のベースブランチから切っても衝突しない。設計書のこの決まりは同じファイルを触る U6 にだけ当てる

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `invoke.py` は 764 行で 20 タスク中 4 タスク（Task 4・5・11・14）が触り、テストは `test_invoke.py` に 59 件。`command.py` は 695 行で 4 タスク（Task 10・15・16 と Task 8 のテスト）が触り、テストは `test_command.py` に 54 件。`review_launch` の移設は U2 の Task 5 の頭で振る舞いを変えずに行い、Task 4 の退行テストで縛る |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `invoke.py` の `template_values` の補いの並び（U2 の `launch_values`・U3 の `_fill_review`・U4 の `_fill_refactor` が順に重なる）と、`command.py` の作成と条件の関数（U3 の `unreviewed_range`・U5 の `_prepare_new`） | 3 つの単位が同じ関数の前後へ足すので、単位の中では重なりが見えない。`test_invoke.py` のプランを組むフィクスチャも U2・U3・U4 が別々に足す |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #3882 のレビューで「リファクタリングの時機」の欄の形や `design-match.md` の節が変わる | U4 の Task 14 は #3882 の最新の head の表の形でテストを作り、U4 の PR を出す前に #3882 の差分を読み直す。U6 は #3882 のマージ後に切るので、マージされた形に合わせる |
| U3 と U4 が並列に `invoke.py` を触り、後にマージする側が衝突する | 触る関数が違う（`resolve_inputs`・`_fill_review` と `_fill_refactor`）。後の単位はスプリントブランチの取り込み（`base-sync`）で解き、取り込みの後の差分は U2 の `review-delta` がレビューする |
| cross-review に `--base <リモート>/<base>` を渡したときの振る舞いが未確認（設計書の「未確認のまま残ること」） | U2 の Task 6 のテストで確かめる。ブランチ名として扱う箇所が見つかれば U2 の中で直す |
| U2 がマージされた後、U3〜U6 の単位の PR そのものに新しいレビューのステップが掛かるのは、NDF をリリースしてからである | この課題の単位の PR は今の手順（手元の cross-review）でレビューする。新しいステップの確かめはリリース後テスト（要求仕様の「検証の方法」）で行う |

## 切り戻し手順
- 単位ごとの PR を revert で戻せる。データの移行は無い
- 文脈のキー `gathered` は任意の入力なので、U3 を戻しても `gathered` を書いた記録は読める（読まれないキーになるだけ）。U3 を戻すときは U2 の後の単位から逆の順に戻す必要は無いが、U6 の手順書は U1〜U5 を指すので、U1〜U5 のどれかを戻すときは U6 の該当する記述も戻す

## 完了の定義
- [ ] 受け入れ条件 1〜5 をすべて満たし、条件ごとに検証の方法（詳細設計の単体テストの観点・`test_stage_table.py`・`ndf check`）と結果が対応している
- [ ] 単位ごとの PR で cross-review が収束し、PR の CI が通っている
- [ ] 「レビューの単位」の表の実際の行数を書き足している
- [ ] 設計との突き合わせ（`quality-gates` の `design-match.md`）で、詳細設計の構成要素 1〜11 が実装と一致している
