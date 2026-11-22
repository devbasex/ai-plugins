# #4105 の詳細設計: リリース承認ゲートの保留を読む範囲（組み立ての時点・判定する PR・含む PR・印）

設計文書 [issue-4105-design.md](./issue-4105-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。前提は #3959 の実装が main へ入った後の形である（[issue-3959-4055-design-detail.md](./issue-3959-4055-design-detail.md) の `validation_rows`・`review_rows`・`review_holds`・`review_prs`・`MvvFacts.review_holds`・`check_review_holds`・`MVV_SKIP_GATES`・`Snapshot.commit`、`gate_outcome` の `holds`）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── workflow/stages/deploy.json            … 先頭に release-prepare
│   ├── workflow/context.schema.json           … $defs.mvv_gate に scope
│   └── templates/release-prepare.json *       … 組み立ての worker のプラン
├── src/ndf/domains/records/
│   ├── mvv_gate.py                            … release_target・review_prs（release）・_judged
│   └── mvv_material_decide.py                 … gate_outcome に scope
├── src/ndf/progress/gate/checks.py           … note が scope を載せる
├── src/ndf/domains/release/
│   ├── record_decide.py                       … assembled_message（_approved_message を置き換える）・fork_point
│   ├── record.py                              … pull_commits（_release_commits を一般化）・assembled_included・_included
│   └── assemble.py                            … open_release_numbers・assembles（判定と組み立てで共有する）
├── src/ndf/progress/
│   ├── model.py                               … MVV_SKIP_GATES・mvv_marked・mvv_skip・mvv_answer_void
│   ├── run.py                                 … StepProgress._answered
│   └── gate/
│       ├── records.py                         … answers
│       └── asking.py                          … question
├── skills/release/SKILL.md                     … 組み立てを release-prepare へ
├── skills/development-workflow/references/    … pace.md・approval-request.md・agent-layers.md・production-merge.md
└── tests/
    ├── domains/records/test_mvv_gate.py
    ├── domains/release/test_release_record.py
    ├── progress/test_model.py
    ├── progress/test_run.py
    ├── progress/test_definition.py
    └── progress/gate/test_gate_answer.py
```

依存の向き: `records.mvv_gate` が `domains.release.record`・`domains.release.assemble`（`open_release_numbers`）・`core.branch_strategy` を読む。`release` は `records` と `progress` を読まない。`progress` は `records` と `release` を読まない（`model.mvv_marked` は `mvv_gate` の値の形だけを見る）。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/workflow/stages/deploy.json` | 変更 | デプロイの工程の定義 | — | — | ステップ `release-prepare`（`ndf.plan`、プラン `release-prepare`）を `release-mvv` の前に足す |
| `contracts/templates/release-prepare.json` | 新設 | 組み立ての worker のプラン | — | — | `release.json` と同じ形。worker の作業 `release-prepare` を 1 つ流す |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | — | `$defs.mvv_gate` に任意のキー `scope`（`pr`・`base`・`pulls`） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの前の判定 | `release.record`・`release.assemble`・`branch_strategy` | モジュール | `release_target`（新設）・`review_prs`（`release` の分を変更）・`_judged`（変更） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力 | — | モジュール | `gate_outcome`（引数 `scope` を足す） |
| `src/ndf/progress/gate/checks.py` | 変更 | 省かない理由の検査と記録 | — | モジュール | `note`（`scope` の行を足す） |
| `src/ndf/domains/release/record_decide.py` | 変更 | リリース記録の純粋な判定 | `included_decide` | モジュール | `assembled_message`・`fork_point`（新設。`_approved_message` を消す）・`approved_included`・`approved_units`（失敗の扱いを変える） |
| `src/ndf/domains/release/record.py` | 変更 | リリース記録 | `record_decide`・`included_decide` | モジュール | `pull_commits`（新設。`_release_commits` を置き換える）・`assembled_included`（新設）・`_included`（`assembled_included` を呼ぶ） |
| `src/ndf/domains/release/assemble.py` | 変更 | 組み立て | — | モジュール | `open_release_numbers`・`assembles`（新設）・`open_releases`・`assemble`（中身を委ねる） |
| `src/ndf/progress/model.py` | 変更 | ステートマシンの型と規則 | — | モジュール | `MVV_SKIP_GATES`（値）・`mvv_marked`（新設）・`mvv_skip`（変更）・`mvv_answer_void`（新設） |
| `src/ndf/progress/run.py` | 変更 | 駆動 | `model` | `StepProgress` | `_answered`（変更） |
| `src/ndf/progress/gate/records.py` | 変更 | 回答の読み口 | `model` | モジュール | `answers`（変更） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認ゲートの質問 | — | モジュール | `question`（変更） |

## 組み立ての時点（`release-prepare`）

満たす決定: 決定 1、受け入れ条件 1

- `deploy.json` の `do` を `release-prepare` → `release-mvv` → `release-gate` → `release` にする。`release-prepare` の `metadata.ndf` は `kind: work`・`range: sprint_diff`（`release` と同じ）、出力のスキーマは `stage-io.schema.json#/$defs/report`
- `release-prepare.json` は `release.json` と同じ引数（`repo`・`worker`）で、`steps` は `{"id": "work", "run": ["{{worker}}", "release-prepare"]}`。出力の `outputs` は空でよい（判定は GitHub から読む。決定 2）
- `skills/release/SKILL.md` の「公開する」の手順 1（組み立て）と CHANGELOG の本文を書く手順を、`release-prepare` の worker が打つ手順として分ける。`release` の worker は手順 2〜4（提示・回答・マージ・公開）だけを打つ。組み立てない戦略（`NoReleasePr`）では `release-prepare` は何もせずに終える。版を持たない `merge_base_branch` でベースから本番への PR を開く手順が要るなら、それも `release-prepare` に置く（今の手順書の扱いを移すだけで、新しい作り方を足さない）

### 異常系の処理

| 失敗 | 振る舞い |
| --- | --- |
| 組み立てが止まる（`ReleaseOpen`・`AssembleError`） | `release-prepare` が `stopped`。工程は判定へ進まない（今の `release` の止まり方と同じ） |
| 組み立てが済まないまま `release-prepare` が `ok` を返す | 判定が開いたリリースの PR を見つけられず、組み立てる戦略では範囲を確定できないとして省かない（下の `release_target`） |

## 判定する PR と含む PR

満たす決定: 決定 2・3・4、受け入れ条件 1・2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `open_release_numbers` | `release/assemble.py` | 新設 | `open_release_numbers(hub: GitHub, production: str, base: str, formats: names_decide.NameFormats) -> list[int]` | 今の `open_releases` の本体（本番宛ての開いた PR を 1 ページ読み、head がリリースブランチの書式に合うか `base`（`base != production` のとき）の番号を小さい順に返す。読み切れなければ `AssembleError`）。`open_releases(hub, target, formats)` は `target.production`・`target.base` を渡してこれを呼ぶ |
| `assembles` | `release/assemble.py` | 新設 | `assembles(strategy: Strategy) -> bool` | ブランチ戦略が版上げの PR を組み立てるか（範囲の取り方が `pr` でなく、版を持つ）。`assemble` の `NoReleasePr` の条件をこの関数で決める（判定と組み立てで条件を 1 つにする） |
| `release_target` | `records/mvv_gate.py` | 新設 | `release_target(hub: GitHub, strategy: Strategy, inputs: Mapping[str, Any]) -> int` | リリースで判定する PR の番号。`open_release_numbers` が 1 つならそれ。0 で、ブランチ戦略が組み立てない（`assemble.assembles(strategy)` が偽）なら文脈の `pr`。それ以外（0 で組み立てる・2 つ以上・文脈の `pr` が無い）は `GateError`（「判定するリリースの PR を 1 つに決められない: <理由>」） |
| `pull_commits` | `release/record.py` | 新設（`_release_commits` を置き換える） | `pull_commits(hub: GitHub, pull: Pull, base_sha: Optional[str]) -> list[dict]` | PR のコミット。マージ済み（`pull.merge_sha` あり）なら今の `_release_commits` と同じ（マージのコミットの第 1 親から head まで）。開いていれば `hub.compare_commits(base_sha, pull.head_sha)`。開いていて `base_sha` が None・マージのコミットが読めないなら `RecordError` |
| `assembled_message` | `release/record_decide.py` | 新設（`_approved_message` を消す） | `assembled_message(commits: Sequence[Mapping[str, Any]], head: str) -> Optional[str]` | `head` から first-parent で辿った PR のコミット（`_first_parent_messages`）のうち、件名が `included_decide` の `RELEASE_TITLE` の形で「含む PR」の行（`included_from_message` が None でない）を持つもの（組み立てのコミット）が 1 つならそのメッセージ。2 つ以上なら `ValueError`。0 のとき、行を持つコミットが 1 つも無ければ None、件名の違うコミットだけが行を持てば `ValueError`（どのコミットの件名と行かを文に入れる）。件名の違うコミットの行は読まない |
| `fork_point` | `release/record_decide.py` | 新設 | `fork_point(commits: Sequence[Mapping[str, Any]], head: str) -> Optional[str]` | `head` から first-parent で PR のコミットを辿り、PR の外へ出た最初の親の SHA（PR がベースから分かれたコミット）。コミットが無ければ None。純粋な関数 |
| `approved_included`・`approved_units` | `release/record_decide.py` | 変更（失敗の扱い） | シグネチャは同じ | `assembled_message` を通す。`ValueError` は呼び出し元へ返す |
| `assembled_included` | `release/record.py` | 新設 | `assembled_included(commits: Sequence[dict], pull: Pull, production: str, formats: NameFormats) -> list[int]` | 今の `_included` の本体（行があれば行の番号、無ければ first-parent の件名から `included_decide.numbers`）。`ValueError` は `RecordError`（「組み立てのコミットの『含む PR』を 1 つに決められない: <原因>」）にする |
| `_included` | `release/record.py` | 変更 | シグネチャは同じ | `assembled_included` を呼ぶ（リリース記録と判定で規則を 1 つにする） |
| `review_prs` | `records/mvv_gate.py` | 変更（#3959 の新設を変える） | `review_prs(gate: str, number: int, inputs: Mapping[str, Any], hub: GitHub, pull: Pull, strategy: Strategy, base_sha: str) -> tuple[tuple[int, ...], Optional[str]]` | 保留を読む PR と、範囲を確定できない理由（確定できれば None）。`design` は `((number,), None)`。`release` は `pull_commits(hub, pull, base_sha)` → `assembled_included(…, strategy の本番ブランチ, formats)` の番号に、`number` と文脈の `pr`（整数なら）を足した和（小さい順・重複なし）。理由は、組み立てない戦略（`assemble.assembles(strategy)` が偽）なら「承認資料の集合の記録が無い」、開いた PR で `fork_point(commits, pull.head_sha)` が `base_sha` でなければ「リリースの PR がベースより遅れている」 |
| `_judged` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `gate_outcome` | `records/mvv_material_decide.py` | 変更 | 引数 `scope: Optional[Mapping[str, Any]] = None` を足す | `scope` が None でなければ出力に `scope` を入れる |
| `note` | `progress/gate/checks.py` | 変更 | シグネチャは同じ（`outcome` から読む） | `scope` があれば「保留を読んだ PR: #a #b …」の 1 行を足す（承認資料と PR のコメントが同じ値を読む） |

### 処理の手順（`_judged` の `release`）

#3959 の `_judged` の手順のうち、PR の番号を決める手順と保留を読む手順 (5) だけを変える。

1. `strategy = branch_strategy.load_at_ref(root, base_sha)`（#3959 が MVV を読んだベースのコミット `Snapshot.commit`。MVV とブランチ戦略を同じコミットで読む）
2. `number = release_target(hub, strategy, inputs)`（`design` は今どおり `pr_number`）
3. `pull = hub.pull(number)`（#3959 が判定する head を先に読む手順と同じ 1 回）
4. 差分・機械の検査・LLM の判定は #3959 のまま、`number` の PR に当てる。判定の記録のコメントも `number` の PR へ書く
5. `prs, unsettled = review_prs(gate, number, inputs, hub, pull, strategy, base_sha)`。読めた行で `review_holds(rows, broken, repo, prs, mvv)`。`unsettled` が None なら `scope = {"pr": number, "base": base_sha, "pulls": list(prs)}`、あれば `scope` は None で、`unsettled` を `why` に足す（`skip` 偽）
6. `gate_outcome(gate, verdict, skip, why, pull.head_sha, holds=…, scope=scope)`。#3959 の `release` の暫定の理由（「#4105 まで省かない」）は消す

### 異常系の処理

| 失敗 | 振る舞い |
| --- | --- |
| `release_target` の `GateError`（開いたリリースの PR が 0 で組み立てる戦略・2 つ以上・文脈の `pr` が無い） | 今の `gate` の例外の扱い（`verdict: null`・`skip` 偽・`why` に理由）。PR を読む前なのでコメントは書かない。`scope` なし |
| `open_release_numbers` の `AssembleError`（1 ページで読み切れない）・`GitHubError` | 同上（`gate` が受ける例外に `AssembleError` を足す） |
| `pull_commits` の `GitHubError`・`RecordError`、`assembled_included` の `RecordError`（組み立てのコミットが 2 つ以上・件名の違うコミットだけが行を持つ） | 判定の後の `may_skip` の前に、`why` に理由を入れて `skip` 偽。判定の記録は PR のコメントへ書く（PR は読めているので）。`scope` なし |
| `load_at_ref` の `StrategyError` | `verdict: null`・`skip` 偽。`scope` なし |
| 記録（`mvv-validation.jsonl`）を読めない | #3959 のまま（保留を「読めない」の 1 件にする）。範囲は確定しているので `scope` は書く（保留があるので省かれない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `assembled_message` | 組み立ての件名と行を持つコミット 1 つ → そのメッセージ | 行の無い直しのコミット・`Ndf-Included-Pulls: none` を持つ直しのコミットが上に積まれている → どちらも組み立てのコミットのメッセージ。行を持つコミットが無い → None | 組み立ての件名で行を持つコミットが 2 → `ValueError`。件名が `Release: …` でないコミットだけが行を持つ → `ValueError` |
| `fork_point` | PR のコミットの最も古い first-parent の親 | コミットが無い → None | — |
| `assembled_included`・`_included` | 行の番号（PR のコミットの外の番号を含む） | 行が無い → first-parent の件名から | `ValueError` → `RecordError`。リリース記録の `record` も同じ入力で止まる |
| `pull_commits` | 開いた PR → `compare_commits` に `base_sha` と head の SHA（ブランチ名を渡さない） | マージ済みの PR → マージのコミットの第 1 親から head | 開いていて `base_sha` が None → `RecordError` |
| `release_target` | 開いたリリースの PR が 1 → その番号 | 0 で `production_pr` → 文脈の `pr` | 0 で `release_pr`・2 つ → `GateError` |
| `review_prs`（`release`） | 組み立てる戦略で分岐点が `base_sha` → 理由 None | 組み立てない戦略 → 番号は件名から・理由「承認資料の集合の記録が無い」 | 分岐点が `base_sha` でない → 理由「ベースより遅れている」 |
| `_judged`（`release`） | `trunk`・`release_pr` で、行に版上げの PR の差分に無い単位の PR、その PR に `redline` → `fast` で「従う」でも `skip` 偽・`holds` にその PR・`scope.pulls` にその番号 | `release_branch` の 2 スプリントの行 → `scope.pulls` が `collect` の `facts.pulls` と同じ。保留が無い → `skip` 真・`scope` あり | 組み立てのコミットが 2・組み立てない戦略・ベースより遅れ → `skip` 偽・`scope` なし・`why` とコメントに理由（組み立てない戦略でも `holds` は載る）。`assemble._facts`・`_span`・`_since_by_unit` を呼ばない（スタブが呼ばれない） |

## 省略の条件と回答の読み口

満たす決定: 決定 5・6、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `MVV_SKIP_GATES` | `progress/model.py` | 変更（値） | `("design", "release")` | MVV 判定で省いてよい承認ゲートの区分 |
| `mvv_marked` | `progress/model.py` | 新設 | `mvv_marked(held: Mapping[str, Any]) -> bool` | 判定の出力が、区分に要る印を持つか。`release` は `scope` が辞書で、`pr` が整数・`base` が 40 桁の 16 進・`pulls` が整数の配列（空でない）のとき真。`design` は常に真 |
| `mvv_skip` | `progress/model.py` | 変更 | シグネチャは同じ | 今の条件（#3959 の `gate in MVV_SKIP_GATES` を含む）に `mvv_marked(held)` を足す |
| `mvv_answer_void` | `progress/model.py` | 新設 | `mvv_answer_void(payload: Mapping[str, Any]) -> Optional[str]` | 回答した者が `MVV_SERVICE` の回答で、`payload[MVV_KEY]` が辞書でないか `mvv_marked` が偽なら理由（「MVV 判定の回答が保留を読んだ範囲を持たない（旧版の判定）」）。ほかは None。#4106 はこの関数に判定の対象との照合を足す |
| `StepProgress._answered` | `progress/run.py` | 変更 | シグネチャは同じ | 変更要求でない回答のうち `mvv_answer_void(p)` が理由を返すものは、`answer` に残さない（承認ゲートは人を待つ）。事象は消さない |
| `answers` | `progress/gate/records.py` | 変更 | シグネチャは同じ | `mvv_answer_void` が理由を返す回答を一覧から外す（`approved_head` がその回答の head を返さない） |
| `question` | `progress/gate/asking.py` | 変更 | シグネチャは同じ | リリース承認ゲートでは、待ちの文脈（`_waiting_context`。直前の判定の出力が重なる）の `mvv_gate.scope.pr` が整数ならそれを質問の PR にし、無ければ今どおり `pr` |

### 異常系の処理

| 失敗 | 振る舞い |
| --- | --- |
| 印の無い `release` の判定の `skip: true`（旧版の出力） | `mvv_skip` が None。`_consume` は `service:mvv` の回答を書かず、承認ゲートは人を待つ |
| 印の無い判定を添えた未消費の `service:mvv` の回答（旧版が書いた事象） | `_answered` が読まず、`decide` は `GATE`。`ndf answer head` は None か人の回答の head |
| `scope` の形が違う（`pulls` が空・`base` が短い） | 印が無いのと同じ |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mvv_skip` | `release`・`skip: true`・`scope` あり → 省く | `design` は `scope` が無くても今どおり省く | `release`・`skip: true`・`scope` なし（#3959 より前の版の形）→ None |
| `mvv_answer_void` | 印のある回答 → None | 人の回答 → None | 印の無い `service:mvv` の `release` の回答 → 理由 |
| `decide`（受け入れ条件 4） | 印のある判定から省いた回答 → `CONSUME` | #3959 の版で人の承認待ち（`waiting`）を挟んだ後に、印の無い判定の出力の上で更新 → 人を待つ | #3959 より前の版が書いた未消費の `service:mvv` の回答から始める → `GATE`、`approved_head` が None |
| `question` | リリース承認ゲートの質問の PR が `mvv_gate.scope.pr` | `scope` が無い → 文脈の `pr` | 判定の後にリリースの PR の head が進む → `recheck` が待ちへ戻す |

## 手順書

| 手順書 | 変えること |
| --- | --- |
| `skills/release/SKILL.md` | 「公開する」の組み立てと CHANGELOG の本文を `release-prepare` の worker の手順にし、`release` の worker の手順から外す |
| `skills/development-workflow/references/pace.md` | 「具体例」の 4 と「MVV 判定」の表の順 4 を、判定する PR が本番宛ての開いたリリースの PR（組み立てない戦略は文脈の `pr`）であること・保留をリリースの PR・文脈の `pr`・含む PR から読むことに書き換える。#3959 が書いた「リリースの保留は #4105 まで読まない」を消す |
| `skills/development-workflow/references/approval-request.md` | リリース承認ゲートの「MVV 判定」の欄に、`scope.pulls`（保留を読んだ PR）を写す。#4105 の断りを消す |
| `skills/development-workflow/references/agent-layers.md` | #3959 が足した「手動の経路でリリース承認ゲートを返す前に `--gate release --machine` で打つ」の前に、組み立てを済ませることを足す |
| `skills/development-workflow/references/production-merge.md` | 「選んで集めるリリース方式のリリース」の順（組み立て → 承認資料 → 承認）が、どの戦略のデプロイの工程でも同じになったことを 1 行で足す |
