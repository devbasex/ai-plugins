# #4337: 起動の設定が無いリポジトリでも、設計とドキュメント再構成が流れる

## 関連リンク

- 要求仕様: [issue-4337-requirements.md](./issue-4337-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4337 の本文）。受け入れ条件は「条件 N」で指す
- 設計書: [issue-4337-design.md](./issue-4337-design.md)、詳細設計書: [issue-4337-design-detail.md](./issue-4337-design-detail.md)
- モード: `standard`（本番の NDF の振る舞いを変える不具合の直し。要求仕様の「種類」）

実装計画を省かない理由: 要求仕様の「影響」の表で公開インタフェースが「変わる」（新しいサブコマンド `ndf document-rebuild run` が増える）ため、省ける条件を満たさない。

## 修正対象

詳細設計書の「地図」の表のとおりで、ここには引き写さない。パスは `plugins/ndf/` からの相対で書く。タスクごとの対象ファイルは下の「タスク分解」にある。地図の表に無いファイルは足さない。

## タスク分解

依存の向き（`core.issue_docs` ← `domains.docrebuild` ← 工程の準備が組むコマンド）の順に並べる。工程の準備が既定のコマンド `ndf document-rebuild run` を組むのは、サブコマンドができた後にする。先に組むと、マージした時点のスプリントブランチで、プランは組めても組み直すステップが「知らないコマンド」で落ちるためである。不具合の再現のテスト（今は落ちる）は、このため Task 7 の先頭に置く。

### Task 1: 設計書と実装計画の名前の決まりを 1 か所へ移す（準備のリファクタリング）

- **対象ファイル:** `src/ndf/core/issue_docs.py`（新設）、`src/ndf/domains/loop/design_tests.py`、`src/ndf/progress/refactor_values.py`、`src/ndf/checks/children.json`（`C3` の `packages` に `ndf.core.issue_docs`）
- **変更内容:** `design_tests.py` の正規表現 `DESIGN_FILE` と、`refactor_values.py` の書式 `PLAN_FILE` を `core.issue_docs` へ移す。名前に含む課題の番号を集合で返す `design_issues` を足し、`_designs` の照合をそれに替える（詳細設計書の「課題の文書の名前」）。振る舞いは変えない
- **満たす受け入れ条件:** 無し（Task 2 の照合の土台）
- **進め方:** `design_issues` は失敗するテスト → 最小実装 → 整理。移す 2 つの定数は、既存のテスト（`tests/domains/loop/test_loop_design_tests.py`・`tests/progress/test_invoke_refactor.py`）が変えずに通ることで見る

### Task 2: 差分と課題の番号から、組み直す文書を選ぶ

- **対象ファイル:** `src/ndf/domains/docrebuild/__init__.py`（新設）、`src/ndf/domains/docrebuild/select_decide.py`（新設）、`src/ndf/checks/children.json`（`M1` の `packages` に `ndf.domains.docrebuild`）、`tests/domains/docrebuild/test_docrebuild_select.py`（新設）
- **変更内容:** ファイルも git も読まない選択の処理（`Target`・`Selection`・`Inspection`・`kind_of`・`plan_paths`・`omits_design`・`candidates`・`select`）を作る。工程とモードから文書の種類を決め、課題の番号で絞り、シンボリックリンクと読めない実装計画を理由付きで外す
- **満たす受け入れ条件:** 6・7・8・9（選び方の規則）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。テストの観点は詳細設計書の「対象の選択」の単体テストの表

### Task 3: 差分の起点を決め、差分を読む

- **対象ファイル:** `src/ndf/domains/docrebuild/drive.py`（新設）、`tests/domains/docrebuild/test_docrebuild_run.py`（新設）
- **変更内容:** `RunArgs`・`Precondition`・`start_point`・`changed_paths`・`inspect` を作る。起点はリモート追跡ブランチを先に使い、読めないときの手元への切り替えと、止める場合（両方読めない・別の名前のリモートだけがある・分岐点が無い）を設計書の「処理の流れ」の表どおりにする。git の読み取りは偽物に替えてテストする
- **満たす受け入れ条件:** 3・4・5・11
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: worker を 1 回起動し、結果を確かめて結果 JSON を組む

- **対象ファイル:** `src/ndf/domains/docrebuild/drive.py`、`contracts/worker/document-rebuild.md`（新設。読み手役の記述を除く）、`contracts/schemas/io/document-rebuild.result.schema.json`（新設。`metrics.readers` を除く）、`tests/domains/docrebuild/test_docrebuild_run.py`
- **変更内容:** `BadReport`・`prompt_text`・`launch_worker`・`added_items`・`drive` を作る。対象が 0 件なら worker を起動せず `ok` を返す。worker の `stopped`・`gate` では結果ファイルを読まずに `stopped` を返す（I7）。worker の `items` は `id` を振り直して全件を残す（I6）。起動する子は git の読み取りと `ndf worker run` だけにする
- **満たす受け入れ条件:** 10・12・13
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。worker の起動（`runner`）は偽物に替える

### Task 5: サブコマンド `ndf document-rebuild run` の入口を作る

- **対象ファイル:** `src/ndf/domains/docrebuild/cli.py`（新設）、`src/ndf/cli.py`（`COMMANDS` に `document-rebuild`）、`contracts/schemas/io/document-rebuild.args.schema.json`（生成）、`docs/contracts/commands.md`（生成）、`tests/domains/docrebuild/test_docrebuild_run.py`
- **変更内容:** `_parser`・`adopt`・`main` を作り、`--out` と標準出力へ同じ結果を書く。生成の 2 ファイルは規約の生成（`checks/spec_render.py`）で作り、手で書かない
- **満たす受け入れ条件:** 12（`--out` を `as_handed` が読める）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 6: 読み手役の確かめの記録を結果に入れる

- **対象ファイル:** `src/ndf/domains/docrebuild/drive.py`、`contracts/worker/document-rebuild.md`、`contracts/schemas/io/document-rebuild.result.schema.json`（`metrics.readers`）、`skills/development-workflow/references/agent-layers.md`、`tests/domains/docrebuild/test_docrebuild_run.py`
- **変更内容:** `reader_gaps`・`reader_items` を作り、記録の欠けを `incomplete` にし（I4）、不合格の文書を `items` の `reader-<番号>` と `warnings` に出す（I5）。対象の外の記録は結果に出さない。起動指示に読み手役の立て方と記録の形を足し、`agent-layers.md` に、`ndf worker run` が起動する組み直しの worker に限り読み手役を立ててよい例外を、#4285 の文とは別の行で足す（決定 5）
- **満たす受け入れ条件:** 16（テストで縛るのは記録の確かめまで。実物の worker での確かめは「完了の定義」の手動確認）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 7: 設定が無いリポジトリで、2 つの工程のプランに既定のコマンドを組む

- **対象ファイル:** `tests/progress/test_sprint_templates.py`、`src/ndf/progress/rebuild_values.py`（新設）、`src/ndf/progress/invoke.py`、`contracts/workflow/stages/document-rebuild.json`、`contracts/templates/params.json`、`src/ndf/checks/children.json`（`P1b` の `packages` に `ndf.progress.rebuild_values`）、`tests/progress/test_invoke_rebuild.py`（新設）
- **変更内容:** まず `test_sprint_templates.py` に、`workflow.launch.rebuild` の無い `standard` のスプリントで 2 つの工程のプランが組めるテストを足し、今は落ちることを確かめる（不具合の再現）。次に `rebuild_values`（`Filled`・`kinds_for`・`launch`・`fill`）を作り、`invoke.template_values` が `route.derived_locally` より先に `fill` を呼ぶようにする。プランの差し込み `base`・`remote` と、既定のコマンドの `--base`・`--remote` を同じ値にし、ブランチ戦略の設定の読み込みと `Git.remote()` を 1 回ずつにする。工程「ドキュメント再構成」の入力に `mode` を足し、`params.json` の `rebuild` の説明に既定を書き足す
- **満たす受け入れ条件:** 1・2・15
- **進め方:** 再現のテスト（今は落ちる） → 通す最小実装 → 整理。条件 15 は今のテストを変えずに残して見る

### Task 8: 既定を決められないとき、直す設定が分かる形で止める

- **対象ファイル:** `src/ndf/progress/rebuild_values.py`（`Undecided`）、`src/ndf/progress/invoke.py`（`TemplateValuesMissing.__init__`・`items`、`PROJECT_KEYS`）、`tests/progress/test_invoke_rebuild.py`
- **変更内容:** `branch_strategy` が無いときなどに `Undecided` を返し、`TemplateValuesMissing` の `rebuild` の行に `reason`・`setting`・`shape` を載せる。ほかの行は今のまま 2 つのキーにする。工程の記録（`started`・`failed`）は書かない
- **満たす受け入れ条件:** 14
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

## レビューの単位

見込みの差分は全体で約 2,100 行になり 1,000 行を超えるので、タスクの依存の向きで 4 つに分ける。どの単位も単独でテストが通り、マージした後のスプリントブランチで壊れたコマンドを指さない。行数は生成の 2 ファイルを数えない（`git diff --shortstat <ベース>...HEAD -- . ':(exclude)plugins/ndf/contracts/schemas/io/document-rebuild.args.schema.json' ':(exclude)plugins/ndf/docs/contracts/commands.md' ':(exclude)uv.lock'`）。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 文書の名前と対象の選び方 | Task 1・2 | 4337:6・4337:7・4337:8・4337:9 | 約 450 | （実装の後に書き足す） |
| サブコマンドの起点・起動・結果 | Task 3・4・5 | 4337:3・4337:4・4337:5・4337:10・4337:11・4337:12・4337:13 | 約 850 | （実装の後に書き足す） |
| 読み手役の確かめの記録 | Task 6 | 4337:16 | 約 400 | （実装の後に書き足す） |
| 工程の準備が組む既定のコマンド | Task 7・8 | 4337:1・4337:2・4337:14・4337:15 | 約 450 | （実装の後に書き足す） |

条件 17（`main` に代わりの設定とスクリプトが無い）は、この 4 つの単位に含めない。設計書の決定 8 のとおり、既定を持つ版のリリース後テストが通った後に、`.ndf/project.json` の `workflow.launch.rebuild` と `scripts/launch-document-rebuild.sh` を消すだけの別の Pull Request で満たす。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | 設計書の名前の正規表現と実装計画のパスの書式を `core.issue_docs` へ移す（Task 1） | 新しい選択の処理と既存の 2 か所（`ndf loop design-tests`・リファクタリングの既定）が同じ決まりを読むため、先に 1 か所にしておかないと 3 つ目の写しができる。ほかの構造は整えない。測った値: 一番大きい `progress/invoke.py` は 982 行で、触るのは 8 タスクのうち 2 つ（Task 7・8）、覆うテストは `tests/progress/test_invoke*.py` の 120 件 |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | (1) `rebuild_values.fill` と `refactor_values._default_launch` のマージ先の決め方（どちらも `branch_strategy.of_project` で起点を決める同じ形）。(2) `drive.launch_worker` の結果 JSON の読み取りと `progress.invoke.read_report`（層の向きのため設計で共有しなかった） | 単位 2 と単位 4 が、それぞれ既存の処理と同じ形のコードを足すため |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #4285 が同じ `agent-layers.md` に、conductor が読み手役を立ててよい文を足す | 例外の文を別の行に書き、後にマージする側が両方の文を残す（設計書の「影響と危険」） |
| 単位 2 の見込みが 1,000 行に近い | 実装の途中で 1,000 行を超えそうなら、Task 5 を単位 3 の側へ移して分け直す |

## 切り戻し手順

- 単位ごとの Pull Request を revert すれば戻る。データの移行は無く、スプリントの記録と設定のスキーマも変えない
- 単位 4 だけを戻すと、設定の無いリポジトリは今のとおり工程が始まらない状態に戻り、`ndf document-rebuild run` は手で打てるまま残る

## 完了の定義

- [ ] 受け入れ条件 1〜15 をすべて満たし、条件ごとに検証手段（`tests/progress`・`tests/domains/docrebuild` のテスト）と結果が対応している
- [ ] `bash scripts/check-lint.sh`、`plugins/ndf/bin/ndf check`、`python3 scripts/check-ndf-contracts.py` が通る
- [ ] 条件 16: 設計 PR をマージした後、スプリントの PR をマージする前に、使い捨ての worktree で既定のコマンドを実物の worker で 1 回打ち、`--out` の `metrics.readers` を読む。結果をスプリントの PR の本文に書く
- [ ] 条件 1・2: リリースした NDF を隔離した設定ディレクトリへ導入し、`workflow.launch.rebuild` の無いリポジトリで 2 つの工程を流す（リリース後の確認）
- [ ] 条件 17: 決定 8 の Pull Request のマージの後に確かめ、それまで #4337 を開いたままにする
