# #4104 の詳細設計: 即時修正の MVV 判定と、CI が確かめた PR の受け渡し

設計文書 [issue-4104-design.md](./issue-4104-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

| 別ファイルにした理由 | 関数 |
| --- | --- |
| 新設する関数が 3 つ | `read_pr_target`・`PrTarget.item`・`records/cli.py` の `_fix_result` |
| シグネチャを変える関数が 6 つ | `gate`・`_judged`・`pr_number`・`GitHub.merge`・`_merge_with`・`_preflight` |

前提は #3959 の実装が main へ入った後の形である。使う部品は [issue-3959-4055-design-detail.md](./issue-3959-4055-design-detail.md) の `gate`・`_judged` の手順 (1)〜(9)・`judgeable`・`CHECKS_BY_GATE`・`refusals(f, gate)`・`may_skip(f, verdict, gate)`・`review_prs`・`snapshot_facts` である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── templates/fix.json                     … ステップに mvv・merge-gate、merge を fix-merge に
│   ├── templates/parts/steps.json             … 部品 fix-mvv・fix-merge
│   ├── workflow/context.schema.json           … $defs.mvv_gate の gate に fix
│   └── schemas/io/
│       ├── records.args.schema.json           … mvv gate の --gate fix・--base・--ci-result
│       ├── ci.args.schema.json                … ci merge の --target（--branch と排他）
│       └── ci.result.schema.json              … watch の成功の items の PR の項目
├── docs/contracts/commands.md                 … 上の 2 つの引数の表から作り直す
├── src/ndf/
│   ├── contracts/result.py                    … PrTarget・read_pr_target
│   ├── ci_cli.py                              … _report・_merge・_parser
│   ├── adapters/github.py                     … merge・_merge_with・_preflight の expect_base
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

依存の向き: `ci_cli` と `records/cli` が `contracts/result` の `PrTarget` を読む。`mvv_gate` は `PrTarget` を型として受け取るだけで、ファイルを読まない（読むのは入口の `records/cli`）。`adapters/github` は `PrTarget` を読まず、突き合わせる値（`expect_head`・`expect_base`）を引数で受ける。`contracts` はどれにも依存しないので循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/contracts/result.py` | 変更 | 結果 JSON の組み立てと読み取り | — | `PrTarget`（新設） | `PrTarget.item`・`read_pr_target`（新設） |
| `src/ndf/ci_cli.py` | 変更 | `ndf ci` の入口 | `contracts.result`・`adapters.github` | モジュール | `_report`・`_merge`・`_parser`（変更） |
| `src/ndf/adapters/github.py` | 変更 | GitHub との接続 | — | `GitHub` | `merge`・`_merge_with`（変更）、`_preflight`（変更） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートと即時修正の前の MVV 判定 | `contracts.result`・`progress.gate.checks` | モジュール | 定数 `FIX`（新設）・`gate`・`_judged`・`pr_number`・`has_gate`・`review_prs`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv_gate`・`contracts.result` | モジュール | `_mvv_gate`（変更）・`_fix_result`（新設の内部の関数） |
| `src/ndf/progress/gate/checks.py` | 変更 | 機械の検査と判定の記録 | — | モジュール | `CHECKS_BY_GATE`（変更）・`note`（変更） |
| `contracts/templates/parts/steps.json` | 変更 | プランの部品 | — | — | 部品 `fix-mvv`・`fix-merge`（新設） |
| `contracts/templates/fix.json` | 変更 | 即時修正のプラン | 部品 | — | `steps`・`params`・`summary` |

## CI が確かめた PR

満たす決定: 決定 2・3、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PrTarget` | `contracts/result.py` | 新設 | `NamedTuple(repo: str, number: int, base: str, head: str)` | CI が確かめた PR の識別 |
| `PrTarget.item` | `contracts/result.py` | 新設 | `item(self) -> dict[str, Any]` | 結果 JSON の項目 `{"kind": "pr", "repo", "number", "base", "head"}` を返す |
| `read_pr_target` | `contracts/result.py` | 新設 | `read_pr_target(path: Path) -> PrTarget` | ファイルの最後の空でない行を結果 JSON として読み、`status` が `ok` で、`items` の最初の `kind: "pr"` の項目が 4 つのキーを形どおりに持てば `PrTarget` を返す。ほかは `ValueError`（理由つき） |
| `_report` | `ci_cli.py` | 変更 | シグネチャは同じ | 成功（`kind is None`）の結果の `items` の先頭に `PrTarget(hub.repo, pull.number, pull.base_ref, pull.head_sha).item()` を置く。`pull` は `HeadReader` が読み終えた PR（今の呼び出しのとおり `reader.pull`）。失敗の結果は今のまま |

- `read_pr_target`
  - 事後条件: 返した値の `repo` は空でない文字列、`number` は 1 以上の整数（`bool` を除く）、`base` は空でない文字列、`head` は 40 桁の 16 進
  - 副作用: ファイルを 1 回読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_pr_target` | ファイルが無い・読めない | CI のステップが走っていない | `OSError` | `ValueError` に包んで返す | 呼び手が `precondition` で止める | プランを `ci` から打ち直す |
| `read_pr_target` | JSON でない・`status` が `ok` でない・PR の項目が無いか形が違う | CI が落ちた・旧い形のファイル | 形の確かめ | `ValueError` | 同上 | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_report` | 成功の結果の `items[0]` が、`HeadReader` が読み直した後の PR の repo・番号・宛先・head | push の直後で最初に読んだ PR が前のコミットを指す → 読み直した head を書く | 失敗の結果に PR の項目を足さない |
| `read_pr_target` | `ndf ci watch` の成功の出力の 1 行から読める | 項目の後ろにチェックの項目が並ぶ | `status: stopped`・項目が無い・`number` が文字列・`head` が 39 桁・空のファイル → `ValueError` |

## 判定の区分 `fix`

満たす決定: 決定 1・2・3・6、受け入れ条件 1・2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FIX` | `records/mvv_gate.py` | 新設 | `FIX = "fix"` | 即時修正の区分の名前 |
| `gate` | `records/mvv_gate.py` | 変更 | `gate(root, gate_name, inputs, hub, ask=llm.ask, expect: Optional[PrTarget] = None) -> dict` | `fix` では `expect` が必須（無ければ「CI が確かめた PR が無い」で `verdict: null`・`head` 無し）。`has_gate` を通さない。ほかは #3959 のまま |
| `_judged` | `records/mvv_gate.py` | 変更 | `_judged(root, gate_name, inputs, open_hub, ask, expect)` | 下の「処理の手順」 |
| `pr_number` | `records/mvv_gate.py` | 変更 | `pr_number(root, gate, inputs, hub, expect: Optional[PrTarget] = None) -> int` | `fix` はブランチで探さず `expect.number` を返す。`expect.repo != hub.repo`・`expect.base != inputs["base"]` なら `GateError`（「CI が確かめた #<n>（<repo>・<base> 宛て）は判定する <repo'> の <base'> 宛ての PR でない」）。`design`・`release` は #3959 のまま |
| `review_prs` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `fix` は `[number]`（`design` と同じ） |
| `CHECKS_BY_GATE` | `progress/gate/checks.py` | 変更 | キー `fix` を足す | 検査 1・4・5・6・7（2 のスプリントの参照と 3 のモードは即時修正に無い） |
| `note` | `progress/gate/checks.py` | 変更 | シグネチャは #3959 のまま | 見出しの区分名を `gate == "fix"` なら「即時修正のマージ」、ほかは `model.gate_label(gate)` にする（`fix` は承認ゲートの区分の表 `workflow/progress.json` の `gates` に足さない）。理由の小見出しは `fix` では「マージへ進めない理由」 |
| `$defs.mvv_gate` | `contracts/workflow/context.schema.json` | 変更 | `gate` の `enum` に `fix` | `skip` の説明に「`fix` では人の回答を待たずにマージへ進めるか」を足す |

### 処理の手順

`_judged` の `fix` の分岐（#3959 の手順の番号で書く。書かない手順は #3959 のまま）:

| 手順 | `fix` の振る舞い |
| --- | --- |
| (1) | `pr_number(..., expect)` で番号を決め、`hub.pull` を 1 回読む。`pull.head_sha != expect.head` か `pull.base_ref != expect.base` なら `GateError`（「CI が確かめた #<n>（<base> 宛て・<a>）と PR の今（<base'> 宛て・<b>）が違う」）。`gate` が受けて `verdict: null`・`head` 無し・コメント無しで返す |
| (3) | `judgeable` が理由を返したとき、ベースの MVV の状態（`snap.state`）が `absent`・`unapproved` なら `skip: true`・`why`: 「MVV が承認されていないので判定しない」で返す（コメントを書かない）。`mismatch` なら #3959 のとおり `skip` 偽・`noted: false` |
| (4)〜(9) | #3959 のまま。検査は `CHECKS_BY_GATE["fix"]`、保留は `review_prs(FIX, number)` |
| 省くか | `skip` は「理由が空・判定がコントラクトの形・『従う』・レッドラインが空」で真。進め方（`inputs["pace"]`）とモードを読まない |

`records/cli.py` の `_mvv_gate`:

| 条件 | 結果 |
| --- | --- |
| `--gate fix` で `--base` か `--ci-result` が無い・`--machine` を渡した | `stopped`・`invalid` |
| `--gate design`・`release` で `--base` か `--ci-result` を渡した | `stopped`・`invalid` |
| `read_pr_target(--ci-result)` が `ValueError` | `stopped`・`precondition`（「MVV 判定を打てない: CI が確かめた PR が無い: <理由>」）。判定の本体を呼ばない |
| `gate(root, FIX, {"base": --base}, _hub, expect=target)` の出力 `out` の `skip` が真 | `ok`。`summary` は `out["why"]` が空なら「MVV 判定: マージへ進める」、空でなければその理由（MVV が承認されていない） |
| `out` に `head` が無い・`out.get("noted") is False` | `stopped`・`precondition`（「MVV 判定を打てない: 」と `why`） |
| ほか | `stopped`・`refused`（「MVV 判定でマージへ進めない: 」と `why`） |

どの行も `outputs.mvv_gate` に `out` を添える（`invalid` と判定の本体を呼ばない `precondition` を除く）。原因は `_fix_result(out)` が `out` の `skip`・`head`・`noted` だけから決め、MVV の状態を読み直さない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pr_number`（`fix`） | CI の PR のリポジトリか宛先が判定の対象と違う | `gh pr view <ブランチ>` が別の宛先の PR を選んだ（決定 3） | `GateError` | `gate` が `verdict: null`・`head` 無しで返す | `precondition`（終了コード 1）。コメント無し | 余分な PR を閉じ、プランを `ci` から打ち直す |
| `_judged`（`fix` の (1)） | CI の後に push・宛先の変更があった | 同じ作業場所からの push・人の宛先の変更 | (1) の比較 | 同上 | 同上 | プランを `ci` から打ち直す（新しい head で CI と判定をやり直す） |
| `_judged`（`fix` の (3)） | ベースの MVV のハッシュが承認の記録と違う | ベースの MVV の本文を承認なしに変えた | `judgeable` と `snap.state` | `skip` 偽・`noted: false` | `precondition` | MVV を承認し直してから打ち直す |
| `_judged`（(9)） | 判定の記録を PR に書けない | GitHub が回答しない | `_recorded` | `noted: false` | `precondition`（人の回答を待たない。コメントの無い理由で承認させない） | `ndf plan next <プラン>` で判定のステップから打ち直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate`（`fix`） | 判定のスタブが「従う」・レッドライン空・保留無し → `skip` 真、コメント 1 件。PR の検索の API を呼ばない | 入力に `pace`・`mode` が無くても判定する。ベースの MVV が `absent` → 判定のスタブを呼ばず `skip` 真・コメント無し | レッドラインのパス・MVV の設定の変更・保留・「従わない」→ `skip` 偽・コメント 1 件。`expect` の head か宛先が今の PR と違う → `head` 無し・判定のスタブもコメントも呼ばない。`expect` が無い → `head` 無し |
| `pr_number`（`fix`） | `expect.number` | 同じブランチの別の宛先の開いた PR があっても `expect.number` | `expect.repo`・`expect.base` が違う → `GateError` |
| `_mvv_gate`（`fix`） | `skip` 真 → 終了コード 0 | MVV が承認されていない → 0 | 「従わない」→ 1・`refused`。`noted: false`・`head` 無し → 1・`precondition`。`ci.json` が無い・失敗の結果 → `precondition` で判定の本体を呼ばない。`--base` 無し・`--machine` 併用 → `invalid` |
| `note`（`fix`） | 見出しが「即時修正のマージ」 | — | — |

## マージ

満たす決定: 決定 4、受け入れ条件 2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_preflight` | `adapters/github.py` | 変更 | `_preflight(pull, number, expect_head, expect_base: Optional[str] = None)` | `expect_base` が与えられ `pull.base_ref` と違えば、マージ済みかを見る前に `MergeOutcome(False, "refused", "#<n> の宛先が <expect_base> でない（今は <base_ref>）", pull.head_sha)`。ほかは今の順（マージ済み → head → 残リスク） |
| `_merge_with` | `adapters/github.py` | 変更 | `_merge_with(number, expect_head, method, route, expect_base: Optional[str] = None)` | 読んだ 1 回の `pull` を `_preflight` に `expect_base` とともに渡す。方式とキューはその `pull` の宛先で選ぶ（今のまま） |
| `merge` | `adapters/github.py` | 変更 | `merge(number, expect_head=None, method=None, require_method=False, jump=False, expect_base: Optional[str] = None)` | `expect_base` を `_merge_with` へ渡す。省けば今と同じ。`merge_as_admin` は変えない |
| `_merge` | `ci_cli.py` | 変更 | シグネチャは同じ | `--target` があれば下の手順、無ければ今のまま（`--branch`） |
| `_parser` | `ci_cli.py` | 変更 | — | `merge` の `--branch` と `--target` を必須の排他の組にする。`--target` と `--reviewed` を同時に渡すと `invalid` |

`_merge` の `--target` の手順:

| 手順 | 振る舞い |
| --- | --- |
| 1 | `read_pr_target(args.target)`。`ValueError` なら `stopped`・`precondition`（「CI が確かめた PR が無い: <理由>」）でマージしない |
| 2 | `hub = GitHub(args.repo or 作業場所の origin)`。`target.repo != hub.repo` なら `stopped`・`refused` |
| 3 | `hub.merge(target.number, expect_head=target.head, expect_base=target.base, method=args.method)`。ブランチで PR を探さず、作業場所の HEAD を読まない |
| 4 | 結果は今の `_merge` と同じ形（`items` は `{"kind": "pr", "number": target.number}`） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_merge`（`--target`） | 判定の後・人の承認の後に head が進んだ | 同じ作業場所からの push | `_preflight` の head | `refused` | 終了コード 1。`cause.instance` に今の head | プランを `ci` から打ち直す |
| `_preflight` | 宛先が変わった | 人の宛先の変更 | `expect_base` の比較 | `refused`（マージ済みでも） | 同上 | 同上 |
| `_merge`（`--target`） | 打ち直しで既にマージ済み | 前の回がマージした後に落ちた | `_preflight` のマージ済み | `ok` | 終了コード 0（「既にマージ済み」） | — |
| `_merge`（`--target`） | 読んでから要求までに宛先を変えた | 書き込みの権限を持つ人の操作 | 検出しない（STD1） | 代償として残す（決定 4） | — | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_preflight` | `expect_base` が同じ → 今と同じ結果 | `expect_base` を省く → 今と同じ | 宛先が違う → マージ済みの PR でも `refused` |
| `_merge_with` | PR を 1 回だけ読み、突き合わせを通った宛先で方式を選ぶ。`--method` が渡る | マージキューのある宛先 → キューへ入れる（今のまま） | — |
| `_merge`（`--target`） | 項目の番号の PR に項目の head・宛先を渡す。`pulls_by_head` を呼ばない | 作業場所の HEAD が項目の head と違っても PR の head が同じならマージする | ファイルが無い → `precondition`。リポジトリが違う → `refused`。`--reviewed` 併用 → `invalid` |

## 即時修正のプラン

満たす決定: 決定 1、受け入れ条件 1・3

| ファイル | 変える中身 |
| --- | --- |
| `contracts/templates/parts/steps.json` | 部品 `fix-mvv`: `{"id": "mvv", "run": ["ndf", "records", "mvv", "gate", "--gate", "fix", "--base", "{{base}}", "--ci-result", "{state_dir}/ci.json"]}`（原因は結果 JSON の `cause.kind` から読み、`exit_cause` を置かない）。部品 `fix-merge`: `{"id": "merge", "run": ["ndf", "ci", "merge", "--target", "{state_dir}/ci.json", "--method", "{{merge_method}}"]}`。`probe` を置かない（決定 4） |
| `contracts/templates/fix.json` | `steps` を `test`・`push`・`pr`・`conflict-check`・`{"use": "ci", "on_cause": {}}`・`{"use": "fix-mvv", "on_cause": {"refused": "merge-gate"}, "next": "merge"}`・`{"use": "merge-gate"}`・`{"use": "fix-merge", "next": "end"}` にする。`params` に `gate` を足す（`params.json` の既定 `ndf answer ask`）。`summary` に「CI の後に MVV 判定を通し、進めない判定は人の回答を待つ」を足す |
| `skills/development-workflow/references/pace.md`・`conductor-entrypoints.md` | 即時修正もマージの前に MVV 判定を通ること、進めない判定では `ndf plan next <プラン> --answer approved|rejected` で答えること、`precondition` で止まったら `--from ci` で打ち直すことを書く |

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fix.json` | 描画したプランのステップの順が test・push・pr・conflict-check・ci・mvv・merge-gate・merge。`mvv` の成功は `merge`、`refused` は `merge-gate`、`merge-gate` の `approved` は `merge`・`rejected` は `end` | `gate` を渡さなければ既定の `ndf answer ask` | `mvv` の `precondition` はルートを持たず止まる |
