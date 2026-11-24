# #4409: 自動反映の本番チャネルへのマージを、リリース承認ゲートの回答まで止める

## 関連リンク

- 要求仕様: [issue-4409-requirements.md](./issue-4409-requirements.md)（ https://github.com/devbasex/ai-plugins/issues/4409 ）。受け入れ条件は `4409:<番号>` で指す
- 設計書: [issue-4409-design.md](./issue-4409-design.md)、詳細設計書: [issue-4409-design-detail.md](./issue-4409-design-detail.md)
- モード: `standard`。公開インタフェースが変わり（要求仕様の「影響」）、触るファイルが 45 あるため、実装計画は省けない

## 修正対象

詳細設計書の「地図」から引き写す。関数ごとの仕様は詳細設計書の各節にある。パスは `plugins/ndf/` からの相対で書く。

| ファイル | 変える中身 |
| --- | --- |
| `src/ndf/core/production_merge.py`（新設） | 判定の部品 `Verdict`・`judge`・`automatic_rows`・`needs_approval`・`reaches_production` |
| `src/ndf/domains/hooks/strict.py` | `_judge`・`_deliveries` が部品を呼ぶ。`_writes_merge_mark` を足し、`_automatic_production` を消す |
| `contracts/hooks/imports.json` | hook の import の表に `ndf.core`・`ndf.core.production_merge` を足す |
| `src/ndf/core/merge_approval.py`（新設） | `MergeTarget`・`MergeRow`・`MERGE_MARK`・`render_merge_row`・`merge_rows`・`merge_judgment`・`write_merge_row` を足し、担い手の語・`BY_LINE`・`TrailError` を `domains/release/trail.py` から移す。工程（`progress/gate`）とドメイン（`domains/release`・`domains/records`）の両方が読むので、層の規則（`ndf check` の規則 `layer`）に合わせて両方より下の `ndf.core` に置く |
| `src/ndf/core/approval_words.py`（新設） | 回答の語 `APPROVED`・`REJECTED`・`ANSWERS` と、承認資料が対象にしたコミットの行の見出し `HEAD_LINE` を `domains/release/approval_decide.py` から移す |
| `src/ndf/core/actors.py`（新設） | 回答した者の形 `ACTORS`・`HUMAN`（`ndf answer --by` と `ndf plan next --by` が共有する） |
| `src/ndf/domains/release/trail.py`・`approval_decide.py`・`cli.py` | 担い手の語・`TrailError`・回答の語を `ndf.core` から読む。振る舞いは変えない |
| `src/ndf/checks/children.json` | 新設したモジュール（`ndf.core.actors`・`ndf.core.approval_words`・`ndf.core.merge_approval`・`ndf.core.production_merge`）のオーナーの子 `PM1` と、それを読む子の先に要る子を足す（設計書の「子の表の変更」） |
| `contracts/causes/approval_stale.json`（新設） | 停止理由 `approval_stale` の定義 |
| `src/ndf/adapters/github.py` | `branch_file`・`viewer`・`_verify` を足し、`merge`・`_merge_with`・`_enqueue` に `expect` を足す |
| `src/ndf/ci_cli.py` | `ndf ci merge` に判定・照合・証跡の確かめと `--production-gate` を足し、`ndf ci ask`・`ndf ci answer` を足す |
| `src/ndf/core/plan/cli.py`・`src/ndf/core/plan/runner.py` | `ndf plan next` に `--by` を足し、回答の記録 `answer-records/<ステップ>.json` を書く |
| `src/ndf/progress/run.py` | `stale_routes`・`_failed_report`（`approval_stale` の経路）、`_consume`（MVV 通過の `seen`）、`migrate_definition`・`_migrated_order`・`view`・`advance`（写しの移行） |
| `src/ndf/progress/gate/asking.py`・`answering.py`・`entry.py` | 質問の対象に承認の対象を足し、回答の前に証跡を書き、承認資料に本番へ反映され得る行を書く |
| `src/ndf/domains/records/mvv_gate.py`・`contracts/workflow/context.schema.json` | MVV 判定の通過で判定の記録のコメントに証跡の行を入れ、出力 `mvv_gate` に `target` を足す |
| `contracts/templates/parts/steps.json`・`params.json`・`{merge,plan-to-spec,close,fix,check,design-merge,release}.json` | `merge` の引数、`approve-*`・`reject-*`、`release-merge`、差し込みの既定（詳細設計書の「共有のステップとテンプレート」） |
| `src/ndf/progress/invoke.py` | `_fill_production_gate`・`_approved_target`・`PRODUCTION_GATE` |
| `contracts/workflow/modes/{standard,light,operation,documentation,legacy-refactor}.json` | クリーンアップをデプロイの直後へ移す |
| `skills/development-workflow/SKILL.md`・`references/{production-merge,workflow-modes,agent-layers}.md` | 新しいマージの流れと工程の順を書く |
| テスト | 設計書の「設計の結果」の 14 本（各タスクの対象ファイル） |

## タスク分解

### Task 1: 再現のテストを書く

- **対象ファイル:** `tests/progress/test_production_merge_flow.py`（新設）
- **変更内容:** 要求仕様の「再現手順」の 3 つ（自動反映の設定・工程「Pull Request」まで進めて PR を開いたまま・`ndf progress next`）を偽物の GitHub と Git で打ち、スプリントの PR へのマージの呼び出しが 0 回であることを確かめる。今は確定仕様化のプランがマージを呼ぶので落ちる。落ちることを確かめた後、単位ごとにテストが通るよう `pytest.mark.xfail(strict=True)` を付けてコミットし、Task 12 で外す
- **満たす受け入れ条件:** 4409:12（Task 12 で満たす）
- **進め方:** 失敗するテストだけを書く。実装は後のタスク

### Task 2: 本番チャネルの判定の部品を作る

- **対象ファイル:** `src/ndf/core/production_merge.py`（新設）、`tests/core/test_production_merge.py`（新設）
- **変更内容:** 詳細設計書の「本番チャネルの判定の部品」の処理の手順の表を、1 行ずつテストにしてから実装する。標準ライブラリだけを読む
- **満たす受け入れ条件:** 4409:7・4409:8・4409:9（判定の値。マージの振る舞いは Task 6）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: hook の guard `merge-production` を部品へ移し、証跡のマーカーを含む Bash を止める

- **対象ファイル:** `src/ndf/domains/hooks/strict.py`、`contracts/hooks/imports.json`、`tests/domains/hooks/test_strict.py`
- **変更内容:** 今の入力で今と同じ結果になる退行テストを先に足し、`_judge` を `automatic_rows` の値で決める形へ変える。部品の戻り値を差し替えると hook の結果が変わることを確かめる。`_writes_merge_mark` で `ndf:merge-approval` を含む `gh pr comment`・`gh issue comment`・`gh api` を止める
- **満たす受け入れ条件:** 4409:5（Bash からの証跡の投稿）、4409:9（hook 側）、設計の決定 2
- **進め方:** 退行テスト → 失敗するテスト → 通す最小実装 → 整理

### Task 4: マージの承認の証跡を読み書きする

- **対象ファイル:** `src/ndf/core/merge_approval.py`（新設）、`src/ndf/core/approval_words.py`（新設）、`src/ndf/core/actors.py`（新設）、`src/ndf/domains/release/trail.py`・`approval_decide.py`・`cli.py`（読む先の付け替え）、`src/ndf/checks/children.json`、`contracts/causes/approval_stale.json`（新設）、`tests/domains/release/test_trail.py`
- **変更内容:** 詳細設計書の「承認の対象とマージの承認の証跡」の関数を `core/merge_approval.py` に足す。`domains/release/trail.py` には置かない。工程の側（`progress/gate`）が読むので、`domains` に置くと層の規則（`ndf check` の規則 `layer`）に反するためである。担い手の語と `TrailError` は `core/merge_approval.py` へ、回答の語は `core/approval_words.py` へ移し、`domains/release` はそこから読む。新設したモジュールは `checks/children.json` に子 `PM1` として載せる。`tests/domains/release/test_trail.py` は設計書では既存の扱いだが、今は無いので新設し、`core/merge_approval.py` の証跡の読み書きと、`trail.read` が証跡を数えないことをまとめて確かめる
- **満たす受け入れ条件:** 4409:5（書き手・後からの却下・本番へ反映されるか）、設計の決定 7（CD の workflow との境界）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 5: GitHub のアダプタにマージ先の設定の読み取りと直前の照合を足す

- **対象ファイル:** `src/ndf/adapters/github.py`、`tests/adapters/test_github_merge.py`
- **変更内容:** `branch_file`・`viewer`・`_verify` を足し、`merge`・`_merge_with`・`_enqueue` に `expect` を足す。`expect` が無い呼び出しの要求の数が今と同じことを先に縛る。`tests/adapters/test_github_merge.py` は設計書では既存の扱いだが、今は無いので新設する（既存の `test_github.py`・`test_merge_queue.py` を膨らませず、照合の観点を 1 本にまとめるため）
- **満たす受け入れ条件:** 設計の決定 3（要求の数・作業場所の設定を読まない）、設計の決定 8
- **進め方:** 退行テスト → 失敗するテスト → 通す最小実装 → 整理

### Task 6: `ndf ci merge` が判定と証跡を確かめてからマージする

- **対象ファイル:** `src/ndf/ci_cli.py`、`src/ndf/progress/run.py`（`stale_routes`・`_failed_report`）、`tests/adapters/test_ci_cli.py`
- **変更内容:** `--pr`・`--production-gate {stop,defer,stale}`・`--approved-head`・`--approved-base`・`--presented-out` を足し、`_pr_target`・`_judge_pr`・`_unapproved` を足す。`approval_stale` で止まった工程に `ndf progress retry` と `ndf progress next` の経路を載せる
- **満たす受け入れ条件:** 4409:2・4409:4・4409:5（承認の記録が無い・HEAD が変わった）・4409:7・4409:8・4409:9、設計の決定 5
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 7: `ndf ci ask`・`ndf ci answer` と、`ndf plan next --by` の回答の記録

- **対象ファイル:** `src/ndf/ci_cli.py`（`_ask`・`_answer`・`_presented`）、`src/ndf/core/plan/cli.py`、`src/ndf/core/plan/runner.py`、`tests/adapters/test_ci_cli.py`、`tests/core/plan/test_runner.py`
- **変更内容:** 回答の経路を `ndf plan next <プラン> --answer <回答> --by human` にし、回答の記録に提示の識別子を写す。`ndf ci answer` は回答の記録と提示のファイルを照らしてから証跡を書く
- **満たす受け入れ条件:** 4409:4・4409:5（回答の記録を経ない呼び出し・提示と回答の結び付き）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 8: 工程の承認ゲートで、回答の前に証跡を書く

- **対象ファイル:** `src/ndf/progress/gate/asking.py`、`src/ndf/progress/gate/answering.py`、`src/ndf/progress/gate/entry.py`、`tests/progress/test_gate_answering.py`
- **変更内容:** `Target` に承認の対象を足し、提示した対象を承認資料の隣に書く。`answer` は証跡を書いてから回答を記録し、`_same` の早期の戻りの前にも証跡を確かめる。承認資料に本番へ反映され得る行を書く。`tests/progress/test_gate_answering.py` は設計書では既存の扱いだが、今は無いので新設する
- **満たす受け入れ条件:** 4409:5（organizer の中継・質問の後にマージ先が変わった・証跡の書き込みの失敗）・4409:6（承認資料）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 9: MVV 判定の通過で証跡を書き、承認の対象を回答の事象へ残す

- **対象ファイル:** `src/ndf/domains/records/mvv_gate.py`、`contracts/workflow/context.schema.json`、`src/ndf/progress/run.py`（`_consume`）、`tests/progress/test_gate_answering.py`
- **変更内容:** 省くと決めた判定のときだけ、判定の記録のコメントに担い手 `mvv` の証跡の行を入れる。`mvv_gate.target` を回答の事象の `seen` へ写す
- **満たす受け入れ条件:** 4409:5（担い手が MVV 判定）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 10: 共有のステップ・テンプレート・差し込みを変える

- **対象ファイル:** `contracts/templates/parts/steps.json`、`contracts/templates/params.json`、`contracts/templates/{merge,plan-to-spec,close,fix,check,design-merge,release}.json`、`src/ndf/progress/invoke.py`、`tests/progress/test_sprint_templates.py`、`tests/progress/test_invoke_template_values.py`
- **変更内容:** 詳細設計書の「共有のステップとテンプレート」と「差し込みの組み立て」のとおり。`merge` の `ok`（`deferred` を含む）が `approve-merge` へ流れないこと、`approve-*`・`reject-*` が `on_answer` からしか到達しないことを縛る
- **満たす受け入れ条件:** 4409:1・4409:2・4409:3・4409:4・4409:5・4409:6、設計の決定 4・決定 5
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 11: クリーンアップをデプロイの後へ移し、古い写しを移行する

- **対象ファイル:** `contracts/workflow/modes/{standard,light,operation,documentation,legacy-refactor}.json`、`src/ndf/progress/run.py`（`migrate_definition`・`_migrated_order`・`view`・`advance`）、`tests/progress/test_definition.py`、`tests/progress/test_definition_migration.py`（新設）
- **変更内容:** 5 つのモードの工程の順を変え、このリリースの前に始めたスプリントの写しを事象 `definition_migrated` で並べ替える
- **満たす受け入れ条件:** 4409:10・4409:11
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 12: 手順書を直し、再現のテストを通す

- **対象ファイル:** `skills/development-workflow/SKILL.md`、`skills/development-workflow/references/production-merge.md`・`workflow-modes.md`・`agent-layers.md`、`tests/progress/test_production_merge_flow.py`
- **変更内容:** 新しいマージの流れ（工程の中は先送り、単発のプランは承認待ち、デプロイの最初にマージ）と工程の順を書く。Task 1 の `xfail` を外し、設計書のテスト設計の通しの行（受け入れ条件 1・5・10・12）を足す
- **満たす受け入れ条件:** 4409:1・4409:5・4409:10・4409:12
- **進め方:** 手順書はテスト駆動を適用しない（`.md` の文言を照合するテストを書かない方針のため）。通しのテストは Task 1 の失敗するテストを通す

## レビューの単位

見込みの差分が 1,000 行を超えるため、依存の向き（判定の部品 → 証跡とアダプタ → マージのコマンド → 工程の承認ゲート → テンプレート → 工程の順）で分ける。どの単位も単独でテストが通る。行数は `git diff --shortstat <ベース>...HEAD -- . ':(exclude)issues/issue-*'` で数える。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 判定の部品と hook | Task 1・2・3 | 4409:9 | 約 350 | （実装の後に書き足す） |
| 証跡と GitHub のアダプタ | Task 4・5 | 4409:5 | 約 600 | （実装の後に書き足す） |
| マージのコマンドとプランの回答 | Task 6・7 | 4409:4・4409:7・4409:8 | 約 700 | （実装の後に書き足す） |
| 工程の承認ゲートと MVV 判定 | Task 8・9 | 4409:6 | 約 600 | （実装の後に書き足す） |
| テンプレートと差し込み | Task 10 | 4409:2・4409:3 | 約 500 | （実装の後に書き足す） |
| 工程の順と手順書 | Task 11・12 | 4409:1・4409:5・4409:10・4409:11・4409:12 | 約 500 | （実装の後に書き足す） |

受け入れ条件の欄は、その単位で満たし終える条件だけを書く。4409:5 は単位 2〜6 にまたがり、最後の単位の通しのテストで満たし終える。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 最大の `adapters/github.py` は 1000 行だが 12 タスク中 1 つが触り、読むテストが 576 件ある。`progress/run.py` は 934 行で 12 タスク中 3 つ（25%）が触り、テストが 114 件ある。`progress/invoke.py` は 984 行で 1 タスクが触り、テストが 126 件ある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | 承認の対象の照合と、提示した対象の保存 | 承認の対象（`MergeTarget`）を今の PR と照らす処理が `ci_cli._merge`・`ci_cli._answer`（単位 3）と `answering._refusal`（単位 4）に分かれる。提示した対象を書く処理も `ci_cli._presented`（単位 3）と `asking.write_material`・`presented_path`（単位 4）の 2 つの形になる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| hook の guard が、マーカーを含まない通常のコメントまで止める | Task 3 で、マーカーの無いコメント・`ndf:release-approval` のコメント・標準入力の本文を通すテストを置く |
| 古い写しの状態機械が、クリーンアップで止まったまま進まない | Task 11 で、`precondition` で止まったクリーンアップを持つ写しの移行をテストで縛る |
| 単位ごとに入れると、途中の単位のマージ時点で工程の振る舞いが半端になる | 単位の PR の宛先はスプリントブランチで、`main` へはスプリントの PR がまとめて入る。工程の定義を変えるのは単位 5・6 だけにする |

## 切り戻し手順

- 単位の PR ごとに revert できる。依存の向きの逆（単位 6 → 1）で戻す
- 単位 6 を入れた版で事象 `definition_migrated` が書かれた状態機械を、戻した版が読めるかは（未確認: 旧版が未知の事象の種類をどう扱うか）。戻すときは、該当の状態機械を工程の頭から流し直す

## 完了の定義

- [ ] 受け入れ条件 4409:1〜12 をすべて満たし、条件ごとに設計書の「テスト設計」の行と検証の結果が対応している
- [ ] 単位ごとに範囲のテストが通り、最後に全体のテストが通る（`quality-gates`）
- [ ] 「レビューの単位」の実際の行数を書き足した
