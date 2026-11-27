# #4580 の詳細設計: 開発の流れをプロジェクトの設定で選べるようにする

設計文書 [issue-4580-design.md](./issue-4580-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方は `design` の `references/detailed-design.md` にある。別ファイルにする条件の正本は `requirements-design` の `references/document-roles.md` の「詳細設計を別ファイルにする条件」で、`detailed-design.md` はそれを引き写している。

別ファイルにした理由: 新設する関数が 52（「関数の仕様」の表の `新設` の行から、型 13 とファイル 3 を除いて数えた）あり、シグネチャを変える既存の関数が 3 つ（`close_decide.verdict` の引数の名前と返り、`record.write_verification` の引数、`GitHub._read_permission` の返り）あるため（上の正本の「詳細設計を別ファイルにする条件」）。型 `record_decide.Record` と `github.Authored` は項目を足すだけで、既定値を持つので今の呼び出しは変わらない。

## 用語

この文書は設計書の語をそのまま使う。読み違えやすい語を次に挙げる。

| 用語 | 意味 |
| --- | --- |
| 本番の PR | 本番ブランチへマージした PR。`record --pr` に渡す |
| 作業の PR | 本番の PR のマージに含まれた、個々の変更の PR |
| 含めた PR | 本番の PR と作業の PR を合わせたもの（記録の `included`）。リリース記録はこのすべてのコメントに書く |
| 記録の PR | 含めた PR のうち番号が最も小さい 1 本。リリース後テストの記録と対象の一覧の訂正の記録はここにだけ書き、閉じる判定もここを読む |
| 値 | リリース記録とリリース後テストの記録の項目 `version` に入る文字列。バージョン番号を持たないリポジトリでは出したものを指す値（例: `main abc1234`。`record --version` に渡し、デプロイの工程の出力 `product_version` と同じもの）、バージョン番号を持つリポジトリではバージョン番号である。「同じ値の記録」は `version` が文字列として同じ記録をいう（設計書の「解き方の全体像」） |
| バージョン番号を持たないリポジトリ | `.ndf/project.json` の `branch_strategy.release.version_store.kind` が `none` のリポジトリ。それ以外（`file` など）はバージョン番号を持つリポジトリである。設計書の「バージョンの保存先が `none`」と同じ意味で、この文書の表の列「保存先」（ソースファイルの置き場所）とは関係が無い |
| バージョン番号を持たないリポジトリの記録 | バージョン番号を持たないリポジトリで `record --version` が書くリリース記録。`version` に値を持ち、加えて `environments`・`targets`・`commit`・`push`・`confirmation` を持つ。バージョン番号を持つリポジトリの記録は今の形のままで、`commit` と `targets` を持たないことで見分ける。設計書の「バージョンの無いリリースの記録」「バージョン付きの記録」と同じものを指す |
| 流れの文書 | 新設する `plugins/ndf/skills/development-workflow/references/project-flow.md`。種類ごとの流れと閉じる条件を利用者向けに 1 ページで示す。生成の印で挟んだ範囲は、手で書かず正本から生成する（設計書の決定 10。正本は「流れの文書の生成」の表） |
| 信用する書き手 | 記録を数えてよい書き手。対象のリポジトリで write 以上（`admin`・`maintain`・`write`）の権限を持つ書き手で、所属の区分（`author_association`）が OWNER なら権限を照会せずに信用し、MEMBER・COLLABORATOR なら権限を照会して write 以上のときだけ信用し、ほかの区分は照会せずに信用しない（設計書の決定 6 と「閉じる手順の読み取り」） |
| 書き手の照会（権限の照会） | 書き手の権限を GitHub に問い合わせること（`GET /repos/{repo}/collaborators/{login}/permission`）。「書き手を照会する」もこの意味である。コメントや本文を読むことは「取得」と書き、照会と呼ばない。「CD の結果の照会」は CD の実行の状態を問い合わせることで、別のものである |
| レート制限 | GitHub の API の利用量の上限。403 か 429 で返る。この文書の「403・404」は、レート制限によるものを含まない |
| 配布単位（`unit`） | 1 つのリリースで複数のスプリントブランチを別々に上げる形（`ndf release-steps` の `--units`）での、単位の名前。並列のレーンでは、そのレーンのスプリントブランチの名前である。単位が 2 つ以上のリリースの記録だけが `unit` を持ち、同じ本番の PR・同じ値に単位ごとの記録が並ぶ（設計書の「閉じる条件を外したときの閉じ方」） |
| 正の `targets`（正の一覧） | 同じ値の記録が従う、今回の対象の配布先の一覧。記録の PR の、信用する書き手の最後の訂正の記録の一覧で、訂正が無ければ同じ値の最初の有効なリリース記録の一覧である（設計書の「正の `targets`」） |
| 代表 | `records` が閉じる判定のために選ぶ 1 件のリリース記録。出力の `release` と、本文を読む PR の一覧（`included`）をこの記録から取る（設計書の「閉じる手順の読み取り」の手順 5） |

構成要素は 2 回のリリース（設計書の決定 9）のどちらで出すかを、小見出しの最初の行の「出す回」に書く。1 回目は閉じる条件の除外と、バージョン番号を持たないリポジトリのリリース記録とテストの記録、2 回目は種類の既定・モードごとの要否・設定の確かめ・流れの文書である。

## 設計書の番号つきの確かめと手順

この文書の「確かめ 0〜8」「書く順の手順 1〜4」「読み取りの手順 1〜6」は、設計書の番号をそのまま使う。判定の詳細は出典にあり、ここでは何を確かめるかだけを要約する。

`record --version` の確かめ（出典: 設計書の「バージョンの無いリリース記録の書き方」の確かめの表）。0〜6 のどれかに当たれば、どの PR にも書かずに止まる。7・8 は配布先ごとに判定し、止まらない。

| # | 確かめること |
| --- | --- |
| 0 | 引数の組。`--version` と `--environment` はそろえて、バージョン番号を持たないリポジトリでだけ受ける。`--target` の要否は、push の時点の宣言を読むかで決まるので確かめ 3 で確かめる |
| 1 | 本番の PR が本番ブランチへ入ったか（マージ済みか、`--production-sha` が正規の squash か）。入れたコミットを「反映したコミット」と呼ぶ |
| 2 | 値の中に、反映したコミットの SHA の先頭 7 文字以上と一致する 16 進の語があるか |
| 3 | 作業ツリーの宣言は見ない。push を読むかと、どの時点の配布先の宣言（`branch_strategy.release.deliveries`）を読むかは、出典の「配布先の宣言を読む時点」の手順 1〜6 だけで決め、ここでは繰り返さない。対象の出典を配布した時点に固定し、宣言を外した後の初回の記録でも、同じ push の後の PR が宣言を足した場合でも、`--target` で受けないためである。push を使う場合は、`after` の宣言を前処理の共有の結果（「配布先ごとに並べて打つ呼び出しの共有」）から取る。共有の結果が無い場合だけ `deliveries_at` で 1 回読み、前処理の結果に含めて共有する。`after` に宣言があれば `--target` を受けず、無ければ要る。push を読まないのは、同じ値の記録に `push` が無く、追跡参照から反映したコミットまで第一親の履歴がつながり、反映したコミットに宣言が無く、その後に設定のファイルを変えたどのコミットにも宣言が無い場合だけで、`--target` が 1 つ以上要る。履歴がつながらなければ、宣言の有無を決めずに止まる。今回の対象の配布先（`targets`）を決める。宣言があれば、`paths` を持たない配布先と、本番の PR の差分と push の差分の両方に出るパスが当たる配布先。無ければ `--target` の集合。空なら止まる |
| 4 | `--environment` が `targets` の部分集合か、`--observed` が `--environment` に含まれるか |
| 5 | 今回の `targets` が正の `targets` と同じか |
| 6 | 値とコミットが 1 対 1 か（同じコミットの記録が別の値で無いか、同じ値の記録が別のコミットで無いか） |
| 7 | 配布先ごとに、同じ値・正の `targets` の記録が含めた PR のすべてに既にあるか。あればその配布先を `kept` に載せ、確かめ 8 も書き込みもしない |
| 8 | 残った配布先ごとに、反映を何で確かめたか（`confirmation` の `cd`・`observed`・`attested`）を決める。失敗は `failed`、未確定は `pending` に載せて書かない（出典の「配布先ごとの反映の確かめ」） |

書く順の手順（出典: 設計書の同じ節の「書く順」）: 1. 記録の PR へ書く。2. 記録の PR を読み直し、正の `targets` か値とコミットの組が自分と違えば、自分のコメントを消して止まる。3. 残りの含めた PR へ書く。4. 書いた PR を読み直し、自分より前に同じ記録があれば自分のコメントを消す。

読み取りの手順（出典: 設計書の「閉じる手順の読み取り」）: 1. マーカーで候補を選ぶ。2. 形と引数で絞る。3. バージョン番号を持たないリポジトリの記録を、信用を確かめながら集め、値とコミットの組と正の `targets` を決めてから引数で絞る。4. 訂正の記録を照らす。5. 代表を選ぶ。6. `--bodies` なら含めた PR の本文を読む。

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
├── skills/
│   ├── development-workflow/{schemas/project.schema.json, references/project-flow.md *}
│   └── progress-tracking/SKILL.md
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
| `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` | 変更 | 同じ形を設定全体のスキーマに入れる。配布先の宣言に `paths`・`observe`・`result.env`（予約した名前を `not` の `pattern` で拒む）を足す | — | スキーマ | — |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | 上の写し（`check-ndf-contracts.py` の写しの一致の検査の対象） | — | スキーマ | — |
| `plugins/ndf/skills/development-workflow/references/project-flow.md` | 新設 | 流れの文書。生成の印で挟んだ範囲を `check-ndf-contracts.py --write` が書き、手では直さない | — | 文書 | — |
| `plugins/ndf/skills/progress-tracking/SKILL.md` | 変更 | 「閉じる条件」のセクションに、`workflow.close` で外せる 2 つの条件と免除する範囲を書き足す（流れの文書の正本の 1 つ） | — | 文書 | — |
| `plugins/ndf/contracts/schemas/release-verification.schema.json` | 変更 | `version` の形は変えない（#4513 が、バージョン番号の形の `pattern` をやめ、工程の文脈の配布識別子 `context.schema.json` の `$defs/distribution_id`（改行を含まない、空白だけでない 1 行の文字列）を参照する形に変える。この課題はその形の上に足す）。任意の `environments`・`unit` と、行の `retest_reason` を足す | — | スキーマ | — |
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
| `plugins/ndf/src/ndf/domains/release/record_decide.py` | 変更 | 記録の形・値と SHA の照合・対象の決め方・正の一覧（同じ値の記録が従う対象の配布先の一覧）・代表（閉じる判定で `release` と `included` を取る 1 件のリリース記録）・判定に使う行・`to_test` | — | モジュール `record_decide` | `Record`（項目）・`Correction`・`Push`・`Confirmation` と下の「関数の仕様」の新設の関数 |
| `plugins/ndf/src/ndf/domains/release/record.py` | 変更 | バージョン番号を持たないリポジトリの記録の書き込みと訂正、閉じる手順の読み取り | `record_decide`・`adapters.github`・`adapters.git`・`httpx` | モジュール `record` | `write_unversioned`・`correct_targets`・`read_release`・`write_verification`（引数） ほか下の表 |
| `plugins/ndf/src/ndf/domains/release/cli.py` | 変更 | 引数の受け口と結果の組み立て | `record` | モジュール `cli` | `cmd_record`・`cmd_records`・`cmd_verification_record`（中身）・`_record_args`（新設） |
| `plugins/ndf/src/ndf/adapters/github.py` | 変更 | 照会できない権限を例外で返す読み方、push の記録・CD の実行の一覧・コメントの削除 | githubkit | クラス `GitHub`・`Authored` | `Authored.comment_id`（項目）・`permission`・`_read_permission`（中身）・`permission_strict`・`trusts_strict`・`branch_activity`・`workflow_runs`・`delete_comment` |
| `scripts/contracts_lib/project_flow_doc.py` | 新設 | 流れの文書（`plugins/ndf/skills/development-workflow/references/project-flow.md`）の生成の印で挟んだ範囲を、正本（「流れの文書の生成」の表の 4 つ）から作る | `contracts` のデータ・`progress-tracking` の `SKILL.md` | モジュール `project_flow_doc` | `render`・`replace_generated`・`mismatches` |
| `scripts/check-ndf-contracts.py` | 変更 | `--write` で書き出し、引数なしで違いを違反にする | `project_flow_doc` | モジュール | `main`（引数）・`flow_doc_violations`（新設） |

## 流れの設定の読み取り

満たす決定: 決定 1・2・4・8、受け入れ条件 1・3〜5・10・11・13、不変条件 I1・I4・I5

出す回: 1 回目は `Flow`・`CloseRule`・`read_flow`・`close_rules` だけ。2 回目に残りを足す。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Flow` | `progress/flow.py` | 新設 | `@dataclass(frozen=True) Flow(kind: str \| None, stages: Mapping[str, Mapping], modes: Mapping[str, Mapping[str, Mapping]], close: Mapping[str, CloseRule], declared: bool)` | 読んだ流れの設定。`declared` は `workflow` のどれかの項目を書いたか |
| `CloseRule` | `progress/flow.py` | 新設 | `NamedTuple CloseRule(required: bool, reason: str \| None)` | 閉じる条件 1 つの要否と理由 |
| `FlowItem` | `progress/flow.py` | 新設 | `NamedTuple FlowItem(kind: str, where: str, reason: str)` | 誤りか食い違いの 1 件（`kind` は設計書の「設定の確かめが示す食い違い」の 6 つと、形の誤りの `flow_invalid`） |
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
| 種類の名前と省く工程 | 設定値 | `contracts/data/workflow-kinds.json` | 種類の名前は `web-develop`・`web-trunk`・`mobile`・`docs`・`plugin` の 5 つ。行わない工程を持つ種類は `docs` だけで、その工程は「リファクタリング」の 1 つ（決定 3）。ほかの 4 つの種類の行わない工程は空 |
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
| `merged_stages` | 同じ工程の指定が複数の層にあるとき、後の層（種類の既定 → `stages` → `modes.<モード>.stages` の順）の指定で丸ごと置き換える | 同じ工程を有効に戻す層がある | — |
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
| `_check` | `domains/project/cli.py` | 変更 | 今と同じ | `items` に `_flow_items` を足し、`summary` に件数を足す。終了コードは今と同じく `check.judge`（`domains/project/check.py`）が返す設定の状態だけで決め、`_flow_items` の件数では変えない。状態と終了コードは、`fresh`（`.ndf/project.json` があり、解析の記録 `analysis` の入力が今と変わっていない）で `ok`（0）、`missing`（設定が無い）・`stale`（`analysis` が無いか入力が変わった。`--force` を渡すと、入力が変わっていなくても `stale` にする。`missing`・`broken` は `--force` でも変わらない）で `not_applicable`（3）、`broken`（設定を読めない）で `invalid`（2）である |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_flow_items` | 流れの設定の形の誤り | `read_flow` の `ContractError` | `_flow_items` | `project check` を止めない。流れの設定を読めないので食い違いの確かめ（`flow.conflicts`）は行わず、`kind` が `flow_invalid` の 1 件（箇所と `ContractError` の理由）だけを返す。`_check` は設定の状態の判定とほかの `items` をそのまま続ける。`flow_invalid` は `project.result.schema.json` の `flow_*` の一覧に、設計書の 6 つと並べて足す | 終了コードは変えない（決定 8） | 読み取りだけ。再実行できる |

### 単体テストの観点

保存先: `plugins/ndf/tests/domains/project/test_project_cli.py`

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_check` | 受け入れ条件 10 の食い違いの 4 つ（(a) 知らない種類 `flow_unknown_kind`・(b) 無いモードか工程の名前 `flow_unknown_name`・(c) 閉じる条件の食い違い `flow_close_conflict`・(d) バージョンの食い違い `flow_version_conflict`）のそれぞれで箇所と理由が `items` に出る | 食い違いの無い設定で出ず、終了コードが変わらない | 形の誤りでも終了コードが変わらない |

## 閉じる判定

満たす決定: 決定 5・7、受け入れ条件 1・6・7・8・9、不変条件 I2

出す回: 1 回目

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Verdict` | `records/close_decide.py` | 新設 | `NamedTuple Verdict(decided: str, reason: str, verified: bool, release: dict[str, Any] \| None)` | 判定・理由・テストで確認できたか・反映の判定 |
| `verdict` | `records/close_decide.py` | 変更 | `verdict(issue: int, verification: Optional[Iterable[Mapping]], enforced: bool) -> Verdict` | 今の判定に `verified`（渡した記録にその課題の行があり、すべて合格）を足す。第 3 引数は `require` から `enforced` に名前を変え、渡す値も変える。`_close_check` が渡すのは `--require` の値そのものでなく、`conditions_of` が決めた `conditions.release_verification.enforced`（`--require` があり、設定でテストの記録を外していないときだけ真）である |
| `conditions_of` | `records/close_decide.py` | 新設 | `conditions_of(rules: Mapping[str, CloseRule], require: bool) -> dict[str, dict]` | 設計書の「close-check の結果」の `conditions` の 6 項目 |
| `records_verdict` | `records/close_decide.py` | 新設 | `records_verdict(issue: int, view: Mapping[str, Any], enforced: bool) -> Verdict` | `records` の出力 1 つから、配布先と課題の組ごとに判定に使う行を選び、設計書の判定の表で決める |
| `_records_view` | `records/cli.py` | 新設 | `_records_view(path: Path) -> tuple[dict \| None, str \| None]` | `--records` のファイルを読み、`release-steps records` の結果 JSON として確かめて `outputs` を返す（下の事後条件） |
| `_close_check` | `records/cli.py` | 変更 | 今と同じ | 設定を読み、引数の組を確かめ、`verdict` か `records_verdict` を呼び、`outputs.conditions`・`verified`・`release` を返す |

- `records_verdict`
  - 事前条件: `view` は `ndf release-steps records --version` の出力の `outputs`（代表・`releases`・`targets`・`verifications`）
  - 事後条件: `targets` を持たない代表（バージョン番号を持つリポジトリの記録）では `release` が `null` である。テストの記録は、代表と `version`・`unit` が同じもののうち記録の PR で最も後に書かれた 1 件だけを選び、その 1 件を `--verification` に渡したときと同じく `verdict` で判定する。それより前のテストの記録の行は使わない
  - 事後条件: `release.complete` と `release.missing` は、代表と `version`・`release`（本番の PR）・`unit`・`commit` が同じで、`targets` が正の一覧と同じリリース記録の `environments` だけで決める（絞るのは `complete_of`）。同じ値を使う別の配布単位や別の本番の PR の記録では、未反映の配布先を埋めない
- `_records_view`
  - 事後条件: 結果 JSON 全体を `release-steps.result.schema.json` で確かめる。通すのは、`tool` が `release-steps`、`status` が `ok`、`outputs.command` が `records` で、`outputs` が `$defs.records_outputs`（`command`・`selection`・`release`・`releases`・`targets`・`verifications`・`bodies`・`to_test`）に合う結果だけである。`cmd_records` は同じ `$defs.records_outputs` に合う `outputs` を書き、契約の検査（`check-ndf-contracts.py`）が書き手の出力の例をこの定義で確かめる。`records.result.schema.json` は `close-check` の結果の形で、入力の検証には使わない
  - 失敗: どれかが合わなければ `(None, 理由)` を返し、`_close_check` が `precondition`（3）で止まる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_close_check` | 流れの設定の形の誤り | `read_flow` の `ContractError` | `_close_check` | 止める | `precondition`（3）。閉じない | 直して打ち直せる |
| `_close_check` | 引数の組の誤り | `--records` と `--verification` を両方渡した。バージョン番号を持たないリポジトリで `--verification` を渡した | `_close_check` | 止める | `precondition`（3）。`--records` を渡すよう示す | 同上 |
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

- 繰り返しの終わる条件: 対象の配布先ごとに `record_decide.pair_verdict` を 1 回呼ぶ

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
| `Record` | `release/record_decide.py` | 変更 | `NamedTuple Record(version, release, included, unit=None, environments: tuple[str, ...] = (), targets: tuple[str, ...] \| None = None, commit: str \| None = None, push: Push \| None = None, confirmation: Mapping[str, str] \| None = None, evidence: Mapping[str, Evidence] \| None = None)` | バージョン番号を持たないリポジトリの記録の項目を足す（バージョン番号を持つリポジトリの記録では足した項目を書かない） |
| `Evidence` | `release/record_decide.py` | 新設 | `NamedTuple Evidence(url: str, value: str)` | 実環境の確認の証跡 1 つ。`url` は読んだ `observe.url`、`value` は `observe.pointer` が指した値（文字列） |
| `render_record` | `release/record_decide.py` | 変更 | 今と同じ | 足した項目を記録の JSON に書く。`evidence` は JSON のキー `evidence`（配布先の名前 → `{"url": <文字列>, "value": <文字列>}`）で、`confirmation` が `observed` の配布先にだけ書く |
| `parse_record` | `release/record_decide.py` | 変更 | 今と同じ | 足した項目を読む。形が違えば None（今と同じ）。`confirmation` が `observed` の配布先に `evidence` が無い記録、`observed` でない配布先に `evidence` がある記録、`url`・`value` が文字列でない記録も形の違いとして None にする |
| `Push` | `release/record_decide.py` | 新設 | `NamedTuple Push(before: str, after: str)` | 反映したコミットを本番ブランチへ入れた 1 回の push |
| `Correction` | `release/record_decide.py` | 新設 | `NamedTuple Correction(version: str, commit: str, targets: tuple[str, ...], previous: tuple[str, ...], reason: str)` | 対象の一覧の訂正の記録 |
| `render_correction`・`parse_correction` | `release/record_decide.py` | 新設 | `render_correction(c: Correction) -> str`・`parse_correction(body: str) -> Correction \| None` | 訂正の記録のコメントの本文と読み取り（マーカー `CORRECTION_MARK`） |
| `value_matches` | `release/record_decide.py` | 新設 | `value_matches(value: str, sha: str) -> bool` | 確かめ 2。値の 16 進の語のどれかが SHA の先頭 7 文字以上と一致するか |
| `production_deliveries` | `release/record_decide.py` | 新設 | `production_deliveries(rows: Sequence[Mapping]) -> list[Mapping]` | 決定 7 の「本番の配布先」の行 |
| `glob_matches` | `release/record_decide.py` | 新設 | `glob_matches(pattern: str, path: str) -> bool` | git の pathspec の `:(glob)` と同じ一致（先に文字そのままで比べ、続けて `**`・`*`・`?`・`[...]`・`\` の打ち消しで照らす。括弧の中の POSIX の文字クラス `[[:alpha:]]` なども受ける。`*` は `/` を越えない。閉じない括弧と知らない文字クラスはどのパスにも当てない） |
| `targets_of` | `release/record_decide.py` | 新設 | `targets_of(rows: Sequence[Mapping], pr_paths: frozenset[str], push_paths: frozenset[str]) -> tuple[str, ...]` | 確かめ 3。`paths` を持たない配布先と、両方の差分に出るパスが当たる配布先 |
| `push_of` | `release/record_decide.py` | 新設 | `push_of(activities: Iterable[Mapping], landed: frozenset[str]) -> Push \| None` | 「反映したコミットを含む push」の手順 2。`landed` は手順 1 のコミットの集合 S（反映したコミットから本番ブランチの先端までの第一親のコミット）である。アクティビティを新しい順に読み、`after` が `landed` にあり、かつ `before` が `landed` に無い更新のうち、最初に当たった 1 件を返す。`after` が `landed` に無い更新に当たったら打ち切って None を返す |
| `authoritative_targets` | `release/record_decide.py` | 新設 | `authoritative_targets(found: Sequence[Record], corrections: Sequence[Correction], version: str) -> tuple[str, ...] \| None` | 「正の `targets`」。最後の訂正、無ければ同じ値の最初の記録の一覧。`found` は `--release`・`--unit` で絞る前の有効な記録を渡す |
| `value_commit_conflict` | `release/record_decide.py` | 新設 | `value_commit_conflict(found: Sequence[Record], commit: str, version: str) -> tuple[str, str] \| None` | 確かめ 6。`value_pairs(found)` の組で、今回のコミットが別の値と組か、今回の値が別のコミットと組なら、その組の `(version, commit)` |
| `value_pairs` | `release/record_decide.py` | 新設 | `value_pairs(found: Sequence[Record]) -> tuple[dict[str, str], list[Record]]` | 設計書の「値とコミットの組」。古い順にたどって値からコミットへの組と、有効な記録（組に合う記録と、バージョン番号を持つリポジトリの記録）を返す。`authoritative_targets`・`has_unversioned`・`representative`・`complete_of` は有効な記録だけを受け取る |
| `has_unversioned` | `release/record_decide.py` | 新設 | `has_unversioned(found: Sequence[Record], version: str, environments: tuple[str, ...], targets: tuple[str, ...]) -> bool` | 重ねない判定（同じ値・同じ `environments`・正の `targets`） |
| `confirm` | `release/record_decide.py` | 新設 | `confirm(delivery: Mapping, run: Mapping \| None, observed: Any, after: str) -> Confirmation` | 「配布先ごとの反映の確かめ」の表を 1 回評価する（`Confirmation(state, kind, reason, evidence: Evidence \| None)`。`state` は `ok`・`failed`・`pending`）。引数 `observed` は、`_observe` が読んだ `observe.pointer` の値で、`--observed` でその配布先を指定していないか、読めなかったときは `_MISSING` である。`kind` が `observed` のときだけ `evidence` を持ち、`url` は `delivery` の `observe.url`、`value` は `observed` の文字列である |
| `json_pointer` | `release/record_decide.py` | 新設 | `json_pointer(doc: Any, pointer: str) -> Any` | RFC 6901 の 1 つの値（無ければ `_MISSING`） |
| `representative` | `release/record_decide.py` | 新設 | `representative(found: Sequence[Record], targets: tuple[str, ...] \| None) -> Record \| None` | 読み取りの手順 5 |
| `pair_verdict` | `release/record_decide.py` | 新設 | `pair_verdict(verifications: Sequence[Mapping], environment: str \| None, issue: int) -> str \| None` | 「判定に使う行」。組の行を古い順に並べ、最後に `retest_reason` を持つ行より前を捨て、残りを投稿の順によらずに合わせる（不合格が 1 つでもあれば `failed`、無ければ合格が 1 つでもあれば `passed`、保留だけなら `not_run`、行が無ければ `None`） |
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
| `value_pairs` | 古い順に組を決める | 同じコミットに 2 つの値が残ると後の値の記録が無効。2 つの SHA を含む同じ値が別のコミットに残ると後の記録が無効 | 渡した記録の順だけで決まり、`release` や `unit` を見ない |
| `value_matches` | `main abc1234` と SHA | 6 文字の語は数えない、大文字の 16 進 | 含まない値 |
| `targets_of` | `paths` の無い配布先は常に入る | 移動の前のパスで当たる。push の差分に出ないパス（打ち消し）は入らない | 空の結果 |
| `glob_matches` | `web/**` | `**/` が 0 段に当たる。`*` が `/` を越えない | — |
| `push_of` | merge queue がまとめた途中の PR の push | 新しい順で `after` が集合の外に出たら打ち切る | 見つからなければ None |
| `parse_record` | `evidence` つきの `observed` の記録を `render_record` の出力から読み戻す | バージョン番号を持つリポジトリの記録は足した項目なしで今と同じに読む | `observed` で `evidence` の無い記録、`evidence` の値が文字列でない記録は None |
| `authoritative_targets` | 最初の記録の一覧 | 最後の訂正が勝つ | 記録が無ければ None |
| `confirm` | `cd`・`observed`・`attested` | `pointer` の外の SHA は数えない | CD の失敗は `failed`。CD が未確定で、`observed` が `_MISSING`・宣言に `observe` が無い・`pointer` の値が `after` と一致しない、のどれかなら `pending` |
| `pair_verdict` | 合格と不合格の行がどちらの順でも `failed`。保留と合格は `passed` | `retest_reason` のある行より前の不合格は捨てる。理由の無い後の合格では不合格が覆らない | — |
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
| `correct_targets` | `release/record.py` | 新設 | `correct_targets(hub: GitHub, number: int, production: str, version: str, targets: tuple[str, ...], reason: str, production_sha: str \| None) -> str` | 確かめ 1・2・6 と訂正だけの確かめを通し、書く token の書き手を `_require_trusted_writer` で確かめてから、記録の PR へ訂正の記録を 1 件書く。書いたコメントの URL を返す |
| `_landed_commit` | `release/record.py` | 新設 | `_landed_commit(hub: GitHub, number: int, production: str, production_sha: str \| None) -> tuple[Pull, str]` | 確かめ 1。`released` の着地の部分を使い、head の名前を問わずに反映したコミットを返す |
| `_changed_paths` | `release/record.py` | 新設 | `_changed_paths(repo_git: Git, pull: Pull, commit: str, push: Push) -> tuple[frozenset[str], frozenset[str]]` | 「対象を決める変更の範囲」の 2 つの差分（`git diff --name-only -z --no-renames`、起動 2 回）。出力は NUL で区切ってパスにする |
| `_find_push` | `release/record.py` | 新設 | `_find_push(hub: GitHub, repo_git: Git, production: str, commit: str, known: Push \| None) -> Push` | 既存の記録の `push` があれば使い、無ければ `git rev-list --first-parent`（起動 1 回）と `branch_activity` で決める |
| `_shared_prefix` | `release/record.py` | 新設 | `_shared_prefix(hub: GitHub, repo_git: Git, number: int, production: str, production_sha: str \| None, compute: Callable[[], Prefix]) -> Prefix` | 設計書の「処理の流れ」の共有の仕方。`<共通の git ディレクトリ>/ndf/release-record/<本番の PR>.json` を `filelock.FileLock` で取り、あれば反映したコミットを照らして使い、無いか違えば `compute` で作って書く |
| `deliveries_at` | `release/record.py` | 新設 | `deliveries_at(repo_git: Git, after: str) -> DeliveriesAt` | 出典の「配布先の宣言を読む時点」。`git cat-file --batch` を 1 回起動し、`<after>^{commit}` と `<after>:.ndf/project.json` を読む。`branch_strategy.validate` の誤りの一覧で、`missing_ref`・`absent`・`broken`・`reserved`（予約した名前の誤りだけ。その配布先の名前を持つ）・`found` に分ける |
| `DeliveriesAt` | `release/record.py` | 新設 | `NamedTuple DeliveriesAt(kind: str, deliveries: tuple[Mapping, ...], no_command: frozenset[str], reason: str)` | `no_command` は予約した名前があり、`result.command` を実行しない配布先の名前 |
| `Prefix` | `release/record.py` | 新設 | `NamedTuple Prefix(commit: str, push: Push \| None, deliveries: Mapping \| None, pr_paths: frozenset[str], push_paths: frozenset[str])` | 共有する前処理の結果（配布の結果によらず決まるものだけ）。`deliveries` は push の `after` の配布先の宣言で、無ければ `None` |
| `_confirm_delivery` | `release/record.py` | 新設 | `_confirm_delivery(hub: GitHub, root: Path, delivery: Mapping, production: str, after: str, observed: bool, wait: bool, now: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> Confirmation` | 「CD の結果の待ち」の 1〜4 を 1 つの配布先で行う。CD の結果は `result.workflow` なら `workflow_runs`、`result.command` なら `_command_result` で読む。`result.command` の配布先では、`delivery` は push の `after` の宣言から読んだもの、`root` は `after` を取り出した一時の作業場所である。作業場所は、`write_unversioned` が `result.command` の配布先を照会するときに 1 回だけ作り、すべての配布先の判定を終えたら外す |
| `_command_result` | `release/record.py` | 新設 | `_command_result(workdir: Path, command: str, env_names: Sequence[str], after: str, deadline: float, now: Callable[[], float] = time.monotonic) -> Mapping` | 設計書の分けたファイルの「結果を読むコマンドの実行の境界」のとおりに実行する。`command` と `env_names`（`result.env`）は push の `after` の宣言から `deliveries_at` で読んだもの、`workdir` は `after` を `git worktree add --detach` で取り出した一時の作業場所である。`{revision}` を `after` に置き換えたコマンドを `workdir` で 1 回実行する。環境変数は `PATH`・`HOME`・`LANG`・`LC_ALL`・`TZ`・`TMPDIR` と `env_names` だけを渡す。`DeliveriesAt.no_command` に入った配布先では呼ばれない（呼び出し元の `_confirm_delivery` が未確定として扱う）。`workdir` は `git -c core.hooksPath=/dev/null worktree add --detach` で、同じ環境変数だけを渡して作る。終了コード 0・75・1・それ以外を、成功・未完了・失敗・未確定の `run` の形（`status`・`conclusion`）にする（cd-setup.md の契約）。時間の上限は `deadline − now()`（期限までの残り）で、0 以下なら実行せずに未確定を返す |
| `_observe` | `release/record.py` | 新設 | `_observe(delivery: Mapping, timeout: float) -> tuple[Any, str]` | `observe.url` を `httpx` で 1 回読み、`pointer` の値と読んだ URL を返す |
| `_write_one` | `release/record.py` | 新設 | `_write_one(hub: GitHub, made: Record, holder: int, others: Sequence[int]) -> list[Written]` | 1 つの配布先の記録を書く順の手順 1〜4 で書き、重なりを自分のコメントの削除で戻す |
| `_candidates` | `release/record.py` | 新設 | `_candidates(hub: GitHub, number: int, select: Selection) -> Candidates` | 設計書の読み取りの手順 1〜4 を行い、引数で絞った有効なリリース記録・値ごとの正の `targets`・訂正・テストの記録を返す（手順は下の箇条） |
| `Candidates` | `release/record.py` | 新設 | `NamedTuple Candidates(releases: list[Record], authoritative: dict[str, tuple[str, ...]], corrections: list[Correction], verifications: list[dict])` | `releases` は引数で絞った有効な記録、`authoritative` は値ごとの正の `targets`（絞る前の有効な記録と訂正で決めた） |
| `Selection` | `release/record.py` | 新設 | `NamedTuple Selection(version: str \| None, release: int \| None, unit: str \| None)` | `records` の絞り込みの引数 |
| `read_release` | `release/record.py` | 新設 | `read_release(hub: GitHub, number: int, select: Selection, bodies: bool, issues: Sequence[int] \| None) -> dict[str, Any]` | 読み取りの手順 1〜6 と `to_test`。`records` の新しい `outputs` を返す |
| `pr_body_only` | `release/record.py` | 新設 | `pr_body_only(hub: GitHub, number: int) -> dict[str, Any]` | `--pr-body-only`。その PR の本文と `trusted` だけを返す |
| `write_verification` | `release/record.py` | 変更 | `write_verification(hub, number, payload, names=_no_names, unversioned: bool = False) -> str` | `unversioned` なら、記録の PR・`environments` の反映の記録・`unit`・`retest_reason` を確かめてから書く。`retest_reason` の要否は `pair_verdict` が `failed` を返すかで決める |
| `cmd_record` | `release/cli.py` | 変更 | 今と同じ | 引数の組（確かめ 0）で `write`・`write_unversioned`・`correct_targets` を呼び分け、`record` の終わり方の表で状態を決める |
| `_record_args` | `release/cli.py` | 新設 | `_record_args(args: argparse.Namespace, strategy: Strategy) -> Unversioned \| None` | 確かめ 0。バージョン番号を持つリポジトリ（`version_store.kind` が `none` でない）では `--version`・`--environment` を断る |
| `cmd_records` | `release/cli.py` | 変更 | 今と同じ | 新しい引数があれば `read_release` か `pr_body_only`、無ければ今の `read` |
| `cmd_verification_record` | `release/cli.py` | 変更 | 今と同じ | バージョン番号を持たないリポジトリなら `unversioned=True` で呼ぶ |

- `_confirm_delivery`
  - 事後条件: 期限 `deadline` は、1 回目の CD の照会の直前に `now() + result.timeout_seconds`（無ければ 1800）で 1 回だけ決め、照会し直すたびには決め直さない。`workflow_runs`・`_command_result`・`_observe` には同じ `deadline` から求めた残り時間（`deadline − now()`）を時間の上限として渡し、次の照会までは `min(間隔, 残り時間)` だけ待つ。どの操作も始める前に残り時間が 0 以下なら始めず、始めた操作が上限に当たったときも、`pending` を返す
- `write_unversioned`
  - 事前条件: バージョン番号を持たないリポジトリである。`args.environments` が 1 つ以上
  - 事後条件: 確かめ 0〜6 で止まれば、どの PR にも書いていない。書いた配布先ごとに、含めた PR の各々に、同じ値・同じ `environments`・正の `targets` の信用する記録がちょうど 1 件ある
  - 副作用: GitHub のコメントの読み取り・書き込み・自分が書いたコメントの削除、git の起動は、記録が `push` を持たず共有の置き場にも結果が無ければ push を読むかの判定の 2 回（第一親の履歴と設定のファイルを変えたコミットを読む `git log` と、`git cat-file --batch` による先端の SHA と宣言の読み取り）と、push の時点の宣言を読むときに前処理の 3 回（差分の 2 回と、`git cat-file --batch` による宣言の読み取りの 1 回。K が 2 以上なら第一親をたどる 1 回を足す。共有の置き場を使えたときはどちらも 0 回）。共通の git ディレクトリは CLI が解決した値を使い、起動しない、`result.command` の配布先があれば作業場所の追加と削除の 2 回、共有の置き場の読み書きと削除、CD の実行の照会、`observe.url` の HTTP の読み取り
- `_candidates`（番号は設計書の読み取りの手順のまま。「取得」はコメントを読むこと、「照会」は書き手の照会）
  - 手順 1: 記録の PR のコメントを 1 回だけ取得し（ページ送りを含む）、マーカーを持つコメントを候補にする
  - 手順 2: JSON の形が誤った候補を、照会せずに除く。バージョン番号を持つリポジトリの記録とテストの記録は、`--version`・`--release`・`--unit` のうち渡したものが一致する候補だけを残す
  - 手順 3: バージョン番号を持たないリポジトリのリリース記録を、次の順に集める
    1. 起点を決める。`--version` があればその値の記録、無ければ `--release`・`--unit` が一致する記録、どちらも無ければすべての記録
    2. 起点の記録の書き手を照会し、信用する書き手の記録だけを残す
    3. 残った記録と値かコミットが同じで、まだ照会していない記録を足し、その書き手を照会する。足す記録が無くなるまで繰り返す。広げる元は信用を確かめた記録だけである
    4. 集めた記録で `value_pairs` を呼び、組に合わない記録を無効にする
    5. 有効な記録から、値ごとの正の `targets` を `authoritative_targets` で決める（まだ引数で絞らない）
    6. 有効な記録を、渡した `--version`・`--release`・`--unit` のすべてが一致するものだけに絞る
  - 手順 4: 訂正の記録のうち、`version` が一致し、`commit` が手順 3 の組のコミットと一致するものだけの書き手を照会し、信用する書き手の訂正が残れば、その値の正の `targets` を最後の訂正の一覧に置き換える
- `read_release`
  - 事後条件: 照会できない書き手が候補か本文にいれば、何も選ばずに `PermissionUnknown` を投げる。候補でないコメントの書き手は照会しない
  - 副作用: GitHub の読み取りだけ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_record_args` | 引数の組の誤り | 確かめ 0 の「止まるとき」 | `_record_args` | 止める | `precondition`（3） | 何も書いていない。直して打ち直せる |
| `_landed_commit` | 本番へ入っていない | 未マージで `--production-sha` が無いか正規の squash でない | `landed_squash` | 止める（`NotReleased`） | 今と同じ | 同上 |
| `write_unversioned` | 値と反映したコミットが対応しない | 確かめ 2 | `value_matches` | 止める | `precondition`（3）。反映したコミットの SHA を載せる | 同上 |
| `_find_push` | push を決められない | 手元の追跡参照が古い（GitHub でマージした直後に取得していない） | 先端から反映したコミットまで第一親の履歴がつながらないか、`git log` が失敗した | 止める（アクティビティは読まない） | `precondition`（3）。`git fetch <リモート> +refs/heads/<本番ブランチ>:refs/remotes/<リモート>/<本番ブランチ>` で取得してから打ち直すよう示す（取得先の追跡参照まで明示する）。浅い clone で履歴が途中で切れていれば、同じ参照の指定で `git fetch --unshallow` を示す | 同上 |
| `_find_push` | push を決められない | アクティビティにまだ載っていないか、追跡参照が古く新しい更新が S の外にある | `push_of` が None | 止める | `precondition`（3）。追跡参照が古ければ `git fetch` で取得し、push の直後なら時間を置いて打ち直すよう示す | 同上 |
| `write_unversioned` | 対象が空・正と違う・同じコミットに別の値 | 確かめ 3・5・6 | 各確かめ | 止める | `precondition`（3）。正の一覧と訂正の打ち方、または既にある値を載せる | 同上 |
| `_confirm_delivery` | CD が失敗 | 実行の `conclusion` が `success` 以外 | `confirm` | 呼び出し元へ返す（`failed`） | 書けた配布先があれば `ok`（0）、無ければ `refused`（1） | その配布先は打ち直さず、配布の失敗として報告する |
| `_confirm_delivery` | 未確定 | 実行が無い・未完了、`observe` で確かめられない、待ちの上限（`result.timeout_seconds`） | `confirm` | 呼び出し元へ返す（`pending`） | すべて未確定なら `precondition`（3）で `items` に理由 | その配布先だけで打ち直せる |
| `_observe` | 読めない | 接続・HTTP の失敗、JSON でない | `httpx` の例外・`json` | 既定値で続ける（未確定の理由にする） | 上と同じ | 同上 |
| `_write_one` | 投稿の失敗 | GitHub の失敗 | `Publisher.comment` | 止める（`GitHubError`） | 今の `release-steps` が GitHub の失敗で止まる形（停止理由 `unknown`・終了コード 1。レート制限で待つ時間が分かるときは `infra_wait`・75）。配布先の一覧の `outputs.failed` には入れない | 打ち直すと、書けた PR は重ねない判定で飛ばす |
| `_write_one` | 並行した書き込みに負けた | 手順 2 で正の `targets` か、値とコミットの対応が自分と違う | 読み直し | 自分のコメントを消して止める | `precondition`（3）。正の一覧か既にある値を載せる | 正しい引数で打ち直せる |
| `_candidates`・`read_release` | 権限を照会できない | 403・404（レート制限によるものを除く） | `GitHub.permission_strict` | 止める（`PermissionUnknown`） | `failed`。照会できなかった書き手の名前を載せる | 閉じる手順は課題を閉じずに「失敗」と報告する |
| `correct_targets` | 訂正できない | 宣言がある・同じ値の信用する記録が無い・引数の組の誤り | `correct_targets` | 止める | `precondition`（3） | 先に `record` を打つか、引数を直す |
| `correct_targets` | 書く token が信用されない | 書く token の書き手が write 以上の権限を持たない | `_require_trusted_writer` | 止める（`RecordError`） | `precondition`（3）。今の `record` と同じ文 | 何も書いていない。利用者の token で打ち直す |
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
| `_shared_prefix` | `filelock`（`plugins/ndf/pyproject.toml` の依存）・`Git.common_dir` | `grep -n filelock plugins/ndf/pyproject.toml`・`grep -n "def common_dir" adapters/git.py` | 使う |
| `_observe` | `httpx`（`plugins/ndf/pyproject.toml` の依存） | `grep -n httpx plugins/ndf/pyproject.toml` | 使う |
| `glob_matches` | `fnmatch`・`PurePath.full_match` | 標準ライブラリの説明 | 使えない（`fnmatch` の `*` は `/` を越える。`full_match` は 3.13 からで、下限は 3.11） |

### 単体テストの観点

保存先: `plugins/ndf/tests/domains/release/test_release_record.py`（GitHub と git は偽物、HTTP は `httpx.MockTransport`）

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_command_result` | 終了コード 0 で成功、1 で失敗 | 75 で未完了、それ以外で未確定。期限までの残りが時間の上限として渡る。残りが 0 なら実行しない | 時間の上限で未確定（実行するのは偽の `subprocess.run`、時刻は偽の `now`。子のプロセスを起動せず、本物の時間を待たない） |
| `_shared_prefix` | 置き場が無ければ `compute` を 1 回呼んで書き、2 回目は呼ばない | 置き場の反映したコミットが今と違えば作り直す。`complete` で消える | 同じ名前で NDF が書いたものでないファイル（壊れた JSON を含む）は残し、`compute` の結果を書かずに返す。`complete` の削除と掃除でも残し、中断で残った書きかけの一時ファイル（`.ndf-tmp`）は掃除で消す（ロックとファイルは一時ディレクトリ。子のプロセスを起動しない） |
| `_confirm_delivery` | 期限の前に CD が成功すれば `cd` | 期限の直前に始めた照会・コマンド・実環境の確認には残り時間だけが上限として渡り、待機は残り時間で切られる | 期限を過ぎたら次の照会をせず `pending`（時刻は偽の `now`、待機は偽の `sleep` で、本物の時間を待たない） |
| `write_unversioned` | 含めた PR の各々へ書き、作業の PR が無ければ本番の PR へ書く | 同じ引数の打ち直しで CD を照会しない。2 つの配布先の一方だけ完了で `recorded` と `pending` に分ける。並行した同じ引数の 2 回で記録が 1 件に戻る | 確かめ 0〜6 の各行で何も書かない。すべて未確定で `precondition`、書けた配布先が無く失敗があれば `refused` |
| `correct_targets` | 訂正の記録を足し、前の記録を消さない | 訂正の後、新しい一覧で打ち直した配布先では前のテストの記録が判定に使われる | 宣言がある・記録が無い値で止まる。信用されない token では書かない |
| `read_release` | `--version` の値の記録を代表にし、`--unit` の単位だけを使う。同じ記録が 2 件残っていても 1 件として数える | 正と違う記録が最後にあっても正の記録が代表。別のリリースの記録の書き手の照会が 403 でも読める | 候補の書き手の照会が 403 で失敗。`--bodies` で読んだ含めた PR の本文のうち、照会が 200 で `read` を返した書き手の本文は、失敗にせず `bodies` の `trusted` が偽になる |
| `_candidates` | 信用したリリース記録のコミットの訂正を残す | 信用されない書き手のリリース記録にだけ対応する訂正は、書き手を照会せずに除く（照会が 403 でも止まらない） | 信用したリリース記録のコミットの訂正の書き手が 403 で `PermissionUnknown` |
| `_candidates`（`--release` との順） | 並行した書き込みで負けた側の本番の PR を `--release` で渡すと、その記録は無効で代表にならない | 同じコミットに残った後の値の記録は、先の値の読み取りに影響しない | — |
| `_candidates`（`--version` で絞る） | 古い順に `(A, X)`・`(B, X)`・`(B, Y)` の記録で `--version A` を渡すと、`releases` は `(A, X)` だけで、`(B, Y)` は代表にも `releases` にも入らない | `--version` を渡さず `--release` だけを渡すと、その本番の PR の記録を起点に組を決めて返す。`--unit` だけでも同じ | 同じ値・同じコミットで `targets` の違う後の記録が消されずに残り、その本番の PR を `--release` で渡しても、正の `targets` は先の記録の一覧で、後の記録は代表にならない |
| `_candidates`（橋渡し） | 信用する記録と値かコミットを共有する記録の書き手を照会する | 信用しない書き手の記録とだけコミットを共有する、別の値の記録の書き手は照会しない（その書き手の照会が 403 でも止まらない） | 信用する記録と値を共有する記録の書き手が 403 なら `PermissionUnknown` |
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
| `permission_strict` | `adapters/github.py` | 新設 | `permission_strict(self, login: str) -> str` | レート制限によるものでない 403・404 を `PermissionUnknown` にする。GitHub が 200 で返した値は `permission` と同じ保存（`_permissions`）に入れて 1 人 1 回にする |
| `permission` | `adapters/github.py` | 変更 | 今と同じ | 返す値は今と同じ（403・404 は `none`）。ただし、403・404 で変換した `none` は `_permissions` に入れず、照会できなかった書き手の集合（`_unreadable`）に入れる。GitHub が返した `none` と、照会できずに `none` と読んだものを保存の上で分けるためである |
| `_read_permission` | `adapters/github.py` | 変更 | `_read_permission(self, login: str) -> str \| None` | レート制限によるものでない 403・404 で `None` を返す（変換しない）。`none` への変換は `permission` が行う |
| `trusts_strict` | `adapters/github.py` | 新設 | `trusts_strict(self, authored: Authored) -> bool` | `trusts` と同じ区分の扱いで、照会に `permission_strict` を使う |
| `branch_activity` | `adapters/github.py` | 新設 | `branch_activity(self, branch: str) -> Iterator[dict]` | `GET /repos/{repo}/activity?ref=refs/heads/<branch>` を新しい順に 1 ページずつ返す（呼び出し元が打ち切れる） |
| `workflow_runs` | `adapters/github.py` | 新設 | `workflow_runs(self, workflow: str, branch: str, commit: str, timeout: float \| None = None) -> list[dict]` | `GET /actions/workflows/<workflow>/runs?branch=&head_sha=&event=push`。push で起動した実行だけを返す。`timeout` はこの照会の時間の上限（`None` は今の既定） |
| `delete_comment` | `adapters/github.py` | 新設 | `delete_comment(self, comment_id: int) -> None` | `DELETE /issues/comments/<id>` |

- `permission_strict`
  - 事後条件: `login` が `_unreadable` にあれば、照会し直さずに `PermissionUnknown` を投げる。同じ実行で `permission` が先に照会に失敗していても、照会できなかった書き手を権限が無い書き手として除かない
  - 事後条件: `_permissions` にある値は、`none` を含めて GitHub が 200 で返した値だけで、そのまま返す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `permission_strict` | 照会できない | レート制限によるものでない 403・404 | `rest` の `GitHubError` | 止める（`PermissionUnknown`） | 呼び出し元が `failed` にする | 権限を直して打ち直せる |
| `branch_activity` | ページの上限 | `github.list-pages` を超えた | ページ数 | 止める（`GitHubError`） | `failed` | 打ち直せる |
| `delete_comment` | 消せない | 権限・ネットワーク | `rest` | 止める（`GitHubError`） | `record` は停止理由 `unknown`（終了コード 1。レート制限で待つ時間が分かるときは `infra_wait`・75）で止まり、消せなかったコメントの URL を `items` に載せる。配布先の一覧の `outputs.failed` には入れない | 消せなかったコメントは残してよい。記録を読むコマンド（`records` と `close-check`）は重複と正でない記録を数えない（設計書の「失敗や重なりの後の動き」）ので、判定は 1 回で書けたときと同じになる。打ち直すと確かめ 7 で `kept` になり、何も書かず消しもしない |

### 単体テストの観点

保存先: `plugins/ndf/tests/adapters/test_github.py`

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `permission_strict` | 200 の権限を返し、2 回目は照会しない | 200 で返った `none` は `none` を返す。先に `permission` が 403 で `none` と読んだ書き手は、照会し直さずに `PermissionUnknown` | 403・404 で `PermissionUnknown`、レート制限は今と同じ `GitHubError` |
| `trusts_strict` | 所属の区分が OWNER の書き手は照会せずに真。MEMBER・COLLABORATOR は照会し、write 以上で真、`read` などで偽 | OWNER・MEMBER・COLLABORATOR 以外の区分（CONTRIBUTOR・NONE など）は照会せずに偽 | MEMBER・COLLABORATOR の照会が 403・404 のときは、偽を返さず `PermissionUnknown` を投げる |
| `branch_activity` | 1 ページずつ返す | 短いページで終わる | 上限で止まる |

## 流れの文書の生成

満たす決定: 決定 10、受け入れ条件 12

出す回: 2 回目

生成先は `plugins/ndf/skills/development-workflow/references/project-flow.md` の生成の印で挟んだ範囲である。正本は次の 4 つで、パスはリポジトリの根から書く（設計書の決定 10）。

| 生成する表 | 正本 |
| --- | --- |
| モードごとの工程の並びと、条件で省く工程 | `plugins/ndf/contracts/workflow/modes/*.json` と工程の定義 `plugins/ndf/contracts/workflow/stages/`（どちらも `progress/definition.py` が読む） |
| 種類ごとに省く工程と理由 | `plugins/ndf/contracts/data/workflow-kinds.json` |
| 種類ごとの本番の承認の場面と、リリースの結果として記録する値 | `plugins/ndf/contracts/data/branch-strategy-presets.json` |
| 閉じる条件と、利用者が外せる条件 | `plugins/ndf/skills/progress-tracking/SKILL.md` の見出し `### 閉じる条件` の次の行から次の見出しの直前まで（機械的に抜き出す） |

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `render` | `scripts/contracts_lib/project_flow_doc.py` | 新設 | `render(root: Path) -> str` | 上の表の 4 つの正本から、生成の印で挟む範囲の本文を作る |
| `replace_generated` | `scripts/contracts_lib/project_flow_doc.py` | 新設 | `replace_generated(doc: str, generated: str) -> str` | 生成の印の間を置き換えた本文 |
| `mismatches` | `scripts/contracts_lib/project_flow_doc.py` | 新設 | `mismatches(root: Path) -> list[dict]` | `project-flow.md` の今の印の間と、生成し直した本文が違えば違反 1 件 |
| `flow_doc_violations` | `scripts/check-ndf-contracts.py` | 新設 | `flow_doc_violations(root: Path, write: bool) -> list[dict]` | `--write` なら `project-flow.md` へ書き出し、無ければ `mismatches` を返す |
| `main` | `scripts/check-ndf-contracts.py` | 変更 | 今と同じ | 引数 `--write` を足し、`find` の違反に `flow_doc_violations` を足す |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `render` | 抜き出せない | `progress-tracking/SKILL.md` に見出し `### 閉じる条件` が無い | `render` | 止める（違反 1 件） | 検査の結果 JSON の違反。終了コード 1 | 正本を直して打ち直す |
| `replace_generated` | 印が無い | `project-flow.md` から印を消した | `replace_generated` | 止める（違反 1 件） | 同上 | 印を戻す |

### 単体テストの観点

保存先: `scripts/tests/test_check_ndf_contracts.py`（今の検査のテスト）

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mismatches` | 生成し直した本文と同じなら空 | データか「閉じる条件」のセクションだけを変えると違反 | 印が無い・見出しが無いで違反 |
