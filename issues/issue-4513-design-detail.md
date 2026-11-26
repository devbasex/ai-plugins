# #4513 の詳細設計: デプロイの工程をバージョン番号を持たないリポジトリでも終える

設計文書 [issue-4513-design.md](./issue-4513-design.md) の構成要素と決定 1〜6 を、ファイルと関数の粒度へ下ろした文書である。受け入れ条件の中身は要求仕様 [issue-4513-requirements.md](./issue-4513-requirements.md) にある。書き方は `design` の `references/detailed-design.md` に従う。

**別ファイルにした理由**: 別ファイルにする条件は「新設する関数が 2 つ以上」か「シグネチャを変える関数がある」で、この設計は両方に当たる。新設は 11（`required_outputs`・`released_answer`・`released_outcome` など）、シグネチャの変更は 7（`require_outputs`・`_add_outputs`・`work_argv` など）である。条件は `requirements-design` の `document-roles.md` の「詳細設計を別ファイルにする条件」にある。

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
    ├── domains/release/test_release_forms.py
    └── progress/{test_definition.py, test_invoke.py, test_invoke_values.py, test_run.py, test_sprint_templates.py, gate/test_gate_work.py, gate/test_gate_release.py}
```

依存の向き: 工程の進行（`progress/run.py`・`invoke.py`）が、定義の読み手（`definition.py`）と値のファイル（`contracts/worker_kinds.py`）を読む。`ndf answer work`（`gate/entry.py`）は値のファイル・ブランチ戦略（`core/branch_strategy.py`）・届け先の選び方（`domains/release/forms_decide.py`）を読み、`ndf worker run`（`core/worker.py`）へ引数で渡す。プラン（`core/plan/`）は `ndf worker run` の結果 JSON を標準出力から読むだけで、工程の進行を読まない。今ある依存の向きは変えない。足す依存は `entry.py` → `forms_decide.py` と `entry.py` → `branch_strategy.py` の 2 本で、どちらも循環を作らない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/schemas/result.schema.json` | 変更 | 結果 JSON の形 | — | `$defs/outputs`・`$defs/version_number` | — |
| `contracts/schemas/release-verification.schema.json` | 変更 | リリース後テストの記録の形 | — | `version` | — |
| `contracts/worker/kinds.json` | 変更 | 作業 `release` の説明 | — | `release` | — |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の `product_version` の説明 | — | `product_version` | — |
| `src/ndf/contracts/worker_kinds.py` | 変更 | `values.json` の形と読み書き | — | モジュール | `write_released`（新設）・`released_of`（新設） |
| `src/ndf/progress/definition.py` | 変更 | 工程の定義と規約の読み手 | `contracts` | モジュール | `required_outputs`（新設） |
| `src/ndf/progress/run.py` | 変更 | 工程の進行 | `invoke`・`definition`・`stages` | モジュール | `released_answer`（新設）・`_run_step`・`_step_draft`・`_finish_stage`（変更） |
| `src/ndf/progress/invoke.py` | 変更 | ステップの呼び出し | `worker_kinds`・`plan` | `StageRun`・モジュール | `StageRun.released`（新設の欄）・`answered_gap_detail`（新設）・`plan_path`・`_no_route`（変更） |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf answer work` | `worker_kinds`・`branch_strategy`・`forms_decide` | モジュール | `released_outcome`（新設）・`_version_kind`（新設）・`_cmd_work`・`work_argv`・`_outputs_of`（変更） |
| `src/ndf/domains/release/forms_decide.py` | 変更 | 届け先の選び方 | — | モジュール | `reaches`（新設）・`deliveries_of`（変更） |
| `src/ndf/core/worker.py` | 変更 | `ndf worker run` | `worker_outcome` | モジュール | `_add_run`・`_cmd_run`（引数を 1 つ足すだけ） |
| `src/ndf/core/worker_outcome.py` | 変更 | worker の結果の確かめ | `contracts` | `Checked`（新設）・モジュール | `require_outputs`・`_output_gap`・`outcome_of`（変更） |
| `src/ndf/core/plan/steps.py` | 変更 | ステップの結果の読み取り | — | モジュール | `outcome_of`（変更） |
| `src/ndf/core/plan/transition_decide.py` | 変更 | プランの遷移 | — | `Outcome` | `Outcome.summary`（新設の欄）・`_after_gate`（変更） |
| `src/ndf/core/plan/runner.py` | 変更 | プランの実行 | `records`・`transition_decide` | モジュール | `run_step`・`_perform`・`final_result`（変更） |
| `src/ndf/core/plan/records.py` | 変更 | プランの記録のファイル | — | モジュール | `_add_outputs`・`recorded_outputs`（変更）・`_stored_entries`・`_keep_step_missing`・`recorded_step_missing`（新設）・`_stored_outputs`（削除） |
| `skills/` の 5 ファイル | 変更 | 手順書の説明 | — | — | — |

`forms_decide.py` と、テストの `test_invoke_values.py`・`test_worker_kinds_shape.py`・`test_steps.py`・`test_release_forms.py`・`test_definition.py` も、設計文書の「設計の結果」の触るファイルに入れた（届け先の条件を 1 か所にするためと、変える関数の既存のテストがそこにあるため）。

## 結果 JSON とリリース後テストの記録の定義

満たす決定: 決定 1、決定 5、受け入れ条件 3・4・5・8

関数を持たない。変える中身だけを書く。

| 場所 | 今 | 直した後 |
| --- | --- | --- |
| `result.schema.json` の `$defs/outputs` の `product_version` | バージョン番号の正規表現を直に書く | `{"$ref": "#/$defs/text"}`（空白だけでない文字列。`$defs/text` は今ある） |
| `result.schema.json` の `$defs/version_number`（新設） | — | 今の正規表現をそのまま移した `{"type": "string", "pattern": ...}`。バージョン番号の形の唯一の保存先 |
| `release-verification.schema.json` の `version` | 先頭の 0 を断るバージョン番号の正規表現 | `{"type": "string", "pattern": "\\S"}`。説明の文も「出したものを指す値」に直す |
| `context.schema.json` の `product_version` の説明 | 手動の 3 行の `## リリース記録` の読み方を書く | バージョン番号を持たないリポジトリでは出したものを指す値であると足す |
| `kinds.json` の `release` | — | 説明に、返す値の形は起動指示の 1 行に従うと足す |

`ndf worker run` がバージョン番号の形を確かめるときは、`$defs/version_number` を参照して確かめる（下の「worker の結果の確かめ」）。正規表現をコードに写さない。

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
- 形の定義 `VALUES_SHAPE` には足さない。`_has_shape` は決まったキーだけを見て、ほかのキーを残すので、キーを持たない `values.json`（直す前の NDF が書いたもの・単発のプラン）もそのまま読める

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
| `released_answer` | `src/ndf/progress/run.py` | 新設 | `released_answer(v: View, stage: str) -> Optional[str]` | リリース承認ゲートへのオーガナイザーの承認が書いた `product_version` を返す |
| `StageRun.released` | `src/ndf/progress/invoke.py` | 新設 | `released: Optional[str] = None`（データクラスの欄） | ステップの呼び出しへ記録済みの値を渡す |
| `_run_step` | `src/ndf/progress/run.py` | 変更 | 今のまま | `approved_seen` と並べて `released_answer` を読み、`StageRun` に入れる |
| `plan_path` | `src/ndf/progress/invoke.py` | 変更 | 今のまま | `_prepared` の後、プランを書く前に毎回 `write_released(work_dir, run.released)` を打つ |

- `released_answer`
  - 事前条件: `v` は同じ工程の事象を持つステートマシンの読み取り
  - 事後条件: 設計文書の「記録済みの値の読み方」の手順 1〜4 の結果を返す。値が無ければ `None`
  - 副作用: 無し（読むだけ）
- 選ぶ回答は `payload.kind == LISTEN` かつ `payload.gate == RELEASE_GATE`（`gate/asking.py`。値は `release`）の最後の `answered` である。worker の判断の求めへの回答は、プランを呼ぶステップ `release` への回答なので、`kind` が `plan` で `gate` を持たない。この条件で除ける。今の `approved_seen` は回答の `gate` を見ないので使わない
- 回答の次の事象は、事象の番号（`seq`）が回答の `seq + 1` の事象を同じ工程の事象の並びから引く。種類が `stages.OUTPUTS_EVENT` のときだけ `payload.outputs.product_version` を読む

`values.json` を作り直すのは `prepare_plan` で、同じ試行の中（承認の回答の後と、自動の打ち直し）で使い回すのは `_prepared` である。工程の retry の後の試行の始めは、`run._prepare` が `values.json` ごと消すので `prepare_plan` が作り直す。どちらの場合も、その後に `plan_path` が `write_released` を打つ。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `released_answer` | 該当の回答が無い | `None` |
| `released_answer` | 回答が `approved` でない、または `by` が `organizer` でない | `None` |
| `released_answer` | 次の事象が出力の事象でない、または `product_version` が無い・空白だけ | `None` |
| `released_answer` | 上のどれでもない | `product_version` の文字列をそのまま |

- 繰り返しの終わる条件: 事象を新しい順にたどり、最初に当たった回答で終える
- 計算量の注意: 1 つの工程の事象の数に比例する。`approved_seen` と同じ読み方で、増えない

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `released_answer` | オーガナイザーの承認の後の値を返す | 後に worker の判断の求めへの `approved` があっても前者を返す | 利用者か MVV 判定の承認・却下・出力の事象が隣に無いときは `None` |
| `plan_path` | 前の試行の `values.json` が残っていても、今の値で書き直す | 値が無い回は `released_version` を消す | — |

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
| `_version_kind` | `src/ndf/progress/gate/entry.py` | 新設 | `_version_kind(workdir: Path) -> Optional[str]` | 作業場所のリポジトリの `release.version_store.kind` を返し、読めなければ `None` |
| `released_outcome` | `src/ndf/progress/gate/entry.py` | 新設 | `released_outcome(work: str, saved: Mapping[str, Any], version_kind: Optional[str], deliveries: Sequence[Mapping[str, Any]]) -> Optional[dict]` | worker を起動せずに返す結果 JSON を組む。起動するときは `None` |
| `reaches` | `src/ndf/domains/release/forms_decide.py` | 新設 | `reaches(row: Mapping[str, Any], channel: str) -> bool` | 届け先の行が承認資料のチャネルに当たるか |
| `deliveries_of` | `src/ndf/domains/release/forms_decide.py` | 変更 | 今のまま | 条件を `reaches` で読む（振る舞いは変えない） |
| `_cmd_work` | `src/ndf/progress/gate/entry.py` | 変更 | 今のまま | `_work_handoff` の前に `released_outcome` を打ち、結果があれば出して 0 で終える |
| `work_argv` | `src/ndf/progress/gate/entry.py` | 変更 | `work_argv(kind, state_dir, cwd, *, prompt_file=None, result_dir_id=None, version_number: bool = False) -> list[str]` | `version_number` が真なら `--require-version-number` を足す |
| `_outputs_of` | `src/ndf/progress/gate/entry.py` | 変更 | `_outputs_of(kind: str, result_file: Path, version_number: bool) -> str` | 起動指示に、`product_version` に返す値の形を 1 行で書く |

- `released_outcome`
  - 事前条件: `saved` は `values_in` で読んだ `values.json`
  - 事後条件: `released_of(saved)` があり、`version_kind == "none"` で、作業の種類 `work` が `release` のときだけ、`status: ok` と `outputs` の `product_version`・`delivery` を持つ結果 JSON を返す。それ以外は `None`
  - 副作用: 無し（設定の行は呼ぶ側が渡す）
- `delivery` は、`deliveries` のうち `reaches(row, "production")` の行が 1 つならその行の辞書をそのまま返す。0 か 2 つ以上なら、設計文書の固定のオブジェクトを返す。`deliveries_of` は行を 3 項目の `Delivery` に縮め、文脈の `delivery` が必須とする `versioned` を落とすので使わない。条件だけを `reaches` で共有する
- `_version_kind` は `branch_strategy.load(root)` の `version_store["kind"]` を読む。`root` は `workdir` で `git rev-parse --show-toplevel` を打って決める（`progress/command.py` の `strategy` と同じ解き方）
- `_cmd_work` で `work_argv` に渡す `version_number` は `_version_kind(...) != "none"` である。読めない（`None`）ときも真になる
- `_outputs_of` の 1 行は、`version_number` が真なら「`product_version` はバージョン番号（例 `1.2.3`）」、偽なら「`product_version` は出したものを指す空白だけでない文字列（例 `main abc1234`）」とする

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_version_kind` | 設定が無い・読めない・`git` が失敗する | 設定の誤り・リポジトリの外 | `branch_strategy.load` の `StrategyError`・`OSError` | 既定値で続ける（`None`。バージョン番号を持つ扱い） | 無し。worker を起動し、形を確かめる | — |
| `released_outcome` | 届け先の設定が配列でない | 設定の誤り | 関数の中 | 既定値で続ける（固定のオブジェクト） | 無し | — |
| `_cmd_work` | `values.json` が無い | 試行の始め前に打った | `values_in` | 止める（今の `WorkUnready`） | `stopped`（`precondition`）。終了コードは今のまま | retry で打ち直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_cmd_work` | 記録済みの値あり・`version_kind` が `none`・作業 `release` | worker を起動せず ok を出す |
| `_cmd_work` | 記録済みの値あり・`version_kind` が `none` 以外か読めない | worker を起動し、形を確かめる |
| `_cmd_work` | 記録済みの値なし・`version_kind` が `none` | worker を起動し、形を確かめない |
| `_cmd_work` | 記録済みの値なし・`version_kind` が `none` 以外か読めない | worker を起動し、形を確かめる（今と同じ） |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_cmd_work` | worker を起動せずに終えたこと（作業の種類と `version_kind`） | ログの `info` と結果 JSON の要約 | 記録済みの値の中身（ログには載せない。結果 JSON の `outputs` にだけ載る） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_version_kind` | `progress/command.py` の `strategy`、`domains/release/route_decide.py` の `route_of` | `grep -rn 'version_store' src/ndf --include=*.py` | 使えない。`strategy` は読めないと `FactUnknown` を上げ、`route_of` は配布の経路全体を組む。`branch_strategy.load` を使う |
| `released_outcome` | `forms_decide.deliveries_of` | `grep -rn 'def deliveries_of'` | 条件だけを `reaches` に切り出して使う。返す形は行を縮めるので使わない |
| `reaches` | `deliveries_of` の中の条件式 | 同上 | 式を移す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `released_outcome` | `none` と記録済みの値で ok と 2 つの出力 | 本番に届く行が 0・2 つで固定のオブジェクト。先頭が `channel: base` の行でも本番の行を返す | `version_kind` が `none` 以外・値なしで `None` |
| `_version_kind` | `none` を返す | — | 設定が無いと `None` |
| `_cmd_work` | 記録済みの値で worker を起動しない（`exec_` が呼ばれない） | `--candidate`・`rev {repo} {state_dir}` の値がそのまま出力になる | `values.json` が無いと `precondition` |
| `reaches` | `channel: production` と省略で真 | `null` で真 | `base` で偽 |

## worker の結果の確かめ（`ndf worker run`）

満たす決定: 決定 1、決定 4、受け入れ条件 3・5・8・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Checked` | `src/ndf/core/worker_outcome.py` | 新設 | `class Checked(NamedTuple): report: Any; outputs: Optional[dict[str, Any]]; missing: tuple[str, ...]` | 確かめの結果を 3 つ組で渡す |
| `require_outputs` | `src/ndf/core/worker_outcome.py` | 変更 | `require_outputs(report: Any, required: Sequence[str], *, version_number: bool = False) -> Checked` | `ok` と `gate` の結果ファイルの必須の `outputs` を確かめる |
| `_output_gap` | `src/ndf/core/worker_outcome.py` | 変更 | `_output_gap(report: Mapping[str, Any], name: str, version_number: bool) -> Optional[dict[str, str]]` | `version_number` が真なら `product_version` を `$defs/version_number` でも確かめる |
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

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `require_outputs` | `ok` で必須の値が無い・形が違う（`ndf v11.0.0` を含む） | worker の誤り | `_output_gap` | 呼び出し元へ返す（欠け） | `stopped`（`incomplete`）。欠けの名前は `product_version` | 工程の失敗。retry で worker を起動し直せる |
| `require_outputs` | `gate` で必須の値が無い・形が違う | worker が値を書かずに判断を求めた | `_output_gap` | 呼び出し元へ返す（`missing`） | `gate` のまま。`metrics.missing_outputs` に名前 | 承認の回答の後に工程の進行が失敗にする（下の「失敗の理由」） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `require_outputs` | `gate` で `delivery`・`product_version` がそろえば両方を返し、欠けは空 | `version_number` 偽で `main abc1234` が通る | `version_number` 真で `ndf v11.0.0` が `ok` では欠け、`gate` では `missing` |
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
| `answered_gap_detail` | `src/ndf/progress/invoke.py` | 新設 | `answered_gap_detail(names: Sequence[str], waited: str) -> str` | 足りない値の名前・回答を待っていた結果の要約・次の操作を 1 つの文にする |
| `_no_route` | `src/ndf/progress/invoke.py` | 変更 | 今のまま | 承認の回答を受けたとき、回答を待っていたプランの結果（承認ゲートで止まった結果）の `metrics.missing_outputs` が空でなければ `bad_report` を返す。空なら、その結果の要約を `metrics.waited` に入れて ok を返す |
| `_step_draft` | `src/ndf/progress/run.py` | 変更 | 今のまま | `ok` の結果が `metrics.waited` を持ち、出力の規約に合わなければ、`required_outputs` から足りない名前を数えて `answered_gap_detail` の文で失敗にする |
| `_finish_stage` | `src/ndf/progress/run.py` | 変更 | 今のまま | 工程の出力が規約に合わず、どれかのステップの結果が `metrics.waited` を持てば、`check_stage_output` の欠けた名前で `answered_gap_detail` の文にする |

- `answered_gap_detail` の文は `足りない出力: {名前の並び}。回答を待っていた結果: {waited}。次の操作: ndf progress retry <ID> <工程> で worker を起動し直し、足りない値を返させる` の形にする。停止理由の種類は `bad_report` のままで、自動の打ち直しは入れない
- 3 つの段で数える名前は設計文書の決定 4 の表に従う。`_step_draft` は「規約が必須とするキーのうち、そのステップの結果に無いもの」、`_finish_stage` は「工程の `outputs` のうち、どのステップの出力にも無いもの」である
- `metrics.waited` が無い結果（承認の回答を経ない ok）は、今の文のままにする

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
| `_no_route` | 欠けが空で ok と `metrics.waited` | 先のステップが同じキーを返していても、欠けがあれば失敗 | 出力の無い判断の求めの承認で、理由に `product_version` と worker の要約 |
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
