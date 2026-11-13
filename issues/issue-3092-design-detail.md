# #3092 の詳細設計: 工程の進行表

設計文書の構成要素とデータ構造・入出力の契約を、ファイルと関数の粒度へ下ろした文書である。元にしたのは次の章である。

- [issue-3092-design.md](issue-3092-design.md) の「構成要素」
- [issue-3092-design-runtime.md](issue-3092-design-runtime.md) の「構成要素の図と配置」「データ構造」「入出力の契約」

決定の番号（決定 <番号>）・不変条件（I<番号>）・事象の種類は設計文書の番号と名前を指す。語の定義は設計文書の「語と参照先」と「用語」にある。

この文書の関数名とモジュール名は、コードの識別子である。説明の文では設計文書の語（進行表のモデル・進行表の DB など）を使う。

| 語 | この文書での意味 |
| --- | --- |
| 接続 | 進行表の DB への SQLAlchemy の接続 1 本（`Connection`）。1 つのトランザクションを持つ |
| 事象の下書き | 追記する前の進行の事象 1 件（番号と時刻を持たない値） |
| 定義のスナップショット | 設計文書の「定義の改訂」の表の `definition` の列に入る値。この文書では `Snapshot` の型で持つ |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は中の関数を取り除くもの）:

```text
plugins/ndf/
├── contracts/
│   ├── workflow/                               *
│   │   ├── serverless-workflow-1.0.3.schema.json *
│   │   ├── modes/{standard,light,operation,legacy-refactor,documentation}.json *
│   │   ├── stages/<工程の ID>.json             *
│   │   └── schemas/{report,stage-io,reshuffle}.schema.json *
│   ├── env/NDF_PROGRESS_DB.json                *
│   ├── entrypoints/plugins__ndf__scripts__progress.py.json *
│   ├── settings/project.json.json
│   └── index.md
├── pyproject.toml
├── scripts/
│   ├── progress.py                             *
│   ├── parallel.py
│   ├── supervise.py                            -
│   ├── sprint-state.py                         -
│   ├── lib/
│   │   ├── workflow_def.py                     *
│   │   ├── progress_model.py                   *
│   │   ├── progress_db.py                      *
│   │   ├── progress_run.py                     *
│   │   ├── progress_activities.py              *
│   │   ├── progress_projection.py              *
│   │   ├── reshuffle.py                        *
│   │   ├── durable.py
│   │   ├── deps.py
│   │   ├── README.md
│   │   ├── parallel_run.py / parallel_status.py / parallel_cli.py
│   │   └── parallel_lane_cli.py / parallel_ledger.py
│   ├── supervise_lib/
│   │   ├── sprint.py / queue_resume.py / sprint_close_steps.py   -
│   │   └── sprint_lane_close.py / new_args.py / templates.py     -
│   └── tests/test_{workflow_def,progress_*,reshuffle,parallel_tables}.py *
└── skills/（手順書の書き換えだけ）
scripts/check-ndf-contracts.py
scripts/contracts_lib/workflow_defs.py          *
```

**依存の向きは一方向で、輪が無い。** 下の層から順に次のとおりで、各層は上の行の層を読む。

1. 工程の定義（データ）
2. 定義の検査（`workflow_def`）
3. 進行表のモデル（`progress_model`。`workflow_def` の型だけを読む）
4. 進行表の DB（`progress_db`）
5. 進捗記録の生成・組み換え・アクティビティの関数
6. 進行表のワークフロー（`progress_run`）
7. エントリポイント（`progress.py`・`parallel.py`・耐久ワークフロー）

進行表の DB が進行表のモデルを読むのは、追記の関数（`append_events`）が書き込みの直前に書き手の表と不変条件の判定を呼ぶためである。設計の「構成要素の図」の「進行表のモデル → 進行表の DB」の矢印は、この形で満たす（判定はモデルが持ち、呼ぶのは DB の追記の関数だけ）。モデルを純粋な処理に保つため、モデルは DB も DBOS も import しない（決定 20）。DBOS を import するのは `lib/durable.py` だけである。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/contracts/workflow/serverless-workflow-1.0.3.schema.json` | 新設 | 借りた標準のスキーマ（取得元と sha256 を `$comment` に持つ） | 無し |
| `plugins/ndf/contracts/workflow/modes/standard.json` | 新設 | `standard` の工程の並び | `plugins/ndf/contracts/workflow/stages/` |
| `plugins/ndf/contracts/workflow/modes/light.json` | 新設 | `light` の工程の並び | `plugins/ndf/contracts/workflow/stages/` |
| `plugins/ndf/contracts/workflow/modes/operation.json` | 新設 | `operation` の工程の並び | `plugins/ndf/contracts/workflow/stages/` |
| `plugins/ndf/contracts/workflow/modes/legacy-refactor.json` | 新設 | `legacy-refactor` の工程の並び | `plugins/ndf/contracts/workflow/stages/` |
| `plugins/ndf/contracts/workflow/modes/documentation.json` | 新設 | `documentation` の工程の並び | `plugins/ndf/contracts/workflow/stages/` |
| `plugins/ndf/contracts/workflow/stages/` | 新設 | 工程ごとのアクティビティの並び・入出力規約の参照・`metadata.ndf` | `plugins/ndf/contracts/workflow/schemas/` |
| `plugins/ndf/contracts/workflow/schemas/` | 新設 | 統一 JSON レポート・工程の入出力・組み換えの出力の JSON Schema | 無し |
| `plugins/ndf/contracts/env/NDF_PROGRESS_DB.json` | 新設 | 環境変数 `NDF_PROGRESS_DB` の契約 | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__progress.py.json` | 新設 | `progress.py` の引数・終了コード・読む環境変数の契約 | 無し |
| `plugins/ndf/contracts/settings/project.json.json` | 変更 | `workflow.stages.<工程>` のキーを足す | 無し |
| `plugins/ndf/contracts/index.md` | 変更 | 新しい契約の行を足す | 無し |
| `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | 定義の読み込みと検査（I7〜I10）・有効の解決（決定 8）・入出力の検査 | `jsonschema`・工程の定義 |
| `plugins/ndf/scripts/lib/progress_model.py` | 新設 | 畳み込み・次の手・書き手の表・不変条件の判定・取り込みの対応。純粋な処理 | `plugins/ndf/scripts/lib/workflow_def.py`（型だけ） |
| `plugins/ndf/scripts/lib/progress_db.py` | 新設 | 接続・表の作成・追記・読み込み・レーンの行を終える関数・実行の鍵の排他 | `plugins/ndf/scripts/lib/progress_model.py`・SQLAlchemy Core・`lib/decl.py`・`lib/locks.py` |
| `plugins/ndf/scripts/lib/progress_run.py` | 新設 | 進行表のワークフローの殻と、`next` の起動・回復・古い実行の始末 | `plugins/ndf/scripts/lib/progress_db.py`・`plugins/ndf/scripts/lib/progress_model.py`・`plugins/ndf/scripts/lib/progress_activities.py`・`plugins/ndf/scripts/lib/durable.py` |
| `plugins/ndf/scripts/lib/progress_activities.py` | 新設 | `call` の名前 → アクティビティの関数 | `plugins/ndf/scripts/lib/progress_db.py`・`plugins/ndf/scripts/lib/progress_projection.py`・`plugins/ndf/scripts/lib/reshuffle.py`・`plugins/ndf/scripts/lib/durable.py`・`supervise.py`（子のプロセス） |
| `plugins/ndf/scripts/lib/progress_projection.py` | 新設 | 事象から課題の工程表・ボード・スプリント PR の「通した工程」を作って書く | `plugins/ndf/scripts/lib/progress_model.py`・`projects-sync.sh`・`pr-steps.py`（子のプロセス） |
| `plugins/ndf/scripts/lib/reshuffle.py` | 新設 | 組み換えの要否（純粋）・変更案の作成・反映と依頼 | `plugins/ndf/scripts/lib/progress_model.py`・`plugins/ndf/scripts/lib/progress_db.py`・`claude`（子のプロセス） |
| `plugins/ndf/scripts/progress.py` | 新設 | 進行表のスクリプトのエントリポイント（副命令ごとの関数） | `plugins/ndf/scripts/lib/progress_run.py`・`plugins/ndf/scripts/lib/progress_db.py`・`plugins/ndf/scripts/lib/workflow_def.py`・`lib/step_result.py` |
| `plugins/ndf/scripts/lib/durable.py` | 変更 | 起動とクライアントに記録の置き場の URL とアプリの版の指定を足す。取り消しと実行の版の読み取りを足す | DBOS |
| `plugins/ndf/scripts/lib/deps.py` | 変更 | 依存のグループ `workflow`（`jsonschema`）を足す | 無し |
| `plugins/ndf/scripts/lib/README.md` | 変更 | 新設の 7 モジュールの行を足す | 無し |
| `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | 1 周ごとに読む先を計画とレーンの進行表へ替え、止めたレーンの中止の起こし方を足す | `plugins/ndf/scripts/lib/progress_db.py`・`plugins/ndf/scripts/lib/progress_model.py` |
| `plugins/ndf/scripts/lib/parallel_status.py` | 変更 | レーンの行を 2 つの進行表から作る | `plugins/ndf/scripts/lib/progress_db.py` |
| `plugins/ndf/scripts/lib/parallel_cli.py` | 変更 | `start` で計画とレーンの進行表を作る | `plugins/ndf/scripts/lib/progress_db.py` |
| `plugins/ndf/scripts/lib/parallel_lane_cli.py` | 変更 | `lane add`・`set` を `lane_added`・`lane_changed`・`request_closed` の事象にする | `plugins/ndf/scripts/lib/progress_db.py` |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | 変更 | 計画の状態を計画の進行表から読む（台帳はメッセージの記録にだけ使う） | `plugins/ndf/scripts/lib/progress_db.py` |
| `plugins/ndf/scripts/parallel.py` | 変更 | 副命令 `request close` を足す | `plugins/ndf/scripts/lib/progress_db.py` |
| `plugins/ndf/scripts/supervise.py` | 変更 | 副命令 `new sprint`・`new close`・`queue --resume` を取り除く | 無し |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | スプリントの目録の組み立てを取り除き、工程ごとのプランを作る関数だけを残す | 無し |
| `plugins/ndf/scripts/supervise_lib/templates.py` | 変更 | リファクタリングの停止の定数と判定を取り除く | 無し |
| `plugins/ndf/scripts/supervise_lib/queue_resume.py` | 削除 | `--from` の再開の組み立て（決定 6 で不要） | 無し |
| `plugins/ndf/scripts/supervise_lib/sprint_close_steps.py` | 変更 | まとめの組み立てを外し、工程ごとのプランの関数として残す | 無し |
| `plugins/ndf/scripts/supervise_lib/sprint_lane_close.py` | 変更 | レーンの行の終わりの手順を取り除く（`end_lane_row` が持つ） | 無し |
| `plugins/ndf/scripts/supervise_lib/new_args.py` | 変更 | `new sprint`・`new close` の引数を取り除く | 無し |
| `plugins/ndf/scripts/sprint-state.py` | 変更 | 副命令 `next` の工程の判断を取り除く | 無し |
| `plugins/ndf/pyproject.toml` | 変更 | 依存のグループ `workflow` に `jsonschema` を足す | 無し |
| `pyproject.toml` | 変更 | 同じグループを開発の依存へ足し、テストのグループに `psycopg` を足す | 無し |
| `uv.lock` | 変更 | 上の 2 つの依存の固定 | 無し |
| `scripts/check-ndf-contracts.py` | 変更 | 工程の定義の検査を呼ぶ | `scripts/contracts_lib/workflow_defs.py` |
| `scripts/contracts_lib/workflow_defs.py` | 新設 | 5 つのモードの定義の検査と、工程表との突き合わせ（I11）を CI で行う | `plugins/ndf/scripts/lib/workflow_def.py`・`lib/md.py` |
| `.github/workflows/pytest.yml` | 変更 | PostgreSQL のサービスと、`NDF_PROGRESS_DB` を替えた 2 回目の結合のテストを足す | 無し |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 変更 | 工程表へ「組み換え」の行を足し、遷移を `progress.py` へ渡す文へ書き換える | 無し |
| `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh` | 変更 | 工程の分類の表へ「組み換え」の行を足す | 無し |
| `plugins/ndf/skills/development-workflow/references/parallel.md` | 変更 | レーンの conductor の手順を `progress.py` へ書き換える | 無し |
| `plugins/ndf/skills/development-workflow/references/pace.md` | 変更 | 同上 | 無し |
| `plugins/ndf/skills/development-workflow/references/waiting.md` | 変更 | 待ちの手順を `progress.py next` の `gate` へ書き換える | 無し |
| `plugins/ndf/skills/development-workflow/references/interrupt-resume.md` | 変更 | 再開の手順を `progress.py next` へ書き換える | 無し |
| `plugins/ndf/skills/development-workflow/references/projects-tracking.md` | 変更 | 進捗記録を事象から作る形へ書き換える | 無し |
| `plugins/ndf/skills/development-workflow/references/conductor-entrypoints.md` | 変更 | エントリポイントの一覧に `progress.py` を足し、外す副命令を消す | 無し |
| `plugins/ndf/skills/progress-tracking/SKILL.md` | 変更 | Skill の本文からの進捗記録の呼び出しを外す（決定 14） | 無し |
| `plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md` | 変更 | 計画の状態の置き場を進行表へ書き換える | 無し |
| `plugins/ndf/scripts/tests/test_workflow_def.py` | 新設 | 定義の検査の単体テスト | 無し |
| `plugins/ndf/scripts/tests/test_progress_model.py` | 新設 | 進行表のモデルの単体テスト（DBOS を起動しない） | 無し |
| `plugins/ndf/scripts/tests/test_progress_db.py` | 新設 | 進行表の DB の結合のテスト（SQLite と PostgreSQL） | 無し |
| `plugins/ndf/scripts/tests/test_progress_run.py` | 新設 | 進行表のワークフローの結合のテスト（落とした後・待ち・版の違い） | 無し |
| `plugins/ndf/scripts/tests/test_progress_cli.py` | 新設 | 副命令の入出力と終了コードのテスト | 無し |
| `plugins/ndf/scripts/tests/test_progress_projection.py` | 新設 | 進捗記録の生成のテスト | 無し |
| `plugins/ndf/scripts/tests/test_reshuffle.py` | 新設 | 組み換えのテスト | 無し |
| `plugins/ndf/scripts/tests/test_parallel_tables.py` | 新設 | 計画とレーンの進行表のテスト | 無し |
| `docs/glossary/glossary.json` | 変更 | 設計文書の「用語」の 13 語を足す | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` で作り直す | 無し |

手順書の書き換えの残り（`relay.md`・`handoff.md` などの 15 本）は、設計の「遷移を書く箇所の検索」の扱いの表に従う。どの手順書を書き換えるかは、その検索の結果で実装の Pull Request が決める。

## クラスと関数の構造

型の関係は設計文書の「データ構造」の ER 図と状態の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/workflow_def.py` | モジュール。`Snapshot`・`StageDef`・`ActivityDef`・`Enablement`（`dataclass`、凍結）・`WorkflowDefError` | 新設: `load_definition`・`mode_heights`・`check_dsl`・`check_vocabulary`・`check_contracts`・`check_order`・`resolve_enabled`・`validate_io` |
| `scripts/contracts_lib/workflow_defs.py` | モジュール | 新設: `check_workflow_defs`・`stage_columns`・`check_table_match` |
| `scripts/check-ndf-contracts.py` | モジュール | 変更: `main`（`check_workflow_defs` の指摘を指摘の一覧へ足すだけ） |
| `plugins/ndf/scripts/lib/progress_model.py` | モジュール。`EventDraft`・`Event`・`RowState`・`Action`・`NextRow`・`Refused`（例外） | 新設: `table_id`・`fold`・`next_action`・`check_writer`・`check_event`・`check_lane_change`・`plan_complete`・`next_row`・`wakeable_lanes`・`carry_rows`・`adopt_rows`。定数 `WRITERS` |
| `plugins/ndf/scripts/lib/progress_db.py` | モジュール。`Store`（エンジンと URL を持つ） | 新設: `default_url`・`connect`・`transaction`・`read_events`・`append_events`・`create_table`・`add_revision`・`end_lane_row`・`open_request`・`run_lock` |
| `plugins/ndf/scripts/lib/progress_run.py` | モジュール | 新設: `run_next`・`stale_execution`・`sprint_workflow`・`step_rows`・`step_append` |
| `plugins/ndf/scripts/lib/progress_activities.py` | モジュール。定数 `ACTIVITIES` | 新設: `run_activity`・`act_plan`・`act_listen`・`act_record`・`act_reshuffle` |
| `plugins/ndf/scripts/lib/progress_projection.py` | モジュール | 新設: `render_passed`・`project_stage` |
| `plugins/ndf/scripts/lib/reshuffle.py` | モジュール | 新設: `needs_reshuffle`・`propose`・`apply_changes` |
| `plugins/ndf/scripts/progress.py` | モジュール | 新設: `main`・`cmd_new`・`cmd_next`・`cmd_show`・`cmd_change`・`cmd_scope`・`cmd_cancel`・`cmd_answer`・`cmd_lane`・`cmd_adopt` |
| `plugins/ndf/scripts/lib/durable.py` | モジュール | 変更: `launch`・`open_client`。新設: `cancel_from`・`workflow_version_from`。引数 `url` を足すだけ: `send_to`・`status_from` |
| `plugins/ndf/scripts/lib/deps.py` | モジュール | 変更なし（定数 `GROUPS` に `"workflow": ["jsonschema"]` を足すだけ） |
| `plugins/ndf/scripts/lib/parallel_run.py` | モジュール | 変更: `step_observe`・`step_finish`・`_handle_stop` |
| `plugins/ndf/scripts/lib/parallel_status.py` | モジュール | 変更: `lane_rows`。取り除く: `_ledger_view` |
| `plugins/ndf/scripts/lib/parallel_cli.py` | モジュール | 変更: `start_plan` |
| `plugins/ndf/scripts/lib/parallel_lane_cli.py` | モジュール | 変更: `write_lane` |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | モジュール | 変更: `plan_state` |
| `plugins/ndf/scripts/parallel.py` | モジュール | 新設: `cmd_request_close` |
| `plugins/ndf/scripts/supervise.py` | モジュール | 取り除く: `new sprint`・`new close`・`queue --resume` の振り分け |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | モジュール | 取り除く: `cmd_new_sprint`・`sprint_command`・`add_resume`・`write_stage_index`・`_stage_entry`・`_write_plans`・`manifest_head`・`next_text`・`close_waves` |
| `plugins/ndf/scripts/supervise_lib/templates.py` | モジュール | 取り除く: `CROSS_REFACTORING_SUSPENDED`・`refactor_suspended`（呼ぶ側は `resolve_enabled` の結果を受ける） |
| `plugins/ndf/scripts/supervise_lib/queue_resume.py` | モジュール | ファイルごと取り除く |
| `plugins/ndf/scripts/supervise_lib/sprint_close_steps.py` | モジュール | 変更なし（呼び元が `close_waves` からアクティビティへ替わるだけ） |
| `plugins/ndf/scripts/supervise_lib/sprint_lane_close.py` | モジュール | 取り除く: `lane_end_step`・`lane_close_plan`・`with_lane_close` |
| `plugins/ndf/scripts/supervise_lib/new_args.py` | モジュール | 取り除く: `check_sprint` と `sprint`・`close` の引数の組み立て |
| `plugins/ndf/scripts/sprint-state.py` | モジュール | 取り除く: `cmd_next`・`next_block` |

## 関数の仕様

型の略記は次のとおりである。

- `Rows`: `dict[str, RowState]`（行の鍵 → 状態）
- `Report`: 統一 JSON レポートの `dict`
- `Conn`: SQLAlchemy の `Connection`

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `load_definition` | `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | `(mode: str, root: Path \| None = None) -> Snapshot` | モードの文書と、それが呼ぶ工程の文書・入出力規約を読み、検査を通した定義のスナップショットを返す | `mode` は 5 つのモードのどれか | 戻り値は `check_dsl`・`check_vocabulary`・`check_contracts`・`check_order` の誤りが 0 件で、定義の文書の版と sha256・工程とアクティビティの並び・使うスキーマの本文を持つ | ファイルの読み込み（`contracts/workflow/` の下） |
| `mode_heights` | `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | `(root: Path \| None = None) -> dict[str, int]` | モードの高さを正本（`workflow-common.sh` の `WF_MODE_HEIGHT`）から読む | 無し | 5 つのモード → 高さを返す。プロセスの中で 1 度だけ読む | ファイルの読み込み（`workflow-common.sh`） |
| `check_dsl` | `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | `(doc: dict, schema: dict) -> list[str]` | 1 つの文書が借りた標準のスキーマに合うかを確かめる | `schema` は標準のスキーマの本文 | 誤りの一覧（JSON のパスと理由）を返す。空なら合う | 無し |
| `check_vocabulary` | `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | `(doc: dict) -> list[str]` | 使う要素を 5 種類に、`metadata.ndf` を閉じた語彙に限る（決定 5） | 無し | `if` を持つ要素・5 種類の外の要素・知らない語と、`kind: review` で `subject` が無い・ほかの `kind` で `subject` がある組をすべて返す | 無し |
| `check_contracts` | `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | `(stages: list[StageDef], schemas: dict[str, dict]) -> list[str]` | 工程とアクティビティのすべてが入出力規約を参照し、参照先があることを確かめる（I7） | `schemas` は読めたスキーマのパス → 本文 | 参照の無い工程とアクティビティ、参照先の無い参照（`$defs` の名前まで）を返す | 無し |
| `check_order` | `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | `(activities: list[ActivityDef]) -> list[str]` | モードの全アクティビティを並びの順に平らにした一覧で、順序・範囲・重複の決まりを確かめる（I8〜I10） | 並びはモードの工程の順 | 「処理の手順」の判定表で断る組をすべて返す | 無し |
| `resolve_enabled` | `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | `(snap: Snapshot, facts: dict[str, bool], declared: dict[str, dict], disables: dict[str, str]) -> dict[str, Enablement]` | 3 つの順位（定義の条件 → プロジェクトの宣言 → 引数）で工程ごとの有効と理由を決める（決定 8） | `facts` は `metadata.ndf.when` の語のすべてを鍵に持つ | 工程ごとに `enabled` と、無効なら理由（条件の名前・宣言の `reason`・引数の理由）を返す。上の順位が下を上書きする | 無し |
| `validate_io` | `plugins/ndf/scripts/lib/workflow_def.py` | 新設 | `(snap: Snapshot, ref: str, obj: object) -> list[str]` | スナップショットに入れたスキーマで入力か出力を検査する（I3・I17） | `ref` はスナップショットの中の参照 | 誤りの一覧を返す。空なら合う | 無し |
| `check_workflow_defs` | `scripts/contracts_lib/workflow_defs.py` | 新設 | `(root: Path) -> list[dict]` | CI で 5 つのモードの定義を読み、検査と工程表との突き合わせを通す | `root` はリポジトリの最上位 | 契約の検査の指摘の形（`kind`・`path`・`name`・`detail`）の一覧を返す。借りたスキーマの sha256 が `$comment` と違えば指摘に入る | ファイルの読み込み（定義・`development-workflow` の `SKILL.md`） |
| `stage_columns` | `scripts/contracts_lib/workflow_defs.py` | 新設 | `(skill_md: str) -> dict[str, list[str]]` | 工程表の本文から、モードごとの `—` でない行名の並びを取り出す | 本文は「モードごとに起動する Skill」の表を持つ | モード → 行名の並びを返す。表が無ければ空の辞書 | 無し |
| `check_table_match` | `scripts/contracts_lib/workflow_defs.py` | 新設 | `(snaps: dict[str, Snapshot], columns: dict[str, list[str]]) -> list[dict]` | モードの工程の並びと工程表の列が同じであることを確かめる（I11） | 無し | 足りない工程・余る工程・順の違いを返す | 無し |
| `table_id` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(level: str, repo: str, name: str) -> str` | 主キー `<階層>:<リポジトリの短い鍵>:<名前>` を作る（決定 15） | `level` は `plan`・`lane`・`sprint` | 同じ 3 つの値から常に同じ文字列を返す | 無し |
| `fold` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(events: list[Event], rev: int \| None = None) -> Rows` | 事象から行の今の状態を畳み込む（I23） | 事象は `seq` の昇順 | スプリントでは `rev` に一致する事象だけを使い、行の鍵ごとに最新の `state` を返す。`state` が空の事象は状態を変えない | 無し |
| `next_action` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(snap: Snapshot, rows: Rows, enabled: dict[str, Enablement], plan_cancelled: bool) -> Action` | 次に流す工程か、飛ばす工程か、終わりかを 1 つ返す | `rows` は今の改訂の畳み込み | 「処理の手順」の判定表の 1 行に当たる `Action` を返す | 無し |
| `check_writer` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(level: str, kind: str, actor: str) -> None` | 事象の種類と書き手の役の組が書き手の表にあるかを確かめる（I6） | 無し | 表に無い組なら `Refused` を上げ、あれば何も返さない | 無し |
| `check_event` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(level: str, rows: Rows, draft: EventDraft, heights: dict[str, int] \| None = None) -> None` | 1 件の事象が今の行の状態から許される遷移かを確かめる（I4・I5・I12b・状態の図） | `rows` は同じ進行表の今の畳み込み | 許されなければ理由つきの `Refused` を上げる。理由が必須の種類で理由が空、スプリントで `rev` が空のときも上げる | 無し |
| `check_lane_change` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(plan: dict, lanes: dict[str, list[dict]], change: EventDraft) -> list[str]` | レーンの順番表の変更を計画の決まりで判定する（I12a・I13・I18〜I21） | `plan` と `lanes` は同じトランザクションで読んだ畳み込み | 断る理由の一覧を返す。空なら通る | 無し |
| `plan_complete` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(lanes: dict[str, list[dict]], requests: list[dict]) -> bool` | 計画を完了にしてよいかを判定する（I16） | 無し | 全レーンの行が完了か中止で、`request_closed` の無い `request_opened` が 0 件のときだけ真 | 無し |
| `next_row` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(lane: list[dict], plan: dict, lanes: dict[str, list[dict]]) -> NextRow` | 終えた行の次の行と、その `after` が満たされているかを返す（決定 7） | 終えた行は完了か中止 | 次の未着手の行の `progress.py new` の引数・`waiting_after`・`null` のどれかを返す | 無し |
| `wakeable_lanes` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(plan: dict, lanes: dict[str, list[dict]]) -> list[tuple[str, int]]` | 実行中の行が無く、`after` を満たす未着手の行があるレーンを返す | 無し | （レーン名, 行の番号）の一覧を、計画の並びの順で返す | 無し |
| `carry_rows` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(old: Rows, new_snap: Snapshot, first_affected: str) -> list[EventDraft]` | 範囲とモードの変更で、新しい改訂の全行の最初の事象を作る（決定 16） | `first_affected` は新しい改訂の工程の名前 | 影響する工程より前で前の改訂が終了か無効の行は `carried`、ほかは `created` の下書きを、工程の並びの順で返す | 無し |
| `adopt_rows` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `(snap: Snapshot, done: set[str], stage_map: dict[str, list[str]]) -> list[EventDraft]` | 取り込みの元の終わったステージから、工程の行の事象を作る（決定 19） | `done` のステージはすべて `stage_map` にある | 当たるステージがすべて終わった工程は終了の `adopted`、一部だけは理由つきで失敗の `adopted`、1 つも無ければ `created` だけを返す | 無し |
| `default_url` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(root: Path) -> str` | `NDF_PROGRESS_DB` か、チェックアウトの状態の置き場の既定の SQLite の URL を返す（決定 2） | 無し | 環境変数があればその値、無ければ `sqlite:///<checkout_home(root)>/progress/ndf-progress.sqlite`（`<git の共通ディレクトリ>/ndf/state` の下） | 外部コマンド（`git rev-parse`。`state_home.checkout_home` の中） |
| `connect` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(url: str) -> Store` | 進行表の DB へつなぎ、表が無ければ作り、表の版を上げる | URL は SQLite か PostgreSQL | 3 つの表と表の版の行があり、SQLite では WAL と `busy_timeout` が効いている | DB の接続と書き込み（表の作成）・SQLite ではディレクトリの作成 |
| `transaction` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(store: Store) -> ContextManager[Conn]` | 1 つのトランザクションを開き、抜けるときに確定か取り消しをする | 無し | 例外で抜ければ書いたものは何も残らない | DB の書き込み（確定と取り消し） |
| `read_events` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(conn: Conn, table_id: str, after: int = 0) -> list[Event]` | 1 つの進行表の事象を番号の順に読む | 無し | `seq > after` の事象を `seq` の昇順で返す | DB の読み込み |
| `append_events` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(conn: Conn, table_id: str, drafts: list[EventDraft], expect_seq: int) -> list[int]` | 事象を追記する唯一の関数。書く前に書き手の表と遷移を判定する（I2・I6） | 呼ぶ側は `expect_seq` を読んだ最後の番号にする | 下書きを `expect_seq + 1` から連番で書き、書いた番号を返す。重複の鍵が既にある下書きは書かずに今の番号を返す | DB の書き込み |
| `create_table` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(conn: Conn, level: str, repo: str, name: str, parent_id: str \| None, definition: dict \| None, drafts: list[EventDraft]) -> str` | 進行表を作り、スプリントなら改訂 1 のスナップショットと最初の事象を書く | 同じトランザクションの中で呼ぶ | 進行表の ID を返す。同じ ID があれば `Refused`（I1）に今の ID を入れて上げる | DB の書き込み |
| `add_revision` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(conn: Conn, table_id: str, definition: dict, drafts: list[EventDraft]) -> int` | 新しい改訂のスナップショットと、その改訂の事象を書く（決定 16） | 呼ぶ側は実行の鍵の排他を持つ | 上げた改訂の番号を返す。前の改訂の行と事象は変わらない | DB の書き込み |
| `end_lane_row` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(conn: Conn, sprint_id: str, outcome: str, actor: str) -> dict \| None` | スプリントの終わりをレーンの進行表の行へ写す唯一の関数（決定 17） | `completed` か `cancelled` を書いたのと同じトランザクションの中で呼ぶ。呼び元は `step_append`（完了）と `cmd_cancel`（中止）の 2 つ | レーンの行が無ければ `None`。あれば `row_ended` を書き、`next_row` の結果を返す。同じスプリントの `row_ended` が既にあれば書かずに、同じ `next_row` の結果を返す（冪等。耐久ステップの流し直しで 2 度呼ばれても `Refused` を上げない） | DB の読み込みと書き込み |
| `open_request` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(conn: Conn, plan_id: str, body: dict, reason: str, actor: str) -> str` | 計画の進行表へ依頼を開く（`request_opened`） | 無し | 依頼の ID（事象の番号から作る）を返す | DB の書き込み |
| `run_lock` | `plugins/ndf/scripts/lib/progress_db.py` | 新設 | `(store: Store, table_id: str) -> ContextManager[None]` | 実行の鍵の排他を、DB と同じ範囲に効く形で取る（I14・決定 20） | 無し | 取れたら抜けるまで持つ。取れなければ待たずに `Refused` を上げる | SQLite: ロックのファイル（`<DB のファイル>.<進行表の ID>.lock`）。PostgreSQL: 専用の接続と勧告的ロック |
| `run_next` | `plugins/ndf/scripts/lib/progress_run.py` | 新設 | `(store: Store, table_id: str) -> tuple[Report, int]` | 排他を取り、古い実行を始末し、進行表のワークフローを回復か新しく始めて、`ok`・`gate`・`stopped` のどれかまで流す（決定 6・7・21） | 進行表はスプリントの階層 | 戻りの組は統一 JSON レポートと終了コード。`gate` のときはプロセスを抜けて戻らない | DBOS の起動と停止・DB の読み書き・プロセスの終了（`gate`） |
| `stale_execution` | `plugins/ndf/scripts/lib/progress_run.py` | 新設 | `(store: Store, events: list[Event], version: str) -> str \| None` | 最新の `execution` の事象が指す実行が、版が違う途中の実行かを判定する（決定 21） | `version` は今のプロセスのアプリの版 | 版が違う `PENDING` の実行の ID を返す。ほかは `None` | DB の読み込み（DBOS の記録） |
| `sprint_workflow` | `plugins/ndf/scripts/lib/progress_run.py` | 新設 | `(table_id: str, rev: int, stale: str \| None) -> dict` | 定義のスナップショットに沿って工程を 1 つずつ流す耐久ワークフローの本体 | `durable.launch` の後に `durable.start` で始める | `completed`・`failed`・待ちのどれかで終わる。終わった行は流し直さない | DB の読み書き（ステップの中）・アクティビティ |
| `step_rows` | `plugins/ndf/scripts/lib/progress_run.py` | 新設 | `(table_id: str, rev: int) -> dict` | 今の改訂の行の状態と、計画の中止を 1 つの耐久ステップで読む | 無し | 畳み込んだ行と `plan_cancelled` を返し、実行の控えに残す | DB の読み込み |
| `step_append` | `plugins/ndf/scripts/lib/progress_run.py` | 新設 | `(table_id: str, drafts: list[dict]) -> dict` | 進行表のワークフローの事象を 1 つの耐久ステップで追記し、下書きに進行表の `completed` があれば同じ `transaction` の中で `end_lane_row(conn, table_id, "completed", "workflow")` を呼ぶ | 下書きは重複の鍵を持つ | `seqs`（書いた番号）と `next_row`（`end_lane_row` の戻り。レーンの行が無いか完了でなければ `None`）を返す。流し直されても同じ事象と `row_ended` は 1 度だけ残る | DB の書き込み（スプリントとレーンの進行表） |
| `run_activity` | `plugins/ndf/scripts/lib/progress_activities.py` | 新設 | `(table_id: str, rev: int, act: ActivityDef, inp: dict) -> dict` | `call` の名前から関数を引いて流し、出力を返す | `act.call` は `ACTIVITIES` にある | アクティビティの出力（統一 JSON レポートを含む）を返す | アクティビティによる（下の 4 行） |
| `act_plan` | `plugins/ndf/scripts/lib/progress_activities.py` | 新設 | `(inp: dict, args: dict) -> dict` | 工程のプランを `supervise.py new <種類>` で作り、`supervise.py queue` で流す | `args.kind` は `supervise.py new` の種類 | `queue` の出力の統一 JSON レポートと、出力のファイルのパスを返す | 外部コマンド（`supervise.py`・その中の Claude の起動）・ファイルの書き込み（プラン） |
| `act_listen` | `plugins/ndf/scripts/lib/progress_activities.py` | 新設 | `(table_id: str, rev: int, row: str) -> dict` | 承認ゲートかリリースの結果の答えを待つ（決定 7・21） | 耐久ワークフローの中で呼ぶ | その行の最新の `answered` の事象があるか受信できればその値を返す（答え直した後は新しい答えが使われる）。待ちに入るときは主のスレッドへ印を立てて戻らない | DB の読み込み・DBOS の受信・プロセスの終了の印 |
| `act_record` | `plugins/ndf/scripts/lib/progress_activities.py` | 新設 | `(table_id: str, rev: int, row: str, phase: str) -> dict` | 進捗記録の生成を流す（決定 14） | `phase` は `start` か `finish` | 進捗記録の結果を返す。進捗記録の失敗は工程を失敗にしない | 外部コマンド（`project_stage` の中） |
| `act_reshuffle` | `plugins/ndf/scripts/lib/progress_activities.py` | 新設 | `(table_id: str, inp: dict) -> dict` | 組み換えの判定・変更案・反映を流す | 入力は組み換えの入力の規約に合う | 組み換えの出力の規約に合う値を返す | 外部コマンド（Claude）・DB の書き込み |
| `render_passed` | `plugins/ndf/scripts/lib/progress_projection.py` | 新設 | `(snap: Snapshot, rows: Rows) -> str` | 「通した工程」の節の Markdown を事象から作る | 無し | 終了の行だけにチェックを付け、無効の行は理由を添えた行にする | 無し |
| `project_stage` | `plugins/ndf/scripts/lib/progress_projection.py` | 新設 | `(store: Store, table_id: str, row: str, phase: str) -> dict` | 工程の開始でボードと課題の `## 進行` を、終了でスプリント PR の「通した工程」を書く | 進行表はスプリントの階層 | 書いた先ごとの結果（`ok`・`skipped`・`failed`）を返す | 外部コマンド（`projects-sync.sh`・`pr-steps.py update`）・ネットワーク（GitHub） |
| `needs_reshuffle` | `plugins/ndf/scripts/lib/reshuffle.py` | 新設 | `(inp: dict, plan: dict \| None, lanes: dict[str, list[dict]]) -> bool` | 組み換えの要否を判定する | 入力は組み換えの入力の規約に合う | 入力の 4 種類がすべて空で、他のレーンとの触る場所の重なりが無いときだけ偽 | 無し |
| `propose` | `plugins/ndf/scripts/lib/reshuffle.py` | 新設 | `(inp: dict, lane: list[dict]) -> dict` | Claude で変更案を作る | `needs_reshuffle` が真 | 組み換えの出力の規約で検査する前の変更案を返す | 外部コマンド（`claude -p`） |
| `apply_changes` | `plugins/ndf/scripts/lib/reshuffle.py` | 新設 | `(store: Store, sprint_id: str, proposal: dict) -> dict` | 自分のレーンの中の案を反映し、レーンをまたぐ案と断られた案を依頼にする | 変更案は組み換えの出力の規約に合う | `decision`・`lane_changes`・`requests`・`refused` を返し、すべての案がどれかに入る | DB の書き込み |
| `main` | `plugins/ndf/scripts/progress.py` | 新設 | `(argv: list[str] \| None = None) -> NoReturn` | 引数を読み、副命令の関数を呼び、統一 JSON レポートを 1 行出して終わる | 無し | 終了コードは `step_result.code_matches` に合う | 標準出力・`deps.require` の再起動 |
| `cmd_new` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | スプリントの進行表を作り、要求とモード判定に終了の印を付ける（F2） | `--requirements` と `--judgment` の出力が規約に合う | 進行表・改訂 1・全行の `created`・無効の行・2 つの `manual_finished` が 1 つのトランザクションで書かれる。`--lane` があれば `row_started` も | DB の書き込み・ファイルの読み込み |
| `cmd_next` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | `run_next` を呼ぶ（F3） | 無し | `run_next` の戻りをそのまま返す | `run_next` と同じ |
| `cmd_show` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | 進行表の行・時刻・出力の要約・無効の理由・事象の履歴を返す（F6） | 無し | `items` に工程ごとの行、`metrics.history` に全改訂の事象を返す | DB の読み込み |
| `cmd_change` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | `disable`・`enable`・`retry`・`finish` の事象を書く（F4） | `--reason` がある。`finish` は `--output` がある | 書いた事象を返す。`retry` は排他を取れたときだけ書く | DB の書き込み・ファイルの読み込み（`finish`） |
| `cmd_scope` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | 範囲とモードの変更で改訂を上げ、待ちの実行を取り消す（決定 16） | 排他を取れる | 新しい改訂・`scope_changed` か `mode_raised`・`carry_rows` の事象が 1 つのトランザクションで書かれ、`--drop` の課題が同じレーンの次の行へ入る | DB の書き込み・DBOS の取り消し |
| `cmd_cancel` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | スプリントを中止し、レーンの行を同じトランザクションで終える（決定 17） | 排他を取れる | 残りの行と進行表が中止になり、`end_lane_row` が `row_ended` を書いている | DB の書き込み・DBOS の取り消し |
| `cmd_answer` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | 承認ゲートの答えかリリースの結果を `answered` と DBOS の送信で届ける（F5） | 答える行が待ちの行か、答えの検査（`validate_io`）で失敗した `listen` の行 | `answered` が書かれ、途中の実行があれば同じ値が送られている | DB の書き込み・DBOS の送信・ファイルの読み込み（`--release-result`） |
| `cmd_lane` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | レーンの順番表の行を足す・動かす・取り消す（F9） | 無し | `check_lane_change` が空のときだけ事象が書かれる | DB の書き込み |
| `cmd_adopt` | `plugins/ndf/scripts/progress.py` | 新設 | `(a: Namespace, store: Store) -> tuple[Report, int]` | 動いているスプリントか計画を 1 つのトランザクションで進行表へ取り込む（F11・決定 19） | 入力のファイルが読める | 作った進行表の ID と、失敗にした工程と理由を返す。失敗のときは何も書かれていない | ファイルの読み込み・DB の書き込み |
| `launch` | `plugins/ndf/scripts/lib/durable.py` | 変更 | `(kind, identity, queues=None, listen=None, keep=None, lock_timeout=None, retire_if=None, url: str \| None = None, app_version: str \| None = FORMAT) -> Launched` | 耐久の記録を開いて DBOS を起動する。`url` があればその DB を使い、`app_version` が `None` なら DBOS がソースから計算する版にする | 1 つのプロセスで 1 回 | `url` が無いときの振る舞いは今と同じ。`url` があるときは鍵ごとのファイルを作らず、`purge_old`・`retire_if`・`cancel_others` を当てない。`Launched.path` は `None` | DB の接続・ロックのファイル・DBOS の起動 |
| `open_client` | `plugins/ndf/scripts/lib/durable.py` | 変更 | `(kind: str, identity: str, start: Path \| None = None, url: str \| None = None) -> ContextManager[DBOSClient]` | 記録の `DBOSClient` を開く。`url` があればその DB を開く | 無し | `url` があればファイルの有無を見ない。無いときは今と同じ | DB の接続 |
| `cancel_from` | `plugins/ndf/scripts/lib/durable.py` | 新設 | `(url: str, wid: str) -> bool` | 外のプロセスから途中の実行を取り消す | 無し | 途中の実行を取り消せば真、無いか終わっていれば偽 | DB の書き込み（DBOS の記録） |
| `workflow_version_from` | `plugins/ndf/scripts/lib/durable.py` | 新設 | `(url: str, wid: str) -> tuple[str, str] \| None` | 外のプロセスから実行の状態とアプリの版を読む | 無し | （状態, アプリの版）を返す。実行が無ければ `None` | DB の読み込み（DBOS の記録） |
| `step_observe` | `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | `(main: str, name: str) -> dict` | 計画とレーンの進行表から、起こすレーン（`wakeable_lanes`）と終わったレーンを読む | 無し | 戻りの鍵は今と同じ。台帳からは読まない | DB の読み込み |
| `step_finish` | `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | `(main: str, name: str, progress: dict) -> None` | `plan_complete` が真のときだけ `plan_completed` を書く | 無し | 偽なら何も書かない | DB の書き込み・ファイルの書き込み（表示） |
| `_handle_stop` | `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | `(run: _Run, main: str) -> dict` | 止めた計画で、実行中の行が残るレーンを `progress.py cancel` を最初の入力にして起こし、残りが無くなれば `stopped` で終える | `plan_cancelled` が書かれている | 実行中の行が 0 件になるまで終わらない | 外部コマンド（`relay.py`）・DB の読み込み |
| `lane_rows` | `plugins/ndf/scripts/lib/parallel_status.py` | 変更 | `(main: Path, name: str, plan: pp.ParallelPlan) -> list[dict]` | `parallel.py status` の行を 2 つの進行表から作る（受け入れ条件 16） | 無し | 出力の形は今と同じで、台帳と `plan.json` の状態を読まない | DB の読み込み |
| `start_plan` | `plugins/ndf/scripts/lib/parallel_cli.py` | 変更 | `(a, reply, main: Path, checked_plan, self_path: Path) -> None` | 計画の検査の後に、計画とレーンの進行表と最初の順番表を 1 つのトランザクションで作る | `checked_plan` は検査を通っている | 計画の `created`・`plan_started`・`lane_added` とレーンごとの `created`・`row_added` が書かれる | DB の書き込み・外部コマンド（耐久ワークフローの起動） |
| `write_lane` | `plugins/ndf/scripts/lib/parallel_lane_cli.py` | 変更 | `(main: Path, plan: pp.ParallelPlan, raw: dict, lane_name: str, action: str) -> list[Path]` | `lane add`・`set` を `lane_added`・`lane_changed` と、応えた依頼の `request_closed` にする | 無し | `check_lane_change` が空のときだけ書く。`plan.json` は入力として書き換えない | DB の書き込み |
| `plan_state` | `plugins/ndf/scripts/lib/parallel_ledger.py` | 変更 | `(main: Path, name: str, status: Optional[str]) -> str` | 計画の状態を計画の進行表の畳み込みから返す | 無し | 返す語は今と同じ一覧 | DB の読み込み |
| `cmd_request_close` | `plugins/ndf/scripts/parallel.py` | 新設 | `(a: Namespace) -> NoReturn` | 答えていない依頼を採らずに `request_closed`（理由つき）で閉じる | `--reason` がある | 依頼が閉じ、`plan_complete` の判定から外れる | DB の書き込み |

## 異常系の処理

終了コードは共通の契約（`lib/exit_codes.py`）の値を使う。`gate` は 10（`EXIT_GATE`。承認ゲートの範囲）、工程の失敗は 1（`EXIT_VIOLATION`。手順が失敗した）、断った操作は 3（`EXIT_PRECONDITION`。不変条件・状態・排他でこのスクリプトが断る正常な否定）、引数・定義の誤りと読めない DB は 2（`EXIT_UNREADABLE`）である。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load_definition` | 定義を読めない・検査の誤り | ファイルが無い・JSON の誤り・検査の誤り | 読み込みと 4 つの検査の後 | 呼び出し元へ返す（`WorkflowDefError` に理由の一覧） | `progress.py` は `stopped` と理由の一覧、2 | 書いたものは無い。定義を直して打ち直せる |
| `check_workflow_defs` | 定義か工程表を読めない | ファイルが無い・壊れている | 読み込み | 指摘の 1 件にして続ける | CI の契約の検査が `stopped`、1 | 無し。直して打ち直せる |
| `mode_heights` | 高さを読めない | ファイルが無い・`WF_MODE_HEIGHT` の行が無いか 5 つのモードがそろわない | 読み込みと行の解析 | 呼び出し元へ返す（`WorkflowDefError`） | `stopped`、2 | 無し。プラグインの導入を直して打ち直せる |
| `default_url` | 置き場が worktree の間で共有されない | git の外で打った（`checkout_home` はコンテナの根を返す） | `state_home.find_git_dir` が `None` | 止める | `stopped`、2（「`NDF_PROGRESS_DB` を渡すか git の中で打つ」） | 無し。環境変数を渡して打ち直せる |
| `connect` | つなげない・表を作れない | URL の誤り・権限・ドライバが無い（`psycopg`） | エンジンの作成と表の作成 | 止める | `stopped`、2（URL の形とドライバの名前。パスワードは出さない） | 無し。直して打ち直せる |
| `connect` | 表の版が新しい | 新しい NDF が上げた DB を古い NDF で開いた | 表の版の行の読み込み | 止める | `stopped`、2（2 つの版） | 書かない。新しい NDF で打ち直す |
| `transaction` | 確定できない | DB の切断・SQLite の `database is locked` が `busy_timeout` を超えた | 確定のとき | 取り消して呼び出し元へ返す | 呼び元の関数の行に従う | 何も残らない。打ち直せる |
| `read_events` | 読めない | DB の切断 | SQL の実行 | 呼び出し元へ返す | `stopped`、2 | 無し。打ち直せる |
| `append_events` | 番号のぶつかり | 同時の追記（I2） | 主キーの一意の違反 | 呼び出し元へ返す（`Conflict`） | 呼び元が読み直して判定し直す。表に出ない | 取り消す。読み直して打ち直せる |
| `append_events` | 書き手か遷移が許されない | I4・I5・I6・I12b ほか | `check_writer`・`check_event` | 呼び出し元へ返す（`Refused`） | `stopped` と理由、3 | 書かない。操作を変えて打ち直す |
| `create_table` | 同じ進行表がある | I1（中止したものを含む） | 主キーの一意の違反 | 呼び出し元へ返す（`Refused` に今の ID） | `stopped` と今の ID、3 | 書かない。別の名前で作り直す |
| `add_revision` | 改訂を書けない | 番号のぶつかり・DB の切断 | 主キーの一意の違反 | 呼び出し元へ返す | `stopped`、1 | トランザクションごと戻る。打ち直せる |
| `end_lane_row` | 同じスプリントがレーンの行を既に終えている | 耐久ステップの流し直し（確定の後、DBOS がステップの出力を記録する前に落ちた） | 同じスプリントの `row_ended` の有無 | 書かずに続ける（`check_lane_change` へ渡さない） | 正常（`next_row` の結果を返す） | `row_ended` は 1 度だけ残る |
| `end_lane_row` | レーンの決まりで断られる | 別のスプリントか手での操作が終えた行など、I12b・I18 に当たる状態 | `check_lane_change` | 呼び出し元へ返す（`Refused`） | E13 の書き込みごと戻り、`stopped`、3 | 何も残らない。オーガナイザーへ戻す |
| `open_request` | 書けない | DB の切断 | SQL の実行 | 呼び出し元へ返す | 呼び元の行に従う | 呼び元のトランザクションごと戻る |
| `run_lock` | 排他を取れない | 同じ進行表の `next` が動いている（I14） | `filelock` の即時の取得・`pg_try_advisory_lock` の偽 | 呼び出し元へ返す（`Refused`） | `stopped`（「動いている実行がある」）、3 | 何も書かない。前の実行が終わってから打つ |
| `run_next` | 工程が失敗した | アクティビティの失敗・出力が規約に合わない（I3） | `sprint_workflow` の戻り | 呼び出し元へ返す | `stopped`（失敗の行・理由・出力のパス）、1 | `failed` が残る。`retry`・`finish`・`cancel` を選ぶ |
| `run_next` | 待ちに入った | 承認ゲートかリリースの結果の待ち | `act_listen` の印 | 止める（実行を途中のまま残して抜ける） | `gate`（`items` に待っている行）、10 | 実行は `PENDING` で残る。答えの後に `next` を打てば続く |
| `run_next` | DBOS を起動できない | 依存が無い・記録を開けない | `durable.launch` の `DurableError` | 止める | `stopped`、2 | 無し。打ち直せる |
| `run_next` | 計画が中止された | `plan_cancelled` が書かれた | `step_rows` の `plan_cancelled` | 止める | `stopped`（`metrics.plan_cancelled: true`）、3 | conductor が `progress.py cancel` を打つ |
| `stale_execution` | DBOS の記録を読めない | DB の切断 | `workflow_version_from` | 呼び出し元へ返す | `stopped`、2 | 無し。打ち直せる |
| `sprint_workflow` | 回復できない古い実行の行が残る | NDF の版が上がった（決定 21） | 始めのステップで `stale` が空でない | 既定値で続ける（実行中の行に `failed` を書いて終わる） | `stopped`（古い実行の ID と 2 つの版）、1 | 古い実行は取り消し済み。`retry` で続ける |
| `sprint_workflow` | 待ちの期限を過ぎた | 30 日を過ぎて答えが無い | `act_listen` が `None` を受けた | 既定値で続ける（その行を `failed` にする） | `stopped`（「待ちの期限を過ぎた」）、1 | `retry` で待ち直せる |
| `step_rows` | 読めない | DB の切断 | `read_events` | 再試行（耐久ステップの再試行 3 回） | 尽きれば `stopped`、2 | 実行は途中のまま残る。`next` を打ち直せる |
| `step_append` | 書けない | 番号のぶつかり・DB の切断 | `append_events` | 再試行（読み直して番号を取り直す。3 回） | 尽きれば `stopped`、1 | 重複の鍵で 2 度は書かれない。`next` を打ち直せる |
| `step_append` | レーンの決まりで断られる | `end_lane_row` が `Refused` を上げた（I12b・I18） | `end_lane_row` | 止める（`completed` も書かずにトランザクションごと戻し、実行を `ERROR` で終える） | `stopped`（理由）、3 | スプリントは完了にならない。オーガナイザーへ戻し、直した後に `next` を打てば完了の書き込みからやり直す |
| `step_append` | 書き手か遷移が許されない | 定義とモデルの食い違い（実装の誤り） | `Refused` | 止める（その行を `failed` にする事象は書かずに実行を `ERROR` で終える） | `stopped`（理由）、1 | `retry` は効かない。不具合として起票する |
| `run_activity` | 知らない `call` の名前 | 定義の検査をすり抜けた | `ACTIVITIES` の引き当て | 呼び出し元へ返す（行を失敗にする） | `stopped`、1 | 定義を直して `retry` |
| `act_plan` | プランが失敗した | `supervise.py queue` が 0 以外で終わった | 子のプロセスの終了コード | 呼び出し元へ返す（行を失敗にする） | `stopped`（プランの出力のパス）、1 | プランの作業ディレクトリは残す。`retry` で流し直せる |
| `act_plan` | `supervise.py queue` が承認ゲートで止まった | プランの中の承認と判断のゲート（`judge` の gate を含む） | 終了コード 10〜19 | 呼び出し元へ返す（待ちとして扱う。止まったステップの名前を行の出力に残す） | `gate`（`items` に待っている行と止まったステップ）、10 | 人の答えは `progress.py answer` でその行へ書く。次の `next` で `act_plan` が `answered` を読み、`supervise.py run <プラン> --from <ゲートの次のステップ>` で続ける（`queue --resume` は使わない。2026-11-13 の設計承認ゲートで A を選んだ） |
| `act_listen` | 答えが規約に合わない | リリースの結果のファイルの形の誤り | `validate_io` | 呼び出し元へ返す（行を失敗にする） | `stopped`（検査の誤り）、1 | 失敗の行に正しい答えを `answer` で書いてから `retry` を打つ。`act_listen` はその行の最新の `answered` を読むため、前の不正な答えは使われない |
| `act_record` | 進捗記録を書けない | `gh` の認証・ネットワーク・ボードの選択肢が無い | `project_stage` の結果 | 既定値で続ける（工程は進める） | 結果の `items` に `kind: progress-record` の失敗を載せる。終了コードは変えない | 次の工程の記録で書き直される |
| `act_reshuffle` | 変更案が規約に合わない | Claude の出力の誤り | `validate_io` | 呼び出し元へ返す（行を失敗にする） | `stopped`、1 | `retry` で作り直せる |
| `project_stage` | 書けない | `gh` の認証・ネットワーク | 子のプロセスの終了コード | 呼び出し元へ返す（結果に `failed`） | `act_record` の行に従う | 書いた先だけ残る。次の記録で書き直される |
| `propose` | Claude が起動できない・時間切れ | 認証・上限 | 子のプロセスの終了コード | 呼び出し元へ返す | `stopped`、1 | `retry` で作り直せる |
| `apply_changes` | 案が断られた | I12a・I13・I18〜I21 | `check_lane_change` | 既定値で続ける（理由つきの依頼にする） | 出力の `refused` と `requests` に載る。`ok` | 依頼はオーガナイザーが閉じる |
| `main` | 引数の誤り | 必須の引数が無い・形が違う | `argparse` | 止める | `stopped`、2 | 無し |
| `main` | 依存が無い | `jsonschema`・`dbos` が無い | `deps.require` | 止める | `deps.require` の案内、69 | 無し。用意してから打つ |
| `cmd_new` | 入力の出力が規約に合わない | 要求かモード判定の出力の誤り | `validate_io` | 止める | `stopped`（検査の誤り）、2 | 書かない。直して打ち直せる |
| `cmd_new` | 同じスプリントがある | I1 | `create_table` | 止める | `stopped` と今の ID、3 | 書かない |
| `cmd_next` | `run_next` の失敗 | `run_next` の行のとおり | `run_next` | 呼び出し元へ返す | `run_next` の行のとおり | `run_next` の行のとおり |
| `cmd_show` | 無い ID | 名前の誤り | `read_events` が 0 件で進行表も無い | 止める | `stopped`、2 | 無し |
| `cmd_change` | 実行中の行の無効・失敗でない行の `retry`・規約に合わない `finish` | I4・決定 6・I17 | `check_event`・`validate_io` | 止める | `stopped` と理由、3 | 書かない |
| `cmd_change` | `retry` のときに実行が動いている | I14 | `run_lock` | 止める | `stopped`、3 | 書かない |
| `cmd_scope` | モードを下げる・動いている実行がある | I5・I22 | `check_event`・`run_lock` | 止める | `stopped` と理由、3 | 書かない |
| `cmd_scope` | 外した課題を次の行へ入れられない | レーンをまたぐ決まり | `check_lane_change` | 既定値で続ける（改訂は上げ、依頼を開く） | `stopped`（依頼の ID と理由）、3 | 改訂は残る。オーガナイザーが依頼に応える |
| `cmd_scope` | 待ちの実行を取り消せない | DBOS の記録を開けない | `cancel_from` の例外 | 止める（トランザクションの前に取り消す） | `stopped`、2 | 何も書かない。打ち直せる |
| `cmd_cancel` | 完了の表・動いている実行がある | 状態の図・I14 | `check_event`・`run_lock` | 止める | `stopped`、3 | 書かない |
| `cmd_answer` | 待っていない行 | 行が待ちでも、答えの検査で失敗した `listen` の行でもない | `check_event` | 止める | `stopped`、3 | 書かない |
| `cmd_answer` | DBOS へ送れない | 記録が無い（実行の前に答えた） | `open_client` の例外 | 既定値で続ける（`answered` だけ書く） | `ok`（`metrics.sent: false`） | `act_listen` が `answered` を読むので失われない |
| `cmd_lane` | 計画の決まりに当たる | I12a・I13・I18〜I21 | `check_lane_change` | 止める | `stopped` と理由、3 | 書かない。オーガナイザーへの依頼にする |
| `cmd_adopt` | 読めない・対応の無いステージ | 元のファイルの誤り | 読み込みと `adopt_rows` | 止める（1 つのトランザクションを戻す） | `stopped` と理由、2 | 何も書かない。元のファイルは消さない。直して打ち直せる |
| `cmd_adopt` | 同じ名前の進行表がある | I1 | `create_table` | 止める（1 つのトランザクションを戻す） | `stopped` と今の ID、3 | 何も書かない。元のファイルは消さない |
| `launch` | 記録を開けない・同じ鍵の起動が動いている | DB の接続・ロック | 今と同じ | 呼び出し元へ返す（`DurableError`） | 呼び元の行に従う | 今と同じ |
| `open_client` | 開けない | `url` の DB へつなげない・ファイルが無い | `DBOSClient` の作成 | 呼び出し元へ返す（`DurableError`） | 呼び元の行に従う | 無し |
| `cancel_from` | 取り消せない | DB の切断 | `DBOSClient.cancel_workflow` | 呼び出し元へ返す（`DurableError`） | 呼び元の行に従う | 無し。打ち直せる（2 度目は偽を返す） |
| `workflow_version_from` | 読めない | DB の切断 | `DBOSClient.list_workflows` | 呼び出し元へ返す（`DurableError`） | 呼び元の行に従う | 無し |
| `step_observe` | 読めない | DB の切断 | `read_events` | 再試行（今の耐久ステップの再試行） | 耐久ワークフローの記録 | 次の 1 周で読み直す |
| `step_finish` | 書けない | DB の切断 | `append_events` | 再試行 | 耐久ワークフローの記録 | 次の 1 周で判定し直す |
| `_handle_stop` | レーンを起こせない | `relay.py spawn` の失敗 | 子のプロセスの終了コード | 再試行（次の 1 周） | `[ndf-parallel]` の通知に失敗のレーン | 実行中の行が残る限り続ける |
| `lane_rows` | 読めない | DB の切断・進行表が無い（取り込み前の計画） | `read_events` | 止める | `stopped`（「`progress.py adopt --plan` を打つ」）、2 | 無し |
| `start_plan` | 同じ計画がある・書けない | I1・DB の切断 | `create_table` | 止める | `stopped`、3（I1）か 2（DB の切断） | 何も書かない。耐久ワークフローを起動しない |
| `write_lane` | 計画の決まりに当たる | I19・I20・I21 | `check_lane_change` | 止める | `stopped` と理由、3 | 書かない |
| `plan_state` | 読めない | DB の切断・進行表が無い | `read_events` | 既定値で続ける（今の台帳から読む語の `unknown`） | 呼び元の表示に `unknown` | 無し |
| `cmd_request_close` | 無いか閉じた依頼 | ID の誤り | 依頼の畳み込み | 止める | `stopped`、3 | 書かない |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `next_action` | 計画が中止された | `Action.stop_plan_cancelled` |
| `next_action` | 今の改訂の行がすべて終了・無効・中止 | `Action.complete` |
| `next_action` | 並びの最初の「終了・無効・中止でない」行が失敗 | `Action.stop_failed`（その行） |
| `next_action` | 同じ行が実行中 | `Action.resume`（その行。回復の後に同じアクティビティから続ける） |
| `next_action` | 同じ行が未着手で、`resolve_enabled` が無効 | `Action.skip`（理由） |
| `next_action` | 同じ行が未着手で有効、`per_issue` が真で進め方が `fast` | `Action.run_children`（課題ごとの子の行 `<工程>#<課題番号>`） |
| `next_action` | 同じ行が未着手で有効 | `Action.run`（その行） |
| `check_order` | `kind: release` より後ろに同じ `range` の `review` か `test` | 断る（I8a） |
| `check_order` | `verify` が `kind: release` より前・`kind: release` が無い | 断る（I8b） |
| `check_order` | `review`・`test` の `range` が `sprint_diff`・`issue_diff` でない／`verify` の `range` が `released` でない | 断る（I9） |
| `check_order` | 同じ（`range`, `subject`）の `review` が 2 つ／同じ `range` の `test` が 2 つ | 断る（I10） |
| `check_order` | 上のどれにも当たらない | 通す |
| `resolve_enabled` | 引数の `--disable` か conductor の `disabled`・`enabled` がある | その値（順位 3） |
| `resolve_enabled` | 順位 3 が無く、`workflow.stages.<工程>` がある | 宣言の値（順位 2） |
| `resolve_enabled` | 順位 2・3 が無い | `when` の語がすべて真なら有効、偽の語があれば最初の偽の語を理由に無効（順位 1） |
| `run_next` | 排他を取れない | `stopped`、3 |
| `run_next` | 最新の実行が無い・終わっている | 新しい実行を始める |
| `run_next` | 最新の実行が `PENDING` で版が同じ | 起動の回復に任せ、その実行の終わりを待つ |
| `run_next` | 最新の実行が `PENDING` で版が違う | `cancel_from` の後、`stale` を渡して新しい実行を始める（決定 21） |
| `adopt_rows` | 工程に当たるステージがすべて終わった | `adopted`（終了） |
| `adopt_rows` | 1 つも終わっていない | `created` だけ |
| `adopt_rows` | 一部だけ終わった | `adopted`（失敗。理由に 2 つのステージの一覧） |
| `adopt_rows` | 工程がモードの行に無い・宣言で無効 | 定義どおり（`created` か無効の下書き） |
| `check_lane_change` | 実行中か完了の行の課題か位置を変える | 断る（I12a） |
| `check_lane_change` | 同時に動き得る別のレーンの行と触る場所が重なる | 断る（I13） |
| `check_lane_change` | 別のレーンの `after` が指す行を消すか後ろへ回す | 断る（I18） |
| `check_lane_change` | 課題が別のレーンと重なる・レーンが上限を超える・`after` が輪になる | 断る（I19・I20・I21） |
| `needs_reshuffle` | 入力の 4 種類がすべて空で、触る場所の重なりが無い | 偽（Claude を起動しない） |
| `needs_reshuffle` | ほか | 真 |

- 繰り返しの終わる条件: `sprint_workflow` は `next_action` が `complete`・`stop_*` を返すか、`act_listen` が待ちに入るまで回る。1 周で行を 1 つ終えるか飛ばすため、周の数は今の改訂の行の数（子の行を含む）を超えない。`_handle_stop` は実行中の行が 0 件になったときに終わる
- 「同時に動き得る行の組」（I13）は、`after` とレーンの中の並びを辺にした有向グラフで、2 つの行の間にどちら向きの道も無い組である。道の有無は行ごとの到達の集合で求める
- 計算量の注意: I13 の判定は、行の数を n とすると到達の集合の計算が O(n²)。計画の行は 6 レーン × 数行で数十件にとどまるため、毎回作り直す。事象の畳み込みは 1 スプリントで数百件で、索引（`table_id`, `seq`）で読み切る

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Snapshot` | 型（モード・定義の文書の版と sha256・工程の一覧・アクティビティの一覧・使うスキーマの本文） | `plugins/ndf/scripts/lib/workflow_def.py` | — |
| `StageDef`・`ActivityDef` | 型（工程の名前・ID・`when`・`per_issue`／アクティビティの ID・`call`・`with`・`kind`・`range`・`subject`・入出力の参照） | `plugins/ndf/scripts/lib/workflow_def.py` | — |
| `Enablement` | 型（`enabled`・`reason`・`source`（`definition`・`project`・`sprint`）） | `plugins/ndf/scripts/lib/workflow_def.py` | — |
| `DSL_VERSION` | 定数 | `plugins/ndf/scripts/lib/workflow_def.py` | `"1.0.3"` |
| `VOCABULARY` | 定数（`metadata.ndf` のキーごとの値の一覧と、使う要素の 5 種類） | `plugins/ndf/scripts/lib/workflow_def.py` | 設計の runtime 文書の「`metadata.ndf` の語彙」の表と同じ値 |
| `EventDraft`・`Event` | 型（設計の「進行の事象」の表の列。下書きは `seq` と `at` を持たない） | `plugins/ndf/scripts/lib/progress_model.py` | — |
| `RowState` | 型（状態・最後の事象の番号・時刻・理由・出力の要約） | `plugins/ndf/scripts/lib/progress_model.py` | — |
| `Action` | 型（種類と行の鍵と理由） | `plugins/ndf/scripts/lib/progress_model.py` | — |
| `Refused`・`Conflict` | 例外 | `plugins/ndf/scripts/lib/progress_model.py`・`plugins/ndf/scripts/lib/progress_db.py` | — |
| `WRITERS` | 定数（（階層, 事象の種類）→ 書ける役の集合。I6 の正本の表と同じ組） | `plugins/ndf/scripts/lib/progress_model.py` | 設計の runtime 文書の「事象の種類と書き手の表」 |
| `REASON_REQUIRED` | 定数（理由が必須の事象の種類） | `plugins/ndf/scripts/lib/progress_model.py` | `disabled`・`enabled`・`retry`・`manual_finished`・`scope_changed`・`mode_raised`・`cancelled`・`request_closed` |
| モードの高さ | 設定値 | `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh` の `WF_MODE_HEIGHT` | 正本はここだけ。`mode_heights` が読み、`check_event` へ引数で渡す（I5）。Python の側に高さの定数を置かない |
| `SCHEMA_VERSION` | 定数 | `plugins/ndf/scripts/lib/progress_db.py` | `1` |
| `ENV_DB` | 定数 | `plugins/ndf/scripts/lib/progress_db.py` | `"NDF_PROGRESS_DB"` |
| `DEFAULT_DB` | 定数（`state_home.checkout_home()` からの相対パス） | `plugins/ndf/scripts/lib/progress_db.py` | `progress/ndf-progress.sqlite` |
| `BUSY_TIMEOUT_SECONDS` | 定数 | `plugins/ndf/scripts/lib/progress_db.py` | `30`（決定 18） |
| `DURABLE_KIND` | 定数（`durable.launch` の `kind`） | `plugins/ndf/scripts/lib/progress_run.py` | `"progress"`。実行者の ID は `launch_key("progress", <進行表の ID>)` |
| 待ちの上限 | 設定値 | `plugins/ndf/scripts/lib/durable.py` の `PAUSE_SECONDS` | 30 日。`act_listen` は新しい定数を置かずにこれを使う |
| `ACTIVITIES` | 定数（`call` の名前 → 関数） | `plugins/ndf/scripts/lib/progress_activities.py` | `ndf.plan`・`ndf.listen`・`ndf.record`・`ndf.reshuffle` の 4 つ |
| ステージと工程の対応 | 定数 | `plugins/ndf/scripts/progress.py`（`cmd_adopt` だけが読む） | 設計の runtime 文書の「移行」の表と同じ対応 |

**アプリの版の扱い。** 今の `durable.launch` は `application_version` を `FORMAT` に固定し、`enable_patching` を真にしている。DBOS 3.1.0 は `enable_patching` が真のとき版を `PATCHING_ENABLED` に固定する。これは手元の `dbos/_dbos.py` の 502〜508 行で確かめた（2026-11-13）。この 2 つのどちらかが残ると版が NDF の版で変わらず、決定 21 の「版が違う」が起きない。そこで `app_version=None` を渡したときは、`application_version` を設定に入れず `enable_patching` を偽にする。こうすると DBOS がワークフローのソースから版を計算する（決定 21 の前提）。進行表のワークフローだけがこの形で呼び、#1142 の呼び手は今の既定のまま（決定 20 の「記録の形を変えない」）。DBOS 3.1.0 の `DBOS.patch` は `enable_patching` が偽だと `DBOSException` を上げるため、進行表のワークフローは `durable.patched` を呼ばない。ワークフローの形を変えるときは、版の違う途中の実行を取り消して新しい実行で続ける決定 21 の経路に任せる。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じスプリントの `progress.py next` が 2 つ（別の端末・コンテナを含む） | スプリントの進行表と DBOS の実行 | `run_lock`（SQLite はロックのファイル、PostgreSQL は勧告的ロック）。取れなければ待たずに断る | 落ちたら排他は外れる（ファイルのロックはプロセスの終わり、勧告的ロックは接続の終わり）。次の `next` が回復か新しい実行で続け、終わった行は流さない |
| `next` と、同じスプリントの `retry`・`scope`・`cancel` | 同上 | 3 つの操作も `run_lock` を取ってから書く。取れなければ断る | 操作は 1 つのトランザクション。中断すれば何も残らない |
| `next` と、同じスプリントの `disable`・`enable`・`finish`・`answer` | スプリントの進行の事象 | 排他を取らない。`append_events` の番号のぶつかり（I2）と `check_event` の遷移の判定で守る | ぶつかれば読み直して判定し直す。`answer` は `answered` を DB に残すため、送信の前後で落ちても失われない |
| 別々のスプリントの `next`（6 レーン） | 同じ SQLite のファイル | WAL と `busy_timeout` 30 秒。進行表ごとの行は主キーで分かれる | 決定 18 の条件に当たれば DBOS の記録を鍵ごとのファイルへ分ける |
| `next` の完了と、耐久ワークフローの 1 周 | レーンの進行表 | `step_append` が `completed` と `row_ended` を 1 つのトランザクションで書く（決定 17）。耐久ワークフローは読むだけで、`row_cancelled` を書く `stop` は計画の進行表の番号のぶつかりで守る | 耐久ワークフローは次の 1 周で読み直す |
| 組み換えのアクティビティと、別のレーンの conductor の `lane` 操作 | 計画の進行表とレーンの進行表 | `check_lane_change` を書き込みと同じトランザクションの中で読み直して判定する | 番号のぶつかりなら読み直して判定し直す |
| 耐久ステップの流し直し（回復） | 進行の事象 | `step_append` の重複の鍵（`<実行の ID>:<行の鍵>:<種類>`） | 同じ事象は 1 度だけ残る |
| `progress.py adopt` と、まだ動いている今の経路（`supervise.py queue`） | 元のファイル（読むだけ） | 取り込みは元のファイルを書き換えない | 失敗すれば何も残らず、打ち直せる |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `main` | 副命令の結果 | 標準出力の統一 JSON レポートの 1 行 | DB の URL のパスワード（`connect` が隠した URL だけを使う） |
| `run_next` | 流した工程・終了の印・次の工程・`metrics.next_row` | 結果 JSON | 同上 |
| `sprint_workflow` | 工程の開始・終了・失敗・無効の事象 | 進行の事象（DB） | アクティビティの出力の本文（パスと要約だけ） |
| `sprint_workflow` | DBOS の起動と回復の案内 | 標準エラー（`durable` の `WARNING` 以上） | 同上 |
| `act_record` | 進捗記録の書けなかった先 | 結果 JSON の `items` | `gh` のトークン |
| `project_stage` | 課題の `## 進行`・ボード・「通した工程」 | GitHub（進捗記録） | 無効の理由のうち、課題の本文に無い URL |
| `append_events` | 断った理由 | 例外の理由（呼び元が結果 JSON へ載せる） | 事象の `payload` の本文 |
| `connect` | 表の版を上げたこと | 標準エラーの 1 行 | URL のパスワード |
| `cmd_adopt` | 作った進行表・失敗にした工程と理由 | 結果 JSON | 元のファイルの本文 |
| `propose` | 変更案と理由 | 組み換えの出力（工程の出力のファイル） | Claude の会話の全文 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `load_definition` | `jsonschema`（LIB3）の `Draft202012Validator` と `referencing` の登録簿 | 設計の外部の調査（LIB3）。`grep -rln "import jsonschema" plugins scripts` は 0 件 | 使う（新しい依存。決定 3） |
| `check_dsl` | `jsonschema`（LIB3） | `load_definition` の行と同じ | 使う |
| `check_vocabulary` | 無し（語彙は NDF の閉じた一覧） | `grep -rn "metadata" plugins/ndf/scripts/lib` | 使えない。作る |
| `check_contracts` | `jsonschema` の参照の解決 | LIB3 の文書 | 使う（参照先の有無は登録簿で引く） |
| `check_order` | 無し | `grep -rn "kind.*review" plugins/ndf/scripts/lib` | 使えない。作る |
| `resolve_enabled` | `supervise_lib/templates.py` の `refactor_suspended`・`ci_only` | `grep -rn "refactor_suspended\|ci_only" plugins/ndf/scripts` | 使わない（定数を消す決定 8）。条件の事実（CI テストの宣言）は `ci_only` の判定を `facts` の作り手が呼ぶ |
| `validate_io` | `lib/step_result.py` の `validate_result` | `grep -n "^def " plugins/ndf/scripts/lib/step_result.py` | 使う（統一 JSON レポートの部分は `validate_result` も通し、追加の必須項目は `jsonschema` で見る） |
| `check_workflow_defs` | `scripts/check-ndf-contracts.py` の指摘の形（`v`） | `grep -n "^def " scripts/check-ndf-contracts.py` | 使う（指摘の形を揃える） |
| `stage_columns` | `lib/md.py` の `tables`・`section_named`。`workflow-common.sh` の `WF_STAGE_MATRIX` | `grep -n "^def " plugins/ndf/scripts/lib/md.py`・`grep -n WF_STAGE_MATRIX` | `md` を使う。`WF_STAGE_MATRIX` は工程表と別に手で持つ表で正本でないため使わない |
| `check_table_match` | 無し | `grep -rln "モードごとに起動する Skill" plugins/ndf/scripts` は `stage-skills.py` だけ（工程から Skill を引くだけで列は読まない） | 使えない。作る |
| `table_id` | `lib/durable_keys.py` の `key_hash` | `grep -n "^def " plugins/ndf/scripts/lib/durable_keys.py` | 使う（リポジトリの短い鍵を `key_hash` で作る） |
| `fold` | 無し | `grep -rn "def fold\|def replay" plugins/ndf/scripts/lib` | 使えない。作る |
| `next_action` | `sprint-state.py` の `cmd_next`・`supervise_lib/queue_resume.py` の `resume_targets` | 2 つの関数を読んだ | 使えない（目録と `done-<番号>.json` を読む形で、取り除く対象） |
| `check_writer` | 無し | 書き手の表は新しい | 作る |
| `check_event` | `mode_heights` が読む高さ | — | 使う（I5 の上げ下げは引数の高さで判定する。高さを 2 か所に持たない） |
| `mode_heights` | `workflow-common.sh` の `WF_MODE_HEIGHT`・Python の高さの定数 | `grep -rn "WF_MODE_HEIGHT\|def .*height" plugins/ndf/scripts plugins/ndf/skills`（Python の定数は 0 件） | 正本の `WF_MODE_HEIGHT` を読む。Python の部品は無いため読む関数を作る |
| `check_lane_change` | `lib/parallel_plan.py` の `touch_overlaps`・`_touch_hit`・`_issue_overlap`・`check_data` | `grep -n "^def " plugins/ndf/scripts/lib/parallel_plan.py` | 使う（触る場所の重なり・課題の重なり・上限・輪の判定）。同時に動き得る組の判定（I13）だけを足す |
| `plan_complete` | `lib/parallel_ledger.py` の `pending_questions` | 関数を読んだ | 使えない（台帳を読む形）。作る |
| `next_row` | `lib/parallel_plan.py` の `ready_lanes` | 関数を読んだ | 使う（`after` の満たし方の判定） |
| `wakeable_lanes` | 同上 | 同上 | 使う |
| `carry_rows` | 無し | 改訂は新しい | 作る |
| `adopt_rows` | `sprint-state.py` の `done_items` | 関数を読んだ | 使う（`done-<番号>.json` の読み方） |
| `default_url` | `lib/state_home.py` の `checkout_home`（#2766） | `grep -n "def checkout_home" plugins/ndf/scripts/lib/state_home.py` | 使う。`lib/decl.py` の `main_dir` は設定の置き場 `.ndf/` を指すため使わない |
| `connect` | SQLAlchemy Core（LIB4。DBOS の依存として手元にある） | 設計の外部の調査 | 使う |
| `transaction` | SQLAlchemy の `Engine.begin` | LIB4 の文書 | 使う |
| `read_events` | SQLAlchemy Core | 同上 | 使う |
| `append_events` | SQLAlchemy Core。`lib/locks.py` の `append_locked` | `grep -n "^def " plugins/ndf/scripts/lib/locks.py` | SQLAlchemy を使う。`append_locked` はファイルへの追記で使えない |
| `create_table` | SQLAlchemy Core | 同上 | 使う |
| `add_revision` | 同上 | 同上 | 使う |
| `end_lane_row` | `supervise_lib/sprint_lane_close.py` の `lane_end_step` | 関数を読んだ | 使えない（プランのステップを組む形で、取り除く対象） |
| `open_request` | `lib/parallel_request_form.py` | `ls plugins/ndf/scripts/lib \| grep parallel` | 依頼の本文の形は使う。書き込みは作る |
| `run_lock` | `lib/locks.py` の `try_exclusive`（`filelock`） | `grep -n "^def " plugins/ndf/scripts/lib/locks.py` | SQLite では使う。PostgreSQL は `pg_try_advisory_lock` を SQLAlchemy で打つ |
| `run_next` | `lib/durable.py` の `launch`・`resolve`・`start`・`wait`・`exit_leaving_pending` | `grep -n "^def " plugins/ndf/scripts/lib/durable.py` | 使う |
| `stale_execution` | `lib/durable.py` の `status_from` | 関数を読んだ（状態だけを返し、版を返さない） | 使えない。`workflow_version_from` を足して使う |
| `sprint_workflow` | `lib/durable.py` の `workflow`・`step` | 同上 | 使う。`patched` は使わない（下の「アプリの版の扱い」） |
| `step_rows` | `lib/durable.py` の `step` | 同上 | 使う |
| `step_append` | 同上 | 同上 | 使う |
| `run_activity` | `lib/stage_skills.py`（工程 → Skill） | `stage-skills.py` を読んだ | 使わない（Skill の引き当てはプランの中の worker が持つ） |
| `act_plan` | `supervise.py new <種類>`・`supervise.py queue` | `grep -n "add_parser" plugins/ndf/scripts/supervise_lib/new_args.py` | 使う（プランの実行の核） |
| `act_listen` | `lib/durable.py` の `recv`・`exit_leaving_pending` | 関数を読んだ | 使う |
| `act_record` | `project_stage` | — | 使う |
| `act_reshuffle` | `needs_reshuffle`・`propose`・`apply_changes` | — | 使う |
| `render_passed` | `supervise_lib/pr.py`・`lib/pr_mode.py` の「通した工程」の組み立て | `grep -rln "通した工程" plugins/ndf/scripts` | 節の見出しと書式は `pr_mode` の定数を使う。行の中身は事象から作る |
| `project_stage` | `projects-sync.sh`・`pr-steps.py update` | 2 つの冒頭の説明を読んだ | 使う |
| `needs_reshuffle` | `lib/parallel_plan.py` の `touch_overlaps` | 関数を読んだ | 使う |
| `propose` | `supervise_lib/claude.py` の Claude の起動 | `ls plugins/ndf/scripts/supervise_lib` | 使う |
| `apply_changes` | `cmd_lane` と同じ `check_lane_change`・`open_request` | — | 使う |
| `main` | `lib/step_result.py` の `emit`・`deps.require` | 関数を読んだ | 使う |
| `cmd_new` | `supervise_lib/sprint.py` の `cmd_new_sprint` | 関数を読んだ | 使えない（目録を書く形で、取り除く対象） |
| `cmd_next` | `run_next` | — | 使う |
| `cmd_show` | `sprint-state.py` の `status_lines`・`state_text` | 関数を読んだ | 表示の書式の考え方だけを倣い、目録は読まない |
| `cmd_change` | `append_events`・`validate_io` | — | 使う |
| `cmd_scope` | `add_revision`・`carry_rows`・`cancel_from` | — | 使う |
| `cmd_cancel` | `end_lane_row`・`cancel_from` | — | 使う |
| `cmd_answer` | `lib/durable.py` の `send_to`・`parallel.py await` の答えの経路 | 関数を読んだ | `send_to`（`url` を足したもの）を使う |
| `cmd_lane` | `check_lane_change` | — | 使う |
| `cmd_adopt` | `lib/parallel_plan.py` の `load_plan`・`lib/parallel_ledger.py` の `read_ledger_rows`・`load_checkpoint` | 関数を読んだ | 使う（元のファイルの読み方） |
| `cancel_from` | `lib/durable.py` の `cancel_others` | 関数を読んだ（記録のファイルのパスを受け、接頭辞で絞る） | 使えない。URL と ID 1 つで取り消す関数を足す |
| `workflow_version_from` | `lib/durable.py` の `status_from` | 同上 | 使えない（版を返さない）。`list_workflows` の `app_version` を返す関数を足す |
| `cmd_request_close` | `lib/parallel_lane_cli.py` の `emit_reply` | 関数を読んだ | 使う（結果の出し方） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load_definition` | 5 つのモードの定義が読め、工程の並びが工程表の列と同じ | 工程が 1 つのモード（`light` の最小） | 工程の文書が無い・JSON が壊れている・検査の誤りで `WorkflowDefError` |
| `mode_heights` | 5 つのモードの高さを返す | 2 度目の呼び出しでファイルを読まない | 行が無い・モードが欠けると `WorkflowDefError` |
| `check_vocabulary` | 5 種類の要素と語彙の語だけの文書が通る | `subject` を持つ `kind: review` だけの文書 | `if` を持つ・知らない語・`review` に `subject` が無い |
| `check_order` | `standard`（コードとドキュメントのレビュー、本番の後のリリース後テスト）が通る | `kind: release` が並びの最後 | 本番の後のレビュー・範囲がベースブランチ・同じ範囲のコードのレビュー 2 つ・同じ範囲の `test` 2 つ・`release` の前の `verify` |
| `resolve_enabled` | 3 つの順位の上書き | 宣言だけ・引数だけ・どちらも無い | `facts` に語が無い（呼び出し側の誤りとして例外） |
| `check_table_match` | 一致 | 工程表に条件付きの行（`任意`・丸括弧） | 1 つ抜く・順を入れ替える・工程表に行を足す |
| `fold` | 開始 → 終了の畳み込み | 改訂 2 で前の改訂の事象を使わない | `state` が空の事象で状態が変わらない |
| `next_action` | 判定表の各行 | 子の行がすべて終わった親の行 | 失敗の行の後ろの行を流さない |
| `check_writer` | 表のすべての組が通る | 取り込みの 6 種類 | オーガナイザーが `started`・`cancelled` を書く・取り込みが `started` を書く |
| `check_event` | 状態の図の遷移 | 失敗 → 終了（`manual_finished`） | 実行中の無効（I4）・モードを下げる（I5）・理由の空・スプリントで `rev` が空・終了の後の遷移 |
| `check_lane_change` | `after: A1` の B1 と A1 の重なりが通る | 6 レーンの上限ちょうど | `after: A1` の B1 と A2 の重なり・実行中の行の書き換え・`after` が指す行の削除・課題の重なり・上限超え・輪 |
| `plan_complete` | 全行が完了で依頼が無い | `request close` で閉じた依頼だけが残る | 答えていない依頼が残る |
| `next_row` | 次の行が `after` を満たす | 次の行が無い（`null`） | `after` が満たされない（`waiting_after`） |
| `carry_rows` | 影響する工程より前が `carried` | 最初の工程から影響する | 前の改訂で失敗の行は `created` で始まる |
| `adopt_rows` | 全部終わった工程が終了 | 1 つのステージが 3 つの工程に当たる | 一部だけ終わったデプロイが失敗・対応の無いステージで例外 |
| `default_url` | 2 つの worktree から同じ URL | 環境変数がある | git の外で止まる |
| `connect` | SQLite と PostgreSQL で表ができる | 表の版が古い DB を開いて上げる | 表の版が新しい DB・ドライバが無い |
| `transaction` | 確定 | 空のトランザクション | 例外で何も残らない |
| `read_events` | 番号の順 | `after` が最後の番号 | 切断で例外 |
| `append_events` | 連番で書ける | 重複の鍵の 2 度目は書かない | 番号のぶつかりで `Conflict`・書き手の誤りで `Refused` |
| `create_table` | 3 階層を作れる | 計画の外の単独のスプリント | 同じ名前の 2 つ目・中止の後の同じ名前（I1） |
| `add_revision` | 改訂 2 を書ける | 改訂 3 まで続けて | 番号のぶつかり |
| `end_lane_row` | 完了でレーンの行が完了になり `next_row` が返る | 計画の外で `None`・同じスプリントで 2 度呼ぶと書かずに同じ `next_row` | レーンの決まりで断られて E13 ごと戻る |
| `open_request` | 依頼が開く | 同じ本文の 2 つ目 | 切断で呼び元ごと戻る |
| `run_lock` | 1 つ目が取れる | 1 つ目のプロセスを落とした後に取れる | 2 つ目のプロセスが断られる（SQLite と PostgreSQL） |
| `run_next` | 最後の工程まで流れて `ok` | 承認ゲートで `gate`（10）、答えの後の `next` で続く | 工程の失敗で 1・排他で 3・DBOS の起動の失敗で 2・計画の中止で 3 |
| `stale_execution` | 版が同じなら `None` | 実行が無い | 版が違う `PENDING` で ID |
| `sprint_workflow` | 落とした後の回復で終わったアクティビティを流さない | 子の行がある工程 | 版の違いで実行中の行が失敗・待ちの期限で失敗 |
| `step_rows` | 行と `plan_cancelled` を返す | 計画の外 | 切断の再試行が尽きる |
| `step_append` | 流し直しで 1 度だけ残る。`completed` の下書きでレーンの行に `row_ended` が同じトランザクションで書かれ `next_row` が返る | 下書き 0 件・計画の外のスプリントの完了（`next_row` が `None`） | 書き手の誤りで実行が終わる・`end_lane_row` が断ると `completed` も残らない |
| `run_activity` | 4 つの名前を引ける | — | 知らない名前で行が失敗 |
| `act_plan` | `queue` の出力を返す | 承認ゲートで 10・答えの後は `run --from <ゲートの次>` で続ける | 0 以外の終了で行が失敗 |
| `act_listen` | `answered` の事象があれば受信を待たない | 答えがまだ無く待ちに入る・答え直した行では最新の `answered` を使う | 規約に合わない答えで失敗 |
| `act_record` | 開始と終了の記録 | ボードの宣言が無い | `gh` の失敗でも工程が進む |
| `act_reshuffle` | 変更なしで Claude を起動しない | 変更案が 0 件 | 規約に合わない案で失敗 |
| `render_passed` | 終了の行にチェック | 無効の行に理由 | 失敗の行にチェックが付かない |
| `project_stage` | 3 つの書き先 | ボードが無い | `gh` の失敗が `failed` で返る |
| `needs_reshuffle` | 入力ありで真 | 4 種類が空で重なりが無いと偽 | 入力が空でも重なりがあれば真 |
| `propose` | 案を返す | 持ち越す課題が 1 件 | 起動の失敗 |
| `apply_changes` | レーンの中の案を反映 | 案が 0 件 | 断られた案が `refused` と依頼に載る |
| `main` | 副命令の振り分け | — | 引数の誤りで 2・依存の欠けで 69 |
| `cmd_new` | 行と無効の行と 2 つの終了の印 | `--lane` 付きで `row_started` | 規約に合わない入力で 2・同じ名前で 3 |
| `cmd_next` | `run_next` の結果を返す | — | `run_next` の失敗をそのまま返す |
| `cmd_show` | 履歴に全改訂が出る | 事象 0 件の表 | 無い ID で 2 |
| `cmd_change` | 4 つの操作 | 失敗の行の `finish` | 実行中の無効・失敗でない `retry`・規約に合わない `finish`・動いている間の `retry` |
| `cmd_scope` | 改訂が上がり外した課題が次の行へ入る | `--mode` で上げる | モードを下げる・動いている間・次の行へ入れられない |
| `cmd_cancel` | 残りの行とレーンの行が中止 | 承認待ちの途中の実行を取り消す | 完了の表・動いている間 |
| `cmd_answer` | 答えが届き `next` で続く。答えの検査で失敗した `listen` の行へ答え直し、`retry` の後の `next` で通る | 実行の前の答え（`sent: false`） | 待っていない行・`listen` でない失敗の行 |
| `cmd_lane` | 3 つの操作 | 最後の行を動かす | 計画の決まりで断られる |
| `cmd_adopt` | 計画 p5 のコピーを取り込み、`show` と `parallel.py status` が元と同じ | 1 つも終わっていないスプリント | 対応の無いステージ・同じ名前で何も残らない |
| `launch` | `url` 無しで今と同じ | `url` と `app_version=None` で版がソースから計算される | 同じ鍵の起動が動いている |
| `open_client` | `url` の DB を開く | `url` 無しで今と同じ | つなげない |
| `cancel_from` | 途中の実行を取り消す | 2 度目は偽 | 切断 |
| `workflow_version_from` | 状態と版を返す | 実行が無いと `None` | 切断 |
| `step_observe` | 起こすレーンを返す | 行が 0 件のレーン | 切断で再試行 |
| `step_finish` | `plan_complete` が真で書く | 偽で書かない | 切断で再試行 |
| `_handle_stop` | 実行中の行のレーンを中止の入力で起こす | 実行中の行が無い計画はすぐ終わる | 起こせないレーンを次の周で起こし直す |
| `lane_rows` | 台帳と `plan.json` が無くても同じ表示 | 計画の外のレーン無し | 取り込み前の計画で案内 |
| `start_plan` | 3 種類の事象がそろう | レーン 1 本 | 同じ計画で何も書かず耐久ワークフローも起動しない |
| `write_lane` | `lane add` が `lane_added` と `request_closed` を書く | 依頼の無い `set` | 課題の重なりで断る |
| `plan_state` | 進行表から語を返す | 中止の計画 | 進行表が無いと `unknown` |
| `cmd_request_close` | 依頼が閉じる | 最後の依頼を閉じた後の完了 | 無いか閉じた依頼で 3 |
