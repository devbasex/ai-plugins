# #4513 の詳細設計: デプロイの工程をバージョン番号を持たないリポジトリでも終える

設計文書 [issue-4513-design.md](./issue-4513-design.md) の構成要素と決定 1〜6 を、ファイルと関数の粒度へ下ろした文書である。受け入れ条件の中身は要求仕様 [issue-4513-requirements.md](./issue-4513-requirements.md) にある。書き方は `design` の `references/detailed-design.md` に従う。

この文書で使う言葉のうち、設計文書の「この文書で使う言葉」に無いもの、または取り違えやすいものを次に置く。ほかの言葉（オーガナイザー・まとめたリリース・conductor・MVV 判定など）は、設計文書の表にある。

| 言葉 | 意味 |
| --- | --- |
| 記録済みの値 | まとめたリリースで、オーガナイザーが `ndf lanes release --version` に渡し、リリース承認ゲートへの回答と一緒に文脈の `product_version` に書かれた値。`values.json` には `released_version` として残す。「承認済みの配布識別子」も同じものを指す |
| 工程・試行・プラン・ステップ | 工程（デプロイなど）は retry の単位である。試行は、工程の retry から次の retry までの 1 回分を指す。1 つの試行の中で、プランは何回か流れる（承認の回答の後・自動の打ち直し）。プランはステップを順に流す |
| 判断の求め | worker が `status: gate` の結果を返して、conductor の判断を待つこと |

**別ファイルにした理由**: 別ファイルにする条件は「新設する関数が 2 つ以上」か「シグネチャを変える関数がある」で、この設計は両方に当たる。新設は 10 を超える（`required_outputs`・`released_answer`・`released_outcome` など）。シグネチャの変更は 10（`require_outputs`・`_add_outputs`・`work_argv`・`work_prompt`・`_work_handoff`・`write_values` など）である。条件は `requirements-design` の `document-roles.md` の「詳細設計を別ファイルにする条件」にある。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設の関数を持つファイル。新設するファイルは無い）:

```text
plugins/ndf/
├── contracts/
│   ├── schemas/{result.schema.json, release-verification.schema.json}
│   ├── worker/kinds.json
│   └── workflow/context.schema.json
├── src/ndf/
│   ├── contracts/worker_kinds.py *
│   ├── progress/
│   │   ├── definition.py *
│   │   ├── invoke.py *
│   │   ├── run.py *
│   │   └── gate/entry.py *
│   ├── core/
│   │   ├── worker.py
│   │   ├── worker_outcome.py
│   │   └── plan/{steps.py, transition_decide.py, runner.py, records.py *}
│   └── domains/release/forms_decide.py *
├── skills/
│   ├── release/{SKILL.md, references/release-steps.md}
│   ├── release-verification/SKILL.md
│   ├── progress-tracking/SKILL.md
│   └── development-workflow/references/parallel.md
└── tests/
    ├── contracts/{test_result.py, test_worker_kinds_shape.py}
    ├── core/{test_worker_outcome.py, plan/test_steps.py, plan/test_transition.py, plan/test_runner.py}
    ├── domains/release/{test_release_forms.py, test_release_record.py}
    └── progress/{test_definition.py, test_invoke.py, test_invoke_values.py, test_run.py, gate/test_gate_work.py, gate/test_gate_release.py}
```

依存の向き: 工程の進行（`progress/run.py`・`invoke.py`）が、定義の読み手（`definition.py`）と値のファイル（`contracts/worker_kinds.py`）を読む。`ndf answer work`（`gate/entry.py`）は値のファイル・ブランチ戦略（`core/branch_strategy.py`）・届け先の選び方（`domains/release/forms_decide.py`）を読み、`ndf worker run`（`core/worker.py`）へ引数で渡す。プラン（`core/plan/`）は `ndf worker run` の結果 JSON を標準出力から読むだけで、工程の進行を読まない。今ある依存の向きは変えない。足す依存は `entry.py` → `forms_decide.py` と `entry.py` → `branch_strategy.py` の 2 本で、どちらも循環を作らない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/schemas/result.schema.json` | 変更 | 結果 JSON の形 | — | `$defs/outputs`・`$defs/version_number` | — |
| `contracts/schemas/release-verification.schema.json` | 変更 | リリース後テストの記録の形 | — | `version` | — |
| `contracts/worker/kinds.json` | 変更 | 作業 `release` の説明 | — | `release` | — |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の `product_version` の説明 | — | `product_version` | — |
| `src/ndf/contracts/worker_kinds.py` | 変更 | `values.json` の形と読み書き | — | モジュール | `write_released`（新設）・`released_of`（新設）・`write_values`（変更） |
| `src/ndf/progress/definition.py` | 変更 | 工程の定義と規約の読み手 | `contracts` | モジュール | `required_outputs`（新設） |
| `src/ndf/progress/plan/__init__.py` | 変更 | まとめたリリースの回答の記録 | `definition` | モジュール | `record_release`（変更） |
| `src/ndf/progress/run.py` | 変更 | 工程の進行 | `invoke`・`definition`・`stages` | モジュール | `released_answer`（新設）・`_prepare`・`_run_step`・`_step_draft`・`_finish_stage`（変更） |
| `src/ndf/progress/invoke.py` | 変更 | ステップの呼び出し | `worker_kinds`・`plan` | `StageRun`・`PreparedPlan`・モジュール | `StageRun.released`（新設の欄）・`PreparedPlan.released`（新設の欄）・`uses_released`（新設）・`answered_gap_detail`（新設）・`prepare_plan`・`_prepared`・`plan_path`・`_no_route`（変更） |
| `src/ndf/core/route/__init__.py` | 変更 | テンプレートからプランを組む | — | モジュール | `rendered`・`_built`（新設）・`render`（変更） |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf answer work` | `worker_kinds`・`branch_strategy`・`forms_decide` | `Released`（新設）・`WorkHandoff`・モジュール | `released_outcome`（新設）・`_strategy_at`（新設）・`WorkHandoff.version_number`（新設の欄）・`_cmd_work`・`_work_handoff`・`work_prompt`・`work_argv`・`_outputs_of`（変更） |
| `src/ndf/domains/release/forms_decide.py` | 変更 | 届け先の選び方 | — | モジュール | `reaches`（新設）・`deliveries_of`（変更） |
| `src/ndf/core/worker.py` | 変更 | `ndf worker run` | `worker_outcome` | モジュール | `_add_run`・`_cmd_run`（引数を 1 つ足すだけ） |
| `src/ndf/core/worker_outcome.py` | 変更 | worker の結果の確かめ | `contracts` | `Checked`（新設）・モジュール | `require_outputs`・`_output_gap`・`outcome_of`（変更） |
| `src/ndf/core/plan/steps.py` | 変更 | ステップの結果の読み取り | — | モジュール | `outcome_of`（変更） |
| `src/ndf/core/plan/transition_decide.py` | 変更 | プランの遷移 | — | `Outcome` | `Outcome.summary`（新設の欄）・`_after_gate`（変更） |
| `src/ndf/core/plan/runner.py` | 変更 | プランの実行 | `records`・`transition_decide` | モジュール | `run_step`・`_perform`・`final_result`（変更） |
| `src/ndf/core/plan/records.py` | 変更 | プランの記録のファイル | — | モジュール | `_add_outputs`・`recorded_outputs`（変更）・`_stored_entries`・`_keep_step_missing`・`recorded_step_missing`（新設）・`_stored_outputs`（削除） |
| `skills/` の 5 ファイル | 変更 | 手順書の説明 | — | — | — |

`forms_decide.py`・`progress/plan/__init__.py` と、テストの `test_plan.py`・`test_invoke_values.py`・`test_worker_kinds_shape.py`・`test_steps.py`・`test_release_forms.py`・`test_release_record.py`・`test_definition.py` も、設計文書の「設計の結果」の触るファイルに入れた（届け先の条件を 1 か所にするためと、変える関数の既存のテストがそこにあるため。`test_release_record.py` は受け入れ条件 4 の記録の経路を確かめる）。

## 結果 JSON とリリース後テストの記録の定義

満たす決定: 決定 1、決定 5、受け入れ条件 3・4・5・8

関数を持たない。変える中身だけを書く。

| 場所 | 今 | 直した後 |
| --- | --- | --- |
| `result.schema.json` の `$defs/outputs` の `product_version` | バージョン番号の正規表現を直に書く | `{"$ref": "#/$defs/text"}`（空白だけでない文字列。`$defs/text` は今ある） |
| `result.schema.json` の `$defs/version_number`（新設） | — | 今の正規表現をそのまま移した `{"type": "string", "pattern": ...}`。バージョン番号の形の唯一の保存先 |
| `release-verification.schema.json` の `version` | 先頭の 0 を断るバージョン番号の正規表現 | `{"type": "string", "allOf": [{"pattern": "\\S"}, {"not": {"pattern": "[\\r\\n]"}}]}`（改行を含まない、空白だけでない 1 行。理由は設計文書の決定 5）。説明の文も「出したものを指す 1 行の値」に直す |
| `context.schema.json` の `product_version` | `{"type": "string", "minLength": 1}`。説明に手動の 3 行の `## リリース記録` の読み方を書く | `{"type": "string", "allOf": [{"pattern": "\\S"}, {"not": {"pattern": "[\\r\\n]"}}]}`（配布識別子の共通の定義。改行を含まない、空白だけでない 1 行）。説明に、バージョン番号を持たないリポジトリでは出したものを指す値であると足す |
| `kinds.json` の `release` | — | 説明に、返す値の形は起動指示の 1 行に従うと足す |

配布識別子を 1 行に限る制約は、文脈の `product_version` の定義の 1 か所に置く。値が入る入口は、どれもこの定義で確かめる。

| 入口 | 確かめ方 | 合わないとき |
| --- | --- | --- |
| オーガナイザーの記録（`ndf lanes release --version`、`progress/plan/__init__.py` の `record_release`） | `released` の回答の `version` を `{"product_version": version}` として文脈のスキーマ全体（`definition.CONTEXT`）で確かめる | 記録せずに `model.Refused` で断る。誤りの文に `product_version` の形が出る |
| リリース承認ゲートへの回答（`gate/answering.py` の `_outputs`） | 今のまま `invoke.value_errors` で確かめる | 今のまま断る |
| worker の結果（`ndf worker run`） | 下の「worker の結果の確かめ」の文脈の形の確かめ | `ok` では欠け、`gate` では `missing` |

`ndf worker run` がバージョン番号の形を確かめるときは、`$defs/version_number` を参照して確かめる（下の「worker の結果の確かめ」）。正規表現をコードに写さない。

リリース後テストの記録の `version` を 1 行に限ったことは、記録のコメントを `record_decide.render_verification` で描き、`record_decide.verifications` で読み直すと `version` と `issues` が元と同じになることで確かめる（`tests/domains/release/test_release_record.py`。設計文書のテスト設計の決定 5 の行）。

## 値のファイル（`values.json`）

満たす決定: 決定 3、受け入れ条件 1・2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write_released` | `src/ndf/contracts/worker_kinds.py` | 新設 | `write_released(state_dir: Path, value: Optional[str]) -> None` | 既にある `values.json` のキー `released_version` だけを書き換える |
| `released_of` | `src/ndf/contracts/worker_kinds.py` | 新設 | `released_of(held: Mapping[str, Any]) -> Optional[str]` | 読んだ `values.json` から、空白だけでない `released_version` を返す |

- `write_released`
  - 事前条件: `state_dir` に `write_values` が書いた `values.json` がある
  - 事後条件: `values.json` の `released_version` が `value`（`None` ならキーが無い）で、ほかのキーは元のまま
  - 副作用: `values.json` を書き直す。書き方は `write_values` と同じで、保存先のディレクトリをファイル記述子で開き、そこを起点に所有者だけが書ける権限で書く
- `released_version` は、形の定義 `VALUES_SHAPE` には足さない。`_has_shape` は決まったキーだけを見て、ほかのキーを残すので、キーを持たない `values.json`（直す前の NDF が書いたもの・単発のプラン）もそのまま読める

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_released` | `values.json` が無い・形が違う | 呼ぶ順の誤り | `read_values` が `None` を返す | 止める（`OSError` を上げる）。`plan_path` の呼び出し元（`invoke` のプランを流す関数）が、今の `OSError` と同じく `precondition` の失敗に変える | 工程の失敗（`precondition`） | 書かない。retry で打ち直せる |
| `released_of` | 値が文字列でない・空白だけ | 手で書き換えた | 関数の中 | 既定値で続ける（`None`） | 無し。worker を起動する経路になる | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `write_released` | 値を書くとほかのキーが残る | `None` でキーが消える | `values.json` が無いと上がる |
| `released_of` | `main abc1234`・`--candidate`・`rev {repo}` をそのまま返す | 空文字・空白だけは `None` | キーが無い・数値は `None` |

## 工程の進行: 記録済みの値の読み方

満たす決定: 決定 2、決定 3、受け入れ条件 1・2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `released_answer` | `src/ndf/progress/run.py` | 新設 | `released_answer(v: View, stage: str) -> Optional[str]` | 工程の事象を新しい順にたどり、リリース承認ゲートへの最後の回答で止まる。その回答がオーガナイザーの承認で、値を書いていれば `product_version` を返す |
| `StageRun.released` | `src/ndf/progress/invoke.py` | 新設 | `released: Callable[[], Optional[str]] = _no_released`（データクラスの欄。既定は `None` を返す関数） | 記録済みの値を読む関数を、ステップの呼び出しへ渡す。呼ばれるまで履歴をたどらない |
| `_run_step`・`_prepare` | `src/ndf/progress/run.py` | 変更 | 今のまま | `approved_seen` は今のまま呼ぶ。ステップがプランのとき（`invoke.kind_of(step) == invoke.PLAN`）は、`released_answer(v, stage)` を初回の呼び出しで 1 回だけ打つ関数（`functools.cache` で包む）を `StageRun.released` に入れる。ここでは呼ばない |
| `PreparedPlan` | `src/ndf/progress/invoke.py` | 変更 | 欄 `released: Optional[str]` を足す | 組んだか読んだ `values.json` が今持つ `released_version` を渡す |
| `prepare_plan` | `src/ndf/progress/invoke.py` | 変更 | 今のまま | 入力の検査で回している作業の一覧（`route.kinds_of`）を `uses_released` に渡して判定する。使うプランのときだけ `run.released()` を呼び、`write_values` に渡して最初の書き込みで `released_version` まで書く。使わないプランでは呼ばない |
| `_prepared` | `src/ndf/progress/invoke.py` | 変更 | 今のまま | 使い回す `values.json` から `released_of` で読んだ値を `PreparedPlan.released` に入れる。判定はしない |
| `plan_path` | `src/ndf/progress/invoke.py` | 変更 | 今のまま | `route.render` の代わりに `route.rendered` でプランを組み、一緒に返る作業の一覧を `uses_released` に渡して判定する。`uses_released` が真のときだけ `run.released()` を呼ぶ。その値が `PreparedPlan.released` と違うときだけ、プランを書く前に `write_released(work_dir, 値)` を打つ。偽なら `run.released()` を呼ばない |
| `_built` | `src/ndf/core/route/__init__.py` | 新設 | `_built(t: Template, given: Mapping[str, Value], local_tests: bool) -> tuple[dict[str, Any], dict[str, Any]]` | 今の `render` の中身を移し、組んだプランと、埋める前に開いたプランの形（`expanded` の戻り値）を返す |
| `rendered` | `src/ndf/core/route/__init__.py` | 新設 | `rendered(t: Template, given: Mapping[str, Value], *, local_tests: bool = True) -> tuple[dict[str, Any], list[str]]` | `_built` が返した開いた形から `work_names` で作業の名前（重なりを除く）を取り、組んだプランと一緒に返す |
| `render` | `src/ndf/core/route/__init__.py` | 変更 | 今のまま | `_built` の 1 つ目の値を返す。`work_names` は打たない |
| `write_values` | `src/ndf/contracts/worker_kinds.py` | 変更 | `write_values(..., released: Optional[str] = None)` | `released` が `None` でなければ `released_version` も書く |
| `uses_released` | `src/ndf/progress/invoke.py` | 新設 | `uses_released(kinds: Iterable[str]) -> bool` | 作業の名前の並びに、作業の定義（`worker_kinds.work_kinds()`）の `outputs` が `product_version` を持つ作業があるかを返す |

**記録済みの値を使うプラン**とは、プランが流す作業（`route.kinds_of(template, local_tests)` と同じ一覧）に、出力に `product_version` を持つ作業（今は `release` だけ）を含むプランである。ほかのプランでは、`values.json` の `released_version` を読む者がいないので書かない。

判定は `values.json` に残さず、プランを書くたびに今のテンプレートから求める。判定を残すと、NDF を更新して同じ名前のプランに `release` の作業が加わったとき、進行中の工程を再開しても前の NDF が残した判定が使われる。その結果、承認済みの配布識別子が渡らないまま worker が起動してしまう。

判定には、プランを組むときに開いたテンプレートの形をそのまま使い、判定のためにテンプレートを開き直さない。`route.render` は差し込みを埋める前に、テンプレートの `use` を開いた形（`route.expanded`）を作る。作業の名前はこの形から取れるので、`route.rendered` が組んだプランと一緒に返す。これで、`values.json` を使い回す回も、判定のための展開が増えない。作業の一覧の走査（`work_names`）は、判定の要る `plan_path` の 1 回だけになる。工程の初回は、`prepare_plan` が入力の検査のために作業の一覧をすでに回しているので、その一覧を判定にも使う。

判定の求め方として、次の 3 つを比べた。費用は 2026-11-26 に手元（8 コア、Python 3.14.4）で、今あるテンプレート 24 個のそれぞれについて、`route.kinds_of` と作業の定義の照合を 200 回ずつ流して測った。読み込み済みのテンプレートでは、1 回あたり平均 0.075 ms、最大 0.32 ms（テンプレート `check`）だった。

| 案 | 判定のために増える処理 | NDF を更新してプランに `release` が加わった後の再開 | 採否 |
| --- | --- | --- | --- |
| `route.render` と同じ展開から作業の一覧を受け取る（`route.rendered`） | `plan_path` で作業の一覧の走査が 1 回（展開は増えない） | 今のテンプレートで判定する | 採る |
| `plan_path` の前に `route.kinds_of` で判定し直す | プランを組むたびに展開と走査が各 1 回（上の費用） | 今のテンプレートで判定する | 採らない。同じ結果を得るのに展開を 1 回余分に回す |
| 判定を `values.json` に残す | 無い | 前の NDF が残した判定を使い、承認済みの配布識別子が渡らない | 採らない。理由は上の「判定は `values.json` に残さず」で始まる段落 |

`rendered` を足すと `route` の公開の関数が 1 つ増える。`render` は `rendered` を経ずに `_built` を呼ぶので、作業の名前を使わないほかの呼び出し元の処理は今と同じで、走査も増えない。

判定を `values.json` に残して変更を検知する案も比べた。変更を検知するには、テンプレートと作業の定義から作った指紋を残して比べる必要がある。指紋を求めるにはテンプレートを開く必要があり、採った案（展開が増えない）より費用が増えるので、キャッシュする利点が無い。そのため採らなかった。

値が前と同じなら書き直さない。そのため、`values.json` を書き直すのは、試行の中で記録済みの値が変わったとき（承認の回答で値が入った、または消えた）だけになる。

**リリース承認ゲートの回答は、プランが配布識別子を使うと分かってから探す。** プランが配布識別子を使うかは、作業の一覧（`prepare_plan` の入力の検査の一覧と、`plan_path` の `route.rendered` の一覧）を見るまで分からない。工程の進行は、プランを呼ぶ前に探すかどうかを決めない。探す関数を `StageRun.released` に入れて渡し、`prepare_plan` と `plan_path` が `uses_released` を真と判定したときだけ呼ぶ。`approved_seen` は、今と同じく直近の承認で探索を終える。そのため、配布識別子を使わないプランでは、リリース承認が無くても残りの履歴をたどらない。

- `released_answer`
  - 事前条件: `v` は同じ工程の事象を持つステートマシンの読み取り
  - 事後条件: 設計文書の「記録済みの値の読み方」の手順 1〜4 の結果を返す。値が無ければ `None`
  - 副作用: 無し（読むだけ）
- `released_answer` が選ぶのは、リリース承認ゲート（ステップ `release-gate`）への最後の回答である。その事象は `payload.kind == LISTEN` で、`payload.gate` に定数 `RELEASE_GATE`（`gate/asking.py`）を持つ。定数の値はステップ `release` の名前と同じ文字列 `release` だが、指すものは違う。worker の判断の求めへの回答はステップ `release` への回答で、`kind` が `plan` であり `gate` を持たないので、この条件で除ける。`approved_seen` が選ぶ条件（工程の最新の承認）は回答の `gate` を見ないので、`released_answer` は別の条件で当てる
- 回答の次の事象（事象の番号 `seq` が回答の `seq + 1` の事象）は、新しい順にたどる途中で 1 つ前に見た事象である。たどり直さずに、見た事象を 1 つ覚えておいて引く。種類が `stages.OUTPUTS_EVENT` のときだけ `payload.outputs.product_version` を読む

ここでの「試行」は、工程の retry から次の retry までの 1 回分を指す。1 つの試行の中でプランは何回か流れる（承認の回答の後・自動の打ち直し）。

| 場面 | `values.json` を扱う関数 | 扱い |
| --- | --- | --- |
| 工程の retry の後の最初 | `run._prepare` が消し、`prepare_plan` が作る | 新しく作る。記録済みの値を使うプランなら、同じ書き込みで `released_version` も書く |
| 同じ試行の中でプランを流す（初回の `prepare_plan` の直後を含む） | `_prepared` | 前に作ったものを使い回す。判定は今のテンプレートから求める |
| 使い回す回で、記録済みの値が前と違う | `plan_path` が `write_released` を打つ | `released_version` だけを書き直す。同じなら書かない |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `StageRun.released` を呼ぶか | 作業の一覧に配布識別子を使う作業が無い（`uses_released` が偽）、またはプランでないステップ | 呼ばない。リリース承認ゲートの回答を探さない |
| `released_answer` | 該当の回答が無い | `None` |
| `released_answer` | 回答が `approved` でない、または `by` が `organizer` でない | `None` |
| `released_answer` | 次の事象が出力の事象でない、または `product_version` が無い・空白だけ | `None` |
| `released_answer` | 上のどれでもない | `product_version` の文字列をそのまま |

- 繰り返しの終わる条件: 事象を新しい順にたどり、リリース承認ゲートへの最後の回答を見た所で終える
- 計算量の注意: 1 つの工程の事象の数に比例する。`approved_seen` のたどり方は今と同じである。配布識別子を使うプランでは、`released_answer` がリリース承認ゲートへの最後の回答まで 1 回たどる。1 回のプランの呼び出しの中で `prepare_plan` と `plan_path` が両方とも値を求めても、`functools.cache` で包むので、たどるのは 1 回である。配布識別子を使わないプランとプランでないステップ（skill・script・承認ゲートなど）では、`released_answer` を呼ばないので、今と同じ所で終わる

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `released_answer` | オーガナイザーの承認の後の値を返す | 後に worker の判断の求めへの `approved` があっても前者を返す | 次のどれかなら `None`: (a) 承認したのがオーガナイザーでなく、利用者か MVV 判定である (b) 回答が却下である (c) 回答の直後の事象が出力の事象でない |
| `plan_path` | 前の試行の `values.json` が残っていても、今の値で書き直す | 値が無い回は `released_version` を消す。値が前と同じ回と、記録済みの値を使わないプランでは `write_released` が呼ばれない | 記録済みの値を使わないプランでは `run.released` が呼ばれない（呼ぶと失敗する関数を渡して確かめる） |
| `plan_path`（判定） | 前の NDF が `release` の無いテンプレートで書いた `values.json` が残っていても、今のテンプレートに `release` があれば `write_released` を打つ（同じ名前のプランが変わった後の再開） | 今のテンプレートに `release` が無ければ、`values.json` に `released_version` が残っていても `write_released` を打たない | — |
| `rendered` | 組んだプランが `render` と同じで、作業の名前が `kinds_of` と同じ並び | `{{worker}}` の無いテンプレートでは空の並び | 組んだプランの誤りは `render` と同じ `RouteError` |
| `uses_released` | `release` を含む並びで真 | 空の並びで偽 | 作業の定義に無い名前は `KeyError`（`kind_gaps` と同じ） |

## 工程の定義の読み手

満たす決定: 決定 4、受け入れ条件 6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `required_outputs` | `src/ndf/progress/definition.py` | 新設 | `required_outputs(ref: str) -> tuple[str, ...]` | ステップの出力の規約が `outputs` に必須とするキーの名前を返す |

- 規約のノードを `ref_target` で引き、`allOf` の各要素と本体の `properties.outputs.required` を集める（`release-result` は `allOf` の 2 つ目に入れ子で持つ）。`find_enum` と同じく木をたどる
- 名前は見つけた順で、重なりを除く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `required_outputs` | 参照が規約を指さない | 定義の誤り | `ref_target` | 呼び出し元へ返す（`ref_target` の今の例外のまま） | 工程の失敗。今の `validate_io` と同じ | 定義を直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `required_outputs` | `release-result` で `("product_version",)` | 必須の無い規約で空 | 規約でない参照で上がる |

## worker の起動（`ndf answer work`）

満たす決定: 決定 1、決定 2、受け入れ条件 1・2・3・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_strategy_at` | `src/ndf/progress/gate/entry.py` | 新設 | `_strategy_at(workdir: Path) -> Optional[branch_strategy.Strategy]` | 作業場所のリポジトリのブランチ戦略の設定を返し、読めなければ `None`。作業場所が設定を持つリポジトリの根なら `git` を起動しない |
| `released_outcome` | `src/ndf/progress/gate/entry.py` | 新設 | `released_outcome(work: str, saved: Mapping[str, Any], strategy: Optional[branch_strategy.Strategy]) -> Optional[dict]` | worker を起動せずに返す結果 JSON（`ok` か `stopped`）を組む。起動するときは `None` |
| `Released` | `src/ndf/progress/gate/entry.py` | 新設 | `class Released(NamedTuple): outcome: dict` | worker を起動せずに終えるときの結果を `_cmd_work` へ渡す |
| `reaches` | `src/ndf/domains/release/forms_decide.py` | 新設 | `reaches(row: Mapping[str, Any], channel: str) -> bool` | 届け先の行が承認資料のチャネルに当たるか |
| `deliveries_of` | `src/ndf/domains/release/forms_decide.py` | 変更 | 今のまま | 条件を `reaches` で読む（振る舞いは変えない） |
| `WorkHandoff` | `src/ndf/progress/gate/entry.py` | 変更 | 欄 `version_number: bool` を足す | 起動指示を組んだときの値の形を、`work_argv` へ同じ値で渡す |
| `_work_handoff` | `src/ndf/progress/gate/entry.py` | 変更 | `_work_handoff(kind: str, state_dir: Path, cwd: Optional[str]) -> WorkHandoff \| Released` | 作業場所を決めた後に設定を読み、記録済みの値で終えるなら起動指示を書かずに `Released` を返す。それ以外は値の形を決めて起動指示を組む |
| `work_prompt` | `src/ndf/progress/gate/entry.py` | 変更 | `work_prompt(kind, saved, workdir, paths, *, version_number: bool = False) -> str` | `version_number` を `_outputs_of` へ渡す |
| `_cmd_work` | `src/ndf/progress/gate/entry.py` | 変更 | 今のまま | `_work_handoff` が `Released` を返したら、その結果（`ok` か `stopped`）を `result.emit` で出して終える。終了コードは、今の止まりの結果と同じく `result.emit` が status から決める。`WorkHandoff` なら `handoff.version_number` を `work_argv` へ渡す |
| `work_argv` | `src/ndf/progress/gate/entry.py` | 変更 | `work_argv(kind, state_dir, cwd, *, prompt_file=None, result_dir_id=None, version_number: bool = False) -> list[str]` | `version_number` が真なら `--require-version-number` を足す |
| `_outputs_of` | `src/ndf/progress/gate/entry.py` | 変更 | `_outputs_of(kind: str, result_file: Path, version_number: bool) -> str` | 起動指示に、`product_version` に返す値の形を 1 行で書く |

設定を読むのは、`product_version` を出力に持つ作業（今の作業の定義では `release` だけ）に限る。ほかの作業（実装・CI の修正・レビューなど）では `git rev-parse` も設定の読み込みもせず、今と同じく起動する。`product_version` を出力に持つかは、作業の定義（`kinds.json`）の `outputs` で決め、作業の名前を文字列リテラルで比べない。

起動指示と `work_argv` の値の形は、`_work_handoff` が 1 回だけ決めた `version_number` から得る。起動指示を組む経路と終了時に確かめる経路で、別々に決め直さない。

- `_work_handoff` の手順は次のとおり。保存先を開き、`values.json` を読み、作業場所を決めるところまでは今と同じである
  1. 作業の定義の `outputs` に `product_version` があれば、`strategy = _strategy_at(workdir)` を読む。無ければ `strategy = None` とし、手順 2 を飛ばす（手順 3 の `version_number` は偽になる）
  2. `released_outcome(kind, saved, strategy)` が結果を返せば、`Released(結果)` を返す。起動指示も、報告の初期化も、記述子の用意もしない
  3. `version_number` は、`product_version` を出力に持つ作業で、`strategy` が `None` か、`strategy.version_store["kind"]` が `"none"` でないときに真である。それ以外は偽である
  4. `work_prompt(kind, saved, workdir, paths, version_number=version_number)` で起動指示を組み、`WorkHandoff(..., version_number)` を返す
- `_strategy_at` は、`workdir` に `.ndf/project.json` があれば、`workdir` を根として `branch_strategy.load(workdir)` を読み、`git` を起動しない。作業 `release` の作業場所は承認の対象のリポジトリの根（差し込み `repo`）なので、ふだんはこの経路になる。無いときだけ、`workdir` で `git rev-parse --show-toplevel` を打って根を決めてから読む（`progress/command.py` の `strategy` と同じ解き方）。版の種類と届け先を同じ 1 回の読み込みから得る
- `released_outcome`
  - 事前条件: `saved` は `values_in` で読んだ `values.json`
  - 事後条件: `released_of(saved)` があり、`strategy` が `None` でなく、その `version_store["kind"]` が `"none"` で、作業 `work` の定義の `outputs` が `product_version` と `delivery` を持つ（今は `release` だけ）ときだけ、結果 JSON を返す。届け先が下の規則で 1 行に決まれば `status: ok` と `outputs` の `product_version`・`delivery`、決まらなければ `status: stopped`（停止理由 `precondition`）である。どちらも worker を起動しない。それ以外は `None`
  - 副作用: 無し（設定は呼ぶ側が読んで渡す）
- `delivery` は、`strategy.section["release"]` の `deliveries` のうち `reaches(row, "production")` の行から決める。行が 1 つなら、その行を文脈の形で確かめ、合えばそのまま返す。確かめ方は、worker の結果の確かめ（下の「worker の結果の確かめ」）と同じく、文脈のスキーマ全体（`contracts.internal.CONTEXT`）へ `{"delivery": 行}` を渡す（`contracts.schema_errors`）。`#/$defs/delivery` だけを指定しない。今の検証器（`contracts._shipped_validator`）は、`$defs` を指定すると `$defs` だけを残してルートの `properties` を捨てる。`$defs/delivery` の `branch` は `#/properties/branch` を参照するので、`branch` を書いた有効な行が参照の誤りで止まる。合わなければ、誤りの文と設定の場所（`branch_strategy.release.deliveries`）を停止の理由に書いて `stopped` を返す。行が 0 か 2 つ以上なら、どの届け先へ出したかが設定から 1 つに決まらないことを停止の理由に書いて `stopped` を返す。固定の値で `delivery` を埋めない（設計文書の「入出力のコントラクト」の `delivery` の表）。`deliveries_of` は行を 3 項目の `Delivery` に縮め、文脈の `delivery` が必須とする `versioned` を落とすので使わない。条件だけを `reaches` で共有する
- `_outputs_of` の 1 行は、`version_number` が真なら「`product_version` はバージョン番号（例 `1.2.3`）」、偽なら「`product_version` は出したものを指す空白だけでない文字列（例 `main abc1234`）」とする。作業の出力に `product_version` が無ければ、この行を書かない

**工程が読んだ設定を使い回さない理由**: プランを組む `prepare_plan` も、1 回の試行に 1 回 `.ndf/project.json` を読む（`invoke._project`）。ただし、読む場所は工程の入力の作業場所（`work_location`）で、worker が動く承認の対象のリポジトリ（`_work_dir`）と同じとは限らない。また、`_project` は `.ndf/project.json` をそのまま読むだけで、`branch_strategy.load` が行う旧いキーとスキーマの検査をしない。そのため、worker の作業場所で `branch_strategy.load` を読む。工程の側で読んで `values.json` へ写す案は、写した値が正本の外のキャッシュになり（Value 8）、作業場所が同じかを確かめるにはやはり根を解く必要があるので採らない。増える処理は、`release` の作業を 1 回起動するごとに設定の読み込み 1 回である。`git rev-parse` は、作業場所が設定を持つリポジトリの根でないときだけ 1 回打つ。ほかの作業では、設定の読み込みも `git` の起動も増えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_strategy_at` | 設定が無い・読めない | 設定の誤り | `branch_strategy.load` の `StrategyError`・`OSError` | 既定値で続ける（`None`。バージョン番号を持つ扱い） | 無し。worker を起動し、形を確かめる | — |
| `_strategy_at` | `git rev-parse` が失敗する・`git` を起動できない | リポジトリの外・`git` の誤り | `Git.run` の `GitError`（`git` を起動できないときの `OSError` を含む） | 既定値で続ける（`None`。バージョン番号を持つ扱い） | 無し。worker を起動し、形を確かめる | — |
| `released_outcome` | 届け先の設定が配列でない・本番に届く行が 0 か 2 つ以上 | 設定の誤り・設定から届け先が決まらない | 関数の中 | 止める（worker を起動しない） | `stopped`（`precondition`）。届け先が 1 つに決まらないことを示す | 設定を直して retry すれば、同じ記録済みの値で終わる |
| `released_outcome` | 本番に届く行が文脈の形に合わない（`target`・`trigger` が空文字列など） | 設定の誤り | `contracts.schema_errors` | 止める（worker を起動しない） | `stopped`（`precondition`）。合わないキーと設定の場所を示す | 同上 |
| `_cmd_work` | `values.json` が無い | 試行の始め前に打った | `values_in` | 止める（今の `WorkUnready`） | `stopped`（`precondition`）。終了コードは今のまま | retry で打ち直せる |

### 処理の手順

表の「版の種類」は、`_strategy_at` が返した設定の `version_store["kind"]` である。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_work_handoff` | 記録済みの値あり・版の種類が `none`・作業の出力が `product_version` と `delivery` を持つ（`release`） | worker を起動せず `Released` を返す。`_cmd_work` は、届け先が 1 行に決まれば ok を、決まらなければ `stopped` を出す |
| `_work_handoff` | 記録済みの値あり・版の種類が `none` 以外か設定を読めない | worker を起動し、形を確かめる |
| `_work_handoff` | 記録済みの値なし・版の種類が `none` | worker を起動し、形を確かめない |
| `_work_handoff` | 記録済みの値なし・版の種類が `none` 以外か設定を読めない | worker を起動し、形を確かめる（今と同じ） |
| `_work_handoff` | 作業の出力に `product_version` が無い | 設定を読まず、記録済みの値も見ずに worker を起動する。形は確かめない（今と同じ） |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_cmd_work` | worker を起動せずに終えたこと（作業の種類と版の種類） | ログの `info` と結果 JSON の要約 | 記録済みの値の中身（ログには載せない。結果 JSON の `outputs` にだけ載る） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_strategy_at` | `progress/command.py` の `strategy`、`domains/release/route_decide.py` の `route_of`、`progress/invoke.py` の `_project` | `grep -rn 'version_store' src/ndf --include=*.py`、`grep -n 'def _project' src/ndf/progress/invoke.py` | 使えない。`strategy` は読めないと `FactUnknown` を上げ、`route_of` は配布の経路全体を組む。`_project` は読む場所が違い、スキーマの検査をしない（上の「工程が読んだ設定を使い回さない理由」）。`branch_strategy.load` を使う |
| `released_outcome` | `forms_decide.deliveries_of` | `grep -rn 'def deliveries_of'` | 条件だけを `reaches` に切り出して使う。返す形は行を縮めるので使わない |
| `reaches` | `deliveries_of` の中の条件式 | 同上 | 式を移す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `released_outcome` | `none` と記録済みの値で ok と 2 つの出力。`branch: main` を書いた有効な本番の行も ok で、その行をそのまま返す | 本番に届く行が 0・2 つで `stopped`（`precondition`）。先頭が `channel: base` の行でも本番の行を返す。本番の行の `target` が空文字列なら `stopped` | 版の種類が `none` 以外・設定が `None`・値なしで `None` |
| `_strategy_at` | 設定を返す。作業場所に `.ndf/project.json` があれば `Git.run` が呼ばれない | 作業場所が根でないときだけ `Git.run` が 1 回呼ばれる | 設定が無い・`git` が `GitError` を上げると `None`（`Git.run` を差し替えて確かめ、`git` を起動しない） |
| `_work_handoff` | `release` で、起動指示の 1 行と `version_number` が同じ値から決まる | 出力に `product_version` の無い作業では `_strategy_at` が呼ばれない | — |
| `_cmd_work` | 記録済みの値で worker を起動しない（`exec_` が呼ばれない） | `--candidate`・`rev {repo} {state_dir}` の値がそのまま出力になる | `values.json` が無いと `precondition` |
| `reaches` | `channel: production` と省略で真 | `null` で真 | `base` で偽 |

## worker の結果の確かめ（`ndf worker run`）

満たす決定: 決定 1、決定 4、受け入れ条件 3・5・8・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Checked` | `src/ndf/core/worker_outcome.py` | 新設 | `class Checked(NamedTuple): report: Any; outputs: Optional[dict[str, Any]]; missing: tuple[str, ...]` | 確かめの結果を 3 つ組で渡す |
| `require_outputs` | `src/ndf/core/worker_outcome.py` | 変更 | `require_outputs(report: Any, required: Sequence[str], *, version_number: bool = False) -> Checked` | `ok` と `gate` の結果ファイルの必須の `outputs` を確かめる |
| `_output_gap` | `src/ndf/core/worker_outcome.py` | 変更 | `_output_gap(report: Mapping[str, Any], name: str, version_number: bool) -> Optional[dict[str, str]]` | 値を結果 JSON の `$defs/outputs` に加えて文脈の形（`context.schema.json`）でも確かめる。`version_number` が真なら `product_version` を `$defs/version_number` でも確かめる |
| `outcome_of` | `src/ndf/core/worker_outcome.py` | 変更 | `outcome_of(kind, result_file, report, items, metrics, *, outputs=None, missing=()) -> dict` | `gate` の結果にも確かめた `outputs` と `metrics.missing_outputs` を載せる |
| `_add_run`・`_cmd_run` | `src/ndf/core/worker.py` | 変更 | 今のまま | 引数 `--require-version-number`（真偽）を足し、`require_outputs` へ渡す |

- `require_outputs`
  - 事後条件は次の表のとおり。`gate` で `gaps` を足さないのは、判断の求めを欠けで止めず、`gate` のまま返すためである

    | 場合 | `report` | `outputs` | `missing` |
    | --- | --- | --- | --- |
    | `ok` で欠けあり | `gaps` を足したコピー | `None` | 欠けた名前 |
    | `ok` で欠けなし | 元の結果 | 確かめた値 | 空 |
    | `gate` | 元の結果 | 確かめを通った名前の値（1 つも無ければ `None`） | 通らなかった名前 |
    | それ以外・`required` が空 | 元の結果 | `None` | 空 |
- `outcome_of` の `gate` は、`outputs` があれば結果に載せ、`missing` が空でなければ `metrics["missing_outputs"]` に名前の並びを載せる。`ok` と `stopped` は今と同じ
- バージョン番号の形は `contracts.schema_errors(value, result.SCHEMA + "#/$defs/version_number")` で確かめる
- 文脈の形は、工程の進行の `invoke.value_errors` と同じスキーマ（`contracts/workflow/context.schema.json`）で `{name: value}` を確かめる。`invoke.finished` は `gate` の結果にもこのスキーマを当て、合わなければ承認を聞く前に `bad_report` にする。ここで先に確かめれば、形の違う値は `outputs` に載らず `missing` に入るので、`gate` のまま判断の求めが届く。スキーマの名前は `contracts` の層の定数 `contracts.internal.CONTEXT` を使い、`core` から `progress` を読まない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `require_outputs` | `ok` で必須の値が無い・形が違う（`ndf v11.0.0` を含む） | worker の誤り | `_output_gap` | 呼び出し元へ返す（欠け） | `stopped`（`incomplete`）。欠けの名前は `product_version` | 工程の失敗。retry で worker を起動し直せる |
| `require_outputs` | `gate` で必須の値が無い・形が違う（文脈の形に合わない `delivery` を含む） | worker が値を書かずに判断を求めた | `_output_gap` | 呼び出し元へ返す（`missing`） | `gate` のまま。`metrics.missing_outputs` に名前 | 承認の回答の後に工程の進行が失敗にする（下の「失敗の理由」） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `require_outputs` | `gate` で `delivery`・`product_version` がそろえば両方を返し、欠けは空 | `version_number` 偽で `main abc1234` が通り、改行を含む値は欠けになる | `version_number` 真で `ndf v11.0.0` が `ok` では欠け、`gate` では `missing`。`versioned` の無い `delivery` が `ok` では欠け、`gate` では `outputs` に載らず `missing` に入る |
| `outcome_of` | `gate` の結果に `outputs` と `metrics.missing_outputs` が載る | `missing` が空ならキーが無い | — |

空文字・空白だけの `product_version`（受け入れ条件 5）は、結果 JSON のスキーマ（`$defs/text`）で形の誤りになり、`ndf contracts emit` が断る。テストは `tests/contracts/test_result.py` に置く。

## プランの遷移と結果の要約

満たす決定: 決定 4、受け入れ条件 6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `outcome_of` | `src/ndf/core/plan/steps.py` | 変更 | 今のまま | 返す辞書にキー `missing_outputs`（結果の `metrics.missing_outputs` の名前の並び。無ければ `None`）を足す |
| `Outcome.summary` | `src/ndf/core/plan/transition_decide.py` | 新設 | `summary: str = ""`（`NamedTuple` の欄） | 遷移へステップの要約を渡す |
| `_perform` | `src/ndf/core/plan/runner.py` | 変更 | 今のまま | `run_step` の結果の `summary` を `Outcome` に入れる |
| `_after_gate` | `src/ndf/core/plan/transition_decide.py` | 変更 | `_after_gate(state: StepState, step: Step, summary: str) -> Next` | 続きの無い承認ゲートの要約に、止まったステップの要約を含める |
| `_keep_step_missing` | `src/ndf/core/plan/records.py` | 新設 | `_keep_step_missing(state_dir: Path, step_id: str, names: Optional[list[str]]) -> None` | ステップの最後の回の `missing_outputs` を残す（無ければ外す） |
| `recorded_step_missing` | `src/ndf/core/plan/records.py` | 新設 | `recorded_step_missing(state_dir: Path) -> dict[str, list[str]]` | 残した `missing_outputs` を読む |
| `run_step` | `src/ndf/core/plan/runner.py` | 変更 | 今のまま | `_keep_step_gaps` と並べて、`steps.outcome_of` の `missing_outputs` を `_keep_step_missing` へ渡す |
| `final_result` | `src/ndf/core/plan/runner.py` | 変更 | 今のまま | 承認ゲートで終わった回は、承認ゲートで止まったステップ（終わりの状態の `last`）の `missing_outputs` を結果の `metrics.missing_outputs` に載せる |

- `_after_gate` の要約は `ステップ {step.id} が承認ゲートで止まった: {summary}`（`summary` が空なら今の文）
- `_keep_step_missing` は `_keep_step_gaps` と同じ形のファイル（ステップの id → 名前の並び）を `step-missing.json` に書く。`step-gaps.json` は `stopped` を返したステップの欠けを残すファイルなので、`gate` の欠けは混ぜない

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_after_gate` | 要約に worker の要約が入る | 要約が空なら今の文 | — |
| `final_result` | `gate` で終わった回に `metrics.missing_outputs` が載る | 同じステップを打ち直して値がそろえば外れる | — |
| `steps.outcome_of` | `metrics.missing_outputs` を読む | 無ければ `None` | 名前の並びでなければ `None` |

## プランの出力の記録

満たす決定: 決定 4、決定 6、受け入れ条件 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_add_outputs` | `src/ndf/core/plan/records.py` | 変更 | `_add_outputs(state_dir: Path, step_id: str, status: str, outputs: Optional[dict[str, Any]]) -> None` | 設計文書の「プランの出力の記録」の表のとおりに `entries` を更新する |
| `_stored_entries` | `src/ndf/core/plan/records.py` | 新設 | `_stored_entries(state_dir: Path) -> list[dict[str, Any]]` | `outputs.json` を要素の並びで読む。直す前の形はステップの分からない要素 1 つにする |
| `recorded_outputs` | `src/ndf/core/plan/records.py` | 変更 | 今のまま | 要素を先頭から重ねた `outputs` を返す（形の確かめは今のまま） |
| `run_step` | `src/ndf/core/plan/runner.py` | 変更 | 今のまま | `gate` のときは `outputs` が無くても `_add_outputs` を打つ（そのステップの前の値を外すため）。`ok`・`stopped` は今と同じく `outputs` があるときに打つ |

- `_add_outputs`
  - 事後条件: `ok`・`stopped` で `outputs` があれば、同じステップの前の要素から今回のキーが外れ、今回の要素が末尾にある。`gate` なら、同じステップの要素がすべて外れ、今回の `outputs` があれば末尾にある。どちらでも、ステップの分からない要素（直す前の形）は残る
  - 副作用: `outputs.json` を書き直す
- ステップの分からない要素は `{"step": null, "outputs": {...}}` で表す。外す対象にしない
- `_stored_outputs` は消し、呼び出し元は `_stored_entries` の要素を重ねた値を使う。`internal.stage_values` が行う旧いキー名から今のキー名への読み替えは、重ねる前に各要素の `outputs` へ当てる
- `invoke._no_route` が承認の後に使う `outputs` は、プランが回ごとに書き残す結果のファイル（`runner.stored_result` で読む）の `outputs` で、`recorded_outputs` が組んだものである。前の試行の判断の求めの値は、`gate` を返したときに `_add_outputs` が外しているので混じらない

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ保存先のプランのステップ | `outputs.json` | 今と同じ。1 つのプランの回はステップを順に 1 つずつ打ち、並行して走らない | 同じステップを同じ出力で打ち直しても要素は増えない（ステップとキーの組ごとに最新だけ） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_add_outputs` | 2 つのステップの値が順に残る | 同じステップが同じキーを 3 回返しても要素は 1 つ | `gate` で値なしなら、そのステップの前の値が外れ、先のステップの同じキーは残る |
| `_stored_entries` | `entries` の形を読む | 直す前の形を先頭の要素 1 つにする | 壊れた JSON は今と同じ扱い |

## 失敗の理由（出力の足りない承認）

満たす決定: 決定 4、受け入れ条件 6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `answered_gap_detail` | `src/ndf/progress/invoke.py` | 新設 | `answered_gap_detail(names: Sequence[str], waited: str) -> str` | 足りない値の名前・判断を求めて止まったステップの要約・次の操作を 1 つの文にする |
| `_no_route` | `src/ndf/progress/invoke.py` | 変更 | 今のまま | 承認の回答を受けたら、承認の後に続くステップがあるかを見る前に、回答を待っていたプランの結果（承認ゲートで止まった結果）の `metrics.missing_outputs` を見る。空でなければ、続きがあっても `bad_report` を返し、プランを続けない。空で続きが無ければ、その結果の要約を `metrics.waited` に入れて ok を返す。空で続きがあれば、今と同じく `None` を返してプランへ渡す |
| `_step_draft` | `src/ndf/progress/run.py` | 変更 | 今のまま | `ok` の結果が `metrics.waited` を持ち、出力の規約に合わなければ、`required_outputs` から足りない名前を数えて `answered_gap_detail` の文で失敗にする |
| `_finish_stage` | `src/ndf/progress/run.py` | 変更 | 今のまま | 工程の出力が規約に合わず、どれかのステップの結果が `metrics.waited` を持てば、`check_stage_output` の欠けた名前で `answered_gap_detail` の文にする |

- `answered_gap_detail` の文は `足りない出力: {名前の並び}。判断を求めたステップの要約: {waited}。次の操作: ndf progress retry <ID> <工程> で工程を打ち直し、足りない値を返させる` の形にする。停止理由の種類は `bad_report` のままで、自動の打ち直しは入れない
- 3 つの段で数える名前は設計文書の決定 4 の表に従う。`_step_draft` は「規約が必須とするキーのうち、そのステップの結果に無いもの」、`_finish_stage` は「工程の `outputs` のうち、どのステップの出力にも無いもの」である
- `metrics.waited` が無い結果（承認の回答を経ない ok）は、今の文のままにする
- 続きのある承認でも欠けで止めるのは、続けた後のステップが同じキーを返すと、判断を求めたステップの欠けが合成した出力で隠れるためである（設計文書の決定 4）。`_no_route` は今、続きがあると `None` を返して何も確かめないので、欠けの確かめを続きの判定より前へ置く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_no_route` | 判断を求めたステップ自身の欠け | worker が値を書かずに判断を求めた | 回答を待っていたプランの結果の `metrics.missing_outputs` | 止める | 工程の失敗（`bad_report`）。理由に名前と worker の要約 | conductor が retry を打つまで起動し直さない |
| `_step_draft` | ステップの出力が規約に合わない | 承認の後の出力の欠け | `validate_io` | 止める | 同上 | 同上 |
| `_finish_stage` | 工程の出力が規約に合わない | 先のステップの出力の欠け | `validate_stage_output` | 止める | 同上 | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `answered_gap_detail` | 名前と要約と次の操作が入る | 名前が 2 つ | — |
| `_no_route` | 欠けが空で ok と `metrics.waited`。欠けが空で続きがあれば `None` | 先のステップが同じキーを返していても、欠けがあれば失敗。承認の後に続くステップがあっても、欠けがあれば `None` を返さずに失敗 | 出力の無い判断の求めの承認で、理由に `product_version` と worker の要約 |
| `_step_draft` | `metrics.waited` の無い ok は今の文 | — | `metrics.waited` のある ok の欠けで名前入りの文 |

## 手順書の説明

満たす決定: 決定 1、決定 2、決定 5、受け入れ条件 3・4

関数を持たない。書き足す中身だけを書く。

| ファイル | 書き足す中身 |
| --- | --- |
| `skills/release/SKILL.md`・`references/release-steps.md` | バージョン番号を持たないリポジトリの `product_version` は、出したものを指す空白だけでない値である。まとめたリリースの後は、デプロイの工程が worker を起動せずに、オーガナイザーが記録した値で終わる |
| `skills/release-verification/SKILL.md` | リリース後テストの記録の `version` は、バージョン番号の形でない値も受け付ける |
| `skills/progress-tracking/SKILL.md` | 出力の足りない承認の失敗の理由の読み方と、次の操作（retry） |
| `skills/development-workflow/references/parallel.md` | まとめたリリースの後のレーンのデプロイの工程は、`ndf lanes release --result released --version` の値で終わる |
