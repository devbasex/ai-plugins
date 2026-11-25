# #4517 の詳細設計: モード判定の場所と即時修正のモード

設計文書 [issue-4517-design.md](./issue-4517-design.md) の決定を、ファイルと関数の粒度へ下ろした文書である。基準の中身（課題ごとの判定・4 条件・境界事例・スプリントのモードの規則）は設計文書の「判定の基準」にあり、実装で `references/mode-criteria.md` へ移す。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── skills/
│   ├── development-workflow/
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── mode-criteria.md *
│   │       ├── workflow-modes.md
│   │       ├── stage-notes.md
│   │       ├── agent-layers.md
│   │       ├── conductor-entrypoints.md
│   │       ├── glossary.md
│   │       ├── loop.md
│   │       └── operation-run.md
│   ├── issue-plan-strategy/references/{parallel-plan.md, execution-plan.md}
│   ├── out-of-scope/SKILL.md
│   ├── quality-gates/SKILL.md
│   └── release-verification/SKILL.md
├── agents/worker.md
├── contracts/
│   ├── templates/fix.json（削除）
│   ├── workflow/
│   │   ├── modes/hotfix.json *
│   │   ├── modes/legacy-refactor.json
│   │   ├── stages/post-release-review.json *
│   │   ├── stages/release-verification.json
│   │   ├── stages/requirements.json
│   │   ├── context.schema.json
│   │   ├── progress.json
│   │   └── parallel-plan.schema.json
│   └── worker/kinds.json
├── src/ndf/
│   ├── core/route/__init__.py
│   ├── progress/
│   │   ├── judgment.py *
│   │   ├── command.py
│   │   ├── run.py
│   │   ├── store.py
│   │   ├── plan/cli.py
│   │   ├── lane/__init__.py
│   │   └── lane/launch.py
│   └── domains/crossreview/cli.py
└── tests/
    ├── progress/{test_judgment.py *, test_command.py, test_run.py, test_definition.py, test_lane_launch.py, test_plan.py}
    └── domains/crossreview/test_crossreview_post_release.py *
```

言葉: 「相手役」は `cross-review` でレビューだけをする別の AI（既定は codex と kiro）、「修正担当」は指摘を直すか見送るかを決める claude である。「モードの高さ」は混ざったときに勝つ順の数（`contracts/workflow/modes/*.json` の `metadata.ndf.height`。大きいほど工程が多い）である。

依存の向き: `judgment.py` は `definition`・`stages`・`model` だけを読み、`command.py`・`run.py`・`plan/cli.py`・`lane/__init__.py`・`lane/launch.py` から呼ばれる。`judgment.py` は `store` を読まず、定義の改訂の書き込みは呼び手が `store.append_definition` で行う。レーンの側が使うのは計画の行の確かめ（`row_errors`）と行のモードの決め方（`sprint_mode`）だけで、正式な判定はスプリントの中で行う。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `skills/development-workflow/references/mode-criteria.md` | 新設 | 判定の基準の唯一の置き場 | — | — | — |
| `skills/development-workflow/SKILL.md` | 変更 | 「2. 上から順に条件を判定する」と「即時修正」を `mode-criteria.md` へのリンクにし、工程表に `hotfix` の列と行「リリース後レビュー」を足す | — | — | — |
| `skills/development-workflow/references/workflow-modes.md` | 変更 | 「判定に使う質問」「よくある境界事例」を消し、`hotfix` のモード別の詳細と標準フローの経路を足す。「途中でモードが変わったとき」に、工程「要求と受け入れ条件」を終えるまでは上げ下げどちらも採り、終えた後は上げる方向だけ、という時点の区切りを足す | — | — | — |
| `skills/development-workflow/references/stage-notes.md` | 変更 | 工程「要求と受け入れ条件」の判定の作業、工程「完了判定」の照らし直し、工程「リリース後レビュー」の扱いを足す | — | — | — |
| `skills/development-workflow/references/agent-layers.md` | 変更 | モードごとの組み方の表に `hotfix` の行を足す | — | — | — |
| `skills/development-workflow/references/conductor-entrypoints.md` | 変更 | `ndf route render immediate_fix` を単独で流す例を消し、`hotfix` のスプリントの始め方に置き換える | — | — | — |
| `skills/development-workflow/references/glossary.md` | 変更 | 「即時修正」の意味を「4 条件を満たす不具合を起票せずその場のプランで直すこと」から「モード `hotfix`（実装とリリースを先に通し、リリースの後にレビューする）」へ変え、「リリース後レビュー」「計画の時点の判定」「判定の記録」を足す。無い `new impl --escape-of` の参照を消す | — | — | — |
| `skills/development-workflow/references/loop.md` | 変更 | `loop sprint` のモードを計画の時点の判定と書き、スプリントの判定が正であることを足す | — | — | — |
| `skills/issue-plan-strategy/references/parallel-plan.md` | 変更 | スプリントの行の `mode`・`basis` の欄と、モードで行を切る手順（基準は `mode-criteria.md` を指す） | — | — | — |
| `skills/issue-plan-strategy/references/execution-plan.md` | 変更 | モードの列の埋め方と、食い違いを「見直し」の表に書く手順 | — | — | — |
| `skills/development-workflow/references/operation-run.md` | 変更 | 外部へ適用する前に dry-run の結果を `operation` の問いで照らし、値の外の変更があれば適用せずに止める（「`operation` の外部への適用の前の確かめ」） | — | — | — |
| `skills/out-of-scope/SKILL.md` | 変更 | 即時修正の参照を「作業中に見つけた不具合」の 3 つの場合へ直す | — | — | — |
| `agents/worker.md` | 変更 | 規則の「即時修正」を「作業中の直し」と `mode-criteria.md` の 4 条件へ | — | — | — |
| `skills/quality-gates/SKILL.md` | 変更 | 差分でモードを照らし直し、`recheck` が `None` でなければ出力 `mode_check` を書いて失敗で返す | — | — | — |
| `skills/release-verification/SKILL.md` | 変更 | 「最終工程」の条件にリリース後レビューを足す。`hotfix` では合否・保留によらず結果を出力 `verification` に書いて工程を終え、不合格でも起票しない（起票はリリース後レビュー） | — | — | — |
| `contracts/templates/fix.json` | 削除 | 課題番号の無い即時修正のプランを無くす | — | — | — |
| `contracts/workflow/progress.json` | 変更 | 事象 `mode_decided` を登録し、`manual_finished` の書き手に `workflow` を、`outputs_recorded` の `from` に `failed` を足す | — | — | — |
| `src/ndf/core/route/__init__.py` | 変更 | `immediate_fix` の説明を消す | — | — | — |
| `src/ndf/progress/lane/__init__.py` | 変更 | `add_row`・`carry_issues` が行の判定を `row_errors` で確かめ、繰り越しで判定を写す | `judgment` | モジュール | `add_row`・`carry_issues`（変更） |
| `contracts/workflow/modes/hotfix.json` | 新設 | `hotfix` の工程の並び | 工程の定義 | — | — |
| `contracts/workflow/modes/legacy-refactor.json` | 変更 | 先頭に工程「要求と受け入れ条件」を足す（判定の場所） | 工程の定義 | — | — |
| `contracts/workflow/stages/post-release-review.json` | 新設 | 工程「リリース後レビュー」の定義（入力 `pr`・`verification`、出力 `post_review`） | `cross-review` のテンプレート | — | — |
| `contracts/workflow/stages/release-verification.json` | 変更 | 出力に `verification`（`result` は `pass`・`fail`・`hold`、`evidence`）を足す | — | — | — |
| `contracts/workflow/stages/requirements.json` | 変更 | 判定の作業 `mode` のステップと出力 `mode_judgment` | `kinds.json` の `mode` | — | — |
| `contracts/workflow/context.schema.json` | 変更 | `gated_mode` に `hotfix`、`mode_judgment`・`mode_check`・`rerun_floor`・`verification`・`post_review` の形 | — | — | — |
| `contracts/workflow/parallel-plan.schema.json` | 変更 | `kind: sprint` の行に必須の `judgments`（課題ごとのモードと根拠） | — | — | — |
| `contracts/worker/kinds.json` | 変更 | 作業の種類 `mode`（`excerpt` は `skills/development-workflow/references/mode-criteria.md`、出力 `mode_judgment`） | — | — | — |
| `src/ndf/progress/judgment.py` | 新設 | 判定の記録を確かめ、スプリントのモードを決め、工程表の作り直しの下書きを作る | `definition`・`stages`・`model` | モジュール | `sprint_mode`・`recheck`・`check`・`row_errors`・`redefine` |
| `src/ndf/progress/store.py` | 変更 | 定義の改訂を足す | — | モジュール | `append_definition`（新設） |
| `src/ndf/progress/command.py` | 変更 | `new` の `--mode` を省略できる参考の値にし、`--requirements` と `finish` で判定を当てる | `judgment` | モジュール | `_sprint_new`・`_prepare_new`・`_cmd_finish`・`parser`（変更） |
| `src/ndf/progress/run.py` | 変更 | 工程が流れて終わったときに判定を当て、判定の無い後の工程を断る | `judgment` | モジュール | `_finish_stage`（変更）・`_judged`（新設） |
| `src/ndf/progress/plan/cli.py` | 変更 | `lanes init`・`lane add` が計画の行の `judgments` を `judgment.row_errors` で確かめ、`row add` に `--judgments` を足す | `judgment` | モジュール | `lanes init` の検査・`_row_add`（変更） |
| `src/ndf/progress/lane/launch.py` | 変更 | 行の判定から決めたモードを参考の値として渡し、`DEFAULT_MODE` を消す | `judgment` | モジュール | `start_commands`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | マージ済みの PR を直さずに読むラウンド | 既存の `run` | モジュール | `run` の引数 `--post-release`（変更） |

## 判定の記録

満たす決定: 決定 2・3、受け入れ条件 4・6・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/progress/judgment.py` | 新設 | `check(record: Mapping[str, Any], issues: Sequence[int], modes: Sequence[str]) -> list[str]` | 判定の記録の形と中身の誤りを並べる |
| `sprint_mode` | `src/ndf/progress/judgment.py` | 新設 | `sprint_mode(per_issue: Mapping[int, str], heights: Mapping[str, int], floor: Optional[str] = None) -> str` | 課題ごとのモードからスプリントのモードを決める。`floor`（文脈の `rerun_floor.mode`）より低くしない |
| `recheck` | `src/ndf/progress/judgment.py` | 新設 | `recheck(sprint: str, diff_mode: str, heights: Mapping[str, int]) -> Optional[str]` | 完了判定で差分から得たモードと、スプリントのモードを比べ、流し直す先のモードか `"split"` を返す（移らなければ `None`） |
| `redefine` | `src/ndf/progress/judgment.py` | 新設 | `redefine(prev: Revision, record: Mapping[str, Any], new_snap: stages.Snapshot, declared: Mapping[str, Mapping[str, Any]]) -> Optional[Redefined]` | 判定のモードが今の定義と違えば、改訂 `rev + 1` の定義の文書と、引き継ぐ事象の下書きを返す |
| `append_definition` | `src/ndf/progress/store.py` | 新設 | `append_definition(conn: sa.Connection, machine_id: str, rev: int, definition: dict[str, Any]) -> None` | 定義の改訂を 1 つ足す |

- `check`
  - 事前条件: `record` は工程「要求と受け入れ条件」の出力の `mode_judgment`
  - 事後条件: 空のリストなら、課題の組がスプリントの課題と等しく、どの課題もモードの値と空でない根拠を持つ
- `sprint_mode`
  - 事前条件: `check` が空を返した
  - 事後条件: 設計文書の「スプリントのモード」の表のとおり。`hotfix` が返るのは、スプリントの課題が全部で 1 つで、その課題が `hotfix` のときだけである。課題が 2 つ以上あれば、`hotfix` の課題は `standard` として比べる（例: `hotfix` 1 件と `light` 1 件 → `standard`）。`documentation` とほかが混ざれば `JudgmentError`（`judgment.py` に置く `ValueError` の子）を投げる
  - `floor` があれば、まず `hotfix` の課題を `standard` として数え（流し直しのスプリントは即時修正にしない）、その結果と `floor` の高い方を返す。`floor` は `recheck` の戻り値なので `hotfix` にならない。したがって `floor` があるとき `hotfix` は返らず、課題 1 つの条件を破らない
- `recheck`
  - 返す値は「流し直す先のモード」で、比べたモードと同じものを返す。`hotfix` は返さない
  - 1. `documentation` が片方だけにあれば（スプリントが `documentation` で差分にコードやリポジトリの文書がある、またはその逆）、高さを比べずに `"split"` を返す。文書とコードは混ぜずに分ける今の規則のためで、モードを上げても解けない
  - 2. スプリントが `hotfix` なら、差分のモードが `hotfix` でなければ、高さによらず差分のモードを返す（4 条件を外れた即時修正は、高さが同じ `standard` でも止める）
  - 3. それ以外は、差分のモードを比べる形に直してから高さを比べる。差分が `hotfix` なら `standard` に直す（課題 1 つの即時修正の資格は、流し直しで判定し直すまで分からないため）。直したモードがスプリントのモードより高いときだけ、直したモードを返す
- `redefine`
  - 入力: 呼び手（`_cmd_finish`・`_finish_stage`）が 1 つのトランザクションの中で集めて渡す。`judgment.py` は `store` も `cwd` も読まない

| 引数 | 中身 | 呼び手が取る所 |
| --- | --- | --- |
| `prev: Revision` | 改訂 `rev` の番号、保存済みの定義の文書（`context`・`enablement`・`judgment` を含む）、工程ごとの状態（`model.fold`）、工程ごとの最後の出力、スプリントが明示した無効化（下の「改訂の工程の有効状態」の 2） | `run.View`（`rev`・`doc`・`progress`・`events`）。工程ごとの最後の出力は `stages.recorded_outputs` と同じ畳み方で工程ごとに取る |
| `record` | 今回の完了で書く要求の工程の出力（`mode_judgment` を含む）。まだ保存していないので、`prev` の出力とは別に渡す | `finish` の `--output` か、工程を流した結果 |
| `new_snap` | 判定のモードの定義 | `definition` で `contracts/workflow/modes/<モード>.json` を読む |
| `declared` | `.ndf/project.json` の `workflow.stages` | `command._declared(cwd)` |

  - 返す値 `Redefined`: `doc`（改訂 `rev + 1` の定義の文書。`judgment: "required"` を `prev` から写す）と `drafts`（改訂 `rev + 1` の事象の下書き）
  - 事後条件: モードが同じなら `None`。違えば、新しいモードの定義を `rev + 1` の改訂として返す。要求の工程の出力は `prev` の出力ではなく `record` を使う（今回の完了の出力はまだ保存されていないため）
  - 引き継ぎ: 下の「改訂で引き継ぐもの」の表のとおりに、改訂 `rev + 1` の定義の文書と事象の下書きを作る。前の改訂の事象は履歴に残る
  - 副作用: 無し（書くのは呼び手）

#### 改訂で引き継ぐもの

改訂 `rev + 1` の直後に `ndf progress next` が読む値は、すべてこの表の行から来る。`run.View.context()` は最新の改訂の定義の `context` に、同じ改訂の `outputs_recorded` だけを重ねるので、前の改訂の出力は写さなければ読めない。

| 引き継ぐもの | 改訂 `rev + 1` での値 |
| --- | --- |
| 定義の `context`（文脈の初期値） | 改訂 `rev` の `context` を写し、`mode` を判定のモードに置き換え、`mode_judgment`（判定の記録）を足す。`issues`・`pace`・`mvv_ref`・`--carry` で写したキー・`rerun_floor` はそのまま |
| 定義の印 `judgment` | 改訂 `rev` の値（`"required"`）を写す |
| 工程の有効状態 | 下の「改訂の工程の有効状態」の手順で決め、定義の `enablement` に書く |
| 工程の状態 | 両方の定義にある工程のうち、終えた工程だけを `manual_finished`（理由「改訂 `rev` から引き継いだ」、payload に元の出力）で引き継ぐ。無効の工程は引き継がず、新しい定義で決め直す（下の「改訂の工程の有効状態」）。工程「要求と受け入れ条件」だけは例外で、改訂 `rev` の `mode` が要求仕様を書かないモード（`hotfix`・`legacy-refactor`）で、判定のモードが書くモードのときは引き継がず `pending` に残す。この工程の流し直しの扱いは下の「判定を済ませた要求の工程の流し直し」 |
| 工程の出力 | 引き継いだ工程の出力（改訂 `rev` の最後の `outputs_recorded` の payload）を、改訂 `rev + 1` の `outputs_recorded` として下書きする |
| `mode_decided` | 判定した改訂 `rev` に書き、`rev + 1` には書かない。後の工程の確かめ（`_judged`）は事象を探さず、`View.context()` の `mode_judgment` を見る |

#### 改訂の工程の有効状態

正式なモードの工程表に初めて現れる工程も、新しく作るときと同じ規則で有効か無効かを決める。手順は次の順で、`_prepare_new` と同じ関数を使う。

1. 判定のモードの定義から `stages.Snapshot` を作る
2. スプリントが明示した無効化の意図を集めて `disables` にする。改訂 `rev` の状態ではなく、意図を入力にする。意図とは、作成のときの `--disable`（改訂 `rev` の `disabled` の事象で payload の `source` が `sprint` のもの）と `ndf progress disable` の事象（後の `enabled` で取り消したものを除く）である。新しい定義に無い工程は落とす。`source` が `project` の無効は意図に入れず、3 で `.ndf/project.json` から決め直す
3. `stages.resolve_enabled(新しい定義, None, declared, disables)` で決める。承認ゲートを持つ工程か、判定を持つ工程（下の「要求の工程は無効にできない」）を無効にしようとしていれば、`Refused` を投げる
4. `Refused` なら判定を書かずに断る（「要求と受け入れ条件」の工程は終わらない）。利用者は `.ndf/project.json` かスプリントの外し方を直して打ち直す
5. 下書きは工程ごとに次のどれか 1 つにする。`disabled` は 1 つの工程に 1 回までである

| 新しい定義での有効状態 | 改訂 `rev` で終えたか | 改訂 `rev + 1` の下書き |
| --- | --- | --- |
| 有効 | 終えた | `created`、`manual_finished` |
| 有効 | 終えていない | `created` だけ（`pending`） |
| 無効 | 終えた | `created`、`manual_finished`（終えた事実を優先し、無効にしない） |
| 無効 | 終えていない | `created`、`disabled`（payload の `source` は `project` か `sprint`） |

#### 要求の工程は無効にできない

判定を求めるステートマシン（印 `judgment: "required"`）では、工程「要求と受け入れ条件」を判定の前に無効にできない。無効にすると判定の作業が一度も流れず、後の工程が `_judged` で断られるためである。承認ゲートと同じく、3 つの入口すべてで断る。

| 入口 | 扱い |
| --- | --- |
| `ndf progress new --disable 要求と受け入れ条件=…` | `refused`、「判定のある工程は無効にできない。要求を先に書いたなら `--requirements` で渡す」 |
| `ndf progress disable <ID> 要求と受け入れ条件` | 同上 |
| `.ndf/project.json` の `workflow.stages` で無効 | `_declared` の検査で同上。印の無い旧いステートマシンには当てない |

要求を別に書いた利用者の経路は `--requirements` である（「処理の手順」）。要求の工程は判定の作業だけを流して終わるので、判定は必ず通る。

#### 判定を済ませた要求の工程の流し直し

改訂 `rev + 1` で `pending` に戻した要求の工程は、文脈に `mode_judgment` があるので判定の作業 `mode` を飛ばし、判定のモードの形で要求仕様だけを書く。この工程を終えるときは、判定は改訂 `rev` で済んでいる。

- `_cmd_finish`・`_finish_stage` は、文脈（`View.context()`）に `mode_judgment` があれば `check`・`mode_decided`・`redefine` を行わない。出力に `mode_judgment` が無くても断らない
- 出力に `mode_judgment` があっても、文脈の値と違えば断る（判定のやり直しはこの経路では受けない）

#### 事象の登録と書く順

`model.check_event` は `contracts/workflow/progress.json` に無い事象を断るので、次を登録する。

| 事象 | 書き手（`actors`） | スコープ | 状態遷移 |
| --- | --- | --- | --- |
| `mode_decided`（新設） | `workflow`・`conductor` | `table` | 変えない（`mode_raised` と同じ記録だけの事象） |
| `manual_finished`（既存） | `workflow` を足す（改訂の引き継ぎを `_finish_stage` が書くため） | 今のまま | 今のまま |
| `outputs_recorded`（既存） | 今のまま | 今のまま | `from` に `failed` を足す（失敗にした工程が `mode_check` を残すため。「完了判定の照らし直しと流し直し」） |

判定のモードが違うときは、1 つのトランザクションで次の順に書く。同じときは 1 から 3 だけを書く。

1. 改訂 `rev`: 要求の工程の `outputs_recorded`（`mode_judgment` を含む）
2. 改訂 `rev`: `mode_decided`
3. 改訂 `rev`: 要求の工程の `finished`
4. `append_definition` で改訂 `rev + 1` の定義
5. 改訂 `rev + 1`: 工程表の順に、工程ごとに「改訂の工程の有効状態」の 5 の表の下書きと、引き継ぐ工程の `outputs_recorded`

#### 判定を求めないステートマシン

この変更の前に作られたステートマシンと、工程を持たないステートマシンには判定を求めない。版上げの前に要求の工程を過ぎたスプリントが、版上げの後も同じ所から続けられるようにするためである。

| ステートマシン | 見分け方 | 扱い |
| --- | --- | --- |
| この変更の後に作った工程のあるもの | 定義の文書に `judgment: "required"` がある（`_new_doc` が工程を持つ定義にだけ書く） | 下の表の分岐をすべて通す |
| この変更の前に作ったもの | 定義の文書に `judgment` が無い | 下の表の分岐をどれも通さず、今のとおり流す。終えた工程・人の回答（`answered`）はそのまま読む。改訂も足さない |
| 開発ループの行（`--loop`、`LOOP_SNAPSHOT`） | 工程を持たない。`_loop_new` は `_new_doc` に印を付けさせない | 判定しない。ループの周のモードは今のとおり `loop sprint --mode --basis` が記録し、その周で作るスプリントの `--mode` に参考の値として渡る |

印で分ける箇所は次の 4 つで、どれも `_cmd_finish`（手で終える経路）と `_finish_stage`（工程を流して終える経路）の両方に置く。

| 分岐 | 印がある | 印が無い |
| --- | --- | --- |
| 判定の作業 `mode` を流すか | 流す（定義に保存した要求の工程のステップに `mode` がある） | 流さない（版上げの前に保存したステップに `mode` は無い。ステップは定義の改訂に保存した `stages` から読むので、版上げで増えない） |
| 要求の工程の出力に `mode_judgment` を求めるか（`check`） | 求める（上の流し直しを除く） | 求めない。今の出力の規約だけで確かめる |
| `mode_decided` を書き、改訂を足すか（`redefine`） | 書く・足す | どちらもしない |
| 後の工程を流す前の確かめ（`_judged`） | 確かめる | 確かめない |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 判定の記録が無い | 印のあるステートマシンで、文脈にも要求の工程の出力にも `mode_judgment` が無い | `_cmd_finish`・`_finish_stage`・`_prepare_new`（`--requirements`） | 止める | `refused`、「判定の記録が無い」。終了コード 1 | 何も書かない。出力を直して打ち直せる |
| `check` | 根拠が空・知らないモード・課題の組の食い違い | worker の出力の誤り | 同上 | 止める | `refused` に誤りの一覧 | 同上 |
| `sprint_mode` | `documentation` の混ざり | 計画の切り方の誤り | 同上 | 止める | `refused`、「documentation はほかのモードと混ぜない。スプリントを分ける」 | 何も書かない。スプリントを中止して分け直す |
| `append_definition` | 改訂の番号がぶつかる | 同時の書き込み | `store` の一意の制約 | 呼び元へ返す | 既存の `Conflict` の扱い（読み直して書き直す回数の方針 `contracts/limits/` の方針 `progress.append`） | 読み直して再試行できる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_cmd_finish`・`_finish_stage` | 印が無い、または文脈に `mode_judgment` が既にある | 今のとおり出力の事象と `finished` だけを書く（上の「判定を求めないステートマシン」と「判定を済ませた要求の工程の流し直し」） |
| `_cmd_finish`・`_finish_stage` | 印があり、工程が「要求と受け入れ条件」で、判定のモードが今の定義と同じ | 出力の事象と `mode_decided`（判定の記録・参考の値）を書く |
| 同上 | 同上で、判定のモードが違う | 上に加え、`append_definition` で改訂を足し、`redefine` の下書きを新しい改訂で書く。結果 JSON の `items` に `{"kind": "mode_changed", "hint": <参考の値>, "mode": <判定の値>}` を 1 行足す |
| `run.py` の工程を流す前の確かめ（`_judged`） | 定義に `judgment: "required"` があり、要求の工程より後の工程で、`View.context()` に `mode_judgment` が無い | 流さずに `refused`、「判定の記録が無い。要求と受け入れ条件を終える」 |
| `_prepare_new` | `--requirements` の `mode_judgment` に出所（`source: {"machine": <ID>, "seq": <mode_decided の事象の番号>}`）がある | 出所のステートマシンをストアから読み、その事象が `mode_decided` で、判定の記録の sha256 と課題の組が `--requirements` と `--issues` に一致するときだけ受ける。判定のモードの定義で改訂 1 を作り、`mode_decided` を最初の事象に含める。`--mode` は参考の値として文脈の `mode_judgment.hint` に残す |
| `_prepare_new` | `--requirements` に `mode_judgment` があるが出所が無い・一致しない | `refused`、「判定の記録は判定の作業で作る。出所の無い判定は受けない」。手で書いた JSON にモードを書くだけで判定を飛ばす経路を作らない |
| `_prepare_new` | `--requirements` に `mode_judgment` が無い | 判定が済んでいないので、要求の工程を終えたものにはしない。`--mode`（省けば `standard`）の仮の改訂 1 を作り、`--requirements` の出力を文脈に置いて、要求の工程の行を `pending` に残す。この工程を流すと、要求の文書は文脈にあるので判定の作業 `mode` だけを通す |
| `_prepare_new` | `--requirements` が無い | `--mode`（省けば `standard`）の定義で仮の改訂 1 を作る |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `mode_judgment` | 型（`issues: [{issue, mode, basis}]`・`mode`・`hint`・任意の `source`）。判定の作業が書き、`check` を通った後にだけ文脈に入る | `contracts/workflow/context.schema.json` | — |
| `mode_check` | 型（`mode`・`basis`・`diff: {base, head}`・`split: bool`）。完了判定と `operation` の適用前の確かめが、工程を失敗にするときに書く | `contracts/workflow/context.schema.json` | — |
| `rerun_floor` | 型（`mode`・`basis`・`diff`・`from`（写し元のステートマシンの ID））。流し直しのスプリントの文脈の初期値。判定ではないので `_judged` は見ない | `contracts/workflow/context.schema.json` | — |
| `MODE_DECIDED` | 定数（事象の種類 `mode_decided`）。事象の登録は `contracts/workflow/progress.json`（上の「事象の登録と書く順」） | `src/ndf/progress/judgment.py` | — |
| `judgment` | 定義の文書の項目（`"required"`）。判定を求めるステートマシンの印 | `src/ndf/progress/command.py` の `_new_doc` | — |
| 仮の工程表の既定のモード | 定数 | `src/ndf/progress/command.py` | `standard`（判定ではない。判定の前に後の工程は流れない） |
| モードの高さ | 設定値 | `contracts/workflow/modes/*.json` の `metadata.ndf.height`。`hotfix.json` は `mixes_as: "standard"` を持ち、高さは `standard` のものを読む | 今の値 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `finish` と `next` | 同じステートマシンの定義と事象 | 今の `run_lock` と事象の番号の一意の制約。改訂の追加（`append_definition`）・引き継ぎの事象・`mode_decided` は、今の `_append` の 1 つのトランザクションでまとめて書く。途中で落ちればどれも書かれない | `mode_decided` は `dedupe_key`（ステートマシン・改訂・判定の記録の sha256）で 1 回だけ書く。改訂を足した後に落ちても、打ち直すと同じ判定は既に改訂済みとして `redefine` が `None` を返す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_cmd_finish`・`_finish_stage` | `mode_decided`（課題ごとのモードと根拠・スプリントのモード・参考の値） | 進捗の事象 | 課題の本文 |
| 同上 | `mode_changed`（参考の値 → 判定の値） | 結果 JSON の `items` の 1 行（上の形） | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `sprint_mode` | 開発ループの `decide_mode`（`domains/loop/control_decide.py`） | `grep -rn "def decide_mode"` | 使えない。ループの周のモードを根拠つきで記録するだけで、課題ごとの組から決める規則を持たない |
| `redefine` | 文書を省いたときの流し直し（`--carry`） | `command.py` の `_carried` を読んだ | 使えない。別のステートマシンを作る経路で、決定 4 で採らなかった |
| `append_definition` | `create_machine` の改訂 1 の書き込み | `store.py` を読んだ | 同じ表へ書く形を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 2 課題とも根拠つき | 課題 1 つ | 判定の記録が無い・根拠が空白だけ・知らないモード・課題の組が違う |
| `sprint_mode` | `light`+`standard` → `standard`、`operation`+`light` → `operation` | `hotfix` 1 件 → `hotfix`、`hotfix` 2 件 → `standard`、`light` 1 件で `floor` が `standard` → `standard`、`hotfix` 1 件で `floor` が `light` → `standard` | `documentation`+`light` → `JudgmentError` |
| `recheck` | `light` のスプリントで差分が `standard` → `standard` | `hotfix` のスプリントで差分が `standard` → `standard`（高さが同じでも返す）、差分が `hotfix` → `None`。`light` のスプリントで差分が `hotfix` → `standard` | `hotfix` のスプリントで差分が `operation` → `operation`。`documentation` のスプリントで差分にコード → `"split"`。`standard` のスプリントで差分に `documentation` の文書 → `"split"` |
| `redefine` | `standard` → `light` で、終えた要求の工程を引き継ぎ、設計の行が消える。改訂 `rev + 1` の文書が `judgment`・`mode_judgment` と、今回の要求の出力を持つ | 同じモード → `None`。`legacy-refactor` へ変えても、その定義も要求の行を先頭に持つので、終えた要求の工程を引き継ぐ。`--disable` で外した工程は改訂 `rev + 1` でも無効で、`disabled` は 1 回だけ | 新しく現れる承認ゲートの工程を `.ndf/project.json` で無効にしてある → `Refused` |
| `_judged` | 判定の後は後の工程が流れる | 要求の工程そのものは判定の前でも流れる。印の無い定義は判定が無くても流れる | 判定の前に後の工程を `next` で流すと `refused` |
| 流し直した要求の工程の終了 | 文脈に `mode_judgment` があれば、出力に無くても終わり、`mode_decided` を書かない | — | 出力の `mode_judgment` が文脈と違う → `refused` |
| 要求の工程の無効化 | — | 印の無い定義では今のとおり無効にできる | 印のある定義で `--disable`・`progress disable`・`workflow.stages` の無効 → `refused` |

## 完了判定の照らし直しと流し直し

満たす決定: 決定 9

完了判定が差分から得たモードは、次のスプリントの判定の下限として渡す。下限が無いと、要求が同じなので次の判定もまた低いモードを選び、同じ完了判定で落ち続ける。

### `mode_check` を残して失敗にする

今の `run.py` は、止まったステップ（`STOPPED`）を理由とステップだけの `failed` に変え、出力は工程が正常に終わったときにしか書かない。そこで、モードの照らし直しで止めるときは、ステップを止めずに出力を返し、工程の側で失敗にする。

| 経路 | 書き方 |
| --- | --- |
| 工程を流す経路（`_finish_stage`） | 完了判定のステップは `ok` で終わり、報告に `mode_check` を入れる。`_finish_stage` は工程の出力に `mode_check` があれば、`finished` の代わりに、1 つのトランザクションで `failed`（理由の種類 `mode_raised`）、`outputs_recorded`（`{"mode_check": …}`）の順に書く |
| 手で終える経路（`ndf progress finish`） | `--output` の JSON に `mode_check` があれば、工程が `running` か `failed` のときだけ受け、上と同じ 2 つの事象を書く。`pending` なら `refused`（先に `next` で流す） |
| 読む側 | `--carry` は写し元の文脈（`command.machine_context`）から読む。文脈は状態によらず `outputs_recorded` を重ねるので、失敗した工程の `mode_check` も読める |

`mode_check` の形は「内部のデータと定数」にある。`operation` の適用前の確かめも、工程「実装」のステップが同じ形で返し、同じ書き方で失敗にする。

### 流し直し

| 時点 | すること |
| --- | --- |
| 完了判定 | 差分を `mode-criteria.md` の基準で判定し、`recheck(スプリントのモード, 差分のモード)` を呼ぶ。`None` でなければ、上の書き方で `mode_check`（`mode` は `recheck` の戻り値、`basis`、`diff` は判定に使った比較元と比較先の SHA）を残して失敗にする。戻り値が `"split"` なら `mode_check.split` を真にし、`mode` は差分のうち分ける側のモードにする |
| 分ける（`split`） | モードを上げても解けないので、流し直さない。conductor は分ける側の変更を今のブランチから外し、`out-of-scope` の手順で起票する（その課題は計画で判定する）。残った差分で完了判定を `ndf progress retry` で流し直す |
| 流し直しの作成 | conductor は今の「途中でモードが変わったとき」と同じく、元のステートマシンを中止し、`ndf progress new --carry <元> --mode <mode_check のモード> --disable 作業場所の用意=元の作業場所を使う` で作る。作業場所の工程の出力 `worktree`・`branch` は、無効にした工程の出力として今の `_carried` が写し元の文脈から写すので、元のブランチの変更を持つ作業場所で後の工程が流れる |
| 下限の受け渡し | `_prepare_new` は写し元の文脈に `mode_check` があれば、新しい文脈の初期値 `rerun_floor`（`mode`・`basis`・`diff`・`from`）に写す。`mode_judgment` には入れない。`_judged` は `mode_judgment` だけを見るので、下限しか無いスプリントは判定済みにならない |
| 流し直しの判定 | 工程「要求と受け入れ条件」の判定の作業は、`rerun_floor` の根拠と差分の範囲を入力に読み、課題ごとの判定を書く。`check` を通った後、スプリントのモードを `sprint_mode(…, floor=rerun_floor.mode)` で決め、`mode_judgment` を文脈に入れる |

### `operation` の外部への適用の前の確かめ

`operation` は工程「実装」の中で外部の系を変えるので、完了判定を待つと、判定より先に外部へ適用されてしまう。そこで、適用の前に、適用する内容そのものを判定し、判定した内容だけを適用する。

| 外部の系 | 適用の前に判定するもの | 適用するもの |
| --- | --- | --- |
| dry-run を持つ（IaC の `terraform plan` など） | [operation-run.md](../plugins/ndf/skills/development-workflow/references/operation-run.md) の dry-run が出す実行計画。リソース・モジュールの追加や削除、構成の変更が出ていないかを、`mode-criteria.md` の `operation` の問い（変えるのが値だけか）で判定する | 判定した実行計画をファイルに保存し（`terraform plan -out` など）、その保存した計画だけを適用する。適用の時点で計画を作り直さない |
| dry-run を持たない（権限・リポジトリの設定など） | 変える前の今の設定の値と、リクエストで指定する予定の値を並べる。対象、変わる項目、確かめられる副作用を確かめ、値以外の変更（項目の追加や削除など）が含まれないことを判定する | 判定したリクエストをそのまま適用する。適用の後に、実際の値を測って記録する |

値以外の変更が出たら、適用せずに工程「実装」を失敗にし、`mode_check` に `standard` と根拠（実行計画やリクエストの該当箇所）を残す。後は上の流し直しと同じである。これは今の operation-run.md の dry-run と記録の手順に、判定と、判定した内容の固定を足すものである。

## 作業中に見つけた不具合

満たす決定: 決定 6、受け入れ条件 1

今の `out-of-scope` と worker の規則 7 は、4 条件を満たす不具合を起票せず、その場で直して続ける。この設計では「即時修正」をモード `hotfix` の名前に使うので、作業の途中の直しは別の名前で扱い、`hotfix` とは経路を分ける。

| 場合 | 経路 |
| --- | --- |
| 今の作業を妨げる不具合で、4 条件を満たし、今の作業場所の中で直せる | 「作業中の直し」として今の変更に含めて直し、報告の「見つけたもの」に書く（今の worker の規則 7 のまま）。同じ PR に入るので、完了判定の照らし直し（`recheck`）とレビューを今の変更と一緒に通る |
| 今の作業を妨げる不具合で、4 条件を満たすが、今の作業場所の中では直せない（別のリポジトリ・別の作業場所・依存先） | その場所を今の作業から編集しない。`out-of-scope` の手順で直す先のリポジトリへ起票する（別のリポジトリへの起票で承認が要るなら、書き先・題・本文の案を添えて止まる）。起票した課題 1 つで、直す先の作業場所に `hotfix` のスプリントを始める。元のスプリントは今の工程を `結果: 判断が要る` で止め、止めた理由に起票した課題を書く。直しがリリースされ、元の作業場所から使える版になったら、conductor が元の工程を `ndf progress retry` で再開する |
| 今の作業を妨げない不具合で、4 条件を満たす | `out-of-scope` の手順で起票し、その課題 1 つで `hotfix` のスプリントを始める（課題の本文に再現の手順と受け入れ条件を書く） |
| 4 条件を満たさない | 今のとおり `out-of-scope` の 3 択（起票・追記・見送り）。起票した課題は計画で判定する |

旧い経路は残さない（Value 4 により後方互換は求めない）。

| 旧いもの | 扱い |
| --- | --- |
| テンプレート `contracts/templates/fix.json`（`immediate_fix`） | 消す。課題番号の無いプランで判定とリリース後レビューを通らずにマージされる経路になるため |
| `ndf route render immediate_fix` の案内（`conductor-entrypoints.md`、`src/ndf/core/route/__init__.py` の説明） | 消し、`hotfix` のスプリントの始め方へ置き換える |
| SKILL.md の「即時修正」を指す参照（`out-of-scope/SKILL.md`・`agents/worker.md`・`agent-layers.md` の規則 7・`release-verification/SKILL.md`） | `mode-criteria.md` の 4 条件と、上の表の「作業中の直し」を指す |

## レーンの起動と計画の行

満たす決定: 決定 7、受け入れ条件 5・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `start_commands` | `src/ndf/progress/lane/launch.py` | 変更 | 変えない | 行の `input.judgments` から `judgment.sprint_mode` で決めたモードを `--mode` に渡す。`DEFAULT_MODE` と、`args` の `--mode` を見る分岐を消す |
| `row_errors` | `src/ndf/progress/judgment.py` | 新設 | `row_errors(issues: Sequence[int], judgments: Sequence[Mapping[str, Any]], heights: Mapping[str, int]) -> list[str]` | 計画の行の課題ごとの判定の誤りを並べる。課題の組の食い違い・根拠が空・知らないモード・`documentation` とほかの混ざり（`sprint_mode` の `JudgmentError`）を誤りにする |
| `add_row`・`carry_issues` | `src/ndf/progress/lane/__init__.py` | 変更 | 変えない | 足す行の `input` を `row_errors` で確かめ、誤りがあれば `Refused` |

計画の行は、課題ごとの判定をそのまま持つ。行のモードは計画を書く者が書かず、コードが課題ごとの判定から決める（決定 3 と同じ規則）。こうすると、`documentation` と他のモードの混ざりを計画の登録の時点で断れる。

- スキーマ `parallel-plan.schema.json` の `kind: sprint` の行は `judgments`（`[{issue, mode, basis}]`。`issues` の課題ごとに 1 つ）を必須にし、`args` に `--mode` を書くことを断る（同じ値を 2 か所に持たない）
- 行を作る入口は 4 つで、どれも同じ `row_errors` を通す。誤りがあれば行を作らない

| 入口 | 判定の受け取り方 |
| --- | --- |
| `ndf lanes init`・`ndf lanes start` | 計画の設定の各行の `judgments` |
| `ndf lanes lane add <計画> <lane.json>` | `lane.json` の各行の `judgments` |
| `ndf lanes row add` | 新しい引数 `--judgments <JSON のファイル>`（必須）。`input` は `{"kind": "sprint", "issues", "judgments", "args"}` |
| `ndf lanes row carry` | 元の行の `judgments` から、移す課題の分だけを写す。元の行に判定が無い課題は断る |

- `hotfix` で課題が 2 つ以上の行は断らない。`sprint_mode` が `standard` を返し、その値でスプリントを始める（決定 3）。計画を書く者には `ndf lanes init` の結果の `warnings` に「`hotfix` の課題 2 つは `standard` で流れる。分けるなら行を分ける」を出す
- 判定はレーンのステートマシンの `row_added` の payload（`input`）に残るので、再起動・セッションの入れ替えの後も `start_commands` が同じ値を読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| 行を作る 4 つの入口 | 行に `judgments` が無い | 計画を作る手順で判定していない | スキーマの検査 | 止める | `invalid` に行の場所。終了コード 2 | 計画の設定か引数を直して打ち直せる（設定はコミットしない入力） |
| 同上 | 課題の組の食い違い・根拠が空・知らないモード・`documentation` の混ざり | 判定の誤り・切り方の誤り | `row_errors` | 止める | `invalid`、誤りの一覧 | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `start_commands` | 判定が `light`・`light` の行 → `progress new ... --mode light` | `hotfix` 2 件の行 → `--mode standard` | `args` に `--mode` を持つ行はスキーマで断られ、ここへ来ない |
| `row_errors` | `light` で課題 3 つ → 空 | 課題 1 つ | 判定の課題が `issues` と違う・`documentation`+`light` |
| `add_row`・`carry_issues` | 判定のある行を足す・移す | 元の行の判定を移す課題の分だけ写す | `judgments` の無い `row add` を断る |

## 工程「リリース後レビュー」

満たす決定: 決定 6、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `run` の `--post-release` | `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review run --pr N --state-dir D --base B --post-release --verification V --findings-out F` | マージ済みの PR の差分を相手役が 1 ラウンド読み、修正担当はコミットせずに指摘を分ける。`--verification` はリリース後テストの結果（下の `verification`） |

- 事前条件: PR がマージ済み
- 読む差分: PR の head ではなく、ベースブランチに実際に取り込まれた変更を読む。マージのときに競合を解いた変更も含めるためである。比較元と比較先は、`gh pr view --json mergeCommit,mergeStateStatus` とベースブランチの履歴からマージの方式ごとに決め、`--base` には比較元を渡す。読む場所は比較先を取り出した作業場所（`--worktree`）である

| マージの方式 | 比較元（`--base`） | 比較先 |
| --- | --- | --- |
| マージコミット（マージキューを含む） | マージコミットの 1 つ目の親 | マージコミット |
| squash | squash のコミットの親 | squash のコミット |
| rebase | ベースブランチに載った最初のコミットの親 | ベースブランチに載った最後のコミット |

- マージ前のレビューとの違い: 今の `cross-review` は、PR の head と比較元の merge-base から差分を取る。`--post-release` だけが上の表の比較先を読む
- 事後条件: `--findings-out` に指摘ごとの次の項目の配列を書き、PR に 1 件のコメントを残す。ラウンドは 1 回で、`--max-rounds` は受けない
  - `id`（指摘の ID）・`outcome`（`revert`・`fix`・`skip`）・`reason`（分けた理由）
  - `title`・`body`（相手役の指摘の本文）・`evidence`（根拠のファイルと行）
  - `pr`（PR の番号）・`commit`（上の表の比較先の SHA）
- 副作用: PR へのコメント 1 件。push はしない
- 工程の定義 `post-release-review.json` は、入力に `pr` と `verification`（リリース後テストの出力）を持つ。この結果から出力 `post_review`（分け方ごとの件数と、指摘ごとの起票した課題の番号）を作る。`revert` と `fix` の起票は `out-of-scope` の起票の手順を使い、課題の本文には指摘の `title`・`body`・`evidence`・`pr`・`commit` を載せる。工程は起票で終わり、戻し・直しのスプリントを待たない

#### 再開の正本と起票の重複の防ぎ方

再開のときに読む正本は、このスプリントの実行記録（`--state-dir` の結果のファイルと、進捗の DB の工程の出力）だけである。PR のコメントは人が読むための写しで、再開では読まない。PR にコメントできる第三者がレビュー済みの結果を装っても、レビューは省かれず、課題も作られない。

| 時点 | すること |
| --- | --- |
| 相手役の結果を得た後 | 分けた指摘の配列を `--state-dir` の結果のファイルに書き、`--findings-out` にも同じものを書く。その後で PR にコメントを 1 件書く |
| 起票の前 | 結果のファイルの指摘に `filing: {"started_at": <時刻>}` を書く |
| 起票の直後 | 作った課題の番号を、結果のファイルの指摘の `issue` に書く。工程の出力 `post_review` も同じ番号を持つ |
| 打ち直し | 結果のファイルがあれば相手役を起動せずそれを使う。`issue` のある指摘は起票しない。`filing` があって `issue` が無い指摘（起票の途中で落ちた指摘）だけ、下の照合で既にある課題を探し、あればその番号を `issue` に書き、無ければ起票する |

起票の途中で落ちた指摘の照合では、課題の本文の先頭の印 `<!-- ndf:post-release-finding pr=<PR の番号> id=<指摘の ID> -->` を探す。探し方は `gh issue list --author @me --state all --json number,body,author` である。

- この一覧はリポジトリの課題一覧（GraphQL の `repository.issues` を作成者で絞る）を読み、検索の索引を使わない。HTML コメントも本文のまま返る。作ったばかりの課題もすぐ一覧に出る（2026-11-25 に `GH_DEBUG=api` で問い合わせの形を確かめ、直前に作った課題が出ることを確かめた）
- 作成者が今の認証の利用者で、`filing.started_at` 以後に作られた課題だけを照合の対象にする。第三者が印を書いた課題は、作成者が違うので使わない

### 前の工程「リリース後テスト」からの続き

`release-verification` の SKILL.md は、振り返りを通らない変更では自分を最終工程とする。`hotfix` は振り返りを通らないが、後にリリース後レビューがあるので、同 Skill の終了の条件を次のとおり直す。

`hotfix` のリリース後テストは、工程としてはどの結果でも終わり（`finished`）、検証の結果は出力 `verification` に持つ。不合格のときの起票はリリース後レビューにまとめ、リリース後テストは起票しない。同じ不合格で直しと戻しの 2 件の課題ができないようにするためである。

| リリース後テストの結果 | 出力 `verification` | 次 |
| --- | --- | --- |
| 合格 | `{"result": "pass", "evidence": <確かめた手順と結果>}` | リリース後レビューへ進む |
| 不合格 | `{"result": "fail", "evidence": …}` | リリース後レビューへ進む。リリース後レビューは、この不合格を受け入れ条件を満たさない指摘（`id` は `verification`）として `revert` に分けて起票する |
| 保留（確かめられない） | `{"result": "hold", "evidence": <確かめられない理由>}` | リリース後レビューへ進む。レビューの結果に保留を載せ、承認資料と同じく人が読む |

「最終工程」の規則は「後に振り返りもリリース後レビューも無い変更」に直す。`hotfix` 以外のモードのリリース後テストは、不合格のときの起票も含めて今のまま変えない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `run --post-release` | PR がマージされていない | 工程の順の誤り | `gh pr view` の状態 | 止める | `refused`、「マージ済みの PR だけを読む」 | 打ち直せる |
| 同上 | 相手役が全員使えない | 未認証・未導入 | 今の起動の前の確認 | 止める | 今と同じ | 今と同じ |
| 同上 | 打ち直し | 中断の後の再開 | `--state-dir` の結果のファイル | 相手役を起動せず、結果のファイルを返す。PR のコメントは読まない。コメントがまだ無ければ書く | `ok` | 冪等 |
| 起票 | 起票の途中で落ちた | 課題を作った後、番号を書く前に落ちた | 結果のファイルの `filing` があり `issue` が無い | 上の照合で既にある課題を使う | `ok` | 同じ指摘の課題は 1 件 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `run --post-release` | 指摘 3 件が 3 つに分かれて、本文・根拠・PR・コミットとともに結果に載る | 指摘 0 件（コメントは「指摘なし」）。squash でマージした PR は squash のコミットを比較先にする | マージされていない PR・知らない分け方 |
| 起票 | `revert` と `fix` の指摘が 1 件ずつ起票される。リリース後テストの不合格が `revert` に入る | `issue` のある指摘は起票しない。`filing` だけの指摘は、自分が作った印のある課題があればその番号を使う | 第三者が作った印のある課題・PR のコメントの JSON は使わない |
