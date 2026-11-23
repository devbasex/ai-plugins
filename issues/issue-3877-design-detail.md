# #3877 の詳細設計: 報告の保存先を `ndf worker prepare` で用意する

設計文書 [issue-3877-design.md](./issue-3877-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/hooks/guards.json                      guard の表（決定 4 で 1 行足す）
├── docs/contracts/commands.md                       サブコマンドの仕様（ndf check --write-spec の生成物）
├── skills/development-workflow/references/
│   ├── agent-layers.md                              起動指示の「置く場所」「作業の結果ファイル」の欄
│   └── waiting.md                                   「中間通知を受けたとき」の用意の記述
├── src/ndf/
│   ├── contracts/cli.py                             ndf contracts emit --out の書き込みをリンクをたどらない経路へ
│   ├── contracts/worker_paths.py *                  report_paths（パスの決め方の唯一の保存先）・リンクをたどらない読み書き
│   ├── core/worker.py                               _cmd_prepare・_cmd_append・_cmd_collect・_cmd_run・delegate
│   └── domains/hooks/guards/rm_variable.py *        決定 4 の guard
└── tests/
    ├── contracts/test_contracts_cli.py              emit --out がリンクを書き換えない
    ├── contracts/test_worker_paths.py *
    ├── core/test_worker_prepare.py *
    ├── domains/hooks/test_rm_variable.py *
    └── worker/test_delegate.py                      委譲中と結果の読み取りでリンクをたどらない
```

依存の向き: `contracts/worker_paths.py` は標準ライブラリ（`contextlib`・`os`・`pathlib`・`re`・`secrets`・`stat`・`typing`）だけに依存し、`core` も `progress` も読まない。同じ `contracts` の層の `contracts/cli.py`（`ndf contracts emit`）も読む。`core/worker.py`（`ndf worker prepare`・`ndf worker run`）と `progress/gate/entry.py`（`ndf answer work`。#3908）がそれを読む一方向で、`progress/gate/entry.py:95` の「worker の層は読まず、サブコマンドのコントラクトだけを使う」（IG1）に触れない（決定 7）。guard（`domains/hooks/guards/rm_variable.py`）は `domains/hooks/verdict` と `re` だけを読む。`contracts/worker_paths.py` も `core/worker.py` も読まない。手順書はコマンドの字面を書くだけで、コードから参照されない。循環は無い。型の関係は設計文書の「構成要素」のモジュールの図にある。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/contracts/worker_paths.py` | 新設 | 作業ディレクトリと名前から、worker の 4 つのファイルのパスを組み、既存のリンクと既存の inode をたどらずに読み・書き・消す | `contextlib`・`os`・`pathlib`・`re`・`secrets`・`stat`・`typing` | モジュール `ndf.contracts.worker_paths` | `ReportPaths`（新設）・`NameInvalid`（新設）・`EntryError`（新設）・`EntryUnsafe`（新設）・`EntryUnreadable`（新設）・`WorkDir`（新設）・`report_paths`（新設）・`report_name`（新設）・`open_workdir`（新設）・`entry_state`（新設）・`read_file`（新設）・`write_file`（新設）・`remove_file`（新設） |
| `plugins/ndf/src/ndf/contracts/cli.py` | 変更 | `ndf contracts emit --out` の書き込みを `write_file` にする | `ndf.contracts.worker_paths` | モジュール `ndf.contracts.cli` | `_cmd_emit`（変更） |
| `plugins/ndf/src/ndf/core/worker.py` | 変更 | 報告の保存先を用意し、報告を追記・読み取るサブコマンドを足し、`worker run` の結果ファイルをリンクをたどらずに扱う | `ndf.contracts.worker_paths`・`ndf.contracts.result` | モジュール `ndf.core.worker` | `_cmd_prepare`（新設）・`_add_prepare`（新設）・`_cmd_append`（新設）・`_add_append`（新設）・`_cmd_collect`（新設）・`_add_collect`（新設）・`_parser`（変更）・`_cmd_run`（変更）・`delegate`（変更）・`_written`（変更）・`_read_result`（新設）・`search_record`（変更）・`_missing_record`（変更）・`_stopped_detail`（変更）・`outcome_of`（変更） |
| `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 新設 | 空かもしれない変数をパスに含む `rm` を止める guard | `re`・`ndf.domains.hooks.verdict` | モジュール `ndf.domains.hooks.guards.rm_variable` | `risky_removal`（新設）・`expanded_variables`（新設）・`check`（新設） |
| `plugins/ndf/contracts/hooks/guards.json` | 変更 | guard の表に `rm-variable` の 1 行を足す | 無し（データ） | 表 `guards` | 無し |
| `plugins/ndf/docs/contracts/commands.md` | 変更 | `ndf worker prepare`・`ndf worker append`・`ndf worker collect` の節（生成物） | 無し（生成物） | 無し | 無し |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 変更 | 起動指示の準備・報告の書き込みをサブコマンドに書き換える | 無し（文書） | 無し | 無し |
| `plugins/ndf/skills/development-workflow/references/waiting.md` | 変更 | 用意と報告の読み取りをサブコマンドに書き換える | 無し（文書） | 無し | 無し |
| `plugins/ndf/tests/contracts/test_worker_paths.py` | 新設 | パスの組み立てと名前の形 | `ndf.contracts.worker_paths` | モジュール | テスト関数 |
| `plugins/ndf/tests/core/test_worker_prepare.py` | 新設 | `prepare`・`append`・`collect` の返りとファイルの始末（リンクと途中の失敗を含む） | `ndf.core.worker` | モジュール | テスト関数 |
| `plugins/ndf/tests/domains/hooks/test_rm_variable.py` | 新設 | guard の判定（決定 4 の条件つき） | `ndf.domains.hooks` | モジュール | テスト関数 |
| `plugins/ndf/tests/contracts/test_contracts_cli.py`・`plugins/ndf/tests/worker/test_delegate.py` | 変更 | `emit --out` の書き込みと、`worker run` の委譲中・結果の読み取りがリンクをたどらないこと | `ndf.contracts.cli`・`ndf.core.worker` | モジュール | テスト関数 |

## 報告の保存先のパスを組む（`report_paths`）

満たす決定: 決定 2、決定 3、決定 7、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `ReportPaths` | `plugins/ndf/src/ndf/contracts/worker_paths.py` | 新設 | `class ReportPaths(NamedTuple): prompt: Path; report: Path; done: Path; result: Path` | 1 人の worker が読み書きする 4 つのファイルのパス |
| `NameInvalid` | `plugins/ndf/src/ndf/contracts/worker_paths.py` | 新設 | `class NameInvalid(ValueError)` | worker の名前の形が合わないことを呼び出し元へ伝える |
| `report_paths` | `plugins/ndf/src/ndf/contracts/worker_paths.py` | 新設 | `report_paths(directory: Path, name: str) -> ReportPaths` | 作業ディレクトリと worker の名前から、プロンプト・報告・完了マーカー・作業の結果ファイルの 4 つの絶対パスを組む |

- `report_paths`
  - 事前条件: 無し（名前の形は呼ぶ側でなくこの関数が確かめる）
  - 事後条件: 返る 4 つはすべて絶対パスで、同じ親ディレクトリ（`directory` を絶対パスにしたもの）の直下にある。ファイルの有無は見ない
  - 副作用: 無し。`Path.resolve()`（`strict=False`）を当てるのは `directory` だけで、名前を足した後の 4 つのパスには当てない（作業ディレクトリの中の名前がリンクでも、リンクの先へ解かない）。ファイルを作らず、書き換えない

4 つのうち `prompt` を使うのは `ndf answer work`（#3908）だけで、`ndf worker prepare` は残りの 3 つを返す。名前の区切りはすべて `.` でそろえる（`<名前>.prompt.md`・`<名前>.report.md`・`<名前>.report.md.done`・`<名前>.result.json`）。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `report_paths` | 名前の形が合わない | `name` が `[A-Za-z0-9._-]` の 1〜64 文字でない（空文字・`../x`・`/` を含む・65 文字以上・日本語） | 関数の先頭（パスを組む前） | 呼び出し元へ返す（`NameInvalid` を上げる） | 呼び出し元が止まる結果 JSON にする（`_cmd_prepare` は `WorkerError("invalid", …)` へ、`ndf answer work` の `work_argv` は `model.Refused` へ包む）。どちらも `stopped`（`invalid`）で、終了コードは停止理由の表（`contracts/causes.json`）の `invalid` の値 | ファイルを 1 つも触っていないので後始末は無い。名前を直せばそのまま打ち直せる |

例外を `ValueError` の子にするのは、この層が `core` と `progress` の型（`WorkerError`・`model.Refused`）を読まないためである。同じ層の `contracts/limits.py:22` の `EnvError(ValueError)` と同じ形にする。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `NAME_RE` | 定数（`re.Pattern`） | `plugins/ndf/src/ndf/contracts/worker_paths.py` | `re.compile(r"[A-Za-z0-9._-]{1,64}\Z")`（`fullmatch` で確かめる） |
| `PROMPT_SUFFIX` | 定数 | 同上 | `".prompt.md"` |
| `REPORT_SUFFIX` | 定数 | 同上 | `".report.md"` |
| `DONE_SUFFIX` | 定数 | 同上 | `".done"`（報告のパスの後ろに付ける） |
| `RESULT_SUFFIX` | 定数 | 同上 | `".result.json"` |

4 つの接尾辞と名前の形の唯一の保存先はこのモジュールで、手順書・テスト・`ndf answer work`（#3908）は字面を持たずにこの関数を呼ぶ。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `report_paths` | `ndf.slot_pool.slot_path(directory, index, suffix)`（`src/ndf/slot_pool.py:72`） | `grep -rn "slot_path" plugins/ndf/src/ndf/` | 使えない。重い起動の同時実行数のスロット（`slot-<番号>.lock`）のパスで、対象が違う（この語を避けるのが決定 6） |
| `report_paths` | `worker.pr_body_draft(state_dir)`（`src/ndf/core/worker.py:183`） | `grep -rn "def [a-z_]*_path(\|def pr_body_draft" plugins/ndf/src/ndf/core/` | 使えない。保存先の下の 1 ファイルだけを返す関数で、4 つの組と名前の検査を持たない。戻り値を `Path` にする形だけ踏襲する |
| `report_paths` | `ndf.progress.gate.entry.work_argv`（`src/ndf/progress/gate/entry.py:81`。`<作業名>-prompt.md`・`<作業名>-result.json` を組む） | `grep -rn "prompt-file\|result-file" plugins/ndf/src/ndf/progress/gate/entry.py` | 同じ役割の決め方が別にある。#3908 がこの関数の呼び出しへ寄せ、区切りを `.` にそろえる（決定 2） |
| `ReportPaths` | `ndf.core.worker.ClosingHit`・`ndf.domains.hooks.context.ShellCommand`（`NamedTuple`） | `grep -rn "NamedTuple" plugins/ndf/src/ndf/ \| head` | 使う（形を踏襲する）。4 つのパスを位置で受け渡すと取り違えるので、名前の付いた組にする |
| `NameInvalid` | `ndf.contracts.limits.EnvError(ValueError)`（`src/ndf/contracts/limits.py:22`） | `grep -rn "^class .*(ValueError)" plugins/ndf/src/ndf/contracts/` | 使う（形を踏襲する）。`contracts.ContractError` は表そのものが読めないときの例外なので、呼び出しの誤りには使わない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `report_paths` | `(Path("/w"), "n")` が `/w/n.prompt.md`・`/w/n.report.md`・`/w/n.report.md.done`・`/w/n.result.json` を返す | 名前が 1 文字と 64 文字は通る。65 文字は断る。相対パスの `directory` でも絶対パスで返る。`<d>/n.result.json` が作業ディレクトリの外へのリンクでも、返る `result` は `<d>/n.result.json` のまま | 名前が空文字・`../x`・`a/b`・`日本語` は `NameInvalid` で、例外の前後でファイルシステムが変わらない |
## リンクをたどらない読み書き（`open_workdir`・`entry_state`・`read_file`・`write_file`・`remove_file`）

満たす決定: 決定 3、決定 7、受け入れ条件 4

作業ディレクトリは supervisor が渡す任意のディレクトリで、その中に前の作業や別の手が作った名前が残りうる。名前の検査（`NAME_RE`）はパスの字面を作業ディレクトリの直下に限るだけで、その名前がシンボリックリンクなら読み書きと削除はリンクの先（作業ディレクトリの外の利用者のファイル）に当たる。ハードリンクも同じで、作業ディレクトリの名前が利用者のファイルと同じ inode を共有していれば、その inode を切り詰める・書き換えると利用者のファイルが変わる（`O_NOFOLLOW` はシンボリックリンクだけを拒み、ハードリンクを拒まない）。さらに、検査の後に作業ディレクトリそのもの（親の名前）を別のディレクトリへのリンクへ差し替えられると、名前で引き直す操作は差し替え先の同名のファイルに当たる。そこで、worker の 4 つのファイルを読む・書く・消す処理はすべてこの 5 つの関数を通す。作業ディレクトリは最初に 1 回だけ開いた記述子で固定し、以後の検査と操作はすべてその記述子に対する相対の操作（`dir_fd`）にする。既存のリンクをたどらず、既存の inode を書き換えない。準備の後の書き込み（worker の報告の追記と完了マーカーの作成）と読み取り（supervisor の報告の読み取り）も、下の「報告の追記と読み取りのサブコマンド」の節でこの関数を通す。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `EntryError` | `plugins/ndf/src/ndf/contracts/worker_paths.py` | 新設 | `class EntryError(ValueError)` | 下の 2 つの共通の親。呼び出し元は読み書きの失敗をこの 1 つで受けて結果 JSON にする |
| `EntryUnsafe` | 同上 | 新設 | `class EntryUnsafe(EntryError)` | 名前がリンクか、リンク数が 1 の通常のファイルでないことを伝える（名前と見つけた種類を持つ。種類は `symlink`・`hardlink`・`directory`・`other`） |
| `EntryUnreadable` | 同上 | 新設 | `class EntryUnreadable(EntryError)` | 通常のファイルを読めない・UTF-8 として解けないことを伝える（名前と理由を持つ。理由は `OSError` の `strerror` か `UnicodeDecodeError` の文） |
| `WorkDir` | 同上 | 新設 | `class WorkDir(NamedTuple): path: Path; fd: int` | 開いた作業ディレクトリ。`path` は表示と結果 JSON のパスを組むためだけに使い、操作には使わない |
| `open_workdir` | 同上 | 新設 | `open_workdir(directory: Path) -> ContextManager[WorkDir]` | `os.open(directory.resolve(), O_RDONLY \| O_DIRECTORY \| O_CLOEXEC)` で 1 回だけ開き、抜けるときに閉じる。リンクを解くのはこの 1 回だけで、開いた後に名前を差し替えられても記述子は開いた時点のディレクトリを指す |
| `entry_state` | 同上 | 新設 | `entry_state(wd: WorkDir, name: str) -> Optional[os.stat_result]` | `os.stat(name, dir_fd=wd.fd, follow_symlinks=False)` で、無ければ `None`、リンク数（`st_nlink`）が 1 の通常のファイルならその `stat_result` を返す。シンボリックリンク・リンク数が 2 以上の通常のファイル（ハードリンク）・ディレクトリ・ほかの種類は `EntryUnsafe`。大きさの判定（`st_size`）はこの返りで行い、中身を読まない |
| `read_file` | 同上 | 新設 | `read_file(wd: WorkDir, name: str) -> Optional[str]` | `os.open(name, O_RDONLY \| O_NOFOLLOW \| O_NONBLOCK \| O_CLOEXEC, dir_fd=wd.fd)` で開き、開いた記述子を `os.fstat` で確かめて（通常のファイルで `st_nlink` が 1）、同じ記述子から読んで UTF-8 で解く。無ければ `None`。最後の要素がシンボリックリンク（`ELOOP`）・確かめに落ちる記述子は読まずに閉じて `EntryUnsafe`。開けない（`ENOENT` のほかの `OSError`）・読めない・解けない（`UnicodeDecodeError`）は `EntryUnreadable` に包む |
| `write_file` | 同上 | 新設 | `write_file(wd: WorkDir, name: str, text: str) -> None` | 下の「書き込みの手順」で、私有の一時ディレクトリに作った新しい inode を `name` へ公開する。既存のファイルを開かず、切り詰めない |
| `remove_file` | 同上 | 新設 | `remove_file(wd: WorkDir, name: str) -> Literal["absent", "removed"]` | `entry_state(wd, name)` で確かめてから `os.unlink(name, dir_fd=wd.fd)` する。確かめの時点でリンクなら消さずに `EntryUnsafe`（リンクの先もリンク自体も残す） |

書き込みの手順（`write_file`）:

1. `entry_state(wd, name)` で確かめる（`EntryUnsafe` はそのまま上げる）
2. 既存のファイルがあれば、`os.access(name, os.W_OK, dir_fd=wd.fd, follow_symlinks=False)` が偽のとき `PermissionError`（`EACCES`）を上げて何も作らない。置き換えを許すのは、今の `Path.write_text` が開けるファイル（呼んだ利用者が書き込めるファイル）だけである。親のディレクトリに書き込めるだけでは、読み取り専用のファイルを置き換えない
3. 作業ディレクトリの権限（グループかその他の利用者の書き込み・sticky bit の有無・持ち主）では拒まない。umask 002 で作った 0775 のディレクトリやプロジェクトの共有の出力先にも書ける。手順 7 の一時ディレクトリの後始末は名前で引く `rmdir` で（記述子に対する `rmdir` は無い）、照合の後から `rmdir` までに名前を別の空のディレクトリへ差し替えられると、そのディレクトリを消しうる。それでも害にならないのは、`rmdir` が空のディレクトリしか消さずシンボリックリンクをたどらないことと、名前を差し替えるのに要る権限（親のディレクトリへの書き込みと sticky bit の制約。別の親から移すときは移すディレクトリへの書き込みも）が、同じ空のディレクトリを消すのに要る権限と同じことによる。差し替えられる手はそのディレクトリを自分で消せるので、この呼び出しが消せるものはその手が消せるものを超えない
4. `os.mkdir(f".{name}.<乱数 16 字>", 0o700, dir_fd=wd.fd)` で私有の一時ディレクトリを作り、`os.open(同じ名前, O_RDONLY \| O_DIRECTORY \| O_NOFOLLOW \| O_CLOEXEC, dir_fd=wd.fd)` で開く。開いた記述子を `os.fstat` で確かめ、ディレクトリで、持ち主が `os.geteuid()` で、グループとその他の利用者の権限（`0o077`）が無くなければ `EntryUnsafe`（`other`）にする（作ってから開くまでに名前を差し替えられたとき）。setgid のディレクトリの下では一時ディレクトリが setgid を継ぐ（`0o2700`）ので、権限を `0o700` と完全一致で比べない。`os.open` に失敗したときは、作ったディレクトリと確かめる記述子が無いので名前を `rmdir` せずにそのまま上げる（その名前は差し替えられた別の手のディレクトリでありうる）
5. 一時ディレクトリの記述子に対して `os.open("data", O_WRONLY \| O_CREAT \| O_EXCL \| O_NOFOLLOW \| O_CLOEXEC, 0o600, dir_fd=一時ディレクトリ)` で新しい inode を作り、`text` を UTF-8 で書いて閉じる
6. `os.replace("data", name, src_dir_fd=一時ディレクトリ, dst_dir_fd=wd.fd)` で公開する
7. 成否によらず、手順 5 で作って公開していない `data` だけを `os.unlink(…, dir_fd=一時ディレクトリ)` で消し（手順 4 の確かめに落ちたときは消さない）、記述子を閉じる。閉じる前に、記述子が指すディレクトリの今の名前を `os.readlink(f"/proc/self/fd/{記述子}")` の最後の要素で引く（共有の作業ディレクトリでは別の利用者が一時ディレクトリを同じ親の別名へ移せ、作った名前だけを見ると移された先が残るため。引けない環境と ` (deleted)` で終わるときは作った名前だけを見る）。今の名前、作った名前の順に、作業ディレクトリの直下の 1 要素で、`os.stat(…, dir_fd=wd.fd, follow_symlinks=False)` が開いた記述子と同じ inode（`st_dev`・`st_ino`）を返す最初の名前だけを `os.rmdir(…, dir_fd=wd.fd)` で消す（差し替え先のディレクトリを消さない。手順 4 の確かめに落ちたときの `rmdir` の失敗は飲む）。途中の失敗は後始末の後に `OSError` を上げ直す

- `entry_state`・`read_file`・`write_file`・`remove_file` は入口で、作業ディレクトリの直下の 1 要素でない名前（空・`.`・`..`・区切りを含む名前。絶対パスを含む）を `EntryUnsafe`（`other`）で拒む。`dir_fd` は絶対パスを制限せず、`O_NOFOLLOW` と `follow_symlinks=False` は途中の要素のリンクに効かないため
- 5 つとも、`report_paths` の返したパスに `resolve()` を当てない。リンクを解いてよいのは `open_workdir` が作業ディレクトリを開く 1 回だけである（作業ディレクトリそのものは supervisor が選んだ場所である）
- 検査と操作の間に名前を差し替えられる競合は次のように塞ぐ
  - 親: 検査と操作は同じ `wd.fd` に対する相対の操作なので、検査の後に作業ディレクトリの名前を別のディレクトリへのリンクへ差し替えても、操作は開いた時点のディレクトリに当たる。差し替え先の同名のファイルは読まず、書かず、消さない
  - 書く側: 公開する inode は、手順 5 で作った inode だけである。一時ディレクトリは呼んだ利用者だけが書き込める（`0o700`）ので、ほかの利用者は `data` の名前を差し替えられない。`os.replace` は名前を一時ディレクトリの記述子から引くので、一時ディレクトリの名前を差し替えられても公開する inode は変わらない。置き換えられる側がシンボリックリンクでもハードリンクでも、その先や共有する inode を書き換えない
  - 読む側: 検査した記述子そのものから読むので、検査の後に名前が替わっても、読むのは検査した inode である
  - 消す側: `os.unlink` は最後の要素のリンクをたどらない。確かめの後に名前を作業ディレクトリの外へのリンクへ差し替えられたときは、差し替えたリンクの名前を消して `removed` を返す（リンクの先は消えない）。リンクの名前を消してよいのは、作業ディレクトリの中の名前で、前の作業のファイルを消す意図の操作だからである。確かめの時点でリンクのときだけ `EntryUnsafe` で残す（下の単体テストの観点もこの 2 つを分けて確かめる）
- 同じ利用者が同じ時刻に別の手で同じ名前を操作する競合は塞がない（同じ権限の手は記述子も一時ディレクトリも操作できる）
- ハードリンクを `write_file` の `entry_state` でも拒むのは、置き換えでは害が無くても、作業ディレクトリに利用者のファイルと inode を共有する名前があること自体を前の作業の残りでない異常として止めるためである（読む側と同じ規則にそろえ、`EntryUnsafe` の条件を 1 つにする）

### 使う側

| 使う所 | 今の書き方 | 変えた後 |
| --- | --- | --- |
| `ndf worker prepare`（`_cmd_prepare`） | この設計で新設 | 作業ディレクトリを作った後に `open_workdir` で 1 回開き、その `wd` で 3 つの名前を `entry_state` でまとめて確かめてから、完了マーカーと結果ファイルを `remove_file`、報告を `write_file(wd, 報告の名前, "")` にする（次の節の「処理の手順」） |
| worker の報告の追記と完了マーカー（`ndf worker append`。下の節） | 起動指示が `Write` で報告を書き、`: > "<置く場所>.done"` で完了マーカーを作る（どちらもリンクをたどり、既存の inode を書き換える） | `read_file` で今の中身を読み、標準入力の本文を足して `write_file` で置き換える。`--done` のときは続けて `write_file(wd, 完了マーカーの名前, "")` |
| supervisor の報告の読み取り（`ndf worker collect`。下の節） | `Read` で `置く場所` を読む（リンクをたどる） | `entry_state(wd, 完了マーカーの名前)` で完了マーカーを確かめ、`read_file(wd, 報告の名前)` で読んだ中身から最後の `## 作業の報告` を返す |
| `ndf answer work`（#3908 の `_write_prompt`） | `Path.write_text` の予定 | `open_workdir(paths.prompt.parent)` の中で `write_file(wd, paths.prompt.name, 本文)`。`EntryUnsafe` なら `model.Refused`（`stopped`（`precondition`））に包む |
| `ndf worker run` の起動前（`_cmd_run`・`delegate`。`src/ndf/core/worker.py:623,385`） | `Path(a.result_file).resolve()` で解いてから、`delegate` の先頭で `unlink(missing_ok=True)` | `_cmd_run` は `Path(a.result_file).absolute()` にする（リンクを解かない）。worker を起動する前に `open_workdir(結果ファイルの親)` で開き、`delegate` と結果の読み取りが終わるまで同じ `wd` を使う（`delegate(…, wd: WorkDir)` を足す）。`delegate` の先頭の削除は `remove_file(wd, 結果ファイルの名前)` にし、`EntryError` なら worker を起動せずに `stopped`（`invalid`）で返す |
| `ndf worker run` の委譲中の判定（`_written`・`_missing_record`・`_stopped_detail`。`src/ndf/core/worker.py:295,322,365`） | `_written` は `path.stat()`（リンクをたどる）で大きさを見る。`_missing_record` は `plan_files.read_json` と `search_record` で 2 回読む | `_written(wd, name)` は `entry_state` の返りの `st_size > 0` で決め、中身を読まない（今と同じくサイズだけを見る）。`_missing_record(handoff, wd)` は作業種別が `RECORD_KINDS` のときだけ、`_read_result(wd, name)`（`read_file` を 1 回呼んで `json.loads` する。JSON として解けなければ `None`）の 1 回の読み取りから `status` と探した記録の両方を見る。`EntryError`（`EntryUnsafe`・`EntryUnreadable`）は上げ直し、`delegate` はそれを受けて再起動をやめ、`cause` を `bad_report`・`detail` を名前と見つけた種類か理由にした `Delivery` を返す（結果 JSON を返す前に例外で止まらない） |
| `ndf worker run` の結果の読み取り（`outcome_of`・`search_record`。`src/ndf/core/worker.py:593,306`） | `_cmd_run` が `search_record` で 1 回、`outcome_of` が `plan_files.read_json` でもう 1 回読む（どちらもリンクをたどる） | `_cmd_run` が `delegate` の後に `_read_result(wd, name)` で 1 回だけ読み、解いた中身を `search_record(report: Any)`（パスでなく解いた中身を受ける）と `outcome_of(kind, result_file, report, items, metrics, attempts)`（`report` を足す）の両方へ渡す。`EntryError` なら `stopped`（`bad_report`。`summary` にパスと見つけた種類か理由）にし、結果ファイルの `summary` も `outputs` も探した記録も載せない |
| `ndf contracts emit --out`（`_cmd_emit`。`src/ndf/contracts/cli.py:138`） | `Path(a.out).write_text(line)`（リンクをたどり、既存の inode を切り詰める。読み取り専用のファイルは開けずに失敗する） | `open_workdir(Path(a.out).absolute().parent)` の中で `write_file(wd, 名前, line)`。worker が結果ファイルを書く通常の経路（起動指示の「作業の結果ファイル」）がこれで、worker の起動後に結果パスがリンクへ差し替わっても指示先の外を書き換えない。既存のファイルに書き込めなければ今と同じく `PermissionError` で失敗する（書き込みの手順 2）。`EntryUnsafe` なら標準出力へも行を出さずに `stopped`（`invalid`。`summary` にパスと種類）を出し、その終了コードで終える |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `open_workdir` | 開いた記述子でディレクトリの中の名前を引ける。抜けると記述子が閉じる | 作業ディレクトリへのシンボリックリンクを渡すと、リンクの先のディレクトリを開く（開くときの 1 回だけ解く） | 開いた後に作業ディレクトリの名前を別のディレクトリへのリンクへ差し替えても、`entry_state`・`read_file`・`write_file`・`remove_file` は開いた時点のディレクトリに当たり、差し替え先の同名のファイルの中身が変わらず消えもしない |
| `entry_state` | 無い名前で `None`、リンク数 1 の通常のファイルで `stat_result` | 0 バイトのファイルで `st_size` が 0 の `stat_result` | シンボリックリンク（先が有っても無くても）・作業ディレクトリの外のファイルのハードリンク・ディレクトリで `EntryUnsafe`。リンクの先のファイルを読まない |
| `read_file` | 通常のファイルの中身を返す。無ければ `None` | 0 バイトで空の文字列 | シンボリックリンク・ハードリンクで `EntryUnsafe`、リンクの先の中身を返さない。不正な UTF-8 で `EntryUnreadable`。開いた後に名前を外のファイルへのリンクへ差し替えても（`os.fstat` を差し替えた見張りの中で差し替える）、返るのは開いた inode の中身 |
| `write_file` | 無い名前に中身を書いて作る。通常のファイルを中身で置き換え、置き換えの後の inode 番号が前と違う | 空の文字列で 0 バイトになる。一時ディレクトリが作業ディレクトリに残らない | シンボリックリンク・ハードリンクの名前で `EntryUnsafe` になり、リンクの先のファイルの中身と更新時刻が変わらない。確かめの後に名前をリンクへ差し替えても（`entry_state` を差し替えて競合を作る）、リンクの先は変わらず、名前が新しいファイルになる。一時ディレクトリの `data` を書いた後に差し替えようとしても（`os.replace` の前に見張りを置く）、公開されるのは書いた inode の中身である。読み取り専用（`0o444`）の既存のファイルは `PermissionError` で、中身も inode も変わらない。書き込みを失敗させると一時ディレクトリが残らず、元のファイルの中身が残る |
| `remove_file` | 通常のファイルを消して `removed`、無ければ `absent` | 2 回続けて呼ぶと 2 回目は `absent` | 確かめの時点でリンクなら `EntryUnsafe` になり、リンクの先のファイルもリンク自体も残る。確かめの後に名前を外のファイルへのリンクへ差し替えると（`entry_state` を差し替えて競合を作る）、`removed` を返してリンクの名前が消え、リンクの先のファイルは中身ごと残る |
| `_cmd_run`（`ndf worker run`） | 通常の結果ファイルの経路は今までどおり。結果ファイルを読むのは委譲中の判定で 1 回（`RECORD_KINDS` のときだけ）と、委譲の後に 1 回だけ（`read_file` の呼び出しを数える） | 相対パスの `--result-file` でも呼んだ場所で絶対パスになる（今までどおり） | `--result-file` が作業ディレクトリの外のファイルへのリンクのとき、worker を起動せずに `stopped`（`invalid`）で、リンクの先のファイルが残る（中身も変わらない）。worker の呼び出し（差し替えた `call`）の中で結果パスを外の JSON へのリンクに差し替えると、再起動せずに `stopped`（`bad_report`）で、外の JSON の `summary` と探した記録が標準出力にも `metrics` にも載らない。結果ファイルが不正な UTF-8 のとき、例外で止まらずに `stopped`（`bad_report`）の結果 JSON を返す |
| `_cmd_emit`（`ndf contracts emit --out`） | 無い `--out` に 1 行を書き、同じ行を標準出力へ出す（今までどおり） | — | `--out` が外のファイルへのリンク・ハードリンクのとき `stopped`（`invalid`）で、リンクの先の中身が変わらない。`--out` が読み取り専用のファイルのとき今までどおり失敗し、中身が変わらない |
| `_write_prompt`（`ndf answer work`。#3908） | プロンプトを書いてから置き換わる | — | `<作業名>.prompt.md` が外のファイルへのリンクのとき `stopped`（`precondition`）で、リンクの先の中身が変わらず、置き換えが呼ばれない |

## 報告の追記と読み取りのサブコマンド（`ndf worker append`・`ndf worker collect`）

満たす決定: 決定 3、受け入れ条件 4

準備の後に報告と完了マーカーを書くのは worker、報告を読むのは supervisor である。どちらも `Write`・`Read` の Tool とシェルのリダイレクトを使うとリンクをたどるので、この 2 つのサブコマンドで上の関数を通す。呼び出しの回数は増えない（worker は `Write` と `: >` の 2 回が `ndf worker append --done` の 1 回に、supervisor は `Read` の 1 回が `ndf worker collect` の 1 回になる）。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `report_name` | `plugins/ndf/src/ndf/contracts/worker_paths.py` | 新設 | `report_name(report: Path) -> tuple[Path, str]` | 起動指示の `置く場所`（報告の絶対パス）から、作業ディレクトリと worker の名前を取り出す。最後の要素が `REPORT_SUFFIX` で終わり、残りが `NAME_RE` に合うことを確かめ、合わなければ `NameInvalid` |
| `_cmd_append` | `plugins/ndf/src/ndf/core/worker.py` | 新設 | `_cmd_append(a: argparse.Namespace) -> int` | `--report <置く場所>` の作業ディレクトリを `open_workdir` で開き、報告を `read_file` で読んで標準入力の本文を末尾へ足し、`write_file` で置き換える。`--done` があれば続けて完了マーカーを `write_file(wd, 完了マーカーの名前, "")` で作る。報告が無ければ（`None`）準備されていないとして `stopped`（`precondition`） |
| `_cmd_collect` | `plugins/ndf/src/ndf/core/worker.py` | 新設 | `_cmd_collect(a: argparse.Namespace) -> int` | `--report <置く場所>` の作業ディレクトリを開き、完了マーカーを `entry_state` で確かめ（無ければ `stopped`（`precondition`））、報告を `read_file` で読んで、最後の `## 作業の報告` から末尾までを `metrics.report` に入れた結果 JSON を 1 行出す |
| `_add_append`・`_add_collect` | `plugins/ndf/src/ndf/core/worker.py` | 新設 | `_add_append(sub: Any) -> None`・`_add_collect(sub: Any) -> None` | `--report`（必須）と、`append` だけ `--done` を登録し、`fn` を置く。`_parser` の並びに足す |

- 本文は標準入力から受ける。起動指示は引用符で囲んだ区切りのヒアドキュメント（`<<'NDF_REPORT'`）で渡す形を書く。囲んだ区切りなので `heredoc` guard（`guards/heredoc.py`）に止められず、本文の `$` も展開されない
- 追記は読み取りと置き換えの 2 回の操作で、同じ報告へ 2 つの手が同時に追記すると後の一方が残る。報告を書くのは 1 人の worker だけなので、この競合は塞がない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_cmd_append`・`_cmd_collect` | 名前の形が合わない | `置く場所` が `<名前>.report.md` の形でない | `report_name` が上げる `NameInvalid` | 呼び出し元へ返す（`WorkerError("invalid", …)` に包む） | `stopped`（`invalid`） | ファイルを触らない。打ち直せる |
| `_cmd_append`・`_cmd_collect` | 報告か完了マーカーがリンクか通常のファイルでない・読めない | 準備の後に名前を差し替えられた・不正な UTF-8 | `EntryError` | 呼び出し元へ返す（同上） | `stopped`（`invalid`）。`summary` に名前と種類か理由 | リンクの先を触らない。`append` は完了マーカーを作らないので、supervisor の待ちは上限まで続き、点検の規則（`waiting.md`）に回る |
| `_cmd_append` | 報告が無い | 準備を打たずに起動された | `read_file` の `None` | 呼び出し元へ返す | `stopped`（`precondition`） | 何も作らない |
| `_cmd_append` | 書けない | 権限・容量（`OSError`） | `write_file` | 呼び出し元へ返す | `stopped`（`invalid`） | 報告を書けなければ完了マーカーを作らない（報告の無い完了の印を作らない） |
| `_cmd_collect` | 完了マーカーが無い・報告に `## 作業の報告` が無い | worker が終えていない | `entry_state` の `None`・見出しの検索 | 呼び出し元へ返す | `stopped`（`precondition`） | 何も変えない。`waiting.md` の「報告が無いまま終わったとき」の規則で扱う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_cmd_append` | 空の報告に本文を足し、`--done` で完了マーカーが 0 バイトで現れる | 2 回足すと 2 つの本文が順に残る。`--done` 無しでは完了マーカーを作らない | 報告・完了マーカーが外のファイルへのシンボリックリンクかハードリンクのとき `stopped`（`invalid`）で、リンクの先の中身が変わらない（完了マーカーのリンクの先が空にならない）。報告の書き込みを失敗させると完了マーカーが無い |
| `_cmd_collect` | 最後の `## 作業の報告` から末尾までを `metrics.report` に返す | 見出しが 2 つあれば後ろだけを返す | 報告が外のファイルへのリンクのとき `stopped`（`invalid`）で、リンクの先の中身を返さない。完了マーカーが無いとき `stopped`（`precondition`） |

## 報告の保存先を用意するサブコマンド（`ndf worker prepare`）

満たす決定: 決定 1、決定 3、決定 5、決定 6、受け入れ条件 1、受け入れ条件 3、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_cmd_prepare` | `plugins/ndf/src/ndf/core/worker.py` | 新設 | `_cmd_prepare(a: argparse.Namespace) -> int` | 作業ディレクトリを作り、3 つのファイルの種類を先に確かめてから、前の完了マーカー → 前の結果ファイル → 報告（空にする）の順に変え、3 つの絶対パスを `metrics.paths` に入れた結果 JSON を 1 行出す |
| `_add_prepare` | `plugins/ndf/src/ndf/core/worker.py` | 新設 | `_add_prepare(sub: Any) -> None` | `prepare` のサブコマンドの引数（`--dir`・`--name`。どちらも必須）を登録し、`fn` に `_cmd_prepare` を置く |
| `_parser` | `plugins/ndf/src/ndf/core/worker.py` | 変更 | `_parser() -> argparse.ArgumentParser`（変わらない） | 登録する関数の並びに `_add_prepare` を足す。責務は変わらない |

- `_cmd_prepare`
  - 事前条件: `a.dir` と `a.name` が文字列で入っている（`argparse` が必須にする）
  - 事後条件: `<作業ディレクトリ>` が存在し、報告のファイルが 0 バイトで存在し、完了マーカーと結果ファイルが存在しない。結果 JSON の `metrics.paths` に `report`・`done`・`result` の 3 つの絶対パスが入っている
  - 副作用: ファイルシステム（ディレクトリの作成・ファイルの作成と削除）と標準出力（結果 JSON 1 行）。外部コマンドとネットワークは使わない。作業ディレクトリを作った直後に `open_workdir` で 1 回開き、確かめ・書く・消すはすべてその `wd` に対する `entry_state`・`write_file`・`remove_file`（リンクをたどらない）を通す
  - 途中で失敗したときの決まり: 前の報告が消えるのは、完了マーカーと結果ファイルが消えた後だけである。そのため、どこで止まっても「前の完了マーカーが残っているのに報告が空」という組は起きない。止まった時点の状態は次の「異常系の処理」の表のとおりで、どの行も supervisor は `status` が `ok` でない返りからパスを読まず、そのパスで待たない

プロンプトのパス（`ReportPaths.prompt`）は返りに載せない。supervisor がサブエージェントで起動する経路は、プロンプトをファイルでなく起動指示の本文で渡すためである。

サブコマンドの仕様の文書（`plugins/ndf/docs/contracts/commands.md`）の `ndf worker prepare` の節は、`ndf check --write-spec` が `_parser()` から生成する。手で書かない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_cmd_prepare` | 名前の形が合わない | 上の `report_paths` の行と同じ | `report_paths` が上げる `NameInvalid` | 呼び出し元（`main`）へ返す（`WorkerError("invalid", …)` に包む） | `stopped`（`invalid`）。`summary` に名前と許す形を書く | ファイルを触らずに返すので後始末は無い。打ち直せる |
| `_cmd_prepare` | 作業ディレクトリを作れない | 親が読めない・同名のファイルがある・容量不足（`OSError`） | `Path.mkdir(parents=True, exist_ok=True)` | 呼び出し元へ返す（`WorkerError("invalid", …)` に包む） | `stopped`（`invalid`）。`summary` にパスと `OSError` の理由を書く | 何も作っていない。原因を直せば打ち直せる |
| `_cmd_prepare` | 3 つのどれかがリンクか通常のファイルでない | 作業ディレクトリの中の名前がリンク・ディレクトリ・ほかの種類になっている | 最初の確かめ（`entry_state` を 3 つに当てる。何かを変える前） | 呼び出し元へ返す（`EntryUnsafe` を `WorkerError("invalid", …)` に包む） | `stopped`（`invalid`）。`summary` にパスと見つけた種類を書く | 1 つも変えていない（前の報告・完了マーカー・結果ファイルがそのまま残り、リンクの先も触らない）。名前を変えるか取り除けば打ち直せる |
| `_cmd_prepare` | 完了マーカーを消せない | 権限が無い（`OSError`） | `remove_file(wd, paths.done.name)`（1 つ目の変更） | 呼び出し元へ返す（同上） | `stopped`（`invalid`） | 1 つも変えていない。前の報告と前の完了マーカーがそろって残る（前の作業の完了の状態のまま）。原因を直して打ち直す |
| `_cmd_prepare` | 結果ファイルを消せない | 権限が無い（`OSError`） | `remove_file(wd, paths.result.name)`（2 つ目の変更） | 呼び出し元へ返す（同上） | `stopped`（`invalid`） | 完了マーカーだけが消え、前の報告と前の結果ファイルが残る。完了の印が無いので、前の報告を完了として読む手は無い。打ち直せる |
| `_cmd_prepare` | 報告のファイルを空にできない | 前の報告に書き込めない（`PermissionError`。書き込みの手順 2）・容量不足（`OSError`。確かめの後にリンクへ差し替えられた名前は `os.replace` が新しいファイルで置き換えるので、ここでは失敗にならない） | `write_file(wd, paths.report.name, "")`（最後の変更） | 呼び出し元へ返す（同上） | `stopped`（`invalid`） | 完了マーカーと結果ファイルは消え、前の報告は残る（`write_file` は一時ディレクトリの中身を公開するまで元の名前を触らず、失敗すれば一時ディレクトリごと消す）。打ち直せる |

失敗したときは `metrics.paths` を出さない。supervisor は `status` が `ok` の結果 JSON からだけパスを読む。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_cmd_prepare` | 作業ディレクトリが無い | 親ごと作る（`parents=True`） |
| `_cmd_prepare` | 作業ディレクトリがある | 何もしない（`exist_ok=True`） |
| `_cmd_prepare` | 3 つのどれかがリンク・ディレクトリ・ほかの種類 | 何も変えずに `stopped`（`invalid`） |
| `_cmd_prepare` | 完了マーカーが無い / ある | 無ければ何もしない（`absent`）/ あれば消す（`removed`）。作らない |
| `_cmd_prepare` | 結果ファイルが無い / ある | 無ければ何もしない（`absent`）/ あれば消す（`removed`）。作らない |
| `_cmd_prepare` | 報告のファイルが無い / ある | どちらも 0 バイトの新しいファイルで置き換える（前の報告の名前は新しい inode を指す。前の inode を切り詰めない。`items` の `action` は `created`） |

変える順は表の上から下で、確かめ → 完了マーカー → 結果ファイル → 報告である。完了マーカーを最初に消すのは、後の手順で止まったときに「前の完了の印が残るのに前の報告が無い」組を作らないためである（上の「途中で失敗したときの決まり」）。

同じ `--dir` と `--name` で 2 回打つと、1 回目の完了マーカーと結果ファイルが消え、報告は空になる。supervisor が 2 回目の `.done` を待つとき、1 回目のマーカーを完了と読むことはない（受け入れ条件 3）。繰り返しの処理は無い。扱うファイルは 3 つで、計算量の注意は無い。

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_cmd_prepare` | `metrics.paths` に `report`・`done`・`result` の 3 つの絶対パス。`items` に触ったパスを `{"kind": "report" / "done" / "result", "path": <絶対パス>, "action": "created" / "removed" / "absent"}` で 3 件。`summary` は「報告の保存先を用意した（報告 …・完了マーカー …・結果 …）」 | 結果 JSON（標準出力 1 行） | 報告のファイルの中身。前の作業の結果ファイルの中身。ログの出力は足さない（ファイルの始末だけの処理で、記録する状態を持たない） |

結果 JSON の `outputs` は使わない（決定 5）。`outputs` は工程の文脈のキーの閉じた語彙である。`contracts/schemas/result.schema.json` の `$defs/outputs` が `additionalProperties: false` を持つ。`result.emit`（`src/ndf/contracts/result.py:144-156`）がそれで検査する。`metrics` は自由な形（`result.schema.json` の `metrics` は型だけを決める）なので、スキーマを触らずに 3 つのパスを渡せる。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_cmd_prepare` | `Path.unlink(missing_ok=True)` | `grep -rn "missing_ok=True" plugins/ndf/src/ndf/`（8 箇所で使っている） | 使えない。種類を確かめずに消すので、リンクのときの扱いを決められない。`remove_file`（作業ディレクトリの記述子に対する `os.stat(follow_symlinks=False)` → `os.unlink`）を通す |
| `_cmd_prepare` | `Path.mkdir(parents=True, exist_ok=True)` | `grep -rc "mkdir(parents=True, exist_ok=True)" -r plugins/ndf/src/ndf/`（10 以上のモジュールで使っている） | 使う |
| `_cmd_prepare` | `identity.prepare_state_dir`（`src/ndf/identity.py:462`） | `grep -n "def prepare_state_dir" -A 6 plugins/ndf/src/ndf/identity.py` | 使えない。NDF 自身の状態の保存先を用意して旧い保存先を退避する処理で、起動指示で渡された任意のディレクトリには当たらない |
| `_cmd_prepare` | `ndf.core.plan.files.write_json` / `relay.signals.replace_json` | `grep -rn "def write_json\|def replace_json" plugins/ndf/src/ndf/` | 使えない。どちらも JSON を書く関数で、0 バイトのファイルを作る用途に合わない（`write_file(wd, paths.report.name, "")` で足りる） |
| `_cmd_prepare` | `result.result` / `result.stopped`（`src/ndf/contracts/result.py:40,65`） | `grep -n "def result\|def stopped" plugins/ndf/src/ndf/contracts/result.py` | 使う。`metrics` と `items` は `result.result` の引数に既にある |
| `_add_prepare` | `_add_run`・`_add_closing`（`src/ndf/core/worker.py:702,711`） | `grep -n "def _add_" plugins/ndf/src/ndf/core/worker.py` | 使う。同じ形（`sub.add_parser` → `add_argument` → `set_defaults(fn=…)`）をそのまま踏襲する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_cmd_prepare` | `--dir D --name n` が `status: ok` を返し、`metrics.paths` の 3 つが絶対パスで、報告が 0 バイトで存在し、完了マーカーと結果ファイルが無い（受け入れ条件 1・4）。`items` が 3 件で `action` を持つ | 無いディレクトリを渡すと作る。相対パスの `--dir` でも `metrics.paths` は絶対パス。2 回目の呼び出しで 1 回目の `.done` と結果ファイルが消え、報告が空に戻る（受け入れ条件 3） | 名前が `../x`・空文字のとき `stopped`（`invalid`）で、`--dir` のディレクトリが作られていない。`--dir` に同名のファイルがあるとき `stopped`（`invalid`）。`<名前>.report.md`・`<名前>.report.md.done`・`<名前>.result.json` のどれかが作業ディレクトリの外のファイルへのシンボリックリンクかハードリンクのとき、`stopped`（`invalid`）で、リンクの先（ハードリンクなら共有する利用者のファイル）の中身が変わらず消えもせず、前の報告も残る（3 通り × 2 種類）。結果ファイルの削除だけを失敗させる（`remove_file` を差し替える）と、前の報告の中身が残り、完了マーカーは無い。完了マーカーの削除を失敗させると、前の報告と完了マーカーがそろって残る。報告の書き込みを失敗させると、前の報告の中身が残る |

## 空かもしれない変数の `rm` を止める guard（`rm-variable`）

満たす決定: 決定 4、受け入れ条件 5

この構成要素は、実装の最初に「`PreToolUse` の guard が Claude Code の安全確認より先に働く」ことを実機で確かめてから足す（設計文書の決定 4 と「未確認のまま残ること」）。先に働かなければ、確かめた結果を Pull Request に残して、この構成要素とテストを落とす。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `risky_removal` | `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 新設 | `risky_removal(script: ShellScript) -> str \| None` | `rm` の削除の対象に、展開が効く位置（引用の外か二重引用の中）の空になりうる変数を含む語があれば、その語の字面を返す。無ければ `None` |
| `expanded_variables` | `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 新設 | `expanded_variables(word: str) -> list[str]` | 語の字面を左から 1 字ずつ読み、展開が効く位置の `$NAME`・`${…}` だけを返す。単引用の中・`\$` で逃がした `$`・`$'…'` の中は返さない |
| `check` | `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 新設 | `check(ctx, svc) -> Verdict \| None` | `risky_removal` が語を返したら `deny` に書き直しの形を添えて返す。ほかは `None`（通す） |

- `risky_removal`
  - 事前条件: `script` は `ctx.shell`（`domains/hooks/context.py:579` の `ShellScript`）である。`commands` の各要素が語ごとの字面（`source`）を持つ
  - 事後条件: 返った語は `script` の中の `rm` の引数のどれかの字面と一致する。判定は `source`（引用とエスケープを残した字面）を `expanded_variables` で読んだ結果だけで行い、値の展開はしない。ドル記号が字面にあっても、引用かエスケープで文字のまま渡る語は止めない
- `expanded_variables`
  - 事前条件: `word` は `ShellCommand.source` の 1 要素（引用とエスケープを残した字面）
  - 事後条件: 状態は「引用の外」「単引用の中」「二重引用の中」の 3 つで、`'` は引用の外でだけ単引用を始め、単引用の中では次の `'` まで何も解かない。`\` は引用の外と二重引用の中で次の 1 字を文字にする（二重引用の中では `$`・`` ` ``・`"`・`\` の前でだけ）。`$NAME`・`${…}` は引用の外か二重引用の中で、逃がされていないときだけ返す
  - 副作用: 無し
  - 副作用: 無し（文字列の判定だけ。シェルを起動しない）
- `check`
  - 事前条件: guard の表の行（`events` が `PreToolUse`・`tools` が `Bash`）から呼ばれる
  - 事後条件: 判定を返すだけで、ファイルも計測の記録も書かない（記録は guard のエントリポイントが行う）
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `risky_removal` | コマンドを語に分けられない | `ctx.shell` が字面を持たない（読めないコマンド） | `script.commands` が空か `source` が空 | 既定値で続ける（`None` を返して通す） | 出力なし（Bash はそのまま走る） | 後始末は無い |
| `check` | 判定の中の例外 | 想定しない形の入力 | guard のエントリポイント（`domains/hooks/dispatch`） | 既定で通す（`guards.json` の `fail_closed` は偽にする。報告の保存先の用意を hook の不具合で止めない） | 出力なし | 後始末は無い |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `risky_removal` | コマンド名が `rm` でない | 見ない（`None`） |
| `risky_removal` | `rm` の引数が固定のパスだけ | `None`（通す） |
| `risky_removal` | 引数の展開が効く位置（`expanded_variables` の返り）に `$NAME` か `${NAME}` があり、`${NAME:?…}`・`${NAME:-…}`・`${NAME:+…}` の形でない | その語を返す（止める） |
| `risky_removal` | 展開が効く位置の変数が `${NAME:?}` の形だけ | `None`（空なら shell が止めるので、確認の条件に当たらない） |
| `risky_removal` | `$` が単引用の中（`'a$b'`）・`\$` で逃がされている（`a\$b`・`"a\$b"`）だけ | `None`（ファイル名の文字で、展開されない） |
| `check` | `risky_removal` が語を返した | `deny("…空になりうる変数 … を含む `rm` を止めた。報告の保存先の用意は `ndf worker prepare --dir <作業ディレクトリ> --name <worker の名前>` で打つ。ほかの削除は `${NAME:?}` の形か固定のパスにする")` |

繰り返しは `script.commands` の数（1 回の Bash の呼び出しに現れる単純なコマンドの数）で終わる。計算量の注意は無い。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `_VARIABLE` | 定数（`re.Pattern`） | `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | `expanded_variables` が返した 1 つの展開に当て、`$NAME` と `${NAME}` に当たり、`${NAME:?…}`・`${NAME:-…}`・`${NAME:+…}` に当たらない形（語の字面全体には当てない） |
| `rm-variable` の行 | 表の 1 行 | `plugins/ndf/contracts/hooks/guards.json` | `events: ["PreToolUse"]`・`tools: ["Bash"]`・`order: 26`（`heredoc` の 25 と `sleep` の 30 の間）・`strict: false`・`deny_key: "none"`・`fail_closed: false`・`suggests` に `ndf worker prepare` の見本 |

`deny_key` を `"none"` にするのは、同じ拒否を数えて上限の後に案内へ下げる仕組み（`domains/hooks/dispatch.py:137,150-152` の `_counted`。`fail_closed` が偽で `deny_key` が `none` でない行が下がる）から外すためである。`command` にすると、supervisor が同じ `rm` を繰り返したときに上限の後で拒否が案内に下がり、Bash が走って Claude Code の確認の画面でレーンが止まる。`fail_closed` は偽のままにする（判定の中の例外は通す側へ倒す。上の「異常系の処理」）。`none` と `fail_closed: false` の組は `guards.json` の既存の行（`deny_key` が `none` の行）と同じである |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `risky_removal` | `ctx.shell`（`ShellScript`。`domains/hooks/context.py:45-64,579`）の `ShellCommand.source`（語ごとの引用とエスケープを残した字面） | `grep -n "class ShellCommand" -A 25 plugins/ndf/src/ndf/domains/hooks/context.py` | 使う。展開前の字面を読める唯一の材料で、`$W` が空かどうかを見ずに形だけで判定できる |
| `risky_removal` | `guards/shell.py` の `is_ndf`・`background_call` | `grep -n "^def " plugins/ndf/src/ndf/domains/hooks/guards/shell.py`（共有の判定はこの 2 つだけ） | 使えない。NDF のコマンドかと背景の起動かの判定で、削除の対象の語を見る処理は無い |
| `risky_removal` | `guards/sleep.py`・`guards/process_wait.py` の走査 | `grep -n "ctx.shell" plugins/ndf/src/ndf/domains/hooks/guards/*.py` | 使えない（同じ関数は無い）。`script.commands` を順に見る形だけ踏襲する |
| `check` | `verdict.deny`（`domains/hooks/verdict.py`） | `grep -n "def deny" plugins/ndf/src/ndf/domains/hooks/verdict.py` | 使う。`heredoc` guard（`guards/heredoc.py:53`）と同じ返し方にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `risky_removal` | `rm -f "$W/$n.report.md.done"` でその語を返す。`rm -rf /tmp/x` で `None` | `rm -f "${W:?}/x"` は `None`。`${W:-/tmp}` も `None`。`rm` を含まない行は `None`。ドル記号を含む固定の名前の `rm -f 'cost$5.txt'`・`rm -f cost\$5.txt`・`rm -f "cost\$5.txt"` は `None`。引用を混ぜた `rm -f 'a$b'"$X"` は `$X` があるので語を返す | 空のコマンド・語に分けられないコマンドで `None`（例外を上げない） |
| `expanded_variables` | `"$W/x"` で `$W`、`a${B}c` で `${B}` | `'$W'` と `\$W` で空。`"\$W"` で空。`"'$W'"`（二重引用の中の単引用の字）で `$W` | 閉じていない引用（`'a$b`）でも例外を上げず、閉じていない単引用の中は返さない |
| `check` | 止める形で `deny` を返し、理由に `ndf worker prepare` の打ち方が入る | 通す形で `None` | 判定の中の例外を guard のエントリポイントが拾い、`fail_closed` が偽なので呼び出しを通す |
| guard の表の `rm-variable` の行（dispatch と組む） | 同じ `rm -f "$W/x"` を 1 回打つと拒否される | 同じ拒否の上限（`limits.json` の拒否の回数）に達した後も、同じコマンドを打つと拒否のまま（案内に下がらない。`deny_key` が `none`）。数え手は増えない | — |

## 手順書の報告の保存先の用意（`agent-layers.md`・`waiting.md`）

満たす決定: 決定 1、決定 5、受け入れ条件 1、受け入れ条件 2

関数を持たない構成要素で、変えるのは次の 6 か所だけである（`agent-layers.md` は並行するほかのスプリントも触るため、該当の行の外を変えない）。

| ファイル:行 | 今の中身 | 変えた後 |
| --- | --- | --- |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md:235` | 起動指示の「置く場所」の欄が、worker には `## 作業の報告` を末尾へ書いて `: > "<置く場所>.done"` で完了マーカーを作ること、supervisor には `: > <置く場所>; rm -f <置く場所>.done` を書く | supervisor は `ndf worker prepare --dir <作業ディレクトリ> --name <worker の名前>` を 1 回打ち、結果 JSON の `metrics.paths.report` を「置く場所」に書き写す。待つのは `metrics.paths.done` である。worker は長い出力を `ndf worker append --report <置く場所>`、最後の `## 作業の報告` を `ndf worker append --report <置く場所> --done` で、どちらも本文を `<<'NDF_REPORT'` のヒアドキュメントで渡して書く。`Write`・`Edit` の Tool とリダイレクト（`>`・`>>`・`: >`）で `置く場所` と完了マーカーを書かない、と書く |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md:252` | 守る規則が「`置く場所` へ報告コピーを書き、その後に `<置く場所>.done` を作る」を書く | 同じ 1 回の `ndf worker append --report <置く場所> --done` で書く、に書き換える |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md:236` | 起動指示の「作業の結果ファイル」の欄が「新しいパスを決め、前のファイルを消してから渡す」を書く | 同じ 1 回の `ndf worker prepare` の結果 JSON の `metrics.paths.result` を書き写す、とだけ書く。パスを決める・前のファイルを消す指示は外す（前のファイルは `ndf worker prepare` が消している）。成否を `status` で決める今の文はそのまま残す |
| `plugins/ndf/skills/development-workflow/references/waiting.md:229` | 「中間通知を受けたとき」が同じ 2 つのコマンドを書く | 同じ 1 行のコマンドへ書き換え、待つパスは `metrics.paths.done` から取ると書く |
| `plugins/ndf/skills/development-workflow/references/waiting.md:233` | worker が報告コピーを書いた後に完了マーカーを作る、と書く | `ndf worker append --report <置く場所> --done` が報告と完了マーカーを続けて書く、に書き換える |
| `plugins/ndf/skills/development-workflow/references/waiting.md:243` | 待ちの終了コード 0 で「`置く場所` の最後の `## 作業の報告` から末尾までを読み」と書く（`Read` で読む） | `ndf worker collect --report <置く場所>` を打ち、`status` が `ok` なら `metrics.report` を報告として読む。`ok` でなければ「報告が無いまま終わったとき」の規則で扱う、と書く。待ちのコマンド（`[ -e "$1.done" ]`）は有無を見るだけで書き込まないので変えない（完了マーカーの種類は `collect` が確かめる） |

6 か所のどれにも、報告の保存先や結果ファイルのパスを組み立てるシェルの変数・`rm`・別の削除の指示を残さない（受け入れ条件 1・2）。「置く場所」と「作業の結果ファイル」の 2 つの欄は同じ 1 回の `ndf worker prepare` の返りを使い、準備のコマンドを 2 回打たない。この 6 か所が変わったことは、実装で 2 つのファイルを読んで確かめる。`.md` の文言を突き合わせる自動テストは置かない（文言を固定しても振る舞いを保証しないため。`AGENTS.md` の「DON'T」）。
