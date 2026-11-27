# バージョン番号を持たないリポジトリでのデプロイの工程と、出力の足りない承認の失敗の理由

## 概要

**バージョン番号を持たないリポジトリ（`branch_strategy.release.version_store.kind: none`）でも、デプロイの工程は出したものを指す値（リリースの識別子。例: `main abc1234`）を `product_version` にして終わる。** バージョン番号の形の確かめは結果 JSON の定義から外し、リポジトリの設定を読める `ndf answer work` が、バージョン番号を持つリポジトリのときだけ `ndf worker run` に確かめさせる。

- オーガナイザーがまとめたリリースを出した後は、`ndf answer work` が worker を起動せず、承認と一緒に記録した値（リリースの識別子と届け先）で工程を終える。同じものをもう一度出さないためである
- 届け先が記録から決まらないときは、worker を起動せずに `stopped`（`precondition`）で止まり、`ndf progress finish` で手で終える手順を示す
- worker が判断を求めた（`status: gate`）後に `approved` と回答しても出力が足りなければ、工程は `bad_report` で失敗する。理由には足りない値の名前、worker が判断を求めた理由の要約、次の操作が入る。この理由の組み立ては工程の種類を問わない
- バージョン番号を持つリポジトリでは、今までどおり `product_version` にバージョン番号の形を求める

対象の Issue: バージョン番号を持たないリポジトリでデプロイの工程が `bad_report` を繰り返して終わらない（ https://github.com/devbasex/ai-plugins/issues/4513 、PR https://github.com/devbasex/ai-plugins/pull/4609 ）。

## 用語

オーガナイザー・レーン・まとめたリリースの定義は用語集の [NDF の並列開発](../glossary/ndf-parallel.md) に、デプロイとリリースの語は [NDF のリリース](../glossary/ndf-release.md) にある。この文書では次の語も使う。

| 語 | 意味 |
| --- | --- |
| リリースの識別子 | 出したものを指す 1 行の値。改行を含まず、空白だけでない文字列。文脈の `product_version` とリリース後テストの記録の `version` が持つ。バージョン番号を持つリポジトリではバージョン番号である |
| 記録済みの値 | まとめたリリースで、オーガナイザーが `ndf lanes release --version` に渡し、リリース承認ゲートへの回答と一緒に文脈へ書かれた `product_version`、同じ事象に書かれた届け先 `delivery`（決まったときだけ）、承認を記録した時点のバージョンの保存先の種類 `release_store` |
| 保存先の種類 | 承認を記録した時点のバージョンの保存先の種類。`none`（バージョン番号を持たない）・`versioned`（持つ）・`unknown`（設定を読めなかった）の 3 語 |
| 判断の求め | worker が結果に `status: gate` を書き、conductor の回答を待つこと |
| 工程のステップ・プランのステップ | デプロイの工程は `release-mvv`・`release-reading`・`release-gate`・`release` の 4 つの工程のステップを流す。`release` がプラン `release` を呼び、プランは `release-merge`（承認した PR のマージ）と `work`（出す作業）の 2 つのプランのステップを流す。worker が判断を求めるのはプランのステップ `work` である |
| 試行・回 | 試行は、工程の retry から次の retry までの 1 回分。試行の始めに実行の保存先の `values.json` は消える。回は、プランを 1 度流すこと。承認の回答の後と自動の打ち直しでは、同じ試行のまま次の回になる |

## 背景

NDF 11.0.36 で、バージョン番号を持たないサービスのリポジトリのレーンが、デプロイの工程を終えられなくなった。オーガナイザーが `--version "main <短いハッシュ>"` でまとめたリリースを記録した後、worker が「バージョン番号の形の値を返せない」と判断を求め、`approved` と回答すると `bad_report` で失敗し、retry しても同じことが繰り返された。

根本原因は 3 つあり、どれか 1 つを直しても工程は止まったままだった。

| # | 原因 | 直した場所 |
| --- | --- | --- |
| 1 | 結果 JSON の `outputs.product_version` が、リポジトリの設定に関係なくバージョン番号の形を求めていた | 結果 JSON の定義と `ndf worker run` |
| 2 | オーガナイザーが記録した値は文脈に入るが、worker には PR の番号しか渡らず、もう出たことを知らなかった | `values.json` と `ndf answer work` |
| 3 | 判断の求めの後の `approved` は、続きが無いとプランの完了になる。ところが判断の求めの結果には worker が書いた `outputs` が引き継がれず、出力が無いまま完了扱いになって `bad_report` になった。誤りの文には足りない値の名前も worker の理由も出なかった | `ndf worker run`・プランの遷移と出力の記録・工程の進行 |

主な判断と理由は次のとおりである。

- **形の確かめは結果 JSON の定義でなく `ndf worker run` で行う。** 結果 JSON の定義と `ndf contracts emit` はリポジトリの設定を読めない。定義を緩めるだけでは、バージョン番号を持つリポジトリで `ndf v11.0.0` のような値が文脈に入る。工程の出力の確かめで見ると、`ndf progress finish` で手で終える経路まで厳しくなる
- **オーガナイザーが出した後は、worker を起動せずスクリプトが値を返す。** 記録済みの値を worker の入力に足して「もう出さずに返す」と指示しても、LLM は同じ入力で判断を求めて止まることがあり、毎回同じ結果にならない。デプロイのプランごと飛ばすと、承認した PR がマージされていない場合を拾えないので、`release-merge` は残す
- **worker を起動するかは、承認の時点の保存先の種類で決める。** デプロイの工程の時点で設定を読めないことを理由にバージョン番号を持つ扱いに落とすと、オーガナイザーが済ませた配布を worker がもう一度出す
- **届け先は、承認を記録する時点に 1 度だけ決めて回答に残し、デプロイの工程の時点の設定からは選ばない。** 設定は後から直せるので、今の設定は過去の配布先の根拠にならない。たとえば配布の時点で本番の届け先が A と B の 2 つあり、止まった後に B を外して retry すると、今の設定から選ぶ形では A だけが実績として残る
- **記録済みの値は工程の定義の入力でもプランの引数でもなく、実行の保存先の `values.json` で渡す。** 工程の定義はスプリントに保存されるので、入力を足しても進行中のスプリントに効かない。プランの実行は argv の `{repo}`・`{state_dir}` を置き換えるので、値を argv に載せると `{repo}` を含む識別子が変わる。ハイフンで始まる値を `argparse` がオプションと読む心配もファイルなら無い
- **リリースの識別子は 1 行に限り、その定義を 1 か所に置く。** リリース後テストの記録のコメント（`record_decide.render_verification`）は `version` を JSON のコードブロックより前の表示の行に埋め、読む側（`payload_of`）は行頭の最初の ```` ```json ```` を記録として読む。改行を許すと、識別子の中の行が記録より先に読まれうる
- **出力の足りない承認の理由は、全工程で共通の層が組み立てる。** 同じ欠けは出力を求めるどの工程にもある。承認の時点で断る案は、承認ゲートの待ちが残り conductor が回答し続けるので採らない。承認の後に worker を起動し直す案は、同じ判断の求めが conductor の操作なしに出し直されるので採らない

配布物に承認した PR の変更が含まれることを本番リリース記録で確かめる仕組み、手動の配布で記録を置く手段、その復旧の経路は、この変更に含めず #4586（ https://github.com/devbasex/ai-plugins/issues/4586 ）で扱う。

## 対象範囲

- NDF を使うすべてのリポジトリのデプロイの工程。worker を起動しないのは、保存先の種類が `none` のリポジトリのまとめたリリースの後だけである
- 出力の足りない承認の失敗の理由は、出力を求めるすべての工程に当たる
- 範囲の外: 本番リリース記録による配布物の確かめと手動の配布の記録（#4586）。本番リリース記録の書き込み（`ndf release-steps record`）はリリースブランチの名前からバージョン番号を読むので変えていない

| 代表のリポジトリ | 設定の要点 | 振る舞い |
| --- | --- | --- |
| Web サービス | `version_store.kind` は `none` かタグ・ファイル | `none` はリリースの識別子で終わる。ほかはバージョン番号の形を確かめる |
| モバイルアプリ | `version_store.kind: pubspec` などでバージョン番号を持つ | 確かめは変わらない。出力の足りない承認の理由だけが詳しくなる |
| 業務文書だけ | `version_store.kind: none` | 提出先を指す値などで終わる |

ai-plugins 自身はバージョンの保存先が `plugin` なので、確かめは変わらない。

## 仕様

### 構成要素と責務

| 要素 | 場所 | 責務 |
| --- | --- | --- |
| リリースの識別子の定義 | `contracts/workflow/context.schema.json` の `$defs/distribution_id` | 唯一の定義。`product_version` とリリース後テストの記録の `version` が参照する |
| 結果 JSON の定義 | `contracts/schemas/result.schema.json` | `product_version` は空白だけでない文字列（`$defs/text`）。バージョン番号の形は `$defs/version_number` に 1 か所だけ置く |
| まとめたリリースの回答の入口 | `progress/plan/__init__.py`（`release_answer_errors`・`released_where`・`record_release`）・`progress/plan/cli.py`（`_read_delivered`）・`progress/plan/external.py`（`read_answer`） | `version` をリリースの識別子の定義で確かめ、保存先の種類と届け先を決めて回答に残す |
| 承認の記録 | `progress/gate/release.py`（`_recorded_where`・`_released`・`take_reply`）・`progress/gate/answering.py`（`Answer.release_store`） | 回答をリリース承認ゲートの回答として書くとき、届け先を `delivery` に、種類を出力の事象の `release_store` に書く |
| 届け先の選び方 | `core/production_merge.py`（`on_channel`・`on_production_channel`）・`core/production_delivery.py`（`fits_context`・`production_delivery`）・`domains/release/forms_decide.py`（`reaches`） | 本番に届く行の条件を `on_channel` の 1 か所に置く |
| 記録済みの値の読み書き | `contracts/worker_kinds.py`（`Recorded`・`recorded_of`・`released_of`・`write_released`・`write_values`・`store_kind_of`） | `values.json` の `released_version`・`released_delivery`・`released_store` を読み書きする |
| 工程の進行 | `progress/run_view.py`（`StageAnswers`・`released_answer`）・`progress/run.py`・`progress/invoke.py`（`uses_released`・`prepare_plan`・`plan_path`・`_no_route`）・`core/route/__init__.py`（`rendered`） | 直近のリリース承認ゲートの回答から記録済みの値を読み、`values.json` に書く。承認の後の欠けで止める |
| `ndf answer work` | `progress/gate/entry.py`（`released_outcome`・`_strategy_at`・`_work_handoff`・`work_prompt`・`work_argv`） | worker を起動するかを決め、起動するなら返す値の形を起動指示と `--require-version-number` に入れる |
| `ndf worker run` | `core/worker.py`・`core/worker_outcome.py`（`Checked`・`require_outputs`・`_output_gap`・`outcome_of`） | 必須の `outputs` を結果 JSON の定義と文脈の定義で確かめる。`gate` の結果にも確かめを通った値を引き継ぐ |
| プランの遷移と記録 | `core/plan/steps.py`・`transition_decide.py`・`runner.py`・`records.py` | 承認ゲートで終わる要約に止まったステップの要約を含め、`missing_outputs` を引き継ぐ。出力をステップごとに残す |
| 失敗の理由 | `progress/definition.py`（`required_outputs`・`answered_gap_detail`・`gap_failure_detail`） | 足りない値の名前と判断の求めの要約から理由の文を組む |

### 常に成り立つ条件

- リリースの識別子は、文脈・オーガナイザーの回答・worker の結果・リリース後テストの記録のどこでも、改行を含まない空白だけでない 1 行である。定義は `$defs/distribution_id` の 1 か所だけにある
- バージョン番号の形の正規表現は `result.schema.json` の `$defs/version_number` の 1 か所だけにあり、コードに写さない
- 保存先の種類が `versioned` のリポジトリ、または種類が記録に無く作業の時点の設定がバージョン番号を持つか読めないリポジトリでは、`product_version` はバージョン番号の形でなければ工程は完了しない
- 承認の時点の種類が記録にあれば、デプロイの工程の時点の設定で worker の起動を決め直さない
- 届け先は、承認と一緒に記録した値だけから返す。デプロイの工程の時点の設定から選ばず、固定の値でも埋めない
- 判断を求めたステップ自身の必須の出力の欠けは、ほかのステップが同じキーを返しても、承認の後に続くステップがあっても隠れない
- 承認された判断の求めの値は、その回の判断の求めのものだけである。前の回の値は使わない
- 出力の足りない承認の失敗では、自動の打ち直しをしない

### オーガナイザーの承認と一緒に記録するもの

`ndf lanes release <計画> <リクエスト> --result released --version <識別子>` を打つと、記録する前に次を行う（`_read_delivered`。DB のトランザクションを開く前）。

1. `--version` の値を文脈のスキーマ全体で確かめる（`release_answer_errors`）。改行を含む値と空白だけの値は、記録せずに断る
2. 打った作業ディレクトリのリポジトリの根を `git rev-parse --show-toplevel` で決め、`.ndf/project.json` を `branch_strategy.load` で読む（`command.strategy`）。作業ディレクトリに `.ndf/project.json` があっても根とはみなさない。Git の保存先の位置から逆算しないのは、`git clone --separate-git-dir` の構成で設定を読めなくなるためである
3. 保存先の種類を `none`・`versioned`・`unknown`（設定を読めない）で決める（`released_where`・`store_kind_of`）
4. 種類が `none` のときだけ、`release.deliveries` から本番に届く行を選ぶ（`production_delivery`）。本番に届く行は `channel` が `production` か、省略か `null` の行で、配列の順番は使わない。行が 1 つで、文脈のスキーマ全体に `{"delivery": 行}` を渡して形に合うときだけ記録する。`$defs/delivery` だけを指定しないのは、`branch` が `#/properties/branch` を参照するので、`$defs` だけを残す検証では有効な行が参照の誤りで落ちるためである
5. 0 か 2 つ以上・形に合わない・設定を読めないときは、届け先を記録しない。回答そのものは断らない（断ると、出した後のスプリントが承認ゲートの待ちに残る）

外のオーガナイザー（Factory のランナーなど）が書く回答のファイル `answers/<id>.json` も、`read_answer` が読む時点で同じ `release_answer_errors` を当てる。`released` で `version` が合わなければ回答のファイルの誤りとして断り、承認ゲートの待ちは残る。回答のファイルを書き直せば、次に読んだときに取り込まれる。回答のファイルには `store` と `delivery` を書ける。書かなければ種類は記録に無いものになる。

レーンが回答をリリース承認ゲートの回答として書くとき（`take_reply`）は、設定を読まない。`_recorded_where` が回答の `store` と `delivery` を 1 回だけ解釈する。種類が 3 語のどれでもなければ記録に無いものとし、届け先は種類が `none` で本番に届く行で文脈の形に合うときだけ使う。承認の回答の事象の直後に、`product_version`・`release_pr`・（あれば）`delivery` を `outputs` に、種類を `release_store` に持つ出力の事象を、同じトランザクションで書く。`release_store` を `outputs` でなく payload の別のキーに置くのは、後の工程へ渡す値ではなく承認を記録した時点の事実だからである。

### 記録済みの値の読み方

工程の進行は、プランを流す直前に、直近のリリース承認ゲートへの回答から記録済みの値を読む（`released_answer`）。

1. 工程の事象 `answered` のうち、`kind` が `listen` で `gate` がリリース承認ゲートの名前（`model.release_gate()`、文字列は `release`）の最後の 1 件を選ぶ。worker の判断の求めへの回答は `kind` が `plan` で `gate` を持たないので選ばれない。新しくても読み飛ばす
2. その回答の `answer` が `approved` で、`by` が `organizer` であることを確かめる
3. 回答の次の事象が出力の事象（`stages.OUTPUTS_EVENT`）なら、その `outputs` の `product_version`・`delivery` と `release_store` を読む
4. 2 か 3 が成り立たないか、`product_version` が空白だけなら、記録済みの値は無い

利用者や MVV 判定が承認した回は、文脈に前の `product_version` が残っていても記録済みの値は無いので、worker を起動する。

**履歴は 1 周しかたどらない。** `StageAnswers` は工程の事象を新しい順にたどる走査を 1 本だけ持ち、たどった事象ごとに「最新の承認の回答」（`approved_seen` が使う）と「リリース承認ゲートへの最後の回答とその次の事象」の両方を照らして覚える。`approved()` の後に `release_gate()` を呼ぶと、止まった所から続ける。

**読むのは、記録済みの値を使うプランだけである。** プランが流す作業に、作業の定義（`kinds.json`）の `outputs` が `product_version` を持つ作業（今は `release`）があるときだけ読む（`uses_released`）。工程の進行は `released_answer` を `functools.cache` で包んだ関数を `StageRun.released` に入れて渡し、判定が真のときだけ呼ぶ。判定は `values.json` に残さず、プランを組むたびに今のテンプレートから求める。残すと、NDF を更新して同じ名前のプランに `release` が加わったとき、前の NDF の判定が使われて値が渡らない。

| 場面 | 判定と書き込み |
| --- | --- |
| 試行の始め（`prepare_plan`） | 入力の検査で回した作業の一覧で判定し、`PreparedPlan.uses_released` に残す。真なら `write_values` の最初の書き込みで記録済みの値まで書く |
| 同じ試行の初回の `plan_path` | `prepare_plan` の判定を使い、作業の一覧を取り直さない |
| 保存済みの `values.json` を使い回す回の `plan_path` | `route.rendered` でプランと作業の一覧を一緒に得て判定する（展開は増えない）。真で、読んだ値が `values.json` の値と違うときだけ `write_released` で 3 つのキーを書き直す。値が無ければキーを消す |

### worker の起動（`ndf answer work`）

作業の定義の `outputs` が `product_version` を持つ作業（今は `release`）だけが対象である。ほかの作業では設定も記録済みの値も読まず、今までどおり起動する。作業の名前を文字列で比べない。

`_work_handoff` は、`values.json` の記録済みの値（`released_of`）を読む。承認の時点の種類が記録に無いか記録済みの値が無いときだけ、作業場所のリポジトリの根の設定を読む（`_strategy_at`。読めなければ `None`）。そのうえで次の表で決める（`released_outcome`）。

| 記録済みの値 | 承認の時点の種類 | 記録した届け先 | 結果 |
| --- | --- | --- | --- |
| ある | `none` | ある | worker を起動せず `ok`。`outputs` は `product_version`（記録した値のまま）と `delivery`（記録した行のまま）。作業の時点で設定を読めなくても同じ |
| ある | `none` | 無い | worker を起動せず `stopped`（`precondition`） |
| ある | `unknown` | — | worker を起動せず `stopped`（`precondition`） |
| ある | `versioned` | — | worker を起動し、バージョン番号の形を確かめる |
| ある | 記録に無い | — | 作業の時点の種類が `none` なら `stopped`（`precondition`）。それ以外か読めなければ worker を起動し、形を確かめる |
| 無い | — | — | 作業の時点の種類が `none` なら worker を起動し、形を確かめない。それ以外か読めなければ形を確かめる |

`mvv_gate` は `release-mvv` が記録済みなので、`ndf answer work` は返さない。工程の進行が全ステップの出力を集めて `delivery`・`product_version`・`mvv_gate` の 3 つがそろうかを確かめる。

worker を起動するときは、起動指示と `work_argv` の値の形を、`_work_handoff` が 1 回だけ決めた `version_number` から得る。真なら起動指示に「product_version はバージョン番号（例 1.2.3）」の 1 行を書き、`ndf worker run` に `--require-version-number` を渡す。偽なら「product_version は出したものを指す空白だけでない 1 行の文字列（例 main abc1234）。バージョン番号を作らない」と書く（`VERSION_NUMBER_LINE`・`DISTRIBUTION_ID_LINE`）。

`stopped` の理由には、届け先が記録から確かめられないこと、設定を直して retry しても終わらないこと、`ndf progress finish <ID> デプロイ --output <JSONファイル> --reason <理由>` で手で終える次の操作を書く。利用者が書く JSON の見本（`manual_finish_example`）と置き換える所の説明（`MANUAL_FINISH_FILL`）が正本で、どちらも `progress/gate/entry.py` にある。見本は工程の出力の規約（`stage-io.schema.json` の `stage-out`）が必須とする `summary`・`steps` と、`outputs` の 3 つを持つ。`product_version` には記録済みの値が入り、`delivery` と `mvv_gate` は値全体を置き換える印の文字列にする。届け先の一部の項目だけを固定値で示すと、利用者がその値を残して実際と違う属性が実績に残りうるためである。

### worker の結果の確かめ（`ndf worker run`）

`require_outputs` は、作業の必須の `outputs` を結果 JSON の `$defs/outputs` と文脈の定義（`contracts.internal.CONTEXT`）の両方で確かめる。`--require-version-number` を受けたときだけ、`product_version` を `$defs/version_number` でも確かめる。

| 結果ファイルの `status` | 返す結果 |
| --- | --- |
| `ok` で欠けあり | `status: stopped`、停止理由 `incomplete`。欠けた名前（例 `product_version`）を `gaps` に示す |
| `ok` で欠けなし | 確かめた `outputs` を載せる |
| `gate` | `gate` のまま。確かめを通った値だけを `outputs` に載せ、通らなかった名前を `metrics.missing_outputs` に載せる |

`gate` でも文脈の定義で確かめるのは、形の違う値（`versioned` の無い `delivery` など）を載せると、工程の進行が承認を聞く前に `bad_report` にするためである。先に外して `missing_outputs` に入れれば、判断の求めは届き、承認の後に欠けとして失敗する。

### プランの出力の記録と遷移

プランは実行の保存先の `outputs.json` に、ステップが返した出力を、どのステップが返したかと併せて書いた順に残す。結果の `outputs` は要素を先頭から重ねて毎回組み直す（同じキーは後の要素が勝つ）。

```json
{"entries": [{"step": "first", "outputs": {"pr": 1}}, {"step": "second", "outputs": {"product_version": "main abc1234"}}]}
```

| 場面 | `outputs.json` の扱い |
| --- | --- |
| `ok` か `stopped` のステップが出力を返した | 同じステップの前の要素から今回のキーを外し（空になった要素は消す）、今回の出力を末尾に足す |
| `gate` を返した（出力が無い場合を含む） | そのステップの要素をすべて外し、今回の出力があれば末尾に足す |
| `entries` を持たない古い形 | ステップの分からない要素 `{"step": null, ...}` 1 つとして先頭に置き、外さない |

ステップとキーの組ごとに最新の値だけを残すので、記録の大きさはステップの数とキーの数で上限が決まる。キーごとに最後の書き手だけを持つ形は、後のステップの値を外すと先のステップの値まで失うので採らない。

`gate` を返したステップの `metrics.missing_outputs` は `step-missing.json` に残す（`_keep_step_missing`。`gate` 以外の結果では読みも書きもしない）。承認ゲートで終わった回の結果は、止まったステップの `missing_outputs` を `metrics.missing_outputs` に載せ（`final_result`）、要約を `ステップ {id} が承認ゲートで止まった: {worker の要約}` にする（`_after_gate`）。

### 出力の足りない承認の失敗

承認の回答を受けると、工程の進行（`invoke._no_route`）は、承認の後に続くステップがあるかを見る前に、回答を待っていた結果の `metrics.missing_outputs` を見る。空でなければ、続きがあっても続けずに `bad_report` で止める。続けた後のステップが同じキーを返すと欠けが隠れるためである。空で続きが無ければ、待っていた結果の要約を `metrics.waited` に入れて `ok` にする。

その後の出力の確かめは 3 段で行い、失敗した段の名前を数える。

| 失敗した段 | 足りない値の名前 |
| --- | --- |
| 判断を求めたステップ自身の確かめ（`_no_route`） | そのステップの `metrics.missing_outputs` |
| ステップの出力の確かめ（`_step_draft`。`release` は規約 `release-result`） | 規約が必須とするキー（`required_outputs`）のうち、そのステップの結果に無いもの。`release-result` なら `product_version` だけ |
| 全ステップを集めた工程の出力の確かめ（`_finish_stage`） | 工程の `outputs` のキーのうち、どのステップの出力にも無いもの |

理由の文は `answered_gap_detail` が組む。

```text
足りない出力: {名前の並び}。判断を求めたステップの要約: {要約}。次の操作: 足りない値を返せる状態にしてから、ndf progress retry <ID> <工程> --reason <理由> で工程を打ち直す
```

停止理由の種類は `bad_report` のままで、自動の打ち直しはしない。`metrics.waited` の無い結果（承認の回答を経ない `ok`）は、今までの文のままである。

## データ・設定

### 読む設定

| キー | 使う所 |
| --- | --- |
| `branch_strategy.release.version_store.kind` | 保存先の種類（`none` か、それ以外か） |
| `branch_strategy.release.deliveries[]`（`channel`） | 本番に届く届け先の行。種類が `none` のときだけ読む |

### スキーマ

| 場所 | 定義 |
| --- | --- |
| `context.schema.json` の `$defs/distribution_id` | `{"type": "string", "allOf": [{"pattern": "\\S"}, {"not": {"pattern": "[\\r\\n]"}}]}`。`^…$` の 1 つの正規表現にしないのは、Python の `$` が末尾の改行の前にも当たり、末尾の改行を断れないためである |
| `context.schema.json` の `product_version` | `{"$ref": "#/$defs/distribution_id"}` |
| `release-verification.schema.json` の `version` | `{"$ref": "workflow/context.schema.json#/$defs/distribution_id"}`。検証器（`contracts._shipped_validator`）は同梱のスキーマを引く登録簿を持ち、ほかのファイルへの参照を解く |
| `result.schema.json` の `outputs.product_version` | `{"$ref": "#/$defs/text"}`（空白だけでない文字列） |
| `result.schema.json` の `$defs/version_number` | バージョン番号の形（`^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$`） |

### 実行の保存先の `values.json`

差し込みの値とは別に、任意のキー（`RELEASED_KEYS`）で記録済みの値を持つ。形の定義 `VALUES_SHAPE` には足さないので、キーを持たない `values.json`（単発のプラン・古い NDF が書いたもの）もそのまま読める。

| キー | 値 | 無いとき |
| --- | --- | --- |
| `released_version` | 空白だけでない文字列 | 記録済みの値は無い |
| `released_delivery` | 届け先のオブジェクト | 届け先は記録されていない |
| `released_store` | `none`・`versioned`・`unknown` | 種類は記録に無い（3 語のどれでもない値も同じ） |

書くときは `write_values` と同じく、保存先のディレクトリを記述子で開き、そこを起点に所有者だけが書ける権限で書く。`write_released` は既にある `values.json` の 3 つのキーだけを書き換え、`values.json` が無ければ `OSError` を上げる（呼び手が `precondition` の失敗にする）。

### リリース承認ゲートの出力の事象

| キー | 値 |
| --- | --- |
| `outputs.product_version` | オーガナイザーの `--version` の値 |
| `outputs.release_pr` | `--release` の値（あれば） |
| `outputs.delivery` | 記録した届け先の行（種類が `none` で 1 行に決まったときだけ） |
| `release_store` | 保存先の種類（この変更の後に書いた回答だけ） |

### プランの実行の保存先

| ファイル | 中身 |
| --- | --- |
| `outputs.json` | `{"entries": [{"step": <id か null>, "outputs": {...}}, ...]}` |
| `step-missing.json` | ステップの id → `gate` のときに確かめを通らなかった必須の出力の名前の並び |

## 外部連携

| 相手 | やり取り |
| --- | --- |
| 外のオーガナイザー（Factory のランナーなど） | 回答のファイル `answers/<id>.json` の `version`・`store`・`delivery` を読む。`version` は `ndf lanes release` と同じ規則で確かめる |
| `ndf worker run` | `ndf answer work` から引数 `--require-version-number` を受ける |
| リリース後テストの記録と課題を閉じる判定 | `ndf release-steps verification-record` と `ndf records close-check` は、バージョン番号の形でない `version` も受け付ける。`verification-record` はリリース記録の `version` と一致する記録だけを書く |

## エラー処理

| 場面 | 扱い | 利用者に見える結果 | 再実行 |
| --- | --- | --- | --- |
| `ndf lanes release --version` が改行を含む・空白だけ | 記録せずに断る | 誤りの文に `product_version` の形 | 1 行の値で打ち直す |
| 外の回答のファイルの `version` が改行を含む・空白だけ | 回答のファイルの誤りとして断る | ファイルの名前と、1 行の識別子で書き直すこと。承認ゲートの待ちは残る | 回答のファイルを書き直す |
| 承認の記録の時点で設定を読めない・届け先が 1 つに決まらない | 届け先を記録しない。回答は記録する | デプロイの工程で `stopped`（`precondition`） | retry しても終わらない。実際の届け先を確かめ `ndf progress finish` で終える |
| `ndf answer work` で作業の時点の設定を読めない | バージョン番号を持つ扱い | 無し。worker が起動し、形を確かめる | — |
| `ok` の結果の `product_version` がバージョン番号の形でない（確かめるとき）・改行を含む | 欠けにする | `stopped`（`incomplete`）、欠けは `product_version` | retry で worker を起動し直せる |
| `gate` の結果の必須の値が無い・形が違う | `gate` のまま `metrics.missing_outputs` に名前を載せる | 承認の後に `bad_report` で失敗し、名前と要約と次の操作を示す | conductor が retry を打つまで起動し直さない |
| `values.json` が無いのに `ndf answer work` を打った | 止める | `stopped`（`precondition`） | retry で打ち直せる |

## セキュリティ

- 記録済みの値の中身はログに載せない。worker を起動せずに終えたことはログの `info` と結果 JSON の要約に出し、値は結果 JSON の `outputs` にだけ載る
- `values.json` は所有者だけが書ける権限で書く
- `released_store` が 3 語のどれでもない値や、`released_delivery` がオブジェクトでない値は、記録されていないものとして扱う。手で書き換えた値で worker の起動の判断を誤らない
- 外のオーガナイザーが `channel: base` の行を `delivery` に書いても、本番の配布の実績にしない

## 運用

- **届け先が記録から決まらずに止まったとき**: 理由の文の見本に沿って、実際の届け先と `release-mvv` が記録した `mvv_gate`（`ndf progress show <ID>` の `metrics.history` で `payload.step` が `release-mvv` の最後の記録の `payload.report.outputs.mvv_gate`）を JSON に書き、`ndf progress finish <ID> デプロイ --output <JSONファイル> --reason <理由>` で終える。`finish` は書く前に `ndf progress check` と同じ確かめをし、合わなければ何も書かない
- **出力の足りない承認で失敗したとき**: 理由の文の「足りない出力」を返せる状態にしてから、`ndf progress retry` で工程を打ち直す
- **手動の配布**: 機械が読む本番リリース記録を置く手段が無いので、リリース後テストの記録と課題を閉じる判定で止まる（変更の前と同じ。#4586 で扱う）
- **配布物に含まれない変更**: まとめたリリースの後に `release-merge` が初めてマージした変更も、変更の前と同じく完了になる。動作の確かめはリリース後テストで行い、デプロイの工程の完了を確かめた根拠として扱わない
- **Git の起動の回数**: `ndf lanes release` の `released` の回答 1 回ごとに `git rev-parse --show-toplevel` が 1 回と設定の読み込みが 1 回増える。`ndf answer work` は、承認の時点の種類が記録に無い回の `release` の作業でだけ 1 回ずつ増える。リポジトリの根は `Git.toplevel` の 1 か所で求め、`progress/settings_roots.py` の `roots_once` の範囲の中では同じ作業場所の根を 1 回だけ求める
- 手順書の説明は `release` の `SKILL.md` と `references/release-steps.md`、`release-verification`・`progress-tracking` の `SKILL.md`、`development-workflow` の `references/parallel.md` にある

### 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 問題が起きたリポジトリで、手作業なしにデプロイの工程が終わるか | そのリポジトリの設定と進行中のスプリントはこのリポジトリから読めない。リリース後テストで利用者が確かめる |

## テスト観点

- 保存先の種類が `none` で、オーガナイザーが `main abc1234` を記録して承認した後、デプロイの工程が worker を 1 回も起動せずに完了し、文脈の `product_version` が同じ値で、`delivery` と `release-mvv` の `mvv_gate` も文脈にある
- オーガナイザーが `--candidate` や `rev {repo} {state_dir}` を記録しても、`product_version` が同じ文字列で、ステップを打つ直前の argv には値が載らない
- 保存先の種類が `none` で、まとめたリリースを使わずに worker が短いハッシュを返すと、工程が完了してその値が `product_version` になる
- バージョン番号の形でない `product_version` で、リリース後テストと体裁レビューの入力が組める。本番リリース記録を偽の GitHub の PR に置いた状態から、`ndf release-steps verification-record` で記録し、投稿されたコメントから取り出した記録を `ndf records close-check --verification <ファイル> --require` に渡すと、閉じてよいという判定になる
- 空文字列と空白だけの `product_version` の結果は形の誤りになる
- 出力の無い判断の求めに `approved` と回答すると、失敗の理由に `product_version` と worker の要約が入る。その後 `ndf progress next` を打っても worker は起動されず、同じ失敗を返す
- バージョン番号を持つ設定と、設定を読めないときに、worker が `ndf v11.0.0` を返すと工程は完了しない。`gate` の結果の値でも同じ
- 値のそろった判断の求め（`gate` で `delivery`・`product_version` を持つ）を `ndf worker run` に通した後の `approved` で、工程が完了する
- 値の付いた判断の求めを却下して retry し、次の回の判断の求めに値が無いか形が違うと、`approved` の後も失敗する（前の回の値を使わない）
- 先のステップと判断を求めるステップが同じキーを返すプランで、後者を値なしで打ち直すと先のステップの値が残り、`approved` と回答すると足りない名前を示して失敗する。判断を求めるステップの後にステップがあっても、後のステップを打たずに失敗する
- 同じステップが同じキーを 3 回返しても、`outputs.json` の要素は増えない。古い形の `outputs.json` の値も残る
- 承認を記録するとき、配列の先頭が `channel: base` の行で次が本番の行なら、本番の行を記録する。`branch: main` を書いた有効な本番の行もそのまま記録する。本番の行が 0 か 2 つ以上か `target` が空文字列なら届け先は記録されず、承認の回答は書かれ、デプロイの工程は worker を起動せずに `stopped`（`precondition`）で止まる
- 承認を記録した時点で本番の届け先が 2 つあり、止まった後に設定から 1 つを外して retry しても、工程は `stopped` のままで `delivery` は文脈に書かれない
- 承認の時点の種類が `none` で届け先が記録された後は、作業の時点で設定を読めなくても worker は起動されず完了する。承認の時点の種類が `unknown` なら worker は起動されず `stopped` になる
- `gate` の結果の `delivery` が文脈の必須キーを欠くと、結果は `gate` のままで `delivery` を `outputs` に載せず `metrics.missing_outputs` に載せる
- 改行を含むリリースの識別子は、`ndf lanes release --version` と外の回答のファイルで断られ、`ndf worker run` の `ok` では欠けになり、リリース後テストの記録（末尾の改行だけのものを含む）では形の誤りになる。1 行の記録は `render_verification` で描いて `verifications` で読み直すと `version` と `issues` が元と同じになる
- 利用者か MVV 判定が承認した回では、文脈に前の `product_version` が残っていても worker を起動する
- 工程の最新の承認とリリース承認ゲートへの回答を探しても、たどる事象の数は工程の事象の数を超えない。工程の初回の `plan_path` は作業の一覧を取り直さない
- 前の回の `values.json` が残っていても、retry で記録済みの値が入る。worker の判断の求めへの回答が後にあっても、リリース承認ゲートへの回答を読む

テストは `plugins/ndf/tests/` の `progress/`（`test_run_answers.py`・`test_run_answered_gaps.py`・`test_invoke_values.py`・`test_invoke_gate_outputs.py`・`gate/test_gate_work.py`・`gate/test_gate_release.py` など）、`core/`（`test_worker_outcome.py`・`plan/test_records_entries.py`・`plan/test_records_step_keyed.py`・`plan/test_transition.py`）、`contracts/test_result.py`、`domains/release/`（`test_release_forms.py`・`test_release_record.py`）、`route/test_templates.py` にある。

## 関連リンク

- Issue: https://github.com/devbasex/ai-plugins/issues/4513
- PR: https://github.com/devbasex/ai-plugins/pull/4609
- 配布物の確かめと手動の配布の記録（範囲の外）: https://github.com/devbasex/ai-plugins/issues/4586
- [本番のリリース記録](ndf-production-release-record.md)
- [リリース後テストの記録と課題を閉じる判定](ndf-post-release-records-close-gate-and-handoff-sections.md)
- [工程の出力の確かめ](ndf-progress-check-stage-output.md)
- [並列の計画のレーンの終わりとリリースのまとめ](ndf-parallel-lane-end-and-verify-close.md)
- 手順: [release](../../plugins/ndf/skills/release/SKILL.md)・[release-steps](../../plugins/ndf/skills/release/references/release-steps.md)・[progress-tracking](../../plugins/ndf/skills/progress-tracking/SKILL.md)・[parallel](../../plugins/ndf/skills/development-workflow/references/parallel.md)
