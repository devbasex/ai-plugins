# #4106 の詳細設計: 省いた回答を消費の直前に判定の対象と照合する

設計文書 [issue-4106-design.md](./issue-4106-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。別ファイルにしたのは、新設する関数が 4 つ（`mvv_targets`・`mvv_vouch`・`git_base_tip`・`rewait`）あり、シグネチャを変える関数が 1 つ（`gate_outcome`）あるためである。パスは `plugins/ndf/` からの相対で書く。#3959 と #4105 の実装の後の形を前提にする（設計文書の「文脈と制約」）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/workflow/context.schema.json     … $defs.mvv_gate に pr・base_ref・base_sha
├── src/ndf/domains/records/
│   ├── mvv_material_decide.py                 … gate_outcome に pr・base_ref・base_sha
│   └── mvv_gate.py                            … _judged が読んだ PR と snap.commit を渡す
├── src/ndf/progress/
│   ├── model.py                               … mvv_skip・mvv_answer_void（判定の対象の欄）
│   ├── run.py                                 … Vouch・Env.vouch・rewait・_consume
│   └── gate/
│       ├── asking.py                          … Target.base_ref
│       ├── answering.py                       … github_observe・mvv_vouch・git_base_tip・recheck
│       ├── entry.py                           … _progress が Env.vouch を差し込む
│       └── records.py                         … approved_head が消費を見る
└── tests/progress/
    ├── gate/test_gate_mvv_answer.py *
    └── test_run.py
```

依存の向き: `progress/gate/*` → `progress/run` → `progress/model` の一方向で、今と変わらない。`run.py` は照合関数の型（`Vouch`）だけを持ち、照合関数の中身（GitHub と git を読む）は `gate/answering.py` が作って `gate/entry.py` が `Env` へ渡す。`domains/records/*` は `progress` を読まない（判定の出力の形は `context.schema.json` が持つ）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | `$defs.mvv_gate` | キー `pr`・`base_ref`・`base_sha`（新設） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力の形 | — | モジュール | `gate_outcome`（変更） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの MVV 判定 | `mvv_material_decide` | モジュール | `_judged`（変更） |
| `src/ndf/progress/model.py` | 変更 | ステートマシンの型と規則 | — | モジュール | 定数 `MVV_TARGET_KEYS`（新設）・`mvv_targets`（新設）・`mvv_skip`・`mvv_answer_void`（変更） |
| `src/ndf/progress/run.py` | 変更 | ステートマシンの駆動 | `model`・`store` | `Env`（欄 `vouch`）、モジュール | 型 `Vouch`（新設）・`rewait`（新設）・`_consume`（変更） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認ゲートの質問 | `run`・`model` | `Target`（欄 `base_ref`） | — |
| `src/ndf/progress/gate/answering.py` | 変更 | 承認ゲートの回答の検証 | `asking`・`run`・`model` | モジュール | `github_observe`・`recheck`（変更）・`mvv_vouch`・`git_base_tip`（新設） |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf progress` のエントリポイント | `answering`・`run` | モジュール | `_progress`（変更） |
| `src/ndf/progress/gate/records.py` | 変更 | 回答の読み出し | `run`・`model` | モジュール | `answers`・`approved_head`（変更） |

## 判定の対象を出力に持たせる

満たす決定: 決定 3・6、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `gate_outcome` | `domains/records/mvv_material_decide.py` | 変更 | `gate_outcome(gate, verdict, skip, why, head=None, holds=(), *, pr: Optional[int] = None, base_ref: Optional[str] = None, base_sha: Optional[str] = None) -> dict`（`holds` は #3959 が足す。キーワードだけの 3 つを足す） | 値があるキーだけを出力に置く（無いキーは置かない。今の出力の形と同じ規則） |
| `_judged` | `domains/records/mvv_gate.py` | 変更 | シグネチャは同じ | #3959 の手順 (4) 以降のすべての `gate_outcome` に、(1) で読んだ PR の番号と `pull.base_ref`、(2) の `snap.commit` を渡す。(3)（承認済みの MVV が無い）は渡さない（`skip` が真になりうるのは (8) だけなので照合には足りる） |
| `$defs.mvv_gate` | `contracts/workflow/context.schema.json` | 変更 | 任意のキー `pr`（整数、1 以上）・`base_ref`（文字列、1 文字以上）・`base_sha`（`^[0-9a-f]{40}$`） | `additionalProperties: false` のまま 3 つを足す |

## 省く条件と読まない回答

満たす決定: 決定 6・7、受け入れ条件 5、不変条件 I2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `MVV_TARGET_KEYS` | `progress/model.py` | 新設 | `tuple[str, ...] = ("pr", "base_ref", "base_sha", "head")` | 判定の対象の欄の名前 |
| `mvv_targets` | `progress/model.py` | 新設 | `mvv_targets(held: Mapping[str, Any]) -> Optional[str]` | 判定の出力 `held` の対象の欄が形どおりか（`pr` が整数・`base_ref` と `head` が空でない文字列・`base_sha` が 40 桁の 16 進）。外れれば欠けた欄の名前を並べた理由、そろえば None |
| `mvv_skip` | `progress/model.py` | 変更 | シグネチャは同じ | #3959・#4105 の条件に、`mvv_targets(held)` が None であることを足す。理由があれば None（承認ゲートで人を待つ） |
| `mvv_answer_void` | `progress/model.py` | 変更（#4105 が新設。#4105 より先に入るならこの Issue が新設し、#4105 が `scope` の条件を足す） | `mvv_answer_void(payload: Mapping[str, Any]) -> Optional[str]` | 回答した者が `MVV_SERVICE` で、`payload[MVV_KEY]` が辞書でない、か `mvv_targets` が理由を返すなら、理由「省いた回答が判定の対象（<欠けた欄>）を持たない」。#4105 の印の条件と並べ、先に当たった理由を返す。ほかは None |

- `mvv_answer_void` を読む側（`StepProgress._answered`・`records.answers`）は #4105 が作る。この Issue は読む側を変えない

## 照合関数

満たす決定: 決定 1・2・4、受け入れ条件 1・2・3、不変条件 I1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Target` | `progress/gate/asking.py` | 変更 | 欄 `base_ref: Optional[str] = None` を足す | 観た PR の宛先。`differs`・`as_dict`・`from_dict` は変えない（人の回答の `seen` の形と比べ方は今のまま） |
| `github_observe` | `progress/gate/answering.py` | 変更 | シグネチャは同じ | 今の `hub.pull(q.pr)` の 1 回の結果から `head_sha` に加えて `base_ref` を読み、`Target(head, material, base_ref=base_ref)` を返す。REST の呼び出しは増えない |
| `git_base_tip` | `progress/gate/answering.py` | 新設 | `git_base_tip(cwd: Path) -> run.BaseTip`（`BaseTip = Callable[[str], Optional[str]]` は `run.py` に置く） | 宛先のブランチ名を受け、`Git(cwd).remote()` のリモートへ `git ls-remote -- <リモート> refs/heads/<ブランチ>` を 1 回打ち、参照の名前がちょうど `refs/heads/<ブランチ>` の行の SHA を返す（ls-remote の照合は末尾の一致なので、`refs/heads/x/refs/heads/<ブランチ>` のような行を採らない）。終了コードが 0 でない・行が無いときは None（ブランチが無いと終了コード 0 で出力が空になる。2026-11-22 に `git ls-remote -- origin refs/heads/<無いブランチ>` で確かめた）。関数を作るだけでは git を打たない |
| `mvv_vouch` | `progress/gate/answering.py` | 新設 | `mvv_vouch(observe: Observe, base_tip: run.BaseTip) -> run.Vouch` | 照合関数を作る。作る関数は `(v, m)` を受け、`m.event` の payload の `MVV_KEY`（判定のコピー `held`）を今の PR と宛先の先端に照らし、照合が通らない理由か None を返す（下の手順） |
| `recheck` | `progress/gate/answering.py` | 変更 | シグネチャは同じ | 読む回答の payload が `by: MVV_SERVICE` なら照合せずに None を返す（照合は `_consume` が排他の中で行う。決定 1）。人の回答は今どおり。待ちへ戻す事象の組み立ては `run.rewait` を使う |

- `mvv_vouch` が作る関数
  - 事前条件: 呼ぶ側（`_consume`）が駆動の排他を持ち、`m.action` が `CONSUME`、`m.event` が `by: MVV_SERVICE` の回答
  - 事後条件: 理由が None のときだけ、PR の番号・宛先・head と宛先の先端が判定のコピーと一致している
  - 副作用: REST の PR の読み取り 1 回（`observe`）と `git ls-remote` 1 回（`base_tip`）。事象を書かない

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `mvv_vouch` が作る関数 | `held` が辞書でない・`model.mvv_targets(held)` が理由を返す | 理由「省いた回答が判定の対象を持たない: <欠けた欄>」（`observe` を呼ばない） |
| 〃 | `q = question(v, replace(m, action=GATE))` の `q.pr` が None か `held["pr"]` と違う | 理由「判定した PR #<held.pr> と承認ゲートの PR #<q.pr> が違う」（`observe` を呼ばない） |
| 〃 | `observe(q)` が `model.Refused` を投げる | 理由「PR #<番号> を読めない: <原因>」（例外を外へ出さない） |
| 〃 | `now.base_ref != held["base_ref"]` | 理由「判定した宛先 <held.base_ref> と今の宛先 <now.base_ref> が違う」 |
| 〃 | `now.head != held["head"]` | 理由「判定した head <held.head> と今の head <now.head> が違う」 |
| 〃 | `base_tip(held["base_ref"])` が None か `held["base_sha"]` と違う | 理由「判定が規則を読んだベース <held.base_sha> と今の <宛先> の先端 <SHA か 読めない> が違う」 |
| 〃 | ほか | None |

- 順序の約束: PR の読み取り（`observe`）を先に、宛先の先端（`base_tip`）を後に打つ。PR を読む間に宛先へマージが入っても、後に読む先端に入る（指摘 r6-1）
- 理由の先頭には「MVV 判定で省いた回答を読まない: 」を付ける（呼ぶ側の `_consume` が付ける）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `mvv_vouch` が作る関数 | PR を読めない | GitHub が回答しない・権限 | `observe` の `model.Refused` | 理由を返す | 承認ゲートの待ち（`gate`）。警告に理由 | 人が回答する |
| `mvv_vouch` が作る関数 | 宛先の先端を読めない | `git ls-remote` の失敗・ブランチが無い | `base_tip` が None | 理由を返す | 同上（「読めない」） | 同上 |
| `git_base_tip` | git が失敗する | ネットワーク・リモートの設定 | `GitError` か終了コード | None を返す（例外を外へ出さない） | — | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `git_base_tip` | `Git.run`・`Git.attempt`・`Git.remote`（`adapters/git.py`）、`ls-remote` の既存の呼び出し（`domains/release/gather.py`・`assemble.py`） | `grep -rn "ls-remote" src/ndf` | 使う（`Git.attempt("ls-remote", "--", remote, ref)` の出力の 1 列目）。新しい git の関数は足さない |
| `mvv_vouch` | `recheck` の対象の読み方（`question`・`observe`・`payload_of`） | `answering.py` を読んだ | 使う。`Target.differs` は使わない（人の回答の比べ方を変えないため） |

## 消費の直前の照合

満たす決定: 決定 1・2、受け入れ条件 1・2・3・6、不変条件 I1・I4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Vouch`・`BaseTip` | `progress/run.py` | 新設（型） | `Vouch = Callable[[View, Move], Optional[str]]`・`BaseTip = Callable[[str], Optional[str]]` | 照合関数と宛先の先端を読む関数の型 |
| `Env` | `progress/run.py` | 変更 | 欄 `vouch: Optional[Vouch] = None` を足す | 省いた回答の照合関数。`ndf progress` のエントリポイント（`entry._progress`）だけが置く |
| `rewait` | `progress/run.py` | 新設 | `rewait(v: View, m: Move, why: str) -> model.EventDraft` | 承認ゲートのステップを待ちへ戻す事象（`STEP_EVENT`・`status: waiting`・`report: None`・今の `generation`・`reason: why`）。今の `recheck` の中の組み立てを移し、`recheck` と `_consume` が使う |
| `_consume` | `progress/run.py` | 変更 | シグネチャは同じ | 回答（`m.event`）が `by: MVV_SERVICE` なら、`d.env.vouch` が None なら理由「省いた回答を照合する関数が無い（ndf progress のエントリポイントを通らない駆動）」、あれば `d.env.vouch(v, m)` を 1 回呼ぶ。理由があれば `d.write(rewait(v, m, "MVV 判定で省いた回答を読まない: " + 理由))` を書き、警告に理由を足して None を返す（駆動を続け、次の手は `GATE`）。理由が無ければ、続けて今の承認の読み方で消費する（`finished` を書く）。照合と消費の間に別の読み取りを挟まない。人の回答は今どおりで、照合関数を呼ばない。回答が無いときに省いた回答を書いて `next` を返す今の手は変えない |
| `_progress` | `progress/gate/entry.py` | 変更 | シグネチャは同じ | `run.Env(facts=…, vouch=mvv_vouch(seen, git_base_tip(cwd)))` を `lane_env` で包んで駆動へ渡す。`seen` は今の `github_observe`（エントリポイントの `recheck` と共有し、`GitHub` の接続は 1 つ） |

- 照合関数を呼ぶのは `_drive` を通る `next`（`run.advance`）と `work`（`run.work`）だけである（2026-11-22 に `run.py` の `COMMANDS` と `_drive` の呼び出し元で確かめた）。`show`・`wait`・`resync`・`answer` は `_drive` を通らないので、`Env.vouch` を持っても PR の読み取りも `git ls-remote` も増えない（指摘 r6-3）。`next` が起動する `ndf progress work` の子は `progress_main` → `_progress` を通り、自分の `Env.vouch` を持つ（指摘 r6-2）。エントリポイントを通らずに `run.work` を直接呼ぶ駆動は `Env.vouch` が None で、省いた回答を待ちへ戻す
- 照合が通らないときの `rewait` は `_consume` が書くので、PR を読めないときも例外を外へ出さずに人の承認待ちへ移る（指摘 r6-4）。その後の待ちの表示（`present`）が同じ取得失敗で質問を出せないのは今のどの承認ゲートでも同じで、この Issue の範囲の外（要求の「含まない」）

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じステートマシンへの `next` と `work` の子 | ステートマシンの事象の表 | `_consume` は `store.run_lock` の中で照合と消費（か待ちへ戻す事象）を続けて書く | 照合の途中で落ちたら事象は書かれず、次の駆動が同じ回答を照合し直す。待ちへ戻した後は `prog.waiting` があり、同じ回答を 2 度照合しない |
| `next` と `ndf answer`（人の回答） | 同上 | `ndf answer` は今どおり。人の回答が書かれた後の `decide` は人の回答を読むので照合関数を呼ばない | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_consume` | 待ちへ戻した理由 | 事象の `reason`・結果 JSON の `warnings` | トークン・認証情報（理由に載るのは PR の番号・ブランチ名・SHA だけ） |

## 承認済みの head

満たす決定: 決定 5、受け入れ条件 4、不変条件 I3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `approved_head` | `progress/gate/records.py` | 変更 | シグネチャは同じ | 区分 `kind` の最後の回答が `by: MVV_SERVICE` なら、その回答の事象番号を `report.metrics.seq` に持つ `STEP_EVENT` の `finished`（`gate_report` が今も書く）が事象の表にあるときだけ head を返し、無ければ None。人の回答は今どおり（文書だけの差で進めた head の扱いを含む） |

- `answers` が返す行には事象番号が要る。行に `seq` を足す（`ndf answer list` の出力にキーが 1 つ増える）

## 単体テストの観点

`tests/progress/gate/test_gate_mvv_answer.py`（新設）で、実際のステートマシン（`store` と `run`）に事象を書き、照合関数・宛先の先端を読む関数のスタブを渡して縛る。`tests/progress/test_run.py` の今の省いた回答のテスト（`service:mvv` で工程が進む）は、一致を返す照合関数を `Env` に渡す形へ直す。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mvv_targets`・`mvv_skip` | 4 つの欄がそろう `skip: true` → 省く | `pr` が文字列・`base_sha` が 39 桁・`base_ref` が無い → None | — |
| `mvv_answer_void` | 欄のそろう省いた回答 → None。人の回答 → None | 欄を 1 つ欠く省いた回答 → 理由。その回答の事象から `decide` → `GATE` | `mvv_gate` が辞書でない → 理由 |
| `mvv_vouch` が作る関数 | 判定と同じ PR・宛先・head・先端 → None。`observe` と `base_tip` は 1 回ずつ | 同じ head で宛先だけが違う → 理由。質問の PR が違う → 理由で `observe` を呼ばない。`observe` のスタブの中で先端を進める → 理由 | `observe` が `model.Refused` → 理由で例外を出さない。`base_tip` が None → 理由 |
| `_consume` | 一致を返す照合関数 → 1 回の駆動で消費し、工程が進む | 理由を返す照合関数 → 待ちへ戻り次の手が `GATE`、`finished` が無い。エントリポイントを通った後に別の駆動が省いた回答を書いた状態で `advance` → 照合関数が 1 回呼ばれてから消費か待ち | `Env.vouch` が None の `run.advance`・`run.work` → 待ちへ戻り `next` を返し続けない |
| `_consume`（人の回答） | 人の承認 → 照合関数を呼ばずに消費 | — | — |
| `recheck` | 人の回答で head が違う → 待ちへ戻す（今と同じ）。文書だけの差 → 出し直さずに進む（#3020） | 省いた回答 → `observe` を呼ばずに None | — |
| `_progress` | `show`・`wait`・`resync` で省いた回答があっても照合関数を呼ばない（呼ぶと落ちるスタブ） | `ndf progress work` の子のエントリポイントで作った `Env` が照合関数を持つ | — |
| `approved_head` | 消費された省いた回答 → その head | 待ちへ戻された・まだ消費されていない省いた回答が最後 → None | — |
| `gate_outcome`・`_judged` | 手順 (8) の出力が `pr`・`base_ref`・`base_sha`（= `snap.commit`）を持ち、`context.schema.json` に通る | 手順 (3) の出力は 3 つを持たない | — |
| `git_base_tip` | 一時のリモートのリポジトリでブランチの先端の SHA を返す | ブランチが無い → None | リモートが無い → None で例外を出さない |
