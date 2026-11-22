# #4104 の詳細設計: 即時修正の MVV 判定と、CI が確かめた PR の受け渡し

設計文書 [issue-4104-design.md](./issue-4104-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

| 別ファイルにした理由 | 関数 |
| --- | --- |
| 新設する関数が 5 つ | `read_pr_target`・`PrTarget.item`・`records/cli.py` の `_fix_result`・`GitHub.pr_check_scope`・`core/plan/records.py` の `approved_after` |
| シグネチャを変える関数が 8 つ | `gate`・`_judged`・`pr_number`・`note`・`GitHub.merge`・`_merge_with`・`_preflight`・`_enqueue` |

前提は #3959 の実装が main へ入った後の形である。使う部品は [issue-3959-4055-design-detail.md](./issue-3959-4055-design-detail.md) の `gate`・`_judged` の手順 (1)〜(9)・`judgeable`・`CHECKS_BY_GATE`・`refusals(f, gate)`・`may_skip(f, verdict, gate)`・`review_prs`・`snapshot_facts` である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── templates/fix.json                     … ステップに mvv・merge-gate、merge を fix-merge に
│   ├── templates/parts/steps.json             … 部品 fix-mvv・fix-merge、部品 ci の --remote
│   ├── workflow/context.schema.json           … $defs.mvv_gate の gate に fix、target・note_url
│   └── schemas/io/
│       ├── records.args.schema.json           … mvv gate の --gate fix・--base・--ci-result・--remote
│       ├── ci.args.schema.json                … ci merge の --target・--judged・--remote、ci watch の --remote
│       └── ci.result.schema.json              … watch の成功の items の PR の項目
├── docs/contracts/commands.md                 … 上の 2 つの引数の表から作り直す
├── src/ndf/
│   ├── contracts/result.py                    … PrTarget・read_pr_target
│   ├── ci_cli.py                              … HeadReader・_watch・_report・_merge・_parser
│   ├── adapters/github.py                     … merge・_merge_with・_preflight・_enqueue の expect_base、pr_check_scope
│   ├── core/plan/records.py                   … approved_after（判定の後の人の承認）
│   ├── core/plan/runner.py                    … gate_result に outputs
│   ├── domains/records/mvv_gate.py            … 区分 fix（gate・_judged・pr_number・review_prs）
│   ├── domains/records/cli.py                 … _mvv_gate の fix の入口
│   └── progress/gate/checks.py                … CHECKS_BY_GATE["fix"]・note の区分名
├── skills/development-workflow/references/
│   ├── pace.md                                … 即時修正の判定と人の回答
│   └── conductor-entrypoints.md               … immediate_fix の説明
└── tests/
    ├── adapters/test_ci_cli.py
    ├── adapters/test_github.py
    ├── domains/records/test_mvv_gate.py
    └── route/test_templates.py
```

依存の向き: `ci_cli` と `records/cli` が `contracts/result` の `PrTarget` を読む。`ci_cli` は人の承認を `core/plan/records` の `approved_after` だけで読み、進捗記録の形を読まない。`mvv_gate` は `PrTarget` を型として受け取るだけで、ファイルを読まない（読むのは入口の `records/cli`）。`adapters/github` は `PrTarget` を読まず、突き合わせる値（`expect_head`・`expect_base`）を引数で受ける。`contracts` はどれにも依存しないので循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/contracts/result.py` | 変更 | 結果 JSON の組み立てと読み取り | — | `PrTarget`（新設） | `PrTarget.item`・`read_pr_target`（新設） |
| `src/ndf/ci_cli.py` | 変更 | `ndf ci` の入口 | `contracts.result`・`adapters.github`・`core.plan.records` | `HeadReader`（変更） | `HeadReader.__call__`・`_watch`・`_report`・`_merge`・`_parser`（変更） |
| `src/ndf/adapters/github.py` | 変更 | GitHub との接続 | — | `GitHub` | `merge`・`_merge_with`・`_enqueue`（変更）、`pr_check_scope`（新設）、`_preflight`（変更） |
| `src/ndf/core/plan/records.py` | 変更 | プランの回をまたぐ記録 | — | モジュール | `approved_after`（新設） |
| `src/ndf/core/plan/runner.py` | 変更 | プランの実行 | `core.plan.records` | モジュール | `gate_result`（変更） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートと即時修正の前の MVV 判定 | `contracts.result`・`progress.gate.checks` | モジュール | 定数 `FIX`（新設）・`gate`・`_judged`・`pr_number`・`has_gate`・`review_prs`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv_gate`・`contracts.result` | モジュール | `_mvv_gate`（変更）・`_fix_result`（新設の内部の関数） |
| `src/ndf/progress/gate/checks.py` | 変更 | 機械の検査と判定の記録 | — | モジュール | `CHECKS_BY_GATE`（変更）・`note`（変更） |
| `contracts/templates/parts/steps.json` | 変更 | プランの部品 | — | — | 部品 `fix-mvv`・`fix-merge`（新設） |
| `contracts/templates/fix.json` | 変更 | 即時修正のプラン | 部品 | — | `steps`・`params`・`summary` |

## CI が確かめた PR

満たす決定: 決定 2・3・7・8、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PrTarget` | `contracts/result.py` | 新設 | `NamedTuple(repo: str, number: int, base: str, head: str)` | CI が確かめた PR の識別 |
| `PrTarget.item` | `contracts/result.py` | 新設 | `item(self) -> dict[str, Any]` | 結果 JSON の項目 `{"kind": "pr", "repo", "number", "base", "head"}` を返す |
| `read_pr_target` | `contracts/result.py` | 新設 | `read_pr_target(path: Path) -> PrTarget` | ファイルの最後の空でない行を結果 JSON として読み、`status` が `ok` で、`items` の最初の `kind: "pr"` の項目が 4 つのキーを形どおりに持てば `PrTarget` を返す。ほかは `ValueError`（理由つき） |
| `_parser`（`watch`） | `ci_cli.py` | 変更 | — | `--remote <名前>` を足す（`--repo` と排他） |
| `_watch` | `ci_cli.py` | 変更 | シグネチャは同じ | リポジトリを `args.repo`、無ければ `git.Git().remote_repo(args.remote)`（`args.remote` が None なら今のまま作業場所のリモート）で決める |
| `GitHub.pr_check_scope` | `adapters/github.py` | 新設 | `pr_check_scope(number: int, head: str) -> CheckScope`（`CheckScope = NamedTuple(base_changed_at: Optional[str], others: tuple[tuple[int, str], ...])`） | GraphQL の 1 回で、PR `number` の `timelineItems(itemTypes: [BASE_REF_CHANGED_EVENT], last: 1)` の `createdAt` と、`repository.object(oid: head)` の `associatedPullRequests(states: OPEN)` のうち `headRefOid == head` で番号が `number` でないものの（番号, `baseRefName`）を返す |
| `HeadReader.__call__` | `ci_cli.py` | 変更 | シグネチャは同じ | 今の読み取りが必須のチェックをすべて通した（結論が成功）ときだけ `pr_check_scope(pull.number, pull.head_sha)` を 1 回呼び、結果を `self.scope` に残す。`base_changed_at` があれば、`started_at` がそれより前のチェックを除いた同じ `runs` で `read` をやり直して返す（API を読み直さない。そろわなければ待ちが続く）。成功でない読み取りでは呼ばない |
| `_report` | `ci_cli.py` | 変更 | シグネチャは同じ | 成功（`kind is None`）で `reader.scope.others` のうち `base` が `pull.base_ref` と違うものがあれば、成功の項目を書かずに `stopped`・`precondition`（「同じ head <sha> の PR #<m> が <base'> 宛てにもあり、チェックの成功がどの宛先で得たものか決まらない」）。ほかの成功は `items` の先頭に `PrTarget(hub.repo, pull.number, pull.base_ref, pull.head_sha).item()` を置く。`pull` は `HeadReader` が読み終えた PR（今の呼び出しのとおり `reader.pull`）。失敗の結果は今のまま |

- `read_pr_target`
  - 事後条件: 返した値の `repo` は空でない文字列、`number` は 1 以上の整数（`bool` を除く）、`base` は空でない文字列、`head` は 40 桁の 16 進
  - 副作用: ファイルを 1 回読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_pr_target` | ファイルが無い・読めない | CI のステップが走っていない | `OSError` | `ValueError` に包んで返す | 呼び手が `precondition` で止める | プランを `ci` から打ち直す |
| `read_pr_target` | JSON でない・`status` が `ok` でない・PR の項目が無いか形が違う | CI が落ちた・旧い形のファイル | 形の確かめ | `ValueError` | 同上 | 同上 |
| `_report` | 同じ head で宛先の違う開いた PR がある | 同じブランチか同じコミットから別の宛先へも PR を出した | `reader.scope.others` | 成功の項目を書かない | `stopped`・`precondition`（終了コード 1） | 余分な PR を閉じ、プランを `ci` から打ち直す |
| `HeadReader` | 宛先の変更の後に打ち直したチェックが無い | 同じ SHA のまま宛先を付け替え、ワークフローが付け替えで起きない | `started_at < base_changed_at` | そのチェックを数えない（待ち） | 予算に届けば今の CI の待ち切れと同じ | CI を打ち直してからプランを `ci` から打ち直す |
| `pr_check_scope` | GraphQL が `GitHubError` | GitHub が回答しない | 例外 | 今の `check_runs` の失敗と同じく `_watch` の外へ出す（成功にしない） | 今と同じ | 打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_report` | 成功の結果の `items[0]` が、`HeadReader` が読み直した後の PR の repo・番号・宛先・head | push の直後で最初に読んだ PR が前のコミットを指す → 読み直した head を書く | 失敗の結果に PR の項目を足さない。同じ SHA を head にする別の宛先の開いた PR があり、同名の必須チェックがその PR の CI で先に通っている → 成功の項目を書かず `precondition` |
| `HeadReader` | 宛先の変更が無い → `pr_check_scope` を成功の読み取りで 1 回だけ呼び、今と同じ結果 | 待ち（pending）の読み取りでは `pr_check_scope` を呼ばない | 同じ SHA のまま宛先を付け替え、付け替えの前に通ったチェックしか無い → 成功にせず待ち。付け替えの後に打ち直したチェックが通れば成功で、項目の `base` は今の宛先 |
| `_watch` | `--remote upstream` で upstream のリポジトリを読む（origin の同じ番号を読まない） | `--remote`・`--repo` を省く → 今のまま | `--remote` と `--repo` を両方渡す → `invalid` |
| `read_pr_target` | `ndf ci watch` の成功の出力の 1 行から読める | 項目の後ろにチェックの項目が並ぶ | `status: stopped`・項目が無い・`number` が文字列・`head` が 39 桁・空のファイル → `ValueError` |

## 判定の区分 `fix`

満たす決定: 決定 1・2・3・6・7、受け入れ条件 1・2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FIX` | `records/mvv_gate.py` | 新設 | `FIX = "fix"` | 即時修正の区分の名前 |
| `gate` | `records/mvv_gate.py` | 変更 | `gate(root, gate_name, inputs, hub, ask=llm.ask, expect: Optional[PrTarget] = None) -> dict` | `fix` では `expect` が必須（無ければ「CI が確かめた PR が無い」で `verdict: null`・`head` 無し）。`inputs["remote"]` を #3959 の手順 (2) の取得のリモートにする。`has_gate` を通さない。ほかは #3959 のまま |
| `_judged` | `records/mvv_gate.py` | 変更 | `_judged(root, gate_name, inputs, open_hub, ask, expect)` | 下の「処理の手順」 |
| `pr_number` | `records/mvv_gate.py` | 変更 | `pr_number(root, gate, inputs, hub, expect: Optional[PrTarget] = None) -> int` | `fix` はブランチで探さず `expect.number` を返す。`expect.repo != hub.repo`・`expect.base != inputs["base"]` なら `GateError`（「CI が確かめた #<n>（<repo>・<base> 宛て）は判定する <repo'> の <base'> 宛ての PR でない」）。`design`・`release` は #3959 のまま |
| `review_prs` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `fix` は `[number]`（`design` と同じ） |
| `CHECKS_BY_GATE` | `progress/gate/checks.py` | 変更 | キー `fix` を足す | 検査 1・4・5・6・7（2 のスプリントの参照と 3 のモードは即時修正に無い） |
| `note` | `progress/gate/checks.py` | 変更 | #3959 のシグネチャに `target: Optional[PrTarget] = None` を足す | 見出しの区分名を `gate == "fix"` なら「即時修正のマージ」、ほかは `model.gate_label(gate)` にする（`fix` は承認ゲートの区分の表 `workflow/progress.json` の `gates` に足さない）。`target` があれば見出しの直後に「判定した PR: <repo>#<number>（<base> 宛て・head <40 桁>）」の行を置く（`fix` は常に渡す）。理由の小見出しは `fix` では「マージへ進めない理由」 |
| `_recorded` | `records/mvv_gate.py` | 変更 | #3959 のまま | `fix` では `note(..., target=expect)` を書き、`Publisher.comment` が返す `html_url` を出力の `note_url` に置く。書けなかったときは #3959 のとおり `noted: false` で `note_url` を置かない |
| `$defs.mvv_gate` | `contracts/workflow/context.schema.json` | 変更 | `gate` の `enum` に `fix`。任意のキー `target`（`{"kind": "pr", "repo", "number", "base", "head"}` の形）と `note_url`（文字列）を足す | `skip` の説明に「`fix` では人の回答を待たずにマージへ進めるか」を足す。`target` は区分 `fix` で判定の本体が PR を読み終えた終わり方に置き、`note_url` は今回の記録を書けたときだけ置く |

### 処理の手順

`_judged` の `fix` の分岐（#3959 の手順の番号で書く。書かない手順は #3959 のまま）:

| 手順 | `fix` の振る舞い |
| --- | --- |
| (1) | `pr_number(..., expect)` で番号を決め、`hub.pull` を 1 回読む。`pull.head_sha != expect.head` か `pull.base_ref != expect.base` なら `GateError`（「CI が確かめた #<n>（<base> 宛て・<a>）と PR の今（<base'> 宛て・<b>）が違う」）。`gate` が受けて `verdict: null`・`head` 無し・コメント無しで返す |
| (2) | ベースの MVV の `git fetch` は `--remote` のリモートで打つ（#3959 の「<リモート>」をこの値にする） |
| (3) | `judgeable` が理由を返したとき、ベースの MVV の状態（`snap.state`）が `absent`・`unapproved` なら `skip: true`・`why`: 「MVV が承認されていないので判定しない」で返す（コメントを書かない）。`mismatch` なら #3959 のとおり `skip` 偽・`noted: false` |
| (4) | #3959 のとおり差分を読んだ後に `hub.pull` を読み直す。読み直した head か宛先が (1) と違うとき・読み直しが `GitHubError` のとき、#3959 はコメントを書かずに返すが、区分 `fix` では出力に `noted: false` を明示する（今回の判定の記録が PR に無いので、`_fix_result` が `refused` でなく `precondition` に分ける。`noted` が無いと `head` を持つ出力は `refused` になり、今回のコメントが無いまま人に承認を求める） |
| (5)〜(9) | #3959 のまま。検査は `CHECKS_BY_GATE["fix"]`、保留は `review_prs(FIX, number)`。(7) で LLM が回答しない（`GateError`）ときも #3959 のとおり (7) が受けて `verdict: null`・(1) の `head` のまま (9) で記録を書くので、記録のある進めない判定（`refused`）になる。`gate` が `GateError` を受けて `head` 無しにするのは (1) までの失敗だけである |
| 出力 | 判定の本体が (1) を読み終えた終わり方は、どれも `target`（`expect.item()`。(1) で `expect` と同じと確かめた値）を持つ。`note_url` は (9) で書けたときだけ |
| 省くか | `skip` は「理由が空・判定がコントラクトの形・『従う』・レッドラインが空」で真。進め方（`inputs["pace"]`）とモードを読まない |

`records/cli.py` の `_mvv_gate`:

| 条件 | 結果 |
| --- | --- |
| `--gate fix` で `--base`・`--ci-result`・`--remote` のどれかが無い・`--machine` を渡した | `stopped`・`invalid` |
| `--gate design`・`release` で `--base`・`--ci-result`・`--remote` を渡した | `stopped`・`invalid` |
| `read_pr_target(--ci-result)` が `ValueError` | `stopped`・`precondition`（「MVV 判定を打てない: CI が確かめた PR が無い: <理由>」）。判定の本体を呼ばない |
| `gate(root, FIX, {"base": --base, "remote": --remote}, _hub, expect=target)` の出力 `out` の `skip` が真 | `ok`。`summary` は `out["why"]` が空なら「MVV 判定: マージへ進める」、空でなければその理由（MVV が承認されていない） |
| `out` に `head` が無い・`out.get("noted") is False` | `stopped`・`precondition`（「MVV 判定を打てない: 」と `why`） |
| ほか | `stopped`・`refused`（「MVV 判定でマージへ進めない: 」と `why`、「判定した PR: <repo>#<number>（<base> 宛て・head <head>）」と「判定の記録: <note_url>」） |

`_hub` は `--remote` があれば `Git(root).remote_repo(remote)` のリポジトリを開く（`design`・`release` は今のまま）。どの行も `outputs.mvv_gate` に `out` を添え、`items` の先頭に `out["target"]` があれば置く（`invalid` と判定の本体を呼ばない `precondition` を除く）。原因は `_fix_result(out)` が `out` の `skip`・`head`・`noted` だけから決め、MVV の状態を読み直さない。部品 `fix-mvv` は出力を `{state_dir}/mvv.json` に `tee` で書き、マージの根拠にする（下の「マージ」）。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pr_number`（`fix`） | CI の PR のリポジトリか宛先が判定の対象と違う | `gh pr view <ブランチ>` が別の宛先の PR を選んだ（決定 3） | `GateError` | `gate` が `verdict: null`・`head` 無しで返す | `precondition`（終了コード 1）。コメント無し | 余分な PR を閉じ、プランを `ci` から打ち直す |
| `_judged`（`fix` の (1)） | CI の後に push・宛先の変更があった | 同じ作業場所からの push・人の宛先の変更 | (1) の比較 | 同上 | 同上 | プランを `ci` から打ち直す（新しい head で CI と判定をやり直す） |
| `_judged`（`fix` の (4)） | 差分を読む間に head・宛先が動いた・差分の後に PR を読み直せない | 判定の最中の push・宛先の変更・GitHub が回答しない | #3959 の (4) の読み直し | `head` あり・コメント無し・`noted: false` | `precondition`（人の回答を待たない） | 動いたときはプランを `ci` から、読み直せないときは判定のステップから打ち直す |
| `_judged`（(7)） | LLM が回答しない | LLM の失敗 | #3959 の (7) の `GateError` | `verdict: null`・`head` あり・(9) で記録を書く | `refused`（記録を読んで人が答える） | 人が却下してから打ち直すか、承認する |
| `_judged`（`fix` の (3)） | ベースの MVV のハッシュが承認の記録と違う | ベースの MVV の本文を承認なしに変えた | `judgeable` と `snap.state` | `skip` 偽・`noted: false` | `precondition` | MVV を承認し直してから打ち直す |
| `_judged`（(9)） | 判定の記録を PR に書けない | GitHub が回答しない | `_recorded` | `noted: false` | `precondition`（人の回答を待たない。コメントの無い理由で承認させない） | `ndf plan next <プラン>` で判定のステップから打ち直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate`（`fix`） | 判定のスタブが「従う」・レッドライン空・保留無し → `skip` 真、コメント 1 件、`target` と `note_url` あり。PR の検索の API を呼ばない | 入力に `pace`・`mode` が無くても判定する。ベースの MVV が `absent` → 判定のスタブを呼ばず `skip` 真・コメント無し | レッドラインのパス・MVV の設定の変更・保留・「従わない」→ `skip` 偽・コメント 1 件。LLM のスタブが `GateError` → `verdict: null`・`head` あり・コメント 1 件・`noted` 無し。差分の後の読み直しで head か宛先が動いた・読み直しが `GitHubError` → `noted: false`・コメント無し・LLM を呼ばない。`expect` の head か宛先が今の PR と違う → `head` 無し・判定のスタブもコメントも呼ばない。`expect` が無い → `head` 無し |
| `pr_number`（`fix`） | `expect.number` | 同じブランチの別の宛先の開いた PR があっても `expect.number` | `expect.repo`・`expect.base` が違う → `GateError` |
| `_mvv_gate`（`fix`） | `skip` 真 → 終了コード 0 | MVV が承認されていない → 0 | 「従わない」→ 1・`refused`・`summary` に PR・宛先・head とコメントの URL。LLM の無回答 → 1・`refused`。差分の後に head が動いた・読み直せない → 1・`precondition`。`noted: false`・`head` 無し → 1・`precondition`。`ci.json` が無い・失敗の結果 → `precondition` で判定の本体を呼ばない。`--base`・`--remote` 無し・`--machine` 併用 → `invalid`。`--remote upstream` → upstream のリポジトリで PR を読みコメントを書き、ベースを upstream から取得する |
| `note`（`fix`） | 見出しが「即時修正のマージ」で、直後に判定した PR・宛先・head の行 | 同じ PR を別の head で判定し直す → 新しいコメントの見出しの head が新しい値 | — |

## マージ

満たす決定: 決定 2・4・7、受け入れ条件 2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_preflight` | `adapters/github.py` | 変更 | `_preflight(pull, number, expect_head, expect_base: Optional[str] = None)` | `expect_base` が与えられたときは、宛先 → head → マージ済み → 残リスクの順に見る。宛先が違えば `MergeOutcome(False, "refused", "#<n> の宛先が <expect_base> でない（今は <base_ref>）", pull.head_sha)`、head が違えば `MergeOutcome(False, "refused", "#<n> の HEAD が <expect_head> でない（今は <head_sha>）", …)`（マージ済みでも。同じ宛先へ別の head がマージされたものを成功にしない）、両方同じでマージ済みなら `MergeOutcome(True, None, "#<n> は既にマージ済み", …)`。`expect_base` が無ければ今の順（マージ済み → head → 残リスク） |
| `_merge_with` | `adapters/github.py` | 変更 | `_merge_with(number, expect_head, method, route, expect_base: Optional[str] = None)` | `expect_base` があるときは、(a) 方式を先に `allowed_merge_methods(expect_base)` で選び、(b) PR を 1 回読んで `_preflight` に渡し、(c) 間に API を挟まずに `route.send` を打つ。405 で断られたら `_enqueue(pull, required, jump, expect_base)`。(d) 直接のマージが通ったか投入したら `self.pull(number)` を 1 回読み直し、`base_ref != expect_base` なら `MergeOutcome(False, "refused", "マージの要求の間に #<n> の宛先が <今> へ変わった（<expect_base> 宛てとして照らした）", …)`（キューに入れていれば `dequeue`）。`expect_base` が無ければ今の順のまま（PR を読んでから方式を選ぶ） |
| `_enqueue` | `adapters/github.py` | 変更 | `_enqueue(pull, method, jump=False, expect_base: Optional[str] = None)` | `expect_base` があるとき、`_MERGE_QUEUE_STATE` の同じ 1 回の読み取りで `pullRequest` の `baseRefName`・`headRefOid` も読み、`expect_base`・`pull.head_sha` と違えば投入せずに `refused` を返す |
| `merge` | `adapters/github.py` | 変更 | `merge(number, expect_head=None, method=None, require_method=False, jump=False, expect_base: Optional[str] = None)` | `expect_base` を `_merge_with` へ渡す。省けば今と同じ。`merge_as_admin` は変えない |
| `approved_after` | `core/plan/records.py` | 新設 | `approved_after(state_dir: Path, judge: str, gate: str, to: str) -> bool` | 進捗記録（`recorded_choices`）で、`from` が `judge` の最後の選択が `to: gate` で、それより後に `from: gate`・`to: to`・`chooser` が人の選択があれば真。記録が無い・最後の選択が別の行き先・後の人の選択が `end`（却下）なら偽 |
| `_merge` | `ci_cli.py` | 変更 | シグネチャは同じ | `--target` があれば下の手順、無ければ今のまま（`--branch`） |
| `_parser` | `ci_cli.py` | 変更 | — | `merge` の `--branch` と `--target` を必須の排他の組にする。`--target` と `--judged` は組で（片方だけは `invalid`）、`--reviewed` とは同時に渡せない（`invalid`）。`--remote` を足す（`--repo` と排他） |

`_merge` の `--target` の手順:

| 手順 | 振る舞い |
| --- | --- |
| 1 | `read_pr_target(args.target)`。`ValueError` なら `stopped`・`precondition`（「CI が確かめた PR が無い: <理由>」）でマージしない |
| 2 | `hub = GitHub(args.repo or git.Git().remote_repo(args.remote))`（`--remote` も省けば作業場所のリモート）。`target.repo != hub.repo` なら `stopped`・`refused` |
| 3 | `args.judged` の最後の空でない行を結果 JSON として読み、`outputs.mvv_gate.target` が `target.item()` と同じかを照らす。読めない・`target` が無いか違う・`cause.kind` が `precondition` なら `stopped`・`refused`（「<repo>#<n>（<base> 宛て・<head>）に対する MVV 判定の通過が無い」）。`status` が `ok` なら次へ。`cause.kind` が `refused` なら `approved_after(Path(args.judged).parent, "mvv", "merge-gate", "merge")` が真のときだけ次へ、偽なら `stopped`・`refused`（「MVV 判定でマージへ進めないと判定され、その後の人の承認が無い」）。どれも要求を送らない |
| 4 | `hub.merge(target.number, expect_head=target.head, expect_base=target.base, method=args.method)`。ブランチで PR を探さず、作業場所の HEAD を読まない |
| 5 | 結果は今の `_merge` と同じ形（`items` は `{"kind": "pr", "number": target.number}`） |

判定のステップは打つたびに `mvv.json` を書き直し（部品 `fix-mvv` の `tee`）、`ci` を打ち直せば `ci.json` の head が変わる。どちらの打ち直しの後も、古い判定・古い承認はマージの根拠にならない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_merge`（`--target`） | 判定の後・人の承認の後に head が進んだ | 同じ作業場所からの push | `_preflight` の head | `refused` | 終了コード 1。`cause.instance` に今の head | プランを `ci` から打ち直す |
| `_preflight` | 宛先が変わった | 人の宛先の変更 | `expect_base` の比較 | `refused`（マージ済みでも） | 同上 | 同上 |
| `_preflight` | 判定の後に別の head が push され、先にマージされた | 人か別の自動処理のマージ | `expect_base` があるときの head の比較（マージ済みより先） | `refused` | 同上 | 人がマージされた変更を確かめる |
| `_merge`（`--target`） | 打ち直しで既にマージ済み | 前の回がマージした後に落ちた | `_preflight` のマージ済み（宛先・head が同じ） | `ok` | 終了コード 0（「既にマージ済み」） | — |
| `_merge`（`--target`） | 判定の通過も承認も無い | 判定が `precondition` で止まった・人が却下した後に `--from merge` で打ち直した | 手順 3 | `refused`（要求を送らない） | 終了コード 1 | プランを `ci` か `mvv` から打ち直す |
| `_merge_with` | 照らした読み取りから要求までの 1 往復の間に宛先を変えた | 書き込みの権限を持つ人の操作 | 要求の後の読み直し | `refused`（直接のマージは戻せないので成功にせず知らせる。キューは外す） | 終了コード 1。`cause.instance` に今の宛先 | 人が入った先を確かめる（決定 4） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_preflight` | `expect_base` が同じ → 今と同じ結果 | `expect_base` を省く → 今と同じ順 | 宛先が違う → マージ済みの PR でも `refused`。同じ宛先へ別の head でマージ済み → `refused`（`ok` にしない） |
| `_merge_with` | 方式を `expect_base` で選んでから PR を 1 回読み、`_preflight` の後に API を挟まずに要求を送る。`--method` が渡る。要求の後に 1 回読み直す | マージキューのある宛先 → 状態の読み取りで宛先・head を照らしてからキューへ入れる | 照らした後・要求の前に宛先が変わる（スタブで要求の後の読み直しの宛先を変える）→ `refused`、キューなら `dequeue` を呼ぶ。キューの状態の読み取りで宛先か head が違う → 投入しない |
| `approved_after` | 判定 → `merge-gate`、その後に人の `merge-gate` → `merge` → 真 | 判定を打ち直した後の承認が無く、前の回の承認だけある → 偽 | 記録が無い・人が `end`（却下）を選んだ → 偽 |
| `_merge`（`--target`） | 判定が `ok` の `mvv.json` で、項目の番号の PR に項目の head・宛先を渡す。`pulls_by_head` を呼ばない。判定が `refused` で承認の記録がある → マージする | 作業場所の HEAD が項目の head と違っても PR の head が同じならマージする。判定が `ok` の後にマージが落ち `--from merge` で打ち直す → マージする | ファイルが無い → `precondition`。リポジトリが違う → `refused`。`mvv.json` が無い・`precondition`・`target` が CI の項目と違う・`refused` で承認が無い・却下した → 要求を送らず `refused`。`--reviewed` 併用・`--judged` 無し → `invalid`。`--remote upstream` で upstream の PR をマージし、origin の同じ番号に触れない |

## 即時修正のプラン

満たす決定: 決定 1・2・7、受け入れ条件 1・3

| ファイル | 変える中身 |
| --- | --- |
| `contracts/templates/parts/steps.json` | 部品 `ci`: PR の選択を `gh pr view "$1" --repo "$(git remote get-url -- "$3")" --json number --jq .number` にし、`ndf ci watch --pr "$pr" --remote "$3"` に渡す（引数に `{{remote}}` を足す。部品 `ci` を使うプランはどれも値 `remote` を持つ。決定 7）。部品 `fix-mvv`: `{"id": "mvv", "run": ["bash", "-c", "set -o pipefail; out=$1; shift; ndf records mvv gate \"$@\" \| tee \"$out\"", "mvv", "{state_dir}/mvv.json", "--gate", "fix", "--base", "{{base}}", "--ci-result", "{state_dir}/ci.json", "--remote", "{{remote}}"]}`（原因は結果 JSON の `cause.kind` から読み、`exit_cause` を置かない。どの終わり方でも `mvv.json` を書き直す）。部品 `fix-merge`: `{"id": "merge", "run": ["ndf", "ci", "merge", "--target", "{state_dir}/ci.json", "--judged", "{state_dir}/mvv.json", "--remote", "{{remote}}", "--method", "{{merge_method}}"]}`。`probe` を置かない（決定 4） |
| `src/ndf/core/plan/runner.py` | `gate_result` が `recorded_outputs(inv.state_dir)` の `outputs` を結果に載せる（読めないときは載せず、今の注意と同じく `warnings` に足す）。即時修正では `mvv_gate` の `target`・`note_url`・`why` が承認待ちの結果に出て、人がどの PR・宛先・head の、どのコメントに答えるかを特定できる |
| `contracts/templates/fix.json` | `steps` を `test`・`push`・`pr`・`conflict-check`・`{"use": "ci", "on_cause": {}}`・`{"use": "fix-mvv", "on_cause": {"refused": "merge-gate"}, "next": "merge"}`・`{"use": "merge-gate"}`・`{"use": "fix-merge", "next": "end"}` にする。`params` に `gate` を足す（`params.json` の既定 `ndf answer ask`）。`summary` に「CI の後に MVV 判定を通し、進めない判定は人の回答を待つ」を足す |
| `skills/development-workflow/references/pace.md`・`conductor-entrypoints.md` | 即時修正もマージの前に MVV 判定を通ること、進めない判定では承認待ちの結果の `mvv_gate.note_url` のコメントを読んで `ndf plan next <プラン> --answer approved|rejected` で答えること、`precondition` で止まったら `--from ci` で打ち直すこと（`--from merge` では判定を飛ばせない）を書く |

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fix.json` | 描画したプランのステップの順が test・push・pr・conflict-check・ci・mvv・merge-gate・merge。`mvv` の成功は `merge`、`refused` は `merge-gate`、`merge-gate` の `approved` は `merge`・`rejected` は `end`。`ci`・`mvv`・`merge` の引数に同じ `remote` の値が入る | `gate` を渡さなければ既定の `ndf answer ask` | `mvv` の `precondition` はルートを持たず止まる |
| `gate_result` | `outputs.json` があれば承認待ちの結果に `outputs` が載る | `outputs.json` が無い → 今と同じ結果 | `outputs.json` の形が違う → 載せずに注意 |
