# #3959 #4055 の詳細設計: MVV 判定（判定を打つ条件・レビューの保留・根拠の行・プランの外のエントリポイント）

設計文書 [issue-3959-4055-design.md](./issue-3959-4055-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。

前提は #4076 の実装が main へ入った後の形である。前提にする部品は、`records/mvv.py` の `current`・`Snapshot`・`principles`・`record_review`・`fenced_lines`・`decision_lines`、`records/mvv_decide.py` の `citation_errors`・`items_of`、`crossreview/drive.py` の `_summary` が `record_review` を呼ぶ形である。#4076 の実装は #4090・#4099 の設計（PR #4121）の直しを含む。そのため #4076 の部品の形は、確定仕様と今のコードの形で読む。確定仕様は [ndf-mvv-current-and-stage-inputs.md](../docs/specifications/ndf-mvv-current-and-stage-inputs.md) と [cross-review-project-mvv.md](../docs/specifications/cross-review-project-mvv.md) である。設計の文書は実装が終わって消した。

#4090・#4099 の決定 16 は、#3959 と重なる 5 点を挙げる。5 点は `Snapshot.commit`・`cite` の `mismatch`・`cite` が使う確かめの関数・`record_review` の契約・`review_holds` の規則である。この文書は、5 点を決定 16 の「#3959 の実装がすること」の列の形で書いた（設計の決定 11）。

この文書で使う語の意味は、末尾の「[用語](#用語)」にある。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/workflow/context.schema.json      … mvv_gate に pr・holds・noted・note_url・stopped_by
├── contracts/workflow/stage-io.schema.json     … stage-in をプランが渡す平らな形に直す
├── contracts/data/review-criteria.json         … 見送りの区分の選び方
├── src/ndf/progress/definition.py              … validate_stage_input を平らな形で確かめる
├── src/ndf/progress/invoke.py                  … resolve_inputs が validate_stage_input で確かめる
├── src/ndf/domains/records/
│   ├── mvv_gate.py                              … 判定を打つ条件と省く条件を分ける・ベースの MVV から事実を作る・保留を読む
│   ├── mvv.py                                   … validation_rows・review_rows・mvv_validate_sprint・record_review・cite・compare_files
│   ├── mvv_decide.py                            … review_holds
│   ├── mvv_material_decide.py                   … gate_outcome に保留・diff_rows
│   └── cli.py                                   … --machine・入力の形の確かめ・cite
├── src/ndf/progress/gate/checks.py             … MvvFacts.review_holds・check_review_holds・区分ごとの検査・note
├── src/ndf/progress/gate/asking.py             … プランの承認資料に「MVV 判定」の節（mvv_lines）
├── src/ndf/progress/gate/entry.py              … present が承認資料を書く前に PR の今の head を読む
├── src/ndf/progress/gate/answering.py          … github_observe が今の head の資料のハッシュを返す
├── src/ndf/progress/store.py                   … 保存先の閲覧範囲を限る・限れない保存先では mvv_gate の自由記述を伏せて書く
├── src/ndf/progress/stage_inputs.py *          … stage_inputs_of（ID から判定のステップの入力。リポジトリを照合する）
├── src/ndf/progress/model.py                   … MVV_SKIP_GATES・hold_checked・mvv_skip・mvv_answer_void（リリース承認ゲートと、保留を確かめていない判定で省かない）
├── src/ndf/progress/run.py                     … _running_move・_consume が数えない service:mvv の回答を読まず、それで済んだ承認ゲートを開き直す
├── src/ndf/progress/gate/records.py            … approved_head が区分の外の service:mvv の回答を承認にしない
├── contracts/domains/ndf-files.json            … 診断の記録 mvv-gate-failures の行
├── contracts/schemas/internal/mvv-gate-failure.schema.json * … 診断の記録の行の形
├── contracts/schemas/internal/mvv-validation.schema.json     … mvv-review の行に任意の欄 defer
├── src/ndf/contracts/internal.py               … 記録の種類 mvv-gate-failure
├── src/ndf/adapters/publish.py                  … 秘密を含む参照を公開範囲の照会へ送らず伏せる・秘密と参照の重なる範囲を 1 回で伏せる
├── src/ndf/adapters/run_log.py                  … secret_spans（秘密の一致範囲）
├── src/ndf/domains/crossreview/
│   ├── drive.py                                 … _summary が record_review の欠けた必須の行を Outcome へ・_fix_prompt に見送りの区分
│   ├── journal.py                               … apply_fix が見送りの区分 defer を持つ
│   ├── finish_decide.py                         … finish(record_failed=…)
│   └── cli.py                                   … cmd_run が record_failed を渡す
└── tests/
    ├── adapters/test_publish.py
    ├── adapters/test_cli_run_log.py
    ├── domains/records/test_mvv_gate.py
    ├── domains/records/test_mvv_review_holds.py *
    ├── domains/records/test_mvv_cite.py *
    ├── domains/records/test_mvv_record_review.py
    ├── domains/records/test_mvv.py
    ├── progress/gate/test_gate_checks.py
    ├── progress/gate/test_gate_present.py
    ├── progress/test_run.py
    ├── progress/gate/test_gate_after_approval.py
    ├── progress/test_command_mvv_inputs.py *
    ├── progress/test_invoke.py
    ├── progress/test_definition.py
    ├── domains/crossreview/test_crossreview_journal.py
    └── domains/crossreview/test_crossreview_finish.py
```

依存の向き: `records/cli.py` が `progress.stage_inputs`（文脈を読む）と `records.mvv_gate` を読む。`mvv_gate` は今どおり `progress.gate.checks` と `progress.definition` を読む。`mvv_decide` は git もファイルも読まない純粋な関数だけを持つ。`progress` は `records` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの前の判定 | `checks`・`mvv`・`mvv_decide`・`mvv_material_decide` | モジュール | `gate`・`_judged`・`pr_number`・`boundary_paths`・`_recorded`（変更）・`snapshot_facts`・`review_prs`・`failure_text`（新設）・定数 `GATE_STEPS`・`GATE_STAGES`（新設）。`base_facts` は消す |
| `src/ndf/progress/model.py` | 変更 | ステートマシンの型と規則 | — | モジュール | 定数 `MVV_SKIP_GATES`・`MVV_GATE_STEPS`・`hold_checked`・`mvv_answer_void`・`mvv_stored_view`（新設）・`mvv_skip`（変更） |
| `src/ndf/progress/run.py` | 変更 | ステートマシンを読んで工程を流す | `model` | モジュール | `_running_move`・`_consume`（変更） |
| `src/ndf/progress/gate/records.py` | 変更 | 承認ゲートの回答の記録の読み手 | `model` | モジュール | `approved_head`（変更） |
| `src/ndf/progress/gate/checks.py` | 変更 | 省かない理由の検査 | — | `MvvFacts`（変更） | `check_review_holds`（新設）・`refusals`（変更）・`judgeable`（新設）・`may_skip`・`note`（変更）・定数 `CHECKS_BY_GATE`（新設） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の記録の読み書き | `jsonl`・`mvv_decide` | `ReviewWrite`（新設）、モジュール | `validation_rows`・`review_rows`・`cite`・`record_failure`・`compare_files`（新設。`_judged` は `pr_files` を呼ばず、`pr_facts` には読み済みの行を渡す）・`record_review`（返り値を変える）・`mvv_validate_sprint`（引数 `rows` と返り値を変える） |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | — | モジュール | `review_holds`（新設） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力 | — | モジュール | `gate_outcome`（変更）・`diff_rows`・`unquote_path`・`DiffError`・定数 `STOP_REASONS`（新設） |
| `contracts/domains/ndf-files.json`・`contracts/schemas/internal/mvv-gate-failure.schema.json`（新設）・`src/ndf/contracts/internal.py` | 変更 | 診断の記録の置き場と行の形 | — | — | 表 ndf-files に `mvv-gate-failures`（保存先 `mvv-state`・`mvv-gate-failures.jsonl`・追跡しない・`ends: kept`）、`internal` の種類 `mvv-gate-failure`（`reading-check` と同じ登録の形） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` のエントリポイント | `mvv_gate`・`progress.stage_inputs` | モジュール | `_mvv_gate`（変更）・`_mvv_inputs`・`_env_inputs`・`_mvv_cite`（新設） |
| `src/ndf/progress/definition.py` | 変更 | 工程の定義と規約の読み手 | `contracts` | `StageInputErrors`（新設）、モジュール | `validate_stage_input`（入れ子の `context` でなく、入力の object そのものを確かめる）・`stage_input_errors`・`stage_io`（新設） |
| `src/ndf/progress/invoke.py` | 変更 | 工程から作業を呼ぶ層 | `definition` | モジュール | `resolve_inputs`（取り出した入力を `validate_stage_input` で確かめる） |
| `contracts/workflow/stage-io.schema.json` | 変更 | 工程の入力と出力の型 | `context.schema.json` | — | `$defs.stage-in` を文脈のスキーマへの参照にし、`context`・`base`・`head` の囲みを外す |
| `src/ndf/progress/stage_inputs.py` | 新設 | ステートマシンの ID から判定のステップの入力を組む | `store`・`invoke` | モジュール | `stage_inputs_of`（新設）。`progress/command.py` に置くと、ファイルの行数が `ndf check` の構造の規則の上限（1,000 行）を超えるので、新しいモジュールに置く（実装計画 `issues/issue-3959-plan.md` の「修正対象」） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認ゲートの質問と承認資料 | — | モジュール | `mvv_lines`（新設）・`material`・`material_path`（変更。資料を作る時点の head ごとに別のファイル）・`write_material`（変更。0600 で、別名の一時ファイルから置き換えずに置く） |
| `src/ndf/progress/gate/entry.py`・`src/ndf/progress/gate/answering.py` | 変更 | 承認ゲートの待ちを質問と承認資料に差し替える・回答と対象を照らす | `asking` | モジュール | `present`（変更。PR の今の head を読んでから、その head の資料を使うか書く）・`github_observe`（変更。今の head の資料のハッシュを返す）・`_docs_only_after`（変更。承認した人が見た head の資料のハッシュと比べる） |
| `src/ndf/progress/store.py` | 変更 | ステートマシンの保存 | `model` | `Store`（変更）、モジュール | 保存先を開くとき、ディレクトリを 0700、`NDF_PROGRESS_DB` の SQLite のファイルと既にある副ファイルを 0600 にし、限れたかを `Store.private` と `conn.info["ndf_private"]` に持つ。`append_events`（変更。限れない保存先では `mvv_gate` を `model.mvv_stored_view` に通して書く）（「手元のファイルの閲覧範囲」） |
| `src/ndf/adapters/publish.py` | 変更 | 書き先へ出す本文の検査 | `run_log` | `Publisher`（変更） | `Publisher.guarded`（変更。引数 `assume_public: bool = False` を足し、真なら書き先の公開範囲を照会せず公開として伏せる。呼び手は `_mvv_cite` だけ）・`Publisher._private_refs`（変更。秘密を含む参照を照会しない。`assume_public` が真なら書き先のリポジトリ自身への参照も照会し、公開でなければ伏せる）・`scrub`（変更。秘密と参照の一致範囲の和を 1 回で置き換える） |
| `src/ndf/adapters/run_log.py` | 変更 | 秘密の伏せ字 | — | — | `secret_spans`（新設。秘密の一致範囲を返し、`redact` もそれで置き換える） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv` | `Outcome`（変更） | `_summary`・`_record_mvv`（変更。状態ファイルへ書く原因を伏せる） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | — | `Journal`（変更） | `apply_fix`（見送りの区分 `defer` を持つ） |
| `contracts/schemas/internal/mvv-validation.schema.json` | 変更 | MVV の記録の行の形 | — | — | 種類 `mvv-review` の行に任意の欄 `defer`（`needs_user`・`filed`・`not_applicable`）。知らない欄を拒むので、足さないと `record_review` が書けない（「レビューの保留」） |
| `contracts/data/review-criteria.json` | 変更 | 指摘の基準の文 | — | — | 見送りの区分の選び方 |
| `src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方の判定 | — | モジュール | `finish`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review` のエントリポイント | `finish_decide` | モジュール | `cmd_run`（変更） |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | — | `$defs.mvv_gate` に任意のキー `pr`・`holds`・`noted`・`note_url`・`stopped_by` |

## 判定を打つ条件と省く条件

満たす決定: 決定 1・3、受け入れ条件 1・2・3

### 機械の検査の一覧

検査の番号・中身・止めるものは、設計書の「[`ndf records mvv gate`](./issue-3959-4055-design.md#ndf-records-mvv-gate)」の検査表と同じで、食い違えば設計書に従う。検査 1 だけが判定を止め、検査 2〜7 は判定を止めずに承認ゲートの省略だけを止める。

| 番号 | 検査（`progress/gate/checks.py`） | 外れたとき |
| --- | --- | --- |
| 1 | PR のベースのプロジェクト MVV が承認済みで、本文の sha256 が承認の記録と一致する（`judgeable`） | 判定を打たない |
| 2 | スプリントの文脈の `mvv_ref` が、`snapshot_facts` が PR のベースから取得した最新の承認の記録（`.ndf/mvv.json` の `versions` の最後の要素。`mvv_decide.latest`）の `sha256` と一致すれば通す。`mvv_ref` が無ければ外れる（既存の `check_approval_record`） | 判定は打ち、省かない |
| 3 | モードが `operation` でも `documentation` でもない | 同上 |
| 4 | PR の変えたファイルが、ベースの `.ndf/pace.json` の `boundary_paths`（レッドラインのパス）に当たらない | 同上 |
| 5 | PR が `.ndf/mvv.md`・`.ndf/mvv.json` を変えない | 同上 |
| 6 | 差し戻しの後に出し直した PR では、PR 本文の「指摘への対応表」で「記載した」とした指摘ごとに、対応表が示す PR 本文のセクションがあり、中身が空でない（既存の検査 `check_claimed_sections`。#2853） | 同上 |
| 7（新設） | `design` では、レビューの記録 `mvv-validation.jsonl` を読み取った結果の保留が 0 件なら通す（`check_review_holds`）。記録がまだ作られていない場合は空として扱って通し、既存の記録を読めない場合は保留にする（`validation_rows`）。`release` では、#4105 まで保留を読まないので、この検査は何も返さない | 同上 |

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `judgeable` | `progress/gate/checks.py` | 新設 | `judgeable(f: MvvFacts) -> Optional[str]` | 判定を打てない理由（検査 1 だけ）。打てれば None |
| `CHECKS_BY_GATE` | `progress/gate/checks.py` | 新設 | `dict[str, tuple[Callable[[MvvFacts], Optional[str]], ...]]` | 区分ごとの省かない理由の検査。`design`・`release` とも、上の「機械の検査の一覧」の検査 1〜7 をその番号の順に並べる（検査 1 は判定を止め、検査 2〜7 は承認ゲートの省略を止める）。`release` は `review_prs` が空なので、#4105 までは保留が常に空になり、検査 7 は何も返さない（即時修正の区分は #4104 が足す） |
| `refusals` | `progress/gate/checks.py` | 変更 | `refusals(f: MvvFacts, gate: str = "design") -> list[str]` | 区分の検査の理由を集める。既定の区分は今の呼び手のため |
| `may_skip` | `progress/gate/checks.py` | 変更 | `may_skip(f: MvvFacts, verdict: Any, gate: str = "design") -> tuple[bool, list[str]]` | `refusals(f, gate)` を使う。ほかは今と同じ |
| `gate` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `_judged` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `pr_number` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `design` は `inputs["worktree"]`（文字列のとき。無ければ `root`）のブランチの開いた PR。`release` は今のまま（文脈の `pr`）。文脈の `design_pr` は読まない（プランの経路の入力に無いので、両方のエントリポイントで同じ PR を選ぶため）。`design` では、両方のエントリポイントで次の 2 つを確かめ、外れれば `GateError` にする（`gate` の手順 (3) で `verdict: null`・`stopped_by.kind: no_pr`・コメント無し）。(a) `worktree` の所属: `git -C <worktree> rev-parse --path-format=absolute --git-common-dir` が `root` の同じ値と一致する（同じリポジトリの worktree である）。worktree でない・別のリポジトリへ移設や再利用されたパスなら「作業場所が判定するリポジトリの worktree でない」で止め、そのパスのブランチ名で PR を探さない。(b) PR の一意性: そのブランチを head に持つ開いた PR のうち、宛先が起動元の GitHub リポジトリ（`hub.repo`）で、head のリポジトリも `hub.repo` のもの（フォークの同名ブランチを除く）だけを数える。0 件なら今どおり「開いた PR が無い」、2 件以上（同じブランチから別の宛先へ開いた PR など）なら「ブランチの開いた PR が複数あり、判定する PR を 1 つに決められない」で止め、どれにもコメントを書かない。`GateError` の文はブランチ名とパスを含みうるので、今どおり `failure_text` で診断の記録にだけ残す |
| `boundary_paths` | `records/mvv_gate.py` | 変更 | `boundary_paths(root: Path, ref: str) -> tuple[str, ...]` | レッドラインのパスを、作業ツリーでなくコミット `ref` の進め方の設定ファイルから読む。ファイルの位置は今の `boundary_paths` と同じく宣言から求める（`files.relpath("pace")`。`ndf-files` の `locations` の宣言から得るリポジトリ相対パスで、`.ndf/pace.json` を文字列リテラルで書かない）。内容だけを、固定したコミットから `git show <ref>:<そのパス>` で読む。`ref` に無ければ空。形が壊れていれば `jsonfile.JsonFileError` |
| `Snapshot` | `records/mvv.py` | 変えない（#4076 の実装が欄 `commit` を足す。#4090・#4099 の決定 9） | — | `current` が解決したコミットの SHA を `commit` に持つ。この Issue は欄を足さずに `snapshot_facts` で読む |
| `current` | `records/mvv.py` | 変えない（#4076 の関数） | — | 参照の解決はエントリポイントの 1 回だけで、呼び手は解決し直さない。ベースの本文が承認の記録と合わない（`mismatch`）ときの振る舞いは、引数 `allow_mismatch` で 2 つに分かれる。既定（`allow_mismatch=False`）では `MvvError` を投げる（#4090・#4099 の決定 15）。`allow_mismatch=True` では投げずに、状態 `mismatch` の `Snapshot` を返す。呼び手ごとの使い分けは次のとおり。`gate`（`_judged` の手順 (2)）は `allow_mismatch=True` で呼び、`mismatch` を検査 1 の外れとして扱う（コメントを書かず `noted: false`。ベースを読めない失敗のコメントは書かない）。`cite`（`_mvv_cite`）は既定のまま呼び、`MvvError` を `precondition` にする（確かめるバージョンを決められないまま `ok` にしない） |
| `snapshot_facts` | `records/mvv_gate.py` | 新設 | `snapshot_facts(root: Path, snap: mvv.Snapshot, inputs: Mapping[str, Any]) -> tuple[MvvFacts, str]` | `base_facts` の置き換え。MVV の状態・本文の sha256・承認の記録の sha256・本文をすべて `snap`（ベースの MVV）から、`boundary_paths` を `boundary_paths(root, snap.commit)` から作る。作業ツリーの `.ndf/mvv.json`・`.ndf/mvv.md`・`.ndf/pace.json` は読まない（PR が `boundary_paths` から自分の変えるパスを外しても、今回の変更にはベースの規則が効く） |
| `compare_files` | `records/mvv.py` | 新設（`pr_files` の置き換え。呼び手は `_judged` だけ） | `compare_files(hub: GitHub, g: git.Git, remote: str, base: str, head: str) -> list[dict[str, Any]]` | `GET /repos/<repo>/compare/<base>...<head>` を 1 回打ち、応答の `files`（`pulls/<番号>/files` と同じ形の行）を返す。`base`・`head` は SHA。基点を SHA で固定するので、読む間に宛先が進んでも差分は変わらない。GitHub の比較は `files` を比較全体で 300 件（定数 `COMPARE_FILES_CAP = 300`）までしか返さない（[GitHub REST API の比較の仕様](https://docs.github.com/en/rest/commits/commits#compare-two-commits) の「Working with large comparisons」）ので、`files` が 300 件に届いたときだけ、同じ SHA の組の差分を手元の git で読み直して返す: `done = g.fetch(remote, head)`（head の SHA を取得する。`base` は手順 (2) の `fetch_branch_or_raise` で手元にある。`Git.fetch` は失敗でも例外を投げず `Completed` を返すので、`done.returncode` が 0 でなければ `GitError(f"{remote} から {head} を取得できない: {done.stderr.strip()}")` を投げる）→ `g.diff_between(base, head, "--merge-base", "--patch", "--no-color", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none", "--submodule=short", "--src-prefix=a/", "--dst-prefix=b/", "-M")` → `mm.diff_rows(text)`。手元の Git の設定で出力の形が変わらないよう、形式と接頭辞を引数で固定する。サブモジュールの参照先だけを変えた差分も、GitHub の `files` と同じく拾う。`--ignore-submodules=none` が無いと、`diff.ignoreSubmodules=all` を設定した環境ではその差分が出力から消え、レッドラインのパスにあるサブモジュールを更新しても検査 4 を素通りする。`--submodule=short` が無いと、`diff.submodule=log` の設定で差分が `Submodule <パス> <a>..<b>:` の要約に変わり、`diff --git` の頭を持たないので `diff_rows` が行を作れない。2 つを付けると、どちらの設定でも `Subproject commit <SHA>` の 1 行ずつを足した・消したパッチになる（git 2.53.0 で確かめた）。`--patch` でパッチの形式を、`--src-prefix=a/`・`--dst-prefix=b/` で `diff --git` と `---`/`+++` の頭を固定する（`diff.noprefix=true` や `diff.mnemonicPrefix=true` の設定でも頭は `a/`・`b/` になる。git 2.53.0 で確かめた）。`--no-ext-diff` は外部の差分の処理だけを止め、`.gitattributes` の textconv は止めないので、`--no-textconv` も付ける（付けないと、textconv を設定したファイルの `patch` が変換後の文になり、実際の変更と違う内容で判定する）。比較の API の `<base>...<head>` は 3 点の比較で、`base` と `head` の merge-base から `head` までの差分（PR の画面の差分と同じ）を返す（[GitHub の 2 点と 3 点の比較](https://docs.github.com/en/pull-requests/reference/branches#three-dot-and-two-dot-git-diff-comparisons)）。`git diff <base> <head>` は両端のツリーを直接比べる 2 点の比較で、`base` が PR の分岐点より進んでいると、宛先だけの変更が逆向きに載り、両ブランチで同じに変えたファイルが落ちるので使わない。手元の経路は `--merge-base` を付け、1 回の `git diff` で `base` と `head` の merge-base から `head` までを比べる（`git diff <base>...<head>` と同じ比較。`git merge-base` を別に起動しない。`--merge-base` は git 2.30 から使える。[git diff の仕様](https://git-scm.com/docs/git-diff)）。共通の祖先が無いなど `git diff` の失敗は、今どおり `GitError`。300 件未満なら git を打たない |
| `diff_rows` | `records/mvv_material_decide.py` | 新設 | `diff_rows(text: str) -> list[dict[str, Any]]` | `git diff` の出力を unidiff（`crossrefactor/diff_decide.py` と同じライブラリ）で読み、`files` と同じ形の行（`filename`・`previous_filename`（改名のとき）・`status`（`added`・`removed`・`modified`・`renamed`）・`patch`（ハンクの本文。`diff --git` の頭は含めない）・`changes`（足した行と消した行の和））を返す純粋な関数（`records` は `crossrefactor` を読まないので、`DiffError` もこのモジュールに `ValueError` の子として置く）。バイナリのファイルは `patch` を持たず `changes` を 1 にする（`incomplete_patches` が載らないファイルとして数える。GitHub の `files` でバイナリが `patch` を持たないのと同じ扱い）。`filename` と `previous_filename` は、どちらも元のパス（GitHub の `files` と同じ UTF-8 の文字列）に復元して置く: 既定の git（`core.quotePath` が真）は ASCII でないバイトや `"`・`\`・制御文字を含むパスを `"` で囲み、C の形のエスケープ（`\"`・`\\`・`\t`・`\n` などと、バイトごとの 8 進の `\ooo`）で書くが、unidiff の `path`・`source_file`・`target_file` はこのエスケープを復元しない。`diff_rows` は unidiff の値をそのまま使わず、`diff --git` の頭・`---`/`+++`・`rename from`/`rename to` の名前が `"` で囲まれていれば、エスケープをバイトへ戻して UTF-8 で読む（復元の関数は同じモジュールの純粋な関数 `unquote_path(name: str) -> str`。UTF-8 で読めないバイトは `DiffError`）。接頭辞の外し方は行の種類で分ける。`diff --git` の頭と `---`/`+++` の名前は、`compare_files` が `--src-prefix=a/`・`--dst-prefix=b/` で付けさせた頭なので、復元の後に `a/`・`b/` を 1 回だけ外す。`rename from`/`rename to` の値には Git が接頭辞を付けないので、外さずにそのまま使う（`rename from a/f` の `a/` は実在のディレクトリの名前である。外すと、ディレクトリ `a/`・`b/` からの改名で `previous_filename` を取り違える）。復元しないとエスケープされたパスがレッドラインのパスと一致せず、検査 4 を素通りする。読めなければ `DiffError` |
| `review_prs` | `records/mvv_gate.py` | 新設 | `review_prs(gate: str, number: int) -> list[int]` | 保留を読む PR。`design` は `[number]`、`release` は空（#4105 まで）。下の「レビューの保留」の接ぎ目 |
| `failure_text` | `records/mvv_gate.py` | 新設 | `failure_text(kind: str, exc: BaseException \| str, where: Mapping[str, Any]) -> str` | 失敗の原因を、PR のコメントと承認資料へ載せてよい文に変える。返す文は `STOP_REASONS[kind]` の定型文と診断の記録の識別子だけで、「<定型文>（診断の記録 <id>）」の形になる。例外の文は返す文に入れない。例外の全文は `mvv.record_failure` で手元の診断の記録へ書く（行は `id`・`at`・`repo`（`mvv.repo_key`）・`gate`・`pr`・`head`・`kind`・`cause`。`where` から `gate`・`pr`・`head` を取り、無いキーは null にする。`cause` は `str(exc)` に `run_log.redact` だけを当てた文で、秘密の値は伏せるが、ホスト名・パス・ブランチ名は残す）。診断の記録に書けない（`OSError`・`internal.InternalError`）ときは例外を出さず、「<定型文>（診断の記録に書けない）」を返す。GitHub も公開範囲の照会も呼ばない。下の「結果に入れる原因の文」 |
| `record_failure` | `records/mvv.py` | 新設 | `record_failure(row: Mapping[str, Any]) -> str` | 診断の記録 `mvv-gate-failures.jsonl`（表 ndf-files の `mvv-gate-failures`。`records.mvv_record_path` で状態の保存先の下に置き、リポジトリにも PR にも出ない）へ、種類 `mvv-gate-failure` の 1 行を `jsonl.append_locked` で足し、行の `id`（`secrets.token_hex(8)`。PR の値も例外の文も含まない）を返す。書けなければ例外をそのまま投げる。行の `cause` は社内のホスト名や手元のパスを含むので、判定を打った OS の利用者だけが読めるファイルにだけ書く。`append_locked` は親のディレクトリを既定の権限で作り、ファイルを通常の追記で開くので、umask が 022 の端末ではほかの OS の利用者も読める。そこで、`append_locked` を呼ぶ前に次の 2 つを行う。(a) 親のディレクトリが無ければ `mkdir(mode=0o700, parents=True)` で作る（既にあるディレクトリの権限は変えない。同じ保存先の他の記録の読み手を変えないため）。(b) 記録のファイルが無ければ `os.open(path, os.O_CREAT \| os.O_WRONLY, 0o600)` で空のファイルを作り、あれば `chmod(0o600)` する。ファイルの権限で守るので、親のディレクトリが広くても中身は読めない。(b) が `OSError`（所有者でないなど）なら、行を書かずにその `OSError` を投げる。`failure_text` はそれを「診断の記録に書けない」の分岐で受け、`cause` をどこにも残さない |

- `gate`
  - 手順:
    - (1) モードの定義がそのゲートを持たなければ、今どおり判定せずに返す
    - (2) `_judged` を呼んで返す
    - (3) `gate` が捕まえるのは、`_judged` の手順 (1)（PR を読み終えるまで）の例外（`GateError`・`GitError`・`GitHubError`・`IdentityError`・`OSError`）だけである。そのときは `verdict: null`・`skip` 偽・`head` 無し・`why` は「MVV 判定を打てない: <`failure_text("no_pr", 例外, …)`>」にし、コメントは書かない（書く先の PR が決まっていない）。PR を読んだ後の失敗は `_judged` が中で受け、`gate` へ例外を出さない（下の「PR を読んだ後の失敗」）
  - 進め方の条件（`inputs["pace"] not in MVV_PACES` で返す行）は消す。進め方は `_judged` の最後の「省くか」でだけ読む
- `_judged`
  - 手順:
    - (1) `pr_number` で PR を決め、`hub.pull` で `pull`（head・`base_ref`・本文）を 1 回読む。ここで読んだ `pull.head_sha` を、以降のすべての出力の `head` にする。
    - (2) `git.Git(root).fetch_branch_or_raise(<リモート>, <base_ref>)`（refspec `+refs/heads/<base_ref>:refs/remotes/<リモート>/<base_ref>`。#4090・#4099 が `current` の CLI に指定した関数と同じ）を 1 回打ち、直後に読むリモート追跡参照そのものを更新する。`git fetch <リモート> <base_ref>` は使わない（`--single-branch` の取得設定では `FETCH_HEAD` だけを更新し、追跡参照が古いまま残る）。
      - 続けて `snap = mvv.current(root, md.signals_ref(base_ref, リモート), allow_mismatch=True)` でベースの MVV を 1 度だけ読む
      - 本文のハッシュが承認の記録と合わないときは、`current` が `MvvError` を投げずに状態 `mismatch` の `snap` を返し、(3) の検査 1 で止まる（コメントを書かず `noted: false`。設計書の分岐表の「検査 1 が外れる」と同じ扱い）
      - 参照を SHA へ解決するのは `current` のエントリポイントの `git rev-parse` の 1 回だけで、`_judged` は `git rev-parse` を打たず、`current` が返した `snap.commit` をベースのコミットとして使う
      - `snapshot_facts(root, snap, inputs)` で事実を作る（レッドラインのパスも同じ `snap.commit` から読む）
      - この後の検査 1・判定の材料・保留のバージョンは、どれもこの `snap` と `snap.commit` だけを使う（作業ツリーの MVV と `.ndf/pace.json` を読まない。PR が `.ndf/mvv.json` を消しても、ベースが承認済みなら検査 1 を通って判定し、検査 5 が外れる）
    - (3) `judgeable` が理由を返せば、`gate_outcome(gate, None, False, [理由], head)` で返し、コメントは書かない（承認済みの MVV が無いリポジトリの出力と PR を今と同じにする）。出力に `noted: false` を置く。
    - (4) 差分を、固定した SHA の組から `mvv.compare_files(hub, git.Git(root), <リモート>, snap.commit, pull.head_sha)` で読む。`GET /repos/<repo>/compare/<snap.commit>...<head_sha>` を 1 回打ち、ページを指定しない。`files` が 300 件に届いたときだけ、同じ SHA の組を手元の git で読み直す。続けて `pr_facts(hub, number, facts, rows, pull)`（読み済みの `pull` を渡す）で事実を足す。
      - `pr_files`（`pulls/<番号>/files`）は GitHub がその時点の宛先の先端と比べた差分を返し、材料を読む間に同じ宛先へ別の PR がマージされると、MVV を読んだ `snap.commit` と違うベースの差分になるので使わない
      - 比較の基点を `snap.commit` に固定するので、差分・MVV・レッドラインのパスはどれも同じベースのコミットに属する。宛先がその後に進んでも、判定は `snap.commit` を宛先とした PR の判定になる。宛先の先端との照合は #4106 が消費の時点で行う
      - GitHub の比較は `files` を 300 件で打ち切る。そこで 300 件に届いたら、`compare_files` が同じ `snap.commit` と (1) の head の組を手元の git から読み直す（比較の API と同じく、両者の merge-base から head までの差分）。今の `pulls/<番号>/files` のページ読みで判定できた 300 件以上の PR を、判定できないものにしない
      - 読み直した行も (6) の `incomplete_patches` を通る
      - 差分を読み終えたら `hub.pull` をもう 1 回読み、head か宛先（`base_ref`）が (1) の値と違えば材料を捨てて止める（下の表。差分は (1) の head と `snap.commit` に固定して読むが、読む間に push されると判定が今の head に属さず、宛先を変えられると `snap` が別のブランチの MVV になるため。判定の材料は (1) の head と宛先と、`snap.commit` から (1) の head までの差分だけにする）
      - この読み直しが `GitHubError` を投げたときも材料を捨てて止める（下の表。未照合の差分を使わず、例外を `gate` へ出さない）
    - (5) 記録 `mvv-validation.jsonl` を `validation_rows` で 1 回だけ読み、保留を求める（下の「レビューの保留」）。読めたときの行は (7) の再利用にも渡す。
    - (6) `mm.incomplete_patches(rows)` があれば、`refusals(facts, gate)` の理由とあわせて `verdict: null` で記録して返す。
    - (7) LLM の判定（今と同じ材料で `mvv_validate_sprint(root, 入力, judge, reuse=読めたか, rows=(5) で読めた行, broken=(5) の同じ読み取りで除いた行)`。(5) で記録を読めたときだけ、その行から同じ材料の「従う」を再利用し、記録を読み直さない。`broken` を省くと既定の空になり、直近の判定の行が壊れていても古い「従う」を今回の判定として使ってしまうので、必ず渡す。読めなかったときは再利用を省いて LLM で判定する）。
      - 判定の行の追記が `OSError` で失敗しても判定は捨てない。`verdict` はそのまま、追記の失敗の印 `unrecorded`（原因）を持つ。`why` に「判定を記録（mvv-validation.jsonl）に書けない: <`failure_text("records_unwritable", 原因, …)`>」を足して (8) へ進む（`skip` は (8) で決める）
      - (5) で求めた保留は `holds` にそのまま残る。記録を読めず追記もできないときは、判定・保留の「読めない」・記録に書けない理由の 3 つがそろって返る
      - `GateError`（LLM が回答しない）はここで受けて (9) へ進む。出力は `verdict: null`・`skip` 偽で、`holds`・`head` は読んだ値のままにする。`why` は「MVV 判定を打てない: <`failure_text("llm_no_answer", 例外, …)`>」と `refusals(facts, gate)` の理由にする
    - (8) `ok, reasons = may_skip(facts, verdict, gate)` とし、`skip = ok and unrecorded is None` にする。`why` は、(7) までに足した理由（記録に書けない理由を含む）の後ろに `reasons` を続けたものにする（`may_skip` の返り値で (7) の `skip` と `why` を上書きしない。記録に行の無い判定で設計承認ゲートを省かないため）。
      - 進め方が `MVV_PACES` に無い（`normal` など）なら `skip` を偽にし、`why` の先頭に「進め方 <値> は承認ゲートを MVV 判定で省かない」を足す
      - 区分が `release` なら `skip` を偽にし、`why` に「リリース承認ゲートは、保留を読む PR の範囲が決まるまで（#4105）MVV 判定で省かない」を足す（決定 3）
    - (9) `_recorded` で PR のコメントへ書いて返す
  - 事後条件:
    - `mvv-validation.jsonl` を読むのは (5) の 1 回だけで、`mvv_validate_sprint` は読み直さない
    - `verdict` が null なのは、(1) の失敗（`gate` が受ける）・(3)・下の「PR を読んだ後の失敗」の表で `verdict: null` とした行のときだけ。表のその行は、(2) のベースの取得・(4) の差分の読み取り・head と宛先の移動・読み直しの失敗・(6)・(7) の LLM の失敗である
    - `normal` の `skip` は常に偽
    - 判定の行を記録に追記できなかった判定の `skip` は、進め方と `may_skip` によらず常に偽
    - 判定の差分・MVV・レッドラインのパスは同じ `snap.commit` に属する
    - PR を読んだ後の出力は、どの終わり方でも (1) で読んだ `head` を持つ
  - 副作用:
    - PR のコメントは、下の表の「コメント」の列が「書く」の終わり方でだけ 1 件書く（(3) と、判定の材料が今の head に属さない終わり方では書かない）
    - `_recorded` を呼ぶと、`Publisher.guarded` が投稿の前に書き先の公開範囲を照会する（GraphQL 1 回）ので、投稿の成否によらず照会がかかる
    - この照会が例外で失敗したら、他のリポジトリへの参照の照会も投稿の REST も打たない（増えるのは照会の 1 回だけ）
    - 照会が成功して書き先が `PUBLIC` か `UNKNOWN` なら、本文にある他のリポジトリへの参照を最大 100 件ずつ照会し（参照 100 件ごとに GraphQL が 1 回。参照が無ければ 0 回）、その後に投稿の REST を 1 回打つ
    - `PRIVATE`・`INTERNAL` なら参照を照会せずに投稿の REST を 1 回打つ
    - 下の表の「書けない」の行（投稿に失敗した判定）も `_recorded` を呼ぶので数える
    - 今は `_recorded` を呼ばずに終わり、変更後に初めて呼ぶ終わり方（`normal` の判定、PR を読む前の検査で返していた判定、PR を読んだ後の例外で返していた判定）では、これがそのまま増える（終わり方ごとの表は設計書の「費用」）
    - ベースの取得（`fetch_branch_or_raise` の `git fetch` 1 回）と PR の読み直し（REST 1 回）も増える
    - 差分は `pulls/<番号>/files` の代わりに比較の REST 1 回で読むので、差分の REST の呼び出しは増えない（`pulls/<番号>/files` のページ読みより減る）
    - `files` が 300 件に届いた PR だけ、head の `git fetch` と、merge-base からの `git diff`（`--merge-base`）が 1 回ずつ増える（Git の起動が 2 回）
    - LLM の判定は `llm.ask` を 1 回呼び、`llm.ask` は回答が形に合わなければ 1 回だけ打ち直すので、`claude` の起動は判定 1 回につき最大 2 回（`llm.ATTEMPTS`）になる
    - ほかにコピーの書き込みが増える（`current` の副作用）
- `_recorded`:
  - `note(outcome["gate"], outcome["verdict"], outcome["why"], outcome["holds"] or ())` を書く。`holds` は下の `gate_outcome` のとおり常にあり、未確認の null は空として渡す
  - `why`・`holds` の失敗の原因は、下の「結果に入れる原因の文」のとおり `failure_text` が返す定型文と診断の記録の識別子だけで、例外の文を含まない
  - `_recorded` は例外の文を受け取らず、PR へ書く本文にも載せない
  - それでも本文には判定の理由など他の文も入るので、書き込みは今どおり `post_note`（`publish.Publisher(hub).comment`）だけを通し、`_mvv_cite` と同じ包み（`Publisher.guarded`。書き先の公開範囲の照会と、秘密・ホームのパス・メールアドレス・アカウント名・非公開の参照の除去）を通した後の本文だけを PR に書く
  - `hub.rest` を直接呼んで書かない
  - 公開範囲の照会に失敗したときも `GitHubError` として下の投稿の失敗と同じに扱う
  - 書き込みの `GitHubError` は外へ出さず、`skip` を偽にし、`why` の末尾に「判定の記録を PR に書けない: <`failure_text("note_unwritten", 例外, …)`>」を足し（例外の全文は診断の記録へ。出力の `why` だけに入り、PR には載らない）、出力に `noted: false` を置いて返す（`verdict` と `head` は保つ）
  - 書けたときは `noted: true` と、今回書いたコメントの URL `note_url`（`Publisher.comment` が返す `Published.url`。投稿の応答の `html_url` なので、GitHub の呼び出しは増えない）を置く
  - `post_note` は `Published` を返すように変える
- 結果に入れる原因の文:
  - 出力（結果 JSON の `outputs.mvv_gate`）の `why` と `holds` に入れる失敗の原因は、失敗の種類ごとに決めた定型文（`STOP_REASONS`）と診断の記録の識別子だけにし、例外の文を入れない
  - 例外の文には、PR にも記録にも無い手元の端末の情報（ホームの絶対パス・アカウント名・秘密の値）、社内の Git ホスト名（`git fetch` の標準エラーなど）、判定する PR と関係の無い他のリポジトリへの参照が入りうる
  - 伏せる処理（`publish.scrub`）は GitHub の URL と課題番号付きの参照しか見分けないので、社内のホスト名のような形の決まらない値を残す
  - 出力は PR のコメント（`_recorded`）と承認資料（下の「手順書」の「載せ方」）へ写されるので、伏せて残すのでなく、初めから載せない
  - 例外の全文は `failure_text` が手元の診断の記録（状態の保存先の `mvv-gate-failures.jsonl`。`record_failure` がファイルを 0600 にしてから書くので、読めるのは判定を打った OS の利用者だけ。0600 にできなければ書かない）へ書き、PR のコメントと承認資料は識別子でそれを指す
  - 判定の理由と保留の自由記述（見送りの理由・レッドラインの指摘本文）は例外の文でないので、この規則の対象でない（下の「手順書」の「承認資料の開示の範囲」）
  - 対象は次のすべてである: `gate` の手順 (3) の「MVV 判定を打てない: 」・下の「PR を読んだ後の失敗」の表の各行の原因・手順 (5) の保留「MVV の記録（mvv-validation.jsonl）を読めない: 」・手順 (7) の追記の失敗の原因（`unrecorded`）と LLM の失敗の理由・`_recorded` の「判定の記録を PR に書けない: 」
  - 例外を受けたその場で `failure_text` に変え、例外の文を `gate_outcome` へ渡さない
  - `warnings` にも例外の文を載せない
- `note`（`checks.py`）: シグネチャを `note(gate: str, verdict: Any, why: Sequence[str], holds: Sequence[str] = ()) -> str` に変える。`holds` が空でなければ「### レビューの保留」の箇条書きを足す。呼び手は `mvv_gate._recorded` だけ
- `gate_outcome`（`mvv_material_decide.py`）: 引数 `holds: Optional[Sequence[str]] = None` と `pr: Optional[int] = None` を足し、出力に `holds` のキーを常に置く。
  - `pr` は判定した PR の番号で、`head` と同じく `_judged` の手順 (1) で決めた値を渡す。PR を読む前に止めたら置かない（`head` と同じ）。承認資料が、判定を質問の PR と head に照らすために読む（下の「載せ方」）
  - 保留を読んだ（`_judged` の手順 (5) まで進み、`review_prs` が PR を返した）ときは読んだ結果の配列（無ければ空の配列 `[]`。読めなかったときは読めない理由の 1 件）、保留を読んでいないときは `null`
  - `null` になるのは、(5) より前で終わった終わり方（(2) のベースの取得・(3) の検査 1・(4) の差分と読み直しの失敗）と、保留を読む PR の範囲を決めない `release`（#4105 まで）である
  - `null` と `[]` を同じ表示にしない（下の「手順書」の載せ方）
  - `context.schema.json` の `$defs.mvv_gate` に任意のキー `pr`（整数。判定した PR の番号）と `holds`（文字列の配列か null）と `noted`（真偽値）と `note_url`（文字列。`noted` が真のときだけ）を足す
  - `gate_outcome` は `noted: false` を既定で置き、`_recorded` が書けたときだけ `noted: true` と `note_url` に置き換える
  - 今回のコメントを書いたかを、キーの有無でなく値で表すためである（キーを省く形では、書けた判定と、差分を読む間に PR が動いて書かなかった判定を見分けられない）
  - 判定した PR の番号・宛先・ベースのコミットを出力に足して省いた回答と照合するのは #4106
  - `$defs.mvv_gate` には下の `stopped_by`（`kind` は下の表のうち `stopped_by` に置く 9 つの種類の列挙、`text` は文字列）も足す

- `STOP_REASONS`・`stopped_by`（`mvv_material_decide.py`）: `STOP_REASONS` は失敗の種類 → 定型文の表で、`stopped_by` と `failure_text` が同じ表を読む（下の表。`verdict` を保つ失敗の 3 つの種類 `records_unreadable`・`records_unwritable`・`note_unwritten` と、`cite` の 2 つの種類 `cite_precondition`・`cite_lookup_failed` は `failure_text` だけが使う。この 5 つは `stopped_by` には置かない）。
  - `verdict` が null の出力には、判定できなかった理由の種類 `stopped_by: {"kind": <種類>, "text": <定型文>}` を置く（`verdict` があればキーを置かない）
  - 定型文は、承認する人が次に何を確かめるかが分かる文で、PR から取った値（ファイル名・ブランチ名・PR の番号・リポジトリ名）も例外の文も含めない
  - 種類と定型文の表は `mvv_material_decide.py` の定数 `STOP_REASONS` の 1 か所に置き、`gate_outcome` が引数 `stop: Optional[str]`（種類）から `text` を引く
  - 手順書は文を写さず、出力の `text` を載せる
  - `why` は今どおり詳しい理由（原因の文を含む）を持つ

| 種類 | 終わり方 | 定型文 |
| --- | --- | --- |
| `no_gate` | モードがその承認ゲートを持たない | このモードには MVV 判定の対象の承認ゲートが無い |
| `no_pr` | 開いた PR が無い・PR を読めない | 判定する PR を決められないか、GitHub から読めない。PR が開いているかと GitHub への接続を確かめる |
| `mvv_unapproved` | 検査 1 が外れる | ベースブランチに承認済みのプロジェクト MVV が無いか、MVV の本文が承認の記録と合わない。MVV の承認の状態を確かめる |
| `base_unreadable` | ベースの MVV・レッドラインの設定を読めない | ベースブランチの MVV か、人の承認が要るパスの設定を読めない。取得をやり直して判定を打ち直す |
| `diff_unreadable` | 差分を読めない | 変更したファイルを読めない。判定を打ち直す |
| `pr_moved` | 差分を読む間に head か宛先が動いた | 判定の材料を読む間に PR の内容か宛先が変わった。判定を打ち直す |
| `pr_reread_failed` | 差分の後に PR を読み直せない | 判定の材料を読んだ後に PR を読み直せない。判定を打ち直す |
| `incomplete_patches` | 差分の全体が材料に載らない | 変更が大きく、差分の全体を判定の材料に載せられない。PR を分ける |
| `llm_no_answer` | LLM が回答しない | 判定する AI が回答しない。判定を打ち直す |
| `records_unreadable` | 記録 `mvv-validation.jsonl` を読めない（`verdict` を保つ） | 手元の MVV の記録を読めない。判定を打った環境の記録のファイルを確かめる |
| `records_unwritable` | 判定の行を記録に追記できない（`verdict` を保つ） | 判定を手元の MVV の記録に書けない。判定を打った環境の記録の保存先を確かめる |
| `note_unwritten` | 判定の記録を PR のコメントに書けない（`verdict` を保つ） | 判定の記録を PR のコメントに書けない。GitHub への接続と書き込みの権限を確かめて判定を打ち直す |
| `cite_precondition` | `cite` がファイルかベースの MVV を読めない（`_mvv_cite` の `precondition`） | 承認資料のファイルか、ベースブランチの承認済みの MVV を読めない。ファイルのパスと取得をやり直して確かめ直す |
| `cite_lookup_failed` | `cite` の伏せる処理の公開範囲の照会に失敗した（注意） | 参照先の公開範囲を GitHub に照会できない。GitHub への接続を確かめて確かめ直す |

### 異常系の処理

PR を読んだ後の失敗（`_judged` が中で受け、`gate` へ例外を出さない）。どの行も `skip` は偽、`head` は (1) で読んだ値。`verdict` が null の行は `why` の先頭が「MVV 判定を打てない: 」。表の `<原因: 種類>` は `failure_text(<種類>, 例外, …)` が返す定型文と診断の記録の識別子で、例外の文は診断の記録にだけ残る。承認ゲートは省かずに人へ問う。

| 手順 | 失敗 | 検出する例外・条件 | `verdict` | `why` に足す理由 | コメント | 結果 | 再試行 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| (2) | ベースの MVV・レッドラインの設定を読めない | `fetch_branch_or_raise` の `GitError`・`current` の `MvvError`（参照を解決できないときを含む。本文のハッシュの不一致は `allow_mismatch=True` で読むのでここに来ず、(3) の検査 1 になる）・`boundary_paths` の `JsonFileError`・`OSError` | null | 「ベースの MVV を読めない: <原因: `base_unreadable`>」。作業ツリーの MVV で代えない | 書く | `ok`・人へ問う | 直して 判定のステップから |
| (4) | 差分を読めない | `compare_files` の `GitHubError`、300 件に届いたときの手元の読み直しの `GitError`（head の取得の終了コードが 0 でない・`git diff`）・`DiffError` | null | 「変えたファイルを読めない: <原因: `diff_unreadable`>」 | 書く | 同上 | 判定のステップから |
| (4) | 差分を読む間に head か宛先が動いた | 読み直した head か `base_ref` が (1) と違う | null | <原因: `pr_moved`>（動く前と後の宛先と head は診断の記録の `cause` に書く） | 書かない（材料が今の PR に属さない） | `ok`・人へ問う | 判定のステップから |
| (4) | 差分の後に PR を読み直せない | 読み直しの `hub.pull` の `GitHubError` | null | 「判定の材料を読んだ後に PR を読み直せない: <原因: `pr_reread_failed`>」。読んだ差分は捨てる | 書かない（材料を照合できていない） | `ok`・人へ問う | 判定のステップから |
| (6) | 差分の全体が材料に載らない | `incomplete_patches` | null | 載らないファイル | 書く | 同上 | PR を分ける |
| (5) | 記録（`mvv-validation.jsonl`）を読めない | `validation_rows` の `OSError`・`UnicodeDecodeError`・`internal.InternalError`（新しい形式のバージョンの行） | 判定のまま（判定は止めない） | 保留の 1 件「MVV の記録（mvv-validation.jsonl）を読めない: <原因: `records_unreadable`>」は `holds` に置く。`why` には `check_review_holds` の固定の文「レビューの保留が 1 件ある（内容は保留の欄）」だけが入り、保留の文と「MVV 判定を打てない: 」は付けない | 書く（保留を載せる） | `ok`・人へ問う（省かない） | 原因を直して判定のステップから。読めない間は同じ材料の「従う」も再利用せず LLM を呼ぶ |
| (7) | LLM が回答しない | `GateError` | null | <原因: `llm_no_answer`> と `refusals` | 書く | 同上 | 打ち直せる |
| (7) | 判定の行を記録に追記できない | `mvv_validate_sprint` が返す追記の失敗の原因（`OSError`） | 判定のまま | 「判定を記録（mvv-validation.jsonl）に書けない: <原因: `records_unwritable`>」（「MVV 判定を打てない: 」は付けない）。(8) の `may_skip` の理由はこの後ろに続け、この理由と `skip` 偽を上書きしない | 書く（判定・保留・記録の失敗を載せる） | `ok`・人へ問う（省かない） | 原因を直して判定のステップから（記録に行が無いので LLM を呼び直す） |
| (9) | コメントを書けない | `_recorded` の `GitHubError` | 判定のまま | 「判定の記録を PR に書けない: <原因: `note_unwritten`>」 | 書けない（`noted: false`） | `ok`・人へ問う（省かない） | 判定のステップから（同じ材料の「従う」は記録を再利用し LLM を呼ばない） |

PR を読む前の失敗（`gate` が受ける）:

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pr_number` | 開いた PR が無い（`design`） | PR を出していない | `GateError` | `verdict: null`・`head` 無し・コメント無し | `ok` で人へ問う | PR を出して打ち直す |
| `hub.pull` | PR を読めない | GitHub が回答しない | `GitHubError` | 同上 | 同上 | 打ち直せる |

### 処理の手順

| 区分 | 進め方 | 検査 1 | 検査 2〜7 の理由 | 判定 | `skip` |
| --- | --- | --- | --- | --- | --- |
| `design`・`release` | `normal` | 通る | 問わない | 打つ | 偽 |
| `design` | `fast`・`auto` | 通る | 無い | 打つ | 「従う」・レッドライン空で真 |
| `release` | `fast`・`auto` | 通る | 問わない | 打つ | 偽（#4105 までの暫定。決定 3） |
| `design` | `fast`・`auto` | 通る | ある | 打つ | 偽 |
| どれも | どれも | 外れる | — | 打たない（`verdict: null`） | 偽 |

「MVV が承認されていない」の扱いも判定と同じ `snap` から `_judged` が決める。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate`（`normal`） | 判定のスタブが 1 回呼ばれ、`verdict` を持ち `skip` は偽、`why` の先頭に normal の理由、コメントが 1 件。レビューの記録を読み、保留が無かった判定（`holds: []`）もコメントを書く。ベースの取得に失敗した終わり方と `release` の判定は `holds: null`（`[]` にならない） | 判定が「従う」・レッドライン空でも `skip` は偽 | LLM の失敗 → `verdict: null` で `ok`。出力は `head` と `holds` を持ち、コメントが 1 件で保留と理由が載る |
| `gate`（`fast`） | 今と同じ材料で「従う」→ `skip` 真 | 検査 4・5・3 が外れても判定は打たれ `skip` は偽 | 検査 1 が外れる → 判定のスタブを呼ばない。記録は正常に読める（保留は空）が判定の行の追記だけが `OSError`、判定は「従う」・検査 2〜7 が通る → `verdict` は判定のまま・`skip` 偽・`why` に記録に書けない理由が残る（`may_skip` の結果で上書きされない） |
| `_judged`（ベースの取得と差分） | ベースの取得が `fetch_branch_or_raise` を 1 回だけ呼ぶ（Git のスタブで refspec が `+refs/heads/<base_ref>:refs/remotes/<リモート>/<base_ref>` であることを見る）。差分の比較の REST が `snap.commit` と (1) の `head_sha` の組で 1 回だけ呼ばれ、`pulls/<番号>/files` を呼ばない | 比較のスタブが返す前に、宛先の先端を進めた（`hub.pull` の 2 回目は同じ head・同じ `base_ref`）→ 比較の基点は `snap.commit` のまま、判定の材料の差分も `snap.commit` からのもの | 比較の `files` が 299 件 → git を打たない。300 件 → 同じ `snap.commit` と head の組で `fetch`・`diff_between` が 1 回ずつ呼ばれ、`merge_base` は呼ばれない。`diff_between` は `snap.commit` と head を受け、引数に `--merge-base` を持ち、手元の差分の行（301 件目のファイルを含む）で判定する。宛先だけが分岐後に進んだ状態を Git のスタブで作る（`diff_between` は `--merge-base` があるときだけ PR の変更の差分を返し、無いときは宛先だけが変えたファイルの逆向きの差分を混ぜ、両ブランチで同じに変えたファイルを落とした差分を返す。子のプロセスは起動しない）→ 比較の API の `files` を模したスタブの変更ファイルと、手元の経路の `filename` の集まりが一致する（宛先だけの変更が載らず、両ブランチで同じに変えたファイルが落ちない）。`diff_between` のスタブが受けた引数に `--merge-base`・`--patch`・`--no-ext-diff`・`--no-textconv`・`--ignore-submodules=none`・`--submodule=short`・`--src-prefix=a/`・`--dst-prefix=b/`・`-M` がある（手元の Git の設定に左右されない引数で起動する。`diff.noprefix=true` などの設定で出る形の違いは、この引数で起きないので、設定ごとの出力をテストに持たない）。Git のスタブの `fetch` が非ゼロの `Completed`（例外を投げない）を返す → `GitError` に変わり、`diff_between` を呼ばず `verdict: null`・「変えたファイルを読めない」・コメント 1 件 |
| `diff_rows` | 追加・削除・変更・改名のファイルが `files` と同じ形の行になる | 中身を変えない改名 → `changes: 0` で `incomplete_patches` に数えない。バイナリ → `patch` 無しで数える。実在のディレクトリ `a/`・`b/` の下からの改名（固定の入力 `diff --git a/a/f b/b/g`・`rename from a/f`・`rename to b/g`。git を起動しない）→ `previous_filename` は `a/f`、`filename` は `b/g`（`rename from`/`rename to` の頭を外さない）。サブモジュールの参照先だけを変えた差分（固定の入力 `diff --git a/s b/s`・`index <a>..<b> 160000`・`-Subproject commit <a>`・`+Subproject commit <b>`。git を起動しない）→ `filename` は `s`、`status` は `modified`、`changes` は 2 | 読めない差分 → `DiffError` |
| `unquote_path`・`diff_rows`（パスの復元） | 既定の `core.quotePath` の git が出す形の差分の文字列（固定の入力。git を起動しない）で、日本語名のファイルの変更と、日本語名からの改名 → `filename` と `previous_filename` が元のパスで、同じ変更の API の行の `filename`・`previous_filename` と一致する | `"`・`\\`・タブを含むパス → 元の文字に戻る。引用の無いパスはそのまま | UTF-8 で読めない 8 進のエスケープ → `DiffError` |
| `snapshot_facts` | 事実がすべて `snap` と `snap.commit` の値。`git rev-parse` を打たない（Git の起動を数えるスタブで `current` の 1 回だけ） | 作業ツリーの本文が `snap` と違っても事実は `snap` のまま。作業ツリーの `.ndf/pace.json` から `boundary_paths` のパスを消した PR が、そのパスも変える → ベースの規則で検査 4 が外れる | ベースの `.ndf/pace.json` が壊れている → `JsonFileError` |
| `pr_number` | `worktree` の指すブランチの PR | 文脈に `design_pr` があっても読まない。同じブランチ名の開いた PR がフォークにもある → `hub.repo` の head の 1 件を選ぶ | 開いた PR が無い → `GateError`。`worktree` が別のリポジトリの worktree（Git のスタブで `--git-common-dir` が `root` と違う）→ `GateError` で、`pulls_by_head` を呼ばない。同じブランチから宛先の違う開いた PR が 2 件 → `GateError`、`gate` の出力は `verdict: null`・`stopped_by.kind: no_pr` で、`post_note` のスタブが呼ばれない。どちらも `NDF_STAGE_INPUT` と `--machine` の両方の起動で同じ結果 |
| `_judged`（PR を読んだ後の失敗） | 上の表の各行が、表の `verdict`・`head`（(1) の値）・`why`・コメントの有無になり、例外を外へ出さない。どの行も `Gated.target` は (1) で読んだ PR | `compare_files` の後の読み直しで head が動いた・head を変えずに宛先が変わった・読み直しが `GitHubError` → 差分を判定に使わず LLM を呼ばず、コメントを書かない | ベースの `fetch_branch_or_raise` の `GitError` → `verdict: null`・`head` あり・コメント 1 件。コメントの書き込みの失敗 → `noted: false`・`skip` 偽・`verdict` は保つ。ベースの本文のハッシュが承認の記録と合わない → (2) で止まらず検査 1 の分岐になり、`verdict: null`・`noted: false`・コメント無し（`post_note` のスタブが呼ばれない）。失敗の原因の文の漏れ: ベースの取得の `GitError` の文に、偽の社内の Git ホスト名（`git.<偽の社名>.internal` の形）・偽のホームの絶対パス（`/home/<偽の名前>/...`）・偽のアカウント名・偽の秘密（環境変数に置いた値）・別のリポジトリへの参照・非公開の偽の所有者とリポジトリを指す認証情報付きのリモートの URL（`https://x-access-token:<偽のトークン>@github.com/<偽の所有者>/<偽のリポジトリ>.git`）と `***@` に伏せ字済みの同じ URL を含める。同じ値を含む `OSError` で記録を読めない（と追記できない）判定と、同じ値を含む `GitHubError` でコメントの投稿に失敗する判定も作る → 出力の `why` と `holds`、その出力で `mvv_lines` が組んだ承認資料の行、`post_note` が受けた本文（`Publisher` の `hub.rest` のスタブが受けた `body`）のどれにも、偽のホスト名・パス・アカウント名・秘密・トークン・所有者名・リポジトリ名が無く、失敗の原因は `STOP_REASONS` のその種類の定型文と診断の記録の識別子だけである。診断の記録（`NDF_MVV_STATE_DIR` を一時ディレクトリにした `mvv-gate-failures.jsonl`）には、識別子が同じ行があり、その `cause` に偽のホスト名とパスが残り、偽の秘密は伏せ字になる |
| `failure_text`・`record_failure` | 種類 `base_unreadable` と、偽の社内の Git ホスト名を含む `GitError` → 返す文は「<`STOP_REASONS["base_unreadable"]`>（診断の記録 <id>）」で、例外の文の断片を含まない。診断の記録に `id`・`kind`・`gate`・`pr`・`head`・`cause` の 1 行が足され、`cause` は例外の全文 | 例外の文が空 → 定型文と識別子だけを返し、記録の `cause` は空の文字列。同じ例外で 2 回呼ぶ → 識別子が違う 2 行。GitHub と公開範囲の照会（`visibilities`）を呼ばない | 偽の秘密（環境変数に置いた値）を含む例外 → 記録の `cause` に秘密が残らない。診断の記録の保存先に書けない（`NDF_MVV_STATE_DIR` を書けないパスにする）→ 例外を出さず「<定型文>（診断の記録に書けない）」を返す。umask を 022 にしたテストで、保存先のディレクトリが無い → ディレクトリは 0700、記録のファイルは 0600 で作られる。記録のファイルが 0644 で既にある → 追記の後は 0600。記録のファイルの `chmod` が `PermissionError`（スタブ）→ ファイルの中身は変わらず（`cause` の行が足されない）、「<定型文>（診断の記録に書けない）」を返す |
| `gate_outcome`（`stopped_by`） | `stop` を渡す → `stopped_by` の `kind` と、`STOP_REASONS` のその種類の `text` | `verdict` あり → `stopped_by` のキーが無い | `_judged` の各終わり方（ベースの取得の失敗・差分を読む間に head が動いた・LLM が回答しない など）の出力の `stopped_by.kind` が表の種類。偽のファイル名・偽のブランチ名を含む失敗でも、`stopped_by.text` にどちらも無い。`STOP_REASONS` の `records_unreadable`・`records_unwritable`・`note_unwritten` は `stopped_by` の `kind` に現れない（`verdict` を保つ失敗なので `stopped_by` を置かない） |
| `_recorded`・`gate_outcome`（`noted`） | 投稿できた → `noted: true` と、`Publisher` の `hub.rest` のスタブが返した `html_url` と同じ `note_url`。REST の呼び出しは投稿の 1 回だけ | 差分を読む間に head が動いた・検査 1 が外れた → `noted: false` で `note_url` が無い（キーを省かない） | 投稿の `GitHubError` → `noted: false` で `note_url` が無く、`why` の末尾は「判定の記録を PR に書けない: 」と `note_unwritten` の定型文と診断の記録の識別子で、`GitHubError` の文を含まない |
| `note` | 保留と判定を載せる | 保留が空なら節を出さない | — |

## リリース承認ゲートの省略の止め

満たす決定: 決定 3

リリース承認ゲートは #4105 まで、判定のステップの出力（新しい判定は `skip: false`）に頼らず、ゲートの側でも省かない。省いてよい区分の表 `MVV_SKIP_GATES` を設計承認ゲートだけにし、`mvv_skip` はほかの区分を省かない。省いた回答を消費の時点で判定の対象（PR・宛先・宛先の先端）と照合する仕組みは #4106 が持ち、この Issue では区分の条件のほかは今のまま head だけを照合する（決定 9）。

更新前の NDF が書いた状態から再開しても、保留を確かめていない MVV 判定では承認ゲートを通さない（決定 3）。更新前の NDF が書いた状態は 3 つある。更新の前に済ませた判定の `skip: true`、まだ読まれていない `service:mvv` の回答、更新の前に消費してステップを済みにした `service:mvv` の回答である。どれも、レビューの保留を確かめた証拠（判定の出力の `holds` が空の配列 `[]`）を持たない。

- リリース承認ゲートは、区分が `MVV_SKIP_GATES` に無いので、`service:mvv` の回答をどれも数えない
- 設計承認ゲートは区分の中だが、`holds` が空の配列でない判定（キーが無い・`null`・保留がある）の `skip: true` と `service:mvv` の回答を数えない。更新前の NDF が書いた判定の出力は `holds` のキーを持たないので、ここに当たる

今のコードは、`mvv_skip` が None でも、記録に残った回答を消費する。`progress/run.py` の `_running_move` は回答（`prog.answer`）があれば `CONSUME` を返し、`_consume` は回答した者を見ずに承認として読む。`progress/gate/records.py` の `approved_head` は、区分の最後の承認の head を返す。また `run.py` は済んだステップ（`step_recorded`）を流し直さないので、更新の前に消費した回答で済んだ承認ゲートのステップには、読み口を変えても戻らない。そこで、数えない回答の判定を `progress/model.py` の 1 つの関数 `mvv_answer_void` に置く。4 つの読み口（`_running_move` の待ちの判定と済んだステップの判定・`_consume`・`approved_head`）がそれを使う。記録の事象は書き換えない。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `MVV_SKIP_GATES` | `progress/model.py` | 新設 | `tuple[str, ...] = ("design",)` | MVV 判定で省いてよい承認ゲートの区分。#4105 が `release` を足す |
| `mvv_skip` | `progress/model.py` | 変更 | シグネチャは同じ | 今の条件に 2 つを足す。`gate in MVV_SKIP_GATES` と、判定の出力の `holds` が空の配列であること（`hold_checked(held)`）。外れれば None（承認ゲートで人を待つ）。`holds` のキーが無い（更新前の NDF が書いた判定）・`null`（保留を読む前に止まった・実装計画の単位 3 が入る前の判定）・保留がある判定の `skip: true` は採らない |
| `hold_checked` | `progress/model.py` | 新設 | `hold_checked(held: Any) -> bool` | 判定の出力 `held` がレビューの保留を確かめて 0 件だったか。`held` が dict で `holds` が空のリストのときだけ真。`mvv_skip` と `mvv_answer_void` が同じ関数で決める |
| `mvv_answer_void` | `progress/model.py` | 新設 | `mvv_answer_void(payload: Mapping[str, Any], gate: Optional[str]) -> bool` | 回答の事象の payload が、回答として数えない MVV 判定の回答かを返す純粋な関数。`payload.get("by") == MVV_SERVICE` で、次のどちらかなら真。(a) `gate` が `MVV_SKIP_GATES` に無い（None を含む）。(b) `hold_checked(payload.get(MVV_KEY))` が偽（回答に添えた判定の写しが、保留を確かめた証拠を持たない。更新前の NDF が書いた回答はここに当たる）。`gate` は呼び手がステップの区分（`step.gate`）か問い合わせの区分で渡し、payload の `gate` は読まない（更新前の NDF が書いた回答の `gate` は判定の出力から写した値で、ステップの区分と同じ出所でないため）。`by` が無い payload は偽 |
| `_running_move` | `progress/run.py` | 変更 | シグネチャは同じ | 2 つを足す。(1) 済んだステップの判定: `prog.next_step(s)` の前に、`s.steps` の承認ゲートのステップ（`listen`）で `prog.done` にあるものを順に見る。その report の `metrics.seq` が指す回答の事象（`v.row_events(s.name)` から seq で引く）で `mvv_answer_void(payload_of(<その事象>), step.gate)` が真で、そのステップより後ろのステップが 1 つも済んでいなければ、そのステップを済んでいないものとして扱う（後ろのステップが済んでいれば、リリースの PR のマージなどが既に流れたので開き直さない）。手はそのステップの承認ゲートの待ち（`GATE`）になり、`ndf progress next` は人へ問う（下の「消費済みの回答の開き直し」）。(2) 回答 `prog.answer` が `mvv_answer_void(payload_of(prog.answer), step.gate)` で真なら、回答が無いものとして手を決める。承認ゲートのステップ（`listen`）なので `waiting` は真、`mvv_skip` は None なので `skip` は偽で、`GATE` を返す。`Move.event` に数えない回答を渡さない。ほかは今と同じ |
| `_consume` | `progress/run.py` | 変更 | シグネチャは同じ | 回答 `m.event` があり、`mvv_answer_void(payload_of(m.event), m.step.gate)` が真なら、事象を書かずに `report(v, replace(m, action=Action.GATE, event=None))` を返す（承認ゲートの待ちの結果。ステップを済みにしない）。`m.event` が None のとき `service:mvv` の回答を書くのは今どおりだが、`mvv_skip` が None なら書かずに同じ待ちの結果を返す（今は `held` が None でも回答を書く形で、`_running_move` の条件に頼って閉じている。区分の条件を `_consume` の側でも持つ） |
| `approved_head` | `progress/gate/records.py` | 変更 | シグネチャは同じ | 区分 `kind` の最後の回答で `model.mvv_answer_void(<その回答の payload>, kind)` が真なら、承認が無いとして None を返す（承認の後に文書だけを足した場合の head を記録した accepted ファイルも読まない）。数えない回答を飛ばしてその前の回答を読むことはしない（前の世代の承認を今の承認と取り違えないため）。`answers` が返す一覧の行は変えない（数えない回答も記録として載る） |

消費済みの回答の開き直し:

- 対象は、更新の前に `service:mvv` の回答を消費して承認ゲートのステップが済み、まだ本番へ反映していない実行である。見分け方は、その工程の行が終わっておらず（完了・中止でない）、承認ゲートより後ろのステップが 1 つも済んでいないことである。後ろのステップが済んだ実行と、工程の行が終わった実行は対象にしない。設計 PR のマージやリリースが済んでいて、承認を取り直しても戻せないためである
- 再開した `decide` は、`_running_move` の (1) で承認ゲートのステップを済んでいないものとして扱い、`GATE` を返す。後ろのステップ（リリースの PR のマージなど）は承認ゲートの後にしか流れないので、人の回答の前には流れない
- 更新の時点で後ろのステップが走っていて、`approved_head` が None を返したためにマージの側で止まり、工程の行が失敗（`failed`）になった実行も同じである。`ndf progress next` は今どおり失敗を返し、打ち直し（行の開始）の後の `decide` が `GATE` を返す。どちらの経路でも、人は `ndf answer` で今の承認ゲートの質問に答える
- 質問の id は `model.question_id` が今の数え始め（`anchor`。消費した回答の `step_recorded` の番号）から作るので、消費済みの回答の質問と別の id になる。人の回答は後の事象なので、`_running_move` の (2) がそれを読む回答にする
- 人が承認すると `_consume` が新しい `step_recorded` を書き、`approved_head` は人の回答の head を返す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_running_move`・`_consume` | 更新前の NDF が書いた `service:mvv` の回答がリリース承認ゲートに残る | 更新の前に `fast`・`auto` のリリース承認ゲートを判定で省いた | `mvv_answer_void` | 回答を数えず `GATE`。事象は消さない | `ndf progress next` が今の承認ゲートの質問（質問の id と回答のルート）を返す。終了コードは人を待つ承認ゲートと同じ | 人が `ndf answer` で回答すれば続く。打ち直しても同じ待ちになる |
| `_running_move`・`_consume` | 更新前の NDF が書いた、保留を確かめた証拠の無い `service:mvv` の回答が設計承認ゲートに残る | 更新の前に `fast`・`auto` の設計承認ゲートを判定で省いた | `mvv_answer_void` の (b) | 同上 | 同上 | 同上 |
| `_running_move` | 更新前の NDF が書いた `service:mvv` の回答で済んだ承認ゲートのステップがあり、後ろのステップがまだ済んでいない | 更新の前に省いた回答を消費した | `mvv_answer_void`（済んだステップの report の `metrics.seq` の回答） | そのステップを済んでいないものとして `GATE` | 同上 | 同上 |
| `approved_head` | 区分の最後の回答が数えない回答 | 同上 | `mvv_answer_void` | None | マージの側（`ndf answer head`）は承認が無いときと同じに止まる | 打ち直すと `_running_move` が承認ゲートを開き直す。人の承認の後は、その回答の head を返す |
| `mvv_answer_void` | payload に `by` が無い・文字列でない | 手で書いた事象 | 関数の中 | 偽（数える。今の振る舞いのまま） | — | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mvv_skip`・`decide`（リリース） | — | 判定の出力が `skip: true` でもリリース承認ゲートでは None → `service:mvv` の回答を書かず `GATE` | — |
| `mvv_skip`（設計） | `skip: true`・`holds: []`・head あり → 今どおり省く | `skip: true` で `holds` のキーが無い（更新前の NDF が書いた判定）・`holds: null`・保留が 1 件 → None | — |
| `mvv_answer_void` | `by: service:mvv`・区分 `release` → 真 | 区分 `design` で `mvv_gate` の写しが `holds: []` → 偽。区分 `design` で写しの `holds` のキーが無い・`null`、写しそのものが無い → 真。区分 None → 真。`by: human`・`conductor` → 偽 | `by` が無い・文字列でない → 偽 |
| `decide`・`_consume`・`approved_head`（更新前の NDF が書いた記録から再開。`tests/progress/test_run.py`・`tests/progress/gate/test_gate_after_approval.py`） | 更新前の NDF が書く形の事象を置いたステートマシン（リリースの判定のステップの済んだ記録に `mvv_gate: {"gate": "release", "skip": true, "head": <sha>}`、続けて `model.mvv_answer` の形の `answered`（`by: service:mvv`・`answer: approved`・その質問の `gate_id`））→ `decide` が `GATE` を返し、`ndf progress next` の結果が人へ問う質問になる。`approved_head(v, "release")` は None | 同じ記録の後に、人が同じ質問の id で `ndf answer`（`by: human`・`approved`）→ 次の `decide` が `CONSUME` で人の回答を読み、ステップが済む。`approved_head` は人の回答の head を返す。設計承認ゲートで、写しが `holds: []` を持つ `service:mvv` の回答 → 今どおり `CONSUME` で済み、`approved_head(v, "design")` はその head。設計承認ゲートで、更新前の NDF が書いた形（写しに `holds` のキーが無い）の `skip: true` と未消費の `service:mvv` の回答 → `GATE` で人へ問い、`approved_head(v, "design")` は None | `CONSUME` の手と数えない回答を `_consume` へ直接渡す → 事象を書かず、ステップは済みにならず、結果は承認ゲートの待ち。`m.event` が None で `mvv_skip` が None の手を `_consume` へ渡す → `service:mvv` の回答を書かない |
| `decide`（消費済みの回答の開き直し。`tests/progress/test_run.py`） | 更新前の NDF が書いた `service:mvv` の回答と、それを消費した承認ゲートの `step_recorded`（report の `metrics.seq` がその回答）を置き、後ろのステップ（リリースの PR のマージ）は済んでいないステートマシン → `decide` が承認ゲートのステップの `GATE` を返し、`ndf progress next` が人へ問う。質問の id は消費済みの回答の `gate_id` と違う。人が `ndf answer` で承認 → 次の `decide` が `CONSUME` で人の回答を読み、新しい `step_recorded` を書き、続く `decide` が後ろのステップへ進む。`approved_head(v, "release")` は人の回答の head | 同じ記録で後ろのステップが済んでいる → 開き直さず、今どおり済んだものとして進む。設計承認ゲートで、写しが `holds: []` の `service:mvv` の回答を消費した記録 → 開き直さない | 後ろのステップ（マージ）が `approved_head` の None で失敗した行 → `ndf progress next` は失敗を返し、打ち直しの後の `decide` が承認ゲートの `GATE` を返す |

## レビューの保留

満たす決定: 決定 2、受け入れ条件 4・5

基本の規則・異常系・テストは、同じ PR の要求仕様書の「[決まった規則](./issue-3959-requirements.md#決まった規則4077-の詳細設計から)」「[異常系](./issue-3959-requirements.md#異常系)」「[テストで落とす壊し方](./issue-3959-requirements.md#テストで落とす壊し方)」「[承認資料](./issue-3959-requirements.md#承認資料)」を参照する。版は、この PR の要求仕様書（#3959 の本文の、2026-11-23 に保留を絞った後の形）である。ただし、読み取り・開示・原因の扱いは、要求仕様書と食い違えばこの章とこの文書のほかの章に従う。要求仕様書から上書きする点は次のとおりである。

- `finish` の終わり方の優先順は、`mvv_record_failed` を `handed` の欠けと `mvv_unresolved` より優先する（要求仕様書の「呼ぶ順序と最終の判定」のとおり）。保存先を直して打ち直し、欠けた行を書けたときは、`finish` の普段の判定（`mvv_unresolved` なら `STOPPED`、欠けが無ければ収束）が終わり方になる。テストは下の `finish`・`cmd_run` の行と、要求仕様書の「テストで落とす壊し方」の `_summary`・`finish`・`cmd_run` の行
- 記録の読み取り: 要求仕様書の `review_rows(root)` は使わない。記録は `validation_rows` で判定 1 回につき 1 回だけ読み、`review_rows(rows)` は読んだ行から `mvv-review` の行を選ぶ純粋な関数にする。読めないときの例外は `OSError`・`UnicodeDecodeError`・`internal.InternalError` の 3 つで、保存先がファイルでないときも読めないものとして扱う（下の `validation_rows`）
- 開示: 保留の文（見送りの理由・レッドラインの指摘本文・記録を読めない原因）は出力の `holds` にだけ置き、`why` には件数の固定の文（`check_review_holds`）だけを入れる。supervisor の経路の承認資料には保留の文を載せず、件数と全文の参照先を載せる（「手順書」の「承認資料の開示の範囲」）
- 原因: 記録を読めない原因は例外の文でなく、`failure_text` の定型文と診断の記録の識別子にする（「結果に入れる原因の文」）
- `review_holds` の引数 `mvv` は `tuple[int, str]` にし、伏せた行（`withheld: true`）で終わるキーを保留にする 3 つ目の規則を足す（下の `review_holds`）

ここには、上の上書きと、この設計で決めた接ぎ目を書く。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `validation_rows` | `records/mvv.py` | 新設 | `validation_rows(root: Path) -> tuple[list[dict[str, Any]], list[jsonl.Broken]]` | `mvv-validation.jsonl` を `jsonl.read_rows` で 1 回読み、すべての種類の行と、読めずに除いた行（`jsonl.Broken`。位置 `before` と辞書として読めた中身 `raw` を持つ）を返す。件数だけにしない（再利用の判定 `reusable` が、除いた行の位置とキーで古い「従う」へさかのぼらないため）。記録がまだ作られていない（保存先のパスに何も無い。`os.path.lexists` が偽）ときだけ（`[]`, `[]`）を返す。`jsonl.read_rows` は `path.is_file()` が偽なら空を返すので、その前に `validation_rows` が保存先を確かめる。パスに何かがあるのに通常のファイルとして読めない（ディレクトリ・壊れたシンボリックリンク・特殊ファイル）ときは、`read_rows` を呼ばずに `OSError`（「MVV の記録の保存先がファイルでない」）を投げ、保留にする（読めた空の記録と扱わない）。`OSError`・`UnicodeDecodeError`・`internal.InternalError`（`read_rows` が新しい形式のバージョンの行で投げる、`kind` が `invalid` でないもの）は呼び出し元へ返す |
| `review_rows` | `records/mvv.py` | 新設（本文のシグネチャを変える） | `review_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]` | `validation_rows` の行から種類 `mvv-review` の行だけを返す純粋な関数。本文の `review_rows(root)` の読み取りを `validation_rows` へ移し、記録の読み取りを判定の再利用と共有する |
| `mvv_validate_sprint` | `records/mvv.py` | 変更 | `mvv_validate_sprint(root, inputs, judge, reuse: bool = True, rows: Optional[Sequence[Mapping[str, Any]]] = None, broken: Sequence[jsonl.Broken] = ()) -> tuple[dict[str, Any], Optional[str]]` | `reuse` が真なら再利用の候補を `rows` から選び、`rows` が None のときだけ今どおり記録を読む。`rows` を渡すときは、同じ読み取りで除いた行 `broken` も渡し、今と同じく `md.reusable(rows, key, [(b.before, b.raw) for b in broken])` に渡す（最後の判定の行が読めないときに、古い「従う」を今回の判定として使わない）。`reuse` が偽なら記録を読まない（今は偽でも読む）。追記の `OSError` は外へ出さず、判定の行と原因を返す（書けたら原因は None）。呼び手は `mvv_gate._judged` だけ（ほかはテスト） |
| `review_holds` | `records/mvv_decide.py` | 新設 | `review_holds(rows: Iterable[Mapping[str, Any]], broken: int, repo: str, prs: Iterable[int], mvv: tuple[int, str]) -> list[str]`。要求仕様のコピーの `mvv: tuple[str, str]` を `tuple[int, str]` に変える（比べる相手の行の `mvv_version` が整数のため） | 本文の規則（2026-11-23 に絞った形）で保留を決める。本文と設計の「今のバージョン」「同じバージョン」は、この文書では（`mvv_version`, `sha256`）の組の一致で判定する（番号だけが同じで `sha256` の違う行は別のバージョン）。(1) レッドライン: キーの最後の `redline` の行より後に同じキーの `fixed` が無ければ保留（後の `fixed` は head・`--state-dir`・バージョンによらず外す）。(2) 見送り: （`mvv_version`, `sha256`）の組が `mvv` と一致する `deferred` のうち、`defer` が `needs_user`・区分が無い・知らない値の行があり、後に同じキーで（`mvv_version`, `sha256`）の組も `mvv` と一致する `fixed` が無ければ保留。`defer` が `filed`・`not_applicable` の行は保留にしない。(3) キーの最後の行が、（`mvv_version`, `sha256`）の組が `mvv` と一致する `withheld: true` の行なら、終わり方によらず「PR #N の <項目>（記録の自由記述が未完了）」で保留する（#4090・#4099 の決定 7・16。記録を読めないときと同じ扱い）。承認資料には、伏せた行から来る理由・本文の代わりに `WITHHELD` の文を載せる。`deferred` の保留は「PR #N の <項目>（deferred・<区分>）: <理由>」の形にする。キーごとに返す保留は多くても 1 件で、優先順は (1) レッドライン、(3) 伏せた行、(2) 見送りである（本文の「見送り: 保留していないキーで」と同じく、上の規則で保留したキーは下の規則で数えない）。(1) は「PR #N の <そのキーの `redline` の行の項目の和>（redline）: <最後の `redline` の行の `text`>」で、最後の `redline` の行が伏せた行なら本文の代わりに `WITHHELD` の文。(2) の区分と理由は、保留の元になった行（今のバージョンで `needs_user`・区分が無い・知らない値の `deferred` のうち最後の行）から採り、その後ろの `filed`・`not_applicable` の行の理由で置き換えない。例: 同じキーに、今のバージョンの `deferred`（`needs_user`、理由 A）→ `deferred`（`filed`、理由 B）→ 後に `fixed` の無い `redline`（本文 T）とあれば、返すのは「PR #N の <項目>（redline）: T」の 1 件だけ。同じ記録から `redline` を除けば「PR #N の <項目>（deferred・needs_user）: A」の 1 件 |
| `check_review_holds` | `progress/gate/checks.py` | 新設 | `check_review_holds(f: MvvFacts) -> Optional[str]` | `f.review_holds` が空でなければ、固定の文「レビューの保留が <件数> 件ある（内容は保留の欄）」を返す。保留の文（見送りの理由・レッドラインの指摘本文・記録を読めない原因）を `why` へ連結しない。保留の文は出力の `holds` にだけ置き、`why` は省かない理由の定型の文（差分に載らないファイルの名前など、PR の識別子を含む）と `failure_text` が返す失敗の定型文だけで組む（承認資料の開示の範囲で、`why` を全件載せる経路があるため） |
| `MvvFacts` | `progress/gate/checks.py` | 変更 | 欄 `review_holds: tuple[str, ...] = ()` | — |
| `Journal.apply_fix` | `crossreview/journal.py` | 変更 | シグネチャは同じ | 修正担当の結果の `findings[]` の `defer`（`needs_user`・`filed`・`not_applicable`）を、`deferred` の行に `reason` とともに持つ。知らない値・無い値は置かない（`record_review` が区分の無い行として書き、`review_holds` が保留にする） |
| `_fix_prompt`（結果の形の行） | `crossreview/drive.py` | 変更 | シグネチャは同じ | 修正担当へ渡す結果の形に `"defer": "needs_user|filed|not_applicable"`（`outcome` が `deferred` のときだけ）を足す |
| `out_of_scope`・区分の説明 | `contracts/data/review-criteria.json` | 変更 | — | 見送るときの区分の選び方を書く。`needs_user`: 利用者の判断が要る（方針・範囲・レッドラインに当たる選択、書き込みの前に承認が要る起票）。`filed`: 範囲外として起票し、番号を理由に書いた。`not_applicable`: 指摘の前提が違う・基準に当たらない・意図どおり |
| `finish` | `crossreview/finish_decide.py` | 変更 | `finish(review, handed, record_failed: Optional[Mapping[str, Any]] = None) -> Verdict` | 本文の規則のとおり（`mvv_record_failed` を最優先） |
| `ReviewWrite` | `records/mvv.py` | 新設 | `NamedTuple(written: int, missing: tuple[Mapping[str, Any], ...], cause: Optional[str])` | `record_review` の結果。`missing` は書けなかったか試さなかった必須の行（`redline`・`deferred`）、`cause` は最初の失敗の原因（ロックの取得・既存の行の読み取り・追記のどれか。失敗が無ければ None）。形と伏せ方は下の `record_review` の手順 (3)（認証情報を伏せた文で、原文を持たない） |
| `record_review` | `records/mvv.py` | 変更（#4076 の契約を変える） | `record_review(repo: str, pr: int, head: str, snapshot: Snapshot, rows: Sequence[Mapping[str, Any]], clean: Optional[Callable[[Sequence[str]], Sequence[str]]]) -> ReviewWrite` | 引数は今のコードのまま変えず、返り値だけを `int` から `ReviewWrite` に変える。`repo` は呼び手が解決済みの `repo_key` の値、MVV の識別子は `snapshot.identity` から取る。呼び手は `crossreview/drive.py` の `_record_mvv` だけ（ほかはテスト）。下の手順。伏せた行（`withheld: true`）の追記の失敗も、その行が必須の行なら `missing` に載せる |

- 接ぎ目（`mvv_gate._judged` の手順 (5)。`review_prs`）:
  - `prs` は `design` では判定する PR の 1 件。
  - `release` では空で、保留を読まない。リリースに含まれる PR の範囲はブランチ戦略ごとに決め方が違い、承認資料と同じ確定した集合から読む仕組みは #4105 が持つ。それまでリリース承認ゲートは `fast`・`auto` でも省かない（`_judged` の手順 (8)、決定 3）ので、保留を読まなくても省かれる経路は無い。承認資料の「MVV 判定」の欄には「リリースの保留は #4105 まで読まない（省かない）」を載せる。API の呼び出しは増えない。
  - `mvv` は `snap` の（`version`・`sha256`）で、行の `mvv_version`（整数）・`sha256` と比べる（行の `version` は行の形のバージョンで、MVV のバージョンではない）。
  - `repo` は `mvv.repo_key(root)`。
  - `validation_rows` が `OSError` か `UnicodeDecodeError`（`jsonl.read_rows` が不正なバイトで投げる。`OSError` の子でない）か `internal.InternalError`（新しい形式のバージョンの行。新しい版の NDF が書いた記録を読む構成で起きる）を投げたら、どれも判定を止めない。保留を「MVV の記録（mvv-validation.jsonl）を読めない: <`failure_text("records_unreadable", 例外, …)`>」の 1 件にし、手順 (7) は記録を読まずに再利用を省いて判定する。要求仕様の異常系の表の `review_rows` の行が挙げる `OSError` は、`validation_rows` が投げる上の 3 つの例外（`OSError`・`UnicodeDecodeError`・`internal.InternalError`）に読み替える。
  - 読めたときは、同じ行を 2 か所へ渡す。1 つは `review_rows` → `review_holds`（除いた行は件数 `len(broken)` を `broken` に渡す）、もう 1 つは手順 (7) の `mvv_validate_sprint(rows=…, broken=…)` である（記録のファイルを読むのは判定 1 回につき 1 回）。
    - 結果を `MvvFacts.review_holds` に入れ、`gate_outcome` の `holds` にも同じ値を渡す（承認資料と PR のコメントは同じ結果を読む）
- `record_review` は `deferred` の行に、ジャーナルの行の `defer` を書く（無ければ書かない）。`defer` は自由記述でないので、伏せた行（`withheld: true`）にも書く。`mvv-validation.schema.json` は知らない欄を拒むので、欄 `defer` を足す
- `record_review`（#3959 の本文の「記録の欠落」の書き込み元の契約。#4076 の `-> int` と `OSError` の契約を置き換える）:
  - (1) 書く行を、必須の行（`redline`・`deferred`）を先に、ほか（`fixed`・`unresolved`）を後に並べる（同じ種類の中は今の順）。
    - 重ねない判定は今のコード（`mvv_decide.review_recorded`）の規則に `defer` の比較だけを足す（`_REVIEW_STATE` に足し、通常の行と伏せた行の両方の比べ方に入れる）
    - ここに全項目を書く: `mvv-validation.jsonl` は、同じ（repo・PR・key）の最後の行（#4076 の「最後」の決め方）と比べる。（`head`・`outcome`・`mvv_version`・`sha256`・`defer`・`item`・`reason`・`text`・`withheld`）がすべて同じときだけ書かない（`defer`・`item`・`reason`・`text` は持たない行どうしでは無いもの同士として同じとみなす。`withheld` は無い行を偽と読む）
    - この比べ方に、伏せた行の 2 つの規則を加える: (a) 書く行が伏せた行（`withheld: true`）で、最後の行が伏せていない行のときを考える。（`head`・`outcome`・`mvv_version`・`sha256`・`defer`）が同じなら書かない（分かっている理由を伏せた行で上書きしない。`defer` が変わっていれば書く）
    - (b) 書く行が伏せていない行で、最後の行が伏せた行なら、ほかの項目が同じでも書く（記録の復旧。これで `review_holds` の 3 つ目の規則の保留が解ける）
    - 同じキーでもバージョンか `sha256` が違えば書く（前のバージョンの `deferred` の行は今のバージョンの保留にならないので、今のバージョンでも見送ったら今のバージョンの行が要る）
    - `mvv-signals.jsonl` は（repo・PR・key・`mvv_version`・`sha256`）が同じ `review_disputed` の行があれば書かない（伏せたかによらない）
  - (2) ロックの中で、まず書く行をすべて重ねない判定にかけ、追記する行の並び（追記の対象）を確定してから、その並びを 1 行ずつ追記する。
    - 重ねない判定と追記を 1 行ずつ交互にしない（交互にすると、追記の失敗の後ろの行が記録に既にあるかを確かめていない）
    - 保存先の決定・ロックの取得・既存の行の読み取り・重ねない判定・追記（今の `_append_missing` の全体）を 1 つの失敗の範囲とする。そこで `OSError`（ロックの時間切れを含む）・`UnicodeDecodeError`・`InternalError`・`IdentityError`・`MvvError` が出たら止め、例外を外へ出さない
    - `mvv-validation.jsonl` で失敗したら `mvv-signals.jsonl` は試さない
  - (3) 必須の行のうち記録に無いものを `missing`、原因を `cause` にして返す。
    - 追記の前（保存先・ロック・読み取り・判定）で失敗したときは、どの行が記録にあるかを確かめられないので、書こうとした必須の行をすべて `missing` にする
    - 追記で失敗したときは、確定した追記の対象のうち、書けなかった行とその後ろのまだ追記していない行で、必須のものだけを `missing` にする（重ねない判定で追記の対象から外した行は記録にあるので入れない）
    - 必須の行を先に書くので、通常の行の追記の失敗は `missing` を作らない
    - `mvv-signals.jsonl` の失敗も `missing` を作らず、`cause` だけを返す
    - 呼び手の `_record_mvv` が `repo` を解けずに `record_review` を呼べないときも、今の MVV で判定した指摘の必須の行をすべて書けなかったと扱う
    - `_summary` は、`missing` が空でなければ `Outcome.mvv_record_failed` に `{"rows": [<key と outcome>], "cause": RECORDS_UNWRITTEN}` を入れる。空で `cause` があれば、今どおり注意に留める
    - 失敗の原因は、例外の文の認証情報を伏せてから、操作の名前と例外の型を添えて、手元の状態ファイルの `store.MVV_RECORD_ERROR` にだけ保存する
    - 調査に要る保存先の絶対パスは残す
    - 例外の文そのもの（伏せる前の文）は、状態ファイルにも結果 JSON にも書かない
    - 状態ファイルも共有されたり調査用に集められたりするので、書く前に伏せる
    - 結果 JSON の `warnings` に `Outcome` と `finish` が足すのは、今の固定の注意 `RECORDS_UNWRITTEN`（「原因は状態ファイルの `mvv_record_error` にある」）だけである。`ReviewWrite.cause` は入れない（保存先の絶対パスが入るため）
    - `ReviewWrite.cause` と状態ファイルに書く値の形は `"<操作>: <例外の型の名前>: <伏せた文>"` である
    - `<操作>` は失敗した処理（保存先の決定・ロックの取得・既存の行の読み取り・追記・`mvv-signals.jsonl` の追記のどれか）である
    - `<伏せた文>` は、例外の文を `publish.scrub(<例外の文>, False).text` に通したものである
    - 書き先を非公開として扱うので、公開範囲を照会しない
    - 秘密の値と、認証情報の付いた URL の全体を伏せ、保存先の絶対パスは残す
    - `_record_mvv` が `record_review` を呼べずに受けた例外も、同じ形と同じ伏せ方で保存する（今の `_record_mvv` は `"<型>: <例外の文>"` を伏せずに書くので変える）
    - 打ち直すと、重ねない判定で書けた行は飛ばし、欠けた行だけを書く
- 保留は判定を止めない。LLM の判定の後の `may_skip` でだけ効く（決定 1）
- 承認資料: 「MVV 判定」の欄の出所を、今回の判定の出力 `outputs.mvv_gate`（`verdict`・`why`・`holds`・`noted` と判定の中身）にする（下の「手順書」の「承認資料の出所」）。プランの経路では、ステップの承認資料の生成元 `progress/gate/asking.py` の `material` がこの欄を組む。supervisor の経路では、`approval-request.md` の手順が同じ載せ方で組む（ただし呼び出し元へ返す本文では自由記述を伏せる。下の「承認資料の開示の範囲」）。規則を手順書にコピーせず、出力の値を載せる

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_holds` | 本文の「テストで落とす壊し方」の表のとおり（絞った後の形）。上の関数の仕様の例（`needs_user` → `filed` → 直っていない `redline`）→ 「（redline）: T」の 1 件。`redline` を除いた同じ記録 → 「（deferred・needs_user）: A」の 1 件 | `redline` → `fixed` → 外す。`redline` → `fixed` → `redline` → 保留。`deferred`（`filed`）・`deferred`（`not_applicable`）だけ → 空。`deferred`（`needs_user`）→ 保留。区分の無い `deferred`・知らない区分 → 保留。`mvv_version` が同じで `sha256` だけが違う行: `deferred`（`needs_user`）の後の `fixed` の `sha256` が違う → 保留のまま。`deferred`（`needs_user`）の `sha256` が違う → 空。最後の `withheld: true` の行の `sha256` が違う → 伏せた行の保留は無い。`redline` の後の `fixed` の `sha256` が違う → 外す（レッドラインはバージョンによらない） | `broken` が 1 以上 → 読めない行の件数を 1 件 |
| `_judged`（保留） | `design` は設計 PR の行を読む。`release` は保留を読まず、`fast`・`auto` でも `skip` は偽で `why` に #4105 の理由。保留があると `fast` で「従う」でも `skip` は偽、`why` と `holds` に載る。記録を読めるとき、`jsonl.read_rows` が判定 1 回で 1 回だけ呼ばれ、同じ材料の「従う」の行があれば LLM を呼ばない | 記録のファイルが無い → 保留は空（`holds: []`） | `validation_rows` の `OSError` → 読めない理由の 1 件。記録の保存先のパスにディレクトリを置く・壊れたシンボリックリンクを置く → `read_rows` を呼ばず、`holds` に読めない理由の 1 件、`fast` で「従う」でも `skip` は偽。不正なバイト（`b"\xff\xfe"` を含む行）を書いた実際のファイルで、`mvv_validate_sprint` をスタブにせず実物を通す → 例外で止まらず、LLM のスタブが 1 回呼ばれ（同じ材料の「従う」の行が記録にあっても再利用しない）、判定の行が追記され、`holds` と PR のコメントに読めない理由の 1 件、`fast` でも `skip` は偽。同じファイルで追記も `OSError` にする → `verdict` は判定のまま・`skip` 偽・`holds` に読めない理由・`why` に記録に書けない理由。新しい形式のバージョンの行を含む実際のファイル → 例外で止まらず、`holds` に読めない理由の 1 件、再利用せず LLM のスタブが 1 回、`skip` 偽。同じ材料の「従う」の行の後ろに、同じキーの壊れた行（形の違う行）がある実際のファイルで、`gate` から `mvv_validate_sprint` をスタブにせず実物を通す → 保留に読めない行の 1 件、古い「従う」を再利用せず LLM のスタブが 1 回呼ばれ、出力と PR のコメントの判定は今回のスタブの判定（`_judged` が `mvv_validate_sprint` に `broken` を渡さないと、このテストが落ちる） |
| `check_review_holds` | 保留 2 件 → 「レビューの保留が 2 件ある（内容は保留の欄）」 | 保留が空 → None | 偽の見送りの理由と偽のレッドラインの本文を持つ保留 → 返す文にどちらも無い。同じ保留を持つ判定の出力の `why` にも無く、`holds` にだけある |
| `mvv_validate_sprint` | `rows` を渡すと記録を読まずにその行から再利用する | `reuse=False` → 記録を読まない（読めないファイルでも止まらない） | 追記の `OSError` → 例外を出さず、判定の行と原因を返す |
| `record_review` | 全部書ける → `missing` 空・`cause` None | 渡した順が `fixed`・`redline` でも `redline` を先に書く。2 行目（通常の行）で失敗 → `missing` 空・`cause` あり。1 行目（必須の行）で失敗 → `missing` に失敗した行と後ろの試さなかった `deferred`。先頭の必須の行の追記が失敗し、後ろの必須の行は同じ状態で既に記録にある → `missing` は先頭の行だけ（既に記録にある行を欠落と報告しない）。`mvv-signals.jsonl` だけが失敗 → `missing` 空・`cause` あり | 既存の `mvv-validation.jsonl` が不正な UTF-8（`b"\xff\xfe"` を含む行）で、`redline`・`deferred`・`fixed` を渡す → 例外を出さず、`written` 0・`missing` に `redline` と `deferred`・`cause` あり、ファイルは変わらない。同じファイルで `fixed` だけを渡す → `missing` 空・`cause` あり。ロックの取得の `OSError`、新しい形式のバージョンの行の `InternalError` → 同じく必須の行がすべて `missing`。失敗の後に打ち直す → 書けた行を重ねず、`missing` の行だけを書く |
| `record_review`（重ねない判定） | 同じキー・同じ head・同じ終わり方・同じバージョン・同じ `sha256`・同じ `defer`・同じ項目・同じ理由の最後の行がある → 書かない | 前のバージョンで `deferred` の最後の行があるキーを、今のバージョンでも同じ理由で `deferred` にする → 今のバージョンの行を書き、`review_holds` が今のバージョンの保留を返す。同じ head・同じ終わり方で理由だけが変わる → 書く。`sha256` だけが変わる → 書く。同じ head・同じ理由の `deferred` で `defer` だけが `filed` から `needs_user` に変わる → 通常の行も伏せた行も書き、`review_holds` が保留を返す。最後の行が伏せた行で、同じ（head・終わり方・バージョン・`sha256`・`defer`）の伏せていない行を書く → 書き、`review_holds` の「記録の自由記述が未完了」が消える。最後の行が伏せていない行で、同じ（head・終わり方・バージョン・`sha256`・`defer`）の伏せた行を書く → 書かない | — |
| `finish`・`cmd_run` | 本文の表のとおり | 同 | 同。既存の `mvv-validation.jsonl` が不正な UTF-8 で `redline` がある → 収束の判定でも `STOPPED`・`mvv_record_failed`。保存先の偽の絶対パス（`/home/<偽の名前>/...`）を含む `OSError` で必須の行を書けない → 結果 JSON の `warnings` と `cause` に偽のパスが無く、状態ファイルの `mvv_record_error` には操作の名前・例外の型の名前・偽のパスが残る。例外の文に偽のトークンを付けたリモートの URL（`https://x-access-token:<偽のトークン>@github.com/<所有者>/<リポジトリ>`）と、環境変数に置いた偽の秘密を含める → 状態ファイルの `mvv_record_error` に偽のトークンと偽の秘密が無く、URL の全体が伏せ字になる（`record_review` の中の失敗でも、`_record_mvv` が受けた例外でも同じ） |

## 根拠の行の確かめ（`cite`）

満たす決定: 決定 4、受け入れ条件 7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Cited` | `records/mvv.py` | 新設 | `NamedTuple(errors: list[dict[str, Any]], checked: int)` | `cite` の結果。`errors` は `citation_errors` の誤り、`checked` は確かめた根拠の行の数（誤りが 0 件でも数える） |
| `cite` | `records/mvv.py` | 新設 | `cite(snap: Snapshot, files: Sequence[Path], require_basis: bool = False) -> Cited` | `require_basis` が真なら、各ファイルに見出し「目指す姿の根拠」の節が無いことを誤りにする（下の「承認資料の見出しの契約」）。各ファイルの本文に `fenced_lines`（#4090・#4099 の形。内外を `code_lines` で決める）と `decision_lines` を当て、フェンスの外の行ごとに（パス・行の番号・本文・決定の根拠の行か）の 4 要素を作って `citation_errors` に `{snap.version: snap.items}` とともに渡す。4 番目は `decision_lines(text)` に行の番号があるかで決め、`False` を決め打ちしない（cross-review の `_citations` と同じ関数・同じ規則）。`checked` は、4 要素を作る同じ走査で、`citation_errors` が根拠の行として読む行（`parse_citation` が引用か形の誤りの候補を返す行。`citation_errors` と同じ関数で数え、別の規則を作らない）を数える。`citation_errors` の返り値から逆算しない（誤りの無い行は返り値に現れない）。読み取りの順は決めてある。`cite` は MVV の状態を見る前に、`files` のすべてを UTF-8 で読む（読めなければ `OSError`・`UnicodeDecodeError` をそのまま投げる）。すべて読めた後で、`snap.state` が `absent`・`unapproved` なら `Cited([], 0)`（確かめるバージョンが無い）を返す。MVV の状態で先に返すと、承認済みの MVV が無いリポジトリでは誤ったパスを渡しても読めないことが報告されないためである。`mismatch` の分岐は持たない（`current` が先に `MvvError` を投げる） |
| `_mvv_cite` | `records/cli.py` | 新設 | `_mvv_cite(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 必須の引数 `--gate design|release`（`ndf records mvv gate` の `--gate` と同じ値）を受け、`design` なら `require_basis=True` で `cite` を呼ぶ（担当者が設計文書の根拠を転記した設計承認資料は「目指す姿の根拠」の欄を必ず持つ。`approval-request.md` の欄の表）。`--base` から #4076 の CLI の `_mvv_current` と同じく取得して `current` を呼び、`cite` の結果を、下の「伏せ方」を通してから結果 JSON にする。どの MVV の状態でも、ファイルを読めなければ `precondition` で、`ok` にしない |

- 事前条件: `files` は UTF-8 のテキスト
- 伏せ方:
  - `citation_errors` の 1 件の `path`・`cited`・`unknown` は承認資料から取った自由記述である。そこで `_mvv_cite` は、結果の `items` と `summary` を組む前に、全件のこの 3 つの欄を 1 つの文字列の並びにする。それを、cross-review の `_summary` と同じ包み（`report_decide.once` で包んだ、cross-review の `_github_hub` の `clean` と同じ `publisher.guarded`）に 1 回で通す。`items` と `summary` には、伏せた後の値だけを置く。
    - `path`・`cited` は文字列で、`unknown` は文字列の配列（`Citation.unknown: list[str]`）なので、並びには `unknown` の要素を 1 つずつ文字列として入れ、各件が何要素を入れたかを覚えておく
    - 包みの返した並びを同じ位置と要素数で切り戻し、`unknown` は伏せた後の文字列の元の順の配列に戻す（配列そのものを包みへ渡さない）
  - 生の値は結果にも `warnings` にも載せない。
    - 包みが `failure` を持てば、`path`・`cited` は `WITHHELD` の文になり、`unknown` は要素ごとに `WITHHELD` の文にした同じ長さの配列になる（配列を 1 つの文字列にまとめない）。あわせて注意「根拠の行の誤りの自由記述を伏せた（参照先の公開範囲を照会できない）: <原因>」を足す（終わり方は誤りの件数で決め、`refused` のまま）
    - この `<原因>` は照会の例外の文なので、例外の文を載せない。代わりに、`mvv_gate.failure_text("cite_lookup_failed", <例外>, {"gate": <区分>})` の定型文と診断の記録の識別子にする（例外の全文は診断の記録にだけ残る）
  - 伏せ方は、判定するリポジトリの公開範囲によらず、公開の書き先と同じにする。`cite` の結果 JSON（`items`・`summary`）の受け手は、承認資料を組む担当とその呼び出し元で、判定するリポジトリの閲覧者とは限らないためである（非公開のリポジトリから打っても、結果は外部の呼び出し元へ渡りうる）。そこで `_mvv_cite` は、`Publisher.guarded` に新設の引数 `assume_public=True` を渡し、書き先（対象のリポジトリ）の公開範囲を照会せずに公開として扱わせる。伏せるのは、秘密の値・認証情報の付いた URL の全体（下の「認証情報の付いた URL」）・ホームの絶対パス・メールアドレス・手元のアカウント名と、照会で公開と確かめられなかったリポジトリへの参照である。`assume_public` の既定は偽で、PR のコメントや Issue へ書くほかの呼び手（書き先そのものが閲覧の範囲を決める）は今どおり書き先を照会する
  - `assume_public=True` では、対象のリポジトリ自身への参照も伏せる対象に入れる。今の `Publisher._private_refs` は、書き先と同じリポジトリ（`same_repo(r, self.hub.repo)`）への参照を照会から外して残す。書き先へ書く本文なら、その参照を読める人は書き先を読める人と同じなので、それで漏れない。しかし `cite` の結果の受け手は対象のリポジトリを読めるとは限らないので、非公開のリポジトリ自身の名前や課題・PR の番号が結果に残ると、閲覧の権限の無い呼び出し元へ渡る。そこで `_private_refs` は、`assume_public` が真のときだけ `hub.repo` を除外せず、他のリポジトリへの参照と同じ 1 回の `visibilities` の照会に入れ、`PUBLIC` と返った参照だけを残す。`assume_public` が偽の呼び手は今どおり `hub.repo` を除外する
  - 誤りが 0 件なら包みを呼ばない（照会が増えない）。誤りが 1 件以上のときの `guarded` の照会は、どれも GitHub の GraphQL の `visibilities` である。`_mvv_cite` は 1 回の起動で `Publisher` を 1 つだけ作り、`guarded` を 1 回だけ呼ぶ。照会するのは、3 つの欄にリポジトリへの参照（`repo_refs`。対象のリポジトリ自身への参照を含む）があるときだけである。その参照の公開範囲を、`github.graphql-batch`（100）件ごとに 1 回照会する。`PUBLIC` でない（`UNKNOWN` を含む）参照を伏せる。
  - したがって GraphQL は、誤りが 0 件なら 0 回、誤りがあってもリポジトリへの参照が無ければ 0 回、参照があれば参照 100 件ごとに 1 回。書き先としての対象のリポジトリの公開範囲（`Publisher.visibility`）は照会しない。対象のリポジトリ自身への参照は、参照の照会の中で他の参照と同じ回に数える。
  - 参照の照会の失敗は包みの `failure` になり、上のとおり各欄を `WITHHELD` にする。
  - 参照の照会の候補は原文から取る。`run_log.redact` より前に秘密の値が GitHub へ送られないよう、`Publisher._private_refs` は候補を照会に渡す前に検査する。次の 2 つの候補は `visibilities` に渡さず、公開でない参照として返す。
    - 候補の参照そのものに `run_log.redact` をかけて変わる候補（秘密の値か `SECRET_SHAPES` の形を含む）
    - 原文の中の候補の一致の範囲が `secret_spans` の範囲と重なる候補。`secret_spans` の範囲は、秘密の値と `SECRET_SHAPES` の一致の範囲である（下のとおり固定の SSH の利用者名 `git@` を除く）
  - 出力の側も、照会の候補の除外と同じ原文の一致範囲を使う。
    - `scrub` は原文の上で、秘密の一致範囲（秘密の環境変数の値と `SECRET_SHAPES`。`run_log` に一致範囲を返す関数 `secret_spans` を足し、`run_log.redact` もそれで置き換える）と非公開の参照の一致範囲を求め、重なる範囲を和にまとめてから 1 回で置き換える
    - 秘密を一部でも含む範囲は秘密の伏せ字にし、参照だけの範囲は `PRIVATE_REF_TEXT` にする
    - 今の `scrub` のように参照を先に置き換えてから `run_log.redact` をかけると、参照の境界をまたぐ秘密の残りの断片が `redact` に一致せず出力に残るためである
    - 今の `scrub` が参照を先に置き換えるのは、逆の順にすると別の漏れが起きるためである
    - 秘密の伏せ字を先にかけると、`ssh://git@github.com/<所有者>/<リポジトリ>` が `ssh://[REDACTED]@github.com/<所有者>/<リポジトリ>` に変わる。残った文が参照の形に一致しなくなり、非公開のリポジトリ名が出力に残る（`publish.py` の `scrub` のコメント）
    - 新しい順では、秘密の範囲も参照の範囲も伏せ字をかける前の原文で求めるので、この参照も原文のまま一致し、和の範囲に入って落ちる
    - どちらの漏れも起きない
  - 認証情報の付いた URL も参照として拾い、URL 全体を伏せる。
    - 今の `publish._REPO_URL` は、スキームの後の利用者の部分に `git@` しか許さない
    - そのため `https://x-access-token:<トークン>@github.com/<所有者>/<リポジトリ>` のような一般の認証情報付きの URL や、`git._mask_urls` が認証情報を `***@` に置き換えた後の `https://***@github.com/<所有者>/<リポジトリ>` からは、`repo_refs` が参照を取れない。2026-11-24 に今のコードで確かめた
    - 参照の範囲が得られないと、秘密の範囲（`<トークン>@`・`***@` の部分）だけが伏せられ、所有者名とリポジトリ名が残る
    - そこで `_REPO_URL` を、スキームの後に任意の利用者の部分（`[^\s/@]+@`。`user:pass@`・`<トークン>@`・`***@` を含む）を許す形に変える
    - 拾った参照の一致の範囲は URL の全体（スキームから末尾まで）なので、利用者の部分の秘密の範囲と重なり、上の和の規則で URL の全体が秘密の伏せ字になる
    - この扱いは参照のリポジトリにも書き先の公開範囲にもよらない
    - 判定する PR のリポジトリの URL でも、公開のリポジトリの URL でも、認証情報が付いていれば URL の全体を伏せる
    - 書き先が `PRIVATE`・`INTERNAL` で `scrub` が `public` 偽で呼ばれるときも、秘密の範囲だけを伏せて終えない
    - `scrub` は公開範囲によらず、原文の上で秘密の範囲と `_REPO_URL` の一致の範囲を求め、秘密の範囲と重なる URL の一致を URL の全体へ広げてから伏せる（URL の範囲は原文から求めるだけなので、公開範囲の照会は増えない）
    - `public` が偽のときは、秘密と重ならない参照は照会せずにそのまま残す（今と同じ）
    - `***@` は `SECRET_SHAPES` の URL の資格情報の形に一致するので、伏せ字済みの URL も同じく秘密の範囲として扱う
  - 固定の SSH の利用者名 `git@` は秘密として扱わない。
    - `SECRET_SHAPES` の URL の資格情報の形（`(?<=://)[^\s/@]+@`）は、`ssh://git@github.com/<所有者>/<リポジトリ>` の `git@` にも一致する
    - このままでは上の検査で、公開のリポジトリの標準の SSH の URL まで照会から外れ、非公開として伏せられる（今の `Publisher` は、公開と分かった参照の所有者名とリポジトリ名を残す）
    - そこで `secret_spans` は、次の 3 つがそろう一致だけを秘密の範囲に入れない。スキームがちょうど `ssh`、利用者の部分がちょうど `git`（パスワードを持たない）、直後のホストがちょうど `github.com`（`github.com.example` のように後ろに続く名前を含まない）
    - `https://git@github.com/...` のように、スキームが `ssh` でなければ利用者名が `git` でも例外にしない。ほかの認証情報付きの URL と同じく URL の全体を伏せる（HTTPS の利用者名は認証に使う値で、固定の SSH の利用者名とは扱いが違う）
    - この `git@` だけを含む参照は、ほかの参照と同じく照会し、公開なら残し、公開でなければ `PRIVATE_REF_TEXT` にする
    - `git@` の外で、秘密の環境変数の値か、実際の認証情報（`git` 以外の利用者名・パスワード・トークン・`***@`）の範囲と重なる参照は、照会せずに URL の全体を秘密の伏せ字にする
    - `run_log.redact` も `secret_spans` で置き換える。そのため、記録やログの `ssh://git@github.com/...` も `ssh://[REDACTED]@github.com/...` でなくそのまま残る（`git` は秘密でないので、伏せ字をやめても漏れは無い）
  - 照会の回数は、検査で外した候補を数えない 100 件ごとの 1 回になる。この検査は `Publisher` の中に置くので、同じ包みを通る cross-review の `_summary` と Issue・コメントの書き込みも同じ契約になる
- 結果 JSON:
  - 誤りが 0 件なら `ok` にする。`summary` は「根拠の行の誤り 0 件（MVV バージョン <snap.version>・<ファイルの数> ファイル）」にする。`metrics` に `mvv_version`・`commit`（`snap.commit`）・`checked`（`Cited.checked`。誤りがあるときも置く）
  - MVV が `absent`・`unapproved` のときは、すべてのファイルを読めた後でだけ `ok` にし、`summary` は「承認済みの MVV が無いので確かめていない」だけにする。確かめていないので、`metrics` に `mvv_version`・`commit`・`checked` を置かず、件数やバージョンを補った文も書かない
  - 1 件以上なら `stopped`・`refused`、`items` に伏せた後の誤り、`summary` は「根拠の行の誤り N 件（最初: <伏せた後の path>:<line> <伏せた後の cited>）」
  - ベースの取得（`fetch_branch_or_raise` の `GitError`）・`current` と `cite` の `MvvError`（`mismatch` を含む）・ファイルの `OSError`・`UnicodeDecodeError` は `stopped`・`precondition` にする。`summary` に原因を置く
  - 原因は例外の文でなく、`mvv_gate.failure_text("cite_precondition", <例外>, {"gate": <区分>})` の定型文と診断の記録の識別子にする（ファイルのエラーも同じ関数を通す）
  - `GitError` の文には認証情報の付いたリモートの URL・社内の Git ホスト名・非公開のリポジトリへの参照が、`MvvError` の文には手元のパスが入りえ、`summary` は承認資料へ写されうるためである
  - 例外の全文は診断の記録で読む
  - `failure_text` は GitHub を呼ばないので、`precondition` の終わり方で GitHub の呼び出しは増えない
  - `warnings` にも生の文字列を載せない
- 承認資料の見出しの契約:
  - 根拠の行のバージョンの欠落（`kind: format`）は、`parse_citation` が決定の根拠の行（`decision_lines` の行）でだけ候補にするので、承認資料の根拠の行も `### 決定 <数>` の見出しの下に置く
  - `approval-request.md` の「目指す姿の根拠」の欄は、設計文書の決定ごとの根拠の行を、その決定の見出し（`### 決定 <数>: <題>`）をそのまま小見出しにして、その下へ載せる（見出しの無い箇条書きへまとめて載せない）
  - cross-review の `_citations` と同じ関数・同じ規則で確かめるので、`cite` は見出しの外の行を決定の根拠の行として扱わない
  - それだけでは、見出しを付け忘れたり崩したりした承認資料の根拠の行が確かめられず、「誤り 0 件」で人へ渡る
  - そこで `cite` は、承認資料の見出し「目指す姿の根拠」の節に限り、次の 2 つも誤り（`kind: heading`）にする
  - 1 つは、節があるのに `### 決定 <数>` の見出しが 1 つも無いこと
  - もう 1 つは、節の中で決定の見出しの下に無い行が根拠の行の形をしていることである
  - 根拠の行の形かは、`parse_citation` と同じ行頭の規則（`mvv_decide._CITATION`。前の空白と `- ` を除いて `根拠:` で始まる）で決め、`MVV` を含むかは問わない（`parse_citation` は決定の外では `MVV` の無い行を拾わないので、`- 根拠: Value 3` のようなバージョンの無い行が未検査のまま残るため）
  - 節の中に有効な決定の見出しが別にあっても、その見出しの下に無い行（節の最初の決定の見出しより前の行、決定でない小見出しの下の行）は同じく誤りにする
  - 節の外の行（文中の引用など）は今どおり確かめの対象にしない
  - 上の 2 つは節があるときの検査なので、節そのものが無い資料はこれだけでは素通りする
  - そこで `require_basis` が真のとき（`--gate design`）は、まとまりごとに「目指す姿の根拠」の節が無いことも `kind: heading` の 1 件にする
  - まとまりは、設計文書 1 本ぶんの項目を並べる範囲である。`approval-request.md` は、設計文書が 2 本以上の資料で、まとまりの見出しを深さ 1 の ATX 見出し `# <設計文書の H1>（#<課題番号>）` にする（題の末尾が全角か半角の括弧で囲んだ `#<数字>`）。`cite` は、フェンスの外にあるこの形の見出しの行から、次の同じ形の見出しの前（無ければファイルの終わり）までを 1 つのまとまりとする
  - ファイルにこの形の見出しが 1 つも無ければ、ファイルの全体を 1 つのまとまりとする（設計文書が 1 本の資料）。まとまりの見出しより前の行（PR 全体で 1 回だけ出す欄）は、どのまとまりにも入れない
  - 節の無いまとまりの誤りは、まとまりの見出しの行（ファイルの全体を 1 つのまとまりとしたときは 1 行目）を指す。節を持つまとまりがほかにあっても、節を丸ごと落としたまとまりは誤りになる
  - 節は、見出し「目指す姿の根拠」の行から、同じ深さか上位（`#` の数が同じか少ない）の次の見出しの前までである
  - 同じ深さの見出しだけで閉じると、設計文書が複数ある資料で、次のまとまりの見出し（上位の見出し）を越えて隣のまとまりの決定と根拠の行を前の節のものとして確かめてしまう
  - `approval-request.md` は設計文書ごとにまとまりを作り、まとまりごとに「目指す姿の根拠」を置くので、各まとまりの節を個別に確かめる。手順書の「目指す姿の根拠」の欄の形を変える行（上の「手順書」の表の `approval-request.md` の行）に、まとまりの見出しの形 `# <設計文書の H1>（#<課題番号>）` も足す
  - 節があるとは、ATX 見出し（`#` を 1 個か 2 個と空白に続けて、前後の空白を除いた題がちょうど「目指す姿の根拠」の行）がフェンスの外にあることである
  - 深さを 2 までにするのは、決定の見出しが 3 の深さ（`###`。`decision_lines` の規則）に決まっていて、3 以上の深さの節は中の決定の見出しで閉じてしまうためである
  - 3 以上の深さの「目指す姿の根拠」は崩れた見出しとして扱う
  - 節を丸ごと消した資料も、見出しを崩した資料（`#` の後の空白が無い・太字の行にした・題を変えた）も、節が無いとして同じ誤りになる
  - 見出しを崩した節の中の根拠の行は、節の外の行として確かめないが、節の欠落の 1 件で資料は `refused` になる
  - 同じく `require_basis` が真のときは、節の中の決定の見出し（`### 決定 <数>`）ごとに、その見出しの下（次の見出しまで）に根拠の行（`parse_citation` が候補を返す行。`checked` に数える行）が 1 行も無いことも、その見出しの行を指す `kind: heading` の 1 件にする
  - 節と決定の見出しが残り、根拠の行だけが抜けた資料を「誤り 0 件」で渡さないためである（設計文書の決定は必ず根拠の行を持つ。`approval-request.md` の欄の表）
  - `--gate release` の資料（欄を持たない）では節の有無も、決定の見出しの下の根拠の行の有無も問わない
- 手順書:
  - `approval-request.md` の「承認資料を組んだら」に `ndf records mvv cite --gate <区分> --base <ベースブランチ> <承認資料のパス>` を置き（`<区分>` は返す承認ゲートの区分。設計承認ゲートは `design`）、結果で次のとおり分ける
  - `ok` なら、結果の `summary` を承認資料の「MVV 判定」の欄の下に「根拠の行の確かめ: <summary>（<ベースのコミット>）」の 1 行として載せてから資料を渡す。承認済みの MVV が無く `metrics` に `commit` が無いときは、括弧を付けずに「根拠の行の確かめ: <summary>」とし、バージョンやコミットを補わない
  - `refused` なら、結果の `items` の `kind` で直す所を分けて打ち直す（`ok` になるまで資料を渡さない）
  - `kind: heading` は、誤りが指す行で 3 通りに分けて直す
  - 節「目指す姿の根拠」が無い・崩れていれば、節の見出し（深さ 2 まで）を直す
  - 決定の見出しの外にある根拠の行を指していれば、設計文書の決定の見出し（`### 決定 <数>: <題>`）をそのまま置き、その行を見出しの下へ移す
  - 決定の見出しの行を指していれば（その見出しの下に根拠の行が無い）、設計文書のその決定の根拠の行を、見出しの下へ書き足す
  - 最後の誤りは見出しを直しても消えないので、見出しには手を入れない
  - `kind: format`・`version`・`unknown` なら、資料の根拠の行を設計文書と今のバージョンから書き直す
  - `precondition`（ベースの取得・資料の読み取り・ベースの本文と承認の記録のハッシュの不一致）なら、資料を利用者に渡さず、`結果: 止まった`（理由は結果の `summary`）で呼び出し元へ返す（確かめていない根拠の行を検査済みとして渡さないため）
  - `agent-layers.md` の supervisor が守る規則 13（設計の工程を、設計テンプレートの「承認する人が読む欄」に従わせる規則。承認資料に写す欄を埋める）の後ろに、承認資料を返す前にこのコマンドを打ち、上の 3 つの分岐に従うことを足す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `cite` | バージョン 1 を引いた行 → `errors` 1 件。`require_basis=True` で、手順書の形の承認資料（節と決定の見出しの下に正しい根拠の行）→ `errors` 0 件。今のバージョンの実在する項目を引いた根拠の行 3 行だけ → `errors` 0 件・`checked` 3。根拠の行の形の誤り（`kind: format`）→ 1 件 | フェンスの中の根拠の行・文中の引用は拾わない。決定の見出しの下の行だけが決定の根拠の行として確かめられる。複数のファイル | MVV が `absent`・`unapproved` → `errors` 空・`checked` 0。`require_basis=True` で、「目指す姿の根拠」の節を丸ごと消した資料 → `kind: heading` の 1 件。節の見出しを崩した資料（`##目指す姿の根拠` と `**目指す姿の根拠**` のそれぞれ）→ `kind: heading` の 1 件。`require_basis=True` で、節と `### 決定 1: <題>`・`### 決定 2: <題>` の見出しがあり、決定 1 の下には正しい根拠の行、決定 2 の下には根拠の行が無い資料 → 決定 2 の見出しの行を指す `kind: heading` の 1 件。`require_basis=False` で節の無い資料、同じく根拠の行の無い決定の見出しがある資料 → 誤りにしない。設計文書 2 本の資料（`# <文書 A の H1>（#100）`・`## 目指す姿の根拠`・`### 決定 1: <題>` と正しい根拠の行、続けて `# <文書 B の H1>（#200）`・`## 目的`・`## 目指す姿の根拠`・`### 決定 1: <題>` で根拠の行が無い）→ 文書 B の決定 1 の見出しの行を指す `kind: heading` の 1 件だけ（文書 A の節は `# <文書 B の H1>` で閉じ、文書 B の決定を文書 A の節に数えない）。同じ資料で文書 B の `## 目的` の下に根拠の行の形の行を置く → 節の外なので拾わない。`require_basis=True` で、設計文書 2 本の資料の文書 A のまとまり（`# <文書 A の H1>（#100）`）は節と正しい根拠の行を持ち、文書 B のまとまり（`# <文書 B の H1>（#200）`）は「目指す姿の根拠」の節を丸ごと落とした資料 → 文書 B のまとまりの見出しの行を指す `kind: heading` の 1 件（ファイルに節が 1 つあっても誤りにする）。同じ形で、まとまりの見出しより前（PR 全体の欄）に `## 目指す姿の根拠` を置き、文書 B のまとまりには節が無い → 文書 B の見出しの行を指す 1 件（前の欄の節を文書 B の節に数えない）。`### 目指す姿の根拠` の資料 → 節が無いとして `kind: heading` の 1 件 |
| `cite`（手順書の形の承認資料） | `approval-request.md` の手順どおりに組んだ承認資料（「目指す姿の根拠」の下に `### 決定 1: <題>` と、その下にバージョンの表記を欠いた `- 根拠: Value 3`）→ `kind: format` の 1 件 | 同じ行を「目指す姿の根拠」の節の外（見出しの無い箇条書き）に置いた資料 → 拾わない（`_citations` と同じ規則） | 同じ行を節の中・決定の見出しの外に置いた資料 → `kind: heading` の 1 件（`MVV` を含まない行でも拾う）。節に有効な `### 決定 1: <題>` とその下の正しい根拠の行があり、同じ節の最初の決定の見出しより前に `- 根拠: Value 3` がある資料 → `kind: heading` の 1 件 |
| `_mvv_cite` | 誤り無し → 0・`ok`、`summary` にバージョンと確かめた行の数、`metrics` に `commit` | MVV が `absent` → `ok`・確かめていない旨の `summary` だけで、`metrics` に `mvv_version`・`commit`・`checked` が無い。MVV が `absent` で、存在しないパスを渡す → `precondition`（`ok` にしない） | 誤りあり → 1・`refused`。偽の秘密（環境変数に置いた値）を `cited` と `unknown` とパスに含む不正な根拠の行 → `items` と `summary` のどこにも偽の秘密が無く、包みの `clean` が 1 回だけ呼ばれる。対象が `PRIVATE` で、`cited` と `unknown` に対象のリポジトリ自身への参照（`<所有者>/<リポジトリ>#12` と `https://github.com/<所有者>/<リポジトリ>/pull/34`）を置く → `visibilities` のスタブが対象のリポジトリを 1 回の照会で受け、`PRIVATE` を返すと `items` と `summary` に所有者名・リポジトリ名・番号の `#12`・`34` が無く `PRIVATE_REF_TEXT` になる。同じ資料でスタブが `PUBLIC` を返すと参照が残る。`assume_public` を渡さない `guarded`（PR のコメントの経路）では、同じ本文の対象のリポジトリ自身への参照を照会せずに残す。対象が `PRIVATE` で、誤りの欄にリポジトリへの参照が無い → `visibilities` を呼ばず、`cited` に置いた偽のホームの絶対パス・偽のメールアドレス・偽のアカウント名が `items` と `summary` に無い（非公開のリポジトリから打っても公開の書き先と同じに伏せる）。他のリポジトリへの参照が 101 件 → 対象の公開範囲によらず `visibilities` が 2 回（参照の 2 回だけ）。包みの照会が失敗 → 各欄が `WITHHELD`・注意あり・`refused` のまま。対象が `PUBLIC` で、`cited` に公開のリポジトリの `ssh://git@github.com/<公開の所有者>/<リポジトリ>.git` と、非公開のリポジトリの同じ形の URL を置く → 2 つの参照とも `visibilities` に渡り、公開のほうは所有者名とリポジトリ名が `items` に残り、非公開のほうは `PRIVATE_REF_TEXT` になる。`ssh://<偽の秘密>@github.com/<公開の所有者>/<リポジトリ>`（`git` 以外の利用者名で、環境変数に置いた値）→ 照会に渡さず、URL の全体が秘密の伏せ字になる。対象が `PUBLIC` で、`cited` に公開のリポジトリの `https://git@github.com/<公開の所有者>/<リポジトリ>` を置く → 照会に渡さず、URL の全体が秘密の伏せ字になる（スキームが `ssh` でないので固定の SSH の利用者名の例外にしない）。対象が `PRIVATE` で、`cited` に別の非公開の偽の所有者とリポジトリを指す `https://x-access-token:<偽のトークン>@github.com/<偽の所有者>/<偽のリポジトリ>.git` と、`https://***@github.com/<偽の所有者>/<偽のリポジトリ>` を置く → `visibilities` を呼ばず、`items` と `summary` に偽のトークン・偽の所有者名・偽のリポジトリ名のどれも無い（URL の全体が秘密の伏せ字になる。`scrub` の単体でも、`public` 偽で同じ）。対象が `PUBLIC` で、根拠の行の `cited` とパスの `<所有者>/<リポジトリ>` の位置（所有者名とリポジトリ名の両方）に偽の秘密を置く → `items` と `summary` に偽の秘密が無く、`visibilities` のスタブが受けた引数のどれにも偽の秘密が無い（その参照は照会されず伏せ字になる）。偽の秘密を、他のリポジトリへの参照の境界をまたぐ位置（秘密の前半が参照の中、後半が参照の外）に置く → `visibilities` のスタブが受けた引数にも、`items`・`summary` にも、秘密のどの断片も無い（`scrub` の単体でも同じ）。「目指す姿の根拠」の節に決定の見出しが無く、バージョンの無い根拠の行がある → `refused`・`kind: heading`。`--gate design` で節の無い資料 → `refused`・`kind: heading`、`--gate release` で同じ資料 → `ok`。`--gate` が無い → `invalid`。節の外の文中の引用 → 誤りにしない。読めないファイル・ベースの取得の `GitError`・ベースが `mismatch` → `precondition`。ベースの取得の `GitError` の文に、偽のトークンを付けたリモートの URL（`https://x-access-token:<偽の秘密>@github.com/<別の所有者>/<リポジトリ>`）と偽の社内の Git ホスト名を含める → `summary` と `warnings` に偽の秘密・別のリポジトリの名前・偽のホスト名が無く、`cite_precondition` の定型文と診断の記録の識別子だけが載り、`visibilities` のスタブが呼ばれない。診断の記録のその識別子の行の `cause` に偽のホスト名が残る。包みの照会の失敗の文に同じ URL を含める → 注意の `<原因>` に偽の秘密・別のリポジトリの名前・偽のホスト名が無い |

## プランの外のエントリポイント（`--machine`）

満たす決定: 決定 5・6、受け入れ条件 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `stage_inputs_of` | `progress/stage_inputs.py` | 新設 | `stage_inputs_of(cwd: Path, machine_id: str, step_id: str) -> dict[str, Any]` | ステートマシンの最新の定義から `step_id` のステップを持つ工程を引き、`machine_context` に `invoke.resolve_inputs` を当てた入力を返す。プランが `NDF_STAGE_INPUT` に渡すものと同じ値だけで、文脈のほかのキー（`design_pr` など）は足さない |
| `_mvv_inputs` | `records/cli.py` | 新設 | `_mvv_inputs(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 区分ごとに入力を決める（下の表）。決められなければ `_InputError`（`invalid`・`refused`） |
| `_env_inputs` | `records/cli.py` | 新設 | `_env_inputs(gate: str, inputs: Mapping[str, Any]) -> dict[str, Any]` | `NDF_STAGE_INPUT` の object を、プランが組むのと同じ規則で確かめる。下の手順 |
| `_mvv_gate` | `records/cli.py` | 変更 | シグネチャは同じ。引数 `--machine <ID>` を足す | `_mvv_inputs` の後に `gate` を呼ぶ。出力は伏せずに返す（自由記述を伏せるのは、閲覧範囲を限れない DB へ保存するときの `store.append_events` だけ。「手元のファイルの閲覧範囲」） |

| 区分 | `NDF_STAGE_INPUT` | `--machine` | 入力 |
| --- | --- | --- | --- |
| `design`・`release` | ある | 無い | 環境変数の JSON を `_env_inputs` で確かめた値（形が違えば `invalid`・`input_missing`） |
| `design`・`release` | 無い | ある | `stage_inputs_of(root, ID, "design-mvv" か "release-mvv")` |
| `design`・`release` | ある | ある | `invalid`（「入力の出所が 2 つある」） |
| `design`・`release` | 無い | 無い | `invalid`（今と同じ文に `--machine` を添える） |

- `_env_inputs` の手順:
  - (1) StageDef は `mode` を使わずに作る。
    - 区分の判定のステップを持つ工程の文書の名前（`design` は `design`、`release` は `deploy`。`mvv_gate.py` の表 `GATE_STAGES = {"design": "design", "release": "deploy"}`）で `definition.stage_io(name)` を呼ぶ
    - `stage_io` は `contracts/workflow/stages/<name>.json` を読み、ステップを持たない StageDef を返す。StageDef は `name`・`inputs`・`optional_inputs`・`input_ref` だけを工程の文書の値で埋め、`steps=()` にする
    - 工程の入力の規則（`metadata.ndf.inputs` と入力の規約の参照）は工程の文書の値で、モードに依らない。モードはステップを絞るだけなので、入力を確かめる前に `mode` を読む必要が無い（`{"context": {...}}` のように最上位に `mode` が無い入力でも StageDef を作れる）
  - (2) `definition.stage_input_errors(stage, inputs)` で誤りを 2 種類に分けて受ける: `schema`（`stage-in` の規約での誤り。object でない・文脈に無いキーがある・値の形が違う。加えて、文脈にあるキーでも工程の `inputs`・`optional_inputs` に宣言されていないキーがある。`resolve_inputs` は宣言したキーだけを渡すので、環境変数の経路でも同じキーの組だけを受ける。除いて通さず拒む）と `missing`（工程の `inputs` のうち object に無いキー）。
  - (3) 優先順は `schema` が先: `schema` が 1 件でもあれば、`missing` によらず `_InputError`（`invalid`、`summary` に `schema` の誤り）。`{"context": {...}}` は、文脈のスキーマが `additionalProperties: false` なので `context` のキーで `schema` に当たる。同時に `mode` などが `missing` にも当たるが、`invalid` になる。`schema` が空で `missing` があれば `input_missing`（`summary` に足りないキー）。
  - (4) 誤りが無ければ `inputs` をそのまま返す（プランが渡した入力は `resolve_inputs` の同じ検査を通っているので、同じ値のまま通る）。入力の `mode` のモードがその区分の判定のステップを持たないときは、ここでは断らず、`gate` の手順 (1) が今どおり判定せずに返す（プランはそのモードで判定のステップを起動しないので、この入力が来るのは手で環境変数を組んだときだけ）
- `definition.stage_input_errors(stage: stages.StageDef, obj: Any) -> StageInputErrors`（新設。`NamedTuple(schema: list[str], missing: list[str])`）: `schema` は 2 つの誤りを合わせたものである。
  - 1 つは `validate_io(stage.input_ref, obj)` の誤り（規約が無ければ「工程 <name> に入力の規約が無い」の 1 件）
  - もう 1 つは、`obj` が object のとき、`stage.inputs` にも `stage.optional_inputs` にも無いキーごとの「工程 <name> の入力に宣言されていないキー <k>」である（文脈のスキーマにあるキーでも拒む。`_env_inputs` の手順 (2)）
  - `resolve_inputs` は宣言したキーだけを取り出すので、プランの経路の合否はこの追加で変わらない
  - `missing` は `obj` が object のときだけ `stage.inputs` のうち無いキーの「文脈のキー <k> が無い」（object でなければ空。`schema` が先に外れる）
  - `validate_stage_input` は `schema + missing` を返す（規則は 1 つで、分けて受けたい呼び手だけがこの関数を使う）
  - `definition.stage_io(name: str) -> stages.StageDef`（新設）: 上の (1)
- `stage_inputs_of` の手順:
  - (1) `store_root_of(cwd)` の保存先を開き、1 つの読み取りのトランザクションで、最初に `store.read_machine(conn, machine_id)` でマシンの行だけを読む。
    - 行が無ければ `model.Refused`（「ステートマシン <ID> は無い」）
    - 行の `repo` を起動元の `repo_id(cwd, store_root_of(cwd))` と比べ、違えば、定義もイベントも読まずにそこで `model.Refused`（「ステートマシン <ID> はこのリポジトリのものでない」）を返す
    - 拒否の文には行の `repo`（別のリポジトリのリモートの URL か、リモートの無いリポジトリのメインディレクトリの絶対パス）を載せず、起動元の `repo_id` も載せない（共有する DB を使う別のリポジトリの呼び手に、所属を漏らさない。拒否の文は、無い ID のときの文と別の文にしてよい。ただし所属は含めない）
  - (1') 一致したときだけ `read_current(conn, machine_id)` で定義とイベントを読む。`NDF_PROGRESS_DB` で共有する DB では、`read_current` が ID だけで読む。後続の `gate` は起動元の GitHub 接続で PR へ書く。そのため、定義と文脈を読む前に照合する（比べる値は `--carry` の `_check_carry` と同じ `repo`。ただし `_check_carry` と違い、拒否の文に相手の `repo` を出さない）。
  - (2) `snapshot.stages` から、ステップの id が `step_id` の工程を引く。無ければ `model.Refused`（「ステートマシンのモードは承認ゲート <区分> を持たない」）。
  - (3) `invoke.resolve_inputs(stage, machine_context(current))`。入力のキーが文脈に無ければ `StageInputMissing` をそのまま返す（プランと同じく判定しない）。文脈の `design_pr` は足さない: 設計の工程の入力（`design.json`）に無いので、足すとプランの経路と判定する PR が分かれる。どちらの経路も `pr_number` が `worktree` のブランチの開いた PR を選ぶ
- CLI の例外の扱い: `model.Refused` は `stopped`・`refused` にする。`StageInputMissing`・`StageInputInvalid` は `stopped`・`input_missing`・`invalid` にする（`exit-codes.md` の既存の種類）
- 判定のステップの id（`design-mvv`・`release-mvv`）は工程の定義（`contracts/workflow/stages/design.json`・`deploy.json`）の値である。区分 → ステップの id の表は `progress/model.py` の `MVV_GATE_STEPS = {"design": "design-mvv", "release": "release-mvv"}` の 1 か所に置く。`mvv_gate.py` の `GATE_STEPS` はそれを読む（`progress` は `records` を読まないので、`asking.material` も同じ表を `model` から読む）。
  - `mvv_gate.py` には 区分 → そのステップを持つ工程の文書の名前の表 `GATE_STAGES = {"design": "design", "release": "deploy"}` を置く
  - 定義の id を変えたら表も変える（テストで、`GATE_STAGES` の工程の文書の中に `GATE_STEPS` の id のステップがあることを縛る）
- 手順書: `agent-layers.md` の supervisor が守る規則に次の 2 つを足す。2 つの場所の使い分けは、どちらの区分でも同じである。
  - コマンドは、起動指示の「作業場所」に示された対象リポジトリの worktree で打つ（`pr_number` がそのブランチの PR を選ぶ）
  - 結果 JSON は、起動指示の「作業ディレクトリ」の絶対パスの下に保存する。作業ディレクトリは担当ごとに与えられる手元の保存先で、作業場所の worktree の外にある（上の「用語」）。結果 JSON は PR から取った自由記述（判定の理由・保留の文）を含むので、コミットの対象になる worktree の中には置かない。作業ディレクトリが作業場所の worktree の下にある（`git -C <作業場所> rev-parse --show-toplevel` の下）なら、保存せずに `結果: 判断が要る` で返す。worktree の外であることだけでは、読める人を限らない。そのため結果 JSON と工程の出力の JSON は、`mktemp` で作った 0600 の新しいファイルに書いてから所定のパスへ `mv` で置き換える（打ち直しでも既存のファイルの権限を引き継がない）（下の「承認資料の開示の範囲」の「手元のファイルの閲覧範囲」）
  - 設計承認ゲート（`結果: 承認ゲート`）を返す前に、作業場所で `ndf records mvv gate --gate design --machine <ステートマシンの ID>` を打つ。結果 JSON は、そのまま作業ディレクトリの `mvv-gate-design.json` に保存する（承認資料の全文の参照先。下の「承認資料の開示の範囲」）。
    - 結果の `outputs.mvv_gate` を、設計の工程の出力の JSON ファイル（規則 15 で作業ディレクトリに書くファイル。規則 15 は、工程を終えるたびに工程の出力の JSON を作業ディレクトリへ書き、フェーズレポートにそのパスを足す規則）の `mvv_gate` へそのまま載せる
    - 承認資料の「MVV 判定」の欄にも同じ値を載せる（下の「承認資料の出所」）
    - このファイルは手元の記録で、呼び出し元へは返さない
    - フェーズレポートの `終えた工程` にはファイルのパスだけを書き、`outputs.mvv_gate` の中身（判定の理由・レッドライン・`why`・`holds`）をレポートの本文（`理由`・`summary` など）へ写さない
    - conductor はパスを `ndf progress finish --output` へ渡すだけで、中身を読んで人へ転送しない
    - 承認資料の欄と、レポートの本文に判定を書くときは、下の「承認資料の開示の範囲」の supervisor の経路の行のとおり自由記述を伏せる
  - リリース承認ゲート（プランを使わず、本番に反映される操作の前で `結果: 承認ゲート` を返す手動の経路。フェーズの表の「仕上げ」など）を返す前に、作業場所で `ndf records mvv gate --gate release --machine <ステートマシンの ID>` を打つ。結果 JSON は作業ディレクトリの `mvv-gate-release.json` に保存する。判定する PR は文脈の `pr`（`ndf progress show` の文脈の `pr` と同じ値）である。
    - 判定した PR と、このリリースで承認する人がマージを承認する PR（承認資料の「対象を開くためのもの」の PR）が同じかを、どちらも同じステートマシンの文脈（`ndf progress show`）から解いて確かめる。ブランチ戦略が `trunk` のときは、版上げの PR（文脈の `release_pr`）と、スプリントの実装を取り込む PR（文脈の `pr`）が別になりうる
    - 2 つが違えば、判定の結果を承認資料の「MVV 判定」の欄に載せない。代わりに欄に「MVV 判定: このリリースでマージする PR（<マージする PR の URL>）の判定は無い。判定したのは別の PR（<文脈の `pr` の URL>）である」と書き、承認する人に対象の違いを示して人へ問う（違う PR の判定を、今回のリリースの判定として渡さない）
    - 結果の `outputs.mvv_gate` は承認資料の「MVV 判定」の欄にだけ載せ、工程の出力へは載せない（#4105 までリリース承認ゲートは省かないので、ゲートが読む出力が要らない）
    - 結果が `ok` でないときは、欄に次の文を載せて承認ゲートを返す（止まらずに人へ問う）。`ok` でないのは、ステートマシンが無い・モードが `release-mvv` を持たない `refused`、文脈に `pr` が無い `input_missing`、入力の形が違う `invalid` である。載せる文は「判定できない: 判定の入力を組めない（<結果の `status`>・<`cause.kind`>）。結果の全文は、判定を打った実行環境の <`mvv-gate-release.json` のパス> で読む」である。`summary`・`cause.detail`・`warnings` は承認資料にもフェーズレポートにも写さない。スキーマの誤りの文には、無効だった入力の値（作業ツリーのパスや非公開の識別子）が入りうるためである。`status` と `cause.kind` は決まった値の列挙なので、そのまま載せる。設計承認ゲートで `ok` でないときも同じ書き方にする
    - プランの経路（`release-mvv` を持つ本番のリリースプラン）では打たない（判定のステップが打つ）
- 手順書: `pace.md` の「MVV 判定」の表から順 2（進め方）を外し、「進め方は省くかだけを決める」と `--machine` の行を足す。`project-mvv.md` の「工程での読み方」（#4076 が足す節）の承認ゲートの行から `pace.md` を指す

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `--machine` の読み出しとプランの書き込み | ステートマシンの DB | 読み取りのトランザクション（`store.transaction`）だけ。書かない | 何度打っても同じ入力。判定の記録は同じ材料の「従う」を使い直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `stage_inputs_of` | 設計の工程の入力（`issues`・`worktree`・`mode`・`pace`・`mvv_ref`）が、同じ文脈で `resolve_inputs` が組むものと同じ | 記録された出力（`design_pr`）が重なった文脈でも、返す入力に `design_pr` が無い | 無い ID → `Refused`。同じ DB に作った別リポジトリ（`repo` の違う行）の ID → `Refused` で、`store.read_definition`・`store.read_events`・`resolve_inputs` を呼ばず文脈を返さない（`_mvv_gate` は `gate` を呼ばず、PR へ書かない）。拒否の結果 JSON（`summary` と `warnings`）に、その行の `repo` の値（リモートの URL・絶対パス）が現れない。判定のステップを持たないモード → `Refused`。`mvv_ref` が文脈に無い → `StageInputMissing` |
| `_mvv_inputs` | 上の表の 4 行（環境変数だけ・`--machine` だけ・両方・どちらも無し）を、`design`・`release` のそれぞれで確かめる（4 行 × 2 区分） | — | 両方ある → `invalid` |
| `_env_inputs` | プランが組む入力（`resolve_inputs` の結果）→ 同じ値を返す | 文脈のキーでない余分なキーを持つ入力 → `invalid`。文脈にはあるが工程で宣言していないキー（`design_pr`）を足した入力 → `invalid`（除いて通さない。`gate` を呼ばない） | `{"context": {...}}` の形（最上位に `mode` が無く、`schema` と `missing` の両方に当たる）→ 例外を出さず `invalid`（判定を打たず、`gate` を呼ばない）。`mode` だけが無く形は合う → `input_missing`。`pace` の値が文脈のスキーマに無い → `invalid`。object でない（配列）→ `invalid` |
| `stage_input_errors`・`stage_io` | `stage_io("design")` の `inputs`・`input_ref` が `load_definition(<design-mvv を持つモード>)` の設計の工程と同じ | `{"context": {...}}` → `schema` と `missing` の両方が空でない。`validate_stage_input` が 2 つを合わせたものと同じ。文脈のスキーマにはあるが設計の工程の `inputs`・`optional_inputs` に無いキー（`design_pr`）を足した入力 → `schema` に「宣言されていないキー design_pr」の 1 件 | object でない → `schema` だけ |
| `_mvv_gate`（両方のエントリポイント） | 同じステートマシンの文脈で、`NDF_STAGE_INPUT`（プランが組む入力）と `--machine` の 2 回の起動が同じ入力で `gate` を呼び、同じ PR の番号を読み、同じ判定のスタブの結果から同じ `mvv_gate` を返す | 文脈の `design_pr` が `worktree` のブランチの PR と違う → 両方とも `worktree` のブランチの PR を判定する | — |
| `GATE_STEPS`（`model.MVV_GATE_STEPS`） | 定義の `design.json`・`deploy.json` に同じ id のステップがある | — | — |

## 手順書

満たす決定: 決定 1〜5、受け入れ条件 1・3・4・7・8

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/pace.md` | 具体例の最後の段落（`normal` は判定しない）を「判定し、省かない」に変える。「MVV 判定」の表から順 2 を外し、機械の検査に 7（レビューの保留）を足し、検査 1 だけが判定を止めること・ほかは省くかだけを決めることを書く。`--machine` の行と、リリース承認ゲートは #4105 まで省かないことを足す |
| `skills/development-workflow/references/approval-request.md` | 「MVV 判定」の行の出所を、進め方によらず今回の判定の出力 `outputs.mvv_gate` にし（下の「承認資料の出所」）、保留（`holds`）を足す。`pace: normal` は助言、の注記を消す。設計文書が 2 本以上の資料のまとまりの見出しを `# <設計文書の H1>（#<課題番号>）` の形にし（`cite` がまとまりを見分ける。上の「承認資料の見出しの契約」）、「目指す姿の根拠」の欄は、深さ 2 までの見出し（まとまりの見出しが `#` なら `## 目指す姿の根拠`）にし、決定の見出し（`### 決定 <数>: <題>`）の下へ根拠の行を載せる形にする（上の「根拠の行の確かめ」の「承認資料の見出しの契約」）。承認資料を渡す前に `ndf records mvv cite` を打ち、`ok`・`refused`・`precondition` で分ける手順を足す。supervisor が呼び出し元へ返す資料では、「MVV 判定」の欄の判定の理由・レッドライン・保留の文と `why` の本文を載せず、件数だけを載せる（下の「承認資料の開示の範囲」の表）。全文の参照先が今回の判定のコメントのときは、コメントを開けない・判断に必要な理由が伏せ字で読めない場合は承認せずに返すよう、承認する人へ求める一文を欄に足す（下の「受け入れ条件 3・4 との関係」の表） |
| `skills/development-workflow/references/agent-layers.md` | supervisor が守る規則に、設計承認ゲートとリリース承認ゲート（手動の経路）を返す前の `--machine` の判定と、結果を載せる先と、`cite` を足す（上の「プランの外のエントリポイント」の手順書）。規則 15 に、工程の出力の `mvv_gate` は出力の JSON ファイルにだけ置き、フェーズレポートの本文へ写さないことと、そのファイルと `--machine` の結果 JSON を `mktemp` で作った新しいファイルに書いてから置き換えることを足す（下の「承認資料の開示の範囲」の表と「手元のファイルの閲覧範囲」） |
| `skills/development-workflow/references/project-mvv.md` | 「工程での読み方」の承認ゲートの行（判定はどの進め方でも打つ） |
| `docs/contracts/commands.md` | `ndf records mvv gate` の `--machine` と、`ndf records mvv cite` の行 |
| `docs/glossary/glossary.json` | 語「レビューの保留」を追加、「MVV 判定」の意味を変え、`ndf records glossary render` で `docs/glossary.md` を作り直す |

承認資料の出所（プランの経路は `progress/gate/asking.py` の `material` が、supervisor の経路は `approval-request.md` の手順が、同じ規則で組む）:

- 出所は今回の判定の出力 `outputs.mvv_gate` だけにする。
  - プランの経路は、待っている工程の判定のステップ（`design-mvv`・`release-mvv`）の済んだ記録（`step_recorded` の report の `outputs.mvv_gate`。`ndf progress show` のその行の `outputs.mvv_gate` と同じ値。文脈 `v.context()` の `mvv_gate` は工程が終わるまで書かれず、前の工程の値が残るので読まない）、supervisor の経路は自分が打った `--machine` の結果の `outputs.mvv_gate` を読む
  - PR のコメントは出所にしない（差分を読む間に head が動いた・読み直しに失敗した・判定を打てなかった・コメントの投稿に失敗した終わり方では今回のコメントが無く、PR には前回の判定のコメントが残るため）
- 載せ方:
  - 判定は、承認を求める PR と head に対するものであるときだけ、今回の判断材料として載せる。判定の `pr` と `head` を、質問の PR（`q.pr`）と、資料を作る時点の PR の head（`q.target.head`）に照らす
    - 照らさないと、判定の後に PR へ push されたとき、新しい head を示す質問の資料に、前の head の判定の理由と保留を載せてしまう。プランの経路では判定のステップ（`design-mvv`・`release-mvv`）が済んだ後に、supervisor の経路では判定を打ってから承認資料を返すまでに（コメントの投稿や `cite` の間に）push されうる
    - 一致しないときは、判定の理由・レッドライン・保留・`why` と、今回の判定のコメントへの案内を載せず、「レビューの保留: 未確認」を添える。承認する人が次に何をすればよいかが違うので、一致しない理由ごとに次の表の文を載せる

| 一致しない理由 | 見分け方 | 欄に載せる文 |
| --- | --- | --- |
| PR を特定する前に判定が止まった | `out` に `pr` も `head` も無く、`stopped_by` がある | 「対象の PR を特定できなかったため、MVV 判定は済んでいない。<`stopped_by.text`>」。比べる相手が無いので、PR と head を並べない |
| 判定が対象を確かめられない形で残っている | `out` に `head` はあるが `pr` が無い（このバージョンより前の判定）、または資料を作る時点の head を読めていない（`q.target.head` が無い） | 「残っている判定が、承認を求める PR #<質問の PR> の今の変更に対するものかを確かめられません。この変更に対する判定はまだありません。承認せず、「変更を求める」の回答で判定のやり直しを依頼してください」 |
| 別の PR か別の head の判定がある | `out` の `pr` と `head` があり、どちらかが質問の値と違う | 「承認を求める PR #<質問の PR>・head <質問の head> の判定はまだ無い。今ある判定は PR #<判定の pr>・head <判定の head> に対するもの（判定の後に PR が更新されたか、別の PR を判定した）。この変更に対する判定はまだありません。承認せず、「変更を求める」の回答で判定のやり直しを依頼してください」 |

  - 表の 2 行目と 3 行目で承認する人が「変更を求める」と答えたとき、判定をやり直すのは担当者の側で、承認する人は操作しない。経路ごとの手順は次のとおりである
    - プランの経路: 変更を求める回答で、待っている工程が初めから流れ直す（`run` の `_answered` が世代を上げ、済んだステップを空にする）。判定のステップ（`design-mvv`・`release-mvv`）もその中で今の head に対して打たれるので、担当者が別のコマンドを打つ必要は無い
    - supervisor の経路: supervisor が同じ作業場所で `ndf records mvv gate --gate <区分> --machine <ステートマシンの ID>` を打ち直し、結果で承認資料を組み直してから、もう一度承認を求める
  - 一致すれば、節の先頭に「判定した PR: #<pr>・head <head>」を載せる
  - プランの経路の資料は、資料を作る時点の head ごとに別のファイルにする（下の「プランの経路の承認資料の生成」の `material_path`）。質問を表示し直す時点で head が進んでいれば、その head 用の資料を新しく作り、古い head の資料を渡さない
  - supervisor の経路でも、資料に「判定した PR: #<pr>・head <head>」を載せ、同じ表で照らす。承認ゲートを返す直前に、承認を求める PR の今の head を 1 回読み（`gh pr view <番号> --json headRefOid`）、その値を呼び出し元へ返す承認資料にも載せる。承認資料の中で head を使う所（設計文書へのリンク・判定との照合・承認資料に示す head）は、すべてこの最後に読んだ値にそろえる。設計文書へのリンクを先に組んでいた場合は、組んだときの head がこの値と同じときだけそのリンクを使い、違えばこの値でリンクを組み直す（資料のリンクから古い設計文書を読んで新しい変更を承認させないため）。読んだ head が判定の `head` と違えば、上の表の 3 行目の文にし、判定の件数・`stopped_by.text`・コメントへの案内を載せない。`--machine` を打ち直して一致すれば、その結果で資料を組み直してよい。返した後の push は、承認する人が承認資料の head と PR の今の head を見比べて気づけるよう、承認資料に「この head と PR の今の head が違えば承認せずに返す」を添える
  - `verdict` があれば判定・理由・根拠の項目・レッドライン、null なら「判定できない: <`why`>」。どちらでも `why` の省かない理由を 1 件残らず載せる。この載せ方はプランの経路の資料のものである。supervisor の経路の資料は、下の「承認資料の開示の範囲」の表のとおり件数だけを載せる。
  - 保留は `holds` の値で 3 通りに分ける: 空でない配列なら 1 件残らず載せる、空の配列 `[]` なら「レビューの保留: 無し（レビューの記録に保留が無い。記録がまだ作られていない場合を含む）」、`null` かキーが無いなら「レビューの保留: 未確認（<理由>）」。
  - `[]` は、記録を読めて保留が 0 件のときと、記録がまだ作られていない（cross-review が MVV の指摘を 1 件も記録していない）ときの両方で出る。`validation_rows` は 2 つを同じ空の結果にし、出力も区別しない。そこで表示は両方に当てはまる文にし、記録を読んで確かめたとは書かない。既存の記録を読めないときは `[]` にならず、読めない理由の 1 件が載る
  - 理由は次の順で決め、`why` が空でも資料を組める（空の `why` の先頭を参照しない）:
    - 区分が `release` なら「リリースの保留は #4105 まで読まない（省かない）」。
    - `verdict` が null なら「保留を読む前に判定が止まった: <`why` の先頭>」（`why` が空なら「保留を読む前に判定が止まった」だけ）。
    - `verdict` があれば「この判定は保留を読んでいない」（途中で止まったのではない。実装計画 `issues/issue-3959-plan.md` のレビューの単位 2（判定の条件の分離）が入り、単位 3（レビューの保留を読む）がまだ入っていない間は、`_judged` が保留を読まずに正常な判定を返す。そのとき `fast` で検査がすべて通れば `why` は空）。
  - 単位 3 が入った後の `design` では、`holds` が null になるのは (5) より前で `verdict: null` にした終わり方だけで、どれも `why` に理由を足す。未確認を「無し」と書かない。
  - `noted` が真なら「今回の判定のコメント: <note_url>」を添え、今回書いたコメントだけを指す。`noted` が真でない（`false`・キーが無い）なら「今回は PR に判定のコメントを載せていない。PR に既存の判定のコメントがあっても、今回の結果はここで確かめる」を添える。前回の判定が PR にあるとは書かない（承認済みの MVV が無いリポジトリでは、初回から一度もコメントを書かない）。書かなかった理由（検査 1 が外れた・投稿に失敗した）は `why` の載せ方で伝わる。
  - `outputs.mvv_gate` が無い（判定のステップが済んでいない・`--machine` が `ok` でない）ときは「判定できない: <理由>」と「レビューの保留: 未確認」を載せ、PR のコメントから補わない
- 承認資料の開示の範囲:
  - 「MVV 判定」の欄の自由記述は、判定した PR の中身を既に読める場所にだけそのまま置き、PR を読めるかを確かめられない書き先へは伏せずに出さない
  - 承認ゲートで答える人が GitHub でその PR を見られるかは、どの経路でも確かめない
  - 代わりに、資料を置く場所で開示の条件を決める
  - 欄に載る自由記述は 2 種類ある
  - 判定の理由とレッドラインは、この PR の差分と MVV を LLM が読んで書いたものである
  - 保留は、この PR の cross-review の記録から作られる。見送りの理由とレッドラインの指摘本文は、記録に書く時点で `record_review` の `clean` が公開範囲を照会して伏せてある（照会できなければ伏せた行で、`WITHHELD` の文になる）
  - 書き込む前に閲覧できる人を限れる経路では自由記述を載せ、結果を別の場所へ転送する経路（supervisor が呼び出し元へ返す本文）では自由記述を伏せる。閲覧できる人を限れる経路は、所有者だけが読めるファイル（下の「手元のファイルの閲覧範囲」）と、書き先の公開範囲を照会する PR のコメントである
  - 手元のファイルの閲覧範囲: 判定の自由記述を保存する手元の場所は、中身を書く前に、判定を打った OS の利用者だけが読めるようにする。ファイルが Git ディレクトリの中にあることや、worktree の外にあることは、閲覧できる人を限らない（同じ端末のほかの OS の利用者も、既定の umask の 022 では読める）。判定を打った OS の利用者は PR を読んだ GitHub の認証を持つので、その利用者だけが読めるなら、GitHub で閲覧を許されていない人へ新しく開示しない。保存先ごとの規則は次のとおりである
    - ステートマシンの保存先: 判定の出力 `outputs.mvv_gate` は、ステップの記録（`step_recorded`）と `ndf progress finish` の工程の出力（`outputs_recorded`）として DB に保存される。承認ゲートを省いたときは、`service:mvv` の回答（`answered`）にも写して保存される。`store` は保存先を開くたびに、DB へ接続する前に閲覧範囲を限り、限れたかどうかを `Store.private`（真偽値）に持つ。保存先の種類ごとの規則は次の表のとおりで、どの規則も失敗したら `private` を偽にする（開くこと自体は止めない）

| 保存先 | 接続の前にすること | `private` が真になる条件 |
| --- | --- | --- |
| 既定の SQLite（`Store.root`。既定は `<Git ディレクトリ>/ndf/state/`） | 保存先のディレクトリを 0700 にする（無ければ `mkdir(mode=0o700)`、あれば `chmod(0o700)`）。ディレクトリの権限で守るので、中のファイルの権限によらず、ほかの OS の利用者は読めない。対象は、SQLite の DB（`progress.sqlite`）とその副ファイル（`-wal`・`-shm`・`.lock`）、バックアップ（`BACKUP_DIR` の下）、プランの承認資料（`progress-work/` の下）である。戻しやバックアップで新しいファイルができても外れない | `chmod`・`mkdir` が成功した |
| `NDF_PROGRESS_DB` で保存先の外に置いた SQLite のファイル | DB のファイルを 0600 にする（無ければ `os.open(path, os.O_CREAT \| os.O_WRONLY, 0o600)` で空のファイルを作り、あれば `chmod(0o600)`）。続けて、既にある副ファイル（`<DB>-wal`・`<DB>-shm`・`<DB>-journal`）もすべて `chmod(0o600)` する。ほかのプロセスの接続が残した副ファイルは、DB のファイルの権限を変えても元の権限のまま残るためである。SQLite がこれから作る副ファイルは、DB のファイルと同じ権限になる。バックアップは保存先の下に置くので、上の行の規則で守られる | DB のファイルと、既にある副ファイルのすべての `chmod` が成功した（所有者でないなどで 1 つでも失敗すれば偽） |
| `NDF_PROGRESS_DB` の PostgreSQL | 何もしない。読める人は DB の認証情報を持つ人で、NDF は限れない | 常に偽 |

  - `private` が偽の保存先には、判定の自由記述を保存しない。伏せるのは DB へ渡す値だけで、`ndf records mvv gate` の出力（結果 JSON）は伏せない（supervisor が作業ディレクトリに置く結果 JSON には全文が残る）
    - 判定の出力が DB へ入る経路は 3 つある。ステップの記録（`step_recorded`）、工程の出力（`outputs_recorded`）、承認ゲートを省いた回答（`answered`）である。`answered` は、`progress/run.py` の `_consume` が `model.mvv_answer` で組む `service:mvv` の回答で、判定の出力の全体を payload の `mvv_gate` に写す。この 3 つのどれかを伏せずに書くと、そこから判定の自由記述が読める
    - `Store` は `private` を、接続ごとの `conn.info["ndf_private"]`（SQLAlchemy の `Connection.info`）に置く。`store.append_events` は、`step_recorded`・`outputs_recorded`・`answered` の事象を書く前にこの値を読む。偽か値が無ければ、payload の `mvv_gate` を `model.mvv_stored_view`（新設）に通してから書く。`step_recorded` では report の `outputs.mvv_gate`、`outputs_recorded` では出力の `mvv_gate`、`answered` では payload の直下の `mvv_gate` を通す
    - `answered` の payload のうち、`mvv_gate` の外の値（`by`・`answer`・`gate`・`gate_id`・`seen`）は伏せない。回答を承認として数えるか（`mvv_answer_void` の `hold_checked`）と、回答した者が見た対象との照合（`recheck` の `seen`）に使うためである。`mvv_gate` の中でも、`holds` が空の配列かどうかと `head` は残る（次の箇条）ので、`mvv_answer_void` の結果は伏せる前と変わらない
    - `mvv_stored_view` は、`verdict.reasons`・レッドラインの本文・`holds` の各要素・`why` の各要素を、件数を保ったまま固定の文「伏せた（全文は今回の判定のコメントで読む）」に置き換える。`skip`・`verdict.verdict`・根拠の項目の名前・`holds` が空の配列かどうか・`stopped_by`・`noted`・`note_url`・`pr`・`head` は残すので、`mvv_skip` と `hold_checked` の結果は変わらない
    - DB から読んで組むプランの承認資料は、理由と保留の本文の代わりに、件数と今回の判定のコメントへの案内を載せる（supervisor の経路の載せ方と同じ）。承認する人が全文を読める場所は、今回の判定のコメントだけになる。そこで資料に次の文を載せ、読めないときは承認させずに判定のやり直しへ回す
      - `noted` が真: 「理由と保留の全文は、今回の判定のコメント <note_url> で読めます。コメントを開けない場合や、判断に必要な理由が伏せ字で読めない場合は、承認せず、『変更を求める』の回答で判定のやり直しを依頼してください」
      - `noted` が真でない（コメントの投稿に失敗した・コメントを書かない終わり方）: 「理由と保留の全文を読める場所がありません。承認せず、『変更を求める』の回答で判定のやり直しを依頼してください」。この終わり方では `_recorded` が `skip` を偽にしているので、`fast`・`auto` でも承認ゲートは省かれず、承認する人に届く
      - 変更を求める回答の後は、上の「載せ方」の担当者の手順と同じく、工程が初めから流れ直して判定のステップが打ち直される
  - プランの承認資料（`write_material`）: 上の保存先の規則に加え、ファイル自体も 0600 で作る。一時ファイルは `tempfile.mkstemp(dir=<資料のディレクトリ>, suffix=".tmp")` で、呼び出しごとに別名・排他的・0600 で作る。中身を書いて閉じた後、`os.link(tmp, path)` で所定のパスに置き、一時ファイルを消す。`os.link` は置き先が既にあれば `FileExistsError` になる。そのときは、同時に走った別の呼び出しが先に完成させた資料をそのまま使い、自分の一時ファイルを消す。`replace` で上書きしない（先に完成した資料を、別の呼び出しが質問と一緒に返した後で書き換えないため）。資料のディレクトリを新しく作るときは `mkdir(mode=0o700)` にする
  - supervisor が作業ディレクトリに書く結果 JSON（`mvv-gate-<区分>.json`）と、規則 15 の工程の出力の JSON ファイル: 打ち直しで同じパスへ書くときも、既存のファイルの権限を引き継がないように、新しいファイルへ書いてから置き換える。`tmp=$(mktemp "<作業ディレクトリ>/.mvv-gate-design.XXXXXX") && ndf records mvv gate … > "$tmp" && mv -f "$tmp" "<作業ディレクトリ>/mvv-gate-design.json"` の形にする。`mktemp` は umask によらず 0600 の新しいファイルを排他的に作る。`mv` は置き先を新しいファイルに入れ替えるので、既存のファイルの権限が広くても、新しい中身はそのファイルに書かれない（`>` で既存のファイルを開き直すと、umask を 077 にしても権限は変わらない）。`agent-layers.md` の supervisor の規則に、このコマンドの形を書く
  - 同じ OS の利用者を複数の人で共有する構成は扱わない。その構成では、どの人も PR を読んだ GitHub の認証そのものを使えるので、ファイルの権限では防げない
  - 経路ごとの条件は次の表のとおりである

| 経路 | 資料を置く場所・渡す先 | その場所で既に読めるもの | 欄の自由記述の扱い |
| --- | --- | --- | --- |
| プランの経路（`write_material`・`presentation_path`） | 判定を打った実行環境の、リポジトリの Git ディレクトリの中（`material_path` の `<Git ディレクトリ>/ndf/state/progress-work/<ステートマシンの識別子の sha256 の頭>/` の下）。保存先のディレクトリは 0700、ファイルは 0600 で、所有者だけが読める（上の「手元のファイルの閲覧範囲」） | 読めるのは判定を打った OS の利用者だけで、その利用者は PR を読んだ GitHub の認証を持つ | 保存先を所有者だけが読める（`Store.private` が真）ならそのまま載せる。読める人は同じ認証で PR の本文と差分を読めるので、新しく開示する情報は無い。保存先の閲覧範囲を限れない（PostgreSQL など、`private` が偽）なら、DB には伏せて保存し、資料には件数と今回の判定のコメントへの案内だけを載せる（上の `mvv_stored_view` の箇条） |
| supervisor の経路（`approval-request.md`） | 呼び出し元へ返す資料の本文。呼び出し元はそれを承認する人へ転送する（チャットへの貼り付けなど） | 確かめられない。supervisor が判定した PR の作業場所で動いても、同じ実行環境にあることは、呼び出し元やその先で資料を読む人が Git ディレクトリと同じ権限を持つ証拠にならない | PR から取った値を含みうる文は、本文を載せずに件数だけを載せる。対象は、判定の理由・レッドライン・保留の文と `why` である。`why` は保留の文を含まない（上の `check_review_holds`）が、差分に載らないファイルの名前・ブランチ名・PR の番号など、非公開の PR から取った値を含みうる。そこで `why` も 1 件ずつの本文を載せず、「省かない理由 <件数> 件（全文は下の参照先で読む）」の 1 行にする。`verdict` が null のときは「判定できない。理由: <`stopped_by.text`>。今回の詳しい結果は <下の参照先> で確かめる」とする。`stopped_by.text` は PR の値を含まない定型文なので、そのまま載せる（上の `stopped_by` の表）。承認する人は、この文で MVV が承認されていないのか、材料の取得に失敗して打ち直しが要るのかを判断できる。載せるのは、判定（従う・従わない・判定できない）・`stopped_by.text`・`skip`・根拠の項目の名前・保留の件数・`why` の件数・`noted` だけである。全文の参照先を 1 つ添える。`noted` が真なら「理由と保留の全文は、今回の判定のコメント <note_url> で読む」とする（PR のコメントは `Publisher.guarded` を通っているので、PR を見られる人だけが全文を読める）。`noted` が真でない（`false`・キーが無い）なら、PR の既存のコメントを指さない。前回の判定のコメントを今回の理由として読ませないためである。代わりに、supervisor が同じ実行で `ndf records mvv gate` の結果 JSON を保存したファイル（作業ディレクトリの `mvv-gate-<区分>.json`）のパスを添え、「理由と保留の全文は、判定を打った実行環境の <パス> で読む」とする。この終わり方では承認する人が全文を読めないことがあるので、表の下の「受け入れ条件 3・4 との関係」の扱いに従う |
| supervisor の工程の出力（規則 15 の出力の JSON ファイル、`--machine` の結果 JSON、`ndf progress finish` が書くステートマシンの記録） | 作業ディレクトリのファイルと、ステートマシンの保存先（どちらも判定を打った実行環境の手元）。作業ディレクトリのファイルは `mktemp` で作った 0600 の新しいファイルに書いてから置き換え、ステートマシンの保存先はディレクトリを 0700 にする（PostgreSQL では自由記述を保存の前に伏せる）。どちらも所有者だけが読める | プランの経路と同じ。手元の記録として持ち、呼び出し元へ返す本文に含めない | `outputs.mvv_gate` をそのまま持つ（承認ゲートを省くかを決める `mvv_skip` と、プランの経路の資料がこの値を読むため）。フェーズレポートにはパスだけを書き、中身を写さない。中身をレポートや資料に書くときは、上の supervisor の経路の行の伏せ方に従う |
| PR のコメント（`_recorded`） | GitHub の PR | PR を見られる人 | 失敗の原因は定型文と診断の記録の識別子だけで、例外の文を載せない（上の「結果に入れる原因の文」）。判定の理由と保留の文は `Publisher.guarded` が書き先の公開範囲を照会して伏せる。照会できなければ公開の書き先と同じに伏せる（上の `_recorded`） |
| 診断の記録（`failure_text`・`record_failure`） | 判定を打った実行環境の状態の保存先（`mvv-gate-failures.jsonl`。リポジトリの外で、追跡しない）。ファイルは 0600 で、所有者だけが読める（上の `record_failure`。限れなければ書かない） | 判定を打った OS の利用者だけ | 例外の全文を持つ（秘密の値だけを伏せる）。PR のコメント・承認資料・結果 JSON へ写さず、識別子だけをそれらに載せる。全文を読むのは、同じ実行環境で原因を直す人である |
| 上のほかの書き先（実行環境の外のファイル・チャット・メールなど） | 今は無い | 確かめられない | 載せない。経路を足す変更の中で、自由記述を `Publisher.guarded` と同じ包みに通すか、`WITHHELD` の文に置き換える処理を入れ、受け手が PR を見られるかを確かめられないときは伏せる |

受け入れ条件 3・4 との関係: 受け入れ条件 3 は「承認資料の『MVV 判定』の欄に、`normal` でも判定と理由とレッドラインが載る」、受け入れ条件 4 は「その保留（PR・項目・理由）が承認資料の『MVV 判定』の欄に載る」と求める。経路ごとに次の形で満たす。満たせない終わり方では、承認する人に内容を読めないことを示し、承認させずに判断を返させる。

| 経路 | 欄に載るもの | 受け入れ条件 3・4 の満たし方 |
| --- | --- | --- |
| プランの経路 | 判定・理由・レッドライン・保留の本文 | 欄の本文で満たす |
| supervisor の経路で `noted` が真 | 判定・`stopped_by.text`・件数と、今回の判定のコメントの URL（`note_url`） | 承認する人が今回の判定のコメント（`note_url`）を開き、判断に必要な理由・レッドライン・保留を読める場合に、この形で満たして承認を求める。コメントは PR を見られる人だけが読めるので、開示の範囲は広がらない。ただし supervisor は、承認する人が PR を見られるかも、投稿のときに理由や保留が伏せ字（`WITHHELD` の文や伏せた参照）に置き換わったかも確かめられない。そこで欄に「コメントを開けない場合や、判断に必要な理由が伏せ字で読めない場合は、承認せずに『変更を求める』で返し、読めないことを伝える」を足す。そう返されたら、supervisor は、承認する人が閲覧できる場所に全文の資料を用意するまで承認を求めない。閲覧できる場所は承認する人の権限で決まり supervisor には選べないので、`結果: 判断が要る` で呼び出し元へ返し、読めなかった理由と、全文がある手元の結果 JSON のパスを添える |
| supervisor の経路で `noted` が真でない（投稿に失敗した・コメントを書かない終わり方） | 判定・`stopped_by.text`・件数と、判定を打った実行環境の結果 JSON のパス | 全文は実行環境の手元のファイルにだけあり、その環境を開けない承認する人は読めない。受け入れ条件 3・4 を満たさない終わり方として扱い、supervisor は欄に「理由と保留の全文は、判定を打った実行環境の <パス> にだけある。その環境を開けない場合は承認せず、『変更を求める』で返して判定の打ち直しを求める」を足す。承認する人が打ち直しを求めたら、supervisor は同じ作業場所で `ndf records mvv gate --machine` を打ち直し、`noted` が真になった結果で資料を組み直す。打ち直しでも投稿できなければ、`結果: 止まった` で投稿できない理由（`stopped_by` が無いので `why` の件数と `note_unwritten` の定型文）を返す。ただし、`verdict` が null で保留の本文も無い（`holds` が `null` か `[]`）ときは、欄の `stopped_by.text` で判定できない理由が分かり、読めない本文が無いので、この行の扱い（満たさない終わり方とすること・欄に足す文・打ち直し）をしない |

要求仕様（#3959 の本文）の受け入れ条件の文は、この関係を書き足さずに残す（受け入れ条件の変更は設計承認ゲートで利用者が決める）。検証する人は、この表の経路ごとの形で受け入れ条件 3・4 を確かめる。

- 同じ端末を、PR の閲覧権限の無い人と共有する構成では、上の「手元のファイルの閲覧範囲」のとおり、自由記述を含むファイルを所有者だけが読める権限で作ることで、ほかの OS の利用者に読ませない。同じ OS の利用者を共有する構成は、PR を読んだ GitHub の認証も共有するので、この設計では扱わない

プランの経路の承認資料の生成（`progress/gate/asking.py`。満たす決定: 決定 2、受け入れ条件 1・3・4）:

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `mvv_lines` | `progress/gate/asking.py` | 新設 | `mvv_lines(out: Optional[Mapping[str, Any]], gate: str, pr: Optional[int], head: Optional[str]) -> list[str]` | 上の「載せ方」の規則で「## MVV 判定」の節の行を返す純粋な関数。`pr`・`head` は質問の PR と、資料を書く時点の PR の head で、`out` の `pr`・`head` と照らす（どちらかが違う・無いなら、今の head の判定が無いものとして載せる）。`out` は、待っている工程の判定のステップ（`design-mvv`・`release-mvv`）の report から `material` が取り出した `outputs.mvv_gate`（無ければ None）。`progress` は `records` を読まないので、`records` の関数を呼ばず値だけを読む |
| `material` | `progress/gate/asking.py` | 変更 | シグネチャは同じ | 質問の区分（`q.gate`）が設計承認・リリース承認で、待っている工程 `q.stage` の定義がその区分の判定のステップ（`model.MVV_GATE_STEPS[q.gate]`。`design-mvv`・`release-mvv`）を持つとき、「## 未確認の点」の後ろに `mvv_lines(out, q.gate, q.pr, q.target.head)` を足す。`out` は `v.context()` の `mvv_gate` から取らず、`_waiting_context` と同じく待っている工程の済んだステップ（`run.step_progress(v, q.stage).done`。イベント `step_recorded`）から、そのステップの id の report の `outputs.mvv_gate` を直接読む（無ければ None）。工程の出力（`outputs_recorded`）は工程が終わる時にだけ書かれるので、`v.context()` には承認ゲートで待っている工程の判定が無く、リリース承認ゲートでは前の工程（設計）が残した `mvv_gate` を今回の判定と取り違えるため。判定のステップを持たないモードでは節を出さない（今の資料と同じ） |

承認資料は、資料を作る時点の PR の head ごとに別のファイルにし、同じ質問を表示し直すときも、その時点の head の資料を返す。今は資料のパスが質問の id だけで決まり、`write_material` は既存の資料をそのまま返すので、承認を待つ間に PR へ push されると、前の head の判定を載せた資料が新しい head の質問に付いてしまう。そこで `material_path`・`entry.present`・`answering.github_observe`・`write_material` を次のように変える。

- `material_path(root, q)`: シグネチャは同じで、`q.target.head` があれば `<質問の id>-<head の SHA>.md`、無ければ今どおり `<質問の id>.md` を返す。前の head の資料は消さずに残す（回答した人が見た資料の sha256 を後から照らせるようにするため）。質問ごとに 1 つの印のファイル（`release._seen_path` の `<質問の id>.seen.json` など）は今どおり質問の id で名付け、head で分けない
- `answering.github_observe`: PR の今の head を読んだ後、その head を入れた質問で `material_path` を求め、その資料があれば sha256 を返す。回答の時点でも、回答した人が見た資料と同じ head の資料のハッシュを照らす。head が進んでいれば、head の比較で今どおり回答を断る
- `answering._docs_only_after`: リリースを承認した後に文書だけが足された場合に、承認を引き継ぐかを決める関数である。資料を head ごとに分けた後も、引き継ぎの結論が、新しい head の資料を誰かが表示したかどうかで変わらないようにする
  - 今の関数は、今の対象の資料のハッシュ（`now.material`）があり、承認した人が見た資料のハッシュ（`seen.material`）と違えば、引き継がない。資料を head ごとに分けると、`now.material` は新しい head の資料のハッシュになる。新しい head の資料は別のファイルなので、ハッシュは必ず違う。そのため、別の人が新しい head の承認資料を表示した後だけ引き継ぎが断られ、承認した人に同じ判断がもう一度求められる
  - 変えた後は、`now.material` を比べない。比べるのは、承認した人が見た head（`seen.head`）の資料である。`seen.head` を入れた質問で `material_path` を求め、そのファイルがあれば sha256 を `material_sha256` で 1 回求め、`seen.material` と比べる。違うときだけ引き継がない。ファイルが無いとき（消された・別の実行環境で表示した）は、今の `now.material` が None のときと同じく、ハッシュでは断らない。新しい head の資料は比べないので、表示済みかどうかで結論は変わらない
  - シグネチャを `_docs_only_after(root: Path, q: Question, seen: Target, now: Target, docs_only: DocsOnly) -> bool` に変え、呼び手の `recheck` が `st.root` を渡す。ほかの条件（リリース承認ゲートであること・2 つの head の間の変更が文書だけであること）は今のまま
- `entry.present`: 資料を書く前に `observe` を 1 回打つ。返った head を質問の `target.head` に入れ、その head の資料のパスを求める
  - `observe` が資料の sha256 を返した（その head の資料が既にある）ときは、資料を書かず、ハッシュも求め直さず、その値をそのまま使う。同じ資料を二度読まない
  - 返さなかった（資料がまだ無い）ときだけ `write_material` を呼び、書いた資料の sha256 を `material_sha256(path)` で 1 回求める
  - GitHub の読み取りは今と同じ 1 回で、資料の全体の読み取りは、どちらの場合も表示 1 回につき 1 回である
- `write_material`: 書いた資料は書き直さない（今どおり）。資料を所有者だけが読める権限で作り、同じ資料の生成が重なっても止まらないように、一時ファイルの作り方と置き方を変える（上の「手元のファイルの閲覧範囲」のプランの承認資料の行）

`presentation_path` で返す資料はこの `material` の出力である。プランの経路では、資料を書く前にも後にも `cite` を打たない。プランが書く資料は `material` がコードで組む定型の文だけで、設計文書の根拠の行を写さず、「目指す姿の根拠」の節も持たないためである。「MVV 判定」の節の根拠の項目も `mvv_lines` が判定の出力から組み、手で書く行は無い。これを下のテストで固定する。将来プランの資料に設計文書の根拠の行を写す変更では、次の処理を同じ PR に入れる。`write_material` の前に `ndf records mvv cite` と同じ確かめを打ち、`refused`・`precondition` なら資料を出さずに工程を止める処理である。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mvv_lines` | 判定・`why`・保留 2 件 → すべて載る | `holds: []` → 「無し（レビューの記録に保留が無い。記録がまだ作られていない場合を含む）」で、「記録を読んで確かめた」とは書かない。`holds: null`（ベースの取得の失敗）→ 「未確認」と止まった理由。`release` の `holds: null` → #4105 の理由。`design` で `verdict` あり・`holds: null`・`why: []`（実装計画の単位 2 が入り単位 3 がまだの間に、`fast` で検査がすべて通った判定）→ 例外を出さず「未確認（この判定は保留を読んでいない）」で、「止まった」と書かない。`verdict: null`・`why: []` → 例外を出さず「保留を読む前に判定が止まった」。`noted: false` → 「今回は PR に判定のコメントを載せていない」の添え書きで、コメントの URL を載せない。`noted: true`・`note_url` あり → 「今回の判定のコメント: <note_url>」。`out` の `pr`・`head` が引数と同じ → 節の先頭に「判定した PR」の行と、head が違えば未確認とする添え書き | `out` が None → 「判定できない」と「未確認」。`out` の `head` が引数の `head` と違う（判定の後に push された）→ 判定の理由・レッドライン・保留・`why` のどれも載せず、「承認を求める PR … の判定はまだ無い」と「未確認（今の head の判定が無い）」。`out` の `pr` が引数の `pr` と違う → 同じ。`out` に `pr` が無い（更新前の NDF が書いた判定）・引数の `head` が None → 「確かめられない」の文で、判定の PR と head を並べない。`out` に `pr`・`head` が無く `stopped_by` がある（PR を特定する前に止まった）→ 「対象の PR を特定できなかったため、MVV 判定は済んでいない。<`stopped_by.text`>」で、「今ある判定は」の文を載せない |
| `material` | 設計の工程の途中（`design-mvv` の `step_recorded` だけがあり、工程の出力は未記録）の設計承認ゲートの資料に「## MVV 判定」の節があり、その記録の `holds` と `why` が載る。`entry.present` で、`observe` のスタブが返す head が判定の `head` と同じ → 判定が載り、`observe` は 1 回だけ呼ばれ、返す結果の `Target` の承認資料の sha256 が書いたファイルのもの。同じ質問を同じ head で表示し直す → `write_material` も `material_sha256` も呼ばず、`observe` が返したハッシュを使う | `design-mvv` の済んだ後に PR の head が進んだ（`observe` のスタブが別の head を返す）→ 新しい head の名前の資料ができ、前の head の判定の理由と保留が無く、「判定はまだ無い」が載る。前の head の資料は残る。push の前に一度表示した質問を push の後に表示し直す → 前の head の資料を返さず、新しい head の資料を返す。前の head の資料のハッシュで回答する → 断る。判定のステップを持たないモード → 節が無い。文脈に設計の工程の `mvv_gate`（`verdict` あり）が残り、deploy の工程の `release-mvv` がまだ済んでいないリリース承認ゲート → 設計の判定を載せず「判定できない」。判定・根拠の項目・保留を持つ `mvv_gate` で組んだ資料を、`cite` と同じ確かめ（`fenced_lines`・`decision_lines`・`citation_errors` と「目指す姿の根拠」の節の中の検査。`require_basis=False`。プランの資料は節を持たないので、節の有無は問わない）に通す → 誤り 0 件 | — |
| `_docs_only_after`・`recheck`（文書だけの追加の引き継ぎ） | 人が head A の資料（ハッシュ h）を見てリリースを承認し、その後に文書だけを足した head B へ進んだ（`docs_only` のスタブが真）。head B の資料がまだ無い（未表示）→ 引き継ぎ、`recheck` は None でなく引き継いだ旨を返し、accepted ファイルに head B を書く | 同じ記録で、回答の前に別の担当者が head B の質問を表示し、head B の資料ができている（`github_observe` が head B の資料のハッシュを返す）→ 未表示のときと同じく引き継ぐ。head A の資料のファイルが無い → ハッシュでは断らず引き継ぐ | head A の資料のファイルの中身が承認の後に変わり、ハッシュが h でない → 引き継がず待ちへ戻す。`docs_only` のスタブが偽 → 今どおり引き継がない。区分が設計承認 → 今どおり引き継がない |
| `write_material` | 書いた承認資料と一時ファイルの権限が 0600（umask を 022 にしたテストでも） | 資料のディレクトリが無い → 0700 で作る。一時ファイルを置く直前に、別の呼び出しが同じ資料を置いた（`os.link` のスタブが `FileExistsError`）→ 例外を出さず先にある資料のパスを返し、その中身を書き換えず、自分の一時ファイルが残らない | 同じ質問・同じ head の資料が既にある → 書き直さず、権限も変えない |
| `store`（保存先の権限） | 既定の保存先を開く → ディレクトリが 0700（既に 0755 で在っても）で `private` が真 | `NDF_PROGRESS_DB` の SQLite のファイルが無い → 0600 で作る。0644 で在る → 0600 にする。0644 の `-wal`・`-shm` が既に在る → どちらも 0600 になり `private` が真 | 副ファイルの `chmod` が `PermissionError`（スタブ）→ 開くのは止めず `private` が偽。PostgreSQL の URL → `private` が偽 |
| `append_events`（判定の保存） | `private` が真の接続で `step_recorded` を書く → `mvv_gate` は全文のまま | `private` が偽の接続で、`holds: []`・理由 2 件・`why` 1 件の判定を写した `model.mvv_answer` の `answered`（`by: service:mvv`）を書く → 保存された payload の `mvv_gate` の理由と `why` が伏せた文で、件数は同じ。`holds` は `[]` のまま、`by`・`answer`・`gate`・`gate_id`・`seen` は書く前と同じ。読み直した回答で `mvv_answer_void(payload, "design")` が偽（伏せる前と同じ）で、`approved_head(v, "design")` がその回答の head を返す。保留 1 件を写した回答 → 保存された `holds` は 1 件の伏せた文で、`mvv_answer_void` は真のまま | `private` が偽の接続・`conn.info` に値が無い接続で書く → 保存された `mvv_gate` の理由・保留・`why` が伏せた文で、件数は同じ（`step_recorded`・`outputs_recorded`・`answered` のどれでも）。同じ判定の `ndf records mvv gate` の結果 JSON は全文のまま |
| `mvv_stored_view` | 理由 2 件・レッドライン 1 件・保留 2 件・`why` 3 件の出力 → 件数は同じで、どの要素にも元の文が無い。`skip`・`verdict.verdict`・`pr`・`head`・`note_url` は同じ | `holds: []` → `[]` のまま。`holds: null` → `null` のまま | — |

## 工程の入力のコントラクト

満たす決定: 決定 10、受け入れ条件 9

工程の入力の規則は今 2 つある。コントラクトの `stage-in` と、それで確かめる `definition.validate_stage_input` は `{"context": {...}}` の形を求める。しかし、本番のコードからは呼ばれていない（呼ぶのは `tests/progress/test_definition.py` だけ）。プランが実際に組むのは `invoke.resolve_inputs` の平らな object で、文脈のスキーマ（`value_errors`）だけで確かめる。規則を `stage-in` と `validate_stage_input` の 1 つにし、プランの経路と `ndf records mvv gate` の環境変数の経路の両方がそれで確かめる。

| ファイル | 変える中身 |
| --- | --- |
| `contracts/workflow/stage-io.schema.json` | `$defs.stage-in` を `{"$ref": "context.schema.json", "description": "工程の入力。工程の定義の inputs と、文脈にある optional_inputs のキーを文脈から取った平らな object（progress.invoke.resolve_inputs）。script の作業は環境変数 NDF_STAGE_INPUT で、Skill の作業は質問の「入力（JSON）」で受ける"}` にする。`context`・`base`・`head` の囲みは、どの書き込み元も作らず、本番のどの読み手も読まないので外す |
| `src/ndf/progress/definition.py` の `validate_stage_input` | 変える前: `obj` を `stage-in` で確かめ、`obj["context"]` に工程の `inputs` のキーがあるかを見る。変えた後: `stage_input_errors(stage, obj)` の `schema + missing` を返す（`obj` を `stage-in` で確かめ、工程の `inputs`・`optional_inputs` に宣言されていないキーを拒み、`obj` そのものに工程の `inputs` のキーがあるかを見る。足りないキーの誤りの文は「文脈のキー <k> が無い」）。シグネチャは同じ。`stage_input_errors`・`stage_io` を新設する（上の「プランの外のエントリポイント」の `_env_inputs` の手順） |
| `src/ndf/progress/invoke.py` の `resolve_inputs` | 変える前: 取り出した値を `value_errors`（文脈のスキーマ）で確かめる。変えた後: `validate_stage_input(stage, picked)` で確かめる（`stage-in` は文脈のスキーマを指すので、プランの入力の合否は変わらない）。足りないキーの `StageInputMissing` は今どおり先に投げる |
| `docs/contracts/commands.md` | `ndf records mvv gate` の行に、`NDF_STAGE_INPUT` は `stage-in` の形で、外れれば `invalid`・`input_missing` で止まることを足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `validate_stage_input` | 工程の入力のキーを平らに持つ object → 誤り無し | 任意の入力が無い object → 誤り無し | `{"context": {...}}` → スキーマの誤り。工程の入力のキーが無い → 「文脈のキー <k> が無い」 |
| `resolve_inputs` | 今のテストが通る（プランの入力の合否が変わらない） | — | 文脈の値の形が違う → 今どおり `StageInputInvalid` |

## 用語

この文書で使う語の意味は次のとおりである。開示の範囲（どこへ何を載せるか）は「プランの経路」と「supervisor の経路」の違いで決まるので、2 つを分けて定義する。

| 語 | 意味 |
| --- | --- |
| MVV 判定 | 承認ゲートの手前で、LLM が PR の本文と差分を、NDF の共通原則とプロジェクト MVV（目指す姿と必ず人の承認が要る操作を書いた `.ndf/mvv.md`）と突き合わせる判定。`ndf records mvv gate` が打つ。LLM の結論は `outputs.mvv_gate.verdict.verdict` に `follow`（従う）・`not_follow`（従わない）・`unknown`（判定できない）のどれかで入る。`unknown` は LLM が判定を実行して判断できなかった結論で、理由は `verdict.reasons` にある。判定結果そのものを得られなかった場合（検査 1 が外れた・材料を読めない・LLM が回答しない など）は `outputs.mvv_gate.verdict` が `null` になり、止まった理由は `outputs.mvv_gate.stopped_by` に入る。`stopped_by` は `verdict` が `null` のときだけ置く |
| 「従う」 | MVV 判定の 3 つの結論のうち、PR が MVV と共通原則に反していないという結論。`verdict.verdict` の値 `follow` |
| レッドライン | MVV 判定が「従う」でも人の承認を省かない操作。共通原則の `C<番号>` とプロジェクト MVV の `P<番号>` の表にある。判定の出力では `verdict.boundary`、cross-review の記録では `outcome: redline` の行で表す |
| `service:mvv` の回答 | MVV 判定が承認ゲートを省いてよいと決めたとき、人の代わりに NDF（`progress/run.py` の `_consume`）が承認ゲートへ書く承認の回答。回答の事象の payload の `by` が `service:mvv` で、判定の出力の写しを `mvv_gate` に持つ（`model.mvv_answer`） |
| 承認ゲートを省く | 承認ゲートで人に問わず、`service:mvv` の回答で承認として進めること。進め方が `fast`・`auto` のときだけ起きる |
| レビューの保留 | 対象の PR の cross-review の記録（`mvv-validation.jsonl` の `mvv-review` の行）に残る、直っていないレッドラインの指摘と、利用者の判断が要る今のバージョンの未解消の見送りなど。1 件でもあれば承認ゲートを省かない。決め方は「レビューの保留」の `review_holds` |
| プランの経路 | プラン（`supervise.py` が流すステップの列）の判定のステップ（`design-mvv`・`release-mvv`）が `NDF_STAGE_INPUT` を渡して判定を打ち、承認資料を NDF のコード（`progress/gate/asking.py` の `material`）が組んで Git ディレクトリの中へ書く経路 |
| supervisor の経路 | プランのテンプレートが無いときなどに、サブエージェントの supervisor（`ndf:supervisor`）が手で工程を進め、`--machine` で判定を打ち、承認資料を手順書（`approval-request.md`）に沿って書いて呼び出し元へ返す経路。呼び出し元はその資料を承認する人へ転送する |
| 作業場所 | 起動指示の「作業場所」に示された、対象リポジトリの worktree のパスとブランチ（`agent-layers.md` の起動指示の表）。判定のコマンドはここで打つ |
| 作業ディレクトリ | 起動指示の「作業ディレクトリ」に示された、supervisor が作業ファイルを置くディレクトリの絶対パス（例 `<scratchpad>/<フェーズ>-<課題番号>/`）。conductor が supervisor ごとに決める手元の保存先で、作業場所の worktree の外にある |
