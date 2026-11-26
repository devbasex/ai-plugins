# #4580 の詳細設計: 開発の流れをプロジェクトの設定で選べるようにする

設計文書 [issue-4580-design.md](./issue-4580-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方と、別ファイルにする条件は `design` の `references/detailed-design.md` にある。

別ファイルにした理由: 新設する関数が 49（「関数の仕様」の表の `新設` の行から、型 10 とファイル 3 を除いて数えた）あり、シグネチャを変える既存の関数が 2 つ（`close_decide.verdict` の引数の名前と返り、`record.write_verification` の引数）あるため（`requirements-design` の「詳細設計を別ファイルにする条件」）。型 `record_decide.Record` と `github.Authored` は項目を足すだけで、既定値を持つので今の呼び出しは変わらない。

構成要素は 2 回のリリース（設計書の決定 9）のどちらで出すかを、小見出しの最初の行の「出す回」に書く。1 回目は閉じる条件の除外と、バージョンの無いリリースとテストの記録、2 回目は種類の既定・モードごとの要否・設定の確かめ・流れの文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── data/workflow-kinds.json *
│   ├── schemas/
│   │   ├── project-workflow.schema.json
│   │   ├── ndf-settings/project.schema.json
│   │   ├── release-verification.schema.json
│   │   └── io/{records.result,project.result,release-steps.args,release-steps.result}.schema.json
│   └── workflow/context.schema.json
├── skills/development-workflow/schemas/project.schema.json
├── src/ndf/
│   ├── adapters/github.py
│   ├── progress/{flow.py *, command.py}
│   └── domains/
│       ├── project/cli.py
│       ├── records/{cli.py, close_decide.py}
│       └── release/{record.py, record_decide.py, cli.py}
└── tests/…（「単体テストの観点」の各表の保存先）
scripts/{check-ndf-contracts.py, contracts_lib/project_flow_doc.py *}
```

依存の向きは一方向である。`progress/flow.py` は `progress/stages.py` と `contracts` だけを読み、`progress/command.py`・`domains/project/cli.py`・`domains/records/cli.py` が `flow.py` を呼ぶ。`domains/records/close_decide.py` は記録の行の選び方を `domains/release/record_decide.py` から借りる（記録の形の持ち主はリリースの側）。`record_decide.py` は純粋な関数だけで、GitHub・git・HTTP を呼ぶのは `record.py` と `adapters/github.py` だけである。`release` から `records` へは依存しない。型の関係は設計文書の「ドメインモデル」の集約の表にあり、ここでは描き直さない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/contracts/data/workflow-kinds.json` | 新設 | 種類の名前と、種類ごとに行わない工程と理由 | — | データ | — |
| `plugins/ndf/contracts/schemas/project-workflow.schema.json` | 変更 | `workflow` の `kind`・`modes`・`close` の形 | — | スキーマ | — |
| `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` | 変更 | 同じ形を設定全体のスキーマに入れる。配布先の宣言に `paths`・`observe` を足す | — | スキーマ | — |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | 上の写し（`check-ndf-contracts.py` の写しの一致の検査の対象） | — | スキーマ | — |
| `plugins/ndf/contracts/schemas/release-verification.schema.json` | 変更 | `version` を任意の空でない文字列にし（#4513 の後の形）、`environments`・`unit`・行の `retest_reason` を足す | — | スキーマ | — |
| `plugins/ndf/contracts/workflow/context.schema.json` | 変更 | 文脈の `product_version` の説明に、出したものを指す値を足す（型は #4513 の形のまま） | — | スキーマ | — |
| `plugins/ndf/contracts/schemas/io/records.result.schema.json` | 変更 | `close-check` の `outputs.conditions`・`verified`・`release` | — | スキーマ | — |
| `plugins/ndf/contracts/schemas/io/project.result.schema.json` | 変更 | `project check` の `items` の `flow_*` と `summary` の件数 | — | スキーマ | — |
| `plugins/ndf/contracts/schemas/io/release-steps.args.schema.json` | 変更 | `record`・`records`・`verification-record` の新しい引数 | — | スキーマ | — |
| `plugins/ndf/contracts/schemas/io/release-steps.result.schema.json` | 変更 | `records` の `outputs` の形（`$defs.records_outputs`）。書き手の `cmd_records` と読み手の `_records_view` が同じ定義を使う | — | スキーマ | — |
| `plugins/ndf/src/ndf/progress/flow.py` | 新設 | 流れの設定を読み、3 層を重ね、誤りと食い違いと閉じる条件を返す | `progress.stages`・`contracts` | モジュール `flow` | `Flow`・`FlowItem`・`CloseRule`・`read_flow`・`kind_defaults`・`merged_stages`・`creation_errors`・`input_missing`・`conflicts`・`close_rules` |
| `plugins/ndf/src/ndf/progress/command.py` | 変更 | スプリントの作成で、重ねた指定を `resolve_enabled` へ渡し、誤りで作らない | `progress.flow` | モジュール `command` | `_declared`（削除）・`_flow_declared`（新設）・`_prepare_new`（中身） |
| `plugins/ndf/src/ndf/domains/project/cli.py` | 変更 | 食い違いを `items` に足す | `progress.flow` | モジュール `cli` | `_check`（中身）・`_flow_items`（新設） |
| `plugins/ndf/src/ndf/domains/records/close_decide.py` | 変更 | 条件ごとの扱い・`verified`・配布先ごとの判定 | `release.record_decide` | モジュール `close_decide` | `verdict`（返りを変える）・`Verdict`・`conditions_of`・`records_verdict`（新設） |
| `plugins/ndf/src/ndf/domains/records/cli.py` | 変更 | `close-check` に `--records` と設定の読み取りを足す | `progress.flow`・`close_decide` | モジュール `cli` | `_close_check`（中身）・`_records_view`・`_add_close_check`（引数） |
| `plugins/ndf/src/ndf/domains/release/record_decide.py` | 変更 | 記録の形・値と SHA の照合・対象の決め方・正の一覧・代表・判定に使う行・`to_test` | — | モジュール `record_decide` | `Record`（項目）・`Correction`・`Push`・`Confirmation` と下の「関数の仕様」の新設の関数 |
| `plugins/ndf/src/ndf/domains/release/record.py` | 変更 | バージョンの無い記録の書き込みと訂正、閉じる手順の読み取り | `record_decide`・`adapters.github`・`adapters.git`・`httpx` | モジュール `record` | `write_unversioned`・`correct_targets`・`read_release`・`write_verification`（引数） ほか下の表 |
| `plugins/ndf/src/ndf/domains/release/cli.py` | 変更 | 引数の受け口と結果の組み立て | `record` | モジュール `cli` | `cmd_record`・`cmd_records`・`cmd_verification_record`（中身）・`_record_args`（新設） |
| `plugins/ndf/src/ndf/adapters/github.py` | 変更 | 照会できない権限を例外で返す読み方、push の記録・CD の実行の一覧・コメントの削除 | githubkit | クラス `GitHub`・`Authored` | `Authored.comment_id`（項目）・`permission`・`_read_permission`（中身）・`permission_strict`・`trusts_strict`・`branch_activity`・`workflow_runs`・`delete_comment` |
| `scripts/contracts_lib/project_flow_doc.py` | 新設 | 流れの文書の生成した範囲を正本から作る | `contracts` のデータ | モジュール `project_flow_doc` | `render`・`replace_generated`・`mismatches` |
| `scripts/check-ndf-contracts.py` | 変更 | `--write` で書き出し、引数なしで違いを違反にする | `project_flow_doc` | モジュール | `main`（引数）・`flow_doc_violations`（新設） |

## 流れの設定の読み手

満たす決定: 決定 1・2・4・8、受け入れ条件 1・3〜5・10・11・13、不変条件 I1・I4・I5

出す回: 1 回目は `Flow`・`CloseRule`・`read_flow`・`close_rules` だけ。2 回目に残りを足す。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Flow` | `progress/flow.py` | 新設 | `@dataclass(frozen=True) Flow(kind: str \| None, stages: Mapping[str, Mapping], modes: Mapping[str, Mapping[str, Mapping]], close: Mapping[str, CloseRule], declared: bool)` | 読んだ流れの設定。`declared` は `workflow` のどれかの項目を書いたか |
| `CloseRule` | `progress/flow.py` | 新設 | `NamedTuple CloseRule(required: bool, reason: str \| None)` | 閉じる条件 1 つの要否と理由 |
| `FlowItem` | `progress/flow.py` | 新設 | `NamedTuple FlowItem(kind: str, where: str, reason: str)` | 誤りか食い違いの 1 件（`kind` は設計書の「設定の確かめが示す食い違い」の 6 つ） |
| `read_flow` | `progress/flow.py` | 新設 | `read_flow(root: Path) -> Flow` | `.ndf/project.json` の `workflow` を読み、形を確かめて `Flow` にする |
| `close_rules` | `progress/flow.py` | 新設 | `close_rules(flow: Flow) -> dict[str, CloseRule]` | `release_verification`・`retrospective_line` の 2 つを、省略を `required: true` で埋めて返す |
| `kind_defaults` | `progress/flow.py` | 新設 | `kind_defaults(kind: str) -> dict[str, dict]` | 種類の既定の工程の指定（理由は「種類 <名前> の既定: …」で始める） |
| `merged_stages` | `progress/flow.py` | 新設 | `merged_stages(flow: Flow, mode: str) -> dict[str, dict]` | 種類の既定 → `stages` → `modes.<モード>.stages` の順に辞書を更新した 1 つの指定 |
| `creation_errors` | `progress/flow.py` | 新設 | `creation_errors(flow: Flow, mode: str, snap: stages.Snapshot, all_snaps: Mapping[str, stages.Snapshot], initial_keys: frozenset[str]) -> list[FlowItem]` | スプリントの作成を止める誤り（`flow_unknown_kind`・`flow_unknown_name`・`flow_gate_disabled`・`flow_input_missing`） |
| `input_missing` | `progress/flow.py` | 新設 | `input_missing(snap: stages.Snapshot, merged: Mapping[str, Mapping], initial_keys: frozenset[str]) -> list[FlowItem]` | 設計書の「工程の入出力の確かめ」の 3 手順 |
| `conflicts` | `progress/flow.py` | 新設 | `conflicts(flow: Flow, all_snaps: Mapping[str, stages.Snapshot], release: Mapping[str, Any], presets: Mapping[str, Any]) -> list[FlowItem]` | 示すだけの食い違い（`flow_close_conflict`・`flow_version_conflict`）と、作成を止める誤りをモードごとに集める |

- `read_flow`
  - 事前条件: `root` はリポジトリの根
  - 事後条件: ファイルか `workflow` が無ければ `Flow(None, {}, {}, {}, False)`。`required: false` には `reason` がある（I5）
  - 副作用: ファイルの読み取り
- `merged_stages`
  - 事前条件: `flow.kind` は `workflow-kinds.json` にある名前か None（`creation_errors` で先に確かめる）
  - 事後条件: 後の層が前の層の同じ工程の指定を丸ごと置き換える（`enabled` と `reason` を混ぜない）
- `creation_errors`
  - 事後条件: 承認ゲートの工程（`StageDef.gates` が空でない）の無効化は、重ねる前の 3 層のどれにあっても 1 件ずつ返す（I1）。名前の照合は、`modes.<モード>.stages` はそのモードの工程の集合、`stages` と種類の既定は `all_snaps` の和集合で行う

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_flow` | 形の誤り | 理由の無い `required: false`・`enabled: false`、型の違い | `contracts.schema_errors`（`project-workflow.schema.json`） | 止める（`contracts.ContractError`） | 呼び出し元の結果 JSON。`progress new` は `invalid`（2）、`close-check` は `precondition`（3）。箇所を `workflow.close.<条件>` の形で載せる | 何も書いていない。直して打ち直せる |
| `read_flow` | 読めない | JSON の構文の誤り | `json.loads` | 止める（同上） | 同上 | 同上 |
| `creation_errors` | 知らない種類 | `workflow.kind` が `workflow-kinds.json` に無い | 種類の照合 | 呼び出し元へ返す | `flow_unknown_kind`。`progress new` は `invalid`（2） | ステートマシンを作らない |
| `creation_errors` | 無い名前 | モードか工程の名前の誤り | 名前の照合 | 呼び出し元へ返す | `flow_unknown_name` | 同上 |
| `creation_errors` | 承認ゲートの無効化 | どれかの層が承認ゲートの工程を `enabled: false` にした | 層ごとの確かめ | 呼び出し元へ返す | `flow_gate_disabled`（`stages.gate_refusal` の文） | 同上 |
| `input_missing` | 入力を作る工程が無い | 無効にした工程だけが後の有効な工程の `inputs` を作る | 3 手順の 3 | 呼び出し元へ返す | `flow_input_missing`。無効にした工程・読めなくなる工程・キー | 同上 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `input_missing` | キーが作成の文脈（`initial_keys`）にある | 誤りにしない |
| `input_missing` | キーが前の有効な工程の `outputs` にある | 誤りにしない |
| `input_missing` | 上のどちらにも無く、前の無効にした工程（流れの設定の 3 層で無効）の `outputs` にある | 誤りにする |
| `input_missing` | どこにも無い（定義の誤り） | 誤りにしない（工程の定義の検査が持つ） |
| `input_missing` | `when` で省く工程・`optional_inputs` | 数えない |

- 繰り返しの終わる条件: モードの工程表を 1 回なめる。計算量は工程の数 × キーの数で、数十以下

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 種類の名前と省く工程 | 設定値 | `contracts/data/workflow-kinds.json` | `docs` の「リファクタリング」だけ（決定 3） |
| 閉じる条件の名前 | 定数 | `project-workflow.schema.json` の `close` の `properties`（コードはスキーマから読み、名前を文字列リテラルで持たない） | `release_verification`・`retrospective_line` |
| 作成の文脈のキー | 定数 | `progress/command.py` が文脈を組んだ後の辞書のキー（`initial_keys` として渡す） | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `read_flow` | `progress/command.py` の `_declared`・`_ndf_settings_errors` | `grep -n "_declared\|workflow" progress/command.py` | 置き換える。`_declared` の形の確かめはスキーマへ移し、`_declared` は消す |
| `merged_stages` | 設定の併合のライブラリ（設計書の LIB1・LIB2） | 設計書の「外部の調査」 | 使わない（辞書の更新 2 回で足りる。決定 4） |
| `creation_errors` | `stages.unknown_stages`・`stages.gate_refusal` | `grep -n "def unknown_stages\|def gate_refusal" progress/stages.py` | 使う |
| `input_missing` | `StageDef.inputs`・`outputs`・`optional_inputs`・`when` | `progress/stages.py` の `StageDef` | 使う |
| `conflicts` | `branch-strategy-presets.json` の `kinds.<種類>.section.release.version_store.kind` | `grep -n version_store contracts/data/branch-strategy-presets.json` | 使う |

### 単体テストの観点

保存先: `plugins/ndf/tests/progress/test_flow.py`（新設）

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `read_flow` | 4 項目を読む | ファイル無し・`workflow` 無しで `declared` が偽 | 理由の無い `required: false` で `ContractError`、構文の誤りで `ContractError` |
| `close_rules` | 外した条件の理由を返す | 省略と `required: true` が同じ | — |
| `merged_stages` | 3 層の順に勝つ | 同じ工程を有効に戻す層がある | — |
| `creation_errors` | 誤りの無い設定で空 | 共通の `stages` に別のモードだけの工程があっても空 | 知らない種類・無いモード・モードに無い工程・承認ゲートを別の層で有効に戻しても `flow_gate_disabled`（テスト用の工程定義の `gates` で決まる） |
| `input_missing` | 前の有効な工程が作るキーは誤りにしない | `optional_inputs` だけを作る工程・`when` で省く工程 | 唯一の作り手を無効にすると `flow_input_missing` |
| `conflicts` | 食い違いの無い設定で空 | 工程表に無い工程・`when` で省く工程は `flow_close_conflict` にしない | 無効にしたリリース後テストと `release_verification` の要求で `flow_close_conflict`、`none` と `versioned: true` で `flow_version_conflict` |

## スプリントの作成

満たす決定: 決定 4・8、受け入れ条件 1・3・4・5・11、不変条件 I1・I4

出す回: 2 回目

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_flow_declared` | `progress/command.py` | 新設 | `_flow_declared(cwd: Path, mode: str, snap: stages.Snapshot, initial_keys: frozenset[str]) -> dict[str, Any]` | `read_flow` → `creation_errors` → `merged_stages` を通し、`resolve_enabled` へ渡す指定を返す |
| `_prepare_new` | `progress/command.py` | 変更 | 今と同じ | `_declared(cwd)` の呼び出しを `_flow_declared` に替え、文脈を組んでから呼ぶ |

- `_flow_declared`
  - 事後条件: 流れの設定が無ければ `{}`（今の `_declared` が `workflow.stages` 無しで返す値と同じ。I4）。`resolve_enabled` の 3 つの順位と保存の形（`source` は `project`）は変えない
  - 副作用: ファイルの読み取り

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_flow_declared` | 作成を止める誤り | `creation_errors` が 1 件以上 | `_flow_declared` | 止める（`definition.WorkflowDefError`。誤りの並びを載せる） | `ndf progress new` の結果が `invalid`（2）。`items` に `FlowItem` を並べる | ステートマシンを作らない。直して打ち直せる |

### 単体テストの観点

保存先: `plugins/ndf/tests/progress/test_command.py`

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_prepare_new` | `kind: docs` で「リファクタリング」が理由つきで無効の行になる。モードの下の指定はそのモードだけを無効にする | 流れの設定が無いとき、全モードの enablement が変更の前と同じ | 名前の誤り・承認ゲートの無効化・入力を作る工程の無効化でステートマシンを作らない |

## 設定の確かめ

満たす決定: 決定 8、受け入れ条件 10

出す回: 2 回目

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_flow_items` | `domains/project/cli.py` | 新設 | `_flow_items(root: Path) -> list[dict[str, Any]]` | 全モードの定義と今の `branch_strategy.release` で `flow.conflicts` を呼び、`items` の形にする |
| `_check` | `domains/project/cli.py` | 変更 | 今と同じ | `items` に `_flow_items` を足し、`summary` に件数を足す。終了コードは設定の新しさだけで決める |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_flow_items` | 流れの設定の形の誤り | `read_flow` の `ContractError` | `_flow_items` | 既定値で続ける（`flow_unknown_name` と同じ並びに、形の誤りの 1 件として載せる） | 終了コードは変えない（決定 8） | 読み取りだけ。再実行できる |

### 単体テストの観点

保存先: `plugins/ndf/tests/domains/project/test_project_cli.py`

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_check` | (a)〜(d) のそれぞれで箇所と理由が `items` に出る | 食い違いの無い設定で出ず、終了コードが変わらない | 形の誤りでも終了コードが変わらない |

## 閉じる判定

満たす決定: 決定 5・7、受け入れ条件 1・6・7・8・9、不変条件 I2

出す回: 1 回目

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Verdict` | `records/close_decide.py` | 新設 | `NamedTuple Verdict(decided: str, reason: str, verified: bool, release: dict[str, Any] \| None)` | 判定・理由・テストで確認できたか・反映の判定 |
| `verdict` | `records/close_decide.py` | 変更 | `verdict(issue: int, verification: Optional[Iterable[Mapping]], enforced: bool) -> Verdict` | 今の判定に `verified`（渡した記録にその課題の行があり、すべて合格）を足す。第 3 引数は `require` から `enforced` に名前を変える |
| `conditions_of` | `records/close_decide.py` | 新設 | `conditions_of(rules: Mapping[str, CloseRule], require: bool) -> dict[str, dict]` | 設計書の「close-check の結果」の `conditions` の 6 項目 |
| `records_verdict` | `records/close_decide.py` | 新設 | `records_verdict(issue: int, view: Mapping[str, Any], enforced: bool) -> Verdict` | `records` の出力 1 つから、配布先と課題の組ごとに判定に使う行を選び、設計書の判定の表で決める |
| `_records_view` | `records/cli.py` | 新設 | `_records_view(path: Path) -> tuple[dict \| None, str \| None]` | `--records` のファイルを読み、`release-steps records` の結果 JSON として確かめて `outputs` を返す（下の事後条件） |
| `_close_check` | `records/cli.py` | 変更 | 今と同じ | 設定を読み、引数の組を確かめ、`verdict` か `records_verdict` を呼び、`outputs.conditions`・`verified`・`release` を返す |

- `records_verdict`
  - 事前条件: `view` は `ndf release-steps records --version` の出力の `outputs`（代表・`releases`・`targets`・`verifications`）
  - 事後条件: `targets` を持たない代表（バージョン付き）では `release` が `null` で、テストの記録は代表と `version`・`unit` が同じ最後のものを `verdict` と同じに判定する
  - 事後条件: `release.complete` と `release.missing` は、代表と `version`・`release`（本番の PR）・`unit`・`commit` が同じで、`targets` が正の一覧と同じリリース記録の `environments` だけで決める（絞るのは `complete_of`）。同じ値を使う別の配布単位や別の本番の PR の記録では、未反映の配布先を埋めない
- `_records_view`
  - 事後条件: 結果 JSON 全体を `release-steps.result.schema.json` で確かめる。通すのは、`tool` が `release-steps`、`status` が `ok`、`outputs.command` が `records` で、`outputs` が `$defs.records_outputs`（`command`・`selection`・`release`・`releases`・`targets`・`verifications`・`bodies`・`to_test`）に合う結果だけである。`cmd_records` は同じ `$defs.records_outputs` に合う `outputs` を書き、契約の検査（`check-ndf-contracts.py`）が書き手の出力の例をこの定義で確かめる。`records.result.schema.json` は `close-check` の結果の形で、入力の検証には使わない
  - 失敗: どれかが合わなければ `(None, 理由)` を返し、`_close_check` が `precondition`（3）で止まる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_close_check` | 流れの設定の形の誤り | `read_flow` の `ContractError` | `_close_check` | 止める | `precondition`（3）。閉じない | 直して打ち直せる |
| `_close_check` | 引数の組の誤り | `--records` と `--verification` を両方渡した。保存先が `none` で `--verification` を渡した | `_close_check` | 止める | `precondition`（3）。`--records` を渡すよう示す | 同上 |
| `_records_view` | 読めない・形が違う | ファイルが無い、JSON でない、`release-steps records` の成功の結果でない（`tool`・`status`・`outputs.command` が違う）、`outputs` が `$defs.records_outputs` に合わない | `_records_view` | 止める | `precondition`（3） | 同上 |
| `records_verdict` | 反映がそろわない | リリース記録が無い、`release.complete` が偽 | `records_verdict` | 閉じない | `refused`（1）。`release.missing` を載せる | 残りの配布先へ反映して `record` を打ってから打ち直す |

### 処理の手順

判定の表は設計書の「close-check の結果」にある。ここでは表を評価する順を決める（悪い方を優先する）。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `records_verdict` | 1. 代表が無いか `complete` が偽 | `KEEP` |
| `records_verdict` | 2. どれかの対象の配布先で選んだ行が `failed` | `KEEP` |
| `records_verdict` | 3. 2 に当たらず、どれかで `not_run` | `ASK` |
| `records_verdict` | 4. 行の無い配布先があり `enforced` | `KEEP` |
| `records_verdict` | 5. 行の無い配布先があり `enforced` が偽 | `CLOSE`、`verified` は偽 |
| `records_verdict` | 6. すべて `passed` | `CLOSE`、`verified` は真 |

- 繰り返しの終わる条件: 対象の配布先ごとに `record_decide.decisive_row` を 1 回呼ぶ

### 単体テストの観点

保存先: `plugins/ndf/tests/domains/records/test_close.py`・`plugins/ndf/tests/domains/records/test_cli.py`

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `conditions_of` | 外した条件の `excluded`・`reason`、`enforced` が `--require` に従う | 省略と `required: true` が同じ | — |
| `verdict` | 合格だけで `verified` が真 | 記録なしで `--require` を付けても、外した設定では閉じてよく `verified` は偽 | 両方の条件を外しても不合格の記録で閉じない（I2） |
| `records_verdict` | 全対象で合格なら閉じてよく `verified` が真 | 別の課題の行しかない記録では `verified` が偽。同じ配布先で別の課題だけを再テストした記録が後にあっても、その課題の不合格で閉じない | `complete` が偽で閉じない |
| `_close_check` | 流れの設定が無く `--records` を渡さなければ今と同じ（受け入れ条件 1） | 記録を渡さない呼び出し（デプロイを飛ばした経路）はリリース記録を求めない | 両方の引数・`none` での `--verification`・形の誤りで `precondition` |

## リリースの記録: 記録の形と純粋な判定

満たす決定: 決定 6・7、受け入れ条件 2・6〜9

出す回: 1 回目

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Record` | `release/record_decide.py` | 変更 | `NamedTuple Record(version, release, included, unit=None, environments: tuple[str, ...] = (), targets: tuple[str, ...] \| None = None, commit: str \| None = None, push: Push \| None = None, confirmation: Mapping[str, str] \| None = None, evidence: Mapping[str, Evidence] \| None = None)` | バージョンの無い記録の項目を足す（バージョン付きの記録では足した項目を書かない） |
| `Evidence` | `release/record_decide.py` | 新設 | `NamedTuple Evidence(url: str, value: str)` | 実環境の確認の証跡 1 つ。`url` は読んだ `observe.url`、`value` は `observe.pointer` が指した値（文字列） |
| `render_record` | `release/record_decide.py` | 変更 | 今と同じ | 足した項目を記録の JSON に書く。`evidence` は JSON のキー `evidence`（配布先の名前 → `{"url": <文字列>, "value": <文字列>}`）で、`confirmation` が `observed` の配布先にだけ書く |
| `parse_record` | `release/record_decide.py` | 変更 | 今と同じ | 足した項目を読む。形が違えば None（今と同じ）。`confirmation` が `observed` の配布先に `evidence` が無い記録、`observed` でない配布先に `evidence` がある記録、`url`・`value` が文字列でない記録も形の違いとして None にする |
| `Push` | `release/record_decide.py` | 新設 | `NamedTuple Push(before: str, after: str)` | 反映したコミットを本番ブランチへ入れた 1 回の push |
| `Correction` | `release/record_decide.py` | 新設 | `NamedTuple Correction(version: str, commit: str, targets: tuple[str, ...], previous: tuple[str, ...], reason: str)` | 対象の一覧の訂正の記録 |
| `render_correction`・`parse_correction` | `release/record_decide.py` | 新設 | `render_correction(c: Correction) -> str`・`parse_correction(body: str) -> Correction \| None` | 訂正の記録のコメントの本文と読み取り（マーカー `CORRECTION_MARK`） |
| `value_matches` | `release/record_decide.py` | 新設 | `value_matches(value: str, sha: str) -> bool` | 確かめ 2。値の 16 進の語のどれかが SHA の先頭 7 文字以上と一致するか |
| `production_deliveries` | `release/record_decide.py` | 新設 | `production_deliveries(rows: Sequence[Mapping]) -> list[Mapping]` | 決定 7 の「本番の配布先」の行 |
| `glob_matches` | `release/record_decide.py` | 新設 | `glob_matches(pattern: str, path: str) -> bool` | git の pathspec の `:(glob)` と同じ一致（`**`・`*`・`?`・`[...]`、`*` は `/` を越えない） |
| `targets_of` | `release/record_decide.py` | 新設 | `targets_of(rows: Sequence[Mapping], pr_paths: frozenset[str], push_paths: frozenset[str]) -> tuple[str, ...]` | 確かめ 3。`paths` を持たない配布先と、両方の差分に出るパスが当たる配布先 |
| `push_of` | `release/record_decide.py` | 新設 | `push_of(activities: Iterable[Mapping], landed: frozenset[str]) -> Push \| None` | 「反映したコミットを含む push」の手順 2（`after` が集合にあり `before` が無い最初の更新。`after` が集合の外の更新で打ち切る） |
| `authoritative_targets` | `release/record_decide.py` | 新設 | `authoritative_targets(found: Sequence[Record], corrections: Sequence[Correction], version: str) -> tuple[str, ...] \| None` | 「正の `targets`」。最後の訂正、無ければ同じ値の最初の記録の一覧 |
| `value_commit_conflict` | `release/record_decide.py` | 新設 | `value_commit_conflict(found: Sequence[Record], corrections: Sequence[Correction], commit: str, version: str) -> tuple[str, str] \| None` | 確かめ 6。同じコミットに別の値の記録か、同じ値に別のコミットの記録があれば、その記録の `(version, commit)` |
| `value_commit` | `release/record_decide.py` | 新設 | `value_commit(found: Sequence[Record], version: str) -> str \| None` | 値のコミット（同じ値の最初の記録の `commit`）。`authoritative_targets`・`has_unversioned`・`representative`・`complete_of` は、`commit` がこれと違う記録を数えない |
| `has_unversioned` | `release/record_decide.py` | 新設 | `has_unversioned(found: Sequence[Record], version: str, environments: tuple[str, ...], targets: tuple[str, ...]) -> bool` | 重ねない判定（同じ値・同じ `environments`・正の `targets`） |
| `confirm` | `release/record_decide.py` | 新設 | `confirm(delivery: Mapping, run: Mapping \| None, observed: Any, after: str) -> Confirmation` | 「配布先ごとの反映の確かめ」の表を 1 回評価する（`Confirmation(state, kind, reason, evidence: Evidence \| None)`。`state` は `ok`・`failed`・`pending`。`kind` が `observed` のときだけ `evidence` を持つ） |
| `json_pointer` | `release/record_decide.py` | 新設 | `json_pointer(doc: Any, pointer: str) -> Any` | RFC 6901 の 1 つの値（無ければ `_MISSING`） |
| `representative` | `release/record_decide.py` | 新設 | `representative(found: Sequence[Record], targets: tuple[str, ...] \| None) -> Record \| None` | 読み取りの手順 5 |
| `decisive_row` | `release/record_decide.py` | 新設 | `decisive_row(verifications: Sequence[Mapping], environment: str \| None, issue: int) -> Mapping \| None` | 「判定に使う行」。組の行を古い順にたどり、不合格の後は `retest_reason` のある行だけで置き換える |
| `to_test` | `release/record_decide.py` | 新設 | `to_test(released: Sequence[str], issues: Sequence[int], verifications: Sequence[Mapping]) -> list[dict]` | 「テストする組の引き方」の表 |
| `complete_of` | `release/record_decide.py` | 新設 | `complete_of(releases: Sequence[Record], representative: Record, targets: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]` | 代表と `version`・`release`・`unit`・`commit` が同じで `targets` が正の一覧と同じ記録だけについて、`environments` の和集合が `targets` を覆うかと、まだの配布先 |

- `glob_matches`
  - 事後条件: 移動したファイルの前後のパスの両方を呼び出し元が渡す（`--no-renames` の出力）。大文字と小文字を区別する
- `confirm`
  - 事後条件: `result` を宣言した配布先は、`run` が失敗なら `failed`。成功なら `ok`/`cd`。未確定で `observed` が push の `after` と一致すれば `ok`/`observed`、それ以外は `pending` と理由。`result` を宣言していなければ `ok`/`attested`

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `CORRECTION_MARK` | 定数 | `release/record_decide.py`（`RECORD_MARK` の隣） | `<!-- ndf:release-targets-correction -->` の形 |
| SHA の一致の最小の長さ | 定数 | `release/record_decide.py` の `SHA_PREFIX`（確かめ 2 と `confirm` の両方がこれを読む） | 7 |
| 反映の区分 | 定数 | `records.result.schema.json` の `confirmation` の `enum` | `cd`・`observed`・`attested` |

### 単体テストの観点

保存先: `plugins/ndf/tests/domains/release/test_release_record.py`

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `value_commit_conflict` | 衝突の無い記録で None | 同じコミットを違う長さの SHA で書いた値で止まる | 2 つの SHA を含む同じ値を別のコミットで打つと止まる |
| `value_matches` | `main abc1234` と SHA | 6 文字の語は数えない、大文字の 16 進 | 含まない値 |
| `targets_of` | `paths` の無い配布先は常に入る | 移動の前のパスで当たる。push の差分に出ないパス（打ち消し）は入らない | 空の結果 |
| `glob_matches` | `web/**` | `**/` が 0 段に当たる。`*` が `/` を越えない | — |
| `push_of` | merge queue がまとめた途中の PR の push | 新しい順で `after` が集合の外に出たら打ち切る | 見つからなければ None |
| `parse_record` | `evidence` つきの `observed` の記録を `render_record` の出力から読み戻す | バージョン付きの記録は足した項目なしで今と同じに読む | `observed` で `evidence` の無い記録、`evidence` の値が文字列でない記録は None |
| `authoritative_targets` | 最初の記録の一覧 | 最後の訂正が勝つ | 記録が無ければ None |
| `confirm` | `cd`・`observed`・`attested` | `pointer` の外の SHA は数えない | 失敗・`observe` 無し・不一致は `failed` か `pending` |
| `decisive_row` | 後の行が前の行に代わる | 不合格の後の理由の無い行は飛ばす | — |
| `to_test` | 行の無い組と保留の組が入る | 一部の課題だけを確かめた配布先の残りの課題が入る | 合格と不合格の組は入らない |

## リリースの記録: 書き込みと読み取り

満たす決定: 決定 6・7、受け入れ条件 2・6〜9

出す回: 1 回目

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Unversioned` | `release/record.py` | 新設 | `NamedTuple Unversioned(version: str, environments: tuple[str, ...], targets: tuple[str, ...], observed: frozenset[str], production_sha: str \| None)` | `record --version` の引数 |
| `Outcome` | `release/record.py` | 新設 | `NamedTuple Outcome(record: Record, recorded: tuple[str, ...], kept: tuple[str, ...], failed: dict[str, str], pending: dict[str, str], complete: bool, missing: tuple[str, ...], written: list[Written])` | `record` の結果の `outputs` の元 |
| `write_unversioned` | `release/record.py` | 新設 | `write_unversioned(hub: GitHub, repo_git: Git, number: int, production: str, release: Mapping[str, Any], args: Unversioned) -> Outcome` | 設計書の確かめ 0〜8 と書く順の手順 1〜4 |
| `correct_targets` | `release/record.py` | 新設 | `correct_targets(hub: GitHub, number: int, production: str, version: str, targets: tuple[str, ...], reason: str, production_sha: str \| None) -> str` | 確かめ 1・2・6 と訂正だけの確かめを通し、記録の PR へ訂正の記録を 1 件書く。書いたコメントの URL を返す |
| `_landed_commit` | `release/record.py` | 新設 | `_landed_commit(hub: GitHub, number: int, production: str, production_sha: str \| None) -> tuple[Pull, str]` | 確かめ 1。`released` の着地の部分を使い、head の名前を問わずに反映したコミットを返す |
| `_changed_paths` | `release/record.py` | 新設 | `_changed_paths(repo_git: Git, pull: Pull, commit: str, push: Push) -> tuple[frozenset[str], frozenset[str]]` | 「対象を決める変更の範囲」の 2 つの差分（`git diff --name-only --no-renames`、起動 2 回） |
| `_find_push` | `release/record.py` | 新設 | `_find_push(hub: GitHub, repo_git: Git, production: str, commit: str, known: Push \| None) -> Push` | 既存の記録の `push` があれば使い、無ければ `git rev-list --first-parent`（起動 1 回）と `branch_activity` で決める |
| `_confirm_delivery` | `release/record.py` | 新設 | `_confirm_delivery(hub: GitHub, root: Path, delivery: Mapping, production: str, after: str, observed: bool, wait: bool) -> Confirmation` | 「CD の結果の待ち」の 1〜4 を 1 つの配布先で行う。CD の結果は `result.workflow` なら `workflow_runs`、`result.command` なら `_command_result` で読む |
| `_command_result` | `release/record.py` | 新設 | `_command_result(root: Path, command: str, after: str, timeout: int) -> Mapping` | `{revision}` を `after` に置き換えたコマンドをリポジトリの根で 1 回実行し、終了コード 0・75・1・それ以外を、成功・未完了・失敗・未確定の `run` の形（`status`・`conclusion`）にする（cd-setup.md の契約） |
| `_observe` | `release/record.py` | 新設 | `_observe(delivery: Mapping) -> tuple[Any, str]` | `observe.url` を `httpx` で 1 回読み、`pointer` の値と読んだ URL を返す |
| `_write_one` | `release/record.py` | 新設 | `_write_one(hub: GitHub, made: Record, holder: int, others: Sequence[int]) -> list[Written]` | 1 つの配布先の記録を書く順の手順 1〜4 で書き、重なりを自分のコメントの削除で戻す |
| `_candidates` | `release/record.py` | 新設 | `_candidates(hub: GitHub, number: int, select: Selection) -> tuple[list[Authored], list[Record], list[Correction], list[dict]]` | 読み取りの手順 1〜3（マーカーで候補にし、JSON で今回のリリースに絞り、リリース記録とテストの記録の書き手を `trusts_strict` で照会してから、信用したリリース記録の値のコミットに一致する訂正だけを照会する） |
| `Selection` | `release/record.py` | 新設 | `NamedTuple Selection(version: str \| None, release: int \| None, unit: str \| None)` | `records` の絞り込みの引数 |
| `read_release` | `release/record.py` | 新設 | `read_release(hub: GitHub, number: int, select: Selection, bodies: bool, issues: Sequence[int] \| None) -> dict[str, Any]` | 読み取りの手順 1〜6 と `to_test`。`records` の新しい `outputs` を返す |
| `pr_body_only` | `release/record.py` | 新設 | `pr_body_only(hub: GitHub, number: int) -> dict[str, Any]` | `--pr-body-only`。その PR の本文と `trusted` だけを返す |
| `write_verification` | `release/record.py` | 変更 | `write_verification(hub, number, payload, names=_no_names, unversioned: bool = False) -> str` | `unversioned` なら、記録の PR・`environments` の反映の記録・`unit`・`retest_reason` を確かめてから書く |
| `cmd_record` | `release/cli.py` | 変更 | 今と同じ | 引数の組（確かめ 0）で `write`・`write_unversioned`・`correct_targets` を呼び分け、`record` の終わり方の表で状態を決める |
| `_record_args` | `release/cli.py` | 新設 | `_record_args(args: argparse.Namespace, strategy: Strategy) -> Unversioned \| None` | 確かめ 0。保存先が `none` でなければ `--version` 系を断る |
| `cmd_records` | `release/cli.py` | 変更 | 今と同じ | 新しい引数があれば `read_release` か `pr_body_only`、無ければ今の `read` |
| `cmd_verification_record` | `release/cli.py` | 変更 | 今と同じ | 保存先が `none` なら `unversioned=True` で呼ぶ |

- `write_unversioned`
  - 事前条件: 保存先が `none`。`args.environments` が 1 つ以上
  - 事後条件: 確かめ 0〜6 で止まれば、どの PR にも書いていない。書いた配布先ごとに、含めた PR の各々に、同じ値・同じ `environments`・正の `targets` の信用する記録がちょうど 1 件ある
  - 副作用: GitHub のコメントの読み取り・書き込み・自分が書いたコメントの削除、git の起動 3 回、CD の実行の照会、`observe.url` の HTTP の読み取り
- `read_release`
  - 事後条件: 照会できない書き手が候補か本文にいれば、何も選ばずに `PermissionUnknown` を投げる。候補でないコメントの書き手は照会しない
  - 副作用: GitHub の読み取りだけ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_record_args` | 引数の組の誤り | 確かめ 0 の「止まるとき」 | `_record_args` | 止める | `precondition`（3） | 何も書いていない。直して打ち直せる |
| `_landed_commit` | 本番へ入っていない | 未マージで `--production-sha` が無いか正規の squash でない | `landed_squash` | 止める（`NotReleased`） | 今と同じ | 同上 |
| `write_unversioned` | 値と反映したコミットが対応しない | 確かめ 2 | `value_matches` | 止める | `precondition`（3）。反映したコミットの SHA を載せる | 同上 |
| `_find_push` | push を決められない | アクティビティにまだ載っていない | `push_of` が None | 止める | `precondition`（3）。時間を置いて打ち直すよう示す | 同上 |
| `write_unversioned` | 対象が空・正と違う・同じコミットに別の値 | 確かめ 3・5・6 | 各確かめ | 止める | `precondition`（3）。正の一覧と訂正の打ち方、または既にある値を載せる | 同上 |
| `_confirm_delivery` | CD が失敗 | 実行の `conclusion` が `success` 以外 | `confirm` | 呼び出し元へ返す（`failed`） | 書けた配布先があれば `ok`（0）、無ければ `refused`（1） | その配布先は打ち直さず、配布の失敗として報告する |
| `_confirm_delivery` | 未確定 | 実行が無い・未完了、`observe` で確かめられない、待ちの上限（`result.timeout_seconds`） | `confirm` | 呼び出し元へ返す（`pending`） | すべて未確定なら `precondition`（3）で `items` に理由 | その配布先だけで打ち直せる |
| `_observe` | 読めない | 接続・HTTP の失敗、JSON でない | `httpx` の例外・`json` | 既定値で続ける（未確定の理由にする） | 上と同じ | 同上 |
| `_write_one` | 投稿の失敗 | GitHub の失敗 | `Publisher.comment` | 止める（`GitHubError`） | `failed`（今の `release-steps` の失敗の形） | 打ち直すと、書けた PR は重ねない判定で飛ばす |
| `_write_one` | 並行した書き込みに負けた | 手順 2 で正の `targets` か、値とコミットの対応が自分と違う | 読み直し | 自分のコメントを消して止める | `precondition`（3）。正の一覧か既にある値を載せる | 正しい引数で打ち直せる |
| `_candidates`・`read_release` | 権限を照会できない | 403・404（上限でない） | `GitHub.permission_strict` | 止める（`PermissionUnknown`） | `failed`。照会できなかった書き手の名前を載せる | 閉じる手順は課題を閉じずに「失敗」と報告する |
| `correct_targets` | 訂正できない | 宣言がある・同じ値の信用する記録が無い・引数の組の誤り | `correct_targets` | 止める | `precondition`（3） | 先に `record` を打つか、引数を直す |
| `write_verification` | 書けない | `environments` が無い、反映の記録が無い配布先、記録の PR でない、不合格の組に `retest_reason` が無い | `write_verification` | 止める（`RecordError`・`RecordElsewhere`） | `precondition`（3）。記録の PR の番号か不合格の組を `items` に入れる | 直して打ち直せる |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 1 回の `record` の中の配布先ごとの確かめ 8 と書き込み | 含めた PR のコメント | 取らない。配布先ごとにスレッド（`concurrent.futures.ThreadPoolExecutor`、本数は `--environment` の数）で走らせ、書き込みの重なりは書いた後の読み直しで戻す | 同じ引数の打ち直しは確かめ 7 で `kept` になり、何も書かない |
| 別々に打った `record`（同じ値・違う配布先、同じ引数、違う `--target`、違う長さの SHA の値） | 記録の PR のコメントの順 | 取らない（GitHub のコメントに条件つきの書き込みが無い。設計書の「記録の照会の回数」の採らなかった案）。手順 2・4 の読み直しで、コメントの順の前にある記録を勝たせる | 負けた側は自分のコメントを消して止まり、正しい引数の打ち直しで残りの PR へ書く |
| `record --correct-targets` を並行して打つ | 記録の PR のコメントの順 | 取らない。最後の訂正が正 | 誤りはもう一度訂正する |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_record` | `outputs` の `complete`・`missing`・`recorded`・`kept`・`failed`・`pending`、`items` に配布先ごとの未確定の理由 | 結果 JSON | token |
| `_write_one` | 記録の JSON（`version`・`environments`・`targets`・`commit`・`push`・`confirmation`、`observed` の配布先なら `evidence` に読んだ URL と値） | PR のコメント | `observe.url` の応答の本文。`evidence` に載せるのは、宣言にある認証の要らない URL と、`pointer` が指した 1 つの値（SHA）だけで、公開の PR に載せてよい |
| `read_release` | `releases`・`targets`・`bodies`（`trusted` つき）・`to_test` | 結果 JSON | 信用しない書き手の本文から集めた課題 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_landed_commit` | `record.released`・`_landed_by`・`landed_squash` | `grep -n "def released\|def _landed_by\|def landed_squash" record.py` | 着地の部分を使う。`released` は head の名前からバージョンを読むので、着地だけを関数に切り出して両方から呼ぶ |
| `_changed_paths`・`_find_push` | `Git.changed_files`・`Git.log_of` | `grep -n "def changed_files\|def log_of" adapters/git.py` | 使う（`--no-renames` は `options` で渡す） |
| `_write_one` | `Publisher.comment`・`record._require_trusted_writer` | `grep -n "def comment" adapters/publish.py` | 使う |
| `_candidates` | `record._trusted_bodies` | 同上 | 使えない（全部のコメントの書き手を先に照会する）。手順を変えた別の関数にし、今の `read` は残す |
| `_command_result` | `result.command` の既存の読み手 | `grep -rn "{revision}" plugins/ndf/src` | 無い（契約は cd-setup.md にだけある）。`subprocess.run` で新設する |
| `_observe` | `httpx`（`plugins/ndf/pyproject.toml` の依存） | `grep -n httpx plugins/ndf/pyproject.toml` | 使う |
| `glob_matches` | `fnmatch`・`PurePath.full_match` | 標準ライブラリの説明 | 使えない（`fnmatch` の `*` は `/` を越える。`full_match` は 3.13 からで、下限は 3.11） |

### 単体テストの観点

保存先: `plugins/ndf/tests/domains/release/test_release_record.py`（GitHub と git は偽物、HTTP は `httpx.MockTransport`）

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_command_result` | 終了コード 0 で成功、1 で失敗 | 75 で未完了、それ以外で未確定 | 時間の上限で未確定（実行するのは偽の `subprocess.run`。子のプロセスを起動しない） |
| `write_unversioned` | 含めた PR の各々へ書き、作業の PR が無ければ本番の PR へ書く | 同じ引数の打ち直しで CD を照会しない。2 つの配布先の一方だけ完了で `recorded` と `pending` に分ける。並行した同じ引数の 2 回で記録が 1 件に戻る | 確かめ 0〜6 の各行で何も書かない。すべて未確定で `precondition`、書けた配布先が無く失敗があれば `refused` |
| `correct_targets` | 訂正の記録を足し、前の記録を消さない | 訂正の後、新しい一覧で打ち直した配布先では前のテストの記録が判定に使われる | 宣言がある・記録が無い値で止まる |
| `read_release` | `--version` の値の記録を代表にし、`--unit` の単位だけを使う。同じ記録が 2 件残っていても 1 件として数える | 正と違う記録が最後にあっても正の記録が代表。別のリリースの記録の書き手の照会が 403 でも読める | 候補の書き手の照会が 403 で失敗。`read` の書き手の本文は `trusted` が偽 |
| `_candidates` | 信用したリリース記録のコミットの訂正を残す | 信用されない書き手のリリース記録にだけ対応する訂正は、書き手を照会せずに除く（照会が 403 でも止まらない） | 信用したリリース記録のコミットの訂正の書き手が 403 で `PermissionUnknown` |
| `pr_body_only` | その PR の本文だけを返す | 別のリリース記録を持つ PR | — |
| `write_verification` | `environments` つきで書ける | `retest_reason` のある不合格のテストし直し | `environments` 無し・反映の記録の無い配布先・記録の PR でない・理由の無いテストし直しで書かない |

## GitHub の読み書き

満たす決定: 決定 6・7、受け入れ条件 9

出す回: 1 回目

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Authored` | `adapters/github.py` | 変更 | 項目 `comment_id: int = 0` を足す（REST の `id`） | 自分が書いたコメントを消すため |
| `PermissionUnknown` | `adapters/github.py` | 新設 | `class PermissionUnknown(GitHubError)`、属性 `login: str` | 照会できない書き手 |
| `permission_strict` | `adapters/github.py` | 新設 | `permission_strict(self, login: str) -> str` | 上限でない 403・404 を `PermissionUnknown` にする。GitHub が 200 で返した値は `permission` と同じ保存（`_permissions`）に入れて 1 人 1 回にする |
| `permission` | `adapters/github.py` | 変更 | 今と同じ | 返す値は今と同じ（403・404 は `none`）。ただし、403・404 で変換した `none` は `_permissions` に入れず、照会できなかった書き手の集合（`_unreadable`）に入れる。GitHub が返した `none` と、照会できずに `none` と読んだものを保存の上で分けるためである |
| `_read_permission` | `adapters/github.py` | 変更 | `_read_permission(self, login: str) -> str \| None` | 上限でない 403・404 で `None` を返す（変換しない）。`none` への変換は `permission` が行う |

- `permission_strict`
  - 事後条件: `login` が `_unreadable` にあれば、照会し直さずに `PermissionUnknown` を投げる。同じ実行で `permission` が先に照会に失敗していても、照会できなかった書き手を権限が無い書き手として除かない
  - 事後条件: `_permissions` にある値は、`none` を含めて GitHub が 200 で返した値だけで、そのまま返す
| `trusts_strict` | `adapters/github.py` | 新設 | `trusts_strict(self, authored: Authored) -> bool` | `trusts` と同じ区分の扱いで、照会に `permission_strict` を使う |
| `branch_activity` | `adapters/github.py` | 新設 | `branch_activity(self, branch: str) -> Iterator[dict]` | `GET /repos/{repo}/activity?ref=refs/heads/<branch>` を新しい順に 1 ページずつ返す（呼び出し元が打ち切れる） |
| `workflow_runs` | `adapters/github.py` | 新設 | `workflow_runs(self, workflow: str, branch: str, commit: str) -> list[dict]` | `GET /actions/workflows/<workflow>/runs?branch=&head_sha=` |
| `delete_comment` | `adapters/github.py` | 新設 | `delete_comment(self, comment_id: int) -> None` | `DELETE /issues/comments/<id>` |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `permission_strict` | 照会できない | 上限でない 403・404 | `rest` の `GitHubError` | 止める（`PermissionUnknown`） | 呼び出し元が `failed` にする | 権限を直して打ち直せる |
| `branch_activity` | ページの上限 | `github.list-pages` を超えた | ページ数 | 止める（`GitHubError`） | `failed` | 打ち直せる |
| `delete_comment` | 消せない | 権限・ネットワーク | `rest` | 止める（`GitHubError`） | `failed`。消せなかったコメントの URL を `items` に載せる | 消せなかったコメントは残してよい。読み手は重複と正でない記録を数えない（設計書の「失敗や重なりの後の動き」）ので、判定は 1 回で書けたときと同じになる。打ち直すと確かめ 7 で `kept` になり、何も書かず消しもしない |

### 単体テストの観点

保存先: `plugins/ndf/tests/adapters/test_github.py`

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `permission_strict` | 200 の権限を返し、2 回目は照会しない | 200 で返った `none` は `none` を返す。先に `permission` が 403 で `none` と読んだ書き手は、照会し直さずに `PermissionUnknown` | 403・404 で `PermissionUnknown`、上限は今と同じ `GitHubError` |
| `trusts_strict` | OWNER は照会しない | ほかの区分は照会せずに偽 | 照会の失敗を偽にしない |
| `branch_activity` | 1 ページずつ返す | 短いページで終わる | 上限で止まる |

## 流れの文書の生成

満たす決定: 決定 10、受け入れ条件 12

出す回: 2 回目

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `render` | `scripts/contracts_lib/project_flow_doc.py` | 新設 | `render(root: Path) -> str` | 決定 10 の 4 つの表と「閉じる条件」の抜き出しを、生成の印で挟む範囲の本文にする |
| `replace_generated` | `scripts/contracts_lib/project_flow_doc.py` | 新設 | `replace_generated(doc: str, generated: str) -> str` | 生成の印の間を置き換えた本文 |
| `mismatches` | `scripts/contracts_lib/project_flow_doc.py` | 新設 | `mismatches(root: Path) -> list[dict]` | 今の文書と生成し直した本文が違えば違反 1 件 |
| `flow_doc_violations` | `scripts/check-ndf-contracts.py` | 新設 | `flow_doc_violations(root: Path, write: bool) -> list[dict]` | `--write` なら書き出し、無ければ `mismatches` を返す |
| `main` | `scripts/check-ndf-contracts.py` | 変更 | 今と同じ | 引数 `--write` を足し、`find` の違反に `flow_doc_violations` を足す |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `render` | 抜き出せない | 正本に見出し `### 閉じる条件` が無い | `render` | 止める（違反 1 件） | 検査の結果 JSON の違反。終了コード 1 | 正本を直して打ち直す |
| `replace_generated` | 印が無い | 文書から印を消した | `replace_generated` | 止める（違反 1 件） | 同上 | 印を戻す |

### 単体テストの観点

保存先: `scripts/tests/test_check_ndf_contracts.py`（今の検査のテスト）

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mismatches` | 生成し直した本文と同じなら空 | データか「閉じる条件」のセクションだけを変えると違反 | 印が無い・見出しが無いで違反 |
