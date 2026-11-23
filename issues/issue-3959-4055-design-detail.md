# #3959 #4055 の詳細設計: MVV 判定（判定を打つ条件・レビューの保留・根拠の行・プランの外のエントリポイント）

設計文書 [issue-3959-4055-design.md](./issue-3959-4055-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。前提は #4076 の実装が main へ入った後の形である（`records/mvv.py` の `current`・`Snapshot`・`principles`・`record_review`、`records/mvv_decide.py` の `citation_errors`・`fenced_lines`・`items_of`、`crossreview/drive.py` の `_summary` が `record_review` を呼ぶ形）。#4076 の実装は #4090・#4099 の設計（PR #4121）の直しを含むので、#4076 の部品の形はその設計の「#4076 の文書のどこを置き換えるか」の後の形で読む。#4090・#4099 の決定 16 が挙げる #3959 と重なる 5 点（`Snapshot.commit`・`cite` の `mismatch`・`cite` が使う確かめの関数・`record_review` の契約・`review_holds` の規則）は、決定 16 の「#3959 の実装がすること」の列の形でこの文書に書いた（設計の決定 11）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/workflow/context.schema.json      … mvv_gate に holds・noted
├── contracts/workflow/stage-io.schema.json     … stage-in をプランが渡す平らな形に直す
├── src/ndf/progress/definition.py              … validate_stage_input を平らな形で確かめる
├── src/ndf/progress/invoke.py                  … resolve_inputs が validate_stage_input で確かめる
├── src/ndf/domains/records/
│   ├── mvv_gate.py                              … 判定を打つ条件と省く条件を分ける・ベースの MVV から事実を作る・保留を読む
│   ├── mvv.py                                   … validation_rows・review_rows・mvv_validate_sprint・record_review・cite
│   ├── mvv_decide.py                            … review_holds
│   ├── mvv_material_decide.py                   … gate_outcome に保留
│   └── cli.py                                   … --machine・入力の形の確かめ・cite
├── src/ndf/progress/gate/checks.py             … MvvFacts.review_holds・check_review_holds・区分ごとの検査・note
├── src/ndf/progress/command.py                 … stage_inputs_of（ID から判定のステップの入力）
├── src/ndf/progress/model.py                   … MVV_SKIP_GATES・mvv_skip（リリース承認ゲートを省かない）
├── src/ndf/domains/crossreview/
│   ├── drive.py                                 … _summary が record_review の欠けた必須の行を Outcome へ
│   ├── finish_decide.py                         … finish(record_failed=…)
│   └── cli.py                                   … cmd_run が record_failed を渡す
└── tests/
    ├── domains/records/test_mvv_gate.py
    ├── domains/records/test_mvv_review_holds.py *
    ├── domains/records/test_mvv_cite.py *
    ├── domains/records/test_mvv_record_review.py
    ├── domains/records/test_mvv.py
    ├── progress/gate/test_gate_checks.py
    ├── progress/test_command_mvv_inputs.py *
    ├── progress/test_invoke.py
    ├── progress/test_definition.py
    └── domains/crossreview/test_crossreview_finish.py
```

依存の向き: `records/cli.py` が `progress.command`（文脈を読む）と `records.mvv_gate` を読む。`mvv_gate` は今どおり `progress.gate.checks` と `progress.definition` を読む。`mvv_decide` は git もファイルも読まない純粋な関数だけを持つ。`progress` は `records` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの前の判定 | `checks`・`mvv`・`mvv_decide`・`mvv_material_decide` | モジュール | `gate`・`_judged`・`pr_number`・`boundary_paths`・`_recorded`（変更）・`snapshot_facts`・`review_prs`（新設）。`base_facts` は消す |
| `src/ndf/progress/model.py` | 変更 | ステートマシンの型と規則 | — | モジュール | 定数 `MVV_SKIP_GATES`（新設）・`mvv_skip`（変更） |
| `src/ndf/progress/gate/checks.py` | 変更 | 省かない理由の検査 | — | `MvvFacts`（変更） | `check_review_holds`（新設）・`refusals`（変更）・`judgeable`（新設）・`may_skip`・`note`（変更）・定数 `CHECKS_BY_GATE`（新設） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の記録の読み書き | `jsonl`・`mvv_decide` | `ReviewWrite`（新設）、モジュール | `validation_rows`・`review_rows`・`cite`（新設）・`record_review`（返り値を変える）・`mvv_validate_sprint`（引数 `rows` と返り値を変える） |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | — | モジュール | `review_holds`（新設） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力 | — | モジュール | `gate_outcome`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` のエントリポイント | `mvv_gate`・`progress.command` | モジュール | `_mvv_gate`（変更）・`_mvv_inputs`・`_env_inputs`・`_mvv_cite`（新設） |
| `src/ndf/progress/definition.py` | 変更 | 工程の定義と規約の読み手 | `contracts` | モジュール | `validate_stage_input`（入れ子の `context` でなく、入力の object そのものを確かめる） |
| `src/ndf/progress/invoke.py` | 変更 | 工程から作業を呼ぶ層 | `definition` | モジュール | `resolve_inputs`（取り出した入力を `validate_stage_input` で確かめる） |
| `contracts/workflow/stage-io.schema.json` | 変更 | 工程の入力と出力の型 | `context.schema.json` | — | `$defs.stage-in` を文脈のスキーマへの参照にし、`context`・`base`・`head` の囲みを外す |
| `src/ndf/progress/command.py` | 変更 | ステートマシンの操作 | `store`・`invoke` | モジュール | `stage_inputs_of`（新設） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv` | `Outcome`（変更） | `_summary`（変更） |
| `src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方の判定 | — | モジュール | `finish`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review` のエントリポイント | `finish_decide` | モジュール | `cmd_run`（変更） |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | — | `$defs.mvv_gate` に任意のキー `holds`・`noted` |

## 判定を打つ条件と省く条件

満たす決定: 決定 1・3、受け入れ条件 1・2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `judgeable` | `progress/gate/checks.py` | 新設 | `judgeable(f: MvvFacts) -> Optional[str]` | 判定を打てない理由（検査 1 だけ）。打てれば None |
| `CHECKS_BY_GATE` | `progress/gate/checks.py` | 新設 | `dict[str, tuple[Callable[[MvvFacts], Optional[str]], ...]]` | 区分ごとの省かない理由の検査。`design`・`release` とも検査 1〜7（即時修正の区分は #4104 が足す） |
| `refusals` | `progress/gate/checks.py` | 変更 | `refusals(f: MvvFacts, gate: str = "design") -> list[str]` | 区分の検査の理由を集める。既定の区分は今の呼び手のため |
| `may_skip` | `progress/gate/checks.py` | 変更 | `may_skip(f: MvvFacts, verdict: Any, gate: str = "design") -> tuple[bool, list[str]]` | `refusals(f, gate)` を使う。ほかは今と同じ |
| `gate` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `_judged` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `pr_number` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `design` は `inputs["worktree"]`（文字列のとき。無ければ `root`）のブランチの開いた PR。`release` は今のまま（文脈の `pr`）。文脈の `design_pr` は読まない（プランの経路の入力に無いので、両方のエントリポイントで同じ PR を選ぶため） |
| `boundary_paths` | `records/mvv_gate.py` | 変更 | `boundary_paths(root: Path, ref: str) -> tuple[str, ...]` | レッドラインのパスを、作業ツリーでなくコミット `ref` の `.ndf/pace.json`（`git show <ref>:.ndf/pace.json`）から読む。`ref` に無ければ空。形が壊れていれば `jsonfile.JsonFileError` |
| `Snapshot` | `records/mvv.py` | 変えない（#4076 の実装が欄 `commit` を足す。#4090・#4099 の決定 9） | — | `current` が解決したコミットの SHA を `commit` に持つ。この Issue は欄を足さずに `snapshot_facts` で読む |
| `current` | `records/mvv.py` | 変えない（#4076 の関数） | — | 参照の解決はエントリポイントの 1 回だけで、呼び手は解決し直さない。ベースの本文が承認の記録と合わない（`mismatch`）ときは `MvvError` を投げる（#4090・#4099 の決定 15） |
| `snapshot_facts` | `records/mvv_gate.py` | 新設 | `snapshot_facts(root: Path, snap: mvv.Snapshot, inputs: Mapping[str, Any]) -> tuple[MvvFacts, str]` | `base_facts` の置き換え。MVV の状態・本文の sha256・承認の記録の sha256・本文をすべて `snap`（ベースの MVV）から、`boundary_paths` を `boundary_paths(root, snap.commit)` から作る。作業ツリーの `.ndf/mvv.json`・`.ndf/mvv.md`・`.ndf/pace.json` は読まない（PR が `boundary_paths` から自分の変えるパスを外しても、今回の変更にはベースの規則が効く） |
| `review_prs` | `records/mvv_gate.py` | 新設 | `review_prs(gate: str, number: int) -> list[int]` | 保留を読む PR。`design` は `[number]`、`release` は空（#4105 まで）。下の「レビューの保留」の接ぎ目 |

- `gate`
  - 手順: (1) モードの定義がそのゲートを持たなければ、今どおり判定せずに返す。(2) `_judged` を呼んで返す。(3) `gate` が捕まえるのは、`_judged` の手順 (1)（PR を読み終えるまで）の例外（`GateError`・`GitError`・`GitHubError`・`IdentityError`・`OSError`）だけで、`verdict: null`・`skip` 偽・`head` 無し・`why` は「MVV 判定を打てない: <理由>」にし、コメントは書かない（書く先の PR が決まっていない）。PR を読んだ後の失敗は `_judged` が中で受け、`gate` へ例外を出さない（下の「PR を読んだ後の失敗」）
  - 進め方の条件（`inputs["pace"] not in MVV_PACES` で返す行）は消す。進め方は `_judged` の最後の「省くか」でだけ読む
- `_judged`
  - 手順: (1) `pr_number` で PR を決め、`hub.pull` で `pull`（head・`base_ref`・本文）を 1 回読む。ここで読んだ `pull.head_sha` を、以降のすべての出力の `head` にする。(2) `git fetch <リモート> <base_ref>` を 1 回打ち、`snap = mvv.current(root, md.signals_ref(base_ref, リモート))` でベースの MVV を 1 度だけ読む。参照を SHA へ解決するのは `current` のエントリポイントの `git rev-parse` の 1 回だけで、`_judged` は `git rev-parse` を打たず、`current` が返した `snap.commit` をベースのコミットとして使う。`snapshot_facts(root, snap, inputs)` で事実を作る（レッドラインのパスも同じ `snap.commit` から読む）。この後の検査 1・判定の材料・保留のバージョンは、どれもこの `snap` と `snap.commit` だけを使う（作業ツリーの MVV と `.ndf/pace.json` を読まない。PR が `.ndf/mvv.json` を消しても、ベースが承認済みなら検査 1 を通って判定し、検査 5 が外れる）。(3) `judgeable` が理由を返せば、`gate_outcome(gate, None, False, [理由], head)` で返し、コメントは書かない（承認済みの MVV が無いリポジトリの出力と PR を今と同じにする）。出力に `noted: false` を置く。(4) 差分を `mvv.pr_files` で読み、`pr_facts(hub, number, facts, rows, pull)`（読み済みの `pull` を渡す）で事実を足す。差分を読み終えたら `hub.pull` をもう 1 回読み、head か宛先（`base_ref`）が (1) の値と違えば材料を捨てて止める（下の表。差分は PR の番号でしか読めず、読む間に push されると別の head の差分に、宛先を変えられると別のベースに対する差分になるため。判定の材料は (1) の head と宛先と、そのまま読み終えた差分だけにする）。この読み直しが `GitHubError` を投げたときも材料を捨てて止める（下の表。未照合の差分を使わず、例外を `gate` へ出さない）。(5) 記録 `mvv-validation.jsonl` を `validation_rows` で 1 回だけ読み、保留を求める（下の「レビューの保留」）。読めたときの行は (7) の再利用にも渡す。(6) `mm.incomplete_patches(rows)` があれば、`refusals(facts, gate)` の理由とあわせて `verdict: null` で記録して返す。(7) LLM の判定（今と同じ材料で `mvv_validate_sprint(root, 入力, judge, reuse=読めたか, rows=(5) の行)`。(5) で記録を読めたときだけ、その行から同じ材料の「従う」を再利用し、記録を読み直さない。読めなかったときは再利用を省いて LLM で判定する）。判定の行の追記が `OSError` で失敗しても判定は捨てず、`verdict` はそのまま、`skip` を偽にし、`why` に「判定を記録（mvv-validation.jsonl）に書けない: <原因>」を足して (8) へ進む（(5) で求めた保留は `holds` にそのまま残る。記録を読めず追記もできないときは、判定・保留の「読めない」・記録に書けない理由の 3 つがそろって返る）。`GateError`（LLM が回答しない）はここで受け、`verdict: null`・`skip` 偽・`why` は「MVV 判定を打てない: <理由>」と `refusals(facts, gate)` の理由、`holds`・`head` は読んだ値のまま (9) へ進む。(8) `skip, why = may_skip(facts, verdict, gate)`。進め方が `MVV_PACES` に無い（`normal` など）なら `skip` を偽にし、`why` の先頭に「進め方 <値> は承認ゲートを MVV 判定で省かない」を足す。区分が `release` なら `skip` を偽にし、`why` に「リリース承認ゲートは、保留を読む PR の範囲が決まるまで（#4105）MVV 判定で省かない」を足す（決定 3）。(9) `_recorded` で PR のコメントへ書いて返す
  - 事後条件: `mvv-validation.jsonl` を読むのは (5) の 1 回だけで、`mvv_validate_sprint` は読み直さない。`verdict` が null なのは、(1) の失敗（`gate` が受ける）・(3)・下の「PR を読んだ後の失敗」の表で `verdict: null` とした行（(2) のベースの取得・(4) の差分の読み取り・head と宛先の移動・読み直しの失敗・(6)・(7) の LLM の失敗）のときだけ。`normal` の `skip` は常に偽。PR を読んだ後の出力は、どの終わり方でも (1) で読んだ `head` を持つ
  - 副作用: PR のコメントは、下の表の「コメント」の列が「書く」の終わり方でだけ 1 件書く（(3) と、判定の材料が今の head に属さない終わり方では書かない）。ベースの取得（`git fetch` 1 回）・PR の読み直し（REST 1 回）とコピーの書き込みが増える（`current` の副作用）
- `_recorded`: `note(outcome["gate"], outcome["verdict"], outcome["why"], outcome.get("holds", ()))` を書く。`gate_outcome` は保留が空なら `holds` を置かないので、キーを必須として読まない。書き込みの `GitHubError` は外へ出さず、`skip` を偽にし、`why` の末尾に「判定の記録を PR に書けない: <原因>」を足し、出力に `noted: false` を置いて返す（`verdict` と `head` は保つ）。書けたときは `noted` を置かない
- `note`（`checks.py`）: シグネチャを `note(gate: str, verdict: Any, why: Sequence[str], holds: Sequence[str] = ()) -> str` に変え、`holds` が空でなければ「### レビューの保留」の箇条書きを足す。呼び手は `mvv_gate._recorded` だけ
- `gate_outcome`（`mvv_material_decide.py`）: 引数 `holds: Sequence[str] = ()` を足し、空でなければ出力に `holds` のキーを置く。`context.schema.json` の `$defs.mvv_gate` に任意のキー `holds`（文字列の配列）と `noted`（真偽値）を足す。判定した PR の番号・宛先・ベースのコミットを出力に足して省いた回答と照合するのは #4106

### 処理の手順

| 区分 | 進め方 | 検査 1 | 検査 2〜7 の理由 | 判定 | `skip` |
| --- | --- | --- | --- | --- | --- |
| `design`・`release` | `normal` | 通る | 問わない | 打つ | 偽 |
| `design` | `fast`・`auto` | 通る | 無い | 打つ | 「従う」・レッドライン空で真 |
| `release` | `fast`・`auto` | 通る | 問わない | 打つ | 偽（#4105 までの暫定。決定 3） |
| `design` | `fast`・`auto` | 通る | ある | 打つ | 偽 |
| どれも | どれも | 外れる | — | 打たない（`verdict: null`） | 偽 |

「MVV が承認されていない」の扱いも判定と同じ `snap` から `_judged` が決める。

### 異常系の処理

PR を読んだ後の失敗（`_judged` が中で受け、`gate` へ例外を出さない）。どの行も `skip` は偽、`head` は (1) で読んだ値。`verdict` が null の行は `why` の先頭が「MVV 判定を打てない: 」。承認ゲートは省かずに人へ問う。

| 段階 | 失敗 | 検出する例外・条件 | `verdict` | `why` に足す理由 | コメント | 結果 | 再試行 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| (2) | ベースの MVV・レッドラインの設定を読めない | `git fetch` の `GitError`・`current` の `MvvError`（参照を解決できないときを含む）・`boundary_paths` の `JsonFileError`・`OSError` | null | 「ベースの MVV を読めない: <原因>」。作業ツリーの MVV で代えない | 書く | `ok`・人へ問う | 直して 判定のステップから |
| (4) | 差分を読めない | `pr_files` の `GitHubError` | null | 「変えたファイルを読めない: <原因>」 | 書く | 同上 | 判定のステップから |
| (4) | 差分を読む間に head か宛先が動いた | 読み直した head か `base_ref` が (1) と違う | null | 「判定の材料を読む間に PR が <base> 宛て・<a> から <base'> 宛て・<b> へ動いた」 | 書かない（材料が今の PR に属さない） | `ok`・人へ問う | 判定のステップから |
| (4) | 差分の後に PR を読み直せない | 読み直しの `hub.pull` の `GitHubError` | null | 「判定の材料を読んだ後に PR を読み直せない: <原因>」。読んだ差分は捨てる | 書かない（材料を照合できていない） | `ok`・人へ問う | 判定のステップから |
| (6) | 差分の全体が材料に載らない | `incomplete_patches` | null | 載らないファイル | 書く | 同上 | PR を分ける |
| (5) | 記録（`mvv-validation.jsonl`）を読めない | `validation_rows` の `OSError`・`UnicodeDecodeError` | 判定のまま（判定は止めない） | 保留の 1 件「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」が `refusals` に入る（「MVV 判定を打てない: 」は付けない） | 書く（保留を載せる） | `ok`・人へ問う（省かない） | 原因を直して判定のステップから。読めない間は同じ材料の「従う」も再利用せず LLM を呼ぶ |
| (7) | LLM が回答しない | `GateError` | null | 回答が無い理由と `refusals` | 書く | 同上 | 打ち直せる |
| (7) | 判定の行を記録に追記できない | `mvv_validate_sprint` が返す追記の失敗の原因（`OSError`） | 判定のまま | 「判定を記録（mvv-validation.jsonl）に書けない: <原因>」（「MVV 判定を打てない: 」は付けない） | 書く（判定・保留・記録の失敗を載せる） | `ok`・人へ問う（省かない） | 原因を直して判定のステップから（記録に行が無いので LLM を呼び直す） |
| (9) | コメントを書けない | `_recorded` の `GitHubError` | 判定のまま | 「判定の記録を PR に書けない: <原因>」 | 書けない（`noted: false`） | `ok`・人へ問う（省かない） | 判定のステップから（同じ材料の「従う」は記録を再利用し LLM を呼ばない） |

PR を読む前の失敗（`gate` が受ける）:

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pr_number` | 開いた PR が無い（`design`） | PR を出していない | `GateError` | `verdict: null`・`head` 無し・コメント無し | `ok` で人へ問う | PR を出して打ち直す |
| `hub.pull` | PR を読めない | GitHub が回答しない | `GitHubError` | 同上 | 同上 | 打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate`（`normal`） | 判定のスタブが 1 回呼ばれ、`verdict` を持ち `skip` は偽、`why` の先頭に normal の理由、コメントが 1 件。保留が無い（`holds` のキーが無い）判定もコメントまで通る | 判定が「従う」・レッドライン空でも `skip` は偽 | LLM の失敗 → `verdict: null` で `ok`。出力は `head` と `holds` を持ち、コメントが 1 件で保留と理由が載る |
| `gate`（`fast`） | 今と同じ材料で「従う」→ `skip` 真 | 検査 4・5・3 が外れても判定は打たれ `skip` は偽 | 検査 1 が外れる → 判定のスタブを呼ばない |
| `snapshot_facts` | 事実がすべて `snap` と `snap.commit` の値。`git rev-parse` を打たない（Git の起動を数えるスタブで `current` の 1 回だけ） | 作業ツリーの本文が `snap` と違っても事実は `snap` のまま。作業ツリーの `.ndf/pace.json` から `boundary_paths` のパスを消した PR が、そのパスも変える → ベースの規則で検査 4 が外れる | ベースの `.ndf/pace.json` が壊れている → `JsonFileError` |
| `pr_number` | `worktree` の指すブランチの PR | 文脈に `design_pr` があっても読まない | 開いた PR が無い → `GateError` |
| `_judged`（PR を読んだ後の失敗） | 上の表の各行が、表の `verdict`・`head`（(1) の値）・`why`・コメントの有無になり、例外を外へ出さない。どの行も `Gated.target` は (1) で読んだ PR | `pr_files` の後の読み直しで head が動いた・head を変えずに宛先が変わった・読み直しが `GitHubError` → 差分を判定に使わず LLM を呼ばず、コメントを書かない | ベースの `git fetch` の `GitError` → `verdict: null`・`head` あり・コメント 1 件。コメントの書き込みの失敗 → `noted: false`・`skip` 偽・`verdict` は保つ |
| `note` | 保留と判定を載せる | 保留が空なら節を出さない | — |

## リリース承認ゲートの省略の止め

満たす決定: 決定 3

リリース承認ゲートは #4105 まで、判定のステップの出力（新しい判定は `skip: false`）に頼らず、ゲートの側でも省かない。省いてよい区分の表 `MVV_SKIP_GATES` を設計承認ゲートだけにし、`mvv_skip` はほかの区分を省かない。省いた回答を消費の時点で判定の対象（PR・宛先・宛先の先端）と照合する仕組みは #4106 が持ち、この Issue では区分の条件のほかは今のまま head だけを照合する（決定 9）。

古いバージョンの NDF が書いた状態（更新の前に済ませた判定の `skip: true`・まだ読まれていない `service:mvv` の回答）から再開する実行中のスプリントは扱わない（決定 3。利用者の方針「普及の段階に入るまで後方互換は不要」、2026-11-16）。実装の PR の `## 移行の手順` に「更新の前にリリース承認ゲートまで進んでいたスプリントは `ndf progress cancel` してから作り直す」を書く。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `MVV_SKIP_GATES` | `progress/model.py` | 新設 | `tuple[str, ...] = ("design",)` | MVV 判定で省いてよい承認ゲートの区分。#4105 が `release` を足す |
| `mvv_skip` | `progress/model.py` | 変更 | シグネチャは同じ | 今の条件に `gate in MVV_SKIP_GATES` を足す。外れれば None（承認ゲートで人を待つ） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mvv_skip`・`decide`（リリース） | — | 判定の出力が `skip: true` でもリリース承認ゲートでは None → `service:mvv` の回答を書かず `GATE` | — |
| `mvv_skip`（設計） | `skip: true`・head あり → 今どおり省く | — | — |

## レビューの保留

満たす決定: 決定 2、受け入れ条件 4・5

#3959 の本文の「#4076 の設計から移した仕組み」の「決まった規則」「異常系」「テストで落とす壊し方」「承認資料」をそのまま採る（#4077 のコミット `c5ac9fced7777d68e5303d5cb4d80a241dce3708` の詳細設計からコピーしたもの）。ここには、この設計で決めた接ぎ目だけを書く。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `validation_rows` | `records/mvv.py` | 新設 | `validation_rows(root: Path) -> tuple[list[dict[str, Any]], int]` | `mvv-validation.jsonl` を `jsonl.read_rows` で 1 回読み、すべての種類の行と読めなかった行の数を返す。ファイルが無ければ（`[]`, 0）。`OSError`・`UnicodeDecodeError` は呼び出し元へ返す |
| `review_rows` | `records/mvv.py` | 新設（本文のシグネチャを変える） | `review_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]` | `validation_rows` の行から種類 `mvv-review` の行だけを返す純粋な関数。本文の `review_rows(root)` の読み取りを `validation_rows` へ移し、記録の読み取りを判定の再利用と共有する |
| `mvv_validate_sprint` | `records/mvv.py` | 変更 | `mvv_validate_sprint(root, inputs, judge, reuse: bool = True, rows: Optional[Sequence[Mapping[str, Any]]] = None) -> tuple[dict[str, Any], Optional[str]]` | `reuse` が真なら再利用の候補を `rows` から選び、`rows` が None のときだけ今どおり記録を読む。`reuse` が偽なら記録を読まない（今は偽でも読む）。追記の `OSError` は外へ出さず、判定の行と原因を返す（書けたら原因は None）。呼び手は `mvv_gate._judged` だけ（ほかはテスト） |
| `review_holds` | `records/mvv_decide.py` | 新設 | `review_holds(rows: Iterable[Mapping[str, Any]], broken: int, repo: str, prs: Iterable[int], mvv: tuple[str, str]) -> list[str]` | 本文の規則（`redline`・今のバージョンの解除されない `deferred`）に 3 つ目を足す: キーの最後の行（本文の「最後」の決め方）が今のバージョンの `withheld: true` の行なら、終わり方によらず「PR #N の <項目>（記録の自由記述が未完了）」で保留する（#4090・#4099 の決定 7・16）。承認資料には、伏せた行から来る理由・本文の代わりに `WITHHELD` の文を載せる |
| `check_review_holds` | `progress/gate/checks.py` | 新設 | `check_review_holds(f: MvvFacts) -> Optional[str]` | `f.review_holds` が空でなければ「レビューの保留: <保留を / でつないだもの>」 |
| `MvvFacts` | `progress/gate/checks.py` | 変更 | 欄 `review_holds: tuple[str, ...] = ()` | — |
| `finish` | `crossreview/finish_decide.py` | 変更 | `finish(review, handed, record_failed: Optional[Mapping[str, Any]] = None) -> Verdict` | 本文の規則のとおり（`mvv_record_failed` を最優先） |
| `ReviewWrite` | `records/mvv.py` | 新設 | `NamedTuple(written: int, missing: tuple[Mapping[str, Any], ...], cause: Optional[str])` | `record_review` の結果。`missing` は書けなかったか試さなかった必須の行（`redline`・`deferred`）、`cause` は最初に失敗した書き込みの原因（失敗が無ければ None） |
| `record_review` | `records/mvv.py` | 変更（#4076 の契約を変える） | `record_review(root, pr, head, snapshot, rows, mvv: str, clean: Optional[Callable[[Sequence[str]], Sequence[str]]]) -> ReviewWrite` | 引数 `mvv`・`clean` は #4090・#4099 の形のまま持ち、返り値だけを `ReviewWrite` にする。下の手順。伏せた行（`withheld: true`）の追記の失敗も、その行が必須の行なら `missing` に載せる |

- 接ぎ目（`mvv_gate._judged` の手順 (5)。`review_prs`）: `prs` は `design` では判定する PR の 1 件。`release` では空で、保留を読まない。リリースに含まれる PR の範囲はブランチ戦略ごとに決め方が違い、承認資料と同じ確定した集合から読む仕組みは #4105 が持つ。それまでリリース承認ゲートは `fast`・`auto` でも省かない（`_judged` の手順 (8)、決定 3）ので、保留を読まなくても省かれる経路は無い。承認資料の「MVV 判定」の欄には「リリースの保留は #4105 まで読まない（省かない）」を載せる。API の呼び出しは増えない。`mvv` は `snap` の（`version` の文字列・`sha256`）。`repo` は `mvv.repo_key(root)`。`validation_rows` が `OSError` か `UnicodeDecodeError`（`jsonl.read_rows` が不正なバイトで投げる。`OSError` の子でない）を投げたら、保留を「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」の 1 件にし、手順 (7) は記録を読まずに再利用を省いて判定する（#3959 の本文の異常系の `review_rows` の行の検出は、`validation_rows` のこの 2 つの例外と読む）。読めたときは、同じ行を `review_rows` → `review_holds` と手順 (7) の `mvv_validate_sprint(rows=…)` の両方へ渡す（記録のファイルを読むのは判定 1 回につき 1 回）。結果を `MvvFacts.review_holds` に入れ、`gate_outcome` の `holds` にも同じ値を渡す（承認資料と PR のコメントは同じ結果を読む）
- `record_review`（#3959 の本文の「記録の欠落」の書き込み元の契約。#4076 の `-> int` と `OSError` の契約を置き換える）: (1) 書く行を、必須の行（`redline`・`deferred`）を先に、ほか（`fixed`・`unresolved`）を後に並べる（同じ種類の中は今の順）。重ねない判定（同じキー・同じ終わり方の最後の行があれば書かない）は今のまま。(2) 1 行ずつ追記し、`OSError` が出たらそこで止め、例外を外へ出さない。(3) 書けなかった行と後ろの試さなかった行のうち必須のものを `missing`、原因を `cause` にして返す。必須の行を先に書くので、通常の行の失敗は `missing` を作らない。`_summary` は `missing` が空でなければ `Outcome.mvv_record_failed` に `{"rows": [<key と outcome>], "cause": cause}` を入れ、空で `cause` があれば今どおり注意に留める。打ち直すと、重ねない判定で書けた行は飛ばし、欠けた行だけを書く
- 保留は判定を止めない。LLM の判定の後の `may_skip` でだけ効く（決定 1）
- 承認資料: `approval-request.md` の「MVV 判定」の行の出所を、今回の判定の出力 `outputs.mvv_gate`（`verdict`・`why`・`holds`・`noted` と判定の中身）にする（下の「手順書」の「承認資料の出所」）。規則を手順書にコピーせず、出力の値を載せる

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_holds` | 本文の「テストで落とす壊し方」の表のとおり | 同 | 同 |
| `_judged`（保留） | `design` は設計 PR の行を読む。`release` は保留を読まず、`fast`・`auto` でも `skip` は偽で `why` に #4105 の理由。保留があると `fast` で「従う」でも `skip` は偽、`why` と `holds` に載る。記録を読めるとき、`jsonl.read_rows` が判定 1 回で 1 回だけ呼ばれ、同じ材料の「従う」の行があれば LLM を呼ばない | 記録のファイルが無い → 保留は空 | `validation_rows` の `OSError` → 読めない理由の 1 件。不正なバイト（`b"\xff\xfe"` を含む行）を書いた実際のファイルで、`mvv_validate_sprint` をスタブにせず実物を通す → 例外で止まらず、LLM のスタブが 1 回呼ばれ（同じ材料の「従う」の行が記録にあっても再利用しない）、判定の行が追記され、`holds` と PR のコメントに読めない理由の 1 件、`fast` でも `skip` は偽。同じファイルで追記も `OSError` にする → `verdict` は判定のまま・`skip` 偽・`holds` に読めない理由・`why` に記録に書けない理由 |
| `mvv_validate_sprint` | `rows` を渡すと記録を読まずにその行から再利用する | `reuse=False` → 記録を読まない（読めないファイルでも止まらない） | 追記の `OSError` → 例外を出さず、判定の行と原因を返す |
| `record_review` | 全部書ける → `missing` 空・`cause` None | 渡した順が `fixed`・`redline` でも `redline` を先に書く。2 行目（通常の行）で失敗 → `missing` 空・`cause` あり。1 行目（必須の行）で失敗 → `missing` に失敗した行と後ろの試さなかった `deferred` | 失敗の後に打ち直す → 書けた行を重ねず、`missing` の行だけを書く |
| `finish`・`cmd_run` | 本文の表のとおり | 同 | 同 |

## 根拠の行の確かめ（`cite`）

満たす決定: 決定 4、受け入れ条件 7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `cite` | `records/mvv.py` | 新設 | `cite(snap: Snapshot, files: Sequence[Path]) -> list[dict[str, Any]]` | 各ファイルの本文に `fenced_lines`（#4090・#4099 の形。内外を `code_lines` で決める）と `decision_lines` を当て、フェンスの外の行ごとに（パス・行の番号・本文・決定の根拠の行か）の 4 要素を作って `citation_errors` に `{snap.version: snap.items}` とともに渡す。4 番目は `decision_lines(text)` に行の番号があるかで決め、`False` を決め打ちしない（cross-review の `_citations` と同じ関数・同じ規則）。`snap.state` が `absent`・`unapproved` なら空（確かめるバージョンが無い）。`mismatch` の分岐は持たない（`current` が先に `MvvError` を投げる） |
| `_mvv_cite` | `records/cli.py` | 新設 | `_mvv_cite(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `--base` から #4076 の CLI の `_mvv_current` と同じく取得して `current` を呼び、`cite` の結果を結果 JSON にする |

- 事前条件: `files` は UTF-8 のテキスト
- 結果 JSON: 誤りが 0 件なら `ok`。1 件以上なら `stopped`・`refused`、`items` に誤り、`summary` は「根拠の行の誤り N 件（最初: <path>:<line> <cited>）」。`current` と `cite` の `MvvError`（`mismatch` を含む）とファイルの `OSError`・`UnicodeDecodeError` は `stopped`・`precondition`、`summary` に原因
- 手順書: `approval-request.md` の「承認資料を組んだら」に `ndf records mvv cite --base <ベースブランチ> <承認資料のパス>` を置き、`refused` なら資料の根拠の行を設計文書と今のバージョンから書き直して打ち直す。`agent-layers.md` の supervisor が守る規則 13 の後ろに、承認資料を返す前にこのコマンドを打つことを足す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `cite` | バージョン 1 を引いた行 → 1 件。今のバージョンの実在する項目だけ → 0 件。根拠の行の形の誤り（`kind: format`）→ 1 件 | フェンスの中の根拠の行・文中の引用は拾わない。決定の見出しの下の行だけが決定の根拠の行として確かめられる。複数のファイル | MVV が `absent`・`unapproved` → 空 |
| `_mvv_cite` | 誤り無し → 0 | — | 誤りあり → 1・`refused`。読めないファイル・ベースが `mismatch` → `precondition` |

## プランの外のエントリポイント（`--machine`）

満たす決定: 決定 5・6、受け入れ条件 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `stage_inputs_of` | `progress/command.py` | 新設 | `stage_inputs_of(cwd: Path, machine_id: str, step_id: str) -> dict[str, Any]` | ステートマシンの最新の定義から `step_id` のステップを持つ工程を引き、`machine_context` に `invoke.resolve_inputs` を当てた入力を返す。プランが `NDF_STAGE_INPUT` に渡すものと同じ値だけで、文脈のほかのキー（`design_pr` など）は足さない |
| `_mvv_inputs` | `records/cli.py` | 新設 | `_mvv_inputs(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 区分ごとに入力を決める（下の表）。決められなければ `_InputError`（`invalid`・`refused`） |
| `_env_inputs` | `records/cli.py` | 新設 | `_env_inputs(gate: str, inputs: Mapping[str, Any]) -> dict[str, Any]` | `NDF_STAGE_INPUT` の object を、プランが組むのと同じ規則で確かめる。区分の判定のステップを持つ工程の定義（`design` は `stages/design.json`、`release` は `stages/deploy.json`。`GATE_STEPS` で引く）で `definition.validate_stage_input(stage, inputs)` を打つ。スキーマの誤り（文脈に無いキー `context` を含む object はここで外れる）は `_InputError`（`invalid`、`summary` に誤り）、工程の入力のキーが足りなければ `input_missing`。誤りが無ければ `inputs` をそのまま返す（プランが渡した入力はこの関数を通っているので、同じ値のまま通る） |
| `_mvv_gate` | `records/cli.py` | 変更 | シグネチャは同じ。引数 `--machine <ID>` を足す | `_mvv_inputs` の後に `gate` を呼ぶ |

| 区分 | `NDF_STAGE_INPUT` | `--machine` | 入力 |
| --- | --- | --- | --- |
| `design`・`release` | ある | 無い | 環境変数の JSON を `_env_inputs` で確かめた値（形が違えば `invalid`・`input_missing`） |
| `design`・`release` | 無い | ある | `stage_inputs_of(root, ID, "design-mvv" か "release-mvv")` |
| `design`・`release` | ある | ある | `invalid`（「入力の出所が 2 つある」） |
| `design`・`release` | 無い | 無い | `invalid`（今と同じ文に `--machine` を添える） |

- `stage_inputs_of` の手順: (1) `store_root_of(cwd)` の保存先を開き、`read_current(conn, machine_id)` で定義とイベントを読む（無い ID は `model.Refused`）。(2) `snapshot.stages` から、ステップの id が `step_id` の工程を引く。無ければ `model.Refused`（「ステートマシンのモードは承認ゲート <区分> を持たない」）。(3) `invoke.resolve_inputs(stage, machine_context(current))`。入力のキーが文脈に無ければ `StageInputMissing` をそのまま返す（プランと同じく判定しない）。文脈の `design_pr` は足さない: 設計の工程の入力（`design.json`）に無いので、足すとプランの経路と判定する PR が分かれる。どちらの経路も `pr_number` が `worktree` のブランチの開いた PR を選ぶ
- CLI の例外の扱い: `model.Refused` は `stopped`・`refused`、`StageInputMissing`・`StageInputInvalid` は `stopped`・`input_missing`・`invalid`（`exit-codes.md` の既存の種類）
- 判定のステップの id（`design-mvv`・`release-mvv`）は工程の定義（`contracts/workflow/stages/design.json`・`deploy.json`）の値で、`mvv_gate.py` に区分 → ステップの id の表 `GATE_STEPS = {"design": "design-mvv", "release": "release-mvv"}` を置く。定義の id を変えたら表も変える（テストで定義の中に id があることを縛る）
- 手順書: `agent-layers.md` の supervisor が守る規則に次の 2 つを足す。
  - 設計承認ゲート（`結果: 承認ゲート`）を返す前に、作業場所で `ndf records mvv gate --gate design --machine <ステートマシンの ID>` を打ち、結果の `outputs.mvv_gate` を設計の工程の出力の `mvv_gate` へそのまま載せ、承認資料の「MVV 判定」の欄にも同じ値を載せる（下の「承認資料の出所」）
  - リリース承認ゲート（プランを使わず、本番へ届く操作の前で `結果: 承認ゲート` を返す手動の経路。フェーズの表の「仕上げ」など）を返す前に、`ndf records mvv gate --gate release --machine <ステートマシンの ID>` を打つ（文脈の `pr` がスプリントの PR）。結果の `outputs.mvv_gate` は承認資料の「MVV 判定」の欄にだけ載せ、工程の出力へは載せない（#4105 までリリース承認ゲートは省かないので、ゲートが読む出力が要らない）。結果が `ok` でないとき（ステートマシンが無い・モードが `release-mvv` を持たない `refused`、文脈に `pr` が無い `input_missing`）は、欄に「判定できない: <結果の summary>」を載せて承認ゲートを返す（止まらずに人へ問う）。プランの経路（`release-mvv` を持つ本番のリリースプラン）では打たない（判定のステップが打つ）`pace.md` の「MVV 判定」の表から順 2（進め方）を外し、「進め方は省くかだけを決める」と `--machine` の行を足す。`project-mvv.md` の「工程での読み方」（#4076 が足す節）の承認ゲートの行から `pace.md` を指す

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `--machine` の読み出しとプランの書き込み | ステートマシンの DB | 読み取りのトランザクション（`store.transaction`）だけ。書かない | 何度打っても同じ入力。判定の記録は同じ材料の「従う」を使い直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `stage_inputs_of` | 設計の工程の入力（`issues`・`worktree`・`mode`・`pace`・`mvv_ref`）が、同じ文脈で `resolve_inputs` が組むものと同じ | 記録された出力（`design_pr`）が重なった文脈でも、返す入力に `design_pr` が無い | 無い ID → `Refused`。判定のステップを持たないモード → `Refused`。`mvv_ref` が文脈に無い → `StageInputMissing` |
| `_mvv_inputs` | 上の表の 6 行 | — | 両方ある → `invalid` |
| `_env_inputs` | プランが組む入力（`resolve_inputs` の結果）→ 同じ値を返す | 文脈のキーでない余分なキーを持つ入力 → `invalid` | `{"context": {...}}` の形 → `invalid`（判定を打たず、`gate` を呼ばない）。`mode` が無い → `input_missing`。`pace` の値が文脈のスキーマに無い → `invalid` |
| `_mvv_gate`（両方のエントリポイント） | 同じステートマシンの文脈で、`NDF_STAGE_INPUT`（プランが組む入力）と `--machine` の 2 回の起動が同じ入力で `gate` を呼び、同じ PR の番号を読み、同じ判定のスタブの結果から同じ `mvv_gate` を返す | 文脈の `design_pr` が `worktree` のブランチの PR と違う → 両方とも `worktree` のブランチの PR を判定する | — |
| `GATE_STEPS` | 定義の `design.json`・`deploy.json` に同じ id のステップがある | — | — |

## 手順書

満たす決定: 決定 1〜5、受け入れ条件 1・3・4・7・8

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/pace.md` | 具体例の最後の段落（`normal` は判定しない）を「判定し、省かない」に変える。「MVV 判定」の表から順 2 を外し、機械の検査に 7（レビューの保留）を足し、検査 1 だけが判定を止めること・ほかは省くかだけを決めることを書く。`--machine` の行と、リリース承認ゲートは #4105 まで省かないことを足す |
| `skills/development-workflow/references/approval-request.md` | 「MVV 判定」の行の出所を、進め方によらず今回の判定の出力 `outputs.mvv_gate` にし（下の「承認資料の出所」）、保留（`holds`）を足す。`pace: normal` は助言、の注記を消す。承認資料を渡す前に `ndf records mvv cite` を打つ手順を足す |
| `skills/development-workflow/references/agent-layers.md` | supervisor が守る規則に、設計承認ゲートとリリース承認ゲート（手動の経路）を返す前の `--machine` の判定と、結果を載せる先と、`cite` を足す（上の「プランの外のエントリポイント」の手順書） |

承認資料の出所（`approval-request.md` に書く手順）:

- 出所は今回の判定の出力 `outputs.mvv_gate` だけにする。プランの経路は判定のステップ（`design-mvv`・`release-mvv`）の済んだ出力（`ndf progress show` のその行の `outputs.mvv_gate`）、supervisor の経路は自分が打った `--machine` の結果の `outputs.mvv_gate` を読む。PR のコメントは出所にしない（差分を読む間に head が動いた・読み直しに失敗した・判定を打てなかった・コメントの投稿に失敗した終わり方では今回のコメントが無く、PR には前回の判定のコメントが残るため）
- 載せ方: `verdict` があれば判定・理由・根拠の項目・レッドライン、null なら「判定できない: <`why`>」。どちらでも `why` の省かない理由と `holds` を 1 件残らず載せる（`holds` のキーが無ければ「レビューの保留: 無し」）。`noted: false` なら「この判定の記録は PR のコメントに書けなかった（PR のコメントは前回の判定）」を添える。`outputs.mvv_gate` が無い（判定のステップが済んでいない・`--machine` が `ok` でない）ときは「判定できない: <理由>」を載せ、PR のコメントから補わない
| `skills/development-workflow/references/project-mvv.md` | 「工程での読み方」の承認ゲートの行（判定はどの進め方でも打つ） |
| `docs/contracts/commands.md` | `ndf records mvv gate` の `--machine` と、`ndf records mvv cite` の行 |
| `docs/glossary/glossary.json` | 語「レビューの保留」を追加、「MVV 判定」の意味を変え、`ndf records glossary render` で `docs/glossary.md` を作り直す |

## 工程の入力のコントラクト

満たす決定: 決定 10、受け入れ条件 9

工程の入力の規則は今 2 つある。コントラクトの `stage-in` と、それで確かめる `definition.validate_stage_input` は `{"context": {...}}` の形を求めるが、本番のコードからは呼ばれていない（呼ぶのは `tests/progress/test_definition.py` だけ）。プランが実際に組むのは `invoke.resolve_inputs` の平らな object で、文脈のスキーマ（`value_errors`）だけで確かめる。規則を `stage-in` と `validate_stage_input` の 1 つにし、プランの経路と `ndf records mvv gate` の環境変数の経路の両方がそれで確かめる。

| ファイル | 変える中身 |
| --- | --- |
| `contracts/workflow/stage-io.schema.json` | `$defs.stage-in` を `{"$ref": "context.schema.json", "description": "工程の入力。工程の定義の inputs と、文脈にある optional_inputs のキーを文脈から取った平らな object（progress.invoke.resolve_inputs）。script の作業は環境変数 NDF_STAGE_INPUT で、Skill の作業は質問の「入力（JSON）」で受ける"}` にする。`context`・`base`・`head` の囲みは、どの書き込み元も作らず、本番のどの読み手も読まないので外す |
| `src/ndf/progress/definition.py` の `validate_stage_input` | 変える前: `obj` を `stage-in` で確かめ、`obj["context"]` に工程の `inputs` のキーがあるかを見る。変えた後: `obj` を `stage-in` で確かめ、`obj` そのものに工程の `inputs` のキーがあるかを見る（誤りの文は「文脈のキー <k> が無い」）。シグネチャは同じ |
| `src/ndf/progress/invoke.py` の `resolve_inputs` | 変える前: 取り出した値を `value_errors`（文脈のスキーマ）で確かめる。変えた後: `validate_stage_input(stage, picked)` で確かめる（`stage-in` は文脈のスキーマを指すので、プランの入力の合否は変わらない）。足りないキーの `StageInputMissing` は今どおり先に投げる |
| `docs/contracts/commands.md` | `ndf records mvv gate` の行に、`NDF_STAGE_INPUT` は `stage-in` の形で、外れれば `invalid`・`input_missing` で止まることを足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `validate_stage_input` | 工程の入力のキーを平らに持つ object → 誤り無し | 任意の入力が無い object → 誤り無し | `{"context": {...}}` → スキーマの誤り。工程の入力のキーが無い → 「文脈のキー <k> が無い」 |
| `resolve_inputs` | 今のテストが通る（プランの入力の合否が変わらない） | — | 文脈の値の形が違う → 今どおり `StageInputInvalid` |
