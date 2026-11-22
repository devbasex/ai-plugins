# #4059 の詳細設計: エージェントが打つ gh も NDF と同じ順番の記録で間を空ける

設計文書 [issue-4059-design.md](./issue-4059-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。#4016 の部品（`WriteGate`・`limit_of`）は [issue-4016-design-detail.md](./issue-4016-design-detail.md) にあり、この文書は足す・置き換える関数だけを書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設。`write_gate.py` は #4016 が新設する）:

```text
plugins/ndf/
├── bin/gh *
├── contracts/limits.json
├── pyproject.toml, uv.lock（graphql-core を足す）
├── src/ndf/adapters/
│   ├── gh_gate.py *
│   ├── github.py
│   └── write_gate.py
└── tests/adapters/
    ├── test_gh_gate.py *
    ├── test_bin_gh.py *
    ├── test_github.py
    └── test_write_gate.py
docs/glossary/glossary.json, docs/glossary.md
```

依存の向き: `bin/gh` → `adapters/gh_gate.py` → `adapters/write_gate.py`・`adapters/github.py`（`limit_words`・`limit_body`・`limit_of` だけ）。`github.py` は `write_gate.py` を読み、`gh_gate.py` を読まない。`write_gate.py` は `gh_gate.py` を読まない。循環は無い。本物の `gh` の探し方は `bin/gh` の 1 か所だけに置き、`gh_gate` は `bin/gh` が渡すパスを使う（同じ規則を bash と python の 2 か所に持たない）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `bin/gh` | 新設 | 名前 `gh` で引かれ、本物の `gh` を探し、起動先の版の `src` と NDF の仮想環境の python で `gh_gate` を起動する | `gh_gate` | シェルのスクリプト | — |
| `src/ndf/adapters/gh_gate.py` | 新設 | `gh` の引数から書き込みの種類と件数を決め、書き込みなら順番を取ってから本物の `gh` を走らせる | `write_gate`・`github` | モジュール | `main`・`plan_of`・`Plan`・`run_held`・`Ends`・`limit_seen`（新設） |
| `src/ndf/adapters/write_gate.py` | 変更（#4016 の新設に足す） | 種類と件数を受けて順番を取る口を足し、GraphQL の文書の分け方を共有する | #4016 のとおり・`graphql`（graphql-core） | `WriteGate` | `WriteGate.hold`・`mutation_kind`（新設）、`WriteGate.acquire`・`within` の throttler の `acquire`・`WriteGate.sent`・`Busy`・`kind_of`（#4016 の仕様を置き換える） |
| `src/ndf/adapters/github.py` | 変更（#4016 の変更に足す） | 二次レート制限の照らしを 1 か所にする | — | モジュール | `limit_words`・`limit_body`（新設）、`limit_of`（#4016 の仕様を置き換える） |
| `contracts/limits.json` | 変更 | `gh` の書き込みが待つ上限 | — | — | `github.gh-wait`（追加） |
| `pyproject.toml`・`uv.lock` | 変更 | GraphQL の文書を構文で読むライブラリを足す | — | — | `graphql-core`（3.2 系を固定） |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更 | 用語を足し、意味を変える | — | — | 「gh の包み」（追加）、「二次レート制限」（意味の変更） |

## bin/gh（入口）

満たす決定: 決定 1、受け入れ条件 4・6

- bash のスクリプトで、実体の位置（`bin/ndf` と同じくシンボリックリンクをたどる）からプラグインの根 `$root` を求める
- 本物の `gh` を探す（この規則の唯一の実装）。`PATH` を `:` で分けて前から見て、`<要素>/gh` が実行できる通常のファイルで、実体が自分の実体と違い、要素の実体が「名前が `bin` で、親が `$root` と違い、親の親が `$root` の親と同じ」（`session_env.should_drop` の NDF のほかの版の `bin` の判定と同じ条件）でない最初のもの。無ければ標準エラーへ `ndf: 本物の gh が PATH に無い` を出して 127 で終わる
- `NDF_GH_GATED` が空でなければ、python を起動せずに本物の `gh` へ `exec` する（G3。入れ子の速い道）
- それ以外は、NDF の仮想環境の python（hook の登録と同じ `${XDG_CACHE_HOME:-$HOME/.cache}/<プラグイン名>/venv/<版>/bin/python`。`bin/ndf` が作る）を次の形で `exec` する。`shopt -s execfail` を先に置き、`exec` が失敗しても bash が終わらずに次の行で本物の `gh` へ `exec` する

  ```bash
  exec "$python" -I -c "$BOOT" "$root/src" "$real" "$@"
  ```

  - `-I`（隔離モード。`-E`・`-P`・`-s` を含む）で、作業ディレクトリ・`PYTHONPATH`・利用者の site を import の探索先に入れない。チェックアウトしたリポジトリに `ndf/` や `json.py` があっても読まない（`bin/ndf` の `-P` と同じ目的。https://docs.python.org/3/using/cmdline.html#cmdoption-I ）。仮想環境の site-packages（依存）は `-I` でも探索先に残る
  - NDF 本体は仮想環境に入らない（`pyproject.toml` の `package = false`）。`$BOOT` は `sys.path` の先頭へ引数の `$root/src`（起動先の版の `src`）を入れてから `from ndf.adapters import gh_gate` を読み、`sys.exit(gh_gate.main(sys.argv[3:], Path(sys.argv[2])))` を呼ぶ。`bin/ndf` が子に設定する `PYTHONPATH` にも、NDF の環境変数にも頼らない（Bash の呼び出しごとに環境が新しくなり、`session_env.tool_env` は NDF の `src` の `PYTHONPATH` を外すため）
  - `$BOOT` は import の失敗（`BaseException` すべて。`ImportError`・構文の誤り・依存の欠け）を受けると、標準エラーへ `ndf: gh の包みを読み込めない（<例外の型>）。順番を取らずに gh を走らせる` を 1 行出し、`os.execv(real, ["gh", *argv])` で本物へ置き換わる。環境は書き換えないので、本物の `gh` と子は呼び出し元の環境をそのまま継ぐ
- python が無い・実行できない（`[ -x "$python" ]` が偽、または `exec` の失敗）ときも本物の `gh` へ置き換わる（NDF の不調で `gh` を止めない。hook の入口の IH4 と同じ扱い）。仮想環境を作るのは `bin/ndf` だけで、`bin/gh` は作らない（`gh` のたびに uv を起こさない）

## gh_gate（gh の包み）

満たす決定: 決定 1・3・4・6・7、受け入れ条件 1〜5、不変条件 G1〜G3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `main` | `adapters/gh_gate.py` | 新設 | `main(argv: list[str], real: Path, environ: Mapping[str, str] = os.environ) -> int` | 引数を `plan_of` で読み、読み取りなら本物へ置き換わり、書き込みなら `run_held` の終了コードを返す |
| `Plan` | 同上 | 新設（型） | `NamedTuple(kind: Optional[str], count: int, argv: list[str], swallow_stdout: bool, temps: tuple[Path, ...])` | 種類（None は読み取り）・数える書き込みの件数・本物へ渡す引数・本物の標準出力を捨てるか・後で消す一時ファイル |
| `plan_of` | 同上 | 新設 | `plan_of(argv: Sequence[str], stdout_tty: bool, snapshot: Callable[[str], Optional[Path]] = _snapshot, count_labels: Callable[[Sequence[str]], int] = _count_labels) -> Plan` | `gh` の引数を下の「処理の手順」のとおり読む |
| `run_held` | 同上 | 新設 | `run_held(real: Path, plan: Plan, gate: WriteGate, budget: float, environ: Mapping[str, str]) -> int` | `gate.within(...).hold(kind, count)` の中で本物の `gh` を子として走らせ、出力を写しながら頭と末尾を持ち、二次レート制限なら `gate.block` を呼ぶ |
| `Ends` | 同上 | 新設（型） | `class Ends`（`feed(data: bytes) -> None`・`head() -> bytes`・`tail() -> bytes`） | 写した出力の頭 `HEAD_BYTES` と末尾 `TAIL_BYTES` を口ごとに持つ |
| `limit_seen` | 同上 | 新設 | `limit_seen(plan: Plan, out: Ends, err: Ends, now: float) -> Optional[float]` | 本物の `gh` の出力から二次レート制限を見分け、待つ秒を返す（違えば None） |

- `main`
  - 事後条件: `plan.kind` が None なら `os.execve(real, ["gh", *plan.argv], environ)` で置き換わる（戻らない）。書き込みなら `run_held(..., budget=limits.get("github.gh-wait").interval)` の終了コード。`plan.temps` は戻る前に消す（例外でも消す）
  - 副作用: 書き込みのときだけ、`machine_gate()` の記録とロック（#4016）を使う
- `plan_of`
  - 事前条件: `argv` は `gh` の後ろの引数（`gh` 自身を含まない）。`stdout_tty` は親の標準出力が端末か
  - 事後条件: 下の「処理の手順」の表のとおり。どの行にも当たらなければ `kind=WRITE`・`count=1`。読み取りと決めるのは、引数を最後まで読めて、表の読み取りの行に当たるときだけ（読めない語・知らないフラグがあれば書き込みの側へ倒す）
  - 副作用: 先読みは「先読みの契約」のとおり。`gh label clone` のときだけ `count_labels` が本物の `gh` で元のリポジトリのラベルの数を読む（読み取り 1 回）
- `run_held`
  - 事前条件: `plan.kind` は `WRITE` か `CONTENT`。`budget` は秒（> 0）
  - 事後条件: `Busy` を受けたら本物を走らせずに、標準エラーへ「異常系の処理」の 1 行を出して 1 を返す。それ以外は本物の `gh` の終了コードをそのまま返す（シグナルで終われば 128 + 番号）。`limit_seen(...)` が秒を返したら、`hold` の出口より前に `gate.block(秒)` を呼んでいる。どの終わり方でも、戻る前に子は終わっていて、子のパイプは閉じ、`hold` の出口で送信のロックを放している
  - 副作用: 子の環境に `NDF_GH_GATED=1` を足す（ほかは `environ` のまま）。`subprocess.Popen(..., restore_signals=True)` で起動し、子の `SIGPIPE` を既定に戻す。標準入力は子へそのまま渡す（継ぐ）。標準出力・標準エラーは「出力の写し方」のとおり
- `limit_seen`
  - 事後条件: 本物の終了コードが 0 なら None。0 以外なら、次の順で見分ける。(1) 標準出力の頭が `gh api -i` の応答の頭（`HTTP/<版> <状態>` の行・ヘッダー・空行）なら、状態とヘッダーと、空行の後の本文を JSON として読んだもの（読めなければ空）の `message` を #4016 の `limit_of(status, headers, message, now)` に渡す（`limit_of` は `limit_body` で本文を照らす）。当たれば `limit.seconds`（`hinted` が偽なら `BLOCK_SECONDS`）。(2) そうでなく、標準出力の末尾の全体が JSON として読め、標準エラーの `gh: … (HTTP <状態>)` の行から状態が分かれば、`limit_body(status, body)` が真なら `BLOCK_SECONDS`。(3) 標準エラーの末尾の行のうち、`gh: `・`GraphQL: `・`HTTP <3 桁>: ` で始まる行（`gh` が誤りを出す形。STD4）のどれかに `limit_words(行)` が当たれば `BLOCK_SECONDS`。(4) ほかは None。標準出力の文字列そのものには `limit_words` を当てない（コメントの本文などを写した出力に語があっても当てない）

### 処理の手順

`plan_of` は次の順に読む。

**(a) グループとサブコマンド。** 先頭から、`-R`・`--repo`・`--hostname` とその値（`-R x`・`-Rx`・`--repo x`・`--repo=x` のどれでも）を除いた語のうち、`-` で始まらない最初の 2 語を「グループ」「サブコマンド」とする。`gh` だけ・`gh --version`・`gh version`・`gh help …` は読み取り。

**(b) ヘルプ。** `--help`・`-h` が読み取りになるのは、(a) で除いた語の後に残る語が「グループ」「サブコマンド」（無くてもよい）と、ヘルプのフラグ 1 つだけのときに限る。それ以外の位置のヘルプのフラグは見ない（`gh pr comment 123 --body '--help'` の `--help` は値で、書き込みのまま）。`gh api` ではさらに、下の (c) でフラグとして読めた `--help`・`-h` だけを数える。

**(c) `gh api` の引数。** GitHub CLI v2.103.3 の `api` のフラグの表（https://github.com/cli/cli/blob/v2.103.3/pkg/cmd/api/api.go ）で、pflag と同じ規則で読む。

- 値を取るフラグ: `-X`・`--method`・`-F`・`--field`・`-f`・`--raw-field`・`-H`・`--header`・`-p`・`--preview`・`--input`・`-q`・`--jq`・`-t`・`--template`・`--cache`・`--hostname`。値を取らないフラグ: `-i`・`--include`・`--paginate`・`--slurp`・`--silent`・`--verbose`・`-h`・`--help`
- 結合形も読む: `--method=POST`・`-XPOST`・`-fbody=x`・`-Fquery=@q.graphql`・`--field=x=1`。短いフラグの並び（`-iX POST` のように値を取らないフラグの後に値を取るフラグが続く形）も pflag と同じに読む
- 値を取るフラグの次の語は値として消費し、フラグとしても位置引数としても見ない（`-H 'X: -f'` の `-f` はフィールドでない）
- `--` の後の語はすべて位置引数
- 同じフラグが複数あれば、`--method` は最後の値が効く（pflag の文字列のフラグ）。フィールドは 1 つでもあれば「フィールドあり」
- 表に無いフラグがあれば、値を取るかが分からず以降を読めないので、読み取りにしない（`CONTENT`・件数 1）
- 最初の位置引数がエンドポイント

| 条件 | 結果 |
| --- | --- |
| 方法が `GET`・`HEAD`（大文字小文字を問わない） | 読み取り |
| 方法の指定が無く、フィールド（`-f`・`-F`・`--field`・`--raw-field`）も `--input` も無い | 読み取り（`gh` は GET で送る） |
| エンドポイントが `graphql`（方法が `GET` 以外） | 文書と `operationName` を (d) で読み、`mutation_kind(document, operation_name)` の結果。None なら読み取り |
| 方法が `POST`、または指定が無くフィールドか `--input` がある（`gh` は POST で送る） | `CONTENT` |
| 方法が `PATCH`・`PUT`・`DELETE`・ほかの語 | `WRITE` |

**(d) GraphQL の文書。** 文書は `-f query=<文>`・`-F query=<文>`・`-F query=@<パス>`・`--input <パス>`（JSON の `query`・`operationName`）から、`operationName` は `-f`・`-F` の `operationName=<名前>` か `--input` の JSON から取る。パスは「先読みの契約」で読む。読めない・どこにも無いときは `CONTENT`。`mutation_kind` は graphql-core の `parse` で文書を構文として読み（https://spec.graphql.org/October2021/#sec-Language.Operations ）、`operationName` があればその名前の操作、無ければ文書に操作が 1 つだけのときその操作を「実行される操作」とする。決まらない（名前の操作が無い・名前が無く操作が複数・構文の誤り）なら `CONTENT`。実行される操作が `query`（省略形 `{ … }` を含む）なら None、`subscription` は `CONTENT`、`mutation` なら最上位の選択（フラグメントの展開とインラインフラグメントをたどる）のフィールドの名前（別名でなく、`a: createIssue` なら `createIssue`）のどれかが `create`・`add` で始まれば `CONTENT`、ほかは `WRITE`。`fragment` の定義は操作として数えない（`fragment F on Query { … } mutation { … }` は mutation）

**(e) ほかのグループ。** グループとサブコマンドの組で決める。上の行から順に最初に当たる行の結果にする。

| グループ | サブコマンド | 結果 | 件数 |
| --- | --- | --- | --- |
| `auth`・`search`・`help`・`version`・`completion`・`config`・`alias`・`browse`・`status` | どれでも | 読み取り | — |
| `label` | `clone` | `CONTENT`（宛先にラベルを作る・`--force` で書き換える。https://cli.github.com/manual/gh_label_clone ） | `count_labels`（元のリポジトリのラベルの数。読めなければ 1 と注意の 1 行） |
| `pr` | `revert` | `CONTENT`（取り消しの PR を作る。https://cli.github.com/manual/gh_pr_revert ） | 1 |
| `repo` | `fork` | `CONTENT`（リポジトリを作る） | 1 |
| `release` | `create` | `CONTENT` | 1 + 位置引数のうちタグの後のアセットの数 |
| `release` | `upload` | `CONTENT` | 位置引数のうちタグの後のファイルの数 |
| `pr`・`issue`・`run`・`release`・`repo`・`gist`・`workflow`・`label`・`cache`・`ruleset`・`project`・`secret`・`variable`・`org`・`codespace`・`extension` | `view`・`list`・`status`・`diff`・`checks`・`watch`・`download`・`checkout`・`clone`・`logs` | 読み取り（`label clone` は上の行が先に当たる） | — |
| どれでも | `create`・`comment` | `CONTENT` | 1 |
| `pr` | `review` | `CONTENT` | 1 |
| ほか | 上のどれでもない（`edit`・`close`・`merge`・`ready`・`delete`・グループだけ・知らない語） | `WRITE` | 1 |

- 計算量の注意: 引数を 1 回なめるだけ。先読みするファイルは query か `--input` の 1 つだけ。`label clone` だけ読み取りの `gh` を 1 回足す（エージェントがまれに打つコマンドで、書き込みの件数を数えるためにだけ使う）

### 先読みの契約

- 先読みするのは、`gh api graphql` の文書を (d) で読むときの `-F query=@<パス>`・`--input <パス>` だけ。REST の `--input` は方法をフラグで決めるので読まない
- `<パス>` が `-`（標準入力）・`os.stat` で通常のファイルでないもの（FIFO・`/dev/fd/<n>`・Bash のプロセス置換・デバイス）・開けないものなら、開かずに（消費せずに）`CONTENT`・件数 1 とし、引数はそのまま本物へ渡す
- 通常のファイルなら、`snapshot` が 1 回だけ読み、その内容を私用の一時ディレクトリ（`tempfile.mkdtemp`、0700）のファイルへ書き、分類はその内容で行い、本物へ渡す引数のパスをその一時ファイルへ差し替える（`-F query=@<一時ファイル>`・`--input <一時ファイル>`）。分類した内容と送る内容は同じバイト列になり、待ちの間に元のファイルが変わっても食い違わない。一時ファイルは `Plan.temps` に載り、`main` が本物の終わった後に消す

### 出力の写し方

- 標準出力・標準エラーは、親の側の口が端末でなければパイプで受け、2 つのパイプを別のスレッドで読み、読んだ順に親の同じ口へ書いてから `Ends` へ足す（口ごとの順を保つ）
- 親の側の口が端末なら、その口はパイプにせずに継ぎ、見分けに使わない（`gh` の端末向けの表示を変えない）。端末の口だけに出る誤りは見分けられない（受け入れ条件 3 の境界。Claude Code の Bash は端末を割り当てない）
- `gh api --silent`（`plan.swallow_stdout`）: 親の標準出力が端末でなければ、`plan_of` は `--silent` を外した引数を本物へ渡し、`run_held` は本物の標準出力を親へ写さずに `Ends` へだけ足す。利用者に見える出力は `--silent` と同じで、422 の本文の `code: abuse` を見分けられる。端末なら `--silent` を残し、本文は見分けない
- 親の口へ書くときに読み手が閉じていた（`BrokenPipeError`・`EPIPE`）ら、その口のスレッドは親へ書くのをやめ、子のパイプの読み口を閉じて抜ける。子は次に書いたときに `SIGPIPE`（既定の動作）で終わり、`gh` を直に打ってパイプの先が閉じたときと同じ終わり方になる。`run_held` は子の終わりを待ち、`hold` を出てロックを放す。もう一方の口のスレッドは続けて読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `bin/gh` | 本物の `gh` が無い | `gh` が入っていない・`PATH` に包みしか無い | `PATH` をなめた結果が空 | 止める | 標準エラー `ndf: 本物の gh が PATH に無い`、127 | `gh` を入れて打ち直す |
| `bin/gh` | 仮想環境の python が無い・実行できない | `ndf` をまだ一度も打っていない・版を入れ替えた直後・基の python が消えた | `[ -x <python> ]`・`exec` の失敗（`execfail`） | 本物の `gh` へ置き換わる | 無し（`gh` の出力だけ） | 次に `ndf` が仮想環境を作った後は包む |
| `bin/gh` の `$BOOT` | `gh_gate` を import できない | `src` が壊れた・依存（graphql-core など）が仮想環境に無い | `BaseException` | 本物の `gh` へ置き換わる | 標準エラー `ndf: gh の包みを読み込めない（<例外の型>）。順番を取らずに gh を走らせる`、本物の終了コード | `ndf` を打って仮想環境を作り直すと包む |
| `run_held` | 待ちが上限に収まらない（次に送れる時刻が分かる） | 二次レート制限の待ちの終わり・ほかのレーンの予約が並ぶ | `Busy` の `until` が None でない（#4016 の `Busy` を置き換える） | 本物を走らせずに返す | 標準エラー `ndf: GitHub への書き込みの順番を <予算> 秒の内に取れない。<until の時刻 JST> 以後に打ち直す`、1 | 予約は `hold` が取り消す（#4016 の `withdraw`）。時刻の後に打ち直せる |
| `run_held` | 待ちが上限に収まらない（時刻が分からない） | 記録のロック・送信のロックを予算の内に取れない（ほかのレーンの長い書き込みなど）・予算が 0 以下 | `Busy` の `until` が None | 本物を走らせずに返す | 標準エラー `ndf: GitHub への書き込みの順番を <予算> 秒の内に取れない（ほかの書き込みが送信中）。少し後で打ち直す`、1。時刻は出さない | 同上 |
| `run_held` | 記録を読めない・ロックを取れない | 壊れた JSON・権限・固まったプロセス | #4016 の `reserve`・`hold` | #4016 の I5（ロックを持たずに送る） | #4016 の 1 行 | 次の `gh` でまた試す |
| `run_held` | 本物の `gh` を起動できない | 実行の権限が無い・消えた | `subprocess.Popen` の `OSError` | 呼び出し元へ返す | 標準エラー `ndf: gh を起動できない: <理由>`、126 | `hold` の出口で送信のロックを放す（送っていないが、出口の `sent` は延ばす側にしか倒れない） |
| `run_held` | 待ちの途中・走らせている途中で中断された | Ctrl-C・Bash の時間切れ | `KeyboardInterrupt`・シグナル | 子へ同じシグナルを送り、子の終わりを待ってから返す | 子の終了コード（シグナルなら 128 + 番号） | `hold` の出口でロックを放す |
| `run_held` | 写し先の読み手が先に閉じた | `gh … \| head -c 1` | 親の口への書き込みの `BrokenPipeError` | 「出力の写し方」のとおり、子のパイプを閉じて子の終わりを待つ | 子の終了コード（`SIGPIPE` なら 141） | `hold` の出口でロックを放す |
| `run_held` | `block` を書けない | 記録の書き込みの失敗 | #4016 の `block` | 既定値で続ける | #4016 の 1 行 | 次の書き込みの待ちは延びない |
| `plan_of` | `label clone` の元のラベルの数を読めない | 元のリポジトリが無い・権限 | `count_labels` の失敗 | 件数 1 で続ける | 標準エラー `ndf: ラベルの数を読めず、書き込みを 1 件と数える` | 本物の `gh label clone` も同じ理由で失敗しうる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `github.gh-wait` | 設定値 | `contracts/limits.json` | `interval` 90（`gh` の書き込みが順番を待つ上限の秒。Claude Code の Bash の既定 120 秒から `gh` が走る時間を残す） |
| `BLOCK_SECONDS` | 定数（設定値から読む） | `adapters/gh_gate.py` | `cross-review.secondary-rate-limit` の `routing.backoff` の 1 回目（60 秒。#4016 の決定 8 の `hinted` が偽のとき）。応答のヘッダーが読めるときは使わない（`limit_seen` の (1)） |
| `HEAD_BYTES` | 定数 | `adapters/gh_gate.py` | 65536（口ごとに持つ頭のバイト数。`gh api -i` の応答の頭を読む） |
| `TAIL_BYTES` | 定数 | `adapters/gh_gate.py` | 65536（口ごとに持つ末尾のバイト数） |
| `NDF_GH_GATED` | 環境変数 | 子の環境 | `1`（入れ子の `gh` が順番を取らない印） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じマシンのレーンの `gh` と NDF のコードの書き込み | 書き込みの順番の記録・送信のロック（#4016） | #4016 の `WriteGate.hold`。`gh_gate` は本物の `gh` が終わるまで送信のロックを持つ（G2） | `gh` の打ち直しはエージェントが決める。`gh_gate` は打ち直さない |
| 外の `gh_gate` と、その子が打つ `gh` | 送信のロック | 子は `NDF_GH_GATED` を見て順番を取らない（G3） | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `run_held` | 待ちが 1 秒を越えるときの `ndf: GitHub への書き込みの順番を待つ（<秒> 秒）` の 1 行、待てないときの 1 行 | 標準エラー | `gh` の引数（本文・token を含みうる） |
| `bin/gh` | 本物の `gh` が無いとき・包みを読み込めないときの 1 行 | 標準エラー | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `plan_of` | #4016 の `write_gate.kind_of`（`httpx.Request` を読む） | #4016 の詳細設計 | 要求を作らない `gh` には使えない。GraphQL の文書の分け方は `kind_of` と同じ `write_gate.mutation_kind(document, operation_name)` を共有する |
| `mutation_kind` | graphql-core（GraphQL の参照実装の Python 版。純 Python・依存無し・MIT） | PyPI と仕様の操作の選び方 | 使う。`fragment` の先置き・複数の操作と `operationName`・別名を自前の字句の読みで漏れなく扱うより、構文木で読む方が確か。import は graphql の文書を読むときだけ（読み取りの `gh` の起動を遅くしない） |
| 本物の `gh` の探し方 | `shutil.which`・`session_env.should_drop` | `grep -rn "def should_drop" plugins/ndf/src` | `shutil.which` は自分を飛ばせない。python を起動しない速い道（G3）と import の失敗の置き換えでも本物が要るので、`bin/gh` の bash に 1 つだけ置き、`should_drop` の NDF のほかの版の `bin` の条件を写す |
| `run_held` の待ち | #4016 の `WriteGate.within` と `Busy` | #4016 の詳細設計 | 使う（予算による打ち切りと予約の取り消しを持つ）。`Busy` に `until` を足す |
| `limit_seen` | #4016 の `limit_of` | #4016 の詳細設計 | `gh api -i` で状態とヘッダーが読めるときは `limit_of` をそのまま使う（待ちの秒の計算を共有する）。読めないときは `limit_body`・`limit_words` を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `plan_of` | 表 (c)・(e) の各行 | `-R owner/repo` が先頭にある、`--method get`（小文字）、`--method=POST`・`-XPOST`・`-fbody=x`・`--field=x=1` → `CONTENT`、`-X GET -X POST` → `CONTENT`（最後が効く）、`-H 'X: -f' repos/o/r` → 読み取り、`-- -f` の後の `-f` はフィールドでない、`gh pr comment 123 --body '--help'` → `CONTENT`、`gh pr comment --help` → 読み取り、`gh label clone o/src` → `CONTENT`・件数はラベルの数、`gh pr revert 1` → `CONTENT`、`gh release create v1 a.zip b.zip` → 件数 3、`--silent` が外れ `swallow_stdout` が真（端末なら残る） | 表に無いフラグのある `gh api` → `CONTENT` |
| `mutation_kind` | query → None、`addComment` → `CONTENT`、`resolveReviewThread` → `WRITE` | `fragment F on Query {…} mutation { addComment … }` → `CONTENT`、`query A {…} mutation B { createIssue … }` に `operationName=B` → `CONTENT`・`A` → None、名前無しで操作が 2 つ → `CONTENT`、`a: createIssue` → `CONTENT`、`createIssue: resolveReviewThread` → `WRITE`、フラグメントの展開の中の `addComment` → `CONTENT` | 構文の誤り → `CONTENT` |
| 先読み（`snapshot`） | 通常のファイルの query の mutation を分け、本物へ渡すパスが一時ファイルで、内容が分類したものと同じ。待ちの間に元のファイルを書き換えても本物が読む内容は変わらない | `--input -`・FIFO・`<(…)`（`/dev/fd/<n>`）→ 開かずに `CONTENT`、FIFO の書き手のデータを本物の `gh` がすべて読む | ファイルが無い → `CONTENT`。終わった後に一時ファイルが残らない |
| `run_held` | 偽の本物の `gh` が 0 で終わり、出力がそのまま写り、終了コードが 0。`hold` が `plan.count` で呼ばれる | 待ちがちょうど `budget` → 走らせない（#4016 の決定 10 の比べ方）。`Busy(until=t)` → 時刻の 1 行、`Busy(until=None)` → 時刻の無い 1 行 | `Busy`・起動できない・中断。偽の `gh` が 1 MiB を書く途中で写し先が 1 バイト読んで閉じる → 子が終わり、`run_held` が 5 秒の内に戻り、送信のロックが放されている |
| `limit_seen` | 403 の `secondary rate limit`（標準エラー）、422 の JSON（`was submitted too quickly`・`errors[].code` が `abuse`）、`GraphQL: was submitted too quickly` → `BLOCK_SECONDS` | `-i` の出力の `Retry-After: 120` の 403 → 120 秒、`--silent` の 422 の `code: abuse` → `BLOCK_SECONDS`（利用者の標準出力は空）。親の標準出力が端末のときは見分けない（偽の端末で確かめる） | 422 の `documentation_url`・`errors[].field` だけに `abuse` があり、`message` と `code` に無い → None。標準出力のコメントの本文に `secondary rate limit` があり標準エラーに無い → None。終了コード 0 → None |
| `main` | 読み取りで `execve` が本物の `gh` と同じ引数で呼ばれる | `NDF_GH_GATED=1` の書き込みが順番を取らない | — |
| `bin/gh`（`test_bin_gh.py`。サブプロセスで起動） | `PYTHONPATH`・`CLAUDE_PLUGIN_ROOT` ほか NDF の環境変数を消した環境から起動して `gh_gate` が動く。`PATH` の先頭に `bin/gh`、ほかの版の NDF の `bin/gh`、本物の順に置くと本物が走る | 作業ディレクトリに `ndf/adapters/gh_gate.py` と `json.py`（読まれたら印のファイルを書く）を置いても読まれない。同じ実体へのシンボリックリンクの包みを飛ばす | 本物が無い → 127。`src` の `gh_gate` を壊すと 1 行を出して本物が走る。python が無い → 本物が走る |

## WriteGate.hold（種類で順番を取る口）

満たす決定: 決定 2、不変条件 G1・G2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `WriteGate.hold` | `adapters/write_gate.py` | 新設 | `hold(self, kind: str) -> Generator[None, Any, Any]`（`@contextmanager`） | #4016 の `acquire` の書き込みの手順 (1)〜(5) を、要求でなく種類で行う |
| `WriteGate.acquire` | 同上 | 変更（#4016 の仕様を置き換える） | #4016 のまま | `kind_of(request)` が None ならすぐ通し、ほかは `with self.hold(kind):` で通す |
| `within` の throttler の `hold`・`acquire` | 同上 | 変更（同上） | `hold(self, kind: str)`・`acquire(self, request)` | #4016 の予算を持つ手順を `hold` に置き、`acquire` は同じく `hold` を呼ぶだけ |
| `mutation_kind` | 同上 | 新設（#4016 の `kind_of` から切り出す） | `mutation_kind(query: str) -> Optional[str]` | GraphQL の query が mutation でなければ None、最初のフィールドが `create`・`add` で始まれば `CONTENT`、ほかは `WRITE` |

- `WriteGate.hold`
  - 事前条件: `kind` は `WRITE` か `CONTENT`
  - 事後条件: #4016 の `acquire` の書き込みの事後条件と同じ（送る時刻は待ちの終わり以後かつ最短の送信時刻以後で、送信のロックを持つほかの書き込みと重ならない）。文脈を出ると `sent(kind)` を呼んで送信のロックを放す（例外でも放す）
  - 単体テストの観点: #4016 の `acquire` のテスト（I1〜I6・決定 10）は `hold` に対して同じ形で書き、`acquire` は読み取りを通すことと `hold` へ渡す種類だけを縛る

## limit_words（二次レート制限の語）

満たす決定: 決定 4・5、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `limit_words` | `adapters/github.py` | 新設 | `limit_words(text: str) -> bool` | 本文に二次レート制限の語があるかを決める |
| `limit_of` | 同上 | 変更（#4016 の仕様を置き換える） | #4016 のまま | 二次レート制限の見分けに `limit_words` を使い、422 も対象にする |

- `limit_words`
  - 事後条件: 小文字にした `text` に `secondary rate limit` か `submitted too quickly` があるか、空白を除いた `text` に `"code":"abuse"` があるか、`IssueComment.data abuse` のような `<型>.<欄> abuse`（`gh` が 422 の誤りを並べる形。STD4）があれば真
  - 副作用: 無い（純粋な関数）
- `limit_of`
  - 事後条件: #4016 の条件（403・429 で本文に `secondary rate limit`、githubkit の `SecondaryRateLimitExceeded`、GraphQL の誤り）に加え、422 で応答の本文に `limit_words` が当たるものを二次レート制限とする。本文の語の照らしはどれも `limit_words` で行う。待ちの秒と `hinted` は #4016 のまま
  - 単体テストの観点: PR #4096 の記録の 2 つの 422 の本文（`code: abuse` と `was submitted too quickly`）が二次レート制限になり、`WriteGate.block` が呼ばれる。`must be part of the diff` の 422 は二次レート制限にならない（#4016 の決定 5 の行の位置の判定が続けて効く）
