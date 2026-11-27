# #4520 の詳細設計: 工程を開き直し、承認の無い工程の先へ進ませない

設計文書 [issue-4520-design.md](./issue-4520-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。事象の中身、処理の順序、条件は設計文書が持つので、ここでは写さずにセクション名で指す。

## 地図

触るファイルだけを示す（`*` は新設。パスは `plugins/ndf/` から）。

```text
contracts/workflow/      progress.json  ndf.schema.json  stages/design.json
src/ndf/adapters/        github.py
src/ndf/core/            merge_approval.py  release_record.py*
src/ndf/progress/        model.py  folding.py  run_view.py  run.py  stages.py  definition.py  records.py  command.py
                         approval_decide.py*  delivery_decide.py*  run_approval.py*  reopening.py*  redo.py*  redo_decide.py*
src/ndf/progress/gate/   asking.py  answering.py  entry.py  records.py  release.py  __init__.py
                         targets.py*  finishing.py*  requests.py*  delivery_check.py*  mvv_trail.py*
src/ndf/domains/         records/cli.py  records/mvv_gate.py  release/record_decide.py  worktree/cli.py  worktree/merged.py
tests/progress/          test_model.py  test_command.py  test_reopen_operations.py*  test_manual_gate.py*
                         test_release_confirm.py*  test_redo.py*  test_requests.py*
skills/                  progress-tracking/SKILL.md  development-workflow/references/{agent-layers,approval-answer,pace,parallel}.md
                         release/SKILL.md  release/references/release-steps.md
docs/                    glossary/glossary.json  glossary/ndf-workflow.md  glossary.md
```

依存の向きは、`ndf check` の層の規則（`contracts` → `adapters` → `core` → `progress` → `domains` の順で、下の層は上の層を読まない）に従う。`progress` は `domains` を読まない。そのため、リリース記録の読み取りは `core` へ移す。`domains` が持つ worktree の作成と外し方は、`ndf` のコマンドを別のプロセスで起動して使う。`*_decide.py` はファイル・外部コマンド・時刻を読まない（同じ検査の規則）。

行数の安全網（1,000 行）に近いファイルがある（`command.py` 1,000 行、`run.py` 988 行）。機能を足す前に分ける。

| パス（`src/ndf/` から） | 種類 | 役割 | 依存する先 | メソッドと最上位の関数（新設 / 変更） |
| --- | --- | --- | --- | --- |
| `core/release_record.py` | 新設 | リリース記録の本文を読む（`domains/release/record_decide.py` から移す） | `contracts` | 移動: `Record`・`parse_record`・`records`・`payload_of`・`UnreadableRecord` |
| `adapters/github.py` | 変更 | workflow の実行の一覧を読む | — | 新設 `GitHub.workflow_runs` |
| `core/merge_approval.py` | 変更 | 証跡の重複を、識別子・回答・対象で判定する | `adapters` | 変更 `write_merge_row` |
| `progress/approval_decide.py` | 新設 | 事象の列と対象から、有効な承認と残る否定の回答を決める | `progress.model` | 新設 `boundary`・`latest_answer`・`differences`・`objection_remains` |
| `progress/delivery_decide.py` | 新設 | リリース記録と CD の実行から、工程を終えてよいかを決める | `core.release_record` | 新設 `pick_records`・`judge_delivery`・`judge_no_release`・`pick_run` |
| `progress/run_approval.py` | 新設 | 承認ゲートの回答を読む手（`run.py` から分ける） | `run`・`approval_decide` | 移動と変更: `_consume`・`approved_seen`。新設 `consume_held`・`prior_approval` |
| `progress/reopening.py` | 新設 | 開き直す工程と事象を組み、作業の停止を確かめる | `run`・`model` | 新設 `plan_reopen`・`work_stopped` |
| `progress/redo.py` | 新設 | 作り直し用の作業場所の記録・作成・取り込み・片付け | `adapters.git`・`redo_decide` | 新設 `redo_record`・`ensure_workspace`・`take_in`・`discard`・`redirect_inputs` |
| `progress/redo_decide.py` | 新設 | 作り直しの要否、ブランチの名前、移すコミットを決める | `progress.model` | 新設 `needed`・`branch_name`・`commits_to_move` |
| `progress/gate/targets.py` | 新設 | 承認の対象を 1 回の呼び出しで 1 回だけ読む | `adapters.github`・`asking` | 新設 `TargetReader` |
| `progress/gate/finishing.py` | 新設 | `finish`・`check`・`retry`・`question`・`request-status` | `answering`・`reopening`・`delivery_check` | 移動と変更: `_cmd_finish`・`_cmd_check`・`_cmd_retry`。新設 `hand_finish`・`judge_design`・`ask_question`・`request_status` |
| `progress/gate/requests.py` | 新設 | 出したリリースのリクエストの記録と取り下げ | `store`・`plan.external` | 新設 `record_issued`・`issued`・`withdraw`・`unrecorded`・`current_request` |
| `progress/gate/delivery_check.py` | 新設 | 記録の PR と CD の実行を読み、判定に渡す | `adapters.github`・`delivery_decide` | 新設 `confirm` |
| `progress/gate/mvv_trail.py` | 新設 | MVV の承認の後に、担い手 `mvv` の証跡を書く | `core.merge_approval` | 新設 `ensure` |

行数の制約で移すだけの関数は、地図の外の挙動を変えない。

## 遷移の表

満たす決定: 決定 1・2・4・9、受け入れ条件 4・8・16・20

変えるのは `contracts/workflow/progress.json` の `events` と `reason_required` と、工程の定義の宣言である。関数は 1 つだけ足す。

| 事象（`level` は `sprint`） | `scope` | `actors` | `from` | `state` | 種類 |
| --- | --- | --- | --- | --- | --- |
| `finish_held` | row | conductor | pending・failed | running | 新設。理由が必須 |
| `reopened` | row | conductor・workflow | pending・running・failed・finished | pending | 新設。理由が必須 |
| `answered` | row | organizer・conductor | pending・running・failed・finished | — | `from` を足す |
| `failed` | row | workflow | pending・running | failed | `from` を足す |
| `outputs_recorded` | row | workflow・conductor | pending・running・failed・finished | — | `from` を足す（`question` の承認が `design_pr` を書く） |
| `request_issued` | table | workflow | — | — | 新設。出したリリースのリクエスト 1 件 |
| `redo_recorded` | table | workflow | — | — | 新設。作り直しの記録の版（同じ記録の最新が今の値） |

`ndf.schema.json` の `metadata.ndf` に `generated_outputs`（NDF が作る出力のキーの並び）を足し、`stages/design.json` に `["mvv_gate"]` を書く。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `generated_outputs` | `progress/definition.py` | 新設 | `(stage_id: str) -> tuple[str, ...]` | 今の工程の定義の `generated_outputs` を返す |
| `check_stage_output` | `progress/definition.py` | 変更 | `(stage, obj, *, handwritten: bool = False, no_release: bool = False) -> tuple[list[str], list[tuple[str, str \| None]]]` | 手書きの出力では、NDF が作るキーの必須を外す。`no_release` では `product_version`・`delivery`・`mvv_gate` を求めず、前の 2 つがあれば誤りにする |

- ステートマシンに保存した定義の写し（`Snapshot`）の形は変えない。改修より前に作ったステートマシンも同じ宣言を読む

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `generated_outputs` | `definition.load_definition` | `definition.py` を読む | 使う。定義の読み込みはこれで行い、宣言のキーだけを取り出す |

### 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| 遷移の表（`model.check_event`） | 新しい事象が表の状態で書ける | 表に無い状態と、理由の無い `reopened`・`finish_held` を断る |
| `check_stage_output` | `mvv_gate` の無い手書きの設計の出力が通る | `no_release` で `product_version` を書くと誤り |

## 畳み込み

満たす決定: 決定 1・2・4・6、受け入れ条件 12・14・16・17

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `StepProgress._reopened` | `progress/folding.py` | 新設 | `(self, ev: Event, p: dict) -> None` | 進み・待ち・回答・打ち直しの印を捨て、世代を上げ、指摘を `notes` に置く |
| `StepProgress._finish_held` | `progress/folding.py` | 新設 | `(self, ev: Event, p: dict) -> None` | 手で終えた回答待ちを `held_finish` に置き、判定のステップの結果を `done` に入れ、数え始めを移す |
| `StepProgress.next_step` | `progress/folding.py` | 変更 | `(self, stage: StageDef) -> StepDef \| None` | `held_finish` があれば承認ゲートのステップを返す |
| `StepProgress._answered` | `progress/folding.py` | 変更 | `(self, ev: Event, p: dict) -> None` | `late: true` の回答では世代を上げず、待ちへの回答にも使わない |
| `recorded_outputs` | `progress/stages.py` | 変更 | `(events: Iterable[Event], rev: int) -> list[Mapping]` | 工程の直近の `reopened` より前にその工程が書いた出力を除く。`issues` は残す |
| `View.last_pr` | `progress/run_view.py` | 新設 | `(self) -> int \| None` | 開き直しで文脈から外れたものを含め、最後に記録した `pr` を返す |

- 内部のデータ: `StepProgress.held_finish: dict | None = None`（`finish_held` の payload）。`_started` と `_reopened` が `None` に戻す
- `_SEES` の表に `reopened` と `finish_held` の行を足す

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `StepProgress._reopened`・`StepProgress._finish_held` | `StepProgress._answered` の変更要求の処理、`_SEES` | `folding.py` を読む | 表 `_SEES` に足す形を使う。変更要求の処理は世代と指摘だけが同じで、事象の種類が違うので別の関数にする |
| `View.last_pr` | `View.context` | `run_view.py` を読む | 使えない。今の文脈は最新の改訂の値だけで、外れた `pr` を返せない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_reopened` | 開き直しの後の次のステップが先頭で、保存先の世代が変わる | 2 回開き直すと世代が 2 つ進む | 前の回の回答を待ちへの回答に使わない |
| `recorded_outputs` | 開き直した工程の出力が文脈に無い | `issues` は残る | 開き直していない工程の出力は消えない |

## 承認の判定

満たす決定: 決定 2・3・4、受け入れ条件 1〜3・7・14、不変条件 I1・I2・I5

条件の中身は設計文書の「有効な承認」が持つ。判定は純粋な関数にし、対象の読み取りは `TargetReader` が持つ。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `boundary` | `progress/approval_decide.py` | 新設 | `(rows: Sequence[Event], gate_step: str) -> int` | 承認を数え始める事象の番号（直近の `reopened`・`finish_held`・質問の出し直し）を返す |
| `latest_answer` | `progress/approval_decide.py` | 新設 | `(rows: Sequence[Event], gate: str, after: int) -> Event \| None` | 境界より後の、この承認ゲートの最新の回答を返す。前へはさかのぼらない |
| `differences` | `progress/approval_decide.py` | 新設 | `(answer: Mapping, now: Mapping, *, gate: str, carried_head: str \| None) -> list[str]` | 回答が承認として数えられない理由と、見た対象と今の対象の違いを返す。空なら有効 |
| `objection_remains` | `progress/approval_decide.py` | 新設 | `(rows: Sequence[Event], gate: str) -> bool` | `late: true` の承認でない回答の後に、承認が無いかを返す（開き直しより前も数える） |
| `TargetReader.read` | `progress/gate/targets.py` | 新設 | `(self, q: Question) -> Reading` | PR を 1 回、マージ先の設定を 1 回読み、`Reading(target: Target, merged: bool)` を返す |
| `TargetReader.forget` | `progress/gate/targets.py` | 新設 | `(self) -> None` | 対象が変わりうる所で、読んだ値を捨てる |
| `github_observe` | `progress/gate/answering.py` | 変更 | `(cwd, root, hub=None, reader: TargetReader \| None = None) -> Observe` | 対象の読み取りを `TargetReader` に任せる |

- `Reading.merged` は PR の `merged`。`Target` の形（事象の `seen`）は変えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `TargetReader.read` | PR かマージ先の設定を読めない | GitHub の失敗 | 読み取り | 呼び出し元へ返す（`model.Refused`） | 「対象を照らせない」`stopped` | 何も書かない。打ち直せる |
| `differences` | 今の値が無い | 対象の PR を読めない | 比較 | 一致としない | 承認を求める | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `boundary`・`latest_answer`・`objection_remains` | `StageAnswers`・`approved_head` | `grep -rn` で `src/ndf/` を `gate_id`・`answered` で検索し、`run_view.py`・`gate/records.py` を読む | 使えない。境界（開き直しと質問の出し直し）を持たない。読み手の側はこの関数を使う形へ寄せる |
| `differences` | `Target.differs`・`_judged_elsewhere`・`_check_presented` | `gate/asking.py`・`gate/answering.py` を読む | 本文・題・資料の比較は `Target.differs` を使う。マージ先と本番への反映の比較は無いので足す |
| `TargetReader.read`・`TargetReader.forget` | `github_observe`・`shared_hub` | `gate/answering.py` を読む | `github_observe` の読み取りを移す。GitHub の接続は `shared_hub` を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `boundary`・`latest_answer` | 境界の後の承認を返す | 境界より前の承認しか無ければ `None` | 最新が承認でなければ、前の承認を返さない |
| `differences` | 5 項目がそろえば空 | HEAD が同じでマージ先か本番への反映が違う | 保留のある MVV の回答は数えない |
| `objection_remains` | 否定の後の承認で偽になる | 開き直しより前の否定も数える | — |

## 開き直し

満たす決定: 決定 4・5・6、受け入れ条件 6・10〜13・15・19、不変条件 I4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `plan_reopen` | `progress/reopening.py` | 新設 | `(v: View, stage: str, *, later: str, reason: str, trigger: Mapping, notes: str \| None) -> Reopen` | 開き直す工程を決め、`reopened` の下書きと、進みがあった工程の名前と、取り消されない操作の警告を返す（書かない） |
| `work_stopped` | `progress/reopening.py` | 新設 | `(st: Store, machine_id: str) -> ContextManager[None]` | 作業の排他を取り、起動のマーカーも確かめる。作業が流れていれば断る |
| `_cmd_retry` | `progress/gate/finishing.py` | 変更 | `(a: Namespace, cwd: Path, st: Store) -> dict` | 失敗の工程は今のまま `retry` を書き、終了と手で終えた回答待ちは `--later` を必須にして開き直す |

- `Reopen`（新設の型）: `drafts: list[EventDraft]`・`stages: list[str]`・`gates: list[tuple[str, str]]`（取り下げの対象の工程と承認ゲート）・`warnings: list[str]`
- `later` は `reopen` か `keep`。`keep` は指定の工程だけを対象にする。無効と中止の工程は対象にしない
- 取り消されない操作は、対象の工程のうち終了したもので、定義がマージかリリースのステップを持つものを挙げる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `work_stopped` | 排他を取れない・起動の直後 | 作業が流れている | 排他と起動のマーカー | 呼び出し元へ返す | `refused`。`ndf progress wait` を案内 | 何も書かない |
| `_cmd_retry` | `--later` が無い・失敗の工程に `--later` | 引数 | 状態の確かめ | 止める | `refused`。打てるコマンドを返す | 打ち直せる |
| `_cmd_retry` | 手で終えていない回答待ち・未着手・実行中・無効・中止、完了・中止したスプリント | 状態 | 状態の確かめ | 止める | `refused`。代わりのコマンドを返す | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 |
| --- | --- | --- |
| `_cmd_retry` | `items`（開き直した工程）・`warnings` | 結果 JSON |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `plan_reopen` | `_cmd_cancel` の工程の列挙 | `command.py` を読む | 使えない。中止は全工程が対象で、開き直しは指定の工程から後ろである |
| `work_stopped` | `run.is_live`・`run._launching`・`store.run_lock` | `run.py` を読む | 使う。排他を持ったまま起動のマーカーを見る形にまとめる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `plan_reopen` | 指定と後ろの工程に `reopened` | `keep` は 1 件。後ろがすべて未着手なら `stages` は指定だけ | 無効・中止の工程を含めない |
| `_cmd_retry` | 失敗の工程は今と同じ事象 | — | 異常系の各行 |

## 手で終える操作

満たす決定: 決定 2・7、受け入れ条件 1〜4・22、不変条件 I1

判断の分岐は設計文書の「処理の流れ」の `finish` の図、リリースの確かめは「リリース済みの確かめ」と「リリースしないで終える」が持つ。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_cmd_finish` | `progress/gate/finishing.py` | 変更 | `(a: Namespace, cwd: Path, st: Store) -> dict` | 出力を照らし、承認ゲートの無い工程は今のまま、持つ工程は `hand_finish` へ渡す |
| `hand_finish` | `progress/gate/finishing.py` | 新設 | `(st: Store, machine_id: str, stage: StageDef, output: Mapping, a: Namespace, seams: Seams) -> dict` | 作業の排他の中で、確かめと判定をし、`manual_finished` か `finish_held` を 1 回の追記で書く |
| `judge_design` | `progress/gate/finishing.py` | 新設 | `(v: View, stage: StageDef, inputs: Mapping, env: Env, cwd: Path, *, no_skip: bool, pr: int) -> dict` | 工程の定義の判定のステップを、入力の `worktree` で流し、結果を返す |
| `_cmd_check` | `progress/gate/finishing.py` | 変更 | `(a, cwd, st) -> dict` | `finish` と同じ出力の規則（手書き・`--no-release`）で確かめる |
| `confirm` | `progress/gate/delivery_check.py` | 新設 | `(hub: GitHub, *, record_pr: int, sprint_pr: int, version: str \| None, target: str \| None, no_release: bool, strategy: Mapping) -> Confirmed` | 記録の PR のコメントと CD の実行を読み、受けるか、足りないものを返す |
| `pick_records` | `progress/delivery_decide.py` | 新設 | `(bodies: Sequence[str], *, sprint_pr: int, version: str) -> list[Found]` | 対象の PR とバージョンで絞り、単位ごとの最新を返す（workflow の記録と手の記録） |
| `judge_delivery` | `progress/delivery_decide.py` | 新設 | `(found: Sequence[Found], runs: Mapping[str, Run \| None]) -> Verdict` | 記録の条件と CD の結果の条件を当てる |
| `judge_no_release` | `progress/delivery_decide.py` | 新設 | `(bodies: Sequence[str], *, sprint_pr: int) -> Verdict` | 最新の記録が `デプロイなし` かを確かめる |
| `pick_run` | `progress/delivery_decide.py` | 新設 | `(runs: Sequence[Mapping]) -> Mapping \| None` | `push`、無ければ `workflow_dispatch` の最新を選ぶ |
| `GitHub.workflow_runs` | `adapters/github.py` | 新設 | `(self, workflow: str, *, branch: str, head_sha: str) -> list[dict]` | workflow のファイル名・ブランチ・コミットで実行の一覧を読む |
| `gate` | `domains/records/mvv_gate.py` | 変更 | `(root, gate_name, inputs, hub, ask=llm.ask, warnings=None, *, no_skip: bool = False) -> dict` | `no_skip` なら省かない。理由に「承認でない回答が残る」を足す |
| `_recorded` | `domains/records/mvv_gate.py` | 変更 | `(hub, number, outcome, where) -> dict` | 判定の記録だけを書き、証跡の行を書かない |

- コマンドの引数: `ndf records mvv gate` に `--no-skip` を足し、`--expect-pr` を `--gate design` でも受ける
- `ndf progress finish` に `--record-pr <番号>` と `--no-release` を足す（デプロイの工程だけ）
- `hand_finish`
  - 事前条件: 工程が承認ゲートを持ち、状態が未着手か失敗
  - 事後条件: 追記した事象は、読み直した最後の事象の番号を条件にしている（`store.Conflict` なら読み直しから）
  - 副作用: PR の読み取り、判定の PR のコメント 1 件、事象の追記、進み具合の表の更新
- `Seams`（新設の型）: `reader: TargetReader`・`hub: Callable[[], GitHub]`・`env: Env`・`post: Post`。テストはここを差し替える

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `hand_finish` | 作業が流れている | 排他 | `work_stopped` | 止める | `refused` | 何も書かない |
| `hand_finish` | 承認した設計 PR が未マージ | 順序 | `Reading.merged` | 止める | `refused`。マージしてから打ち直す | 何も書かない |
| `hand_finish` | 無効で承認ゲートを持つ | 古い記録 | 状態の確かめ | 止める | `refused`。`ndf progress enable` を案内 | — |
| `judge_design` | 判定のステップが失敗・PR が違う | コマンドの失敗 | ステップの結果 | 既定値で続ける（回答待ち、人へ問う） | 結果の `warnings` | 判定の結果を残さない |
| `confirm` | 記録が無い・条件に合わない | 設計文書の表の各行 | `judge_delivery`・`judge_no_release` | 止める | `refused`。足りないものと打ち直し方 | 何も書かない |
| `confirm` | コメント・実行・設定を読めない | GitHub の失敗 | 読み取り | 呼び出し元へ返す | `stopped`（`transient`） | 打ち直せる |
| `pick_records` | 信用する書き手の記録の JSON が壊れている | 手の編集 | `core.release_record` | 呼び出し元へ返す（`UnreadableRecord`） | `stopped` | 記録を直して打ち直す |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `finish` と変更要求・却下の回答 | 工程の事象 | 両方が作業の排他を取る | 途中で止まった `finish` は何も追記していないので、打ち直して判定し直す |
| `finish` と承認の回答 | 同上 | 承認は排他を取らない | 追記の前に読み直し、番号の条件で追記する |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `hand_finish`・`judge_design` | `run._run_step`・`invoke.stage_run`・`Env.invoker` | `run.py`・`invoke.py` を読む | ステップの起動は使う。判定の後の記録は手の経路だけの形なので新設する |
| `confirm`・`pick_records`・`judge_delivery`・`judge_no_release`・`pick_run` | `record_decide.records`・`record.read`・`production_delivery` | `grep -rn` で `src/ndf/` を `リリース記録`・`workflow_runs`・`actions/workflows` で検索 | 記録の読み取りは `records` を使う。手の記録の行と CD の条件を判定する関数は無い |
| `GitHub.workflow_runs` | `GitHub.workflow_run`・`GitHub.check_runs` | `adapters/github.py` を読む | 使えない。実行 1 件の読み取りと check の一覧で、workflow とコミットから実行を引けない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `hand_finish` | 承認ありは終了、無しは回答待ち | 判定の間に承認が増える | 異常系の各行 |
| `pick_records` | 単位ごとの最新 | 単位の名前の無い記録は 1 組 | 別の単位の後の記録に引かれない |
| `judge_delivery` | CD 成功 | 実行が無く `CD の結果: 未確定` は受けて `unknown` | 失敗の完了・`段階: 反映失敗` |
| `pick_run` | `push` を優先 | `workflow_dispatch` だけ | ほかのイベントは数えない |
| `gate`（`no_skip`） | `skip` が偽 | — | 証跡の行が書かれない |

## 質問の作成

満たす決定: 決定 3、受け入れ条件 5・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `ask_question` | `progress/gate/finishing.py` | 新設 | `(st: Store, machine_id: str, stage: str, pr: int \| None, seams: Seams) -> dict` | 待っていない承認ゲートの工程の質問と承認資料を作り、`gate` の結果を返す。待っていれば今の質問を返す |
| `off_wait_question` | `progress/gate/asking.py` | 新設 | `(v: View, stage: StageDef, *, pr: int \| None, repo: str \| None) -> Question` | 工程の最後の事象の番号を起点に、待ちと同じ形の質問を組む |
| `material` | `progress/gate/asking.py` | 変更 | `(v: View, q: Question, *, private: bool = True) -> str` | 追認（リリース済み）とリリースしない回答待ちでは、その説明を足す。MVV 判定の欄は `finish_held` の記録からも読む |

- 承認資料と提示した対象は、今の `present_material` が今の保存先（`material_path`）へ書く。新しい置き場とファイルの種類は足さない。保存先を移す変更（[#3500](https://github.com/devbasex/ai-plugins/issues/3500)）は `material_path` の側で当たる
- `question` はリリースのリクエストを出さない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ask_question` | 承認ゲートが無い・中止か無効の工程 | 引数 | 定義と状態 | 止める | `refused` と理由 | — |
| `ask_question` | 設計の対象の PR が無い・文脈と違う `--pr`・ほかのゲートに `--pr` | 引数 | 対象の決定 | 止める | `refused`。正しい打ち方 | 打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `ask_question`・`off_wait_question` | `asking.question`・`present_material`・`gate_result` | `gate/asking.py` を読む | 資料と結果の組み立ては使う。`question` は待ちの手だけを受けるので、待たない工程の分を足す |

### 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `ask_question` | 未着手・実行中・失敗・終了で質問ができる | 異常系の各行 |
| `off_wait_question` | 事象が増えると id が変わる | — |

## 回答の記録

満たす決定: 決定 3・4、受け入れ条件 5〜9・19、不変条件 I4・I5

書く順序は設計文書の「排他と送り直し」と「外への書き込みの順序」が持つ。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `answer` | `progress/gate/answering.py` | 変更 | `(st, machine_id, a: Answer, observe=no_observe, post=no_post, env: Mapping[str, str] = os.environ) -> dict` | 送り直しを先に照らし、開き直す回答では排他・証跡・取り下げ・追記・表の更新の順に書く |
| `_refusal` | `progress/gate/answering.py` | 変更 | `(v, a, observe, root=None) -> tuple[Question, Target]` | 待っている質問か、`off_wait_question` の質問を受ける |
| `_find_question` | `progress/gate/answering.py` | 新設 | `(v: View, gate_id: str, root: Path \| None) -> Question` | 待ちの質問、無ければ承認ゲートの工程ごとの待っていない質問から id の合うものを返す。対象の PR は提示した対象から引く |
| `_effects` | `progress/gate/answering.py` | 新設 | `(v: View, q: Question, a: Answer) -> Effects` | 回答の種類と工程の状態から、`late`・`after_release`・開き直し・`failed` の要否を返す |
| `_drafts` | `progress/gate/answering.py` | 新設 | `(v, q, a, effects: Effects, extra: Mapping) -> list[EventDraft]` | `reopened`、`answered`、`failed`、出力、外した課題の順に下書きを並べる |
| `write_merge_row` | `core/merge_approval.py` | 変更 | `(hub, row: MergeRow, viewer) -> bool` | 記録の識別子・回答・対象のすべてが同じ証跡があるときだけ書かない |

- `answer`
  - 副作用: PR の証跡 1 件、リクエストのファイルの削除、事象の追記（1 トランザクション）、`gate-rejections`、進み具合の表
- 追認（`after_release`）の承認では `_trail_by` が `None` を返し、証跡を書かない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `answer` | 同じキーで質問・回答・回答者が違う | 使い回し | `_same` | 止める | `refused` | — |
| `answer` | 作業が流れている（変更要求・却下） | 排他 | `work_stopped` | 止める | `refused` | 何も書かない |
| `answer` | 証跡を書けない | 権限・GitHub | `post` | 止める | `refused` | DB へ書かない。打ち直せる |
| `answer` | リクエストのファイルを消せない | 権限・ファイル | `requests.withdraw` | 止める | `refused`。パスを示す | DB へ書かない。打ち直すと残りを消す |
| `answer` | 追記の直前に事象が増えた | 競合 | `store.Conflict` | 呼び出し元へ返す | `stopped`（`transient`） | 打ち直すと証跡を再利用する |
| `answer` | 表の更新の失敗 | GitHub | `sync_records` | 既定値で続ける | `warnings` に `ndf progress resync` | 回答は記録済み |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_find_question`・`_effects`・`_drafts` | `_refusal`・`run.answer_draft`・`_dropped`・`_outputs` | `gate/answering.py` を読む | 下書きの部品は使う。待っていない質問と開き直しの分岐は無い |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_effects` | 終了の工程の変更要求は開き直し、却下は `failed` も | 待っていない未着手への否定は記録だけ | 自動の流れの回答待ちは今と同じ |
| `answer` | 開き直しを起こす回答の事象の順 | 送り直し（作業が流れていても同じ結果） | 異常系の各行 |
| `write_merge_row` | 同じ回答の打ち直しは増えない | 同じ識別子で回答が違えば書く | — |

## 依頼の有効性の問い合わせ

満たす決定: 決定 9、受け入れ条件 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `request_status` | `progress/gate/finishing.py` | 新設 | `(st: Store, machine_id: str, ident: str) -> dict` | `metrics.valid` を返す。何も書かず、GitHub を読まない |
| `current_request` | `progress/gate/requests.py` | 新設 | `(v: View) -> str \| None` | 今待っている質問の id について、最後に記録した `request_issued` の依頼の ID を返す。待っていなければ `None` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `request_status`・`current_request` | `release_request_of`・`plan_request` | `gate/asking.py` を読む | 使えない。並行開発の計画の事象だけを読み、外のオーガナイザーの依頼を持たない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `request_status` | 今の ID は真 | 出し直した後の前の ID は偽 | 開き直しの後は、ファイルが残っていても偽 |

## 次の 1 手の判断

満たす決定: 決定 2・4・7、受け入れ条件 1・2・7・14・22

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_running_move` | `progress/run.py` | 変更 | `(v: View, s: StageDef) -> Move` | `held_finish` があれば、ステップを流さず `GATE` か `CONSUME` を返す。否定の回答が残る承認ゲートは省かない |
| `consume` | `progress/run_approval.py` | 変更 | `(d: Drive, v: View, m: Move) -> dict \| None` | 今の `_consume`。MVV で省くときは対象の 5 項目を照らし、承認の記録・証跡・マージの確かめを同じ呼び出しで行う |
| `consume_held` | `progress/run_approval.py` | 新設 | `(d: Drive, v: View, m: Move) -> dict \| None` | 有効な承認を照らし、設計はマージ済み、デプロイはリリースの確かめを通して終了を書く |
| `prior_approval` | `progress/run_approval.py` | 新設 | `(d: Drive, v: View, s: StageDef, step: StepDef) -> list[EventDraft] \| None` | 自動の流れの承認ゲートに有効な先の承認があれば、質問せずに済ませる事象を返す |
| `approved_seen` | `progress/run_approval.py` | 変更 | `(answers: StageAnswers, root: Path) -> dict \| None` | 直近の開き直しより後で、工程の対象の PR を見た承認だけを返す |
| `ensure` | `progress/gate/mvv_trail.py` | 新設 | `(hub: GitHub, v: View, answered: Event, now: Target) -> bool` | 記録した `service:mvv` の承認がまだ有効なら、判定の記録へのリンクと担い手 `mvv` の行を持つ証跡を書く |
| `recheck` | `progress/gate/answering.py` | 変更 | `(st, machine_id, observe, docs_only=never_docs_only) -> str \| None` | 手で終えた回答待ちと MVV の回答でも、マージ先と本番への反映を比べる |
| `_reading_skipped` | `progress/run.py` | 変更 | `(d, v, s, step, prog) -> list[EventDraft] \| None` | 有効な先の承認がある設計では、読み手役のステップを打たない |

- `run.py` の `Env` に 3 つの口を足し、`gate` のエントリポイントが差し込む。`run` は GitHub を直接読まない今の形を保つ
  - `approved_state: Callable[[View, Question], Reading]`
  - `confirm: Callable[..., Confirmed]`
  - `mvv_trail: Callable[..., bool]`
- 証跡の記録の識別子は `mvv:<ゲート>:<質問の id>:<HEAD>`。本文は `## MVV 判定（` の見出しを持つ（今の証跡の数え方に合わせる）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `consume` | 判定した対象が今と違う | push・付け替え・設定の変更 | `differences` | 人へ質問し直す | `gate` | 承認も証跡も書かない |
| `consume`・`ensure` | 証跡を書けない | GitHub | 投稿 | 止める | `stopped`（`transient`） | 承認は記録済み。次の `next` が書く |
| `consume`・`consume_held` | 承認した PR が未マージ | 順序 | `Reading.merged` | 止める | `stopped`（`precondition`）。HEAD とマージの手順 | 承認は残る |
| `consume_held` | マージされた HEAD かマージ先が承認と違う | 承認の後の変更 | `differences` | 質問し直す | `gate`。違う HEAD がマージされたと載せる | — |
| `consume_held` | リリースの確かめに合わない | 待つ間に `反映失敗` | `confirm` | 止める | `stopped`（`precondition`） | 終了を書かない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `consume_held`・`prior_approval` | `run._consume`・`gate_report`・`answering.recheck` | `run.py`・`gate/answering.py` を読む | 終了と失敗の書き方は使う。ステップを流さずに終える手は無い |
| `ensure` | `merge_approval.write_merge_row`・`mvv_gate._recorded` | `core/merge_approval.py`・`records/mvv_gate.py` を読む | 投稿は `write_merge_row` を使う。本文に判定の見出しとリンクを足す形を新設する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `consume_held` | 承認とマージで終了し、次の工程が始まる | 承認した HEAD が前からマージ済み | 異常系の各行 |
| `prior_approval` | 対象が同じなら質問しない | HEAD が違えば質問する | 否定が残れば MVV で省かない |
| 読み取りの回数 | 1 回の呼び出しで PR と設定が 1 回ずつ | 読み直しのある呼び出しは 1 回ずつ増える | — |

## 承認を読む側

満たす決定: 決定 3・6、受け入れ条件 14・17

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `StageAnswers.__init__` | `progress/run_view.py` | 変更 | `(self, v: View, stage: str) -> None` | 走査を直近の `reopened` で止め、工程の対象の PR と違う PR を見た回答を飛ばす |
| `approved_head` | `progress/gate/records.py` | 変更 | `(v: View, kind: str, root: Path \| None = None) -> str \| None` | 同じ条件で最後の回答を選ぶ |
| `answers` | `progress/gate/records.py` | 変更 | `(v: View) -> list[dict]` | すべての回答を出し、`after_release` の回答は「追認」と示す項目を足す |

### 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `approved_head` | 開き直しの後の承認を返す | 開き直しより前の承認と、違う PR の承認は返さない |

## リリースのリクエスト

満たす決定: 決定 4・9、受け入れ条件 15

出したリクエストは、事象 `request_issued`（ステートマシンの DB）に記録する。payload は `stage`・`gate_id`・`id`・`sink`（`file`・`plan`・`none`）・`path`（ファイルのときの絶対パス）・`body`（書いた項目の全部）である。重複のキーは `request_issued:<ステートマシンの ID>:<id>:<path>` で、同じ組は 2 度書かない。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `request_release` | `progress/gate/release.py` | 変更 | `(st, v, q, env) -> dict \| None` | 手で終えた回答待ちでは出さない。出す前に `record_issued` を呼ぶ |
| `record_issued` | `progress/gate/requests.py` | 新設 | `(st: Store, v: View, q: Question, *, sink: str, path: Path \| None, body: Mapping) -> None` | 事象 `request_issued` を追記する |
| `issued` | `progress/gate/requests.py` | 新設 | `(v: View, stages: Collection[str]) -> list[Issued]` | 指定の工程が出した記録を古い順に返す |
| `withdraw` | `progress/gate/requests.py` | 新設 | `(records: Sequence[Issued]) -> list[str]` | 中身が記録と一致するファイルを消し、残したものと無いものを警告にする |
| `unrecorded` | `progress/gate/requests.py` | 新設 | `(v: View, gates: Collection[tuple[str, str]], root: Path, env: Mapping[str, str]) -> list[str]` | 記録の無い改修前の依頼を、今の `.seen.json` から探して警告にする（消さない） |

- 並行開発の計画へ出した依頼は、`answer` と `_cmd_retry` の追記と同じトランザクションで、今の `plan.close_request` で閉じる
- 中身の比較は、書いた時刻 `at` を除く全項目（今の `external._same_body` と同じ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_issued` | 追記が競合した | 別の追記 | `store.Conflict` | 再試行（読み直して追記） | — | ファイルはまだ書いていない |
| `request_release` | 同じ名前で中身の違うファイルがある | 別の書き手 | `external.write_request` | 呼び出し元へ返す | 今と同じ警告 | 記録は残るが、中身が違うので取り下げで消さない |
| `withdraw` | 消せない | 権限 | 削除 | 止める | `refused` とパス | 打ち直せる |
| `withdraw` | 中身が違う・ファイルが無い | 書き換え・回収済み・別の機械で出した | 読み取り | 既定値で続ける | `warnings` に ID とパス | 消さない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `record_issued`・`issued`・`withdraw`・`unrecorded` | `external.write_request`・`external._same_body`・`release._seen_path` | `grep -rn` で `src/ndf/` を `organizer`・`unlink` で検索 | 中身の比較は `_same_body` を公開名にして使う。削除と記録の関数は無い |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `withdraw` | 打った側に `NDF_ORGANIZER_DIR` が無くても消す | 2 つの発行先の両方 | 中身が違うファイルは残る |
| `issued` | 開き直す工程の分だけ | `--later keep` で残した工程を含めない | — |
| `unrecorded` | 警告に ID が出る | 環境変数が無ければパスを載せない | 消さない |

## 進み具合の表の更新

満たす決定: 決定 6、受け入れ条件 18

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_targets` | `progress/records.py` | 変更 | `(v: View) -> list[tuple[str, int, str, str]]` | 文脈に `pr` が無ければ `View.last_pr` を PR の欄の宛先にする |

開き直しを起こす操作と、回答待ちから終了を書く手は、今の `sync_records` を 1 回呼ぶ。同じキーの送り直しは呼ばない。

### 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `_targets` | 開き直しで `pr` が外れても PR の欄を書き直す | 本文の失敗の後の `resync` で事象が増えない |

## 作り直し用の作業場所

満たす決定: 決定 8、受け入れ条件 6・12

記録の項目、作る条件、取り込み、片付けの順は設計文書の「開き直した設計を新しい PR に載せる」が持つ。記録は事象 `redo_recorded` で、同じ記録（開き直しの番号が同じもの）の最新の payload が今の値である。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `needed` | `progress/redo_decide.py` | 新設 | `(events: Sequence[Event], pr_merged: bool) -> bool` | 開き直しの時点の `design_pr` がマージ済みで、記録がまだ無いかを返す |
| `branch_name` | `progress/redo_decide.py` | 新設 | `(issue: int, machine_id: str, generation: int) -> str` | 作り直し用のブランチの名前を返す |
| `commits_to_move` | `progress/redo_decide.py` | 新設 | `(branch_commits: Sequence[str], merged_heads: Sequence[str], applied: Collection[str]) -> list[str]` | マージされておらず、まだ移していないコミットを順に返す |
| `redo_record` | `progress/redo.py` | 新設 | `(d: Drive, v: View, s: StageDef) -> Record \| None` | 要否を 1 回だけ決めて記録し、今の記録を返す |
| `ensure_workspace` | `progress/redo.py` | 新設 | `(d: Drive, v: View, rec: Record) -> dict \| None` | 予定を記録してから `ndf worktree create <ブランチ> --from <ベース>` を打つ。失敗は `stopped` の結果を返す |
| `redirect_inputs` | `progress/redo.py` | 新設 | `(rec: Record \| None, inputs: Mapping) -> dict` | 設計の工程の `worktree`・`branch` を差し替える |
| `take_in` | `progress/redo.py` | 新設 | `(d: Drive, v: View, rec: Record) -> dict \| None` | ベースの固定、マージ、`cherry-pick -x` を行う。止まれば `stopped` の結果を返す |
| `discard` | `progress/redo.py` | 新設 | `(d: Drive, rec: Record) -> list[str]` | `ndf worktree remove` を打ち、残ったものを警告にする |
| `remove_one` | `domains/worktree/merged.py` | 新設 | `(main: Git, branch: str, path: Path, rec: Record) -> None` | 今の外し方（未コミットは残す・追跡外は退避・`--force` なし）と `git branch -d` |

- `domains/worktree/cli.py` に `ndf worktree remove <ブランチ> --path <パス>` を足す。パスがそのブランチの worktree でなければ何もしない
- 設計の工程の入力の差し替えは、`_start`・`_run_step`・`hand_finish` が同じ `redirect_inputs` を通る

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ensure_workspace` | 同じ名前のブランチか保存先がある・`new_branch` が偽 | 別の作業 | 作成の前後 | 止める | `stopped`（`precondition`）と名前 | 予定の記録を無効にする |
| `ensure_workspace` | fetch と作成の失敗 | ネットワーク・git | コマンドの結果 | 呼び出し元へ返す | そのコマンドの `cause` | 打ち直すと予定の記録から続ける |
| `take_in` | 途中の状態が残る | 中断 | git の状態 | 止める | `stopped`。`--continue` か `--abort` を案内 | 打ち直せる |
| `take_in` | 解けない衝突 | 両側の変更 | マージと cherry-pick | 止める | `stopped`（`precondition`）と衝突したファイル | 取りやめて元に戻す |
| `discard` | worktree かブランチが残る | squash・rebase・未コミット | コマンドの結果 | 既定値で続ける | `warnings` に名前と消すコマンド | 利用者が決める |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `next` の取り込みと `retry`・変更要求 | 今の作業場所 | `next` と作業が持つ作業の排他 | 取り込むコミットを記録してから始め、打ち直しは記録を使う |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `needed`・`branch_name`・`commits_to_move`・`redo_record`・`ensure_workspace`・`redirect_inputs` | `ndf worktree create`・`model.branch_prefix` | `grep -rn` で `src/ndf/` を `redo`・`rebuild` で検索 | 作成と接頭辞は使う。記録と差し替えを持つものは無い |
| `take_in` | `core/converge/worktree.py` の `cherry-pick`、`adapters.git.Git` | `grep -rn` で `src/ndf/` を `cherry-pick` で検索 | `Git.run`・`Git.fetch` を使う。収束ループの取り込みは担当の worktree の前提を持つので使えない |
| `discard`・`remove_one` | `merged._remove_worktree`・`merged._delete_branch` | `domains/worktree/merged.py` を読む | 使う。`remove_one` は 2 つを PR の番号なしで呼ぶ入口である |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `needed` | マージ済みで真 | 記録があれば偽 | 未マージは偽 |
| `commits_to_move` | マージした PR より後のコミット | マージした PR が無い | 適用済みを飛ばす |
| `take_in` | 実の一時リポジトリで取り込む | 差分なし | 衝突・中断からの再開 |

## 手順書

満たす決定: 決定 3・7・9、受け入れ条件 21・23

| 保存先 | 書き換える中身 |
| --- | --- |
| `skills/progress-tracking/SKILL.md` | `finish`・`retry`・`question`・`request-status` の打ち方と結果 |
| `skills/development-workflow/references/agent-layers.md` | 手の経路の設計は判定を打たない（規則 15・17）。承認ゲートの工程の `finish` は回答待ちになる |
| `skills/development-workflow/references/pace.md` | supervisor の経路の判定から設計を外す |
| `skills/development-workflow/references/approval-answer.md` | 待っていない工程への回答の 2 段の打ち方、追認 |
| `skills/development-workflow/references/parallel.md` | 外のオーガナイザーは、依頼を示す前に `request-status` を打つ |
| `skills/release/SKILL.md`・`references/release-steps.md` | 手の経路の `finish` が記録を読むことと、`--record-pr`・`--no-release` |
| `docs/glossary/glossary.json` と生成する文書 | 「開き直し」「有効な承認」 |

断る結果の理由と代わりのコマンドは、各関数の `model.Refused` の文に持たせ、手順書へ写さない。文言を照合するテストは書かない。
