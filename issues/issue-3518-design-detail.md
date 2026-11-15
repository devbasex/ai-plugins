# #3518 の詳細設計: ndf-next の宣言と入出力の規約の生成と検査

設計文書 [issue-3518-design.md](issue-3518-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。決定の番号（決定 1〜13）は [issue-3518-design-decisions.md](issue-3518-design-decisions.md) を指す。不変条件の番号（I1〜I15）は、設計文書の「不変条件」の表を指す。パスは断りの無い限り `plugins/ndf-next/` からの相対パスである。

### 用語の対応

| この文書の語 | 指すもの |
| --- | --- |
| 置き場の表 | `contracts/domains/ndf-files.json` |
| 環境変数の表 | `contracts/domains/env.json` |
| エントリポイントの表 | `contracts/domains/entrypoints.json`（公開する面の表として読む。決定 8） |
| hook の表 | `contracts/hooks/runtimes.json` |
| 止まった理由の表・終了コードの表 | `contracts/causes.json`・`contracts/facts/exit-codes.json` |
| 副命令の表 | `src/ndf/cli.py` の `COMMANDS`（22 個） |
| 配る Skill の表 | `manifests/skills.json` の `skills` |
| 規約の生成物 | 契約のページ（`docs/contracts/*.md` の 8 ページ）と引数のスキーマ（`contracts/schemas/io/*.args.schema.json` の 22 個） |
| 宣言の行 | 置き場の表のうち、種類（`kind`）が `declaration` で置き場（`place`）が `ndf` の 19 行 |

**宣言の行を `place` が `ndf` の行に絞る。** 置き場の表で `kind` が `declaration` の行は、2026-11-14 時点で 20 行ある。そのうち `relay-pinned-account`（置き場 `relay-accounts`、状態の置き場の下の `pinned.json`）は、利用者のリポジトリの `.ndf/` に置く宣言ではない。この行は設定ページの対象でなく、受け渡しのファイルのページに載る。設計文書の「19 行」「スキーマ 18 個と形の表 1 個」はこの絞り方で数えた値である。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── contracts/
│   ├── domains/
│   │   ├── entrypoints.json            （spec の 3 行と列の説明を足す）
│   │   ├── entrypoint-cases.json       （spec の見本 3 行を足す）
│   │   ├── env.json                    （HOME・UV_PROJECT_ENVIRONMENT の行を足す）
│   │   ├── ndf-files.json              （宣言の行の keys を直す）
│   │   ├── launchers.json *
│   │   ├── manual-args.json *
│   │   └── skill-io.json *
│   └── schemas/
│       ├── declarations/ *             （<id>.schema.json 18 個と mvv-body.sections.json）
│       └── io/ *                       （<副命令>.args.schema.json 22 個は生成物、<副命令>.result.schema.json 22 個は手書き）
├── docs/contracts/ *                   （index.md ほか 8 ページ。すべて生成物）
├── src/ndf/checks/
│   ├── README.md                       （規則 5 つと --write-spec を足す）
│   ├── rules.json                      （鍵 spec を足す）
│   ├── run.py                          （規約の検査を呼び、--write-spec を受ける）
│   ├── spec_args.py *
│   ├── spec_docs.py *
│   ├── spec_model.py *
│   └── spec_render.py *
└── tests/
    ├── conftest.py                     （結果 JSON の検査と実行の終わりの確かめ）
    ├── checks/test_spec_args.py *
    ├── checks/test_spec_docs.py *
    ├── cli/test_every_output.py *
    └── domains/test_entrypoints.py     （accepts に spec の枝を足す）
```

**依存は一方向で、輪は無い。** `run.py` → `spec_docs.py` → `spec_render.py` → `spec_model.py` → `spec_args.py` の順に下へだけ向く。`spec_model.py` は、既存の `ndf.contracts`（`facts`・`causes`・`exit_codes`）と `ndf.cli`（副命令の表）を読む。型には `ndf.checks.model`（`Finding`・`CheckError`・`Scope`）を使う。`ndf.cli` と `ndf.contracts` は `ndf.checks` を読まないため、輪にならない。`tests/conftest.py` は `ndf.contracts.result` と `ndf.checks.spec_model` の `validator` を読む。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/src/ndf/checks/spec_args.py` | 新設 | 副命令の `main` を引数を読む直前で止めて argparse の定義を受け取り、引数の木を作る | `argparse`・`importlib`（標準） |
| `plugins/ndf-next/src/ndf/checks/spec_model.py` | 新設 | 正本の表・スキーマ・引数の木・配る Skill の表・`bin/` を読み、規約のモデルを組む | `spec_args`・`ndf.contracts`・`ndf.cli`・`ndf.checks.model`・`jsonschema`・`referencing` |
| `plugins/ndf-next/src/ndf/checks/spec_render.py` | 新設 | モデルから契約のページと引数のスキーマの中身を文字列で作る | `spec_model` |
| `plugins/ndf-next/src/ndf/checks/spec_docs.py` | 新設 | 規則 `spec-docs`・`spec-decl`・`spec-io`・`spec-skill`・`spec-public` を当てて指摘を返し、`--write-spec` のときは生成物を書く | `spec_model`・`spec_render`・`ndf.checks.model` |
| `plugins/ndf-next/src/ndf/checks/run.py` | 変更 | 規約の検査を規則の 1 つとして呼び、`--write-spec` を受ける | `spec_docs` を足す |
| `plugins/ndf-next/src/ndf/checks/rules.json` | 変更 | 鍵 `spec`（規則の名前・生成物の置き場）を持つ | 無し |
| `plugins/ndf-next/src/ndf/checks/README.md` | 変更 | 規則 5 つと `--write-spec` の説明を持つ | 無し |
| `plugins/ndf-next/contracts/domains/manual-args.json` | 新設 | argparse を使わない副命令の引数の事実の表 | 無し |
| `plugins/ndf-next/contracts/domains/skill-io.json` | 新設 | 実体のある配る Skill の入出力の事実の表 | 置き場の表・環境変数の表の id |
| `plugins/ndf-next/contracts/domains/launchers.json` | 新設 | `bin/` のファイルごとの種類・渡し先・意味の事実の表 | 無し |
| `plugins/ndf-next/contracts/domains/ndf-files.json` | 変更 | 宣言の行の `keys` を読み手のコードに合わせる | 無し |
| `plugins/ndf-next/contracts/domains/env.json` | 変更 | `HOME`・`UV_PROJECT_ENVIRONMENT` の行を足す | 無し |
| `plugins/ndf-next/contracts/domains/entrypoints.json` | 変更 | 種類 `spec` の 3 行と、列 `kind`・`target` の説明を足す | 置き場の表の id（`files`） |
| `plugins/ndf-next/contracts/domains/entrypoint-cases.json` | 変更 | 種類 `spec` の 3 行に受ける見本を 1 つずつ足す | エントリポイントの表の id |
| `plugins/ndf-next/contracts/schemas/declarations/project.schema.json` | 新設 | 宣言 `project` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/supervise.schema.json` | 新設 | 宣言 `supervise` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/release.schema.json` | 新設 | 宣言 `release` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/worktree.schema.json` | 新設 | 宣言 `worktree` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/worktree-local.schema.json` | 新設 | 宣言 `worktree-local` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/glossary.schema.json` | 新設 | 宣言 `glossary` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/instructions.schema.json` | 新設 | 宣言 `instructions` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/mvv.schema.json` | 新設 | 宣言 `mvv` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/pace.schema.json` | 新設 | 宣言 `pace` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/review.schema.json` | 新設 | 宣言 `review` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/backlog.schema.json` | 新設 | 宣言 `backlog` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/runtime-policy.schema.json` | 新設 | 宣言 `runtime-policy` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/ask.schema.json` | 新設 | 宣言 `ask` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/context.schema.json` | 新設 | 宣言 `context` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/estimate.schema.json` | 新設 | 宣言 `estimate` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/issue-items.schema.json` | 新設 | 宣言 `issue-items` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/projects.schema.json` | 新設 | 宣言 `projects` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/code-metrics.schema.json` | 新設 | 宣言 `code-metrics` のスキーマ | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/mvv-body.sections.json` | 新設 | 宣言 `mvv-body`（`mvv.md`）の節の並びの形の表 | 無し |
| `plugins/ndf-next/contracts/schemas/io/` | 新設 | 引数のスキーマ 22 個（生成物）と結果のスキーマ 22 個（手書き。`../result.schema.json` を `$ref` で指す） | `contracts/schemas/result.schema.json` |
| `plugins/ndf-next/docs/contracts/index.md` | 新設 | 面の一覧と、公開と内部の約束（生成物） | 無し |
| `plugins/ndf-next/docs/contracts/declarations.md` | 新設 | 宣言のページ（生成物） | 無し |
| `plugins/ndf-next/docs/contracts/commands.md` | 新設 | 副命令の引数と結果のページ（生成物） | 無し |
| `plugins/ndf-next/docs/contracts/exit-codes.md` | 新設 | 終了コードと止まった理由のページ（生成物） | 無し |
| `plugins/ndf-next/docs/contracts/env.md` | 新設 | 環境変数のページ（生成物） | 無し |
| `plugins/ndf-next/docs/contracts/files.md` | 新設 | 受け渡しのファイルのページ（生成物） | 無し |
| `plugins/ndf-next/docs/contracts/hooks.md` | 新設 | hook の入出力のページ（生成物） | 無し |
| `plugins/ndf-next/docs/contracts/skills.md` | 新設 | Skill と起動スクリプトのページ（生成物） | 無し |
| `plugins/ndf-next/tests/conftest.py` | 変更 | テストの間に出た結果 JSON を確かめ、実行の終わりに ok の出力が無い副命令を落とす | `ndf.contracts.result`・`ndf.checks.spec_model`・`ndf.cli` |
| `plugins/ndf-next/tests/cli/test_every_output.py` | 新設 | 既存のテストが ok の出力を出さない副命令を呼ぶ | `ndf.cli` |
| `plugins/ndf-next/tests/checks/test_spec_args.py` | 新設 | 引数の取り出しの単体テスト | `spec_args` |
| `plugins/ndf-next/tests/checks/test_spec_docs.py` | 新設 | モデル・描画・検査の単体テストと、壊したら落ちる組み合わせ | `spec_model`・`spec_render`・`spec_docs` |
| `plugins/ndf-next/tests/domains/test_entrypoints.py` | 変更 | `accepts` に種類 `spec` の枝を足す | 無し |
| `docs/ndf-swap-doc-destinations.md` | 変更 | 行き先の表に 3 行を足す（決定 7 の表） | 無し |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。クラス図の型はすべて `spec_model.py` と `spec_args.py` の凍結した dataclass（`@dataclass(frozen=True)`）で置く。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `src/ndf/checks/spec_args.py` | `Arg`（新設）・`ArgTree`（新設）・`_Captured`（新設。例外） | `extract`・`tree_of`・`arg_of`・`from_manual`（新設） |
| `src/ndf/checks/spec_model.py` | `SpecModel`・`DeclarationSpec`・`CommandSpec`・`SkillSpec`・`HookSpec`（新設） | `load`・`declarations`・`commands`・`skills`・`hooks`・`launchers`・`public_ids`・`validator`（新設） |
| `src/ndf/checks/spec_render.py` | モジュール | `render`・`args_schema`・`page_index`・`page_declarations`・`page_commands`・`page_exit_codes`・`page_env`・`page_files`・`page_hooks`・`page_skills`・`md_table`・`dumps`（新設） |
| `src/ndf/checks/spec_docs.py` | モジュール | `check`・`decl_findings`・`io_findings`・`env_reads`・`skill_findings`・`written_calls`・`call_errors`・`public_findings`・`docs_findings`・`write`（新設） |
| `src/ndf/checks/run.py` | モジュール | `check`・`select`・`main`（変更） |
| `tests/conftest.py` | モジュール | `_checked_emit`（新設の fixture）・`record_output`・`pytest_configure`・`pytest_testnodedown`・`pytest_sessionfinish`・`is_full_run`（新設） |
| `tests/cli/test_every_output.py` | モジュール | `test_ok_output`（新設。副命令ごとに parametrize） |
| `tests/domains/test_entrypoints.py` | モジュール | `accepts`（変更。`spec` の枝を足すだけでシグネチャ・責務・失敗の扱いは変えない） |

## 関数の仕様

### 引数の取り出し（`spec_args.py`）

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `extract` | `src/ndf/checks/spec_args.py` | 新設 | `extract(module_path: str) -> ArgTree \| None` | 副命令のモジュールの `main([])` を `parse_known_args` の呼び出しで止め、その parser から引数の木を返す | `module_path` は副命令の表の値 | parser に達したら木を返し、達せずに `main` が戻るか例外で終わったら `None` を返す。`argparse.ArgumentParser.parse_known_args` は呼ぶ前の関数へ戻っている | モジュールの import と、`main` が parser に達するまでの処理。その間の標準出力と標準エラーは捨てる |
| `tree_of` | `src/ndf/checks/spec_args.py` | 新設 | `tree_of(parser: argparse.ArgumentParser) -> ArgTree` | parser の `_actions` を辿り、引数と下位の命令の木を作る | 無し | `--help` は含まない。下位の命令は `_SubParsersAction.choices` の名前の昇順、引数は定義の順に並ぶ | 無し |
| `arg_of` | `src/ndf/checks/spec_args.py` | 新設 | `arg_of(action: argparse.Action) -> Arg \| None` | 1 つの action を `Arg`（名前・位置引数か・型・必須・既定値・取りうる値・説明）にする | 無し | `_HelpAction`・`_VersionAction`・`_SubParsersAction` は `None`。既定値が `None` か `argparse.SUPPRESS` なら既定値を持たない | 無し |
| `from_manual` | `src/ndf/checks/spec_args.py` | 新設 | `from_manual(row: dict[str, Any]) -> ArgTree` | 手書きの引数の表の 1 行から引数の木を作る | 行は事実の表の形を通っている | 列 `subcommands` の下位の命令ごとに `ArgTree` を持つ。引数の並びは表の順 | 無し |

### 規約のモデル（`spec_model.py`）

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `load` | `src/ndf/checks/spec_model.py` | 新設 | `load(plugin_root: Path) -> SpecModel` | 下の 6 つの関数を呼び、規約のモデルを 1 つ組む | `ndf.contracts` の置き場が `plugin_root/contracts` を指す | 返したモデルの各並びは id の昇順で、同じ入力から同じモデルになる | ファイルを読む・副命令のモジュールを import する |
| `declarations` | `src/ndf/checks/spec_model.py` | 新設 | `declarations(plugin_root: Path) -> list[DeclarationSpec]` | 宣言の行ごとに、スキーマか形の表を読んで `DeclarationSpec` を作る | 無し | 行にスキーマも形の表も無ければ `schema` が `None` の `DeclarationSpec` を返す（I1 は `spec_docs` が判定する）。範囲は列 `tracked` から導く | ファイルを読む |
| `commands` | `src/ndf/checks/spec_model.py` | 新設 | `commands(plugin_root: Path) -> list[CommandSpec]` | 副命令の表の 22 個に、引数の木（`extract` か `from_manual`）と結果のスキーマを付ける | 無し | `CommandSpec.extracted` は `extract` が木を返したか、`manual` は手書きの引数の表に行があるかを持つ。両方が真なら木は `extract` の側を使う。結果のスキーマが無ければ `result_schema` は `None` | ファイルを読む・副命令のモジュールを import する |
| `skills` | `src/ndf/checks/spec_model.py` | 新設 | `skills(plugin_root: Path, public: set[tuple[str, str]]) -> list[SkillSpec]` | 配る Skill の表の行のうち `skills/<名前>/SKILL.md` がある Skill に、Skill の入出力の表の行を付ける | 無し | 公開する Skill の `args`・`env` はエントリポイントの表から、ほかは Skill の入出力の表から取る。入出力の表に行が無い Skill は `goal_end` などが `None` の `SkillSpec` になる（I7 は `spec_docs` が判定する） | ファイルを読む |
| `hooks` | `src/ndf/checks/spec_model.py` | 新設 | `hooks(plugin_root: Path) -> list[HookSpec]` | hook の表のランタイムと事象ごとに、読む入力の鍵・無くてよい鍵・判定ごとの出力の形を `HookSpec` にする | 無し | ランタイムの名前・事象の名前の昇順に並ぶ | ファイルを読む |
| `launchers` | `src/ndf/checks/spec_model.py` | 新設 | `launchers(plugin_root: Path) -> tuple[list[dict[str, Any]], list[str]]` | 起動スクリプトの表の行と、`bin/` にあるファイルの名前を返す | 無し | 2 つ目の並びは `bin/` の直下のファイルの名前の昇順（`__pycache__` を含めない） | ファイルを読む |
| `public_ids` | `src/ndf/checks/spec_model.py` | 新設 | `public_ids(entrypoints: list[dict[str, Any]], files: list[dict[str, Any]]) -> set[tuple[str, str]]` | エントリポイントの表と置き場の表の列 `reader` から、公開する項目の組（面・id）を集める | 無し | 組の面は `command`・`skill`・`hook`・`file`・`declaration`・`env` のどれか。種類 `spec` の行の `files` は面 `declaration`、ほかの行の `files` と `reader` を持つ置き場の表の行は面 `file`、`env` は面 `env` の組になる | 無し |
| `validator` | `src/ndf/checks/spec_model.py` | 新設 | `validator(rel: str) -> Draft202012Validator` | `contracts/<rel>` のスキーマの検証器を、`contracts/schemas/` の下のスキーマを `$id` で引ける registry つきで作る | `rel` は `contracts/` からの相対パス | 同じ `rel` には同じ検証器を返す（`ndf.contracts.cached_reader` で 1 回だけ作る）。`$ref: "../result.schema.json"` が引ける | ファイルを読む |

### 規約の描画（`spec_render.py`）

描画の関数はすべて純粋で、同じモデルから 1 字も違わない文字列を返す。行の並びは id の昇順で、日時・ホスト名・プラグインの名前を入れない（設計文書の「非機能の実現方式」の移行性）。

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `render` | `src/ndf/checks/spec_render.py` | 新設 | `render(model: SpecModel) -> dict[str, str]` | 契約のページ 8 つと引数のスキーマ 22 個の中身を、プラグインの根からの相対パスをキーにして返す | 無し | キーの集合は `docs/contracts/` の 8 ページと、`model.commands` の名前ごとの `contracts/schemas/io/<名前>.args.schema.json` に等しい | 無し |
| `args_schema` | `src/ndf/checks/spec_render.py` | 新設 | `args_schema(command: CommandSpec) -> dict[str, Any]` | 引数の木を、下位の命令ごとの `oneOf` の枝を持つ JSON Schema 2020-12 にする | `command.args` が `None` でない | 下位の命令の無い副命令は枝 1 つ。下位の命令の道（`lanes add` なら `["add"]`）は枝の `properties.subcommand.const` に入る。入れ子の下位の命令は空白でつないだ道にする | 無し |
| `page_index` | `src/ndf/checks/spec_render.py` | 新設 | `page_index(model: SpecModel) -> str` | 面の一覧と、公開と内部の約束（内部は予告なく変えてよい）を書く | 無し | 7 ページへのリンクと、面ごとの公開と内部の件数を持つ | 無し |
| `page_declarations` | `src/ndf/checks/spec_render.py` | 新設 | `page_declarations(model: SpecModel) -> str` | 宣言のファイルごとに、置き場・範囲・読む工程と、キーごとの型・既定値・取りうる値・必須か・説明・例を書く | 無し | `main_keys` のキーに「メインディレクトリから読む」の印が付く。非推奨のキーは「ndf-next は読まない」と書く | 無し |
| `page_commands` | `src/ndf/checks/spec_render.py` | 新設 | `page_commands(model: SpecModel) -> str` | 副命令と下位の命令ごとに、引数の表と、結果の `metrics`・`items` の形を書く | 無し | 各副命令の節は、引数のスキーマと結果のスキーマへの相対リンクを持つ | 無し |
| `page_exit_codes` | `src/ndf/checks/spec_render.py` | 新設 | `page_exit_codes(model: SpecModel) -> str` | 終了コードの表と止まった理由の表の全行を書く | 無し | 止まった理由の行は、種類・意味・終了コード・行き先を持つ | 無し |
| `page_env` | `src/ndf/checks/spec_render.py` | 新設 | `page_env(model: SpecModel) -> str` | 環境変数の表の全行を、公開か内部かと並べて書く | 無し | 列は名前・設定する者・型・秘密か・意味・公開か内部かの 6 つで、値の例の列を持たない | 無し |
| `page_files` | `src/ndf/checks/spec_render.py` | 新設 | `page_files(model: SpecModel) -> str` | 置き場の表の全行を、公開か内部かと並べて書く | 無し | 形（列 `fields`）は公開する行だけに書く | 無し |
| `page_hooks` | `src/ndf/checks/spec_render.py` | 新設 | `page_hooks(model: SpecModel) -> str` | ランタイムと事象ごとに、読む入力の鍵と判定ごとの出力の形を書く | 無し | 出力の形は JSON のコードブロックで書く | 無し |
| `page_skills` | `src/ndf/checks/spec_render.py` | 新設 | `page_skills(model: SpecModel) -> str` | 実体のある配る Skill ごとの入出力と、起動スクリプトの表の `launcher` の行を書く | 無し | 種類 `part` のファイルは載らない | 無し |
| `md_table` | `src/ndf/checks/spec_render.py` | 新設 | `md_table(header: list[str], rows: list[list[str]]) -> str` | Markdown の表を組み、セルの `\|` と改行を逃がす | 各行の長さが `header` と等しい | 末尾に改行を 1 つ持つ | 無し |
| `dumps` | `src/ndf/checks/spec_render.py` | 新設 | `dumps(obj: Any) -> str` | 引数のスキーマを決まった形の JSON の文字列にする | 無し | `json.dumps(obj, ensure_ascii=False, indent=2) + "\n"`。キーの並びは描画が組んだ順のまま | 無し |

### 規約の検査（`spec_docs.py`）

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `check` | `src/ndf/checks/spec_docs.py` | 新設 | `check(scope: Scope, rules: dict[str, Any], write_spec: bool = False) -> list[Finding]` | モデルを読み、描画し、`write_spec` なら書き、5 つの規則の指摘を返す | `scope.sources` が集めてある（`run.unparsable` の後） | 指摘の `rule` は `rules["spec"]["rules"]` の 5 つのどれか。`write_spec` のときは `spec-docs` の指摘が 0 件になる | ファイルを読む・副命令のモジュールを import する・`write_spec` のとき生成物を書く |
| `decl_findings` | `src/ndf/checks/spec_docs.py` | 新設 | `decl_findings(model: SpecModel, repo_root: Path) -> list[Finding]` | I1・I2・I12・I3 を当てる | 無し | I3 は `repo_root/.ndf/` にある JSON の宣言だけを確かめ、無いファイルは指摘しない | ファイルを読む |
| `io_findings` | `src/ndf/checks/spec_docs.py` | 新設 | `io_findings(model: SpecModel, scope: Scope) -> list[Finding]` | I4・I6・I11・I13 を当てる | `scope.code()` の構文木が作れる | I6 は `env_reads` が返した名前のうち環境変数の表に無いものを、ファイルと行つきで返す | 無し |
| `env_reads` | `src/ndf/checks/spec_docs.py` | 新設 | `env_reads(tree: ast.Module) -> list[tuple[int, str]]` | 構文木から、名前の文字列で読む環境変数（`os.environ["X"]`・`os.environ.get("X")`・`os.getenv("X")`・`"X" in os.environ`）を行と名前の組で集める | 無し | 名前が文字列の定数でない読み方は数えない | 無し |
| `skill_findings` | `src/ndf/checks/spec_docs.py` | 新設 | `skill_findings(model: SpecModel, scope: Scope) -> list[Finding]` | I7 と、Skill の本文・references・エージェント定義の書かれた呼び出し（I8）を当てる | 無し | I8 が見るのは `skills/` と `agents/` の下の `.md` だけ。公開する Skill が Skill の入出力の表に `args`・`env` を持てば指摘する（決定 6） | 無し |
| `written_calls` | `src/ndf/checks/spec_docs.py` | 新設 | `written_calls(text: str) -> list[tuple[int, list[str]]]` | 本文の行から `ndf` か末尾が `/bin/ndf` の語で始まる呼び出しを探し、行と後ろの語の並びを返す | 無し | `shlex.split` で語に分ける。引用符が閉じない行は空白で分ける。`ndf:` で始まる語（Skill の名前）は呼び出しに数えない | 無し |
| `call_errors` | `src/ndf/checks/spec_docs.py` | 新設 | `call_errors(words: list[str], commands: dict[str, CommandSpec]) -> list[str]` | 呼び出しの語を副命令・下位の命令・引数の名前で引数の木に当て、当たらない語を返す | 無し | `-` で始まる語は `=` の前までを名前として見る。位置の引数の数と値の型は見ない（決定 4）。`<…>` と `$…` の語は置き換え用の語として飛ばす | 無し |
| `public_findings` | `src/ndf/checks/spec_docs.py` | 新設 | `public_findings(model: SpecModel, plugin_root: Path) -> list[Finding]` | I10 と I15 を当てる | 無し | 列 `order` が `caller-first` の行は I10 の対象から外し、契約のページの「呼び手と合わせる前の行」に載る（設計の決定 13）。種類 `spec` の行も I10 の対象から外し、I15 だけを当てる | ファイルの有無を見る |
| `docs_findings` | `src/ndf/checks/spec_docs.py` | 新設 | `docs_findings(rendered: dict[str, str], plugin_root: Path, repo_root: Path) -> list[Finding]` | I9 を当て、描画した中身とコミットされたファイルの違いと、描画に無い生成物の残りを返す | 無し | 指摘はファイルごとに 1 件で、行は最初に違う行の番号 | ファイルを読む |
| `write` | `src/ndf/checks/spec_docs.py` | 新設 | `write(rendered: dict[str, str], plugin_root: Path) -> list[str]` | 描画した中身を書き、描画に無い生成物を消す | `rendered` のキーはプラグインの根からの相対パス | 中身の同じファイルは書かない（更新の時刻を変えない）。消すのは `docs/contracts/*.md` と `contracts/schemas/io/*.args.schema.json` に当たるファイルだけ | 生成物のファイルを書く・消す |

### 配線と出力の検査

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `check` | `src/ndf/checks/run.py` | 変更 | `check(scope: Scope, rules: dict[str, Any], write_spec: bool = False) -> list[Finding]` | 既存の規則に規約の検査を加えて当てる | 既存と同じ | 既存の指摘に `spec_docs.check` の指摘が加わり、並びは既存と同じくパスと行の順 | `write_spec` のとき生成物を書く |
| `select` | `src/ndf/checks/run.py` | 変更 | `select(scope: Scope, rules: dict[str, Any], only: list[str] \| None, write_spec: bool = False) -> list[Finding]` | `write_spec` を `check` へ渡す | 既存と同じ | 差分の規則だけのとき（`invariants.DIFF_RULES`）は規約の検査を通らず、`write_spec` を無視する | `write_spec` のとき生成物を書く |
| `main` | `src/ndf/checks/run.py` | 変更 | `main(argv: list[str]) -> int` | `--write-spec` を受けて `select` へ渡す | 既存と同じ | 結果 JSON の `metrics` に `written`（書いたファイルの数。`--write-spec` のときだけ）を足す | `--write-spec` のとき生成物を書く |
| `_checked_emit` | `tests/conftest.py` | 新設 | `_checked_emit(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None`（autouse の fixture） | テストの間だけ `ndf.contracts.result.emit` を、`record_output` を通してから元の `emit` を呼ぶ関数へ差し替える | 無し | テストが終わると元の `emit` へ戻る（`monkeypatch` が戻す） | 無し |
| `record_output` | `tests/conftest.py` | 新設 | `record_output(obj: dict[str, Any], code: int \| None, allow_bad: bool) -> None` | 結果 JSON と終了コードの組を I5 と I14 で確かめ、ok なら副命令の名前を記録する | `obj` は `emit` が受けた値 | I5 か I14 に合わなければ `pytest.fail` で、副命令と項目を出してテストを落とす。`allow_bad` が真なら I5 の誤りを落とさない。`tool` が副命令の表に無ければ I14 を見ない | 無し |
| `pytest_configure` | `tests/conftest.py` | 新設 | `pytest_configure(config: pytest.Config) -> None` | 印 `ndf_bad_result`（形の誤った結果を意図して作るテスト）を登録する | 無し | `--strict-markers` でも印を使える | 無し |
| `pytest_testnodedown` | `tests/conftest.py` | 新設 | `pytest_testnodedown(node: Any, error: Any) -> None` | 並列の実行で、終わった worker の記録（`node.workeroutput["ndf_ok_tools"]`）を集める | pytest-xdist の controller で呼ばれる | 集めた名前が controller の記録に加わる | 無し |
| `pytest_sessionfinish` | `tests/conftest.py` | 新設 | `pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None` | worker では記録を `workeroutput` へ渡し、controller か並列でない実行では、全体の実行のときだけ ok の出力の無い副命令で落とす | 無し | 落とすときは副命令の名前を標準エラーへ出し、`session.exitstatus` を 1 にする | 標準エラーへ書く |
| `is_full_run` | `tests/conftest.py` | 新設 | `is_full_run(config: pytest.Config) -> bool` | 実行が全体テスト（プラグインの根を指し、絞り込みが無い）かを返す | 無し | 引数のパスがプラグインの根だけで、`-k`・`-m`・`--deselect`・`--lf` が無いときだけ真 | 無し |
| `test_ok_output` | `tests/cli/test_every_output.py` | 新設 | `test_ok_output(name: str, tmp_path: Path) -> None`（副命令ごとに parametrize） | 既存のテストが ok の出力を出さない副命令を、ok を出す引数で呼ぶ | 呼ぶ副命令と引数は表 `CALLS` が持つ | 呼んだ副命令の終了コードが 0 | 副命令が書く `tmp_path` の下のファイル |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `extract` | 副命令のモジュールを import できない | 依存の欠け・構文の誤り | `importlib.import_module` の例外 | 止める（`CheckError` に包む） | `ndf check` が `config_broken` で終わる。終了コード 2 | 後始末は不要。直して打ち直す |
| `extract` | `main` が parser に達しない | argparse を使わない副命令・parser の前で戻る・`SystemExit` | `main` が戻る・`_Captured` 以外の例外 | 呼び出し元へ返す（`None`） | 手書きの引数の表に行が無ければ `spec-io`（I13）の指摘。終了コード 1 | 状態を残さない。`parse_known_args` は `finally` で元へ戻す |
| `extract` | argparse の内部の属性が変わった | Python の版の上げ | `tree_of` の `AttributeError` | 止める（`CheckError` に包む） | `config_broken`。終了コード 2。`test_spec_args.py` も落ちる | 後始末は不要。取り出しを直して打ち直す |
| `load` | 正本の表・スキーマ・配る Skill の表が読めない | ファイルが無い・JSON の誤り・同じキーが 2 つ | `ndf.contracts.ContractError`・`json` の例外 | 止める（`CheckError` に包む） | `config_broken`。終了コード 2。読めないファイルのパスを `summary` に出す | 後始末は不要。直して打ち直す |
| `declarations` | 宣言のスキーマがスキーマとして誤り | 書き誤り | `Draft202012Validator.check_schema` | 止める（`CheckError` に包む） | `config_broken`。終了コード 2 | 同上 |
| `commands` | 結果のスキーマが読めない | 同上 | `ContractError` | 止める（`CheckError` に包む） | `config_broken`。終了コード 2 | 同上 |
| `skills` | Skill の入出力の表が読めない | 同上 | `ContractError` | 止める（`CheckError` に包む） | `config_broken`。終了コード 2 | 同上 |
| `hooks` | hook の表が読めない | 同上 | `ContractError` | 止める（`CheckError` に包む） | `config_broken`。終了コード 2 | 同上 |
| `launchers` | 起動スクリプトの表が読めない・`bin/` が無い | 同上 | `ContractError`・`OSError` | 止める（`CheckError` に包む） | `config_broken`。終了コード 2 | 同上 |
| `validator` | `$ref` を引けない | `$id` の重複・相対パスの誤り | `referencing.exceptions.Unresolvable` | 止める（`CheckError` に包む） | `config_broken`。終了コード 2 | 同上 |
| `check` | 規則のデータに鍵 `spec` が無い | `rules.json` の書き誤り | 鍵の有無を見る | 止める（`CheckError`） | `config_broken`。終了コード 2 | 同上 |
| `check` | 規約の指摘がある | コード・正本の表・生成物の食い違い | 5 つの規則 | 呼び出し元へ返す（指摘の並び） | 1 件 1 行で標準エラーへ `<規則>: <パス>:<行>: <内容>`、結果 JSON の `items`。`check_failed`、終了コード 1 | 後始末は不要。直すか `--write-spec` を打って打ち直す |
| `decl_findings` | `.ndf/` の実物が JSON として読めない | 利用者の書き誤り | `json` の例外 | 呼び出し元へ返す（`spec-decl` の指摘 1 件） | `check_failed`。終了コード 1 | 実物を直して打ち直す |
| `public_findings` | `spec` の行の `target` のディレクトリが無い | 置き場の移動の書き漏れ | `Path.is_dir` | 呼び出し元へ返す（`spec-public` の指摘） | `check_failed`。終了コード 1 | 表を直して打ち直す |
| `docs_findings` | コミットされた生成物が読めない | 文字コードの誤り | `UnicodeDecodeError` | 呼び出し元へ返す（`spec-docs` の指摘。「読めない」） | `check_failed`。終了コード 1 | `--write-spec` で書き直せる |
| `write` | 書けない | 権限・ディスクの満杯 | `OSError` | 止める（`CheckError` に包む） | `config_broken`。終了コード 2。書けなかったパスを `summary` に出す | 途中まで書いたファイルが残る。書く中身は毎回同じなので、打ち直せば揃う |
| `run.check` | 規約の検査の例外 | 上の各行 | `spec_docs.check` | 呼び出し元へ返す（`CheckError` のまま） | `main` が `config_broken` の結果 JSON に変える | 既存と同じ |
| `select` | 同上 | 同上 | `check` | 呼び出し元へ返す | 同上 | 既存と同じ |
| `main` | `--write-spec` と差分の規則だけの `--only` を同時に渡した | 呼び方の誤り | `select` の分岐 | 既定値で続ける（書かずに差分の規則だけを当てる） | 既存の結果。`metrics.written` を置かない | 打ち直せる |
| `pytest_sessionfinish` | ok の出力が 1 度も確かめられない副命令がある | テストが足りない | 記録と副命令の表の差 | 止める（`exitstatus` を 1 にする） | 標準エラーに `ok の出力が確かめられない副命令: <名前>`。pytest の終了コード 1 | `test_every_output.py` に呼び出しを足して打ち直す |
| `test_ok_output` | 副命令が ok を返さない | 用意の不足か副命令の不具合 | 終了コードの比較 | 呼び出し元へ返す（テストの失敗） | pytest の失敗の表示 | `tmp_path` は pytest が片付ける |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `spec_docs.io_findings`（I13） | 取り出せた・手書きの行が無い | 指摘なし（生成の元は argparse） |
| 同上 | 取り出せた・手書きの行がある | 指摘「argparse から取り出せる。手書きの引数の表の行を消す」 |
| 同上 | 取り出せない・手書きの行がある | 指摘なし（生成の元は手書きの引数の表） |
| 同上 | 取り出せない・手書きの行が無い | 指摘「引数を取り出せず、手書きの引数の表にも行が無い」。引数のスキーマは描画しない |
| `spec_docs.decl_findings`（I1） | JSON の宣言（`path` が `.json`）・スキーマがある | 指摘なし |
| 同上 | Markdown の宣言（`path` が `.md`）・形の表がある | 指摘なし |
| 同上 | 上のどちらでもない | 指摘「宣言 <id> にスキーマか形の表が無い」 |
| `spec_docs.decl_findings`（I2） | スキーマの最上位の非推奨でないキー ＝ 表の `keys` | 指摘なし |
| 同上 | 表だけにあるキー・スキーマだけにあるキー | キーごとに 1 件（宣言の id とキーの名前と、どちらにだけあるか） |
| `spec_docs.decl_findings`（I3） | `.ndf/<path>` が無い | 指摘なし（`.ndf/` の無いリポジトリは 0 件で通る） |
| 同上 | ある・スキーマに通る | 指摘なし |
| 同上 | ある・通らない | 誤りごとに 1 件（ファイルと JSON Pointer） |
| `spec_docs.public_findings`（I10） | エントリポイントの行が規約の項目に当たる | 指摘なし（項目は公開） |
| 同上 | 当たらない・列 `order` が `caller-first` | 指摘なし（契約のページの「呼び手と合わせる前の行」に載る） |
| 同上 | 種類 `spec` | I10 の指摘なし（I15 だけを当てる） |
| 同上 | 当たらない・ほかの `order` | 指摘「公開する面の行 <id> が規約のどの項目にも当たらない」 |
| `spec_docs.docs_findings`（I9） | 描画にある・ファイルが無い | 指摘「生成物が無い」 |
| 同上 | 描画にある・中身が違う | 指摘「生成し直した中身と違う」（最初に違う行） |
| 同上 | 描画に無い・生成物の置き場にファイルがある | 指摘「生成されないファイルが残る」 |
| `spec_args.arg_of`（型） | `store_true`・`store_false` | `boolean` |
| 同上 | `type` が `int`・`float` | `integer`・`number` |
| 同上 | `append`・`nargs` が `*`・`+`・数 | `array`（要素の型は上の規則） |
| 同上 | それ以外 | `string` |
| `conftest.record_output` | I5 に合わない・印 `ndf_bad_result` が無い | テストを落とす |
| 同上 | I5 に合わない・印がある | 何もしない（元の `emit` が invalid を返す） |
| 同上 | I5 に合う・`tool` が副命令の表にある・結果のスキーマに通らない | テストを落とす |
| 同上 | I5 と I14 に合う・`status` が `ok` | 副命令の名前を記録する |

- 繰り返しの終わる条件: `tree_of` は `_SubParsersAction.choices` を再帰で辿り、下位の命令の無い parser で戻る。argparse の下位の命令は木で、輪を作れない
- 計算量の注意: `extract` は副命令のモジュールを 22 個 import する。2026-11-14 の試作で 22 個の取り出しを手元で打ち、1 秒台で終わった。`ndf check` の 1 回に 1 度だけ呼ぶ（`load` の中）。`env_reads` と `written_calls` は `run.collect` が集めた `Source` の構文木と本文を使い、ファイルを読み直さない

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Arg` | 型（`name: str`・`positional: bool`・`type: str`・`required: bool`・`default: Any`・`choices: tuple[str, ...] \| None`・`help: str`） | `src/ndf/checks/spec_args.py` | — |
| `ArgTree` | 型（`options: tuple[Arg, ...]`・`subcommands: dict[str, ArgTree]`） | `src/ndf/checks/spec_args.py` | — |
| `DeclarationSpec` | 型（設計文書のクラス図の欄に `path: str`・`tracked: bool`・`sections: list[dict] \| None` を足す） | `src/ndf/checks/spec_model.py` | — |
| `CommandSpec` | 型（クラス図の欄に `module: str`・`extracted: bool`・`manual: bool` を足す） | `src/ndf/checks/spec_model.py` | — |
| `SkillSpec` | 型（クラス図の欄のまま） | `src/ndf/checks/spec_model.py` | — |
| `HookSpec` | 型（`runtime: str`・`event: str`・`reads: list[str]`・`optional: list[str]`・`outputs: dict[str, Any]`・`surface: str`） | `src/ndf/checks/spec_model.py` | — |
| `SpecModel` | 型（クラス図の欄に `orders: dict[str, str]`（エントリポイントの id → 列 `order`）を足す） | `src/ndf/checks/spec_model.py` | — |
| `DECL_KIND`・`DECL_PLACE` | 定数 | `src/ndf/checks/spec_model.py` | `"declaration"`・`"ndf"` |
| `DECL_SCHEMA_DIR`・`IO_SCHEMA_DIR` | 定数（`contracts/` からの相対パス） | `src/ndf/checks/spec_model.py` | `"schemas/declarations"`・`"schemas/io"` |
| `PAGES_DIR` | 定数（プラグインの根からの相対パス） | `src/ndf/checks/spec_model.py` | `"docs/contracts"` |
| 規則の名前 5 つと生成物の置き場 | 設定値 | `src/ndf/checks/rules.json` の鍵 `spec`（`rules`・`generated` の 2 つ） | `rules`: `spec-docs`・`spec-decl`・`spec-io`・`spec-skill`・`spec-public`。`generated`: `docs/contracts/*.md`・`contracts/schemas/io/*.args.schema.json` |
| 範囲の語 | 定数 | `src/ndf/checks/spec_render.py` | `tracked` が真なら「チーム」、偽なら「個人」 |
| 公開か内部かの語 | 定数 | `src/ndf/checks/spec_render.py` | 「公開」・「内部（予告なく変えてよい）」 |
| `ndf_ok_tools` | `workeroutput` の鍵 | `tests/conftest.py` | — |
| `CALLS` | 表（副命令の名前 → 引数と用意） | `tests/cli/test_every_output.py` | 実装で全体テストを流して決める（設計の「未確認のまま残ること」） |

**副命令の表と、置き場・環境変数・hook の表の値を写さない。** 規約のモデルは `ndf.cli.COMMANDS` と `ndf.contracts.facts` を毎回読み、名前の一覧を定数に持たない。生成物の置き場は `rules.json` の `generated` だけが持ち、`write` と `docs_findings` は同じ値を読む。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `ndf check --write-spec` を 2 つの worktree で打つ | 無し（worktree ごとに別のファイル） | 不要 | 冪等。同じ入力から同じ中身を書く |
| 同じ worktree で `ndf check --write-spec` を 2 つ同時に打つ | 生成物のファイル | 取らない。書く中身が同じで、後に書いた方が残っても中身は変わらない | 冪等。途中で止まっても打ち直せば揃う |
| pytest-xdist の並列の worker | `ndf.contracts.result.emit`（worker ごとに別のプロセス） | 不要。worker ごとの記録を controller が `pytest_testnodedown` で集める | 1 つの worker が落ちたときは `workeroutput` が届かず、全体の確かめの漏れになる。その実行は既に失敗なので、打ち直す |
| `extract` が副命令のモジュールの `main` を流す間 | `argparse.ArgumentParser.parse_known_args`（プロセスの中のクラスの属性） | `ndf check` は 1 本のスレッドで動く。差し替えは `try`/`finally` で戻す | 例外で抜けても戻る |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `run.main` | 規約の指摘を 1 件 1 行（`<規則>: <パス>:<行>: <内容>`） | 標準エラー | 秘密の値（環境変数は名前だけを出す） |
| `run.main` | `metrics.findings` の規則ごとの件数、`metrics.written` | 結果 JSON | 同上 |
| `spec_docs.write` | 書いた・消したファイルのパス | 結果 JSON の `metrics.written`（数）。パスは出さない | 同上 |
| `spec_args.extract` | 無し。`main` が出す標準出力と標準エラーは捨てる | — | 副命令が出す内容 |
| `spec_render.page_env` | 環境変数の名前・意味 | 契約のページ | 値の例（列を持たない） |
| `conftest.record_output` | 合わなかった副命令と項目 | pytest の失敗の表示 | 結果 JSON の全文（項目の場所と理由だけ） |
| `conftest.pytest_sessionfinish` | ok の出力の無い副命令の名前 | 標準エラー | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `extract` | `ndf.cli` の副命令の呼び出し（`_run`） | `src/ndf/cli.py` を読む | 使えない。状態の置き場の用意と結果 JSON の出力まで流すため、引数の定義だけを取れない。モジュールの引きは `COMMANDS` の値をそのまま使う |
| `tree_of` | sphinx-argparse・argparse-manpage（LIB4・LIB5） | 設計の外部の調査 | 使えない。JSON Schema を出さない（決定 3） |
| `arg_of` | 同上 | 同上 | 同上 |
| `from_manual` | `ndf.contracts.facts.row` | `grep -n "^def " src/ndf/contracts/facts.py` | 使う。行の読み出しはこの関数で行い、`from_manual` は行から木を作るだけにする |
| `load` | `ndf.contracts.facts`（`rows`・`ids`・`row`）・`ndf.contracts.load_json` | 同上と `src/ndf/contracts/__init__.py` を読む | 使う。事実の表とスキーマの読み出しはこれらに任せる |
| `declarations` | 同上 | 同上 | 使う |
| `commands` | `ndf.cli.COMMANDS` | `src/ndf/cli.py` を読む | 使う。副命令の名前を写さない |
| `skills` | `ndf.domains.manifests` の配る Skill の表の読み出し | `grep -rn "skills.json" src/ndf/domains/manifests` | 実装の着手時に関数の名前を確かめて使う。読み出しを持たなければ `ndf.contracts.load_json` と同じ形で読む |
| `hooks` | `ndf.domains.hooks.registration` の hook の表の読み出し | `src/ndf/domains/hooks/registration.py` を読む | 実装の着手時に関数の名前を確かめて使う。登録のファイルの生成と同じ読み出しを通し、表を 2 度解釈しない |
| `launchers` | `ndf.contracts.facts.rows` | 同上 | 使う |
| `public_ids` | `tests/domains/test_entrypoints.py` の `accepts` | 読む | 使えない。テストの中のコピーで、製品のコードから読めない。公開か内部かの判定は新しく持つ |
| `validator` | `ndf.contracts._schema_validator`・`ndf.contracts.cached_reader` | `src/ndf/contracts/__init__.py:100-113` を読む | `cached_reader` は使う。`_schema_validator` は registry を持たず、別ファイルの `$ref`（結果のスキーマから結果の契約）を引けないため使えない |
| `render` | json-schema-for-humans・jsonschema2md（LIB2・LIB3） | 設計の外部の調査 | 使えない（決定 2） |
| `args_schema` | 同上 | 同上 | 同上 |
| `page_index`・`page_declarations`・`page_commands`・`page_exit_codes`・`page_env`・`page_files`・`page_hooks`・`page_skills` | 同上と `ndf.domains.hooks.registration` の描画 | `grep -rn "def render" src/ndf` | 使えない。登録のファイルの描画は JSON を出すもので、Markdown の表を組まない |
| `md_table` | `ndf` の中の Markdown の表の組み立て | `grep -rn '" | "\.join\|"|".join' src/ndf` | 実装の着手時に検索し、あれば使う。無ければ新設する |
| `dumps` | `ndf.contracts.result.dumps_line` | `src/ndf/contracts/result.py:139-141` を読む | 使えない。1 行の JSON を出すもので、差分を読める字下げを持たない |
| `check` | `ndf.checks.facts.check` の形（`scope`・`rules` を受けて `Finding` を返す） | `src/ndf/checks/facts.py:225` を読む | 形を揃える。`run.check` が同じ呼び方で並べる |
| `decl_findings` | `ndf.contracts.schema_errors` | `src/ndf/contracts/__init__.py:116-120` を読む | 誤りの文の形（`<場所>: <理由>`）を揃え、検証器は `validator` を使う |
| `io_findings` | `ndf.checks.model.Scope.code` | `src/ndf/checks/model.py:80-82` を読む | 使う |
| `env_reads` | `ndf.checks.facts.lit` | `src/ndf/checks/facts.py:46` を読む | 使う。添字と引数の定数の文字列の取り出しに使う |
| `skill_findings` | `ndf.checks.model.Scope.sources` | 読む | 使う。`skills/` と `agents/` の `.md` は `run.collect` が既に集めている |
| `written_calls` | 標準の `shlex` | — | 使う |
| `call_errors` | 無し | `grep -rn "shlex" src/ndf/checks` | 該当なし。新設する |
| `public_findings` | 無し | `grep -rn "entrypoints" src/ndf` | `progress/plan/external.py` が id で 1 行を引くだけで、表全体を突き合わせる部品は無い。新設する |
| `docs_findings` | `ndf.contracts.result.render_report` と `ndf.domains.hooks.registration.stale` | 2 つを読む | `stale` の形（生成し直した中身と差分を比べる）に倣う。`render_report` は副命令の結果を出す部品で、`ndf check` の規則の指摘には使えない |
| `write` | 同上 | 同上 | 同上 |
| `_checked_emit` | `pytest.MonkeyPatch.setattr` | — | 使う |
| `record_output` | `ndf.contracts.result.errors` | `src/ndf/contracts/result.py:113-125` を読む | 使う（I5） |
| `pytest_configure` | 無し | `grep -rn "pytest_configure" tests` | 該当なし。新設する |
| `pytest_testnodedown` | pytest-xdist の `workeroutput` | `pyproject.toml` の `pytest-xdist==3.8.0` | 使う |
| `pytest_sessionfinish` | 同上 | 同上 | 使う |
| `is_full_run` | 無し | `grep -rn "sessionfinish\|workeroutput" tests` | 該当なし。新設する |
| `test_ok_output` | `tests/cli/test_cli.py` の呼び方（`cli.main` を直に呼ぶ） | 読む | 使う |

## 単体テストの観点

置き場は `tests/checks/test_spec_args.py`（`spec_args`）と `tests/checks/test_spec_docs.py`（ほか）である。検査の関数は、事実の表とスキーマを `tmp_path` に作った小さな契約の置き場（`ndf.contracts.use_dir`）で確かめ、このリポジトリの実物に頼らない。壊したら落ちる組み合わせは設計文書の「テスト設計」の表を正本にし、ここでは関数の粒度だけを書く。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `extract` | 下位の命令を 2 つ持つ見本のモジュールから木を取り出す | `main` が parser の前に戻る見本で `None` | import できない見本で `CheckError`。`main` が `SystemExit` を出す見本で `None`。取り出しの後に `parse_known_args` が元の関数へ戻っている |
| `tree_of` | 引数と下位の命令の並び | 下位の命令の中にさらに下位の命令がある | argparse の内部の属性が無い偽の parser で `CheckError` |
| `arg_of` | 型の判定表の 4 行 | 既定値が `None`・`SUPPRESS` で既定値を持たない | `_HelpAction` で `None` |
| `from_manual` | `notify` の行（`send` だけ） | 下位の命令も引数も無い行（`launch-args`） | 無し（行は事実の表の形を通っている） |
| `load` | 小さな契約の置き場からモデルを組む | 2 回組んで等しい | 表の JSON を壊して `CheckError` |
| `declarations` | JSON の宣言と Markdown の宣言 | `tracked` が偽で範囲が「個人」 | スキーマとして誤りで `CheckError`。スキーマが無い行で `schema` が `None` |
| `commands` | argparse の副命令と手書きの副命令 | 両方に当たる副命令で `extracted` と `manual` が真 | 結果のスキーマが壊れて `CheckError` |
| `skills` | 公開する Skill の `args` がエントリポイントの表から来る | `SKILL.md` の無い配る Skill は載らない | 入出力の表が壊れて `CheckError` |
| `hooks` | 2 つのランタイムの事象が並ぶ | 出力の形の無い判定 | 表が壊れて `CheckError` |
| `launchers` | 3 ファイルと 3 行 | `__pycache__` を数えない | `bin/` が無くて `CheckError` |
| `public_ids` | 種類ごとの組 | `reader` を持つ置き場の表の行が面 `file` になる | 無し（純粋な関数） |
| `validator` | 結果のスキーマが `../result.schema.json` を引く | 同じ `rel` で同じ検証器 | `$ref` の先が無くて `CheckError` |
| `render` | キーの集合が 8 ページと副命令ごとの引数のスキーマ | 2 回描いて 1 字も違わない | 無し（純粋な関数） |
| `args_schema` | 下位の命令ごとの `oneOf` の枝 | 下位の命令の無い副命令で枝 1 つ | 無し |
| `page_index` | 7 ページへのリンク | 公開の項目が 0 件の面 | 無し |
| `page_declarations` | `main_keys` の印 | 非推奨のキー | 無し |
| `page_commands` | 引数の表と結果の形 | 引数の無い副命令 | 無し |
| `page_exit_codes` | 2 つの表の全行 | 無し | 無し |
| `page_env` | 全行と公開か内部か | 秘密の行が名前・設定する者・型・秘密か・意味・公開か内部かの 6 つのセルだけを持つ | 無し |
| `page_files` | 全行 | 公開しない行に形を書かない | 無し |
| `page_hooks` | 事象ごとの読む鍵と出力の形 | 無くてよい鍵 | 無し |
| `page_skills` | Skill と起動スクリプト | 種類 `part` のファイルが載らない | 無し |
| `md_table` | 表の組み立て | セルの `\|` と改行を逃がす | 無し |
| `dumps` | 末尾の改行と字下げ | 非 ASCII をそのまま | 無し |
| `check` | 食い違いの無い置き場で 0 件 | `write_spec` の後に `spec-docs` が 0 件 | 鍵 `spec` の無い規則のデータで `CheckError` |
| `decl_findings` | I1・I2・I12・I3 が 0 件 | `.ndf/` の無いリポジトリで 0 件 | 判定表の指摘の行ごと。実物が JSON でなくて 1 件 |
| `io_findings` | 0 件 | 判定表の I13 の 4 行 | `bin/` に表に無いファイル（I11）・結果のスキーマの無い副命令（I4）・表に無い環境変数（I6） |
| `env_reads` | 4 つの読み方 | 名前が変数の読み方を数えない | 無し |
| `skill_findings` | 0 件 | 呼び出しの無い本文 | 入出力の表に行の無い Skill（I7）・規約に無い引数の呼び出し（I8）・公開する Skill が `args` を持つ行 |
| `written_calls` | `ndf lanes add --x` と `bin/ndf check` | `ndf:design` を数えない | 引用符の閉じない行を空白で分ける |
| `call_errors` | 当たる呼び出しで空 | `<番号>`・`$VAR`・`--x=1` | 無い副命令・無い下位の命令・無い引数の名前 |
| `public_findings` | 0 件 | `order` が `caller-first` の当たらない行 | 当たらない行（I10）・欠けた置き場と無いディレクトリ（I15） |
| `docs_findings` | 0 件 | 最初に違う行の番号 | 無い・違う・残る・読めないの 4 種 |
| `write` | 書いて `docs_findings` が 0 件 | 中身の同じファイルの更新の時刻が変わらない | 書けないディレクトリで `CheckError` |
| `run.check` | 既存の指摘と規約の指摘が並ぶ | 規約の指摘 0 件で既存のまま | `spec_docs` の `CheckError` がそのまま上がる |
| `select` | `write_spec` を渡す | 差分の規則だけの `only` で規約の検査を通らない | 無し |
| `main` | `--write-spec` で `metrics.written` | 書くものが無くて `written` が 0 | 読めない表で `config_broken`・終了コード 2 |
| `_checked_emit` | テストの間だけ差し替わる | テストの後に元の `emit` | 無し |
| `record_output` | ok を記録する | `tool` が副命令の表に無い結果は I14 を見ない | I5 に合わずに落ちる・印で落ちない・I14 に合わずに落ちる |
| `pytest_configure` | 印が登録される | 無し | 無し |
| `pytest_testnodedown` | worker の記録が集まる | 記録の無い worker | 無し |
| `pytest_sessionfinish` | すべての副命令が記録され通る | 一部の実行で確かめない | 記録の無い副命令で終了コード 1 |
| `is_full_run` | プラグインの根だけで真 | パスの指定・`-k` で偽 | 無し |
| `test_ok_output` | `CALLS` の副命令が ok | 無し | 無し（落ちたら副命令か用意を直す） |
