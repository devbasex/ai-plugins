# NDF の作り直し: 停止理由の種類を結果 JSON で返し、ルートをスクリプトが選ぶ

NDF の作り直し（#1142）のうち「分岐と例外のルートをスクリプトが決め、AI は補助として呼ばれるだけにする」を持つ確定仕様である。
手順のスクリプトは止まった理由を閉じた語彙の種類（停止理由）で結果 JSON に返し、プランの実行とステートマシンは停止理由の表から
ルート（打ち直す・直すステップへ・conductor へ・人へ）を機械で選ぶ。AI は、スクリプトが決めた時点で選択肢か JSON のスキーマに
縛った形で呼ばれる。決定の番号は作り直しの 6 本の確定仕様を通して一意で、索引と番号の対応表は [ndf-rebuild.md](ndf-rebuild.md) にある。

## 概要

**例: 実装のプランで、PR の CI のテストが落ちる。** 2026-11-12、10 系では CI の監視が分割ジョブの失敗を「CI 以外の理由」と
読み違え、AI がその文を読んでレーン lrel の手でやり直しを組んだ（#3153）。11 系の実装のプラン（テンプレート `impl`）では次のように進む。

| 順 | 起きること |
| ---: | --- |
| 1 | ステップ `ci` の `ndf ci watch` が結果 JSON を `status: stopped`・`cause: {"kind": "test_failed", "detail": …, "instance": <チェックの名前>}` で出し、終了コードは種類の終了コード 1 になる |
| 2 | 遷移の判断がステップの `on_cause` `{"test_failed": "repeat-check", "base_conflict": "base-sync"}` を引き、`repeat-check`（`ndf route repeat`。同じ原因の続いた回数を数える）へ進む。`summary` の文は読まない |
| 3 | 選んだルートを選択の記録 `{"event": "route_chosen", "rule": "R03", "chooser": "script", "from": "ci", "to": "repeat-check", "cause": "test_failed", …}` として状態の保存先の `progress.jsonl` へ 1 行書く |
| 4 | `repeat-check` の後に worker の `ci-fix`、落ちたテストだけを手元で打つ `ci-local`、`push-fixed` を通り、`ci` へ戻る |
| 5 | Runner が付かず `ndf ci watch` が `infra_wait` で止まったら、`on_cause` に無いので表の既定のルート `retry` を引き、上限の設定 `cause.infra-wait`（3 回・600 秒から倍）の待ちの後に同じステップを打ち直す。打ち直しはステップの数に数えない |
| 6 | 3 回を使い切ると表の `exhausted`（`step`）へ進む。`ci` は `on_fail` を持たないので conductor へ戻り、プランの結果は `infra_wait` の `stopped` と、止まったステップから続ける `next`（`ndf plan next <プラン> --from ci`）を持つ |
| 7 | 同じ原因が上限（`supervise.step-rerun` の `same_cause` 2 回）を超えて続けば、`ndf route repeat` が `fix_limit` で止まり、表の既定のルート `human` で人へ戻る |

### 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| 停止理由の表（`plugins/ndf/contracts/causes/`） | 種類ごとの意味・標準の誤りの型・終了コード（終了コードの表の id）・既定のルート・再試行の方針の名前・打ち直さないときのルート。データだけを持つ |
| 形（`contracts/schemas/causes.schema.json`・`result.schema.json`・`route-chosen.schema.json`） | 表・結果 JSON（`cause`・`routes` を含む）・選択の記録の JSON Schema |
| 終了コードの表（`contracts/facts/exit-codes.json`）・上限の設定（`contracts/limits/`） | 終了コードの値と区分、再試行の方針 `cause.<名前>` の回数・間隔・倍率・頭打ち。値はそれぞれここだけが持つ |
| 回答の語彙（`contracts/facts/answers.json`） | 結果 JSON の `routes` のキーとプランの `on_answer` の回答に使える語 |
| `ndf.contracts.causes` | 表の唯一の読み手。`cause` を組んで確かめ、種類から終了コードと方針を引く。ステートマシンの打ち直しの秒（`retry_delay`） |
| `ndf.contracts.routing` | ルートを選ぶ純粋な関数 `pick` と待ちの計算 `backoff`。ルートの選び方はここだけが持つ |
| `ndf.contracts.result` | 結果 JSON を組み、形と終了コードを確かめて 1 行で出す（`emit`）。形が違えば代わりの `stopped`（`invalid`）を 1 行で出し、終了コード 2 |
| `ndf.contracts.chosen` | 選択の記録の組み立てと確かめと、選んだ主体の決め方（`chooser`） |
| `ndf.core.plan`（`model`・`transition_decide`・`steps`・`runner`） | プランの形の確かめ（IS3）、遷移の判断（`pick` を呼ぶ）、ステップの結果の読み取り、耐久の待ちと選択の記録の書き込み |
| `ndf.progress.run` | ステートマシンの工程を流す `work` の中で打ち直し、ほかの止まりを事象 `failed` に `cause` つきで残し、`next` に `routes` を載せる |
| `ndf.adapters.llm`（`ask`） | 判断の呼び出し。回答を選択肢か JSON のスキーマで縛り、確かめ直す |
| `scripts/measure/ai-branching.py` | 洗い出しの一覧の規則ごとに、ルートを選んだ主体を数える（数え方は [ndf-rebuild-measure.md](ndf-rebuild-measure.md)） |

## 用語

語の定義は用語集にあり、ここでは写さない。

- 停止理由・conductor・承認ゲート: [NDF のワークフローの用語集](../glossary/ndf-workflow.md)
- 選択の記録・作り直し: [用語集: NDF の作り直し](../glossary/ndf-rebuild.md)
- 耐久ワークフロー・耐久ステップ: [docs/glossary/ndf-durable.md](../glossary/ndf-durable.md)

この文書で、**ルート**は止まったステップか工程の次に進む先（コードの `Route`）、**判断の呼び出し**は `ndf.adapters.llm.ask` で AI を
1 回呼ぶ操作を指す。種類と終了コードの一覧の生成物は `plugins/ndf/docs/contracts/exit-codes.md` にある。

## 背景

10 系では、プランが止まった後の立て直し・待ちの知らせの扱い・承認の回答の後のルートを、conductor と supervisor の AI が
出力の文と手順書の表を読んで決めていた。2026-10-14〜11-13 の会話の記録では、AI がプランの記録の文を読んだ回が 2,040 回、
`--from` と `--resume` で再開点を自分で選んだ回が 983 回あった。利用者は 2026-11-12 に「最終的に、すべてのワークフローは
スクリプトで動き、AI は補助的に呼ばれるだけなのが理想である」と決めた（#3152 を #1142 へ統合）。

工程の粒度の分岐（次にどの工程を流すか）はステートマシン（#3092）が持つ。この文書はその下の、工程の中とプランの中の
分岐と例外を持つ。

**要求の受け入れ条件。** #1142 の受け入れ条件「AI の判断からスクリプトへ」は、この文書が仕様として持つ
（[ndf-rebuild.md](ndf-rebuild.md) の「要求の受け入れ条件のオーナー」）。

| 受け入れ条件 | この文書の持ち方 |
| --- | --- |
| AI が判断している分岐と例外の洗い出しが、回数の多い順に残る | 「洗い出しの一覧」と決定 62・63 |
| 止まった理由を種類で返し、ルートをプランが機械で選ぶ | 「停止理由の表」〜「ルートの当て順」と決定 54〜58・130 |
| スクリプト化できない箇所の、呼ぶ時点と回答の形をスクリプトが決める | 「判断の呼び出し」と決定 59・60 |

## 対象範囲

- **含む**: 停止理由の表とその形、結果 JSON の `cause`・`routes`、プランのステップのキー `on_cause`・`on_fail`・`retry`・`exit_cause`・`on_answer`、
  ルートの当て順、プランとステートマシンの打ち直し、判断の呼び出し、選択の記録、洗い出しの一覧 R01〜R27
- **含まない**: 承認するかどうかの判断そのもの、物差し G1〜G3 の数え方（[ndf-rebuild-measure.md](ndf-rebuild-measure.md)）、
  11.0.0 と 11.x の範囲と閉じる証拠（[ndf-rebuild-review-scope.md](ndf-rebuild-review-scope.md)）、DBOS の殻（[ndf-rebuild-libraries.md](ndf-rebuild-libraries.md)）
- **プロジェクトごとに違うもの**: 無い。種類の語彙・終了コード・既定のルート・再試行の値はプラグインが配り、プランのステップの
  `retry` だけがステップごとに方針を上書きする

## 仕様

### 停止理由の表

`contracts/causes/` は種類 1 つを 1 つのファイル（`<種類>.json`）に持ち、`_meta.json` が `owner` を持つ。1 つの種類は
`meaning`・`std_type`・`exit`・`route`・`retry` を必ず持ち、`kind` はファイル名（拡張子 `.json` を除く）から読み手が足す。
`route` が `retry` の種類は `exhausted` を、利用上限の種類は `requires_retry_after: true` を持つ。38 種類あり、既定のルートで
分けると次のとおりである。

| 既定のルート（`route` → `exhausted`） | 種類 |
| --- | --- |
| `step`（`on_fail` のステップ。無ければ conductor） | `test_failed`・`check_failed`・`push_rejected`・`base_conflict`・`work_stopped`・`unreviewed`・`samples_mismatch` |
| `retry` → `step` | `ci_pending`（`cause.ci-pending`）・`infra_wait`（`cause.infra-wait`）・`transient`（`cause.transient`）・`store_busy`（`cause.store-busy`）・`store_unreachable`（`cause.store-unreachable`） |
| `retry` → `human`（`retry_after` があるときだけ打ち直す） | `usage_limit`（`cause.usage-limit`。1 回の待ちの頭打ちは `supervise.limit-wait-max`） |
| `conductor`（会話の AI が結果の `next`・`routes` から次の手を選ぶ） | `incomplete`・`not_applicable`・`invalid`・`wait_timeout`・`interrupted`・`input_missing`・`bad_report`・`mvv_unresolved`・`unknown` |
| `human`（人へ問う） | `fix_limit`・`deploy_failed`・`precondition`・`deps_missing`・`refused`・`not_ci`・`needs_human`・`config_broken`・`store_broken`・`store_in_use`・`restore_unsupported`・`format_newer`・`format_mismatch`・`account_unavailable`・`merge_method`・`spin` |

- `std_type` は Serverless Workflow DSL 1.0.3 の標準の誤りの型 8 つ（configuration・validation・expression・authentication・authorization・timeout・communication・runtime）のどれかである
- `exit` は終了コードの表の、`status` が `stopped` の行の id（`failed` 1・`unreadable` 2・`precondition` 3・`base_conflict` 4・`cd_failed` 5・`incomplete` 6・`deps_missing` 69・`infra_wait` 75）を指す。値は終了コードの表だけが持ち、読み手が引き直す
- 終了コードは結果の `status` の区分を表すだけで、ルートの判定に使わない。終了コード 3 は前提の欠け・正常な否定の結果・待ちの上限など 8 種類が共有し、プランは種類で分ける
- `retry` は上限の設定の方針の名前で、方針は `attempts` を持ち、`retry_after` を待たない種類は `interval` も持つ（読み手が確かめる）

### 結果 JSON の `cause` と `routes`

| 項目 | 置く結果 | 形 |
| --- | --- | --- |
| `cause` | `stopped` では必須。`ok` と `gate` には置かない | `kind`（表の値）・`detail`（空白だけでない文字列）を必ず持ち、任意で `instance`（落ちた対象。チェックの名前・ステップの id・ファイル）・`retry_after`（打ち直してよいまでの秒。0 以上） |
| `routes` | `gate` では必須。`ok` には置かない。`stopped` には置ける | キーは回答の語彙（`approved`・`rejected`・`request_changes`・`retry`・`finish`・`cancel`）。値は打てるコマンド `{"argv": ["ndf", …], "ask": [<打つ側が埋めるキー>]}` か `null`（何も打たない） |
| `gaps` | `cause.kind` が `incomplete` の結果だけ（その結果には必須） | 欠けの配列 |

- 結果は `ndf.contracts.result.emit` が出す。形の誤り（スキーマ・表に無い種類・回答の語彙に無いキー）があるか、`stopped` の終了コードが `cause.kind` に決めたものと違えば、元の結果を出さずに、元の値を含まないエラーの文を標準エラーへ書き、元の値を含まない代わりの `stopped`（`invalid`）を 1 行で出して終了コード 2 で終える
- `next` は次に打つコマンド 1 つで、`routes` の値と同じ形である。打つ側は `argv` をそのまま打ち、`ask` のキーごとに `--<キー> <値>` を末尾へ足す
- 承認ゲートで止まったプランの結果は `routes` `{"approved": <続きの ndf plan next>, "rejected": null}`、止まったプランの結果は止まったステップから続ける `next` を持つ

### プランのステップのキー

`ndf.core.plan.model` がプランを読んで確かめる。形が違えば `PlanError` で流さない（`ndf plan check` も同じ検査を打つ）。

| キー | 形 | 意味 |
| --- | --- | --- |
| `on_cause` | `{"<種類>": "<ステップの id>" \| "end" \| "stop" \| "retry"}` | 種類ごとのルート。当て順の最初に引く |
| `on_fail` | ステップの id | 既定のルートが `step` の種類（と打ち直しを使い切った後の `exhausted: step`）が進むステップ |
| `retry` | `{"<種類>": {"attempts", "interval", "factor", "max_interval"}}` | 再試行の方針をこのステップだけ上書きする。キーは上限の設定の方針と同じ |
| `exit_cause` | `{"<終了コード>": "<種類>"}` | 結果 JSON を出さないコマンド（`sh -c`・`git` など）の終了コードを種類に対応づける。結果 JSON を出したステップでは読まない |
| `on_answer` | `{"<回答>": "<ステップの id>" \| "end" \| "stop"}` | 承認ゲートを返したステップの、回答ごとのルート。無ければプランを承認ゲートで終える |

ステップの結果の種類は、標準出力の最後の結果 JSON の `cause.kind` から読む。結果 JSON が無ければ、終了コードが 0 なら `ok`、
ほかは `exit_cause` にある終了コードならその種類、無ければ `unknown` にする（`ndf.core.plan.steps.outcome_of`）。
文の中身から種類を推さない。

### ルートの当て順

止まったステップのルートは `ndf.contracts.routing.pick` だけが選ぶ。引数は種類・`on_cause`・`on_fail`・打ち直しの文脈（回数・方針・`retry_after`・頭打ちの秒）だけである。

| 順 | 条件 | ルート |
| ---: | --- | --- |
| 1 | `on_cause` に種類がある | その値をルートの語にする（無ければ表の `route`）。ステップの id ならそのステップ、`end`・`stop` はそのまま、`retry` は 2 へ |
| 2 | ルートの語が `retry` で、方針の回数が残り、`requires_retry_after` の種類なら `retry_after` がある | `retry`。待ちは `retry_after`、無ければ `interval × factor^回数`。`max_interval` と頭打ちの秒で抑える |
| 3 | ルートの語が `retry` で、2 で打ち直さない | 表の `exhausted`（無ければ `step`）を 4 か 5 で引く。ルートの語が `retry` でなければ `exhausted` を見ない |
| 4 | ルートの語が `step` | `on_fail` があればそのステップ、無ければ `conductor` |
| 5 | ルートの語が `conductor` か `human` | その語。`on_fail` を見ない |

遷移の判断（`ndf.core.plan.transition_decide`）は選んだルートを次の手にする。

| ルート | 次の手 |
| --- | --- |
| `retry` | 耐久の受信待ち（`durable.recv`）で待った後に同じステップを打つ。回数は（ステップ, 種類）ごとに数え、ステップの数の上限に数えない |
| ステップ | そのステップを打つ。開始点の前に通すステップの中なら、進まずにその種類で止まる |
| `end` | プランを `ok` で終える |
| `stop` | `refused` で止まる |
| `conductor`・`human` | その種類で止まる。結果は止まったステップから続ける `next` を持つ |

知らないステップへ進めば `invalid`、ステップの数がプランの `limit`（既定はステップの数の 3 倍）を超えれば `fix_limit` で止まる。

### ステートマシンの止まり

ステートマシンの工程は、工程から作業を呼ぶ層を通してプランを呼ぶ。

- `work` は、止まった結果の種類が打ち直しで直るもの（`causes.retry_delay` が秒を返す）なら方針どおりに待って打ち直す。打ち直しても同じプランの回の結果が返れば、それ以上は打ち直さない
- ほかの止まりは事象 `failed` に `{"cause": <止まった結果の cause>, "step": <ステップの id>}` を載せる。`cause` が無ければ `unknown`、出力の規約に合わない結果は `bad_report` にする
- `ndf progress next` は失敗した行を、その種類の `stopped` と `routes`（`retry` → `ndf progress retry <ID> <工程>`・`finish` → `ndf progress finish <ID> <工程>`・`cancel` → `ndf progress cancel <ID>`。どれも `ask` に理由を持つ）で返す。承認ゲートは回答ごとの `ndf progress answer` を `routes` に持つ

### 判断の呼び出し

`ndf.adapters.llm.ask(question, material, schema, timeout)` は、スクリプトが決めた時点で AI を呼ぶ唯一の形である。今の呼び手は
MVV 判定（`ndf.domains.records.mvv_gate`）で、プランのステップに AI を呼ぶ型は無い（AI を使う作業は worker のステップとして打つ）。

| モード | 回答のスキーマ（`answer_schema`） | 回答の確かめ |
| --- | --- | --- |
| 選択肢（`choices`） | `{"decision": <選択肢の enum>, "reason": <空でない文字列>, "basis": [<文字列>]}`。追加のキーを許さない | `decision` が選択肢の中にある |
| 任意の JSON（`schema`。スキーマのファイルのパス） | 渡したファイル（draft 2020-12 として正しいもの） | そのスキーマだけに合う。`decision` を求めない |

- `claude -p --json-schema <スキーマ>` を判断の権限（Tool を使わせない）で起動し、回答を `jsonschema` で確かめ直す
- 合わなければ前の誤りを添えて 1 回だけ打ち直し（`ATTEMPTS` 2）、なお合わなければ `needs_human` で止まり、2 回の回答の先頭 200 字を `detail` に載せる。回答の文から近い選択肢を拾わない
- 起動できない・判断を断った（`precondition`）、期限を超えた（`interrupted`）、利用上限（`usage_limit`）、重い起動のスロットが空かない（`infra_wait`）は打ち直さず、その種類で返す
- 呼ぶたびに回数・合ったか・費用を実行の記録（`judge`）へ 1 行書く

### 選択の記録

ルートを選ぶたびに 1 行残す事象で、形は `route-chosen.schema.json` が持つ。プランの実行は状態の保存先の `progress.jsonl` へ、
耐久ステップ（`record_choices`）で 1 件ずつ追記する（回復で同じ選択を 2 回書かない）。

| キー | 必須 | 値 |
| --- | --- | --- |
| `event` | 必須 | `route_chosen` |
| `at` | 必須 | ISO 8601 の時刻（時差つき） |
| `run_id` | 必須 | プランの実行の回 |
| `rule` | 必須 | 洗い出しの一覧の番号（`R` と数）。今の書き手は、止まったステップのルートを R03（`base_conflict` なら R10）、承認の回答の後のルートを R13 で書く |
| `chooser` | 必須 | `script`・`conductor`・`human` |
| `from`・`to` | 必須 | 選んだときのステップと、進む先（ステップの id か `retry`・`end`・`stop`・`conductor`・`human`） |
| `cause` | 任意 | 停止理由の種類（表にあることを読み手が照らす） |

選んだ主体は `chosen.chooser` が決める。承認ゲートの人の回答なら `human`、打った引数が結果の `next` の `argv` で始まり、残りが
`ask` のキーの `--<キー> <値>` の組だけなら `script`、ほかは `conductor` である。追加のキーを許さない。

### 洗い出しの一覧

10 系で AI が分岐と例外のルートを決めていた箇所 27 件を、回数の多い順に並べる。回数は 2026-10-14〜11-13 の会話の記録・
プランの記録・並列の計画の台帳から数えた上限の値で、すべてが誤りではない（数え方と規則の表は [ndf-rebuild-measure.md](ndf-rebuild-measure.md)）。
判定の基準は決定 62 にある。

| # | AI が判断していた分岐 | 判定 | 回数 |
| --- | --- | --- | ---: |
| R01 | 止まったプランの理由を記録の文から読み取る | 一部できる | 2,040 |
| R02 | 開発ループの結果の終了コードと項目から次の 1 手を選ぶ | 一部できる | 1,129 |
| R03 | 止まったプランの再開点を選ぶ | 一部できる | 983 |
| R04 | supervisor の報告の `結果` から、次の起動か報告かを選ぶ | 一部できる | 865 |
| R05 | 並列の計画の待ちが返した種類ごとに次の 1 手を打つ | 一部できる | 623 |
| R06 | CI のチェックを手で読み、直す・待つ・マージを決める | できる | 621 |
| R07 | プランの待ちの終わり方から次を決める | できる | 553 |
| R08 | マージの失敗（テスト・テスト以外のチェック・拒否）を見分ける | 一部できる | 449 |
| R09 | 基盤の待ちで止まったプランを、いつ打ち直すかを決める | できる | 408 |
| R10 | 衝突で止まったプランで、取り込みを手で打つか決める | できる | 339 |
| R11 | 会話を切る時点と、続けるか切るかを決める | 一部できる | 198 |
| R12 | 人へ問うかを決め、問いの選択肢を組む | 一部できる | 175 |
| R13 | 承認の回答の後に打つコマンドを選ぶ | できる | 158 |
| R14 | 遅れたステップを打ち直すか・直すか・止めるか | できない | 154 |
| R15 | worktree の設定の確かめの終了コードから進むか止まるか | できる | 124 |
| R16 | 耐久ワークフローが失われたときに起動し直すか | できる | 96 |
| R17 | 設計のプランの最後で、設計承認ゲートへ渡すか止めるか | できる | 74 |
| R18 | 中断した supervisor と worker を誰がどう起こすか | 一部できる | 61 |
| R19 | CI の監視の「CI 以外の理由」の後の立て直し（#3153） | できる | 46 |
| R20 | テストの失敗が変更に関係するかを読み、直すか進むか | 一部できる | 44 |
| R21 | worker が残した欠けを読み、続けるか作り直すか | できない | 32 |
| R22 | MVV 判定を打ち、従うなら MVV の回答を送る | できる | 25 |
| R23 | MVV の承認に戻るか `normal` で進めるか | 一部できる | 18 |
| R24 | リファクタリングを飛ばすか退避するか | できる | 12 |
| R25 | 中身が空・版数が出し済みのリリースを差し戻すか | 一部できる | 8 |
| R26 | worker の「判断が要る」を supervisor が決める | できない | 数えられない |
| R27 | モードを判定する | できない | 数えられない |

判定の内訳は、できる 11 件・一部できる 12 件・できない 4 件である。R26 は結果ファイルが worktree ごとに散って作業の後に消え、
R27 は判定が会話の本文に残って Tool の呼び出しにならないため数えられない。2 件は、worker の結果ファイル（`ok`・`gate`・`stopped`）と
モードの判定の出力が回答の形を決めている。

### 常に成り立つ条件

番号は作り直しのほかの不変条件と分けて `IS` を付ける。

| # | 対象 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| IS1 | 停止理由の表 | 結果 JSON の `cause.kind` は表にある値だけである | `emit` が出さずに 2 で終える（`causes.kind_errors`） |
| IS2 | 結果 JSON | `stopped` は必ず `cause` を持ち、`ok` と `gate` は持たない | `result.schema.json` の `allOf` で `emit` が出さない |
| IS3 | プランの実行 | `on_cause`・`retry`・`exit_cause` の種類は表にあり、`next`・`on_fail`・`on_cause`・`on_answer` のルートはステップの id か許した語（`on_cause` は `end`・`stop`・`retry`、ほかは `end`・`stop`）で、`on_answer` の回答は回答の語彙にある | `model.parse` が `PlanError` で流す前に断る |
| IS4 | プランの実行 | 止まったステップのルートは「当て順」の 1〜5 の順で最初に当たったもので決まる。既定のルートが `conductor`・`human` の種類は `on_fail` へ進まない | `routing.pick` だけがルートを返し、遷移の判断はほかに分岐を持たない |
| IS5a | プランの実行 | 打ち直しの回数は、種類ごとの方針（ステップの `retry` か上限の設定）の `attempts` を超えない | `pick` が上限に当たった後は `retry` を返さない |
| IS5b | プランの実行 | 打ち直しを使い切った後のルートは表の `exhausted` で決まる | IS4 と同じ関数が返す |
| IS6a | 判断の呼び出し | 選択肢のモードの回答は `decision`・`reason`・`basis` の形に合い、`decision` が選択肢の中にある | 1 回だけ打ち直し、なお合わなければ `needs_human` で止まる |
| IS6b | 判断の呼び出し | 任意の JSON のモードの回答は、渡したスキーマだけに合えばよい | IS6a と同じ |
| IS7 | プランの実行 | ルートを選ぶ関数は `summary`・`detail`・出力の文を受けない | `pick` の引数に文が無いことをテストが確かめる |
| IS8 | 停止理由の表 | 種類を決められない止まり（例外・結果 JSON を出さず `exit_cause` にも無い終了コード）は `unknown` で、その既定のルートは `conductor` である。AI に種類を推させず、`on_fail` へ進めない | IS4 の順の 5 |
| IS9 | 結果 JSON | 承認ゲートの結果は回答ごとのコマンドを `routes` に持ち、`ok` は `routes` を持たない | `emit` が出さない |
| IS10 | 停止理由の表 | 各種類は標準の誤りの型を 1 つ持つ | `causes.schema.json` の `enum` で表の読み手が落とす |
| IS11 | 停止理由の表 | 各種類は終了コードの表の `stopped` の行を 1 つ指す。`stopped` の終了コードは種類から引き、手順のスクリプトは別に渡さない | 表の読み手が落とし、`emit` は種類と違う終了コードを形の誤りにする |
| IS12 | 結果 JSON | `gaps` を持つことと、`cause.kind` が `incomplete` であることは同じである | `emit` が出さない |

## 決定と理由

### 決定 54: ルートを文から決めさせないため、停止理由を結果 JSON の項目 `cause` で返し、種類を閉じた語彙の表に置く

止まったときに読む値が `summary` と出力の文しかないと、AI が文を読んでルートを決める（R01）。種類を 1 語で返せば、プランと
ステートマシンは語の一致だけで表を引ける。項目は `kind`・`detail`・`instance` の形にし、Serverless Workflow の `Error` と
RFC 9457 の Problem Details（種類と人の読む文を分け、知らない拡張の項目は受け手が無視する）にそろえる。種類の語彙は表のデータ
（`contracts/causes/`）にだけ置き、コードは種類を `cause("<種類>", …)` の引数の文字列でだけ書く（[ndf-rebuild-data-and-smells.md](ndf-rebuild-data-and-smells.md) の決定 42）。

- 採らなかった案: `metrics` の中に種類を置く。`metrics` の中身はスクリプトごとに自由で、形の検査（IS1）を掛けられない
- 採らなかった案: 種類を URI（標準の `type`）で持つ。NDF の中で読み書きする語が長くなり、標準との対応は表の `std_type` の列で足りる

### 決定 55: 終了コードと種類を食い違わせないため、`stopped` の結果は必ず種類を持ち、終了コードは種類から引く

終了コードは「読めない（2）」「前提が無い（3）」のように意味が広く、種類ごとに足すと承認ゲート（10〜19）と判断待ち（20〜29）の範囲の
契約と衝突し、シェルの読み手（`set -e`・`|| exit`）が区分を読めなくなる。終了コードは成否と大きな区分だけを表し、表の各行が
終了コードの表の id を 1 つ持つ（IS11）。手順のスクリプトは種類だけを渡し、`emit` が終了コードを引く。種類を決められない止まりは
`unknown`・`invalid`・`interrupted` を付ける。終了コードから種類を推す経路は、プランのステップが `exit_cause` で明示した対応だけである。

- 採らなかった案: 終了コードを種類ごとに足す（例: テストの失敗を 7 にする）。範囲の契約と衝突する
- 採らなかった案: `cause` を任意にし、無い結果を終了コードから既定の種類へ読み替える。種類を返さないスクリプトが残り続ける（決定 130）

### 決定 56: プランのルートを表から機械で選ぶため、ステップに `on_cause` と `retry` を持たせ、ルートを返す関数を 1 つにして当てる順を固定する

AWS Step Functions の `Retry` → `Catch` のように当てる順を決めた表にすれば、同じ止まりは毎回同じルートへ進む。順は「当て順」の
1〜5 で、`on_cause` を最初に引くのは、プランの書き手がそのステップの止まりの意味を知って書いた表だからである。既定のルートが
`conductor`・`human` の種類は `on_fail` へ進めない。種類を問わないフォールバックへ送ると、上限に当たった止まりや種類を決められない
止まりまで直すステップへ進むためである。打ち直しを使い切った後のルートは種類ごとに違う（`usage_limit` は人へ、ほかは `on_fail` へ）ため、
表に `exhausted` の列を持つ。順を決める関数は `ndf.contracts.routing.pick` の 1 つだけで、文を受けない（IS7）。

- 採らなかった案: 種類の既定のルートだけで決め、ステップに表を持たせない。同じ種類でもプランごとに直すステップが違う（`repeat-check`・`fix-base`・`review-delta`）
- 採らなかった案: 終了コードでルートを書く `on_exit` と、同じ役の `skip_to`・`on_fail_only`・`on_section_missing` を持つ（10 系の形）。種類の表と二重になる
- 採らなかった案: 既定のルートを `retry`・`step`・`human` の 3 つにし、`unknown` と前提の欠けを人へ戻す（scripted-flow の初めの形）。会話の AI が結果の `next` と `routes` から選べる止まり（`invalid`・`not_applicable`・`wait_timeout` など）まで人へ問うことになるため、`conductor` を足した

### 決定 57: 待てば解ける止まりに AI と人を使わないため、打ち直しで直る種類はプランの中で耐久の待ちの後に打ち直す

10 系で基盤の待ちの文を読んだ回は 408 回、`gh run rerun` の手打ちは 27 回あった（R09）。いずれも待ってから同じステップを打ち直せば
済む。Temporal の再試行の方針と同じく、待てば解ける種類（`ci_pending`・`infra_wait`・`transient`・`store_busy`・`store_unreachable`・`usage_limit`）
だけに打ち直しを持たせ、ほかは打ち直さない。回数と間隔の値は上限の設定の `cause.<名前>` にだけ置き、表は方針の名前だけを持つ。
待ちはステップの再試行ではなく、プランの耐久ワークフローの中の受信待ち（`durable.recv`）で行う。ステップは 1 回の試行ごとに記録を
残すため、再試行をステップの中に隠すと試行の記録が 1 つにまとまる。ステートマシンの `work` も同じ方針の秒（`causes.retry_delay`）で打ち直す。

- 採らなかった案: DBOS のステップの再試行（`retries_allowed`）に任せる。試行ごとの記録と打ち直すかの判定を、停止理由の表と二重に持つ
- 採らなかった案: 利用先のリポジトリの設定（`.ndf/supervise.json` の `causes.<種類>`）で回数と間隔を上書きする（scripted-flow の初めの形）。上書きはプランのステップの `retry` だけにした

### 決定 58: 工程の粒度と工程の中のルートを食い違わせないため、プランの停止理由をステートマシンの事象 `failed` へコピーし、`next` が回答ごとのコマンドを返す

ステートマシンは次にどの工程を流すかを持つ。工程が失敗したとき conductor が理由の文を読むと、この主題の問題が工程の境で残る。
そのため、プランの結果の `cause` を事象 `failed` へそのままコピーし、打ち直しで直る止まりは `work` の中で済ませ、conductor へ返すのは
ほかの止まりだけにする。返すときは種類の終了コードの `stopped` と、`routes` の `retry`・`finish`・`cancel` のコマンドを載せる。
打ち直しは失敗した行だけに当たるため、再開点を conductor が選ぶ余地は残らない（R03）。

- 採らなかった案: 止まりの扱いをプランの中だけで閉じ、工程へは成否だけを渡す。プランで決められない止まり（`precondition`・`refused`）のルートを conductor が文から決めることになる
- 採らなかった案: 工程の定義に種類ごとのルート（`metadata.ndf.on_cause`）を持ち、ステートマシンが `pick` で工程のルートを選ぶ（scripted-flow の初めの形）。工程の中の分岐はプランの `on_cause` が持ち、工程の失敗で conductor が選ぶ手は `retry`・`finish`・`cancel` の 3 つで足りる

### 決定 59: スクリプト化できない箇所の呼ぶ時点と回答の形をスクリプトが決めるため、AI を呼ぶ形を判断の呼び出し 1 つに寄せ、回答を選択肢か JSON のスキーマで縛る

AI でなければ決められない箇所（R14・R20・R21・R26・R27）も、ルートの候補は有限である。Anthropic の「Building effective agents」の
workflow と LangGraph の条件つきの辺のとおり、流れはコードが持ち、LLM には決まった時点で選択肢を選ばせ、名前からルートへの表は
スクリプトが持つ。呼ぶ時点は呼び手のスクリプトが持ち、AI が自分で「ここで判断する」と決めない。回答は `claude -p --json-schema` で
縛り、受けた後に `jsonschema` で確かめ直す（CLI の振る舞いに依らない）。打ち直しても直らない失敗はその種類で返す。

- 採らなかった案: 回答の文から決定を取り出す（10 系の judge の `parse_decision`）。選択肢の外の回答や候補を複数含む文を黙って読み替えうる
- 採らなかった案: 判断の箇所ごとに別の呼び出しの形を作る。同じ確かめを箇所の数だけ持つ
- 採らなかった案: プランのステップに判断の型（`type: judge` の `question`・`choices`・`schema`・`inputs`）を持つ（scripted-flow の初めの形）。AI を使う作業は worker のステップとして打ち、判断の呼び出しはスクリプトの中から呼ぶ形で足りた

### 決定 60: AI の回答を推して進まないため、判断の呼び出しの回答が形に合わなければ 1 回だけ打ち直し、なお合わなければ人へ戻す

回答が形に合わないのは入力が足りないか問いが曖昧なときで、打ち直しを重ねても費用だけが増える。1 回の打ち直しは出力の途切れのような
一時の誤りを拾うためで、2 回目には前の誤りを添える。2 回とも合わなければ `needs_human` で止まり、2 回の回答の先頭を結果に載せる。

- 採らなかった案: 回答から最も近い選択肢を選ぶ。AI の誤りを黙ってルートに変える
- 採らなかった案: 打ち直しを上限まで繰り返す。費用だけが増える

### 決定 61: 承認の回答の後に AI が文を読まないため、承認ゲートの結果に回答ごとの次のコマンド（`routes`）を必ず添える

承認の判断そのものは範囲の外である。ただし 10 系では、回答の後に打つコマンドを目録の文から AI が選んでいた（R13）。結果に
回答ごとのコマンドを添えれば、conductor は回答を記録して値を打つだけになる。値は文字列ではなく `argv` と `ask` の形にし、打つ側が
組み立てる余地を残さない。キーは回答の語彙の表にある語だけにする。人へ戻す止まり（ステートマシンの失敗）にも `routes` を置ける。

- 採らなかった案: 回答を受けたスクリプトがそのまま次を流す。承認ゲートの回答を書く手順とコマンドを打つ手順を 1 つに混ぜ、人の回答が無いのに回答を書かない決まり（#2707）の確かめる範囲が変わる
- 採らなかった案: `routes` の値をシェルのコマンドの文字列にする。打つ側が文字列を分割する規則に依る

### 決定 62: 判定がぶれないため、洗い出しの判定を「入力が機械で読めるか」と「ルートが有限の表で書けるか」の 2 つで決める

判断の入力が機械で読める値（終了コード・結果 JSON の項目・記録の行・GitHub の状態）だけで、ルートが有限の表で書ければ「できる」とする。
ルートの候補と入力の一部は機械で決まり、残りに中身の判断（直し方・関係の有無・文の意味）が要れば「一部できる」、ルートそのものが
中身の判断で決まれば「できない」とする。「一部できる」は機械で決まる分を `cause` と表へ移し、残りを判断の呼び出しにする。
こうすれば「一部できる」も「できない」も、AI を呼ぶ時点と回答の形はスクリプトが決める。

- 採らなかった案: AI の呼び出しの回数の多さで判定する。回数が少なくても誤りの影響が大きい箇所（R19。テストが落ちたのに直しを飛ばす）を落とす。回数は並べる順にだけ使う

### 決定 63: 効果を同じ物差しで比べるため、回数を数えるスクリプトを `scripts/measure/ai-branching.py` に置き、入れ替えの後に同じコマンドで数え直す

数え方（規則・文の一覧・期間）が変わると前後を比べられないため、スクリプトに固定する。会話の数は NDF そのものを開発した会話も含む
上限の値であることを残す。CI の検査（ラチェット）にはしない。会話の記録は利用者の手元にしか無く、CI で読めないためである。
前後の形を同じ規則の番号へ寄せる数え方は [ndf-rebuild-measure.md](ndf-rebuild-measure.md) の決定 114 が持つ。

- 採らなかった案: ターンの型を数える実験版のスクリプトを広げる。実験版を既定で動くものから参照しない決まりと、測るもの（ターンの型と分岐の跡）の違いから採らない

### 決定 130: 移行の間だけの経路を残さないため、結果 JSON・プランのキー・テストを最終の形へ直接変える

利用者の方針（2026-11-13）は、後方互換を意識せず、形を一番綺麗な最終形へ直接変えることである（[ndf-rebuild-libraries.md](ndf-rebuild-libraries.md) の決定 65）。
そのため `cause` は最初から `stopped` に必須で（IS2）、`cause` の無い結果を終了コードから読み替える経路を持たない（決定 55）。
`on_exit` と同じ役のキーは持たず（決定 56）、`routes` は承認ゲートに最初から必須である（IS9）。テストは新しい形の観点で書き、
10 系のテストを安全網にしない。

- 採らなかった案: `cause` を任意にして手順のスクリプトを少しずつ移す、`on_exit` を `on_cause` と並べて残す。どちらも移した後に古い経路を消す作業が要り、その間は 2 つの経路のどちらで動いたかを読み手が見分けることになる

## テスト観点

テストは `plugins/ndf/tests/contracts/`（`test_causes.py`・`test_routing.py`・`test_result.py`・`test_chosen.py`）・`tests/core/plan/`・`tests/adapters/test_llm.py` が持つ。

- 表の各項目が標準の型と `stopped` の終了コードの id を持ち、終了コードの値は終了コードの表にだけある。打ち直しの種類の方針が上限の設定にあり、`interval` の無い方針は `retry_after` を待つ種類だけが持てる（IS10・IS11）
- 表に無い `kind`・空の `detail`・`ok` に添えた `cause`・`cause` の無い `stopped`・種類と違う終了コード・`gaps` があるのに `incomplete` でない結果・`routes` の無い承認ゲート・回答の語彙に無い `routes` のキーを、`emit` が出さずに 2 で終える（IS1・IS2・IS9・IS12）
- `pick` は `on_cause` を最初に当て、`on_cause` の `retry` は種類の方針で打ち直す。打ち直しは回数の内で行い、上限の後は `exhausted` へ進む。呼び手が解いた方針を種類の既定より優先する（IS4・IS5a・IS5b）
- `usage_limit` は `retry_after` が無ければ人へ、`transient` は倍の間隔で打ち直して人へ止まらない。既定が `conductor`・`human` の種類は `on_fail` を見ない。既定が `step` の種類は `on_fail`、無ければ `conductor` へ進む（IS4・IS8）
- `pick` の引数に文が無い。同じ種類で文だけ違う 2 件が同じルートへ進む（IS7）
- 表に無い種類を `on_cause`・`retry`・`exit_cause` に持つプランと、知らないルート・回答の語彙に無い `on_answer` を持つプランが、流す前に断られる（IS3）
- 結果 JSON を出さずに 1 で終えたステップは、`exit_cause` に無ければ `unknown` になり、`on_fail` を持っていても conductor へ戻る（IS8）
- 打ち直しの待ちの途中でプロセスを落としても、耐久の記録から続きの待ちと同じステップを打ち、選択の記録を 2 回書かない
- 判断の呼び出しは、1 回目で合えば進み、1 回目が外れて 2 回目で合えば進み、2 回とも外れれば `needs_human` で止まる。回答の文に選択肢の語を含んでも拾わない。任意の JSON のモードで `decision` の無い回答を受ける。起動の失敗・期限・利用上限は打ち直さない（IS6a・IS6b）
- 選択の記録は形に合わない値（主体の語・規則の番号・表に無い `cause`）を断る。`chooser` は人の回答で `human`、`next` のとおりなら `script`、`ask` の外の引数を足せば `conductor`
- ステートマシンの `work` が打ち直しで直る止まりを方針どおりに打ち直し、ほかの止まりを `failed` に `cause` つきで残し、`next` が種類の終了コードと `routes` の 3 つを返す

## 関連リンク

- [ndf-rebuild.md](ndf-rebuild.md)（作り直しの索引・子の記号・番号の対応表）・[ndf-rebuild-measure.md](ndf-rebuild-measure.md)（物差し G1〜G3 と規則の表）・[ndf-rebuild-libraries.md](ndf-rebuild-libraries.md)（DBOS の殻・決定 65）・[ndf-rebuild-data-and-smells.md](ndf-rebuild-data-and-smells.md)（判定データと事実の表）・[ndf-rebuild-review-scope.md](ndf-rebuild-review-scope.md)（11.0.0 と 11.x の範囲）
- `plugins/ndf/contracts/causes/`・`plugins/ndf/contracts/schemas/result.schema.json`・`plugins/ndf/contracts/schemas/route-chosen.schema.json`・`plugins/ndf/docs/contracts/exit-codes.md`
- [#1142](https://github.com/devbasex/ai-plugins/issues/1142)（作り直しの傘）・[#3152](https://github.com/devbasex/ai-plugins/issues/3152)（AI の判断からスクリプトへ）・[#3153](https://github.com/devbasex/ai-plugins/issues/3153)（CI の監視の読み違い）・[#3092](https://github.com/devbasex/ai-plugins/issues/3092)（ステートマシン）・[#3395](https://github.com/devbasex/ai-plugins/issues/3395)（選択の記録）・[#3412](https://github.com/devbasex/ai-plugins/issues/3412)（確定仕様化と測り直し）
