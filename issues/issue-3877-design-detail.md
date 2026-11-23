# #3877 の詳細設計: 報告の置き場所を `ndf worker slot` で用意する

設計文書 [issue-3877-design.md](./issue-3877-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── hooks/guards.json                            guard の表（決定 4 で 1 行足す）
│   └── schemas/result.schema.json                   結果 JSON の形（outputs のキーを 3 つ足す）
├── docs/contracts/commands.md                       サブコマンドの仕様（ndf check --write-spec の生成物）
├── skills/development-workflow/references/
│   ├── agent-layers.md                              起動指示の「置く場所」「作業の結果ファイル」の欄
│   └── waiting.md                                   「中間通知を受けたとき」の用意の記述
├── src/ndf/
│   ├── core/worker.py                               slot_paths・_cmd_slot・_add_slot
│   └── domains/hooks/guards/rm_variable.py *        決定 4 の guard
└── tests/core/test_worker_slot.py *                 サブコマンドとパスの組み立てのテスト
```

依存の向き: `core/worker.py` は今までどおり `ndf.contracts`（`result`・`causes`）と標準ライブラリの `pathlib`・`re` だけに依存し、新しい依存を足さない。guard（`domains/hooks/guards/rm_variable.py`）は `domains/hooks/verdict` と標準ライブラリの `re` だけを読み、`core/worker.py` を読まない（guard とサブコマンドは互いを知らない）。手順書（`agent-layers.md`・`waiting.md`）はコマンドの字面を書くだけで、コードから参照されない。循環は無い。型の関係は設計文書の「構成要素」のモジュールの図にある。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/core/worker.py` | 変更 | 3 つのパスを組み、置き場所を用意するサブコマンドを足す | `pathlib`・`re`・`ndf.contracts.result` | モジュール `ndf.core.worker` | `slot_paths`（新設）・`_cmd_slot`（新設）・`_add_slot`（新設）・`_parser`（変更） |
| `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 新設 | 空かもしれない変数をパスに含む `rm` を止める guard | `re`・`ndf.domains.hooks.verdict` | モジュール `ndf.domains.hooks.guards.rm_variable` | `risky_removal`（新設）・`check`（新設） |
| `plugins/ndf/contracts/hooks/guards.json` | 変更 | guard の表に `rm-variable` の 1 行を足す | 無し（データ） | 表 `guards` | 無し |
| `plugins/ndf/contracts/schemas/result.schema.json` | 変更 | 結果 JSON の `outputs` に `report`・`done`・`result` のキーを足す | 無し（データ） | `$defs/outputs` | 無し |
| `plugins/ndf/docs/contracts/commands.md` | 変更 | `ndf worker slot` の節（生成物） | 無し（生成物） | 無し | 無し |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 変更 | 起動指示の 2 つの欄をコマンド 1 行に書き換える | 無し（文書） | 無し | 無し |
| `plugins/ndf/skills/development-workflow/references/waiting.md` | 変更 | 用意の記述を同じコマンドに書き換える | 無し（文書） | 無し | 無し |
| `plugins/ndf/tests/core/test_worker_slot.py` | 新設 | 設計文書のテスト設計の 6 行 | `ndf.core.worker` | モジュール | テスト関数 |

## 置き場所のパスを組む（`slot_paths`）

満たす決定: 決定 2、決定 3、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `slot_paths` | `plugins/ndf/src/ndf/core/worker.py` | 新設 | `slot_paths(directory: Path, name: str) -> tuple[Path, Path, Path]` | 作業ディレクトリと worker の名前から、報告・完了マーカー・作業の結果ファイルの 3 つの絶対パスをこの順で組む |

- `slot_paths`
  - 事前条件: 無し（名前の形は呼ぶ側でなくこの関数が確かめる）
  - 事後条件: 返る 3 つはすべて絶対パスで、同じ親ディレクトリ（`directory` を絶対パスにしたもの）の下にある。ファイルの有無は見ない
  - 副作用: 無し。絶対パスにするのは `Path.resolve()`（`strict=False`。存在しないパスでも解ける）で、ファイルを作らず、書き換えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `slot_paths` | 名前の形が合わない | `name` が `[A-Za-z0-9._-]` の 1〜64 文字でない（空文字・`../x`・`/` を含む・65 文字以上・日本語） | 関数の先頭（パスを組む前） | 呼び出し元へ返す（`WorkerError("invalid", …)` を上げる） | `_cmd_slot` が捕まえず `main` の `except WorkerError` が `stopped`（`invalid`）の結果 JSON を 1 行出す。終了コードは停止理由の表（`contracts/causes.json`）の `invalid` の値 | ファイルを 1 つも触っていないので後始末は無い。名前を直せばそのまま打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SLOT_NAME_RE` | 定数（`re.Pattern`） | `plugins/ndf/src/ndf/core/worker.py` | `re.compile(r"[A-Za-z0-9._-]{1,64}\Z")`（`fullmatch` で照らす） |
| `REPORT_SUFFIX` | 定数 | `plugins/ndf/src/ndf/core/worker.py` | `".report.md"` |
| `DONE_SUFFIX` | 定数 | `plugins/ndf/src/ndf/core/worker.py` | `".done"`（報告のパスの後ろに付ける） |
| `RESULT_SUFFIX` | 定数 | `plugins/ndf/src/ndf/core/worker.py` | `".result.json"` |

3 つの接尾辞の唯一の保存先はこの 4 つの定数で、手順書・テスト・`ndf answer work`（#3908）は字面を持たずにこの関数を呼ぶ。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `slot_paths` | `ndf.slot_pool.slot_path(directory, index, suffix)`（`src/ndf/slot_pool.py:72`） | `grep -rn "slot_path" plugins/ndf/src/ndf/` | 使えない。重い起動の同時実行数のスロット（`slot-<番号>.lock`）のパスで、名前が同じだけで対象が違う |
| `slot_paths` | `worker.pr_body_draft(state_dir)`（`src/ndf/core/worker.py:183`） | `grep -rn "def [a-z_]*_path(\|def pr_body_draft" plugins/ndf/src/ndf/core/` | 使えない。保存先の下の 1 ファイルだけを返す関数で、3 つの組と名前の検査を持たない。戻り値を `Path` にする形だけ踏襲する |
| `slot_paths` | `ndf.progress.gate.entry.work_argv`（`src/ndf/progress/gate/entry.py:81`。`<作業名>-prompt.md`・`<作業名>-result.json` を組む） | `grep -rn "prompt-file\|result-file" plugins/ndf/src/ndf/progress/gate/entry.py` | 同じ役割の決め方が別にある。#3908 がこの関数を `slot_paths` の呼び出しへ寄せる（決定 2）ので、新設する側を唯一の保存先にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `slot_paths` | `(Path("/w"), "n")` が `/w/n.report.md`・`/w/n.report.md.done`・`/w/n.result.json` を返す | 名前が 1 文字と 64 文字は通る。65 文字は断る。相対パスの `directory` でも絶対パスで返る | 名前が空文字・`../x`・`a/b`・`日本語` は `WorkerError`（`kind` が `invalid`）で、例外の前後でファイルシステムが変わらない |

## 置き場所を用意するサブコマンド（`ndf worker slot`）

満たす決定: 決定 1、決定 3、受け入れ条件 1、受け入れ条件 3、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_cmd_slot` | `plugins/ndf/src/ndf/core/worker.py` | 新設 | `_cmd_slot(a: argparse.Namespace) -> int` | 作業ディレクトリを作り、報告を空のファイルにし、前の完了マーカーと前の結果ファイルを消し、3 つの絶対パスを `outputs` に入れた結果 JSON を 1 行出す |
| `_add_slot` | `plugins/ndf/src/ndf/core/worker.py` | 新設 | `_add_slot(sub: Any) -> None` | `slot` のサブコマンドの引数（`--dir`・`--name`。どちらも必須）を登録し、`fn` に `_cmd_slot` を置く |
| `_parser` | `plugins/ndf/src/ndf/core/worker.py` | 変更 | `_parser() -> argparse.ArgumentParser`（変わらない） | 登録する関数の並びに `_add_slot` を足す。責務は変わらない |

- `_cmd_slot`
  - 事前条件: `a.dir` と `a.name` が文字列で入っている（`argparse` が必須にする）
  - 事後条件: `<作業ディレクトリ>` が存在し、報告のファイルが 0 バイトで存在し、完了マーカーと結果ファイルが存在しない。結果 JSON の `outputs` に `report`・`done`・`result` の 3 つの絶対パスが入っている
  - 副作用: ファイルシステム（ディレクトリの作成・ファイルの作成と削除）と標準出力（結果 JSON 1 行）。外部コマンドとネットワークは使わない

サブコマンドの仕様の文書（`plugins/ndf/docs/contracts/commands.md`）の `ndf worker slot` の節は、`ndf check --write-spec` が `_parser()` から生成する。手で書かない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_cmd_slot` | 名前の形が合わない | 上の `slot_paths` の行と同じ | `slot_paths` の呼び出し | 呼び出し元（`main`）へ返す | `stopped`（`invalid`）。`summary` に名前と許す形を書く | ファイルを触らずに返すので後始末は無い。打ち直せる |
| `_cmd_slot` | 作業ディレクトリを作れない | 親が読めない・同名のファイルがある・容量不足（`OSError`） | `Path.mkdir(parents=True, exist_ok=True)` | 呼び出し元へ返す（`WorkerError("invalid", …)` に包む） | `stopped`（`invalid`）。`summary` にパスと `OSError` の理由を書く | 何も作っていない。原因を直せば打ち直せる |
| `_cmd_slot` | 報告のファイルを空にできない | 書き込みの権限が無い・同名のディレクトリがある（`OSError`） | `Path.write_text("")` | 呼び出し元へ返す（同上） | `stopped`（`invalid`） | 作業ディレクトリだけが残る（次の呼び出しが `exist_ok=True` で通る）。打ち直せる |
| `_cmd_slot` | 完了マーカーか結果ファイルを消せない | 権限が無い・ディレクトリになっている（`OSError`。`FileNotFoundError` は `missing_ok=True` で起きない） | `Path.unlink(missing_ok=True)` | 呼び出し元へ返す（同上） | `stopped`（`invalid`） | 報告のファイルは空になっている。古いマーカーが残るため、supervisor はこのパスで待たない。原因を直して打ち直す |

失敗したときは `outputs` を出さない。supervisor は `status` が `ok` の結果 JSON からだけパスを読む。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_cmd_slot` | 作業ディレクトリが無い | 親ごと作る（`parents=True`） |
| `_cmd_slot` | 作業ディレクトリがある | 何もしない（`exist_ok=True`） |
| `_cmd_slot` | 報告のファイルが無い / ある | どちらも 0 バイトで作り直す（前の報告は消える） |
| `_cmd_slot` | 完了マーカーが無い / ある | 無ければ何もしない / あれば消す（作らない） |
| `_cmd_slot` | 結果ファイルが無い / ある | 無ければ何もしない / あれば消す（作らない） |

同じ `--dir` と `--name` で 2 回打つと、1 回目の完了マーカーと結果ファイルが消え、報告は空になる。supervisor が 2 回目の `.done` を待つとき、1 回目のマーカーを完了と読むことはない（受け入れ条件 3）。繰り返しの処理は無い。扱うファイルは 3 つで、計算量の注意は無い。

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_cmd_slot` | `items` に、作ったパス（`{"kind": "created", "path": …}`）と消したパス（`{"kind": "removed", "path": …}`）を並べる。`outputs` に `report`・`done`・`result` の 3 つの絶対パス。`summary` は「置き場所を用意した（報告 …・完了マーカー …・結果 …）」 | 結果 JSON（標準出力 1 行） | 報告のファイルの中身。前の作業の結果ファイルの中身。ログの出力は足さない（ファイルの始末だけの処理で、記録する状態を持たない） |

結果 JSON の `outputs` は閉じた語彙で、`contracts/schemas/result.schema.json` の `$defs/outputs` が `additionalProperties: false` を持つ。`report`・`done`・`result` の 3 つのキー（`type: string`・`minLength: 1`）を足さないと、`result.emit` の検査（`src/ndf/contracts/result.py:144-156`）が出力を断って `invalid` の終了コードを返す。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_cmd_slot` | `Path.unlink(missing_ok=True)` | `grep -rn "missing_ok=True" plugins/ndf/src/ndf/`（8 箇所で使っている） | 使う。無いファイルを消す経路をこのプラグインの既存の書き方に合わせる |
| `_cmd_slot` | `Path.mkdir(parents=True, exist_ok=True)` | `grep -rc "mkdir(parents=True, exist_ok=True)" -r plugins/ndf/src/ndf/`（10 以上のモジュールで使っている） | 使う |
| `_cmd_slot` | `identity.prepare_state_dir`（`src/ndf/identity.py:462`） | `grep -n "def prepare_state_dir" -A 6 plugins/ndf/src/ndf/identity.py` | 使えない。NDF 自身の状態の保存先を用意して旧い保存先を退避する処理で、起動指示で渡された任意のディレクトリには当たらない |
| `_cmd_slot` | `ndf.core.plan.files.write_json` / `relay.signals.replace_json` | `grep -rn "def write_json\|def replace_json" plugins/ndf/src/ndf/` | 使えない。どちらも JSON を書く関数で、0 バイトのファイルを作る用途に合わない（`Path.write_text("")` で足りる） |
| `_cmd_slot` | `result.result` / `result.stopped`（`src/ndf/contracts/result.py:40,65`） | `grep -n "def result\|def stopped" plugins/ndf/src/ndf/contracts/result.py` | 使う。`outputs` は `result.result` の任意の項目として既にあり（`_OPTIONAL`）、渡すだけで載る |
| `_add_slot` | `_add_run`・`_add_closing`（`src/ndf/core/worker.py:702,711`） | `grep -n "def _add_" plugins/ndf/src/ndf/core/worker.py` | 使う。同じ形（`sub.add_parser` → `add_argument` → `set_defaults(fn=…)`）をそのまま踏襲する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_cmd_slot` | `--dir D --name n` が `status: ok` を返し、`outputs` の 3 つが絶対パスで、報告が 0 バイトで存在し、完了マーカーと結果ファイルが無い（受け入れ条件 1・4） | 無いディレクトリを渡すと作る。相対パスの `--dir` でも `outputs` は絶対パス。2 回目の呼び出しで 1 回目の `.done` と結果ファイルが消え、報告が空に戻る（受け入れ条件 3） | 名前が `../x`・空文字のとき `stopped`（`invalid`）で、`--dir` のディレクトリが作られていない。`--dir` に同名のファイルがあるとき `stopped`（`invalid`） |

## 空かもしれない変数の `rm` を止める guard（`rm-variable`）

満たす決定: 決定 4、受け入れ条件 5

この構成要素は、実装の最初に「`PreToolUse` の guard が Claude Code の安全確認より先に働く」ことを実機で確かめてから足す（設計文書の決定 4 と「未確認のまま残ること」）。先に働かなければ、確かめた結果を Pull Request に残して、この構成要素とテストを落とす。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `risky_removal` | `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 新設 | `risky_removal(script: ShellScript) -> str \| None` | `rm` の削除の対象に、空になりうる変数の展開を含む語があれば、その語の字面を返す。無ければ `None` |
| `check` | `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 新設 | `check(ctx, svc) -> Verdict \| None` | `risky_removal` が語を返したら `deny` に書き直しの形を添えて返す。ほかは `None`（通す） |

- `risky_removal`
  - 事前条件: `script` は `ctx.shell`（`domains/hooks/context.py:579` の `ShellScript`）で、`commands` の各要素が語ごとの字面（`source`）を持つ
  - 事後条件: 返った語は `script` の中の `rm` の引数のどれかの字面と一致する。判定は `source`（引用とエスケープを残した字面）だけで行い、値の展開はしない
  - 副作用: 無し（文字列の判定だけ。シェルを起動しない）
- `check`
  - 事前条件: guard の表の行（`events` が `PreToolUse`・`tools` が `Bash`）から呼ばれる
  - 事後条件: 判定を返すだけで、ファイルも計測の記録も書かない（記録は guard のエントリポイントが行う）
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `risky_removal` | コマンドを語に分けられない | `ctx.shell` が字面を持たない（読めないコマンド） | `script.commands` が空か `source` が空 | 既定値で続ける（`None` を返して通す） | 出力なし（Bash はそのまま走る） | 後始末は無い |
| `check` | 判定の中の例外 | 想定しない形の入力 | guard のエントリポイント（`domains/hooks/dispatch`） | 既定で通す（`guards.json` の `fail_closed` は偽にする。置き場所の用意を hook の不具合で止めない） | 出力なし | 後始末は無い |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `risky_removal` | コマンド名が `rm` でない | 見ない（`None`） |
| `risky_removal` | `rm` の引数が固定のパスだけ | `None`（通す） |
| `risky_removal` | 引数の字面が `$NAME` か `${NAME}` を含み、`${NAME:?…}`・`${NAME:-…}`・`${NAME:+…}` の形でない | その語を返す（止める） |
| `risky_removal` | 引数の字面が `${NAME:?}` の形だけ | `None`（空なら shell が止めるので、確認の条件に当たらない） |
| `check` | `risky_removal` が語を返した | `deny("…空になりうる変数 … を含む `rm` を止めた。置き場所の用意は `ndf worker slot --dir <作業ディレクトリ> --name <worker の名前>` で打つ。ほかの削除は `${NAME:?}` の形か固定のパスにする")` |

繰り返しは `script.commands` の数（1 回の Bash の呼び出しに現れる単純なコマンドの数）で終わる。計算量の注意は無い。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `_VARIABLE` | 定数（`re.Pattern`） | `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | `$NAME` と `${NAME}` に当たり、`${NAME:?…}`・`${NAME:-…}`・`${NAME:+…}` に当たらない形 |
| `rm-variable` の行 | 表の 1 行 | `plugins/ndf/contracts/hooks/guards.json` | `events: ["PreToolUse"]`・`tools: ["Bash"]`・`order: 26`（`heredoc` の 25 と `sleep` の 30 の間）・`strict: false`・`deny_key: "command"`・`fail_closed: false`・`suggests` に `ndf worker slot` の見本 |

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
| `risky_removal` | `rm -f "$W/$n.report.md.done"` でその語を返す。`rm -rf /tmp/x` で `None` | `rm -f "${W:?}/x"` は `None`。`${W:-/tmp}` も `None`。`rm` を含まない行は `None` | 空のコマンド・語に分けられないコマンドで `None`（例外を上げない） |
| `check` | 止める形で `deny` を返し、理由に `ndf worker slot` の打ち方が入る | 通す形で `None` | 判定の中の例外を guard のエントリポイントが拾い、`fail_closed` が偽なので呼び出しを通す |

## 手順書の置き場所の用意（`agent-layers.md`・`waiting.md`）

満たす決定: 決定 1、受け入れ条件 1、受け入れ条件 2

関数を持たない構成要素で、変えるのは次の 2 行だけである（`agent-layers.md` は並行するほかのスプリントも触るため、該当の行の外を変えない）。

| ファイル:行 | 今の中身 | 変えた後 |
| --- | --- | --- |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md:235` | 起動指示の「置く場所」の欄が `: > <置く場所>; rm -f <置く場所>.done` を書く | supervisor は `ndf worker slot --dir <作業ディレクトリ> --name <worker の名前>` を 1 回打ち、結果 JSON の `outputs.report` を「置く場所」に、`outputs.result` を「作業の結果ファイル」に書き写す。待つのは `outputs.done` である、とだけ書く |
| `plugins/ndf/skills/development-workflow/references/waiting.md:229` | 「中間通知を受けたとき」が同じ 2 つのコマンドを書く | 同じ 1 行のコマンドへ書き換え、待つパスは `outputs.done` から取ると書く |

どちらの行にも、置き場所のパスを組み立てるシェルの変数と `rm` を残さない（受け入れ条件 1・2）。この 2 行が変わったことは、実装で 2 つのファイルを読んで確かめる。`.md` の文言を照合する自動テストは置かない（文言を固定しても振る舞いを保証しないため。`AGENTS.md` の「DON'T」）。
