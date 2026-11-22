# #3959 #4055 の詳細設計: MVV 判定（判定を打つ条件・レビューの保留・即時修正・根拠の行・プランの外の入口）

設計文書 [issue-3959-4055-design.md](./issue-3959-4055-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。前提は #4076 の実装が main へ入った後の形である（`records/mvv.py` の `current`・`Snapshot`・`principles`・`record_review`、`records/mvv_decide.py` の `citation_errors`・`fenced_lines`・`items_of`、`crossreview/drive.py` の `_summary` が `record_review` を呼ぶ形）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/workflow/context.schema.json      … mvv_gate.gate に fix
├── contracts/templates/fix.json                … ステップ mvv・merge-gate・判定した head でのマージ
├── contracts/templates/parts/steps.json        … 部品 mvv・fix-merge
├── src/ndf/domains/records/
│   ├── mvv_gate.py                              … 判定を打つ条件と省く条件を分ける・区分 fix・保留を読む
│   ├── mvv.py                                   … review_rows・cite
│   ├── mvv_decide.py                            … review_holds
│   ├── mvv_material_decide.py                   … gate_outcome に保留
│   └── cli.py                                   … --machine・--gate fix・cite
├── src/ndf/progress/gate/checks.py             … MvvFacts.review_holds・check_review_holds・区分ごとの検査・note
├── src/ndf/progress/command.py                 … stage_inputs_of（ID から判定のステップの入力）
├── src/ndf/domains/release/assemble.py         … included_numbers（今回のリリースに含む PR）
├── src/ndf/domains/crossreview/
│   ├── drive.py                                 … _summary が record_review の欠けた必須の行を Outcome へ
│   ├── finish_decide.py                         … finish(record_failed=…)
│   └── cli.py                                   … cmd_run が record_failed を渡す
└── tests/
    ├── domains/records/test_mvv_gate.py
    ├── domains/records/test_mvv_review_holds.py *
    ├── domains/records/test_mvv_cite.py *
    ├── domains/records/test_mvv_record_review.py
    ├── domains/release/test_included_numbers.py *
    ├── progress/gate/test_gate_checks.py
    ├── progress/test_command_mvv_inputs.py *
    ├── domains/crossreview/test_crossreview_finish.py
    └── route/test_templates.py
```

依存の向き: `records/cli.py` が `progress.command`（文脈を読む）と `records.mvv_gate` を読む。`mvv_gate` は今どおり `progress.gate.checks` と `progress.definition` を読み、リリースの対象の PR を決めるために `release.assemble` を読む（`release` は `records` を読まない）。`mvv_decide` は git もファイルも読まない純粋な関数だけを持つ。`progress` は `records` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートと即時修正の前の判定 | `checks`・`mvv`・`mvv_decide`・`mvv_material_decide`・`release.assemble` | モジュール | `gate`・`_judged`・`pr_number`（変更）・`snapshot_facts`・`review_prs`・`gate_label`（新設）・定数 `FIX`（新設）。`base_facts` は消す |
| `src/ndf/progress/gate/checks.py` | 変更 | 省かない理由の検査 | — | `MvvFacts`（変更） | `check_review_holds`（新設）・`refusals`（変更）・`judgeable`（新設）・`may_skip`・`note`（変更）・定数 `CHECKS_BY_GATE`（新設） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の記録の読み書き | `jsonl`・`mvv_decide` | `ReviewWrite`（新設）、モジュール | `review_rows`・`cite`（新設）・`record_review`（返り値を変える） |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | — | モジュール | `review_holds`（新設） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力 | — | モジュール | `gate_outcome`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv_gate`・`progress.command` | モジュール | `_mvv_gate`（変更）・`_mvv_inputs`・`_mvv_cite`（新設） |
| `src/ndf/progress/command.py` | 変更 | ステートマシンの操作 | `store`・`invoke` | モジュール | `stage_inputs_of`（新設） |
| `src/ndf/domains/release/assemble.py` | 変更 | リリースの承認資料の組み立て | `gather`・`since_decide` | モジュール | `included_numbers`（新設。承認資料と同じ範囲の含む PR の番号）・`_facts`（変更。番号はこの関数から得る） |
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
| `gate` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `_judged` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `pr_number` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `design` は `inputs["worktree"]`（文字列のとき。無ければ `root`）のブランチの開いた PR。`fix` は `root` のブランチの開いた PR。`release` は今のまま（文脈の `pr`）。文脈の `design_pr` は読まない（プランの経路の入力に無いので、両入口で同じ PR を選ぶため） |
| `snapshot_facts` | `records/mvv_gate.py` | 新設 | `snapshot_facts(root: Path, snap: mvv.Snapshot, inputs: Mapping[str, Any]) -> tuple[MvvFacts, str]` | `base_facts` の置き換え。MVV の状態・本文の sha256・承認の記録の sha256・本文をすべて `snap`（ベースの MVV）から作る。作業ツリーの `.ndf/mvv.json`・`.ndf/mvv.md` は読まない。`boundary_paths` は今どおり作業場所の設定から読む |
| `review_prs` | `records/mvv_gate.py` | 新設 | `review_prs(root: Path, gate: str, number: int, hub: GitHub) -> list[int]` | 保留を読む PR。下の「レビューの保留」の接ぎ目 |
| `gate_label` | `records/mvv_gate.py` | 新設 | `gate_label(gate: str) -> str` | `fix` は `即時修正のマージ`、ほかは `model.gate_label` |

- `gate`
  - 手順: (1) 区分が `fix` でなく、モードの定義がそのゲートを持たなければ、今どおり判定せずに返す。(2) `_judged` を呼ぶ。(3) PR を読む前の例外（`GateError`・`GitError`・`GitHubError`・`MvvError`・`JsonFileError`・`IdentityError`・`OSError`）は今どおり `verdict: null` と「MVV 判定を打てない: <理由>」
  - 進め方の条件（`inputs["pace"] not in MVV_PACES` で返す行）は消す。進め方は `_judged` の最後の「省くか」でだけ読む
- `_judged`
  - 手順: (1) `pr_number` で PR を決め、`hub.pull` で head と `base_ref` を読む。(2) `git fetch <リモート> <base_ref>` を 1 回打ち、`snap = mvv.current(root, md.signals_ref(base_ref, リモート))` で対象の PR のベースの MVV を 1 度だけ読む。この後の検査 1・判定の材料・保留の版・`fix` の例外は、どれもこの `snap` だけを使う（作業ツリーの MVV を読まない。PR が `.ndf/mvv.json` を消しても、ベースが承認済みなら検査 1 を通って判定し、検査 5 が外れる）。(3) `snapshot_facts` で事実を作る。`judgeable` が理由を返せば、`gate_outcome(gate, None, skip, [理由], head)` で返し、コメントは書かない（承認済みの MVV が無いリポジトリの出力と PR を今と同じにする）。`skip` は `fix` で `snap.state` が `absent`・`unapproved` のときだけ真（理由は「MVV が承認されていないので判定しない」）、ほかは偽（`mismatch`・`unreadable` の `fix` は止まる）。(4) 本文・差分を読み、`pr_facts` で事実を足す。(5) 保留を求める（下の「レビューの保留」）。(6) `mm.incomplete_patches(rows)` があれば、`refusals(facts, gate)` の理由とあわせて `verdict: null` で記録して返す。(7) LLM の判定（今と同じ材料と `mvv_validate_sprint`）。`GateError`（LLM が回答しない）はここで受け、`verdict: null`・`skip` 偽・`why` は「MVV 判定を打てない: <理由>」と `refusals(facts, gate)` の理由、`holds`・`head` は読んだ値のまま (9) へ進む。(8) `skip, why = may_skip(facts, verdict, gate)`。進め方が `MVV_PACES` に無い（`design`・`release`）なら `skip` を偽にし、`why` の先頭に「進め方 <値> は承認ゲートを MVV 判定で省かない」を足す。`fix` は進め方を見ない。(9) `_recorded` で PR のコメントへ書いて返す
  - 事後条件: `verdict` が null なのは、(3)・(6)・(7) の LLM の失敗・PR を読む前の例外のときだけ。`normal` の `skip` は常に偽。PR を読んだ後の出力は `head` を持つ
  - 副作用: (3) を通った後は、どの終わり方でも PR のコメントを 1 件書く（今と同じ）。ベースの取得（`git fetch` 1 回）と写しの書き込みが増える（`current` の副作用）
- `_recorded`: `note(gate_label(outcome["gate"]), outcome["verdict"], outcome["why"], outcome.get("holds", ()))` を書く。`gate_outcome` は保留が空なら `holds` を置かないので、キーを必須として読まない
- `note`（`checks.py`）: シグネチャを `note(label: str, verdict: Any, why: Sequence[str], holds: Sequence[str] = ()) -> str` に変え、見出しに `label` を使い、`holds` が空でなければ「### レビューの保留」の箇条書きを足す。呼び手は `mvv_gate._recorded` だけ
- `gate_outcome`（`mvv_material_decide.py`）: 引数 `holds: Sequence[str] = ()` を足し、空でなければ出力に `holds` のキーを置く。`context.schema.json` の `$defs.mvv_gate` に任意のキー `holds`（文字列の配列）を足す

### 処理の手順

| 区分 | 進め方 | 検査 1 | 検査 2〜7 の理由 | 判定 | `skip` | `fix` の結果 |
| --- | --- | --- | --- | --- | --- | --- |
| `design`・`release` | `normal` | 通る | 問わない | 打つ | 偽 | — |
| `design`・`release` | `fast`・`auto` | 通る | 無い | 打つ | 「従う」・レッドライン空で真 | — |
| `design`・`release` | `fast`・`auto` | 通る | ある | 打つ | 偽 | — |
| どれも | どれも | 外れる | — | 打たない（`verdict: null`） | `fix` でベースの MVV が `absent`・`unapproved` なら真、ほかは偽 | `skip` が真なら `ok`、偽（`mismatch`・`unreadable`）なら `stopped`（`refused`） |
| `fix` | 見ない | 通る | 無い | 打つ | 「従う」・レッドライン空で真 | `skip` が真なら `ok`、偽なら `stopped`（`refused`） |
| `fix` | 見ない | 通る | ある | 打つ | 偽 | `stopped`（`refused`） |

`fix` の `skip` は「マージへ進めてよい」を表す。出力の形を区分で変えないため、同じキーを使う。「MVV が承認されていない」の例外も判定と同じ `snap` から `_judged` が決め、CLI は MVV を読み直さない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_judged` | LLM が回答しない | 上限・接続 | `_judged` の (7) で `GateError` を受ける | `verdict: null`。読んだ `head`・`holds` と理由を `_recorded` へ渡し、PR のコメントを書く | `design`・`release` は `ok` で人へ問う。`fix` は `stopped`（`refused`） | 打ち直せる |
| `_judged` | ベースの MVV を読めない | ベースを取得できない・設定が壊れている | `git fetch` の `GitError`・`current` の `MvvError` | `verdict: null`（省かない）。作業ツリーの MVV で代えない | 同上 | 直して打ち直す |
| `_judged` | 差分の全体が材料に載らない | 大きな PR | `incomplete_patches` | 判定せず記録 | 同上 | PR を分ける |
| `pr_number` | 開いた PR が無い | PR を出していない | `GateError` | `verdict: null` | 同上 | PR を出して打ち直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate`（`normal`） | 判定のスタブが 1 回呼ばれ、`verdict` を持ち `skip` は偽、`why` の先頭に normal の理由、コメントが 1 件。保留が無い（`holds` のキーが無い）判定もコメントまで通る | 判定が「従う」・レッドライン空でも `skip` は偽 | LLM の失敗 → `verdict: null` で `ok`。出力は `head` と `holds` を持ち、コメントが 1 件で保留と理由が載る |
| `gate`（`fast`） | 今と同じ材料で「従う」→ `skip` 真 | 検査 4・5・3 が外れても判定は打たれ `skip` は偽 | 検査 1 が外れる → 判定のスタブを呼ばない |
| `gate`（`fix`） | モードの定義を問わずに判定する | 文脈の無い入力（空の辞書）で動く | ベースが承認済みで、head が `.ndf/mvv.json` を消した PR → 判定が打たれ、検査 5 で `skip` 偽（作業ツリーの `absent` で `ok` にならない）。ベースが `absent` で head が MVV を足した PR → 判定せず `skip` 真 |
| `snapshot_facts` | 事実がすべて `snap` の値 | 作業ツリーの本文が `snap` と違っても事実は `snap` のまま | — |
| `pr_number` | `worktree` の指すブランチの PR | 文脈に `design_pr` があっても読まない | 開いた PR が無い → `GateError` |
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
| `included_numbers` | `release/assemble.py` | 新設 | `included_numbers(root: Path, hub: GitHub, target: Target, strategy: Strategy) -> frozenset[int]` | 承認資料（`collect`）と同じ範囲の決め方で、今回のリリースに含む PR の番号を返す。版上げの計画と `pull_facts`（PR ごとの CI の読み取り）はしない |

- 接ぎ目（`mvv_gate._judged` の手順 (5)。`review_prs`）: `prs` は `design`・`fix` では判定する PR の 1 件。`release` では文脈の `pr` と、`included_numbers` が返す今回のリリースに含む PR の和である。`included_numbers` は `collect` の範囲の取り方（`since_decide.kind_of` でブランチ戦略から `base`・`trunk`・`units`・`pr` を決め、`_span`・`_since_by_unit`・`gather.included` で範囲の中の PR を集め、`production_pr` は `target.pr` を足す）をそのまま使い、`_facts` もこの関数から番号を得る（承認資料の「作るもの」と保留の対象を 1 つの規則で決める）。`collect` と違い、作業場所の HEAD が範囲の終点かは問わない（参照だけを読む）。`Target` はブランチ戦略の宣言（`branch_strategy.load(root)`）のベースブランチ・本番ブランチ・リモートと本番のチャネルで組み、`units`（`release_branch`）は文脈の `pr` の head のブランチ、`pr`（`production_pr`）は文脈の `pr` にする。これで `trunk` の `release_pr` のように版上げの PR の差分に単位の PR が現れない形でも、前のリリースの後に本番ブランチへ入った PR の保留を拾う。範囲を決められない（`AssembleError`・`GitError`・`GitHubError`）ときは `GateError` に変えて `verdict: null`（省かない）。上げるものが無い（`NothingToRelease`）ときは文脈の `pr` だけにする。`mvv` は `snap` の（`version` の文字列・`sha256`）。`repo` は `mvv.repo_key(root)`。`review_rows` が `OSError` か `UnicodeDecodeError`（`jsonl.read_rows` が不正なバイトで投げる。`OSError` の子でない）を投げたら、保留を「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」の 1 件にする（#3959 の本文の異常系の `review_rows` の行の検出は、この 2 つの例外と読む）。結果を `MvvFacts.review_holds` に入れ、`gate_outcome` の `holds` にも同じ値を渡す（承認資料と PR のコメントは同じ結果を読む）
- `record_review`（#3959 の本文の「記録の欠落」の書き手の契約。#4076 の `-> int` と `OSError` の契約を置き換える）: (1) 書く行を、必須の行（`redline`・`deferred`）を先に、ほか（`fixed`・`unresolved`）を後に並べる（同じ種類の中は今の順）。重ねない判定（同じキー・同じ終わり方の最後の行があれば書かない）は今のまま。(2) 1 行ずつ追記し、`OSError` が出たらそこで止め、例外を外へ出さない。(3) 書けなかった行と後ろの試さなかった行のうち必須のものを `missing`、原因を `cause` にして返す。必須の行を先に書くので、通常の行の失敗は `missing` を作らない。`_summary` は `missing` が空でなければ `Outcome.mvv_record_failed` に `{"rows": [<key と outcome>], "cause": cause}` を入れ、空で `cause` があれば今どおり注意に留める。打ち直すと、重ねない判定で書けた行は飛ばし、欠けた行だけを書く
- 保留は判定を止めない。LLM の判定の後の `may_skip` でだけ効く（決定 1）
- 承認資料: `approval-request.md` の「MVV 判定」の行の出所を「判定の記録の `holds` と判定・理由・レッドライン」にする。規則を手順書に写さず、記録の値を写す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_holds` | 本文の「テストで落とす壊し方」の表のとおり | 同 | 同 |
| `_judged`（保留） | `release` はスプリントの PR と単位の PR の行を読む。保留があると `fast` で「従う」でも `skip` は偽、`why` と `holds` に載る | 記録のファイルが無い → 保留は空 | `review_rows` の `OSError` → 読めない理由の 1 件。不正なバイト（`b"\xff\xfe"` を含む行）を書いた実際のファイル → 判定は打たれ、`holds` と PR のコメントに読めない理由の 1 件、`fast` でも `skip` は偽 |
| `included_numbers` | `merge_base_branch`（スプリントブランチ）で単位の PR を返す | `trunk` の `release_pr`: 前のリリースのタグの後に本番ブランチへ入った PR を返し、版上げの PR の差分に無い PR も含む。前のリリースより前の PR は返さない | 範囲を決められない → `AssembleError` |
| `_judged`（`release` の保留） | `trunk` の `release_pr` で、版上げの PR の差分に現れない単位の PR に `redline` の行 → `fast`・`auto` で「従う」でも `skip` は偽、`holds` にその PR | — | `included_numbers` の失敗 → `verdict: null` |
| `record_review` | 全部書ける → `missing` 空・`cause` None | 渡した順が `fixed`・`redline` でも `redline` を先に書く。2 行目（通常の行）で失敗 → `missing` 空・`cause` あり。1 行目（必須の行）で失敗 → `missing` に失敗した行と後ろの試さなかった `deferred` | 失敗の後に打ち直す → 書けた行を重ねず、`missing` の行だけを書く |
| `finish`・`cmd_run` | 本文の表のとおり | 同 | 同 |

## 即時修正のプラン

満たす決定: 決定 3、受け入れ条件 6

| ファイル | 変える中身 |
| --- | --- |
| `contracts/templates/parts/steps.json` | 部品 `mvv` を足す: `{"id": "mvv", "run": ["ndf", "records", "mvv", "gate", "--gate", "fix", "--head-out", "{state_dir}/mvv-head"]}`。原因は結果 JSON の `cause.kind`（`refused`）から読み、`exit_cause` は置かない。部品 `fix-merge` を足す: id は `merge`、`run` は `["sh", "-c", "r=$(cat \"$0\") && [ -n \"$r\" ] || exit 2; exec ndf ci merge --branch \"$1\" --method \"$2\" --reviewed \"$r\"", "{state_dir}/mvv-head", "{{branch}}", "{{merge_method}}"]`、`probe` は部品 `merge` と同じ |
| `contracts/templates/fix.json` | `steps` を `test`・`push`・`pr`・`conflict-check`・`ci`・`{"use": "mvv", "on_cause": {"refused": "merge-gate"}, "next": "merge"}`・`{"use": "merge-gate"}`・`{"use": "fix-merge", "next": "end"}` にする。`params` に `gate`（`params.json` の既定 `ndf answer ask`）を足す。`summary` に MVV 判定と、進めない判定のときは人の承認でマージすることを足す |

- 判定した head でマージする: `_mvv_gate` の `fix` は、`--head-out <パス>` に `outputs.mvv_gate.head`（判定した PR の head。PR を読めなければ空）を書いてから結果を返す（`ok`・`stopped` のどちらでも書く）。`fix-merge` はそれを `ndf ci merge --reviewed` に渡す。`ndf ci merge` は今どおり、`--reviewed` が作業場所の HEAD と違えば PR を読まずに `refused` で止まり、マージは作業場所の HEAD を `expect_head` にして PR の head と照らす。判定の後に同じ worktree で直して push すると、作業場所の HEAD が判定した head と違うので止まる。PR は判定と同じく `--branch` の開いた PR で、その head が判定した SHA と一致することをこの 2 つの照合が保証する。ファイルが無い・空なら終了コード 2 で止まる（判定を通らない head をマージしない）。止まったら、プランを `mvv` から打ち直して今の head で判定し直す
- 判定が進めないとき（`refused`）の扱い: `mvv` は `merge-gate`（既存の部品。`{{gate}}` で人の回答を待ち、`approved` は `merge`、`rejected` は `end`）へ進む。人は PR のコメントの判定の記録（理由・レッドライン・保留・外れた検査 4・5・7）を読んで決める。承認したら `fix-merge` が判定した head のままマージする（承認の後に head が動けば上の照合で止まる）。PR を直して進めるときは `rejected` で終え、直したコミットでプランを打ち直す（`mvv` が新しい head で判定し直す）。判定を通らずに手でマージする経路は作らない。検査 4・5 で外れる即時修正（レッドラインのパス・MVV の設定を変えるもの）も同じ経路で人が承認してマージする（受け入れ条件 6 の「機械の検査が外れていないときだけ進む」を保つため、検査を注意に下げない）。`precondition`・`invalid` など `refused` 以外の原因は今どおり止まって人へ戻る
- CLI（`_mvv_gate` の `fix`）: `gate` の出力の `skip` が真なら `ok`（`summary`: 判定したときは「MVV 判定: マージへ進める」、ベースの MVV が承認されていないときは `why` の「MVV が承認されていないので判定しない」）。偽なら `result.stopped(TOOL, "refused", "MVV 判定でマージへ進めない: " + " / ".join(why))` に `outputs.mvv_gate` を添える。MVV の状態を CLI で読み直さない（判定と例外を `_judged` が同じベースの `snap` から決める）

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fix.json` | プランを読み込むと、ステップの順が test・push・pr・conflict-check・ci・mvv・merge-gate・merge。`mvv` の `refused` は `merge-gate`、成功は `merge` へ | — | — |
| `_mvv_gate`（`fix`） | 「従う」・レッドライン空・保留無し → 終了コード 0、`--head-out` に判定した head | ベースの MVV が `absent` → 0 で判定しない（head は書く） | 「判定できない」・レッドラインあり・保留あり・ベースが `mismatch` → 1・`refused`、head は書く。PR を読めない → head は空 |
| `fix-merge` | 判定した head と作業場所の HEAD が同じ → `ndf ci merge` に `--reviewed` が渡る | 判定の後に作業場所でコミットした → `ndf ci merge` が `refused` | `mvv-head` が無い・空 → 終了コード 2 |
| `merge-gate`（`fix`） | `refused` の後に承認 → 判定した head でマージ | 承認の後に head が進んだ → マージが `refused` | 却下 → `end`（マージしない） |

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
| `_mvv_gate` | `records/cli.py` | 変更 | シグネチャは同じ。引数 `--head-out <パス>`（`fix` だけ）を足す | `_mvv_inputs` の後に `gate` を呼ぶ。`fix` は上の「即時修正のプラン」の結果にする |

| 区分 | `NDF_STAGE_INPUT` | `--machine` | 入力 |
| --- | --- | --- | --- |
| `design`・`release` | ある | 無い | 環境変数の JSON（今と同じ） |
| `design`・`release` | 無い | ある | `stage_inputs_of(root, ID, "design-mvv" か "release-mvv")` |
| `design`・`release` | ある | ある | `invalid`（「入力の出所が 2 つある」） |
| `design`・`release` | 無い | 無い | `invalid`（今と同じ文に `--machine` を添える） |
| `fix` | 問わない | ある | `invalid`（「即時修正はステートマシンを持たない」） |
| `fix` | 問わない | 無い | 空の辞書 |

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
| `docs/contracts/commands.md` | `ndf records mvv gate` の `--machine`・`--gate fix`・`--head-out` と、`ndf records mvv cite` の行 |
| `docs/glossary/glossary.json` | 語「レビューの保留」を追加、「MVV 判定」の意味を変え、`ndf records glossary render` で `docs/glossary.md` を作り直す |
