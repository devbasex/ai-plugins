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
| `contracts/worker/kinds.json`・`contracts/workflow/context.schema.json` | `product_version` の説明 |
| `src/ndf/contracts/worker_kinds.py` | `write_released`・`released_of`（新設）、`write_values`（変更） |
| `src/ndf/progress/definition.py` | `required_outputs`（新設） |
| `src/ndf/progress/run.py` | `released_answer`（新設）、`_run_step`・`_step_draft`・`_finish_stage`（変更） |
| `src/ndf/progress/invoke.py` | `StageRun.released`・`PreparedPlan.released`・`uses_released`・`answered_gap_detail`（新設）、`prepare_plan`・`_prepared`・`plan_path`・`_no_route`（変更） |
| `src/ndf/core/route/__init__.py` | `rendered`（新設）、`render`（変更） |
| `src/ndf/progress/gate/entry.py` | `released_outcome`・`_strategy_at`・`Released`（新設）、`WorkHandoff`・`_cmd_work`・`_work_handoff`・`work_prompt`・`work_argv`・`_outputs_of`（変更） |
| `src/ndf/domains/release/forms_decide.py` | `reaches`（新設）、`deliveries_of`（変更） |
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

- **対象ファイル:** `result.schema.json`・`release-verification.schema.json`・`kinds.json`・`context.schema.json`・`core/worker.py`・`core/worker_outcome.py`・`progress/gate/entry.py`（`_strategy_at`・`WorkHandoff.version_number`・`_work_handoff`・`work_prompt`・`work_argv`・`_outputs_of`・`_cmd_work`）・`skills/release-verification/SKILL.md`・`tests/domains/release/test_release_record.py`
- **変更内容:** 詳細設計書の「結果 JSON とリリース後テストの記録の定義」と「worker の結果の確かめ」の `ok` の分。`ndf answer work` は、`_work_handoff` が決めた `version_number` を起動指示と `work_argv` の両方へ渡す。記録済みの値で終える `Released` は Task 4 で足す。`gate` の引き継ぎは Task 5 に回す
- **満たす受け入れ条件:** 3・4・5・8
- **進め方:**
  1. Task 1 のテストを通す
  2. `none` 以外と設定を読めないとき（`git` の `GitError` を含む）に `ndf v11.0.0` が欠けになるテスト（条件 8）を足して通す
  3. 条件 4 の経路のテストを `test_release_record.py` に足して通す。配布識別子 `main abc1234` を持つ本番リリース記録（`record_decide.render_record` の本文）を、信頼する書き手のコメントとして偽の GitHub の対象 PR に置く。本番リリース記録の書き込み（`ndf release-steps record`・`record.write`）はリリースブランチの名前からバージョン番号を読むのでこの記録を作れず、この課題では変えない。同じ識別子のリリース後テストの結果を `ndf release-steps verification-record` でその PR に記録する。`close-check` へは投稿前の入力ファイルでなく、投稿されたコメントから取り出した JSON を渡す。手順は設計書のテスト設計の条件 4 の行にある。GitHub は `tests/domains/release/_fakes.py` の偽物で差し替える
  4. 整理

### Task 3: オーガナイザーの承認の後にデプロイの工程が止まることを再現する

- **対象ファイル:** `tests/progress/test_run.py`・`tests/progress/gate/test_gate_work.py`
- **変更内容:** `none` の設定で、オーガナイザーが `main abc1234` を記録して承認した後のデプロイの工程が、判断の求めで止まらず完了し、worker を起動しないことを期待で書く（今は落ちる）
- **テストの組み方:** ケースごとに Git の子プロセスを増やさない。`test_sprint_templates.py` の `sprint` フィクスチャ（ケースごとに `symbolic-ref`・`commit` などを打つ）は使わない
  - `test_gate_work.py`: 保存先に `values.json` を書き、`_strategy_at` を `version_store.kind: none` の設定を返す関数に差し替え、`exec_` を記録するだけの関数にする。worker を起動しないことは `exec_` が呼ばれないことで確かめる
  - `test_run.py`: `repo`・`st`・`env` のフィクスチャは使わない。`repo` は `git init` と、後始末で保存先を探す `git rev-parse` を打ち、`st` も保存先を探すのに `git rev-parse` を打つので、ケースごとに Git の子プロセスが 3 回増えるためである。代わりに、`conftest.py` の `root` フィクスチャ（`tmp_path` の下に保存先のディレクトリを作るだけで、後始末でロックを放す）を使う。保存先を探す `command.store_root_of` を `root` を返す関数に差し替え、`durable.prepare_store(root)` の後に `store.open_store(store.default_url(root), root)` でステートマシンを開く。`run.Env` はケースの中で組む。承認の履歴は、リリース承認ゲートへのオーガナイザーの承認と出力の事象をステートマシンへ追記して作る。プランを流す境界（`invoke` のプランの実行）と `_strategy_at` を差し替え、Git も worker も起動しない
- **満たす受け入れ条件:** 1・2（再現）
- **進め方:** 失敗するテストを先に書くだけ。実装は Task 4

### Task 4: 記録済みの値を `values.json` で渡し、worker を起動せずに終える

- **対象ファイル:** `contracts/worker_kinds.py`（`write_released`・`released_of`・`write_values`）・`progress/run.py`（`released_answer`・`_run_step`）・`progress/invoke.py`（`StageRun.released`・`PreparedPlan.released`・`uses_released`・`prepare_plan`・`_prepared`・`plan_path`）・`core/route/__init__.py`（`rendered`・`render`）・`progress/gate/entry.py`（`released_outcome`・`Released`・`_work_handoff`・`_cmd_work`）・`domains/release/forms_decide.py`・`skills/release/SKILL.md`・`references/release-steps.md`・`skills/development-workflow/references/parallel.md`
- **変更内容:** 詳細設計書の「値のファイル」「工程の進行: 記録済みの値の読み方」「worker の起動」の記録済みの値の分
- **満たす受け入れ条件:** 1・2
- **進め方:**
  1. Task 3 のテストを通す
  2. 設計書のテスト設計の決定 2・3 と条件 1 の行を足して通す。届け先の選び方、利用者の承認の回で worker を起動すること、前の `values.json` が残った retry、ハイフンと波括弧を含む値である
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
  5. テストを足して通す。設計書のテスト設計の決定 6 の行、却下して retry した後の前の回の値の行、先のステップが同じキーを返すプランで値なしの判断の求めに `approved` と回答すると失敗する行（決定 4）である
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
- **現状の手順の問題:** U3 を戻すと、NDF は `outputs.json` を直す前の形（キーと値だけのオブジェクト）で読む。U3 の後に書いた要素の並びの形（`entries`）は、直す前の NDF には `entries` という名前の出力 1 つに見え、出力の形の確かめで落ちてプランの結果に載らない。これまでの案はそのファイルを消して打ち直す手順だったが、それでは進行中のプランがすでに返した出力（`pr`・`product_version` など）を失い、取り戻せない
- **改める手順:** U3 を戻すときは、消さずに直す前の形へ変換する。変換しても結果の `outputs` は変わらない。直す前の形の値は、`entries` の要素ごとに旧い名前を読み替えてから先頭から重ねた値で、U3 の後の NDF がプランの結果に載せる値と同じだからである。ステップごとの区別は直す前の形に置けないので、原本を退避して残す
  1. 変換する保存先のプランだけを止める。変換する保存先とは、`outputs.json` が `entries` の並びを持つ保存先である。止めるのは、その保存先へ書き込むプロセス（そのプランの supervisor と、その下の `ndf progress`・`ndf plan next`・worker）と、そのプランを再開するプロセス（conductor による再開と自動の打ち直し）である。手順 4 で打ち直すまで再開しない。ほかのプランは止めずに走らせ続けてよい。ただし、走らせ続けたプランが手順 4 より前に `entries` の形の `outputs.json` を書いたら、その保存先も変換の対象に加え、手順 1 から行う。止めずに変換すると、プランが読み込みと置き換えの間に出力を書き足し、変換がその出力を古い内容で上書きする。また、U3 を戻す前の NDF が変換の後に動くと、`outputs.json` を `entries` の形へ書き戻してしまう
  2. 手順 1 で止めた、変換する保存先ごとに、下のコマンドで `outputs.json` を変換する。引数の 2 つのパスは例なので、実際の `outputs.json` のパスに置き換え、空白や記号を含んでもよいように引用符で囲む。コマンドは保存先ごとに次の順で処理する
     - 保存先のディレクトリを、根から 1 要素ずつシンボリックリンクをたどらずに開き、以後の読み書きはすべてその記述子を起点にする。パスの途中にリンクがある、または祖先のディレクトリをほかの利用者が書き換えられる（`worker_paths.check_unshared` と同じ規則）と、ほかの利用者がパスを差し替えて別のプランの保存先を開かせられるので、変えずに止める。自分が持ち主でない保存先や、ほかの利用者が書き込める保存先も止める。ファイルを開くときもリンクをたどらない
     - プランの実行が持つロック（保存先の `plan.lock`）を待たずに取る。取れなければ、プランがまだ動いているので変えずに止める。ロックは保存先の処理を終えるまで持ち続ける
     - 形を見る。`entries` の並びを持たないファイルは変換済みなので、触らずに飛ばす
     - 原本を同じディレクトリの `outputs.json.entries` へ退避する。退避は、乱数の名前の一時ファイルを排他的に作って書き、ディスクへ書き出してから、`os.link` で `outputs.json.entries` の名前を付けて公開する。`os.link` は名前が既にあれば失敗するので、途中で止まっても、書きかけのファイルが `outputs.json.entries` になることは無い。退避先が既にあれば、中身が今の `outputs.json` と同じときだけ、そのまま使って先へ進む。違えば変えずに止める
     - `entries` の要素ごとに、旧い名前 `version` を `product_version` へ読み替える（`internal.stage_values` と同じ規則で、1 つの要素に両方あれば `product_version` を残す）。読み替えた `outputs` を先頭から順に重ね、乱数の名前の一時ファイルへ書き、`os.replace` で `outputs.json` と置き換える。途中で止まっても、`outputs.json` は変換前か変換後のどちらかの形で残る
  3. 1 つの保存先で止めても、コマンドは残りの保存先の処理を続け、保存先ごとに結果を 1 行ずつ出す。保存先が見つからない、`outputs.json` を JSON として読めないといった失敗も、その保存先の「止めた」として出す。コマンドが「止めた」と出した保存先は、理由を見て対処してから、同じコマンドを同じ保存先の並びでもう一度打つ。どこまで処理したか分からないときも同じである。処理済みの保存先は飛ばし、退避した原本は変わらないので、結果は 1 回打ったときと同じになる。理由ごとの対処は次のとおりである
     - 「プランが動いている」: その保存先のプランのプロセスのうち、手順 1 で止め損ねたものを止める。ほかのプランは止めない
     - 「退避した原本が今の `outputs.json` と違う」: 前の切り戻しの退避が残っている。2 つのファイルを見比べ、残す方を決めてから、`outputs.json.entries` を別の名前へ移す
     - 「自分だけが書ける保存先でない」・「親をほかの利用者が書き換えられる」・リンクを開けない: 保存先とその祖先の持ち主と権限を確かめる。ほかの利用者が差し替えたファイルは開かない。パスの途中のリンクは、リンク先を確かめてから、リンクを含まないパスで打ち直す
     - 保存先が見つからない・JSON を読めない・`entries` の要素の形が違う: パスと `outputs.json` の中身を確かめる
     - 保存先に残った `.outputs.json.<乱数>.tmp` は、途中で止まった回の一時ファイルなので消してよい
  4. U3 を revert した NDF を入れ、止めたプランを打ち直す。U3 を入れ直すときは、`outputs.json.entries` を `outputs.json` へ戻す

2026-11-26 に手元の一時ディレクトリで、このコマンドを次の場面で流して確かめた。

| 場面 | 結果 |
| --- | --- |
| 2 つの保存先に 2 回打つ。1 つ目は直す前の形の要素の `product_version` の後に、旧い名前 `version` の要素がある | 1 回目で両方を変換し、後の `version` の値が `product_version` に残った。退避は原本と一致した。2 回目は両方を飛ばした |
| 退避先に原本と違う中身がある | 止まり、`outputs.json` も退避先も変わらなかった |
| ほかのプロセスが `plan.lock` を持っている | 止まり、`outputs.json` は変わらなかった |
| `outputs.json` がほかのファイルへのシンボリックリンク | 止まり、リンク先のファイルは変わらなかった |
| 保存先がほかの利用者も書き込める権限（`777`） | 止まり、`outputs.json` は変わらなかった |
| パスの途中のディレクトリがシンボリックリンク | 止まり、リンク先の `outputs.json` は変わらなかった |
| 祖先のディレクトリがほかの利用者も書き込める権限（`777`、sticky bit なし） | 止まり、`outputs.json` は変わらなかった |
| 3 つの保存先を並べ、1 つ目は無く、2 つ目の `outputs.json` は JSON として読めず、3 つ目は直す前の形 | 1 つ目と 2 つ目は止まり、3 つ目は変換した。結果は 3 行出た |
| `entries` の要素に `outputs` が無い | 止まり、退避先も作らなかった |

止めた保存先が 1 つでもあれば、終了コードは 1 になる。

```sh
python3 -I - '/path/to/state-1/outputs.json' '/path/to/state-2/outputs.json' <<'EOF'
import fcntl, grp, json, os, pwd, secrets, stat, sys

NAME, HELD = "outputs.json", "outputs.json.entries"
ME = os.geteuid()

class Stop(Exception):
    """この保存先を変えずに止める理由。"""

def private_group(gid):
    """gid が実行者だけの個人グループ（主グループで、実行者と同じ名前で、ほかの利用者を項目に持たない）なら真。"""
    if gid != os.getegid():
        return False
    names = [u.pw_name for u in pwd.getpwall() if u.pw_uid == ME]
    groups = [g for g in grp.getgrall() if g.gr_gid == gid]
    return bool(names and groups) and groups[0].gr_name == names[0] and set(groups[0].gr_mem) <= {names[0]}

def others_write(st):
    return bool(st.st_mode & 0o002 or (st.st_mode & 0o020 and not private_group(st.st_gid)))

def fixed(parent, child):
    """parent の直下の child を、実行者と root のほかが消す・差し替えることができないなら真（worker_paths の _entry_fixed と同じ規則）。"""
    if parent.st_uid not in (0, ME):
        return False
    if not others_write(parent):
        return True
    return bool(parent.st_mode & stat.S_ISVTX) and child.st_uid in (0, ME)

def open_dir(path):
    """根から 1 要素ずつリンクをたどらずに開き、どの祖先もほかの利用者が次の要素を差し替えられないことを確かめる。"""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in [x for x in path.split("/") if x]:
            if part in (".", ".."):
                raise Stop("パスに . か .. がある")
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                raise Stop(f"{part} をリンクをたどらずに開けない（無いかリンク）: {exc}") from None
            if not fixed(os.fstat(fd), os.fstat(child)):
                os.close(child)
                raise Stop(f"{part} の親をほかの利用者が書き換えられる")
            os.close(fd)
            fd = child
        st = os.fstat(fd)
        if st.st_uid != ME or others_write(st):
            raise Stop("自分だけが書ける保存先でない")
        return fd
    except BaseException:
        os.close(fd)
        raise

def read(d, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=d)
    with os.fdopen(fd, "rb") as f:
        return f.read()

def put(d, data):
    """保存先の中に乱数の名前で一時ファイルを排他的に作って書き、その名前を返す。"""
    tmp = f".{NAME}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=d)
    with os.fdopen(fd, "wb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    return tmp

def renamed(out):
    """直す前の NDF と同じく、旧い名前 version を product_version へ読み替える。両方あれば product_version を残す。"""
    return {("product_version" if k == "version" else k): v for k, v in out.items()
            if not (k == "version" and "product_version" in out)}

def convert(d):
    lock = os.open("plan.lock", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=d)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("プランが動いている") from None
        return locked(d)
    finally:
        os.close(lock)

def locked(d):
    raw = read(d, NAME)
    held = json.loads(raw)
    if not (isinstance(held, dict) and isinstance(held.get("entries"), list)):
        return "飛ばした（変換済み）"
    entries = held["entries"]
    if not all(isinstance(e, dict) and isinstance(e.get("outputs"), dict) for e in entries):
        raise Stop("entries の要素の形が違う")
    try:
        if read(d, HELD) != raw:
            raise Stop("退避した原本が今の outputs.json と違う")
    except FileNotFoundError:
        tmp = put(d, raw)
        os.link(tmp, HELD, src_dir_fd=d, dst_dir_fd=d, follow_symlinks=False)
        os.unlink(tmp, dir_fd=d)
    merged = {}
    for e in entries:
        merged.update(renamed(e["outputs"]))
    tmp = put(d, json.dumps(merged, ensure_ascii=False).encode())
    os.replace(tmp, NAME, src_dir_fd=d, dst_dir_fd=d)
    os.fsync(d)
    return "変換した"

failed = False
for p in sys.argv[1:]:
    try:
        d = open_dir(os.path.dirname(os.path.abspath(p)))
        try:
            msg = convert(d)
        finally:
            os.close(d)
    except Stop as exc:
        msg = f"止めた（{exc}）"
    except (OSError, ValueError) as exc:  # JSON の解析の誤りと文字コードの誤りは ValueError
        msg = f"止めた（{type(exc).__name__}: {exc}）"
    failed |= msg.startswith("止めた")
    print(f"{msg}: {p}")
sys.exit(1 if failed else 0)
EOF
```

## 完了の定義

- [ ] 受け入れ条件 1〜9 をすべて満たし、条件ごとに設計書の「テスト設計」の行のテストと結果が対応している
- [ ] 単位ごとの PR の CI が通る。このリポジトリはテストを CI に寄せる設定（`.ndf/project.json` の `test.ci.only: true`）なので、テストは手元で走らせず、CI の結果で合否を決める
- [ ] レビューの単位の「実際の行数」を書き足した
