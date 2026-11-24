# #4104 の詳細設計: 即時修正の MVV 判定と、CI が確かめた PR の受け渡し

設計文書 [issue-4104-design.md](./issue-4104-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

| 別ファイルにした理由 | 関数 |
| --- | --- |
| 新設する関数が 4 つ | `read_pr_target`・`PrTarget.item`・`records/cli.py` の `_fix_result`・`core/plan/records.py` の `approved_after` |
| シグネチャを変える関数と型が 11 個 | `gate`・`_judged`・`pr_number`・`note`・`GitHub.merge`・`_merge_with`・`_preflight`・`_enqueue`・`chosen.record`・`transition_decide.Choice`・`transition_decide.Outcome`（`ndf ci watch` の引数 `--pr-of`・`--out` は CLI の追加で、関数のシグネチャは変えない） |

前提は #3959 の実装が main へ入った後の形である。使う部品は、#3959 の確定仕様 [ndf-mvv-gate-every-pace.md](../docs/specifications/ndf-mvv-gate-every-pace.md) の `gate`・「処理の流れ」の判定の本体（`_judged`）の手順 (1)〜(9)・`judgeable`・`CHECKS_BY_GATE`・`refusals(f, gate)`・`may_skip(f, verdict, gate)`・`review_prs`・`snapshot_facts` である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── templates/fix.json                     … ステップに mvv・merge-gate、merge を fix-merge に
│   ├── templates/parts/steps.json             … 部品 fix-mvv・fix-merge（新設）、ci（ndf ci watch --pr-of --out を直接打つ）
│   ├── workflow/context.schema.json           … $defs.mvv_gate の gate に fix、judgment・target・note_url
│   ├── schemas/route-chosen.schema.json       … 選択の記録の任意の ref
│   └── schemas/io/
│       ├── records.args.schema.json           … mvv gate の --gate fix・--base・--branch・--ci-result（fix で必須・design と release で拒否）
│       ├── ci.args.schema.json                … ci merge の --target・--judged（--branch と排他）、ci watch の --pr-of（--pr と排他）・--out
│       └── ci.result.schema.json              … watch の成功の items の PR の項目
├── docs/contracts/commands.md                 … 上の 2 つの引数の表から作り直す
├── src/ndf/
│   ├── contracts/result.py                    … PrTarget・read_pr_target
│   ├── contracts/chosen.py                    … record の ref
│   ├── ci_cli.py                              … _watch・_report・_merge・_parser
│   ├── adapters/github.py                     … merge・_merge_with・_preflight・_enqueue の expect_base
│   ├── core/plan/records.py                   … approved_after（判定の識別子を指す人の承認）
│   ├── core/plan/runner.py                    … gate_result に outputs、Outcome に instance
│   ├── core/plan/steps.py                     … 結果の cause.instance を読む
│   ├── core/plan/transition_decide.py         … Choice の ref、承認の選択が入った選択の ref を引き継ぐ
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
| `src/ndf/ci_cli.py` | 変更 | `ndf ci` の入口 | `contracts.result`・`adapters.github`・`core.plan.records` | モジュール | `_watch`・`_report`・`_merge`・`_parser`（変更） |
| `src/ndf/adapters/github.py` | 変更 | GitHub との接続 | — | `GitHub` | `merge`・`_merge_with`・`_enqueue`（変更）、`_preflight`（変更） |
| `src/ndf/core/plan/records.py` | 変更 | プランの回をまたぐ記録 | — | モジュール | `approved_after`（新設） |
| `src/ndf/core/plan/runner.py` | 変更 | プランの実行 | `core.plan.records` | モジュール | `gate_result`・`_perform`（変更） |
| `src/ndf/core/plan/steps.py` | 変更 | ステップを打って結果を読む | — | モジュール | 結果の読み取り（`instance` を足す） |
| `src/ndf/core/plan/transition_decide.py` | 変更 | 遷移の判断 | `contracts.chosen` | `Choice`・`Outcome`（変更） | 結果からの選択・回答からの選択（`ref`） |
| `src/ndf/contracts/chosen.py` | 変更 | 選択の記録の形 | — | モジュール | `record`（変更） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートと即時修正の前の MVV 判定 | `contracts.result`・`progress.gate.checks` | モジュール | 定数 `FIX`（新設）・`gate`・`_judged`・`pr_number`・`has_gate`・`review_prs`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv_gate`・`contracts.result` | モジュール | `_mvv_gate`（変更）・`_fix_result`（新設の内部の関数） |
| `src/ndf/progress/gate/checks.py` | 変更 | 機械の検査と判定の記録 | — | モジュール | `CHECKS_BY_GATE`（変更）・`note`（変更） |
| `contracts/templates/parts/steps.json` | 変更 | プランの部品 | — | — | 部品 `fix-mvv`・`fix-merge`（新設）・`ci`（変更） |
| `contracts/templates/fix.json` | 変更 | 即時修正のプラン | 部品 | — | `steps`・`params`・`summary` |

## CI が確かめた PR

満たす決定: 決定 2・3、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PrTarget` | `contracts/result.py` | 新設 | `NamedTuple(repo: str, number: int, base: str, head: str, head_ref: str)` | CI が確かめた PR の識別（`head_ref` は PR の head のブランチ） |
| `PrTarget.item` | `contracts/result.py` | 新設 | `item(self) -> dict[str, Any]` | 結果 JSON の項目 `{"kind": "pr", "repo", "number", "base", "head", "head_ref"}` を返す |
| `read_pr_target` | `contracts/result.py` | 新設 | `read_pr_target(path: Path) -> PrTarget` | ファイルの最後の空でない行を結果 JSON として読み、`status` が `ok` で、`items` の最初の `kind: "pr"` の項目が 5 つのキーを形どおりに持てば `PrTarget` を返す。ほかは `ValueError`（理由つき） |
| `_watch` | `ci_cli.py` | 変更 | シグネチャは同じ | `--out` があれば、PR を探す前に `os.unlink(out)` で項目を消す（`FileNotFoundError` は無視。リンクはリンクそのものを消し、リンク先を開かない）。ほかの `OSError` なら `stopped`・`precondition`（「前回の CI の結果 <out> を消せない: <理由>」）を標準出力に出して終わり、PR を探さず `--out` に書かない。`--pr-of` があれば `gh pr view <ブランチ> --json number --jq .number` を 1 回打って番号を決め（今の部品のシェルと同じ選び方）、失敗・空・整数でない出力は `stopped`・`precondition`（「<ブランチ> の PR を探せない」）。以降は今のまま。結果の 1 行は標準出力に出し、`--out` があれば `out` と同じディレクトリに `tempfile.mkstemp` で作った新しいファイルへ書いて `os.replace(tmp, out)` で置く（項目の置き換えなので、途中でリンクが置かれてもリンク先を開かない）。書けなければ一時ファイルを消し、`stopped`・`precondition` を標準出力に出す |
| `_report` | `ci_cli.py` | 変更 | シグネチャは同じ | 成功（`kind is None`）の結果の `items` の先頭に `PrTarget(hub.repo, pull.number, pull.base_ref, pull.head_sha, pull.head_ref).item()` を置く。`pull` は `HeadReader` が読み終えた PR（今の呼び出しのとおり `reader.pull`）。失敗の結果は今のまま |

- `read_pr_target`
  - 事後条件: 返した値の `repo` は空でない文字列、`number` は 1 以上の整数（`bool` を除く）、`base` は空でない文字列、`head` は 40 桁の 16 進、`head_ref` は空でない文字列
  - 副作用: ファイルを 1 回読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_watch`（`--out`） | 前回の結果の項目を消せない | 保存先のディレクトリに書けない | `os.unlink` の `OSError` | PR を探さない・`--out` に書かない | `precondition`（終了コード 1）。前回の `ci.json` は残るが、`_merge` の手順 0 が根拠にしない | ディレクトリを書けるようにして `ci` から打ち直す |
| `_watch`（`--pr-of`） | PR を探せない | PR が無い・`gh` の失敗 | `gh` の終了コードと出力 | 結果を `--out` に書く | `precondition`（終了コード 1）。`ci.json` は失敗の結果 | プランを `ci` から打ち直す |
| `_watch`（`--out`） | 結果を書けない | ディスクの容量・権限 | 書き込みと `os.replace` の `OSError` | 一時ファイルを消す。`ci.json` は始めに消したまま無い | `precondition`（終了コード 1） | 同上 |
| `read_pr_target` | ファイルが無い・読めない | CI のステップが走っていない | `OSError` | `ValueError` に包んで返す | 呼び手が `precondition` で止める | プランを `ci` から打ち直す |
| `read_pr_target` | 空のファイル・JSON でない・`status` が `ok` でない・PR の項目が無いか形が違う | CI が落ちた・PR を探せなかった結果・旧い形のファイル | 形の確かめ | `ValueError` | 同上 | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_watch`（`--pr-of`・`--out`） | 成功の結果を標準出力と `--out` の両方に同じ 1 行で書く。`--pr-of` の番号を `gh pr view` のスタブから読む。試行ごとの外部コマンドは `gh` の 1 回だけ | `--out` が無い → 前回の結果が無くても今と同じ。既存の `ci.json` が読み取り専用の通常のファイル → 消してから新しいファイルを置く。既存の `ci.json` がほかのファイルを指すシンボリックリンク → `gh pr view` のスタブが失敗した試行でも成功した試行でも、リンク先の内容が変わらない（成功の後の `ci.json` はリンクでない通常のファイル） | `os.unlink` のスタブが `PermissionError`（ディレクトリが書けない）→ `precondition`、`gh` を呼ばず `--out` に書かない。`gh pr view` のスタブの失敗・空の出力 → `precondition` の結果を `--out` に書く。`os.replace` の失敗 → 一時ファイルが残らない。`--pr` と `--pr-of` の併用・どちらも無い → `invalid` |
| `_report` | 成功の結果の `items[0]` が、`HeadReader` が読み直した後の PR の repo・番号・宛先・head・head のブランチ | push の直後で最初に読んだ PR が前のコミットを指す → 読み直した head を書く | 失敗の結果に PR の項目を足さない |
| `read_pr_target` | `ndf ci watch` の成功の出力の 1 行から読める | 項目の後ろにチェックの項目が並ぶ | `status: stopped`・項目が無い・`number` が文字列・`head` が 39 桁・`head_ref` が無い・空のファイル → `ValueError` |

## 判定の区分 `fix`

満たす決定: 決定 1・2・3・6、受け入れ条件 1・2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FIX` | `records/mvv_gate.py` | 新設 | `FIX = "fix"` | 即時修正の区分の名前 |
| `gate` | `records/mvv_gate.py` | 変更 | `gate(root, gate_name, inputs, hub, ask=llm.ask, expect: Optional[PrTarget] = None) -> dict` | `fix` では `expect` が必須（無ければ「CI が確かめた PR が無い」で `verdict: null`・`head` 無し）。`has_gate` を通さない。ほかは #3959 のまま |
| `_judged` | `records/mvv_gate.py` | 変更 | `_judged(root, gate_name, inputs, open_hub, ask, expect)` | 下の「処理の手順」 |
| `pr_number` | `records/mvv_gate.py` | 変更 | `pr_number(root, gate, inputs, hub, expect: Optional[PrTarget] = None) -> int` | `fix` はブランチで探さず `expect.number` を返す。`expect.repo != hub.repo`・`expect.base != inputs["base"]`・`expect.head_ref != inputs["branch"]` なら `GateError`（「CI が確かめた #<n>（<repo>・<head_ref> から <base> 宛て）は判定する <repo'> の <branch'> から <base'> 宛ての PR でない」）。API を呼ばずに照らす。`design`・`release` は #3959 のまま |
| `review_prs` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `fix` は `[number]`（`design` と同じ） |
| `CHECKS_BY_GATE` | `progress/gate/checks.py` | 変更 | キー `fix` を足す | 検査 1・4・5・6・7（2 のスプリントの参照と 3 のモードは即時修正に無い） |
| `note` | `progress/gate/checks.py` | 変更 | #3959 のシグネチャに `target: Optional[PrTarget] = None` を足す | 見出しの区分名を `gate == "fix"` なら「即時修正のマージ」、ほかは `model.gate_label(gate)` にする（`fix` は承認ゲートの区分の表 `workflow/progress.json` の `gates` に足さない）。`target` があれば見出しの直後に「判定した PR: <repo>#<number>（<base> 宛て・head <40 桁>）」の行を置く（`fix` は常に渡す）。理由の小見出しは `fix` では「マージへ進めない理由」 |
| `_recorded` | `records/mvv_gate.py` | 変更 | #3959 のまま | `fix` では `note(..., target=expect)` を書き、`Publisher.comment` が返す `html_url` を出力の `note_url` に置く。書けなかったときは #3959 のとおり `noted: false` で `note_url` を置かない |
| `mvv gate` の引数 | `contracts/schemas/io/records.args.schema.json` | 変更 | `--gate` の `enum` に `fix`。`--base`・`--branch`・`--ci-result`（どれも文字列）を `properties` に足す（`additionalProperties: false` のまま） | `allOf` の `if`/`then` で、`--gate` が `fix` なら `--base`・`--branch`・`--ci-result` を `required` にし `--machine` を拒否（`not: {required: ["--machine"]}`）、`design`・`release` なら 3 つのどれも拒否する。`records/cli.py` の `_mvv_gate` の `invalid` の 2 行と同じ条件 |
| `$defs.mvv_gate` | `contracts/workflow/context.schema.json` | 変更 | `gate` の `enum` に `fix`。任意のキー `judgment`（32 桁の 16 進）・`target`（`PrTarget.item()` の形 `{"kind": "pr", "repo", "number", "base", "head", "head_ref"}`。6 つのキーすべて必須・`additionalProperties: false`）・`note_url`（文字列）を足す | `skip` の説明に「`fix` では人の回答を待たずにマージへ進めるか」を足す。`judgment` は区分 `fix` のどの終わり方にも置き、`target` は判定の本体が PR を読み終えた終わり方に置き、`note_url` は今回の記録を書けたときだけ置く |

### 処理の手順

`_judged` の `fix` の分岐（#3959 の確定仕様の「処理の流れ」にある手順の番号で書く。書かない手順は #3959 のまま）:

| 手順 | `fix` の振る舞い |
| --- | --- |
| (1) | `pr_number(..., expect)` で番号を決め、`hub.pull` を 1 回読む。`pull.head_sha != expect.head` か `pull.base_ref != expect.base` なら `GateError`（「CI が確かめた #<n>（<base> 宛て・<a>）と PR の今（<base'> 宛て・<b>）が違う」）。`gate` が受けて `verdict: null`・`head` 無し・コメント無しで返す |
| (2)・(4) の読み取りの失敗 | #3959 のまま。ベースか差分を読めなければ `verdict: null`・(1) の `head` で止まり、PR を読み直さずに失敗の記録をコメントに書く（`base_unreadable`・`diff_unreadable`）。書けたときは `_recorded` が `noted` を真にし `note_url` を置くので、`_fix_result` は記録のある進めない判定（`refused`）に分け、承認待ちの結果に `note_url` が出る。書けなければ `noted: false` で `precondition` |
| (3) | `judgeable` が理由を返したとき、ベースの MVV の状態（`snap.state`）が `absent`・`unapproved` なら `skip: true`・`why`: 「MVV が承認されていないので判定しない」で返す（コメントを書かない）。`mismatch` なら #3959 のとおり `skip` 偽・`noted: false` |
| (4) | 区分 `fix` だけの追加の処理として、差分を読んだ直後に `hub.pull` を 1 回読み直す（#3959 は差分を読んだ直後には読み直さない）。即時修正では CI が確かめた PR から動いたときに LLM を起動しないためで、PR の読み取りが判定 1 回につき 1 回増える。読み直した head か宛先が (1) と違うとき・読み直しが `GitHubError` のときは、LLM を呼ばずコメントを書かずに返し、出力に `noted: false` を明示する（今回の判定の記録が PR に無いので、`_fix_result` が `refused` でなく `precondition` に分ける。`noted` が無いと `head` を持つ出力は `refused` になり、今回のコメントが無いまま人に承認を求める） |
| (5)〜(9) | #3959 のまま。検査は `CHECKS_BY_GATE["fix"]`、保留は `review_prs(FIX, number)`。(7) で LLM が回答しない（`GateError`）ときも #3959 のとおり (7) が受けて `verdict: null`・(1) の `head` のまま (9) で記録を書くので、記録のある進めない判定（`refused`）になる。`gate` が `GateError` を受けて `head` 無しにするのは (1) までの失敗だけである |
| 出力 | 判定の本体が (1) を読み終えた終わり方は、どれも `target` に `expect.item()` をそのまま置く（`head_ref` を含む 6 つのキー。(1) で `expect` と同じと確かめた値で、項目を組み直さない。手順 3 のマージがこの値と CI の項目の完全一致を照らす）。`note_url` は今回の記録を PR に書けたとき（(2)・(4) の失敗のコメントか (9) のコメント）だけ |
| 省くか | `skip` は「理由が空・判定がコントラクトの形・『従う』・レッドラインが空」で真。進め方（`inputs["pace"]`）とモードを読まない |

即時修正では、手順 (4) で差分を読んだ直後に PR を読み直す。これは #4104 の設計（ https://github.com/devbasex/ai-plugins/pull/4141 で承認済み。[issue-4104-design.md](./issue-4104-design.md) の「`ndf records mvv gate --gate fix`」の終わり方の表と決定 1）が決めた止まり方で、実装はこれに従う。差分を読めた場合は、この読み直しか手順 (9) の読み直しで PR が動いたと分かった時点で、コメントを書かずに `precondition` で止まる。ベースか差分を読めない場合は、上の表の「(2)・(4) の読み取りの失敗」の行に従い、読み直さずに失敗の記録をコメントに書く。

MVV 判定を承認ゲートの手前で進め方によらず打つ #3959（ https://github.com/devbasex/ai-plugins/issues/3959 ）の設計は、初めは差分を読んだ直後に PR を読み直す形だった。#4104 の設計はそれに合わせて、差分の後の読み直しを即時修正の止まり方に入れた。その後 #3959 は、差分を読む間に PR が動く判定が約 0.17% と小さいため、差分を読んだ直後には読み直さないと決めた（確定仕様の「運用」）。即時修正でも読み直しを外すと、次の表のとおり費用の掛かり方が変わる。外すかどうかは、#4104 の設計を変えることになるので、この詳細設計では決めない。

| 案 | PR が動いていない判定（ほとんどの判定） | 差分を読む間に PR が動いた判定 |
| --- | --- | --- |
| (4) で読み直す（承認済みの #4104 の設計。この詳細設計が実装する案） | PR の読み取りが判定 1 回につき 1 回増える（実測 約 0.6 秒。トークンは使わない） | LLM を起動せずに止まる |
| (4) で読み直さず (9) だけで止める（#3959 の振る舞い） | 増えない | LLM を 1 回起動した後に (9) で判定を捨てる（`claude -p` の 1 回の費用の中央値 $0.072 と判定の所要時間） |

`records/cli.py` の `_mvv_gate`:

| 条件 | 結果 |
| --- | --- |
| `--gate fix` で `--base`・`--branch`・`--ci-result` のどれかが無い・`--machine` を渡した | `stopped`・`invalid` |
| `--gate design`・`release` で `--base`・`--branch`・`--ci-result` のどれかを渡した | `stopped`・`invalid` |
| `read_pr_target(--ci-result)` が `ValueError` | `stopped`・`precondition`（「MVV 判定を打てない: CI が確かめた PR が無い: <理由>」）。判定の本体を呼ばない |
| `gate(root, FIX, {"base": --base, "branch": --branch}, _hub, expect=target)` の出力 `out` の `skip` が真 | `ok`。`summary` は `out["why"]` が空なら「MVV 判定: マージへ進める」、空でなければその理由（MVV が承認されていない） |
| `out` に `head` が無い・`out.get("noted") is False` | `stopped`・`precondition`（「MVV 判定を打てない: 」と `why`） |
| ほか | `stopped`・`refused`（「MVV 判定でマージへ進めない: 」と `why`、「判定した PR: <repo>#<number>（<base> 宛て・head <head>）」と「判定の記録: <note_url>」）。`cause.instance` は `judgment` |

`--gate fix` の入口は、引数を確かめた後・CI の PR を読む前に `judgment = uuid.uuid4().hex` を作り、どの行（`invalid` を除く）の `outputs.mvv_gate` にも `judgment` を置く。打つたびに新しい値なので、同じ PR・宛先・head の再判定でも前回と違う。`refused` の `cause.instance` に置いた値は、プランの実行が `mvv` → `merge-gate` の選択の記録の `ref` に写し、`merge-gate` の人の回答の選択がそれを引き継ぐ（下の「マージ」の `approved_after`）。

どの行も `outputs.mvv_gate` に `out` を添え、`items` の先頭に `out["target"]` があれば置く（`invalid` と判定の本体を呼ばない `precondition` を除く）。`gate` は手順 (1) で PR を CI の項目と同じと確かめた後にだけ `target` を置くので、(1) までの失敗（`pr_number` の `GateError`・PR を読めない・head か宛先の違い）の `precondition` は `target` も `items` の PR の項目も持たない。入口は `out` に `target` が無いとき CI の項目で補わない。原因は `_fix_result(out)` が `out` の `skip`・`head`・`noted` だけから決め、MVV の状態を読み直さない。部品 `fix-mvv` は出力を `{state_dir}/mvv.json` に `tee` で書き、マージの根拠にする（下の「マージ」）。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pr_number`（`fix`） | CI の PR のリポジトリ・宛先・head のブランチが判定の対象と違う | `gh pr view <ブランチ>` が別の宛先の PR を選んだ・`gh` の既定のリポジトリが origin と違い、同じ番号の別のブランチの PR を CI が読んだ（決定 3） | `GateError` | `gate` が `verdict: null`・`head` 無しで返す | `precondition`（終了コード 1）。コメント無し | 余分な PR を閉じ、プランを `ci` から打ち直す |
| `_judged`（`fix` の (1)） | CI の後に push・宛先の変更があった | 同じ作業場所からの push・人の宛先の変更 | (1) の比較 | 同上 | 同上 | プランを `ci` から打ち直す（新しい head で CI と判定をやり直す） |
| `_judged`（`fix` の (4)） | 差分を読む間に head・宛先が動いた・差分の後に PR を読み直せない | 判定の最中の push・宛先の変更・GitHub が回答しない | `fix` の (4) で足す、差分を読んだ直後の読み直し | `head` あり・コメント無し・`noted: false` | `precondition`（人の回答を待たない） | 動いたときはプランを `ci` から、読み直せないときは判定のステップから打ち直す |
| `_judged`（(7)） | LLM が回答しない | LLM の失敗 | #3959 の (7) の `GateError` | `verdict: null`・`head` あり・(9) で記録を書く | `refused`（記録を読んで人が答える） | 人が却下してから打ち直すか、承認する |
| `_judged`（`fix` の (3)） | ベースの MVV のハッシュが承認の記録と違う | ベースの MVV の本文を承認なしに変えた | `judgeable` と `snap.state` | `skip` 偽・`noted: false` | `precondition` | MVV を承認し直してから打ち直す |
| `_judged`（(9)） | 判定の記録を PR に書けない | GitHub が回答しない | `_recorded` | `noted: false` | `precondition`（人の回答を待たない。コメントの無い理由で承認させない） | `ndf plan next <プラン>` で判定のステップから打ち直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate`（`fix`） | 判定のスタブが「従う」・レッドライン空・保留無し → `skip` 真、コメント 1 件、`target` と `note_url` あり。PR の検索の API を呼ばない | 入力に `pace`・`mode` が無くても判定する。ベースの MVV が `absent` → 判定のスタブを呼ばず `skip` 真・コメント無し | レッドラインのパス・MVV の設定の変更・保留・「従わない」→ `skip` 偽・コメント 1 件。LLM のスタブが `GateError` → `verdict: null`・`head` あり・コメント 1 件・`noted` 真・`note_url` あり（投稿に成功したときの #3959 の `_recorded` の共通の形）。ベースの取得か差分の読み取りが失敗 → `verdict: null`・`head` あり・PR を読み直さずにコメント 1 件・`noted` 真・`note_url` あり・LLM を呼ばない。同じ失敗でコメントの投稿も失敗 → `noted: false`・`note_url` 無し。差分の後の読み直しで head か宛先が動いた・読み直しが `GitHubError` → `noted: false`・コメント無し・LLM を呼ばない。`expect` の head か宛先が今の PR と違う・(1) の `hub.pull` が `GitHubError` → `head` 無し・`target` 無し・判定のスタブもコメントも呼ばない。`expect` が無い → `head` 無し・`target` 無し |
| `pr_number`（`fix`） | `expect.number` | 同じブランチの別の宛先の開いた PR があっても `expect.number` | `expect.repo`・`expect.base`・`expect.head_ref` が違う → `GateError`（PR を読まない） |
| `_mvv_gate`（`fix`） | `skip` 真 → 終了コード 0、`mvv_gate.judgment` が 32 桁の 16 進、`mvv_gate.target` が `ci.json` の項目と完全に一致（`head_ref` を含む）。部品 `fix-mvv` が渡す引数（`--gate fix --base --branch --ci-result`）を `records.args.schema.json` が受ける | MVV が承認されていない → 0。同じ入力で 2 回打つ → `judgment` が違う | 「従わない」→ 1・`refused`・`summary` に PR・宛先・head とコメントの URL・`cause.instance` が `mvv_gate.judgment`。LLM の無回答 → 1・`refused`。ベースか差分を読めずにコメントを書けた → 1・`refused`・`summary` に判定の記録の URL（`note_url`）。コメントを書けなかった → 1・`precondition`。差分の後に head が動いた・読み直せない → 1・`precondition`。`noted: false`・`head` 無し → 1・`precondition`。`ci.json` が無い・空・失敗の結果 → `precondition` で判定の本体を呼ばない。項目の `head_ref` が `--branch` と違う・項目のリポジトリか宛先が違う・(1) で PR を読めない・読んだ PR の head か宛先が項目と違う → `precondition`・`mvv_gate.target` 無し・`items` に PR の項目無し・判定のスタブもコメントも呼ばない。`--base` 無し・`--branch` 無し・`--machine` 併用・`--gate design` に `--branch` → `invalid`（引数のスキーマも同じく拒む） |
| `note`（`fix`） | 見出しが「即時修正のマージ」で、直後に判定した PR・宛先・head の行 | 同じ PR を別の head で判定し直す → 新しいコメントの見出しの head が新しい値 | — |

## マージ

満たす決定: 決定 2・4、受け入れ条件 2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_preflight` | `adapters/github.py` | 変更 | `_preflight(pull, number, expect_head, expect_base: Optional[str] = None)` | `expect_base` が与えられたときは、宛先 → head → マージ済み → 残リスクの順に見る。宛先が違えば `MergeOutcome(False, "refused", "#<n> の宛先が <expect_base> でない（今は <base_ref>）", pull.head_sha)`、head が違えば `MergeOutcome(False, "refused", "#<n> の HEAD が <expect_head> でない（今は <head_sha>）", …)`（マージ済みでも。同じ宛先へ別の head がマージされたものを成功にしない）、両方同じでマージ済みなら `MergeOutcome(True, None, "#<n> は既にマージ済み", …)`。`expect_base` が無ければ今の順（マージ済み → head → 残リスク） |
| `_merge_with` | `adapters/github.py` | 変更 | `_merge_with(number, expect_head, method, route, expect_base: Optional[str] = None)` | `expect_base` があるときは、(a) 方式を先に `allowed_merge_methods(expect_base)` で選び、(b) PR を 1 回読んで `_preflight` に渡し、(c) 間に API を挟まずに `route.send` を打つ。405 で断られたら `_enqueue(pull, required, jump, expect_base)`。(d) 直接のマージが通ったか投入したら `self.pull(number)` を 1 回読み直し、`base_ref != expect_base` なら `MergeOutcome(False, "refused", "マージの要求の間に #<n> の宛先が <今> へ変わった（<expect_base> 宛てとして照らした）", …)`（キューに入れていれば `dequeue`）。`expect_base` が無ければ今の順のまま（PR を読んでから方式を選ぶ） |
| `_enqueue` | `adapters/github.py` | 変更 | `_enqueue(pull, method, jump=False, expect_base: Optional[str] = None)` | `expect_base` があるとき、`_MERGE_QUEUE_STATE` の同じ 1 回の読み取りで `pullRequest` の `baseRefName`・`headRefOid` も読み、`expect_base`・`pull.head_sha` と違えば投入せずに `refused` を返す |
| `merge` | `adapters/github.py` | 変更 | `merge(number, expect_head=None, method=None, require_method=False, jump=False, expect_base: Optional[str] = None)` | `expect_base` を `_merge_with` へ渡す。省けば今と同じ。`merge_as_admin` は変えない |
| `approved_after` | `core/plan/records.py` | 新設 | `approved_after(state_dir: Path, judge: str, gate: str, to: str, ref: str) -> bool` | 進捗記録（`recorded_choices`）で、`from` が `judge` の最後の選択が `to: gate` かつ `ref == ref` で、それより後に `from: gate`・`to: to`・`chooser` が人・`ref == ref` の選択があれば真。記録が無い・最後の選択が別の行き先か別の `ref`（再判定の結果を書いた後、追記の前に中断した）・後の人の選択が `end`（却下）か別の `ref` なら偽 |
| `record` | `contracts/chosen.py` | 変更 | `record(run_id, rule, chooser, frm, to, cause=None, ref: Optional[str] = None)` | `ref` があれば記録に `"ref"` を置く。`route-chosen.schema.json` に任意の文字列 `ref`（「選択のもとになったステップの結果の `cause.instance`、承認の回答ではそのステップへ入った選択の `ref`」）を足す |
| `Choice`・`Outcome` | `core/plan/transition_decide.py` | 変更 | `Choice` に `ref: Optional[str] = None`、`Outcome` に `instance: Optional[str] = None` | 結果からの選択は `Outcome.instance` を `ref` に置く。承認の回答からの選択（`RULE_ANSWER`）は、`state.choices` のうち `to` がその承認のステップの最後の選択の `ref` を置く。`runner._perform` は `run_step` の結果の `instance` を `Outcome` に渡し、`steps` の結果の読み取りは `cause.instance` を `instance` として返す |
| `_merge` | `ci_cli.py` | 変更 | シグネチャは同じ | `--target` があれば下の手順、無ければ今のまま（`--branch`） |
| `_parser` | `ci_cli.py` | 変更 | — | `watch` の `--pr` と `--pr-of` を必須の排他の組にし、任意の `--out` を足す（`--pr` の `required=True` を外す）。`merge` の `--branch` と `--target` を必須の排他の組にする。`--target` と `--judged` は組で（片方だけは `invalid`）、`--reviewed` とは同時に渡せない（`invalid`） |

`_merge` の `--target` の手順:

| 手順 | 振る舞い |
| --- | --- |
| 0 | `Path(args.target).is_symlink()` か、`os.access(Path(args.target).parent, os.W_OK \| os.X_OK)` が偽なら `stopped`・`precondition`（「CI の結果 <path> は次の CI の試行が消せない場所にある」）でマージしない。部品 `ci` の初期化（`_watch` の `os.unlink`）が失敗するのはディレクトリに書けないときなので、初期化に失敗した試行の後に残った前回の結果を根拠にしない。`ndf ci watch` はリンクを書かないので、リンクはほかの手で置かれたものである |
| 1 | `read_pr_target(args.target)`。`ValueError` なら `stopped`・`precondition`（「CI が確かめた PR が無い: <理由>」）でマージしない |
| 2 | `hub = GitHub(args.repo or 作業場所の origin のリポジトリ, github_mod.client())`（今の `_merge` と同じく `api` に `github_mod.client()` を渡す）。`target.repo != hub.repo` なら `stopped`・`refused` |
| 3 | `args.judged` の最後の空でない行を結果 JSON として読み、`outputs.mvv_gate.target` が `target.item()` と同じかを照らす。読めない・`target` が無いか違う・`cause.kind` が `precondition` なら `stopped`・`refused`（「<repo>#<n>（<base> 宛て・<head>）に対する MVV 判定の通過が無い」）。`status` が `ok` なら次へ。`cause.kind` が `refused` なら、`judgment = outputs.mvv_gate.judgment` が 32 桁の 16 進で、`cause.instance == judgment` で、`approved_after(Path(args.judged).parent, "mvv", "merge-gate", "merge", judgment)` が真のときだけ次へ。どれかが偽なら `stopped`・`refused`（「MVV 判定 <judgment> でマージへ進めないと判定され、その判定への人の承認が無い」）。どれも要求を送らない |
| 4 | `hub.merge(target.number, expect_head=target.head, expect_base=target.base, method=args.method)`。ブランチで PR を探さず、作業場所の HEAD を読まない |
| 5 | 結果は今の `_merge` と同じ形（`items` は `{"kind": "pr", "number": target.number}`） |

判定のステップは打つたびに新しい `judgment` で `mvv.json` を書き直し（部品 `fix-mvv` の `tee`）、`ci` を打ち直せば `ci.json` の head が変わる。どちらの打ち直しの後も、古い判定・古い承認はマージの根拠にならない。`mvv.json` の上書き（ステップの中）と進捗記録の追記（ステップの後の `record_choices`）の間で中断しても、進捗記録に残る前回の選択と承認の `ref` は前回の `judgment` なので、今回の `mvv.json` の判定への承認にはならない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_merge`（`--target`） | 判定の後・人の承認の後に head が進んだ | 同じ作業場所からの push | `_preflight` の head | `refused` | 終了コード 1。`cause.instance` に今の head | プランを `ci` から打ち直す |
| `_preflight` | 宛先が変わった | 人の宛先の変更 | `expect_base` の比較 | `refused`（マージ済みでも） | 同上 | 同上 |
| `_preflight` | 判定の後に別の head が push され、先にマージされた | 人か別の自動処理のマージ | `expect_base` があるときの head の比較（マージ済みより先） | `refused` | 同上 | 人がマージされた変更を確かめる |
| `_merge`（`--target`） | 打ち直しで既にマージ済み | 前の回がマージした後に落ちた | `_preflight` のマージ済み（宛先・head が同じ） | `ok` | 終了コード 0（「既にマージ済み」） | — |
| `_merge`（`--target`） | CI の結果が次の試行で消せない場所にある | 保存先のディレクトリに書けない・ほかの手で `ci.json` にリンクが置かれた（初期化に失敗した試行の後に前回の結果が残り得る） | 手順 0 | `precondition`（要求を送らない） | 終了コード 1 | ディレクトリを書けるようにし、プランを `ci` から打ち直す |
| `_merge`（`--target`） | 判定の通過も承認も無い | 判定が `precondition` で止まった・人が却下した後に `--from merge` で打ち直した | 手順 3 | `refused`（要求を送らない） | 終了コード 1 | プランを `ci` か `mvv` から打ち直す |
| `_merge_with` | 照らした読み取りから要求までの 1 往復の間に宛先を変えた | 書き込みの権限を持つ人の操作 | 要求の後の読み直し | `refused`（直接のマージは戻せないので成功にせず知らせる。キューは外す） | 終了コード 1。`cause.instance` に今の宛先 | 人が入った先を確かめる（決定 4） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_preflight` | `expect_base` が同じ → 今と同じ結果 | `expect_base` を省く → 今と同じ順 | 宛先が違う → マージ済みの PR でも `refused`。同じ宛先へ別の head でマージ済み → `refused`（`ok` にしない） |
| `_merge_with` | 方式を `expect_base` で選んでから PR を 1 回読み、`_preflight` の後に API を挟まずに要求を送る。`--method` が渡る。要求の後に 1 回読み直す | マージキューのある宛先 → 状態の読み取りで宛先・head を照らしてからキューへ入れる | 照らした後・要求の前に宛先が変わる（スタブで要求の後の読み直しの宛先を変える）→ `refused`、キューなら `dequeue` を呼ぶ。キューの状態の読み取りで宛先か head が違う → 投入しない |
| `approved_after` | 判定 → `merge-gate`（`ref` J）、その後に人の `merge-gate` → `merge`（`ref` J）→ J で真 | 判定を打ち直した後の承認が無く、前の回の承認だけある → 偽。前回 J1 の判定と承認だけが記録にあり、`mvv.json` が J2（追記の前に中断）→ J2 で偽 | 記録が無い・人が `end`（却下）を選んだ・承認の `ref` が違う → 偽 |
| `transition_decide`（選択の `ref`） | `cause.instance` を持つ結果からの選択が `ref` を持ち、承認の回答の選択がそのステップへ入った選択の `ref` を持つ | `cause.instance` の無い結果 → `ref` を置かない（記録の形は今と同じ） | — |
| `_merge`（`--target`） | 判定が `ok` の `mvv.json` で、項目の番号の PR に項目の head・宛先を渡す。`pulls_by_head` を呼ばない。判定が `refused` で承認の記録がある → マージする | 作業場所の HEAD が項目の head と違っても PR の head が同じならマージする。判定が `ok` の後にマージが落ち `--from merge` で打ち直す → マージする | ファイルが無い → `precondition`。前回の CI と判定が成功済み（`ci.json` と判定が `ok` の `mvv.json`）のまま、部品 `ci` の打ち直しが `gh pr view` のスタブの失敗で落ちた後 → `ci.json` が失敗の結果に置き換わっているので要求を送らず `precondition`（前回の `ci.json` が読み取り専用でも同じ）。同じ状態で保存先のディレクトリを書けなくして打ち直しの初期化が失敗した後 → 手順 0 で要求を送らず `precondition`。`ci.json` がシンボリックリンク → `precondition`。リポジトリが違う → `refused`。`mvv.json` が無い・`precondition`・`target` が CI の項目と違う・`refused` で承認が無い・却下した・承認が前回の `judgment` を指す → 要求を送らず `refused`。`GitHub` に `github_mod.client()` を渡す（`--target` の経路が `TypeError` で止まらない）。`--reviewed` 併用・`--judged` 無し → `invalid` |

## 即時修正のプラン

満たす決定: 決定 1・2、受け入れ条件 1・3

| ファイル | 変える中身 |
| --- | --- |
| `contracts/templates/parts/steps.json` | 部品 `fix-mvv`: `{"id": "mvv", "run": ["bash", "-c", "set -o pipefail; out=$1; shift; ndf records mvv gate \"$@\" \| tee \"$out\"", "mvv", "{state_dir}/mvv.json", "--gate", "fix", "--base", "{{base}}", "--branch", "{{branch}}", "--ci-result", "{state_dir}/ci.json"]}`（原因は結果 JSON の `cause.kind` から読み、`exit_cause` を置かない。どの終わり方でも `mvv.json` を書き直す）。部品 `fix-merge`: `{"id": "merge", "run": ["ndf", "ci", "merge", "--target", "{state_dir}/ci.json", "--judged", "{state_dir}/mvv.json", "--method", "{{merge_method}}"]}`。`probe` を置かない（決定 4）。共有の部品 `ci`: `run` を `["ndf", "ci", "watch", "--pr-of", "{{branch}}", "--out", "{state_dir}/ci.json", "--budget", "{{ci_budget}}", "--required={{required}}", "--test={{tests}}"]` にし、`bash -c`・`gh pr view`・`tee` のシェルを外す。前回の `ci.json` の削除と結果の書き込みは `_watch` が項目の操作（`os.unlink`・`os.replace`）で行い、リンク先を開かない（決定 2。PR を探せずに落ちた試行の後に前回の成功が根拠に残らない）。部品 `ci` を使う全プランで、試行ごとの外部コマンドは `bash`・`gh`・`ndf`・`tee` から `ndf`・`gh` に減る。`ci.json` を読むほかの部品（`repeat-check`・`test --failed-from`）が読む中身は今と同じ 1 行 |
| `src/ndf/core/plan/runner.py` | `gate_result` が `recorded_outputs(inv.state_dir)` の `outputs` を結果に載せる（読めないときは載せず、今の注意と同じく `warnings` に足す）。即時修正では `mvv_gate` の `target`・`note_url`・`why` が承認待ちの結果に出て、人がどの PR・宛先・head の、どのコメントに答えるかを特定できる |
| `contracts/templates/fix.json` | `steps` を `test`・`{"use": "push", "on_fail": null}`・`pr`・`{"use": "conflict-check", "on_cause": {}}`・`{"use": "ci", "on_cause": {}}`・`{"use": "fix-mvv", "on_cause": {"refused": "merge-gate"}, "next": "merge"}`・`{"use": "merge-gate"}`・`{"use": "fix-merge", "next": "end"}` にする。`conflict-check`・`ci` の `on_cause: {}` は今の `fix.json` と同じ空の上書きのまま残す（共有の部品の `test_failed → repeat-check`・`base_conflict → base-sync` の行き先は即時修正のプランに無いので引き継がない）。`params` に `gate` を足す（`params.json` の既定 `ndf answer ask`）。`summary` に「CI の後に MVV 判定を通し、進めない判定は人の回答を待つ」を足す |
| `skills/development-workflow/references/pace.md`・`conductor-entrypoints.md` | 即時修正もマージの前に MVV 判定を通ること、進めない判定では承認待ちの結果の `mvv_gate.note_url` のコメントを読んで `ndf plan next <プラン> --answer approved|rejected` で答えること、`precondition` で止まったら `--from ci` で打ち直すこと（`--from merge` では判定を飛ばせない）を書く |

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fix.json` | 描画したプランのステップの順が test・push・pr・conflict-check・ci・mvv・merge-gate・merge。`mvv` の成功は `merge`、`refused` は `merge-gate`、`merge-gate` の `approved` は `merge`・`rejected` は `end`。`conflict-check`・`ci` の描画後の `on_cause` が空 | `gate` を渡さなければ既定の `ndf answer ask` | `mvv` の `precondition` はルートを持たず止まる |
| `gate_result` | `outputs.json` があれば承認待ちの結果に `outputs` が載る | `outputs.json` が無い → 今と同じ結果 | `outputs.json` の形が違う → 載せずに注意 |
