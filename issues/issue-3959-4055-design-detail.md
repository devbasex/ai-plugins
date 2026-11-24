# #3959 #4055 の詳細設計: MVV 判定（判定を打つ条件・レビューの保留・根拠の行・プランの外のエントリポイント）

設計文書 [issue-3959-4055-design.md](./issue-3959-4055-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。前提は #4076 の実装が main へ入った後の形である（`records/mvv.py` の `current`・`Snapshot`・`principles`・`record_review`・`fenced_lines`・`decision_lines`、`records/mvv_decide.py` の `citation_errors`・`items_of`、`crossreview/drive.py` の `_summary` が `record_review` を呼ぶ形）。#4076 の実装は #4090・#4099 の設計（PR #4121）の直しを含むので、#4076 の部品の形は確定仕様（[ndf-mvv-current-and-stage-inputs.md](../docs/specifications/ndf-mvv-current-and-stage-inputs.md)・[cross-review-project-mvv.md](../docs/specifications/cross-review-project-mvv.md)）と今のコードの形で読む（設計の文書は実装が終わって消した）。#4090・#4099 の決定 16 が挙げる #3959 と重なる 5 点（`Snapshot.commit`・`cite` の `mismatch`・`cite` が使う確かめの関数・`record_review` の契約・`review_holds` の規則）は、決定 16 の「#3959 の実装がすること」の列の形でこの文書に書いた（設計の決定 11）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/workflow/context.schema.json      … mvv_gate に holds・noted
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
├── src/ndf/progress/command.py                 … stage_inputs_of（ID から判定のステップの入力。リポジトリを照合する）
├── src/ndf/progress/model.py                   … MVV_SKIP_GATES・mvv_skip（リリース承認ゲートを省かない）
├── src/ndf/adapters/publish.py                  … 秘密を含む参照を公開範囲の照会へ送らず伏せる・秘密と参照の重なる範囲を 1 回で伏せる
├── src/ndf/adapters/run_log.py                  … secret_spans（秘密の一致範囲）
├── src/ndf/domains/crossreview/
│   ├── drive.py                                 … _summary が record_review の欠けた必須の行を Outcome へ・_fix_prompt に見送りの区分
│   ├── journal.py                               … apply_fix が見送りの区分 defer を持つ
│   ├── finish_decide.py                         … finish(record_failed=…)
│   └── cli.py                                   … cmd_run が record_failed を渡す
└── tests/
    ├── adapters/test_publish.py
    ├── domains/records/test_mvv_gate.py
    ├── domains/records/test_mvv_review_holds.py *
    ├── domains/records/test_mvv_cite.py *
    ├── domains/records/test_mvv_record_review.py
    ├── domains/records/test_mvv.py
    ├── progress/gate/test_gate_checks.py
    ├── progress/gate/test_gate_present.py
    ├── progress/test_command_mvv_inputs.py *
    ├── progress/test_invoke.py
    ├── progress/test_definition.py
    ├── domains/crossreview/test_crossreview_journal.py
    └── domains/crossreview/test_crossreview_finish.py
```

依存の向き: `records/cli.py` が `progress.command`（文脈を読む）と `records.mvv_gate` を読む。`mvv_gate` は今どおり `progress.gate.checks` と `progress.definition` を読む。`mvv_decide` は git もファイルも読まない純粋な関数だけを持つ。`progress` は `records` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの前の判定 | `checks`・`mvv`・`mvv_decide`・`mvv_material_decide` | モジュール | `gate`・`_judged`・`pr_number`・`boundary_paths`・`_recorded`（変更）・`snapshot_facts`・`review_prs`・`public_cause`（新設）・定数 `GATE_STEPS`・`GATE_STAGES`（新設）。`base_facts` は消す |
| `src/ndf/progress/model.py` | 変更 | ステートマシンの型と規則 | — | モジュール | 定数 `MVV_SKIP_GATES`・`MVV_GATE_STEPS`（新設）・`mvv_skip`（変更） |
| `src/ndf/progress/gate/checks.py` | 変更 | 省かない理由の検査 | — | `MvvFacts`（変更） | `check_review_holds`（新設）・`refusals`（変更）・`judgeable`（新設）・`may_skip`・`note`（変更）・定数 `CHECKS_BY_GATE`（新設） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の記録の読み書き | `jsonl`・`mvv_decide` | `ReviewWrite`（新設）、モジュール | `validation_rows`・`review_rows`・`cite`・`compare_files`（新設。`_judged` は `pr_files` を呼ばず、`pr_facts` には読み済みの行を渡す）・`record_review`（返り値を変える）・`mvv_validate_sprint`（引数 `rows` と返り値を変える） |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | — | モジュール | `review_holds`（新設） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力 | — | モジュール | `gate_outcome`（変更）・`diff_rows`・`unquote_path`・`DiffError`（新設） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` のエントリポイント | `mvv_gate`・`progress.command` | モジュール | `_mvv_gate`（変更）・`_mvv_inputs`・`_env_inputs`・`_mvv_cite`（新設） |
| `src/ndf/progress/definition.py` | 変更 | 工程の定義と規約の読み手 | `contracts` | `StageInputErrors`（新設）、モジュール | `validate_stage_input`（入れ子の `context` でなく、入力の object そのものを確かめる）・`stage_input_errors`・`stage_io`（新設） |
| `src/ndf/progress/invoke.py` | 変更 | 工程から作業を呼ぶ層 | `definition` | モジュール | `resolve_inputs`（取り出した入力を `validate_stage_input` で確かめる） |
| `contracts/workflow/stage-io.schema.json` | 変更 | 工程の入力と出力の型 | `context.schema.json` | — | `$defs.stage-in` を文脈のスキーマへの参照にし、`context`・`base`・`head` の囲みを外す |
| `src/ndf/progress/command.py` | 変更 | ステートマシンの操作 | `store`・`invoke` | モジュール | `stage_inputs_of`（新設） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認ゲートの質問と承認資料 | — | モジュール | `mvv_lines`（新設）・`material`（変更） |
| `src/ndf/adapters/publish.py` | 変更 | 書き先へ出す本文の検査 | `run_log` | `Publisher`（変更） | `Publisher._private_refs`（変更。秘密を含む参照を照会しない）・`scrub`（変更。秘密と参照の一致範囲の和を 1 回で置き換える） |
| `src/ndf/adapters/run_log.py` | 変更 | 秘密の伏せ字 | — | — | `secret_spans`（新設。秘密の一致範囲を返し、`redact` もそれで置き換える） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv` | `Outcome`（変更） | `_summary`（変更） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | — | `Journal`（変更） | `apply_fix`（見送りの区分 `defer` を持つ） |
| `contracts/data/review-criteria.json` | 変更 | 指摘の基準の文 | — | — | 見送りの区分の選び方 |
| `src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方の判定 | — | モジュール | `finish`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review` のエントリポイント | `finish_decide` | モジュール | `cmd_run`（変更） |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | — | `$defs.mvv_gate` に任意のキー `holds`・`noted` |

## 判定を打つ条件と省く条件

満たす決定: 決定 1・3、受け入れ条件 1・2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `judgeable` | `progress/gate/checks.py` | 新設 | `judgeable(f: MvvFacts) -> Optional[str]` | 判定を打てない理由（検査 1 だけ）。打てれば None |
| `CHECKS_BY_GATE` | `progress/gate/checks.py` | 新設 | `dict[str, tuple[Callable[[MvvFacts], Optional[str]], ...]]` | 区分ごとの省かない理由の検査。`design`・`release` とも検査 1〜7 を並べる。`release` は `review_prs` が空なので、#4105 までは保留が常に空になり、検査 7 は何も返さない（即時修正の区分は #4104 が足す） |
| `refusals` | `progress/gate/checks.py` | 変更 | `refusals(f: MvvFacts, gate: str = "design") -> list[str]` | 区分の検査の理由を集める。既定の区分は今の呼び手のため |
| `may_skip` | `progress/gate/checks.py` | 変更 | `may_skip(f: MvvFacts, verdict: Any, gate: str = "design") -> tuple[bool, list[str]]` | `refusals(f, gate)` を使う。ほかは今と同じ |
| `gate` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `_judged` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `pr_number` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `design` は `inputs["worktree"]`（文字列のとき。無ければ `root`）のブランチの開いた PR。`release` は今のまま（文脈の `pr`）。文脈の `design_pr` は読まない（プランの経路の入力に無いので、両方のエントリポイントで同じ PR を選ぶため） |
| `boundary_paths` | `records/mvv_gate.py` | 変更 | `boundary_paths(root: Path, ref: str) -> tuple[str, ...]` | レッドラインのパスを、作業ツリーでなくコミット `ref` の進め方の設定ファイルから読む。ファイルの位置は今の `boundary_paths` と同じく宣言から求める（`files.relpath("pace")`。`ndf-files` の `locations` の宣言から得るリポジトリ相対パスで、`.ndf/pace.json` を文字列リテラルで書かない）。内容だけを、固定したコミットから `git show <ref>:<そのパス>` で読む。`ref` に無ければ空。形が壊れていれば `jsonfile.JsonFileError` |
| `Snapshot` | `records/mvv.py` | 変えない（#4076 の実装が欄 `commit` を足す。#4090・#4099 の決定 9） | — | `current` が解決したコミットの SHA を `commit` に持つ。この Issue は欄を足さずに `snapshot_facts` で読む |
| `current` | `records/mvv.py` | 変えない（#4076 の関数） | — | 参照の解決はエントリポイントの 1 回だけで、呼び手は解決し直さない。ベースの本文が承認の記録と合わない（`mismatch`）ときは `MvvError` を投げる（#4090・#4099 の決定 15） |
| `snapshot_facts` | `records/mvv_gate.py` | 新設 | `snapshot_facts(root: Path, snap: mvv.Snapshot, inputs: Mapping[str, Any]) -> tuple[MvvFacts, str]` | `base_facts` の置き換え。MVV の状態・本文の sha256・承認の記録の sha256・本文をすべて `snap`（ベースの MVV）から、`boundary_paths` を `boundary_paths(root, snap.commit)` から作る。作業ツリーの `.ndf/mvv.json`・`.ndf/mvv.md`・`.ndf/pace.json` は読まない（PR が `boundary_paths` から自分の変えるパスを外しても、今回の変更にはベースの規則が効く） |
| `compare_files` | `records/mvv.py` | 新設（`pr_files` の置き換え。呼び手は `_judged` だけ） | `compare_files(hub: GitHub, g: git.Git, remote: str, base: str, head: str) -> list[dict[str, Any]]` | `GET /repos/<repo>/compare/<base>...<head>` を 1 回打ち、応答の `files`（`pulls/<番号>/files` と同じ形の行）を返す。`base`・`head` は SHA。基点を SHA で固定するので、読む間に宛先が進んでも差分は変わらない。GitHub の比較は `files` を比較全体で 300 件（定数 `COMPARE_FILES_CAP = 300`）までしか返さない（[GitHub REST API の比較の仕様](https://docs.github.com/en/rest/commits/commits#compare-two-commits) の「Working with large comparisons」）ので、`files` が 300 件に届いたときだけ、同じ SHA の組の差分を手元の git で読み直して返す: `done = g.fetch(remote, head)`（head の SHA を取得する。`base` は手順 (2) の `fetch_branch_or_raise` で手元にある。`Git.fetch` は失敗でも例外を投げず `Completed` を返すので、`done.returncode` が 0 でなければ `GitError(f"{remote} から {head} を取得できない: {done.stderr.strip()}")` を投げる）→ `mb = g.merge_base(base, head)` → `g.diff_between(mb, head, "--no-color", "--no-ext-diff", "-M")` → `mm.diff_rows(text)`。比較の API の `<base>...<head>` は 3 点の比較で、`base` と `head` の merge-base から `head` までの差分（PR の画面の差分と同じ）を返す（[GitHub の 2 点と 3 点の比較](https://docs.github.com/en/pull-requests/reference/branches#three-dot-and-two-dot-git-diff-comparisons)）。`git diff <base> <head>` は両端のツリーを直接比べる 2 点の比較で、`base` が PR の分岐点より進んでいると、宛先だけの変更が逆向きに載り、両ブランチで同じに変えたファイルが落ちるので使わない。手元の経路も merge-base を先に求め、merge-base と `head` を比べる（`git diff <base>...<head>` と同じ。[git diff の仕様](https://git-scm.com/docs/git-diff)）。`merge_base` の失敗も `GitError`。300 件未満なら git を打たない |
| `diff_rows` | `records/mvv_material_decide.py` | 新設 | `diff_rows(text: str) -> list[dict[str, Any]]` | `git diff` の出力を unidiff（`crossrefactor/diff_decide.py` と同じライブラリ）で読み、`files` と同じ形の行（`filename`・`previous_filename`（改名のとき）・`status`（`added`・`removed`・`modified`・`renamed`）・`patch`（ハンクの本文。`diff --git` の頭は含めない）・`changes`（足した行と消した行の和））を返す純粋な関数（`records` は `crossrefactor` を読まないので、`DiffError` もこのモジュールに `ValueError` の子として置く）。バイナリのファイルは `patch` を持たず `changes` を 1 にする（`incomplete_patches` が載らないファイルとして数える。GitHub の `files` でバイナリが `patch` を持たないのと同じ扱い）。`filename` と `previous_filename` は、どちらも元のパス（GitHub の `files` と同じ UTF-8 の文字列）に復元して置く: 既定の git（`core.quotePath` が真）は ASCII でないバイトや `"`・`\`・制御文字を含むパスを `"` で囲み、C の形のエスケープ（`\"`・`\\`・`\t`・`\n` などと、バイトごとの 8 進の `\ooo`）で書くが、unidiff の `path`・`source_file`・`target_file` はこのエスケープを復元しない。`diff_rows` は unidiff の値をそのまま使わず、`diff --git` の頭・`---`/`+++`・`rename from`/`rename to` の名前が `"` で囲まれていれば、エスケープをバイトへ戻して UTF-8 で読み、その後に `a/`・`b/` の頭を外す（復元の関数は同じモジュールの純粋な関数 `unquote_path(name: str) -> str`。UTF-8 で読めないバイトは `DiffError`）。復元しないとエスケープされたパスがレッドラインのパスと一致せず、検査 4 を素通りする。読めなければ `DiffError` |
| `review_prs` | `records/mvv_gate.py` | 新設 | `review_prs(gate: str, number: int) -> list[int]` | 保留を読む PR。`design` は `[number]`、`release` は空（#4105 まで）。下の「レビューの保留」の接ぎ目 |
| `public_cause` | `records/mvv_gate.py` | 新設 | `public_cause(exc: BaseException \| str, repo: str) -> str` | 例外の文字列（と `mvv_validate_sprint` が返す追記の失敗の原因）を、公開の書き先へ渡してよい文に変える純粋な関数。書き先を公開とみなし、`publish.scrub(text, True, <text の repo_refs のうち repo と同じでないものすべて>, publish.local_accounts())` の本文を返す。秘密の値・ホームの絶対パス・メールアドレス・手元のアカウント名と、判定する PR のリポジトリ以外への参照をすべて伏せる。公開範囲を照会しないので、GitHub の呼び出しは増えない。下の「結果に入れる原因の文」 |

- `gate`
  - 手順: (1) モードの定義がそのゲートを持たなければ、今どおり判定せずに返す。(2) `_judged` を呼んで返す。(3) `gate` が捕まえるのは、`_judged` の手順 (1)（PR を読み終えるまで）の例外（`GateError`・`GitError`・`GitHubError`・`IdentityError`・`OSError`）だけで、`verdict: null`・`skip` 偽・`head` 無し・`why` は「MVV 判定を打てない: <理由>」にし、コメントは書かない（書く先の PR が決まっていない）。PR を読んだ後の失敗は `_judged` が中で受け、`gate` へ例外を出さない（下の「PR を読んだ後の失敗」）
  - 進め方の条件（`inputs["pace"] not in MVV_PACES` で返す行）は消す。進め方は `_judged` の最後の「省くか」でだけ読む
- `_judged`
  - 手順: (1) `pr_number` で PR を決め、`hub.pull` で `pull`（head・`base_ref`・本文）を 1 回読む。ここで読んだ `pull.head_sha` を、以降のすべての出力の `head` にする。(2) `git.Git(root).fetch_branch_or_raise(<リモート>, <base_ref>)`（refspec `+refs/heads/<base_ref>:refs/remotes/<リモート>/<base_ref>`。#4090・#4099 が `current` の CLI に指定した関数と同じ）を 1 回打ち、直後に読むリモート追跡参照そのものを更新する（`git fetch <リモート> <base_ref>` は `--single-branch` の取得設定では `FETCH_HEAD` だけを更新し、追跡参照が古いまま残るので使わない）。続けて `snap = mvv.current(root, md.signals_ref(base_ref, リモート), allow_mismatch=True)` でベースの MVV を 1 度だけ読む。本文のハッシュが承認の記録と合わないときは、`current` が `MvvError` を投げずに状態 `mismatch` の `snap` を返し、(3) の検査 1 で止まる（コメントを書かず `noted: false`。設計書の分岐表の「検査 1 が外れる」と同じ扱い）。参照を SHA へ解決するのは `current` のエントリポイントの `git rev-parse` の 1 回だけで、`_judged` は `git rev-parse` を打たず、`current` が返した `snap.commit` をベースのコミットとして使う。`snapshot_facts(root, snap, inputs)` で事実を作る（レッドラインのパスも同じ `snap.commit` から読む）。この後の検査 1・判定の材料・保留のバージョンは、どれもこの `snap` と `snap.commit` だけを使う（作業ツリーの MVV と `.ndf/pace.json` を読まない。PR が `.ndf/mvv.json` を消しても、ベースが承認済みなら検査 1 を通って判定し、検査 5 が外れる）。(3) `judgeable` が理由を返せば、`gate_outcome(gate, None, False, [理由], head)` で返し、コメントは書かない（承認済みの MVV が無いリポジトリの出力と PR を今と同じにする）。出力に `noted: false` を置く。(4) 差分を、固定した SHA の組から `mvv.compare_files(hub, git.Git(root), <リモート>, snap.commit, pull.head_sha)`（`GET /repos/<repo>/compare/<snap.commit>...<head_sha>` を 1 回。ページを指定しない。`files` が 300 件に届いたときだけ、同じ SHA の組を手元の git で読み直す）で読み、`pr_facts(hub, number, facts, rows, pull)`（読み済みの `pull` を渡す）で事実を足す。`pr_files`（`pulls/<番号>/files`）は GitHub がその時点の宛先の先端と比べた差分を返し、材料を読む間に同じ宛先へ別の PR がマージされると、MVV を読んだ `snap.commit` と違うベースの差分になるので使わない。比較の基点を `snap.commit` に固定するので、差分・MVV・レッドラインのパスはどれも同じベースのコミットに属する（宛先がその後に進んでも、判定は `snap.commit` を宛先とした PR の判定になり、宛先の先端との照合は #4106 が消費の時点で行う）。GitHub の比較は `files` を 300 件で打ち切るので、300 件に届いたら `compare_files` が同じ `snap.commit` と (1) の head の組を手元の git から読み直す（比較の API と同じく、両者の merge-base から head までの差分）（今の `pulls/<番号>/files` のページ読みで判定できた 300 件以上の PR を、判定できないものにしない）。読み直した行も (6) の `incomplete_patches` を通る。差分を読み終えたら `hub.pull` をもう 1 回読み、head か宛先（`base_ref`）が (1) の値と違えば材料を捨てて止める（下の表。差分は (1) の head と `snap.commit` に固定して読むが、読む間に push されると判定が今の head に属さず、宛先を変えられると `snap` が別のブランチの MVV になるため。判定の材料は (1) の head と宛先と、`snap.commit` から (1) の head までの差分だけにする）。この読み直しが `GitHubError` を投げたときも材料を捨てて止める（下の表。未照合の差分を使わず、例外を `gate` へ出さない）。(5) 記録 `mvv-validation.jsonl` を `validation_rows` で 1 回だけ読み、保留を求める（下の「レビューの保留」）。読めたときの行は (7) の再利用にも渡す。(6) `mm.incomplete_patches(rows)` があれば、`refusals(facts, gate)` の理由とあわせて `verdict: null` で記録して返す。(7) LLM の判定（今と同じ材料で `mvv_validate_sprint(root, 入力, judge, reuse=読めたか, rows=(5) で読めた行, broken=(5) の同じ読み取りで除いた行)`。(5) で記録を読めたときだけ、その行から同じ材料の「従う」を再利用し、記録を読み直さない。`broken` を省くと既定の空になり、直近の判定の行が壊れていても古い「従う」を今回の判定として使ってしまうので、必ず渡す。読めなかったときは再利用を省いて LLM で判定する）。判定の行の追記が `OSError` で失敗しても判定は捨てず、`verdict` はそのまま、追記の失敗の印 `unrecorded`（原因）を持ち、`why` に「判定を記録（mvv-validation.jsonl）に書けない: <原因>」を足して (8) へ進む（`skip` は (8) で決める）（(5) で求めた保留は `holds` にそのまま残る。記録を読めず追記もできないときは、判定・保留の「読めない」・記録に書けない理由の 3 つがそろって返る）。`GateError`（LLM が回答しない）はここで受け、`verdict: null`・`skip` 偽・`why` は「MVV 判定を打てない: <理由>」と `refusals(facts, gate)` の理由、`holds`・`head` は読んだ値のまま (9) へ進む。(8) `ok, reasons = may_skip(facts, verdict, gate)` とし、`skip = ok and unrecorded is None`、`why` は (7) までに足した理由（記録に書けない理由を含む）の後ろに `reasons` を続けたものにする（`may_skip` の返り値で (7) の `skip` と `why` を上書きしない。記録に行の無い判定で設計承認ゲートを省かないため）。進め方が `MVV_PACES` に無い（`normal` など）なら `skip` を偽にし、`why` の先頭に「進め方 <値> は承認ゲートを MVV 判定で省かない」を足す。区分が `release` なら `skip` を偽にし、`why` に「リリース承認ゲートは、保留を読む PR の範囲が決まるまで（#4105）MVV 判定で省かない」を足す（決定 3）。(9) `_recorded` で PR のコメントへ書いて返す
  - 事後条件: `mvv-validation.jsonl` を読むのは (5) の 1 回だけで、`mvv_validate_sprint` は読み直さない。`verdict` が null なのは、(1) の失敗（`gate` が受ける）・(3)・下の「PR を読んだ後の失敗」の表で `verdict: null` とした行（(2) のベースの取得・(4) の差分の読み取り・head と宛先の移動・読み直しの失敗・(6)・(7) の LLM の失敗）のときだけ。`normal` の `skip` は常に偽。判定の行を記録に追記できなかった判定の `skip` は、進め方と `may_skip` によらず常に偽。判定の差分・MVV・レッドラインのパスは同じ `snap.commit` に属する。PR を読んだ後の出力は、どの終わり方でも (1) で読んだ `head` を持つ
  - 副作用: PR のコメントは、下の表の「コメント」の列が「書く」の終わり方でだけ 1 件書く（(3) と、判定の材料が今の head に属さない終わり方では書かない）。`_recorded` を呼ぶと、`Publisher` が投稿の前に書き先の公開範囲を照会する（GraphQL 1 回）ので、投稿の成否によらず照会がかかる。続けて投稿の REST が最大 1 回（照会に失敗すれば打たない）、書き先が公開か公開範囲を読めず本文に他のリポジトリへの参照があれば参照 100 件ごとに GraphQL が 1 回かかる。下の表の「書けない」の行（投稿に失敗した判定）も `_recorded` を呼ぶので数える。今は `_recorded` を呼ばずに終わり、変更後に初めて呼ぶ終わり方（`normal` の判定、PR を読む前の検査で返していた判定、PR を読んだ後の例外で返していた判定）では、これがそのまま増える（終わり方ごとの表は設計書の「費用」）。ベースの取得（`fetch_branch_or_raise` の `git fetch` 1 回）・PR の読み直し（REST 1 回）。差分は `pulls/<番号>/files` の代わりに比較の REST 1 回で読むので、差分の REST の呼び出しは増えない（`pulls/<番号>/files` のページ読みより減る）。`files` が 300 件に届いた PR だけ、head の `git fetch`・`git merge-base`・`git diff` が 1 回ずつ増える（Git の起動が 3 回）。LLM の判定は `llm.ask` を 1 回呼び、`llm.ask` は回答が形に合わなければ 1 回だけ打ち直すので、`claude` の起動は判定 1 回につき最大 2 回（`llm.ATTEMPTS`）になる。ほかにコピーの書き込みが増える（`current` の副作用）
- `_recorded`: `note(outcome["gate"], outcome["verdict"], outcome["why"], outcome["holds"] or ())` を書く（`holds` は下の `gate_outcome` のとおり常にあり、未確認の null は空として渡す）。`why`・`holds` の原因の文は、下の「結果に入れる原因の文」のとおり `public_cause` で変えた後の値である。それでも PR へ書く本文には判定の理由など他の文も入るので、書き込みは今どおり `post_note`（`publish.Publisher(hub).comment`）だけを通し、`_mvv_cite` と同じ包み（`Publisher.guarded`。書き先の公開範囲の照会と、秘密・ホームのパス・メールアドレス・アカウント名・非公開の参照の除去）を通した後の本文だけを PR に書く。`hub.rest` を直接呼んで書かない。公開範囲の照会に失敗したときも `GitHubError` として下の投稿の失敗と同じに扱う。書き込みの `GitHubError` は外へ出さず、`skip` を偽にし、`why` の末尾に「判定の記録を PR に書けない: <原因>」を足し、出力に `noted: false` を置いて返す（`verdict` と `head` は保つ）。書けたときは `noted` を置かない
- 結果に入れる原因の文: 出力（結果 JSON の `outputs.mvv_gate`）の `why` と `holds` に入れる原因は、生の例外の文字列でなく、`public_cause(<例外>, hub.repo)` で変えた文にする。例外の文には、PR にも記録にも無い手元の端末の情報（ホームの絶対パス・アカウント名・秘密の値）と、判定する PR と関係の無い他のリポジトリへの参照が入りうる。出力は承認資料へ全件転記され（下の「手順書」の「載せ方」）、PR のコメントにもなるので、書き先の公開範囲を照会せずに、公開の書き先と同じに伏せる。判定の理由と保留の自由記述は、この関数の対象でない（下の「手順書」の「承認資料の開示の範囲」）。対象は、`gate` の手順 (3) の「MVV 判定を打てない: <理由>」、下の「PR を読んだ後の失敗」の表の各行の `<原因>`、手順 (5) の保留「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」、手順 (7) の追記の失敗の原因（`unrecorded`）と LLM の失敗の理由、`_recorded` の「判定の記録を PR に書けない: <原因>」のすべてである。例外を受けたその場で変え、生の文字列を `gate_outcome` へ渡さない。`warnings` にも生の文字列を載せない
- `note`（`checks.py`）: シグネチャを `note(gate: str, verdict: Any, why: Sequence[str], holds: Sequence[str] = ()) -> str` に変え、`holds` が空でなければ「### レビューの保留」の箇条書きを足す。呼び手は `mvv_gate._recorded` だけ
- `gate_outcome`（`mvv_material_decide.py`）: 引数 `holds: Optional[Sequence[str]] = None` を足し、出力に `holds` のキーを常に置く。保留を読んだ（`_judged` の手順 (5) まで進み、`review_prs` が PR を返した）ときは読んだ結果の配列（無ければ空の配列 `[]`。読めなかったときは読めない理由の 1 件）、保留を読んでいないときは `null`。`null` になるのは、(5) より前で終わった終わり方（(2) のベースの取得・(3) の検査 1・(4) の差分と読み直しの失敗）と、保留を読む PR の範囲を決めない `release`（#4105 まで）である。`null` と `[]` を同じ表示にしない（下の「手順書」の載せ方）。`context.schema.json` の `$defs.mvv_gate` に任意のキー `holds`（文字列の配列か null）と `noted`（真偽値）を足す。判定した PR の番号・宛先・ベースのコミットを出力に足して省いた回答と照合するのは #4106

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

| 手順 | 失敗 | 検出する例外・条件 | `verdict` | `why` に足す理由 | コメント | 結果 | 再試行 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| (2) | ベースの MVV・レッドラインの設定を読めない | `fetch_branch_or_raise` の `GitError`・`current` の `MvvError`（参照を解決できないときを含む。本文のハッシュの不一致は `allow_mismatch=True` で読むのでここに来ず、(3) の検査 1 になる）・`boundary_paths` の `JsonFileError`・`OSError` | null | 「ベースの MVV を読めない: <原因>」。作業ツリーの MVV で代えない | 書く | `ok`・人へ問う | 直して 判定のステップから |
| (4) | 差分を読めない | `compare_files` の `GitHubError`、300 件に届いたときの手元の読み直しの `GitError`（head の取得の終了コードが 0 でない・merge-base・`git diff`）・`DiffError` | null | 「変えたファイルを読めない: <原因>」 | 書く | 同上 | 判定のステップから |
| (4) | 差分を読む間に head か宛先が動いた | 読み直した head か `base_ref` が (1) と違う | null | 「判定の材料を読む間に PR が <base> 宛て・<a> から <base'> 宛て・<b> へ動いた」 | 書かない（材料が今の PR に属さない） | `ok`・人へ問う | 判定のステップから |
| (4) | 差分の後に PR を読み直せない | 読み直しの `hub.pull` の `GitHubError` | null | 「判定の材料を読んだ後に PR を読み直せない: <原因>」。読んだ差分は捨てる | 書かない（材料を照合できていない） | `ok`・人へ問う | 判定のステップから |
| (6) | 差分の全体が材料に載らない | `incomplete_patches` | null | 載らないファイル | 書く | 同上 | PR を分ける |
| (5) | 記録（`mvv-validation.jsonl`）を読めない | `validation_rows` の `OSError`・`UnicodeDecodeError`・`internal.InternalError`（新しい形式のバージョンの行） | 判定のまま（判定は止めない） | 保留の 1 件「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」が `refusals` に入る（「MVV 判定を打てない: 」は付けない） | 書く（保留を載せる） | `ok`・人へ問う（省かない） | 原因を直して判定のステップから。読めない間は同じ材料の「従う」も再利用せず LLM を呼ぶ |
| (7) | LLM が回答しない | `GateError` | null | 回答が無い理由と `refusals` | 書く | 同上 | 打ち直せる |
| (7) | 判定の行を記録に追記できない | `mvv_validate_sprint` が返す追記の失敗の原因（`OSError`） | 判定のまま | 「判定を記録（mvv-validation.jsonl）に書けない: <原因>」（「MVV 判定を打てない: 」は付けない）。(8) の `may_skip` の理由はこの後ろに続け、この理由と `skip` 偽を上書きしない | 書く（判定・保留・記録の失敗を載せる） | `ok`・人へ問う（省かない） | 原因を直して判定のステップから（記録に行が無いので LLM を呼び直す） |
| (9) | コメントを書けない | `_recorded` の `GitHubError` | 判定のまま | 「判定の記録を PR に書けない: <原因>」 | 書けない（`noted: false`） | `ok`・人へ問う（省かない） | 判定のステップから（同じ材料の「従う」は記録を再利用し LLM を呼ばない） |

PR を読む前の失敗（`gate` が受ける）:

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pr_number` | 開いた PR が無い（`design`） | PR を出していない | `GateError` | `verdict: null`・`head` 無し・コメント無し | `ok` で人へ問う | PR を出して打ち直す |
| `hub.pull` | PR を読めない | GitHub が回答しない | `GitHubError` | 同上 | 同上 | 打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate`（`normal`） | 判定のスタブが 1 回呼ばれ、`verdict` を持ち `skip` は偽、`why` の先頭に normal の理由、コメントが 1 件。レビューの記録を読み、保留が無かった判定（`holds: []`）もコメントを書く。ベースの取得に失敗した終わり方と `release` の判定は `holds: null`（`[]` にならない） | 判定が「従う」・レッドライン空でも `skip` は偽 | LLM の失敗 → `verdict: null` で `ok`。出力は `head` と `holds` を持ち、コメントが 1 件で保留と理由が載る |
| `gate`（`fast`） | 今と同じ材料で「従う」→ `skip` 真 | 検査 4・5・3 が外れても判定は打たれ `skip` は偽 | 検査 1 が外れる → 判定のスタブを呼ばない。記録は正常に読める（保留は空）が判定の行の追記だけが `OSError`、判定は「従う」・検査 2〜7 が通る → `verdict` は判定のまま・`skip` 偽・`why` に記録に書けない理由が残る（`may_skip` の結果で上書きされない） |
| `_judged`（ベースの取得と差分） | ベースの取得が `fetch_branch_or_raise` を 1 回だけ呼ぶ（Git のスタブで refspec が `+refs/heads/<base_ref>:refs/remotes/<リモート>/<base_ref>` であることを見る）。差分の比較の REST が `snap.commit` と (1) の `head_sha` の組で 1 回だけ呼ばれ、`pulls/<番号>/files` を呼ばない | 比較のスタブが返す前に、宛先の先端を進めた（`hub.pull` の 2 回目は同じ head・同じ `base_ref`）→ 比較の基点は `snap.commit` のまま、判定の材料の差分も `snap.commit` からのもの | 比較の `files` が 299 件 → git を打たない。300 件 → 同じ `snap.commit` と head の組で `fetch`・`merge_base`・`diff_between` が 1 回ずつ呼ばれ、`diff_between` の左端は `merge_base` の返した SHA（`snap.commit` でない）で、手元の差分の行（301 件目のファイルを含む）で判定する。宛先だけが分岐後に進んだ状態を Git のスタブで作る（`merge_base` は分岐点を返し、`diff_between` は左端が分岐点のときだけ PR の変更の差分を返し、`snap.commit` のときは宛先だけが変えたファイルの逆向きの差分を混ぜ、両ブランチで同じに変えたファイルを落とした差分を返す。子のプロセスは起動しない）→ 比較の API の `files` を模したスタブの変更ファイルと、手元の経路の `filename` の集まりが一致する（宛先だけの変更が載らず、両ブランチで同じに変えたファイルが落ちない）。Git のスタブの `fetch` が非ゼロの `Completed`（例外を投げない）を返す → `GitError` に変わり、`diff_between` を呼ばず `verdict: null`・「変えたファイルを読めない」・コメント 1 件 |
| `diff_rows` | 追加・削除・変更・改名のファイルが `files` と同じ形の行になる | 中身を変えない改名 → `changes: 0` で `incomplete_patches` に数えない。バイナリ → `patch` 無しで数える | 読めない差分 → `DiffError` |
| `unquote_path`・`diff_rows`（パスの復元） | 既定の `core.quotePath` の git が出す形の差分の文字列（固定の入力。git を起動しない）で、日本語名のファイルの変更と、日本語名からの改名 → `filename` と `previous_filename` が元のパスで、同じ変更の API の行の `filename`・`previous_filename` と一致する | `"`・`\\`・タブを含むパス → 元の文字に戻る。引用の無いパスはそのまま | UTF-8 で読めない 8 進のエスケープ → `DiffError` |
| `snapshot_facts` | 事実がすべて `snap` と `snap.commit` の値。`git rev-parse` を打たない（Git の起動を数えるスタブで `current` の 1 回だけ） | 作業ツリーの本文が `snap` と違っても事実は `snap` のまま。作業ツリーの `.ndf/pace.json` から `boundary_paths` のパスを消した PR が、そのパスも変える → ベースの規則で検査 4 が外れる | ベースの `.ndf/pace.json` が壊れている → `JsonFileError` |
| `pr_number` | `worktree` の指すブランチの PR | 文脈に `design_pr` があっても読まない | 開いた PR が無い → `GateError` |
| `_judged`（PR を読んだ後の失敗） | 上の表の各行が、表の `verdict`・`head`（(1) の値）・`why`・コメントの有無になり、例外を外へ出さない。どの行も `Gated.target` は (1) で読んだ PR | `compare_files` の後の読み直しで head が動いた・head を変えずに宛先が変わった・読み直しが `GitHubError` → 差分を判定に使わず LLM を呼ばず、コメントを書かない | ベースの `fetch_branch_or_raise` の `GitError` → `verdict: null`・`head` あり・コメント 1 件。コメントの書き込みの失敗 → `noted: false`・`skip` 偽・`verdict` は保つ。ベースの本文のハッシュが承認の記録と合わない → (2) で止まらず検査 1 の分岐になり、`verdict: null`・`noted: false`・コメント無し（`post_note` のスタブが呼ばれない）。偽のホームの絶対パス（`/home/<偽の名前>/...`）と偽のアカウント名を含む `OSError` で記録を読めない（と追記できない）判定を、公開の書き先で書く → `post_note` が受けた本文（`Publisher` の `hub.rest` のスタブが受けた `body`）に偽のパスとアカウント名が無い。同じ判定の出力の `why` と `holds`、その出力で `mvv_lines` が組んだ承認資料の行にも、偽のパスとアカウント名が無い。ベースの取得の `GitError` の文に偽のパス・偽のアカウント名・偽の秘密（環境変数に置いた値）・別のリポジトリへの参照を含めても、出力の `why` にどれも無い |
| `public_cause` | 偽のホームの絶対パスと偽のアカウント名を含む `OSError` → どちらも伏せ字になり、ほかの文は残る | 判定する PR のリポジトリへの参照 → 残る。別のリポジトリへの参照 → 伏せる。公開範囲の照会（`visibilities`）を呼ばない | 偽の秘密（環境変数に置いた値）を含む文 → 秘密が残らない |
| `note` | 保留と判定を載せる | 保留が空なら節を出さない | — |

## リリース承認ゲートの省略の止め

満たす決定: 決定 3

リリース承認ゲートは #4105 まで、判定のステップの出力（新しい判定は `skip: false`）に頼らず、ゲートの側でも省かない。省いてよい区分の表 `MVV_SKIP_GATES` を設計承認ゲートだけにし、`mvv_skip` はほかの区分を省かない。省いた回答を消費の時点で判定の対象（PR・宛先・宛先の先端）と照合する仕組みは #4106 が持ち、この Issue では区分の条件のほかは今のまま head だけを照合する（決定 9）。

古いバージョンの NDF が書いた状態（更新の前に済ませた判定の `skip: true`・まだ読まれていない `service:mvv` の回答）から再開する実行中のスプリントは扱わない（決定 3。利用者の方針「普及フェーズに入るまで後方互換は不要」、2026-11-16）。実装の PR の `## 移行の手順` に「更新の前にリリース承認ゲートまで進んでいたスプリントは `ndf progress cancel` してから作り直す」を書く。

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
| `validation_rows` | `records/mvv.py` | 新設 | `validation_rows(root: Path) -> tuple[list[dict[str, Any]], list[jsonl.Broken]]` | `mvv-validation.jsonl` を `jsonl.read_rows` で 1 回読み、すべての種類の行と、読めずに除いた行（`jsonl.Broken`。位置 `before` と辞書として読めた中身 `raw` を持つ）を返す。件数だけにしない（再利用の判定 `reusable` が、除いた行の位置とキーで古い「従う」へさかのぼらないため）。ファイルが無ければ（`[]`, `[]`）。`OSError`・`UnicodeDecodeError`・`internal.InternalError`（`read_rows` が新しい形式のバージョンの行で投げる、`kind` が `invalid` でないもの）は呼び出し元へ返す |
| `review_rows` | `records/mvv.py` | 新設（本文のシグネチャを変える） | `review_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]` | `validation_rows` の行から種類 `mvv-review` の行だけを返す純粋な関数。本文の `review_rows(root)` の読み取りを `validation_rows` へ移し、記録の読み取りを判定の再利用と共有する |
| `mvv_validate_sprint` | `records/mvv.py` | 変更 | `mvv_validate_sprint(root, inputs, judge, reuse: bool = True, rows: Optional[Sequence[Mapping[str, Any]]] = None, broken: Sequence[jsonl.Broken] = ()) -> tuple[dict[str, Any], Optional[str]]` | `reuse` が真なら再利用の候補を `rows` から選び、`rows` が None のときだけ今どおり記録を読む。`rows` を渡すときは、同じ読み取りで除いた行 `broken` も渡し、今と同じく `md.reusable(rows, key, [(b.before, b.raw) for b in broken])` に渡す（最後の判定の行が読めないときに、古い「従う」を今回の判定として使わない）。`reuse` が偽なら記録を読まない（今は偽でも読む）。追記の `OSError` は外へ出さず、判定の行と原因を返す（書けたら原因は None）。呼び手は `mvv_gate._judged` だけ（ほかはテスト） |
| `review_holds` | `records/mvv_decide.py` | 新設 | `review_holds(rows: Iterable[Mapping[str, Any]], broken: int, repo: str, prs: Iterable[int], mvv: tuple[int, str]) -> list[str]`。要求仕様のコピーの `mvv: tuple[str, str]` を `tuple[int, str]` に変える（比べる相手の行の `mvv_version` が整数のため） | 本文の規則（2026-11-23 に絞った形）で保留を決める。本文と設計の「今のバージョン」「同じバージョン」は、この文書では（`mvv_version`, `sha256`）の組の一致で判定する（番号だけが同じで `sha256` の違う行は別のバージョン）。(1) レッドライン: キーの最後の `redline` の行より後に同じキーの `fixed` が無ければ保留（後の `fixed` は head・`--state-dir`・バージョンによらず外す）。(2) 見送り: （`mvv_version`, `sha256`）の組が `mvv` と一致する `deferred` のうち、`defer` が `needs_user`・区分が無い・知らない値の行があり、後に同じキーで（`mvv_version`, `sha256`）の組も `mvv` と一致する `fixed` が無ければ保留。`defer` が `filed`・`not_applicable` の行は保留にしない。(3) キーの最後の行が、（`mvv_version`, `sha256`）の組が `mvv` と一致する `withheld: true` の行なら、終わり方によらず「PR #N の <項目>（記録の自由記述が未完了）」で保留する（#4090・#4099 の決定 7・16。記録を読めないときと同じ扱い）。承認資料には、伏せた行から来る理由・本文の代わりに `WITHHELD` の文を載せる。`deferred` の保留は「PR #N の <項目>（deferred・<区分>）: <理由>」の形にする。キーごとに返す保留は多くても 1 件で、優先順は (1) レッドライン、(3) 伏せた行、(2) 見送りである（本文の「見送り: 保留していないキーで」と同じく、上の規則で保留したキーは下の規則で数えない）。(1) は「PR #N の <そのキーの `redline` の行の項目の和>（redline）: <最後の `redline` の行の `text`>」で、最後の `redline` の行が伏せた行なら本文の代わりに `WITHHELD` の文。(2) の区分と理由は、保留の元になった行（今のバージョンで `needs_user`・区分が無い・知らない値の `deferred` のうち最後の行）から採り、その後ろの `filed`・`not_applicable` の行の理由で置き換えない。例: 同じキーに、今のバージョンの `deferred`（`needs_user`、理由 A）→ `deferred`（`filed`、理由 B）→ 後に `fixed` の無い `redline`（本文 T）とあれば、返すのは「PR #N の <項目>（redline）: T」の 1 件だけ。同じ記録から `redline` を除けば「PR #N の <項目>（deferred・needs_user）: A」の 1 件 |
| `check_review_holds` | `progress/gate/checks.py` | 新設 | `check_review_holds(f: MvvFacts) -> Optional[str]` | `f.review_holds` が空でなければ「レビューの保留: <保留を / でつないだもの>」 |
| `MvvFacts` | `progress/gate/checks.py` | 変更 | 欄 `review_holds: tuple[str, ...] = ()` | — |
| `Journal.apply_fix` | `crossreview/journal.py` | 変更 | シグネチャは同じ | 修正担当の結果の `findings[]` の `defer`（`needs_user`・`filed`・`not_applicable`）を、`deferred` の行に `reason` とともに持つ。知らない値・無い値は置かない（`record_review` が区分の無い行として書き、`review_holds` が保留にする） |
| `_fix_prompt`（結果の形の行） | `crossreview/drive.py` | 変更 | シグネチャは同じ | 修正担当へ渡す結果の形に `"defer": "needs_user|filed|not_applicable"`（`outcome` が `deferred` のときだけ）を足す |
| `out_of_scope`・区分の説明 | `contracts/data/review-criteria.json` | 変更 | — | 見送るときの区分の選び方を書く。`needs_user`: 利用者の判断が要る（方針・範囲・レッドラインに当たる選択、書き込みの前に承認が要る起票）。`filed`: 範囲外として起票し、番号を理由に書いた。`not_applicable`: 指摘の前提が違う・基準に当たらない・意図どおり |
| `finish` | `crossreview/finish_decide.py` | 変更 | `finish(review, handed, record_failed: Optional[Mapping[str, Any]] = None) -> Verdict` | 本文の規則のとおり（`mvv_record_failed` を最優先） |
| `ReviewWrite` | `records/mvv.py` | 新設 | `NamedTuple(written: int, missing: tuple[Mapping[str, Any], ...], cause: Optional[str])` | `record_review` の結果。`missing` は書けなかったか試さなかった必須の行（`redline`・`deferred`）、`cause` は最初の失敗の原因（ロックの取得・既存の行の読み取り・追記のどれか。失敗が無ければ None） |
| `record_review` | `records/mvv.py` | 変更（#4076 の契約を変える） | `record_review(repo: str, pr: int, head: str, snapshot: Snapshot, rows: Sequence[Mapping[str, Any]], clean: Optional[Callable[[Sequence[str]], Sequence[str]]]) -> ReviewWrite` | 引数は今のコードのまま変えず、返り値だけを `int` から `ReviewWrite` に変える。`repo` は呼び手が解決済みの `repo_key` の値、MVV の識別子は `snapshot.identity` から取る。呼び手は `crossreview/drive.py` の `_record_mvv` だけ（ほかはテスト）。下の手順。伏せた行（`withheld: true`）の追記の失敗も、その行が必須の行なら `missing` に載せる |

- 接ぎ目（`mvv_gate._judged` の手順 (5)。`review_prs`）: `prs` は `design` では判定する PR の 1 件。`release` では空で、保留を読まない。リリースに含まれる PR の範囲はブランチ戦略ごとに決め方が違い、承認資料と同じ確定した集合から読む仕組みは #4105 が持つ。それまでリリース承認ゲートは `fast`・`auto` でも省かない（`_judged` の手順 (8)、決定 3）ので、保留を読まなくても省かれる経路は無い。承認資料の「MVV 判定」の欄には「リリースの保留は #4105 まで読まない（省かない）」を載せる。API の呼び出しは増えない。`mvv` は `snap` の（`version`・`sha256`）で、行の `mvv_version`（整数）・`sha256` と比べる（行の `version` は行の形のバージョンで、MVV のバージョンではない）。`repo` は `mvv.repo_key(root)`。`validation_rows` が `OSError` か `UnicodeDecodeError`（`jsonl.read_rows` が不正なバイトで投げる。`OSError` の子でない）か `internal.InternalError`（新しい形式のバージョンの行。新しい版の NDF が書いた記録を読む構成で起きる）を投げたら、どれも判定を止めず、保留を「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」の 1 件にし、手順 (7) は記録を読まずに再利用を省いて判定する（要求仕様の異常系の表の `review_rows` の行が挙げる `OSError` は、`validation_rows` が投げる上の 3 つの例外（`OSError`・`UnicodeDecodeError`・`internal.InternalError`）に読み替える）。読めたときは、同じ行を `review_rows` → `review_holds`（除いた行は件数 `len(broken)` を `broken` に渡す）と手順 (7) の `mvv_validate_sprint(rows=…, broken=…)` の両方へ渡す（記録のファイルを読むのは判定 1 回につき 1 回）。結果を `MvvFacts.review_holds` に入れ、`gate_outcome` の `holds` にも同じ値を渡す（承認資料と PR のコメントは同じ結果を読む）
- `record_review` は `deferred` の行に、ジャーナルの行の `defer` を書く（無ければ書かない）。`defer` は自由記述でないので、伏せた行（`withheld: true`）にも書く。`mvv-validation.schema.json` は知らない欄を拒むので、欄 `defer` を足す
- `record_review`（#3959 の本文の「記録の欠落」の書き込み元の契約。#4076 の `-> int` と `OSError` の契約を置き換える）: (1) 書く行を、必須の行（`redline`・`deferred`）を先に、ほか（`fixed`・`unresolved`）を後に並べる（同じ種類の中は今の順）。重ねない判定は今のコード（`mvv_decide.review_recorded`）の規則に `defer` の比較だけを足す（`_REVIEW_STATE` に足し、通常の行と伏せた行の両方の比べ方に入れる）。ここに全項目を書く: `mvv-validation.jsonl` は、同じ（repo・PR・key）の最後の行（#4076 の「最後」の決め方）と（`head`・`outcome`・`mvv_version`・`sha256`・`defer`・`item`・`reason`・`text`・`withheld`）がすべて同じときだけ書かない（`defer`・`item`・`reason`・`text` は持たない行どうしでは無いもの同士として同じとみなす。`withheld` は無い行を偽と読む）。この比べ方に、伏せた行の 2 つの規則を加える: (a) 書く行が伏せた行（`withheld: true`）で、最後の行が伏せていない行かつ（`head`・`outcome`・`mvv_version`・`sha256`・`defer`）が同じなら書かない（分かっている理由を伏せた行で上書きしない。`defer` が変わっていれば書く）。(b) 書く行が伏せていない行で、最後の行が伏せた行なら、ほかの項目が同じでも書く（記録の復旧。これで `review_holds` の 3 つ目の規則の保留が解ける）。同じキーでもバージョンか `sha256` が違えば書く（前のバージョンの `deferred` の行は今のバージョンの保留にならないので、今のバージョンでも見送ったら今のバージョンの行が要る）。`mvv-signals.jsonl` は（repo・PR・key・`mvv_version`・`sha256`）が同じ `review_disputed` の行があれば書かない（伏せたかによらない）。(2) ロックの中で、まず書く行をすべて重ねない判定にかけ、追記する行の並び（追記の対象）を確定してから、その並びを 1 行ずつ追記する。重ねない判定と追記を 1 行ずつ交互にしない（交互にすると、追記の失敗の後ろの行が記録に既にあるかを確かめていない）。保存先の決定・ロックの取得・既存の行の読み取り・重ねない判定・追記（今の `_append_missing` の全体）を 1 つの失敗の範囲とし、`OSError`（ロックの時間切れを含む）・`UnicodeDecodeError`・`InternalError`・`IdentityError`・`MvvError` が出たらそこで止め、例外を外へ出さない。`mvv-validation.jsonl` で失敗したら `mvv-signals.jsonl` は試さない。(3) 必須の行のうち記録に無いものを `missing`、原因を `cause` にして返す。追記の前（保存先・ロック・読み取り・判定）で失敗したときは、どの行が記録にあるかを確かめられないので、書こうとした必須の行をすべて `missing` にする。追記で失敗したときは、確定した追記の対象のうち、書けなかった行とその後ろのまだ追記していない行で、必須のものだけを `missing` にする（重ねない判定で追記の対象から外した行は記録にあるので入れない）。必須の行を先に書くので、通常の行の追記の失敗は `missing` を作らない。`mvv-signals.jsonl` の失敗も `missing` を作らず、`cause` だけを返す。呼び手の `_record_mvv` が `repo` を解けずに `record_review` を呼べないときも、今の MVV で判定した指摘の必須の行をすべて書けなかったと扱う。`_summary` は `missing` が空でなければ `Outcome.mvv_record_failed` に `{"rows": [<key と outcome>], "cause": cause}` を入れ、空で `cause` があれば今どおり注意に留める。打ち直すと、重ねない判定で書けた行は飛ばし、欠けた行だけを書く
- 保留は判定を止めない。LLM の判定の後の `may_skip` でだけ効く（決定 1）
- 承認資料: 「MVV 判定」の欄の出所を、今回の判定の出力 `outputs.mvv_gate`（`verdict`・`why`・`holds`・`noted` と判定の中身）にする（下の「手順書」の「承認資料の出所」）。プランの経路はステップの承認資料の生成元 `progress/gate/asking.py` の `material` がこの欄を組み、supervisor の経路は `approval-request.md` の手順が同じ載せ方で組む。規則を手順書にコピーせず、出力の値を載せる

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_holds` | 本文の「テストで落とす壊し方」の表のとおり（絞った後の形）。上の関数の仕様の例（`needs_user` → `filed` → 直っていない `redline`）→ 「（redline）: T」の 1 件。`redline` を除いた同じ記録 → 「（deferred・needs_user）: A」の 1 件 | `redline` → `fixed` → 外す。`redline` → `fixed` → `redline` → 保留。`deferred`（`filed`）・`deferred`（`not_applicable`）だけ → 空。`deferred`（`needs_user`）→ 保留。区分の無い `deferred`・知らない区分 → 保留。`mvv_version` が同じで `sha256` だけが違う行: `deferred`（`needs_user`）の後の `fixed` の `sha256` が違う → 保留のまま。`deferred`（`needs_user`）の `sha256` が違う → 空。最後の `withheld: true` の行の `sha256` が違う → 伏せた行の保留は無い。`redline` の後の `fixed` の `sha256` が違う → 外す（レッドラインはバージョンによらない） | `broken` が 1 以上 → 読めない行の件数を 1 件 |
| `_judged`（保留） | `design` は設計 PR の行を読む。`release` は保留を読まず、`fast`・`auto` でも `skip` は偽で `why` に #4105 の理由。保留があると `fast` で「従う」でも `skip` は偽、`why` と `holds` に載る。記録を読めるとき、`jsonl.read_rows` が判定 1 回で 1 回だけ呼ばれ、同じ材料の「従う」の行があれば LLM を呼ばない | 記録のファイルが無い → 保留は空 | `validation_rows` の `OSError` → 読めない理由の 1 件。不正なバイト（`b"\xff\xfe"` を含む行）を書いた実際のファイルで、`mvv_validate_sprint` をスタブにせず実物を通す → 例外で止まらず、LLM のスタブが 1 回呼ばれ（同じ材料の「従う」の行が記録にあっても再利用しない）、判定の行が追記され、`holds` と PR のコメントに読めない理由の 1 件、`fast` でも `skip` は偽。同じファイルで追記も `OSError` にする → `verdict` は判定のまま・`skip` 偽・`holds` に読めない理由・`why` に記録に書けない理由。新しい形式のバージョンの行を含む実際のファイル → 例外で止まらず、`holds` に読めない理由の 1 件、再利用せず LLM のスタブが 1 回、`skip` 偽。同じ材料の「従う」の行の後ろに、同じキーの壊れた行（形の違う行）がある実際のファイルで、`gate` から `mvv_validate_sprint` をスタブにせず実物を通す → 保留に読めない行の 1 件、古い「従う」を再利用せず LLM のスタブが 1 回呼ばれ、出力と PR のコメントの判定は今回のスタブの判定（`_judged` が `mvv_validate_sprint` に `broken` を渡さないと、このテストが落ちる） |
| `mvv_validate_sprint` | `rows` を渡すと記録を読まずにその行から再利用する | `reuse=False` → 記録を読まない（読めないファイルでも止まらない） | 追記の `OSError` → 例外を出さず、判定の行と原因を返す |
| `record_review` | 全部書ける → `missing` 空・`cause` None | 渡した順が `fixed`・`redline` でも `redline` を先に書く。2 行目（通常の行）で失敗 → `missing` 空・`cause` あり。1 行目（必須の行）で失敗 → `missing` に失敗した行と後ろの試さなかった `deferred`。先頭の必須の行の追記が失敗し、後ろの必須の行は同じ状態で既に記録にある → `missing` は先頭の行だけ（既に記録にある行を欠落と報告しない）。`mvv-signals.jsonl` だけが失敗 → `missing` 空・`cause` あり | 既存の `mvv-validation.jsonl` が不正な UTF-8（`b"\xff\xfe"` を含む行）で、`redline`・`deferred`・`fixed` を渡す → 例外を出さず、`written` 0・`missing` に `redline` と `deferred`・`cause` あり、ファイルは変わらない。同じファイルで `fixed` だけを渡す → `missing` 空・`cause` あり。ロックの取得の `OSError`、新しい形式のバージョンの行の `InternalError` → 同じく必須の行がすべて `missing`。失敗の後に打ち直す → 書けた行を重ねず、`missing` の行だけを書く |
| `record_review`（重ねない判定） | 同じキー・同じ head・同じ終わり方・同じバージョン・同じ `sha256`・同じ `defer`・同じ項目・同じ理由の最後の行がある → 書かない | 前のバージョンで `deferred` の最後の行があるキーを、今のバージョンでも同じ理由で `deferred` にする → 今のバージョンの行を書き、`review_holds` が今のバージョンの保留を返す。同じ head・同じ終わり方で理由だけが変わる → 書く。`sha256` だけが変わる → 書く。同じ head・同じ理由の `deferred` で `defer` だけが `filed` から `needs_user` に変わる → 通常の行も伏せた行も書き、`review_holds` が保留を返す。最後の行が伏せた行で、同じ（head・終わり方・バージョン・`sha256`・`defer`）の伏せていない行を書く → 書き、`review_holds` の「記録の自由記述が未完了」が消える。最後の行が伏せていない行で、同じ（head・終わり方・バージョン・`sha256`・`defer`）の伏せた行を書く → 書かない | — |
| `finish`・`cmd_run` | 本文の表のとおり | 同 | 同。既存の `mvv-validation.jsonl` が不正な UTF-8 で `redline` がある → 収束の判定でも `STOPPED`・`mvv_record_failed` |

## 根拠の行の確かめ（`cite`）

満たす決定: 決定 4、受け入れ条件 7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Cited` | `records/mvv.py` | 新設 | `NamedTuple(errors: list[dict[str, Any]], checked: int)` | `cite` の結果。`errors` は `citation_errors` の誤り、`checked` は確かめた根拠の行の数（誤りが 0 件でも数える） |
| `cite` | `records/mvv.py` | 新設 | `cite(snap: Snapshot, files: Sequence[Path]) -> Cited` | 各ファイルの本文に `fenced_lines`（#4090・#4099 の形。内外を `code_lines` で決める）と `decision_lines` を当て、フェンスの外の行ごとに（パス・行の番号・本文・決定の根拠の行か）の 4 要素を作って `citation_errors` に `{snap.version: snap.items}` とともに渡す。4 番目は `decision_lines(text)` に行の番号があるかで決め、`False` を決め打ちしない（cross-review の `_citations` と同じ関数・同じ規則）。`checked` は、4 要素を作る同じ走査で、`citation_errors` が根拠の行として読む行（`parse_citation` が引用か形の誤りの候補を返す行。`citation_errors` と同じ関数で数え、別の規則を作らない）を数える。`citation_errors` の返り値から逆算しない（誤りの無い行は返り値に現れない）。`snap.state` が `absent`・`unapproved` なら `Cited([], 0)`（確かめるバージョンが無い）。`mismatch` の分岐は持たない（`current` が先に `MvvError` を投げる） |
| `_mvv_cite` | `records/cli.py` | 新設 | `_mvv_cite(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `--base` から #4076 の CLI の `_mvv_current` と同じく取得して `current` を呼び、`cite` の結果を、下の「伏せ方」を通してから結果 JSON にする |

- 事前条件: `files` は UTF-8 のテキスト
- 伏せ方: `citation_errors` の 1 件の `path`・`cited`・`unknown` は承認資料から取った自由記述なので、`_mvv_cite` は結果の `items` と `summary` を組む前に、全件のこの 3 つの欄を 1 つの文字列の並びにして、cross-review の `_summary` と同じ包み（`report_decide.once` で包んだ、cross-review の `_github_hub` の `clean` と同じ `publisher.guarded`）に 1 回で通し、伏せた後の値だけを `items` と `summary` に置く。`path`・`cited` は文字列で、`unknown` は文字列の配列（`Citation.unknown: list[str]`）なので、並びには `unknown` の要素を 1 つずつ文字列として入れ、各件が何要素を入れたかを覚えておく。包みの返した並びを同じ位置と要素数で切り戻し、`unknown` は伏せた後の文字列の元の順の配列に戻す（配列そのものを包みへ渡さない）。生の値は結果にも `warnings` にも載せない。包みが `failure` を持てば、`path`・`cited` は `WITHHELD` の文になり、`unknown` は要素ごとに `WITHHELD` の文にした同じ長さの配列になって（文字列に変えない）、注意「根拠の行の誤りの自由記述を伏せた（参照先の公開範囲を照会できない）: <原因>」を足す（終わり方は誤りの件数で決め、`refused` のまま）。誤りが 0 件なら包みを呼ばない（照会が増えない）。誤りが 1 件以上のときの `guarded` の 1 回の照会（どれも GitHub の GraphQL の `visibilities`。`_mvv_cite` は 1 回の起動で `Publisher` を 1 つだけ作り、`guarded` を 1 回だけ呼ぶ）: (a) 対象のリポジトリ（`repo.resolve_target_repo` で決めた `hub.repo`）の公開範囲を 1 回。結果が `PRIVATE`・`INTERNAL` なら (b) を打たず、伏せるのは秘密の値（`run_log.redact`。公開範囲によらず常に伏せる）だけ。`PUBLIC` か読めない（`UNKNOWN`）なら、秘密の値に加えて、ホームの絶対パス・メールアドレス・手元のアカウント名と、(b) で公開でないと分かった他のリポジトリへの参照を伏せる。(b) 対象が `PUBLIC` か `UNKNOWN` で、3 つの欄に他のリポジトリへの参照（`repo_refs`）があるときだけ、その参照の公開範囲を `github.graphql-batch`（100）件ごとに 1 回。`PUBLIC` でない（`UNKNOWN` を含む）参照を伏せる。したがって GraphQL は、誤りが 0 件なら 0 回、対象が非公開なら 1 回、公開か範囲不明なら 1 回に他のリポジトリへの参照 100 件ごとの 1 回を足した回数。(a) か (b) の失敗は包みの `failure` になり、上のとおり各欄を `WITHHELD` にする。(b) の照会の候補は原文から取るので、`run_log.redact` より前に秘密の値が GitHub へ送られないよう、`Publisher._private_refs` は候補を照会に渡す前に検査する: 候補の参照そのものに `run_log.redact` をかけて変わる（秘密の値か `SECRET_SHAPES` の形を含む）か、原文の中の候補の一致の範囲が秘密の値か `SECRET_SHAPES` の一致の範囲と重なる候補は、`visibilities` に渡さず、公開でない参照として返す。出力の側も、照会の候補の除外と同じ原文の一致範囲を使う。`scrub` は原文の上で、秘密の一致範囲（秘密の環境変数の値と `SECRET_SHAPES`。`run_log` に一致範囲を返す関数 `secret_spans` を足し、`run_log.redact` もそれで置き換える）と非公開の参照の一致範囲を求め、重なる範囲を和にまとめてから 1 回で置き換える。秘密を一部でも含む範囲は秘密の伏せ字にし、参照だけの範囲は `PRIVATE_REF_TEXT` にする。今の `scrub` のように参照を先に置き換えてから `run_log.redact` をかけると、参照の境界をまたぐ秘密の残りの断片が `redact` に一致せず出力に残るためである（参照を先に落とす今の理由の `ssh://git@github.com/...` も、原文の範囲で求めるので同じく落ちる）。照会の回数は、検査で外した候補を数えない 100 件ごとの 1 回になる。この検査は `Publisher` の中に置くので、同じ包みを通る cross-review の `_summary` と Issue・コメントの書き込みも同じ契約になる
- 結果 JSON: 誤りが 0 件なら `ok`、`summary` は「根拠の行の誤り 0 件（MVV バージョン <snap.version>・<ファイルの数> ファイル）」、`metrics` に `mvv_version`・`commit`（`snap.commit`）・`checked`（`Cited.checked`。誤りがあるときも置く）。MVV が `absent`・`unapproved` のときも `ok` で、`summary` は「承認済みの MVV が無いので確かめていない」。1 件以上なら `stopped`・`refused`、`items` に伏せた後の誤り、`summary` は「根拠の行の誤り N 件（最初: <伏せた後の path>:<line> <伏せた後の cited>）」。ベースの取得（`fetch_branch_or_raise` の `GitError`）・`current` と `cite` の `MvvError`（`mismatch` を含む）・ファイルの `OSError`・`UnicodeDecodeError` は `stopped`・`precondition`、`summary` に原因（ファイルの原因はパスを伏せた後の値）
- 承認資料の見出しの契約: 根拠の行のバージョンの欠落（`kind: format`）は、`parse_citation` が決定の根拠の行（`decision_lines` の行）でだけ候補にするので、承認資料の根拠の行も `### 決定 <数>` の見出しの下に置く。`approval-request.md` の「目指す姿の根拠」の欄は、設計文書の決定ごとの根拠の行を、その決定の見出し（`### 決定 <数>: <題>`）をそのまま小見出しにして、その下へ載せる（見出しの無い箇条書きへまとめて載せない）。cross-review の `_citations` と同じ関数・同じ規則で確かめるので、`cite` は見出しの外の行を決定の根拠の行として扱わない。それだけでは、見出しを付け忘れたり崩したりした承認資料の根拠の行が確かめられず、「誤り 0 件」で人へ渡る。そこで `cite` は、承認資料の見出し「目指す姿の根拠」の節（その見出しから、同じ深さの次の見出しまで）に限り、次の 2 つも誤り（`kind: heading`）にする。1 つは、節があるのに `### 決定 <数>` の見出しが 1 つも無いこと。もう 1 つは、節の中で決定の見出しの下に無い行が `根拠:` で始まることである。節の外の行（文中の引用など）は今どおり確かめの対象にしない
- 手順書: `approval-request.md` の「承認資料を組んだら」に `ndf records mvv cite --base <ベースブランチ> <承認資料のパス>` を置き、結果で次のとおり分ける。`ok` なら、結果の `summary` を承認資料の「MVV 判定」の欄の下に「根拠の行の確かめ: <summary>（<ベースのコミット>）」の 1 行として載せてから資料を渡す。`refused` なら資料の根拠の行を設計文書と今のバージョンから書き直して打ち直す（`ok` になるまで資料を渡さない）。`precondition`（ベースの取得・資料の読み取り・ベースの本文と承認の記録のハッシュの不一致）なら、資料を利用者に渡さず、`結果: 停止`（理由は結果の `summary`）で呼び出し元へ返す（確かめていない根拠の行を検査済みとして渡さないため）。`agent-layers.md` の supervisor が守る規則 13 の後ろに、承認資料を返す前にこのコマンドを打ち、上の 3 つの分岐に従うことを足す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `cite` | バージョン 1 を引いた行 → `errors` 1 件。今のバージョンの実在する項目を引いた根拠の行 3 行だけ → `errors` 0 件・`checked` 3。根拠の行の形の誤り（`kind: format`）→ 1 件 | フェンスの中の根拠の行・文中の引用は拾わない。決定の見出しの下の行だけが決定の根拠の行として確かめられる。複数のファイル | MVV が `absent`・`unapproved` → `errors` 空・`checked` 0 |
| `cite`（手順書の形の承認資料） | `approval-request.md` の手順どおりに組んだ承認資料（「目指す姿の根拠」の下に `### 決定 1: <題>` と、その下にバージョンの表記を欠いた `- 根拠: Value 3`）→ `kind: format` の 1 件 | 同じ行を決定の見出しの外（見出しの無い箇条書き）に置いた資料 → 拾わない（`_citations` と同じ規則） | — |
| `_mvv_cite` | 誤り無し → 0・`ok`、`summary` にバージョンと確かめた行の数、`metrics` に `commit` | MVV が `absent` → `ok`・確かめていない旨の `summary` | 誤りあり → 1・`refused`。偽の秘密（環境変数に置いた値）を `cited` と `unknown` とパスに含む不正な根拠の行 → `items` と `summary` のどこにも偽の秘密が無く、包みの `clean` が 1 回だけ呼ばれる。対象が `PRIVATE` → `visibilities` が 1 回だけ。対象が `PUBLIC` で他のリポジトリへの参照が 101 件 → `visibilities` が 3 回（対象 1 回と参照 2 回）。包みの照会が失敗 → 各欄が `WITHHELD`・注意あり・`refused` のまま。対象が `PUBLIC` で、根拠の行の `cited` とパスの `<所有者>/<リポジトリ>` の位置（所有者名とリポジトリ名の両方）に偽の秘密を置く → `items` と `summary` に偽の秘密が無く、`visibilities` のスタブが受けた引数のどれにも偽の秘密が無い（その参照は照会されず伏せ字になる）。偽の秘密を、他のリポジトリへの参照の境界をまたぐ位置（秘密の前半が参照の中、後半が参照の外）に置く → `visibilities` のスタブが受けた引数にも、`items`・`summary` にも、秘密のどの断片も無い（`scrub` の単体でも同じ）。「目指す姿の根拠」の節に決定の見出しが無く、バージョンの無い根拠の行がある → `refused`・`kind: heading`。節の外の文中の引用 → 誤りにしない。読めないファイル・ベースの取得の `GitError`・ベースが `mismatch` → `precondition` |

## プランの外のエントリポイント（`--machine`）

満たす決定: 決定 5・6、受け入れ条件 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `stage_inputs_of` | `progress/command.py` | 新設 | `stage_inputs_of(cwd: Path, machine_id: str, step_id: str) -> dict[str, Any]` | ステートマシンの最新の定義から `step_id` のステップを持つ工程を引き、`machine_context` に `invoke.resolve_inputs` を当てた入力を返す。プランが `NDF_STAGE_INPUT` に渡すものと同じ値だけで、文脈のほかのキー（`design_pr` など）は足さない |
| `_mvv_inputs` | `records/cli.py` | 新設 | `_mvv_inputs(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 区分ごとに入力を決める（下の表）。決められなければ `_InputError`（`invalid`・`refused`） |
| `_env_inputs` | `records/cli.py` | 新設 | `_env_inputs(gate: str, inputs: Mapping[str, Any]) -> dict[str, Any]` | `NDF_STAGE_INPUT` の object を、プランが組むのと同じ規則で確かめる。下の手順 |
| `_mvv_gate` | `records/cli.py` | 変更 | シグネチャは同じ。引数 `--machine <ID>` を足す | `_mvv_inputs` の後に `gate` を呼ぶ |

| 区分 | `NDF_STAGE_INPUT` | `--machine` | 入力 |
| --- | --- | --- | --- |
| `design`・`release` | ある | 無い | 環境変数の JSON を `_env_inputs` で確かめた値（形が違えば `invalid`・`input_missing`） |
| `design`・`release` | 無い | ある | `stage_inputs_of(root, ID, "design-mvv" か "release-mvv")` |
| `design`・`release` | ある | ある | `invalid`（「入力の出所が 2 つある」） |
| `design`・`release` | 無い | 無い | `invalid`（今と同じ文に `--machine` を添える） |

- `_env_inputs` の手順: (1) StageDef は `mode` を使わずに作る。区分の判定のステップを持つ工程の文書の名前（`design` は `design`、`release` は `deploy`。`mvv_gate.py` の表 `GATE_STAGES = {"design": "design", "release": "deploy"}`）で `definition.stage_io(name)` を呼ぶ。`stage_io` は `contracts/workflow/stages/<name>.json` を読み、ステップを持たない StageDef（`name`・`inputs`・`optional_inputs`・`input_ref` だけを工程の文書の値で埋め、`steps=()`）を返す。工程の入力の規則（`metadata.ndf.inputs` と入力の規約の参照）は工程の文書の値でモードに依らず、モードはステップを絞るだけなので、入力を確かめる前に `mode` を読む必要が無い（`{"context": {...}}` のように最上位に `mode` が無い入力でも StageDef を作れる）。(2) `definition.stage_input_errors(stage, inputs)` で誤りを 2 種類に分けて受ける: `schema`（`stage-in` の規約での誤り。object でない・文脈に無いキーがある・値の形が違う。加えて、文脈にあるキーでも工程の `inputs`・`optional_inputs` に宣言されていないキーがある。`resolve_inputs` は宣言したキーだけを渡すので、環境変数の経路でも同じキーの組だけを受ける。除いて通さず拒む）と `missing`（工程の `inputs` のうち object に無いキー）。(3) 優先順は `schema` が先: `schema` が 1 件でもあれば、`missing` によらず `_InputError`（`invalid`、`summary` に `schema` の誤り）。`{"context": {...}}` は文脈のスキーマが `additionalProperties: false` なので `context` のキーで `schema` に当たり、同時に `mode` などが `missing` にも当たるが、`invalid` になる。`schema` が空で `missing` があれば `input_missing`（`summary` に足りないキー）。(4) 誤りが無ければ `inputs` をそのまま返す（プランが渡した入力は `resolve_inputs` の同じ検査を通っているので、同じ値のまま通る）。入力の `mode` のモードがその区分の判定のステップを持たないときは、ここでは断らず、`gate` の手順 (1) が今どおり判定せずに返す（プランはそのモードで判定のステップを起動しないので、この入力が来るのは手で環境変数を組んだときだけ）
- `definition.stage_input_errors(stage: stages.StageDef, obj: Any) -> StageInputErrors`（新設。`NamedTuple(schema: list[str], missing: list[str])`）: `schema` は 2 つの誤りを合わせたものである。1 つは `validate_io(stage.input_ref, obj)` の誤り（規約が無ければ「工程 <name> に入力の規約が無い」の 1 件）。もう 1 つは、`obj` が object のとき、`stage.inputs` にも `stage.optional_inputs` にも無いキーごとの「工程 <name> の入力に宣言されていないキー <k>」である（文脈のスキーマにあるキーでも拒む。`_env_inputs` の手順 (2)）。`resolve_inputs` は宣言したキーだけを取り出すので、プランの経路の合否はこの追加で変わらない。`missing` は `obj` が object のときだけ `stage.inputs` のうち無いキーの「文脈のキー <k> が無い」（object でなければ空。`schema` が先に外れる）。`validate_stage_input` は `schema + missing` を返す（規則は 1 つで、分けて受けたい呼び手だけがこの関数を使う）。`definition.stage_io(name: str) -> stages.StageDef`（新設）: 上の (1)
- `stage_inputs_of` の手順: (1) `store_root_of(cwd)` の保存先を開き、1 つの読み取りのトランザクションで、最初に `store.read_machine(conn, machine_id)` でマシンの行だけを読む。行が無ければ `model.Refused`（「ステートマシン <ID> は無い」）。行の `repo` を起動元の `repo_id(cwd, store_root_of(cwd))` と比べ、違えば、定義もイベントも読まずにそこで `model.Refused`（「ステートマシン <ID> はこのリポジトリのものでない」）を返す。拒否の文には行の `repo`（別のリポジトリのリモートの URL か、リモートの無いリポジトリのメインディレクトリの絶対パス）を載せず、起動元の `repo_id` も載せない（共有する DB を使う別のリポジトリの呼び手に、所属を漏らさない。無い ID と区別できない文にはしないが、所属は含めない）。(1') 一致したときだけ `read_current(conn, machine_id)` で定義とイベントを読む。`NDF_PROGRESS_DB` で共有する DB では `read_current` が ID だけで読み、後続の `gate` は起動元の GitHub 接続で PR へ書くので、定義と文脈を読む前に照合する（比べる値は `--carry` の `_check_carry` と同じ `repo`。ただし `_check_carry` と違い、拒否の文に相手の `repo` を出さない）。(2) `snapshot.stages` から、ステップの id が `step_id` の工程を引く。無ければ `model.Refused`（「ステートマシンのモードは承認ゲート <区分> を持たない」）。(3) `invoke.resolve_inputs(stage, machine_context(current))`。入力のキーが文脈に無ければ `StageInputMissing` をそのまま返す（プランと同じく判定しない）。文脈の `design_pr` は足さない: 設計の工程の入力（`design.json`）に無いので、足すとプランの経路と判定する PR が分かれる。どちらの経路も `pr_number` が `worktree` のブランチの開いた PR を選ぶ
- CLI の例外の扱い: `model.Refused` は `stopped`・`refused`、`StageInputMissing`・`StageInputInvalid` は `stopped`・`input_missing`・`invalid`（`exit-codes.md` の既存の種類）
- 判定のステップの id（`design-mvv`・`release-mvv`）は工程の定義（`contracts/workflow/stages/design.json`・`deploy.json`）の値で、区分 → ステップの id の表は `progress/model.py` の `MVV_GATE_STEPS = {"design": "design-mvv", "release": "release-mvv"}` の 1 か所に置き、`mvv_gate.py` の `GATE_STEPS` はそれを読む（`progress` は `records` を読まないので、`asking.material` も同じ表を `model` から読む）。`mvv_gate.py` には 区分 → そのステップを持つ工程の文書の名前の表 `GATE_STAGES = {"design": "design", "release": "deploy"}` を置く。定義の id を変えたら表も変える（テストで、`GATE_STAGES` の工程の文書の中に `GATE_STEPS` の id のステップがあることを縛る）
- 手順書: `agent-layers.md` の supervisor が守る規則に次の 2 つを足す。
  - 設計承認ゲート（`結果: 承認ゲート`）を返す前に、作業場所で `ndf records mvv gate --gate design --machine <ステートマシンの ID>` を打ち、結果の `outputs.mvv_gate` を設計の工程の出力の `mvv_gate` へそのまま載せ、承認資料の「MVV 判定」の欄にも同じ値を載せる（下の「承認資料の出所」）
  - リリース承認ゲート（プランを使わず、本番に反映される操作の前で `結果: 承認ゲート` を返す手動の経路。フェーズの表の「仕上げ」など）を返す前に、`ndf records mvv gate --gate release --machine <ステートマシンの ID>` を打つ（文脈の `pr` がスプリントの PR）。結果の `outputs.mvv_gate` は承認資料の「MVV 判定」の欄にだけ載せ、工程の出力へは載せない（#4105 までリリース承認ゲートは省かないので、ゲートが読む出力が要らない）。結果が `ok` でないとき（ステートマシンが無い・モードが `release-mvv` を持たない `refused`、文脈に `pr` が無い `input_missing`）は、欄に「判定できない: <結果の summary>」を載せて承認ゲートを返す（止まらずに人へ問う）。プランの経路（`release-mvv` を持つ本番のリリースプラン）では打たない（判定のステップが打つ）`pace.md` の「MVV 判定」の表から順 2（進め方）を外し、「進め方は省くかだけを決める」と `--machine` の行を足す。`project-mvv.md` の「工程での読み方」（#4076 が足す節）の承認ゲートの行から `pace.md` を指す

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
| `skills/development-workflow/references/approval-request.md` | 「MVV 判定」の行の出所を、進め方によらず今回の判定の出力 `outputs.mvv_gate` にし（下の「承認資料の出所」）、保留（`holds`）を足す。`pace: normal` は助言、の注記を消す。「目指す姿の根拠」の欄は決定の見出し（`### 決定 <数>: <題>`）の下へ根拠の行を載せる形にする（上の「根拠の行の確かめ」の「承認資料の見出しの契約」）。承認資料を渡す前に `ndf records mvv cite` を打ち、`ok`・`refused`・`precondition` で分ける手順を足す |
| `skills/development-workflow/references/agent-layers.md` | supervisor が守る規則に、設計承認ゲートとリリース承認ゲート（手動の経路）を返す前の `--machine` の判定と、結果を載せる先と、`cite` を足す（上の「プランの外のエントリポイント」の手順書） |
| `skills/development-workflow/references/project-mvv.md` | 「工程での読み方」の承認ゲートの行（判定はどの進め方でも打つ） |
| `docs/contracts/commands.md` | `ndf records mvv gate` の `--machine` と、`ndf records mvv cite` の行 |
| `docs/glossary/glossary.json` | 語「レビューの保留」を追加、「MVV 判定」の意味を変え、`ndf records glossary render` で `docs/glossary.md` を作り直す |

承認資料の出所（プランの経路は `progress/gate/asking.py` の `material` が、supervisor の経路は `approval-request.md` の手順が、同じ規則で組む）:

- 出所は今回の判定の出力 `outputs.mvv_gate` だけにする。プランの経路は、待っている工程の判定のステップ（`design-mvv`・`release-mvv`）の済んだ記録（`step_recorded` の report の `outputs.mvv_gate`。`ndf progress show` のその行の `outputs.mvv_gate` と同じ値。文脈 `v.context()` の `mvv_gate` は工程が終わるまで書かれず、前の工程の値が残るので読まない）、supervisor の経路は自分が打った `--machine` の結果の `outputs.mvv_gate` を読む。PR のコメントは出所にしない（差分を読む間に head が動いた・読み直しに失敗した・判定を打てなかった・コメントの投稿に失敗した終わり方では今回のコメントが無く、PR には前回の判定のコメントが残るため）
- 載せ方: `verdict` があれば判定・理由・根拠の項目・レッドライン、null なら「判定できない: <`why`>」。どちらでも `why` の省かない理由を 1 件残らず載せる。保留は `holds` の値で 3 通りに分ける: 空でない配列なら 1 件残らず載せる、空の配列 `[]` なら「レビューの保留: 無し（記録を読んで確かめた）」、`null` かキーが無いなら「レビューの保留: 未確認（<理由>）」。理由は次の順で決め、`why` が空でも資料を組める（空の `why` の先頭を参照しない）: 区分が `release` なら「リリースの保留は #4105 まで読まない（省かない）」。`verdict` が null なら「保留を読む前に判定が止まった: <`why` の先頭>」（`why` が空なら「保留を読む前に判定が止まった」だけ）。`verdict` があれば「この判定は保留を読んでいない」（途中で止まったのではない。実装計画 `issues/issue-3959-plan.md` のレビューの単位 2（判定の条件の分離）が入り、単位 3（レビューの保留を読む）がまだ入っていない間は、`_judged` が保留を読まずに正常な判定を返す。そのとき `fast` で検査がすべて通れば `why` は空）。単位 3 が入った後の `design` では、`holds` が null になるのは (5) より前で `verdict: null` にした終わり方だけで、どれも `why` に理由を足す。未確認を「無し」と書かない。`noted: false` なら「今回は PR に判定のコメントを載せていない。PR に既存の判定のコメントがあっても、今回の結果はここで確かめる」を添える。前回の判定が PR にあるとは書かない（承認済みの MVV が無いリポジトリでは、初回から一度もコメントを書かない）。書かなかった理由（検査 1 が外れた・投稿に失敗した）は `why` の載せ方で伝わる。`outputs.mvv_gate` が無い（判定のステップが済んでいない・`--machine` が `ok` でない）ときは「判定できない: <理由>」と「レビューの保留: 未確認」を載せ、PR のコメントから補わない
- 承認資料の開示の範囲: 「MVV 判定」の欄の自由記述は、判定した PR の中身を既に読める場所にだけそのまま置き、PR を読めるかを確かめられない書き先へは伏せずに出さない。承認ゲートで答える人が GitHub でその PR を見られるかは、どの経路でも確かめない。代わりに、資料を置く場所で開示の条件を決める。欄に載る自由記述は 2 種類ある。判定の理由とレッドラインは、この PR の差分と MVV を LLM が読んで書いたものである。保留は、この PR の cross-review の記録から作られ、見送りの理由とレッドラインの指摘本文は、記録に書く時点で `record_review` の `clean` が公開範囲を照会して伏せてある（照会できなければ伏せた行で、`WITHHELD` の文になる）。経路ごとの条件は次の表のとおりである

| 経路 | 資料を置く場所・渡す先 | その場所で既に読めるもの | 欄の自由記述の扱い |
| --- | --- | --- | --- |
| プランの経路（`write_material`・`presentation_path`） | 判定を打った実行環境の、リポジトリの Git ディレクトリの中（`material_path` の `<Git ディレクトリ>/ndf/state/progress-work/<ステートマシンの識別子の sha256 の頭>/` の下） | 同じ Git ディレクトリに、判定した PR のブランチ（差分の全体）と、PR を読んだ GitHub の認証がある | そのまま載せる。このファイルを読める人は、同じ場所で PR の差分そのものを読めるので、新しく開示する情報は無い |
| supervisor の経路（`approval-request.md`） | 同じ実行環境で、呼び出し元へ返す資料 | 同上（supervisor は判定した PR の作業場所で動く） | そのまま載せる。理由は同上 |
| PR のコメント（`_recorded`） | GitHub の PR | PR を見られる人 | `Publisher.guarded` が書き先の公開範囲を照会して伏せる。照会できなければ公開の書き先と同じに伏せる（上の `_recorded`） |
| 上のほかの書き先（実行環境の外のファイル・チャット・メールなど） | 今は無い | 確かめられない | 載せない。経路を足す変更の中で、自由記述を `Publisher.guarded` と同じ包みに通すか、`WITHHELD` の文に置き換える処理を入れ、受け手が PR を見られるかを確かめられないときは伏せる |

- 承認資料を読む人が実行環境のファイルを読めても GitHub で PR を見られない構成（同じ端末を、PR の閲覧権限の無い人と共有するなど）は、上の表のプランの経路の行の前提を崩す。その構成では資料のファイルより先に Git ディレクトリの PR のブランチが読めるので、資料の伏せ方では防げない。この設計では扱わない

プランの経路の承認資料の生成（`progress/gate/asking.py`。満たす決定: 決定 2、受け入れ条件 1・3・4）:

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `mvv_lines` | `progress/gate/asking.py` | 新設 | `mvv_lines(out: Optional[Mapping[str, Any]], gate: str) -> list[str]` | 上の「載せ方」の規則で「## MVV 判定」の節の行を返す純粋な関数。`out` は、待っている工程の判定のステップ（`design-mvv`・`release-mvv`）の report から `material` が取り出した `outputs.mvv_gate`（無ければ None）。`progress` は `records` を読まないので、`records` の関数を呼ばず値だけを読む |
| `material` | `progress/gate/asking.py` | 変更 | シグネチャは同じ | 質問の区分（`q.gate`）が設計承認・リリース承認で、待っている工程 `q.stage` の定義がその区分の判定のステップ（`model.MVV_GATE_STEPS[q.gate]`。`design-mvv`・`release-mvv`）を持つとき、「## 未確認の点」の後ろに `mvv_lines(out, q.gate)` を足す。`out` は `v.context()` の `mvv_gate` から取らず、`_waiting_context` と同じく待っている工程の済んだステップ（`run.step_progress(v, q.stage).done`。イベント `step_recorded`）から、そのステップの id の report の `outputs.mvv_gate` を直接読む（無ければ None）。工程の出力（`outputs_recorded`）は工程が終わる時にだけ書かれるので、`v.context()` には承認ゲートで待っている工程の判定が無く、リリース承認ゲートでは前の工程（設計）が残した `mvv_gate` を今回の判定と取り違えるため。判定のステップを持たないモードでは節を出さない（今の資料と同じ） |

`write_material` は変えない（書いた資料を書き直さないので、判定のステップが済んだ後に組まれる今の順のまま、資料は今回の判定を載せる）。`presentation_path` で返す資料はこの `material` の出力である。プランの経路では、資料を書く前にも後にも `cite` を打たない。プランが書く資料は `material` がコードで組む定型の文だけで、設計文書の根拠の行を写さず、「目指す姿の根拠」の節も持たないためである。「MVV 判定」の節の根拠の項目も `mvv_lines` が判定の出力から組み、手で書く行は無い。これを下のテストで固定する。将来プランの資料に設計文書の根拠の行を写す変更では、`write_material` の前に `ndf records mvv cite` と同じ確かめを打ち、`refused`・`precondition` なら資料を出さずに工程を止める処理を、同じ PR に入れる。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mvv_lines` | 判定・`why`・保留 2 件 → すべて載る | `holds: []` → 「無し（記録を読んで確かめた）」。`holds: null`（ベースの取得の失敗）→ 「未確認」と止まった理由。`release` の `holds: null` → #4105 の理由。`design` で `verdict` あり・`holds: null`・`why: []`（実装計画の単位 2 が入り単位 3 がまだの間に、`fast` で検査がすべて通った判定）→ 例外を出さず「未確認（この判定は保留を読んでいない）」で、「止まった」と書かない。`verdict: null`・`why: []` → 例外を出さず「保留を読む前に判定が止まった」。`noted: false` → 添え書き | `out` が None → 「判定できない」と「未確認」 |
| `material` | 設計の工程の途中（`design-mvv` の `step_recorded` だけがあり、工程の出力は未記録）の設計承認ゲートの資料に「## MVV 判定」の節があり、その記録の `holds` と `why` が載る | 判定のステップを持たないモード → 節が無い。文脈に設計の工程の `mvv_gate`（`verdict` あり）が残り、deploy の工程の `release-mvv` がまだ済んでいないリリース承認ゲート → 設計の判定を載せず「判定できない」。判定・根拠の項目・保留を持つ `mvv_gate` で組んだ資料を、`cite` と同じ確かめ（`fenced_lines`・`decision_lines`・`citation_errors` と「目指す姿の根拠」の節の検査）に通す → 誤り 0 件 | — |

## 工程の入力のコントラクト

満たす決定: 決定 10、受け入れ条件 9

工程の入力の規則は今 2 つある。コントラクトの `stage-in` と、それで確かめる `definition.validate_stage_input` は `{"context": {...}}` の形を求めるが、本番のコードからは呼ばれていない（呼ぶのは `tests/progress/test_definition.py` だけ）。プランが実際に組むのは `invoke.resolve_inputs` の平らな object で、文脈のスキーマ（`value_errors`）だけで確かめる。規則を `stage-in` と `validate_stage_input` の 1 つにし、プランの経路と `ndf records mvv gate` の環境変数の経路の両方がそれで確かめる。

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
