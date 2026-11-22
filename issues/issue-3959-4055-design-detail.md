# #3959 #4055 の詳細設計: MVV 判定（判定を打つ条件・レビューの保留・即時修正・根拠の行・プランの外の入口）

設計文書 [issue-3959-4055-design.md](./issue-3959-4055-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。前提は #4076 の実装が main へ入った後の形である（`records/mvv.py` の `current`・`Snapshot`・`principles`・`record_review`、`records/mvv_decide.py` の `citation_errors`・`fenced_lines`・`items_of`、`crossreview/drive.py` の `_summary` が `record_review` を呼ぶ形）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/workflow/context.schema.json      … mvv_gate.gate に fix
├── contracts/templates/fix.json                … ステップ mvv・merge-gate・判定した PR でのマージ
├── contracts/templates/parts/steps.json        … 部品 fix-ci・mvv・fix-merge
├── src/ndf/adapters/github.py                  … merge の expect_base・expect_head_ref（マージ前の検査で宛先とブランチも照合）
├── src/ndf/ci_cli.py                           … watch --branch --base・成功に CI が確かめた PR（repo・番号・宛先・head）・merge --judged・merged
├── src/ndf/domains/records/
│   ├── mvv_gate.py                              … 判定を打つ条件と省く条件を分ける・区分 fix・保留を読む
│   ├── mvv.py                                   … review_rows・cite
│   ├── mvv_decide.py                            … review_holds
│   ├── mvv_material_decide.py                   … gate_outcome に保留
│   └── cli.py                                   … --machine・--gate fix・cite
├── src/ndf/progress/gate/checks.py             … MvvFacts.review_holds・check_review_holds・区分ごとの検査・note
├── src/ndf/progress/command.py                 … stage_inputs_of（ID から判定のステップの入力）
├── src/ndf/domains/release/record.py           … assembled_included（組み立てのコミットの「含む PR」の行だけを読む）
├── src/ndf/domains/release/record_decide.py    … assembled_message（「含む PR」の行を持つコミットが 1 つに決まるときだけ返す）
├── src/ndf/domains/crossreview/
│   ├── drive.py                                 … _summary が record_review の欠けた必須の行を Outcome へ
│   ├── finish_decide.py                         … finish(record_failed=…)
│   └── cli.py                                   … cmd_run が record_failed を渡す
└── tests/
    ├── domains/records/test_mvv_gate.py
    ├── domains/records/test_mvv_review_holds.py *
    ├── domains/records/test_mvv_cite.py *
    ├── domains/records/test_mvv_record_review.py
    ├── domains/release/test_release_record.py
    ├── adapters/test_ci_cli.py
    ├── progress/gate/test_gate_checks.py
    ├── progress/test_command_mvv_inputs.py *
    ├── domains/crossreview/test_crossreview_finish.py
    └── route/test_templates.py
```

依存の向き: `records/cli.py` が `progress.command`（文脈を読む）と `records.mvv_gate` を読む。`mvv_gate` は今どおり `progress.gate.checks` と `progress.definition` を読み、リリースの対象の PR を決めるために `release.record` と `core.branch_strategy` を読む（`release` は `records` を読まない）。`mvv_decide` は git もファイルも読まない純粋な関数だけを持つ。`progress` は `records` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートと即時修正の前の判定 | `checks`・`mvv`・`mvv_decide`・`mvv_material_decide`・`release.record`・`branch_strategy` | `Target`・`Gated`（新設）、モジュール | `gate`・`_judged`・`pr_number`・`boundary_paths`・`_recorded`（変更）・`snapshot_facts`・`review_prs`・`gate_label`（新設）・定数 `FIX`（新設）。`base_facts` は消す |
| `src/ndf/progress/gate/checks.py` | 変更 | 省かない理由の検査 | — | `MvvFacts`（変更） | `check_review_holds`（新設）・`refusals`（変更）・`judgeable`（新設）・`may_skip`・`note`（変更）・定数 `CHECKS_BY_GATE`（新設） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の記録の読み書き | `jsonl`・`mvv_decide` | `ReviewWrite`（新設）、モジュール | `review_rows`・`cite`（新設）・`record_review`（返り値を変える） |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | — | モジュール | `review_holds`（新設） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力 | — | モジュール | `gate_outcome`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv_gate`・`progress.command` | モジュール | `_mvv_gate`（変更）・`_mvv_inputs`・`_mvv_cite`（新設） |
| `src/ndf/progress/command.py` | 変更 | ステートマシンの操作 | `store`・`invoke` | モジュール | `stage_inputs_of`（新設） |
| `src/ndf/domains/release/record.py` | 変更 | リリース記録 | `record_decide`・`included_decide` | モジュール | `assembled_included`（新設。判定が使う。リリース記録の `_included` は変えない） |
| `src/ndf/domains/release/record_decide.py` | 変更 | リリース記録の純粋な判定 | — | モジュール | `assembled_message`（新設） |
| `src/ndf/adapters/github.py` | 変更 | GitHub の読み書き | — | `GitHub`（変更） | `merge`・`_merge_with`・`_preflight`（引数 `expect_base`・`expect_head_ref` を足す） |
| `src/ndf/ci_cli.py` | 変更 | `ndf ci` の入口 | — | モジュール | `_watch`（`--branch`・`--base`）・`_report`（成功にも `instance` に head、`items` に CI が確かめた PR）・`_merge`（`--judged`）・`_merged`（新設）・`_parser` |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv` | `Outcome`（変更） | `_summary`（変更） |
| `src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方の判定 | — | モジュール | `finish`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review` の入口 | `finish_decide` | モジュール | `cmd_run`（変更） |
| `contracts/templates/fix.json`・`parts/steps.json` | 変更 | 即時修正のプラン | — | — | ステップ `mvv`・`merge-gate`・`merge`（部品 `fix-merge`） |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | — | `$defs.mvv_gate.properties.gate.enum` に `fix` |

## 判定を打つ条件と省く条件

満たす決定: 決定 1・3、受け入れ条件 1・2・3・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `judgeable` | `progress/gate/checks.py` | 新設 | `judgeable(f: MvvFacts) -> Optional[str]` | 判定を打てない理由（検査 1 だけ）。打てれば None |
| `CHECKS_BY_GATE` | `progress/gate/checks.py` | 新設 | `dict[str, tuple[Callable[[MvvFacts], Optional[str]], ...]]` | 区分ごとの省かない（進めない）理由の検査。`design`・`release` は検査 1〜7、`fix` は 1・4・5・6・7 |
| `refusals` | `progress/gate/checks.py` | 変更 | `refusals(f: MvvFacts, gate: str = "design") -> list[str]` | 区分の検査の理由を集める。既定の区分は今の呼び手のため |
| `may_skip` | `progress/gate/checks.py` | 変更 | `may_skip(f: MvvFacts, verdict: Any, gate: str = "design") -> tuple[bool, list[str]]` | `refusals(f, gate)` を使う。ほかは今と同じ |
| `Target` | `records/mvv_gate.py` | 新設 | `NamedTuple(repo: str, pr: int, base: str, head: str)` | 判定した PR の識別情報。`fix` では CI が確かめた PR の識別情報（`ci.json` の `items` の `kind: "pr"`）にも使う |
| `Gated` | `records/mvv_gate.py` | 新設 | `NamedTuple(outcome: dict[str, Any], target: Optional[Target])` | `gate` の返り値。`outcome` は今の `outputs.mvv_gate`。`target` は (1) で読んだ PR の識別情報（`Target(hub.repo, 番号, pull.base_ref, pull.head_sha)`）で、PR を読む前に止まれば None。CLI は `target` をそのまま `--judged-out` へ書き、PR を探し直さない |
| `gate` | `records/mvv_gate.py` | 変更 | `gate(root, gate_name, inputs, hub, ask=llm.ask, expect: Optional[Target] = None) -> Gated` | 下の手順。`expect` は `fix` で CI が確かめた PR |
| `_judged` | `records/mvv_gate.py` | 変更 | `_judged(root, gate_name, inputs, open_hub, ask, expect) -> Gated` | 下の手順 |
| `pr_number` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `design` は `inputs["worktree"]`（文字列のとき。無ければ `root`）のブランチの開いた PR。`fix` はブランチで探さず `expect.pr`（CI が確かめた PR の番号）を返す。`expect` が無い・`expect.repo` が `hub.repo` と違う・`expect.base` が `inputs["base"]` と違えば `GateError`（「CI が確かめた PR が判定する PR と違う」。CI と判定とマージで同じ PR を使う）。`release` は今のまま（文脈の `pr`）。文脈の `design_pr` は読まない（プランの経路の入力に無いので、両入口で同じ PR を選ぶため） |
| `boundary_paths` | `records/mvv_gate.py` | 変更 | `boundary_paths(root: Path, ref: str) -> tuple[str, ...]` | レッドラインのパスを、作業ツリーでなくコミット `ref` の `.ndf/pace.json`（`git show <ref>:.ndf/pace.json`）から読む。`ref` に無ければ空。形が壊れていれば `jsonfile.JsonFileError` |
| `snapshot_facts` | `records/mvv_gate.py` | 新設 | `snapshot_facts(root: Path, snap: mvv.Snapshot, base_sha: str, inputs: Mapping[str, Any]) -> tuple[MvvFacts, str]` | `base_facts` の置き換え。MVV の状態・本文の sha256・承認の記録の sha256・本文をすべて `snap`（ベースの MVV）から、`boundary_paths` を `boundary_paths(root, base_sha)` から作る。作業ツリーの `.ndf/mvv.json`・`.ndf/mvv.md`・`.ndf/pace.json` は読まない（PR が `boundary_paths` から自分の変えるパスを外しても、今回の変更にはベースの規則が効く） |
| `review_prs` | `records/mvv_gate.py` | 新設 | `review_prs(root: Path, gate: str, pull: Pull, base_sha: str, inputs: Mapping[str, Any], hub: GitHub) -> list[int]` | 保留を読む PR。下の「レビューの保留」の接ぎ目 |
| `gate_label` | `records/mvv_gate.py` | 新設 | `gate_label(gate: str) -> str` | `fix` は `即時修正のマージ`、ほかは `model.gate_label` |

- `gate`
  - 手順: (1) 区分が `fix` でなく、モードの定義がそのゲートを持たなければ、今どおり判定せずに返す（`target` は None）。(2) `_judged` を呼んで返す。(3) `gate` が捕まえるのは、`_judged` の手順 (1)（PR を読み終えるまで）の例外（`GateError`・`GitError`・`GitHubError`・`IdentityError`・`OSError`）だけで、`verdict: null`・`skip` 偽・`head` 無し・`why` は「MVV 判定を打てない: <理由>」にし、コメントは書かず（書く先の PR が決まっていない）、`target` は None。PR を読んだ後の失敗は `_judged` が中で受け、`gate` へ例外を出さない（下の「PR を読んだ後の失敗」）
  - 進め方の条件（`inputs["pace"] not in MVV_PACES` で返す行）は消す。進め方は `_judged` の最後の「省くか」でだけ読む
- `_judged`
  - 手順: (1) `pr_number` で PR を決め、`hub.pull` で `pull`（head・`base_ref`・本文）を 1 回読み、`target = Target(hub.repo, 番号, pull.base_ref, pull.head_sha)` を作る。ここで読んだ `pull.head_sha` を、以降のすべての出力の `head` にし、`target` をどの終わり方の `Gated` にも添える。`expect`（`fix`）が与えられて、`pull.base_ref` が `expect.base` と違うか `pull.head_sha` が `expect.head` と違えば、ここで止める（下の表）。(2) `git fetch <リモート> <base_ref>` を 1 回打ち、`md.signals_ref(base_ref, リモート)` を `git rev-parse` で 1 つの SHA `base_sha` に固定する。`snap = mvv.current(root, base_sha)` でベースの MVV を 1 度だけ読み、`snapshot_facts(root, snap, base_sha, inputs)` で事実を作る（レッドラインのパスも同じ `base_sha` から読む）。この後の検査 1・判定の材料・保留の版・`fix` の例外は、どれもこの `snap` と `base_sha` だけを使う（作業ツリーの MVV と `.ndf/pace.json` を読まない。PR が `.ndf/mvv.json` を消しても、ベースが承認済みなら検査 1 を通って判定し、検査 5 が外れる）。(3) `judgeable` が理由を返せば、`gate_outcome(gate, None, skip, [理由], head)` で返し、コメントは書かない（承認済みの MVV が無いリポジトリの出力と PR を今と同じにする）。`skip` は `fix` で `snap.state` が `absent`・`unapproved` のときだけ真（理由は「MVV が承認されていないので判定しない」）、ほかは偽（`mismatch`・`unreadable` の `fix` は止まる）。`skip` が偽なら出力に `noted: false` を置く（コメントを書かないので、`fix` で人の承認へ回さない）。(4) 差分を `mvv.pr_files` で読み、`pr_facts(hub, number, facts, rows, pull)`（読み済みの `pull` を渡す）で事実を足す。差分を読み終えたら `hub.pull` をもう 1 回読み、head か宛先（`base_ref`）が (1) の値と違えば材料を捨てて止める（下の表。差分は PR の番号でしか読めず、読む間に push されると別の head の差分に、宛先を変えられると別のベースに対する差分になるため。判定の材料は (1) の head と宛先と、そのまま読み終えた差分だけにする）。この読み直しが `GitHubError` を投げたときも材料を捨てて止める（下の表。未照合の差分を使わず、例外を `gate` へ出さない）。(5) 保留を求める（下の「レビューの保留」）。(6) `mm.incomplete_patches(rows)` があれば、`refusals(facts, gate)` の理由とあわせて `verdict: null` で記録して返す。(7) LLM の判定（今と同じ材料と `mvv_validate_sprint`）。`GateError`（LLM が回答しない）はここで受け、`verdict: null`・`skip` 偽・`why` は「MVV 判定を打てない: <理由>」と `refusals(facts, gate)` の理由、`holds`・`head` は読んだ値のまま (9) へ進む。(8) `skip, why = may_skip(facts, verdict, gate)`。進め方が `MVV_PACES` に無い（`design`・`release`）なら `skip` を偽にし、`why` の先頭に「進め方 <値> は承認ゲートを MVV 判定で省かない」を足す。`fix` は進め方を見ない。(9) `_recorded` で PR のコメントへ書いて返す
  - 事後条件: `verdict` が null なのは、(1) の失敗（`gate` が受ける）・(3)・下の「PR を読んだ後の失敗」の表で `verdict: null` とした行（(1) の head の食い違い・(2) のベースの取得・(4) の差分の読み取り・head と宛先の移動・読み直しの失敗・(5) の保留の対象の失敗・(6)・(7) の LLM の失敗）のときだけ。`normal` の `skip` は常に偽。PR を読んだ後の出力は、どの終わり方でも (1) で読んだ `head` を持つ
  - 副作用: PR のコメントは、下の表の「コメント」の列が「書く」の終わり方でだけ 1 件書く（(3) と、判定の材料が今の head に属さない終わり方では書かない）。ベースの取得（`git fetch` 1 回）・PR の読み直し（REST 1 回）と写しの書き込みが増える（`current` の副作用）
- `_recorded`: `note(gate_label(outcome["gate"]), outcome["verdict"], outcome["why"], outcome.get("holds", ()))` を書く。`gate_outcome` は保留が空なら `holds` を置かないので、キーを必須として読まない。書き込みの `GitHubError` は外へ出さず、`skip` を偽にし、`why` の末尾に「判定の記録を PR に書けない: <原因>」を足し、出力に `noted: false` を置いて返す（`verdict` と `head` は保つ）。書けたときは `noted` を置かない
- `note`（`checks.py`）: シグネチャを `note(label: str, verdict: Any, why: Sequence[str], holds: Sequence[str] = ()) -> str` に変え、見出しに `label` を使い、`holds` が空でなければ「### レビューの保留」の箇条書きを足す。呼び手は `mvv_gate._recorded` だけ
- `gate_outcome`（`mvv_material_decide.py`）: 引数 `holds: Sequence[str] = ()` を足し、空でなければ出力に `holds` のキーを置く。`context.schema.json` の `$defs.mvv_gate` に任意のキー `holds`（文字列の配列）と `noted`（真偽値）を足す

### 処理の手順

| 区分 | 進め方 | 検査 1 | 検査 2〜7 の理由 | 判定 | `skip` | `fix` の結果 |
| --- | --- | --- | --- | --- | --- | --- |
| `design`・`release` | `normal` | 通る | 問わない | 打つ | 偽 | — |
| `design`・`release` | `fast`・`auto` | 通る | 無い | 打つ | 「従う」・レッドライン空で真 | — |
| `design`・`release` | `fast`・`auto` | 通る | ある | 打つ | 偽 | — |
| どれも | どれも | 外れる | — | 打たない（`verdict: null`） | `fix` でベースの MVV が `absent`・`unapproved` なら真、ほかは偽 | `skip` が真なら `ok`、偽（`mismatch`・`unreadable`）なら `stopped`（`precondition`。コメントが無いので `merge-gate` へ進まない） |
| `fix` | 見ない | 通る | 無い | 打つ | 「従う」・レッドライン空で真 | `skip` が真なら `ok`、偽なら `stopped`（`refused`） |
| `fix` | 見ない | 通る | ある | 打つ | 偽 | `stopped`（`refused`） |

`fix` の `skip` は「マージへ進めてよい」を表す。出力の形を区分で変えないため、同じキーを使う。「MVV が承認されていない」の例外も判定と同じ `snap` から `_judged` が決め、CLI は MVV を読み直さない。

### 異常系の処理

PR を読んだ後の失敗（`_judged` が中で受け、`gate` へ例外を出さない）。どの行も `skip` は偽、`head` は (1) で読んだ値、`why` の先頭は「MVV 判定を打てない: 」。「コメント」が「書かない」の行は判定の記録が PR に無いので、`fix` では人へ問わずに `precondition` で止め、理由は結果 JSON の `summary`（`why` を / でつないだもの）で呼び手へ返す。「書く」の行の `fix` は `refused` で `merge-gate` へ進み、人は PR のコメントの理由を読んで決める。

| 段階 | 失敗 | 検出する例外・条件 | `verdict` | `why` に足す理由 | コメント | `design`・`release` | `fix` の原因 | 再試行 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (1) | CI が確かめた PR と、読んだ PR の head か宛先が違う | `expect.head != pull.head_sha`・`expect.base != pull.base_ref` | null | 「CI が確かめた #<n>（<base> 宛て・<a>）と PR の今（<base'> 宛て・<b>）が違う」 | 書かない | —（`fix` だけ） | `precondition` | プランを `ci` から打ち直す |
| (2) | ベースの MVV・レッドラインの宣言を読めない | `git fetch`・`rev-parse` の `GitError`・`current` の `MvvError`・`boundary_paths` の `JsonFileError`・`OSError` | null | 「ベースの MVV を読めない: <原因>」。作業ツリーの MVV で代えない | 書く | `ok`・人へ問う | `refused` | 直して `mvv` から |
| (4) | 差分を読めない | `pr_files` の `GitHubError` | null | 「変えたファイルを読めない: <原因>」 | 書く | 同上 | `refused` | `mvv` から |
| (4) | 差分を読む間に head か宛先が動いた | 読み直した head か `base_ref` が (1) と違う | null | 「判定の材料を読む間に PR が <base> 宛て・<a> から <base'> 宛て・<b> へ動いた」 | 書かない（材料が今の PR に属さない） | `ok`・人へ問う | `precondition` | `fix` は `ci` から、ほかは判定のステップから |
| (4) | 差分の後に PR を読み直せない | 読み直しの `hub.pull` の `GitHubError` | null | 「判定の材料を読んだ後に PR を読み直せない: <原因>」。読んだ差分は捨てる | 書かない（材料を照合できていない） | `ok`・人へ問う | `precondition` | `fix` は `ci` から、ほかは判定のステップから |
| (5) | 保留の対象を決められない | `review_prs` の `GitHubError`・`StrategyError`・`JsonFileError`・`RecordError`（組み立てのコミットを 1 つに決められない） | null | 「レビューの保留の対象を決められない: <原因>」 | 書く | `ok`・人へ問う | `refused` | `mvv` から |
| (6) | 差分の全体が材料に載らない | `incomplete_patches` | null | 載らないファイル | 書く | 同上 | `refused` | PR を分ける |
| (7) | LLM が回答しない | `GateError` | null | 回答が無い理由と `refusals` | 書く | 同上 | `refused` | 打ち直せる |
| (9) | コメントを書けない | `_recorded` の `GitHubError` | 判定のまま | 「判定の記録を PR に書けない: <原因>」 | 書けない（`noted: false`） | `ok`・人へ問う（省かない） | `precondition` | `mvv` から（同じ材料の「従う」は記録を再利用し LLM を呼ばない） |

PR を読む前の失敗（`gate` が受ける）:

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pr_number` | 開いた PR が無い（`design`）・CI が確かめた PR が無いか判定の対象と合わない（`fix`） | PR を出していない・`ci.json` の PR のリポジトリか宛先が `--base`・作業場所の origin と違う | `GateError` | `verdict: null`・`head` 無し・コメント無し | `design`・`release` は `ok` で人へ問う。`fix` は `stopped`（`precondition`） | PR を整えて打ち直す |
| `hub.pull` | PR を読めない | GitHub が回答しない | `GitHubError` | 同上 | 同上 | 打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate`（`normal`） | 判定のスタブが 1 回呼ばれ、`verdict` を持ち `skip` は偽、`why` の先頭に normal の理由、コメントが 1 件。保留が無い（`holds` のキーが無い）判定もコメントまで通る | 判定が「従う」・レッドライン空でも `skip` は偽 | LLM の失敗 → `verdict: null` で `ok`。出力は `head` と `holds` を持ち、コメントが 1 件で保留と理由が載る |
| `gate`（`fast`） | 今と同じ材料で「従う」→ `skip` 真 | 検査 4・5・3 が外れても判定は打たれ `skip` は偽 | 検査 1 が外れる → 判定のスタブを呼ばない |
| `gate`（`fix`） | モードの定義を問わずに判定する。`Gated.target` が判定した PR の repo・番号・宛先・head | 文脈の無い入力（`base` だけの辞書）で動く。`expect` の head・宛先が PR と同じなら判定へ進む | ベースが承認済みで、head が `.ndf/mvv.json` を消した PR → 判定が打たれ、検査 5 で `skip` 偽（作業ツリーの `absent` で `ok` にならない）。ベースが `absent` で head が MVV を足した PR → 判定せず `skip` 真 |
| `snapshot_facts` | 事実がすべて `snap` と `base_sha` の値 | 作業ツリーの本文が `snap` と違っても事実は `snap` のまま。作業ツリーの `.ndf/pace.json` から `boundary_paths` のパスを消した PR が、そのパスも変える → ベースの規則で検査 4 が外れる | ベースの `.ndf/pace.json` が壊れている → `JsonFileError` |
| `pr_number` | `worktree` の指すブランチの PR。`fix` は `expect.pr` | 文脈に `design_pr` があっても読まない。`fix` で同じブランチから別の宛先にも PR がある → ブランチで探さず `expect.pr` | 開いた PR が無い・`fix` で `expect` が無い・`expect.repo`・`expect.base` が合わない → `GateError` |
| `_judged`（PR を読んだ後の失敗） | 上の表の各行が、表の `verdict`・`head`（(1) の値）・`why`・コメントの有無になり、例外を外へ出さない。どの行も `Gated.target` は (1) で読んだ PR | `pr_files` の後の読み直しで head が動いた・head を変えずに宛先が変わった・読み直しが `GitHubError` → 差分を判定に使わず LLM を呼ばず、コメントを書かない | ベースの `git fetch` の `GitError` → `verdict: null`・`head` あり・コメント 1 件。コメントの書き込みの失敗 → `noted: false`・`skip` 偽・`verdict` は保つ |
| `note` | 保留と判定を載せる | 保留が空なら節を出さない | — |

## レビューの保留

満たす決定: 決定 2、受け入れ条件 4・5

#3959 の本文の「#4076 の設計から移した仕組み」の「決まった規則」「異常系」「テストで落とす壊し方」「承認資料」をそのまま採る（#4077 のコミット `c5ac9fced7777d68e5303d5cb4d80a241dce3708` の詳細設計から写したもの）。ここには、この設計で決めた接ぎ目だけを書く。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `review_rows` | `records/mvv.py` | 新設 | `review_rows(root: Path) -> tuple[list[dict[str, Any]], int]` | 本文の規則のとおり |
| `review_holds` | `records/mvv_decide.py` | 新設 | `review_holds(rows: Iterable[Mapping[str, Any]], broken: int, repo: str, prs: Iterable[int], mvv: tuple[str, str]) -> list[str]` | 本文の規則のとおり |
| `check_review_holds` | `progress/gate/checks.py` | 新設 | `check_review_holds(f: MvvFacts) -> Optional[str]` | `f.review_holds` が空でなければ「レビューの保留: <保留を / でつないだもの>」 |
| `MvvFacts` | `progress/gate/checks.py` | 変更 | 欄 `review_holds: tuple[str, ...] = ()` | — |
| `finish` | `crossreview/finish_decide.py` | 変更 | `finish(review, handed, record_failed: Optional[Mapping[str, Any]] = None) -> Verdict` | 本文の規則のとおり（`mvv_record_failed` を最優先） |
| `ReviewWrite` | `records/mvv.py` | 新設 | `NamedTuple(written: int, missing: tuple[Mapping[str, Any], ...], cause: Optional[str])` | `record_review` の結果。`missing` は書けなかったか試さなかった必須の行（`redline`・`deferred`）、`cause` は最初に失敗した書き込みの原因（失敗が無ければ None） |
| `record_review` | `records/mvv.py` | 変更（#4076 の契約を変える） | `record_review(root, pr, head, snapshot, rows) -> ReviewWrite` | 下の手順 |
| `assembled_message` | `release/record_decide.py` | 新設 | `assembled_message(commits: Sequence[Mapping[str, Any]], head: str) -> Optional[str]` | `head` から first-parent で辿った PR のコミットのうち、「含む PR」の行（`INCLUDED_TRAILER`）を持つものが 1 つだけで、その件名が `included_decide.RELEASE_TITLE` の形（組み立てが書く件名）ならそのメッセージ。行を持つコミットが無ければ None。2 つ以上ある・1 つでも件名が組み立ての形でなければ `ValueError`（組み立てが書いたコミットを決められない） |
| `assembled_included` | `release/record.py` | 新設 | `assembled_included(commits: Sequence[dict], pull: Pull, production: str, formats: NameFormats) -> list[int]` | `assembled_message` が返したメッセージの「含む PR」の番号。None なら（組み立てない設定）first-parent の件名から決める（`_included` と同じ規則）。`ValueError` は `RecordError`（「組み立てのコミットの『含む PR』を 1 つに決められない」）にして投げる。リリース記録の `_included`（最も新しい行を読む）は変えない |

- 接ぎ目（`mvv_gate._judged` の手順 (5)。`review_prs`）: `prs` は `design`・`fix` では判定する PR の 1 件。`release` では文脈の `pr` と、承認資料が確定した今回のリリースに含む PR の和である。含む PR は範囲を計算し直さず、確定済みの記録から読む: (1) `branch_strategy.load_at_ref(root, base_sha)`（MVV と同じベースのコミット）から本番ブランチと `names_decide.formats_of(strategy.section["release"])` を得る。(2) `hub.compare_commits(base_sha, pull.head_sha)` で判定する PR のコミットを、MVV を読んだベースの SHA と (1) の手順で読んだ head の 2 つの固定した SHA の間で読む（可変のブランチ名を渡さない。ベースが進んでも、ページ送りの途中でも範囲が MVV と同じまま）。(3) `record.assembled_included(commits, pull, production, formats)` で番号を得る。組み立てのリリースの PR（`merge_base_branch` の版を持つ設定・`release_branch`・`trunk` の `release_pr`）では、`assemble` が承認資料の `facts.pulls`（`_facts` が単位ごとの起点から集めた番号の和）を、件名が `RELEASE_TITLE` のリリースのコミット 1 つにだけ「含む PR」の行として書くので、承認資料と同じ集合になる。組み立ての後の直しのコミットが同じ行（`Ndf-Included-Pulls: none` など）を持つと、組み立てのコミットを 1 つに決められず `RecordError` になり、下の (5) の行（`verdict: null`・`skip` 偽）で人へ問う（後から足した行で承認資料の PR の保留を落とさない。照合できなければ省かない）。`release_branch` で複数のスプリントを集めた PR も、組み立てた一時の作業場所の範囲の番号がこの行に入るので、元のスプリントブランチを読み直さない。組み立てない設定（`trunk` の `production_pr`・版を持たない `merge_base_branch`）では行が無く、リリース記録と同じく first-parent の件名から決め、文脈の `pr` を必ず足す。`assemble.py` の `_facts`・`_span`・`_since_by_unit` は変えず呼ばない（本番ブランチ・タグの fetch と照会、PR の読み直しを足さない）。追加の費用は `release` で `compare` の一覧の読み取り（`github.list-pages` ページまで）だけで、`design`・`fix` では API を呼ばない。読めない（`GitHubError`・`StrategyError`・`JsonFileError`）・組み立てのコミットを決められない（`RecordError`）ときは上の表の (5) の行（`verdict: null`）。`mvv` は `snap` の（`version` の文字列・`sha256`）。`repo` は `mvv.repo_key(root)`。`review_rows` が `OSError` か `UnicodeDecodeError`（`jsonl.read_rows` が不正なバイトで投げる。`OSError` の子でない）を投げたら、保留を「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」の 1 件にする（#3959 の本文の異常系の `review_rows` の行の検出は、この 2 つの例外と読む）。結果を `MvvFacts.review_holds` に入れ、`gate_outcome` の `holds` にも同じ値を渡す（承認資料と PR のコメントは同じ結果を読む）
- `record_review`（#3959 の本文の「記録の欠落」の書き手の契約。#4076 の `-> int` と `OSError` の契約を置き換える）: (1) 書く行を、必須の行（`redline`・`deferred`）を先に、ほか（`fixed`・`unresolved`）を後に並べる（同じ種類の中は今の順）。重ねない判定（同じキー・同じ終わり方の最後の行があれば書かない）は今のまま。(2) 1 行ずつ追記し、`OSError` が出たらそこで止め、例外を外へ出さない。(3) 書けなかった行と後ろの試さなかった行のうち必須のものを `missing`、原因を `cause` にして返す。必須の行を先に書くので、通常の行の失敗は `missing` を作らない。`_summary` は `missing` が空でなければ `Outcome.mvv_record_failed` に `{"rows": [<key と outcome>], "cause": cause}` を入れ、空で `cause` があれば今どおり注意に留める。打ち直すと、重ねない判定で書けた行は飛ばし、欠けた行だけを書く
- 保留は判定を止めない。LLM の判定の後の `may_skip` でだけ効く（決定 1）
- 承認資料: `approval-request.md` の「MVV 判定」の行の出所を「判定の記録の `holds` と判定・理由・レッドライン」にする。規則を手順書に写さず、記録の値を写す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_holds` | 本文の「テストで落とす壊し方」の表のとおり | 同 | 同 |
| `_judged`（保留） | `release` はスプリントの PR と単位の PR の行を読む。保留があると `fast` で「従う」でも `skip` は偽、`why` と `holds` に載る | 記録のファイルが無い → 保留は空 | `review_rows` の `OSError` → 読めない理由の 1 件。不正なバイト（`b"\xff\xfe"` を含む行）を書いた実際のファイル → 判定は打たれ、`holds` と PR のコメントに読めない理由の 1 件、`fast` でも `skip` は偽 |
| `review_prs`（`release`） | 組み立てのリリースの PR の「含む PR」の行の番号と文脈の `pr` の和。`compare_commits` に渡すのは `base_sha` と head の SHA | `release_branch` で 2 つのスプリントを集めた PR → 承認資料（`collect` の `facts.pulls`）と同じ集合。行を持たない `production_pr` → first-parent の件名の番号と文脈の `pr`。組み立ての後に行の無い直しのコミットを積む → 組み立てのコミットの行を読む | `compare` の `GitHubError` → (5) の行。組み立ての後の直しのコミットに `Ndf-Included-Pulls: none` → `RecordError` で (5) の行、`fast` でも `skip` 偽 |
| `_judged`（`release` の保留） | `trunk` の `release_pr` で、版上げの PR の差分に現れない単位の PR に `redline` の行 → `fast`・`auto` で「従う」でも `skip` は偽、`holds` にその PR | `assemble` の `_span`・`_since_by_unit` を呼ばない（fetch とタグの照会の回数が増えない） | `review_prs` の失敗 → `verdict: null` |
| `record_review` | 全部書ける → `missing` 空・`cause` None | 渡した順が `fixed`・`redline` でも `redline` を先に書く。2 行目（通常の行）で失敗 → `missing` 空・`cause` あり。1 行目（必須の行）で失敗 → `missing` に失敗した行と後ろの試さなかった `deferred` | 失敗の後に打ち直す → 書けた行を重ねず、`missing` の行だけを書く |
| `finish`・`cmd_run` | 本文の表のとおり | 同 | 同 |

## 即時修正のプラン

満たす決定: 決定 3、受け入れ条件 6

| ファイル | 変える中身 |
| --- | --- |
| `contracts/templates/parts/steps.json` | 部品 `fix-ci` を足す: id は `ci`、`run` は `["bash", "-c", "set -o pipefail; out=$1; shift; ndf ci watch \"$@\" \| tee \"$out\"", "ci", "{state_dir}/ci.json", "--branch", "{{branch}}", "--base", "{{base}}", "--budget", "{{ci_budget}}", "--required={{required}}", "--test={{tests}}"]`、`on_cause` は部品 `ci` と同じ（`gh pr view <branch>` で PR を選ばない）。部品 `mvv` を足す: `{"id": "mvv", "run": ["ndf", "records", "mvv", "gate", "--gate", "fix", "--base", "{{base}}", "--ci-result", "{state_dir}/ci.json", "--judged-out", "{state_dir}/mvv-judged.json"]}`。原因は結果 JSON の `cause.kind`（`refused`・`precondition`）から読み、`exit_cause` は置かない。部品 `fix-merge` を足す: id は `merge`、`run` は `["ndf", "ci", "merge", "--branch", "{{branch}}", "--method", "{{merge_method}}", "--judged", "{state_dir}/mvv-judged.json"]`、`probe` は `["ndf", "ci", "merged", "--judged", "{state_dir}/mvv-judged.json"]`（部品 `merge` の `gh pr view <branch>` の probe を使わない） |
| `contracts/templates/fix.json` | `steps` を `test`・`push`・`pr`・`conflict-check`・`{"use": "fix-ci", "on_cause": {}}`・`{"use": "mvv", "on_cause": {"refused": "merge-gate"}, "next": "merge"}`・`{"use": "merge-gate"}`・`{"use": "fix-merge", "next": "end"}` にする。`params` に `gate`（`params.json` の既定 `ndf answer ask`）を足す。`summary` に MVV 判定と、進めない判定のときは人の承認でマージすることを足す |
| `src/ndf/ci_cli.py` | `watch` に `--branch <b> --base <base>` を足す（`--pr` と排他。どちらか必須）。`hub.pulls_by_head([b])` の開いた PR のうち `base_ref` が `base` のものを 1 件選び、0 件・2 件以上は `stopped`・`precondition`（「ブランチ <b> から <base> 宛ての開いた PR が 1 件に決まらない」。API の呼び出しは `gh pr view` の 1 回を置き換えるだけ）。`_report` の成功の結果にも `instance` に CI が確かめた head（`reader.pull.head_sha`。`HeadReader` が作業場所の HEAD に追いつくまで読み直した PR の head）を置き、`items` の先頭に CI が確かめた PR `{"kind": "pr", "repo": hub.repo, "number": 番号, "base": reader.pull.base_ref, "head": reader.pull.head_sha}` を置く。`merge` に引数 `--judged <パス>` を、サブコマンド `merged --judged <パス>` を足す（下） |
| `src/ndf/adapters/github.py` | `merge`・`_merge_with`・`_preflight` に `expect_base: Optional[str] = None`・`expect_head_ref: Optional[str] = None` を足す。`_preflight` は、マージ済みかを見る前に、`_merge_with` が読んだ `pull` の `base_ref`・`head_ref` が与えられた値と違えば `MergeOutcome(False, "refused", "#<n> の宛先が <base> でない（今は <base'>）" など)` を返す（マージ方式とキューの選択はこの照合を通った `pull.base_ref` で行う）。省けば今と同じ |

- CI が確かめた PR に束ねる: `fix-ci` のステップは CI の前に `--branch` と `--base` で PR を 1 件に決め、結果を `{state_dir}/ci.json` に書く。成功の結果は `items` に CI が確かめた PR（repo・番号・宛先・head）を持つ。`_mvv_gate` の `fix` はその項目を `Target` にして `gate(..., expect=<その Target>)` を呼ぶ。ファイルが無い・成功でない・`kind: "pr"` の項目が無いか形が違えば判定せずに `stopped`・`precondition`（「CI が確かめた PR が無い」）。`_judged` は CI の PR の番号の PR だけを読み（`pr_number`）、リポジトリ・宛先・head のどれかが CI の値と違えば止まる（`pr_number` の `GateError` か (1)。どちらも `precondition`）。同じコミットから別の宛先に PR があっても、その PR の CI の成功で判定する PR を進めない。CI の後に同じ作業場所から追加のコミットを push しても、CI を通らない head は MVV 判定もマージも通らない。止まったらプランを `ci` から打ち直す
- 判定した PR でマージする: `_mvv_gate` の `fix` は、`gate` の返した `Gated.target` が None でなければ `{"repo": target.repo, "pr": target.pr, "base": target.base, "head": target.head}` を `--judged-out <パス>` に書いてから結果を返す（`ok`・`stopped` のどちらでも書く）。None（PR を読む前に止まった）なら空のファイルを書く。CLI は PR を探し直さず、`outputs.mvv_gate` からも識別情報を組まない（判定に使った PR と記録が分かれない）。`ndf ci merge --judged <パス>` は、(1) ファイルが無い・空・形が違えば `stopped`・`precondition`（終了コード 2。判定を通らない PR をマージしない）。(2) `head` が作業場所の HEAD と違えば、PR を読まずに `refused`（今の `--reviewed` と同じ照合）。(3) `repo` が `--repo`（省けば作業場所の origin）と違えば `refused`。(4) `--branch` で開いた PR を探し直さず、`hub.merge(pr, expect_head=head, expect_base=base, expect_head_ref=args.branch, method=args.method)` を呼ぶ。宛先・ブランチ・head の照合は `_merge_with` が読む 1 回の `pull` で `_preflight` が行い、マージ方式とキューもその `pull` の宛先で選ぶ（別に `hub.pull` を読まないので、照合した宛先と方式を選ぶ宛先が分かれず、API の呼び出しも増えない）。断りは `refused`（宛先を変えた PR・同じブランチの別の宛先の PR を、判定に使ったベースの MVV と違うまま通さない）。プランの `--method` は今の `merge` と同じく渡す。`--judged` と `--reviewed` は同時に渡せない（`invalid`）。`ndf ci merged --judged <パス>`（`fix-merge` の `probe`）は記録の `repo` の `pr` を `hub.pull` で 1 回読み、マージ済みで `base_ref`・`head_sha` が記録と同じときだけ終了コード 0、ほか（記録が無い・空・形が違うを含む）は 1 を返す。ブランチ名で PR を探さないので、同じブランチの別の宛先の PR がマージ済みでも、記録した PR が開いていればステップを済んだとみなさず本体（`merge --judged`）へ進む。読んでからマージ API までの間に宛先を変える操作は、GitHub の API が head の SHA しか条件にできないので残る（脅威の前提はうっかりと権限の無い人で、宛先の変更には書き込みの権限が要る）。止まったら、プランを `ci` から打ち直して今の PR で判定し直す
- 判定が進めないとき（`refused`）の扱い: `mvv` は `merge-gate`（既存の部品。`{{gate}}` で人の回答を待ち、`approved` は `merge`、`rejected` は `end`）へ進む。`refused` になるのは、判定の記録を PR のコメントに書けた終わり方だけである（上の「PR を読んだ後の失敗」の表）。人は PR のコメントの判定の記録（理由・レッドライン・保留・外れた検査 4・5・7）を読んで決める。承認したら `fix-merge` が記録した PR と head のままマージする（承認の後に head・宛先が動けば上の照合で止まる）。PR を直して進めるときは `rejected` で終え、直したコミットでプランを打ち直す（`ci` と `mvv` が新しい head で確かめ直す）。判定を通らずに手でマージする経路は作らない。検査 4・5 で外れる即時修正（レッドラインのパス・MVV の設定を変えるもの）も同じ経路で人が承認してマージする（受け入れ条件 6 の「機械の検査が外れていないときだけ進む」を保つため、検査を注意に下げない）。`precondition`・`invalid` など `refused` 以外の原因は今どおり止まって人へ戻り、`merge-gate` へ進まない（コメントの無い理由で人に承認させない）
- CLI（`_mvv_gate` の `fix`）: `gate` の出力の `skip` が真なら `ok`（`summary`: 判定したときは「MVV 判定: マージへ進める」、ベースの MVV が承認されていないときは `why` の「MVV が承認されていないので判定しない」）。偽なら、出力に `head` が無い・`noted: false`・上の表で「コメント」が「書かない」の行のときは `result.stopped(TOOL, "precondition", "MVV 判定を打てない: " + " / ".join(why))`、ほかは `result.stopped(TOOL, "refused", "MVV 判定でマージへ進めない: " + " / ".join(why))`。どちらも `outputs.mvv_gate` を添える。`_judged` は表の行を見分けられるよう、「書かない」の行の出力に `noted: false` を置く（コメントを書けなかった (9) と同じ印）。CLI は `noted` と `head` の有無だけで原因を決め、MVV の状態を読み直さない（判定と例外を `_judged` が同じベースの `snap` から決める）

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fix.json` | プランを読み込むと、ステップの順が test・push・pr・conflict-check・ci・mvv・merge-gate・merge。`mvv` の `refused` は `merge-gate`、成功は `merge` へ | — | — |
| `ci_cli._watch`・`_report` | 成功の結果の `instance` が `HeadReader` の読んだ PR の head、`items` に repo・番号・宛先・head の `kind: "pr"` | push の直後で PR が前のコミットを指す間は成功を返さない（今のまま）。同じブランチから別の宛先にも PR がある → `--base` 宛ての PR の CI を待つ | `--base` 宛ての開いた PR が 0 件・2 件 → `precondition`。`--pr` と `--branch` を同時 → `invalid` |
| `_mvv_gate`（`fix`） | 「従う」・レッドライン空・保留無し → 終了コード 0、`--judged-out` に `Gated.target` の repo・PR・宛先・head（PR の検索の API を呼ばない） | ベースの MVV が `absent` → 0 で判定しない（記録は書く） | 「判定できない」・レッドラインあり・保留あり・ベースの取得の失敗 → 1・`refused`、記録は書く。ベースが `mismatch` → 1・`precondition`。CI の PR と head・宛先が違う・CI の PR が `--base` 宛てでない・コメントを書けない・材料の間に head か宛先が動いた・材料の後の読み直しの失敗 → 1・`precondition`（`merge-gate` へ進まない）。`ci.json` が無い・`kind: "pr"` の項目が無い → `precondition`。PR を読めない → `precondition`・記録は空 |
| `ci merge --judged` | 記録と PR・作業場所の HEAD が一致 → 記録の PR を `--method` の方式でマージ。PR の読み取りは `_merge_with` の 1 回だけ | 判定の後に作業場所でコミットした → `refused`。判定の後に PR の宛先を変えた → `_preflight` が `refused`（方式・キューを選ぶ前）。同じブランチから別の宛先にも PR がある → 記録の番号の PR だけを読む | 記録が無い・空 → 2・`precondition`。`--reviewed` と同時 → `invalid` |
| `ci merged --judged` | 記録の PR がマージ済みで宛先・head が記録と同じ → 0 | 同じブランチの別の宛先の PR がマージ済みで、記録の PR は開いている → 1（本体へ進む） | 記録が無い・空 → 1 |
| `GitHub._preflight` | `expect_base`・`expect_head_ref` が `pull` と同じ → None | 省けば今と同じ | 宛先が違う → マージ済みでも `refused` |
| `merge-gate`（`fix`） | `refused` の後に承認 → 判定した PR と head でマージ | 承認の後に head が進んだ → マージが `refused` | 却下 → `end`（マージしない） |

## 根拠の行の確かめ（`cite`）

満たす決定: 決定 4、受け入れ条件 7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `cite` | `records/mvv.py` | 新設 | `cite(snap: Snapshot, files: Sequence[Path]) -> list[dict[str, Any]]` | 各ファイルの全体の行を（パス・行の番号・本文）にし、`fenced_lines` の行を除いて `citation_errors` に `{snap.version: snap.items}` を渡す。`snap.state` が `absent`・`unapproved` なら空（確かめる版が無い）。`mismatch`（ベースの本文と承認の記録が合わない）は `MvvError`（「ベースの MVV の本文が承認の記録と合わない」）を投げ、空にしない |
| `_mvv_cite` | `records/cli.py` | 新設 | `_mvv_cite(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `--base` から #4076 の CLI の `_mvv_current` と同じく取得して `current` を呼び、`cite` の結果を結果 JSON にする |

- 事前条件: `files` は UTF-8 のテキスト
- 結果 JSON: 誤りが 0 件なら `ok`。1 件以上なら `stopped`・`refused`、`items` に誤り、`summary` は「根拠の行の誤り N 件（最初: <path>:<line> <cited>）」。`current` と `cite` の `MvvError`（`mismatch` を含む）とファイルの `OSError`・`UnicodeDecodeError` は `stopped`・`precondition`、`summary` に原因
- 手順書: `approval-request.md` の「承認資料を組んだら」に `ndf records mvv cite --base <ベースブランチ> <承認資料のパス>` を置き、`refused` なら資料の根拠の行を設計文書と今の版から書き直して打ち直す。`agent-layers.md` の supervisor が守る規則 13 の後ろに、承認資料を返す前にこのコマンドを打つことを足す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `cite` | 版 1 を引いた行 → 1 件。今の版の実在する項目だけ → 0 件 | フェンスの中の根拠の行・文中の引用は拾わない。複数のファイル | MVV が `absent`・`unapproved` → 空。`mismatch` → `MvvError` |
| `_mvv_cite` | 誤り無し → 0 | — | 誤りあり → 1・`refused`。読めないファイル・ベースが `mismatch` → `precondition` |

## プランの外の入口（`--machine`）

満たす決定: 決定 5・6、受け入れ条件 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `stage_inputs_of` | `progress/command.py` | 新設 | `stage_inputs_of(cwd: Path, machine_id: str, step_id: str) -> dict[str, Any]` | ステートマシンの最新の定義から `step_id` のステップを持つ工程を引き、`machine_context` に `invoke.resolve_inputs` を当てた入力を返す。プランが `NDF_STAGE_INPUT` に渡すものと同じ値だけで、文脈のほかのキー（`design_pr` など）は足さない |
| `_mvv_inputs` | `records/cli.py` | 新設 | `_mvv_inputs(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 区分ごとに入力を決める（下の表）。決められなければ `_InputError`（`invalid`・`refused`） |
| `_mvv_gate` | `records/cli.py` | 変更 | シグネチャは同じ。引数 `--base <ブランチ>`・`--ci-result <パス>`・`--judged-out <パス>`（`fix` だけ。`fix` では 3 つとも必須）を足す | `_mvv_inputs` の後に `gate` を呼ぶ。`fix` は上の「即時修正のプラン」の結果にする |

| 区分 | `NDF_STAGE_INPUT` | `--machine` | 入力 |
| --- | --- | --- | --- |
| `design`・`release` | ある | 無い | 環境変数の JSON（今と同じ） |
| `design`・`release` | 無い | ある | `stage_inputs_of(root, ID, "design-mvv" か "release-mvv")` |
| `design`・`release` | ある | ある | `invalid`（「入力の出所が 2 つある」） |
| `design`・`release` | 無い | 無い | `invalid`（今と同じ文に `--machine` を添える） |
| `fix` | 問わない | ある | `invalid`（「即時修正はステートマシンを持たない」） |
| `fix` | 問わない | 無い | `{"base": --base の値}`（`pr_number` が宛先の合う PR を選ぶ） |

- `stage_inputs_of` の手順: (1) `store_root_of(cwd)` の保存先を開き、`read_current(conn, machine_id)` で定義と事象を読む（無い ID は `model.Refused`）。(2) `snapshot.stages` から、ステップの id が `step_id` の工程を引く。無ければ `model.Refused`（「ステートマシンのモードは承認ゲート <区分> を持たない」）。(3) `invoke.resolve_inputs(stage, machine_context(current))`。入力のキーが文脈に無ければ `StageInputMissing` をそのまま返す（プランと同じく判定しない）。文脈の `design_pr` は足さない: 設計の工程の入力（`design.json`）に無いので、足すとプランの経路と判定する PR が分かれる。どちらの経路も `pr_number` が `worktree` のブランチの開いた PR を選ぶ
- CLI の例外の扱い: `model.Refused` は `stopped`・`refused`、`StageInputMissing`・`StageInputInvalid` は `stopped`・`input_missing`・`invalid`（`exit-codes.md` の既存の種類）
- 判定のステップの id（`design-mvv`・`release-mvv`）は工程の定義（`contracts/workflow/stages/design.json`・`deploy.json`）の値で、`mvv_gate.py` に区分 → ステップの id の表 `GATE_STEPS = {"design": "design-mvv", "release": "release-mvv"}` を置く。定義の id を変えたら表も変える（テストで定義の中に id があることを縛る）
- 手順書: `agent-layers.md` の supervisor が守る規則に「設計承認ゲート（`結果: 承認ゲート`）を返す前に、作業場所で `ndf records mvv gate --gate design --machine <ステートマシンの ID>` を打ち、結果の `outputs.mvv_gate` を設計の工程の出力の `mvv_gate` へそのまま写す。判定の記録は PR のコメントに載る」を足す。`pace.md` の「MVV 判定」の表から順 2（進め方）を外し、「進め方は省くかだけを決める」と `--machine` の行を足す。`project-mvv.md` の「工程での読み方」（#4076 が足す節）の承認ゲートの行から `pace.md` を指す

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `--machine` の読み出しとプランの書き込み | ステートマシンの DB | 読み取りのトランザクション（`store.transaction`）だけ。書かない | 何度打っても同じ入力。判定の記録は同じ材料の「従う」を使い直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `stage_inputs_of` | 設計の工程の入力（`issues`・`worktree`・`mode`・`pace`・`mvv_ref`）が、同じ文脈で `resolve_inputs` が組むものと同じ | 記録された出力（`design_pr`）が重なった文脈でも、返す入力に `design_pr` が無い | 無い ID → `Refused`。判定のステップを持たないモード → `Refused`。`mvv_ref` が文脈に無い → `StageInputMissing` |
| `_mvv_inputs` | 上の表の 6 行 | — | 両方ある → `invalid` |
| `_mvv_gate`（両入口） | 同じステートマシンの文脈で、`NDF_STAGE_INPUT`（プランが組む入力）と `--machine` の 2 回の起動が同じ入力で `gate` を呼び、同じ PR の番号を読み、同じ判定のスタブの結果から同じ `mvv_gate` を返す | 文脈の `design_pr` が `worktree` のブランチの PR と違う → 両方とも `worktree` のブランチの PR を判定する | — |
| `GATE_STEPS` | 定義の `design.json`・`deploy.json` に同じ id のステップがある | — | — |

## 手順書

満たす決定: 決定 1〜5、受け入れ条件 1・3・4・7・8

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/pace.md` | 具体例の最後の段落（`normal` は判定しない）を「判定し、省かない」に変える。「MVV 判定」の表から順 2 を外し、機械の検査に 7（レビューの保留）を足し、検査 1 だけが判定を止めること・ほかは省くかだけを決めることを書く。`--machine` と `--gate fix` の行を足す |
| `skills/development-workflow/references/approval-request.md` | 「MVV 判定」の行の出所を、進め方によらず判定の記録（PR のコメント）にし、保留（`holds`）を足す。`pace: normal` は助言、の注記を消す。承認資料を渡す前に `ndf records mvv cite` を打つ手順を足す |
| `skills/development-workflow/references/agent-layers.md` | supervisor が守る規則に、承認ゲートを返す前の `--machine` の判定と `cite` を足す |
| `skills/development-workflow/references/project-mvv.md` | 「工程での読み方」の承認ゲートと即時修正の行（判定はどの進め方でも打つ。即時修正はマージの前に判定する） |
| `docs/contracts/commands.md` | `ndf records mvv gate` の `--machine`・`--gate fix`・`--base`・`--ci-result`・`--judged-out` と、`ndf ci merge` の `--judged`、`ndf ci watch` の成功の `instance` と、`ndf records mvv cite` の行 |
| `docs/glossary/glossary.json` | 語「レビューの保留」を追加、「MVV 判定」の意味を変え、`ndf records glossary render` で `docs/glossary.md` を作り直す |
