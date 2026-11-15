# 副命令の引数と結果

<!-- ndf check --write-spec が生成する。手で直さない（正本は各節の出典） -->

`ndf <副命令>` の引数と結果である。どの副命令も結果 JSON（`contracts/schemas/result.schema.json`）を標準出力の最後の 1 行に出す。正本は副命令の表（`src/ndf/cli.py` の `COMMANDS`）・各副命令の引数の定義・手書きの引数の表（`contracts/domains/manual-args.json`）・結果のスキーマ。終了コードと止まった理由は [終了コードと止まった理由](exit-codes.md) にある

## `ndf answer`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/answer.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/answer.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf answer` | `table_id`（string・必須・位置）: 答える進行表の ID<br>`--gate`（string・必須）: 問いの id（承認ゲートの結果の metrics.gate.id）<br>`--result`（string・必須）: 答えの語（承認ゲートの結果の routes のキー）<br>`--reason`（string・必須）<br>`--by`（string・必須）: 答えた者（human・conductor・organizer か service:<主体>）<br>`--key`（string・必須）: 冪等の鍵（同じ鍵の答え直しは書かない）<br>`--notes`（string・任意）: 差し戻し（revise）の指摘ファイル<br>`--head`（string・任意）: 見た PR の head の SHA<br>`--material-sha256`（string・任意）: 見た承認資料の sha256<br>`--drop`（array of integer・任意）: 設計承認ゲートで外す課題の番号 |

結果の `metrics`: `carried_row`（string か null）・`gate`（object か string）・`gate_id`（string）・`head`（string）・`move`（string）・`release_request`（object）・`replayed`（boolean）・`rev`（integer）・`seqs`（array of integer）・`stage`（string）・`state`（string か null）・`step`（string）・`table_id`（string）。`items` の要素: `answer`（string）・`at`（string）・`by`（string）・`gate`（string か null）・`gate_id`（string か null）・`head`（string か null）・`notes`（string か null）・`reason`（string か null）・`seq`（integer）・`stage`（string か null）・`step`（string か null）

## `ndf check`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/check.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/check.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf check` | `--base`（string・任意）: 差分の起点（I2）。無ければ差分を見ない<br>`--event`（string・任意・既定 `""`）: PR 本文を読む GitHub の事象の JSON。無ければ GITHUB_EVENT_PATH<br>`--only`（array of string・任意）: この規則の指摘だけを違反にする（繰り返せる）<br>`--write-spec`（boolean・任意）: 規約の生成物（契約のページと引数のスキーマ）を書いてから検査する |

結果の `metrics`: `files`（integer）・`findings`（object）・`written`（integer）。`items` の要素: `detail`（string）・`line`（integer）・`path`（string）・`rule`（string）

## `ndf ci`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/ci.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/ci.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf ci merge` | `--branch`（string・必須）: PR の head のブランチ<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば作業場所の origin<br>`--method`（string・任意）: マージの方式（squash・merge・rebase）。省けば基底のブランチで許された方式の先頭 |
| `ndf ci watch` | `--pr`（integer・必須）<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば作業場所の origin<br>`--required`（array of string・必須）: 待つ必須のチェック（PR の先端の宣言から渡す）<br>`--test`（array of string・任意）: テストのチェックの名前（分割ジョブも数える）<br>`--budget`（number・必須）: 待ちの合計の秒（ランナーの割り当ての待ちは数えない）<br>`--after-merge`（boolean・任意）: マージの段階が落ちた後の判定<br>`--merge-failed`（array of string・任意）: マージの段階が落ちたと読んだチェックの名前 |

結果の `metrics`: `elapsed`（number）・`failed_tests`（array of string）・`log_error`（string）・`queued_for`（number）・`rounds`（integer）・`test_files`（array of string）。`items` の要素: `job`（integer）・`kind`（string）・`name`（string）・`number`（integer）・`result`（string）・`test`（boolean）

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
| `ndf cross-review review-scope` | `worktree`（string・必須・位置）<br>`--purpose`（string・必須）<br>`--base`（string・必須）<br>`--state-dir`（string・必須）<br>`--pr`（integer・必須） |
| `ndf cross-review run` | `--pr`（integer・必須）<br>`--state-dir`（string・必須）<br>`--base`（string・必須）<br>`--worktree`（string・任意）<br>`--branch`（string・任意）<br>`--purpose`（string・任意・既定 `"cross-review"`）<br>`--reviewer`（array of string・任意）<br>`--max-rounds`（integer・任意・既定 `3`）<br>`--findings`（string・任意） |
| `ndf cross-review state-file` | `--state-dir`（string・必須）<br>`--pr`（integer・必須）<br>`--current`（integer・任意） |
| `ndf cross-review sync` | `worktree`（string・必須・位置）<br>`--branch`（string・必須） |
| `ndf cross-review target-repo` | `--repo`（string・任意）<br>`--state-dir`（string・任意）<br>`--pr`（integer・任意） |

結果の `metrics`: `findings`（integer）・`rounds`（integer）。`items` の要素: `after`（string）・`base`（string か null）・`before`（string）・`branch`（string）・`conflicts`（array of string）・`current_pr`（integer）・`head`（string か null）・`id`（string）・`kind`（string）・`locked`（string）・`number`（integer）・`ok`（boolean）・`origin_head`（string か null）・`origin_pr`（integer）・`outcome`（string）・`path`（string）・`points_to`（string）・`reason`（string か null）・`rebased`（boolean）・`replayed`（integer）・`scope`（string）・`slug`（string）・`source`（string）・`unreviewed`（array of string）

## `ndf gws`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/gws.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/gws.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf gws check` | `--install`（boolean・任意）: 導入の同意の印。gws が無ければ npm で入れる |
| `ndf gws thumbnails` | `presentation_id`（string・必須・位置）: プレゼンテーションの ID<br>`out_dir`（string・必須・位置）: 出力のディレクトリ（無ければ作る）<br>`--size`（string・任意・既定 `"LARGE"`・値 `SMALL`・`MEDIUM`・`LARGE`）: サムネイルの大きさ |

結果の `metrics`: `auth_method`（string）・`client_config_exists`（boolean）・`credential_source`（string）・`out_dir`（string）・`pages`（integer）・`size`（string）・`state`（string）。`items` の要素: `detail`（string）・`name`（string）・`objectId`（string）・`result`（string）

## `ndf hooks`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/hooks.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/hooks.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf hooks capture` | `runtime`（string・必須・位置）<br>`version`（string・必須・位置）<br>`event`（string・必須・位置） |
| `ndf hooks check-budget` | 引数を受けない |
| `ndf hooks render` | `--check`（boolean・任意）: 書かずに、作り直した結果と違えば stopped |
| `ndf hooks stats` | `--days`（number・任意・既定 `14.0`） |

結果の `metrics`: `days`（number）。`items` の要素: `events`（object）・`guards`（object）・`path`（string）

## `ndf issues`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/issues.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/issues.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf issues apply` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）<br>`--plan`（string・必須）<br>`--run-id`（string・任意） |
| `ndf issues candidates` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）<br>`--add`（string・任意）: 必ず候補に入れる番号（カンマ区切り。上限で切らない）<br>`--limit`（integer・任意・既定 `10`）<br>`--depth`（string・任意・既定 `"diff"`・値 `diff`・`review`）<br>`--since-ref`（string・任意）: 差分の起点（タグか SHA） |
| `ndf issues depth-limits` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）<br>`depth`（string・必須・位置・値 `review`・`overhaul`） |
| `ndf issues depth-record` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）<br>`--depth`（string・必須・値 `review`・`overhaul`）<br>`--event`（string・必須・値 `timed_out`・`ran`）<br>`--left`（string・任意） |
| `ndf issues due` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）<br>`depth`（string・必須・位置・値 `review`・`overhaul`）<br>`--manual`（boolean・任意）: 利用者が求めた |
| `ndf issues rank` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）<br>`--scores`（string・任意）: 見積の入力（{"issues": [...]}）<br>`--sizes`（string・任意）: 見積もりの記録から決めた Size（{"issues": [{"number", "size"}]}。size は尺度の値か "over"）<br>`--capacity`（integer・任意）<br>`--milestones`（string・任意）: 着手の順のマイルストーンの題（カンマ区切り）<br>`--approved`（string・任意）<br>`--depth`（string・任意・既定 `"diff"`・値 `diff`・`review`） |
| `ndf issues revert` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）<br>`--run`（string・必須） |
| `ndf issues settle` | `--repo`（string・任意）: <所有者>/<名前>（省けば origin）<br>`--state-dir`（string・任意）: 状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）<br>`--file`（string・必須）: 入力（{"settle": [...]}）<br>`--base`（string・任意）: 基底のブランチ（省けば既定のブランチ）<br>`--dry-run`（boolean・任意）: 確かめるだけで閉じない |

結果の `metrics`: `already`（array of integer）・`applied`（array of integer）・`at`（string）・`backward`（array of object）・`base`（string）・`before`（string か null）・`by_route`（object）・`candidates`（integer）・`carried`（array of integer）・`carry`（integer）・`closed`（array of integer）・`create_rejected`（array of object）・`created`（array of object）・`deferred`（array of integer）・`deferred_unscored`（array of integer）・`depth`（string）・`digest`（string）・`dropped`（array of object）・`event`（string）・`excluded`（array of object）・`failed`（array of object）・`forward`（array of object）・`held`（array of integer）・`left`（array of integer）・`limit`（integer）・`moved`（array of integer）・`needs_approval`（array of integer）・`next_steps`（array of string）・`notes`（array of string）・`open`（integer）・`order`（array of string）・`rejected_stale`（array of object）・`reparented`（array of integer）・`repo`（string）・`returned`（array of integer）・`reverted`（array of integer）・`seconds`（number）・`since_ref`（string か null）・`sized`（array of integer）・`skipped_changed`（array of integer）・`tables`（object）・`trigger`（array of string）・`unchanged`（array of integer）・`values`（object）。`items` の要素: `boundary`（object か null）・`child`（string）・`digest`（string）・`error`（string）・`evidence`（string）・`index`（integer）・`issue`（—）・`kind`（const "issue"）・`large_slot`（object か null）・`name`（string）・`number`（integer）・`order`（integer）・`over_scale`（array of integer）・`quality_slot`（object）・`reason`（string）・`result`（string）・`routes`（array of string）・`rows`（array of object）・`title`（string）・`updated_at`（string）・`verdict`（string）

## `ndf lanes`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/lanes.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/lanes.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf lanes answered` | `sprint_id`（string・必須・位置）<br>`--answer`（string・必須） |
| `ndf lanes init` | `plan_file`（string・必須・位置） |
| `ndf lanes lane add` | `plan`（string・必須・位置）<br>`lane_file`（string・必須・位置）<br>`--request`（string・任意） |
| `ndf lanes limits` | `plan`（string・必須・位置）<br>`--max-lanes`（integer・任意）<br>`--max-plans`（integer・任意）<br>`--reason`（string・必須） |
| `ndf lanes move` | `plan`（string・必須・位置）<br>`from_lane`（string・必須・位置）<br>`row`（string・必須・位置）<br>`to_lane`（string・必須・位置）<br>`--reason`（string・必須） |
| `ndf lanes question` | `plan`（string・必須・位置）<br>`--wrapper`（string・必須）: 問いを出したレーンのラッパーの名前（NDF_RELAY_NAME）<br>`--question`（string・必須）: hook が書いた問いのファイル（question.json） |
| `ndf lanes release` | `plan`（string・必須・位置）<br>`requests`（array of string・必須・位置）<br>`--result`（string・必須・値 `released`・`rejected`）<br>`--reason`（string・必須）<br>`--version`（string・任意）<br>`--release`（string・任意） |
| `ndf lanes request close` | `plan`（string・必須・位置）<br>`request`（string・必須・位置）<br>`--reason`（string・必須） |
| `ndf lanes row add` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`--issues`（array of integer・必須）<br>`--index`（integer・任意）: 並びの位置（0 が先頭）<br>`--args`（array of string・任意）<br>`--reason`（string・必須） |
| `ndf lanes row cancel` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`row`（string・必須・位置）: 行の鍵（r1 など）<br>`--reason`（string・必須） |
| `ndf lanes row carry` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`row`（string・必須・位置）: 行の鍵（r1 など）<br>`--issues`（array of integer・必須）<br>`--reason`（string・必須） |
| `ndf lanes row end` | `sprint_id`（string・必須・位置）<br>`--outcome`（string・必須・値 `completed`・`cancelled`） |
| `ndf lanes row move` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`row`（string・必須・位置）: 行の鍵（r1 など）<br>`--index`（integer・必須）: 並びの位置（0 が先頭）<br>`--reason`（string・必須） |
| `ndf lanes row resumed` | `sprint_id`（string・必須・位置） |
| `ndf lanes row start` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`row`（string・必須・位置）: 行の鍵（r1 など）<br>`--sprint`（string・必須）: スプリントの進行表の ID |
| `ndf lanes run` | `plan`（string・必須・位置） |
| `ndf lanes set` | `plan`（string・必須・位置）<br>`lane`（string・必須・位置）<br>`--plans`（integer・任意）<br>`--touches`（array of string・任意）<br>`--after`（array of string・任意）<br>`--request`（string・任意）<br>`--reason`（string・必須） |
| `ndf lanes start` | `plan_file`（string・必須・位置） |
| `ndf lanes status` | `plan`（string・必須・位置） |
| `ndf lanes stop` | `plan`（string・必須・位置）<br>`--now`（boolean・任意）: 区切りを待たずに中止する（未着手の行を中止にし、実行中のスプリントを中止させる）<br>`--reason`（string・必須） |
| `ndf lanes wait` | `plan`（string・必須・位置）<br>`--after`（integer・任意・既定 `0`）: 計画の進行表の事象の番号（前の wait の next が渡す）<br>`--known-stalls`（array of string・任意）: 知らせ済みの止まり（<レーン>/<行>） |

結果の `metrics`: `changes`（object）・`closed_requests`（array of object）・`complete`（boolean）・`current_id`（string）・`errors`（array of string）・`failed`（array of string）・`lane_table_id`（string）・`last_seq`（integer）・`launched`（array of string）・`max_lanes`（integer）・`max_plans`（integer）・`new_stalls`（array of string）・`next_row`（object か null）・`open_requests`（array of object）・`opened`（boolean）・`outcome`（string か null）・`plan`（string）・`plan_id`（string）・`releases`（object）・`request`（string か null）・`requests`（array of string）・`result`（string）・`row`（string）・`run_pid`（integer）・`running`（array of string）・`seq`（integer か null）・`sprint_id`（string）・`stalled`（integer）・`stalls`（array of string）・`state`（string）・`wakeable`（array of array of string）。`items` の要素: `actor`（string）・`after`（array of string）・`at`（string）・`kind`（string）・`lane`（string）・`liveness`（object）・`machine_wait`（object）・`next`（object）・`path`（string）・`payload`（object か null）・`reason`（string か null）・`rev`（integer か null）・`row_key`（string か null）・`rows`（array of object）・`seq`（integer）・`sprint_id`（string）・`state`（string か null）

## `ndf launch-args`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/launch-args.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/launch-args.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf launch-args` | 引数を受けない |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `args`（array of string）

## `ndf loop`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/loop.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/loop.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf loop acceptance check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--issue`（integer・必須） |
| `ndf loop acceptance record` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--pre-merge`（array of string・任意）: '<条件の番号>=<手段（テスト名・実行したコマンド）>'<br>`--post-release`（array of integer・任意）: リリースの後でしか確かめられない条件の番号<br>`--issue`（integer・必須） |
| `ndf loop acceptance show` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--issue`（integer・必須） |
| `ndf loop answer` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--pr`（integer・必須）<br>`--answer`（string・必須・値 `approved`・`revise`・`rejected`）<br>`--note`（string・任意・既定 `""`）: 差し戻しの理由など |
| `ndf loop close-plan` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--pace`（string・必須）<br>`--route`（array of string・必須・値 `promote`・`merged-by-check`・`manual`・`none`）<br>`--mvv`（boolean・任意）: MVV の判定がある<br>`--record-pr`（integer・任意）: リリース記録の PR の番号（0 は記録なし）<br>`--retrospective-due`（boolean・任意） |
| `ndf loop created` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`issues`（array of integer・必須・位置） |
| `ndf loop design-tests` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--issue`（array of integer・任意）: HEAD の issues/issue-<N>-design*.md を読む<br>`--design`（array of string・任意）: 作業場所の設計文書のパス |
| `ndf loop exclude` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--issue`（integer・必須）<br>`--reason`（string・必須） |
| `ndf loop gate` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--pr`（integer・必須） |
| `ndf loop get` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--field`（string・任意・値 `status`・`stop`・`holder`・`laps`・`current`・`rejections`・`created`） |
| `ndf loop lap` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--failed`（boolean・任意）: 手順の失敗の印<br>`--failure-key`（string・任意）<br>`--version`（array of string・任意）: この周で出した版（記録に無いもの）<br>`--returned`（boolean・任意）: リリースを承認されずに人へ戻した<br>`--unreadable`（string・任意）: 版の記録を読めなかった理由 |
| `ndf loop released` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--version`（string・必須） |
| `ndf loop review-args` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--purpose`（string・必須・値 `design`・`code`）: cross-review run の --purpose<br>`--pr`（integer・必須）<br>`--state-dir`（string・必須）<br>`--base`（string・必須） |
| `ndf loop select` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--candidates`（string・必須）: 候補の行の JSON の配列のファイル<br>`--first`（array of integer・任意）: 先頭に選ぶ課題（直して出し直すと答えた課題）<br>`--size`（integer・任意・既定 `1`）: 周に入れる課題の数 |
| `ndf loop sprint` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子<br>`--mode`（string・必須）<br>`--basis`（string・必須）: モードの判定の根拠 |
| `ndf loop start` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--session`（string・必須）: 打ったセッションの識別子 |
| `ndf loop stop` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--by`（string・必須・値 `human`・`organizer`）<br>`--reason`（string・任意・既定 `""`）<br>`--now`（boolean・任意）: 周の境を待たずにその場で止める |

結果の `metrics`: `answer`（string）・`designs`（integer）・`head`（string）・`lap`（integer）・`missing`（integer）・`names`（integer）・`outcome`（string）・`pace`（string）・`record_pr`（string）・`remaining`（array of integer）・`renamed`（integer）・`skipped`（integer）・`status`（string）・`streak`（integer）・`unverified`（integer）・`verify`（boolean）。`items` の要素: `chosen`（boolean）・`created`（array of integer）・`current`（object か null）・`doc`（string）・`holder`（string か null）・`issue`（integer）・`kind`（string）・`laps`（array of object）・`line`（integer）・`means`（string）・`name`（string）・`reason`（string か null）・`rejections`（array of object）・`renamed_to`（string）・`result`（string）・`review`（array of string）・`section`（string か null）・`status`（string）・`steps`（array of string）・`stop`（object か null）・`text`（string）

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
| `ndf plan next` | `plan`（string・必須・位置）<br>`--from`（string・任意）<br>`--state-dir`（string・任意）<br>`--answer`（string・任意）<br>`--set`（array of string・任意）<br>`--fresh`（boolean・任意） |
| `ndf plan pending` | 引数を受けない |
| `ndf plan queue` | `plans`（array of string・必須・位置）<br>`--then`（array of array of string・任意）<br>`--from`（array of string・任意）<br>`--resume`（boolean・任意）<br>`--fresh`（boolean・任意）<br>`--max`（integer・任意）: 同時に流すプランの本数の上限（無ければ触るファイルの重ならない組の本数まで）<br>`--queue-dir`（string・任意） |
| `ndf plan wait` | `plans`（array of string・必須・位置）<br>`--state-dir`（string・任意）<br>`--timeout`（number・任意・既定 `600.0`） |

結果の `metrics`: `gates`（integer）・`ok`（integer）・`pending`（integer）・`plans`（integer）・`round`（integer）・`steps`（integer）・`stopped`（integer）・`waited_seconds`（number）・`waves`（integer）。`items` の要素: `cause`（object）・`detail`（string）・`gate`（boolean）・`kind`（string）・`name`（string）・`number`（integer）・`path`（string）・`plans`（array of string）・`reason`（string）・`routes`（object）・`run`（string）・`status`（string か null）・`step`（string か null）・`updated_at`（string か null）・`workflow`（string）

## `ndf progress`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/progress.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/progress.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf progress cancel` | `table_id`（string・必須・位置）<br>`--reason`（string・必須） |
| `ndf progress disable` | `table_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--reason`（string・必須） |
| `ndf progress enable` | `table_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--reason`（string・必須） |
| `ndf progress finish` | `table_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--reason`（string・必須）<br>`--output`（string・必須）: 工程の出力の規約に合う JSON のファイル |
| `ndf progress new` | `name`（string・必須・位置）: スプリント名<br>`--mode`（string・必須）<br>`--issues`（array of integer・必須）<br>`--pace`（string・任意・既定 `"normal"`）<br>`--disable`（array of string・任意）: <工程>=<理由><br>`--requirements`（string・任意）: 要求と受け入れ条件の出力（工程の出力の規約に合う JSON）<br>`--parent`（string・任意）: 親の進行表（レーンの進行表）の ID |
| `ndf progress restore` | `--from`（string・必須） |
| `ndf progress retry` | `table_id`（string・必須・位置）<br>`stage`（string・必須・位置）: 工程の名前（工程表の行名）<br>`--reason`（string・必須） |
| `ndf progress show` | `table_id`（string・必須・位置） |

結果の `metrics`: `answer`（string）・`cancelled_rows`（integer）・`current_id`（string）・`disabled`（array of string）・`history`（array of object）・`mode`（string）・`move`（string）・`rev`（integer）・`rows`（integer）・`seq`（integer）・`seqs`（array of integer）・`stage`（string）・`state`（string か null）・`step`（string）・`table_id`（string）。`items` の要素: `at`（string か null）・`changed`（boolean）・`error`（string）・`from`（string）・`gates`（array of string）・`key`（string）・`kind`（string）・`label`（string）・`number`（integer）・`path`（string）・`phase`（string）・`reason`（string か null）・`restored`（string）・`retired`（string）・`source`（string）・`stage`（string）・`state`（string か null）

## `ndf project`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/project.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/project.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf project check` | `--force`（boolean・任意）: 新しくても古いと判定する（手動の再解析）<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所 |
| `ndf project drift` | `--measure`（string・必須）: ndf project measure の測定の結果<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所 |
| `ndf project measure` | `--out`（string・必須）: 測定の結果の置き場（リポジトリの外の一時ディレクトリ）<br>`--budget`（number・任意・既定 `120.0`）: 締め切りの秒（既定 120）<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所 |
| `ndf project schema` | `--out`（string・任意）: 書く先。省けば標準出力 |
| `ndf project write` | `--measure`（string・必須）: ndf project measure の測定の結果<br>`--answers`（string・任意）: 問いの項目への答え（キー → {value} か {unknown}）<br>`--dry-run`（boolean・任意）: 書かずに差分だけを出す<br>`--root`（string・任意・既定 `"."`）: リポジトリの作業場所 |

結果の `metrics`: `declared`（boolean）・`drifted`（integer）・`dry_run`（boolean）・`elapsed_seconds`（number）・`files`（array of string）・`inputs`（integer）・`measured`（integer）・`out`（string）・`question`（integer）・`unknown`（integer）・`unknown_items`（array of string）・`written`（integer）。`items` の要素: `analyzed`（—）・`confidence`（string か null）・`declared`（—）・`evidence`（array）・`kept_previous`（boolean）・`key`（string）・`kind`（string）・`measured`（—）・`name`（string）・`origin`（string）・`pattern`（string）・`proposal`（string）・`reason`（string）・`result`（string）・`source`（string）・`text`（string）

## `ndf records`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/records.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/records.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf records cleanup-stage` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`prs`（array of integer・必須・位置） |
| `ndf records close-check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`issue`（integer・必須・位置）<br>`--verification`（string・任意）: リリース後テストの記録（release-verification のスキーマの JSON）<br>`--require`（boolean・任意）: 記録が無ければ閉じない |
| `ndf records glossary meanings` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf records glossary render` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--check`（boolean・任意）: 書かずに、今と違うファイルを出す |
| `ndf records gws-check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf records handoff check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`path`（string・必須・位置）<br>`--trim`（boolean・任意）: 雛形に無い節を退避先へ移してから確かめる<br>`--history`（string・任意）: 退避先（--trim のとき要る） |
| `ndf records mvv answer` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--version`（integer・任意）: 答えたときの MVV の版（無ければ worktree の宣言の現行の版） |
| `ndf records mvv signals` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--base`（string・必須）: ベースブランチ |
| `ndf records preflight` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf records spec finalize` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--spec`（array of string・必須）: 溶かした先の確定仕様（複数可）<br>`--remove`（array of string・任意）: 消す設計（複数可）<br>`--source-of`（array of string・任意）: <語>=<ファイル> で語の正本を明示する<br>`--message（-m）`（string・任意）: コミットの件名。無ければコミットせずパスだけを出す |
| `ndf records usage report` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--since`（string・必須）<br>`--until`（string・必須） |
| `ndf records usage stop` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |

結果の `metrics`: `bytes`（integer）・`commit`（string）・`lines`（integer）・`revise`（boolean）。`items` の要素: `agent_id`（string）・`at`（string）・`broken_rows`（integer）・`caller`（string）・`cause`（object）・`closable`（boolean）・`closed_issues`（integer）・`context`（string）・`files`（array of string）・`gate`（—）・`issue`（integer）・`kind`（string）・`lines`（integer）・`missing_path`（string）・`moved`（string）・`name`（string）・`overrides`（integer）・`path`（string）・`pr`（—）・`reason`（string）・`repo`（string）・`result`（string）・`since`（string）・`state`（—）・`status`（string）・`summary`（string）・`term`（string）・`transcript`（string）・`unknown`（integer）・`unknown_pass`（integer）・`unknown_rows`（integer）・`until`（string）・`usage`（object か string）・`version`（integer か null）・`violation`（string）・`warnings`（array of string）・`word`（string）

## `ndf refactor`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/refactor.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/refactor.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf refactor collected` | `--paths`（array of string・必須）<br>`--timeout`（number・任意）<br>`--work`（string・任意・既定 `"."`） |
| `ndf refactor release` | `--pr`（integer・必須）<br>`--work`（string・任意・既定 `"."`） |
| `ndf refactor run` | `--pr`（integer・必須）<br>`--branch`（string・必須）<br>`--base`（string・必須）<br>`--scope`（array of string・必須）<br>`--required`（array of string・必須）<br>`--ci-test`（array of string・任意）<br>`--symbols`（array of string・任意）<br>`--participants`（array of string・任意）<br>`--max-fix`（integer・任意・既定 `2`）<br>`--budget-minutes`（integer・任意）<br>`--safety-net-minutes`（number・任意）<br>`--work`（string・任意・既定 `"."`） |
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
| `ndf relay account add-bedrock` | `--profile`（string・任意）: AWS のプロファイル<br>`--region`（string・任意）: 地域（省けばプロファイルの region）<br>`--model`（string・任意）: モデルの ID<br>`--yes`（boolean・任意）: 確かめが通れば保存する（前の宣言を置き換える） |
| `ndf relay account api-key-monthly` | `name`（string・必須・位置）: API キーの名前<br>`usd`（string・必須・位置）: 月の額（ドル。0 で外す） |
| `ndf relay account api-key-use` | `mode`（string・任意・位置・値 `off`・`always`・`near-limit`）: 使い方（省けば今の値を出す）<br>`--weekly-at`（string・任意）: 閾値（百分率。1〜100） |
| `ndf relay account capacity` | `name`（string・必須・位置）: アカウントの名前<br>`five`（string・必須・位置）: 5 時間の枠（USD か -）<br>`seven`（string・必須・位置）: 週の枠（USD か -） |
| `ndf relay account check` | `target`（string・必須・位置）: metered か API キーの名前 |
| `ndf relay account clear` | 引数を受けない |
| `ndf relay account list` | 引数を受けない |
| `ndf relay account remove` | `target`（string・必須・位置）: アカウントの名前・metered・API キーの名前 |
| `ndf relay account show` | 引数を受けない |
| `ndf relay account use` | `name`（string・必須・位置） |
| `ndf relay context calibrate` | `--days`（integer・任意・既定 `30`）: 較正に使う直近の日数 |
| `ndf relay context record` | `--verdict`（string・任意・値 `再起動`）: 再起動の行を書く（--reason と組む）<br>`--event`（string・任意・値 `引継ぎ開始`・`復元完了`）: 印の行を書く<br>`--reason`（string・任意・値 `容量`・`運用上限`・`費用`・`独立性`・`手動`・`カットポイント`・`待ち`）: 再起動の理由<br>`--work`（string・必須）: 作業の識別子（課題番号の並び） |
| `ndf relay context report` | `--work`（string・任意）: 集計する作業の識別子<br>`--all`（boolean・任意）: 記録にあるすべての作業を集計する<br>`--since`（string・任意）: この日（YYYY-MM-DD）以降の判定の行だけを読む<br>`--from`（string・任意）: この時刻（ISO 8601）からの呼び出しと行だけを数える<br>`--until`（string・任意）: この時刻（ISO 8601）までの呼び出しと行だけを数える |
| `ndf relay context resolve` | `--root`（string・任意）: 設定を読むリポジトリ（省けば今の作業場所の git の根）<br>`--policy`（string・任意・値 `current`・`trial`）: 方針を上書きする |
| `ndf relay decide` | 引数を受けない |
| `ndf relay install` | `--replace-old`（boolean・任意）: 旧い NDF の複製を置き換える（旧いラッパーが動いていれば断る） |
| `ndf relay ls` | 引数を受けない |
| `ndf relay mark` | `session_id`（string・必須・位置） |
| `ndf relay notice` | 引数を受けない |
| `ndf relay pending` | 引数を受けない |
| `ndf relay restart` | `name`（string・必須・位置） |
| `ndf relay run` | `--name`（string・任意）<br>`rest`（array of string・任意・位置）<br>`--prompt`（string・任意） |
| `ndf relay spawn` | `--name`（string・任意）<br>`rest`（array of string・任意・位置）<br>`--cwd`（string・任意）<br>`--role`（string・任意）<br>`--plan`（string・任意） |
| `ndf relay status` | 引数を受けない |
| `ndf relay stop` | `name`（string・任意・位置）: 止めるラッパーの名前<br>`--all`（boolean・任意）: 動いている名前つきのラッパーをすべて止める |
| `ndf relay uninstall` | 引数を受けない |

結果の `metrics`: `accounts`（integer）・`inside`（boolean）・`keys`（integer）・`limit`（integer）・`no_policy_rows`（integer）・`policy`（string）・`samples`（integer）・`skipped`（integer）・`stopped`（integer）・`works`（integer）。`items` の要素: `account`（string か null）・`args`（array か null）・`at`（string）・`auto_compacts`（integer か string）・`band`（string か null）・`basis`（string か null）・`capacity_declared`（object）・`config_error`（string か null）・`context`（integer か null）・`cwd`（string か null）・`decide_from`（integer）・`details`（object）・`effective_limit`（integer か null）・`email`（string か null）・`estimate`（object か null）・`event`（string）・`expires_at`（string）・`handoff_calls`（number か null）・`handoff_output`（number か null）・`keys`（array）・`kind`（string）・`limit`（integer）・`margin`（integer）・`missing`（array）・`model`（string か null）・`monthly_usd`（number か null）・`name`（string）・`no_policy_rows`（integer）・`ok`（boolean）・`org_name`（string か null）・`parts`（object）・`passed_once`（boolean）・`path`（string）・`pending`（array か null）・`percent`（number か null）・`pinned`（boolean）・`plan`（string か null）・`policy`（string）・`position`（string）・`prompt`（string）・`provider`（string）・`quality`（object）・`reason`（string か null）・`relay`（boolean）・`replaced`（boolean）・`restart_count`（integer）・`restarts`（object）・`restore_calls`（number か null）・`restore_context`（number か null）・`restores`（array）・`role`（string か null）・`run`（string）・`running`（boolean）・`runtime`（string）・`samples`（object）・`schema`（integer）・`session_id`（string か null）・`sessions`（integer）・`source`（string）・`sources`（object）・`stage`（string か null）・`stage_calls`（object）・`stage_expiries`（object）・`starts`（object）・`state`（string か null）・`status`（string か null）・`summary`（string）・`text`（string）・`transcript`（string か null）・`unknown_parts`（object）・`updated_at`（string か null）・`url`（string）・`use`（string）・`verdict`（string か null）・`version`（string）・`weekly_at`（number）・`window`（integer か null）・`work`（string）・`workflow`（string）

## `ndf release-steps`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/release-steps.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/release-steps.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf release-steps answer` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--route`（string・任意・既定 `"gate"`・値 `gate`・`github-merge`）: 承認の経路（承認ゲート gate / 人の GitHub でのマージ github-merge。github-merge は答えた者と対象をマージから読む）<br>`--answer`（string・任意・値 `approved`・`rejected`）: 答え（gate で要る）<br>`--presented`（string・任意）: 提示した先端の SHA（present の値の項目 head。gate で要る）<br>`--by`（string・任意・値 `user`・`mvv`）: 答えた担い手（利用者 user / MVV 判定 mvv。迂回は user だけを数える。gate で要る）<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch） |
| `ndf release-steps approval-check` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch） |
| `ndf release-steps assemble` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch）<br>`--plugin`（string・任意）: 予定の版を出すプラグイン（省けば登録の順の先頭） |
| `ndf release-steps bump` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch）<br>`--budget`（number・必須）: 必須のチェックを待つ秒 |
| `ndf release-steps cleanup` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--branch`（array of string・任意）: worktree を外すブランチ（省けば --pr の head のブランチ）<br>`--pr`（integer・任意）: head のブランチの worktree を外す PR |
| `ndf release-steps config-check` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--before`（string・必須） |
| `ndf release-steps config-snapshot` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--out`（string・必須） |
| `ndf release-steps confirm` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch）<br>`--budget`（number・必須）: 必須のチェックを待つ秒 |
| `ndf release-steps guard` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--branch`（array of string・必須）<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch） |
| `ndf release-steps materials` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch）<br>`--plugin`（string・任意）: 予定の版を出すプラグイン（省けば登録の順の先頭）<br>`--out`（string・任意）: 承認資料を書くファイル（省けば標準出力） |
| `ndf release-steps merge` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch）<br>`--budget`（number・必須）: 必須のチェックを待つ秒 |
| `ndf release-steps pr-check` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須） |
| `ndf release-steps present` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--material`（string・任意）: 承認資料のファイル（省けば PR の本文） |
| `ndf release-steps publish` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--revision`（string・必須） |
| `ndf release-steps record` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・任意）: 本番ブランチ（省けば宣言 worktree.json の production_branch） |
| `ndf release-steps records` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須） |
| `ndf release-steps sync-base` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--main-dir`（string・必須）<br>`--base`（string・必須） |
| `ndf release-steps verification-record` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--record`（string・必須）: リリース後テストの記録の JSON（スキーマ release-verification） |
| `ndf release-steps verify-ref` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `change`（string）・`checks`（integer）・`checks_passed`（integer）・`checks_skipped`（integer）・`detail`（string）・`failed`（array of string）・`kind`（string）・`name`（string）・`number`（integer）・`outcome`（string）・`path`（string）・`reason`（string）・`result`（string）・`value`（—）

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
| `ndf test-slot run` | `--wait-limit`（integer・任意）: 枠とメモリを待つ上限の秒（既定は設定の値）<br>`command`（array of string・任意・位置）: -- の後ろにシェルのコマンド 1 つ |

結果の `metrics`: `available_mib`（integer か null）・`gave_up`（integer）・`memory_stopped`（integer）・`oom_kill_increase`（integer）・`runs`（integer）・`waited_runs`（integer）・`waited_seconds`（number）。`items` の要素: `detail`（string）・`retired_from`（string）・`retired_to`（string）

## `ndf training`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/training.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/training.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf training check` | `--runtime`（array of string・任意・値 `claude`・`codex`）: 確かめる担当（既定は確かめる口のある担当すべて） |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `checked_at`（string）・`reason`（string）・`runtime`（string）・`training`（boolean か null）

## `ndf worker`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/worker.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/worker.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf worker attention` | `progress`（string・必須・位置） |
| `ndf worker closing-check` | `--base`（string・必須）<br>`--cwd`（string・任意・既定 `"."`） |
| `ndf worker plan-edit` | `plan`（string・必須・位置）<br>`--new`（string・必須）<br>`--reason`（string・必須）<br>`--state-dir`（string・任意） |
| `ndf worker plan-edits` | `plans`（array of string・必須・位置）<br>`--state-dir`（string・任意） |
| `ndf worker run` | `--kind`（string・必須）<br>`--prompt-file`（string・必須）<br>`--result-file`（string・必須）<br>`--cwd`（string・必須）<br>`--state-dir`（string・必須）<br>`--sources`（string・任意）<br>`--timeout`（number・任意・既定 `3600.0`） |

結果の `metrics`: `attempts`（integer）・`attention`（integer）・`edits`（integer）・`expectation_search`（object か null）。`items` の要素: `at`（—）・`backup`（string）・`diff`（string か null）・`kind`（string）・`plan`（string か null）・`reason`（string か null）・`result_file`（string）・`session`（string か null）・`sha`（string）・`state`（string）・`subject`（string）・`text`（—）・`words`（array of string）

## `ndf worktree`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/worktree.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/worktree.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf worktree check` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf worktree cleanup` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--dry-run`（boolean・任意）: 判断だけを出し、何も消さない |
| `ndf worktree create` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`branch`（string・必須・位置）<br>`--from`（string・任意）: ベースブランチ（既定はスプリントの起点） |
| `ndf worktree deps` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`worktree`（string・必須・位置）<br>`--if-unprepared`（boolean・任意）: 今の HEAD と宣言で用意した印があれば何もしない |
| `ndf worktree guard` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`paths`（array of string・任意・位置）<br>`--base`（string・任意）: 相対パスの起点（既定は --cwd）<br>`--unread`（boolean・任意）: 書き込み先を読み切れなかった（構文を解けないコマンド） |
| `ndf worktree init` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--force`（boolean・任意）: 既にある宣言を書き直す |
| `ndf worktree list` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf worktree localenv` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`action`（string・必須・位置・値 `setup`・`verify`・`healthcheck`・`aim`・`mode`）<br>`worktree`（string・任意・位置） |
| `ndf worktree merged` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`prs`（array of integer・必須・位置）<br>`--flow-record`（string・任意）: スプリントの開始時の記録（branch_flow の写しと production_branch） |
| `ndf worktree session` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ） |
| `ndf worktree testenv` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`action`（string・必須・位置・値 `env`・`tag`・`bake`・`up`・`stop`・`down`・`test`・`compose-env`・`reap`・`reclaim`・`expose`・`unexpose`）<br>`worktree`（string・任意・位置）<br>`--profile`（string・任意）<br>`--tag`（string・任意）<br>`--kind`（string・任意）<br>`--out`（string・任意）<br>`--idle`（string・任意）<br>`--volumes`（boolean・任意）<br>`--wait-service`（array of string・任意） |
| `ndf worktree trash` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--ref`（string・必須）: 本番に出たコミット（タグ・本番チャネル）<br>`--yes`（boolean・任意）: --only の退避先を消す。人が候補を見て承認したときだけ付ける<br>`--only`（array of string・任意） |

結果の `metrics`: `branch`（string か null）・`branch:deleted`（integer）・`branch:kept`（integer）・`branches_deleted`（integer）・`candidates`（integer）・`count`（integer）・`created`（integer）・`dirty`（integer）・`environment`（string）・`existing`（integer）・`exit`（integer）・`followed`（boolean）・`ports`（object）・`present`（boolean）・`removed`（integer）・`result`（string）・`seconds`（number）・`skipped`（boolean）・`slot`（integer）・`steps`（integer）・`tag`（string）・`url`（string）・`values`（integer）・`worktree`（string）・`worktree:kept`（integer）・`worktree:removed`（integer）・`worktrees`（integer）。`items` の要素: `base`（string）・`base_branch`（string か null）・`branch`（string か null）・`branch_deleted`（boolean）・`context`（string）・`declaration`（string）・`detail`（string）・`environment`（string）・`head`（string）・`key`（string）・`kind`（string）・`line`（string）・`local_ignored`（array of string）・`locked`（boolean）・`name`（string）・`new_branch`（boolean）・`note`（string）・`path`（string）・`ports`（object）・`production_branch`（string か null）・`prunable`（boolean）・`reason`（string）・`released`（boolean）・`removed`（boolean）・`restore`（string）・`result`（string）・`sha`（string）・`slot`（integer）・`start_ref`（string）・`value`（string）・`verdict`（string）・`worktree`（string）・`written`（boolean）
