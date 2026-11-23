# #3908 の詳細設計: 工程のプランが作業のプロンプトを組んで worker を起動する

設計文書 [issue-3908-design.md](./issue-3908-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── templates/params.json                        差し込み worker の既定
│   ├── worker/kinds.json *                          作業の名前ごとの定義（唯一の基準）
│   ├── worker/prompt.md *                           起動指示の共通のテンプレート
│   └── workflow/context.schema.json                 文脈のキーごとの x-source
├── docs/contracts/commands.md                       サブコマンドの仕様（生成物）
├── src/ndf/core/
│   ├── worker.py                                    結果ファイルの outputs を worker run の結果へ写す
│   └── route/__init__.py                            リファクタリングの既定の起動
├── src/ndf/progress/
│   ├── worker_kinds.py *                            kinds.json を読み、作業ごとの入力の欠けを返す
│   ├── invoke.py                                    差し込みを 1 回だけ組んで values.json に残す・止まりの分類
│   ├── run.py                                       started の前に組んで確かめて止まる
│   ├── command.py                                   ndf progress check の結果
│   ├── definition.py                                欠けた文脈のキーに取り場所を添える
│   └── gate/entry.py                                プロンプトを組んで書き、worker へ置き換わる
└── tests/
    ├── core/test_worker_outcome.py *                結果ファイルの outputs の受け渡し
    └── progress/
        ├── test_invoke.py                           values.json と決められない差し込み・1 回だけ組む
        ├── test_invoke_refactor.py                  リファクタリングの既定の起動
        ├── test_run.py                              started の前の止まり
        ├── test_sprint_templates.py                 worker の既定と退行
        └── gate/test_gate_work.py                   プロンプトの組み立て・作業場所
```

依存の向き: `contracts/`（データ）→ `progress/worker_kinds.py`（作業の定義を読む）→ `progress/invoke.py`（工程がプランを組む層）→ `progress/gate/entry.py`（プランのステップが打つサブコマンド）の一方向である。`invoke.py` は `entry.py` を読めない（`entry.py` が `invoke.py` を読む）ので、作業の定義を読む関数は両方から読める `worker_kinds.py` に置く。`core/worker.py`（`ndf worker run`）は作業の定義を読まず、必須の出力の名前を引数で受ける。逆向きの import は足さない。`progress/gate/entry.py` は `values.json` を読む側として `progress/invoke.py` を読む（今も `from ndf.progress import command, invoke, model, run, store` で読んでいる）。パスは `ndf.contracts.worker_paths.report_paths`（#3877）で組む。`core/plan`（プランの実行の層）は触らない。ステップの結果 JSON の `outputs` をプランの結果へ重ねる処理は `core/plan/runner.py:121-122`・`:425-426`（`outputs.json`）に既にある。`progress/run.py` は `progress/invoke.py` を呼ぶ既存の向きのままである。循環は無い。要素どうしの順序は設計文書の「処理の流れ」にある。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/contracts/templates/params.json` | 変更 | 差し込み `worker` に既定を持たせる | 無し（データ） | `params` | 無し |
| `plugins/ndf/contracts/worker/kinds.json` | 新設 | 作業の名前 → 作業種別・作業の 1 行・読む Skill・手順の抜粋・読む値・作業場所・必須の出力 | 無し（データ） | `kinds` | 無し |
| `plugins/ndf/src/ndf/progress/worker_kinds.py` | 新設 | 作業の定義を読み、作業ごとの入力と作業場所の欠けを名前と出所で返す | `ndf.contracts` | モジュール `ndf.progress.worker_kinds` | `work_kinds`（新設）・`kind_gaps`（新設）・`work_dir_of`（新設）・`kind_values`（新設） |
| `plugins/ndf/contracts/worker/prompt.md` | 新設 | 起動指示の 7 項目と規則 7 個の共通のテンプレート | 無し（データ） | 無し | 無し |
| `plugins/ndf/contracts/workflow/context.schema.json` | 変更 | 文脈のキーごとに値の取り場所（`x-source`）を持つ | 無し（データ） | `properties` | 無し |
| `plugins/ndf/src/ndf/progress/invoke.py` | 変更 | 差し込みを 1 回だけ組んで `values.json` に残し、プランを書き出すときはそれを使う。止まりの分類を 1 か所にする | `ndf.core.route`・`ndf.core.plan.files`・`ndf.progress.worker_kinds` | モジュール `ndf.progress.invoke` | `PreparedPlan`（新設）・`KindInputsMissing`（新設）・`prepare_plan`（新設）・`plan_failure`（新設）・`write_values`（新設）・`read_values`（新設）・`plan_path`（変更）・`_call_plan`（変更）・`_fill_refactor`（変更） |
| `plugins/ndf/src/ndf/progress/run.py` | 変更 | `started` を書く前に最初のプランの差し込みを組んで確かめる | `ndf.progress.invoke` | モジュール `ndf.progress.run` | `step_dir`（新設）・`_start`（変更）・`_run_step`（変更） |
| `plugins/ndf/src/ndf/core/worker.py` | 変更 | 結果ファイルの `outputs` を `ndf worker run` の結果 JSON へ写し、必須の出力の欠けを返す | `ndf.contracts.result` | モジュール `ndf.core.worker` | `outcome_of`（変更）・`_add_run`（変更）・`_cmd_run`（変更） |
| `plugins/ndf/src/ndf/core/route/__init__.py` | 変更 | リファクタリングの既定の起動の argv を組む | `ndf.core.git` | モジュール `ndf.core.route` | `REFACTOR_WITH_PR`（新設）・`refactor_launch`（新設） |
| `plugins/ndf/src/ndf/progress/definition.py` | 変更 | 欠けた文脈のキーの行に取り場所を添える | `ndf.contracts` | モジュール `ndf.progress.definition` | `context_source`（新設）・`_missing_keys`（変更） |
| `plugins/ndf/src/ndf/progress/command.py` | 変更 | `ndf progress check` の結果に取り場所の付いた行を出す | `ndf.progress.definition` | モジュール `ndf.progress.command` | `_cmd_check`（変更） |
| `plugins/ndf/src/ndf/progress/gate/entry.py` | 変更 | プロンプトを組んで書き、`ndf worker run` へ置き換わる | `ndf.progress.invoke`・`ndf.progress.worker_kinds`・`ndf.contracts.worker_paths`・`ndf.contracts`・`string` | モジュール `ndf.progress.gate.entry` | `work_prompt`（新設）・`_write_prompt`（新設）・`work_argv`（変更）・`work_parser`（変更）・`_cmd_work`（変更） |
| `plugins/ndf/docs/contracts/commands.md` | 変更 | `ndf answer work` の節（生成物） | 無し（生成物） | 無し | 無し |
| `plugins/ndf/tests/progress/test_invoke.py`・`test_invoke_refactor.py`・`test_run.py`・`test_sprint_templates.py`・`gate/test_gate_work.py` | 変更 | 差し込みの既定・`values.json`・1 回だけ組むこと・決められない工程・リファクタリングの既定・プロンプトの組み立てと作業場所 | `ndf.progress` | モジュール | テスト関数 |
| `plugins/ndf/tests/core/test_worker_outcome.py` | 新設 | 結果ファイルの `outputs` の受け渡しと必須の出力の欠け | `ndf.core.worker` | モジュール | テスト関数 |

## worker の起動の引数の既定（`params.json`）

満たす決定: 決定 1、受け入れ条件 1、受け入れ条件 5

関数を持たない構成要素である。`params.json` の `worker` に `"default": ["ndf", "answer", "work", "--state-dir", "{state_dir}"]` を足す。既定に `--cwd` を置かない。`{repo}` はリポジトリの根（`params.json` の `repo` の定義と `core/plan/runner.py:242-250` の `_base_values`）で、作業場所（`{worktree}`）とは別の値である。`impl`・`fix`・`ci-fix` などコードを編集する作業の worker は作業場所で動かなければならず（`AGENTS.md` は開発の変更を worktree の中だけで行うと定める）、作業場所の用意（`worktree`）や要求（`requirements`）のように作業場所がまだ無い作業はリポジトリの根で動く。どちらで動くかは作業ごとに違うので、`kinds.json` の `cwd`（次の節）が持ち、`ndf answer work` が `values.json` の値から作業場所を決める（「作業のプロンプトを組んで worker を起動する」の節）。`summary` からは「既定は持たない」の但し書きと作業の名前の列挙を外し、作業の名前の唯一の基準が `contracts/worker/kinds.json` であることを書く。

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `worker` | 差し込みの定義 | `plugins/ndf/contracts/templates/params.json` | `["ndf", "answer", "work", "--state-dir", "{state_dir}"]`（`list` が真） |

`{state_dir}` はプランの実行が埋める決まった名前の値で、`core/plan/runner.py:242-250` の `_base_values` が持つ（このファイルは触らない）。既定を持つ差し込みは `invoke._missing`（`src/ndf/progress/invoke.py:578-582`）が「決まらない」に数えない。そのため `FILL_SOURCES` の `worker` の行は残したまま、17 工程が差し込みで止まらなくなる。`merged.json` と `fix.json` は `worker` を使わないため、クリーンアップの工程は引数が変わらない（受け入れ条件 5）。

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `params.json` の `worker` | `standard` のすべてのテンプレートを `route.render` で組むとき、`worker` が差し込みの不足に挙がらない。組んだ argv に `--cwd` が無い | `worker` を使わない 2 件のテンプレートは引数が変わらない | 既定を消すと、工程「要求と受け入れ条件」の起動が差し込みの不足で止まる |

## 作業ごとの定義（`contracts/worker/kinds.json`）

満たす決定: 決定 3、受け入れ条件 1、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `work_kinds` | `plugins/ndf/src/ndf/progress/worker_kinds.py` | 新設 | `work_kinds() -> dict[str, dict[str, Any]]` | `contracts/worker/kinds.json` の `kinds` を返す。`WORK_KINDS` の定数を置き換える |
| `kind_values` | 同上 | 新設 | `kind_values(kind: str, values: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]` | 作業の定義の `inputs` の名前ごとに、差し込みの値（`values`）にあればそれを、無ければ工程の入力（`inputs`）の値を取る。どちらにも無い名前は返りに入れない |
| `kind_gaps` | 同上 | 新設 | `kind_gaps(kind: str, values: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, str]` | 作業の定義の `inputs` と `cwd` が指す値のうち、どちらにも無い（または空の）名前 → 出所の人が読む文を返す。そろえば空 |
| `work_dir_of` | 同上 | 新設 | `work_dir_of(kind: str, values: Mapping[str, Any]) -> Optional[str]` | 作業の定義の `cwd`（`"worktree"` か `"repo"`）が指す差し込みの値を返す。空か無ければ `None` |

- `work_kinds`
  - 事前条件: 無し
  - 事後条件: 返るキーは、テンプレートが `{{worker}}` の後ろに置く作業の名前 18 件をすべて含む。値の形は設計文書の「データ構造」にある
  - 副作用: コントラクトのファイルを 1 度読む（`contracts.cached_reader` で回ごとに 1 回）
- `kind_gaps`
  - 事前条件: `kind` が `work_kinds()` のキーである。`values` は差し込みの値（`template_values` の返り）、`inputs` は工程の入力（`StageRun.inputs`）
  - 事後条件: 返る辞書のキーは欠けた名前で、値は出所の文（工程の入力の名前なら「工程の入力 \<名前\>（工程の定義の `metadata.ndf.inputs`）」、差し込みの名前なら `FILL_SOURCES` の文。`cwd` が指す値なら「作業 \<名前\> の作業場所（差し込み \<worktree か repo\>）」）。`started` の前（`prepare_plan`）と `ndf answer work` の両方がこの関数で確かめ、検査を 2 か所に持たない
  - 副作用: 無し

`inputs` の名前は、差し込み（テンプレートの `params` にあるもの。`worktree`・`branch`・`acceptance` など）と工程の入力（`issues` など）のどちらも指せる。テンプレートの `params` に無い工程の入力は `template_values` が落とす（`src/ndf/progress/invoke.py:461` の `if k in t.params`）ので、`values.json` は差し込みの値と別に工程の入力を持つ（次の節）。たとえば `requirements.json` の `params` は `repo`・`worker` だけだが、作業 `requirements` の `inputs` は `["issues"]` で、`issues` は工程の入力から取る。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `work_kinds` | 定義を読めない | ファイルが無い・JSON が壊れている | `contracts.load_json` | 呼び出し元へ返す（`contracts.ContractError`） | `ndf` のエントリポイントが `stopped`（`config_broken`）の結果 JSON を出す（`src/ndf/cli.py` の `main`） | 後始末は無い。プラグインを入れ直せば直る |
| `work_kinds` の呼び手（`work_argv`） | 作業の名前が定義に無い | テンプレートが定義に無い名前を使う・打ち間違い | `work_argv` | 呼び出し元へ返す（`model.Refused`） | `stopped`（`invalid`）。`summary` に名前と、定義にある名前を並べる | ファイルを触らずに返す。名前を直せば打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `KINDS_FILE` | 定数 | `plugins/ndf/src/ndf/progress/gate/entry.py` | `"worker/kinds.json"`（`contracts.load_json` に渡す相対パス） |
| 作業の名前 18 件と定義 | データ | `plugins/ndf/contracts/worker/kinds.json` | `impl`・`fix`・`ci-fix`・`fix-base`・`fix-push`・`design`・`plan-to-spec`・`requirements`・`worktree`・`design-detail`・`document-sources`・`implementation-plan`・`quality-gates`・`release`・`release-verification`・`layout-review`・`retrospective`・`backlog-refinement` |
| `cwd` の値 | データ | 同上（作業ごと） | `"worktree"`: `impl`・`fix`・`ci-fix`・`fix-base`・`fix-push`・`design`・`design-detail`・`implementation-plan`・`quality-gates`・`plan-to-spec`・`document-sources`・`layout-review`。`"repo"`: `requirements`・`worktree`（作業場所をこれから作る）・`release`・`release-verification`・`retrospective`・`backlog-refinement`。テンプレートが `worktree` を持たない作業を `"worktree"` にしない（下のテストで縛る） |
| `outputs` の値 | データ | 同上（作業ごと） | 工程が必須にする出力のうち worker が作るもの。`worktree` は `["worktree", "branch"]`（`contracts/workflow/stages/worktree.json` の `metadata.ndf.outputs`）。ほかの作業は、工程の `outputs` のうちプランのほかのステップが作らないキー。無ければ空 |

`WORK_KINDS` の定数（`src/ndf/progress/gate/entry.py:77`）は消し、`ndf.progress.gate.__init__` の再公開（`:73`・`:84`）も `work_kinds` へ置き換える。作業の名前を 2 か所に持たない。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `work_kinds` | `contracts.load_json` と `contracts.cached_reader`（`src/ndf/contracts/__init__.py:49,68`） | `grep -n "^def " plugins/ndf/src/ndf/contracts/__init__.py` | 使う。コントラクトの表を読む経路はこの 2 つが唯一の保存先で、`route.params`（`core/route/__init__.py:129`）も同じ形を使う |
| `work_kinds` | `route.templates()`・`route.params()` | `grep -n "def templates\|def params" plugins/ndf/src/ndf/core/route/__init__.py` | 使えない。テンプレートと差し込みの定義を読む関数で、作業の名前の定義を持たない。読み方（`cached_reader` + `load_json`）だけ踏襲する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `work_kinds` | 18 件のキーが返り、どれも `work`・`summary`・`skills`・`excerpt`・`inputs`・`cwd`・`outputs` を持つ | テンプレートが `{{worker}}` の後ろに置く名前の集合と、キーの集合が一致する（受け入れ条件 1・決定 3）。`cwd` が `"worktree"` の作業を使うテンプレートは、どれも `params` に `worktree` を持つ | 定義に無い名前を `ndf answer work` に渡すと `stopped`（`invalid`） |
| `kind_gaps` | 作業 `requirements` に `values` が `{repo, worker}`・`inputs` が `{"issues": [12, 34]}` を渡すと空 | 同じ名前が両方にあれば差し込みの値が勝つ（`kind_values`） | `inputs` に `issues` が無いと `{"issues": "工程の入力 issues（…）"}`。作業 `impl` で `worktree` が空の文字列なら、作業場所の欠けが返る |

## 起動指示の共通のテンプレート（`contracts/worker/prompt.md`）

満たす決定: 決定 1、決定 3、決定 6

関数を持たない構成要素である。`agent-layers.md` の「supervisor → worker」が定める 7 項目（作業・入力・手順・読む Skill・返す形・置く場所・作業の結果ファイル）の見出しと、worker が守る規則 7 個を本文に持つ。差し込みだけを `string.Template` の形（`$名前`・`${名前}`）にする。本文に `$` の字を書くときは `$$` で逃がす（決定 6 の代償）。

| 差し込み | 入る値 |
| --- | --- |
| `$work` | 作業種別（`kinds.json` の `work`） |
| `$summary` | 作業の 1 行（`kinds.json` の `summary`） |
| `$inputs` | 作業が読む値（`kinds.json` の `inputs`）を 1 行 1 件で並べた字面。`worker_kinds.kind_values` で `values.json` の `values` と `inputs` から取る。配列（`issues` など）は要素を `, ` でつなぐ。欠けは組む前に `kind_gaps` で確かめ、`$inputs` に欠けを残さない |
| `$outputs` | 作業の結果ファイルに必ず入れる `outputs` のキーと、`ndf contracts emit --outputs '<JSON>'` の見本（`kinds.json` の `outputs`。空なら「無し」） |
| `$workdir` | worker が動く作業場所の絶対パス（`work_dir_of` の返りか `--cwd`） |
| `$steps` | 手順の抜粋（`kinds.json` の `excerpt` のファイルの中身。無ければ「手順の抜粋は無い」の 1 行） |
| `$skills` | 読む Skill の名前と `SKILL.md` の絶対パス（無ければ「無し」） |
| `$report`・`$done`・`$result` | 報告の保存先の 3 つの絶対パス（`contracts.worker_paths.report_paths`。#3877） |

本文に書く 7 項目と規則 7 個の文面は、worker を起動指示で起動するときはこのファイルが、supervisor がサブエージェントで起動するときは `agent-layers.md` が持つ。どちらも同じ 7 項目と規則 7 個である。

## 差し込みの値の受け渡し（`values.json`）

満たす決定: 決定 2、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write_values` | `plugins/ndf/src/ndf/progress/invoke.py` | 新設 | `write_values(state_dir: Path, plan: str, values: Mapping[str, Any], inputs: Mapping[str, Any], local_tests: bool) -> None` | プランの名前・差し込みの値・工程の入力・手元のテストを含めるか・書いた時刻を `<保存先>/values.json` へ書く |
| `read_values` | `plugins/ndf/src/ndf/progress/invoke.py` | 新設 | `read_values(state_dir: Path) -> Optional[dict[str, Any]]` | `<保存先>/values.json` を読む。無い・壊れていれば `None` |
| `plan_path` | `plugins/ndf/src/ndf/progress/invoke.py` | 変更 | `plan_path(run: StageRun, work_dir: Path, cwd: Path) -> Path`（変わらない） | 同じ保存先に同じプランの `values.json` があればその値と描画の条件（`local_tests`）でプランを描く。無ければ `prepare_plan` で組んでから描く。差し込みを組むのはどちらの経路でも 1 回だけである |

- `write_values`
  - 事前条件: `state_dir` が存在する（呼ぶのは `prepare_plan` だけで、`prepare_plan` が先に `work_dir.mkdir(parents=True, exist_ok=True)` を済ませる。今 `_call_plan` が `src/ndf/progress/invoke.py:727` で行っている作成をここへ移す）
  - 事後条件: `<保存先>/values.json` が設計文書の「データ構造」の形で存在する。同じ工程を打ち直しても最後の中身になる
  - 副作用: ファイルの書き込み 1 つ
- `read_values`
  - 事前条件: 無し
  - 事後条件: 返る辞書は `plan`・`values`・`inputs`・`local_tests`・`at` を持つ。形が違えば `None`
  - 副作用: 無し（読み取りだけ）
- `plan_path`
  - 事後条件: 書き出したプランの `{state_dir}`（= `work_dir`）に `values.json` があり、その `values` がそのプランを組むのに使った値と一致する
  - 副作用: `values.json` が無い（または別のプランの）ときだけ `prepare_plan` を呼ぶ（課題の題と本文の書き出し・候補の書き出し・`git` と GitHub の読み取りが 1 回）。あれば読み取り 1 つだけで、`template_values` を呼ばない

`values.json` を書くのは `prepare_plan` だけで、`_start`（工程の最初のプランのステップ。`started` の前）か `plan_path`（それ以外のプランのステップ。そのステップを流すとき）のどちらかが 1 回だけ呼ぶ。`plan_path` は `values.json` の値をそのまま描くので、プランと値が食い違うことがない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_values` | 書けない | 保存先の権限・容量（`OSError`） | `files.write_json` | 呼び出し元へ返す（`OSError` をそのまま上げる） | `_start` の中なら `plan_failure` が `stopped`（`precondition`）にし、`started` を書かない。`plan_path` の中なら `_call_plan` が同じ分類で返す | プランを起動していないので後始末は無い。打ち直せる |
| `plan_path` | `values.json` が別のプランのもの | 同じ保存先を別のテンプレートが使った（工程の定義を書き換えた後の打ち直し） | 読んだ `plan` とテンプレートの名前の違い | 既定値で続ける（読まなかったものとして `prepare_plan` を呼ぶ） | 出力なし | 後始末は無い |
| `read_values` | 読めない・形が違う | ファイルが無い（工程を通さずに手で打ったプラン）・JSON が壊れている | `files.read_json` の返り | 既定値で続ける（`None` を返す） | 呼び手（`_cmd_work`）が `stopped`（`precondition`）にする | 後始末は無い |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VALUES_FILE` | 定数 | `plugins/ndf/src/ndf/progress/invoke.py` | `"values.json"` |

`values` に入るのは `template_values` が返す差し込みの値そのものである。テンプレートの `params` にある工程の入力・課題の題と本文・リポジトリの設定・打った場所（`route.derived`）・起動の補い（`route.launched`。`acceptance` など）をすべて含む。`inputs` には工程の入力（`StageRun.inputs`）をそのまま入れる。テンプレートの `params` に無い工程の入力（工程「要求と受け入れ条件」の `issues` など）は `values` から落ちるので、作業ごとの入力（`kinds.json` の `inputs`）はこちらから取る。`local_tests` は描画の条件（`ci_only` の判定の返り）で、`plan_path` が設定を読み直さずに同じ条件で描くために残す。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ工程の `ndf progress next`（打ち直し・承認の回答） | `<保存先>/values.json` | 取らない（工程の行の排他（`store.run_lock`）の中で 1 つの駆動だけがプランを組む） | 同じ工程・同じ入力なら中身は同じで、何度書いても結果が変わらない |
| プランのステップ（`ndf answer work`） | 同上 | 排他を取らずに読むだけ | 読み取りだけで、読めなければ `stopped`（`precondition`）で止まる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `write_values`・`read_values` | `ndf.core.plan.files.write_json`・`read_json`（`invoke.py` が `plan_files` として既に読んでいる） | `grep -n "plan_files\|def write_json\|def read_json" plugins/ndf/src/ndf/progress/invoke.py plugins/ndf/src/ndf/core/plan/files.py` | 使う。保存先の JSON を読み書きする既存の関数で、`invoke.py` に新しい依存を足さない |
| `write_values`・`read_values` | `ndf.core.plan.records`（`prs.json`・`outputs.json` の読み書き。`src/ndf/core/plan/records.py:17-22`） | `grep -n "^def \|^[A-Z_]* = " plugins/ndf/src/ndf/core/plan/records.py` | 使えない。プランの実行の層が回をまたいで書く記録で、工程がプランを組むときの値を持たない（決定 2 で書き込み元を工程の層にした） |
| `write_values` | `invoke._issue_values` がコピーを置く形（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE` を同じ `work_dir` へ書く。`invoke.py:386-412`） | `grep -n "ISSUE_BODY_FILE\|ISSUE_TITLE_FILE" plugins/ndf/src/ndf/progress/invoke.py` | 使う（置き方を踏襲する）。工程の保存先へ材料を残す形が既にある |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `plan_path` | プランを書き出した保存先に `values.json` があり、`plan`・`values`・`inputs`・`local_tests`・`at` を持つ。`values` に `acceptance`・`title`・`body_file` の補いが入る。工程「要求と受け入れ条件」で `inputs` に `issues` が入る | 同じ保存先に `values.json` があるとき、`template_values` を差し替えた見張りが 0 回呼ばれ（`_start` で組んだ値を使う）、無いときは 1 回呼ばれる | 保存先が読み取り専用のとき `OSError` が上がり、プランを起動しない。別のプランの `values.json` は使わずに組み直す |
| `read_values` | 書いた中身をそのまま読める | 打ち直した工程でも最後の値を読める | ファイルが無い・壊れている・`inputs` が無いとき `None` |

## 作業のプロンプトを組んで worker を起動する（`ndf answer work`）

満たす決定: 決定 1、決定 2、決定 3、決定 6、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `work_prompt` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 新設 | `work_prompt(kind: str, saved: Mapping[str, Any], workdir: Path, paths: worker_paths.ReportPaths) -> str` | 共通のテンプレート（`contracts/worker/prompt.md`）と作業の定義と `values.json` の中身（`saved`）から、起動指示の本文を組む |
| `_write_prompt` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 新設 | `_write_prompt(kind: str, state_dir: Path, saved: Mapping[str, Any], workdir: Path) -> Path` | `work_prompt` を呼び、`report_paths(...).prompt`（`<作業名>.prompt.md`）へ `worker_paths.write_file`（既存のリンクをたどらない。#3877）で書いてそのパスを返す |
| `work_argv` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 変更 | `work_argv(kind: str, state_dir: Path, workdir: Path) -> list[str]` | 作業の名前を `work_kinds()` で確かめ、プロンプトと結果ファイルのパスを `worker_paths.report_paths` から取り（`WORK_KINDS` の定数と `<作業名>-prompt.md`・`<作業名>-result.json` の字面をやめる）、`--cwd <workdir>` と、`kinds.json` の `outputs` の名前ごとの `--require-output <名前>` を足す |
| `work_parser` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 変更 | `work_parser() -> argparse.ArgumentParser`（変わらない） | 作業の名前の説明を `work_kinds()` のキーから組む。`--cwd` を省けるようにする（省くと作業の定義の `cwd` と `values.json` から決める） |
| `_cmd_work` | `plugins/ndf/src/ndf/progress/gate/entry.py` | 変更 | `_cmd_work(argv: list[str], exec_: Callable[[str, list[str]], Any] = os.execv) -> int` | プロンプトを書いてから `ndf worker run` へ置き換わる |

- `work_prompt`
  - 事前条件: `kind` が `work_kinds()` のキーである。`saved` は `read_values` の返りで、`kind_gaps(kind, saved["values"], saved["inputs"])` が空である（`_cmd_work` が先に確かめる）
  - 事後条件: 返る本文に、起動指示の 7 項目の見出しと規則 7 個がそろう。差し込みは 1 つも残らない。`$inputs` に入る値は `kinds.json` の `inputs` の名前のすべてで、欠けた名前を持たない（`$inputs` は組んだ後の 1 つの文字列なので、`string.Template.substitute` の `KeyError` では中の欠けを見つけられない。そのため欠けは組む前に `kind_gaps` で名前ごとに確かめる）
  - 副作用: コントラクトのファイル（テンプレートと手順の抜粋）を読む
- `_write_prompt`
  - 事前条件: `state_dir` に `values.json` がある
  - 事後条件: `<保存先>/<作業名>.prompt.md` が存在し、中身が `work_prompt` の返り
  - 副作用: ファイルの読み取り 1 つと書き込み 1 つ
- `_cmd_work`
  - 事前条件: `--state-dir` と作業の名前がある。`--cwd` は省ける
  - 手順: (1) 作業の名前を確かめる → (2) `read_values` → (3) `kind_gaps` が空かを確かめる → (4) 作業場所を決める（`--cwd` があればそれ、無ければ `work_dir_of(kind, saved["values"])`。工程から起動するときは `--cwd` を渡さないので、作業ごとの `cwd` が効く）→ (5) プロンプトを書く → (6) `work_argv` で `ndf worker run --cwd <作業場所> …` へ置き換わる
  - 事後条件: プロンプトのファイルが書かれた後に、プロセスが `ndf worker run` へ置き換わる。置き換わる前に戻る経路は失敗のときだけ。作業の定義の `cwd` が `"worktree"` の作業は、`values.json` の `worktree`（プランの `worktree` と同じ値）で worker が動き、リポジトリの根（`repo`）では動かない
  - 副作用: ファイルの書き込みとプロセスの置き換え（`os.execv`）

引数は `--cwd` を省けるようになるだけで、ほかは変わらない。作業の名前の説明が `work_kinds()` のキーから組まれるため、サブコマンドの仕様の文書（`plugins/ndf/docs/contracts/commands.md`）の `ndf answer work` の節が変わる。この節は `ndf check --write-spec` が生成する。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `work_argv` | 作業の名前が定義に無い | テンプレートの名前と `kinds.json` の食い違い | `work_argv` の先頭 | 呼び出し元へ返す（`model.Refused`） | `stopped`（`invalid`）。今の振る舞いと同じ | ファイルを触らずに返す |
| `work_argv` | 名前がパスの形に合わない | `[A-Za-z0-9._-]{1,64}` でない名前 | `report_paths` が上げる `worker_paths.NameInvalid` | 呼び出し元へ返す（`model.Refused` に包む） | `stopped`（`invalid`） | ファイルを触らずに返す |
| `_write_prompt` | `values.json` が無い・読めない | 工程を通さずに手で打ったプラン・保存先が違う | `invoke.read_values` の返り | 呼び出し元へ返す（`model.Refused` に包む） | `stopped`（`precondition`）。`summary` に保存先のパスと理由を書く（決定 2） | 何も書いていない。工程から打ち直せる |
| `_cmd_work` | 作業ごとの入力か作業場所が足りない | 作業の定義の `inputs`・`cwd` の名前が `values.json` の `values` にも `inputs` にも無い（工程の起動では `started` の前に `prepare_plan` が同じ `kind_gaps` で止めるので、ここに来るのは手で打ったときか定義を変えた後の打ち直しだけ） | `worker_kinds.kind_gaps` の返り | 呼び出し元へ返す（`model.Refused` に包む） | `stopped`（`precondition`）。足りない名前と出所を `summary` と `items` に書く | 何も書いていない。工程の入力か設定を足して打ち直せる |
| `work_prompt` | 共通のテンプレートの差し込みが足りない | `prompt.md` に上の表に無い `$名前` がある（テンプレートの書き誤り） | `string.Template.substitute` の `KeyError` | 呼び出し元へ返す（`model.Refused` に包む） | `stopped`（`precondition`）。名前を `summary` に書く | 何も書いていない。テンプレートを直す |
| `_write_prompt` | プロンプトのパスがリンクになっている | 作業ディレクトリの中の `<作業名>.prompt.md` が別の場所へのリンク | `worker_paths.write_file`（`O_NOFOLLOW`）の `EntryUnsafe`・`ELOOP` | 呼び出し元へ返す（`model.Refused` に包む） | `stopped`（`precondition`）。パスを書く | リンクの先を書き換えない。リンクを取り除けば打ち直せる |
| `work_prompt` | 手順の抜粋を読めない | `kinds.json` の `excerpt` のパスが無い | 抜粋の読み取り | 呼び出し元へ返す（`model.Refused`） | `stopped`（`precondition`） | 手順を渡せない worker を起動しない（`core/worker.py` の既存の方針と同じ） |
| `_cmd_work` | プロンプトを書けない | 保存先の権限・容量（`OSError`） | `Path.write_text` | 呼び出し元へ返す | `stopped`（`precondition`） | 置き換えの前に返るので、worker は起動しない |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_cmd_work` | 作業の名前が定義にある・`values.json` が読める・差し込みがそろう | プロンプトを書き、`ndf worker run` へ置き換わる |
| `_cmd_work` | 作業の名前が定義に無い | `stopped`（`invalid`）。プロンプトを書かない |
| `_cmd_work` | `values.json` が無い・読めない | `stopped`（`precondition`）。プロンプトを書かない |
| `_cmd_work` | 作業ごとの入力か作業場所が足りない | `stopped`（`precondition`）。プロンプトを書かない |
| `_cmd_work` | `--cwd` が無い・作業の定義の `cwd` が `"worktree"` | `values.json` の `worktree` で worker を起動する |
| `_cmd_work` | `--cwd` が無い・作業の定義の `cwd` が `"repo"` | `values.json` の `repo` で worker を起動する |
| `_cmd_work` | `--cwd` がある | その場所で worker を起動する（手で打つときの今までの形） |

繰り返しは作業の定義の `inputs` の数（多くて数件）で終わる。計算量の注意は無い。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `PROMPT_FILE` | 定数 | `plugins/ndf/src/ndf/progress/gate/entry.py` | `"worker/prompt.md"`（コントラクトの保存先からの相対パス） |
| 4 つのファイルの名前 | 定数 | `plugins/ndf/src/ndf/contracts/worker_paths.py`（#3877） | `<作業名>.prompt.md`・`<作業名>.report.md`・`<作業名>.report.md.done`・`<作業名>.result.json`。`entry.py` は字面を持たない |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_cmd_work` | 失敗したときだけ `stopped` の結果 JSON（標準出力 1 行）。成功したときは置き換わるので出力しない | 結果 JSON | プロンプトの本文（長く、課題の本文を含みうる。ファイルにだけ置く）。課題の本文・認証情報 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `work_prompt` | `string.Template`（Python 標準ライブラリ。設計文書の LIB1・決定 6） | 設計文書の「ライブラリとサービス」の表 | 使う。`$名前`・`${名前}` の差し込みで足り、共通のテンプレートの差し込みの欠けは `substitute()` の `KeyError` で止まる。作業ごとの入力の欠けは `$inputs` の 1 つの文字列の中に隠れて `KeyError` にならないので、`kind_gaps` で名前ごとに確かめる |
| `work_prompt` | `core/route/__init__.py` の `_fill`（テンプレートの `{{名前}}` を埋める） | `grep -n "def _fill\|def resolve" plugins/ndf/src/ndf/core/route/__init__.py` | 使えない。プランの JSON の構造（配列の要素の展開）を埋める処理で、Markdown の本文を埋める用途と形が合わない |
| `work_prompt` | `core/worker.py` の `system_prompt`・`skills_section`（`src/ndf/core/worker.py:207-233`） | `grep -n "def system_prompt" -A 10 plugins/ndf/src/ndf/core/worker.py` | 使えない。`ndf worker run` が `claude -p` へ渡す規則文を組む処理で、起動指示の 7 項目を組む処理ではない。読む Skill の本文を載せる側はこちらが持ち続ける |
| `_write_prompt` | `invoke.read_values`（この設計で新設） | 同じ文書の「差し込みの値の受け渡し」 | 使う |
| `_write_prompt` | `contracts.worker_paths.report_paths`（#3877 で新設） | 設計文書の「文脈と制約」（報告の保存先のパスの決め方は #3877 が持つ） | 使う。プロンプト・報告・完了マーカー・結果ファイルの 4 つはこの関数から取り、別の決め方を作らない。`contracts` の層なので `entry.py:95` の IG1 に触れない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `work_prompt` | 組んだ本文に 7 項目の見出しと規則 7 個がそろう（決定 1）。作業 `requirements` に `inputs` が `{"issues": [12, 34]}` の `values.json` を渡すと、本文の入力の欄に `12` と `34` の両方が載る（`requirements.json` の `params` に `issues` が無くても渡る） | 読む Skill が無い作業は「無し」、手順の抜粋が無い作業は抜粋の無い本文になる。本文の `$$` が `$` 1 つになる。作業 `worktree` の本文に `outputs` の `worktree`・`branch` と `emit --outputs` の見本が載る | `prompt.md` に表に無い差し込みがあると `model.Refused` |
| `_cmd_work` | `<保存先>/<作業名>.prompt.md` を書いてから `ndf worker run` の argv へ置き換わる（置き換えは差し替えた `exec_` で確かめる） | 2 回打つとプロンプトを書き直す。`values.json` の `repo` が `/r`・`worktree` が `/r/.worktrees/b` のとき、作業 `impl` の argv の `--cwd` は `/r/.worktrees/b`、作業 `worktree` と `requirements` の argv の `--cwd` は `/r`（両者が違う値で確かめる） | `values.json` の無い保存先で `stopped`（`precondition`）になり、置き換えが呼ばれない（決定 2）。`inputs` に `issues` が無い `values.json` で作業 `requirements` を打つと `stopped`（`precondition`）で、`items` に `issues` と出所が載り、置き換えが呼ばれない |
| `work_argv` | 定義にある 18 件の名前すべてで argv を組める | `--prompt-file` と `--result-file` が `report_paths` の返りと一致する（区切りが `.`）。作業 `worktree` の argv に `--require-output worktree --require-output branch` が入る | 定義に無い名前で `stopped`（`invalid`）（決定 3） |

## 差し込みを 1 回だけ組み、決められなければ起動の前に返す

満たす決定: 決定 2、決定 4、受け入れ条件 3

工程を起動するとき、`_start`（`started` を書く前）が工程の最初のプランのステップの差し込みを組み、確かめ、`values.json` まで書く。`_start` の後、作業は別のプロセス（`d.env.launch`。`src/ndf/progress/run.py:602`）で流れるので、組んだ値はメモリでなく `values.json` で渡す。そのステップを流すとき、`plan_path` は `values.json` を読んで描くだけで、差し込みを組み直さない。差し込みを組む処理（設定の読み取り・`route.derived` の `git` の呼び出し・課題の題と本文の書き出し・GitHub の読み取り）は、プラン 1 つにつき 1 回だけ走る。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PreparedPlan` | `plugins/ndf/src/ndf/progress/invoke.py` | 新設 | `class PreparedPlan(NamedTuple): template: route.Template; values: dict[str, route.Value]; local_tests: bool` | 組んだ差し込みと描画の条件 |
| `KindInputsMissing` | 同上 | 新設 | `class KindInputsMissing(route.RouteError)`。`missing: dict[str, str]` と `items` プロパティ（`TemplateValuesMissing` と同じ形） | 作業ごとの入力か作業場所が決まらないことを、名前と出所付きで伝える |
| `prepare_plan` | 同上 | 新設 | `prepare_plan(run: StageRun, work_dir: Path, cwd: Path) -> PreparedPlan` | 保存先を作り（`work_dir.mkdir(parents=True, exist_ok=True)`）、設定を 1 回読み、`template_values` で差し込みを組み、テンプレートの `{{worker}}` の後ろの作業の名前ごとに `worker_kinds.kind_gaps` で確かめ、`write_values` で残す |
| `plan_failure` | 同上 | 新設 | `plan_failure(name: str, exc: Exception) -> Optional[dict[str, Any]]` | `prepare_plan` が上げる例外を `stopped` の結果 JSON に分類する（今の `_call_plan` の `except` の並び。`src/ndf/progress/invoke.py:732-744`）。分類に無い例外は `None`（呼び手が上げ直す） |
| `plan_path` | 同上 | 変更 | `plan_path(run: StageRun, work_dir: Path, cwd: Path) -> Path`（変わらない） | 同じプランの `values.json` があればそれで描き、無ければ `prepare_plan` を呼んでから描く |
| `_call_plan` | 同上 | 変更 | シグネチャは変わらない | 例外の分類を `plan_failure` へ移し、保存先の作成を `prepare_plan` へ移す |
| `step_dir` | `plugins/ndf/src/ndf/progress/run.py` | 新設 | `step_dir(d: Drive, v: View, s: stages.StageDef, step: stages.StepDef) -> Path` | ステップのプランの実行の保存先（`work_root(...) / s.id / step_location(step.id, prog)`）。今 `_run_step`（`:861`）が組んでいる式を移し、`_start` と `_run_step` が同じ関数で同じ世代の保存先を得る |
| `_start` | `plugins/ndf/src/ndf/progress/run.py` | 変更 | `_start(d: Drive, v: View, s: stages.StageDef) -> Optional[dict[str, Any]]`（変わらない） | 入力の検査（`invoke.resolve_inputs`）の後、`started` を書く前に、工程の最初のステップがプランなら `step_dir` の保存先で `prepare_plan` を呼ぶ。止まれば `plan_failure` の結果を返し、`started` を書かない |
| `_run_step` | `plugins/ndf/src/ndf/progress/run.py` | 変更 | シグネチャは変わらない | 保存先を `step_dir` から得る |

- `prepare_plan`
  - 事前条件: `run` は `invoke.stage_run` が組んだ呼び出しで、`kind` がプラン（`PLAN`）である
  - 事後条件: 返ったときは `<work_dir>/values.json` が書かれていて、その値で `route.render` が `TemplateValuesMissing` を上げず、プランの `{{worker}}` の作業ごとに `kind_gaps` が空である。決まらなければ `TemplateValuesMissing`（差し込み）か `KindInputsMissing`（作業ごとの入力・作業場所）を上げ、`values.json` を書かない
  - 副作用: 今の `template_values` と同じ（課題の題と本文の書き出し・リファクタリングの候補の書き出し・`git` と GitHub の読み取り）に、`values.json` の書き込み 1 つ。書き出しはどれも `work_dir` の下で、`work_dir` は `_run_step` が使う保存先と同じ（`step_dir`）なので、新しい世代の保存先でも書き込み先が先にそろう
- `plan_failure`
  - 事後条件: `TemplateValuesMissing`・`KindInputsMissing` は `stopped`（`precondition`）で、`summary` は「工程 \<名前\> のプラン \<テンプレート\> の差し込み \<名前\>… を決められない」、`items` は名前ごとの `{"key": 名前, "source": 出所}`。`route.RouteError` は `invalid`、`GitError` は `precondition`、`FactUnknown` は `precondition`、`github.GitHubError` は `_github_kind(exc)`（一時障害なら `transient` と `retry_after`）、`OSError` は `precondition`。今の `_call_plan` の分類と同じで、差し込みの欠けだけを `invalid` から `precondition` にする（決定 4。`_call_plan` の経路も同じ関数を通るので、2 つの経路で分類が分かれない）
  - 副作用: 無し
- `_start`
  - 事後条件: 差し込みか作業ごとの入力を決められない工程では、ステートマシンに `started` も `failed` も増えず、行は未着手のままである（受け入れ条件 3）。GitHub の一時障害では、今の `_call_plan` と同じ `transient` の結果を返す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `prepare_plan` | 差し込みを決められない | 工程の入力・設定・課題のどこにも値が無い | `template_values` が上げる `TemplateValuesMissing` | 呼び出し元へ返す（上げる） | `_start` が `plan_failure` で `stopped`（`precondition`）。`items` に名前と出所 | ステートマシンに何も書いていない。`values.json` も無い。値を足して `ndf progress next` を打ち直せる |
| `prepare_plan` | 作業ごとの入力か作業場所を決められない | `kinds.json` の `inputs`・`cwd` が指す値が差し込みにも工程の入力にも無い | `worker_kinds.kind_gaps` の返り | 呼び出し元へ返す（`KindInputsMissing` を上げる） | 同上。出所は「工程の入力 \<名前\>」か差し込みの出所の文 | 同上 |
| `prepare_plan` | 課題を読めない | GitHub の不達・認証（`github.GitHubError`） | `template_values` の中 | 呼び出し元へ返す（上げる） | `plan_failure` が `_call_plan` と同じ `transient`（`retry_after` 付き）か `precondition` にする | `started` を書いていないので、打ち直せる |
| `prepare_plan` | 保存先を作れない・`values.json` を書けない | 権限・容量（`OSError`） | `mkdir`・`write_values` | 呼び出し元へ返す（上げる） | `plan_failure` が `stopped`（`precondition`） | 同上 |
| `_start` | テンプレートが無い | 工程の定義の `plan` の名前が違う | `template()` の返りが `None` | `prepare_plan` を呼ばずに `started` を書く（起動の層の既存の `precondition` に任せる） | `_call_plan` の今までの結果 | 後始末は無い |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_start` | 工程の条件が偽 | 今までどおり `skipped` を書く |
| `_start` | 工程の入力が欠ける・形が違う | 今までどおり `stopped`（入力の種類） |
| `_start` | 最初のステップがプランで、`prepare_plan` が上げる | `started` を書かずに `plan_failure` の結果を返す |
| `_start` | 最初のステップがプランで、`prepare_plan` が通る | `started` を書いて起動する。そのステップの `plan_path` は `values.json` を読むだけ |
| `_start` | 最初のステップがプランでない（`deploy` の `ndf.script` など） | 今までどおり `started` を書いて起動する |
| `plan_path` | 同じ保存先に同じプランの `values.json` がある | それで描く（`template_values` を呼ばない） |
| `plan_path` | 無い（工程の 2 つ目以降のプランのステップ・`values.json` を消した打ち直し） | そのステップを流す時点で `prepare_plan` を呼んでから描く（1 回） |

工程の 2 つ目以降のプランのステップ（工程「設計」の `cross-review`・`design-merge`、工程「Pull Request」の `merge` など）は、前のステップが作った push やレビューの範囲を差し込みに使うので、`_start` の時点では組まない（先に組むと古い値で描くことになる）。それらの差し込みと作業ごとの入力の欠けは、そのステップを流すときに同じ `prepare_plan` と `plan_failure` で見つかり、今までどおり `failed` になる。`worker` の既定が入るので、`worker` の欠けで止まることはもう無い。工程ごとのプランのステップの数（多くて 3 件）だけ繰り返す。計算量の注意は無い。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `prepare_plan` | `invoke.template_values` と `TemplateValuesMissing`（`src/ndf/progress/invoke.py:115-126,433`） | `grep -n "TemplateValuesMissing\|def template_values" plugins/ndf/src/ndf/progress/invoke.py` | 使う。決まらない差し込みの名前 → 出所は例外の `missing` が既に持ち、`items` の形（`{"key", "source"}`）も `items` プロパティが持つ。`KindInputsMissing` も同じ形にする |
| `prepare_plan` | `invoke._project`・`ci_only`（今 `plan_path` が `:593-594` で呼ぶ） | `grep -n "_project(\|ci_only(" plugins/ndf/src/ndf/progress/invoke.py` | 使う。設定は `prepare_plan` が 1 回だけ読み、`local_tests` を `values.json` に残して `plan_path` が読み直さない |
| `plan_failure` | `_call_plan` の `except` の並び（`:732-744`）と `_github_kind` | `grep -n "def _call_plan" -A 22 plugins/ndf/src/ndf/progress/invoke.py` | 使う（移す）。分類を 1 か所にし、`_start` と `_call_plan` の両方が呼ぶ |
| `step_dir` | `_run_step` の保存先の式（`src/ndf/progress/run.py:861`）・`step_location`（`:846`） | `grep -n "work_root(d.st.root" plugins/ndf/src/ndf/progress/run.py` | 使う（移す）。`_start` と `_run_step` が別々に組むと世代が食い違いうる |
| `prepare_plan` | `invoke.resolve_inputs`・`StageInputError`（`:86-112`） | `grep -n "class StageInput\|def resolve_inputs" plugins/ndf/src/ndf/progress/invoke.py` | 使えない。工程の入力が文脈にあるかの検査で、プランの差し込みを見ない。`_start` が止まる形（`kind` と `items` を持つ）だけそろえる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `prepare_plan` | 差し込みのそろう工程で `values.json` を書いて返る | 手元のテストのステップを外す設定（`test.ci.only`）のとき、そこだけが使う差し込みを求めず、`local_tests` が偽で残る | 決まらない工程で `TemplateValuesMissing`。工程「要求と受け入れ条件」で工程の入力に `issues` が無いと `KindInputsMissing` で、`missing` に `issues` と出所が入る。どちらも `values.json` を書かない |
| `_start` と `plan_path`（組んだ回数） | 工程「要求と受け入れ条件」を `ndf progress next` で起動し、そのステップを流すまでに、`template_values`・`_project`・`route.derived` を差し替えた見張りがそれぞれ 1 回だけ呼ばれる | 課題のコピー（`ISSUE_BODY_FILE`）を書く工程「実装」でも、課題の読み取りの見張りが 1 回 | — |
| `_start` | 決められない工程を `ndf progress next` で起動すると、事象に `started` も `failed` も増えない（受け入れ条件 3） | 決まる工程では今までどおり `started` が 1 件増え、`step_dir` の保存先（新しい世代の保存先を含む）に `values.json` と課題のコピーがある | 結果が `stopped`（`precondition`）で、`items` に名前と出所が並ぶ。課題の読み取りを一時障害の `GitHubError` にすると、`started` を書かずに `transient` と `retry_after` が返る |
| `plan_failure` | 6 つの例外の型ごとに、今の `_call_plan` と同じ種類（差し込みの欠けだけ `precondition`） | 分類に無い例外で `None` | — |

## 作業が作った出力をプランの結果へ渡す（`ndf worker run` の `outputs`）

満たす決定: 決定 1、受け入れ条件 1

工程「作業場所の用意」（`contracts/workflow/stages/worktree.json`）は `worktree`・`branch` を必須の出力にしている。プラン `worktree` のステップは worker の 1 つだけで、ほかに出力を作るステップが無い。今の `outcome_of`（`src/ndf/core/worker.py:593-614`）は結果ファイルの `status` と `summary` を写すだけで、`outputs` を写さないので、worker が作業場所を作っても工程は必須の出力の欠けで止まる。そこで、検証した結果ファイルの `outputs` を `ndf worker run` の結果 JSON の `outputs` へ写す。プランの実行の層は、ステップの結果 JSON の `outputs` を `outputs.json` に重ねてプランの結果へ載せる処理を既に持つ（`core/plan/runner.py:121-122`・`:425-426`）ので、そこから先の経路は今のままで工程の出力まで届く。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `outcome_of` | `plugins/ndf/src/ndf/core/worker.py` | 変更 | `outcome_of(kind: str, result_file: Path, items: list[dict[str, Any]], metrics: dict[str, Any], attempts: int, required: Sequence[str] = ()) -> dict[str, Any]` | `status` が `ok` のとき、結果ファイルの `outputs`（object）を返りの `outputs` へ写す。`required` の名前が `outputs` に無ければ、その名前を欠け（`gaps`）にした `incomplete` にする |
| `_add_run` | 同上 | 変更 | シグネチャは変わらない | `--require-output <名前>`（`action="append"`。省ける）を足す |
| `_cmd_run` | 同上 | 変更 | シグネチャは変わらない | `a.require_output` を `outcome_of` へ渡す |

- `outcome_of`
  - 事後条件: 返りの `outputs` は結果ファイルの `outputs` と同じ中身で、`result.result` が結果 JSON のスキーマ（`contracts/schemas/result.schema.json` の `$defs/outputs`。閉じた語彙）で検査する。`ndf worker run` は作業の定義を読まず、必須の名前は `ndf answer work` が `--require-output` で渡す（`kinds.json` の `outputs`）
  - 副作用: 無し（読むのは今までどおり結果ファイル 1 つ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `outcome_of` | `outputs` の形が違う | object でない・語彙に無いキー・値の型の違い（`worktree` が空の文字列など） | スキーマの検査 | 結果を `bad_report` にする | `stopped`（`bad_report`）。`summary` に結果ファイルのパスと検査の誤りを書く | worker の結果ファイルを直すか作業を打ち直す |
| `outcome_of` | 必須の出力が無い | worker が `--outputs` を付けずに `ok` を書いた | `required` と `outputs` のキーの差 | `incomplete` にする（欠けは `{"kind": "output", "name": <名前>, "reason": "作業の結果ファイルの outputs に無い"}`） | `stopped`（`incomplete`） | 打ち直せる。工程は `bad_report` でなく欠けの名前付きで止まる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `outcome_of` | 結果ファイルが `status: ok` と `outputs: {"worktree": "/r/.worktrees/b", "branch": "b"}` を持つとき、返りの `outputs` が同じ中身になる | `required` が空で `outputs` の無い結果ファイルは今までどおり `ok`。`gate`・`stopped` の結果ファイルの `outputs` は写さない | `required` が `worktree`・`branch` で結果ファイルに `branch` が無いと `incomplete` で、欠けに `branch` が入る。`outputs` が語彙に無いキーを持つと `bad_report` |
| プランの結果まで（`test_sprint_templates.py`） | プラン `worktree` の worker のステップを、`outputs` を書く結果ファイルで差し替えて流すと、プランの結果の `outputs` に `worktree`・`branch` が載り、工程「作業場所の用意」の出力の検査（`_stage_output` → `stages.keep_declared`）を通る | — | 結果ファイルが `outputs` を持たないと、工程は `bad_report` でなく `incomplete` で `branch`・`worktree` の欠けを返す |

## リファクタリングの既定の起動（`refactor`）

満たす決定: 決定 7、受け入れ条件 2

`ndf refactor run` の必須の引数は `--pr`・`--branch`・`--base`・`--scope`・`--required` の 5 つである（2026-11-23 に `ndf refactor run --help` で確かめた）。工程「リファクタリング」（`contracts/workflow/stages/refactor.json`）の入力は `worktree`・`branch`・`issues` で、テンプレート `cross-refactoring` の `params` は `repo`・`worktree`・`branch`・`remote`・`refactor`・`worker` である。PR の番号・マージ先・触るファイル・必須のチェックは、どれも工程の入力にもテンプレートの差し込みにも無い。そこで、それぞれを次の出所から決める。

| 引数 | 出所 | 決める時点 |
| --- | --- | --- |
| `--branch` | 差し込み `branch`（工程の入力） | プランを組むとき |
| `--base` | `route.default_base(repo)`（ブランチ戦略の設定）。`_review_value` がテンプレートに `base` が無いときに使う決め方と同じ（`core/route/__init__.py:476-478`） | プランを組むとき |
| `--required` | `.ndf/project.json` の `ci.required_checks`（`prepare_plan` が 1 回読んだ設定。`FILL_SOURCES` の `required` と同じ場所） | プランを組むとき |
| `--pr` | ブランチの PR の番号（`gh pr view <branch> --json number`）。`REVIEW_WITH_PR`（`core/route/__init__.py:95`）と同じく、打つ時点に読む | ステップを打つとき |
| `--scope` | マージ先からの差分のファイル（`git diff --name-only --diff-filter=d <remote>/<base>...HEAD`。作業場所で打つ）。工程の条件 `code_in_diff` が見る差分と同じ範囲 | ステップを打つとき |

PR の番号と差分は、前の工程（実装の PR の作成・push）が終わってからでないと決まらないので、`REVIEW_WITH_PR` と同じく `sh -c` の包みが打つ時点に読んで足す。工程の並びでは工程「実装」が単位の PR を作る（`impl.json` の `pr` のステップ）ので、工程「リファクタリング」の時点ではふつう PR がある。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `REFACTOR_WITH_PR` | `plugins/ndf/src/ndf/core/route/__init__.py` | 新設 | 定数（`sh -c` の本文） | `$0` のブランチの PR の番号と、`$1` のマージ先からの差分のファイルを読み、`"$@" --pr <番号> --scope <ファイル>…` を `exec` する。PR が無い・差分が空なら、`ndf contracts emit --tool refactor --status stopped --cause precondition "<理由>"` を出して終わる |
| `refactor_launch` | 同上 | 新設 | `refactor_launch(branch: str, base: str, required: Sequence[str]) -> list[str]` | `["sh", "-c", REFACTOR_WITH_PR, branch, base, <entry>, "refactor", "run", "--branch", branch, "--base", base, "--required", *required]` を返す。`review_launch`（`:461-466`）と同じ形 |
| `_fill_refactor` | `plugins/ndf/src/ndf/progress/invoke.py` | 変更 | `_fill_refactor(t, values, run, state_dir, *, project: Mapping[str, Any]) -> dict[str, route.Value]` | テンプレートが `cross-refactoring` で、`refactor` が設定（`workflow.refactor`）から決まっていなければ、`refactor_launch` で既定を組む。そのあと今までどおり候補を書いて `--candidates` を足す |

- `_fill_refactor`
  - 事前条件: `values` に `branch` と `repo` がある（`route.derived` の後）。`project` は `prepare_plan` が 1 回だけ読んだ設定
  - 事後条件: 設定に `workflow.refactor` があればそれを使い、無ければ既定の起動を返す。`ci.required_checks` が無い・空なら `refactor` を返さない（`_missing` が `refactor` を `FILL_SOURCES` の出所の文付きで挙げ、`prepare_plan` が `started` の前に止める）
  - 副作用: 今までどおり（候補のファイルの書き込み）。`default_base` の設定の読み取りが 1 回増える（`refactor` を設定から決めたときは読まない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_fill_refactor` | 必須のチェックが無い | `.ndf/project.json` に `ci.required_checks` が無い | 設定の値が空 | 既定を組まない（`refactor` が欠けとして残る） | `_start` が `stopped`（`precondition`）。`items` に `refactor` と出所。工程の条件 `ci_declared` が偽なら工程自体が飛ぶので、ここに来るのは宣言と設定が食い違うときだけ | 設定を足して打ち直せる |
| `REFACTOR_WITH_PR` | PR が無い | 工程「実装」が PR を作る前に流した・PR を閉じた | `gh pr view` の終了コードと空の返り | `stopped`（`precondition`）の結果 JSON を出して終える（`--pr` を空で `ndf refactor run` を打たない） | プランのステップが `stopped`（`precondition`）。`summary` は「ブランチ \<branch\> の PR が無い（工程「実装」の PR を作ってから打ち直す）」 | PR を作って工程を打ち直せる |
| `REFACTOR_WITH_PR` | 差分が空 | マージ先と同じ内容 | `git diff` の返りが空 | 同上（`stopped`（`precondition`）） | 「マージ先 \<base\> からの差分のファイルが無い」 | 後始末は無い |
| `REFACTOR_WITH_PR` | GitHub に届かない | 不達・認証 | `gh pr view` の失敗 | PR が無いときと分けず、`gh` の標準エラーを理由に添えて `stopped`（`precondition`） | 同上 | 打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_fill_refactor`（`test_invoke_refactor.py`） | `.ndf/project.json` に `workflow` が無く `ci.required_checks` が `["test"]` のリポジトリで、`refactor` が `sh -c <REFACTOR_WITH_PR> <branch> <base> ndf refactor run --branch <branch> --base <base> --required test` になる（受け入れ条件 2） | 設定に `workflow.refactor` があればそれが勝つ。候補があれば既定の後ろに `--candidates` が付く | `ci.required_checks` が無いと `prepare_plan` が `TemplateValuesMissing` で `refactor` を挙げる |
| `refactor_launch` | 返る argv の先頭が `sh`・`-c`・`REFACTOR_WITH_PR` で、続く位置引数が `branch`・`base`、その後ろが `ndf refactor run --branch … --base … --required …` | `required` が 2 件なら `--required` の後ろに 2 件並ぶ | — |

`REFACTOR_WITH_PR` の本文（PR が無いとき・差分が空のときの分かれ）は、子のプロセスを起動して確かめるテストを足さない（`tdd-cycle` の「テストに書かないもの」）。`REVIEW_WITH_PR` と同じく、実装の最後に工程「リファクタリング」を 1 回流して確かめ、Pull Request に残す。

## 出力の値の取り場所（`x-source`）

満たす決定: 決定 5、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `context_source` | `plugins/ndf/src/ndf/progress/definition.py` | 新設 | `context_source(key: str) -> Optional[str]` | 文脈のスキーマのキーの `x-source`（値の取り場所の 1 行）。無ければ `None` |
| `_missing_keys` | `plugins/ndf/src/ndf/progress/definition.py` | 変更 | `_missing_keys(obj: Any, part: str, keys: tuple[str, ...]) -> list[str]`（変わらない） | 欠けたキーの行に取り場所を添える |
| `_cmd_check` | `plugins/ndf/src/ndf/progress/command.py` | 変更 | `_cmd_check(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]`（変わらない） | 取り場所の付いた行を `summary` と `items` に出す |

- `context_source`
  - 事前条件: 無し
  - 事後条件: 文脈のスキーマ（`contracts/workflow/context.schema.json`）の `properties` にあるキーで、`x-source` を持つものはその文を返す
  - 副作用: コントラクトのファイルを 1 度読む（`contracts.load_json`。`definition.CONTEXT` の既存の読み取りと同じ）
- `_cmd_check`
  - 事後条件: 欠けたキーがあるとき、`items` に `{"kind": "missing", "key": <キー>, "source": <取り場所か null>}` が並ぶ。`summary` は今までどおり誤りをつないだ 1 文
  - 副作用: 無し（書かない。今までどおり）

欠けたキーの行は「`outputs: 文脈のキー <キー> が無い（取り場所: <x-source>）`」になり、`x-source` が無いキーでは今までどおり括弧を付けない。この行は `ndf progress check` の `summary`（`src/ndf/progress/command.py:674-684`）と `ndf progress finish` の拒否の理由にそのまま載る。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `context_source` | スキーマを読めない | ファイルが無い・壊れている | `contracts.load_json` | 呼び出し元へ返す（`contracts.ContractError`） | エントリポイントが `stopped`（`config_broken`） | 後始末は無い |
| `context_source` | キーが `x-source` を持たない | スキーマに書き足していないキー | 返り | 既定値で続ける（`None`。今までどおりの行） | 取り場所の無い行 | 後始末は無い |
| `_cmd_check` | 出力が規約に合わない | 工程の出力のキーの欠け・形の違い | `_read_stage_output` | 今までどおり `stopped`（`refused`） | `stopped`（`refused`）。欠けたキーの行に取り場所が付く | 出力を直して打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `X_SOURCE` | 定数 | `plugins/ndf/src/ndf/progress/definition.py` | `"x-source"` |
| キーごとの取り場所 | データ | `plugins/ndf/contracts/workflow/context.schema.json` の各 `properties` | 例: `delivery` は「`.ndf/project.json` の `branch_strategy.release.deliveries` の 1 行に `result` を足す」、`mvv_gate` は「ステートマシンの `release-mvv` のステップの outputs」 |

`x-source` は JSON Schema の語彙に無い接頭辞付きのキーで、検証では無視される（決定 5 の代償）。値の取り場所の唯一の保存先はこのスキーマで、手順書には書かない。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `context_source` | `definition._context_keys()`（`src/ndf/progress/definition.py:196`。`contracts.load_json(CONTEXT)["properties"]` を読む） | `grep -n "CONTEXT\b" plugins/ndf/src/ndf/progress/definition.py` | 使う。同じ読み取りの経路を共有し、スキーマを 2 か所から読まない |
| `context_source` | `definition.dig`（`:147`） | 同上 | 使う。入れ子の辞書を安全にたどる既存の関数 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `context_source` | `delivery`・`mvv_gate` で取り場所の文が返る（受け入れ条件 4） | `x-source` を持たないキーで `None` | スキーマに無いキーで `None` |
| `_missing_keys` | 欠けた `delivery` の行に取り場所が添う | 取り場所の無いキーの行は今までどおり | 出力が辞書でないときも今までどおり欠けとして並ぶ |
| `_cmd_check` | 欠けたキーのある出力で `items` に取り場所が載る（受け入れ条件 4） | 欠けが無い出力では `items` が空 | 出力のファイルが読めないときは今までどおり |
