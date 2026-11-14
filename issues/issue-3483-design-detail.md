# #3483 の詳細設計: 中断した後に PR の head が進んでも、今の head から作り直して続けられる

設計文書 [issue-3483-design.md](issue-3483-design.md) の「構成要素」と「処理の流れ」を、ファイルと関数の粒度へ下ろした文書である。照合の規則の文・不変条件（I1〜I6）・決定（1〜7）は設計文書が持ち、ここでは番号で指す。変更は ndf-next（`plugins/ndf-next/`）だけで、今の ndf（`plugins/ndf/`）は触らない。

この文書で使う語:

| 語 | 意味 |
| --- | --- |
| 作業場所の根 | 1 つの PR の cross-refactoring が作業する `<置き場>/rf<PR>`（`workspace.root_of`） |
| 退避した先 | 古い起点のために作業場所の中身を移した `<置き場>/rf<PR>.retired-<YYYYmmddHHMMSS>` |
| 退避の途中の印 | 退避した先に置くファイル `.ndf-retiring.json`。中身は移す元・作業の起点・PR の head。移し終えたら消す |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
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
| `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | 照合の判定と、知らせる 1 行・結果の 1 件の文面を持つ純粋な関数 | 無し |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 変更 | 作業場所の用意の前に照合し、古い起点なら退避して空の状態から始め、結果へ 1 件足す | `resume_decide`・`workspace`・`ndf.adapters.github`・`ndf.adapters.git` |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 変更 | 作業場所を退避した先へ移す関数と、退避の途中の印を探す関数を足し、`release` に退避した先の片付けを足す。冒頭の説明に退避した先を足す | `ndf.core.converge.worktree`・`ndf.core.converge.place`・`ndf.adapters.git`・`store` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_drive.py` | 変更 | 駆動の照合・退避・続きのテストを足す（中断 → head を進める → 打ち直す） | `drive`・`_drive_world` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_resume_decide.py` | 新設 | 照合の判定の表のテスト | `resume_decide` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_workspace.py` | 新設 | 退避と、退避した先の片付けのテスト（実際の git の worktree で） | `workspace` |

**依存の向き。** 駆動が照合の判定（`resume_decide.py`）と作業場所（`workspace.py`）を呼ぶ。`resume_decide.py` はどこも import せず、`workspace.py` は `resume_decide.py` を読まない。一方向で、循環は無い。

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | モジュール | `Verdict`（新設）・`state_kind`（新設）・`needs_head`（新設）・`decide`（新設）・`notice`（新設） |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | モジュール | `_Run`（変更: 欄 `retired` を足す）・`_open`（変更）・`drive`（変更）・`_resume_check`（新設）・`_pr_head`（新設）・`_contains`（新設） |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | モジュール | `RetireFailed`（新設）・`pending_retire`（新設）・`begin_retire`（新設）・`finish_retire`（新設）・`_move_worktree`（新設）・`_release_retired`（新設）・`release`（変更） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `resume_decide.state_kind` | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | `state_kind(state: Mapping[str, Any]) -> StateKind` | 状態ファイルの中身を `none`（空）・`done`（完了の記録）・`final`（最終ゲートの記録）・`running`（途中）に分ける | 無し | 戻り値は 4 つのどれか | 無し |
| `resume_decide.needs_head` | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | `needs_head(retiring: bool, state: StateKind, has_work: bool) -> bool` | PR の head と祖先の判定を取る必要があるかを決める | 無し | 真になるのは、印が無く、状態が `running` で、`work` がある場合だけ | 無し |
| `resume_decide.decide` | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | `decide(retiring: bool, state: StateKind, has_work: bool, head: Optional[str] = None, contains: Optional[bool] = None) -> Verdict` | 照合の規則の表（7 行）を上から当てて答えを返す | `needs_head` が真なら `head` と、`head` があれば `contains` を渡す | 戻り値は `SKIP`・`CONTINUE`・`STOP`・`RETIRE`・`RESUME_RETIRE` のどれか | 無し |
| `resume_decide.notice` | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | 新設 | `notice(dest: str, work_head: Optional[str], pr_head: Optional[str]) -> tuple[str, dict[str, str]]` | 標準エラーの 1 行と `Finish.items` の 1 件を、設計文書の入出力の契約の形で組む | 無し | 1 行は `↻` で始まり、1 件は `kind` が `retired`。起点が無いときは 7 字の欄に `—`、40 字の欄に空文字を書く | 無し |
| `_pr_head` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 新設 | `_pr_head(cfg: RunConfig, ports: Ports) -> Optional[str]` | 既存の GitHub のアダプタで PR を 1 回読み、head の commit を返す | 無し | 取れれば 40 字の commit、取れなければ None | GitHub の REST の読み取り 1 回 |
| `_contains` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 新設 | `_contains(work: Path, head: str) -> bool` | `work` の HEAD が `head` を含むかを、`git merge-base --is-ancestor` の終了コード 0 だけで真と読む | `work` がある | 0 なら真、1 と 128 ほかの誤りは偽（決定 2） | `git merge-base --is-ancestor` 1 回 |
| `_resume_check` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 新設 | `_resume_check(cfg: RunConfig, ports: Ports, root: Path, state: dict[str, Any]) -> _Resume` | 照合の事実を集めて `decide` に渡し、答えに従って続ける・止まる・退避するのどれかを行う | 完了の記録の判定（`_reused`）と引数の照合（`_resume_mismatch`）を通った後 | `_Resume` の `stop` があれば何も変えていない（退避の失敗のときは印が残る）。退避したときは `state` が空で、`item` に 1 件 | GitHub の REST の読み取り・`git rev-parse`・`git merge-base`・`workspace` の退避・標準エラー |
| `_open` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 変更 | 変えない | `_resume_mismatch` の後、`workspace.prepare` の前に `_resume_check` を打ち、止まるならその `Finish` を返す | 変えない | 退避したときの `_Run` は空の状態と `retired` の 1 件を持つ | 既存の分に `_resume_check` の分を足す |
| `drive` | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | 変更 | 変えない | 返す `Finish` の `items` の末尾へ `run.retired` を足す（完了の記録へ保存した後に足す） | 変えない | 退避したときは、止まり・完了のどちらでも `items` の末尾が退避の 1 件。完了の記録の `result` には入らない（決定 6） | 変えない |
| `workspace.pending_retire` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `pending_retire(root: Path) -> Optional[tuple[Path, dict[str, str]]]` | `root` と同じ PR の退避した先のうち、印の残るものを探す | 無し | 印の残る最も新しい退避した先とその印の中身か None | ディレクトリの一覧とファイルを読む |
| `workspace.begin_retire` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `begin_retire(root: Path, mark: Mapping[str, str], now: Optional[float] = None) -> Path` | 退避した先を、印を入れた状態で一度に作る（一時のディレクトリに印を書いてから改名する） | `root` がある。同じ秒の退避した先が無い | 退避した先と印があり、それ以外は何も無い。`root` は変わらない | ディレクトリの作成・ファイルの書き込み・改名 |
| `workspace.finish_retire` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `finish_retire(repo: git.Git, root: Path, dest: Path, pr: int) -> None` | `root` に残る worktree・`logs`・状態ファイルの順に移し、`dest` の worktree に NDF の lock を掛け、印を消す | `dest` に印がある | `root` が無く、`dest` の worktree はすべて NDF の lock を持ち、印が無い。途中で止まれば印が残る | `git worktree unlock`・`move`・`lock`・ファイルの改名・削除 |
| `workspace._move_worktree` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `_move_worktree(repo: git.Git, src: Path, dst: Path) -> None` | NDF の lock を外してから worktree 1 本を `git worktree move` で移す（`--force` を使わない） | `src` は登録された worktree。`dst` は無い | `dst` に登録が移り、lock は外れている | `git worktree unlock`・`git worktree move` |
| `workspace._release_retired` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 新設 | `_release_retired(repo: git.Git, dest: Path) -> list[str]` | 印の無い退避した先 1 つを、自分のコミットも未コミットの変更も無ければ外し、あれば残す理由を返す | `dest` に印が無い | 外したときは `dest` が無い。残したときは `dest` が変わらず、理由が 1 件 | `git rev-list`・`git status`・`git worktree unlock`・`remove`・ディレクトリの削除 |
| `workspace.release` | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | 変更 | `release(repo: git.Git, ws: Workspace) -> list[str]`（変えない） | 既存の作業場所の片付けの後に、同じ PR の印の無い退避した先を `_release_retired` で片付ける | 変えない | 戻り値に、残した退避した先の理由が足される | 既存の分に `_release_retired` の分を足す |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_pr_head` | PR を読めない | 通信・認証の失敗・枠の上限・PR が無い・origin が読めない | `github.GitHubError`・`git.GitError` | 既定値で続ける（None を返し、`decide` が `STOP` にする） | 無し（`_resume_check` が出す） | 変えない |
| `_contains` | git を起動できない | git の欠け | 起動の `OSError` | 呼び出し元へ返す（既存の駆動の誤りと同じ扱い） | 既存の種類 `unknown` | 何も変えていない |
| `_resume_check` | `decide` が `STOP` | PR の head を取れない | `Verdict.STOP` | 止める（`Finish("transient", <summary>)`。決定 7） | `stopped`・種類 `transient`・終了コード `infra_wait`（75）・`summary` は設計文書の入出力の契約の文 | 何も変えていない（I5）。打ち直せる |
| `_resume_check` | `work` の HEAD を読めない | `work` が壊れている | `git rev-parse` の `GitError` | 既定値で続ける（比べる起点が無いとして `contains` を偽にし、古い起点と読む） | 退避の 1 行の作業の起点が `—` | 退避が `work` を移せなければ `finish_retire` の行の扱い |
| `_resume_check` | 退避が失敗した | `workspace.RetireFailed` | 例外 | 止める（`Finish("refused", <理由と退避した先>)`） | `stopped`・種類 `refused`・終了コード `failed`（1）・`summary` に移せなかったパス | 印が残れば、打ち直すと退避を続ける（I2） |
| `_open` | `_resume_check` の止まり | 上のとおり | `_Resume.stop` | 呼び出し元へ返す（`drive` が `Finish` をそのまま返す） | 上のとおり | 上のとおり |
| `drive` | 退避した後の実行の止まり | 既存の経路 | 既存の `DriveStopped` ほか | 既定値で続ける（止まりの `Finish` にも退避の 1 件を足す） | 既存の止まりの出力の `items` の末尾に退避の 1 件 | 変えない |
| `workspace.pending_retire` | 置き場の根を一覧できない | 権限・ディスクの誤り | `iterdir` の `OSError` | 呼び出し元へ返す | 既存の種類 `unknown` | 何も変えていない |
| `workspace.pending_retire` | 印を読めない | 印の JSON が壊れている | `json.loads` の `ValueError` | 既定値で続ける（中身を空として、退避の途中として扱う） | 退避の 1 行の起点と head が `—` | 退避を続けられる |
| `workspace.begin_retire` | 退避した先を作れない | 同じ秒の退避した先がある・権限・ディスクの満杯 | `os.rename` と書き込みの `OSError` | 呼び出し元へ返す（`RetireFailed`）。一時のディレクトリは消す | `_resume_check` の `refused` の行 | 何も変えていない。打ち直すと同じ照合からやり直す |
| `workspace.finish_retire` | worktree・ファイルを移せない | 利用者の lock・移す先がある・git の誤り | `_move_worktree` の `RetireFailed`・改名の `OSError` | 止める（`RetireFailed`） | `_resume_check` の `refused` の行 | 印が残る。打ち直すと残りから続ける |
| `workspace.finish_retire` | lock を掛け直せない | git の誤り | `git worktree lock` の `GitError` | 止める（`RetireFailed`）。worktree は外さない | 同上 | 印が残る。打ち直すと lock の無い worktree に掛け直す |
| `workspace.finish_retire` | `root` を消せない | 移している間に別のものが置かれた | `rmdir` の `OSError` | 止める（`RetireFailed`） | 同上 | 印が残る。残ったものを確かめて打ち直す |
| `workspace._move_worktree` | 利用者の lock が掛かっている | 理由が NDF の前置きで始まらない lock | `worktree.lock_of` | 止める（`RetireFailed`。外さない） | `summary` に `利用者の lock なので移さない（理由: …）` | 印が残る。利用者が lock を外してから打ち直す |
| `workspace._move_worktree` | 移せない | 移す先がある・git の誤り | `git worktree move` の `GitError` | 止める（`RetireFailed`）。外した lock は掛け直す | 同上 | 印が残る。打ち直せる |
| `workspace._release_retired` | 判定できない | 状態ファイルが無い・壊れている・`branch` が無い・git の誤り | `store.load` の例外・`GitError` | 既定値で続ける（残して理由を返す） | `ndf refactor release` と駆動の結果に残した理由 | 退避した先は残る。人が取り出すか消す |
| `workspace._release_retired` | 自分のコミットか未コミットの変更が残る | 退避した `work` に push していない作業がある | `worktree.own_commits`・`Git.status_paths` | 既定値で続ける（残して理由を返す） | `<退避した先>: 自分のコミットが <件数> 件残る` など | 退避した先は残る（要求の前提 2: 人が取り出す） |
| `workspace._release_retired` | worktree を外せない | 利用者の lock・git の誤り | `worktree.release` の `removed` が偽 | 既定値で続ける（残して理由を返す） | 既存の `release` と同じ形の理由 | 退避した先は残る |
| `workspace.release` | 退避した先の片付けの失敗 | 上のとおり | `_release_retired` の戻り値 | 既定値で続ける（理由を足して返す） | 上のとおり | 上のとおり |

## 処理の手順

照合の判定（`resume_decide.decide`）。上から当て、最初に当たった行で決める。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `resume_decide.decide` | `retiring` が真 | `RESUME_RETIRE` |
| `resume_decide.decide` | `state` が `none` か `done` | `SKIP` |
| `resume_decide.decide` | `state` が `final` | `SKIP`（I4。決定 5） |
| `resume_decide.decide` | `state` が `running` で `has_work` が偽 | `RETIRE` |
| `resume_decide.decide` | `head` が None | `STOP` |
| `resume_decide.decide` | `contains` が真 | `CONTINUE` |
| `resume_decide.decide` | `contains` が偽 | `RETIRE` |

`state_kind` は `done` を最初に見て、次に `final`、次に途中の鍵（`args` ほか）の有無を見る。`done` と `final` が両方ある状態は `done` である。

`_resume_check` の手順:

1. `workspace.pending_retire(root)` で印の残る退避した先を探す
2. `state_kind(state)` と `work` の有無（`(root / "work").is_dir()`）を求める
3. `needs_head` が真のときだけ、`_pr_head` を打ち、head が取れたら `git -C <work> rev-parse HEAD` と `_contains` を打つ
4. `decide` の答えが `SKIP`・`CONTINUE` なら何もせずに `state` をそのまま返す。`STOP` なら `transient` の `Finish` を返す
5. `RETIRE` なら `begin_retire(root, mark)` → `finish_retire`、`RESUME_RETIRE` なら `finish_retire` だけを打つ。どちらも `notice` の 1 行を標準エラーへ出し、空の状態と 1 件を返す

`_open` は、`_resume_check` が返した状態で `workspace.prepare` へ進む。退避した後は `rf<PR>` が無いため、`prepare` が今の head から `work` と `read/<ランタイム>` を作る。

`workspace.finish_retire` の移す順は、`work` → `read/<ランタイム>`（名前の順）→ `read` が空なら消す → `logs` → `state.json`（最後）→ `dest` の worktree のうち lock の無いものへ NDF の lock を掛ける → `root` が空なら消す → 印を消す、である。状態ファイルを最後に移すことが、古い状態を終わらせることに当たる（決定 3）。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `workspace.finish_retire` | `root` の子が登録された worktree | `_move_worktree(<子>, <dest>/<子の相対パス>)` |
| `workspace.finish_retire` | `root` の子が worktree でないファイルかディレクトリ | `os.rename` で `<dest>/<子の名前>` へ |
| `workspace.finish_retire` | `root` が無い（前の打ち直しで移し終えた） | 移さずに lock を確かめ、印を消す |

`workspace._release_retired` の判定:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `workspace._release_retired` | `dest/state.json` から `branch` を読めない | 残す（判定できない） |
| `workspace._release_retired` | `dest` の worktree のどれかに `own_commits(<worktree>, refs/remotes/origin/<branch>, HEAD)` が 1 件以上ある | 残す（自分のコミットが残る） |
| `workspace._release_retired` | `dest` の worktree のどれかに未コミットの変更がある | 残す（未コミットの変更が残る） |
| `workspace._release_retired` | 上のどれにも当たらない | worktree を `worktree.release` で 1 本ずつ外し、すべて外れたら `dest` を消す。外れないものがあれば残す |

- 繰り返しの終わる条件: `finish_retire` と `_release_retired` は作業場所の子の数（`work`・担当ごとの読み取り用・`logs`・状態ファイル）で終わる。`pending_retire` と `release` の退避した先の一覧は、同じ PR の退避した先の数で終わる
- 計算量の注意: 照合で増える外部への問い合わせは、未完了の状態を続ける回につき GitHub の REST の読み取り 1 回・`git rev-parse` 1 回・`git merge-base` 1 回である。`git fetch` は足さない（決定 2）。`git rev-parse` は要求の非機能の条件の数に入れていない。数える対象は PR の head の取得と祖先の判定で、`work` の HEAD の読み取りは手元の参照を読むだけのためである

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Verdict` | 型（列挙: `SKIP`・`CONTINUE`・`STOP`・`RETIRE`・`RESUME_RETIRE`） | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | — |
| `StateKind` | 型（`Literal["none", "done", "final", "running"]`） | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | — |
| `UNKNOWN_HEAD` | 定数（起点が無いときに 7 字の欄へ書く文字） | `plugins/ndf-next/src/ndf/domains/crossrefactor/resume_decide.py` | `"—"` |
| `RETIRED_SEP` | 定数（退避した先の名前の区切り） | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | `".retired-"` |
| `RETIRING_MARK` | 定数（退避の途中の印のファイル名） | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | `".ndf-retiring.json"` |
| `RETIRE_STAMP` | 定数（時刻の形。地方時の 14 桁） | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | `"%Y%m%d%H%M%S"` |
| `_RETIRED_NAME` | 定数（正規表現） | `plugins/ndf-next/src/ndf/domains/crossrefactor/workspace.py` | `^rf(\d+)\.retired-\d{14}$` |
| 印の中身 | JSON（`from`: 移す元のパス・`work_head`・`pr_head`。起点が無ければ空文字） | 退避した先の `.ndf-retiring.json` | — |
| `_Resume` | 型（`state: dict`・`item: Optional[dict]`・`stop: Optional[Finish]`） | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | — |
| `_Run.retired` | 欄（退避の 1 件） | `plugins/ndf-next/src/ndf/domains/crossrefactor/drive.py` | `None` |

退避の名前と印のファイル名は `workspace.py` の 1 か所に置き、駆動と片付けはそこから読む。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ PR の駆動 2 本 | `rf<PR>`・状態ファイル | 駆動に PR ごとの排他は無い（`grep -n "lease\|flock\|filelock\|exclusive\|hold" src/ndf/domains/crossrefactor/cli.py src/ndf/domains/crossrefactor/drive.py` は `release` の行だけを返した）。既存の状態ファイルの書き込みも同じ前提で、この変更は排他を足さない | 退避は印から続けられるため、片方が途中で止まっても次の打ち直しで揃う |
| 駆動と、作業場所の片付け（`release`・`ndf refactor release`） | 退避した先 | 一時のディレクトリの改名で、印を入れた状態の退避した先を作る。片付けは印の残る退避した先を外さない（I6） | 印が消えた後の退避した先だけが片付けの対象になる |
| 駆動と、孤児の lock の片付け（`worktree.sweep_orphans`） | 退避した先の worktree の NDF の lock | 移した後のディレクトリは在るため、孤児（ディレクトリが無い）に当たらない。移している間に lock が外れている worktree は孤児の判定の対象外（lock の無い worktree を見ない） | `finish_retire` の最後に lock の無い worktree へ掛け直す |
| 駆動と、同じリポジトリの別の `git worktree` の操作 | worktree の登録（`.git/worktrees`） | git の登録の書き込みの排他に任せる | `git worktree move` の失敗は印を残して止まり、打ち直しで続ける |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_resume_check` | `notice` の 1 行（設計文書の入出力の契約の文） | 標準エラー（1 回） | トークン・環境変数の値 |
| `drive` | 退避の 1 件（種類 `retired`） | `Finish.items` の末尾（完了の記録には入れない） | 同上 |
| `_resume_check` | head を取れない止まり・退避の失敗の `summary` | `Finish`（`transient`・`refused`） | GitHub の応答の本文 |
| `workspace.release` | 残した退避した先の理由 | 戻り値（`ndf refactor release` の結果の `items`） | — |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `resume_decide.state_kind` | `drive._reused` の `"done" in state` の判定・`drive._final` の `"final" in run.state` | `drive.py` の `_reused`・`_open`・`_final` を読んだ | 使えない（判定を純粋な関数へ寄せる側。同じ鍵 `done`・`final` を見る） |
| `resume_decide.needs_head` | 無し | `grep -rn "is_ancestor\|resume" plugins/ndf-next/src/ndf/domains/crossrefactor` | 使えない（該当の判定が無い） |
| `resume_decide.decide` | `finished_decide.finished`（完了の記録の判定） | `grep -n "def finished" plugins/ndf-next/src/ndf/domains/crossrefactor/finished_decide.py` | 使えない（完了の記録を返すかの判定で、途中の状態の照合を持たない） |
| `resume_decide.notice` | 無し | `grep -rn "retired\|退避" plugins/ndf-next/src/ndf` | 使えない（該当の文面が無い） |
| `_pr_head` | `Ports.github`・`github.GitHub.pull` の `head_sha` | `grep -n "def pull\|head_sha" plugins/ndf-next/src/ndf/adapters/github.py` | 使う（決定 2） |
| `_contains` | `git.Git.is_ancestor` | `plugins/ndf-next/src/ndf/adapters/git.py` の `is_ancestor` を読んだ | 使えない（終了コード 1 以外を `GitError` にし、128 を古い起点と読めない。決定 2）。`Git.attempt` を使う |
| `_resume_check` | `drive._resume_mismatch`（未完了の状態の照合） | `drive.py` を読んだ | 使えない（引数の比べで、head を見ない）。並べて呼ぶ |
| `workspace.pending_retire` | 無し | `grep -rn "retired" plugins/ndf-next/src/ndf/domains/crossrefactor` | 使えない（該当が無い） |
| `workspace.begin_retire` | 無し | 同上 | 使えない（該当が無い） |
| `workspace.finish_retire` | `worktree.lock_review_worktree` | `plugins/ndf-next/src/ndf/core/converge/worktree.py` を読んだ | 使えない（掛けられなければ worktree を `--force` で外すため、退避した作業を失いうる）。理由の文は `worktree.held_reason` を使う |
| `workspace._move_worktree` | `worktree.lock_of`・`worktree.LOCK_PREFIX` | 同上 | 使う（NDF の lock かを見る） |
| `workspace._release_retired` | `worktree.release`・`worktree.own_commits`・`Git.status_paths`・`store.load` | 同上と `grep -n "def status_paths" plugins/ndf-next/src/ndf/adapters/git.py` | 使う（外す前に作業の残りを見て、外すのは `worktree.release`） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `resume_decide.state_kind` | 空・`done` あり・`final` あり・途中で 4 つに分かれる | `done` と `final` が両方あると `done` | — |
| `resume_decide.needs_head` | 印なし・`running`・`work` ありで真 | ほかの組み合わせはすべて偽 | — |
| `resume_decide.decide` | 照合の規則の表の 7 行で表と同じ答え | 印と `running` が両方あると `RESUME_RETIRE`。`final` で PR の head が進んでいても `SKIP` | `head` が None で `STOP` |
| `resume_decide.notice` | 1 行と 1 件が入出力の契約の形 | 起点が無いと `—` | — |
| `_pr_head` | 偽の GitHub のアダプタの `head_sha` を返す | — | `GitHubError`・`GitError` で None |
| `_contains` | 祖先・同じ commit で真 | 子孫でない commit で偽 | 手元に無い commit（128）で偽 |
| `_resume_check` | `RETIRE` で状態が空になり 1 件を返す。GitHub の読み取りと `git merge-base` が 1 回ずつで、`git fetch` の回数は変わらない | `SKIP` では外部への問い合わせを打たない。`CONTINUE` では読み取りと `merge-base` が 1 回ずつ | `STOP` で `transient`、作業場所と状態ファイルが変わらない。`RetireFailed` で `refused` |
| `_open` | 退避した後に `prepare` が新しい `work` を PR の今の head から作る | 完了の記録の回は照合しない | 照合の止まりの `Finish` を返す |
| `drive` | 中断 → head を進める → 打ち直すと新しい `work` の HEAD が PR の今の head（受け入れ条件 2・8）。退避の 1 件が `items` の末尾にあり、完了の記録の `result` には無い | 止まりの `Finish` にも 1 件。自分で push した後の打ち直しは退避しない（5） | 2 回目の打ち直しで古い状態を続けない（4） |
| `workspace.pending_retire` | 印の残る退避した先を返す | 印が無ければ None。2 つあれば新しい方 | 壊れた印でも退避した先を返し、中身は空 |
| `workspace.begin_retire` | 退避した先に印だけがある | 同じ秒の退避した先で `RetireFailed` | 書けない親で `RetireFailed`、一時のディレクトリが残らない |
| `workspace.finish_retire` | `work`・`read/<ランタイム>`・`logs`・`state.json` が移り、`git -C <dest>/work rev-parse HEAD` が元の HEAD で、worktree に NDF の lock が掛かり、印が無い | `work` だけ移った後の打ち直しで残りを移す。`root` が既に無いときは印を消すだけ。2 回打っても同じ | lock を掛け直せないと `RetireFailed`、worktree は外れない |
| `workspace._move_worktree` | NDF の lock を外して移す | lock の無い worktree も移す | 利用者の lock で `RetireFailed`、移さない |
| `workspace._release_retired` | 自分のコミットも変更も無い退避した先を外す | 読み取り用の worktree だけに変更が残っても残す | 状態ファイルが無いと残して理由を返す。自分のコミットが残ると残す |
| `workspace.release` | 既存の作業場所に加えて、同じ PR の印の無い退避した先を片付ける | 別の PR の退避した先は見ない。印の残る退避した先は見ない | 退避した先の理由が戻り値に足される |
