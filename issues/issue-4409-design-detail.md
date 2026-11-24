# #4409 の詳細設計: 自動反映の本番チャネルへのマージを、リリース承認ゲートの回答まで止める

設計文書 [issue-4409-design.md](./issue-4409-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。決定の中身と採らなかった案は、設計文書の「決定の記録」にある。受け入れ条件は、要求仕様 [issue-4409-requirements.md](./issue-4409-requirements.md) にある。

別ファイルにした理由: 新設する関数が 2 つ以上ある（判定の部品の `judge`、`ndf ci ask`・`ndf ci answer` など）。既存の関数のシグネチャも変える（`GitHub.merge`・`answering.answer` など）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── causes/approval_stale.json *
│   ├── hooks/imports.json
│   ├── templates/{params,merge,plan-to-spec,close,fix,check,design-merge,release}.json
│   ├── templates/parts/steps.json
│   └── workflow/{context.schema.json, modes/{standard,light,operation,documentation,legacy-refactor}.json}
├── src/ndf/
│   ├── core/{production_merge.py, merge_approval.py, approval_words.py, actors.py} *
│   ├── checks/children.json
│   ├── core/plan/{cli.py, runner.py}
│   ├── ci_cli.py
│   ├── adapters/github.py
│   ├── domains/release/{trail.py, approval_decide.py, cli.py}
│   ├── domains/records/mvv_gate.py
│   ├── domains/hooks/strict.py
│   └── progress/{invoke.py, run.py, gate/{asking.py, answering.py, entry.py}}
└── tests/ （設計文書の「設計の結果」のとおり）
```

依存の向き: 判定の部品 `core/production_merge.py` は標準ライブラリだけを読み、ほかのどのモジュールも読まない。hook（`domains/hooks/strict.py`）と `ci_cli.py` と `progress/gate/asking.py` がこの部品を読む。承認の対象とマージの承認の証跡の読み書き（`core/merge_approval.py`）は `adapters/github.py`・`adapters/publish.py` と回答の語（`core/approval_words.py`）を読み、工程（`progress/`）もドメイン（`domains/`）も読まない。工程の側（`progress/gate/answering.py`・`progress/gate/asking.py`）とドメインの側（`domains/records/mvv_gate.py`・`domains/release/trail.py`）の両方が読むので、層の順（`contracts` < `adapters` < `core` < `progress` < `domains`。`checks/rules.json` の `layers`）で両方より下の `ndf.core` に置く。回答の語は判断のモジュール（`domains/release/approval_decide.py`）も読むので、外部との接続を読まないモジュールに分ける。`ci_cli.py` は証跡と GitHub のアダプタを読み、工程を読まない（決定 7 の Value 9）。`adapters/github.py` は判定の部品も証跡も読まず、判定のやり直しは呼び手が渡す関数で行う（「`GitHub.merge`」の節）。輪は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/production_merge.py` | 新設 | `branch_strategy` とマージ先から、本番へ自動で届くかの判定の値を返す | 標準ライブラリ | モジュール | `Verdict`・`judge`・`automatic_rows`・`needs_approval`・`reaches_production`（新設） |
| `src/ndf/ci_cli.py` | 変更 | `ndf ci merge` に判定と証跡の確かめを足し、`ndf ci ask`・`ndf ci answer` を足す | `adapters.github`・`core.merge_approval`・`core.approval_words`・`core.production_merge`・`contracts.result` | モジュール | `_parser`・`_merge`（変更）、`_pr_target`・`_judge_pr`・`_unapproved`・`_ask_approval`・`_answer`・`_presented`（新設） |
| `src/ndf/adapters/github.py` | 変更 | マージ先の設定の読み取り・token の利用者・直前の照合を足す | `githubkit` | `GitHub` | `merge`・`_merge_with`・`_enqueue`（変更）、`branch_file`・`viewer`・`_verify`（新設） |
| `src/ndf/core/merge_approval.py` | 新設 | 承認の対象とマージの承認の証跡を組み、読み、判定し、書く | `adapters.github`・`adapters.publish`・`core.approval_words` | モジュール | `MergeTarget`・`MergeRow`・`render_merge_row`・`merge_rows`・`merge_judgment`・`write_merge_row`（新設）。`By`・`USER_BY`・`MVV_BY`・`BY_CHOICES`・`BY_LINE`・`TrailError`（`domains/release/trail.py` から移す） |
| `src/ndf/core/approval_words.py` | 新設 | 回答の語と、承認資料が対象にしたコミットの行の見出し | 標準ライブラリ | モジュール | `APPROVED`・`REJECTED`・`ANSWERS`・`HEAD_LINE`（`domains/release/approval_decide.py` から移す） |
| `src/ndf/core/actors.py` | 新設 | 回答した者の形 | 標準ライブラリ | モジュール | `ACTORS`・`HUMAN`（`progress/gate/answering.py` の `ACTORS` を移し、`ndf answer --by` と `ndf plan next --by` が共有する） |
| `src/ndf/domains/release/trail.py`・`approval_decide.py`・`cli.py` | 変更 | 担い手の語・`TrailError`・回答の語を `ndf.core` から読む | `core.merge_approval`・`core.approval_words` | モジュール | 振る舞いは変えない |
| `src/ndf/checks/children.json` | 変更 | 新設したモジュールのオーナーの子と、読む子の先に要る子を足す | — | — | 設計文書の「子の表の変更」 |
| `src/ndf/progress/gate/asking.py` | 変更 | 質問の対象に承認の対象を足し、承認資料に本番へ反映され得る行を書く | `core.production_merge` | `Target` | `Target`・`differs`・`as_dict`・`from_dict`・`material`・`write_material`（変更）、`binding`・`presented_path`・`read_presented`（新設） |
| `src/ndf/progress/gate/answering.py` | 変更 | 回答の照合を承認の対象まで広げ、回答の前に証跡を書く | `core.merge_approval`・`core.approval_words`・`asking` | モジュール | `github_observe`・`answer`・`_refusal`・`_same`・`recheck`（変更）、`Post`・`github_post`・`no_post`・`_trail_by`・`_post_trail`（新設） |
| `src/ndf/progress/gate/entry.py` | 変更 | 質問の対象を承認資料の前に読み、回答に証跡の書き手を渡す | `answering`・`asking` | モジュール | `present`・`_cmd_give`（変更） |
| `src/ndf/core/plan/cli.py` | 変更 | `ndf plan next` に `--by` を足す | `runner` | モジュール | `_parser`・`_cmd_next`（変更） |
| `src/ndf/core/plan/runner.py` | 変更 | 承認待ちへの回答を回答の記録へ書き、経路に `--by human` を載せる | `contracts.result` | モジュール | `advance`・`_routes`（変更）、`answer_path`・`_record_answer`（新設） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | MVV 判定の通過で、判定の記録のコメントに証跡の行を入れる | `core.merge_approval`・`core.approval_words` | モジュール | `_judged`・`_recorded`（変更） |
| `src/ndf/domains/hooks/strict.py` | 変更 | 届け先の判定を部品から呼び、証跡のマーカーを含む Bash を止める | `core.production_merge` | モジュール | `_judge`・`_deliveries`（変更）、`_writes_merge_mark`（新設）。`_automatic_production` は削除 |
| `src/ndf/progress/invoke.py` | 変更 | 工程から組むプランへ `production_gate`・`approved_head`・`approved_base` を入れる | `asking`・`core.production_merge` | モジュール | `template_values`（変更）、`_fill_production_gate`・`_approved_target`（新設） |
| `src/ndf/progress/run.py` | 変更 | `approval_stale` の再試行の経路、MVV 通過の `seen`、工程の定義の写しの移行 | `stages`・`store` | モジュール | `_failed_report`・`_consume`・`view`・`advance`（変更）、`stale_routes`・`migrate_definition`・`_migrated_order`（新設） |
| `contracts/causes/approval_stale.json` | 新設 | 停止理由 `approval_stale` の定義 | — | — | — |
| `contracts/workflow/context.schema.json` | 変更 | 出力 `mvv_gate` に `target` を足す | — | — | — |
| `contracts/hooks/imports.json` | 変更 | hook の import の表に判定の部品を足す | — | — | — |
| `contracts/templates/parts/steps.json`・`params.json`・各テンプレート | 変更 | `merge` の引数、`approve-*`・`reject-*`、`release-merge`、差し込みの既定 | — | — | — |
| `contracts/workflow/modes/*.json` | 変更 | クリーンアップをデプロイの直後へ移す | — | — | — |

## 本番チャネルの判定の部品

満たす決定: 決定 2、決定 3、受け入れ条件 7・8・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Verdict` | `core/production_merge.py` | 新設 | `class Verdict(str, Enum)`: `NOT_PRODUCTION`・`AUTOMATIC`・`MANUAL`・`UNSET`・`UNKNOWN` | 設計文書の表「判定の部品が返す値」の 5 つの値 |
| `automatic_rows` | `core/production_merge.py` | 新設 | `(section: object) -> Verdict` | `branch_strategy` の `release` だけを見て、`AUTOMATIC`・`MANUAL`・`UNSET`・`UNKNOWN` を返す（マージ先を見ない。hook が使う） |
| `judge` | `core/production_merge.py` | 新設 | `(section: object, base: str) -> Verdict` | マージ先が `branches.production` と違えば `NOT_PRODUCTION`、同じなら `automatic_rows` の値。`branches.production` を読めなければ `UNKNOWN` |
| `needs_approval` | `core/production_merge.py` | 新設 | `(v: Verdict) -> bool` | `ndf ci merge` の扱い。`AUTOMATIC`・`UNSET`・`UNKNOWN` で真 |
| `reaches_production` | `core/production_merge.py` | 新設 | `(v: Verdict) -> bool` | 承認資料の行と証跡の「本番へ反映されるか」の値。`needs_approval` と同じ集合を返す（決定 7。同じ条件を 2 か所に書かないため、`needs_approval` を呼ぶ） |

- `automatic_rows`
  - 事前条件: `section` は JSON を読んだ値そのもの（`dict` 以外も渡ってよい）
  - 事後条件: 例外を投げない。形の違いはすべて `UNKNOWN` へ寄せる

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `automatic_rows` | `section` か `section["release"]` が `dict` でない | `UNKNOWN` |
| `automatic_rows` | `release.deliveries` が配列で、`channel: production` かつ `kind` が `manual` でない行がある | `AUTOMATIC` |
| `automatic_rows` | `release.deliveries` が配列で、当たる行が無い | `MANUAL` |
| `automatic_rows` | `release.deliveries` が配列でない | `UNKNOWN` |
| `automatic_rows` | `deliveries` のキーが無く、`release.cd` が `dict` | `AUTOMATIC` |
| `automatic_rows` | `deliveries` のキーが無く、`release.cd` が `null` | `UNSET` |
| `automatic_rows` | `deliveries` のキーが無く、`cd` のキーも無いか、`null`・`dict` のどちらでもない | `UNKNOWN` |
| `judge` | `section["branches"]["production"]` が空でない文字列でない | `UNKNOWN` |
| `judge` | `base` が `branches.production` と違う | `NOT_PRODUCTION` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `automatic_rows`・`judge` | `domains/hooks/strict.py` の `_deliveries`・`_automatic_production` | `grep -n "_automatic_production\|_deliveries" src/ndf/domains/hooks/strict.py` | 規則をここへ移し、hook はこの部品を呼ぶ（決定 2）。ファイルの読み取りは hook に残す |
| `Verdict`・`needs_approval`・`reaches_production` | `trail.By`・`approval_decide` の判定の値 | `grep -n "class .*StrEnum" src/ndf/domains/release/trail.py` | 使えない。担い手と回答の値で、届け先の判定の値を持たない。`StrEnum` の置き方だけをそろえる |
| `judge` | `core/branch_strategy.py` の `of_project`・`get` | `grep -n "^def " src/ndf/core/branch_strategy.py` | 使えない。`jsonschema` を読み、hook の標準ライブラリだけの制約に合わない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `automatic_rows` | `kind: auto` の行で `AUTOMATIC`、`kind: manual` だけで `MANUAL` | `deliveries: []` で `MANUAL`、`cd: null` で `UNSET`、`cd` のキー無しで `UNKNOWN` | `release` が配列・`deliveries` が文字列で `UNKNOWN` |
| `judge` | `develop` 宛てで `NOT_PRODUCTION`、`main` 宛てで行の値 | `branches.production` が空文字で `UNKNOWN` | `section` が `None` で `UNKNOWN` |
| `needs_approval` | 5 つの値の真偽が設計文書の表と一致する | — | — |

## `ndf ci merge`

満たす決定: 決定 1、決定 3、決定 4、決定 5、決定 7、受け入れ条件 1・2・3・4・5・7・8・9・12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_parser` | `ci_cli.py` | 変更 | `() -> argparse.ArgumentParser` | `merge` に `--pr`（`--branch` と排他で必須）・`--production-gate {stop,defer,stale}`（既定 `stop`）・`--approved-head`・`--approved-base`・`--presented-out` を足し、`ask`・`answer` を足す |
| `_merge` | `ci_cli.py` | 変更 | `(args: argparse.Namespace) -> dict` | 設計文書の「`ndf ci merge` がマージするかを決める順序」の図のとおりに、PR を読み、照合・判定・証跡の確かめを通してからマージする |
| `_worktree_head`・`_held_mismatch`・`_approved_by`・`_merged` | `ci_cli.py` | 新設 | `_worktree_head(args) -> (head, 結果)`・`_held_mismatch(args, pull, target) -> Optional[dict]`・`_approved_by(args, hub, pull, target, verdict) -> (担い手, 結果)`・`_merged(args, target, outcome, verdict, approved_by) -> dict` | `_merge` の分岐を順序の段ごとに分けた部品（分岐の多い関数の検査 PLR0912）。順に、作業場所の HEAD と `--reviewed` の照合、承認の時点と `--pr` のレビューを経た HEAD の照合、承認が要るときの証跡の担い手（無ければキューから外して `--production-gate` の結果）、`GitHub.merge` の結果からの結果 JSON。振る舞いは `_merge` と同じ |
| `_pr_target` | `ci_cli.py` | 新設 | `(hub: GitHub, pull: Pull) -> merge_approval.MergeTarget` | 読んだ PR から承認の対象（ベースリポジトリ・番号・マージ先・HEAD）を作る |
| `_judge_pr` | `ci_cli.py` | 新設 | `(hub: GitHub, pull: Pull) -> tuple[Verdict, Optional[str]]` | PR のベースリポジトリのマージ先から設定を読み（`GitHub.branch_file`）、判定と設定の blob の oid を返す |
| `_unapproved` | `ci_cli.py` | 新設 | `(args: argparse.Namespace, target: merge_approval.MergeTarget, verdict: Verdict) -> dict` | 承認が要って有効な証跡が無いときの結果を、`--production-gate` の値で作る（設計文書の `--production-gate` の表） |
| `stale_routes` | `progress/run.py` | 新設 | `(machine_id: str, stage: str) -> dict[str, Any]` | `approval_stale` で止まった工程の経路 `ndf progress retry <ID> <工程> --reason approval_stale` と `ndf progress next <ID>` を、この順で返す |
| `_failed_report` | `progress/run.py` | 変更 | `(v: View, m: Move) -> dict[str, Any]` | 原因の種類が `approval_stale` なら `failed_routes` の代わりに `stale_routes` を載せる |

- `_merge`
  - 事前条件: `--approved-head` と `--approved-base` は両方あるか両方無い。片方だけなら引数の誤り
  - 事後条件: マージかキューへの投入を送ったのは、判定が「要らない」か、`merge_approval.merge_judgment` が承認を返したときだけである
  - 副作用: GitHub の REST・GraphQL（PR の読み取り、設定の読み取り、コメントの読み取り、マージ、キューへの投入）。`stop` のときだけ `--presented-out` のファイルを書く
- `_unapproved`
  - 事後条件: `stop` は `gate`（終了コード 10）で、`metrics.gate` が `production-merge`、`metrics.presented_id` が新しく作った識別子。`defer` は `ok` で `metrics.merge` が `deferred`。`stale` は `stopped`（`approval_stale`）で `routes` を持たない
  - 副作用: `stop` のときだけ、`_presented` で提示のファイルを書く（「`ndf ci ask`」の節）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_merge` | 開いた PR が無い | `--branch` の PR が閉じている | `hub.pulls_by_head` | 止める | `stopped`（`precondition`）、終了コード 1 | 再実行できる |
| `_merge` | 承認の時点と違う | `--approved-head`・`--approved-base` が今の HEAD・マージ先と違う。`--approved-base` が空 | 照合（判定の前） | 止める | `stopped`（`approval_stale`） | 工程の実行側が `stale_routes` を載せる |
| `_merge` | 設定を読めない | ファイルが無い・JSON でない・GraphQL の応答のリポジトリが違う | `_judge_pr` | 既定値で続ける（`UNKNOWN` として承認を求める） | 承認が要るときの出力 | 再実行できる |
| `_merge` | GitHub に届かない | 通信・上限 | `GitHubError` | 呼び出し元へ返す（`main` の既存の変換） | `stopped`（`infra_wait` か `unknown`） | 再実行できる |
| `_merge` | 判定の後にマージ先か HEAD が変わった | 直前の照合が違いを見つけた（`github_merge.TARGET_MOVED`） | `GitHub.merge` の照合 | 止める | 承認の対象を持つ呼び出し（`--approved-head` か `--production-gate stale`）は `stopped`（`approval_stale`）、ほかは `stopped`（`refused`） | `approval_stale` は工程が承認を取り直す。`refused` は再実行で判定からやり直す |
| `_unapproved` | 提示のファイルを書けない | 保存先の権限・容量 | `OSError` | 止める | `stopped`（`precondition`） | 再実行できる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_merge` | `--pr` で、PR がマージ済み | 何もせず `ok`、`metrics.merge: already`（方式の問い合わせ・設定の読み取り・照合のどれもしない） |
| `_merge` | 承認の時点の値が渡され、今と違う | `approval_stale` |
| `_merge` | 判定が `NOT_PRODUCTION`・`MANUAL` | 証跡を読まずにマージする |
| `_merge` | 判定が `AUTOMATIC`・`UNSET`・`UNKNOWN`、`merge_judgment` が承認 | マージする。`metrics.approved_by` に担い手 |
| `_merge` | 同じ判定で、証跡が無い・却下・古い・「本番へ反映される: いいえ」だけ | `_unapproved` |
| `_merge` | 添える HEAD | `--approved-head` があればそれ。無ければ `--branch` は作業場所の HEAD、`--pr` は証跡の HEAD か PR を読んだ HEAD |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `PRODUCTION_GATES` | 定数 | `ci_cli.py` | `core/production_merge.py` の `GATES`（`(STOP, DEFER, STALE)` = `("stop", "defer", "stale")`）。工程の `PRODUCTION_GATE` の値と同じ語を 1 か所から読む |
| `GATE_NAME` | 定数 | `ci_cli.py` | `"production-merge"`（`metrics.gate` の値。`ndf ci ask` と共有） |
| `approval_stale` | 停止理由 | `contracts/causes/approval_stale.json` | 説明: 承認の後に承認の対象が変わった、または有効な承認が無い。承認ゲートを持つ工程を流し直す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_merge` | `metrics.merge`（`merged`・`deferred`・`already`）・`metrics.gate`・`metrics.approved_by`・`metrics.verdict`（判定の値）・`items` の PR | 結果 JSON | token・設定の本文 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_judge_pr` | `core/branch_strategy.py` の `load_at_ref` | `grep -n "def load_at_ref" src/ndf/core/branch_strategy.py` | 使えない。Git のリモートから読み、決定 3 で採らなかった |
| `stale_routes` | `progress/run.py` の `failed_routes`・`_cmd` | `grep -n "def failed_routes\|def _cmd" src/ndf/progress/run.py` | `_cmd` を使う。`failed_routes` は回答の語彙の 3 つを返すので別に持つ |
| `_pr_target` | `merge_approval.MergeTarget` | 同じ設計の新設 | 使う |
| `_unapproved` | `contracts.result` の `result`・`stopped`・`gate` | `grep -n "^def " src/ndf/contracts/result.py` | 使う。結果の形はこの 3 つで作る |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_merge` | 本番チャネルでないマージ先は `--production-gate` によらずマージする（受け入れ条件 7）。有効な証跡でマージする | `--pr` のマージ済みで要求が PR の読み取り 1 回だけ（決定 5）。コメント 99・100・199 件の要求の数（決定 3 の表） | 承認の時点と違うと、判定が `MANUAL` でも `approval_stale`。作業場所の設定を書き換えても判定は変わらない（決定 3） |
| `_unapproved` | 3 つの値の結果の形 | `stop` の `presented_id` がファイルと結果で同じ | 提示のファイルを書けないと `precondition` |
| `_failed_report` | `approval_stale` で `routes` が `retry` と `next` の 2 つ | — | ほかの種類は今と同じ `failed_routes` |

## 承認の対象とマージの承認の証跡

満たす決定: 決定 7、受け入れ条件 5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `MergeTarget` | `core/merge_approval.py` | 新設 | `class MergeTarget(NamedTuple)`: `repo: str`・`number: int`・`base: str`・`head: str` | 承認を結び付ける 4 つの値の組。`repo` は小文字にそろえて比べる |
| `MergeRow` | `core/merge_approval.py` | 新設 | `class MergeRow(NamedTuple)`: `answer: str`・`by: str`・`target: MergeTarget`・`production: bool`・`record_id: str` | 証跡の 1 行の中身 |
| `MERGE_MARK` | `core/merge_approval.py` | 新設 | 定数 `"<!-- ndf:merge-approval -->"`（名前空間は `identity.FIXED_WORDS['workflow_namespace']`） | マージの承認の証跡のマーカー。`ndf:release-approval` を含まない |
| `render_merge_row` | `core/merge_approval.py` | 新設 | `(row: MergeRow) -> str` | マーカーと行（回答・担い手・ベースリポジトリ・PR・マージ先・HEAD・本番へ反映されるか・回答の記録の識別子）の Markdown を作る |
| `merge_rows` | `core/merge_approval.py` | 新設 | `(hub: GitHub, number: int, viewer: Viewer) -> list[MergeRow]` | PR のコメントから、数える証跡を古い順に返す（決定 7 の「証跡の判定」の規則 1・2） |
| `merge_judgment` | `core/merge_approval.py` | 新設 | `(rows: Sequence[MergeRow], now: MergeTarget) -> Optional[MergeRow]` | 規則 3・4 で残した最新の 1 件が承認ならその行、無いか却下なら `None` |
| `write_merge_row` | `core/merge_approval.py` | 新設 | `(hub: GitHub, row: MergeRow, viewer: Viewer) -> bool` | 同じ `record_id` の証跡が無ければ投稿して真、あれば投稿せず偽 |

- `merge_rows`
  - 事前条件: `viewer` は `GitHub.viewer()` の値。アカウント ID が 0（読めない）なら数えずに `TrailError`（`NO_LOGIN`）
  - 事後条件: 編集されたコメント、書き手のアカウント ID（`Authored.author_id`）が `viewer.account_id` と違うコメント（名前が同じでも別のアカウントなら含まない）、マーカーの無いコメント、行が読めないコメントは含まない。担い手 `mvv` の行は、判定の記録（`checks.note` の見出し）と同じコメントにあるものだけを含む
  - 副作用: GitHub のコメントの読み取り（`GitHub.comments`。floor(N / 100) + 1 回）
- `write_merge_row`
  - 副作用: コメントの読み取りと、無ければ投稿 1 回（`Publisher.comment`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `merge_rows` | 行が読めない | 手で書いた・形が古い | 行の解釈 | 既定値で続ける（数えない） | 無し | — |
| `write_merge_row` | 投稿できない | 通信・権限 | `GitHubError` | 呼び出し元へ返す | 呼び手の結果 | 同じ `record_id` で送り直せる（重ならない） |
| `write_merge_row` | `by` が `user`・`mvv` でない、`answer` が承認・却下でない | 呼び手の誤り | 引数の確かめ | 止める（`TrailError`） | 呼び手の結果 | 書かない |
| `merge_rows`・`write_merge_row` | `viewer` のアカウント ID が 0 | token の利用者を読めない（GraphQL の `viewer` が空） | 引数の確かめ | 止める（`TrailError`。理由は `NO_LOGIN`） | `ndf ci merge` は `precondition`（キューに入っていれば外す。`approval_stale` にしない）。`ndf ci answer` は `refused`。工程の回答は `model.Refused` で回答を記録しない | 書かない。login を読める token で打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `merge_judgment` | 対象が `now` と同じで `production` が真の行が無い | `None` |
| `merge_judgment` | 残した中の最新が承認 | その行 |
| `merge_judgment` | 残した中の最新が却下（承認の後の却下） | `None` |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 行の見出し（`回答:`・`担い手:`・`対象:`・`本番へ反映される:`・`回答の記録:`） | 定数 | `core/merge_approval.py` | 既存の `BY_LINE` と同じ置き方。`担い手:` は `BY_LINE`（`domains/release/trail.py` から移す）を使う |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `MergeTarget`・`MergeRow`・`MERGE_MARK` | `trail.Read`・`ADMIN_MARK`・`HUMAN_MARK` | `grep -n "_MARK = \|class Read" src/ndf/domains/release/trail.py` | 使えない。どれも HEAD だけを持つか別の記録の印である。マーカーの作り方（`identity.FIXED_WORDS` の名前空間）はそろえる |
| `merge_rows` | `trail._counts`・`GitHub.trusts` | `grep -n "def _counts\|def trusts" src/ndf/domains/release/trail.py src/ndf/adapters/github.py` | 使えない。`trusts` は書き込み権限で数え、決定 7 の規則 1（同じ login だけ）と違う。編集の扱い（`Authored.edited`）は同じものを使う |
| `merge_judgment` | `approval_decide.judge` | `grep -n "def judge" src/ndf/domains/release/approval_decide.py` | 使えない。HEAD だけで照らし、マージ先と「本番へ反映されるか」を持たない。最新の 1 件で決める規則は同じにする |
| `write_merge_row` | `trail.answer`・`Publisher.comment` | `grep -n "def answer\|def comment" src/ndf/domains/release/trail.py src/ndf/adapters/publish.py` | `Publisher.comment` を使う。`trail.answer` はリリースの PR の書き手の表（`trail_writer`）に縛られるので使わない |
| `render_merge_row` | `approval_decide.render_trail` | 同上 | 使えない。マーカーが `ndf:release-approval` で、CD の workflow が数える（決定 7） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `merge_rows` | 同じ login の行を数える | 編集されたコメントを数えない | 書き込み権限だけを持つ別の login、`ndf:release-approval` の行を数えない |
| `merge_judgment` | 対象が同じ承認で行を返す | 承認の後の却下で `None`、「いいえ」だけで `None` | HEAD・マージ先・リポジトリのどれかが違えば `None` |
| `render_merge_row` | 行が `merge_rows` で読み戻せる | — | 本文が `ndf:release-approval` を含まない |
| `write_merge_row` | 1 回目は投稿する | 同じ `record_id` の 2 回目は投稿しない | `by: mvv` 以外の不正な担い手で `TrailError` |

## 回答を証跡に書くコマンド `ndf ci answer` と PR の承認を問うコマンド `ndf ci ask`

満たす決定: 決定 7、受け入れ条件 4・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_ask` | `ci_cli.py` | 新設 | `(args: argparse.Namespace) -> dict` | `--branch` の PR を読み、判定し、承認資料と提示のファイルを書いて `gate`（終了コード 10）で止まる |
| `_answer` | `ci_cli.py` | 新設 | `(args: argparse.Namespace) -> dict` | 回答の記録（`--answer-record`）と提示のファイル（`--presented`）を照らし、今の PR と同じなら証跡を書く |
| `_presented` | `ci_cli.py` | 新設 | `(path: Path, target: merge_approval.MergeTarget, production: bool) -> str` | 提示の識別子（`uuid4` の 16 進）を作り、承認の対象と合わせてファイルへ書き、隣に承認資料（`production` が真なら「この承認で PR がマージされ、本番へ反映され得る」の行を含む）を書いて、識別子を返す。`_ask` と `_unapproved` の `stop` が使う |

- `_ask`
  - 副作用: PR・設定の読み取り、`--presented-out` のファイルと承認資料の書き込み
- `_answer`
  - 事前条件: 回答の記録はプランの実行（`runner._record_answer`）が書いた形である
  - 事後条件: 証跡を書いたのは、回答した者が `human` で、記録の提示の識別子が提示のファイルの識別子と同じで、提示のファイルの承認の対象が今の PR と同じときだけである。担い手はいつも `user`
  - 副作用: PR の読み取り、コメントの読み取り、投稿 1 回（`write_merge_row`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_answer` | 回答の記録が無い・読めない | 端末から直に打った | ファイルの読み取り | 止める | `stopped`（`refused`） | 書かない |
| `_answer` | 回答した者が `human` でない | `conductor`・`organizer`・`service:*`・`--by` 無し | 記録の `by` | 止める | `stopped`（`refused`） | 書かない |
| `_answer` | 提示の識別子が違う | 流し直しで提示が置き換わった | 記録と提示のファイルの比較 | 止める | `stopped`（`refused`） | プランを流し直して回答し直す |
| `_answer` | 承認の対象が今の PR と違う | 質問の後に HEAD・マージ先が変わった | `_pr_target` との比較 | 止める | `stopped`（`refused`） | 同上 |
| `_ask` | 提示のファイルを書けない | 保存先の権限 | `OSError` | 止める | `stopped`（`precondition`） | 再実行できる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 提示のファイル | JSON | `--presented-out`（`{state_dir}/presented-<ステップ>.json`） | `{"presented_id", "repo", "number", "base", "head", "production"}` |
| 回答の記録 | JSON | `{state_dir}/answer-records/<ステップ>.json` | `{"step", "answer", "by", "at", "run", "presented_id"}`（書くのは `runner._record_answer`） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_ask` | `progress/gate/entry.py` の `ask`（`ndf answer ask`） | `grep -n "^def ask" src/ndf/progress/gate/entry.py` | 使えない。PR を読まず、承認の対象を残さない |
| `_answer` | `domains/release/cli.py` の `answer`（`ndf release-steps answer`） | `grep -n "\"--answer\"" src/ndf/domains/release/cli.py` | 使えない。`--answer` と `--by` を受けて書く形で、決定 7 で採らなかった |
| `_presented` | `trail.present` | `grep -n "def present" src/ndf/domains/release/trail.py` | 使えない。PR へ資料を投稿する形で、提示の識別子を持たない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_ask` | `gate` と `metrics.presented_id`、ファイルの識別子が同じ | 判定が `UNSET`・`UNKNOWN` でも資料に本番の行がある | 書けないと `precondition` |
| `_answer` | 承認・却下のそれぞれで証跡を書く | 同じ記録で 2 回打っても投稿は 1 回 | 異常系の表の行ごとに `refused` で、投稿 0 回 |

## プランの実行

満たす決定: 決定 7、受け入れ条件 4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_parser` | `core/plan/cli.py` | 変更 | `() -> argparse.ArgumentParser` | `next` に `--by`（形は `answering.ACTORS`）を足す |
| `_cmd_next` | `core/plan/cli.py` | 変更 | `(a: argparse.Namespace) -> int` | `a.by` を `runner.advance` へ渡す |
| `advance` | `core/plan/runner.py` | 変更 | `(root: Path, inv: Invocation, answer: Optional[str], on_gate: Callable[[dict[str, Any]], None], url: Optional[str] = None, by: Optional[str] = None) -> dict[str, Any]` | 回答を送る前に、待っていたステップの回答の記録を書く。`by` が無ければ回答を受けない |
| `_routes` | `core/plan/runner.py` | 変更 | `(inv: Invocation) -> dict[str, dict[str, dict[str, Any]]]` | 回答の経路を `ndf plan next <プラン> --answer <回答> --by human` にする |
| `answer_path` | `core/plan/runner.py` | 新設 | `(state_dir: Path, step: str) -> Path` | 回答の記録のパス `{state_dir}/answer-records/<ステップ>.json`（保存先の名前は `runner.ANSWERS_DIR`。`answers` は結果 JSON の語 `result.ANSWERS` と同じ文字列になるので避ける） |
| `_record_answer` | `core/plan/runner.py` | 新設 | `(inv: Invocation, baseline: dict[str, Any], answer: str, by: str) -> None` | 待ちのマーカーのステップと、そのステップの結果の `metrics.presented_id` を写して回答の記録を書く |

- `_record_answer`
  - 事前条件: `baseline` は待ちのマーカー（`waiting_path`）を読んだ値
  - 事後条件: 一時ファイルから置き換えて書く。提示の識別子が結果に無ければ `presented_id` は `null`（`ndf ci answer` が `refused` にする）
  - 副作用: ファイルの書き込み

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `advance` | `--by` が無い | 古い経路を打った | `answer` があり `by` が無い | 止める | `stopped`（`invalid`）。返す経路に `--by human` を示す | 回答を送らない。打ち直せる |
| `_cmd_next` | `--by` の形が違う | 打ち間違い | `ACTORS` の照合 | 止める | `stopped`（`invalid`） | 同上 |
| `_record_answer` | 書けない | 保存先の権限 | `OSError` | 止める | `stopped`（`precondition`） | 回答を送らない。打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_record_answer` | `runner.stored_result`・`result_path` | `grep -n "def stored_result\|def result_path" src/ndf/core/plan/runner.py` | ステップの結果を読む口として使う |
| `answer_path` | `runner.waiting_path` | 同上 | 同じ置き方（保存先の下のパス）にそろえる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `advance` | `--by human` の回答で記録が書かれ、`on_answer` の先へ進む | 返された `routes` のコマンドをそのまま打って受け付けられる | `--by` 無しで `invalid`、回答を送らない |
| `_record_answer` | `presented_id` が結果の `metrics` から写る | 結果に `presented_id` が無いと `null` | 書けないと `precondition` |

## 工程の承認ゲートの質問と回答・承認資料

満たす決定: 決定 7、受け入れ条件 5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Target` | `progress/gate/asking.py` | 変更 | `@dataclass(frozen=True) class Target`: `head`・`material`・`repo`・`pr: Optional[int]`・`base`・`production: Optional[bool]` | 見た対象に承認の対象と「本番へ反映されるか」を足す |
| `Target.differs` | `progress/gate/asking.py` | 変更 | `(self, seen: Target) -> list[str]` | 今と同じく、回答した者が渡す HEAD と承認資料の sha256 だけを比べる |
| `Target.binding` | `progress/gate/asking.py` | 新設 | `(self) -> Optional[merge_approval.MergeTarget]` | 4 つの値がそろえば承認の対象を返し、欠ければ `None` |
| `Target.as_dict`・`from_dict` | `progress/gate/asking.py` | 変更 | 今と同じ | `repo`・`pr`・`base`・`production` を足す。旧版の形（無いキー）は `None` で読む |
| `presented_path` | `progress/gate/asking.py` | 新設 | `(root: Path, q: Question) -> Path` | 提示した対象のパス（承認資料の隣の `<質問の id>.target.json`） |
| `read_presented` | `progress/gate/asking.py` | 新設 | `(root: Path, q: Question) -> Target` | 提示した対象を読む。無ければ空の `Target` |
| `write_material` | `progress/gate/asking.py` | 変更 | `(root: Path, v: run.View, q: Question) -> Path` | 承認資料と同時に、`q.target` を提示した対象として書く（書いてあれば書き直さない） |
| `material` | `progress/gate/asking.py` | 変更 | `(v: run.View, q: Question) -> str` | マージ先を載せ、`q.target.production` が真なら「この承認で PR がマージされ、本番へ反映され得る」の行を書く |
| `github_observe` | `progress/gate/answering.py` | 変更 | 今と同じ | PR の HEAD に加えて、ベースリポジトリ・番号・マージ先と、判定の部品で決めた `production` を返す |
| `Post` | `progress/gate/answering.py` | 新設 | `Callable[[merge_approval.MergeRow], bool]` | 証跡を書く口 |
| `github_post` | `progress/gate/answering.py` | 新設 | `(cwd: Path, hub: Optional[Callable[[], GitHub]] = None) -> Post` | `merge_approval.write_merge_row` を token の利用者の login で呼ぶ口を作る |
| `no_post` | `progress/gate/answering.py` | 新設 | `(row: merge_approval.MergeRow) -> bool` | 書かない口（PR を持たないテスト用）。PR を持つ質問に渡されたら `model.Refused` |
| `answer` | `progress/gate/answering.py` | 変更 | `(st: store.Store, machine_id: str, a: Answer, observe: Observe = no_observe, post: Post = no_post) -> dict[str, Any]` | 設計文書の「回答の記録と証跡の書き込みの順」の 1〜4 で、証跡を書いてから回答を記録する |
| `_refusal` | `progress/gate/answering.py` | 変更 | `(root: Path, v: run.View, a: Answer, observe: Observe) -> tuple[Question, Target]` | 今の確かめに加え、提示した対象の `binding()` と今の `binding()` が違えば断る。照らした対象を返す |
| `_trail_by` | `progress/gate/answering.py` | 新設 | `(conn: Any, q: Question, a: Answer) -> Optional[str]` | 回答した者から担い手を決める。`human` と中継を確かめた `organizer` は `user`、`service:mvv` は `None`（書かない）。ほかは、承認でマージが本番へ反映され得る（`seen.production` が真）ときだけ断り、反映されないなら `None`（書かずに回答を記録する。既存の Factory の `service:factory` の回答を壊さない） |
| `_post_trail` | `progress/gate/answering.py` | 新設 | `(q: Question, a: Answer, seen: Target, by: str, post: Post) -> None` | 照らした対象と質問の id で `MergeRow` を作り、`post` で書く |
| `_same` | `progress/gate/answering.py` | 変更 | `(held: model.Event, a: Answer, machine_id: str, ensure: Callable[[], None]) -> dict[str, Any]` | 早期の戻りの前に `ensure`（証跡があるかを確かめ、無ければ書く）を呼ぶ |
| `recheck` | `progress/gate/answering.py` | 変更 | 今と同じ | 回答の `seen.production` が真なら、文書だけの変更でも HEAD を繰り上げずに待ちへ戻す |
| `present` | `progress/gate/entry.py` | 変更 | 今と同じ | 対象を承認資料の前に読み（`observe`）、対象を持つ質問で承認資料と提示した対象を書き、その後に承認資料の sha256 を足す |
| `_cmd_give` | `progress/gate/entry.py` | 変更 | 今と同じ | `answer` に `github_post(cwd)` を渡す |

- `answer`
  - 事前条件: `post` は PR を持つ承認ゲートでは `github_post` の口である
  - 事後条件: 回答の事象の `seen` には、回答した者の HEAD と承認資料の sha256 が入る。そこへ、提示した対象の `repo`・`pr`・`base`・`production` を足す（`invoke._approved_target` がここを読む）
  - 副作用: GitHub の PR の読み取り（`observe`）、コメントの読み取りと投稿（`post`）、状態の記録のトランザクション
- `_trail_by`
  - 副作用: `organizer` のときだけ、計画の状態機械のリクエスト（`release_request_of`）を読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `answer` | 照合・証跡・記録のどれかで断る | 下の行の原因 | `_refusal`・`_trail_by`・`_post_trail` | 止める（`model.Refused` か `GitHubError` を呼び出し元へ返す） | `stopped`（`refused` など） | 回答を記録しないので、同じ回答を送り直せる |
| `_refusal` | 承認の対象が違う | 質問の後にマージ先・HEAD が変わった | `binding()` の比較 | 止める（`model.Refused`） | `stopped`（`refused`）。待ちへ戻すので今の対象で回答し直す | 再実行できる |
| `_refusal` | 提示した対象が欠ける | 旧版で出した質問 | `read_presented` が空 | 止める | 同上 | 質問し直す |
| `_trail_by` | 回答した者が `conductor`・`service:<主体>`（`mvv` 以外）で、承認の対象が本番へ反映され得る | AI の判断の回答 | `a.by`・`seen.production` | 止める | `stopped`（`refused`） | 証跡も回答も書かない |
| `_trail_by` | `organizer` の中継を確かめられない | リクエストが無い・語が違う・外のオーガナイザーのファイルの中継 | リクエストの回答 | 止める | 同上 | 同上 |
| `_post_trail` | 投稿できない | 通信・権限 | `GitHubError` | 止める（回答を記録しない） | `stopped`（`unknown` か `infra_wait`） | 同じ回答を送り直すと続きから回復する |
| `present` | 対象を読めない | GitHub に届かない | `observe` | 止める（今と同じ `model.Refused`） | `stopped` | 再実行できる |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ回答の送り直し・2 つの端末からの同じ回答 | PR の証跡のコメント、状態の記録 | 証跡は提示ごとの識別子（`record_id`。質問の id と `presentation_key` の組）で重ならないように書く（`write_merge_row`）。状態の記録は今のトランザクションと冪等のキー | 証跡の後・記録の前で止まっても、送り直しで証跡は 1 件のまま記録される。記録の後に証跡が無ければ `_same` の `ensure` が書く |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_trail_by` | `asking.release_request_of`・`plan_request` | `grep -n "def release_request_of" src/ndf/progress/gate/asking.py` | 使う |
| `presented_path`・`read_presented` | `asking.accepted_path` | `grep -n "def accepted_path" src/ndf/progress/gate/asking.py` | 同じ置き方（承認資料の隣）にそろえる |
| `Target.binding`・`Post`・`no_post` | `answering.Observe`・`no_observe` | `grep -n "^Observe\|def no_observe" src/ndf/progress/gate/answering.py` | 同じ形（型の別名と何もしない既定の口）にそろえる。`binding` は `merge_approval.MergeTarget` を返す |
| `_post_trail` | `merge_approval.write_merge_row` | 同じ設計の新設 | 使う |
| `github_post` | `github_observe` の GitHub を 1 つだけ作る形 | `sed -n 53,72p src/ndf/progress/gate/answering.py` | 同じ形で作る |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `answer` | 承認・却下のどちらでも証跡を書いてから記録する | 送り直しで投稿は 1 回。記録の後に証跡が無いと書く | 投稿の失敗で記録しない |
| `_refusal` | 同じ対象で受ける | 旧版の `seen`（キーが無い）を `None` として読む | マージ先が変わると断る |
| `_trail_by` | `human` で `user` | `service:mvv` で書かない。本番へ反映されない承認の対象への `service:factory` は書かずに記録する | 本番へ反映され得る承認の対象への `conductor`・`service:foo`・中継の無い `organizer` を断る |
| `material` | `production` が真で本番の行がある | `production` が `None`（PR を持たない質問）で行が無い | — |
| `recheck` | 本番へ反映されない PR は今と同じく繰り上げる | — | 本番へ反映される PR は文書だけでも待ちへ戻す |

## MVV 判定の承認ゲート

満たす決定: 決定 7、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_judged` | `domains/records/mvv_gate.py` | 変更 | 今と同じ | 読んだ PR から承認の対象と判定（`production_merge.judge`）を作り、出力 `mvv_gate` に `target` として入れる |
| `_recorded` | `domains/records/mvv_gate.py` | 変更 | `(hub: GitHub, number: int, outcome: dict[str, Any]) -> dict[str, Any]` | 省くと決めた判定のときだけ、判定の記録の本文の後ろに `merge_approval.render_merge_row`（担い手 `mvv`）を足して 1 件で投稿する |
| `_consume` | `progress/run.py` | 変更 | 今と同じ | `service:mvv` の回答を書くとき、`mvv_gate.target` を回答の事象の `seen` へ写す |

- `_recorded`
  - 事後条件: レッドラインや省かない判定では、証跡の行とマーカーを入れない
  - 副作用: 投稿 1 回（今と同じ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_recorded` | 投稿できない | 通信・権限 | `GitHubError` | 既定値で続ける（今と同じく省かない側へ倒し、人へ問う） | 承認ゲートの質問 | 証跡は書かれない |
| `_judged` | 設定を読めない | ファイルが無い | `GitHub.branch_file` | 既定値で続ける（`UNKNOWN` で `production: true`） | 無し | — |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `mvv_gate.target` | 出力のキー | `contracts/workflow/context.schema.json` の `$defs.mvv_gate` | `{"repo", "pr", "base", "head", "production"}`。`additionalProperties: false` なのでスキーマに足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_recorded` | 省く判定で 1 件の投稿に証跡の行がある | — | 省かない判定・レッドラインで証跡の行が無い |
| `_consume` | `seen` に承認の対象が入る | `mvv_gate.target` が無い旧版の出力では `seen` は HEAD だけ（`release-merge` は `approval_stale`） | — |

## `GitHub.merge`

満たす決定: 決定 3、決定 8、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `branch_file` | `adapters/github.py` | 新設 | `(self, base: str, path: str) -> FileAt` | 1 回の GraphQL で、ベースリポジトリの名前・ブランチのファイルの本文と blob の oid を読む。無ければ本文と oid が `None`。同じ要求で token の利用者（`viewer { login databaseId }`）も読み、`viewer` のために持つ。応答の `viewer` が空でも空として持つ |
| `viewer` | `adapters/github.py` | 新設 | `(self) -> Viewer` | token の利用者（`Viewer(login, account_id)`。1 回だけ読み、インスタンスに持つ）。`branch_file` が同じ要求で読んでいれば、空だったときも要求を送らない。読んでいなければ GraphQL の `viewer { login databaseId }` を 1 回読む（REST の `/user` は GitHub App のインストールトークンと Actions の `GITHUB_TOKEN` で 403 になるため使わない）。読めなければアカウント ID が 0 |
| `merge` | `adapters/github.py` | 変更 | `(self, number: int, expect_head: Optional[str] = None, method: Optional[str] = None, require_method: bool = False, jump: bool = False, expect: Optional[MergeExpect] = None) -> MergeOutcome` | `expect` があれば、要求を送る直前に照らす |
| `_merge_with` | `adapters/github.py` | 変更 | `(self, number: int, expect_head: Optional[str], method: Optional[str], route: _MergeRoute, expect: Optional[MergeExpect] = None) -> MergeOutcome` | 最初の PR の読み直しをやめ、方式の問い合わせの後・送る直前に `_verify` を呼ぶ。`expect` が無ければ今と同じ |
| `_enqueue` | `adapters/github.py` | 変更 | `(self, pull: Pull, method: Optional[str], jump: bool = False, expect: Optional[MergeExpect] = None) -> Optional[MergeOutcome]` | キューの状態の問い合わせに `baseRefName`・HEAD・設定の oid と本文を足して照らし、投入の後に PR を読み直してマージ先が変わればキューから外す |
| `_verify` | `adapters/github.py` | 新設 | `(self, fresh: Verified, expect: MergeExpect) -> Optional[MergeOutcome]` | 照合の応答と期待を比べ、マージ先か HEAD が違えば `github_merge.TARGET_MOVED`、ベースリポジトリが違えば `refused`。oid だけが違えば `expect.rejudge(本文)` の結果を返す |

- `merge`
  - 事前条件: `expect.base`・`expect.head` は `ndf ci merge` が判定に使った値
  - 事後条件: 要求を送ったのは、照合でマージ先・HEAD が同じで、oid が同じか `rejudge` が `None` を返したときだけである
  - 副作用: GitHub の REST・GraphQL

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `merge` | 照合で断る・GitHub が断る | 下の行の原因と、今と同じ断り（`base_conflict`・`not_ci` など） | `_verify`・送信 | 呼び出し元へ返す（`MergeOutcome`） | `ndf ci merge` の `stopped` | 再実行で判定からやり直す |
| `_verify` | マージ先か HEAD が違う | 判定の後に PR が変わった | 照合 | 止める | `MergeOutcome(False, github_merge.TARGET_MOVED, …)`。`ndf ci merge` が `approval_stale` か `refused` に写す | 再実行で判定からやり直す |
| `_verify` | 設定の oid が違い、やり直した判定で承認が要って証跡が無い | 判定の後に設定が変わった | `rejudge` | 止める | `rejudge` が返した結果（`--production-gate` の形） | 同上 |
| `_enqueue` | 投入の後にマージ先が変わった | キューへ入れた後の変更 | 投入の後の読み直し | 止める（`dequeue` してから） | `github_merge.TARGET_MOVED` | 同上 |
| `branch_file` | リポジトリが違う | PR の応答と読んだリポジトリが違う | 応答の `nameWithOwner` | 呼び出し元へ返す（`FileAt.repo` を呼び手が比べ `UNKNOWN`） | 無し | — |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `FileAt` | 型 `NamedTuple` | `adapters/github.py` | `repo: str`・`text: Optional[str]`・`oid: Optional[str]` |
| `MergeExpect` | 型 `NamedTuple` | `adapters/github.py` | `base: str`・`head: str`・`config_path: str`・`config_oid: Optional[str]`・`rejudge: Callable[[Optional[str]], Optional[MergeOutcome]]` |
| `Verified` | 型 `NamedTuple` | `adapters/github.py` | `base: str`・`head: str`・`repo: str`・`config: FileAt`（照合の応答） |
| `_VERIFY` | GraphQL の文 | `adapters/github.py` | PR の `baseRefName`・`headRefOid`・`baseRepository.nameWithOwner` と `object(expression: "<base>:<path>")` の `oid`・`text` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| マージ先の変更・設定の変更と `ndf ci merge` | PR のマージ先、マージ先の設定 | 排他は取らない。送る直前の照合と、REST の `sha`・GraphQL の `expectedHeadOid` で HEAD を GitHub 側で固定する | 照合と送信の間の 1 回の往復の余地は残る（決定 8）。止まったら再実行で判定からやり直す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `branch_file` | `GitHub.repository`・`GitHub.graphql` | `grep -n "def repository\|def graphql" src/ndf/adapters/github.py` | `repository` を使って問い合わせる |
| `viewer` | `GitHub` のメソッド | `grep -n "viewer" src/ndf/adapters/*.py` | 0 件。新設する |
| `_verify` | `_preflight` | `grep -n "def _preflight" src/ndf/adapters/github.py` | HEAD の照合は `_preflight` と同じ規則にそろえ、マージ先と oid を足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `merge` | 照合が同じで送る。要求の数が決定 3 の表と同じ | `expect` が無い呼び出しは今と同じ要求の数 | 方式の問い合わせの間・キューの状態の問い合わせの間のマージ先の変更で `github_merge.TARGET_MOVED` |
| `_verify` | oid が違い `rejudge` が `None` でそのまま送る | oid の違いで設定を読み直す要求を足さない | `rejudge` が結果を返すと送らない |
| `_enqueue` | 投入して `ci_pending` | 既にキューにあれば入れ直さない | 投入の後のマージ先の変更で `dequeue` |

## hook の guard `merge-production`

満たす決定: 決定 2、決定 7、受け入れ条件 5・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_deliveries` | `domains/hooks/strict.py` | 変更 | `(repo_root: Path) -> object` | 設定のファイルを読んで `branch_strategy` のセクションを返す。読めなければ `None`。規則は持たない |
| `_judge` | `domains/hooks/strict.py` | 変更 | 今と同じ | `production_merge.automatic_rows` の値で、`AUTOMATIC`・`UNKNOWN` を止め、`MANUAL`・`UNSET` を通す |
| `_writes_merge_mark` | `domains/hooks/strict.py` | 新設 | `(here: Optional[Path], words: List[str], text: str) -> Optional[Verdict]` | `gh pr comment`・`gh issue comment`・`gh api` の呼び出しで、コマンドの全文（ヒアドキュメントを含む）か、本文として渡したファイル（`--body-file`・`-F`・`--input`）に `ndf:merge-approval` があれば止める |
| `_call_verdict` | `domains/hooks/strict.py` | 変更 | 今と同じ | マージの呼び出しに加えて `_writes_merge_mark` を当てる |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_judge` | 設定を読めない | ファイル・セクションが無い | `_deliveries` が `None` | 止める（今と同じ） | hook の拒否 | — |
| `_writes_merge_mark` | 本文のファイルを読めない | 標準入力（`-`）・無いパス | ファイルの読み取り | 既定値で続ける（通す）。防ぐ相手はうっかりだけで（決定 7 の脅威の整理）、ファイルを読めないコメントをすべて止めると通常のコメントも止まる | 無し | — |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| hook の import の表 | 設定 | `contracts/hooks/imports.json` | `entry.modules` と `stdlib_only` に `ndf.core`・`ndf.core.production_merge` を足す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_writes_merge_mark` | `_merge_call`・`_command_words` | `grep -n "def _merge_call\|def _command_words" src/ndf/domains/hooks/strict.py` | 使う。ラッパーを外した語の並びと `gh` の呼び出しの見分け方を同じにする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_judge` | 今の入力で今と同じ結果 | `cd: null` で通す、`cd` のキー無しで止める | 部品の戻り値を差し替えると結果が変わる |
| `_writes_merge_mark` | マーカーの無いコメントを通す | `ndf:release-approval` のコメントは止めない。標準入力の本文は通す | `gh pr comment --body`・ヒアドキュメント・`--body-file` のファイル・`gh api` の本文にマーカーがあれば止める |

## 共有のステップとテンプレート

満たす決定: 決定 4、決定 5、決定 7、受け入れ条件 1〜6・10・11

設定のファイルだけを変えるので、関数の仕様は無い。中身は次の表のとおりである。

| ファイル | 変える中身 |
| --- | --- |
| `contracts/templates/parts/steps.json` | `merge` の `run` に `--production-gate {{production_gate}} --presented-out {{state_dir}}/presented-merge.json` を足し、`on_answer` を `{"approved": "approve-merge", "rejected": "reject-merge"}` にする。`approve-merge`・`reject-merge`・`approve-gate`・`reject-gate` を足す。`run` は `ndf ci answer --branch {{branch}} --answer-record {state_dir}/answer-records/<merge か merge-gate>.json --presented {{state_dir}}/presented-<merge か merge-gate>.json`。`approve-*` の `next` は `merge`、`reject-*` は終わり。`merge-gate` の `on_answer` を `{"approved": "approve-gate", "rejected": "reject-gate"}` にする |
| `contracts/templates/params.json` | `production_gate`（既定 `stop`）・`approved_head`・`approved_base`・`pr` を足す。`gate` の既定を `["ndf", "ci", "ask", "--branch", "{{branch}}", "--presented-out", "{{state_dir}}/presented-merge-gate.json"]` にする |
| `contracts/templates/{merge,plan-to-spec,close,fix,check,design-merge}.json` | `params` に `production_gate` を足し、`steps` に `approve-*`・`reject-*` を足す（`merge-gate` を持つ `check`・`design-merge` は `*-gate`、ほかは `*-merge`） |
| `contracts/templates/release.json` | `params` に `pr`・`approved_head`・`approved_base` を足す。最初のステップ `release-merge` は `["ndf", "ci", "merge", "--pr", "{{pr}}", "--production-gate", "stale", "--approved-head", "{{approved_head}}", "--approved-base", "{{approved_base}}"]`、その後に今の `work` |
| `contracts/causes/approval_stale.json` | 停止理由の定義を足す |

単体テストの観点（`tests/progress/test_sprint_templates.py`）: テンプレートを組んだプランで、次の 2 つを確かめる。

- `merge` の `ok`（`deferred` を含む）が `approve-merge` へ流れない
- `approve-*`・`reject-*` が `on_answer` からしか到達しない

## 差し込みの組み立て

満たす決定: 決定 4、決定 5、受け入れ条件 1・2・3・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `template_values` | `progress/invoke.py` | 変更 | 今と同じ | 工程から組むときに `_fill_production_gate` と、`release` では `_approved_target` を通す |
| `_fill_production_gate` | `progress/invoke.py` | 新設 | `(t: route.Template, run: StageRun) -> dict[str, route.Value]` | テンプレートの名前とステップから `production_gate` を決める。`merge`（`pr-merge`・`docs-merge`）と `plan-to-spec` は `defer`、`design-merge` と `release` は `stale`。工程が承認ゲートを持たない（`StageRun.gated` が偽。`light` の設計）なら `stale` を `defer` に替える。表に無いテンプレートは値を入れず、既定の `stop` のまま |
| `_approved_target` | `progress/invoke.py` | 新設 | `(run: StageRun) -> dict[str, route.Value]` | リリース承認ゲートの最新の承認の回答の事象の `seen` から `approved_head`・`approved_base` を作る。`base` が無ければ空文字 |

- `_approved_target`
  - 事前条件: `run` は工程「デプロイ」の実行で、承認の回答がある（`release-gate` の後にだけ流れる）
  - 事後条件: 旧版の回答（`seen` にマージ先が無い）では `approved_base` が空文字になり、`release-merge` が `approval_stale` で止まる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_fill_production_gate` | 表に無いテンプレート | `merge` のステップを持つテンプレートを足した | 名前の表 | 止めずに既定の `stop` のまま組む | マージのステップが承認待ち（`gate`） | 先送りや取り直しが要るなら表へ足す |
| `_approved_target` | 承認の回答が無い | 工程の定義の誤り | 事象の検索 | 既定値で続ける（空文字） | `release-merge` の `approval_stale` | 工程を流し直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `PRODUCTION_GATE` | 定数 | `progress/invoke.py` | `{"merge": "defer", "plan-to-spec": "defer", "design-merge": "stale", "release": "stale"}`。`STAGE_GATE` の隣に置く。値の語は `core/production_merge.py` の `DEFER`・`STALE` から読む |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_fill_production_gate` | `invoke.STAGE_GATE`・`FILL_SOURCES` | `grep -n "STAGE_GATE\|FILL_SOURCES" src/ndf/progress/invoke.py` | 同じ置き方（名前の表の定数）にそろえる。値はテンプレートごとに違うので別の表にする |
| `_approved_target` | `answering._answered_with_key`・`asking.Target.from_dict` | `grep -n "def _answered_with_key\|def from_dict" src/ndf/progress/gate/*.py` | `Target.from_dict` で `seen` を読む。回答の検索はキーでなく最新の承認なので、同じ形の検索を足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_fill_production_gate` | 4 つのテンプレートの値。承認ゲートを持たない工程の `design-merge` は `defer` | 単発（`ndf route render`）では入れず既定 `stop`。表に無いテンプレートも既定 `stop` | — |
| `_approved_target` | 利用者の回答と `service:mvv` の回答の両方から同じ値 | 旧版の回答で `approved_base` が空 | — |

## 工程の順と、工程の定義の写しの移行

満たす決定: 決定 6、受け入れ条件 10・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `migrate_definition` | `progress/run.py` | 新設 | `(st: store.Store, machine_id: str) -> Optional[int]` | 写しが古い順で、デプロイが始まっておらず、クリーンアップが終わっていなければ、事象 `definition_migrated`（payload に新しい工程の順）を 1 回書き、その番号を返す |
| `_migrated_order` | `progress/run.py` | 新設 | `(snap: stages.Snapshot, events: Sequence[model.Event]) -> stages.Snapshot` | 事象 `definition_migrated` があれば、その順に工程を並べ替えた写しを返す |
| `view` | `progress/run.py` | 変更 | 今と同じ | `_migrated_order` を通した写しで `View` を作る |
| `advance` | `progress/run.py` | 変更 | 今と同じ | 駆動の前に `migrate_definition` を呼ぶ |

- `migrate_definition`
  - 事後条件: 1 つの状態機械で事象は高々 1 件。写しの `sha256`・`definition_version` は変えない（写しの由来を残すため）
  - 副作用: 状態の記録の追記（`append`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `migrate_definition` | 追記がぶつかる | 2 つの `next` が同時に打たれた | `store.Conflict` | 再試行（`retrying_transaction`）。読み直して既に事象があれば書かない | 無し | 冪等 |
| `_migrated_order` | 事象の順が写しの工程と合わない | 手で書いた事象 | 工程の名前の集合の比較 | 既定値で続ける（写しのまま） | 無し | — |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `migrate_definition` | 写しでクリーンアップがデプロイより前、デプロイの開始の事象が無い、クリーンアップが終わっていない（`precondition` で止まった失敗を含む） | 移行する |
| `migrate_definition` | クリーンアップが終わっている、またはデプロイが始まっている | 移行しない |
| `migrate_definition` | 既に `definition_migrated` がある | 移行しない |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ状態機械への `ndf progress next` | 状態機械の事象 | 今の追記のトランザクションと番号のぶつかりの再試行 | 事象の有無で判定するので、何度打っても 1 件 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `migrate_definition` | `run.append`・`command.retrying_transaction` | `grep -n "def append\|def retrying_transaction" src/ndf/progress/run.py src/ndf/progress/command.py` | 使う |
| `_migrated_order` | `store` の定義の書き込み | `grep -n "def .*definition" src/ndf/progress/store.py` | 書き込みの関数は無い（`read_definition` だけ）。写しは書き換えず、事象から並べ替える |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `migrate_definition` | 古い写しで 1 件書き、デプロイ → クリーンアップの順で進む | `precondition` で止まったクリーンアップでも移行する。2 回打っても 1 件 | 終わったクリーンアップ・始まったデプロイでは書かない |
| `_migrated_order` | 事象の順に並ぶ | 事象が無ければ写しのまま | 名前の合わない事象では写しのまま |
