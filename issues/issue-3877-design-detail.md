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
│   ├── contracts/worker_paths.py *                  report_paths（パスの決め方の唯一の保存先）・リンクをたどらない読み書き
│   ├── core/worker.py                               _cmd_prepare・_add_prepare・_cmd_run・delegate
│   └── domains/hooks/guards/rm_variable.py *        決定 4 の guard
└── tests/
    ├── contracts/test_worker_paths.py *
    ├── core/test_worker_prepare.py *
    └── domains/hooks/test_rm_variable.py *
```

依存の向き: `contracts/worker_paths.py` は標準ライブラリ（`os`・`pathlib`・`re`・`stat`・`typing`）だけに依存し、`core` も `progress` も読まない。`core/worker.py`（`ndf worker prepare`）と `progress/gate/entry.py`（`ndf answer work`。#3908）がそれを読む一方向で、`progress/gate/entry.py:95` の「worker の層は読まず、サブコマンドのコントラクトだけを使う」（IG1）に触れない（決定 7）。guard（`domains/hooks/guards/rm_variable.py`）は `domains/hooks/verdict` と `re` だけを読む。`contracts/worker_paths.py` も `core/worker.py` も読まない。手順書はコマンドの字面を書くだけで、コードから参照されない。循環は無い。型の関係は設計文書の「構成要素」のモジュールの図にある。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/contracts/worker_paths.py` | 新設 | 作業ディレクトリと名前から、worker の 4 つのファイルのパスを組み、既存のリンクをたどらずに書き・消す | `os`・`pathlib`・`re`・`stat`・`typing` | モジュール `ndf.contracts.worker_paths` | `ReportPaths`（新設）・`NameInvalid`（新設）・`EntryUnsafe`（新設）・`report_paths`（新設）・`entry_state`（新設）・`write_file`（新設）・`remove_file`（新設） |
| `plugins/ndf/src/ndf/core/worker.py` | 変更 | 報告の保存先を用意するサブコマンドを足し、`worker run` の結果ファイルをリンクをたどらずに扱う | `ndf.contracts.worker_paths`・`ndf.contracts.result` | モジュール `ndf.core.worker` | `_cmd_prepare`（新設）・`_add_prepare`（新設）・`_parser`（変更）・`_cmd_run`（変更）・`delegate`（変更） |
| `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 新設 | 空かもしれない変数をパスに含む `rm` を止める guard | `re`・`ndf.domains.hooks.verdict` | モジュール `ndf.domains.hooks.guards.rm_variable` | `risky_removal`（新設）・`expanded_variables`（新設）・`check`（新設） |
| `plugins/ndf/contracts/hooks/guards.json` | 変更 | guard の表に `rm-variable` の 1 行を足す | 無し（データ） | 表 `guards` | 無し |
| `plugins/ndf/docs/contracts/commands.md` | 変更 | `ndf worker prepare` の節（生成物） | 無し（生成物） | 無し | 無し |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 変更 | 起動指示の 2 つの欄をコマンド 1 行に書き換える | 無し（文書） | 無し | 無し |
| `plugins/ndf/skills/development-workflow/references/waiting.md` | 変更 | 用意の記述を同じコマンドに書き換える | 無し（文書） | 無し | 無し |
| `plugins/ndf/tests/contracts/test_worker_paths.py` | 新設 | パスの組み立てと名前の形 | `ndf.contracts.worker_paths` | モジュール | テスト関数 |
| `plugins/ndf/tests/core/test_worker_prepare.py` | 新設 | サブコマンドの返りとファイルの始末（リンクと途中の失敗を含む） | `ndf.core.worker` | モジュール | テスト関数 |
| `plugins/ndf/tests/domains/hooks/test_rm_variable.py` | 新設 | guard の判定（決定 4 の条件つき） | `ndf.domains.hooks` | モジュール | テスト関数 |

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
## リンクをたどらない読み書き（`entry_state`・`write_file`・`remove_file`）

満たす決定: 決定 3、決定 7、受け入れ条件 4

作業ディレクトリは supervisor が渡す任意のディレクトリで、その中に前の作業や別の手が作った名前が残りうる。名前の検査（`NAME_RE`）はパスの字面を作業ディレクトリの直下に限るだけで、その名前がリンクなら書き込みと削除はリンクの先（作業ディレクトリの外の利用者のファイル）に当たる。そこで、worker の 4 つのファイルを書く・消す処理はすべてこの 3 つの関数を通し、既存のリンクをたどらない。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `EntryUnsafe` | `plugins/ndf/src/ndf/contracts/worker_paths.py` | 新設 | `class EntryUnsafe(ValueError)` | パスがリンクか通常のファイルでないことを呼び出し元へ伝える（パスと見つけた種類を持つ） |
| `entry_state` | 同上 | 新設 | `entry_state(path: Path) -> Literal["absent", "file"]` | `os.lstat`（リンクをたどらない）で、無いか通常のファイルかを返す。リンク・ディレクトリ・ほかの種類は `EntryUnsafe` |
| `write_file` | 同上 | 新設 | `write_file(path: Path, text: str) -> None` | `os.open(path, O_WRONLY \| O_CREAT \| O_TRUNC \| O_NOFOLLOW, 0o600)` で開いて書く。最後の要素がリンクなら開けずに `ELOOP` の `OSError` になる |
| `remove_file` | 同上 | 新設 | `remove_file(path: Path) -> Literal["absent", "removed"]` | `entry_state` で確かめてから `os.unlink` する。リンクは消さずに `EntryUnsafe`（リンクの先も消さない） |

- 3 つとも、パスに `resolve()` を当てない。親のディレクトリ（`report_paths` が解いた作業ディレクトリ）だけはリンクを解いてよい（作業ディレクトリそのものは supervisor が選んだ場所である）
- `write_file` と `remove_file` は、`entry_state` で見てから書く・消すまでの間に名前を差し替えられる競合を `O_NOFOLLOW`（書く側）で塞ぐ。消す側の `os.unlink` は最後の要素のリンクをたどらない（リンクだけを消す）ので、競合で差し替えられても作業ディレクトリの外のファイルは消えない

### 使う側

| 使う所 | 今の書き方 | 変えた後 |
| --- | --- | --- |
| `ndf worker prepare`（`_cmd_prepare`） | この設計で新設 | 報告は `write_file(paths.report, "")`、完了マーカーと結果ファイルは `remove_file`。どれも先に `entry_state` で 3 つまとめて確かめる（次の節の「処理の手順」） |
| `ndf answer work`（#3908 の `_write_prompt`） | `Path.write_text` の予定 | `write_file(paths.prompt, 本文)`。リンクなら `model.Refused`（`stopped`（`precondition`））に包む |
| `ndf worker run`（`_cmd_run`・`delegate`。`src/ndf/core/worker.py:623,385`） | `Path(a.result_file).resolve()` で解いてから、`delegate` の先頭で `unlink(missing_ok=True)` | `_cmd_run` は `Path(a.result_file).absolute()` にする（リンクを解かない）。`delegate` の先頭の削除は `remove_file` にし、`EntryUnsafe` なら worker を起動せずに `stopped`（`invalid`）で返す。結果ファイルを読む `plan_files.read_json` の前にも `entry_state` で確かめ、リンクに替わっていれば `bad_report` にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `entry_state` | 無いパスで `absent`、通常のファイルで `file` | 0 バイトのファイルも `file` | リンク（先が有っても無くても）・ディレクトリで `EntryUnsafe`。リンクの先のファイルを読まない |
| `write_file` | 無いパスに中身を書いて作る。通常のファイルを中身で置き換える | 空の文字列で 0 バイトになる | 作業ディレクトリの外のファイルを指すリンクのパスで `EntryUnsafe`（または `ELOOP` の `OSError`）になり、リンクの先のファイルの中身と更新時刻が変わらない |
| `remove_file` | 通常のファイルを消して `removed`、無ければ `absent` | 2 回続けて呼ぶと 2 回目は `absent` | リンクのパスで `EntryUnsafe` になり、リンクの先のファイルもリンク自体も残る |
| `_cmd_run`（`ndf worker run`） | 通常の結果ファイルの経路は今までどおり | 相対パスの `--result-file` でも呼んだ場所で絶対パスになる（今までどおり） | `--result-file` が作業ディレクトリの外のファイルへのリンクのとき、worker を起動せずに `stopped`（`invalid`）で、リンクの先のファイルが残る（中身も変わらない） |
| `_write_prompt`（`ndf answer work`。#3908） | プロンプトを書いてから置き換わる | — | `<作業名>.prompt.md` が外のファイルへのリンクのとき `stopped`（`precondition`）で、リンクの先の中身が変わらず、置き換えが呼ばれない |

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
  - 副作用: ファイルシステム（ディレクトリの作成・ファイルの作成と削除）と標準出力（結果 JSON 1 行）。外部コマンドとネットワークは使わない。書く・消すはすべて `write_file`・`remove_file`（リンクをたどらない）を通す
  - 途中で失敗したときの約束: 前の報告が消えるのは、完了マーカーと結果ファイルが消えた後だけである。そのため、どこで止まっても「前の完了マーカーが残っているのに報告が空」という組は起きない。止まった時点の状態は次の「異常系の処理」の表のとおりで、どの行も supervisor は `status` が `ok` でない返りからパスを読まず、そのパスで待たない

プロンプトのパス（`ReportPaths.prompt`）は返りに載せない。supervisor がサブエージェントで起動する経路は、プロンプトをファイルでなく起動指示の本文で渡すためである。

サブコマンドの仕様の文書（`plugins/ndf/docs/contracts/commands.md`）の `ndf worker prepare` の節は、`ndf check --write-spec` が `_parser()` から生成する。手で書かない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_cmd_prepare` | 名前の形が合わない | 上の `report_paths` の行と同じ | `report_paths` が上げる `NameInvalid` | 呼び出し元（`main`）へ返す（`WorkerError("invalid", …)` に包む） | `stopped`（`invalid`）。`summary` に名前と許す形を書く | ファイルを触らずに返すので後始末は無い。打ち直せる |
| `_cmd_prepare` | 作業ディレクトリを作れない | 親が読めない・同名のファイルがある・容量不足（`OSError`） | `Path.mkdir(parents=True, exist_ok=True)` | 呼び出し元へ返す（`WorkerError("invalid", …)` に包む） | `stopped`（`invalid`）。`summary` にパスと `OSError` の理由を書く | 何も作っていない。原因を直せば打ち直せる |
| `_cmd_prepare` | 3 つのどれかがリンクか通常のファイルでない | 作業ディレクトリの中の名前がリンク・ディレクトリ・ほかの種類になっている | 最初の確かめ（`entry_state` を 3 つに当てる。何かを変える前） | 呼び出し元へ返す（`EntryUnsafe` を `WorkerError("invalid", …)` に包む） | `stopped`（`invalid`）。`summary` にパスと見つけた種類を書く | 1 つも変えていない（前の報告・完了マーカー・結果ファイルがそのまま残り、リンクの先も触らない）。名前を変えるか取り除けば打ち直せる |
| `_cmd_prepare` | 完了マーカーを消せない | 権限が無い（`OSError`） | `remove_file(paths.done)`（1 つ目の変更） | 呼び出し元へ返す（同上） | `stopped`（`invalid`） | 1 つも変えていない。前の報告と前の完了マーカーがそろって残る（前の作業の完了の状態のまま）。原因を直して打ち直す |
| `_cmd_prepare` | 結果ファイルを消せない | 権限が無い（`OSError`） | `remove_file(paths.result)`（2 つ目の変更） | 呼び出し元へ返す（同上） | `stopped`（`invalid`） | 完了マーカーだけが消え、前の報告と前の結果ファイルが残る。完了の印が無いので、前の報告を完了として読む手は無い。打ち直せる |
| `_cmd_prepare` | 報告のファイルを空にできない | 書き込みの権限が無い・容量不足（`OSError`。リンクに差し替えられた競合は `O_NOFOLLOW` の `ELOOP`） | `write_file(paths.report, "")`（最後の変更） | 呼び出し元へ返す（同上） | `stopped`（`invalid`） | 完了マーカーと結果ファイルは消え、前の報告は残る（`O_TRUNC` は開けた後にだけ効くので、開けなければ中身は消えない）。打ち直せる |

失敗したときは `metrics.paths` を出さない。supervisor は `status` が `ok` の結果 JSON からだけパスを読む。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_cmd_prepare` | 作業ディレクトリが無い | 親ごと作る（`parents=True`） |
| `_cmd_prepare` | 作業ディレクトリがある | 何もしない（`exist_ok=True`） |
| `_cmd_prepare` | 3 つのどれかがリンク・ディレクトリ・ほかの種類 | 何も変えずに `stopped`（`invalid`） |
| `_cmd_prepare` | 完了マーカーが無い / ある | 無ければ何もしない（`absent`）/ あれば消す（`removed`）。作らない |
| `_cmd_prepare` | 結果ファイルが無い / ある | 無ければ何もしない（`absent`）/ あれば消す（`removed`）。作らない |
| `_cmd_prepare` | 報告のファイルが無い / ある | どちらも 0 バイトで作り直す（前の報告は消える。`items` の `action` は `created`） |

変える順は表の上から下で、確かめ → 完了マーカー → 結果ファイル → 報告である。完了マーカーを最初に消すのは、後の段で止まったときに「前の完了の印が残るのに前の報告が無い」組を作らないためである（上の「途中で失敗したときの約束」）。

同じ `--dir` と `--name` で 2 回打つと、1 回目の完了マーカーと結果ファイルが消え、報告は空になる。supervisor が 2 回目の `.done` を待つとき、1 回目のマーカーを完了と読むことはない（受け入れ条件 3）。繰り返しの処理は無い。扱うファイルは 3 つで、計算量の注意は無い。

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_cmd_prepare` | `metrics.paths` に `report`・`done`・`result` の 3 つの絶対パス。`items` に触ったパスを `{"kind": "report" / "done" / "result", "path": <絶対パス>, "action": "created" / "removed" / "absent"}` で 3 件。`summary` は「報告の保存先を用意した（報告 …・完了マーカー …・結果 …）」 | 結果 JSON（標準出力 1 行） | 報告のファイルの中身。前の作業の結果ファイルの中身。ログの出力は足さない（ファイルの始末だけの処理で、記録する状態を持たない） |

結果 JSON の `outputs` は使わない（決定 5）。`outputs` は工程の文脈のキーの閉じた語彙である。`contracts/schemas/result.schema.json` の `$defs/outputs` が `additionalProperties: false` を持つ。`result.emit`（`src/ndf/contracts/result.py:144-156`）がそれで検査する。`metrics` は自由な形（`result.schema.json` の `metrics` は型だけを決める）なので、スキーマを触らずに 3 つのパスを渡せる。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_cmd_prepare` | `Path.unlink(missing_ok=True)` | `grep -rn "missing_ok=True" plugins/ndf/src/ndf/`（8 箇所で使っている） | 使えない。種類を確かめずに消すので、リンクのときの扱いを決められない。`remove_file`（`os.lstat` → `os.unlink`）を通す |
| `_cmd_prepare` | `Path.mkdir(parents=True, exist_ok=True)` | `grep -rc "mkdir(parents=True, exist_ok=True)" -r plugins/ndf/src/ndf/`（10 以上のモジュールで使っている） | 使う |
| `_cmd_prepare` | `identity.prepare_state_dir`（`src/ndf/identity.py:462`） | `grep -n "def prepare_state_dir" -A 6 plugins/ndf/src/ndf/identity.py` | 使えない。NDF 自身の状態の保存先を用意して旧い保存先を退避する処理で、起動指示で渡された任意のディレクトリには当たらない |
| `_cmd_prepare` | `ndf.core.plan.files.write_json` / `relay.signals.replace_json` | `grep -rn "def write_json\|def replace_json" plugins/ndf/src/ndf/` | 使えない。どちらも JSON を書く関数で、0 バイトのファイルを作る用途に合わない（`Path.write_text("")` で足りる） |
| `_cmd_prepare` | `result.result` / `result.stopped`（`src/ndf/contracts/result.py:40,65`） | `grep -n "def result\|def stopped" plugins/ndf/src/ndf/contracts/result.py` | 使う。`metrics` と `items` は `result.result` の引数に既にある |
| `_add_prepare` | `_add_run`・`_add_closing`（`src/ndf/core/worker.py:702,711`） | `grep -n "def _add_" plugins/ndf/src/ndf/core/worker.py` | 使う。同じ形（`sub.add_parser` → `add_argument` → `set_defaults(fn=…)`）をそのまま踏襲する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_cmd_prepare` | `--dir D --name n` が `status: ok` を返し、`metrics.paths` の 3 つが絶対パスで、報告が 0 バイトで存在し、完了マーカーと結果ファイルが無い（受け入れ条件 1・4）。`items` が 3 件で `action` を持つ | 無いディレクトリを渡すと作る。相対パスの `--dir` でも `metrics.paths` は絶対パス。2 回目の呼び出しで 1 回目の `.done` と結果ファイルが消え、報告が空に戻る（受け入れ条件 3） | 名前が `../x`・空文字のとき `stopped`（`invalid`）で、`--dir` のディレクトリが作られていない。`--dir` に同名のファイルがあるとき `stopped`（`invalid`）。`<名前>.report.md`・`<名前>.report.md.done`・`<名前>.result.json` のどれかが作業ディレクトリの外のファイルへのリンクのとき、`stopped`（`invalid`）で、リンクの先のファイルの中身が変わらず消えもせず、前の報告も残る（3 通り）。結果ファイルの削除だけを失敗させる（`remove_file` を差し替える）と、前の報告の中身が残り、完了マーカーは無い。完了マーカーの削除を失敗させると、前の報告と完了マーカーがそろって残る。報告の書き込みを失敗させると、前の報告の中身が残る |

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

関数を持たない構成要素で、変えるのは次の 3 行だけである（`agent-layers.md` は並行するほかのスプリントも触るため、該当の行の外を変えない）。

| ファイル:行 | 今の中身 | 変えた後 |
| --- | --- | --- |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md:235` | 起動指示の「置く場所」の欄が `: > <置く場所>; rm -f <置く場所>.done` を書く | supervisor は `ndf worker prepare --dir <作業ディレクトリ> --name <worker の名前>` を 1 回打ち、結果 JSON の `metrics.paths.report` を「置く場所」に書き写す。待つのは `metrics.paths.done` である、とだけ書く |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md:236` | 起動指示の「作業の結果ファイル」の欄が「新しいパスを決め、前のファイルを消してから渡す」を書く | 同じ 1 回の `ndf worker prepare` の結果 JSON の `metrics.paths.result` を書き写す、とだけ書く。パスを決める・前のファイルを消す指示は外す（前のファイルは `ndf worker prepare` が消している）。成否を `status` で決める今の文はそのまま残す |
| `plugins/ndf/skills/development-workflow/references/waiting.md:229` | 「中間通知を受けたとき」が同じ 2 つのコマンドを書く | 同じ 1 行のコマンドへ書き換え、待つパスは `metrics.paths.done` から取ると書く |

3 行のどれにも、報告の保存先や結果ファイルのパスを組み立てるシェルの変数・`rm`・別の削除の指示を残さない（受け入れ条件 1・2）。「置く場所」と「作業の結果ファイル」の 2 つの欄は同じ 1 回の `ndf worker prepare` の返りを使い、準備のコマンドを 2 回打たない。この 3 行が変わったことは、実装で 2 つのファイルを読んで確かめる。`.md` の文言を突き合わせる自動テストは置かない（文言を固定しても振る舞いを保証しないため。`AGENTS.md` の「DON'T」）。
