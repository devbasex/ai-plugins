# 副命令の引数と結果

<!-- ndf check --write-spec が生成する。手で直さない（正本は各節の出典） -->

`ndf <副命令>` の引数と結果である。どの副命令も結果 JSON（`contracts/schemas/result.schema.json`）を標準出力の最後の 1 行に出す。正本は副命令の表（`src/ndf/cli.py` の `COMMANDS`）・各副命令の引数の定義・手書きの引数の表（`contracts/domains/manual-args.json`）・結果のスキーマ。終了コードと止まった理由は [終了コードと止まった理由](exit-codes.md) にある

## `ndf answer`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/answer.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/answer.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf answer` | `table_id`（string・必須・位置）: 答える進行表の ID<br>`--gate`（string・必須）: 問いの id（承認ゲートの結果の metrics.gate.id）<br>`--result`（string・必須）: 答えの語（承認ゲートの結果の routes のキー）<br>`--reason`（string・必須）<br>`--by`（string・必須）: 答えた者（human・conductor・organizer か service:<主体>）<br>`--key`（string・必須）: 冪等の鍵（同じ鍵の答え直しは書かない）<br>`--notes`（string・任意）: 差し戻し（revise）の指摘ファイル<br>`--head`（string・任意）: 見た PR の head の SHA<br>`--material-sha256`（string・任意）: 見た承認資料の sha256<br>`--drop`（array of integer・任意）: 設計承認ゲートで外す課題の番号 |

結果の `metrics`: `gate`（—）・`head`（—）・`table_id`（—）。`items` の要素: 決まった項目を持たない

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

結果の `metrics`: 決まった項目を持たない。`items` の要素: `kind`（string か null）・`number`（—）

## `ndf contracts`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/contracts.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/contracts.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf contracts check` | 引数を受けない |
| `ndf contracts emit` | `--tool`（string・必須）<br>`--status`（string・必須・値 `ok`・`gate`・`stopped`）<br>`--summary`（string・必須）<br>`--cause`（array of string・任意）<br>`--cause-instance`（string・任意）<br>`--retry-after`（number・任意）<br>`--route`（array of string・任意）: CMD は {"argv": ["ndf", ...]} の JSON。- なら何も打たない<br>`--gap`（array of string・任意）<br>`--item`（array of string・任意）: items の 1 要素（JSON のオブジェクト）<br>`--metrics`（string・任意）: metrics（JSON のオブジェクト）<br>`--next`（string・任意）: 次に打つコマンド（{"argv": ["ndf", ...], "ask": [...]} の JSON）<br>`--outputs`（string・任意）: 工程どうしで渡す値（JSON のオブジェクト）<br>`--run-id`（string・任意）<br>`--presentation-path`（string・任意）<br>`--code`（integer・任意）: 終了コード（省けば status と cause.kind から決める）<br>`--out`（string・任意）: 結果を 1 行で書くファイル（形が合うときだけ書く） |
| `ndf contracts fact-ids` | `table`（string・必須・位置）<br>`where`（array of string・任意・位置） |
| `ndf contracts fact-value` | `table`（string・必須・位置）<br>`id`（string・必須・位置）<br>`column`（string・必須・位置） |

結果の `metrics`: `fact_tables`（integer か null）。`items` の要素: 決まった項目を持たない

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

結果の `metrics`: 決まった項目を持たない。`items` の要素: `kind`（string か null）・`path`（—）・`points_to`（string か null）

## `ndf hooks`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/hooks.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/hooks.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf hooks capture` | `runtime`（string・必須・位置）<br>`version`（string・必須・位置）<br>`event`（string・必須・位置） |
| `ndf hooks check-budget` | 引数を受けない |
| `ndf hooks render` | `--check`（boolean・任意）: 書かずに、作り直した結果と違えば stopped |
| `ndf hooks stats` | `--days`（number・任意・既定 `14.0`） |

結果の `metrics`: `days`（—）。`items` の要素: `path`（string か null）

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

結果の `metrics`: `depth`（—）・`seconds`（—）。`items` の要素: 決まった項目を持たない

## `ndf lanes`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/lanes.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/lanes.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf lanes answered` | `sprint_id`（string・必須・位置）<br>`--answer`（string・必須） |
| `ndf lanes init` | `plan_file`（string・必須・位置） |
| `ndf lanes lane add` | `plan`（string・必須・位置）<br>`lane_file`（string・必須・位置）<br>`--request`（string・任意） |
| `ndf lanes limits` | `plan`（string・必須・位置）<br>`--max-lanes`（integer・任意）<br>`--max-plans`（integer・任意）<br>`--reason`（string・必須） |
| `ndf lanes move` | `plan`（string・必須・位置）<br>`from_lane`（string・必須・位置）<br>`row`（string・必須・位置）<br>`to_lane`（string・必須・位置）<br>`--reason`（string・必須） |
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

結果の `metrics`: 決まった項目を持たない。`items` の要素: 決まった項目を持たない

## `ndf launch-args`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/launch-args.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/launch-args.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf launch-args` | 引数を受けない |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `args`（—）

## `ndf loop`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/loop.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/loop.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf loop answer` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`--pr`（integer・必須）<br>`--answer`（string・必須・値 `approved`・`revise`・`rejected`）<br>`--note`（string・任意・既定 `""`）: 差し戻しの理由など |
| `ndf loop close-plan` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`--pace`（string・必須）<br>`--route`（array of string・必須・値 `promote`・`merged-by-check`・`manual`・`none`）<br>`--mvv`（boolean・任意）: MVV の判定がある<br>`--record-pr`（integer・任意）: リリース記録の PR の番号（0 は記録なし）<br>`--retrospective-due`（boolean・任意） |
| `ndf loop created` | `--cwd`（string・任意）: 対象のリポジトリの中の位置（既定はカレントディレクトリ）<br>`name`（string・必須・位置）<br>`issues`（array of integer・必須・位置） |
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

結果の `metrics`: `answer`（—）・`head`（—）・`lap`（—）・`outcome`（—）・`remaining`（array か null）・`streak`（—）。`items` の要素: 決まった項目を持たない

## `ndf manifests`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/manifests.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/manifests.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf manifests render` | `--check`（boolean・任意）: 書かずに、作り直した結果と違えば stopped |

結果の `metrics`: 決まった項目を持たない。`items` の要素: 決まった項目を持たない

## `ndf notify`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/notify.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/notify.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf notify send` | 引数を受けない |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `args`（—）

## `ndf plan`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/plan.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/plan.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf plan check` | `plans`（array of string・必須・位置） |
| `ndf plan next` | `plan`（string・必須・位置）<br>`--from`（string・任意）<br>`--state-dir`（string・任意）<br>`--answer`（string・任意）<br>`--set`（array of string・任意）<br>`--fresh`（boolean・任意） |
| `ndf plan pending` | 引数を受けない |
| `ndf plan queue` | `plans`（array of string・必須・位置）<br>`--then`（array of string・任意）<br>`--from`（array of string・任意）<br>`--resume`（boolean・任意）<br>`--fresh`（boolean・任意）<br>`--queue-dir`（string・任意） |
| `ndf plan wait` | `plans`（array of string・必須・位置）<br>`--state-dir`（string・任意）<br>`--timeout`（number・任意・既定 `600.0`） |

結果の `metrics`: `waited_seconds`（number か null）。`items` の要素: 決まった項目を持たない

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

結果の `metrics`: `gate`（—）・`head`（—）・`table_id`（—）。`items` の要素: 決まった項目を持たない

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

結果の `metrics`: 決まった項目を持たない。`items` の要素: 決まった項目を持たない

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

結果の `metrics`: 決まった項目を持たない。`items` の要素: 決まった項目を持たない

## `ndf relay`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/relay.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/relay.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf relay account clear` | 引数を受けない |
| `ndf relay account show` | 引数を受けない |
| `ndf relay account use` | `name`（string・必須・位置） |
| `ndf relay decide` | 引数を受けない |
| `ndf relay install` | `--replace-old`（boolean・任意）: 旧い NDF の複製を置き換える（旧いラッパーが動いていれば断る） |
| `ndf relay ls` | 引数を受けない |
| `ndf relay mark` | `session_id`（string・必須・位置） |
| `ndf relay pending` | 引数を受けない |
| `ndf relay restart` | `name`（string・必須・位置） |
| `ndf relay run` | `--name`（string・任意）<br>`rest`（string・必須・位置）<br>`--prompt`（string・任意） |
| `ndf relay spawn` | `--name`（string・任意）<br>`rest`（string・必須・位置）<br>`--cwd`（string・任意）<br>`--role`（string・任意）<br>`--plan`（string・任意） |
| `ndf relay status` | 引数を受けない |
| `ndf relay stop` | `name`（string・必須・位置） |
| `ndf relay uninstall` | 引数を受けない |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `account`（—）・`path`（string か null）

## `ndf release-steps`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/release-steps.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/release-steps.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf release-steps answer` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--answer`（string・必須・値 `approved`・`rejected`）<br>`--presented`（string・必須）: 提示した先端の SHA（present の outputs.head）<br>`--by`（string・必須・値 `user`・`mvv`）: 答えた担い手（利用者 user / MVV 判定 mvv。迂回は user だけを数える） |
| `ndf release-steps approval-check` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・必須） |
| `ndf release-steps assemble` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--base`（string・必須）: ベースブランチ（宣言 worktree.json の base_branch）<br>`--production`（string・必須）: 本番ブランチ（宣言 worktree.json の production_branch）<br>`--channel`（string・必須・値 `production`・`dev`）<br>`--level`（string・任意・既定 `"patch"`・値 `major`・`minor`・`patch`）<br>`--plugin`（string・任意）: ブランチの名前に版を使うプラグイン（省けば登録の順の先頭） |
| `ndf release-steps cleanup` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--branch`（array of string・必須） |
| `ndf release-steps config-check` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--before`（string・必須） |
| `ndf release-steps config-snapshot` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--out`（string・必須） |
| `ndf release-steps materials` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--base`（string・必須）: ベースブランチ（宣言 worktree.json の base_branch）<br>`--production`（string・必須）: 本番ブランチ（宣言 worktree.json の production_branch）<br>`--channel`（string・必須・値 `production`・`dev`）<br>`--level`（string・任意・既定 `"patch"`・値 `major`・`minor`・`patch`）<br>`--plugin`（string・任意）: ブランチの名前に版を使うプラグイン（省けば登録の順の先頭）<br>`--out`（string・任意）: 承認資料を書くファイル（省けば標準出力） |
| `ndf release-steps merge` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--base`（string・必須）: ベースブランチ（宣言 worktree.json の base_branch）<br>`--production`（string・必須）: 本番ブランチ（宣言 worktree.json の production_branch）<br>`--budget`（number・必須）: 必須のチェックを待つ秒 |
| `ndf release-steps present` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--material`（string・任意）: 承認資料のファイル（省けば PR の本文） |
| `ndf release-steps publish` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--revision`（string・必須） |
| `ndf release-steps record` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--production`（string・必須） |
| `ndf release-steps records` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須） |
| `ndf release-steps sync-base` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--main-dir`（string・必須）<br>`--base`（string・必須） |
| `ndf release-steps verification-record` | `--root`（string・任意・既定 `"."`）: リポジトリの根<br>`--repo`（string・任意）: <所有者>/<リポジトリ>。省けば origin<br>`--pr`（integer・必須）<br>`--record`（string・必須）: リリース後テストの記録の JSON（スキーマ release-verification） |

結果の `metrics`: 決まった項目を持たない。`items` の要素: 決まった項目を持たない

## `ndf route`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/route.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/route.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf route render` | `purpose`（string・必須・位置）<br>`--out`（string・必須）<br>`--set`（array of string・任意）<br>`--add`（array of string・任意） |
| `ndf route repeat` | `--result`（string・必須）<br>`--log`（string・必須） |
| `ndf route run` | `purpose`（string・必須・位置）<br>`--out`（string・必須）<br>`--set`（array of string・任意）<br>`--add`（array of string・任意） |
| `ndf route templates` | 引数を受けない |
| `ndf route test` | `--result`（string・任意）<br>`--failed-from`（string・任意）<br>`command`（string・任意・位置） |

結果の `metrics`: `failed_tests`（—）。`items` の要素: 決まった項目を持たない

## `ndf test-slot`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/test-slot.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/test-slot.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf test-slot report` | `--since`（string・任意・既定 `"7d"`）: 期間（既定 7d） |
| `ndf test-slot run` | `--wait-limit`（integer・任意）: 枠とメモリを待つ上限の秒（既定は設定の値）<br>`command`（string・必須・位置）: -- の後ろにシェルのコマンド 1 つ |

結果の `metrics`: `available_mib`（—）・`waited_seconds`（number か null）。`items` の要素: 決まった項目を持たない

## `ndf training`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/training.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/training.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf training check` | `--runtime`（array of string・任意・値 `claude`・`codex`）: 確かめる担当（既定は確かめる口のある担当すべて） |

結果の `metrics`: 決まった項目を持たない。`items` の要素: `args`（—）

## `ndf worker`

内部（予告なく変えてよい）。[引数のスキーマ](../../contracts/schemas/io/worker.args.schema.json)・[結果のスキーマ](../../contracts/schemas/io/worker.result.schema.json)

| 呼び方 | 引数 |
| --- | --- |
| `ndf worker attention` | `progress`（string・必須・位置） |
| `ndf worker closing-check` | `--base`（string・必須）<br>`--cwd`（string・任意・既定 `"."`） |
| `ndf worker plan-edit` | `plan`（string・必須・位置）<br>`--new`（string・必須）<br>`--reason`（string・必須）<br>`--state-dir`（string・任意） |
| `ndf worker plan-edits` | `plans`（array of string・必須・位置）<br>`--state-dir`（string・任意） |
| `ndf worker run` | `--kind`（string・必須）<br>`--prompt-file`（string・必須）<br>`--result-file`（string・必須）<br>`--cwd`（string・必須）<br>`--state-dir`（string・必須）<br>`--sources`（string・任意）<br>`--timeout`（number・任意・既定 `3600.0`） |

結果の `metrics`: `failed_tests`（—）・`waited_seconds`（number か null）。`items` の要素: 決まった項目を持たない

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

結果の `metrics`: `created`（—）・`existing`（—）・`exit`（—）・`tag`（—）・`url`（—）。`items` の要素: 決まった項目を持たない
