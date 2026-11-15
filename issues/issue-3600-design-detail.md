# #3600 の詳細設計: エピックの土台・起動の前の検査・完了条件・土台の知らせ

設計文書 [issue-3600-design.md](issue-3600-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。書き方は NDF の `design` Skill の `references/detailed-design.md` にある。

対象は 11.0 の構造（2026-11-15 の `plugins/ndf-next/`）である。今の `plugins/ndf/` には書かない。11.0（#1142）が閉じるまでに置き場や関数の名前が変わりうるため、11.x の着手の前に「ファイル配置」の表のパスがあるかを確かめる（設計文書の「未確認のまま残ること」）。

**用語と表記。**

| この文書の書き方 | 指すもの |
| --- | --- |
| 土台 | エピックの土台（設計文書の「用語」）。土台の文書 1 本を指す |
| 計画 | 並列の計画。実装計画とは呼び分ける |
| ベースの版 | `origin/<ベースブランチ>` にある土台。ベースブランチは `.ndf/worktree.json` の `base_branch`、無ければ origin の既定ブランチ |
| 完了条件待ちの依頼 | 計画の進行表の依頼のうち、中身の `kind` が `epic_done_wait` のもの（設計文書の「計画の終わりの判定」） |
| 指摘 | 結果 JSON の `items` の 1 行（`rule`・`name`・`detail`） |

関数は「モジュールの短い名前.関数名」で書く。短い名前とパスの対応は次のとおりである（パスは `plugins/ndf-next/src/ndf/` から）。

| 短い名前 | パス |
| --- | --- |
| `markdown` | `adapters/markdown.py` |
| `foundation_decide` | `progress/epic/foundation_decide.py` |
| `foundation` | `progress/epic/foundation.py` |
| `verify` | `progress/epic/verify.py` |
| `epic_cli` | `progress/epic/cli.py` |
| `plan` | `progress/plan/__init__.py` |
| `plan_cli` | `progress/plan/cli.py` |
| `plan_epic` | `progress/plan/epic.py` |
| `plan_run` | `progress/plan/run.py` |
| `run_decide` | `progress/plan/run_decide.py` |
| `lane` | `progress/lane/__init__.py` |
| `fold` | `progress/lane/fold.py` |
| `rules` | `progress/lane/rules.py` |
| `types` | `progress/lane/types.py` |
| `command` | `progress/command.py` |
| `progress_run` | `progress/run.py` |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── contracts/
│   ├── epic/
│   │   └── foundation.schema.json *
│   ├── limits.json
│   └── workflow/
│       ├── context.schema.json
│       ├── parallel-plan.schema.json
│       └── progress.json
├── docs/contracts/commands.md
├── skills/
│   ├── design/
│   │   ├── SKILL.md
│   │   └── references/epic-foundation.md *
│   ├── development-workflow/SKILL.md
│   └── issue-plan-strategy/references/parallel-plan.md
├── src/ndf/
│   ├── cli.py
│   ├── adapters/markdown.py
│   └── progress/
│       ├── command.py
│       ├── run.py
│       ├── epic/ *
│       │   ├── __init__.py *
│       │   ├── cli.py *
│       │   ├── foundation.py *
│       │   ├── foundation_decide.py *
│       │   └── verify.py *
│       ├── lane/
│       │   ├── __init__.py
│       │   ├── fold.py
│       │   ├── rules.py
│       │   └── types.py
│       └── plan/
│           ├── __init__.py
│           ├── cli.py
│           ├── epic.py *
│           ├── run.py
│           └── run_decide.py
└── tests/
    ├── adapters/test_markdown.py
    └── progress/
        ├── test_command.py
        ├── test_epic_cli.py *
        ├── test_epic_foundation.py *
        ├── test_epic_verify.py *
        ├── test_plan_epic.py *
        └── test_run.py
```

**依存の向き。** 土台の読み取りは層 `ndf.progress` に置く。並列の計画（`ndf.progress.plan`）が土台を読み、層の規則（`checks/rules.json` の `structure.layers`）は `ndf.progress` から `ndf.domains` を読むことを許さないためである。`ndf.progress.epic` は `ndf.contracts`・`ndf.adapters`・`ndf.core.route` だけを読み、`ndf.progress.plan`・`ndf.progress.lane` を読まない。`ndf.progress.plan` と `ndf.progress.lane` が `ndf.progress.epic` を読む。`ndf.progress.lane` は `ndf.progress.plan` を読まない（今と同じ）。輪は無い。判断のモジュール（`foundation_decide`・`run_decide`）は `ndf.adapters` と入出力を読まない（`structure.decide_forbidden_imports`）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/contracts/epic/foundation.schema.json` | 新設 | 土台の印・節の名前と必須か・表の列・照合と確かめ方の種類を持つ唯一の正本 | 無し |
| `plugins/ndf-next/contracts/limits.json` | 変更 | 完了条件の判定の間隔（`lanes.epic-recheck`）と、照合と確かめ方のコマンドの上限（`epic.command`）の 2 行を足す | 無し |
| `plugins/ndf-next/contracts/workflow/context.schema.json` | 変更 | 任意の鍵 `scale`（`value`・`reason`）を足す | 無し |
| `plugins/ndf-next/contracts/workflow/parallel-plan.schema.json` | 変更 | 任意の欄 `epic`（`name`・`foundation`・`done`）を足す | 無し |
| `plugins/ndf-next/contracts/workflow/progress.json` | 変更 | 計画の事象 `done_checked`・`foundation_noticed` とレーンの事象 `notice_read` を足す | 無し |
| `plugins/ndf-next/docs/contracts/commands.md` | 変更 | 副命令の一覧の生成物。`ndf check --write-spec` で描き直す | `plugins/ndf-next/src/ndf/cli.py` |
| `plugins/ndf-next/skills/design/SKILL.md` | 変更 | 触る領域の表に土台の参照の 1 行を足す | `plugins/ndf-next/skills/design/references/epic-foundation.md` |
| `plugins/ndf-next/skills/design/references/epic-foundation.md` | 新設 | 土台の書き方・規模の目安・#1142 の例。節の表は契約から生成する | `plugins/ndf-next/contracts/epic/foundation.schema.json` |
| `plugins/ndf-next/skills/development-workflow/SKILL.md` | 変更 | 規模の判定の 5 行を足す | `plugins/ndf-next/skills/design/references/epic-foundation.md` |
| `plugins/ndf-next/skills/issue-plan-strategy/references/parallel-plan.md` | 変更 | 計画の宣言の `epic` の欄と、起動の前の検査の指摘を書く | `plugins/ndf-next/contracts/workflow/parallel-plan.schema.json` |
| `plugins/ndf-next/src/ndf/cli.py` | 変更 | 副命令の表に `epic` を足す | `plugins/ndf-next/src/ndf/progress/epic/cli.py` |
| `plugins/ndf-next/src/ndf/adapters/markdown.py` | 変更 | 最上位の表を、行の番号つきで返す関数を足す | markdown-it-py |
| `plugins/ndf-next/src/ndf/progress/epic/__init__.py` | 新設 | パッケージの説明と `TOOL` の名前 | `ndf.cli`（`tool_of`） |
| `plugins/ndf-next/src/ndf/progress/epic/foundation_decide.py` | 新設 | 土台の値の型と、解析済みの見出しと表から土台の値を作る・形を判定する純粋な処理 | 無し（標準ライブラリだけ） |
| `plugins/ndf-next/src/ndf/progress/epic/foundation.py` | 新設 | 土台の文書をファイル・ベースの版・blob から読み、契約を読む入出力 | `ndf.adapters.markdown`、`ndf.adapters.git`、`ndf.core.route`、`ndf.contracts` |
| `plugins/ndf-next/src/ndf/progress/epic/verify.py` | 新設 | 契約の行の照合と、完了条件の確かめ方の実行 | `foundation_decide`、`ndf.adapters.github`、`ndf.contracts`、jsonschema |
| `plugins/ndf-next/src/ndf/progress/epic/cli.py` | 新設 | 副命令 `ndf epic init`・`check` の引数・結果 JSON・終了コード | `foundation`、`verify`、`ndf.contracts.result` |
| `plugins/ndf-next/src/ndf/progress/plan/__init__.py` | 変更 | 計画の作成と起動に土台の検査と起動の時点の版を足し、完了と状態に完了条件を足す | `ndf.progress.epic.foundation`、`ndf.progress.lane` |
| `plugins/ndf-next/src/ndf/progress/plan/cli.py` | 変更 | `init`・`start` の前に土台の検査を打ち、`row start`・`resumed` の出力に知らせを足す | `plan`、`ndf.progress.lane` |
| `plugins/ndf-next/src/ndf/progress/plan/epic.py` | 新設 | 耐久ワークフローのエピックの手（土台の比べ・知らせ・完了条件の判定・完了条件待ちの依頼の開閉） | `ndf.progress.epic`、`ndf.progress.lane`、`ndf.core.durable` |
| `plugins/ndf-next/src/ndf/progress/plan/run.py` | 変更 | エピックの手を打ち、スナップショットにエピックの有無と行の終わりを載せる | `plan_epic`、`run_decide` |
| `plugins/ndf-next/src/ndf/progress/plan/run_decide.py` | 変更 | エピックの手を打つ時点を決める | 無し（判断のモジュール） |
| `plugins/ndf-next/src/ndf/progress/lane/__init__.py` | 変更 | レーン宛ての未読の知らせを読み、読んだ印を書く | `fold`、`rules` |
| `plugins/ndf-next/src/ndf/progress/lane/fold.py` | 変更 | 計画の事象 3 つ（`plan_started` の版・`done_checked`・`foundation_noticed`）とレーンの `notice_read` を畳み込む | `types` |
| `plugins/ndf-next/src/ndf/progress/lane/rules.py` | 変更 | 完了の判定に完了条件を足し、知らせの宛先を選ぶ | `types` |
| `plugins/ndf-next/src/ndf/progress/lane/types.py` | 変更 | 計画のスナップショットにエピックの欄・判定・知らせを足す | 無し |
| `plugins/ndf-next/src/ndf/progress/command.py` | 変更 | `ndf progress new` に `--scale`・`--scale-reason` を足し、最初のスプリントの組み合わせを断る | `ndf.contracts` |
| `plugins/ndf-next/src/ndf/progress/run.py` | 変更 | 課題の `## 進行` に `規模:` の行を出し、`next`・`wait` の結果に知らせを載せる | `ndf.progress.lane` |
| `.github/workflows/ndf-next-checks.yml` | 変更 | 印を持つ土台があれば `bin/ndf epic check` を打つ手順を足す | `plugins/ndf-next/src/ndf/progress/epic/cli.py` |
| `docs/glossary/glossary.json` | 変更 | 設計 PR で反映済み。実装では変えない | 無し |
| `docs/glossary.md` | 変更 | 同上（`glossary.py render` の生成物） | `docs/glossary/glossary.json` |
| `plugins/ndf-next/tests/adapters/test_markdown.py` | 変更 | 表の読み出しのテストを足す | `markdown` |
| `plugins/ndf-next/tests/progress/test_epic_foundation.py` | 新設 | 土台の解析と形の判定のテスト | `foundation_decide`、`foundation` |
| `plugins/ndf-next/tests/progress/test_epic_verify.py` | 新設 | 照合と確かめ方のテスト | `verify` |
| `plugins/ndf-next/tests/progress/test_epic_cli.py` | 新設 | `ndf epic init`・`check` の結果 JSON と終了コードのテスト | `epic_cli` |
| `plugins/ndf-next/tests/progress/test_plan_epic.py` | 新設 | 起動の前の検査・完了条件・知らせのテスト（受け入れ条件 2・4〜6・8） | `plan`、`plan_epic`、`lane` |
| `plugins/ndf-next/tests/progress/test_command.py` | 変更 | `--scale` の記録と断りのテスト | `command` |
| `plugins/ndf-next/tests/progress/test_run.py` | 変更 | `規模:` の行と `next`・`wait` の知らせのテスト | `progress_run` |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `adapters/markdown.py` | モジュール | 新設: `Table`（型）・`tables` |
| `progress/epic/foundation_decide.py` | モジュール | 新設: `Foundation`・`Section`・`ContractRow`・`DoneRow`・`Finding`（型）・`parse`・`shape_errors`・`unknown_done`・`changed_sections`・`places_of`・`template`・`parse_verify`・`parse_check` |
| `progress/epic/foundation.py` | モジュール | 新設: `FoundationError`（例外）・`spec`・`read_text`・`read_file`・`base_ref`・`fetch_base`・`read_at`・`read_blob`・`marked_files` |
| `progress/epic/verify.py` | モジュール | 新設: `DoneVerdict`（型）・`verify_rows`・`verify_one`・`done_verdicts`・`run_command` |
| `progress/epic/cli.py` | モジュール | 新設: `parser`・`main`・`_cmd_init`・`_cmd_check` |
| `cli.py` | モジュール | 変更: 表 `COMMANDS` に 1 行（関数は変えない） |
| `progress/plan/__init__.py` | モジュール | 新設: `epic_gate`・`epic_items`。変更: `plan_payload`・`create_plan`・`start_plan`・`complete_plan`・`status_of`・`_plan_metrics` |
| `progress/plan/cli.py` | モジュール | 変更: `_dispatch`・`_cmd_create`・`_row_start`・`_row_resumed` |
| `progress/plan/epic.py` | モジュール | 新設: `TickInput`（型）・`tick`・`gather`・`write_tick` |
| `progress/plan/run.py` | モジュール | 変更: `_perform`・`snapshot_of`・`spec_of`・`policy` |
| `progress/plan/run_decide.py` | モジュール | 新設: `EpicTick`（手）・`_epic_due`。変更: `Snapshot`・`RunState`・`Policy`・`snapshot`・`_after_observe`・`decide` |
| `progress/lane/__init__.py` | モジュール | 新設: `take_notices`・`sprint_notices` |
| `progress/lane/fold.py` | モジュール | 新設: `last_notice_read`。変更: `_fold_plan_event` |
| `progress/lane/rules.py` | モジュール | 新設: `rows_done`・`done_remaining`・`notice_targets`。変更: `plan_complete` |
| `progress/lane/types.py` | モジュール | 新設: `EpicDecl`・`Notice`（型）・定数 `DONE_WAIT`。変更: `PlanView`（欄を 4 つ足す） |
| `progress/command.py` | モジュール | 変更: `_prepare_new`。`parser` は引数を 2 つ足すだけ |
| `progress/run.py` | モジュール | 変更: `issue_section`・`_cmd_next`・`_cmd_wait` |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `markdown.tables` | `adapters/markdown.py` | 新設 | `tables(text: str) -> list[Table]` | 最上位の GFM の表を、見出しの行・本文の行・各行の行番号つきで返す | 無し | 引用とリストの中の表は返さない。セルは前後の空白を除いた文字列 | 無し |
| `foundation_decide.parse` | `progress/epic/foundation_decide.py` | 新設 | `parse(path: str, lines: list[str], headings: Sequence[Heading], tables: Sequence[Table], kinds: Sequence[str], spec: Mapping[str, Any]) -> Foundation` | 解析済みの見出し・表・行の種類から、節・契約の行・完了条件の行を持つ土台の値を作る | `kinds` の長さが `lines` と同じ | 節は `##` の見出しごとに 1 つ。節の本文の行は HTML のコメントと空行を除いた行。契約の行と完了条件の行は契約が表を持つと定める節からだけ作る | 無し |
| `foundation_decide.shape_errors` | `progress/epic/foundation_decide.py` | 新設 | `shape_errors(f: Foundation, spec: Mapping[str, Any]) -> list[Finding]` | 必須の節の有無と空、表の列と各行の形を判定する | `f` は `parse` の結果 | 指摘は `empty-section`（無い節も含む）か `shape`。並びは契約の節の順、節の中は行の順 | 無し |
| `foundation_decide.unknown_done` | `progress/epic/foundation_decide.py` | 新設 | `unknown_done(f: Foundation, ids: Sequence[str]) -> list[Finding]` | 計画の完了条件の番号のうち、土台の完了条件の表に無いものを指摘にする | 無し | 指摘は `unknown-done`。番号の並びは `ids` の順 | 無し |
| `foundation_decide.changed_sections` | `progress/epic/foundation_decide.py` | 新設 | `changed_sections(old: Foundation, new: Foundation) -> list[str]` | 2 つの版で内容の違う節の名前を返す | 無し | 片方にだけある節も違う節に含む。並びは `new` の節の順、`old` にだけある節は後ろ | 無し |
| `foundation_decide.places_of` | `progress/epic/foundation_decide.py` | 新設 | `places_of(old: Foundation, new: Foundation, names: Sequence[str]) -> list[str]` | 違う節の触る場所を集める | `names` は `changed_sections` の結果 | 表の節は 2 つの版で違う行の正本と照合のパス。表の無い節は `触る場所:` の行。どちらも無い節は `**`。重複を除き、並びは出た順 | 無し |
| `foundation_decide.template` | `progress/epic/foundation_decide.py` | 新設 | `template(name: str, spec: Mapping[str, Any]) -> str` | 印・見出し・節ごとの手引きのコメント・表の見出しの行を持つテンプレートの文を作る | 無し | 返した文を `parse` して `shape_errors` に掛けると、必須の節がすべて `empty-section` になる | 無し |
| `foundation_decide.parse_verify` | `progress/epic/foundation_decide.py` | 新設 | `parse_verify(cell: str) -> Verify \| None` | 契約の行の照合の欄を `exists`・`schema`・`generated`・`—` のどれかに読む | 無し | 4 つのどれでもなければ None。`—` は種類 `none` | 無し |
| `foundation_decide.parse_check` | `progress/epic/foundation_decide.py` | 新設 | `parse_check(cell: str) -> Check \| None` | 完了条件の確かめ方の欄を `issues`（番号の並び）か `command`（コマンドの文）に読む | 無し | どちらでもない・番号が 1 つも無い・コマンドが空なら None | 無し |
| `foundation.spec` | `progress/epic/foundation.py` | 新設 | `spec() -> dict[str, Any]` | 土台の契約を読んで返す | 無し | 返す値は契約の形の検査を通っている | 契約のファイルを読む |
| `foundation.read_text` | `progress/epic/foundation.py` | 新設 | `read_text(text: str, path: str) -> Foundation` | 文から見出し・表・行の種類を作り、`foundation_decide.parse` へ渡す | 無し | `parse` の事後条件と同じ | 無し |
| `foundation.read_file` | `progress/epic/foundation.py` | 新設 | `read_file(root: Path, path: str) -> Foundation` | 作業ツリーの土台を読む（`ndf epic check` が使う） | `path` は `root` からの相対 | 読めなければ `FoundationError` | ファイルを読む |
| `foundation.base_ref` | `progress/epic/foundation.py` | 新設 | `base_ref(root: Path) -> str` | ベースの版を指す参照 `origin/<ベースブランチ>` を返す | `root` は git のリポジトリの根 | ベースブランチは `ndf.core.route.default_base` の結果。決まらなければ `FoundationError` | git を読む（`symbolic-ref`） |
| `foundation.fetch_base` | `progress/epic/foundation.py` | 新設 | `fetch_base(root: Path, ref: str) -> None` | ベースブランチを origin から取り込む | `ref` は `base_ref` の結果 | 成功すれば `ref` が origin の最新を指す | ネットワーク（`git fetch origin <ブランチ>`） |
| `foundation.read_at` | `progress/epic/foundation.py` | 新設 | `read_at(root: Path, ref: str, path: str) -> tuple[Foundation, str] \| None` | ベースの版の土台と、その blob の識別子を返す | 無し | `ref` にファイルが無ければ None。識別子は `git rev-parse <ref>:<path>` の値 | git を読む（`rev-parse`・`cat-file`） |
| `foundation.read_blob` | `progress/epic/foundation.py` | 新設 | `read_blob(root: Path, blob: str) -> Foundation` | blob の識別子から過去の版の土台を読む（知らせの比べの元） | 無し | 読めなければ `FoundationError` | git を読む（`cat-file`） |
| `foundation.marked_files` | `progress/epic/foundation.py` | 新設 | `marked_files(root: Path) -> list[str]` | 追跡している Markdown のうち、先頭の行が印のものを返す | 無し | パスは `root` からの相対で、辞書順 | git を読む（`ls-files`）・ファイルを読む |
| `verify.verify_rows` | `progress/epic/verify.py` | 新設 | `verify_rows(root: Path, f: Foundation, timeout: float) -> list[Finding]` | 照合の指定を持つ契約の行をすべて照合し、食い違いを指摘にする | `f` に `shape` の指摘が無い | 指摘は `contract-mismatch`。種類 `none` の行は照合しない | ファイルを読む・外部コマンド（`generated:`） |
| `verify.verify_one` | `progress/epic/verify.py` | 新設 | `verify_one(root: Path, row: ContractRow, timeout: float) -> str \| None` | 契約の行 1 つを照合し、食い違いの文を返す | `row.verify` が None でない | 食い違いが無ければ None | ファイルを読む・外部コマンド（`generated:`） |
| `verify.done_verdicts` | `progress/epic/verify.py` | 新設 | `done_verdicts(root: Path, rows: Sequence[DoneRow], states: Callable[[Sequence[int]], dict[int, str]], timeout: float) -> list[DoneVerdict]` | 完了条件の行ごとに確かめ方を実行し、満たしたかを返す | `rows` は計画の `done` の番号に絞ってある | 1 行に 1 つの判定。課題の状態はまとめて 1 回だけ読む | 外部コマンド（`command:`）・ネットワーク（`states` が GitHub を読む） |
| `verify.run_command` | `progress/epic/verify.py` | 新設 | `run_command(root: Path, text: str, timeout: float) -> tuple[int, str]` | シェルを通さずにコマンドを `root` で実行し、終了コードと標準出力を返す | `text` は `shlex.split` で割れる | 上限に当たれば終了コード `-1` と上限の文 | 外部コマンド |
| `epic_cli.main` | `progress/epic/cli.py` | 新設 | `main(argv: list[str]) -> int` | 引数を読み、副命令を流し、結果 JSON を最後の 1 行に出して終了コードを返す | 無し | 終了コードは結果の契約の `cause.kind` から引く（0・1・2） | 標準出力 |
| `epic_cli._cmd_init` | `progress/epic/cli.py` | 新設 | `_cmd_init(a: Namespace, root: Path) -> dict[str, Any]` | テンプレートを書く | 無し | ファイルがあれば書かずに `refused`。書いたらパスを `items` に返す | ファイルを書く（親のディレクトリを作る） |
| `epic_cli._cmd_check` | `progress/epic/cli.py` | 新設 | `_cmd_check(a: Namespace, root: Path) -> dict[str, Any]` | 渡した土台か印を持つ土台のすべてに形の検査と、`--shape-only` でなければ照合を当てる | 無し | 指摘が無ければ `ok`、あれば `check_failed`。印を持つ土台が 0 本なら `ok` で `metrics.files` が 0 | ファイルを読む・外部コマンド |
| `plan.epic_gate` | `progress/plan/__init__.py` | 新設 | `epic_gate(root: Path, doc: Mapping[str, Any]) -> tuple[list[Finding], str \| None]` | 計画の宣言に `epic` があれば、ベースの版の土台に形の検査と番号の照らし合わせを当てる | `doc` は計画の宣言のスキーマを通っている | `epic` が無ければ `([], None)`。指摘があれば識別子は None | git を読む・ネットワーク（`fetch_base`） |
| `plan.plan_payload` | `progress/plan/__init__.py` | 変更 | `plan_payload(doc: dict[str, Any]) -> dict[str, Any]` | 計画の行に `epic` の欄を足す | 無し | `epic` が無い宣言の値は今と同じ | 無し |
| `plan.create_plan` | `progress/plan/__init__.py` | 変更 | `create_plan(conn, repo: str, doc: dict[str, Any], start: bool, foundation_rev: str \| None = None) -> str` | 起動するときの `plan_started` に起動の時点の版を書く | `epic` があれば `epic_gate` が指摘 0 件で通っている | `foundation_rev` が None なら今と同じ事象 | 進行表の DB に事象を書く |
| `plan.start_plan` | `progress/plan/__init__.py` | 変更 | `start_plan(conn, pid: str, foundation_rev: str \| None = None) -> None` | 止めた計画の起動にも版を書く | 同上 | 比べる元は最初の `plan_started` の版のまま（`fold` が最初の値だけを持つ） | 進行表の DB に事象を書く |
| `plan.complete_plan` | `progress/plan/__init__.py` | 変更 | `complete_plan(conn, pid: str) -> bool` | 完了条件待ちの依頼が開いていれば、同じトランザクションで閉じてから計画を完了にする | 無し | 完了にしたら真。`rules.plan_complete` が偽なら何も書かない | 進行表の DB に事象を書く |
| `plan.epic_items` | `progress/plan/__init__.py` | 新設 | `epic_items(view: PlanView) -> tuple[list[dict[str, Any]], dict[str, int]]` | 完了条件ごとの行と、満たした件数 / 全件数を作る | 無し | `epic` が無ければ `([], {})`。判定の記録の無い番号は `met: null` | 無し |
| `plan.status_of` | `progress/plan/__init__.py` | 変更 | 変えない | `items` に完了条件の行、`metrics.epic_done` に件数を足す | 無し | `epic` の無い計画の結果は今と同じ | 無し |
| `plan_cli._dispatch` | `progress/plan/cli.py` | 変更 | 変えない | `init`・`start` で、トランザクションを開く前に `epic_gate` を打ち、指摘があれば止める | 無し | 指摘があれば進行表を 1 つも書かず、`check_failed` の結果を返す | git を読む・ネットワーク |
| `plan_cli._row_start` | `progress/plan/cli.py` | 変更 | 変えない | 行を始めた同じトランザクションで、レーンの未読の知らせを結果の `notices` に載せる | 無し | 載せたら読んだ印が書かれている | 進行表の DB に事象を書く |
| `plan_cli._row_resumed` | `progress/plan/cli.py` | 変更 | 変えない | 同上（スプリントの ID からレーンを引く） | 無し | 同上 | 進行表の DB に事象を書く |
| `plan_epic.tick` | `progress/plan/epic.py` | 新設 | `tick(spec: dict[str, Any], judge: bool) -> dict[str, Any]` | 耐久ステップ。ベースの版を読み、知らせと、`judge` なら完了条件の判定を書く | 計画が `epic` を持つ | 戻り値は `{"noticed": 件数, "judged": 真偽, "remaining": [番号], "error": 文 \| None}` | git・ネットワーク・外部コマンド・進行表の DB |
| `plan_epic.gather` | `progress/plan/epic.py` | 新設 | `gather(root: Path, view: PlanView, judge: bool, timeout: float) -> TickInput` | DB の外で、ベースの版・比べる元の版・判定を読む | 無し | 読めなかったものは `TickInput.error` に理由を持ち、その部分は空 | git・ネットワーク・外部コマンド |
| `plan_epic.write_tick` | `progress/plan/epic.py` | 新設 | `write_tick(conn, plan_id: str, got: TickInput) -> dict[str, Any]` | 読み直した計画に、変わった判定・知らせ・依頼の開閉を書く | 無し | 計画が実行中でなければ何も書かない。同じ入力で 2 回呼んでも 2 回目は何も書かない | 進行表の DB に事象を書く |
| `plan_run._perform` | `progress/plan/run.py` | 変更 | 変えない | 手 `EpicTick` を `plan_epic.tick` へ渡す | 無し | 結果は `Done(act, 戻り値)` | 耐久ステップを打つ |
| `plan_run.snapshot_of` | `progress/plan/run.py` | 変更 | 変えない | スナップショットに `epic`（欄の有無）と `rows_done`（全行が終わったか）を足す | 無し | `epic` の無い計画は `epic: false` | 無し |
| `run_decide._epic_due` | `progress/plan/run_decide.py` | 新設 | `_epic_due(state: RunState, snap: Snapshot, policy: Policy) -> EpicTick \| None` | エピックの手を打つ時点かを決める | 無し | 打つなら `judge` の真偽を持つ手、打たなければ None | 無し |
| `run_decide._after_observe` | `progress/plan/run_decide.py` | 変更 | 変えない | 完了・知らせ・起動の判断の前に `_epic_due` を当てる | 無し | `epic: false` のスナップショットでは今と同じ手を返す | 無し |
| `run_decide.decide` | `progress/plan/run_decide.py` | 変更 | 変えない | `Sleep` の結果で `epic_slept` を足し、`EpicTick(judge=True)` の結果で 0 に戻す | 無し | ほかの手の扱いは今と同じ | 無し |
| `lane.take_notices` | `progress/lane/__init__.py` | 新設 | `take_notices(conn, plan_id: str, lane_name: str, actor: str) -> list[dict[str, Any]]` | レーン宛ての未読の知らせを返し、読んだ印を書く | 無し | 返した知らせが 1 件以上なら、レーンの進行表の最後の事象が `notice_read`（最後の知らせの番号） | 進行表の DB に事象を書く |
| `lane.sprint_notices` | `progress/lane/__init__.py` | 新設 | `sprint_notices(conn, sprint_id: str, actor: str) -> list[dict[str, Any]]` | スプリントを持つレーンの行が実行中なら `take_notices` を打つ | 無し | レーンの外か、行が実行中でなければ空で、何も書かない | 進行表の DB に事象を書く |
| `fold.last_notice_read` | `progress/lane/fold.py` | 新設 | `last_notice_read(events: Iterable[Event]) -> int` | レーンの進行表の最後の `notice_read` の番号を返す | 無し | 無ければ 0 | 無し |
| `fold._fold_plan_event` | `progress/lane/fold.py` | 変更 | 変えない | `created` の `epic`、最初の `plan_started` の `foundation_rev`、`done_checked`（番号ごとに最後）、`foundation_noticed`（並び）をスナップショットに畳む | 無し | 欄の無い事象の扱いは今と同じ | 無し |
| `rules.rows_done` | `progress/lane/rules.py` | 新設 | `rows_done(view: PlanView, lanes: Lanes) -> bool` | 全レーンの行が完了か中止かを返す | 無し | 今の `plan_complete` の前半と同じ判定 | 無し |
| `rules.done_remaining` | `progress/lane/rules.py` | 新設 | `done_remaining(view: PlanView) -> list[str]` | 計画の完了条件の番号のうち、最後の判定が満たしていないもの（未判定を含む）を返す | 無し | `epic` が無ければ空。並びは `epic.done` の順 | 無し |
| `rules.plan_complete` | `progress/lane/rules.py` | 変更 | 変えない | 全行の終わりと、完了条件待ちの依頼を除いた答えていない依頼が無いことに、完了条件の残りが無いことを足す | 無し | `epic` が無ければ今と同じ（I6） | 無し |
| `rules.notice_targets` | `progress/lane/rules.py` | 新設 | `notice_targets(view: PlanView, lanes: Lanes, places: Sequence[str]) -> list[str]` | 終わっていないレーンのうち、触る場所が `places` のどれかと重なるものを返す | 無し | 重なりは既存の `touch_hit`。`**` はすべてのレーンに当たる。並びは計画のレーンの順 | 無し |
| `command._prepare_new` | `progress/command.py` | 変更 | 変えない | `--scale` があれば文脈に `scale` を足し、`epic-first` の組み合わせを確かめる | 無し | `--scale` が無ければ文脈は今と同じ。`epic-first` でモードが `standard` でないか進め方が `normal` でなければ `model.Refused` | 無し |
| `progress_run.issue_section` | `progress/run.py` | 変更 | 変えない | 文脈に `scale` があれば `モード:` の次の行に `規模:` の行を出す | 無し | `scale` が無ければ今と同じ文 | 無し |
| `progress_run._cmd_next` | `progress/run.py` | 変更 | 変えない | 結果に、レーンの未読の知らせ `notices` を足す | 無し | 知らせが無ければ結果は今と同じ | 進行表の DB に事象を書く |
| `progress_run._cmd_wait` | `progress/run.py` | 変更 | 変えない | 同上 | 無し | 同上 | 進行表の DB に事象を書く |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `foundation.spec` | 契約が読めない・形が違う | ファイルの欠け・JSON の誤り | `contracts` の読み出し | 止める（`ContractError`） | `config_broken`・終了コード 2 | 後始末は無い。直して打ち直せる |
| `foundation.read_file` | 土台が読めない | パスの誤り・権限・UTF-8 でない | `Path.read_text` の `OSError`・`UnicodeDecodeError` | 呼び出し元へ返す（`FoundationError`） | `ndf epic check` は `config_broken`・終了コード 2。`summary` にパス | 後始末は無い。直して打ち直せる |
| `foundation.base_ref` | ベースブランチが決まらない | 宣言の `base_branch` も origin の既定ブランチも無い | `ndf.core.route.default_base` が None | 呼び出し元へ返す（`FoundationError`） | `ndf lanes init`・`start` は指摘 `missing-foundation`（詳細に「ベースブランチが決まらない」）・終了コード 1 | 後始末は無い。宣言を直すか `git remote set-head` の後に打ち直せる |
| `foundation.fetch_base` | 取り込めない | ネットワーク・認証 | `git fetch` の終了コード | `ndf lanes`: 止める。耐久ワークフロー: 呼び出し元へ返す | `ndf lanes init`・`start` は `config_broken`・終了コード 2。耐久ワークフローは `run.log` に 1 行 | 何も書いていない。打ち直せる。耐久ワークフローは次の間隔で打ち直す |
| `foundation.read_at` | 土台がベースの版に無い | 設計 PR がまだマージされていない・パスの誤り | `git rev-parse <ref>:<path>` の失敗 | None を返す | `ndf lanes init`・`start` は指摘 `missing-foundation`・終了コード 1 | 後始末は無い。マージの後に打ち直せる |
| `foundation.read_at` | git が読めない | リポジトリでない・壊れた参照 | `GitError` | 呼び出し元へ返す（`FoundationError`） | `config_broken`・終了コード 2 | 後始末は無い |
| `foundation.read_blob` | 比べる元の blob が無い | 記録した識別子のオブジェクトが gc された | `git cat-file` の失敗 | 呼び出し元へ返す（`FoundationError`） | 耐久ワークフローは比べる元を空の土台として扱い、すべての節を違う節にして知らせる（`run.log` に理由） | 知らせを 1 件書けば、比べる元はその `to_rev` に進む |
| `foundation.marked_files` | 追跡しているファイルを列挙できない | git でない | `GitError` | 呼び出し元へ返す | `config_broken`・終了コード 2 | 後始末は無い |
| `verify.verify_rows` | 照合の対象が無い | `schema:`・`exists:` の glob に当たるファイルが 0 件 | glob の結果 | 指摘にする | 指摘 `contract-mismatch`（詳細「照合の対象が無い: <glob>」）・終了コード 1 | 後始末は無い |
| `verify.verify_one` | スキーマが読めない | 正本のパスの誤り・JSON の誤り | jsonschema の読み出し | 指摘にする | 指摘 `contract-mismatch`（詳細に正本のパスと理由）・終了コード 1 | 後始末は無い |
| `verify.verify_one` | 生成のコマンドが失敗する・上限に当たる | コマンドの誤り・遅い生成 | `run_command` の終了コード | 指摘にする | 指摘 `contract-mismatch`（詳細に終了コードか上限の秒）・終了コード 1 | コマンドの子のプロセスを止める。打ち直せる |
| `verify.done_verdicts` | 課題の状態が読めない | GitHub の上限・ネットワーク | `states` の例外 | 呼び出し元へ返す | 耐久ワークフローはこの回の判定を書かない（`run.log` に 1 行） | 前の判定が今の値のまま残る。次の間隔で打ち直す |
| `verify.done_verdicts` | 確かめ方のコマンドが失敗する・上限に当たる | 条件がまだ成り立たない・遅いコマンド | `run_command` の終了コード | 判定の値にする | `met: false`、`detail` に終了コードか上限の秒 | 打ち直しは次の間隔 |
| `verify.run_command` | コマンドが見つからない | 環境に無いコマンド | `FileNotFoundError` | 終了コード `127` と理由の文を返す | 呼び手の指摘か判定の `detail` に出る | 後始末は無い |
| `epic_cli.main` | 使い方の誤り | 知らない引数 | argparse | 止める | 使い方の文・終了コード 2 | 後始末は無い |
| `epic_cli._cmd_init` | 同名のファイルがある | 既に土台がある | `Path.exists` | 止める | `refused`・終了コード 1。上書きしない | 書いていない |
| `epic_cli._cmd_init` | 書けない | 権限・ディスク | `OSError` | 止める | `config_broken`・終了コード 2 | 途中のファイルは残らない（一時ファイルに書いて置き換える） |
| `epic_cli._cmd_check` | 形か照合の指摘がある | 空の節・列の誤り・食い違い | `shape_errors`・`verify_rows` | 止める | `check_failed`・終了コード 1。指摘は 1 件 1 行で `items` | 後始末は無い |
| `plan.epic_gate` | 土台の検査の指摘がある | 空の節・列の誤り・土台に無い番号・ベースの版に土台が無い | `shape_errors`・`unknown_done`・`read_at` | 呼び出し元へ返す（指摘の並び） | 呼び手の `plan_cli._dispatch` が出す | 何も書いていない |
| `plan.create_plan` | 計画の決まりに当たる | 既存の決まり（レーン名の重なりなど） | 既存の `plan_errors` | 止める（`model.Refused`） | 今と同じ `refused`・終了コード 1 | トランザクションごと戻る |
| `plan.start_plan` | 始められない状態 | 既存の決まり | 既存の状態の検査 | 止める（`model.Refused`） | 今と同じ | 同上 |
| `plan.complete_plan` | 書く前に計画が動いた | 同時の書き込み（「並行と排他」） | `store.append_events` の番号の食い違い | 呼び出し元へ返す | 耐久ワークフローの既存の打ち直し | トランザクションごと戻る。次の読み直しで判定し直す |
| `plan_cli._dispatch` | 起動の前の検査の指摘がある | `epic_gate` の指摘 | `epic_gate` の戻り値 | 止める | `check_failed`・終了コード 1。指摘は `items`（`rule` は `missing-foundation`・`unknown-done`・`empty-section`・`shape`） | 進行表を 1 つも書いていない。直して打ち直せる |
| `plan_cli._dispatch` | 土台・git が読めない | `FoundationError` | `epic_gate` の例外 | 止める | `config_broken`・終了コード 2 | 同上 |
| `plan_cli._row_start` | 行を始められない | 既存の決まり | 既存の `start_row` | 止める | 今と同じ `refused`。知らせの読んだ印も書かない（同じトランザクション） | トランザクションごと戻る |
| `plan_cli._row_resumed` | 書く前にレーンの進行表が動いた | 同時の書き込み | `checked_write` の番号の食い違い | 呼び出し元へ返す | 既存の打ち直し（`retrying_transaction`） | トランザクションごと戻る |
| `plan_epic.tick` | 読み取りの一部が失敗した | `gather` の `error` | `TickInput.error` | 既定値で続ける（読めた分だけ書く） | 戻り値の `error`。`run.log` に 1 行 | 次の間隔で打ち直す |
| `plan_epic.gather` | ベースの版に土台が無くなった | 土台を消す・移す変更がマージされた | `read_at` が None | 既定値で続ける（知らせと判定を書かない） | 戻り値の `error`（「ベースの版に土台が無い」）。`status` の完了条件は前の判定のまま | 土台が戻れば次の間隔で続く |
| `plan_epic.write_tick` | 計画が実行中でない | 止めた・中止した・完了した | 読み直した計画の状態 | 何も書かずに返す | 無し | 無し |
| `plan_epic.write_tick` | 書く前に計画が動いた | 同時の書き込み | `store.append_events` の番号の食い違い | 呼び出し元へ返す | 耐久ステップの失敗として記録される | トランザクションごと戻る。冪等なので次の手で打ち直せる |
| `plan_run._perform` | エピックの手が例外で落ちた | `tick` の中の予期しない例外 | 耐久ステップ | 呼び出し元へ返す | 既存の耐久ワークフローの失敗の扱い（`ndf lanes run` の結果） | 再開すると同じ手から打ち直す |
| `lane.take_notices` | 書く前にレーンの進行表が動いた | 同時の書き込み | `checked_write` | 呼び出し元へ返す | 呼び手の既存の打ち直し | トランザクションごと戻る |
| `lane.sprint_notices` | 同上 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |
| `command._prepare_new` | 最初のスプリントの組み合わせが違う | `--scale epic-first` でモードが `standard` でない・進め方が `normal` でない | 引数の比べ | 止める（`model.Refused`） | `refused`・終了コード 1。`summary` に渡したモードと進め方 | 進行表を作っていない |
| `command._prepare_new` | `--scale-reason` だけがある | 引数の組み合わせの誤り | 引数の比べ | 止める（`definition.WorkflowDefError`） | 今の文脈の誤りと同じ出力・終了コード | 同上 |
| `progress_run._cmd_next` | 知らせの読み書きが失敗した | DB の同時の書き込み | `retrying_transaction` が尽きた | 呼び出し元へ返す | 今の DB の失敗と同じ出力 | `next` の本体は先に終わっている。知らせは未読のまま残り、次の `next`・`wait` で受ける |
| `progress_run._cmd_wait` | 同上 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |

## 処理の手順

**計画を完了にしてよいか（`rules.plan_complete`）。** 完了条件待ちの依頼を答えていない依頼に数えない（設計文書の I5）。

| 計画の `epic` | 全行が終わった | 完了条件待ちの依頼のほかに開いた依頼 | 完了条件の残り | 結果 |
| --- | --- | --- | --- | --- |
| 無し | いいえ | どれでも | — | 偽 |
| 無し | はい | ある | — | 偽 |
| 無し | はい | 無い | — | 真（今と同じ） |
| ある | いいえ | どれでも | どれでも | 偽 |
| ある | はい | ある | どれでも | 偽 |
| ある | はい | 無い | ある（未判定を含む） | 偽 |
| ある | はい | 無い | 無い | 真 |

**エピックの手を打つか（`run_decide._epic_due`）。** スナップショットの計画が実行中で、`epic` を持つときだけ当てる。

| `snap.rows_done` | `state.epic_slept` が `policy.epic_every` 以上 | 最後の手が `EpicTick` | 結果 |
| --- | --- | --- | --- |
| どれでも | どれでも | はい | None（同じ読み直しで 2 回打たない） |
| はい | どれでも | いいえ | `EpicTick(judge=True)` |
| いいえ | はい | いいえ | `EpicTick(judge=True)` |
| いいえ | いいえ | いいえ | `EpicTick(judge=False)`（土台の比べだけ。手元の参照を読み、取り込まない） |

`judge=False` の手は `fetch_base` を打たず、前に取り込んだ参照の版で比べる。取り込みと判定は `judge=True` の手だけが行い、ネットワークと GitHub の読み取りは間隔ごとに 1 回になる。

**`plan_epic.write_tick` が書くもの。** 読み直した計画の状態が実行中のときだけ書く。上から順に、同じトランザクションで書く。

| 条件 | 書く事象 |
| --- | --- |
| ベースの版の識別子が比べる元と違い、宛先が 1 本以上ある | `foundation_noticed`（`from_rev`・`to_rev`・`sections`・`places`・`lanes`） |
| ベースの版の識別子が比べる元と違い、宛先が 0 本 | `foundation_noticed`（`lanes` は空。比べる元を進めるために書く） |
| `judge` で、番号の判定（`met`・`detail`）が最後の `done_checked` と違う | 番号ごとに `done_checked` |
| 全行が終わり、ほかの依頼が無く、残りがあり、残りの並びの鍵の依頼がまだ開いたことが無い | 開いている完了条件待ちの依頼を閉じ、`request_opened`（`kind: epic_done_wait`・`remaining`・理由に番号の並び） |
| 全行が終わっていない（オーガナイザーがレーンか行を足した）で、完了条件待ちの依頼が開いている | `request_closed`（理由「レーンか行が足された」） |
| 残りが無い | 何も書かない（次の読み直しで `Complete` が依頼を閉じて完了にする） |

依頼の鍵は `epic-done:<残りの番号をカンマでつないだ文>` である。「開いたことが無い」は、計画のスナップショットの依頼（閉じたものを含む）にその鍵が無いことで判定する。オーガナイザーが閉じた鍵では開き直さない。

**照合の指定（`verify.verify_one`）。**

| 指定 | 当てる対象 | 食い違いとするとき |
| --- | --- | --- |
| `exists: <glob>` | glob に当たる追跡中のファイル | 契約の行の名前が、どのファイルにも文字どおり現れない。当たるファイルが 0 件 |
| `schema: <glob>` | glob に当たる追跡中の JSON のファイル | 1 つでも正本のスキーマに通らない。当たるファイルが 0 件 |
| `generated: <コマンド>` | 正本のファイル | コマンドの標準出力が正本の中身と 1 字でも違う。終了コードが 0 でない。上限に当たる |
| `—` | 無し | 照合しない |

**規模の記録（`command._prepare_new`）。**

| `--scale` | モード | 進め方 | 結果 |
| --- | --- | --- | --- |
| 無し | どれでも | どれでも | 今と同じ（文脈に `scale` を書かない） |
| `sprint`・`epic` | どれでも | どれでも | 文脈に `scale` を書く |
| `epic-first` | `standard` | `normal` | 文脈に `scale` を書く |
| `epic-first` | `standard` 以外 | どれでも | `model.Refused` |
| `epic-first` | `standard` | `normal` 以外 | `model.Refused` |

`--scale-reason` を省いたときの理由は `理由の記載無し` とする。`--scale` 無しで `--scale-reason` だけを渡すと誤りにする。

- 繰り返しの終わる条件: 耐久ワークフローのループは今と同じく計画の終わりで止まる。エピックの手は終わりの条件を変えず、完了の判定（`rules.plan_complete`）に残りが無いことを足すだけである。`verify.done_verdicts` と `verify.verify_rows` は渡した行の数だけ回る
- 計算量の注意: `judge=False` の手は毎周 `git rev-parse` を 1 回打つ。識別子が変わったときだけ 2 つの版を読む。`verify.done_verdicts` は課題の状態を `GitHub.nodes` で 1 回にまとめて読み（`github.graphql-batch` の件数ごと）、ループの中で GitHub を読まない（兆候の規則 `NDF201`）。`exists:` の照合は glob に当たるファイルを 1 回ずつだけ読む

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| 土台の印 | 定数 | `contracts/epic/foundation.schema.json` の `marker` | `<!-- ndf: epic-foundation -->` |
| 節の一覧（名前・必須か・表の列） | 定数 | 同じ契約の `sections` | 設計文書の「土台の文書の形」の 12 行 |
| 照合の種類 | 定数 | 同じ契約の `verify_kinds` | `exists`・`schema`・`generated`・`none` |
| 確かめ方の種類 | 定数 | 同じ契約の `check_kinds` | `issues`・`command` |
| 触る場所の行の頭 | 定数 | 同じ契約の `places_prefix` | `触る場所:` |
| 完了条件の判定の間隔 | 設定値 | `contracts/limits.json` の `lanes.epic-recheck` | `seconds: 600` |
| 照合と確かめ方のコマンドの上限 | 設定値 | `contracts/limits.json` の `epic.command` | `per_call: 300` |
| 完了条件待ちの依頼の種類 | 定数 | `progress/lane/types.py` の `DONE_WAIT` | `epic_done_wait` |
| 完了条件待ちの依頼の鍵の頭 | 定数 | `progress/lane/types.py`（`DONE_WAIT` の隣） | `epic-done:` |
| `Foundation`・`Section`・`ContractRow`・`DoneRow`・`Finding` | 型（不変の値） | `progress/epic/foundation_decide.py` | — |
| `DoneVerdict` | 型 | `progress/epic/verify.py` | — |
| `EpicDecl`・`Notice` | 型 | `progress/lane/types.py` | — |
| `PlanView` の足す欄 | 型 | `progress/lane/types.py` | `epic: EpicDecl \| None = None`・`foundation_rev: str \| None = None`・`done: dict[str, DoneVerdict] = {}`・`notices: list[Notice] = []` |
| `TickInput` | 型 | `progress/plan/epic.py` | — |
| `Snapshot` の足す欄 | 型 | `progress/plan/run_decide.py` | `epic: bool = False`・`rows_done: bool = False`（前の版の記録には無いので既定で補って読む） |
| `RunState.epic_slept` | 型の欄 | `progress/plan/run_decide.py` | `0.0` |
| `Policy.epic_every` | 型の欄 | `progress/plan/run_decide.py` | `lanes.epic-recheck` の `seconds`（`plan_run.policy` が読む） |

**節の名前と列は契約 1 か所だけが持つ。** `foundation_decide` は節の名前を定数に写さず、引数の `spec` から読む。参照（`epic-foundation.md`）の節の表は、`ndf check --write-spec` の生成の仕組みで契約から描く。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 耐久ワークフローのエピックの手と、オーガナイザーの副命令（`lane`・`row add`・`request` など） | 計画の進行表 | 既存のとおり、書く側が `lock_plan` を取り、`store.append_events` が最後の番号を確かめる。食い違えばトランザクションごと戻る | `write_tick` は最新の記録と比べて違うときだけ書くため、同じ入力で打ち直しても 2 回目は何も書かない。途中で落ちても、再開した耐久ワークフローが同じ手から打ち直す |
| 耐久ワークフローの `Complete` と、オーガナイザーの行の追加 | 計画の進行表 | `complete_plan` は同じトランザクションで計画を読み直し、`rules.plan_complete` が真のときだけ依頼を閉じて完了を書く | 偽なら何も書かない。次の読み直しで判定し直す |
| 1 つのレーンの `ndf progress next`・`wait` と `ndf lanes row resumed` | レーンの進行表 | 既存の `checked_write`（最後の番号の確かめ）と `retrying_transaction` | `notice_read` は読んだ最後の番号を持つ。同じ番号を 2 回書いても、未読の判定（最後の `notice_read` より後）は変わらない |
| 耐久ワークフローの `gather`（git の読み取りと取り込み）と、同じマシンの `ndf lanes start` の `epic_gate` | 主ディレクトリの `.git` の参照 | 取らない。`git fetch` は参照の更新を git 自身の lock で守る。lock で落ちた側は失敗として扱う | 耐久ワークフローは次の間隔で打ち直す。`ndf lanes start` は `config_broken` で止まり、打ち直せる |
| 確かめ方のコマンド（`command:`）と、主ディレクトリでの他の作業 | 主ディレクトリの作業ツリー | 取らない。コマンドは主ディレクトリで動かす | 確かめ方は読み取りだけのコマンドを書く、と参照（`epic-foundation.md`）に書く。書き込むコマンドで食い違う危険は「未確認・残リスク」に残す |

GitHub の読み取り（`states`）と DB の書き込みを同じトランザクションに入れない。`plan_epic.tick` は `gather`（DB の外の読み取り）の後に `write_tick`（DB の書き込み）を打つ。既存の `observe` が生死の材料をトランザクションの外で読む順と同じである。

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `epic_cli.main` | 結果 JSON（`items` に指摘、`metrics.files`・`metrics.findings`）。指摘は 1 件 1 行で標準エラーにも出す | 標準出力の最後の 1 行・標準エラー | コマンドの標準出力の全文（`generated:` の食い違いは最初に違う行の番号だけを出す） |
| `plan_cli._dispatch` | 起動の前の検査の指摘 | 結果 JSON の `items` | 土台の本文 |
| `plan.status_of` | 完了条件ごとの行と `metrics.epic_done` | 結果 JSON | 確かめ方のコマンドの出力 |
| `plan_epic.write_tick` | `done_checked`・`foundation_noticed`・完了条件待ちの依頼 | 計画の進行表の事象 | 確かめ方のコマンドの出力の全文（`detail` は終了コードと最後の 1 行の 200 字まで） |
| `plan_epic.tick` | 読めなかった理由（`error`） | 耐久ワークフローの `run.log` に 1 行（既存の `RUN_LOG`） | 環境変数の値・トークン |
| `lane.take_notices` | 読んだ印 | レーンの進行表の事象 `notice_read` | 無し |
| `plan_cli._row_start`・`_row_resumed`・`progress_run._cmd_next`・`_cmd_wait` | `notices`（節・触る場所・前と後の版・事象の番号） | 結果 JSON | 土台の本文 |
| `progress_run.issue_section` | `規模:` の行 | 課題の本文の `## 進行` | 無し |

**確かめ方のコマンドの出力は秘密を含みうる。** `verify.done_verdicts` と `verify.verify_one` は、`detail` に載せる文を最後の 1 行の 200 字で切り、既存の伏せ字の処理 `ndf.adapters.run_log.redact`（秘密の環境変数の値とトークンの形を伏せる）を通してから返す。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `markdown.tables` | `ndf.adapters.markdown` の `atx_headings`・`block_kinds` | `grep -nE '^def ' src/ndf/adapters/markdown.py` | 表を返す関数が無い。同じ markdown-it のトークンから作り、正規表現で `\|` を数えない（モジュールの冒頭の決まり） |
| `foundation_decide.parse` | `ndf.domains.records.glossary` の表の読み出し、NDF 10 の `scripts/lib/md.py` の `tables_in` | `grep -rn 'def tables\|tables_in' src/ndf` | `ndf.domains` は層が上で読めない。10 の `md.py` は ndf-next に無い。`markdown.tables` の結果を受けて作る |
| `foundation_decide.shape_errors` | 設計の検査（`design-survey.py` の `detail_section`） | `grep -rn 'detail_section' plugins/ndf-next/src` | ndf-next に無い。節の空の定義（HTML のコメントと空行のほか）が違うため写さない |
| `foundation_decide.unknown_done` | 無し | `grep -rn 'unknown' src/ndf/progress/lane/rules.py` | 番号の照らし合わせの部品は無い。集合の差で書く |
| `foundation_decide.changed_sections` | `difflib` | 標準ライブラリ | 使わない。節の単位で本文の行の並びを比べるだけで足りる |
| `foundation_decide.places_of` | `rules.touch_hit` | `sed -n 87,110p src/ndf/progress/lane/rules.py` | 重なりの判定は `rules.notice_targets` が `touch_hit` で行う。ここは場所を集めるだけ |
| `foundation_decide.template` | `contracts/templates/` のテンプレート | `ls contracts/templates` | 節の一覧を契約から描くため、固定のテンプレートのファイルを置かない |
| `foundation_decide.parse_verify` | 無し | `grep -rn 'generated' src/ndf/checks/spec_docs.py` | 規約の生成と照合（`spec_docs`）は ndf-next 自身の生成物だけを見る。利用者のコマンドを受ける部品は無い |
| `foundation_decide.parse_check` | 無し | 同上 | 同上 |
| `foundation.spec` | `ndf.contracts` の契約の読み出し（`read_doc` が使うもの） | `sed -n 52,64p src/ndf/progress/plan/__init__.py` | 使う |
| `foundation.read_text` | `markdown.atx_headings`・`markdown.block_kinds`・`markdown.tables` | 同上の grep | 使う |
| `foundation.read_file` | `pathlib.Path.read_text` | 標準ライブラリ | 使う |
| `foundation.base_ref` | `ndf.core.route.default_base`、`ndf.domains.worktree.repo.base_branch` | `grep -rn 'def default_base\|def base_branch' src/ndf` | `default_base` を使う。`ndf.domains.worktree` は層が上で読めない |
| `foundation.fetch_base` | `ndf.adapters.git.Git.run` | `grep -nE 'def ' src/ndf/adapters/git.py` | 使う（`run("fetch", "origin", <ブランチ>)`） |
| `foundation.read_at` | `ndf.domains.records.mvv.decl_at`（`git show <ref>:<path>`） | `sed -n 66,76p src/ndf/domains/records/mvv.py` | 同じ形を `Git.run` で書く。`mvv` は層が上で読めない |
| `foundation.read_blob` | `Git.run` | 同上 | 使う（`run("cat-file", "-p", <識別子>)`） |
| `foundation.marked_files` | `ndf.adapters.git.Git.run` | 同上 | 使う（`run("ls-files", "*.md")`） |
| `verify.verify_rows` | jsonschema（依存済み）、`ndf.contracts.schema_errors` | `grep -rn 'def schema_errors' src/ndf/contracts` | `schema:` は jsonschema を直に使う（利用者のスキーマで、NDF の契約の置き場に無い） |
| `verify.verify_one` | 同上 | 同上 | 同上 |
| `verify.done_verdicts` | `ndf.adapters.github.GitHub.nodes`（番号ごとの読み取りをまとめる）、`ndf.adapters.run_log.redact` | `grep -n 'def nodes' src/ndf/adapters/github.py`・`grep -rn 'def redact' src/ndf` | 使う（`nodes(numbers, "issue", "state")` と、`detail` の伏せ字） |
| `verify.run_command` | `ndf.process_group`、`subprocess.run` | `ls src/ndf/process_group.py` | `process_group` の子のプロセスを止める処理を使い、上限で子を残さない |
| `epic_cli.main` | `ndf.progress.command.run_main` | `sed -n 542,556p src/ndf/progress/command.py` | 使えない。`run_main` は進行表の DB を開く前提の止まりの表を持つ。`ndf check` の `main` と同じく結果の契約へ直に出す |
| `epic_cli._cmd_init` | `ndf.adapters.paths` の関数 | `grep -n 'def ' src/ndf/adapters/paths.py` | パスの一致の関数だけで、書き出しの部品は無い。一時ファイルに書いて `os.replace` で置き換える |
| `epic_cli._cmd_check` | `ndf.checks.run` の結果の組み立て | `sed -n 1,40p src/ndf/checks/README.md` | 結果の形（`items`・`metrics.findings`・`check_failed`）を同じにする。関数は共有しない（`ndf.checks` はプラグイン自身の検査） |
| `plan.epic_gate` | `foundation.read_at`・`foundation_decide.shape_errors`・`unknown_done` | この文書の新設 | 使う |
| `plan.epic_items` | `plan._plan_metrics` | `sed -n 416,431p src/ndf/progress/plan/__init__.py` | 足す先として使う。行は別の関数で作る |
| `plan_epic.tick` | `plan_run.observe`（読み取りをトランザクションの外に置く耐久ステップ） | `sed -n 226,232p src/ndf/progress/plan/run.py` | 同じ形で書く |
| `plan_epic.gather` | `foundation`・`verify` | この文書の新設 | 使う |
| `plan_epic.write_tick` | `plan.open_request`・`plan.close_request`（依頼の開閉） | `grep -n 'def open_request\|def close_request' src/ndf/progress/plan/__init__.py` | 使う。書き手は `model.WORKFLOW_ACTOR`（既存の止まりの知らせと同じ） |
| `run_decide._epic_due` | `run_decide._reportable`・`_withdrawable` | `sed -n 399,416p src/ndf/progress/plan/run_decide.py` | 同じ形の判断の関数として足す |
| `lane.take_notices` | `lane.checked_write` | `sed -n 151,170p src/ndf/progress/lane/__init__.py` | 使う |
| `lane.sprint_notices` | `lane.sprint_row` | `sed -n 273,300p src/ndf/progress/lane/__init__.py` | 使う |
| `fold.last_notice_read` | `fold.fold_lane` | `grep -n 'def ' src/ndf/progress/lane/fold.py` | `fold_lane` は行の単位で畳む。表の単位の事象を読む関数として足す |
| `rules.rows_done` | `rules.plan_complete` の前半 | `sed -n 250,253p src/ndf/progress/lane/rules.py` | 切り出して `plan_complete` から呼ぶ |
| `rules.done_remaining` | 無し | 同上 | スナップショットの `done` と `epic.done` から作る |
| `rules.notice_targets` | `rules.touch_hit`・`rules.lane_state` | `sed -n 87,110p src/ndf/progress/lane/rules.py` | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `markdown.tables` | 見出しと 2 行の表を、行番号つきで返す | 見出しだけの表は本文 0 行。引用の中の表は返さない | 区切りの行の無い `\|` の並びは表にしない |
| `foundation_decide.parse` | 12 節の土台から節・契約の行・完了条件の行を作る | コメントだけの節は本文 0 行。コードブロックの中の `##` は節にしない | 契約に無い名前の `##` の節は値に残り、`shape_errors` が指摘しない（追加の節を許す） |
| `foundation_decide.shape_errors` | すべて埋めた土台で 0 件 | 必須の節が無い・空のとき `empty-section`。該当時の節が空でも指摘しない | 列の名前が違う・`#` が `D<数>` でない・確かめ方が読めない行で `shape`（行の番号と欄の名前を持つ） |
| `foundation_decide.unknown_done` | すべてある番号で 0 件 | 空の並びで 0 件 | 無い番号ごとに `unknown-done` |
| `foundation_decide.changed_sections` | 同じ版で空 | 片方にだけある節を含む | 空白だけの違いも違いとする（本文の行を比べる） |
| `foundation_decide.places_of` | 表の節で、違う行の正本と照合のパスを返す | `触る場所:` の行を持つ表の無い節 | どちらも無い節で `**` |
| `foundation_decide.template` | 返した文の必須の節がすべて `empty-section` になる | 名前に空白を含む | 契約の節が 0 件なら印と H1 だけ |
| `foundation_decide.parse_verify` | 4 種類を読む | `—` | 知らない頭で None |
| `foundation_decide.parse_check` | `issues: #1 #2` と `command: make test` を読む | 番号 1 つ | 番号 0 個・空のコマンド・知らない頭で None |
| `foundation.spec` | 契約を読む | — | 壊れた契約で `ContractError` |
| `foundation.read_text` | `parse` と同じ値 | 空の文 | — |
| `foundation.read_file` | 読む | — | 無いパスで `FoundationError` |
| `foundation.base_ref` | 宣言の `base_branch` を使う | 宣言が無く origin の既定ブランチを使う | どちらも無いとき `FoundationError` |
| `foundation.fetch_base` | 一時のリポジトリの origin から取り込む | — | origin が無いとき `FoundationError` |
| `foundation.read_at` | ベースの版の土台と識別子を返す | 作業ツリーにだけある土台で None（I9） | 参照が無いとき `FoundationError` |
| `foundation.read_blob` | 識別子から読む | — | 無い識別子で `FoundationError` |
| `foundation.marked_files` | 印を持つ追跡中のファイルだけを返す | 2 行目の印は数えない | git でないとき `FoundationError` |
| `verify.verify_rows` | 食い違いの無い行で 0 件 | `—` の行は照合しない | 3 種類それぞれの食い違いで `contract-mismatch`（受け入れ条件 3） |
| `verify.verify_one` | `schema:` が通る | glob が 0 件で食い違い | 読めないスキーマ・失敗する生成のコマンド・上限 |
| `verify.done_verdicts` | 閉じた課題と終了コード 0 で `met: true` | 課題の一部だけが閉じている | `states` の例外を呼び手へ返す。コマンドの失敗は `met: false` |
| `verify.run_command` | 終了コードと出力を返す | 上限ちょうど | 無いコマンドで `127`。上限で `-1` と子が残らないこと |
| `epic_cli.main` | `check` が 0 件で終了コード 0 | 印を持つ土台が 0 本で 0 | 使い方の誤りで 2 |
| `epic_cli._cmd_init` | テンプレートを書く | 親のディレクトリが無い | 同名のファイルで `refused`・終了コード 1 |
| `epic_cli._cmd_check` | `--shape-only` で照合しない | 引数のパスと印の両方 | 指摘で `check_failed`・1。読めないファイルで `config_broken`・2 |
| `plan.epic_gate` | `epic` の無い宣言で `([], None)`（受け入れ条件 8） | すべて埋めた土台で識別子を返す | 空の節・無い番号・ベースの版に無い土台で指摘（受け入れ条件 2・I4・I9） |
| `plan.plan_payload` | `epic` を写す | `epic` が無い | — |
| `plan.create_plan` | `plan_started` に `foundation_rev` | `foundation_rev` が None | 既存の決まりの `Refused` |
| `plan.start_plan` | 止めた計画の起動で版を書き、比べる元は最初の版のまま | — | 既存の決まりの `Refused` |
| `plan.complete_plan` | 完了条件待ちの依頼を閉じてから完了を書く | 依頼が無い | 残りがあれば何も書かない |
| `plan.epic_items` | 番号ごとの行と `{met, total}` | 判定の記録の無い番号で `met: null`（受け入れ条件 5） | — |
| `plan.status_of` | `epic` の行が `items` に載る | `epic` の無い計画で今と同じ | — |
| `plan_cli._dispatch` | 指摘 0 件で今と同じ | — | 指摘で進行表を 1 つも書かず `check_failed` |
| `plan_cli._row_start` | 未読の知らせを `notices` に載せ印を書く | 知らせが無ければ印を書かない | 始められない行で印も書かない |
| `plan_cli._row_resumed` | 同上（I8） | 同上 | — |
| `plan_epic.tick` | 版の違いで知らせを書き、`judge` で判定を書く | `judge=False` で取り込まない | `gather` の失敗で読めた分だけ書く |
| `plan_epic.gather` | 2 つの版と判定を読む | 比べる元が無い（最初の起動） | 比べる元の blob が無いとき空の土台として比べる |
| `plan_epic.write_tick` | 依頼の開閉の表のとおり書く（受け入れ条件 4・6） | 同じ入力の 2 回目は何も書かない | 実行中でない計画で何も書かない。閉じた鍵で開き直さない |
| `plan_run._perform` | `EpicTick` を `tick` へ渡す | — | — |
| `plan_run.snapshot_of` | `epic`・`rows_done` を載せる | `epic` の無い計画 | — |
| `run_decide._epic_due` | 判定表の 4 行 | `epic_slept` がちょうど間隔 | `epic: false` で None |
| `run_decide._after_observe` | `epic` のスナップショットで手を挟む | `epic: false` で今と同じ手 | — |
| `run_decide.decide` | `Sleep` で `epic_slept` が増え、`judge` の後に 0 | — | — |
| `lane.take_notices` | 宛先のレーンだけが受ける（I7） | 2 回目は空 | — |
| `lane.sprint_notices` | 実行中の行のスプリントで受ける | レーンの外のスプリントで空 | — |
| `fold.last_notice_read` | 最後の番号 | 無ければ 0 | — |
| `fold._fold_plan_event` | 新しい事象を畳む | 前の版の記録（欄が無い）を読める | — |
| `rules.rows_done` | 全行が完了か中止で真 | 行が 0 | — |
| `rules.done_remaining` | 満たしていない番号 | 未判定の番号を含む | `epic` が無ければ空 |
| `rules.plan_complete` | 判定表の 7 行 | 完了条件待ちの依頼だけが開いている | — |
| `rules.notice_targets` | 重なるレーンだけ | `**` で終わっていないレーンすべて | 終わったレーンは選ばない |
| `command._prepare_new` | `scale` を文脈に書く（受け入れ条件 7） | `--scale` 無しで今と同じ | 判定表の断る 2 行。`--scale-reason` だけで誤り |
| `progress_run.issue_section` | `規模:` の行が `モード:` の次 | `scale` が無ければ今と同じ | — |
| `progress_run._cmd_next` | 実行中のレーンの `next` に `notices` が載り、次の `next` には出ない（受け入れ条件 6） | レーンの外で載らない | — |
| `progress_run._cmd_wait` | 同上 | 同上 | — |
