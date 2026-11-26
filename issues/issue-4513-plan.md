# #4513: デプロイの工程をバージョン番号を持たないリポジトリでも終える

## 関連リンク

- 要求仕様: [issue-4513-requirements.md](./issue-4513-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4513 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4513-design.md](./issue-4513-design.md)。詳細設計書: [issue-4513-design-detail.md](./issue-4513-design-detail.md)
- モード: `standard`（公開インタフェースの結果 JSON の形と工程の振る舞いを変える不具合の修正で、触るファイルが 4 を超える）
- 外した範囲: 配布物に含まれることの確かめ・手動の配布の記録・その復旧は #4586 で扱う

## 修正対象

詳細設計書の「地図」の表から引き写す。関数ごとの変える前と後は、詳細設計書の構成要素ごとの「関数の仕様」にある。

| ファイル | 変える中身 |
| --- | --- |
| `contracts/schemas/result.schema.json` | `product_version` を空白だけでない文字列にし、バージョン番号の形を `$defs/version_number` へ移す |
| `contracts/schemas/release-verification.schema.json` | `version` を空白だけでない文字列にする |
| `contracts/worker/kinds.json`・`contracts/workflow/context.schema.json` | `product_version` の説明。文脈の `product_version` を改行を含まない 1 行に限る |
| `src/ndf/contracts/worker_kinds.py` | `Recorded`・`write_released`・`released_of`（新設）、`write_values`（変更） |
| `src/ndf/progress/definition.py` | `required_outputs`（新設） |
| `src/ndf/progress/plan/__init__.py` | `record_release`（`--version` を文脈の `product_version` の定義で確かめる） |
| `src/ndf/progress/run.py` | `released_answer`（新設）、`_prepare`・`_run_step`・`_step_draft`・`_finish_stage`（変更） |
| `src/ndf/progress/invoke.py` | `StageRun.released`・`PreparedPlan.released`・`uses_released`・`answered_gap_detail`（新設）、`prepare_plan`・`_prepared`・`plan_path`・`_no_route`（変更） |
| `src/ndf/core/route/__init__.py` | `_built`・`rendered`（新設）、`render`（変更） |
| `src/ndf/progress/gate/entry.py` | `released_outcome`・`_strategy_at`・`Released`（新設）、`WorkHandoff`・`_cmd_work`・`_work_handoff`・`work_prompt`・`work_argv`・`_outputs_of`（変更） |
| `src/ndf/progress/gate/release.py` | `_released`・`take_reply`（変更。オーガナイザーの承認と一緒に届け先を記録する） |
| `src/ndf/domains/release/forms_decide.py` | `reaches`・`production_delivery`（新設）、`deliveries_of`（変更） |
| `src/ndf/core/worker.py`・`core/worker_outcome.py` | 引数 `--require-version-number`、`Checked`（新設）、`require_outputs`・`_output_gap`・`outcome_of`（変更） |
| `src/ndf/core/plan/steps.py`・`transition_decide.py`・`runner.py`・`records.py` | `missing_outputs` と要約の引き継ぎ、`outputs.json` の要素の並び |
| `skills/` の 5 ファイル | `product_version` の意味と、出力の足りない承認の読み方 |

テストは詳細設計書の地図の `tests/` のファイルに足す。数を書かないのは、地図とこの文の 2 か所で数を持つと、地図を直したときに食い違うためである。

## タスク分解

変更の種類は不具合なので、各単位の最初のタスクは、今は落ちる再現のテストにする。

### Task 1: 短いハッシュの値が結果 JSON の形の誤りになることを再現する

- **対象ファイル:** `tests/contracts/test_result.py`・`tests/core/test_worker_outcome.py`
- **変更内容:** `product_version: "main abc1234"` の結果 JSON が今は形の誤りになることを、受け入れ条件 3 の期待（通る）で書く。空文字・空白だけが誤りになる期待（条件 5）も並べる
- **満たす受け入れ条件:** 3・5（再現）
- **進め方:** 失敗するテストを先に書くだけ。実装は Task 2

### Task 2: 形の確かめを結果の定義から `ndf worker run` へ移す

- **対象ファイル:** `result.schema.json`・`release-verification.schema.json`・`kinds.json`・`context.schema.json`・`core/worker.py`・`core/worker_outcome.py`・`progress/gate/entry.py`（`_strategy_at`・`WorkHandoff.version_number`・`_work_handoff`・`work_prompt`・`work_argv`・`_outputs_of`・`_cmd_work`）・`progress/plan/__init__.py`（`record_release`）・`skills/release-verification/SKILL.md`・`tests/domains/release/test_release_record.py`・`tests/progress/test_plan.py`
- **変更内容:** 詳細設計書の「結果 JSON とリリース後テストの記録の定義」と「worker の結果の確かめ」の `ok` の分。`ndf answer work` は、`_work_handoff` が決めた `version_number` を起動指示と `work_argv` の両方へ渡す。記録済みの値で終える `Released` は Task 4 で足す。`gate` の引き継ぎは Task 5 に回す
- **満たす受け入れ条件:** 3・4・5・8
- **進め方:**
  1. Task 1 のテストを通す
  2. `none` 以外と設定を読めないとき（`git` の `GitError` を含む）に `ndf v11.0.0` が欠けになるテスト（条件 8）を足して通す
  3. リリース後テストの記録の `version` が改行を含むと形の誤りになり、1 行の記録は描いて読み直しても `version` と `issues` が変わらないテスト（設計書のテスト設計の決定 5 の行）を足して通す
  4. 改行を含む配布識別子が、`ndf lanes release --version` では記録されずに断られ、`ndf worker run` の `ok` の結果では欠けになるテスト（設計書のテスト設計の決定 5 の行）を足して通す
  5. 条件 4 の経路のテストを `test_release_record.py` に足して通す。条件 4 は本番リリース記録を読める場合に限った条件なので、テストは記録が置かれた状態から始める。配布識別子 `main abc1234` を持つ本番リリース記録（`record_decide.render_record` の本文）を、信頼する書き手のコメントとして偽の GitHub の対象 PR に置く。本番リリース記録の書き込み（`ndf release-steps record`・`record.write`）はリリースブランチの名前からバージョン番号を読むのでこの記録を作れず、この課題では変えない。同じ識別子のリリース後テストの結果を `ndf release-steps verification-record` でその PR に記録する。`close-check` へは投稿前の入力ファイルでなく、投稿されたコメントから取り出した JSON を渡す。手順は設計書のテスト設計の条件 4 の行にある。GitHub は `tests/domains/release/_fakes.py` の偽物で差し替える
  6. 整理

### Task 3: オーガナイザーの承認の後にデプロイの工程が止まることを再現する

- **対象ファイル:** `tests/progress/test_run.py`・`tests/progress/gate/test_gate_work.py`
- **変更内容:** `none` の設定で、オーガナイザーが `main abc1234` を記録して承認した後のデプロイの工程が、判断の求めで止まらず完了し、worker を起動しないことを期待で書く（今は落ちる）
- **テストの組み方:** ケースごとに Git の子プロセスを増やさない。`test_sprint_templates.py` の `sprint` フィクスチャ（ケースごとに `symbolic-ref`・`commit` などを打つ）は使わない
  - `test_gate_work.py`: 保存先に `values.json` を書き、`_strategy_at` を `version_store.kind: none` の設定を返す関数に差し替え、`exec_` を記録するだけの関数にする。worker を起動しないことは `exec_` が呼ばれないことで確かめる
  - `test_run.py`: `repo`・`st`・`env` のフィクスチャは使わない。`repo` は `git init` と、後始末で保存先を探す `git rev-parse` を打ち、`st` も保存先を探すのに `git rev-parse` を打つので、ケースごとに Git の子プロセスが 3 回増えるためである。代わりに、`conftest.py` の `root` フィクスチャ（`tmp_path` の下に保存先のディレクトリを作るだけで、後始末でロックを放す）を使う。保存先を探す `command.store_root_of` を `root` を返す関数に差し替え、`durable.prepare_store(root)` の後に `store.open_store(store.default_url(root), root)` でステートマシンを開く。`run.Env` はケースの中で組む。承認の履歴は、リリース承認ゲートへのオーガナイザーの承認と出力の事象をステートマシンへ追記して作る。プランを流す境界（`invoke` のプランの実行）と `_strategy_at` を差し替え、Git も worker も起動しない
- **満たす受け入れ条件:** 1・2（再現）
- **進め方:** 失敗するテストを先に書くだけ。実装は Task 4

### Task 4: 記録済みの値を `values.json` で渡し、worker を起動せずに終える

- **対象ファイル:** `contracts/worker_kinds.py`（`Recorded`・`write_released`・`released_of`・`write_values`）・`progress/run.py`（`released_answer`・`_prepare`・`_run_step`）・`progress/invoke.py`（`StageRun.released`・`PreparedPlan.released`・`uses_released`・`prepare_plan`・`_prepared`・`plan_path`）・`core/route/__init__.py`（`_built`・`rendered`・`render`）・`progress/gate/entry.py`（`released_outcome`・`Released`・`_work_handoff`・`_cmd_work`）・`progress/gate/release.py`（`_released`・`take_reply`）・`domains/release/forms_decide.py`（`reaches`・`production_delivery`）・`tests/progress/gate/test_gate_release.py`・`skills/release/SKILL.md`・`references/release-steps.md`・`skills/development-workflow/references/parallel.md`
- **変更内容:** 詳細設計書の「値のファイル」「工程の進行: 記録済みの値の読み方」「worker の起動」の記録済みの値の分と、「オーガナイザーの承認と一緒に届け先を記録する」
- **満たす受け入れ条件:** 1・2
- **進め方:**
  1. Task 3 のテストを通す
  2. 設計書のテスト設計の決定 2・3 と条件 1 の行を足して通す。届け先の記録（承認を記録した時点で 1 行に決まって文脈の形に合うときだけ記録し、記録が無ければ worker を起動せずに `stopped`。止まった後に設定を直して retry しても `stopped` のまま）、利用者の承認の回で worker を起動すること、前の `values.json` が残った retry、ハイフンと波括弧を含む値である
  3. 整理

### Task 5: 値をそろえた判断の求めへの承認で終わることを再現し、`gate` の出力を引き継ぐ

- **対象ファイル:** `core/worker_outcome.py`・`core/plan/records.py`（`_add_outputs`・`_stored_entries`・`recorded_outputs`・`_keep_step_missing`・`recorded_step_missing`）・`core/plan/steps.py`（`outcome_of`）・`core/plan/runner.py`（`run_step`・`final_result`）・`progress/invoke.py`（`_no_route` の欠けの判定）・`tests/core/test_worker_outcome.py`・`tests/core/plan/test_runner.py`・`tests/progress/test_invoke.py`
- **変更内容:** 再現のテスト（`gate` で `delivery`・`product_version` を持つ結果ファイルの後の `approved` で工程が完了する。今は落ちる）を先に書く。続けて、詳細設計書の「worker の結果の確かめ」の `gate` の分と「プランの出力の記録」を実装する。部分の出力を引き継ぐのと同じ単位で、判断を求めたステップ自身の `metrics.missing_outputs` が空でなければ承認の後に `bad_report` で止める判定も入れる。この単位だけを取り込んだときに、先のステップの同じキーで欠けが埋まって完了するのを防ぐためである。この単位の失敗の文は今の文のままでよく、足りない名前と worker の理由を入れた文は Task 6 で足す
- **満たす受け入れ条件:** 9、8（`gate` の値の形）
- **進め方:**
  1. 失敗するテスト
  2. `require_outputs` の `gate`
  3. `outputs.json` の要素の並び
  4. `metrics.missing_outputs` の引き継ぎ（`steps.outcome_of`・`_keep_step_missing`・`final_result`）と、`_no_route` の欠けの判定
  5. テストを足して通す。設計書のテスト設計の決定 6 の行、却下して retry した後の前の回の値の行、先のステップが同じキーを返すプランで値なしの判断の求めに `approved` と回答すると失敗する行、承認の後に続くステップがあるプランでも続けずに失敗する行、文脈の必須キーを欠く `delivery` の判断の求めが `gate` のまま届く行（どれも決定 4）である
  6. 整理

### Task 6: 出力の足りない承認を、名前と理由を示して失敗にする

- **対象ファイル:** `progress/definition.py`・`progress/invoke.py`（`answered_gap_detail`・`_no_route` の失敗の文と `metrics.waited`）・`progress/run.py`（`_step_draft`・`_finish_stage`）・`core/plan/transition_decide.py`・`runner.py`（`_perform`）・`skills/progress-tracking/SKILL.md`
- **変更内容:** 詳細設計書の「工程の定義の読み手」「プランの遷移と結果の要約」「失敗の理由」のうち、Task 5 に入れた欠けの判定を除く分。失敗の理由に、足りない名前・worker の理由・次の操作を入れる
- **満たす受け入れ条件:** 6・7
- **進め方:**
  1. 失敗するテスト: 出力の無い判断の求めに `approved` と回答すると、理由に `product_version` と worker の要約が入る（今は落ちる）
  2. 実装して通す
  3. テストを足して通す: 失敗の後に `ndf progress next` を打っても worker が起動されない（条件 7）。先のステップが同じキーを返すプランでも、失敗の文に足りない名前が入る
  4. 整理

## レビューの単位

1 つの課題の見込みが 1,000 行を超えるので、タスクの依存の向きで 4 つに分ける。どの単位も単独でテストが通る。行数は `git diff --shortstat <ベース>...HEAD -- plugins/ndf` で数える。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| U1 形の確かめの移し替え | Task 1・2 | 4513:3・4513:4・4513:5・4513:8 | 約 300 | （実装の後に書き足す） |
| U2 記録済みの値で終える | Task 3・4 | 4513:1・4513:2 | 約 400 | （実装の後に書き足す） |
| U3 判断の求めの出力の引き継ぎと欠けの判定 | Task 5 | 4513:9 | 約 450 | （実装の後に書き足す） |
| U4 出力の足りない承認の理由 | Task 6 | 4513:6・4513:7 | 約 300 | （実装の後に書き足す） |

- 順序は U1 → U2 → U3 → U4 にする。U2 は U1 の `_strategy_at` と緩めた結果の定義を使う。U4 は U3 の `metrics.missing_outputs` を使う
- U2 を 2 番目に置くのは、他のプロジェクトの進行を止めている事象（オーガナイザーの承認の後に工程が終わらない）を直す単位だからである
- U3 と U4 を分けるのは、合わせると約 750 行で 400 行の目安を超え、U3 だけで受け入れ条件 9 が単独で通るためである
- 判断を求めたステップ自身の欠けで止める判定は、U4 でなく U3 に入れる。U3 は `gate` の部分の出力を引き継ぐので、判定が無いと、U3 だけを取り込んだ時点で、値を返さなかった判断の求めが先のステップの値で補われて完了する。そのため U3 は目安を約 50 行超える。U4 には失敗の文を詳しくする分だけを残す

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | しない | 測った値: 触るファイルのうち 700 行を超えるのは `invoke.py` 930・`run.py` 965・`core/worker.py` 998・`runner.py` 731 の 4 つ。同じファイルを触るタスクの割合は、最も多い `invoke.py`・`run.py`・`entry.py` で 6 タスク中 2〜3。どのファイルも既存のテストが厚い（`test_invoke.py` 68・`test_run.py` 62・`test_gate_work.py` 37・`test_runner.py` 29）。足すのは関数の単位で、既存の関数の分け方を変えない。`core/worker.py` は引数を 1 つ足すだけである |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `records.py` の `_keep_step_gaps` と `_keep_step_missing`（同じ形のステップごとの記録が 2 つ並ぶ）、`run.py` の `_step_draft` と `_finish_stage` の失敗の理由の組み立て | U3 と U4 が同じファイルに同じ形の処理を足す。1 つにまとめるかは、2 つが並んだ後に見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 結果 JSON の定義を緩め、`product_version` を書く経路が worker の確かめを通らないと、バージョン番号の形が守られない | U1 で `none` 以外と設定を読めないときの `ndf v11.0.0` のテスト（条件 8）を置く。NDF の中のほかの書き手（バージョンアップの組み立て・`release-steps`）は保存先から値を作るので変えない |
| `outputs.json` の形を変え、直す前の形を読む実行がある | 直す前の形をステップの分からない要素 1 つとして読むテストを U3 に置く |
| 進行中のスプリントで、直す前の NDF が書いた `values.json` が残る | 記録済みの値を使うプランでは、`plan_path` が `values.json` の `released_version` と今の記録済みの値を比べ、違えば書き直す（直す前の NDF が書いた `values.json` には `released_version` が無いので、値があれば書かれる）。U2 の retry のテストで確かめる |

## 切り戻し手順

- 単位ごとの PR を revert すれば戻る。前へ進めるときのデータの移行は無い
- **U3 だけは戻す時機を選ぶ。** U3 は実行の保存先の `outputs.json` を、キーと値だけのオブジェクトから、ステップごとの要素の並び（`entries`）へ変える。直す前の NDF は `entries` を読めない。そのため、`entries` の形の `outputs.json` を読むプランが残っている間に U3 を戻すと、そのプランの出力が結果に載らなくなる
- **そこで、U3 は、`entries` の形の `outputs.json` を再開の後に読むプランが 1 つも無いときにだけ戻す。** プランのプロセスが動いていないだけでは足りない。承認ゲートで回答を待つプランと、失敗して retry を待つ工程のプランも、再開すると同じ保存先の `outputs.json` を読む（工程の retry が試行の始めに消すのは `values.json` と課題のコピーだけで、`outputs.json` は残る。`run._prepare`）
- **確かめてから戻し終えるまで、プランを起動も再開もさせない。** 確かめた後に新しいプランが起動するか、止まっていたプランが再開すると、そのプランが `entries` の形の `outputs.json` を書き、承認待ちへ進めるためである。調べた時点で `outputs.json` が無かったプランも同じである。その後に戻すと、利用者が旧版でそのプランを再開したとき、保存済みの出力が工程の結果から落ちる
- **確かめる対象は、スプリントのプランと単発のプランの両方である。** U3 が変える `core/plan/records.py` は、スプリントの工程が流すプランだけでなく、利用者が `ndf plan next <プラン定義ファイル>` で単発で流すプランも使う。単発のプランの保存先は、`--state-dir` を渡したならその場所、渡さなければプラン定義ファイルの隣の `<プラン名>.state`（`assemble_decide.state_dir_for`）で、`.git/ndf/state/` の下とは限らない
- 戻す手順は次のとおりである
  1. 切り替える範囲を決める。U3 を戻した NDF を入れる環境（同じプラグインのキャッシュ）で、次の 2 つを一覧にする
     - スプリント: 状態の保存先（`.git/ndf/state/`）のステートマシンの ID
     - 単発のプラン: 切り替えた NDF で再開するプランの定義ファイルのパスと、その保存先
  2. その範囲で、プランの起動と再開を止める
     - 並列の計画ごとに、計画の定義ファイルのパスを控えてから `ndf lanes stop <計画> --reason "U3 の切り戻し"` を打ち、未着手の行を始めさせない。再開するときに、控えたパスを渡す（手順 8）
     - 計画の外のスプリントを進める会話（conductor）も止め、`ndf progress next`・`ndf progress retry`・`ndf answer` を打たせない
     - 1 の単発のプランに `ndf plan next` を打たせない
  3. 1 の範囲で実行中の処理が終わるのを待つ。区切りで止めた計画でも、実行中の行は終わりまで流れる。プロセスの名前では探さない。`ndf progress next` が背景で起動する `progress work` は、`python -I -c … progress work` のコマンド行で動き（`cli.ndf_command`）、`bin/ndf` の名前では見つからないためである。また、範囲の外の処理（別のリポジトリや別の導入先の `progress wait`・`relay run`）まで待つことになるためでもある。代わりに、状態に書き込める処理が持つ排他を 1 つずつ確かめる
     - スプリントは、ID ごとに `ndf progress show <ID>` を打ち、結果の `next` が `ndf progress wait` を指す間は待つ。`next` が `wait` になるのは、ほかの処理がそのステートマシンの実行の排他（`store.run_lock`）を持つときと、`next` が起動した作業が排他を取る前に置く起動のマーカー（作業の保存先の `launched.json`）の組が生きているときである（`run.is_live`）
     - 単発のプランは、保存先の `plan.lock`（`runner.plan_lock` が流す間だけ持つ排他）を `flock -n <保存先>/plan.lock true` で取れるようになるまで待つ
  4. 止まった状態のまま、1 の保存先（スプリントは `.git/ndf/state/progress-work/` の下、単発のプランはそのプランの保存先）で、キー `entries` を持つ `outputs.json` を探す
  5. 見つかった保存先ごとに、完了しているかを見る
     - スプリント: 持ち主のステートマシンで `ndf progress show <ID>` を打ち、その保存先の工程が完了しているかを見る。回答を待っている・失敗して retry を待っている・まだ流れていない工程は、完了していない。`progress-work/` の直下のディレクトリの名前はステートマシンの ID の SHA-256 の先頭なので（`run.work_root`）、各スプリントの ID から同じ求め方で名前を出して照らす
     - 単発のプラン: `ndf plan wait <プラン定義ファイル> --state-dir <保存先> --timeout 0` を打ち、回の結果が `ok` なら完了である。承認を待っている（`gate`）・止まった（`stopped`）・まだ流れていない回は、再開されうるので完了していない
  6. 完了していないものがあれば、それだけを再開して完了まで進める（回答を待つなら回答し、retry を待つなら retry して終える）。進め終えたら 3 から確かめ直す。進められなければ U3 を戻さず、2 で止めた計画・会話・単発のプランを再開する
  7. 完了したものだけになったら、2 で止めた状態のまま NDF を切り替える。完了した工程の出力はステートマシンへ写されているので、直す前の NDF がその `outputs.json` を読まなくても困らない。完了した単発のプランの結果は、回ごとの結果のファイル（`runner.stored_result` が読む）に出力ごと残るので、`ndf plan wait` は `outputs.json` を読まずに結果を返す
  8. 切り替えた後に、2 で控えた定義ファイルのパスごとに `ndf lanes start <計画の定義ファイル>` を打って計画を再開し、止めた会話と単発のプランを再開する
- **ファイルを変換・退避・消す手順は置かない。** これまでの案は、走っているプランの `outputs.json` を直す前の形へ変換する手順だった。しかし、変換の途中で書き足された出力の扱いや、入れ直したときの上書きで出力を失う経路が次々に見つかった（設計 PR の cross-review の C4 の指摘 4 件）。戻す時機を選べば、出力を失う操作そのものが要らない
- U3 を入れ直すときも、ファイルに触らない。U3 は直す前の形の `outputs.json` を、ステップの分からない要素 1 つとして読む（リスクと対処の 2 行目のテスト）

## 完了の定義

- [ ] 受け入れ条件 1〜9 をすべて満たし、条件ごとに設計書の「テスト設計」の行のテストと結果が対応している
- [ ] 単位ごとの PR の CI が通る。このリポジトリはテストを CI に寄せる設定（`.ndf/project.json` の `test.ci.only: true`）なので、テストは手元で走らせず、CI の結果で合否を決める
- [ ] レビューの単位の「実際の行数」を書き足した
