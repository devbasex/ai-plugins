# サブコマンドの引数と結果

<!-- ndf check --write-spec が生成する。手で直さない（SSOT は各節の出典） -->

`ndf <サブコマンド>` の引数と結果である。どのサブコマンドも結果 JSON（`contracts/schemas/result.schema.json`）を標準出力の最後の 1 行に出す。SSOT はサブコマンドの表（`src/ndf/cli.py` の `COMMANDS`）・各サブコマンドの引数の定義・手書きの引数の表（`contracts/domains/manual-args.json`）・結果のスキーマ。終了コードと停止理由は [終了コードと停止理由](exit-codes.md) にある

## `ndf answer`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/answer.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/answer.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf answer` | `machine_id`（string・必須・位置）: 回答するステートマシンの ID<br>`--gate`（string・必須）: 質問の id（承認ゲートの結果の metrics.gate.id）<br>`--result`（string・必須）: 回答の語（承認ゲートの結果の routes のキー）<br>`--reason`（string・必須）<br>`--by`（string・必須）: 回答した者（human・conductor・organizer か service:<主体>）<br>`--key`（string・必須）: 冪等のキー（同じキーの回答し直しは書かない）<br>`--notes`（string・任意）: 変更要求（request_changes）の指摘ファイル<br>`--head`（string・任意）: 見た PR の head の SHA<br>`--material-sha256`（string・任意）: 見た承認資料の sha256<br>`--body-sha256`（string・任意）: 見た PR の本文の sha256（#4446）<br>`--title-sha256`（string・任意）: 見た PR の題の sha256（承認の質問の文は題から作る。#4446）<br>`--presentation`（string・任意）: 利用者に示した承認資料を識別する値。提示時に承認対象の PR が特定され、routes にこの値が入っている場合は必須。提示時のコマンドから値をそのまま渡す（資料を出し直すと変わる）<br>`--drop`（array of integer・任意）: 設計承認ゲートで外す課題の番号 |

結果の `metrics`: `carried_row`（string か null）・`gate`（object か string）・`gate_id`（string）・`head`（string）・`machine_id`（string）・`move`（string）・`release_request`（object）・`replayed`（boolean）・`rev`（integer）・`seqs`（array of integer）・`stage`（string）・`state`（string か null）・`step`（string）。`items` の要素: `answer`（string）・`at`（string）・`by`（string）・`gate`（string か null）・`gate_id`（string か null）・`head`（string か null）・`key`（string）・`notes`（string か null）・`reason`（string か null）・`seq`（integer）・`source`（string）・`stage`（string か null）・`step`（string か null）

## `ndf check`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/check.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/check.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf check` | `--only`（array of string・任意）: この規則の指摘だけを違反にする（繰り返せる）<br>`--write-spec`（boolean・任意）: 規約の生成物（コントラクトのページと引数のスキーマ）を書き、減った件数へラチェットの基準を下げてから検査する<br>`--ratchet-base`（string・任意）: ラチェットの基準が、この commit の基準より上がっていないかも見る |

結果の `metrics`: `budget`（object）・`files`（integer）・`findings`（object）・`written`（integer）。`items` の要素: `detail`（string）・`line`（integer）・`path`（string）・`rule`（string）

## `ndf ci`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/ci.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/ci.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf ci answer` | `--branch`（string・必須）: PR の head のブランチ<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば作業場所の origin<br>`--answer-record`（string・必須）: ndf plan next --answer が書いた回答の記録<br>`--presented`（string・必須）: 承認待ちのステップが書いた承認の対象のファイル |
| `ndf ci ask` | `--branch`（string・必須）: PR の head のブランチ<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば作業場所の origin<br>`--presented-out`（string・必須）: 承認の対象を書くファイル（ndf ci answer が読む） |
| `ndf ci merge` | `--branch`（string・任意）: PR の head のブランチ（作業場所の HEAD を添える）<br>`--pr`（integer・任意）: PR の番号（作業場所が無くても打てる。マージ済みなら何もしない）<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば作業場所の origin<br>`--method`（string・任意）: マージの方式（squash・merge・rebase）。省けば基底のブランチで許された方式の先頭<br>`--reviewed`（string・任意）: レビューを経た HEAD。作業場所の HEAD と違えばマージしない<br>`--production-gate`（string・任意・既定 `"stop"`・値 `stop`・`defer`・`stale`）: 自動反映の本番チャネルへ承認の証跡が無いとき: stop は承認待ち、defer はマージせずに ok、stale は approval_stale<br>`--approved-head`（string・任意）: 工程の承認ゲートで承認した HEAD（--approved-base と組で渡す）<br>`--approved-base`（string・任意）: 工程の承認ゲートで承認したマージ先<br>`--presented-out`（string・任意）: stop で止まるときに承認の対象を書くファイル（ndf ci answer が読む）<br>`--required`（array of string・任意）: マージの前に、マージする HEAD の結論を待つ必須のチェック。渡さなければ待たない<br>`--test`（array of string・任意）: --required で待つときのテストのチェックの名前<br>`--budget`（number・任意）: --required で待つ合計の秒 |
| `ndf ci watch` | `--pr`（integer・必須）<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば作業場所の origin<br>`--required`（array of string・必須）: 待つ必須のチェック（PR の HEAD の設定から渡す）<br>`--test`（array of string・任意）: テストのチェックの名前（分割ジョブも数える）<br>`--budget`（number・必須）: 待ちの合計の秒（ランナーの割り当ての待ちは数えない）<br>`--after-merge`（boolean・任意）: マージの段階が落ちた後の判定<br>`--merge-failed`（array of string・任意）: マージの段階が落ちたと読んだチェックの名前 |

結果の `metrics`: `answer`（string）・`approved_by`（string）・`elapsed`（number）・`failed_test_reads`（array of object）・`failed_tests`（array of string）・`gate`（const "production-merge"）・`junit_artifacts`（string）・`log_error`（string）・`merge`（string）・`presented_id`（string）・`production`（boolean）・`queued_for`（number）・`rounds`（integer）・`test_files`（array of string）・`verdict`（string）・`written`（boolean）。`items` の要素: `job`（integer）・`kind`（string）・`name`（string）・`number`（integer）・`result`（string）・`test`（boolean）

## `ndf contracts`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/contracts.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/contracts.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf contracts check` | 引数を受けない |
| `ndf contracts emit` | `--tool`（string・必須）<br>`--status`（string・必須・値 `ok`・`gate`・`stopped`）<br>`--summary`（string・必須）<br>`--cause`（array of string・任意）<br>`--cause-instance`（string・任意）<br>`--retry-after`（number・任意）<br>`--route`（array of array of string・任意）: CMD は {"argv": ["ndf", ...]} の JSON。- なら何も打たない<br>`--gap`（array of array of string・任意）<br>`--item`（array of string・任意）: items の 1 要素（JSON のオブジェクト）<br>`--metrics`（string・任意）: metrics（JSON のオブジェクト）<br>`--next`（string・任意）: 次に打つコマンド（{"argv": ["ndf", ...], "ask": [...]} の JSON）<br>`--outputs`（string・任意）: 工程どうしで渡す値（JSON のオブジェクト）<br>`--run-id`（string・任意）<br>`--presentation-path`（string・任意）<br>`--code`（integer・任意）: 終了コード（省けば status と cause.kind から決める）<br>`--out`（string・任意）: 結果を 1 行で書くファイル（形が合うときだけ書く） |
| `ndf contracts fact-ids` | `table`（string・必須・位置）<br>`where`（array of string・任意・位置） |
| `ndf contracts fact-value` | `table`（string・必須・位置）<br>`id`（string・必須・位置）<br>`column`（string・必須・位置） |

結果の `metrics`: `fact_tables`（integer）。`items` の要素: `kind`（const "violation"）・`reason`（string）

## `ndf cross-review`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/cross-review.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/cross-review.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf cross-review check-worktree` | `worktree`（string・必須・位置） |
| `ndf cross-review finish` | `--summary`（string・必須）<br>`--findings`（string・任意） |
| `ndf cross-review fix-gate` | `--state-dir`（string・必須）<br>`--pr`（integer・必須）<br>`--findings`（string・任意） |
| `ndf cross-review head` | `--pr`（integer・必須）<br>`--repo`（string・任意） |
| `ndf cross-review lock` | `worktree`（string・必須・位置）<br>`--pr`（integer・必須）<br>`--tool`（string・任意・既定 `"cross-review"`） |
| `ndf cross-review merge-fix` | `--state-dir`（string・必須）<br>`--pr`（integer・必須）<br>`--file`（string・必須）<br>`--findings`（string・任意） |
| `ndf cross-review push-fix` | `worktree`（string・必須・位置）<br>`--branch`（string・必須）<br>`--commit`（string・任意） |
| `ndf cross-review release` | `worktree`（string・必須・位置）<br>`--repo-dir`（string・必須） |
| `ndf cross-review report` | `--state-dir`（string・必須）<br>`--pr`（integer・必須）<br>`--repo`（string・任意） |
| `ndf cross-review review-scope` | `worktree`（string・必須・位置）<br>`--purpose`（string・必須）<br>`--base`（string・必須）<br>`--reviewer`（array of string・任意）<br>`--state-dir`（string・必須）<br>`--pr`（integer・必須） |
| `ndf cross-review run` | `--pr`（integer・必須）<br>`--state-dir`（string・必須）<br>`--base`（string・必須）<br>`--worktree`（string・任意）<br>`--branch`（string・任意）<br>`--purpose`（string・任意・既定 `"cross-review"`）<br>`--reviewer`（array of string・任意）<br>`--max-rounds`（integer・任意・既定 `3`）<br>`--findings`（string・任意）<br>`--reviewed-out`（string・任意） |
| `ndf cross-review state-file` | `--state-dir`（string・必須）<br>`--pr`（integer・必須）<br>`--current`（integer・任意） |
| `ndf cross-review sync` | `worktree`（string・必須・位置）<br>`--branch`（string・必須） |
| `ndf cross-review target-repo` | `--repo`（string・任意）<br>`--state-dir`（string・任意）<br>`--pr`（integer・任意） |

結果の `metrics`: `findings`（integer）・`rounds`（integer）。`items` の要素: `after`（string）・`base`（string か null）・`before`（string）・`branch`（string）・`conflicts`（array of string）・`current_pr`（integer）・`head`（string か null）・`id`（string）・`kind`（string）・`locked`（string）・`number`（integer）・`ok`（boolean）・`origin_head`（string か null）・`origin_pr`（integer）・`outcome`（string）・`path`（string）・`points_to`（string）・`reason`（string か null）・`rebased`（boolean）・`remote`（string）・`replayed`（integer）・`scope`（string）・`slug`（string）・`source`（string）・`unreviewed`（array of string）・`url`（string）

## `ndf document-rebuild`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/document-rebuild.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/document-rebuild.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf document-rebuild gate` | `--gate`（string・必須・値 `design`・`release`）: 承認ゲートの種類<br>`--pr`（integer・任意）: 承認する PR の番号（省けば、設計は作業場所のブランチの PR、リリースは工程の入力の pr）<br>`--material`（string・任意）: リリースの承認資料のファイル（ndf release-steps materials の出力。--gate release だけ）<br>`--reviewed`（string・任意）: 設計のレビューを済ませた再レビュー待ちのコミット<br>`--no-doc-fix`（boolean・任意）: 文書を直させず、残った文として承認資料に書かせる<br>`--state-dir`（string・必須）: 起動指示・承認資料の作業用のコピー・worker の結果ファイルの保存先<br>`--out`（string・必須）: 結果 JSON を書くファイル |
| `ndf document-rebuild run` | `--base`（string・必須）: ベースブランチの名前（スプリントの PR のマージ先）<br>`--remote`（string・必須）: 起点の参照を探すリモートの名前<br>`--issue`（array of integer・必須）: 工程が扱う課題の番号（1 回以上）<br>`--kind`（array of string・必須・値 `design`・`plan`・`manuscript`）: 選ぶ文書の種類（1 回以上）<br>`--state-dir`（string・必須）: 起動指示と worker の結果ファイルの保存先<br>`--out`（string・必須）: 結果 JSON を書くファイル |

結果の `metrics`: `readers`（array of reader）・`reads`（array of object）・`targets`（array of string）。`items` の要素: `doc`（string）・`id`（string）・`text`（string）

## `ndf gws`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/gws.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/gws.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf gws check` | `--install`（boolean・任意）: 導入の同意のフラグ。gws が無ければ npm で入れる |
| `ndf gws thumbnails` | `presentation_id`（string・必須・位置）: プレゼンテーションの ID<br>`out_dir`（string・必須・位置）: 出力のディレクトリ（無ければ作る）<br>`--size`（string・任意・既定 `"LARGE"`・値 `SMALL`・`MEDIUM`・`LARGE`）: サムネイルの大きさ |

結果の `metrics`: `auth_method`（string）・`client_config_exists`（boolean）・`credential_source`（string）・`out_dir`（string）・`pages`（integer）・`size`（string）・`state`（string）。`items` の要素: `detail`（string）・`name`（string）・`objectId`（string）・`result`（string）

## `ndf hooks`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/hooks.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/hooks.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf hooks capture` | `runtime`（string・必須・位置）<br>`version`（string・必須・位置）<br>`event`（string・必須・位置） |
| `ndf hooks check-budget` | 引数を受けない |
| `ndf hooks judge-cases` | `--cases`（string・任意）: 判定の例の組のパス（省けばコントラクトの組）<br>`--rounds`（integer・任意）: 1 つの例を打つ周の数（省けば上限の設定の値） |
| `ndf hooks render` | `--check`（boolean・任意）: 書かずに、作り直した結果と違えば stopped |
| `ndf hooks stats` | `--days`（number・任意・既定 `14.0`） |

結果の `metrics`: `calls`（integer）・`cases`（integer）・`days`（number）・`max_seconds`（number）・`min_seconds`（number）・`missed`（integer）。`items` の要素: `events`（object）・`expect`（string）・`got`（array of string）・`guards`（object）・`id`（string）・`path`（string）

## `ndf issues`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/issues.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/issues.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf issues apply` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の保存先（省けば環境変数の保存先かメインディレクトリの既定の保存先）<br>`--plan`（string・必須）<br>`--run-id`（string・任意） |
| `ndf issues candidates` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の保存先（省けば環境変数の保存先かメインディレクトリの既定の保存先）<br>`--add`（string・任意）: 必ず候補に入れる番号（カンマ区切り。上限で切らない）<br>`--limit`（integer・任意・既定 `10`）<br>`--depth`（string・任意・既定 `"diff"`・値 `diff`・`review`）<br>`--since-ref`（string・任意）: 差分の起点（タグか SHA） |
| `ndf issues depth-limits` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の保存先（省けば環境変数の保存先かメインディレクトリの既定の保存先）<br>`depth`（string・必須・位置・値 `review`・`overhaul`） |
| `ndf issues depth-record` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の保存先（省けば環境変数の保存先かメインディレクトリの既定の保存先）<br>`--depth`（string・必須・値 `review`・`overhaul`）<br>`--event`（string・必須・値 `timed_out`・`ran`）<br>`--left`（string・任意） |
| `ndf issues due` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の保存先（省けば環境変数の保存先かメインディレクトリの既定の保存先）<br>`depth`（string・必須・位置・値 `review`・`overhaul`）<br>`--manual`（boolean・任意）: 利用者が求めた |
| `ndf issues rank` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の保存先（省けば環境変数の保存先かメインディレクトリの既定の保存先）<br>`--scores`（string・任意）: 見積の入力（{"issues": [...]}）<br>`--sizes`（string・任意）: 見積もりの記録から決めた Size（{"issues": [{"number", "size"}]}。size は尺度の値か "over"）<br>`--capacity`（integer・任意）<br>`--milestones`（string・任意）: 着手の順のマイルストーンの題（カンマ区切り）<br>`--approved`（string・任意）<br>`--depth`（string・任意・既定 `"diff"`・値 `diff`・`review`）<br>`--since-ref`（string・任意）: 差分の棚卸しの起点（タグか SHA）。見積が無く起点以前に作られた課題を見直しへ回す（diff だけ） |
| `ndf issues revert` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の保存先（省けば環境変数の保存先かメインディレクトリの既定の保存先）<br>`--run`（string・必須） |
| `ndf issues settle` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の保存先（省けば環境変数の保存先かメインディレクトリの既定の保存先）<br>`--file`（string・必須）: 入力（{"settle": [...]}）<br>`--base`（string・任意）: 基底のブランチ（省けば既定のブランチ）<br>`--dry-run`（boolean・任意）: 確かめるだけで閉じない |

結果の `metrics`: `already`（array of integer）・`applied`（array of integer）・`at`（string）・`backward`（array of object）・`base`（string）・`before`（string か null）・`by_route`（object）・`candidates`（integer）・`carried`（array of integer）・`carry`（integer）・`closed`（array of integer）・`create_rejected`（array of object）・`created`（array of object）・`deferred`（array of integer）・`deferred_unscored`（array of integer）・`depth`（string）・`digest`（string）・`dropped`（array of object）・`event`（string）・`excluded`（array of object）・`failed`（array of object）・`forward`（array of object）・`held`（array of integer）・`left`（array of integer）・`left_for_review`（array of integer）・`limit`（integer）・`moved`（array of integer）・`needs_approval`（array of integer）・`next_steps`（array of string）・`notes`（array of string）・`open`（integer）・`order`（array of string）・`rejected_stale`（array of object）・`reparented`（array of integer）・`repo`（string）・`restored`（array of integer）・`returned`（array of integer）・`reverted`（array of integer）・`seconds`（number）・`since`（string か null）・`since_ref`（string か null）・`sized`（array of integer）・`skipped_changed`（array of integer）・`tables`（object）・`trigger`（array of string）・`unchanged`（array of integer）・`values`（object）。`items` の要素: `boundary`（object か null）・`child`（string）・`digest`（string）・`error`（string）・`evidence`（string）・`index`（integer）・`issue`（—）・`kind`（const "issue"）・`large_slot`（object か null）・`name`（string）・`number`（integer）・`order`（integer）・`over_scale`（array of integer）・`quality_slot`（object）・`reason`（string）・`result`（string）・`routes`（array of string）・`rows`（array of object）・`title`（string）・`updated_at`（string）・`verdict`（string）

## `ndf lanes`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/lanes.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/lanes.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf lanes answered` | `sprint_id`（string・必須・位置）<br>`--answer`（string・必須） |
| `ndf lanes init` | `plan_file`（string・必須・位置） |
| `ndf lanes lane add` | `plan`（string・必須・位置）<br>`lane_file`（string・必須・位置）<br>`--request`（string・任意） |
| `ndf lanes move` | `plan`（string・必須・位置）<br>`from_lane`（string・必須・位置）<br>`row`（string・必須・位置）<br>`to_lane`（string・必須・位置）<br>`--reason`（string・必須） |
| `ndf lanes question` | `plan`（string・必須・位置）<br>`--wrapper`（string・必須）: 質問を出したレーンのラッパーの名前（NDF_RELAY_NAME）<br>`--question`（string・必須）: hook が書いた質問のファイル（question.json） |
| `ndf lanes release` | `plan`（string・必須・位置）<br>`requests`（array of string・必須・位置）<br>`--result`（string・必須・値 `released`・`rejected`）<br>`--reason`（string・必須）<br>`--version`（string・任意）<br>`--release`（string・任意） |
| `ndf lanes request close` | `plan`（string・必須・位置）<br>`request`（string・必須・位置）<br>`--reason`（string・必須） |
| `ndf lanes row add` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`--issues`（array of integer・必須）<br>`--index`（integer・任意）: 並びの位置（0 が先頭）<br>`--args`（array of string・任意）<br>`--reason`（string・必須） |
| `ndf lanes row cancel` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`row`（string・必須・位置）: 行のキー（r1 など）<br>`--reason`（string・必須） |
| `ndf lanes row carry` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`row`（string・必須・位置）: 行のキー（r1 など）<br>`--issues`（array of integer・必須）<br>`--reason`（string・必須） |
| `ndf lanes row end` | `sprint_id`（string・必須・位置）<br>`--outcome`（string・必須・値 `completed`・`cancelled`） |
| `ndf lanes row move` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`row`（string・必須・位置）: 行のキー（r1 など）<br>`--index`（integer・必須）: 並びの位置（0 が先頭）<br>`--reason`（string・必須） |
| `ndf lanes row resumed` | `sprint_id`（string・必須・位置） |
| `ndf lanes row start` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`row`（string・必須・位置）: 行のキー（r1 など）<br>`--sprint`（string・必須）: スプリントのステートマシンの ID |
| `ndf lanes run` | `plan`（string・必須・位置） |
| `ndf lanes set` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`--touches`（array of string・任意）<br>`--after`（array of string・任意）<br>`--request`（string・任意）<br>`--reason`（string・必須） |
| `ndf lanes start` | `plan_file`（string・必須・位置） |
| `ndf lanes status` | `plan`（string・必須・位置） |
| `ndf lanes stop` | `plan`（string・必須・位置）<br>`--now`（boolean・任意）: 区切りを待たずに中止する（未着手の行を中止にし、実行中のスプリントを中止させる）<br>`--reason`（string・必須） |
| `ndf lanes wait` | `plan`（string・必須・位置）<br>`--after`（integer・任意・既定 `0`）: 計画のステートマシンの事象の番号（前の wait の next が渡す）<br>`--known-stalls`（array of string・任意）: 知らせ済みの止まり（<レーン>/<行>） |

結果の `metrics`: `changes`（object）・`closed_requests`（array of object）・`complete`（boolean）・`current_id`（string）・`errors`（array of string）・`failed`（array of string）・`lane_machine_id`（string）・`last_seq`（integer）・`launched`（array of string）・`machine`（object）・`new_stalls`（array of string）・`next_row`（object か null）・`open_requests`（array of object）・`opened`（boolean）・`outcome`（string か null）・`plan`（string）・`plan_id`（string）・`releases`（object）・`request`（string か null）・`requests`（array of string）・`result`（string）・`row`（string）・`run_pid`（integer）・`running`（array of string）・`seq`（integer か null）・`sprint_id`（string）・`stalled`（integer）・`stalls`（array of string）・`state`（string）・`wakeable`（array of array of string）。`items` の要素: `actor`（string）・`after`（array of string）・`at`（string）・`kind`（string）・`lane`（string）・`liveness`（object）・`machine_wait`（object）・`next`（object）・`path`（string）・`payload`（object か null）・`reason`（string か null）・`rev`（integer か null）・`row_key`（string か null）・`rows`（array of object）・`seq`（integer）・`sprint_id`（string）・`state`（string か null）

## `ndf launch-args`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/launch-args.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/launch-args.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf launch-args` | 引数を受けない |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `args`（array of string）・`kind`（string）・`reason`（string）

## `ndf loop`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/loop.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/loop.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf loop acceptance check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--issue`（array of integer・必須）: 照らす課題（繰り返せる）<br>`--criteria`（array of string・任意）: '<課題>:<条件の番号>'。その課題はこの条件だけを照らす |
| `ndf loop acceptance record` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--pre-merge`（array of string・任意）: '<条件の番号>=<手段（テスト名・実行したコマンド）>'<br>`--post-release`（array of integer・任意）: リリースの後でしか確かめられない条件の番号<br>`--issue`（integer・必須） |
| `ndf loop acceptance show` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--issue`（integer・必須） |
| `ndf loop answer` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--pr`（integer・必須）<br>`--answer`（string・必須・値 `approved`・`request_changes`・`rejected`）<br>`--note`（string・任意・既定 `""`）: 変更要求の理由など |
| `ndf loop check-trigger` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--base`（string・必須）: 範囲の始め（前回の検査の位置）<br>`--head`（string・任意・既定 `"HEAD"`）: 範囲の終わり<br>`--exclude`（array of string・任意）: 数えない PR（検査・リリース）のマージのコミット<br>`--threshold`（integer・任意）: 行数の閾値（既定は .ndf/pace.json の triggers.lines） |
| `ndf loop close-plan` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--pace`（string・必須）<br>`--route`（array of string・必須・値 `promote`・`merged-by-check`・`manual`・`none`）<br>`--mvv`（boolean・任意）: MVV の判定がある<br>`--record-pr`（integer・任意）: リリース記録の PR の番号（0 は記録なし）<br>`--retrospective-due`（boolean・任意） |
| `ndf loop created` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`issues`（array of integer・必須・位置） |
| `ndf loop design-tests` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--issue`（array of integer・任意）: HEAD の issues/issue-<N>-design*.md を読む<br>`--design`（array of string・任意）: 作業場所の設計文書のパス |
| `ndf loop exclude` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--issue`（integer・必須）<br>`--reason`（string・必須） |
| `ndf loop gate` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--pr`（integer・必須） |
| `ndf loop get` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--field`（string・任意・値 `status`・`stop`・`holder`・`row`・`laps`・`current`・`rejections`・`created`） |
| `ndf loop lap` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--failed`（boolean・任意）: 手順の失敗のマーカー<br>`--failure-key`（string・任意）<br>`--version`（array of string・任意）: この周で出したバージョン（記録に無いもの）<br>`--returned`（boolean・任意）: リリースを承認されずに人へ戻した<br>`--unreadable`（string・任意）: バージョンの記録を読めなかった理由 |
| `ndf loop released` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--version`（string・必須） |
| `ndf loop review-args` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--purpose`（string・必須・値 `design`・`code`）: cross-review run の --purpose<br>`--pr`（integer・必須）<br>`--state-dir`（string・必須）<br>`--base`（string・必須） |
| `ndf loop select` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--candidates`（string・必須）: 候補の行の JSON の配列のファイル<br>`--first`（array of integer・任意）: 先頭に選ぶ課題（直して出し直すと回答した課題）<br>`--size`（integer・任意・既定 `1`）: 周に入れる課題の数 |
| `ndf loop sprint` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--mode`（string・必須）<br>`--basis`（string・必須）: モードの判定の根拠 |
| `ndf loop start` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--row`（string・任意）: 結び付けるレーンの行のステートマシンの ID（`ndf progress new --loop` の結果） |
| `ndf loop stop` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--by`（string・必須・値 `human`・`organizer`）<br>`--reason`（string・任意・既定 `""`）<br>`--now`（boolean・任意）: 周の境を待たずにその場で止める |

結果の `metrics`: `answer`（string）・`designs`（integer）・`excluded`（integer）・`head`（string）・`lap`（integer）・`lines`（integer）・`missing`（integer）・`names`（integer）・`outcome`（string）・`pace`（string）・`record_pr`（string）・`remaining`（array of integer）・`renamed`（integer）・`skipped`（integer）・`status`（string）・`streak`（integer）・`threshold`（integer）・`unverified`（integer）・`verify`（boolean）。`items` の要素: `chosen`（boolean）・`created`（array of integer）・`current`（object か null）・`doc`（string）・`holder`（string か null）・`issue`（integer）・`kind`（string）・`laps`（array of object）・`line`（integer）・`means`（string）・`name`（string）・`reason`（string か null）・`rejections`（array of object）・`renamed_to`（string）・`result`（string）・`review`（array of string）・`row`（string か null）・`section`（string か null）・`status`（string）・`steps`（array of string）・`stop`（object か null）・`text`（string）・`threshold`（integer）・`trigger`（string）・`value`（integer）

## `ndf manifests`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/manifests.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/manifests.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf manifests render` | `--check`（boolean・任意）: 書かずに、作り直した結果と違えば stopped |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `path`（string）

## `ndf materials`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/materials.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/materials.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf materials` | `--issue`（array of integer・必須）: 課題の番号（1 つ以上）<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所<br>`--out`（string・任意）: 判定の根拠のファイルの下書きを書く先 |

結果の `metrics`: `gaps`（integer）・`issues`（integer）・`out`（string）。`items` の要素: `files`（array of object）・`files_found`（integer）・`kind`（string か null）・`number`（integer）・`public_interface`（object）

## `ndf notify`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/notify.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/notify.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf notify send` | 引数を受けない |

結果の `metrics`: `duplicate`（integer）・`failed`（integer）・`left`（integer）・`sent`（integer）・`stale`（integer）・`unconfigured`（integer）。`items` の要素: 決まった項目を持たない

## `ndf plan`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/plan.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/plan.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf plan check` | `plans`（array of string・必須・位置） |
| `ndf plan next` | `plan`（string・必須・位置）<br>`--from`（string・任意）<br>`--state-dir`（string・任意）<br>`--answer`（string・任意）<br>`--by`（string・任意）: 回答した者（human・conductor・organizer か service:<主体>）。--answer と組で渡す<br>`--waiting`（string・任意）: 回答する待ち（<ステップ>:<待ちの番号>）。返された routes が持つ。今の待ちと違えば記録しない<br>`--presented`（string・任意）: 回答する提示の識別子。返された routes が持つ。今の提示と違えば記録しない<br>`--set`（array of string・任意）<br>`--fresh`（boolean・任意） |
| `ndf plan pending` | 引数を受けない |
| `ndf plan queue` | `plans`（array of string・必須・位置）<br>`--then`（array of array of string・任意）<br>`--from`（array of string・任意）<br>`--resume`（boolean・任意）<br>`--fresh`（boolean・任意）<br>`--max`（integer・任意）: 同時に流すプランの本数の上限（無ければ触るファイルの重ならない組の本数まで）<br>`--queue-dir`（string・任意） |
| `ndf plan wait` | `plans`（array of string・必須・位置）<br>`--state-dir`（string・任意）<br>`--timeout`（number・任意・既定 `3000.0`） |

結果の `metrics`: `gates`（integer）・`ok`（integer）・`pending`（integer）・`plans`（integer）・`production`（boolean）・`round`（integer）・`steps`（integer）・`stopped`（integer）・`waited_seconds`（number）・`waves`（integer）。`items` の要素: `cause`（object）・`detail`（string）・`gate`（boolean）・`kind`（string）・`name`（string）・`number`（integer）・`path`（string）・`plans`（array of string）・`presentation_path`（string）・`production`（boolean）・`reason`（string）・`routes`（object）・`run`（string）・`status`（string か null）・`step`（string か null）・`updated_at`（string か null）・`workflow`（string）

## `ndf progress`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/progress.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/progress.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf progress cancel` | `machine_id`（string・必須・位置）<br>`--reason`（string・必須）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress check` | `machine_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--output`（string・必須）: 確かめる工程の出力の JSON のファイル<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress disable` | `machine_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--reason`（string・必須）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress docs-only` | `--base`（string・必須）: ベースブランチ<br>`--remote`（string・任意）: リモート名（省けば worktree の設定の remote か既定）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress enable` | `machine_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--reason`（string・必須）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress finish` | `machine_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--reason`（string・必須）<br>`--output`（string・必須）: 工程の出力の規約に合う JSON のファイル<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress gathered` | `--branch`（string・任意）: スプリントブランチ（省けば worktree のブランチ）<br>`--remote`（string・任意）: リモート名（省けば worktree の設定の remote か既定）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress new` | `name`（string・必須・位置）: スプリント名<br>`--mode`（string・任意）<br>`--issues`（array of integer・任意）<br>`--loop`（string・任意）: 開発ループの行のステートマシンを作る（工程なし。ループの名前）<br>`--pace`（string・任意・既定 `"normal"`）<br>`--disable`（array of string・任意）: <工程>=<理由><br>`--requirements`（string・任意）: 要求と受け入れ条件の出力（工程の出力の規約に合う JSON）<br>`--parent`（string・任意）: 親のステートマシン（レーンのステートマシン）の ID<br>`--carry`（string・任意）: 無効にした工程の出力のキーを、中止したこのステートマシンの文脈から写す<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress reread` | `machine_id`（string・必須・位置）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress restore` | `--from`（string・必須）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress retry` | `machine_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--reason`（string・必須）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf progress show` | `machine_id`（string・必須・位置）<br>`--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |

結果の `metrics`: `answer`（string）・`behavior_docs_in_diff`（integer）・`cancelled_rows`（integer）・`code_in_diff`（integer）・`current_id`（string）・`disabled`（array of string）・`docs_only_diff`（integer）・`history`（array of object）・`machine_id`（string）・`mode`（string）・`move`（string）・`outputs`（array of string）・`rev`（integer）・`rows`（integer）・`seq`（integer）・`seqs`（array of integer）・`stage`（string）・`state`（string か null）・`step`（string）。`items` の要素: `at`（string か null）・`changed`（boolean）・`error`（string）・`from`（string）・`gates`（array of string）・`key`（string）・`kind`（string）・`label`（string）・`number`（integer）・`outputs`（array of string）・`path`（string）・`phase`（string）・`reason`（string か null）・`restored`（string）・`retired`（string）・`source`（string か null）・`stage`（string）・`state`（string か null）

## `ndf project`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/project.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/project.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf project branch-strategy` | `verb`（string・必須・位置・値 `init`・`measure`・`write`・`migrate`・`remove`）: init・measure・write・migrate・remove<br>`--kind`（string・任意・値 `web-develop`・`web-trunk`・`mobile`・`docs`・`plugin`）: 代表のリポジトリの種類（init は必須。measure・write は候補の先頭にする）<br>`--answers`（string・任意）: 質問への回答（セクションの JSON Pointer → 値）<br>`--result`（string・任意）: write の基盤にする measure の結果（省けば今のセクション、無ければ測り直す）<br>`--digest`（string・任意）: 示した差分のハッシュ。合うときだけ書く<br>`--dry-run`（boolean・任意）: init で書かずに差分とハッシュだけを示す<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所 |
| `ndf project check` | `--force`（boolean・任意）: 新しくても古いと判定する（手動の再解析）<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所 |
| `ndf project measure` | `--out`（string・必須）: 測定の結果の保存先（リポジトリの外の一時ディレクトリ）<br>`--budget`（number・任意）: 締め切りの秒（省けば上限の設定の project.measure）<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所 |
| `ndf project schema` | `--out`（string・任意）: 書く先。省けば標準出力 |
| `ndf project write` | `--measure`（string・必須）: ndf project measure の測定の結果<br>`--answers`（string・任意）: 質問の項目への回答（キー → {value} か {unknown}）<br>`--dry-run`（boolean・任意）: 書かずに差分だけを出す<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所 |

結果の `metrics`: `dry_run`（boolean）・`elapsed_seconds`（number）・`files`（array of string）・`inputs`（integer）・`measured`（integer）・`out`（string）・`question`（integer）・`unknown`（integer）・`unknown_items`（array of string）・`written`（integer）。`items` の要素: `analyzed`（—）・`declared`（—）・`kept_previous`（boolean）・`key`（string）・`kind`（string）・`name`（string）・`origin`（string）・`pattern`（string）・`reason`（string）・`result`（string）・`source`（string）・`text`（string）

## `ndf records`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/records.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/records.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf records cleanup-stage` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`prs`（array of integer・必須・位置） |
| `ndf records close-check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`issue`（integer・必須・位置）<br>`--verification`（string・任意）: リリース後テストの記録（release-verification のスキーマの JSON）<br>`--require`（boolean・任意）: 記録が無ければ閉じない |
| `ndf records glossary meanings` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf records glossary render` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--check`（boolean・任意）: 書かずに、今と違うファイルを出す |
| `ndf records gws-check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf records handoff check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`path`（string・必須・位置）<br>`--trim`（boolean・任意）: テンプレートに無いセクションを退避先へ移してから確かめる<br>`--history`（string・任意）: 退避先（--trim のとき要る） |
| `ndf records mvv answer` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--version`（integer・任意）: 回答したときの MVV のバージョン（無ければ worktree の設定の現行のバージョン） |
| `ndf records mvv cite` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--gate`（string・必須・値 `design`・`release`）: 資料を返す承認ゲートの区分（design なら「目指す姿の根拠」のセクションと、決定ごとの根拠の行の有無も確かめる）<br>`--base`（string・必須）: ベースブランチ（取得して承認済みの MVV を読む）<br>`files`（array of string・必須・位置）: 確かめる承認資料 |
| `ndf records mvv current` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--base`（string・必須）: ベースブランチ<br>`--allow-mismatch`（boolean・任意）: 本文が承認の記録と合わなくても止まらず、警告を付けて状態だけを返す |
| `ndf records mvv gate` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--gate`（string・必須・値 `design`・`release`）: 承認ゲートの区分<br>`--machine`（string・任意）: ステートマシンの ID（プランの外で打つとき。環境変数 NDF_STAGE_INPUT の代わりに文脈から入力を組み、両方は渡せない）。NDF_STAGE_INPUT は工程の入力の規約 stage-in の平らな形で、外れれば invalid、キーが足りなければ input_missing で止まる<br>`--expect-pr`（integer・任意）: 承認する PR の番号（--gate release のときだけ）。判定に使う入力の PR と違えば、PR を読まず判定もコメントもせずに not_applicable で止まる |
| `ndf records mvv signals` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--base`（string・必須）: ベースブランチ |
| `ndf records preflight` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf records reading add` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--file`（string・必須）: 入力の JSON のファイル |
| `ndf records reading check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--gate`（string・必須・値 `design`・`release`）: 承認ゲートの種類<br>`--pr`（integer・必須）: 承認する PR の番号<br>`--material`（string・任意）: 会話のセッションのリリースで present に渡す承認資料のファイル<br>`--questions-out`（string・必須）: 作り直した承認の質問（questions）を書くファイル |
| `ndf records reading prompt` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--file`（string・必須）: 入力の JSON のファイル |
| `ndf records spec finalize` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--spec`（array of string・必須）: 溶かした先の確定仕様（複数可）<br>`--remove`（array of string・任意）: 消す設計（複数可）<br>`--source-of`（array of string・任意）: <語>=<ファイル> で語の SSOT を明示する<br>`--message（-m）`（string・任意）: コミットの件名。無ければコミットせずパスだけを出す |
| `ndf records usage report` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--since`（string・必須）<br>`--until`（string・必須） |
| `ndf records usage stop` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |

結果の `metrics`: `bytes`（integer）・`commit`（string）・`lines`（integer）・`prompt`（string）・`revision_due`（boolean）。`items` の要素: `agent_id`（string）・`at`（string）・`broken_rows`（integer）・`caller`（string）・`cause`（object）・`closable`（boolean）・`closed_issues`（integer）・`context`（string）・`error`（string）・`files`（array of string）・`gate`（—）・`issue`（integer）・`kind`（string）・`lines`（integer か array）・`missing`（string）・`missing_path`（string）・`moved`（string）・`mvv_version`（integer か null）・`name`（string）・`overrides`（integer）・`path`（string）・`pr`（—）・`prs`（array of object）・`question_key`（string）・`reads`（array of object）・`reason`（string）・`repo`（string）・`result`（string）・`since`（string）・`state`（—）・`status`（string）・`summary`（string）・`term`（string）・`transcript`（string）・`unknown`（integer）・`unknown_pass`（integer）・`unknown_rows`（integer）・`until`（string）・`usage`（object か string）・`version`（integer）・`violation`（string）・`warnings`（array of string）・`word`（string）

## `ndf refactor`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/refactor.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/refactor.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf refactor collected` | `--paths`（array of string・必須）<br>`--timeout`（number・任意）<br>`--work`（string・任意・既定 `"."`） |
| `ndf refactor release` | `--pr`（integer・必須）<br>`--work`（string・任意・既定 `"."`） |
| `ndf refactor run` | `--pr`（integer・必須）<br>`--branch`（string・必須）<br>`--base`（string・必須）<br>`--scope`（array of string・必須）<br>`--required`（array of string・必須）<br>`--ci-test`（array of string・任意）<br>`--symbols`（array of string・任意）<br>`--participants`（array of string・任意）<br>`--max-fix`（integer・任意・既定 `2`）<br>`--budget-minutes`（integer・任意）<br>`--safety-net-minutes`（number・任意）<br>`--candidates`（string・任意）<br>`--work`（string・任意・既定 `"."`） |
| `ndf refactor scope-args` | `--base`（string・必須）<br>`--tests`（array of string・任意）<br>`--scope`（array of string・任意）<br>`--work`（string・任意・既定 `"."`） |
| `ndf refactor state` | `--pr`（integer・必須）<br>`--work`（string・任意・既定 `"."`） |
| `ndf refactor tests` | `--changed`（array of string・必須）<br>`--work`（string・任意・既定 `"."`） |
| `ndf refactor touched` | `--base`（string・必須）<br>`--head`（string・任意・既定 `"HEAD"`）<br>`--scope`（array of string・必須）<br>`--symbols`（array of string・任意）<br>`--work`（string・任意・既定 `"."`） |

結果の `metrics`: `adopted`（integer）・`budget_minutes`（number）・`coding_minutes`（number）・`collected`（integer）・`deferred`（integer）・`files`（integer）・`fix_rounds`（integer）・`flaky_fixed`（integer）・`items`（integer）・`judged`（integer）・`kept`（integer）・`open`（integer）・`paths`（integer）・`plan_comment`（string）・`report`（string）・`reverted`（integer）・`safety_net_minutes`（number）・`scope_tests`（integer）・`skipped`（integer）・`swept`（integer）・`symbols`（integer）・`uncollected`（integer）・`undecided`（integer）・`unmapped`（integer）・`unreadable`（integer）・`whole_files`（integer）。`items` の要素: `by`（array of string）・`collected`（array of string）・`estimate`（object）・`feasibility`（array of object）・`files`（array of string）・`final`（object か null）・`head`（string）・`history`（array）・`id`（string）・`inherited`（array of string）・`kind`（string）・`outside_failures`（object）・`path`（string）・`paths`（array of string）・`phases`（object）・`reason`（string）・`retired_from`（string）・`retired_to`（string）・`rounds`（integer）・`start`（string）・`status`（string）・`symbol`（string）・`symbols`（object）・`tier`（string）・`title`（string）・`uncollected`（array of string）・`undecided`（array of string）・`unmapped`（array of string）・`unreadable`（array of string）・`whole_files`（array of string）

## `ndf relay`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/relay.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/relay.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf relay account add` | `name`（string・必須・位置）: アカウントの名前（英小文字・数字・- と _ の 32 字まで）<br>`--code`（string・任意）: 認可コード。- なら標準入力の 1 行（引数と記録に残さない） |
| `ndf relay account add-api-key` | `name`（string・必須・位置）: API キーの名前<br>`--account`（string・必須）: 結び付ける登録済みアカウント<br>`--key-file`（string・任意）: キーの値を 1 行目に持つファイル（省けば標準入力の 1 行）<br>`--monthly`（string・任意）: 月の額（ドル） |
| `ndf relay account add-bedrock` | `--profile`（string・任意）: AWS のプロファイル<br>`--region`（string・任意）: 地域（省けばプロファイルの region）<br>`--model`（string・任意）: モデルの ID<br>`--yes`（boolean・任意）: 確かめが通れば保存する（前の設定を置き換える） |
| `ndf relay account api-key-helper` | `target`（string・必須・位置）: metered か API キーの名前<br>`fingerprint`（string・任意・位置）: キーの値の SHA-256 の 16 進（metered だけ）<br>`--generation`（string・任意）: 起動のときの登録簿のキーの世代（API キーの名前だけ）<br>`--record`（string・任意）: 払い出しの記録（ラッパーが作る）<br>`--id`（string・任意）: helper の ID<br>`--pin`（string・任意）: 確かめた設定への固定のファイル（ラッパーと ndf plan が作る） |
| `ndf relay account api-key-monthly` | `name`（string・必須・位置）: API キーの名前<br>`usd`（string・必須・位置）: 月の額（ドル。0 で外す） |
| `ndf relay account api-key-use` | `mode`（string・任意・位置・値 `off`・`always`・`near-limit`）: 使い方（省けば今の値を出す）<br>`--weekly-at`（string・任意）: 閾値（百分率。1〜100） |
| `ndf relay account capacity` | `name`（string・必須・位置）: アカウントの名前<br>`five`（string・必須・位置）: 5 時間のクォータ（USD か -）<br>`seven`（string・必須・位置）: 週のクォータ（USD か -） |
| `ndf relay account check` | `target`（string・必須・位置）: metered か API キーの名前 |
| `ndf relay account clear` | 引数を受けない |
| `ndf relay account config-gate` | `--pin`（string・必須）: 確かめた設定への固定のファイル<br>`--record`（string・任意）: 払い出しの記録（ラッパーが作る）<br>`--id`（string・任意）: helper の ID |
| `ndf relay account list` | 引数を受けない |
| `ndf relay account remove` | `target`（string・必須・位置）: アカウントの名前・metered・API キーの名前 |
| `ndf relay account show` | 引数を受けない |
| `ndf relay account use` | `name`（string・必須・位置） |
| `ndf relay cache report` | `--since`（string・任意）: この日時（ISO 8601）以後に始まった待ちと、以後の Stop の行だけを集計する |
| `ndf relay cap` | `--named`（integer・任意）: マシンの設定の上限を書く（1 以上の整数）<br>`--clear`（boolean・任意）: マシンの設定を消す（環境変数 → 上限の設定に戻る） |
| `ndf relay context calibrate` | `--days`（integer・任意・既定 `30`）: 較正に使う直近の日数 |
| `ndf relay context record` | `--verdict`（string・任意・値 `再起動`）: 再起動の行を書く（--reason と組む）<br>`--event`（string・任意・値 `引継ぎ開始`・`復元完了`）: マーカーの行を書く<br>`--reason`（string・任意・値 `容量`・`運用上限`・`費用`・`独立性`・`手動`・`カットポイント`・`待ち`）: 再起動の理由<br>`--work`（string・必須）: 作業の識別子（課題番号の並び） |
| `ndf relay context report` | `--work`（string・任意）: 集計する作業の識別子<br>`--all`（boolean・任意）: 記録にあるすべての作業を集計する<br>`--since`（string・任意）: この日（YYYY-MM-DD）以降の判定の行だけを読む<br>`--from`（string・任意）: この時刻（ISO 8601）からの呼び出しと行だけを数える<br>`--until`（string・任意）: この時刻（ISO 8601）までの呼び出しと行だけを数える |
| `ndf relay context resolve` | 引数を受けない |
| `ndf relay decide` | 引数を受けない |
| `ndf relay install` | `--replace-old`（boolean・任意）: 旧い NDF の複製を置き換える（旧いラッパーが動いていれば断る） |
| `ndf relay ls` | `--activity`（boolean・任意）: 動いている行に conductor の活動（turn_done・moved・background）も載せる |
| `ndf relay mark` | `session_id`（string・必須・位置） |
| `ndf relay notice` | 引数を受けない |
| `ndf relay pending` | 引数を受けない |
| `ndf relay restart` | `name`（string・必須・位置） |
| `ndf relay run` | `--name`（string・任意）<br>`rest`（array of string・任意・位置）<br>`--prompt`（string・任意） |
| `ndf relay spawn` | `--name`（string・任意）<br>`rest`（array of string・任意・位置）<br>`--cwd`（string・任意）<br>`--role`（string・任意）<br>`--plan`（string・任意）<br>`--seat`（string・任意）<br>`--held`（array of string・任意）: 起動の許可が数えた、実行中の行を持つレーンの名前（ラッパーの生死を問わない。ハブも数える）<br>`--replace`（boolean・任意）: 同じ名前のラッパーを、上限を判定してから止めて置き換える<br>`--seated`（boolean・任意）: 行を始めた起動の席を使う続きの起動（本数を判定しない） |
| `ndf relay status` | 引数を受けない |
| `ndf relay stop` | `name`（string・任意・位置）: 止めるラッパーの名前<br>`--all`（boolean・任意）: 動いている名前つきのラッパーをすべて止める |
| `ndf relay uninstall` | 引数を受けない |

結果の `metrics`: `accounts`（integer）・`by_version`（object）・`d_median`（number か null）・`decide_from`（integer）・`estimated_avoided_cost`（number）・`estimated_avoided_rewrites`（integer）・`estimated_avoided_tokens`（number）・`hit_rate_by_age`（object）・`inside`（boolean）・`keys`（integer）・`limit`（integer）・`limit_stopped_waits`（integer）・`logs`（integer）・`orphan_results`（integer）・`partial_waits`（integer）・`preempted_wakes`（integer）・`samples`（integer）・`skipped`（integer）・`source`（string）・`stopped`（integer）・`unmatched_stopped_waits`（integer）・`unmatched_wakes`（integer）・`unpriced_waits`（integer）・`usage_skipped`（integer）・`value`（integer）・`voided_waits`（integer）・`wait_seconds`（object）・`waits`（integer）・`wake_cost_median`（number か null）・`wakes`（integer）・`works`（integer）。`items` の要素: `account`（string か null）・`args`（array か null）・`at`（string）・`auto_compacts`（integer か string）・`background`（integer か null）・`band`（string か null）・`basis`（string か null）・`cap`（integer）・`capacity_declared`（object）・`context`（integer か null）・`cwd`（string か null）・`decide_from`（integer）・`details`（object）・`effective_limit`（integer か null）・`email`（string か null）・`estimate`（object か null）・`event`（string）・`expires_at`（string）・`five_hour`（number か null）・`handoff_calls`（number か null）・`handoff_output`（number か null）・`keys`（array）・`kind`（string）・`limit`（integer）・`missing`（array）・`model`（string か null）・`monthly_usd`（number か null）・`moved`（number か null）・`name`（string）・`ok`（boolean）・`org_name`（string か null）・`parts`（object）・`passed_once`（boolean）・`path`（string）・`pending`（array か null）・`percent`（number か null）・`pid`（integer か null）・`pinned`（boolean）・`plan`（string か null）・`position`（string）・`prompt`（string）・`provider`（string）・`quality`（object）・`reason`（string か null）・`refusal`（const "max-named"）・`registered`（boolean）・`relay`（boolean）・`replaced`（boolean）・`restart_count`（integer）・`restarts`（object）・`restore_calls`（number か null）・`restore_context`（number か null）・`restores`（array）・`role`（string か null）・`run`（string）・`running`（boolean か integer）・`runtime`（string）・`samples`（object）・`seat`（string か null）・`session_id`（string か null）・`sessions`（integer）・`seven_day`（number か null）・`source`（string）・`sources`（object）・`stage`（string か null）・`stage_calls`（object）・`stage_expiries`（object）・`state`（string か null）・`status`（string か null）・`summary`（string）・`text`（string）・`transcript`（string か null）・`turn_done`（boolean か null）・`unknown_parts`（object）・`updated_at`（string か null）・`url`（string）・`use`（string）・`verdict`（string か null）・`version`（string）・`weekly_at`（number）・`window`（integer か null）・`work`（string）・`workflow`（string）

## `ndf release-steps`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/release-steps.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/release-steps.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf release-steps answer` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--answer`（string・必須・値 `approved`・`rejected`）<br>`--presented`（string・必須）: 提示した HEAD の SHA（present の outputs.head）<br>`--material-sha256`（string・任意）: 回答した承認資料のファイル全体の SHA-256（--by user で必須。present の routes が固定する。#4446）<br>`--by`（string・必須・値 `user`・`mvv`）: 回答した担い手（利用者 user / MVV 判定 mvv。迂回は user だけを数える） |
| `ndf release-steps approval-check` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・必須） |
| `ndf release-steps approval-state` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須） |
| `ndf release-steps assemble` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--base`（string・必須）: ベースブランチ（設定 project.json の branch_strategy.branches.base）<br>`--production`（string・必須）: 本番ブランチ（設定 project.json の branch_strategy.branches.production）<br>`--channel`（string・必須・値 `production`・`dev`）<br>`--level`（string・任意・既定 `"patch"`・値 `major`・`minor`・`patch`）<br>`--plugin`（string・任意）: ブランチの名前にバージョンを使う単位（省けば単位の順の先頭）<br>`--units`（array of string・任意）: 集めるスプリントブランチ（release_branch の設定で必須）<br>`--pr`（integer・任意）: 本番の PR の番号（承認の対象が production_pr の設定で必須） |
| `ndf release-steps cleanup` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--branch`（array of string・必須） |
| `ndf release-steps config-check` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--before`（string・必須） |
| `ndf release-steps config-snapshot` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--out`（string・必須） |
| `ndf release-steps materials` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--base`（string・必須）: ベースブランチ（設定 project.json の branch_strategy.branches.base）<br>`--production`（string・必須）: 本番ブランチ（設定 project.json の branch_strategy.branches.production）<br>`--channel`（string・必須・値 `production`・`dev`）<br>`--level`（string・任意・既定 `"patch"`・値 `major`・`minor`・`patch`）<br>`--plugin`（string・任意）: ブランチの名前にバージョンを使う単位（省けば単位の順の先頭）<br>`--units`（array of string・任意）: 集めるスプリントブランチ（release_branch の設定で必須）<br>`--pr`（integer・任意）: 本番の PR の番号（承認の対象が production_pr の設定で必須）<br>`--out`（string・任意）: 承認資料を書くファイル（省けば標準出力） |
| `ndf release-steps merge` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--budget`（number・必須）: 必須のチェックを待つ秒 |
| `ndf release-steps present` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--material`（string・任意）: 承認資料のファイル（省けば PR の本文） |
| `ndf release-steps publish` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--revision`（string・必須）<br>`--since`（string・任意）: 前の push の先頭。祖先なら --revision までの first-parent のコミットを古い順にすべて公開する |
| `ndf release-steps pull-checks` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須） |
| `ndf release-steps record` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・必須）<br>`--production-sha`（string・任意）: 本番へ入れたコミット（strict の squash で閉じた PR の記録に要る） |
| `ndf release-steps records` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須） |
| `ndf release-steps sync-base` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--main-dir`（string・必須）<br>`--base`（string・必須） |
| `ndf release-steps verification-record` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--record`（string・必須）: リリース後テストの記録の JSON（スキーマ release-verification） |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `change`（string）・`checks`（integer）・`checks_passed`（integer）・`checks_skipped`（integer）・`failed`（array of string）・`kind`（string）・`name`（string）・`number`（integer）・`path`（string）・`reason`（string）・`result`（string）

## `ndf route`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/route.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/route.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf route render` | `purpose`（string・必須・位置）<br>`--out`（string・必須）<br>`--set`（array of string・任意）<br>`--add`（array of string・任意） |
| `ndf route repeat` | `--result`（string・必須）<br>`--log`（string・必須） |
| `ndf route run` | `purpose`（string・必須・位置）<br>`--out`（string・必須）<br>`--set`（array of string・任意）<br>`--add`（array of string・任意） |
| `ndf route templates` | 引数を受けない |
| `ndf route test` | `--result`（string・任意）<br>`--failed-from`（string・任意）<br>`command`（string・任意・位置） |

結果の `metrics`: `exit_code`（integer）・`failed_runs`（integer）・`failed_tests`（array of string）・`runs`（integer）・`same_cause`（integer）・`streak`（integer）・`templates`（integer）。`items` の要素: `command`（string）・`exit_code`（integer）・`kind`（string）・`name`（string）・`params`（array of string）・`passed`（boolean）・`path`（string）・`purpose`（string）・`suite`（string）

## `ndf samples`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/samples.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/samples.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf samples verify` | `--peer`（string・任意）: この相手の見本だけを突き合わせる |

結果の `metrics`: `calls`（object）・`samples`（integer）。`items` の要素: `detail`（string）・`kind`（string）・`peer`（string）・`sample`（string）

## `ndf settings`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/settings.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/settings.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf settings migrate` | `--root`（string・必須）: リポジトリの根<br>`--apply`（string・任意）: 承認した差分のハッシュ（migrate が示した 64 桁の 16 進） |

結果の `metrics`: `digest`（string）・`files`（integer）。`items` の要素: `file`（string）・`kind`（const "settings-file"）・`legacy_keys`（integer）・`operations`（integer）・`patch`（array of object）・`path`（string）・`reindent`（boolean）

## `ndf statusline`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/statusline.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/statusline.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf statusline ensure` | 引数を受けない |
| `ndf statusline restore` | 引数を受けない |
| `ndf statusline set` | 引数を受けない |
| `ndf statusline status` | 引数を受けない |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `action`（string）・`backup`（boolean）・`current`（object）・`message`（string）

## `ndf test-slot`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/test-slot.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/test-slot.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf test-slot report` | `--since`（string・任意・既定 `"7d"`）: 期間（既定 7d） |
| `ndf test-slot run` | `--wait-limit`（integer・任意）: スロットとメモリを待つ上限の秒（既定は設定の値）<br>`command`（array of string・任意・位置）: -- の後ろにシェルのコマンド 1 つ |

結果の `metrics`: `available_mib`（integer か null）・`gave_up`（integer）・`memory_stopped`（integer）・`oom_kill_increase`（integer）・`runs`（integer）・`waited_runs`（integer）・`waited_seconds`（number）。`items` の要素: `detail`（string）・`retired_from`（string）・`retired_to`（string）

## `ndf training`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/training.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/training.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf training check` | `--runtime`（array of string・任意・値 `claude`・`codex`）: 確かめる担当（既定は確かめる口のある担当すべて） |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `checked_at`（string）・`reason`（string）・`rewrites`（array of object）・`runtime`（string）・`training`（boolean か null）

## `ndf worker`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/worker.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/worker.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf worker append` | `--report`（string・必須）<br>`--done`（boolean・任意） |
| `ndf worker attention` | `progress`（string・必須・位置） |
| `ndf worker closing-check` | `--base`（string・必須）<br>`--cwd`（string・任意・既定 `"."`） |
| `ndf worker collect` | `--report`（string・必須） |
| `ndf worker draft` | `--state-dir`（string・必須） |
| `ndf worker plan-edit` | `plan`（string・必須・位置）<br>`--new`（string・必須）<br>`--reason`（string・必須）<br>`--state-dir`（string・任意） |
| `ndf worker plan-edits` | `plans`（array of string・必須・位置）<br>`--state-dir`（string・任意） |
| `ndf worker prepare` | `--dir`（string・必須）<br>`--name`（string・必須） |
| `ndf worker run` | `--kind`（string・必須）<br>`--prompt-file`（string・必須）<br>`--result-file`（string・必須）<br>`--cwd`（string・必須）<br>`--state-dir`（string・必須）<br>`--sources`（string・任意）<br>`--timeout`（number・任意）<br>`--result-dir-id`（string・任意）<br>`--require-output`（array of string・任意） |
| `ndf worker write` | `--path`（string・必須）: 書くファイル。本文は標準入力に渡し、既定では全文を置き換える<br>`--append`（boolean・任意）: 本文を末尾へ追記する。--op が必須で、--edit と同時に指定できない<br>`--edit`（boolean・任意）: 本文の [{"old": ..., "new": ...}] で一部を置き換える。--op が必須で、--append と同時に指定できない<br>`--op`（string・任意）: --append・--edit の操作ごとに新しくする識別子（同じ識別子で打ち直しても重ねて書かない） |

結果の `metrics`: `attempts`（integer）・`attention`（integer）・`edits`（integer）・`expectation_search`（object か null）・`paths`（object）・`report`（string）。`items` の要素: `action`（string）・`at`（—）・`backup`（string）・`diff`（string か null）・`kind`（string）・`path`（string）・`plan`（string か null）・`reason`（string か null）・`result_file`（string）・`session`（string か null）・`sha`（string）・`state`（string）・`subject`（string）・`text`（—）・`words`（array of string）

## `ndf worktree`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/worktree.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/worktree.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf worktree check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf worktree cleanup` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--dry-run`（boolean・任意）: 判断だけを出し、何も消さない |
| `ndf worktree create` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`branch`（string・必須・位置）<br>`--from`（string・任意）: worktree を切る起点（既定はブランチ戦略の設定のスプリントの起点）<br>`--state-dir`（string・任意）: スプリントの状態の保存先。渡すとブランチ戦略の設定の全体をスナップショットとして書く（スプリントの開始） |
| `ndf worktree deps` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`worktree`（string・必須・位置）<br>`--if-unprepared`（boolean・任意）: 今の HEAD と設定で用意したマーカーがあれば何もしない |
| `ndf worktree guard` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`paths`（array of string・任意・位置）<br>`--base`（string・任意）: 相対パスの起点（既定は --cwd）<br>`--unread`（boolean・任意）: 書き込み先を読み切れなかった（構文を解けないコマンド） |
| `ndf worktree init` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--force`（boolean・任意）: 既にある設定を書き直す |
| `ndf worktree list` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf worktree localenv` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`action`（string・必須・位置・値 `setup`・`verify`・`healthcheck`・`aim`・`mode`）<br>`worktree`（string・任意・位置） |
| `ndf worktree merged` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`prs`（array of integer・必須・位置）<br>`--snapshot`（string・任意）: スプリントの開始時のブランチ戦略のスナップショット（create --state-dir が書いたファイル） |
| `ndf worktree session` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf worktree testenv` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`action`（string・必須・位置・値 `env`・`tag`・`bake`・`up`・`stop`・`down`・`test`・`compose-env`・`reap`・`reclaim`・`expose`・`unexpose`）<br>`worktree`（string・任意・位置）<br>`--profile`（string・任意）<br>`--tag`（string・任意）<br>`--kind`（string・任意）<br>`--out`（string・任意）<br>`--idle`（string・任意）<br>`--volumes`（boolean・任意）<br>`--wait-service`（array of string・任意） |
| `ndf worktree trash` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--ref`（string・必須）: 本番に出たコミット（タグ・本番チャネル）<br>`--yes`（boolean・任意）: --only の退避先を消す。人が候補を見て承認したときだけ付ける<br>`--only`（array of string・任意） |

結果の `metrics`: `branch`（string か null）・`branch:deleted`（integer）・`branch:kept`（integer）・`branches_deleted`（integer）・`candidates`（integer）・`count`（integer）・`created`（integer）・`dirty`（integer）・`environment`（string）・`existing`（integer）・`exit`（integer）・`followed`（boolean）・`ports`（object）・`present`（boolean）・`removed`（integer）・`result`（string）・`seconds`（number）・`skipped`（boolean）・`slot`（integer）・`steps`（integer）・`tag`（string）・`url`（string）・`values`（integer）・`worktree`（string）・`worktree:kept`（integer）・`worktree:removed`（integer）・`worktrees`（integer）。`items` の要素: `base`（string）・`branch`（string か null）・`branch_deleted`（boolean）・`context`（string）・`detail`（string）・`environment`（string）・`head`（string）・`key`（string）・`kind`（string）・`line`（string）・`local_ignored`（array of string）・`locked`（boolean）・`name`（string）・`ndf_settings`（string）・`new_branch`（boolean）・`note`（string）・`path`（string）・`ports`（object）・`production`（string）・`prunable`（boolean）・`reason`（string）・`released`（boolean）・`removed`（boolean）・`restore`（string）・`result`（string）・`sha`（string）・`slot`（integer）・`snapshot`（object）・`sprint_start`（string）・`start_ref`（string）・`value`（string）・`verdict`（string）・`worktree`（string）・`written`（boolean）
