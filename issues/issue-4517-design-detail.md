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
│   ├── design/SKILL.md
│   ├── document-drafting/{SKILL.md, references/type-repo-knowledge.md *}
│   ├── document-rebuild/references/related-skills.md
│   ├── issue-plan-strategy/references/{parallel-plan.md, execution-plan.md}
│   ├── out-of-scope/SKILL.md
│   ├── quality-gates/SKILL.md
│   ├── release/SKILL.md
│   └── release-verification/SKILL.md
├── agents/worker.md
├── contracts/
│   ├── templates/fix.json（削除）
│   ├── workflow/
│   │   ├── modes/hotfix.json *
│   │   ├── stages/{document-sources,layout-review,plan-to-spec}.json
│   │   ├── stages/{design,implement,document-rebuild,deploy}.json
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

依存の向き: `judgment.py` は `definition`・`stages`・`model` だけを読み、`command.py`・`run.py`・`plan/cli.py`・`lane/__init__.py`・`lane/launch.py` から呼ばれる。`command.py` は流し直しの行の付け替えで `lane.restart_row` を呼ぶ（`lane` は `command` を読まない）。`judgment.py` は `store` を読まず、定義の改訂の書き込みは呼び手が `store.append_definition` で行う。レーンの側が使うのは計画の行の確かめ（`row_errors`）と行のモードの決め方（`sprint_mode`）だけで、正式な判定はスプリントの中で行う。循環は無い。

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
| `skills/issue-plan-strategy/references/parallel-plan.md` | 変更 | スプリントの行の `mode`・`basis` の欄と、モードで行を切る手順（基準は `mode-criteria.md` を指す）。課題の本文に印 `ndf:split-branch` があれば、行の `args` に `--from-branch` を書く手順 | — | — | — |
| `skills/issue-plan-strategy/references/execution-plan.md` | 変更 | モードの列の埋め方と、食い違いを「見直し」の表に書く手順 | — | — | — |
| `skills/development-workflow/references/operation-run.md` | 変更 | 外部へ適用する前に dry-run の結果を `operation` の問いで照らし、値の外の変更があれば適用せずに止める（「`operation` の外部への適用の前の確かめ」）。必ず人の承認が要る操作に当たる単位は検証用の系でも承認の記録まで適用せず、記録には秘密と公開していない値を書かない（「必ず人の承認が要る操作と公開しない情報」） | — | — | — |
| `contracts/workflow/stages/operation-implement.json` | 新設 | `operation` の工程「実装」の定義。`prepare`・回答を待つ `apply-gate`（`gate: release`）・`apply` の 3 ステップ。`contracts/workflow/modes/operation.json` の「実装」はこの定義を指す（「`operation` の承認の置き場所」） | 工程の定義 | — | — |
| `skills/out-of-scope/SKILL.md` | 変更 | 即時修正の参照を「作業中に見つけた不具合」の 3 つの場合へ直す | — | — | — |
| `agents/worker.md` | 変更 | 規則の「即時修正」を「作業中の直し」と `mode-criteria.md` の 4 条件へ | — | — | — |
| `skills/quality-gates/SKILL.md` | 変更 | 差分でモードを照らし直し、`recheck` が `None` でなければ出力 `mode_check` を書いて失敗で返す | — | — | — |
| `skills/release-verification/SKILL.md` | 変更 | 「最終工程」の条件にリリース後レビューを足す。`hotfix` では合否・保留によらず結果を出力 `verification` に書いて工程を終え、不合格でも起票しない（起票はリリース後レビュー） | — | — | — |
| `contracts/templates/fix.json` | 削除 | 課題番号の無い即時修正のプランを無くす | — | — | — |
| `contracts/workflow/progress.json` | 変更 | 事象 `mode_decided`・`issues_split` とレーンの行の事象 `row_restarted`・`row_issues_split` を登録し、`manual_finished` の書き手に `workflow` を、`outputs_recorded` の `from` に `failed` を足す | — | — | — |
| `src/ndf/core/route/__init__.py` | 変更 | `immediate_fix` の説明を消す | — | — | — |
| `src/ndf/progress/lane/__init__.py` | 変更 | `add_row`・`carry_issues` が行の判定を `row_errors` で確かめ、繰り越しで判定を写す。実行中の行のスプリントの ID を流し直しのスプリントへ付け替える。スプリントの始めに外した課題を実行中の行から除く。レーンを作るときに初期の行を優先の順に並べる | `judgment` | モジュール | `add_row`・`carry_issues`・`create_lane`・`fold_lane`（変更）・`restart_row`・`split_row_issues`・`order_rows`（新設） |
| `contracts/workflow/modes/hotfix.json` | 新設 | `hotfix` の工程の並び。`metadata.ndf.priority` は 1 | 工程の定義 | — | — |
| `contracts/workflow/modes/standard.json`・`light.json`・`operation.json`・`documentation.json`・`legacy-refactor.json` | 変更 | `metadata.ndf.priority` を足す（`operation` 2・`light` 3・`documentation`・`legacy-refactor` 4・`standard` 5） | — | — | — |
| `src/ndf/progress/lane/machine.py` | 変更 | 上限で待つ行を優先の順に許可する。計画ごとに、起動を待つレーンの次の行の優先を読む | `judgment`・`lane.rules` | モジュール | `PlanLanes`・`lanes_of`・`admitted`（変更）・`waiting_order`（新設） |
| `contracts/workflow/modes/legacy-refactor.json` | 変更 | 先頭に工程「要求と受け入れ条件」を足す（判定の場所） | 工程の定義 | — | — |
| `contracts/workflow/stages/document-sources.json`・`layout-review.json`・`plan-to-spec.json` | 変更 | 条件（`when`）に `outside_reader` を足す | 条件の語 | — | — |
| `skills/development-workflow/references/document-types.md` | 変更 | 「何が `documentation` に当たるか」の `README.md`・`docs/` の行を分け、タイプ「リポジトリの知識」を足す | — | — | — |
| `skills/document-drafting/references/type-repo-knowledge.md` | 新設 | タイプ「リポジトリの知識」の執筆の手引き | — | — | — |
| `skills/document-drafting/SKILL.md` | 変更 | タイプと参照先の表に「リポジトリの知識」→ `type-repo-knowledge.md` の行を足し、タイプは文脈の `mode_judgment` の課題ごとの `doc_type` から選ぶと書く | — | — | — |
| `skills/document-rebuild/references/related-skills.md` | 変更 | 「文書タイプと目的別ガイドの対応」に「リポジトリの知識」→ `purpose-decided.md` の行を足す | — | — | — |
| `skills/design/SKILL.md` | 変更 | `documentation` の行に、`doc_type` が `repo-knowledge` なら `layout-*.md` を読まず構成案だけを書くと足す | — | — | — |
| `skills/release/SKILL.md` | 変更 | 「`documentation` モードでは読まない」の例外として、`doc_type` が `repo-knowledge` のスプリントはコードのモードと同じく `release.deliveries` を読み、生成と提出の経路へ入らないと足す | — | — | — |
| `contracts/workflow/stages/design.json`・`implement.json`・`document-rebuild.json`・`deploy.json` | 変更 | `design.json`・`deploy.json` の入力（`inputs`）に `mode_judgment` を足す。`design.json` の出力と、`implement.json`・`document-rebuild.json` の入力に `documents`（文書ごとのパス・課題・タイプ）を足す（「工程へのタイプの渡し方」） | — | — | — |
| `contracts/workflow/stages/worktree.json`・`contracts/templates/worktree.json` | 変更 | 入力に任意の `from_branch` を足し、あればその名前のブランチで作業場所を作る（分けた変更の続き） | — | — | — |
| `contracts/workflow/stage-io.schema.json` | 変更 | `release-result` の `outputs.product_version` に `null`（版を作らない）を許し、`outputs.merge`（マージと届いたことの確かめの結果）を足す。`deploy.json` の出力に `merge` を足す | — | — | — |
| `contracts/workflow/stages/post-release-review.json` | 新設 | 工程「リリース後レビュー」の定義（入力 `pr`・`verification`、出力 `post_review`） | `cross-review` のテンプレート | — | — |
| `contracts/workflow/stages/release-verification.json` | 変更 | 出力に `verification`（`result` は `pass`・`fail`・`hold`、`evidence`）を足す。条件（`when`）に `outside_reader` を足す | — | — | — |
| `contracts/workflow/stages/requirements.json` | 変更 | 判定の作業 `mode` のステップと出力 `mode_judgment` | `kinds.json` の `mode` | — | — |
| `contracts/workflow/context.schema.json` | 変更 | `gated_mode` に `hotfix`、`mode_judgment`・`mode_check`・`rerun_floor`・`verification`・`post_review` の形 | — | — | — |
| `contracts/workflow/parallel-plan.schema.json` | 変更 | `kind: sprint` の行に必須の `judgments`（課題ごとのモードと根拠） | — | — | — |
| `contracts/worker/kinds.json` | 変更 | 作業の種類 `mode`（`excerpt` は `skills/development-workflow/references/mode-criteria.md`、出力 `mode_judgment`） | — | — | — |
| `src/ndf/progress/judgment.py` | 新設 | 判定の記録を確かめ、スプリントのモードを決め、工程表の作り直しの下書きを作る | `definition`・`stages`・`model` | モジュール | `sprint_mode`・`split_groups`・`priority_of`・`recheck`・`check`・`row_errors`・`redefine` |
| `src/ndf/progress/store.py` | 変更 | 定義の改訂を足す。写し元の ID から流し直しの後継を引く | — | モジュール | `append_definition`・`find_successor`（新設） |
| `src/ndf/progress/command.py` | 変更 | `new` の `--mode` を省略できる参考の値にし、`--requirements` と `finish` で判定を当てる。`--carry` の写し元に未解決のモードの引き上げ（判定の訂正を含む）があれば `rerun_floor` を作り、写し元がレーンの行のスプリントなら同じトランザクションで行を付け替える。写し元に後継（`carried_from` が写し元の流し直し）があれば、名前にかかわらず作らずに断り、後継の ID を返す。`show` の結果に `context`（`issues`・`pace`）と `parent` と、分けたときの `split_out_rows`（外した課題の新しい行の名前と判定）を足す。`new` に `--from-branch` を足し、文脈の `from_branch` に置く | `judgment`・`lane` | モジュール | `_sprint_new`・`_prepare_new`・`_cmd_finish`・`_cmd_show`・`parser`（変更）・`_unresolved_raise`（新設） |
| `src/ndf/progress/run.py` | 変更 | 工程が流れて終わったときに判定を当て、判定の無い後の工程を断る | `judgment` | モジュール | `_finish_stage`（変更）・`_judged`（新設）・`_outside_reader`（新設） |
| `src/ndf/progress/plan/cli.py` | 変更 | `lanes init`・`lane add` が計画の行の `judgments` を `judgment.row_errors` で確かめ、`row add` に `--judgments` を足す | `judgment` | モジュール | `lanes init` の検査・`_row_add`（変更） |
| `src/ndf/progress/lane/launch.py` | 変更 | 行の判定から決めたモードを参考の値として渡し、`DEFAULT_MODE` を消す | `judgment` | モジュール | `start_commands`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | マージ済みの PR を直さずに読むラウンド | 既存の `run` | モジュール | `run` の引数 `--post-release`（変更） |

## 判定の記録

満たす決定: 決定 2・3、受け入れ条件 4・6・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/progress/judgment.py` | 新設 | `check(record: Mapping[str, Any], issues: Sequence[int], modes: Sequence[str]) -> list[str]` | 判定の記録の形と中身の誤りを並べる |
| `sprint_mode` | `src/ndf/progress/judgment.py` | 新設 | `sprint_mode(per_issue: Mapping[int, str], heights: Mapping[str, int], floor: Optional[str] = None, doc_types: Optional[Mapping[int, str]] = None) -> str` | 課題ごとのモードがそろっていれば、そのモードをスプリントのモードとして返す。そろっていなければ `MixedModes` を投げる。`floor`（文脈の `rerun_floor.mode`）より低くしない。`doc_types` は `documentation` の課題ごとのタイプ |
| `recheck` | `src/ndf/progress/judgment.py` | 新設 | `recheck(sprint: Mapping[str, Any], parts: Sequence[Mapping[str, Any]], whole: Mapping[str, Any], heights: Mapping[str, int]) -> Optional[Recheck]` | 完了判定で差分の部分ごとに得たモード（`parts`）と差分の全体の判定（`whole`）を、スプリントの判定（`sprint`: モード・課題・タイプ）と比べる。移らなければ `None`。移るなら `Recheck`（`mode`・`doc_type`・`kind`・`split_plan`）を返す。`kind` は `rerun`（モードを移して流し直す）・`reclassify`（差分全体の分類を訂正する）・`split`（分ける）のどれか |
| `redefine` | `src/ndf/progress/judgment.py` | 新設 | `redefine(prev: Revision, record: Mapping[str, Any], new_snap: stages.Snapshot, declared: Mapping[str, Mapping[str, Any]]) -> Optional[Redefined]` | 判定のモードが今の定義と違えば、改訂 `rev + 1` の定義の文書と、引き継ぐ事象の下書きを返す |
| `append_definition` | `src/ndf/progress/store.py` | 新設 | `append_definition(conn: sa.Connection, machine_id: str, rev: int, definition: dict[str, Any]) -> None` | 定義の改訂を 1 つ足す |

- `check`
  - 事前条件: `record` は工程「要求と受け入れ条件」の出力の `mode_judgment`
  - 事後条件: 空のリストなら、課題の組がスプリントの課題と等しく、どの課題もモードの値と空でない根拠を持つ。モードの混ざりは `check` では見ない（`sprint_mode` が見る）。`check` を当てるのは判定の作業の出力（分ける前）である
- `sprint_mode`
  - 事前条件: `check` が空を返した
  - 事後条件: 設計文書の「スプリントのモード」の表のとおり、課題を「組」に分け、組が 1 つのときだけそのモードを返す。組が 2 つ以上なら、高い方へ寄せずに `MixedModes` を投げる。`MixedModes` は `judgment.py` に置く `JudgmentError` の子で、`JudgmentError` は `judgment.py` に置く `ValueError` の子である
  - 組の作り方は次の表のとおりで、1 つの組が 1 つのスプリントになる。`MixedModes.groups` は組の一覧 `[{"mode", "doc_side", "issues"}]` を持つ。`doc_side` は `documentation` の組だけが持ち、値は `repo-knowledge` か `outside`（今の 6 つのタイプ）である

| 課題 | 組 |
| --- | --- |
| `hotfix` | 課題ごとに 1 つの組（課題 1 つの即時修正だけがスプリントになる） |
| `documentation` で `doc_type` が `repo-knowledge` | `documentation`・`repo-knowledge` の組に集める |
| `documentation` でほかのタイプ | `documentation`・`outside` の組に集める（6 つのタイプどうしは今のまま 1 つの組に入る） |
| それ以外 | モードごとに 1 つの組 |

  - `repo-knowledge` と社外向けのタイプを別の組にするのは、社外の文書に要る工程（素材の収集と出典の確定・体裁レビュー）が課題ごとには選べず、スプリント全体で有効か無効かが決まるためである。モードの違いと同じく `MixedModes` で表し、要求の工程では分けて続け、計画の行では断る（下の「混ざりを分ける・出現の少ないモードを先に流す」）
  - `floor` があれば、まず `hotfix` の課題を `standard` として数え（流し直しのスプリントは即時修正にしない）、その結果と `floor` の高い方を返す。`floor` は `recheck` の戻り値なので `hotfix` にならない。したがって `floor` があるとき `hotfix` は返らず、課題 1 つの条件を破らない
  - `floor` と課題の判定で側（「コード」「リポジトリの知識」「社外の文書」。`recheck` の「側」）が違えば、高さを比べずに `JudgmentError` を投げる（「差分の分類は `floor` の側。課題の判定を直す」）。`floor` の側は `rerun_floor.mode` と `rerun_floor.doc_type` で決まる。`floor` が文書の側なのは判定の訂正（`reclassify`）のときだけで、そのときは課題の判定がすべて同じ側であることを求め、`documentation` を返す
- `recheck`
  - 返す `mode` は「流し直す先のモード」で、比べたモードと同じものを返す。`hotfix` は返さない
  - 側: 部分とスプリントを、実行する工程の違いで 3 つの側に分けて比べる。`documentation` でないものは「コード」、`documentation` で `doc_type` が `repo-knowledge` のものは「リポジトリの知識」、`documentation` でほかのタイプのものは「社外の文書」である。側が違えば工程表か有効な工程が違うので、高さでは比べない。スプリントの側は、判定の記録の課題ごとのモードとタイプから決まる（`sprint_mode` が 1 つの側しか受けないので 1 つに決まる）
  - 1. 部分の組にスプリントと違う側の部分があるかを見る。あれば、スプリントと同じ側の部分があるかで分ける
    - 同じ側の部分があれば `kind: split` を返す。部分の組から「分ける手順」の分割案（`split_plan`）を作る。文書とコードを混ぜない今の規則と、リポジトリの知識と社外の文書を混ぜない規則のためで、モードを上げても解けない
    - 同じ側の部分が無ければ（差分の全体がスプリントと違う側）、`kind: reclassify` を返す。モードは違う側の部分から 2・3 と同じ規則で決め、違う側が文書なら `documentation` と、その部分の `doc_type` を返す。違う側の部分が 2 つの側にまたがれば（例: コードのスプリントの差分が、リポジトリの知識と社外の文書だけ）、訂正では済まず、どちらを残すかも差分からは決まらない。このときは `kind: split` を分割案無しで返し、`basis` に「残す側が決まらない」と書く。conductor は利用者へ分け方を問う。`reclassify` では外す変更は無く、変更は今のブランチに残したまま正しいモードで流し直す（「流し直し」）
  - 2. 1 に当たらなければ、部分のモードのうち高いもの（`hotfix` は `standard` の高さで比べる）を差分のモードとする。スプリントが `hotfix` なら、即時修正の資格を差分の全体で確かめる。次をすべて満たすときだけ `None` を返す
    - 部分がすべて `hotfix` である
    - どの部分の `issue` も、スプリントのただ 1 つの課題である（その課題の不具合を直すのに要る変更だけである）。無関係の不具合の直しは、それ自身が 4 条件を満たしても、課題が別なので満たさない
    - 差分の全体の判定 `whole.mode` が `hotfix` である（4 条件を差分の全体に当てて満たす。部分ごとに満たしても、合わせると契約や方針を変えることがある）
  - 満たさなければ、高さによらず `kind: rerun` と差分のモードを返す。差分のモードは `hotfix` の部分を `standard` として数えるので `standard` 以上になる。戻り値は次の表のとおりである。即時修正のまま一部を切り出す経路は設けない。切り出した部分の起票と移す範囲の決め方が流し直しより重く、切り出した後の差分を即時修正の資格で確かめ直すことにもなるためである

| `hotfix` のスプリントの差分 | 戻り値 |
| --- | --- |
| 部分がすべて `hotfix` で、課題が同じで、全体も `hotfix` | `None` |
| `hotfix` と `operation`・`light`・`standard` の部分（コードの側どうし） | `rerun`・`standard` |
| 部分はすべて `hotfix` だが、別の課題の部分がある | `rerun`・`standard` |
| 部分はすべて `hotfix` だが、全体の判定が `hotfix` でない | `rerun`・`standard` と全体の判定のモードの高い方 |
| `hotfix` の部分と文書の部分 | 1 に当たり `split`（文書の部分を分ける）。分けた後の再試行で、残りに 2 を当てる |
| 部分がすべて文書 | 1 に当たり `reclassify`・`documentation` |

  - 3. それ以外は、差分のモードを比べる形に直してから高さを比べる。差分が `hotfix` なら `standard` に直す（課題 1 つの即時修正の資格は、流し直しで判定し直すまで分からないため）。直したモードがスプリントのモードより高いときだけ、`kind: rerun` と直したモードを返す
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

判定を求めるステートマシン（印 `judgment: "required"`）では、工程「要求と受け入れ条件」をどの時点でも無効にできない。判定の前に無効にすると判定の作業が一度も流れず、後の工程が `_judged` で断られる。判定の後でも、改訂で `pending` に戻した要求の工程（「判定を済ませた要求の工程の流し直し」）を無効にすると、要求仕様が無いまま後の工程へ進んでしまう。承認ゲートと同じく、3 つの入口すべてで、判定の有無によらず断る。

| 入口 | 扱い |
| --- | --- |
| `ndf progress new --disable 要求と受け入れ条件=…` | `refused`、「判定のある工程は無効にできない。要求を先に書いたなら `--requirements` で渡す」 |
| `ndf progress disable <ID> 要求と受け入れ条件` | 同上 |
| `.ndf/project.json` の `workflow.stages` で無効 | `_declared` の検査で同上。印の無い旧いステートマシンには当てない |

要求を別に書いた利用者の経路は `--requirements` である（「処理の手順」）。要求の工程は判定の作業だけを流して終わるので、判定は必ず通る。

#### 判定を済ませた要求の工程の流し直し

改訂 `rev + 1` で `pending` に戻した要求の工程は、判定の作業 `mode` を流さず、判定のモードの形で要求仕様だけを書く。この工程を終えるときは、判定は改訂 `rev` で済んでいる。

`mode` を流さない方法は、改訂 `rev + 1` の定義に保存する要求の工程から、ステップ `mode` を外すことである。今の実行器は、次のステップの選び方（`StepProgress.next_step`）と工程の出力の組み立て（`_stage_output`）の両方で、定義に保存した `steps` を読む。片方だけで飛ばすと、もう片方が流していない `mode` の報告を探して止まる。定義から外せば、両方が同じ `steps` を読むので食い違わない。外すのは `redefine` で、この工程を `pending` に戻すときだけである。

- `_cmd_finish`・`_finish_stage` は、文脈（`View.context()`）に `mode_judgment` があれば `check`・`mode_decided`・`redefine` を行わない。出力に `mode_judgment` が無くても断らない
- 出力に `mode_judgment` があっても、文脈の値と違えば断る（判定のやり直しはこの経路では受けない）

#### 事象の登録と書く順

`model.check_event` は `contracts/workflow/progress.json` に無い事象を断るので、次を登録する。

| 事象 | 書き手（`actors`） | スコープ | 状態遷移 |
| --- | --- | --- | --- |
| `mode_decided`（新設） | `workflow`・`conductor` | `table` | 変えない（`mode_raised` と同じ記録だけの事象） |
| `manual_finished`（既存） | `workflow` を足す（改訂の引き継ぎを `_finish_stage` が書くため） | 今のまま | 今のまま |
| `outputs_recorded`（既存） | 今のまま | 今のまま | `from` に `failed` を足す（失敗にした工程が `mode_check` を残すため。「完了判定の照らし直しと流し直し」） |
| `failed`（既存） | `conductor` を足す（手で `mode_check` を渡して失敗にする経路のため） | 今のまま | 今のまま（`running` → `failed`） |
| `row_restarted`（新設、レベル `lane`） | `conductor` | `row` | `running` → `running`。行の `sprint_id` を流し直しのスプリントの ID に置き換える（「レーンの行の付け替えと、途中で止まったときの再開」） |
| `issues_split`（新設） | `workflow`・`conductor` | `table` | 変えない（記録だけの事象）。payload は外した課題の判定の行（`split_out`）と残す課題。文脈の `issues` を残す課題に置き換える（「混ざりを分ける・出現の少ないモードを先に流す」） |
| `row_issues_split`（新設、レベル `lane`） | `workflow`・`conductor` | `row` | `running` → `running`。payload は外した課題の番号。行の畳み方は、行の課題と `judgments` から外した課題を除く（同上） |

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
| `sprint_mode` | 組が 2 つ以上（`MixedModes`。モードの混ざり・`hotfix` の課題が 2 つ以上・`repo-knowledge` と社外向けのタイプの混ざり） | 計画の切り方の誤りか、要求で判定が分かれた | 計画の行の登録・要求の工程を終えるとき | 計画の行では止める。要求の工程では `split_groups` で分けて続ける（下の「混ざりを分ける」） | 計画の行は `invalid`、「組の違う課題は行を分ける」。要求の工程は `ok` と `items` の `split_out` | 計画の行は直して打ち直せる |
| `sprint_mode` | `floor` と課題の判定で側が違う（`JudgmentError`。`MixedModes` ではない） | 流し直しの判定が差分の分類と合わない | 流し直しのスプリントの要求の工程を終えるとき | 止める。分けない | `refused`、「差分の分類は `floor` の側。課題の判定を直す」 | 何も書かない。判定を直して打ち直せる |
| `append_definition` | 改訂の番号がぶつかる | 同時の書き込み | `store` の一意の制約 | 呼び元へ返す | 既存の `Conflict` の扱い（読み直して書き直す回数の方針 `contracts/limits/` の方針 `progress.append`） | 読み直して再試行できる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_cmd_finish`・`_finish_stage` | 印が無い、または文脈に `mode_judgment` が既にある | 今のとおり出力の事象と `finished` だけを書く（上の「判定を求めないステートマシン」と「判定を済ませた要求の工程の流し直し」） |
| `_cmd_finish`・`_finish_stage` | 印があり、工程が「要求と受け入れ条件」で、判定のモードが今の定義と同じ | 出力の事象と `mode_decided`（判定の記録・参考の値）を書く |
| 同上 | 同上で、判定のモードが違う | 上に加え、`append_definition` で改訂を足し、`redefine` の下書きを新しい改訂で書く。結果 JSON の `items` に `{"kind": "mode_changed", "hint": <参考の値>, "mode": <判定の値>}` を 1 行足す |
| 同上 | 印があり、工程が「要求と受け入れ条件」で、`sprint_mode` が `MixedModes` を投げた | `split_groups` で残す組を決め、残す課題だけの判定の記録で上の 2 行のどちらかを行う。同じトランザクションで `issues_split` と、レーンの行なら `row_issues_split` を書き、`items` に `split_out` の行を出す（「混ざりを分ける・出現の少ないモードを先に流す」） |
| `run.py` の工程を流す前の確かめ（`_judged`） | 定義に `judgment: "required"` があり、要求の工程より後の工程で、`View.context()` に `mode_judgment` が無い | 流さずに `refused`、「判定の記録が無い。要求と受け入れ条件を終える」 |
| `_prepare_new` | `--requirements` の `mode_judgment` に出所（`source: {"machine": <ID>, "seq": <mode_decided の事象の番号>}`）がある | 出所のステートマシンをストアから読み、その事象が `mode_decided` で、判定の記録の sha256 が `--requirements` の `mode_judgment` と一致し、判定の記録の `issues` の課題の組（分けたときは残した課題だけ。`split_out` は含めない）が `--issues` と一致するときだけ受ける。判定のモードの定義で改訂 1 を作り、`mode_decided` を最初の事象に含める。`--mode` は参考の値として文脈の `mode_judgment.hint` に残す |
| `_prepare_new` | `--requirements` に `mode_judgment` があるが出所が無い・一致しない | `refused`、「判定の記録は判定の作業で作る。出所の無い判定は受けない」。手で書いた JSON にモードを書くだけで判定を飛ばす経路を作らない |
| `_prepare_new` | `--requirements` に `mode_judgment` が無い | 判定が済んでいないので、要求の工程を終えたものにはしない。`--mode`（省けば `standard`）の仮の改訂 1 を作り、`--requirements` の出力を文脈に置いて、要求の工程の行を `pending` に残す。この工程を流すと、要求の文書は文脈にあるので判定の作業 `mode` だけを通す |
| `_prepare_new` | `--requirements` が無い | `--mode`（省けば `standard`）の定義で仮の改訂 1 を作る |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `mode_judgment` | 型（`issues: [{issue, mode, basis, doc_type}]`・`mode`・`hint`・任意の `split_out`・任意の `source`）。`doc_type` は `mode` が `documentation` の行だけが持ち、値は下の `DOC_TYPES` のどれかである。判定の作業が書き、`check` を通った後にだけ文脈に入る。混ざりを分けたときは、文脈と `mode_decided` に書く値の `issues` を残す課題の行だけにし、外した課題の行は `issues` と同じ形のまま `split_out` へ移す（「混ざりを分ける・出現の少ないモードを先に流す」）。後の工程（`documents` の検査など）が読む課題の組は `issues` で、文脈の `issues` と等しい | `contracts/workflow/context.schema.json` | — |
| `DOC_TYPES` | 定数。`documentation` のタイプの値 `proposal`（提案・企画）・`decision`（決裁・稟議）・`report`（定例報告）・`metrics`（指標定義）・`manual`（運用マニュアル）・`briefing`（説明・研修）・`repo-knowledge`（リポジトリの知識）。値は執筆の手引きのファイル名 `type-<値>.md` と同じにする。判定を作る作業の種類 `mode`、`context.schema.json` の `mode_judgment.issues[].doc_type` の列挙、`check`、条件の語 `outside_reader`、用語集のコードは、どれもこの値を使う | `src/ndf/progress/judgment.py`（スキーマの列挙は同じ値を書く） | — |
| `mode_check` | 型（`mode`・`basis`・`evidence`・`kind`（`rerun`・`reclassify`・`split`）・`doc_type`（文書の側へ移すときだけ）・`split_plan`（`split` のときだけ。「分ける手順」）・`needs_human`（真偽。省けば偽））。`null` も取る（解消の記録）。完了判定と `operation` の適用前の確かめが、工程を失敗にするときに書く。`needs_human` が真のときは、根拠の説明に実値が要るので人が読むまで流し直さない（「必ず人の承認が要る操作と公開しない情報」） | `contracts/workflow/context.schema.json` | — |
| `evidence` | 判定の根拠の型。次のどれか 1 つ。Git の差分 `{"kind": "git", "base", "head"}`（比較元と比較先の SHA）、保存した実行計画 `{"kind": "plan", "ref", "sha256"}`、外部の設定のリクエスト `{"kind": "request", "target", "changes": [{"field", "change"}], "ref"}`。`changes` は変わる項目の名前と変更の種類（`add`・`modify`・`delete`）だけを持ち、今の値も変える予定の値も持たない。実行計画とリクエストの本体は秘密や公開していない値を含みうるので、リポジトリと進捗の DB の外の制限された保存先に置き、`ref` にはその場所の参照（パス）だけを書く | `contracts/workflow/context.schema.json` | — |
| `from_branch` | `ndf progress new --from-branch` で作ったステートマシンの文脈の初期値。分けた変更を置いたブランチの名前。工程「作業場所の用意」が読む | `contracts/workflow/context.schema.json` | — |
| `carried_from` | `--carry` で作ったステートマシンの文脈の初期値。写し元の ID。後継を引くのに使う | `contracts/workflow/context.schema.json` | — |
| `rerun_floor` | 型（`mode`・`doc_type`（`mode_check` にあれば）・`kind`（`rerun` か `reclassify`）・`basis`・`evidence`・`from`（写し元のステートマシンの ID））。流し直しのスプリントの文脈の初期値。`evidence` は `mode_check` の値をそのまま写す。上の型が実値を持たないので、写しても実値は広がらない。判定の作業は `evidence` を `mode_check` と同じ型で読み、実値が要れば `ref` の保存先を読む。判定ではないので `_judged` は見ない | `contracts/workflow/context.schema.json` | — |
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
| `sprint_mode` | `light` 3 件 → `light`。`documentation` の `proposal`+`report` → `documentation` | `hotfix` 1 件 → `hotfix`、`hotfix` 2 件 → `MixedModes`（`groups` に課題 1 つずつの 2 つ）、`light`+`standard` → `MixedModes`（`groups` に 2 つ）、`documentation`+`light` → `MixedModes`（`groups` に 2 つ）、`documentation` の `repo-knowledge`+`proposal` → `MixedModes`（`doc_side` が `repo-knowledge` と `outside` の 2 つ）、`light` 1 件で `floor` が `standard` → `standard`、`hotfix` 1 件で `floor` が `light` → `standard`。`documentation` 1 件で `floor` が `documentation`（訂正）→ `documentation` | `light` 1 件で `floor` が `documentation` → `JudgmentError`（`MixedModes` ではない） |
| `recheck` | `light` のスプリントで部分が `standard` だけ → `rerun`・`standard`。`standard` のスプリントで部分が `standard` とビジネス文書の `documentation` → `split`、`split_plan.move` は文書の変更 | `hotfix` のスプリントで差分が `standard` → `standard`（高さが同じでも返す）。部分がすべて `hotfix` で課題が同じで全体も `hotfix` → `None`。`light` のスプリントで差分が `hotfix` → `standard`。同じ `README.md` の同じ hunk にコードの説明と章立ての組み直しがある → `split`。`base` に `move` だけを当てた木が `move_tree` で移す変更だけを持ち、`head` に `unmove` を当てた木が `keep_tree` で、2 つを `base` で合わせると `head` の木になる | `hotfix` のスプリントで部分が `hotfix` と `operation` → `rerun`・`standard`、`hotfix` と `light` → `rerun`・`standard`、`hotfix` と文書 → `split`。部分がすべて `hotfix` でも別の課題の部分がある → `rerun`・`standard`。部分がすべて `hotfix` でも `whole.mode` が `standard` → `rerun`・`standard`。`light` のスプリントで差分の全体が `documentation` → `reclassify`・`documentation`。リポジトリの知識のスプリントで部分に `proposal` の文書がある → `split`、差分の全体が `proposal` → `reclassify`・`documentation`・`proposal`。`base` に `move` だけを当てられない・当てた木に残す側の行が入る・2 つを合わせても `head` にならない → `split`、`split_plan` は無く、根拠は「自動で分けられない」 |
| `redefine` | `standard` → `light` で、終えた要求の工程を引き継ぎ、設計の行が消える。改訂 `rev + 1` の文書が `judgment`・`mode_judgment` と、今回の要求の出力を持つ | 同じモード → `None`。`legacy-refactor` へ変えても、その定義も要求の行を先頭に持つので、終えた要求の工程を引き継ぐ。`--disable` で外した工程は改訂 `rev + 1` でも無効で、`disabled` は 1 回だけ | 新しく現れる承認ゲートの工程を `.ndf/project.json` で無効にしてある → `Refused` |
| `_judged` | 判定の後は後の工程が流れる | 要求の工程そのものは判定の前でも流れる。印の無い定義は判定が無くても流れる | 判定の前に後の工程を `next` で流すと `refused` |
| 流し直した要求の工程の終了 | 文脈に `mode_judgment` があれば、出力に無くても終わり、`mode_decided` を書かない。改訂 `rev + 1` の要求の工程の `steps` に `mode` が無い | — | 出力の `mode_judgment` が文脈と違う → `refused` |
| 要求の工程の無効化 | — | 印の無い定義では今のとおり無効にできる | 印のある定義で `--disable`・`progress disable`・`workflow.stages` の無効 → `refused`。改訂 `rev + 1` で `pending` に戻した要求の工程を `progress disable` → `refused` |
| 流し直しの作成（`--carry`） | 完了判定が `mode_check` を残して失敗した写し元を `ndf progress cancel` で中止し、`new --carry` で作ると、文脈に `rerun_floor` と元の `worktree`・`branch` があり、`mode_judgment` が無い。要求の工程で判定の作業が流れる（中止で工程の状態が `cancelled` になっても下限が渡る）。`reclassify` の写し元からも `rerun_floor`（`kind: reclassify`）が渡る | 写し元の `mode_check` が `null`（再試行で解消）・`kind` が `split`・最後の `started` より前 → `rerun_floor` を作らない。中止の直前の状態が `failed` でない（失敗の前に中止した）→ 作らない | 写し元に後継がある（名前を変えて打ち直した場合を含む）→ 作らずに断り、`metrics.successor` に後継の ID を返す。レーンの行は付け替えない |
| 分ける手順の書き込み | 移す先のコミットの木が `move_tree`、打ち消しのコミットの木が `keep_tree` になって push される | — | 分割案を作った後に PR の head が動いた・作業場所の `HEAD` が違う・コミットしていない変更がある → 何も書かずに分割案を作り直す。当てた木の SHA が記録と違う → push しない |
| 分けた変更の続き | `--from-branch` で作ったスプリントの作業場所の用意が、そのブランチの名前で作業場所を作り、移した変更を持つ | — | `--from-branch` のブランチがリモートに無い → `precondition`、ベースから切り直さない |
| レーンの行の付け替え | レーンの行から始めたスプリントを中止して `new --carry` で作ると、同じトランザクションで行の `sprint_id` が新しい ID になり、行は実行中のまま。新しいスプリントの完了が `end_row` で行に写る | 中止と作成の間で止まった後に `new --carry` を打つと付け替わる | 行が実行中でない・行の `sprint_id` が写し元でない → `Refused`、ステートマシンも作らない |
| `mode_check` の書き方 | 流す経路で `failed`（`step: null`）と `outputs_recorded` が書かれ、`retry` の後に完了判定の全ステップが流れ直す | 手で終える経路で工程が既に `failed` なら `outputs_recorded` だけを書く | 工程が `pending` → `refused` |

## 完了判定の照らし直しと流し直し

満たす決定: 決定 9

完了判定が差分から得たモードは、次のスプリントの判定の下限として渡す。下限が無いと、要求が同じなので次の判定もまた低いモードを選び、同じ完了判定で落ち続ける。

### `mode_check` を残して失敗にする

今の `run.py` は、止まったステップ（`STOPPED`）を理由とステップだけの `failed` に変え、出力は工程が正常に終わったときにしか書かない。そこで、モードの照らし直しで止めるときは、ステップを止めずに出力を返し、工程の側で失敗にする。

| 経路 | 工程の状態 | 書き方 |
| --- | --- | --- |
| 工程を流す経路（`_finish_stage`） | `running` | 完了判定のステップは `ok` で終わり、報告に `mode_check` を入れる。`_finish_stage` は工程の出力に `mode_check` があれば、`finished` の代わりに、1 つのトランザクションで `failed`（書き手 `workflow`、理由の種類 `mode_raised`、payload の `step` は `null`）、`outputs_recorded`（`{"mode_check": …}`）の順に書く |
| 手で終える経路（`ndf progress finish`） | `running` | `--output` の JSON に `mode_check` があれば、上と同じ 2 つの事象を書き手 `conductor` で書く |
| 同上 | `failed` | 状態はもう失敗なので `failed` を重ねず、`outputs_recorded`（書き手 `conductor`）だけを書く |
| 同上 | `pending` など | `refused`（先に `next` で流す） |

- 事象の登録: `failed` の書き手に `conductor` を足し、`outputs_recorded` の `from` に `failed` を足す（「事象の登録と書く順」）
- 再試行で流し直すステップ: `failed` の payload の `step` を `null` にするので、今の `StepProgress._failed` が `redo` を立て、`ndf progress retry` の後の開始で終えたステップの記録をすべて捨てる。完了判定は初めのステップから流れ直し、前の `mode_check` を返したステップも差分を読み直す
- 解消の記録: 再試行した工程が `mode_check` 無しで終われば、`_finish_stage` は出力の事象に `{"mode_check": null}` を含めて書き、文脈に古い値を残さない

`mode_check` の形は「内部のデータと定数」にある。`operation` の適用前の確かめも、工程「実装」のステップが同じ形で返し、同じ書き方で失敗にする。

### 照らし直しの入力と分ける判定

`split`（側の違う変更を分ける。側は「コード」「リポジトリの知識」「社外の文書」）は、差分全体のモード 1 つでは決められない。ビジネス文書とコードが一緒に入った差分は、全体では `standard` になり、`standard` のスプリントと比べても違いが出ないためである。そこで、完了判定は差分を部分ごとに判定し、その組を `recheck` へ渡す。

- 完了判定の作業は、比べた 2 つのコミット（比較元 `base` と比較先 `head` の SHA）の差分を `mode-criteria.md` の基準で部分に分け、部分ごとに次を書く（1 つの課題・PR が性質の違う部分を含むときの境界事例と同じ分け方）

| 項目 | 中身 |
| --- | --- |
| `id` | 部分の番号 |
| `paths` | 部分に入るファイルのパス |
| `mode`・`basis` | 部分のモードと根拠 |
| `doc_type` | `mode` が `documentation` のときだけ。その部分の文書のタイプ（`DOC_TYPES` のどれか）。差分の文書が承認済みのタイプから外れたかを、側で見分けるために要る |
| `issue` | 部分が直す・満たす課題の番号。スプリントのどの課題にも当たらなければ `null` |

- 差分の全体にも 1 つの判定 `whole: {mode, basis}` を書く。スプリントが `hotfix` のとき、4 条件を差分の全体に当てた結果として使う
- `recheck` は、まず側の違う部分があるかを見る（「関数の仕様」の `recheck` の 1）。差分にスプリントと同じ側の部分があれば `kind: split` を返し、分割案を `mode_check.split_plan` に残す。差分の全体がスプリントと違う 1 つの側なら `kind: reclassify` を返す
- どちらにも当たらなければ、部分のモードのうち高いもの（`hotfix` の扱いは「関数の仕様」の `recheck` の 2 と 3）を差分のモードとして比べる

### 流し直し

| 時点 | すること |
| --- | --- |
| 完了判定 | 上の「照らし直しの入力と分ける判定」で `recheck` を呼ぶ。`None` でなければ、上の書き方で `mode_check` を残して失敗にする |
| 分ける（`kind: split`） | モードを上げても解けないので、流し直さない。下の「分ける手順」で、分ける側の変更を起票した課題へ移してから今のブランチから外し、残った差分で完了判定を `ndf progress retry` で流し直す |
| 分類を訂正する（`kind: reclassify`） | 差分の全体が反対側なので、外す変更は無い。変更を今のブランチに残したまま、下の「流し直しの作り方」で正しいモードのスプリントを作る。例: `light` で始めた `README.md` の全面改稿が差分の判定で `documentation`（タイプ「リポジトリの知識」）になったら、`documentation` のスプリントが元のブランチの作業場所で工程「設計」へ進み、構成案を設計 PR に載せて企画の承認（設計承認ゲート）を受ける |
| 流し直しの作成 | 下の「流し直しの作り方」のとおり |
| 下限の受け渡し | `_prepare_new` は、写し元に「未解決のモードの引き上げ」があるときだけ、その `mode_check` を新しい文脈の初期値 `rerun_floor` に写す（下の「写す値と写さない値」）。`_judged` は `mode_judgment` だけを見るので、下限しか無いスプリントは判定済みにならない |
| 流し直しの判定 | 新しい文脈には `mode_judgment` が無いので、工程「要求と受け入れ条件」は判定の作業 `mode` を流す。判定の作業は `rerun_floor` の根拠（`evidence`）を入力に読み、課題ごとの判定を書く。`check` を通った後、スプリントのモードを `sprint_mode(…, floor=rerun_floor.mode)` で決め、`mode_judgment` を文脈に入れる。`reclassify` の下限では、課題の判定が下限と同じ側（コード・リポジトリの知識・社外の文書）でなければ `sprint_mode` が断るので、判定の作業は差分に合わせて判定を直す |

#### 分ける手順

分ける側の変更は、移す先を作り、元の PR から打ち消したことを確かめてから、完了判定を流し直す。分ける範囲はパスや hunk の見出しではなく、比べた 2 つのコミットにひも付けた 2 本のパッチと、それを当てた結果の木（tree）の SHA で残す。同じ hunk の中に両方の変更が入ることがあり、見出しでは区別できないためである。

2 本のパッチは起点が違う。移す先は `base` から作り、残す側は `head` から作る。1 本のパッチを `base` と `head` の両方へ当てると、同じ hunk で移す変更の前後の行（文脈の行）に残す変更が入っているとき、片方では当たらないためである。

分割案 `split_plan` は、完了判定が工程を失敗にするときに `mode_check` に書く。

| 項目 | 中身 |
| --- | --- |
| `base`・`head` | 比べた 2 つのコミットの SHA |
| `move` | `base` に当てて、移す側の変更だけを持つ木を作るパッチ。`{"path": <ファイル>, "sha256": <中身の sha256>}` |
| `move_tree` | `base` に `move` を当てた結果の木の SHA |
| `unmove` | `head` に当てて、移す側の変更だけを取り除いた木を作るパッチ。形は `move` と同じ |
| `keep_tree` | `head` に `unmove` を当てた結果の木の SHA |
| `paths` | 移す側に出るファイルの一覧 `[{path, scope}]`。`scope` は `whole`（そのファイルの変更をすべて移す）か `partial`（一部だけを移す） |

パッチのファイルは、ステートマシンの DB と同じ置き場の `split/<ステートマシンの ID>/` に置く。工程の出力に残るのはパスと sha256 で、再開した conductor はこの 2 つから同じパッチを読み直す。sha256 が合わなければ使わず、完了判定を流し直して作り直す。

1. 分割案を作る（完了判定の作業）: 部分の組から `move` と `unmove` を組み立てる。1 つのファイルの変更が両方の側に入るときは、hunk を行の単位で分けて、それぞれの起点に合わせて文脈の行を作る。組み立てた後、作業場所を変えずに一時のインデックス（`GIT_INDEX_FILE` に一時のファイルを指定し、`git read-tree` で起点の木を読んでから `git apply --cached` で当て、`git write-tree` で木の SHA を得る）で次をすべて確かめる。`git apply --check` は当てられるかしか見ないので、結果は木の SHA で比べる
   - `base` に `move` を当てられ、その木が `move_tree` になる
   - `head` に `unmove` を当てられ、その木が `keep_tree` になる
   - `git diff base move_tree` が、移す側の部分の変更だけを持つ（`paths` の外のファイルが変わらず、`partial` のファイルでは残す側の部分の行が入っていない）
   - `git merge-tree --write-tree --merge-base=base keep_tree move_tree` が競合無しで `head` の木を返す（2 つに分けても失う変更も増える変更も無い）

   どれかが通らなければ、分割案を作らずに `split` を返し、`mode_check.basis` に「自動で分けられない」と書いて止める（conductor は利用者へ分け方を問う）
2. 保存先を確定する: `out-of-scope` の手順で起票する（その課題は計画で判定する）。課題の本文に、移す範囲の要約と、移す変更を置くブランチの名前（`<今のブランチ>-split-<課題番号>`）を書き、印 `<!-- ndf:split-branch name=<ブランチ> base=<base> -->` を置く
3. 書き込みの前の確かめ: 次をすべて確かめる。1 つでも違えば何も書かず、完了判定を `ndf progress retry` で流し直して分割案を作り直す（判定していない版へ古いパッチを当てると、利用者が後から足した変更まで外すことがあるため）
   - 今の作業場所に、コミットしていない変更が無い（`git status --porcelain` が空）
   - 今の作業場所の `HEAD` が `split_plan.head` である
   - PR の head（`gh pr view <PR> --json headRefOid`）が `split_plan.head` である
4. 移す: `base` から 2 のブランチを作り、`move` を当ててコミットする。コミットの木（`git rev-parse HEAD^{tree}`）が `move_tree` であることを確かめてから push する。違えば push せず、ブランチを消して 1 からやり直す
5. 外す: 今の作業場所で `unmove` を当てて打ち消しのコミットを作り、その木が `keep_tree` であることを確かめる。違えばコミットを捨てて（`git reset --hard split_plan.head`）1 からやり直す。合えば元の PR のブランチへ push し、PR の head が push したコミットになったことを `gh pr view <PR> --json headRefOid` で確かめる。push の前に PR の head が `split_plan.head` から動いていれば push は拒まれる（`--force` を使わない）ので、そのときも 3 からやり直す。起票した課題の本文に、打ち消しのコミットの SHA を書く
6. 完了判定を `ndf progress retry` で流し直す。完了判定は更新した head で差分を読み直す。残った差分にまだ違う側の部分があれば、もう一度 `split` で止まる

分けた課題のスプリントは、2 で決めて 4 で push したブランチから始める。計画を作る者は、課題の本文の印からブランチを読み、計画の行の `args` に `--from-branch <ブランチ>` を書く（対話の conductor も同じ引数で `ndf progress new` を打つ）。`ndf progress new --from-branch` は文脈に `from_branch` を置き、工程「作業場所の用意」は入力に `from_branch` を持ち、値があればブランチの名前を自分で決めずにその名前で `ndf worktree create <ブランチ>` を打つ。今の `create` は、リモートに同じ名前のブランチがあれば新しく切らずにそれを取り出して作業場所を作る（`domains/worktree/create.py`）ので、移した変更の上で作業が続く。ブランチがリモートに無ければ、ベースから切り直さずに `precondition` で止まる（`--from-branch` のときだけの確かめ）。

#### `hotfix` に別の目的の変更が混ざったとき

完了判定は `kind: rerun` と `standard` 以上のモードを返し（「関数の仕様」の `recheck` の 2 の表）、スプリントは下の「流し直しの作り方」でそのモードへ移る。流し直したスプリントは、設計承認ゲートとマージ前のレビューを通る。即時修正のまま一部を切り出す経路は設けない。文書の部分が混ざったときだけは、側が違うので上の「分ける手順」で文書の部分を分け、残りで完了判定を流し直す。

#### 流し直しの作り方

conductor は、元のステートマシンを中止した後、次のコマンドで新しいスプリントを作る。今の `--carry` の検査は、元と違う名前と、元と同じ課題を求める。

```text
ndf progress new <元の名前>-rerun<回数> --mode <mode_check のモード> --issues <元の課題> \
  --pace <元の進め方> --parent <元の親> --carry <元の ID> --disable 作業場所の用意=元の作業場所を使う
```

| 引数 | 取る所 |
| --- | --- |
| 名前 | 元の名前に `-rerun<回数>`（1 から数える）を付ける。元と同じ名前は今の検査が断る |
| `--issues`・`--pace` | `ndf progress show <元の ID>` の `metrics.context`（この変更で `show` の結果に足す。`issues`・`pace` を持つ） |
| `--parent` | `ndf progress show <元の ID>` の `metrics.parent`（同じく足す。レーンの行から作ったスプリントならレーンのステートマシンの ID、無ければ渡さない） |
| `--disable 作業場所の用意=…` | 作業場所の工程の出力 `worktree`・`branch` を、無効にした工程の出力として今の `_carried` が写し元の文脈から写す。元のブランチの変更を持つ作業場所で後の工程が流れる |

#### レーンの行の付け替えと、途中で止まったときの再開

レーンの行から始めたスプリントでは、流し直しのスプリントを作るのと同じトランザクションで、行が持つスプリントの ID を新しい ID へ付け替える。今の `start_row` は次の未着手の行しか受けず、実行中の行の ID を変える入口が無い。作成と付け替えを分けると、その間で止まったときに行が中止済みの元のスプリントを指したまま残る。

- 新設の `lane.restart_row(conn, plan_id, lane, key, from_sprint, to_sprint)` は、行が実行中で、その `sprint_id` が `from_sprint` のときだけ、事象 `row_restarted`（書き手 `conductor`、payload `{"sprint_id": <to_sprint>, "from": <from_sprint>}`）を書く。行の畳み方（`fold_lane`）は `sprint_id` を新しい値に置き換え、状態は実行中のまま変えない。条件に合わなければ `Refused`
- `progress.json` に `row_restarted`（レベル `lane`、書き手 `conductor`、スコープ `row`、`from` は `running`、状態 `running`）を登録する
- `_sprint_new` は、`--carry` の写し元を `lane.sprint_row` で引き、レーンの行があれば、新しいステートマシンの作成と同じトランザクションで `restart_row` を呼ぶ。行の状態が実行中でなければ作らずに断る
- conductor は元のスプリントを中止した後に `ndf lanes row end` を打たない。打つと行が終わり、付け替える先が無くなる

1 つの写し元から作る流し直しは 1 つだけにする。後継（写し元から作った流し直し）は名前ではなく写し元の ID で見つける。付け替えの後は、`lane.sprint_row(<元の ID>)` は行を返さない（行は新しい ID を指す）ので、レーンの行からは後継を引けないためである。

- `--carry` で作るステートマシンは、文脈に `carried_from`（写し元の ID）を持つ。新設の `store.find_successor(conn, source_id)` は、改訂 1 の文脈の `carried_from` が `source_id` のステートマシンの ID を返す
- `_sprint_new` は、写し元の `run_lock` を取った同じトランザクションの中で `find_successor` を引き、後継があれば名前にかかわらず作らずに断る。結果は `refused` で、`metrics.successor` に後継の ID を載せる。レーンの行の付け替えも書かない
- 写し元の `run_lock` を取るので、同じ写し元から 2 つの `new --carry` が同時に走っても、後から取った方が後継を見つけて断る

手順の途中で止まったときは、どこまで進んだかを次の順に確かめて続きから打つ。

| 止まった所 | 見分け方 | 続け方 |
| --- | --- | --- |
| 元の中止の前 | `ndf progress show <元の ID>` が中止になっていない | 中止から打ち直す |
| 中止の後、作成の前 | 元は中止済みで、後継が無い | `ndf progress new … --carry` を打つ |
| 作成の後（応答を受け取れなかったときを含む） | 後継がある。作成と付け替えは同じトランザクションなので、レーンの行は後継の ID を指している | `ndf progress new … --carry <元の ID>` を打ち直してよい。名前を変えて打っても作られず、`refused` の `metrics.successor` に後継の ID が返るので、その ID で続ける |

#### 写す値と写さない値

| 写し元の値 | 新しい文脈 |
| --- | --- |
| 無効にした工程の出力のキー（`worktree`・`branch` など） | 今の `_carried` のとおり写す |
| `mode_judgment` | 写さない。写すと要求の工程が判定済みとして判定の作業を飛ばし、下限が効かない。今の `_carried` は無効にした工程の出力のキーしか写さないので、そのままで写らない。`_carried` に足さないことをテストで縛る |
| `mode_check` | 「未解決のモードの引き上げ」のときだけ `rerun_floor` に写す（`kind` も写す）。`mode_check` のキーそのものは写さない |

「未解決のモードの引き上げ」は、写し元の事象の履歴から判定する（`command._unresolved_raise`）。今の状態は見ない。流し直しの前に必ず打つ `ndf progress cancel` は、終わっていない工程をすべて `cancelled` にするので（`progress.json` の `terminal` に `failed` は入っていない）、中止の後の今の状態は `failed` にならないためである。次をすべて満たすときに写す。キーがあるだけでは写さない。

1. 写し元の工程（完了判定か実装）の、中止の直前の状態が `failed` である。中止の直前の状態は、写し元の最新の改訂の事象のうち、中止のときに書いた最初の `cancelled`（工程ごとの `cancelled` とステートマシン全体の `cancelled` は同じトランザクションで書かれる）より前の事象だけを `model.fold` で畳んで得る
2. その工程の最後の `failed` の理由の種類が `mode_raised` である
3. その工程の最後の試行（最後の `started` より後）に書いた `outputs_recorded` の `mode_check` が `null` でない
4. `mode_check.kind` が `rerun` か `reclassify` である（`split` は流し直しでは解けないので、下限にしない）

### `operation` の外部への適用の前の確かめ

`operation` は工程「実装」の中で外部の系を変えるので、完了判定を待つと、判定より先に外部へ適用されてしまう。そこで、適用の前に、適用する内容そのものを判定し、判定した内容だけを適用する。

| 外部の系 | 適用の前に判定するもの | 適用するもの |
| --- | --- | --- |
| dry-run を持つ（IaC の `terraform plan` など） | [operation-run.md](../plugins/ndf/skills/development-workflow/references/operation-run.md) の dry-run が出す実行計画。リソース・モジュールの追加や削除、構成の変更が出ていないかを、`mode-criteria.md` の `operation` の問い（変えるのが値だけか）で判定する | 判定した実行計画を制限された保存先のファイルに保存し（`terraform plan -out` など）、その保存した計画だけを適用する。適用の時点で計画を作り直さない |
| dry-run を持たない（権限・リポジトリの設定など） | 変える前の今の設定の値と、リクエストで指定する予定の値を並べる。対象、変わる項目、確かめられる副作用を確かめ、値以外の変更（項目の追加や削除など）が含まれないことを判定する。並べた値は判定の作業の中だけで読み、記録には書かない | 判定したリクエストを制限された保存先に保存し、そのまま適用する。適用の後に実際の値を測り、記録には項目名と変更の種類と、測った値が予定どおりだったかの真偽だけを書く |

値以外の変更が出たら、適用せずに工程「実装」を失敗にし、`mode_check` に `standard` と根拠を残す。書き方は次のとおりで、記録にも、流し直し先へ写す `rerun_floor` にも、実値は入らない。後は上の流し直しと同じである。

| 系 | `mode_check.evidence` | `mode_check.basis` |
| --- | --- | --- |
| dry-run を持つ | `{"kind": "plan", "ref": <保存した実行計画の参照>, "sha256": <計画のファイルの sha256>}` | 値以外の変更に当たったリソースの種類と名前と、変更の種類（例「`aws_iam_role.deploy` の追加」） |
| dry-run を持たない | `{"kind": "request", "target": <対象の名前>, "changes": [{"field": <項目名>, "change": "add" か "modify" か "delete"}], "ref": <保存したリクエストの参照>}` | 値以外の変更に当たった項目名と変更の種類 |
| どちらも、項目名と変更の種類では根拠を説明できない | 上と同じ | 「項目名では説明できない。`ref` の保存先を人が読む」と書き、`needs_human` を真にする。conductor は流し直しを作らず、利用者へ保存先の参照を示して判断を求める |

制限された保存先は、operation-run.md の記録の置き場（リポジトリの中）とは別の、リポジトリと進捗の DB の外の場所である。これは今の operation-run.md の dry-run と記録の手順に、判定と、判定した内容の固定と、実値を記録から外すことを足すものである。

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

## `documentation` のタイプ「リポジトリの知識」

満たす決定: 決定 10、受け入れ条件 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_outside_reader` | `src/ndf/progress/run.py` | 新設 | `_outside_reader(v: View) -> bool` | 判定の記録の課題ごとの行のうち `doc_type` を持つ行が 1 つ以上あり、そのすべてが `repo-knowledge` なら偽、ほかは真を返す。`TABLE_FACTS` に `outside_reader` の語で足す |

- 1 つのスプリントに `repo-knowledge` とほかのタイプは入らない。`sprint_mode` は 2 つを別の組にして `MixedModes` を投げる。要求の工程を終えるときは `split_groups` で分けて続け、計画の行の登録（`row_errors`）では断る（「判定の記録」の `sprint_mode`）。`_outside_reader` が「すべて」で見るのは、この検査を通らない旧い記録が来ても社外の文書の工程を外さない側に倒すためである
- 工程の定義 `stages/document-sources.json`・`stages/layout-review.json`・`stages/plan-to-spec.json`・`stages/release-verification.json` の条件（`when`）に `outside_reader` を足す。ほかのモードでは記録のタイプが無いので真になり、今の振る舞いのまま流れる
- `check` は、`documentation` の課題の行に `doc_type`（「判定の記録」の `DOC_TYPES` のどれか）が無ければ誤りにし、ほかのモードの行に `doc_type` があっても誤りにする。計画の行の `judgments` も同じ形で、`row_errors` が同じ確かめをする
- タイプの値は `DOC_TYPES` の 1 か所で決める。リポジトリの知識は `repo-knowledge` で、判定の作業 `mode` の出力、`context.schema.json` の `mode_judgment.issues[].doc_type` の列挙、`check`、`_outside_reader`、用語集（`docs/glossary/glossary.json` の「リポジトリの知識」のコード）で同じ値を使う

#### 工程へのタイプの渡し方

工程が処理する単位は文書のファイルで、判定の記録は課題ごとなので、設計の工程で文書ごとのタイプを 1 つに決めて後の工程へ渡す。今の 6 つのタイプどうしは 1 つのスプリントに混ざりうるため、課題ごとのタイプだけでは、執筆や再構成がファイルごとにどのガイドを使うかを決められない。

1. 工程 `design`・`deploy` の入力（`inputs`）に `mode_judgment` を足す
2. `documentation` のスプリントの設計の工程は、構成案で作る・組み直す文書ごとに、出力 `documents: [{path, issues, doc_type}]` を書く。`issues` はその文書を変える課題の番号、`doc_type` はその文書のタイプである
3. 文書のタイプは、その文書を変える課題の `doc_type` から決める。課題が 1 つならその課題のタイプ、2 つ以上で同じタイプならそのタイプである。違うタイプの課題が同じ文書を変えるときは、設計の工程はタイプを選ばずに `結果: 判断が要る` で止まり、文書を分けるか課題を分けるかを利用者に問う。1 つの文書に 2 つのタイプのガイドは当てられず、どちらかを選ぶと片方の読み手の問いが抜けるためである
4. 設計の工程の出力の検査（`finish` の出力の確かめ）で、`documents` の文書ごとに次を確かめる。どれかが違えば `refused` にする
   - `issues` が空でなく、どの番号も `mode_judgment` の課題である
   - `issues` のすべての課題の `doc_type` が、その文書の `doc_type` と同じである（どれか 1 つと同じでは足りない）
   - スプリントのどの課題も、1 つ以上の文書の `issues` に出る
5. 工程 `implement`・`document-rebuild` の入力に `documents` を足す。執筆と再構成は、処理するファイルのパスで `documents` を引き、その `doc_type` で使う参照を選ぶ。`documents` に無いファイルを作るときは、作業を止めて設計に戻す（構成案に無い文書だからである）

各工程が文書のタイプで選ぶものは次のとおりである。

| 工程（Skill） | `doc_type` が今の 6 つのタイプ | `doc_type` が `repo-knowledge` |
| --- | --- | --- |
| 設計（`design`） | 今のまま。構成案と、出力の形の `layout-*.md` による体裁設計 | 構成案だけを書く（章立て・置き場所・既存の文書からのリンク）。`layout-*.md` は読まず、体裁設計を作らない |
| 実装・執筆（`document-drafting`） | 今のまま `type-<doc_type>.md` | `type-repo-knowledge.md`。SKILL.md のタイプと参照先の表に行を足す |
| ドキュメント再構成（`document-rebuild`） | 今のまま、`related-skills.md` の「文書タイプと目的別ガイドの対応」で目的別ガイドを選ぶ | 同じ表に足す行で `purpose-decided.md` を選ぶ。出力の形は Markdown（`format-markdown.md`） |
| デプロイ（`release`） | 今のまま、生成と提出 | 下の「デプロイ」 |

#### デプロイ

`repo-knowledge` のスプリントは配布物も版も作らず、工程「デプロイ」は PR をマージして、ベースブランチに届いたことを確かめて終わる。工程の定義 `deploy.json` のステップ（MVV の記録・リリース承認ゲート・`release` のプラン）は変えない。変えるのは `release` の SKILL.md の分岐と、結果の型である。

今の `release` の手順 1 は、差分が版数の記載と説明文書（`README`・変更履歴・`docs/`）だけなら工程を飛ばす。リポジトリの知識の差分はまさにこれに当たるので、そのまま実装すると PR がマージされずに工程が終わる。そこで、次を手順 1 の早期終了の例外として足す。

| 手順 | `doc_type` がすべて `repo-knowledge` のスプリント |
| --- | --- |
| 先頭 | コードのモードと同じく、ブランチ戦略の設定の `release.deliveries` を読む |
| 1. 変わったかの確かめ | 差分が説明文書だけでも飛ばさない。版を上げる手順（2・3）だけを飛ばし、4 のマージへ進む |
| 2・3. 版の大小と版の更新 | 行わない。版を作らない |
| 承認 | マージ先がプロジェクトの宣言で本番チャネル（`kind: auto` の行。ai-plugins では `main`）なら、工程のリリース承認ゲートで承認を得てからマージする。そうでなければ、今のコードのモードと同じくゲートの扱いは宣言に従う |
| 4. マージ | 承認の取り方とマージする主体は、今のコードのモードと同じくブランチ戦略の設定の `approval` と `merge` に従う（`merge.actor: ndf` なら workflow の `merge` が入れ、会話のセッションはマージを打たない）。承認の対象はリポジトリの知識の PR そのもので、版上げのリリースの PR は作らない |
| 5. 届いたことの確かめ | `gh pr view <PR> --json state,mergeCommit,baseRefName` で状態が `MERGED` であることと、マージのコミットと対象のブランチの名前を得る。次に `git fetch origin <対象のブランチ>` で対象のブランチの今の先端を取り、`git merge-base --is-ancestor <マージのコミット> origin/<対象のブランチ>` が成功したときだけ届いたとする。`gh pr view` の値だけでは、マージのコミットが今の対象のブランチに入っているかは分からないためである。版を作らないので、タグと GitHub Release は確かめない |
| 生成と提出 | 通らない。体裁レビューも条件 `outside_reader` で無効になる |

結果は `release-result` の形で返す。今の型は `outputs.product_version` を必須にしているので、版を作らない場合の値を決める。

- `outputs.product_version` は `null`（版を作らない）。型は文字列か `null` を受け、`null` は `outputs.merge` があるときだけ受ける
- `outputs.merge` は `{"pr": <番号>, "merge_commit": <SHA>, "base": <ベースブランチ>, "base_head": <確かめたときの対象のブランチの先端の SHA>, "contained": true}` で、5 の確かめの結果である。`contained` は `git merge-base --is-ancestor` が成功したときだけ真にする。`contained` が偽なら工程を失敗にする
- 後の工程で `product_version` を読むのはリリース後テストで、リポジトリの知識では条件 `outside_reader` で無効になるので、`null` を読む工程は無い

- 執筆の手引き `skills/document-drafting/references/type-repo-knowledge.md` を新設する。読み手（このリポジトリで作業するエンジニアと AI）・本体（仕組み・決まり・経緯）・構成案だけを書き体裁設計を作らないことを持つ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | `documentation` の行に `doc_type` が無い・知らない値 | 判定の作業の誤り | 要求の工程を終えるとき | 止める | `refused`、「documentation の課題はタイプを持つ」 | 何も書かない。出力を直して打ち直せる |
| `sprint_mode`（`row_errors` と要求の工程の終わりから） | `repo-knowledge` とほかのタイプの混ざり（`MixedModes`） | 計画の切り方の誤りか、要求で判定が分かれた | 要求の工程を終えるとき・計画の行の登録 | 計画の行では止める。要求の工程では `split_groups` で分けて続ける | 計画の行は `invalid`、「リポジトリの知識は社外の文書と混ぜない。行を分ける」。要求の工程は `ok` と `items` の `split_out` | 計画の行は直して打ち直せる |
| `_outside_reader` | 判定の記録がまだ無い | 要求の工程の前 | 工程を流す前 | 真として扱う（判定の前に後の工程は流れないので、当たる工程は無い） | — | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_outside_reader` | `repo-knowledge` の行だけ → 偽。`proposal` の行 → 真 | `standard` のスプリント（`doc_type` が無い）→ 真。`repo-knowledge` と `proposal` の行が両方ある記録 → 真 | — |
| 工程の条件 | タイプ「リポジトリの知識」のスプリントで、素材の収集・体裁レビュー・確定仕様化・リリース後テストが無効になる | 提案のタイプでは 4 つとも有効のまま | — |
| タイプの混在 | 要求の工程の終わりで `repo-knowledge` と `proposal` の課題が混ざる → 分けて続け、片方の組の課題が `split_out` に出る | 今の 6 つのタイプどうし（`proposal` と `report`）の課題は 1 つのスプリントに入る | 計画の行の登録（`row_errors`）で `repo-knowledge` と `proposal` の課題を渡す → `invalid` |
| タイプの値 | 判定の記録の `doc_type` が `repo-knowledge` なら `check` と `context.schema.json` の検査を通る | — | `repo_knowledge`（下線）は知らないタイプとして断る |
| 文書ごとのタイプ | 提案と報告の課題を含むスプリントで、設計の出力 `documents` が文書ごとに 1 つのタイプを持ち、執筆と再構成がパスで引いたタイプのガイドを使う | 2 つの課題が同じタイプで同じ文書を変える → その文書のタイプは 1 つ | 違うタイプの課題が同じ文書を変える → 設計が `判断が要る` で止まる。報告の課題だけを持つ文書の `doc_type` が `proposal`（スプリントに提案の課題はある）→ 出力の検査で断る。判定に無い課題番号・どの文書にも出ない課題 → 断る |
| デプロイ | リポジトリの知識のスプリントで差分が `docs/` だけでも、デプロイが工程を飛ばさずに PR のマージまで進み、結果の `product_version` が `null`、`merge.contained` が真 | 版の無い結果がデプロイの結果の型の検査を通る | `merge` の無い結果で `product_version` が `null` → 型の検査で断る。マージのコミットが対象のブランチの今の先端の祖先でない → `contained` を真にせず工程を失敗にする |

## 混ざりを分ける・出現の少ないモードを先に流す

満たす決定: 決定 3・11、受け入れ条件 6・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `split_groups` | `src/ndf/progress/judgment.py` | 新設 | `split_groups(err: MixedModes, record: Mapping[str, Any], hint: Optional[str], heights: Mapping[str, int]) -> Split` | 混ざった判定から、残す組と外す組を決める。`Split` は `mode`（残すモード）・`keep`（残す課題の判定の行）・`split_out`（外す課題の判定の行）・`rows`（外す課題から作る新しい行の課題の組の一覧）を持つ |
| `priority_of` | `src/ndf/progress/judgment.py` | 新設 | `priority_of(mode: str) -> int` | モードの定義の `metadata.ndf.priority` を返す（小さいほど先） |
| `split_row_issues` | `src/ndf/progress/lane/__init__.py` | 新設 | `split_row_issues(conn: sa.Connection, plan_id: str, lane: str, key: str, sprint_id: str, issues: Sequence[int]) -> None` | 実行中の行から外した課題を除く事象 `row_issues_split` を書く。行が実行中で、その `sprint_id` が `sprint_id` のときだけ書き、ほかは `Refused` |
| `order_rows` | `src/ndf/progress/lane/__init__.py` | 新設 | `order_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[str]]` | 同じレーンの未着手の行を、依存（`after`）を守る範囲で優先の順に並べ直し、並べ直した行と警告を返す |
| `create_lane` | `src/ndf/progress/lane/__init__.py` | 変更 | 戻り値に警告の一覧を足す | 初期の行を書く前に `order_rows` を通す。`ndf lanes init`・`ndf lanes start`・`ndf lanes lane add` はどれもこの関数でレーンを作るので、3 つとも同じ優先の順と警告になる |
| `lanes_of`・`PlanLanes` | `src/ndf/progress/lane/machine.py` | 変更 | `PlanLanes.lanes` の要素を（ラッパーの名前, 実行中の行を持つか, 起動を待つ行の優先）にする | 起動を待つ行の優先を、計画を読むときに一緒に求める（下の「起動の許可」） |
| `waiting_order` | `src/ndf/progress/lane/machine.py` | 新設 | `waiting_order(plans: Sequence[PlanLanes]) -> list[str]` | 数えたすべての計画の、起動を待つレーンのラッパーの名前を、優先の小さい順（同じ優先ならラッパーの名前の順）に返す |
| 起動の許可（`admitted`） | `src/ndf/progress/lane/machine.py` | 変更 | 変えない | 上限の空きを、`waiting_order` の先頭から割り当てる（下の「起動の許可」） |

- `split_groups`
  - 参考の値: `hint` は判定の記録の `hint` で、`ndf progress new --mode` を明示したとき、またはレーンの行から始めたときに `start_commands` が計画の行の判定から決めて渡した値である。`--mode` を省いて作ったスプリントの仮の工程表の `standard` は参考の値にしない（`hint` は `None`）
  - 残す組の選び方: 次の順に比べ、最初に 1 つに決まった組を残す
    1. 組のモードが `hint` と同じ組だけを候補にする。同じ組が無い（`hint` が `None` を含む）ときは、すべての組を候補にする
    2. 課題の数が最も多い組
    3. 高さの高い組（`hotfix` は `standard` の高さで比べる）
    4. 優先（`priority_of`）の値が小さい組
    5. 番号の最も小さい課題を含む組。どの課題も 1 つの組にしか入らないので、ここで必ず 1 つに決まる
  - 例: `hint` が `light` で課題が `hotfix` 1 件と `standard` 1 件なら、1 で候補が変わらず、2 と 3 で同じ（`hotfix` は `standard` の高さ）なので、4 で `hotfix`（優先 1）を残す。`hotfix` の課題が 2 件なら、組は課題ごとなので、5 で番号の小さい課題を残す
  - 外す課題の新しい行（`rows`）: 外す課題を `sprint_mode` と同じ組に分け、1 つの組を 1 つの行にする。したがって `hotfix` は課題ごとに 1 行、`documentation` は `repo-knowledge` の組と社外向けのタイプの組を別の行にし、ほかのモードはモードごとに 1 行になる。どの行も `row_errors` を通る
  - 事後条件: `keep` は 1 つの組で、`sprint_mode` を通る。`split_out` は外す課題ごとに、判定の作業の出力の行をそのまま持つ（`{"issue", "mode", "basis", "doc_type"}`。`doc_type` は `documentation` の課題だけ）。新しい行の `judgments` にそのまま使える
- `_cmd_finish`・`_finish_stage`（「判定の記録」）は、要求の工程の出力で `sprint_mode` が `MixedModes` を投げたら `split_groups` で分け、`mode_decided` と同じトランザクションで次を書く
  - 事象 `issues_split`（payload は `keep` の課題と `split_out`）。文脈の `issues` を残す課題に置き換える。文脈と `mode_decided` に書く `mode_judgment` は、`issues` を `keep`、`split_out` を外した課題の行にする
  - スプリントがレーンの行から始めたもの（`lane.sprint_row` で行が引ける）なら、`split_row_issues` で行の事象 `row_issues_split` を書く。行の畳み方（`fold_lane`）は、行の `input.issues` と `input.judgments` から外した課題を除く。これで、計画の決まり I19（同じリポジトリの未着手・実行中の行どうしで課題が重ならない）が元の行の課題として外した課題を数えなくなり、一時レーンへ同じ課題を登録できる
  - 結果 JSON の `items` に、新しい行ごとに `{"kind": "split_out", "name": <一時レーンか新しいスプリントの名前>, "judgments": <その行の課題の判定の行>}` を 1 行出す。名前は下の「名前を決める」の規則で決める
- 外した課題の行の登録: 結果 JSON を受け取れずに止まっても同じ手順で続けられるよう、正本は進捗の DB に置き、登録は名前で一度だけにする

| 手順 | すること |
| --- | --- |
| 未処理の分割を得る | オーガナイザー（レーンでないスプリントでは conductor）は、結果 JSON の `items` か、`ndf progress show <スプリントの ID>` の `metrics.split_out_rows` から、新しい行ごとの名前と判定を得る。`show` は文脈の `mode_judgment.split_out` から `split_groups` の `rows` と同じ規則で行を作り直して返すので、`items` と同じ一覧になる。進捗の DB から読むので、結果 JSON を受け取る前に止まっても同じ値が取れる |
| 名前を決める | 一時レーンの名前は `<元のレーン>-split-<行の最小の課題番号>`、レーンでないスプリントの新しいスプリントの名前は `<元の名前>-split-<行の最小の課題番号>` にする。同じ分割からは必ず同じ名前になる |
| 登録する | 1 行ずつ `ndf lanes lane add`（一時レーン 1 つに行 1 つ）か `ndf progress new` を打つ |
| 既にあったとき | `lane add` が「レーンは既にある」で、`progress new` が同じ名前のステートマシンがあることで断ったら、そのレーンかスプリントの課題が行の課題と同じかを確かめる。同じなら登録済みとして次の行へ進む。違えば止めて利用者に問う |

- モードの定義に `metadata.ndf.priority` を足す（`hotfix` 1・`operation` 2・`light` 3・`documentation`・`legacy-refactor` 4・`standard` 5）。手順書は値を写さず、`execution-plan.md`・`parallel-plan.md` は「モードの定義の優先の順」と書いて指す
- 行の並べ直し: レーンを作るとき（`create_lane`。`lanes init`・`lanes start`・`lane add` の 3 つの入口）は、初期の行を書く前に `order_rows` を通す。既にあるレーンへ行を足すとき（`lanes row add`）は、足した後の未着手の行に `order_rows` を通し、位置が変わる行を `row_moved` で動かす。どの入口でも、並べ直したら結果の `warnings` に出す

#### 起動の許可

今の `plan/run.py` は、起動の候補ごとに `admitted` を呼び、空きがあれば先に呼ばれた候補が枠を取る。今の `lanes_of` が渡すのはラッパーの名前と実行中の行の有無だけで、待っている行のモードは分からない。そこで、待っている行の優先を計画を読むときに集め、空きの割り当てを `admitted` の排他の中で決める。

| 項目 | 決まり |
| --- | --- |
| 待っている行の集め方 | 起動を待つ行は、今すぐ起動できる行だけにする。`lanes_of` は、今の `lane.rules.wakeable_lanes` が返すレーンだけを候補にし、そのレーンの次の行（`wakeable_lanes` が返す行のキー）から優先を求める。`wakeable_lanes` は、計画が実行中でないとき（停止中の計画）は何も返さず、依存（`after`）が済んでいないレーンも返さない。`next_row` だけを使ってはいけない。`next_row` は依存が済んでいなくても行を返し、済んでいない依存を `waiting_after` に入れるだけだからである。優先は、行の `input.judgments` から `sprint_mode` で決めたモードの `priority_of` である。判定が無い行（開発ループの行など）の優先は `None` にする |
| どの計画まで見るか | 今の `machine_plans` が数える計画すべて（自分の計画・登録簿の計画・索引の計画）。読めない計画の行は優先が分からないので待つ行に入れない |
| 空きの割り当て | `admitted` は、今の `admit_lock` の中で上限と使っている数を数えた後、空き `n` を求める。空きは優先を持つ待ち行（`waiting_order(plans)`。優先が `None` の行は入らない）に先頭から割り当てる。自分の行の優先が `None` でなければ、`waiting_order` の先頭 `n` 個に自分のラッパーが入っているときだけ許可する。入っていなければ「優先の高い行が待つ」として、上限で待つときと同じ待ちの結果を返す |
| 待たせた候補 | 今の上限の待ちと同じく、計画の次の起動の機会に打ち直す。優先の高い行が起動して実行中になれば待つ行から抜けるので、次の打ち直しで順が回る |
| 自分の行の優先が `None` | 優先を持つ待ち行に割り当てた後に残る枠があるときだけ許可する。残る枠の数は `n` から `waiting_order` の長さを引いた数で、1 以上なら許可し、0 以下なら待たせる。例: 空き 1 枠で `hotfix` の行が待っていれば、判定を持たない開発ループの行が先に起動を試みても待たせ、枠は `hotfix` に回る。優先を持つ待ち行が無くなれば、開発ループの行も起動する |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `split_row_issues` | 行が実行中でない・行の `sprint_id` がスプリントと違う | 行の付け替えと分割が食い違った | 要求の工程を終えるとき | 止める。`issues_split` も `mode_decided` も書かない | `refused`、「レーンの行がこのスプリントを指していない」 | 何も書かない。行の状態を直して打ち直せる |
| 外した課題の行の登録 | 同じ名前のレーンかスプリントがあり、課題が違う | 別の用途で同じ名前を使った | `lane add`・`progress new` の応答 | 止める | 利用者へ問う | 名前を変えずに、既にあるものを片付けてから打ち直す |
| `order_rows` | 優先の順が依存（`after`）とぶつかる | 計画の依存 | 行の登録 | 依存を守り、優先は守れる範囲で並べる | `warnings` に「依存のため優先どおりに並べられない」 | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `split_groups` | 参考 `standard`・課題 `standard` 2 と `light` 1 → `standard` の 2 を残し、`light` 1 を外して 1 行 | 参考 `light`・課題 `standard` 1 と `operation` 1 → 同じ数なので高い `standard` を残す。参考 `light`・課題 `hotfix` 1 と `standard` 1 → 優先で `hotfix` を残す。参考 `None`・課題 `light` 1 と `operation` 1 → 高さか優先で決まり、どちらでも同じ結果。`hotfix` 3 件 → 番号の最小を残し、外す 2 件は 1 件ずつ 2 行。`standard` 1・`documentation` の `repo-knowledge` 1・`proposal` 1 を `standard` で残す → 外す行は 2 つで、どちらも `doc_type` を持つ判定の行で `row_errors` を通る | — |
| `issues_split` と `row_issues_split` | レーンの行から始めたスプリントで分けると、行の課題と `judgments` から外した課題が消え、同じ課題の一時レーンを `lane add` で登録できる | 結果 JSON を受け取らずに止まった後、`show` の `split_out_rows` から同じ名前で登録し直すと、既にあるレーンは登録済みとして飛ばす | 行の `sprint_id` が違う → `refused`、何も書かない |
| `order_rows`・`create_lane` | `standard`・`light`・`hotfix` の行 → `hotfix`・`light`・`standard`。`lane add` で `standard`・`light` の順に渡したレーンが `light` から始まり、`warnings` が出る | `light` の行が `standard` の行の後（`after`）→ 並べ替えない | — |
| `admitted` | 上限 1・空き 1 で `standard` と `light` の行が待ち、`standard` の候補が先に呼ばれる → `standard` を待たせ、`light` を許可する | 1 行だけ待つ → 今のまま。`light` の行の依存が済んでいない → `light` は待つ行に入らず、`standard` を許可する。`light` の行が停止中の計画にある → 待つ行に入らず、`standard` を許可する。空き 1 で `hotfix` の行が待ち、優先が `None` の開発ループの行が先に呼ばれる → 開発ループの行を待たせる。優先を持つ待ち行が無い → 開発ループの行を許可する | — |

## 必ず人の承認が要る操作と公開しない情報

満たす決定: 決定 12、受け入れ条件 11

| 対象 | 変える場所 | 振る舞い |
| --- | --- | --- |
| `operation` の外部への適用 | `operation-run.md` の「4. 実行の前に承認を得る」と、実行計画の単位の表 | 単位ごとに、必ず人の承認が要る操作（C1・C2・C3・C4・P3）に当たるかの列を持つ。当たる単位の承認の取り方は、下の「`operation` の承認の置き場所」のとおり。CD による再実行は当たらない |
| `operation` の実行の記録・`mode_check`・`rerun_floor` | `operation-run.md` の記録の形と、`recheck` が書く `evidence` | 値は項目名と変更の種類（追加・変更・削除）で書く。秘密と公開していない値は書かない。実値が要るときは、リポジトリと進捗の DB の外の制限された保存先に置いて参照（`evidence.ref`）だけを書き、`mode_check.needs_human` を真にして工程を失敗で返す（「`operation` の外部への適用の前の確かめ」） |
| リリース後レビューの PR のコメントと起票 | `run --post-release` が書くコメントと、起票の本文 | 指摘・分け方の理由・リリース後テストの根拠を、項目名と変更の種類で書く。公開していない情報を除くと指摘を説明できないときは、その指摘を書かずに `outcome` を `hold`（人へ戻す）にする。扱いは下の「リリース後レビューの `hold`」のとおり |

### `operation` の承認の置き場所

承認は、今の工程「デプロイ」がリリース承認ゲートで使っている仕組み（承認待ちのステップ `listen` と `ndf answer`）を、`operation` の工程「実装」にも置いて受ける。今の operation-run.md の「4. 実行の前に承認を得る」は、本番系の単位について工程「実装」の中でリリース承認ゲートの問い（`metadata.source` が `ndf-gate:2`）を出すと書いているが、工程の定義に回答を受けるステップが無い。今の `run.answer_draft` は、回答を待っているステップ（`listen`）が無い工程への回答を「回答を待っていない」として断るので、このままでは承認を記録できない。そこで、`operation` のときだけ工程「実装」の定義を 3 つのステップに分け、間に回答を待つステップを置く。

問いの種類は 2 つ目の承認ゲート（本番系へ届く操作）と同じ `gate: release` で、新しい種類の承認ゲートは足さない。工程「デプロイ」のゲートとは別の回答として残る。

| ステップ | 定義 | すること |
| --- | --- | --- |
| 1. `prepare`（作業） | 新設の `contracts/workflow/stages/operation-implement.json`（工程名は「実装」。`operation.json` の「実装」はこの定義を指す） | 範囲の表を作り、dry-run を流し、`operation` の問いで判定し、実行計画かリクエストを制限された保存先に保存する（「`operation` の外部への適用の前の確かめ」）。報告の出力に承認要求 `apply_request: {"id", "units": [{"unit", "target", "needs_approval", "ref", "sha256"}]}` を書く。`needs_approval` は、単位が本番系に届くか、必ず人の承認が要る操作（C1・C2・C3・C4・P3）に当たるときに真である。`id` は承認要求の ID で、`units` の sha256 を並べた値の sha256 にする |
| 2. `apply-gate`（回答を待つ `listen`。`gate: release`） | 同上 | `needs_approval` が真の単位が 1 つでもあれば、人へ問う。問いには `apply_request` の `id` と単位の表と、各単位の `ref` と sha256 を載せる。真の単位が無ければ、MVV 判定で省けるゲートと同じく、回答した者 `service:operation` の承認を書いて人に問わずに通す（今の `service:mvv` と同じ経路） |
| 3. `apply`（作業） | 同上 | 承認された単位だけを、保存した実行計画かリクエストのまま適用する。適用の後の記録は項目名と変更の種類だけにする |

| 項目 | 決まり |
| --- | --- |
| 回答の入口 | 今の承認ゲートと同じ `ndf answer` で、質問の id（`gate_id`）と見た対象を添える。見た対象には `apply_request.id` を書く。工程「実装」は 2 のステップで回答を待っている（`next` が `gate` を返す）ので、`answer_draft` は断らない |
| 承認の記録 | 事象 `answered`（2 のステップへの回答）。payload の `gate_id` と見た対象の `apply_request.id` で、承認した単位と sha256 の組に結び付く。単位と sha256 は 1 の報告の出力に残っているので、回答に写し直さない |
| 再開 | 回答の後に conductor が `ndf progress next` を打つと、今のゲートと同じく回答を読んで 3 のステップを流す。`retry` は使わない |
| 適用してよいかの判定 | 3 のステップは単位ごとに、2 への回答が承認で、その見た対象が 1 の `apply_request.id` と同じかを見る。さらに、適用しようとしている実行計画かリクエストの sha256 が、1 の出力の sha256 と同じかを見る。どれかが違えば、その単位を適用しない |
| 承認されなかったとき・適用しないとき | 工程「実装」を失敗にし、理由に承認の無い単位を書く。計画を作り直すときは `ndf progress retry` で 1 から流し直し、新しい `apply_request.id` で問い直す。前の承認は `id` が違うので使われない |

### リリース後レビューの `hold`

`hold` は、公開していない情報を書かないと説明できない指摘を、人へ戻すための分け方である。`outcome` の値は `revert`・`fix`・`skip`・`hold` の 4 つにする（「工程『リリース後レビュー』」の事後条件も同じ）。

| 時点 | すること |
| --- | --- |
| 指摘を分けた後 | `hold` の指摘が 1 つでもあれば、コメントも起票もしない。結果のファイルと工程の出力 `post_review` に分け方ごとの件数を書き、工程を `結果: 判断が要る` で止める。`hold` の指摘の題・本文・理由・根拠は、結果のファイル（`--state-dir`。リポジトリの外の制限された場所）にだけ残す。`--findings-out` と PR のコメントと課題には、下の「`hold` だった指摘の書き出し」の公開してよい項目だけを書く |
| 人の判断 | conductor は `hold` の指摘ごとに、`revert`・`fix`・`skip` のどれにするかを利用者に問う。答えを結果のファイルの指摘の `outcome` に書き、`decided_by: "human"` と `held: true`（一度 `hold` だった印）を足す。`held` は人が分け方を決めた後も消さない |
| 再開 | conductor が `ndf progress retry` で工程を流し直す。打ち直しは結果のファイルを読み（「再開の正本と起票の重複の防ぎ方」）、`hold` が残っていなければコメントと起票へ進む。残っていれば同じく止まる。`held` が真の指摘は、`revert`・`fix` に決まっても、下の公開してよい項目だけでコメントと起票をする |

#### `hold` だった指摘の書き出し

`hold` のまま、または一度 `hold` だった（`held` が真の）指摘は、人が分け方を決めた後も、公開の場所とリポジトリの中へ書ける項目を次に限る。人が `revert` や `fix` に決めても、指摘の本文が公開してよいものに変わるわけではないためである。

| 書き先 | 書く項目 | 書かない項目 |
| --- | --- | --- |
| 結果のファイル（`--state-dir`） | すべて | — |
| `--findings-out` | `id`・`outcome`・`held`・`summary`（項目名と変更の種類だけで書いた 1 行。相手役の本文から作らず、修正担当が書く）・`state_ref`（結果のファイルのパスと指摘の `id`）・`pr`・`commit` | `title`・`body`・`reason`・`evidence` |
| PR のコメント | `summary`・`outcome`・`state_ref` | 同上 |
| 起票する課題の本文（`revert`・`fix` に決まったとき） | `summary`・`state_ref`・`pr`・`commit` と、印 `ndf:post-release-finding` | 同上。直す人は `state_ref` から結果のファイルを読む |

`--findings-out` には、リポジトリの中のパスを渡しても非公開の情報が入らない。`hold` でない指摘の書き出しは今のまま（「工程『リリース後レビュー』」の事後条件）である。

### この節の確かめ

| 確かめ | 正常 | 異常 |
| --- | --- | --- |
| 承認の記録 | 検証用の系の認証・認可の変更で、2 のステップが回答を待ち、`ndf answer` で承認すると `next` で 3 が流れて適用する。`needs_approval` の単位が無い → `service:operation` の承認で人に問わずに 3 へ進む | 承認しない回答 → 適用せず工程を失敗にする。承認の後に計画を作り直して sha256 か `apply_request.id` が変わった → 適用しない。2 のステップが無い工程への `ndf answer` → 今のとおり断る |
| 記録の値 | 項目名と変更の種類だけが記録・`mode_check`・`rerun_floor` に残る。`evidence` の型が `current`・`planned` を受けない | 秘密の形の値（`check-repo-secrets.py` の規則に当たるもの）が記録に入る → 書かずに止める |
| 公開の書き込み | 項目名で説明できる指摘はコメントに書く | 説明に実値が要る指摘 → `hold` になり、コメントも起票もせず `判断が要る` で止まる。`--findings-out` のその指摘は `summary` と `state_ref` だけで、`body`・`evidence` を持たない。人が `fix` に決めた後の `retry` → コメントと課題の本文は `summary`・`state_ref` だけで、`body`・`evidence` を含まない |

## レーンの起動と計画の行

満たす決定: 決定 7、受け入れ条件 5・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `start_commands` | `src/ndf/progress/lane/launch.py` | 変更 | 変えない | 行の `input.judgments` から `judgment.sprint_mode` で決めたモードを `--mode` に渡す。`DEFAULT_MODE` と、`args` の `--mode` を見る分岐を消す |
| `row_errors` | `src/ndf/progress/judgment.py` | 新設 | `row_errors(issues: Sequence[int], judgments: Sequence[Mapping[str, Any]], heights: Mapping[str, int]) -> list[str]` | 計画の行の課題ごとの判定の誤りを並べる。課題の組の食い違い・根拠が空・知らないモード・`doc_type` の欠けと知らない値・モードの混ざり・`hotfix` で課題が 2 つ以上・`repo-knowledge` とほかのタイプの混ざり（どれも `sprint_mode` の `MixedModes`）を誤りにする |
| `add_row`・`carry_issues` | `src/ndf/progress/lane/__init__.py` | 変更 | 変えない | 足す行の `input` を `row_errors` で確かめ、誤りがあれば `Refused` |

計画の行は、課題ごとの判定をそのまま持つ。行のモードは計画を書く者が書かず、コードが課題ごとの判定から決める（決定 3 と同じ規則）。こうすると、`documentation` と他のモードの混ざりを計画の登録の時点で断れる。

- スキーマ `parallel-plan.schema.json` の `kind: sprint` の行は `judgments`（`[{issue, mode, basis, doc_type}]`。`doc_type` は `documentation` の課題だけ。`issues` の課題ごとに 1 つ）を必須にし、`args` に `--mode` を書くことを断る（同じ値を 2 か所に持たない）
- 行を作る入口は 4 つで、どれも同じ `row_errors` を通す。誤りがあれば行を作らない

| 入口 | 判定の受け取り方 |
| --- | --- |
| `ndf lanes init`・`ndf lanes start` | 計画の設定の各行の `judgments` |
| `ndf lanes lane add <計画> <lane.json>` | `lane.json` の各行の `judgments` |
| `ndf lanes row add` | 新しい引数 `--judgments <JSON のファイル>`（必須）。`input` は `{"kind": "sprint", "issues", "judgments", "args"}` |
| `ndf lanes row carry` | 元の行の `judgments` から、移す課題の分だけを写す。元の行に判定が無い課題は断る |

- `hotfix` で課題が 2 つ以上の行と、モードの混ざった行は断る（決定 3）。計画を書く者は行を分けて打ち直す
- 判定はレーンのステートマシンの `row_added` の payload（`input`）に残るので、再起動・セッションの入れ替えの後も `start_commands` が同じ値を読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| 行を作る 4 つの入口 | 行に `judgments` が無い | 計画を作る手順で判定していない | スキーマの検査 | 止める | `invalid` に行の場所。終了コード 2 | 計画の設定か引数を直して打ち直せる（設定はコミットしない入力） |
| 同上 | 課題の組の食い違い・根拠が空・知らないモード・モードの混ざり・`hotfix` で課題が 2 つ以上 | 判定の誤り・切り方の誤り | `row_errors` | 止める | `invalid`、誤りの一覧 | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `start_commands` | 判定が `light`・`light` の行 → `progress new ... --mode light` | `hotfix` 1 件の行 → `--mode hotfix` | `hotfix` 2 件の行は登録で `row_errors` が断り（`invalid`）、行が作られないので起動のコマンドも作られない。`args` に `--mode` を持つ行はスキーマで断られ、ここへ来ない |
| `row_errors` | `light` で課題 3 つ → 空 | 課題 1 つ | 判定の課題が `issues` と違う・`documentation`+`light`・`light`+`standard`・`hotfix` 2 件 |
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
  - `id`（指摘の ID）・`outcome`（`revert`・`fix`・`skip`・`hold`。`hold` は「リリース後レビューの `hold`」）・`reason`（分けた理由）
  - `title`・`body`（相手役の指摘の本文）・`evidence`（根拠のファイルと行）
  - `pr`（PR の番号）・`commit`（上の表の比較先の SHA）
- 副作用: PR へのコメント 1 件。push はしない
- 工程の定義 `post-release-review.json` は、入力に `pr` と `verification`（リリース後テストの出力）を持つ。この結果から出力 `post_review`（分け方ごとの件数と、指摘ごとの起票した課題の番号）を作る。`revert` と `fix` の起票は `out-of-scope` の起票の手順を使い、課題の本文には指摘の `title`・`body`・`evidence`・`pr`・`commit` を載せる。ただし `held` が真の指摘は、`title`・`body`・`evidence` を載せず、「`hold` だった指摘の書き出し」の項目だけを載せる。工程は起票で終わり、戻し・直しのスプリントを待たない

#### 再開の正本と起票の重複の防ぎ方

再開のときに読む正本は、このスプリントの実行記録（`--state-dir` の結果のファイルと、進捗の DB の工程の出力）だけである。PR のコメントは人が読むための写しで、再開では読まない。PR にコメントできる第三者がレビュー済みの結果を装っても、レビューは省かれず、課題も作られない。

| 時点 | すること |
| --- | --- |
| 相手役の結果を得た後 | 分けた指摘の配列を `--state-dir` の結果のファイルに書く。`--findings-out` にも書くが、`hold` か `held` の指摘は「`hold` だった指摘の書き出し」の公開してよい項目だけにする |
| コメントの前 | 結果のファイルに `comment: {"started_at": <時刻>}` を書く |
| コメントの直後 | 書いたコメントの ID を結果のファイルの `comment.id` に書く |
| 起票の前 | 結果のファイルの指摘に `filing: {"started_at": <時刻>}` を書く |
| 起票の直後 | 作った課題の番号を、結果のファイルの指摘の `issue` に書く。工程の出力 `post_review` も同じ番号を持つ |
| 打ち直し | 結果のファイルがあれば相手役を起動せずそれを使う。コメントと起票は、結果のファイルの記録で次のように分ける |

コメントと起票は、どちらも「書く前に始めた時刻を記録し、書いた直後に ID か番号を記録する」。打ち直しでは、その 2 つの記録の有無で、書いていない・書いた・書いたか分からないの 3 つに分ける。

| 記録 | 状態 | 打ち直しですること |
| --- | --- | --- |
| `started_at` が無い | 書いていない | 書く |
| ID（コメント）か番号（課題）がある | 書いた | 書かない |
| `started_at` があり、ID か番号が無い | 書いたか分からない（書いた直後に落ちた） | 下の照合で探す。見つかればその ID か番号を記録し、書かない。見つからなければ書く。照合の API 呼び出しが失敗したら、書かずに工程を失敗（理由の種類 `transient`）にし、打ち直しを待つ |

照合では、GitHub の一覧の API をページの終わりまで読み、検索の API は使わない。どちらも作成者と作成時刻で絞り、本文の先頭の印で見分ける。

| 対象 | 印 | 一覧の取り方 |
| --- | --- | --- |
| 課題 | `<!-- ndf:post-release-finding pr=<PR の番号> id=<指摘の ID> -->` | `gh api "repos/<owner>/<repo>/issues?creator=<今の利用者>&state=all&since=<started_at の 10 分前>&per_page=100" --paginate`。PR を除き（`pull_request` のある要素）、`created_at` が `started_at` の 10 分前以後の要素だけを照合する |
| PR のコメント | `<!-- ndf:post-release-review head=<比較先の SHA> -->` | `gh api "repos/<owner>/<repo>/issues/<PR の番号>/comments?since=<started_at の 10 分前>&per_page=100" --paginate`。作成者が今の利用者で、`created_at` が同じ範囲の要素だけを照合する |

- 今の利用者は `gh api user --jq .login` で得る。作成者が違う要素は、印があっても使わない。PR にコメントできる第三者が印を書いても、照合にも再開にも使われない
- 10 分は、手元の時計と GitHub の時刻のずれを見込んだ余裕である。`since` は更新時刻で絞るが、作成した後の要素は更新時刻が作成時刻以後なので漏れない
- `gh issue list` は使わない。既定の件数の上限（`--limit` の既定は 30 件）を超えると印のある課題が一覧から漏れ、「無い」と判断して重複して起票するためである
- 2026-11-25 に、この取り方を ai-plugins で確かめた。PR を除いた課題の一覧は、100 件ずつのページをまたいで 2 日分の 423 件を返し、直前に作った課題（[#4517](https://github.com/devbasex/ai-plugins/issues/4517)、モード判定の基準を直す課題）も `created_at` とともに出た

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
| 同上 | 打ち直し | 中断の後の再開 | `--state-dir` の結果のファイル | 相手役を起動せず、結果のファイルを返す。PR のコメントの中身は読まない。コメントは `comment` の記録で、書く・書かない・照合するを分ける（「再開の正本と起票の重複の防ぎ方」） | `ok` | 冪等 |
| 同上 | 照合の API 呼び出しが失敗した | ネットワーク・認証・レート制限 | `gh api` の終了コード | 書かずに止める | `failed`（理由の種類 `transient`） | 打ち直せる。何も書いていないので重複しない |
| 起票 | 起票の途中で落ちた | 課題を作った後、番号を書く前に落ちた | 結果のファイルの `filing` があり `issue` が無い | 上の照合で既にある課題を使う。100 件を超えてもページの終わりまで読む | `ok` | 同じ指摘の課題は 1 件 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `run --post-release` | 指摘 3 件が 3 つに分かれて、本文・根拠・PR・コミットとともに結果に載る | 指摘 0 件（コメントは「指摘なし」）。squash でマージした PR は squash のコミットを比較先にする。`hold` の指摘が 1 件ある → コメントも起票もせず `判断が要る`。その指摘に人の分け方を書いて打ち直す → コメントと起票へ進む | マージされていない PR・4 つの値に無い分け方 |
| 起票 | `revert` と `fix` の指摘が 1 件ずつ起票される。リリース後テストの不合格が `revert` に入る | `issue` のある指摘は起票しない。`filing` だけの指摘は、自分が作った印のある課題があればその番号を使う。その課題が一覧の 101 件目以降にあっても見つける | 第三者が作った印のある課題・PR のコメントの JSON は使わない。照合の API が失敗したら起票しない |
| コメント | 結果を書いた後に 1 件書き、ID を記録する | `comment.started_at` だけがある打ち直しで、自分が書いた印のあるコメントがあれば書かない | 第三者が書いた印のあるコメントは使わず、自分のコメントを書く |
