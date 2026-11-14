# #3483 の詳細設計: 中断した後に PR の head が進んでも、今の head から作り直して続けられる

設計文書 [issue-3483-design.md](issue-3483-design.md) の「構成要素」と「処理の流れ」を、ファイルと関数の粒度へ下ろした文書である。照合の規則の文・不変条件（I1〜I6）・決定（1〜9）は設計文書が持ち、ここでは番号で指す。

この文書で使う語:

| 語 | 意味 |
| --- | --- |
| 置き場 | 1 つの PR の cross-refactoring が作業する `rf<PR>` のディレクトリ。ndf は `<既定の根>/<owner--repo>/rf<PR>`（`--worktree-root` を渡せばその値）、ndf-next は `<置き場の根>/rf<PR>` |
| 退避した先 | 古い起点のために置き場の中身を移した `rf<PR>.retired-<YYYYmmddHHMMSS>` |
| 退避の途中の印 | 退避した先に置くファイル `.ndf-retiring.json`。中身は移す元・作業の起点・PR の head。移し終えたら消す |
| 退避の計画 | 照合の結果、退避すると決まったときに駆動が持つ値（退避した先・作業の起点・PR の head・続きからか） |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/
├── ndf/
│   ├── scripts/lib/
│   │   ├── durable.py            # 最後の実行の回を DBOS を起動せずに読む
│   │   ├── run_state.py          # 退避と、退避した先の片付け
│   │   └── run_state_kinds.py    # 残る状態の一覧へ退避の名前を足す
│   ├── scripts/tests/
│   │   ├── test_durable.py
│   │   └── test_run_state.py
│   └── skills/cross-refactoring/
│       ├── SKILL.md
│       ├── scripts/drive.py      # 照合して、古い起点なら終わらせて退避する
│       └── tests/test_refactor_drive_resume.py *
└── ndf-next/
    ├── src/ndf/domains/crossrefactor/
    │   ├── drive.py              # 照合して、古い起点なら退避する
    │   ├── resume_decide.py *    # 照合の判定（純粋な関数）
    │   └── workspace.py          # 退避と、退避した先の片付け
    └── tests/domains/crossrefactor/
        ├── test_drive.py
        ├── test_resume_decide.py *
        └── test_workspace.py *
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/durable.py` | 変更 | 最後の実行の回の ID・状態・止まりのイベントの有無を、DBOS を起動せずに読む関数を足す | `dbos`（`DBOSClient`）・`durable_keys` |
| `plugins/ndf/scripts/lib/run_state.py` | 変更 | 置き場を退避した先へ移す関数と、退避の途中の印を探す関数を足し、片付けの名前の規則に退避の名前を足す | `run_owner`・`run_state_kinds`・`git` |
| `plugins/ndf/scripts/lib/run_state_kinds.py` | 変更 | 種類 `review-worktree` の置き場の説明に `rf<n>.retired-<時刻>` を足す | 無し |
| `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 変更 | 耐久の記録を開く前に照合し、古い起点なら終わらせて退避し、結果へ 1 件足す | `durable`・`run_state`・`loop_drive`・`refactor_lib.paths` |
| `plugins/ndf/skills/cross-refactoring/SKILL.md` | 変更 | 再開の段落に、古い起点で作り直すことと退避した先の扱いを書く | 無し |
| `plugins/ndf/skills/cross-refactoring/tests/test_refactor_drive_resume.py` | 新設 | ndf の駆動の照合・退避・続きの結合のテスト | `drive`・`crossref_helpers` |
| `plugins/ndf/scripts/tests/test_run_state.py` | 変更 | 退避と、退避した先の片付けの単体のテストを足す | `run_state` |
| `plugins/ndf/scripts/tests/test_durable.py` | 変更 | 最後の実行の回の読み取りの単体のテストを足す | `durable` |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | 照合の判定と、知らせる 1 行・結果の 1 件の文面を持つ純粋な関数 | 無し |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 変更 | 作業場所の用意の前に照合し、古い起点なら退避して空の状態から始め、結果へ 1 件足す | `resume_decide`・`workspace`・`ndf.adapters.github`・`ndf.adapters.git` |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 変更 | 作業場所を退避した先へ移す関数と、退避の途中の印を探す関数を足し、`release` に退避した先の片付けを足す | `ndf.core.converge.worktree`・`ndf.adapters.git` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_drive.py` | 変更 | ndf-next の駆動の照合・退避・続きのテストを足す | `drive`・`_drive_world` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_resume_decide.py` | 新設 | 照合の判定の表のテスト | `resume_decide` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_workspace.py` | 新設 | 退避と、退避した先の片付けのテスト（実際の git の worktree で） | `workspace` |

**依存の向き。** ndf では駆動（`drive.py`）が耐久の記録（`durable.py`）と開始の片付け（`run_state.py`）を呼び、`run_state.py` は `durable.py` を読まない。ndf-next では駆動が照合の判定（`resume_decide.py`）と作業場所（`workspace.py`）を呼び、`resume_decide.py` はどこも import せず、`workspace.py` は `resume_decide.py` を読まない。ndf と ndf-next は互いを import しない（設計文書の関係「別々の道」）。どちらも一方向で、循環は無い。

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/durable.py` | モジュール | `LastRun`（新設）・`last_run`（新設）・`_runs`（新設）・`resolve`（変更: 回の番号の読み取りを `_runs` へ移す。振る舞いは変えない） |
| `plugins/ndf/scripts/lib/run_state.py` | モジュール | `RetireError`（新設）・`_review_key`（新設）・`begin_retire`（新設）・`pending_retire`（新設）・`finish_retire`（新設）・`review_holder`（変更）・`_sweep_review`（変更）・`_sweep_one_review`（変更） |
| `plugins/ndf/scripts/lib/run_state_kinds.py` | モジュール | `KINDS` の `review-worktree` の行の `place`（変更） |
| `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | `Drive` | `run`（変更）・`run_dir`（新設）・`resume_check`（新設）・`retire`（新設） |
| `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | モジュール | `RetirePlan`（新設）・`retire_notice`（新設） |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | モジュール | `Verdict`（新設）・`state_kind`（新設）・`needs_head`（新設）・`decide`（新設）・`notice`（新設） |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | モジュール | `_Run`（変更: 欄 `retired` を足す）・`_open`（変更）・`drive`（変更）・`_resume_check`（新設）・`_pr_head`（新設）・`_contains`（新設） |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | モジュール | `RetireFailed`（新設）・`pending_retire`（新設）・`begin_retire`（新設）・`finish_retire`（新設）・`_move_worktree`（新設）・`_release_retired`（新設）・`release`（変更） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `durable._runs` | `plugins/ndf/scripts/lib/durable.py` | 新設 | `_runs(rows: Iterable[Any], prefix: str) -> dict[int, Any]` | 耐久ワークフローの一覧から `<接頭辞>-<回>` に当たる行を回の番号ごとに引く | `rows` の各行が `workflow_id` を持つ | 戻り値の鍵は回の番号、値はその行。当たらない行は含まない | 無し |
| `durable.last_run` | `plugins/ndf/scripts/lib/durable.py` | 新設 | `last_run(kind: str, identity: str, start: Optional[Path] = None) -> Optional[LastRun]` | DBOS を起動せずに、実行の鍵の最後の実行の回の ID・状態・形の版・止まりのイベントの有無を読む | `launch` の前に呼ぶ（起動中の DBOS を使わない） | 記録のファイルが無い・回が 1 つも無いときは None。それ以外は最大の回の `LastRun` | 記録の SQLite のファイルを読む |
| `run_state._review_key` | `plugins/ndf/scripts/lib/run_state.py` | 新設 | `_review_key(name: str) -> tuple[str, int] \| None` | ディレクトリの名前から、レビュー worktree の種類（`pr`・`rf`）と PR の番号を読む。退避した先の名前は `rf` として読む | 無し | `pr<n>`・`rf<n>`・`rf<n>.retired-<14 桁>` なら `(種類, n)`、それ以外は None | 無し |
| `run_state.begin_retire` | `plugins/ndf/scripts/lib/run_state.py` | 新設 | `begin_retire(rf_dir: Path, mark: dict[str, str], now: float \| None = None) -> Path` | 退避した先を、退避の途中の印を入れた状態で一度に作る | `rf_dir` がある。同じ秒の退避した先が無い | 退避した先があり、その中に印があり、それ以外は何も無い。`rf_dir` は変わらない | ディレクトリの作成・ファイルの書き込み・改名 |
| `run_state.pending_retire` | `plugins/ndf/scripts/lib/run_state.py` | 新設 | `pending_retire(rf_dir: Path) -> tuple[Path, dict[str, str]] \| None` | `rf_dir` と同じ PR の退避した先のうち、印の残るものを探す | 無し | 印の残る退避した先があれば、最も新しいものとその印の中身。無ければ None | ディレクトリの一覧とファイルを読む |
| `run_state.finish_retire` | `plugins/ndf/scripts/lib/run_state.py` | 新設 | `finish_retire(root: Path, rf_dir: Path, dest: Path) -> None` | `rf_dir` に残る中身を退避した先へ移し、空になった `rf_dir` を消し、印を消す | `dest` に印がある。古い実行の回は終わっている | `rf_dir` が無く、`dest` に元の中身があり、worktree の登録は `dest` の下を指し、印が無い。途中で止まれば印が残る | `git worktree move`・ファイルの改名・ディレクトリと印の削除 |
| `run_state.review_holder` | `plugins/ndf/scripts/lib/run_state.py` | 変更 | `review_holder(path: Path \| str, now: float \| None = None) -> str \| None`（変えない） | 持ち主の鍵の名前を `_review_key` で引き、退避した先も元の PR の持ち主で判定する | 変えない | 退避した先の持ち主は `cross-refactoring:pr=<n>` の記録で決まる | 持ち主の記録を読む |
| `run_state._sweep_review` | `plugins/ndf/scripts/lib/run_state.py` | 変更 | 変えない | 置き場の下の `pr<n>`・`rf<n>` に加えて `rf<n>.retired-<時刻>` を片付けの対象にする | 変えない | 退避した先 1 つにつき結果が 1 件以上ある | git の worktree の削除・`gh pr view`（既存の経路） |
| `run_state._sweep_one_review` | `plugins/ndf/scripts/lib/run_state.py` | 変更 | 変えない | 退避の途中の印が残る退避した先を、理由つきで残す | 変えない | 印の残る退避した先は `kept` で、中身は変わらない | git の worktree の削除・`gh pr view`（既存の経路） |
| `Drive.run_dir` | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 新設 | `run_dir(self) -> Path \| None` | init と同じ規則で、この PR の置き場の `rf<PR>` を求める | 対象のリポジトリの中で呼ぶ | `--worktree-root` があればその絶対パス、無ければ `paths.run_root(<owner/repo>, <PR>)`。origin が GitHub でなければ None | `git remote get-url origin` を打つ |
| `Drive.resume_check` | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 新設 | `resume_check(self, identity: str) -> RetirePlan \| None` | 照合の規則の表（ndf の行）を上から当て、退避するなら退避の計画を、しないなら None を返す | `self.root` が決まっている。耐久の記録を開いていない | None なら置き場・状態ファイル・耐久の記録は変わらない。head を取れなければ `Stop(…, ABORT)` で、何も変えていない | 記録の SQLite を読む・`gh pr view` 1 回・`git rev-parse` 1 回・`git merge-base --is-ancestor` 1 回 |
| `Drive.retire` | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 新設 | `retire(self, identity: str, plan: RetirePlan) -> dict` | 印を置き、古い実行の回を取り消し、置き場を移し、印を消し、結果に足す 1 件を返す | `plan` は `resume_check` の戻り値 | 古い実行の回は `CANCELLED`、`rf<PR>` は無く、耐久の記録は開いている。標準エラーに 1 行出ている | ディレクトリの作成・耐久の記録を開く（取り消し）・`git worktree move`・標準エラー |
| `retire_notice` | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 新設 | `retire_notice(dest: Path, work_head: str, pr_head: str) -> tuple[str, dict]` | 標準エラーの 1 行と結果 JSON の 1 件を、設計文書の入出力の契約の形で組む | 無し | 1 行は `↻` で始まり、1 件は `kind` が `run-dir`・`result` が `kept` | 無し |
| `Drive.run` | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 変更 | `run(self) -> dict`（変えない） | 耐久の記録を開く前に `resume_check` を打ち、退避の計画があれば `retire` を打ってから既存の経路へ進み、最初に返す結果の `items` の末尾へ 1 件足す | 変えない | 退避したときは、返す結果の `items` の末尾が退避の 1 件 | 既存の分に `resume_check`・`retire` の分を足す |
| `resume_decide.state_kind` | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | `state_kind(state: Mapping[str, Any]) -> StateKind` | 状態ファイルの中身を `none`（空）・`done`（完了の記録）・`running`（途中）に分ける | 無し | 戻り値は 3 つのどれか | 無し |
| `resume_decide.needs_head` | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | `needs_head(retiring: bool, state: StateKind, has_work: bool) -> bool` | PR の head と祖先の判定を取る必要があるかを決める | 無し | 真になるのは、印が無く、状態が `running` で、`work` がある場合だけ | 無し |
| `resume_decide.decide` | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | `decide(retiring: bool, state: StateKind, has_work: bool, head: Optional[str] = None, contains: Optional[bool] = None) -> Verdict` | 照合の規則の表（ndf-next の 7 行）を上から当てて答えを返す | `needs_head` が真なら `head` と、`head` があれば `contains` を渡す | 戻り値は `SKIP`・`CONTINUE`・`STOP`・`RETIRE`・`RESUME_RETIRE` のどれか | 無し |
| `resume_decide.notice` | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | `notice(dest: str, work_head: Optional[str], pr_head: Optional[str]) -> tuple[str, dict[str, str]]` | 標準エラーの 1 行と `Finish.items` の 1 件を、ndf と同じ文面で組む | 無し | ndf の `retire_notice` と同じ形。起点が無いときは `—` を書く | 無し |
| `_pr_head` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 新設 | `_pr_head(cfg: RunConfig, ports: Ports) -> Optional[str]` | 既存の GitHub のアダプタで PR を 1 回読み、head の commit を返す | 無し | 取れれば 40 字の commit、取れなければ None | GitHub の REST の読み取り 1 回 |
| `_contains` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 新設 | `_contains(work: Path, head: str) -> bool` | `work` の HEAD が `head` を含むかを、`git merge-base --is-ancestor` の終了コード 0 だけで真と読む | `work` がある | 0 なら真、1 と 128 ほかの誤りは偽（決定 2） | `git merge-base --is-ancestor` 1 回 |
| `_resume_check` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 新設 | `_resume_check(cfg: RunConfig, ports: Ports, root: Path, state: dict[str, Any]) -> _Resume` | 照合の事実を集めて `decide` に渡し、答えに従って続ける・止まる・退避するのどれかを行う | 完了の記録の判定（`_reused`）と引数の照合（`_resume_mismatch`）を通った後 | `_Resume` の `stop` があれば何も変えていない（退避の失敗のときは印が残る）。退避したときは `state` が空で、`item` に 1 件 | GitHub の REST の読み取り・`git merge-base`・`workspace` の退避・標準エラー |
| `_open` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 変更 | 変えない | `_resume_mismatch` の後、`workspace.prepare` の前に `_resume_check` を打ち、止まるならその `Finish` を返す | 変えない | 退避したときの `_Run` は空の状態と `retired` の 1 件を持つ | 既存の分に `_resume_check` の分を足す |
| `drive` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 変更 | 変えない | 返す `Finish` の `items` の末尾へ `run.retired` を足す（完了の記録へ保存した後に足す） | 変えない | 退避したときは、止まり・完了のどちらでも `items` の末尾が退避の 1 件。完了の記録の `result` には入らない | 変えない |
| `workspace.pending_retire` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `pending_retire(root: Path) -> Optional[Path]` | `root` と同じ PR の退避した先のうち、印の残るものを探す | 無し | 印の残る最も新しい退避した先か None | ディレクトリの一覧を読む |
| `workspace.begin_retire` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `begin_retire(root: Path, mark: Mapping[str, Optional[str]]) -> Path` | 退避した先を、印を入れた状態で一度に作る | `root` がある | 退避した先と印があり、`root` は変わらない | ディレクトリの作成・ファイルの書き込み・改名 |
| `workspace.finish_retire` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `finish_retire(repo: git.Git, root: Path, dest: Path, pr: int) -> None` | `root` に残る worktree・`logs`・状態ファイルの順に移し、`dest` の worktree に NDF の lock を掛け、印を消す | `dest` に印がある | `root` が無く、`dest` の worktree はすべて NDF の lock を持ち、印が無い。途中で止まれば印が残る | `git worktree unlock`・`move`・`lock`・ファイルの改名・削除 |
| `workspace._move_worktree` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `_move_worktree(repo: git.Git, src: Path, dst: Path) -> None` | NDF の lock を外してから worktree 1 本を `git worktree move` で移す（`--force` を使わない） | `src` は登録された worktree。`dst` は無い | `dst` に登録が移り、lock は外れている | `git worktree unlock`・`git worktree move` |
| `workspace._release_retired` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `_release_retired(repo: git.Git, dest: Path) -> list[str]` | 印の無い退避した先 1 つを、自分のコミットも未コミットの変更も無ければ外し、あれば残す理由を返す | `dest` に印が無い | 外したときは `dest` が無い。残したときは `dest` が変わらず、理由が 1 件 | `git rev-list`・`git status`・`git worktree unlock`・`remove`・ディレクトリの削除 |
| `workspace.release` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 変更 | `release(repo: git.Git, ws: Workspace) -> list[str]`（変えない） | 既存の作業場所の片付けの後に、同じ PR の印の無い退避した先を `_release_retired` で片付ける | 変えない | 戻り値に、残した退避した先の理由が足される | 既存の分に `_release_retired` の分を足す |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `durable.last_run` | 記録を読めない | SQLite が壊れている・開けない | `DBOSClient` の例外 | 呼び出し元へ返す（`DurableError`） | `Drive.run` が `stopped`・`metrics.exit` 1（既存の `launch` の失敗と同じ） | 何も変えていない。直してから打ち直せる |
| `run_state.begin_retire` | 退避した先を作れない | 同じ秒の退避した先がある・書き込みの権限・ディスクの満杯 | `os.rename` と書き込みの `OSError` | 呼び出し元へ返す（`RetireError`）。途中に作った一時のディレクトリは消す | `Drive.retire` が `stopped`・`metrics.exit` 1・`summary` に作れなかったパス | 何も変えていない。打ち直すと同じ照合からやり直す |
| `run_state.pending_retire` | 印を読めない | 印の JSON が壊れている | `json.loads` の `ValueError` | 既定値で続ける（中身を空として、退避の途中として扱う） | 退避の 1 行の起点と head が `—` になる | 退避を続けられる |
| `run_state.pending_retire` | 置き場の親を一覧できない | 権限・ディスクの誤り | `iterdir` の `OSError` | 呼び出し元へ返す | `Drive.run` が `stopped`・`metrics.exit` 1 | 何も変えていない |
| `run_state.finish_retire` | worktree を移せない | 未登録・submodule を含む・移す先がある | `git worktree move` の非ゼロ終了 | 止める（`RetireError` に移せなかったパスと git の 1 行） | `stopped`・`metrics.exit` 1・`summary` に移せなかったパスと退避した先 | 印が残る。打ち直すと移せなかったものから続ける |
| `run_state.finish_retire` | `rf<PR>` を消せない | 移している間に別のものが置かれた | `rmdir` の `OSError` | 止める（`RetireError`） | 同上 | 印が残る。残ったものを確かめて打ち直す |
| `run_state.review_holder` | 持ち主の記録が壊れている | 既存の経路 | `lease.LeaseError` | 既定値で続ける（既存のとおり判定できない理由を返す） | 片付けの結果に `kept` と理由 | 変えない |
| `run_state._sweep_review` | 退避した先 1 つの判定で例外 | 既存の経路 | 既存の `except Exception` | 既定値で続ける（その 1 件を `kept` にし、ほかを続ける） | 片付けの結果に `kept`・`判定できない: …` | 次の片付けでやり直す |
| `run_state._sweep_one_review` | 印の残る退避した先に当たった | 前の退避が途中で止まった | 印のファイルの有無 | 既定値で続ける（外さずに残す） | `kept`・`退避の途中の印が残る（<印のパス>）。同じ PR の cross-refactoring を打ち直すと退避を続ける` | 打ち直しが退避を続けた後に片付けの対象になる |
| `Drive.run_dir` | 置き場を求められない | origin が GitHub でない・origin が無い | `github_repo_from_origin` が None | 既定値で続ける（None。照合しない） | 何も出さない。後の init が既存の誤りで止まる | 変えない |
| `Drive.resume_check` | PR の今の head を取れない | `gh` の認証・通信の失敗・PR が無い | `pr_head` が None | 止める（`Stop(<summary>, ABORT)`） | `stopped`・終了コード 1・`metrics.exit` 4・`summary` は設計文書の入出力の契約の文 | 何も変えていない（I5）。打ち直せる |
| `Drive.resume_check` | `work` の HEAD を読めない | `work` が壊れている・無い | `git rev-parse` の非ゼロ終了 | 既定値で続ける（比べる起点が無いとして古い起点と読む） | 退避の 1 行の作業の起点が `—` | 退避が `work` を移せなければ `finish_retire` の行の扱い |
| `Drive.resume_check` | 祖先の判定が 0 以外 | 1（含まない）・128（head が手元に無い）ほか | `git merge-base --is-ancestor` の終了コード | 既定値で続ける（古い起点と読む。決定 2） | 退避の 1 行 | 退避へ進む |
| `Drive.retire` | 古い実行の回を取り消せない | 記録を開けない・取り消しの後も回が途中のまま | `durable.launch` の `DurableError`・`durable.status` が `PENDING` か `ENQUEUED` | 止める（`Stop(…, 1)`） | `stopped`・`metrics.exit` 1・`summary` に回の ID と退避した先 | 印が残る。打ち直すと取り消しから続ける（I3） |
| `Drive.retire` | 退避が途中で止まった | `begin_retire`・`finish_retire` の `RetireError` | 例外 | 止める（`Stop(…, 1)`） | 上の 2 関数の行のとおり | 上の 2 関数の行のとおり |
| `Drive.run` | `resume_check`・`retire` の止まり | 上のとおり | `Stop` | 呼び出し元へ返す（既存の `dp.main` が結果 JSON にする） | 上のとおり | 上のとおり |
| `_pr_head` | PR を読めない | 通信・認証の失敗・枠の上限・PR が無い・origin が読めない | `github.GitHubError`・`git.GitError` | 既定値で続ける（None を返し、`decide` が `STOP` にする） | 無し（`_resume_check` が出す） | 変えない |
| `_contains` | git を起動できない | git の欠け | 起動の `OSError` | 呼び出し元へ返す（既存の駆動の誤りと同じ扱い） | 既存の種類 `unknown` | 何も変えていない |
| `_resume_check` | `decide` が `STOP` | PR の head を取れない | `Verdict.STOP` | 止める（`Finish("transient", <summary>)`。決定 8） | `stopped`・種類 `transient`・終了コード `infra_wait`・`summary` は ndf と同じ文 | 何も変えていない（I5）。打ち直せる |
| `_resume_check` | 退避が失敗した | `workspace.RetireFailed` | 例外 | 止める（`Finish("refused", <理由と退避した先>)`） | `stopped`・種類 `refused`・`detail` に移せなかったパス | 印が残れば、打ち直すと退避を続ける（I2） |
| `_open` | `_resume_check` の止まり | 上のとおり | `_Resume.stop` | 呼び出し元へ返す（`drive` が `Finish` をそのまま返す） | 上のとおり | 上のとおり |
| `drive` | 退避した後の実行の止まり | 既存の経路 | 既存の `DriveStopped` ほか | 既定値で続ける（止まりの `Finish` にも退避の 1 件を足す） | 既存の止まりの出力の `items` の末尾に退避の 1 件 | 変えない |
| `workspace.pending_retire` | 置き場の根を一覧できない | 権限・ディスクの誤り | `iterdir` の `OSError` | 呼び出し元へ返す | 既存の種類 `unknown` | 何も変えていない |
| `workspace.begin_retire` | 退避した先を作れない | 同じ秒の退避した先がある・権限・ディスクの満杯 | `os.rename` と書き込みの `OSError` | 呼び出し元へ返す（`RetireFailed`）。一時のディレクトリは消す | `_resume_check` の `refused` の行 | 何も変えていない。打ち直すと同じ照合からやり直す |
| `workspace.finish_retire` | worktree・ファイルを移せない | 利用者の lock・移す先がある・git の誤り | `_move_worktree` の `RetireFailed`・改名の `OSError` | 止める（`RetireFailed`） | `_resume_check` の `refused` の行 | 印が残る。打ち直すと残りから続ける |
| `workspace.finish_retire` | lock を掛け直せない | git の誤り | `git worktree lock` の `GitError` | 止める（`RetireFailed`）。worktree は外さない | 同上 | 印が残る。打ち直すと lock の無い worktree に掛け直す |
| `workspace._move_worktree` | 利用者の lock が掛かっている | 理由が NDF の前置きで始まらない lock | `worktree.lock_of` | 止める（`RetireFailed`。外さない） | `detail` に `利用者の lock なので移さない（理由: …）` | 印が残る。利用者が lock を外してから打ち直す |
| `workspace._move_worktree` | 移せない | 移す先がある・git の誤り | `git worktree move` の `GitError` | 止める（`RetireFailed`）。外した lock は掛け直す | 同上 | 印が残る。打ち直せる |
| `workspace._release_retired` | 判定できない | 状態ファイルが無い・壊れている・`branch` が無い・git の誤り | `store.load` の例外・`GitError` | 既定値で続ける（残して理由を返す） | `ndf refactor release` と駆動の結果に残した理由 | 退避した先は残る。人が取り出すか消す |
| `workspace._release_retired` | 自分のコミットか未コミットの変更が残る | 退避した `work` に push していない作業がある | `worktree.own_commits`・`Git.status_paths` | 既定値で続ける（残して理由を返す） | `<退避した先>: 自分のコミットが <件数> 件残る` など | 退避した先は残る（前提 2: 人が取り出す） |
| `workspace._release_retired` | worktree を外せない | 利用者の lock・git の誤り | `worktree.release` の `removed` が偽 | 既定値で続ける（残して理由を返す） | 既存の `release` と同じ形の理由 | 退避した先は残る |
| `workspace.release` | 退避した先の片付けの失敗 | 上のとおり | `_release_retired` の戻り値 | 既定値で続ける（理由を足して返す） | 上のとおり | 上のとおり |

## 処理の手順

ndf の照合（`Drive.resume_check`）。上から当て、最初に当たった行で決める。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `Drive.resume_check` | `run_dir` が None | None（照合しない） |
| `Drive.resume_check` | `pending_retire(rf)` が印の残る退避した先を返す | 退避の計画（`resume=True`。印の中身の起点と head を使う。head を取らない） |
| `Drive.resume_check` | `last_run` が None・最後の回が `PENDING` でも `ENQUEUED` でもない・形の版が `FORMAT` と違う | None（`resolve` が新しく始めるか完了の記録を返す） |
| `Drive.resume_check` | 最後の回にイベント `pause` がある | None（I4） |
| `Drive.resume_check` | 状態の置き場（`known_tmp`）が `rf<PR>` の中に無い | None（環境変数 `CROSS_REFACTORING_TMP_DIR` で置き場を外へ出した実行。移すと状態と `work` が分かれる） |
| `Drive.resume_check` | 状態ファイル（`state_path(known_tmp, PR)`）が無い | None（init の前の中断。init が流れ直して既存の同期が効く） |
| `Drive.resume_check` | `pr_head` が None | `Stop(<summary>, ABORT)` |
| `Drive.resume_check` | `git -C <rf>/work merge-base --is-ancestor <head> <work の HEAD>` が 0 | None（続ける） |
| `Drive.resume_check` | 上のどれにも当たらない（祖先の判定が 0 以外・`work` の HEAD を読めない） | 退避の計画（`resume=False`） |

ndf の退避（`Drive.retire`）は次の順に進む。順は決定 4（取り消してから移す）による。

1. `resume` が偽なら `begin_retire(rf, mark)` で退避した先と印を作る
2. 開いている耐久の記録があれば閉じ、`durable.launch(KIND, identity, keep=())` で開く。`keep` を空にすると `launch` が記録のロックの中で `cancel_others` を打ち、途中の回を子ごと取り消す（決定 3）
3. 1 で読んだ最後の回の ID の状態を `durable.status` で読み、`PENDING`・`ENQUEUED` なら止まる（I3）
4. `finish_retire(Path(self.root), rf, dest)` で残りを移し、印を消す
5. `retire_notice` の 1 行を標準エラーへ出し、1 件を返す

`Drive.run` は 1 件を持ったまま既存の経路（`launched` の確かめ → `resolve` → `start` → `wait`）へ進み、返す結果の `items` の末尾へ足す。2 で記録を開いているため、既存の経路の `launch` は打たれない。

`finish_retire` の移す順:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `run_state.finish_retire` | `rf` の子が登録された worktree（`_listed(root)` にある） | `git worktree move <子> <dest>/<子の名前>` |
| `run_state.finish_retire` | `rf` の子が worktree でないファイルかディレクトリ | `os.rename` で `<dest>/<子の名前>` へ |
| `run_state.finish_retire` | `rf` が無い（前の打ち直しで移し終えた） | 移さずに印を消す |
| `run_state.finish_retire` | 移した後の `rf` が空 | `rf` を消し、印を消す |

ndf-next の照合（`resume_decide.decide`）。上から当てる。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `resume_decide.decide` | `retiring` が真 | `RESUME_RETIRE` |
| `resume_decide.decide` | `state` が `none` か `done` | `SKIP` |
| `resume_decide.decide` | `state` が `running` で `has_work` が偽 | `RETIRE` |
| `resume_decide.decide` | `head` が None | `STOP` |
| `resume_decide.decide` | `contains` が真 | `CONTINUE` |
| `resume_decide.decide` | `contains` が偽 | `RETIRE` |

`state` が `none` で `work` が無い場合（init の前の中断）と、`state` が `none` で `work` がある場合は、どちらも 2 行目の `SKIP` に当たる。設計文書の照合の規則の表の「状態ファイルも `work` も無い」は前者である。

`_resume_check` は、`needs_head` が真のときだけ `_pr_head` と（head が取れたら）`_contains` を打つ。`RETIRE` なら `begin_retire` → `finish_retire`、`RESUME_RETIRE` なら `finish_retire` だけを打ち、どちらも状態を空にして `notice` の 1 件を返す。

`workspace.finish_retire` の移す順は、`work` → `read/<ランタイム>`（名前の順）→ `read` が空なら消す → `logs` → `state.json`（最後）→ `dest` の worktree のうち lock の無いものへ NDF の lock を掛ける → `root` が空なら消す → 印を消す、である。状態ファイルを最後に移すことが、古い実行の回を終わらせることに当たる（決定 4）。

`workspace._release_retired` の判定:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `workspace._release_retired` | `dest/state.json` から `branch` を読めない | 残す（判定できない） |
| `workspace._release_retired` | `dest` の worktree のどれかに `own_commits(<worktree>, refs/remotes/origin/<branch>, HEAD)` が 1 件以上ある | 残す（自分のコミットが残る） |
| `workspace._release_retired` | `dest` の worktree のどれかに未コミットの変更がある | 残す（未コミットの変更が残る） |
| `workspace._release_retired` | 上のどれにも当たらない | worktree を `worktree.release` で 1 本ずつ外し、すべて外れたら `dest` を消す。外れないものがあれば残す |

- 繰り返しの終わる条件: `finish_retire` と `_release_retired` は置き場の子の数（`work`・担当ごとの読み取り用・`logs`・状態ファイル）で終わる。`pending_retire` と `release` の退避した先の一覧は、同じ PR の退避した先の数で終わる
- 計算量の注意: 照合で増える外部コマンドは、中断した実行を続ける回につき、ndf が `gh pr view` 1 回・`git rev-parse` 1 回・`git merge-base` 1 回、ndf-next が GitHub の REST の読み取り 1 回・`git merge-base` 1 回である。`git fetch` は足さない（決定 2・9）。`git rev-parse` は設計文書の非機能の条件の数に入れていない。数える対象は PR の head の取得と祖先の判定で、`work` の HEAD の読み取りは手元の参照を読むだけのためである

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `LastRun` | 型（`id: str`・`number: int`・`status: str`・`app_version: str`・`paused: bool`。性質 `running` は `status in RUNNING and app_version == FORMAT`） | `plugins/ndf/scripts/lib/durable.py` | — |
| `RETIRED_SEP` | 定数（退避した先の名前の区切り） | `plugins/ndf/scripts/lib/run_state.py` | `".retired-"` |
| `RETIRING_MARK` | 定数（退避の途中の印のファイル名） | `plugins/ndf/scripts/lib/run_state.py` | `".ndf-retiring.json"` |
| `RETIRE_STAMP` | 定数（時刻の形。既存の `work.stale-<時刻>` と同じ地方時の 14 桁） | `plugins/ndf/scripts/lib/run_state.py` | `"%Y%m%d%H%M%S"` |
| `_RETIRED_NAME` | 定数（正規表現） | `plugins/ndf/scripts/lib/run_state.py` | `^rf(\d+)\.retired-\d{14}$` |
| `RetirePlan` | 型（`dest: Path \| None`・`rf: Path`・`run_id: str \| None`・`work_head: str`・`pr_head: str`・`resume: bool`） | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | — |
| `ABORT` | 定数（既存。head を取れない止まりの `metrics.exit` に使う） | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | `4` |
| 印の中身 | JSON（`from`: 移す元のパス・`work_head`・`pr_head`。起点が無ければ空文字） | 退避した先の `.ndf-retiring.json` | — |
| `Verdict` | 型（列挙: `SKIP`・`CONTINUE`・`STOP`・`RETIRE`・`RESUME_RETIRE`） | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | — |
| `StateKind` | 型（`Literal["none", "done", "running"]`） | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | — |
| `UNKNOWN_HEAD` | 定数（起点が無いときに 7 字の欄へ書く文字） | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | `"—"` |
| `RETIRED_SEP`・`RETIRING_MARK`・`RETIRE_STAMP` | 定数（ndf と同じ値。ndf-next の唯一の置き場） | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | ndf と同じ |
| `_Resume` | 型（`state: dict`・`item: Optional[dict]`・`stop: Optional[Finish]`） | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | — |
| `_Run.retired` | 欄（退避の 1 件） | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | `None` |

退避の名前と印のファイル名は、ndf と ndf-next でそれぞれ 1 か所に置く。2 つのパッケージは互いを import しないため、値は設計文書の「照合の規則」の節の文で揃える（決定 1）。ndf の `RETIRE_STAMP` は `setup.py` の `work.stale-<時刻>` と同じ形だが、既存の側は文字列を直に書いており、定数への寄せはこの変更に含めない。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| ndf の同じ PR の駆動 2 本 | `rf<PR>`・耐久の記録 | 既存の持ち主の鍵（`main` の `lease_stage.hold`。#2342）。`--worktree-root` を渡した実行は呼び出し元が持つ。取り消しは `launch` の記録のロック（I17）の中で打つ | 照合・退避・取り消しは何度打っても同じ結果になる。取り消し済みの回の取り消しは止める ID が無いだけ |
| ndf の駆動と、開始の片付け・`/ndf:merged`（`run_state._sweep_review`） | 退避した先 | 退避した先は印を入れた一時のディレクトリ（`_REVIEW_NAME` に当たらない名前）を改名して作るため、片付けが印の無い退避した先を見る時点は無い。片付けは印の残る退避した先を外さない（I6） | 印が消えた後の退避した先だけが片付けの対象になる |
| ndf の駆動と、同じリポジトリの別の `git worktree` の操作 | worktree の登録（`.git/worktrees`） | git の登録の書き込みの排他に任せる | `git worktree move` の失敗は印を残して止まり、打ち直しで続ける |
| ndf-next の同じ PR の駆動 2 本 | `rf<PR>`・状態ファイル | 駆動に PR ごとの排他は無い（`grep -n "lease\|flock\|filelock\|exclusive\|hold" src/ndf/domains/crossrefactor/cli.py src/ndf/domains/crossrefactor/drive.py` は `release` の行だけを返した）。既存の状態ファイルの書き込みも同じ前提で、この変更は排他を足さない | 退避は印から続けられるため、片方が途中で止まっても次の打ち直しで揃う |
| ndf-next の駆動と、作業場所の片付け（`release`・`ndf refactor release`） | 退避した先 | 一時のディレクトリの改名で作り、片付けは印の残る退避した先を外さない | 印が消えた後の退避した先だけが片付けの対象になる |
| ndf-next の駆動と、孤児の lock の片付け（`worktree.sweep_orphans`） | 退避した先の worktree の NDF の lock | 移した後のディレクトリは在るため、孤児（ディレクトリが無い）に当たらない。移している間に lock が外れている worktree は孤児の判定の対象外（lock の無い worktree を見ない） | `finish_retire` の最後に lock の無い worktree へ掛け直す |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `Drive.retire` | `↻ 古い起点のため退避し、今の head から新しい実行の回を始める: <退避した先>（作業の起点 <7 字>・PR の head <7 字>）` | 標準エラー（1 回） | トークン・環境変数の値 |
| `Drive.run` | 退避の 1 件（`kind` `run-dir`・`result` `kept`・`reason`） | 結果 JSON の `items` の末尾 | 同上 |
| `Drive.resume_check` | head を取れない止まりの `summary` | 結果 JSON（`stopped`） | `gh` の標準エラーの全文（既存の `call` が標準エラーへそのまま流す分だけが出る） |
| `Drive.retire` | 取り消し・移動の失敗の `summary`（回の ID・移せなかったパス・git の 1 行の先頭 300 字） | 結果 JSON（`stopped`） | 同上 |
| `durable.launch`（既存） | 取り消した回の ID（`[ndf durable] 続けない途中の耐久ワークフローを止めた: …`） | 標準エラー | — |
| `run_state._sweep_one_review` | 印の残る退避した先を残した理由 | 片付けの結果の 1 件 | — |
| `_resume_check` | `notice` の 1 行（ndf と同じ文） | 標準エラー（1 回） | トークン・環境変数の値 |
| `drive` | 退避の 1 件 | `Finish.items` の末尾（完了の記録には入れない） | 同上 |
| `_resume_check` | head を取れない止まり・退避の失敗の `detail` | `Finish`（`transient`・`refused`） | GitHub の応答の本文 |
| `workspace.release` | 残した退避した先の理由 | 戻り値（`ndf refactor release` の結果の `items`） | — |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `durable._runs` | `durable.resolve` の中の回の番号の読み取り | `plugins/ndf/scripts/lib/durable.py` の `resolve` を読んだ | 使う（`resolve` の中の正規表現と辞書を `_runs` へ移し、両方が呼ぶ） |
| `durable.last_run` | `durable.status_from`・`durable.event_from`・`durable.open_client` | `grep -n "def .*_from\|def open_client" plugins/ndf/scripts/lib/durable.py` | 使う（`open_client` で 1 回開き、一覧とイベントを同じ接続で読む）。`status_from` は ID が決まっている前提で、最後の回を探さないため使えない |
| `run_state._review_key` | `run_state._REVIEW_NAME` | `run_state.py` を読んだ | 使う（`pr<n>`・`rf<n>` はそのまま当て、退避の名前だけ `_RETIRED_NAME` を足す） |
| `run_state.begin_retire` | `durable_keys.retire_record`・`setup.py` の `work.stale-<時刻>` | `grep -rn "retired-\|stale-" plugins/ndf/scripts/lib plugins/ndf/skills/cross-refactoring/scripts` | 使えない。どちらも印を置かずに 1 回の改名で済ませる形で、複数の worktree を移す途中の失敗を表せない。時刻の形だけ揃える |
| `run_state.pending_retire` | `run_state._sweep_review` の一覧 | `run_state.py` を読んだ | 使えない（置き場のすべての名前を片付けの判定へ回す関数で、印の有無を返さない）。名前の判定は `_review_key` を共有する |
| `run_state.finish_retire` | `run_state._listed`（worktree の登録の一覧） | `run_state.py` を読んだ | 使う（子が worktree かを見る） |
| `Drive.run_dir` | `refactor_lib.paths.run_root`・`github_repo_from_origin` | `grep -n "def run_root\|def github_repo_from_origin" plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/paths.py` | 使う（init と同じ規則で求める） |
| `Drive.resume_check` | `loop_drive.pr_head`・`loop_drive.call` | `grep -n "def pr_head\|def call" plugins/ndf/scripts/lib/loop_drive.py` | 使う（`gh pr view` 1 回。耐久の記録を開く前なので、耐久ステップの `self.call` でなく `call` を渡す） |
| `Drive.retire` | `durable.cancel_others`（`launch` の `keep`）・`durable.status` | `durable.py` の `launch` を読んだ | 使う（`keep=()` で `launch` に取り消させる。決定 3） |
| `retire_notice` | `run_state._item`（片付けの結果の 1 件） | `run_state.py` を読んだ | 使えない（`reason` を必須にせず、名前を `str` にするだけの部品で、文面を組まない）。形（鍵の並び）は同じにする |
| `resume_decide.state_kind` | `drive._reused` の `"done" in state` の判定 | `drive.py` の `_reused`・`_open` を読んだ | 使えない（判定を純粋な関数へ寄せる側。同じ鍵 `done` を見る） |
| `resume_decide.needs_head` | 無し | `grep -rn "is_ancestor\|resume" plugins/ndf-next/src/ndf/domains/crossrefactor` | 使えない（該当の判定が無い） |
| `resume_decide.decide` | `finished_decide.finished`（完了の記録の判定） | `grep -n "def finished" plugins/ndf-next/src/ndf/domains/crossrefactor/finished_decide.py` | 使えない（完了の記録を返すかの判定で、途中の状態の照合を持たない） |
| `resume_decide.notice` | 無し | `grep -rn "retired\|退避" plugins/ndf-next/src/ndf` | 使えない（該当の文面が無い） |
| `_pr_head` | `Ports.github`・`github.GitHub.pull` の `head_sha` | `grep -n "def pull\|head_sha" plugins/ndf-next/src/ndf/adapters/github.py` | 使う（決定 9） |
| `_contains` | `git.Git.is_ancestor` | `plugins/ndf-next/src/ndf/adapters/git.py` の `_probe` を読んだ | 使えない（終了コード 1 以外を `GitError` にし、128 を古い起点と読めない。決定 2）。`Git.attempt` を使う |
| `_resume_check` | `drive._resume_mismatch`（未完了の状態の照合） | `drive.py` を読んだ | 使えない（引数の比べで、head を見ない）。並べて呼ぶ |
| `workspace.pending_retire` | 無し | `grep -rn "retired" plugins/ndf-next/src/ndf/domains/crossrefactor` | 使えない（該当が無い） |
| `workspace.begin_retire` | 無し | 同上 | 使えない（該当が無い） |
| `workspace.finish_retire` | `worktree.lock_review_worktree` | `plugins/ndf-next/src/ndf/core/converge/worktree.py` を読んだ | 使えない（掛けられなければ worktree を `--force` で外すため、退避した作業を失いうる）。理由の文は `worktree.held_reason` を使う |
| `workspace._move_worktree` | `worktree.lock_of`・`worktree.LOCK_PREFIX` | 同上 | 使う（NDF の lock かを見る） |
| `workspace._release_retired` | `worktree.release`・`worktree.own_commits`・`Git.status_paths`・`store.load` | 同上と `grep -n "def status_paths" plugins/ndf-next/src/ndf/adapters/git.py` | 使う（外す前に作業の残りを見て、外すのは `worktree.release`） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `durable.last_run` | 回 1・2 のうち 2 の ID・状態・`paused` を返す | 記録のファイルが無い・回が 0 件で None。形の版が違う回は `running` が偽 | 壊れた SQLite で `DurableError` |
| `durable._runs` | `<接頭辞>-1`・`-2` を番号で引く | 接頭辞の後ろが数字でない行・子の ID を含まない | — |
| `run_state._review_key` | `pr12`・`rf12`・`rf12.retired-20261115012700` | 13 桁・15 桁の時刻・`pr12.retired-…` は None | — |
| `run_state.begin_retire` | 退避した先に印だけがあり、`rf` は変わらない | 同じ秒の退避した先があると `RetireError` | 書けない親で `RetireError`、一時のディレクトリが残らない |
| `run_state.pending_retire` | 印の残る退避した先を返す | 印の無い退避した先だけなら None。2 つあれば新しい方 | 壊れた印でも退避した先を返し、中身は空 |
| `run_state.finish_retire` | `work`・担当の worktree・ファイルが移り、`git -C <dest>/work rev-parse HEAD` が元の HEAD | `rf` が既に無いときは印を消すだけ。2 回打っても同じ | 移す先がある worktree で `RetireError`、印が残る |
| `run_state._sweep_review` | PR が閉じて作業の無い退避した先を外す | 未 push のコミットが残る退避した先は理由つきで残る | 判定の例外は 1 件の `kept` になる |
| `run_state._sweep_one_review` | 印の無い退避した先を `rf<n>` と同じ判定にかける | 印の残る退避した先は PR が閉じていても残る | — |
| `run_state.review_holder` | 退避した先の持ち主を `cross-refactoring:pr=<n>` で引く | 持ち主が終わっていれば None | 壊れた持ち主の記録で判定できない理由 |
| `Drive.run_dir` | `--worktree-root` の値・既定の `rf<PR>` | origin が GitHub でなければ None | — |
| `Drive.resume_check` | 照合の規則の表の ndf の各行で表と同じ答え | 同じ head・祖先の head で None（受け入れ条件 5）。`pause` のある回で None（6） | head を取れず `Stop` で `metrics.exit` 4、置き場・状態ファイル・記録が変わらない（7）。`gh`・`git merge-base` が 1 回ずつ |
| `Drive.retire` | 古い回が `CANCELLED`、`rf` が無く、1 行と 1 件が出る（3・4） | 印から続ける打ち直しで、取り消し済みの回でも進む | 移動の失敗で止まり、打ち直すと人の操作なしに新しい回へ進む |
| `retire_notice` | 1 行と 1 件が入出力の契約の形 | 7 字に満たない head はそのまま | — |
| `Drive.run` | 中断 → head を進める → 打ち直すと新しい回の `work` の HEAD が PR の今の head（2・9） | 退避した後の回が止まり 23 で返っても `items` の末尾に 1 件 | `resume_check` の止まりが結果 JSON になる |
| `resume_decide.state_kind` | 空・`done` あり・途中で 3 つに分かれる | `done` と途中の鍵が両方あると `done` | — |
| `resume_decide.needs_head` | 印なし・`running`・`work` ありで真 | ほかの組み合わせはすべて偽 | — |
| `resume_decide.decide` | 照合の規則の表の ndf-next の 7 行で表と同じ答え（受け入れ条件 8） | 印と `running` が両方あると `RESUME_RETIRE` | `head` が None で `STOP` |
| `resume_decide.notice` | ndf の `retire_notice` と同じ文面 | 起点が無いと `—` | — |
| `_pr_head` | 偽の GitHub のアダプタの `head_sha` を返す | — | `GitHubError`・`GitError` で None |
| `_contains` | 祖先・同じ commit で真 | 子孫でない commit で偽 | 手元に無い commit（128）で偽 |
| `_resume_check` | `RETIRE` で状態が空になり 1 件を返す。GitHub の読み取りと `git merge-base` が 1 回ずつで、`git fetch` の回数は変わらない | `SKIP`・`CONTINUE` では外部コマンドを打たないか 2 回だけ | `STOP` で `transient`、何も変わらない。`RetireFailed` で `refused` |
| `_open` | 退避した後に `prepare` が新しい `work` を PR の今の head から作る | 完了の記録の回は照合しない | 照合の止まりの `Finish` を返す |
| `drive` | 退避の 1 件が `items` の末尾にあり、完了の記録の `result` には無い | 止まりの `Finish` にも 1 件 | — |
| `workspace.pending_retire` | 印の残る退避した先を返す | 印が無ければ None | — |
| `workspace.begin_retire` | 退避した先に印だけがある | 同じ秒の退避した先で `RetireFailed` | 書けない親で `RetireFailed`、一時のディレクトリが残らない |
| `workspace.finish_retire` | `work`・`read/<ランタイム>`・`logs`・`state.json` が移り、worktree に NDF の lock が掛かり、印が無い | `work` だけ移った後の打ち直しで残りを移す。印を消した後の形（状態はあるが `work` が無い）は `decide` が `RETIRE` にする | lock を掛け直せないと `RetireFailed`、worktree は外れない |
| `workspace._move_worktree` | NDF の lock を外して移す | lock の無い worktree も移す | 利用者の lock で `RetireFailed`、移さない |
| `workspace._release_retired` | 自分のコミットも変更も無い退避した先を外す | 読み取り用の worktree だけに変更が残っても残す | 状態ファイルが無いと残して理由を返す。自分のコミットが残ると残す |
| `workspace.release` | 既存の作業場所に加えて、同じ PR の印の無い退避した先を片付ける | 別の PR の退避した先は見ない。印の残る退避した先は見ない | 退避した先の理由が戻り値に足される |
