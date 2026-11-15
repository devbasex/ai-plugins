# #3600 の詳細設計: エピックのアーキテクチャ設計書・起動前チェック・完了条件の項目・変更通知

設計文書 [issue-3600-design.md](issue-3600-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。書き方は NDF の `design` Skill の `references/detailed-design.md` にある。

対象は 11.0 の構造（2026-11-15 の `plugins/ndf-next/`）である。今の `plugins/ndf/` には書かない。11.0（#1142）が閉じるまでに、配置先や関数の名前が変わりうる。11.x の着手の前に「ファイル配置」の表のパスがあるかを確かめる（設計文書の「未確認のまま残ること」）。

**用語と表記。**

| この文書の書き方 | 指すもの |
| --- | --- |
| アーキテクチャ設計書 | エピックのアーキテクチャ設計書（設計文書の「用語」）。アーキテクチャ設計書 1 本を指す |
| 計画 | 並列の計画。実装計画とは呼び分ける |
| ベースブランチのリビジョン | ベースブランチを `git fetch` で取得し直した後の、リモート追跡ブランチ `origin/<ベースブランチ>` の先端のコミットにあるアーキテクチャ設計書。ベースブランチは `.ndf/worktree.json` の `base_branch`、無ければ origin の既定ブランチ |
| 判定のコミット | Durable Workflow の 1 回のエピックのアクションが、取得し直した直後に読んだ先端のコミット。同じ回のリビジョンの比較・完了条件の判定・`command:` の実行は、このコミット 1 つを読む（設計文書の「ベースブランチの取得と判定のリビジョン」） |
| 判定の作業ディレクトリ | 判定のコミットを `git worktree add --detach` で切り出した作業ディレクトリ。`command:` の確認方法だけがここで動き、判定の後に消す |
| 完了条件待ちのリクエスト | 計画のワークフロー状態のリクエストのうち、中身の `kind` が `epic_done_wait` のもの（設計文書の「計画の終了判定」） |
| エピックのリクエスト | Durable Workflow が開いて閉じるリクエスト 4 種類。リクエストのキーは既存の `plan.open_request` が振る `request-<連番>` で、種類と重複の判定は中身のフィールド `epic_key` が持つ。完了条件待ち（`epic_key` が `epic-done:<番号の並び>`）・取得の失敗（`fetch-failed`）・アーキテクチャ設計書に無い番号（`unknown-done:<番号の並び>`）・消せない判定の作業ディレクトリ（`workdir-left`） |
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

**依存の向き。** アーキテクチャ設計書のパーサーは層 `ndf.progress` に置く。並列の計画（`ndf.progress.plan`）がアーキテクチャ設計書を読むためである。層の規則（`checks/rules.json` の `structure.layers`）は、`ndf.progress` から `ndf.domains` を読むことを許さない。`ndf.progress.epic` は `ndf.contracts`・`ndf.adapters`・`ndf.core.route`・`ndf.core.converge.place` だけを読む。`ndf.progress.plan`・`ndf.progress.lane` は読まない。`ndf.progress.plan` と `ndf.progress.lane` が `ndf.progress.epic` を読む。`ndf.progress.lane` は `ndf.progress.plan` を読まない（今と同じ）。輪は無い。判断のモジュール（`foundation_decide`・`run_decide`）は `ndf.adapters` と入出力を読まない（`structure.decide_forbidden_imports`）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/contracts/epic/foundation.schema.json` | 新設 | アーキテクチャ設計書のマーカー・セクションの名前と必須か・表の列・検証と確認方法の種類を持つ唯一の正本 | 無し |
| `plugins/ndf-next/contracts/limits.json` | 変更 | エピックのアクションの間隔（`lanes.epic-recheck`）・ベースブランチの取得が続けて失敗してよい回数（`lanes.epic-fetch-failures`）・検証と確認方法のコマンドの上限（`epic.command`）の 3 行を足す | 無し |
| `plugins/ndf-next/contracts/workflow/context.schema.json` | 変更 | 任意のキー `scale`（`value`・`reason`）を足す | 無し |
| `plugins/ndf-next/contracts/workflow/parallel-plan.schema.json` | 変更 | 任意のフィールド `epic`（`name`・`foundation`・`done`）を足す | 無し |
| `plugins/ndf-next/contracts/workflow/progress.json` | 変更 | 計画のイベント `done_checked`・`foundation_noticed` とレーンのイベント `notice_read` を足す。既存のイベントの中身の変更は 2 つで、`plan_started` に任意の `foundation_rev`、`request_opened` にエピックのリクエストの `epic_key` と、完了条件待ちのリクエストだけの `kind: epic_done_wait` と `remaining`（残りの完了条件の番号の配列）が足される。`request_opened` の行（書き手 `organizer`・`workflow`）は変えない | 無し |
| `plugins/ndf-next/docs/contracts/commands.md` | 変更 | サブコマンドの一覧の生成物。`ndf check --write-spec` で描き直す | `plugins/ndf-next/src/ndf/cli.py` |
| `plugins/ndf-next/skills/design/SKILL.md` | 変更 | 触る領域の表にアーキテクチャ設計書のガイドの 1 行を足す | `plugins/ndf-next/skills/design/references/epic-foundation.md` |
| `plugins/ndf-next/skills/design/references/epic-foundation.md` | 新設 | アーキテクチャ設計書の書き方・規模の目安・#1142 の例。セクションの表はコントラクトから生成する | `plugins/ndf-next/contracts/epic/foundation.schema.json` |
| `plugins/ndf-next/skills/development-workflow/SKILL.md` | 変更 | 規模の判定の 5 行を足す | `plugins/ndf-next/skills/design/references/epic-foundation.md` |
| `plugins/ndf-next/skills/issue-plan-strategy/references/parallel-plan.md` | 変更 | 計画の設定ファイルの `epic` フィールドと、起動前チェックの指摘を書く | `plugins/ndf-next/contracts/workflow/parallel-plan.schema.json` |
| `plugins/ndf-next/src/ndf/cli.py` | 変更 | サブコマンドの表に `epic` を足す | `plugins/ndf-next/src/ndf/progress/epic/cli.py` |
| `plugins/ndf-next/src/ndf/adapters/markdown.py` | 変更 | 最上位の表を、行の番号つきで返す関数を足す | markdown-it-py |
| `plugins/ndf-next/src/ndf/progress/epic/__init__.py` | 新設 | パッケージの説明と `TOOL` の名前 | `ndf.cli`（`tool_of`） |
| `plugins/ndf-next/src/ndf/progress/epic/foundation_decide.py` | 新設 | アーキテクチャ設計書の値の型と、解析済みの見出しと表からアーキテクチャ設計書の値を作る・形を判定する純粋な処理 | 無し（標準ライブラリだけ） |
| `plugins/ndf-next/src/ndf/progress/epic/foundation.py` | 新設 | ベースブランチを取得し直し、アーキテクチャ設計書と CI の定義をファイル・コミット・blob から読み、判定の作業ディレクトリを切り出して消す入出力 | `ndf.adapters.markdown`、`ndf.adapters.git`、`ndf.core.route`、`ndf.core.converge.place`、`ndf.contracts` |
| `plugins/ndf-next/src/ndf/progress/epic/verify.py` | 新設 | コントラクトの行の検証と、完了条件の確認方法の実行 | `foundation_decide`、`ndf.adapters.github`、`ndf.contracts`、jsonschema |
| `plugins/ndf-next/src/ndf/progress/epic/cli.py` | 新設 | サブコマンド `ndf epic init`・`check` の引数・結果 JSON・終了コード | `foundation`、`verify`、`ndf.contracts.result` |
| `plugins/ndf-next/src/ndf/progress/plan/__init__.py` | 変更 | 計画の作成と起動にアーキテクチャ設計書のチェック（形・番号・CI の定義）と起動時点のリビジョンを足し、完了と状態に完了条件の項目と計画の終了判定の状態を足す | `ndf.progress.epic.foundation`、`ndf.progress.lane` |
| `plugins/ndf-next/src/ndf/progress/plan/cli.py` | 変更 | `init`・`start` の前にアーキテクチャ設計書のチェックを実行し、`row start`・`resumed` の出力に変更通知を足す | `plan`、`ndf.progress.lane` |
| `plugins/ndf-next/src/ndf/progress/plan/epic.py` | 新設 | Durable Workflow のエピックのアクション（ベースブランチの取得・アーキテクチャ設計書の比較・変更通知・完了条件の判定・エピックのリクエストの開閉） | `ndf.progress.epic`、`ndf.progress.lane`、`ndf.core.durable` |
| `plugins/ndf-next/src/ndf/progress/plan/run.py` | 変更 | エピックのアクションを実行し、スナップショットにエピックの有無と終わった行の数を載せる | `plan_epic`、`run_decide` |
| `plugins/ndf-next/src/ndf/progress/plan/run_decide.py` | 変更 | エピックのアクションを実行する時点を決め、取得の失敗が続いた回数を数える | 無し（判断のモジュール） |
| `plugins/ndf-next/src/ndf/progress/lane/__init__.py` | 変更 | レーン宛ての未読の変更通知を読み、既読フラグを書く | `fold`、`rules` |
| `plugins/ndf-next/src/ndf/progress/lane/fold.py` | 変更 | 計画のイベント 3 つ（`plan_started` のリビジョン・`done_checked`・`foundation_noticed`）とレーンの `notice_read` を畳み込む | `types` |
| `plugins/ndf-next/src/ndf/progress/lane/rules.py` | 変更 | 完了の判定に完了条件の項目を足し、計画の終了判定の状態を導き、変更通知の宛先を選ぶ | `types` |
| `plugins/ndf-next/src/ndf/progress/lane/types.py` | 変更 | 計画のスナップショットに `epic` フィールド・判定・変更通知を足し、エピックのリクエストの種類とキーの頭を定数で持つ | 無し |
| `plugins/ndf-next/src/ndf/progress/command.py` | 変更 | `ndf progress new` に `--scale`・`--scale-reason` を足し、最初のスプリントの組み合わせを断る | `ndf.contracts` |
| `plugins/ndf-next/src/ndf/progress/run.py` | 変更 | Issue の `## 進行` に `規模:` の行を出し、`next`・`wait` の結果に変更通知を載せる | `ndf.progress.lane` |
| `.github/workflows/ndf-next-checks.yml` | 変更 | マーカーを持つアーキテクチャ設計書があれば `bin/ndf epic check` を実行する手順を足す | `plugins/ndf-next/src/ndf/progress/epic/cli.py` |
| `docs/glossary/glossary.json` | 変更 | 設計 PR で反映済み。実装では変えない | 無し |
| `docs/glossary.md` | 変更 | 同上（`glossary.py render` の生成物） | `docs/glossary/glossary.json` |
| `plugins/ndf-next/tests/adapters/test_markdown.py` | 変更 | 表の読み出しのテストを足す | `markdown` |
| `plugins/ndf-next/tests/progress/test_epic_foundation.py` | 新設 | アーキテクチャ設計書の解析と形の判定のテスト | `foundation_decide`、`foundation` |
| `plugins/ndf-next/tests/progress/test_epic_verify.py` | 新設 | 検証と確認方法のテスト | `verify` |
| `plugins/ndf-next/tests/progress/test_epic_cli.py` | 新設 | `ndf epic init`・`check` の結果 JSON と終了コードのテスト | `epic_cli` |
| `plugins/ndf-next/tests/progress/test_plan_epic.py` | 新設 | 起動前チェック・完了条件の項目・変更通知のテスト（受け入れ条件 2・4〜6・8） | `plan`、`plan_epic`、`lane` |
| `plugins/ndf-next/tests/progress/test_command.py` | 変更 | `--scale` の記録と断りのテスト | `command` |
| `plugins/ndf-next/tests/progress/test_run.py` | 変更 | `規模:` の行と `next`・`wait` の変更通知のテスト | `progress_run` |

## クラスと関数の構造

型の関係は設計文書の「構造」のセクションのクラス図を指し、ここでは描き直さない。

| 配置先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `adapters/markdown.py` | モジュール | 新設: `Table`（型）・`tables` |
| `progress/epic/foundation_decide.py` | モジュール | 新設: `Foundation`・`Section`・`ContractRow`・`DoneRow`・`Finding`（型）・`parse`・`shape_errors`・`unknown_done`・`ci_files`・`ci_check_missing`・`changed_sections`・`places_of`・`template`・`parse_verify`・`parse_check` |
| `progress/epic/foundation.py` | モジュール | 新設: `FoundationError`・`FetchFailed`（例外。`FetchFailed` は `FoundationError` の子で、`plan.epic_gate` が捕まえて指摘 `fetch-failed` にする。`WorkdirLeft` も `FoundationError` の子で、`--force` を付けない削除で消せない判定の作業ディレクトリの配置先を持つ）・`spec`・`read_text`・`read_file`・`base_ref`・`fetch_base`・`read_at`・`read_paths_at`・`read_blob`・`checkout`・`marked_files` |
| `progress/epic/verify.py` | モジュール | 新設: `DoneVerdict`（型）・`verify_rows`・`verify_one`・`done_verdicts`・`run_command` |
| `progress/epic/cli.py` | モジュール | 新設: `parser`・`main`・`_cmd_init`・`_cmd_check` |
| `cli.py` | モジュール | 変更: 表 `COMMANDS` に 1 行（関数は変えない） |
| `progress/plan/__init__.py` | モジュール | 新設: `epic_gate`・`epic_items`。変更: `plan_payload`・`create_plan`・`start_plan`・`complete_plan`・`status_of`・`_plan_metrics` |
| `progress/plan/cli.py` | モジュール | 変更: `_dispatch`・`_cmd_create`・`_row_start`・`_row_resumed` |
| `progress/plan/epic.py` | モジュール | 新設: `TickInput`（型）・`tick`・`gather`・`write_tick` |
| `progress/plan/run.py` | モジュール | 変更: `_perform`・`snapshot_of`・`spec_of`・`policy` |
| `progress/plan/run_decide.py` | モジュール | 新設: `EpicTick`（アクション）・`_epic_due`。変更: `Snapshot`・`RunState`・`Policy`・`snapshot`・`_after_observe`・`decide` |
| `progress/lane/__init__.py` | モジュール | 新設: `take_notices`・`sprint_notices` |
| `progress/lane/fold.py` | モジュール | 新設: `last_notice_read`。変更: `_fold_plan_event` |
| `progress/lane/rules.py` | モジュール | 新設: `rows_done`・`done_remaining`・`epic_state`・`notice_targets`。変更: `plan_complete` |
| `progress/lane/types.py` | モジュール | 新設: `EpicDecl`・`Notice`（型）・定数 `DONE_WAIT`・`EPIC_KEYS`。変更: `PlanView`（フィールドを 4 つ足す） |
| `progress/command.py` | モジュール | 変更: `_prepare_new`。`parser` は引数を 2 つ足すだけ |
| `progress/run.py` | モジュール | 変更: `issue_section`・`_cmd_next`・`_cmd_wait` |

## 関数の仕様

| 関数 | 配置先 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `markdown.tables` | `adapters/markdown.py` | 新設 | `tables(text: str) -> list[Table]` | 最上位の GFM の表を、見出しの行・本文の行・各行の行番号つきで返す | 無し | 引用とリストの中の表は返さない。セルは前後の空白を除いた文字列 | 無し |
| `foundation_decide.parse` | `progress/epic/foundation_decide.py` | 新設 | `parse(path: str, lines: list[str], headings: Sequence[Heading], tables: Sequence[Table], kinds: Sequence[str], spec: Mapping[str, Any]) -> Foundation` | 解析済みの見出し・表・行の種類から、セクション・コントラクトの行・完了条件の行を持つアーキテクチャ設計書の値を作る | `kinds` の長さが `lines` と同じ | セクションは `##` の見出しごとに 1 つ。セクションの本文の行は HTML のコメントと空行を除いた行。コントラクトの行と完了条件の行はコントラクトが表を持つと定めるセクションからだけ作る | 無し |
| `foundation_decide.shape_errors` | `progress/epic/foundation_decide.py` | 新設 | `shape_errors(f: Foundation, spec: Mapping[str, Any]) -> list[Finding]` | 必須セクションの有無と空、表の列と各行の形を判定する | `f` は `parse` の結果 | 指摘は `empty-section`（無いセクションも含む）か `shape`。並びはコントラクトのセクションの順、セクションの中は行の順 | 無し |
| `foundation_decide.unknown_done` | `progress/epic/foundation_decide.py` | 新設 | `unknown_done(f: Foundation, ids: Sequence[str]) -> list[Finding]` | 計画の完了条件の番号のうち、アーキテクチャ設計書の完了条件の表に無いものを指摘にする | 無し | 指摘は `unknown-done`。番号の並びは `ids` の順 | 無し |
| `foundation_decide.ci_files` | `progress/epic/foundation_decide.py` | 新設 | `ci_files(f: Foundation, spec: Mapping[str, Any]) -> list[str]` | 「テストと検証の基盤」のセクションの `触るファイル:` の行が名指す CI の定義のパスを返す | 無し | セクションか行が無ければ空。並びは行に書いた順 | 無し |
| `foundation_decide.ci_check_missing` | `progress/epic/foundation_decide.py` | 新設 | `ci_check_missing(paths: Sequence[str], texts: Mapping[str, str \| None], spec: Mapping[str, Any]) -> list[Finding]` | 名指した CI の定義のどれかに、コントラクトの `ci_command`（`ndf epic check`）が文字どおり現れるかを判定する（I11） | `texts` のキーは `paths` と同じ。値が None のファイルはコミットに無い | 現れなければ指摘 `missing-ci-check` を 1 件。詳細は、パスが 0 件なら「触るファイルの行が無い」、コミットに無いファイルがあればそのパス、それ以外は名指したパスの並び | 無し |
| `foundation_decide.changed_sections` | `progress/epic/foundation_decide.py` | 新設 | `changed_sections(old: Foundation, new: Foundation, spec: Mapping[str, Any]) -> list[str]` | 2 つのリビジョンで内容の違うセクションの名前を返す | 無し | 片方にだけあるセクションも違うセクションに含む。コントラクトの表を持つセクションは、コントラクトの `verify_column`（検証方法の列）を除いた行で比べ、検証方法だけが違うセクションは返さない。並びは `new` のセクションの順、`old` にだけあるセクションは後ろ | 無し |
| `foundation_decide.places_of` | `progress/epic/foundation_decide.py` | 新設 | `places_of(old: Foundation, new: Foundation, names: Sequence[str], spec: Mapping[str, Any]) -> list[str]` | 違うセクションの触るファイルを集める | `names` は `changed_sections` の結果 | 表のセクションは 2 つのリビジョンで違う行（検証方法の列を除いて比べる）の正本と検証方法のパス。表の無いセクションは `触るファイル:` の行。どちらも無いセクションは `**`。重複を除き、並びは出た順 | 無し |
| `foundation_decide.template` | `progress/epic/foundation_decide.py` | 新設 | `template(name: str, spec: Mapping[str, Any]) -> str` | マーカー・見出し・セクションごとのガイドのコメント・表の見出しの行を持つテンプレートの文を作る | 無し | 返した文を `parse` して `shape_errors` に掛けると、必須セクションがすべて `empty-section` になる | 無し |
| `foundation_decide.parse_verify` | `progress/epic/foundation_decide.py` | 新設 | `parse_verify(cell: str) -> Verify \| None` | コントラクトの行の検証のフィールドを `exists`・`schema`・`generated`・`—` のどれかに読む | 無し | 4 つのどれでもなければ None。`—` は種類 `none` | 無し |
| `foundation_decide.parse_check` | `progress/epic/foundation_decide.py` | 新設 | `parse_check(cell: str) -> Check \| None` | 完了条件の確認方法のフィールドを `issues`（番号の並び）か `command`（コマンドの文）に読む | 無し | どちらでもない・番号が 1 つも無い・コマンドが空なら None | 無し |
| `foundation.spec` | `progress/epic/foundation.py` | 新設 | `spec() -> dict[str, Any]` | アーキテクチャ設計書のスキーマを読んで返す | 無し | 返す値はコントラクトの形のチェックを通っている | コントラクトのファイルを読む |
| `foundation.read_text` | `progress/epic/foundation.py` | 新設 | `read_text(text: str, path: str) -> Foundation` | 文から見出し・表・行の種類を作り、`foundation_decide.parse` へ渡す | 無し | `parse` の事後条件と同じ | 無し |
| `foundation.read_file` | `progress/epic/foundation.py` | 新設 | `read_file(root: Path, path: str) -> Foundation` | worktree のアーキテクチャ設計書を読む（`ndf epic check` が使う） | `path` は `root` からの相対 | 読めなければ `FoundationError` | ファイルを読む |
| `foundation.base_ref` | `progress/epic/foundation.py` | 新設 | `base_ref(root: Path) -> str` | ベースブランチの名前を返す | `root` は git のリポジトリのルート | ベースブランチは `ndf.core.route.default_base` の結果。決まらなければ `FoundationError` | git を読む（`symbolic-ref`） |
| `foundation.fetch_base` | `progress/epic/foundation.py` | 新設 | `fetch_base(root: Path, branch: str) -> str` | ベースブランチを origin からリモート追跡ブランチへ取得し直し、その先端のコミットを返す | `branch` は `base_ref` の結果 | 返すコミットは取得した直後の `origin/<branch>` の先端。`FETCH_HEAD` を読まない。取得できなければ `FetchFailed` | ネットワーク（`git fetch -q --no-tags -- origin +refs/heads/<branch>:refs/remotes/origin/<branch>`）・git を読む（`rev-parse`） |
| `foundation.read_at` | `progress/epic/foundation.py` | 新設 | `read_at(root: Path, commit: str, path: str) -> tuple[Foundation, str] \| None` | コミットにあるアーキテクチャ設計書と、その blob の識別子を返す | `commit` は `fetch_base` の結果か、記録した `done_checked` の `commit` | `commit` にファイルが無ければ None。識別子は `git rev-parse <commit>:<path>` の値 | git を読む（`rev-parse`・`cat-file`） |
| `foundation.read_paths_at` | `progress/epic/foundation.py` | 新設 | `read_paths_at(root: Path, commit: str, paths: Sequence[str]) -> dict[str, str \| None]` | コミットにある複数のファイルの中身を返す（CI の定義の確かめに使う） | 無し | コミットに無いファイルと UTF-8 で読めないファイルの値は None | git を読む（`cat-file`） |
| `foundation.read_blob` | `progress/epic/foundation.py` | 新設 | `read_blob(root: Path, blob: str) -> Foundation` | blob の識別子から過去のリビジョンのアーキテクチャ設計書を読む（変更通知の比較の元） | 無し | 読めなければ `FoundationError` | git を読む（`cat-file`） |
| `foundation.checkout` | `progress/epic/foundation.py` | 新設 | `checkout(root: Path, plan_id: str, commit: str) -> ContextManager[Path]` | 判定のコミットを判定の作業ディレクトリへ切り出し、ブロックを抜けたら消す | 無し | 配置先は `ndf.core.converge.place.work_root(<git>, "epic-<計画の ID>")`。前の回の作業ディレクトリが残っていれば、`--force` を付けない `worktree remove` で先に消してから作る。消せなければ作らずに `WorkdirLeft`（`FoundationError` の子。配置先を持つ）を出す。抜けるときも `--force` を付けずに消し、消せなければ配置先を残して呼び手へ知らせる（`gather` が `TickInput.workdir_left` に入れる）。消せた後は配置先が無く、`git worktree list` にも残らない | git を書く（`worktree add --detach`・`worktree remove`（`--force` を付けない）・`worktree prune`）・ファイルを書く |
| `foundation.marked_files` | `progress/epic/foundation.py` | 新設 | `marked_files(root: Path) -> list[str]` | 追跡している Markdown のうち、先頭の行がマーカーのものを返す | 無し | パスは `root` からの相対で、辞書順 | git を読む（`ls-files`）・ファイルを読む |
| `verify.verify_rows` | `progress/epic/verify.py` | 新設 | `verify_rows(root: Path, f: Foundation, timeout: float) -> list[Finding]` | 検証方法を持つコントラクトの行をすべて検証し、食い違いを指摘にする | `f` に `shape` の指摘が無い | 指摘は `contract-mismatch`。種類 `none` の行は検証しない | ファイルを読む・外部コマンド（`generated:`） |
| `verify.verify_one` | `progress/epic/verify.py` | 新設 | `verify_one(root: Path, row: ContractRow, timeout: float) -> str \| None` | コントラクトの行 1 つを検証し、食い違いの文を返す | `row.verify` が None でない | 食い違いが無ければ None | ファイルを読む・外部コマンド（`generated:`） |
| `verify.done_verdicts` | `progress/epic/verify.py` | 新設 | `done_verdicts(cwd: Path \| None, rows: Sequence[DoneRow], states: Callable[[Sequence[int]], dict[int, str]], timeout: float) -> list[DoneVerdict]` | 完了条件の行ごとに確認方法を実行し、満たしたかを返す | `rows` は計画の `done` の番号に絞ってある。`command:` の行があれば `cwd` は判定の作業ディレクトリ | 1 行に 1 つの判定。`command:` は `cwd` で実行する。Issue の状態はまとめて 1 回だけ読む | 外部コマンド（`command:`）・ネットワーク（`states` が GitHub を読む） |
| `verify.run_command` | `progress/epic/verify.py` | 新設 | `run_command(root: Path, text: str, timeout: float) -> tuple[int, str]` | シェルを通さずにコマンドを `root` で実行し、終了コードと標準出力を返す | `text` は `shlex.split` で割れる | 上限に当たれば終了コード `-1` と上限の文 | 外部コマンド |
| `epic_cli.main` | `progress/epic/cli.py` | 新設 | `main(argv: list[str]) -> int` | 引数を読み、サブコマンドを流し、結果 JSON を最後の 1 行に出して終了コードを返す | 無し | 終了コードは結果のコントラクトの `cause.kind` から引く（0・1・2） | 標準出力 |
| `epic_cli._cmd_init` | `progress/epic/cli.py` | 新設 | `_cmd_init(a: Namespace, root: Path) -> dict[str, Any]` | テンプレートを書く | 無し | ファイルがあれば書かずに `refused`。書いたらパスを `items` に返す | ファイルを書く（親のディレクトリを作る） |
| `epic_cli._cmd_check` | `progress/epic/cli.py` | 新設 | `_cmd_check(a: Namespace, root: Path) -> dict[str, Any]` | 渡したアーキテクチャ設計書かマーカーを持つアーキテクチャ設計書のすべてに形のチェックと、`--shape-only` でなければ検証を当てる | 無し | 指摘が無ければ `ok`、あれば `check_failed`。マーカーを持つアーキテクチャ設計書が 0 本なら `ok` で `metrics.files` が 0 | ファイルを読む・外部コマンド |
| `plan.epic_gate` | `progress/plan/__init__.py` | 新設 | `epic_gate(root: Path, doc: Mapping[str, Any]) -> tuple[list[Finding], str \| None]` | 計画の設定ファイルに `epic` があれば、ベースブランチを取得し直し、そのリビジョンのアーキテクチャ設計書に形のチェック・番号の突き合わせ・CI の定義の確かめを当てる | `doc` は計画の設定ファイルのスキーマを通っている | `epic` が無ければ `([], None)`。指摘があれば識別子は None。手順は「処理の手順」の起動前チェックの表のとおり | git を読む・ネットワーク（`fetch_base`） |
| `plan.plan_payload` | `progress/plan/__init__.py` | 変更 | `plan_payload(doc: dict[str, Any]) -> dict[str, Any]` | 計画の行に `epic` フィールドを足す | 無し | `epic` が無い設定ファイルの値は今と同じ | 無し |
| `plan.create_plan` | `progress/plan/__init__.py` | 変更 | `create_plan(conn, repo: str, doc: dict[str, Any], start: bool, foundation_rev: str \| None = None) -> str` | 起動するときの `plan_started` に起動時点のリビジョンを書く | `epic` があれば `epic_gate` が指摘 0 件で通っている | `foundation_rev` が None なら今と同じイベント | ワークフロー状態の DB にイベントを書く |
| `plan.start_plan` | `progress/plan/__init__.py` | 変更 | `start_plan(conn, pid: str, foundation_rev: str \| None = None) -> None` | 止めた計画の起動にもリビジョンを書く | 同上 | 比較元は最初の `plan_started` のリビジョンのまま（`fold` が最初の値だけを持つ） | ワークフロー状態の DB にイベントを書く |
| `plan.complete_plan` | `progress/plan/__init__.py` | 変更 | `complete_plan(conn, pid: str) -> bool` | 完了条件待ちのリクエストが開いていれば、同じトランザクションで閉じてから計画を完了にする | 無し | 完了にしたら真。`rules.plan_complete` が偽なら何も書かない | ワークフロー状態の DB にイベントを書く |
| `plan.epic_items` | `progress/plan/__init__.py` | 新設 | `epic_items(root: Path, view: PlanView) -> tuple[list[dict[str, Any]], dict[str, int]]` | 完了条件の項目ごとの行（`kind: done`・`id`・`text`・`met`・`detail`・`checked_at`）と、満たした件数 / 全件数を作る | 無し | `epic` が無ければ `([], {})`。判定の記録の無い番号は `met: null`。`text` は最後の `done_checked` の `commit` のアーキテクチャ設計書から読み、判定の記録が無ければ `foundation_rev` の blob から読む。読めない番号の `text` は null | git を読む（`read_at`・`read_blob`） |
| `plan.status_of` | `progress/plan/__init__.py` | 変更 | 変えない | `items` に完了条件の行、`metrics.epic_done` に件数、`metrics.epic_state` に `rules.epic_state` の値を足す | 無し | `epic` の無い計画の結果は今と同じ | git を読む（`epic_items`） |
| `plan_cli._dispatch` | `progress/plan/cli.py` | 変更 | 変えない | `init`・`start` で、トランザクションを開く前に `epic_gate` を実行し、指摘があれば止める | 無し | 指摘があればワークフロー状態を 1 つも書かず、`check_failed` の結果を返す | git を読む・ネットワーク |
| `plan_cli._row_start` | `progress/plan/cli.py` | 変更 | 変えない | 行を始めた同じトランザクションで、レーンの未読の変更通知を結果の `notices` に載せる | 無し | 載せたら既読フラグが書かれている | ワークフロー状態の DB にイベントを書く |
| `plan_cli._row_resumed` | `progress/plan/cli.py` | 変更 | 変えない | 同上（スプリントの ID からレーンを引く） | 無し | 同上 | ワークフロー状態の DB にイベントを書く |
| `plan_epic.tick` | `progress/plan/epic.py` | 新設 | `tick(spec: dict[str, Any], fetch_failures: int) -> dict[str, Any]` | Durable Step。1 回分のエピックのアクションとして、ベースブランチを取得し直し、判定のコミットで比較と判定を行って書く | 計画が `epic` を持つ。`fetch_failures` はこの回より前に続けて失敗した回数 | 戻り値は `{"fetch_failed": 真偽, "commit": 判定のコミット \| None, "noticed": 件数, "judged": 件数, "remaining": [番号], "error": 文 \| None, "judged_all": 真偽}`。`judged_all` は、取得・アーキテクチャ設計書の読み取り・判定の作業ディレクトリの作成・Issue の状態の取得がすべて通り、計画の番号をすべて判定し直せた回だけ真 | git・ネットワーク・外部コマンド・ワークフロー状態の DB |
| `plan_epic.gather` | `progress/plan/epic.py` | 新設 | `gather(root: Path, view: PlanView, timeout: float) -> TickInput` | DB の外で、ベースブランチの取得・判定のコミットのアーキテクチャ設計書・比較元のリビジョン・判定を読む | 無し | 取得できなければ `TickInput.fetch_failed` が真で、ほかは空。`command:` の行があれば `foundation.checkout` の中で判定し、戻る前に判定の作業ディレクトリを消す（消せなければ `TickInput.workdir_left` に配置先）。計画の番号のうちアーキテクチャ設計書に無いものは `met: false`（`detail` は「アーキテクチャ設計書に無い番号」）にし、`TickInput.unknown` に並べる | git・ネットワーク・外部コマンド |
| `plan_epic.write_tick` | `progress/plan/epic.py` | 新設 | `write_tick(conn, plan_id: str, got: TickInput, fetch_failures: int, fetch_limit: int) -> dict[str, Any]` | 読み直した計画に、変わった判定・変更通知・エピックのリクエストの開閉を書く | 無し | 計画が実行中でなければ何も書かない。同じ入力で 2 回呼んでも 2 回目は何も書かない。書く順は「処理の手順」の表のとおり | ワークフロー状態の DB にイベントを書く |
| `plan_run._perform` | `progress/plan/run.py` | 変更 | 変えない | アクション `EpicTick` を `plan_epic.tick` へ渡す | 無し | 結果は `Done(act, 戻り値)` | Durable Step を実行する |
| `plan_run.snapshot_of` | `progress/plan/run.py` | 変更 | 変えない | スナップショットに `epic`（フィールドの有無）と `rows_finished`（完了か中止の行の数）を足す | 無し | `epic` の無い計画は `epic: false` | 無し |
| `run_decide._epic_due` | `progress/plan/run_decide.py` | 新設 | `_epic_due(state: RunState, snap: Snapshot, policy: Policy) -> EpicTick \| None` | エピックのアクションを実行する時点かを決める | 無し | 実行するなら `EpicTick(rows_finished, fetch_failures)`、実行しなければ None | 無し |
| `run_decide._after_observe` | `progress/plan/run_decide.py` | 変更 | 変えない | 完了・変更通知・起動の判断の前に `_epic_due` を当てる。`epic: true` のスナップショットでは、`state.epic_judged` が真で、`state.epic_rows_seen` が `snap.rows_finished` と同じときだけ `Complete` を返す（全行が終わった後の最新の回が判定し直せていなければ完了にしない。設計文書の I5） | 無し | `epic: false` のスナップショットでは今と同じアクションを返す | 無し |
| `run_decide.decide` | `progress/plan/run_decide.py` | 変更 | 変えない | `Sleep` の結果で `epic_slept` を足す。`EpicTick` の結果で `epic_slept` を 0、`epic_rows_seen` をアクションの `rows_finished` にし、戻り値の `fetch_failed` が真なら `epic_fetch_failures` を 1 足し、偽なら 0 に戻す。`epic_judged` を戻り値の `judged_all` にする（例外で落ちた回は偽） | 無し | ほかのアクションの扱いは今と同じ | 無し |
| `lane.take_notices` | `progress/lane/__init__.py` | 新設 | `take_notices(conn, plan_id: str, lane_name: str, actor: str) -> list[dict[str, Any]]` | レーン宛ての未読の変更通知を返し、既読フラグを書く | 無し | 返した変更通知が 1 件以上なら、レーンのワークフロー状態の最後のイベントが `notice_read`（最後の変更通知の番号） | ワークフロー状態の DB にイベントを書く |
| `lane.sprint_notices` | `progress/lane/__init__.py` | 新設 | `sprint_notices(conn, sprint_id: str, actor: str) -> list[dict[str, Any]]` | スプリントを持つレーンの行が実行中なら `take_notices` を実行する | 無し | レーンの外か、行が実行中でなければ空で、何も書かない | ワークフロー状態の DB にイベントを書く |
| `fold.last_notice_read` | `progress/lane/fold.py` | 新設 | `last_notice_read(events: Iterable[Event]) -> int` | レーンのワークフロー状態の最後の `notice_read` の番号を返す | 無し | 無ければ 0 | 無し |
| `fold._fold_plan_event` | `progress/lane/fold.py` | 変更 | 変えない | `created` の `epic`、最初の `plan_started` の `foundation_rev`、`done_checked`（番号ごとに最後）、`foundation_noticed`（並び）をスナップショットに畳む | 無し | フィールドの無いイベントの扱いは今と同じ | 無し |
| `rules.rows_done` | `progress/lane/rules.py` | 新設 | `rows_done(view: PlanView, lanes: Lanes) -> bool` | 全レーンの行が完了か中止かを返す | 無し | 今の `plan_complete` の前半と同じ判定 | 無し |
| `rules.done_remaining` | `progress/lane/rules.py` | 新設 | `done_remaining(view: PlanView) -> list[str]` | 計画の完了条件の番号のうち、最後の判定が満たしていないもの（未判定を含む）を返す | 無し | `epic` が無ければ空。並びは `epic.done` の順 | 無し |
| `rules.plan_complete` | `progress/lane/rules.py` | 変更 | 変えない | 全行の終わりと、完了条件待ちのリクエストを除いた答えていないリクエストが無いことに、完了条件の残りが無いことを足す | 無し | `epic` が無ければ今と同じ（I6）。取得の失敗・アーキテクチャ設計書に無い番号・消せない判定の作業ディレクトリのリクエストは、答えていないリクエストに数える | 無し |
| `rules.epic_state` | `progress/lane/rules.py` | 新設 | `epic_state(view: PlanView, lanes: Lanes) -> str \| None` | 計画の終了判定の状態を `running`・`waiting_done`・`done` のどれかに導く | 無し | `epic` が無ければ None。手順は「処理の手順」の計画の終了判定の状態の表のとおり。完了してよいかは `plan_complete` を呼んで決め、規則を別に持たない | 無し |
| `rules.notice_targets` | `progress/lane/rules.py` | 新設 | `notice_targets(view: PlanView, lanes: Lanes, places: Sequence[str]) -> list[str]` | 終わっていないレーンのうち、触るファイルが `places` のどれかと重なるものを返す | 無し | 重なりは既存の `touch_hit`。`**` はすべてのレーンに当たる。並びは計画のレーンの順 | 無し |
| `command._prepare_new` | `progress/command.py` | 変更 | 変えない | `--scale` があればコンテキストに `scale` を足し、`epic-first` の組み合わせを確かめる | 無し | `--scale` が無ければコンテキストは今と同じ。`epic-first` でモードが `standard` でないか進め方が `normal` でなければ `model.Refused` | 無し |
| `progress_run.issue_section` | `progress/run.py` | 変更 | 変えない | コンテキストに `scale` があれば `モード:` の次の行に `規模:` の行を出す | 無し | `scale` が無ければ今と同じ文 | 無し |
| `progress_run._cmd_next` | `progress/run.py` | 変更 | 変えない | 結果に、レーンの未読の変更通知 `notices` を足す | 無し | 変更通知が無ければ結果は今と同じ | ワークフロー状態の DB にイベントを書く |
| `progress_run._cmd_wait` | `progress/run.py` | 変更 | 変えない | 同上 | 無し | 同上 | ワークフロー状態の DB にイベントを書く |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `foundation.spec` | コントラクトが読めない・形が違う | ファイルの欠け・JSON の誤り | `contracts` の読み出し | 止める（`ContractError`） | `config_broken`・終了コード 2 | 後始末は無い。直して再実行できる |
| `foundation.read_file` | アーキテクチャ設計書が読めない | パスの誤り・権限・UTF-8 でない | `Path.read_text` の `OSError`・`UnicodeDecodeError` | 呼び出し元へ返す（`FoundationError`） | `ndf epic check` は `config_broken`・終了コード 2。`summary` にパス | 後始末は無い。直して再実行できる |
| `foundation.base_ref` | ベースブランチが決まらない | 設定ファイルの `base_branch` も origin の既定ブランチも無い | `ndf.core.route.default_base` が None | 呼び出し元へ返す（`FoundationError`） | `ndf lanes init`・`start` は指摘 `missing-foundation`（詳細に「ベースブランチが決まらない」）・終了コード 1 | 後始末は無い。設定ファイルを直すか `git remote set-head` の後に再実行できる |
| `foundation.fetch_base` | 取得できない | ネットワーク・認証・origin に無いブランチ | `Git.fetch_branch` の終了コード | 呼び出し元へ返す（`FetchFailed`） | `ndf lanes init`・`start` は指摘 `fetch-failed`（詳細に、git の標準エラーの全文を `redact` に通した後の最後の 1 行）・終了コード 1。Durable Workflow はその回のイベントを書かず、`run.log` に 1 行 | 何も書いていない。再実行できる。Durable Workflow は次の回で取り直し、続けて失敗した回数が `lanes.epic-fetch-failures` を超えたらリクエスト `fetch-failed` を開く |
| `foundation.read_at` | アーキテクチャ設計書がベースブランチのリビジョンに無い | 設計 PR がまだマージされていない・パスの誤り | `git rev-parse <commit>:<path>` の失敗 | None を返す | `ndf lanes init`・`start` は指摘 `missing-foundation`・終了コード 1 | 後始末は無い。マージの後に再実行できる |
| `foundation.read_at` | git が読めない | リポジトリでない・壊れた参照 | `GitError` | 呼び出し元へ返す（`FoundationError`） | `config_broken`・終了コード 2 | 後始末は無い |
| `foundation.read_paths_at` | 名指した CI の定義がコミットに無い | パスの誤り・CI の定義がまだマージされていない | `git cat-file` の失敗 | そのパスの値を None にして続ける | 呼び手の `ci_check_missing` が指摘 `missing-ci-check`（詳細にパス）にする・終了コード 1 | 後始末は無い。直して再実行できる |
| `foundation.read_paths_at` | git が読めない | リポジトリでない・壊れた参照 | `GitError` | 呼び出し元へ返す（`FoundationError`） | `config_broken`・終了コード 2 | 後始末は無い |
| `foundation.checkout` | 判定の作業ディレクトリを作れない | ディスク・権限 | `git worktree add` の失敗 | 呼び出し元へ返す（`FoundationError`） | Durable Workflow はその回の `command:` の項目を判定せず（前の判定が今の値のまま残る）、`judged_all` が偽で完了にしない。`run.log` に 1 行 | 作りかけの配置先は `--force` を付けない `worktree remove` と `worktree prune` で消す。消せなければ次の行と同じ扱い。次の回で作り直す |
| `foundation.checkout` | 判定の作業ディレクトリを消せない | `command:` がファイルを書き換えたか残した・中のプロセスがファイルを開いている・権限 | `--force` を付けない `git worktree remove` の失敗 | 強制では消さない（共通原則 C4）。配置先を残し、`TickInput.workdir_left` に入れる。前の回の残りで作れないときは `WorkdirLeft` | `write_tick` がリクエスト `workdir-left` を開いて人へ戻す（理由に配置先と「`--force` を付けない削除が断られた」）。`run.log` に 1 行。残る間は `command:` の項目を判定せず、`judged_all` が偽で完了にしない | 人が中身を確かめて消した後、次の回の `checkout` が作り直し、作れた回に `write_tick` がリクエストを閉じる。配置先は計画ごとに 1 つで、増え続けない |
| `foundation.read_blob` | 比較元の blob が無い | 記録した識別子のオブジェクトが gc された | `git cat-file` の失敗 | 呼び出し元へ返す（`FoundationError`） | Durable Workflow は比較元を空のアーキテクチャ設計書として扱い、すべてのセクションを違うセクションにして通知する（`run.log` に理由） | 変更通知を 1 件書けば、比較元はその `to_rev` に進む |
| `foundation.marked_files` | 追跡しているファイルを列挙できない | git でない | `GitError` | 呼び出し元へ返す | `config_broken`・終了コード 2 | 後始末は無い |
| `verify.verify_rows` | 検証の対象が無い | `schema:`・`exists:` の glob に当たるファイルが 0 件 | glob の結果 | 指摘にする | 指摘 `contract-mismatch`（詳細「検証の対象が無い: <glob>」）・終了コード 1 | 後始末は無い |
| `verify.verify_one` | スキーマが読めない | 正本のパスの誤り・JSON の誤り | jsonschema の読み出し | 指摘にする | 指摘 `contract-mismatch`（詳細に正本のパスと理由）・終了コード 1 | 後始末は無い |
| `verify.verify_one` | 生成のコマンドが失敗する・上限に当たる | コマンドの誤り・遅い生成 | `run_command` の終了コード | 指摘にする | 指摘 `contract-mismatch`（詳細に終了コードか上限の秒）・終了コード 1 | コマンドの子のプロセスを止める。再実行できる |
| `verify.done_verdicts` | `command:` を実行する場所が無い | `cwd` が None（切り出しに失敗した回） | 引数の確かめ | `command:` の行だけ判定から外す | 外した番号の `done_checked` を書かない。前の判定が今の値のまま残る | 次の回で判定し直す |
| `verify.done_verdicts` | Issue の状態が読めない | GitHub の上限・ネットワーク | `states` の例外 | 呼び出し元へ返す | Durable Workflow はこの回の判定を書かず、`judged_all` が偽で完了にしない（`run.log` に 1 行） | 前の判定が今の値のまま残る。次の間隔で再実行する |
| `verify.done_verdicts` | 確認方法のコマンドが失敗する・上限に当たる | 条件がまだ成り立たない・遅いコマンド | `run_command` の終了コード | 判定の値にする | `met: false`、`detail` に終了コードか上限の秒 | 次の間隔で再実行する |
| `verify.run_command` | コマンドが見つからない | 環境に無いコマンド | `FileNotFoundError` | 終了コード `127` と理由の文を返す | 呼び手の指摘か判定の `detail` に出る | 後始末は無い |
| `epic_cli.main` | 使い方の誤り | 知らない引数 | argparse | 止める | 使い方の文・終了コード 2 | 後始末は無い |
| `epic_cli._cmd_init` | 同名のファイルがある | 既にアーキテクチャ設計書がある | `Path.exists` | 止める | `refused`・終了コード 1。上書きしない | 書いていない |
| `epic_cli._cmd_init` | 書けない | 権限・ディスク | `OSError` | 止める | `config_broken`・終了コード 2 | 途中のファイルは残らない（一時ファイルに書いて置き換える） |
| `epic_cli._cmd_check` | 形か検証の指摘がある | 空のセクション・列の誤り・食い違い | `shape_errors`・`verify_rows` | 止める | `check_failed`・終了コード 1。指摘は 1 件 1 行で `items` | 後始末は無い |
| `plan.epic_gate` | アーキテクチャ設計書のチェックの指摘がある | 取得できない・ベースブランチのリビジョンにアーキテクチャ設計書が無い・空のセクション・列の誤り・アーキテクチャ設計書に無い番号・CI の定義に `ndf epic check` が無い | `fetch_base`・`read_at`・`shape_errors`・`unknown_done`・`ci_check_missing` | 呼び出し元へ返す（指摘の並び） | 呼び手の `plan_cli._dispatch` が出す | 何も書いていない |
| `plan.epic_items` | 項目の文を読めない | 記録したコミットか blob が gc された・アーキテクチャ設計書が移された | `read_at` が None・`FoundationError` | 既定値で続ける | その番号の `text` を null にし、`met` と件数は記録のまま出す | 後始末は無い。次の判定の `done_checked` の `commit` から読める |
| `plan.status_of` | 項目の文を読めない | `epic_items` と同じ | `epic_items` | 既定値で続ける | `text` が null の行を出す。終了コードは今と同じ | 後始末は無い |
| `plan.create_plan` | 計画のルールに当たる | 既存のルール（レーン名の重なりなど） | 既存の `plan_errors` | 止める（`model.Refused`） | 今と同じ `refused`・終了コード 1 | トランザクションごと戻る |
| `plan.start_plan` | 始められない状態 | 既存のルール | 既存の状態のチェック | 止める（`model.Refused`） | 今と同じ | 同上 |
| `plan.complete_plan` | 書く前に計画が動いた | 同時の書き込み（「並行と排他」） | `store.append_events` の番号の食い違い | 呼び出し元へ返す | Durable Workflow の既存の再実行 | トランザクションごと戻る。ループの次の 1 回で判定し直す |
| `plan_cli._dispatch` | 起動前チェックの指摘がある | `epic_gate` の指摘 | `epic_gate` の戻り値 | 止める | `check_failed`・終了コード 1。指摘は `items`（`rule` は `fetch-failed`・`missing-foundation`・`unknown-done`・`missing-ci-check`・`empty-section`・`shape`） | ワークフロー状態を 1 つも書いていない。直して再実行できる |
| `plan_cli._dispatch` | アーキテクチャ設計書・git が読めない | `FoundationError` | `epic_gate` の例外 | 止める | `config_broken`・終了コード 2 | 同上 |
| `plan_cli._row_start` | 行を始められない | 既存のルール | 既存の `start_row` | 止める | 今と同じ `refused`。変更通知の既読フラグも書かない（同じトランザクション） | トランザクションごと戻る |
| `plan_cli._row_resumed` | 書く前にレーンのワークフロー状態が動いた | 同時の書き込み | `checked_write` の番号の食い違い | 呼び出し元へ返す | 既存の再実行（`retrying_transaction`） | トランザクションごと戻る |
| `plan_epic.tick` | 読み取りの一部が失敗した | `gather` の `error` | `TickInput.error` | 既定値で続ける（読めた分だけ書く） | 戻り値の `error` と、偽の `judged_all`（完了にしない）。`run.log` に 1 行 | 次の間隔で再実行する |
| `plan_epic.tick` | ベースブランチを取得できない | `fetch_base` の `FetchFailed` | `TickInput.fetch_failed` | 比較も判定もせず、`write_tick` は取得の失敗のリクエストだけを扱う | 戻り値の `fetch_failed` が真。続けた回数が上限を超えたらリクエスト `fetch-failed`（理由に回数と最後のエラー） | 次の回で取り直す。取得できた回に `write_tick` がリクエストを閉じる |
| `plan_epic.gather` | 計画の番号がアーキテクチャ設計書に無い | 起動の後に、アーキテクチャ設計書を書き換える Pull Request が行を消すか番号を付け替えた（I10） | `unknown_done` | 判定の値にする | 無い番号は `met: false`（`detail` は「アーキテクチャ設計書に無い番号」）。リクエスト `unknown-done:<番号の並び>` が開く | 起動の後に計画の `done` を書き換える経路は持たない。オーガナイザーがアーキテクチャ設計書を直す Pull Request をベースブランチへ入れ、無い番号がなくなった回に `write_tick` が閉じる |
| `plan_epic.gather` | ベースブランチのリビジョンにアーキテクチャ設計書が無くなった | アーキテクチャ設計書を消す・移す変更がマージされた | `read_at` が None | 既定値で続ける（変更通知と判定を書かない） | 戻り値の `error`（「ベースブランチのリビジョンにアーキテクチャ設計書が無い」）と、偽の `judged_all`（完了にしない）。`status` の完了条件の項目は前の判定のまま | アーキテクチャ設計書が戻れば次の間隔で続く |
| `plan_epic.write_tick` | 計画が実行中でない | 止めた・中止した・完了した | 読み直した計画の状態 | 何も書かずに返す | 無し | 無し |
| `plan_epic.write_tick` | 書く前に計画が動いた | 同時の書き込み | `store.append_events` の番号の食い違い | 呼び出し元へ返す | Durable Step の失敗として記録される | トランザクションごと戻る。冪等なので次のアクションで再実行できる |
| `plan_run._perform` | エピックのアクションが例外で落ちた | `tick` の中の予期しない例外 | Durable Step | 呼び出し元へ返す | 既存の Durable Workflow の失敗の扱い（`ndf lanes run` の結果） | 再開すると同じアクションから再実行する |
| `lane.take_notices` | 書く前にレーンのワークフロー状態が動いた | 同時の書き込み | `checked_write` | 呼び出し元へ返す | 呼び手の既存の再実行 | トランザクションごと戻る |
| `lane.sprint_notices` | 同上 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |
| `command._prepare_new` | 最初のスプリントの組み合わせが違う | `--scale epic-first` でモードが `standard` でない・進め方が `normal` でない | 引数の比較 | 止める（`model.Refused`） | `refused`・終了コード 1。`summary` に渡したモードと進め方 | ワークフロー状態を作っていない |
| `command._prepare_new` | `--scale-reason` だけがある | 引数の組み合わせの誤り | 引数の比較 | 止める（`definition.WorkflowDefError`） | 今のコンテキストの誤りと同じ出力・終了コード | 同上 |
| `progress_run._cmd_next` | 変更通知の読み書きが失敗した | DB の同時の書き込み | `retrying_transaction` が尽きた | 呼び出し元へ返す | 今の DB の失敗と同じ出力 | `next` の本体は先に終わっている。変更通知は未読のまま残り、次の `next`・`wait` で受ける |
| `progress_run._cmd_wait` | 同上 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |

## 処理の手順

**起動前チェック（`plan.epic_gate`）。** 上から順に当て、止める行に当たったらそこで返す。止めない行は指摘を集めて続ける。

| 順 | 当てるもの | 指摘 | 止めるか |
| --- | --- | --- | --- |
| 1 | `base_ref` でベースブランチが決まるか | `missing-foundation`（詳細「ベースブランチが決まらない」） | 止める |
| 2 | `fetch_base` で取得し直せるか | `fetch-failed` | 止める |
| 3 | `read_at` で取得したコミットにアーキテクチャ設計書があるか | `missing-foundation` | 止める |
| 4 | `shape_errors` | `empty-section`・`shape` | 続ける |
| 5 | `unknown_done`（計画の `epic.done` の番号） | `unknown-done` | 続ける |
| 6 | `ci_files` が名指すファイルを `read_paths_at` で読み、`ci_check_missing` | `missing-ci-check` | 続ける |

指摘が 0 件なら、3 で読んだ blob の識別子を返す。`ndf epic check` は 1・2・3・5・6 を当てない（計画の設定ファイルと CI の定義を読まず、渡されたファイルを読む。設計文書の決定 9）。

**計画を完了にしてよいか（`rules.plan_complete`）。** 完了条件待ちのリクエストを答えていないリクエストに数えない（設計文書の I5）。取得の失敗・アーキテクチャ設計書に無い番号・消せない判定の作業ディレクトリのリクエストは数える。過去の判定で真になっても、Durable Workflow の `Complete` は全行が終わった後の最新の回が判定し直せたときだけ当てる（`run_decide._after_observe`）。

| 計画の `epic` | 全行が終わった | 完了条件待ちのリクエストのほかに開いたリクエスト | 完了条件の残り | 結果 |
| --- | --- | --- | --- | --- |
| 無し | いいえ | どれでも | — | 偽 |
| 無し | はい | ある | — | 偽 |
| 無し | はい | 無い | — | 真（今と同じ） |
| ある | いいえ | どれでも | どれでも | 偽 |
| ある | はい | ある | どれでも | 偽 |
| ある | はい | 無い | ある（未判定とアーキテクチャ設計書に無い番号を含む） | 偽 |
| ある | はい | 無い | 無い | 真 |

**計画の終了判定の状態（`rules.epic_state`）。** `ndf lanes status` の `metrics.epic_state` と、Durable Workflow の完了条件待ちのリクエストの開閉が、この 1 つの関数を読む。

| 計画の状態 | `plan_complete` | 全行が終わり、完了条件待ちのリクエストのほかに開いたリクエストが無い | 完了条件の残り | 結果 |
| --- | --- | --- | --- | --- |
| 完了 | どれでも | どれでも | どれでも | `done` |
| 実行中 | 真 | はい | 無い | `done`（ループの次の 1 回で `Complete` が完了にする。全行が終わった後の最新の回が判定し直せていなければ、`Complete` は判定し直せた回まで待つ。`_after_observe`） |
| 実行中 | 偽 | はい | ある | `waiting_done` |
| 実行中 | 偽 | いいえ | どれでも | `running` |
| 止めた・中止した | どれでも | どれでも | どれでも | `running`（終了判定の外。計画の状態は `status` の既存のフィールドが出す） |

**エピックのアクションを実行するか（`run_decide._epic_due`）。** スナップショットの計画が実行中で、`epic` を持つときだけ当てる。1 回のアクションは、取得し直し・比較・判定を判定のコミット 1 つで行う（設計文書の「ベースブランチの取得と判定のリビジョン」）。

| 最後のアクションが `EpicTick` | `snap.rows_finished` が `state.epic_rows_seen` と違う | `state.epic_slept` が `policy.epic_every` 以上 | 結果 |
| --- | --- | --- | --- |
| はい | どれでも | どれでも | None（同じループの 1 回で 2 回実行しない） |
| いいえ | はい | どれでも | `EpicTick`（行が終わった。起動の直後は `epic_rows_seen` が `-1` なので必ず当たる） |
| いいえ | いいえ | はい | `EpicTick`（間隔が来た。完了条件待ちの間の再判定もこの行） |
| いいえ | いいえ | いいえ | None |

取得と GitHub の読み取りは、行が終わるたびと間隔ごとに 1 回だけ行う。間隔の間に毎周の比較はしない（取得し直さずに比べると、設計文書の I9 が求めるリビジョンを読まない）。

**`plan_epic.write_tick` が書くもの。** 読み直した計画の状態が実行中のときだけ書く。上から順に、同じトランザクションで書く。

| 順 | 条件 | 書くイベント |
| --- | --- | --- |
| 1 | `got.fetch_failed` が真で、`fetch_failures + 1` が `fetch_limit` を超え、`epic_key` が `fetch-failed` のリクエストが開いていない | `request_opened`（`epic_key` が `fetch-failed`、理由に続けて失敗した回数と最後のエラー）。この行に当たってもほかの行は書かない |
| 2 | `got.fetch_failed` が真で、1 に当たらない | 何も書かない（ほかの行も書かない） |
| 3 | 取得できて、`epic_key` が `fetch-failed` のリクエストが開いている | `request_closed`（理由「ベースブランチを取得できた」） |
| 4 | 判定のコミットにアーキテクチャ設計書が無い | 何も書かない（5 から 9 を書かない。`status` の項目は前の判定のまま） |
| 5 | blob の識別子が比較元と違い、`changed_sections` が 1 つ以上 | `foundation_noticed`（`from_rev`・`to_rev`・`sections`・`places`・`lanes`。宛先が 0 本でも、比較元を進めるために `lanes` を空で書く） |
| 6 | blob の識別子が比較元と違い、`changed_sections` が 0（検証方法の列だけの変更） | 書かない。比較元は進めない |
| 7 | 番号の判定（`met`・`detail`）が最後の `done_checked` と違う | 番号ごとに `done_checked`（`commit` は判定のコミット。`commit` だけの違いでは書かない） |
| 8 | `got.unknown` が空でなく、`epic_key` が `unknown-done:<番号の並び>` のリクエストがまだ開いたことが無い | `epic_key` が `unknown-done:` で始まる開いたリクエストを閉じ、`request_opened`（`epic_key` が `unknown-done:<番号の並び>`）。`got.unknown` が空で、`epic_key` が `unknown-done:` で始まるリクエストが開いていれば `request_closed`（理由「アーキテクチャ設計書に無い番号がなくなった」） |
| 9 | 5〜8 を書いた後の計画で `rules.epic_state` が `waiting_done` になり、`epic_key` が残りの並びの `epic-done:<番号の並び>` のリクエストがまだ開いたことが無い | 開いている完了条件待ちのリクエストを閉じ、`request_opened`（`epic_key`・`kind: epic_done_wait`・`remaining`・理由に番号の並び） |
| 10 | 5〜8 を書いた後の計画で `rules.epic_state` が `running` になり（オーガナイザーがレーンか行を足した）、完了条件待ちのリクエストが開いている | `request_closed`（理由「レーンか行が足された」） |
| 11 | 残りが無い | 何も書かない（ループの次の 1 回で `Complete` がリクエストを閉じて完了にする） |
| 12 | 取得できて、`got.workdir_left` があり、`epic_key` が `workdir-left` のリクエストが開いていない | `request_opened`（`epic_key` が `workdir-left`、理由に配置先と「`--force` を付けない削除が断られた」）。`got.workdir_left` が無く、`epic_key` が `workdir-left` のリクエストが開いていれば `request_closed`（理由「判定の作業ディレクトリを作り直せた」） |

リクエストのキーは `plan.open_request` が振る `request-<連番>` で、呼び手は渡せない。そのため、エピックのリクエストの種類と重複は、既存の停止の通知（`run.write_report` が `NOTICE_FIELDS` の値を中身に入れる形）と同じく、中身のフィールド `epic_key` で判定する。「開いている」は開いたリクエストの中身の `epic_key` が一致すること、「開いたことが無い」は計画のスナップショットのリクエスト（閉じたものを含む）の中身にその `epic_key` が無いことである。オーガナイザーが閉じた `epic_key` では開き直さない（12 の `workdir-left` は、閉じた後に再び消せなくなったときに開き直せるよう、開いているかだけで判定する）。`epic_key` の頭（`epic-done:`・`unknown-done:`・`fetch-failed`・`workdir-left`）は `types.EPIC_KEYS` が持つ。

**比較元のリビジョン。** 最後の `foundation_noticed` の `to_rev` である。無ければ最初の `plan_started` の `foundation_rev` を使う（設計文書の「計画のワークフロー状態へ足すイベントと値」）。6 の行で比較元を進めないため、検証方法だけを書き換えた変更が続く間は、毎回 2 つのリビジョンを読んで比べる。

**検証方法（`verify.verify_one`）。**

| 指定 | 当てる対象 | 食い違いとするとき |
| --- | --- | --- |
| `exists: <glob>` | glob に当たる追跡中のファイル | コントラクトの行の名前が、どのファイルにも文字どおり現れない。当たるファイルが 0 件 |
| `schema: <glob>` | glob に当たる追跡中の JSON のファイル | 1 つでも正本のスキーマに通らない。当たるファイルが 0 件 |
| `generated: <コマンド>` | 正本のファイル | コマンドの標準出力が正本の中身と 1 字でも違う。終了コードが 0 でない。上限に当たる |
| `—` | 無し | 検証しない |

**規模の記録（`command._prepare_new`）。**

| `--scale` | モード | 進め方 | 結果 |
| --- | --- | --- | --- |
| 無し | どれでも | どれでも | 今と同じ（コンテキストに `scale` を書かない） |
| `sprint`・`epic` | どれでも | どれでも | コンテキストに `scale` を書く |
| `epic-first` | `standard` | `normal` | コンテキストに `scale` を書く |
| `epic-first` | `standard` 以外 | どれでも | `model.Refused` |
| `epic-first` | `standard` | `normal` 以外 | `model.Refused` |

`--scale-reason` を省いたときの理由は `理由の記載無し` とする。`--scale` 無しで `--scale-reason` だけを渡すと誤りにする。

- 繰り返しの終わる条件: Durable Workflow のループは今と同じく計画の終わりで止まる。エピックのアクションは終わりの条件を変えず、完了の判定（`rules.plan_complete`）に残りが無いことを足すだけである。`verify.done_verdicts` と `verify.verify_rows` は渡した行の数だけ回る
- 計算量の注意: エピックのアクションは行が終わるたびと間隔ごとに 1 回だけ動き、そのたびに `git fetch` を 1 回、`git rev-parse` を 1 回実行する。識別子が比較元と違うときだけ 2 つのリビジョンを読む。判定の作業ディレクトリは、計画の `done` の行に `command:` があるときだけ切り出す。`verify.done_verdicts` は Issue の状態を `GitHub.nodes` で 1 回にまとめて読む（`github.graphql-batch` の件数ごと）。ループの中で GitHub を読まない（コードスメルの規則 `NDF201`）。`exists:` の検証は glob に当たるファイルを 1 回ずつだけ読む

## 内部のデータと定数

| 名前 | 種類 | 配置先 | 既定値 |
| --- | --- | --- | --- |
| アーキテクチャ設計書のマーカー | 定数 | `contracts/epic/foundation.schema.json` の `marker` | `<!-- ndf: epic-foundation -->` |
| セクションの一覧（名前・必須か・表の列） | 定数 | 同じコントラクトの `sections` | 設計文書の「アーキテクチャ設計書の形」の 12 行 |
| 検証の種類 | 定数 | 同じコントラクトの `verify_kinds` | `exists`・`schema`・`generated`・`none` |
| 確認方法の種類 | 定数 | 同じコントラクトの `check_kinds` | `issues`・`command` |
| 触るファイルの行の頭 | 定数 | 同じコントラクトの `places_prefix` | `触るファイル:` |
| 変更通知の比較から除く列 | 定数 | 同じコントラクトの `verify_column` | `検証方法` |
| CI の定義に現れるべき文字列と、それを名指すセクション | 定数 | 同じコントラクトの `ci_command`・`ci_section` | `ndf epic check`・`テストと検証の基盤` |
| エピックのアクションの間隔 | 設定値 | `contracts/limits.json` の `lanes.epic-recheck` | `seconds: 600` |
| ベースブランチの取得が続けて失敗してよい回数 | 設定値 | `contracts/limits.json` の `lanes.epic-fetch-failures` | `count: 3`（4 回続けて失敗した回にリクエスト `fetch-failed` を開く） |
| 検証と確認方法のコマンドの上限 | 設定値 | `contracts/limits.json` の `epic.command` | `per_call: 300` |
| 完了条件待ちのリクエストの種類 | 定数 | `progress/lane/types.py` の `DONE_WAIT` | `epic_done_wait` |
| エピックのリクエストの中身の `epic_key` の頭 | 定数 | `progress/lane/types.py` の `EPIC_KEYS` | `epic-done:`・`unknown-done:`・`fetch-failed`・`workdir-left` |
| 判定の作業ディレクトリの名前 | 定数 | `progress/epic/foundation.py` | `epic-<計画の ID>`（`place.work_root` の下） |
| `Foundation`・`Section`・`ContractRow`・`DoneRow`・`Finding` | 型（不変の値） | `progress/epic/foundation_decide.py` | — |
| `DoneVerdict` | 型 | `progress/epic/verify.py` | `id`・`met`・`detail`。`commit` は `write_tick` が判定のコミットを添えて `done_checked` に書く |
| `EpicDecl`・`Notice` | 型 | `progress/lane/types.py` | — |
| `PlanView` の足すフィールド | 型 | `progress/lane/types.py` | `epic: EpicDecl \| None = None`・`foundation_rev: str \| None = None`・`done: dict[str, DoneVerdict] = {}`・`notices: list[Notice] = []` |
| `TickInput` | 型 | `progress/plan/epic.py` | `fetch_failed: bool`・`fetch_error: str \| None`・`commit: str \| None`・`foundation: Foundation \| None`・`blob: str \| None`・`base: Foundation \| None`（比較元）・`verdicts: list[DoneVerdict]`・`unknown: list[str]`・`error: str \| None`・`workdir_left: str \| None`（`--force` を付けない削除で消せなかった判定の作業ディレクトリ）・`judged_all: bool` |
| `EpicTick` | アクション | `progress/plan/run_decide.py` | `rows_finished: int`・`fetch_failures: int` |
| `Snapshot` の足すフィールド | 型 | `progress/plan/run_decide.py` | `epic: bool = False`・`rows_finished: int = 0`（前の Durable Workflow の記録には無いので既定で補って読む） |
| `RunState` の足すフィールド | 型のフィールド | `progress/plan/run_decide.py` | `epic_slept: float = 0.0`・`epic_rows_seen: int = -1`・`epic_fetch_failures: int = 0`・`epic_judged: bool = False`（最後の `EpicTick` が計画の番号をすべて判定し直せたか） |
| `Policy` の足すフィールド | 型のフィールド | `progress/plan/run_decide.py` | `epic_every`（`lanes.epic-recheck` の `seconds`）・`epic_fetch_limit`（`lanes.epic-fetch-failures` の `count`）。どちらも `plan_run.policy` が読む |

**セクションの名前と列はコントラクト 1 か所だけが持つ。** `foundation_decide` はセクションの名前を定数にコピーせず、引数の `spec` から読む。ガイド（`epic-foundation.md`）のセクションの表は、`ndf check --write-spec` の生成の仕組みでコントラクトから描く。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| Durable Workflow のエピックのアクションと、オーガナイザーのサブコマンド（`lane`・`row add`・`request` など） | 計画のワークフロー状態 | 既存のとおり、書く側が `lock_plan` を取り、`store.append_events` が最後の番号を確かめる。食い違えばトランザクションごと戻る | `write_tick` は最新の記録と比べて違うときだけ書くため、同じ入力で再実行しても 2 回目は何も書かない。途中で落ちても、再開した Durable Workflow が同じアクションから再実行する |
| Durable Workflow の `Complete` と、オーガナイザーの行の追加 | 計画のワークフロー状態 | `complete_plan` は同じトランザクションで計画を読み直し、`rules.plan_complete` が真のときだけリクエストを閉じて完了を書く | 偽なら何も書かない。ループの次の 1 回で判定し直す |
| 1 つのレーンの `ndf progress next`・`wait` と `ndf lanes row resumed` | レーンのワークフロー状態 | 既存の `checked_write`（最後の番号の確かめ）と `retrying_transaction` | `notice_read` は読んだ最後の番号を持つ。同じ番号を 2 回書いても、未読の判定（最後の `notice_read` より後）は変わらない |
| Durable Workflow の `gather`（git の取得と読み取り）と、同じマシンの `ndf lanes start` の `epic_gate` やほかのセッションの `git fetch` | メインディレクトリの `.git` のリモート追跡ブランチ | 取らない。`git fetch` は参照の更新を git 自身の lock で守る。`FETCH_HEAD` を読まずにリモート追跡ブランチを読むため、ほかのセッションの取得と取り合わない（`Git.fetch_branch`） | lock で落ちた側は取得の失敗として扱う。Durable Workflow は次の回で取り直し、`ndf lanes start` は `fetch-failed` で止まり、再実行できる |
| 確認方法のコマンド（`command:`）と、メインディレクトリでの他の作業 | 無し（コマンドは判定の作業ディレクトリで動く） | 取らない。判定の作業ディレクトリは計画ごとに 1 つで、同じ計画の Durable Workflow は 1 つだけ動く | コマンドがファイルを書き換えても、判定の作業ディレクトリと一緒に消える。リポジトリの外を書き換えるコマンドは止められず、設計文書の「未確認のまま残ること」に残す |
| Durable Workflow の中断と、判定の作業ディレクトリ | 判定の作業ディレクトリ | 取らない | 判定の途中で落ちると作業ディレクトリが残る。再開した回の `foundation.checkout` が作る前に `--force` を付けずに消すため、残りは 1 つを超えない。消せなければリクエスト `workdir-left` で人へ戻す |
| Durable Workflow の再開と、取得の失敗の回数 | `RunState.epic_fetch_failures` | 取らない | 再開すると回数は 0 から数え直し、`epic_judged` は偽から始める（再開の後の最初の `EpicTick` が判定し直すまで完了にしない）。開いているリクエスト `fetch-failed` は、取得できた回に閉じる |

GitHub の読み取り（`states`）と DB の書き込みを同じトランザクションに入れない。`plan_epic.tick` は `gather`（DB の外の読み取り）の後に `write_tick`（DB の書き込み）を実行する。既存の `observe` が生死の材料をトランザクションの外で読む順と同じである。

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `epic_cli.main` | 結果 JSON（`items` に指摘、`metrics.files`・`metrics.findings`）。指摘は 1 件 1 行で標準エラーにも出す | 標準出力の最後の 1 行・標準エラー | コマンドの標準出力の全文（`generated:` の食い違いは最初に違う行の番号だけを出す） |
| `plan_cli._dispatch` | 起動前チェックの指摘 | 結果 JSON の `items` | アーキテクチャ設計書の本文 |
| `plan.status_of` | 完了条件の項目ごとの行（項目の文を含む）・`metrics.epic_done`・`metrics.epic_state` | 結果 JSON | 確認方法のコマンドの出力 |
| `plan_epic.write_tick` | `done_checked`・`foundation_noticed`・完了条件待ちのリクエスト | 計画のワークフロー状態のイベント | 確認方法のコマンドの出力の全文（`detail` は終了コードと、全文を `redact` に通した後の最後の 1 行の 200 字まで） |
| `plan_epic.tick` | 読めなかった理由（`error`）・取得の失敗（`fetch_error`）・判定の作業ディレクトリを作れない・消せない理由 | Durable Workflow の `run.log` に 1 行（既存の `RUN_LOG`） | 環境変数の値・トークン・git の認証の情報（`fetch_error` は git の標準エラーの全文を `redact` に通してから最後の 1 行を取り出す） |
| `lane.take_notices` | 既読フラグ | レーンのワークフロー状態のイベント `notice_read` | 無し |
| `plan_cli._row_start`・`_row_resumed`・`progress_run._cmd_next`・`_cmd_wait` | `notices`（セクション・触るファイル・前後のリビジョン・イベントの番号） | 結果 JSON | アーキテクチャ設計書の本文 |
| `progress_run.issue_section` | `規模:` の行 | Issue の本文の `## 進行` | 無し |

**確認方法のコマンドの出力は秘密を含みうる。** `verify.done_verdicts` と `verify.verify_one` は、コマンドの出力の全文を先に既存のマスキングの処理 `ndf.adapters.run_log.redact`（秘密の環境変数の値とトークンの形を伏せる）へ通し、伏せた後の文から最後の 1 行を取り出して 200 字で切り、`detail` に載せる。切ってから伏せると、切れ目をまたぐ秘密の値が一部だけ残り、値の全体との突き合わせに当たらずに `detail` へ漏れるため、伏せる処理は必ず切る前に当てる。`fetch_error` も同じ順で、git の標準エラーの全文を伏せてから最後の 1 行を取り出す。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `markdown.tables` | `ndf.adapters.markdown` の `atx_headings`・`block_kinds` | `grep -nE '^def ' src/ndf/adapters/markdown.py` | 表を返す関数が無い。同じ markdown-it のトークンから作り、正規表現で `\|` を数えない（モジュールの冒頭のルール） |
| `foundation_decide.parse` | `ndf.domains.records.glossary` の表の読み出し、NDF 10 の `scripts/lib/md.py` の `tables_in` | `grep -rn 'def tables\|tables_in' src/ndf` | `ndf.domains` は層が上で読めない。10 の `md.py` は ndf-next に無い。`markdown.tables` の結果を受けて作る |
| `foundation_decide.shape_errors` | 設計のチェック（`design-survey.py` の `detail_section`） | `grep -rn 'detail_section' plugins/ndf-next/src` | ndf-next に無い。セクションの空の定義（HTML のコメントと空行のほか）が違うためコピーしない |
| `foundation_decide.unknown_done` | 無し | `grep -rn 'unknown' src/ndf/progress/lane/rules.py` | 番号の突き合わせの部品は無い。集合の差で書く |
| `foundation_decide.ci_files` | `foundation_decide.places_of` が読む `触るファイル:` の行 | この文書の新設 | 同じ行の読み出しを使う |
| `foundation_decide.ci_check_missing` | 無し | `grep -rn 'ndf epic check' src/ndf` | CI の定義の中の文字列を確かめる部品は無い。部分文字列の一致で書く |
| `foundation_decide.changed_sections` | `difflib` | 標準ライブラリ | 使わない。セクションの単位で本文の行の並びを比べるだけで足りる |
| `foundation_decide.places_of` | `rules.touch_hit` | `sed -n 87,110p src/ndf/progress/lane/rules.py` | 重なりの判定は `rules.notice_targets` が `touch_hit` で行う。ここは場所を集めるだけ |
| `foundation_decide.template` | `contracts/templates/` のテンプレート | `ls contracts/templates` | セクションの一覧をコントラクトから描くため、固定のテンプレートのファイルを置かない |
| `foundation_decide.parse_verify` | 無し | `grep -rn 'generated' src/ndf/checks/spec_docs.py` | 規約の生成と検証（`spec_docs`）は ndf-next 自身の生成物だけを見る。利用者のコマンドを受ける部品は無い |
| `foundation_decide.parse_check` | 無し | 同上 | 同上 |
| `foundation.spec` | `ndf.contracts` のコントラクトの読み出し（`read_doc` が使うもの） | `sed -n 52,64p src/ndf/progress/plan/__init__.py` | 使う |
| `foundation.read_text` | `markdown.atx_headings`・`markdown.block_kinds`・`markdown.tables` | 同上の grep | 使う |
| `foundation.read_file` | `pathlib.Path.read_text` | 標準ライブラリ | 使う |
| `foundation.base_ref` | `ndf.core.route.default_base`、`ndf.domains.worktree.repo.base_branch` | `grep -rn 'def default_base\|def base_branch' src/ndf` | `default_base` を使う。`ndf.domains.worktree` は層が上で読めない |
| `foundation.fetch_base` | `ndf.adapters.git.Git.fetch_branch`・`Git.rev_parse` | `sed -n 263,275p src/ndf/adapters/git.py` | 使う。`fetch_branch` はリモート追跡ブランチへ取り込み、`FETCH_HEAD` を読まない |
| `foundation.read_at` | `ndf.domains.records.mvv.decl_at`（`git show <ref>:<path>`） | `sed -n 66,76p src/ndf/domains/records/mvv.py` | 同じ形を `Git.run` で書く。`mvv` は層が上で読めない |
| `foundation.read_paths_at` | `Git.run` | `grep -nE 'def ' src/ndf/adapters/git.py` | 使う（`run("cat-file", "-p", "<コミット>:<パス>")` をパスごとに） |
| `foundation.checkout` | `ndf.domains.crossrefactor.workspace._add`（`worktree add --detach`）、`ndf.core.converge.place.work_root` | `sed -n 34,37p src/ndf/core/converge/place.py`・`sed -n 63,69p src/ndf/domains/crossrefactor/workspace.py` | `work_root` を配置先に使う。`crossrefactor.workspace` は層が上で読めず、lock を掛けて残す前提なので使わない。`Git.run` で `worktree add --detach`・`worktree remove`・`worktree prune` を打つ。`--force` は付けない（共通原則 C4。消せなければリクエスト `workdir-left` で人へ戻す） |
| `foundation.read_blob` | `Git.run` | 同上 | 使う（`run("cat-file", "-p", <識別子>)`） |
| `foundation.marked_files` | `ndf.adapters.git.Git.run` | 同上 | 使う（`run("ls-files", "*.md")`） |
| `verify.verify_rows` | jsonschema（依存済み）、`ndf.contracts.schema_errors` | `grep -rn 'def schema_errors' src/ndf/contracts` | `schema:` は jsonschema を直に使う（利用者のスキーマで、NDF のコントラクトの配置先に無い） |
| `verify.verify_one` | 同上 | 同上 | 同上 |
| `verify.done_verdicts` | `ndf.adapters.github.GitHub.nodes`（番号ごとの読み取りをまとめる）、`ndf.adapters.run_log.redact` | `grep -n 'def nodes' src/ndf/adapters/github.py`・`grep -rn 'def redact' src/ndf` | 使う（`nodes(numbers, "issue", "state")` と、`detail` のマスキング） |
| `verify.run_command` | `ndf.process_group`、`subprocess.run` | `ls src/ndf/process_group.py` | `process_group` の子のプロセスを止める処理を使い、上限で子を残さない |
| `epic_cli.main` | `ndf.progress.command.run_main` | `sed -n 542,556p src/ndf/progress/command.py` | 使えない。`run_main` はワークフロー状態の DB を開く前提の停止の表を持つ。`ndf check` の `main` と同じく結果のコントラクトへ直に出す |
| `epic_cli._cmd_init` | `ndf.adapters.paths` の関数 | `grep -n 'def ' src/ndf/adapters/paths.py` | パスの一致の関数だけで、書き出しの部品は無い。一時ファイルに書いて `os.replace` で置き換える |
| `epic_cli._cmd_check` | `ndf.checks.run` の結果の組み立て | `sed -n 1,40p src/ndf/checks/README.md` | 結果の形（`items`・`metrics.findings`・`check_failed`）を同じにする。関数は共有しない（`ndf.checks` はプラグイン自身のチェック） |
| `plan.epic_gate` | `foundation.read_at`・`foundation_decide.shape_errors`・`unknown_done` | この文書の新設 | 使う |
| `plan.epic_items` | `plan._plan_metrics` | `sed -n 416,431p src/ndf/progress/plan/__init__.py` | 足す先として使う。行は別の関数で作る |
| `plan_epic.tick` | `plan_run.observe`（読み取りをトランザクションの外に置く Durable Step） | `sed -n 226,232p src/ndf/progress/plan/run.py` | 同じ形で書く |
| `plan_epic.gather` | `foundation`・`verify` | この文書の新設 | 使う |
| `plan_epic.write_tick` | `plan.open_request`・`plan.close_request`（リクエストの開閉） | `grep -n 'def open_request\|def close_request' src/ndf/progress/plan/__init__.py` | 使う。書き手は `model.WORKFLOW_ACTOR`（既存の停止の通知と同じ） |
| `run_decide._epic_due` | `run_decide._reportable`・`_withdrawable` | `sed -n 399,416p src/ndf/progress/plan/run_decide.py` | 同じ形の判断の関数として足す |
| `lane.take_notices` | `lane.checked_write` | `sed -n 151,170p src/ndf/progress/lane/__init__.py` | 使う |
| `lane.sprint_notices` | `lane.sprint_row` | `sed -n 273,300p src/ndf/progress/lane/__init__.py` | 使う |
| `fold.last_notice_read` | `fold.fold_lane` | `grep -n 'def ' src/ndf/progress/lane/fold.py` | `fold_lane` は行の単位で畳む。表の単位のイベントを読む関数として足す |
| `rules.rows_done` | `rules.plan_complete` の前半 | `sed -n 250,253p src/ndf/progress/lane/rules.py` | 切り出して `plan_complete` から呼ぶ |
| `rules.done_remaining` | 無し | 同上 | スナップショットの `done` と `epic.done` から作る |
| `rules.epic_state` | `rules.plan_complete`・`rules.rows_done` | 同上 | 使う。完了してよいかの規則を別に書かない |
| `rules.notice_targets` | `rules.touch_hit`・`rules.lane_state` | `sed -n 87,110p src/ndf/progress/lane/rules.py` | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `markdown.tables` | 見出しと 2 行の表を、行番号つきで返す | 見出しだけの表は本文 0 行。引用の中の表は返さない | 区切りの行の無い `\|` の並びは表にしない |
| `foundation_decide.parse` | 12 セクションのアーキテクチャ設計書からセクション・コントラクトの行・完了条件の行を作る | コメントだけのセクションは本文 0 行。コードブロックの中の `##` はセクションにしない | コントラクトに無い名前の `##` のセクションは値に残り、`shape_errors` が指摘しない（追加のセクションを許す） |
| `foundation_decide.shape_errors` | すべて埋めたアーキテクチャ設計書で 0 件 | 必須セクションが無い・空のとき `empty-section`。該当時のセクションが空でも指摘しない | 列の名前が違う・`#` が `D<数>` でない・確認方法が読めない行で `shape`（行の番号と列の名前を持つ） |
| `foundation_decide.unknown_done` | すべてある番号で 0 件 | 空の並びで 0 件 | 無い番号ごとに `unknown-done` |
| `foundation_decide.ci_files` | 名指したパスを返す | `触るファイル:` の行が無いセクションで空 | セクションが無いと空 |
| `foundation_decide.ci_check_missing` | どれかのファイルに `ndf epic check` があれば 0 件 | 2 つのうち 1 つにだけある | パスが 0 件・コミットに無いファイル・どれにも無いとき `missing-ci-check`（I11） |
| `foundation_decide.changed_sections` | 同じリビジョンで空 | 片方にだけあるセクションを含む。コントラクトの表の検証方法の列だけが違うと空（受け入れ条件 6） | 空白だけの違いも違いとする（本文の行を比べる） |
| `foundation_decide.places_of` | 表のセクションで、違う行の正本と検証方法のパスを返す | `触るファイル:` の行を持つ表の無いセクション | どちらも無いセクションで `**` |
| `foundation_decide.template` | 返した文の必須セクションがすべて `empty-section` になる | 名前に空白を含む | コントラクトのセクションが 0 件ならマーカーと H1 だけ |
| `foundation_decide.parse_verify` | 4 種類を読む | `—` | 知らない頭で None |
| `foundation_decide.parse_check` | `issues: #1 #2` と `command: make test` を読む | 番号 1 つ | 番号 0 個・空のコマンド・知らない頭で None |
| `foundation.spec` | コントラクトを読む | — | 壊れたコントラクトで `ContractError` |
| `foundation.read_text` | `parse` と同じ値 | 空の文 | — |
| `foundation.read_file` | 読む | — | 無いパスで `FoundationError` |
| `foundation.base_ref` | 設定ファイルの `base_branch` を使う | 設定ファイルが無く origin の既定ブランチを使う | どちらも無いとき `FoundationError` |
| `foundation.fetch_base` | 一時のリポジトリの origin で進めたコミットを、古いリモート追跡ブランチから取得し直して返す | — | origin が無いとき `FetchFailed` |
| `foundation.read_at` | コミットのアーキテクチャ設計書と識別子を返す | worktree にだけあるアーキテクチャ設計書で None（I9） | 参照が無いとき `FoundationError` |
| `foundation.read_paths_at` | 複数のファイルを読む | コミットに無いファイルで None | git でないとき `FoundationError` |
| `foundation.checkout` | 判定のコミットの作業ディレクトリを渡し、抜けた後に配置先も `git worktree list` の行も残らない | 前の回の作業ディレクトリが残っていても、変更が無ければ消して作り直す | ブロックの中で例外が出ても消す。追跡外のファイルが残ると `--force` を付けずに断られ、配置先を残して知らせる。前の回の残りが消せなければ `WorkdirLeft` |
| `foundation.read_blob` | 識別子から読む | — | 無い識別子で `FoundationError` |
| `foundation.marked_files` | マーカーを持つ追跡中のファイルだけを返す | 2 行目のマーカーは数えない | git でないとき `FoundationError` |
| `verify.verify_rows` | 食い違いの無い行で 0 件 | `—` の行は検証しない | 3 種類それぞれの食い違いで `contract-mismatch`（受け入れ条件 3） |
| `verify.verify_one` | `schema:` が通る | glob が 0 件で食い違い | 読めないスキーマ・失敗する生成のコマンド・上限 |
| `verify.done_verdicts` | 閉じた Issue と終了コード 0 で `met: true`。`command:` が `cwd` で動く | Issue の一部だけが閉じている。`cwd` が None なら `command:` の行を外す | `states` の例外を呼び手へ返す。コマンドの失敗は `met: false` |
| `verify.run_command` | 終了コードと出力を返す | 上限ちょうど | 無いコマンドで `127`。上限で `-1` と子が残らないこと |
| `epic_cli.main` | `check` が 0 件で終了コード 0 | マーカーを持つアーキテクチャ設計書が 0 本で 0 | 使い方の誤りで 2 |
| `epic_cli._cmd_init` | テンプレートを書く | 親のディレクトリが無い | 同名のファイルで `refused`・終了コード 1 |
| `epic_cli._cmd_check` | `--shape-only` で検証しない | 引数のパスとマーカーの両方 | 指摘で `check_failed`・1。読めないファイルで `config_broken`・2 |
| `plan.epic_gate` | `epic` の無い設定ファイルで `([], None)`（受け入れ条件 8） | すべて埋めたアーキテクチャ設計書で識別子を返す。リモート追跡ブランチが古くても、取得し直したコミットで通る | 空のセクション・無い番号・ベースブランチのリビジョンに無いアーキテクチャ設計書・取得の失敗・CI の定義に `ndf epic check` が無いときに指摘（受け入れ条件 2・I9・I10・I11） |
| `plan.plan_payload` | `epic` をコピーする | `epic` が無い | — |
| `plan.create_plan` | `plan_started` に `foundation_rev` | `foundation_rev` が None | 既存のルールの `Refused` |
| `plan.start_plan` | 止めた計画の起動でリビジョンを書き、比較元は最初のリビジョンのまま | — | 既存のルールの `Refused` |
| `plan.complete_plan` | 完了条件待ちのリクエストを閉じてから完了を書く | リクエストが無い | 残りがあれば何も書かない |
| `plan.epic_items` | 番号ごとの行と `{met, total}`。`text` は最後の判定のコミットの項目の文 | 判定の記録の無い番号で `met: null`、`text` は起動時点のリビジョンから読む（受け入れ条件 5） | 記録のコミットが読めないと `text` が null |
| `plan.status_of` | `epic` の行が `items` に、`metrics.epic_state` が載る | `epic` の無い計画で今と同じ | — |
| `plan_cli._dispatch` | 指摘 0 件で今と同じ | — | 指摘でワークフロー状態を 1 つも書かず `check_failed` |
| `plan_cli._row_start` | 未読の変更通知を `notices` に載せ既読フラグを書く | 変更通知が無ければ既読フラグを書かない | 始められない行でマーカーも書かない |
| `plan_cli._row_resumed` | 同上（I8） | 同上 | — |
| `plan_epic.tick` | 取得し直したコミットで、リビジョンの違いの変更通知と判定を書く | 取得の失敗で `fetch_failed` が真 | `gather` の失敗で読めた分だけ書く |
| `plan_epic.gather` | 2 つのリビジョンと判定を読み、`command:` を判定の作業ディレクトリで動かす（メインディレクトリのチェックアウトを別のコミットにしても結果が同じ） | 比較元が無い（最初の起動）。`command:` の行が無ければ切り出さない | 比較元の blob が無いとき空のアーキテクチャ設計書として比べる。アーキテクチャ設計書に無い番号を `met: false` と `unknown` に出す（I10） |
| `plan_epic.write_tick` | 書くものの表の 12 行のとおり書く（受け入れ条件 4・6）。重複の判定はリクエストの中身の `epic_key` で行い、リクエストのキーは `request-<連番>` | 同じ入力の 2 回目は何も書かない。検証方法の列だけの変更で `foundation_noticed` を書かず比較元を進めない。取得の失敗がちょうど上限の回数では開かない | 実行中でない計画で何も書かない。閉じた `epic_key` で開き直さない |
| `plan_run._perform` | `EpicTick` を `tick` へ渡す | — | — |
| `plan_run.snapshot_of` | `epic`・`rows_finished` を載せる | `epic` の無い計画 | — |
| `run_decide._epic_due` | 判定表の 4 行。行が 1 つ終わると間隔の前でも当たる | `epic_slept` がちょうど間隔。起動の直後 | `epic: false` で None |
| `run_decide._after_observe` | `epic` のスナップショットでアクションを挟む | `epic: false` で今と同じアクション | 全行が終わった後の最新の `EpicTick` の `judged_all` が偽なら、過去の判定で `plan_complete` が真でも `Complete` を返さない（設計文書の I5） |
| `run_decide.decide` | `Sleep` で `epic_slept` が増え、`EpicTick` の後に 0、`epic_rows_seen` が進む | 取得の失敗が続くと `epic_fetch_failures` が増え、取得できると 0。`epic_judged` が戻り値の `judged_all` に従う | — |
| `lane.take_notices` | 宛先のレーンだけが受ける（I7） | 2 回目は空 | — |
| `lane.sprint_notices` | 実行中の行のスプリントで受ける | レーンの外のスプリントで空 | — |
| `fold.last_notice_read` | 最後の番号 | 無ければ 0 | — |
| `fold._fold_plan_event` | 新しいイベントを畳む | この変更の前に書かれたイベント（フィールドが無い）を読める | — |
| `rules.rows_done` | 全行が完了か中止で真 | 行が 0 | — |
| `rules.done_remaining` | 満たしていない番号 | 未判定の番号を含む | `epic` が無ければ空 |
| `rules.plan_complete` | 判定表の 7 行 | 完了条件待ちのリクエストだけが開いている | `epic_key` が `fetch-failed`・`unknown-done:`・`workdir-left` のリクエストが開いていると偽 |
| `rules.epic_state` | 判定表の 5 行 | 全行が終わり完了条件待ちのリクエストだけが開いていて残りがあると `waiting_done` | `epic` が無いと None。Durable Workflow が完了にした計画で `done`（`waiting_done` のまま残らない） |
| `rules.notice_targets` | 重なるレーンだけ | `**` で終わっていないレーンすべて | 終わったレーンは選ばない |
| `command._prepare_new` | `scale` をコンテキストに書く（受け入れ条件 7） | `--scale` 無しで今と同じ | 判定表の断る 2 行。`--scale-reason` だけで誤り |
| `progress_run.issue_section` | `規模:` の行が `モード:` の次 | `scale` が無ければ今と同じ | — |
| `progress_run._cmd_next` | 実行中のレーンの `next` に `notices` が載り、次の `next` には出ない（受け入れ条件 6） | レーンの外で載らない | — |
| `progress_run._cmd_wait` | 同上 | 同上 | — |
