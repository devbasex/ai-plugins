# #4059 の詳細設計: エージェントが打つ gh も NDF と同じ順番の記録で間を空ける

設計文書 [issue-4059-design.md](./issue-4059-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。#4016 の部品（`WriteGate`・`limit_of`）は #4016 の確定仕様（[ndf-github-write-gate.md](../docs/specifications/ndf-github-write-gate.md)）にあり、この文書は足す・置き換える関数だけを書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設。`write_gate.py` は #4016 が新設する）:

```text
plugins/ndf/
├── bin/gh *
├── contracts/limits.json
├── pyproject.toml, uv.lock（graphql-core を足す）
├── src/ndf/adapters/
│   ├── gh_gate.py *
│   ├── write_rules.py *
│   ├── github.py
│   └── write_gate.py
└── tests/adapters/
    ├── test_gh_gate.py *
    ├── test_bin_gh.py *
    ├── test_write_rules.py *
    ├── test_github.py
    └── test_write_gate.py
docs/glossary/glossary.json, docs/glossary.md
```

依存の向き: `bin/gh` → `adapters/gh_gate.py` → `adapters/write_rules.py`（最上位で読む）、書き込みと決めた後だけ `adapters/write_gate.py`・`adapters/github.py`（`limit_of` だけ）を関数の中で読む。`write_gate.py`・`github.py` は `write_rules.py` を読み、`gh_gate.py` を読まない。`write_rules.py` は標準ライブラリだけを最上位で読み（graphql-core は `mutation_kind` の中で読む）、ほかの NDF のモジュールを読まない。循環は無い。本物の `gh` の探し方は `bin/gh` の 1 か所だけに置き、`gh_gate` は `bin/gh` が渡すパスを使う（同じ規則を bash と python の 2 か所に持たない）。

import の重さ（2026-11-22 にこのマシンで `python -I -c` を 10 回走らせた平均）: 起動だけ 約 0.01 秒、`gh_gate` が最上位で読む標準ライブラリ（`json`・`os`・`subprocess`・`threading`・`tempfile`）約 0.03 秒、graphql-core の import と 1 回の `parse` 約 0.11 秒、githubkit（httpx・pydantic を含む）約 0.22〜0.25 秒。`write_gate.py` と `github.py` は githubkit を読むので、読み取りの `gh` がそれらを読まないように、照らしと分け方の関数（`mutation_kind`・`limit_words`・`limit_body`）を `write_rules.py` に置く。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `bin/gh` | 新設 | 名前 `gh` で引かれ、本物の `gh` を探し、起動先の版の `src` と NDF の仮想環境の python で `gh_gate` を起動する | `gh_gate` | シェルのスクリプト | — |
| `src/ndf/adapters/gh_gate.py` | 新設 | `gh` の引数から書き込みの種類と件数を決め、書き込みなら順番を取ってから本物の `gh` を走らせる | `write_rules`（最上位）・`write_gate`・`github`（書き込みのときだけ関数の中で） | モジュール | `main`・`plan_of`・`Plan`・`run_child`・`run_held`・`Ends`・`limit_seen`（新設） |
| `src/ndf/adapters/write_rules.py` | 新設 | GraphQL の文書の分け方と二次レート制限の照らしを、githubkit を読まずに 1 か所に置く | `graphql`（graphql-core。`mutation_kind` の中で読む） | モジュール | `mutation_kind`・`limit_words`・`limit_body`（新設） |
| `src/ndf/adapters/write_gate.py` | 変更（#4016 の新設に足す） | 種類と件数を受けて順番を取る口を足す | #4016 のとおり・`write_rules` | `WriteGate` | `WriteGate.hold`（新設）、`WriteGate.acquire`・`within` の throttler の `acquire`・`WriteGate.reserve`・`WriteGate.sent`・`Busy`・`kind_of`（#4016 の仕様を置き換える） |
| `src/ndf/adapters/github.py` | 変更（#4016 の変更に足す） | 二次レート制限の照らしを `write_rules` に任せる | `write_rules` | モジュール | `limit_of`（#4016 の仕様を置き換える） |
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
| `main` | `adapters/gh_gate.py` | 新設 | `main(argv: list[str], real: Path, environ: Mapping[str, str] = os.environ) -> int` | 引数を `plan_of` で読み、写しの無い読み取りなら本物へ置き換わり、写しのある読み取りなら `run_child` の、書き込みなら `run_held` の終了コードを返す |
| `Plan` | 同上 | 新設（型） | `NamedTuple(kind: Optional[str], count: int, argv: list[str], stdout_shown: str, tmpdir: Optional[Path])` | 種類（None は読み取り）・数える書き込みの件数・本物へ渡す引数・本物の標準出力のどこまでを親へ写すか（`"all"`・`"none"`・`"headers"`。(c) の `--silent` の行で決まる）・先読みの写しを置いた私用の一時ディレクトリ（無ければ None） |
| `plan_of` | 同上 | 新設 | `plan_of(argv: Sequence[str], stdout_tty: bool, snapshot: Callable[[str, Path], Optional[Path]] = _snapshot, count_labels: Callable[[str], int] = _count_labels) -> Plan` | `gh` の引数を下の「処理の手順」のとおり読む |
| `run_child` | 同上 | 新設 | `run_child(real: Path, plan: Plan, environ: Mapping[str, str], ends: Optional[tuple[Ends, Ends]] = None) -> int` | 本物の `gh` を子として走らせ、「出力の写し方」のとおり写して、子が終わるまで待つ（順番は取らない） |
| `run_held` | 同上 | 新設 | `run_held(real: Path, plan: Plan, gate: WriteGate, budget: float, environ: Mapping[str, str]) -> int` | `gate.within(...).hold(kind, count)` の中で `run_child` を呼び、出力の頭と末尾を持ち、二次レート制限なら `gate.block` を呼ぶ |
| `Ends` | 同上 | 新設（型） | `class Ends`（`feed(data: bytes) -> None`・`head() -> bytes`・`tail() -> bytes`・`whole() -> Optional[bytes]`） | 写した出力の頭 `HEAD_BYTES` と末尾 `TAIL_BYTES` を口ごとに持つ。`whole` は足したバイトの総数が `HEAD_BYTES + TAIL_BYTES` 以下なら頭と末尾の重なりを除いて全体を返し、越えていれば None（途中が欠けたので、頭と末尾をつないだ値を返さない） |
| `limit_seen` | 同上 | 新設 | `limit_seen(plan: Plan, out: Ends, err: Ends, now: float) -> Optional[float]` | 本物の `gh` の出力から二次レート制限を見分け、待つ秒を返す（違えば None） |

- `main`
  - 事後条件: `plan.kind` が None で `plan.tmpdir` が None なら `os.execve(real, ["gh", *plan.argv], environ)` で置き換わる（戻らない）。`plan.kind` が None で `plan.tmpdir` があれば（先読みした文書が読み取りだった）、`execve` せずに `run_child` の終了コードを返す（順番は取らない）。書き込みなら `run_held(..., budget=limits.get("github.gh-wait").interval)` の終了コード。`plan.tmpdir` があれば、子が終わった後、戻る前に `shutil.rmtree` でディレクトリごと消す（例外・中断でも `finally` で消す）。`execve` する経路には写しが無いので、消し忘れが起きない
  - 副作用: 書き込みのときだけ、`write_gate`・`github` を関数の中で import し、`machine_gate()` の記録とロック（#4016）を使う。読み取りの経路は githubkit を読まない（`write_rules` と標準ライブラリだけ。graphql-core は `gh api graphql` の文書を読むときだけ）
- `plan_of`
  - 事前条件: `argv` は `gh` の後ろの引数（`gh` 自身を含まない）。`stdout_tty` は親の標準出力が端末か
  - 事後条件: 下の「処理の手順」の表のとおり。どの行にも当たらなければ `kind=WRITE`・`count=1`。読み取りと決めるのは、引数を最後まで読めて、表の読み取りの行に当たるときだけ（読めない語・知らないフラグがあれば書き込みの側へ倒す）
  - 副作用: 先読みは「先読みの契約」のとおり。`gh label clone` のときだけ `count_labels` が本物の `gh` で元のリポジトリのラベルの総数を読む（`gh` の起動 1 回・API の要求 1 回。「ラベルの数え方」）
- `run_child`
  - 事後条件: 本物の `gh` の終了コードをそのまま返す（シグナルで終われば 128 + 番号）。どの終わり方でも、戻る前に子は終わっていて、子のパイプは閉じている
  - 副作用: 子の環境に `NDF_GH_GATED=1` を足す（ほかは `environ` のまま）。`subprocess.Popen(..., restore_signals=True)` で起動し、子の `SIGPIPE` を既定に戻す。標準入力は子へそのまま渡す（継ぐ）。標準出力・標準エラーは「出力の写し方」のとおりで、`ends` があればその `Ends` へ足す
- `run_held`
  - 事前条件: `plan.kind` は `WRITE` か `CONTENT`。`budget` は秒（> 0）
  - 事後条件: `Busy` を受けたら本物を走らせずに、標準エラーへ「異常系の処理」の 1 行を出して 1 を返す。それ以外は `run_child` の終了コードをそのまま返す。`limit_seen(...)` が秒を返したら、`hold` の出口より前に `gate.block(秒)` を呼んでいる。どの終わり方でも、戻る前に子は終わっていて、`hold` の出口で送信のロックを放している
  - 送信のロックの時間切れ（決定 6）: `within` の送信のロックの待ちは #4016 のとおり `min(github.write-send-lock, remaining())`。待ちの上限が `github.write-send-lock`（60 秒）だった時間切れ（取りに行った時点の残りの予算が 60 秒を越えていた）は #4016 の I5 で、送信のロックを持たずに本物を走らせ、#4016 の 1 行を出す。待ちの上限が `remaining()` だった時間切れ（残りの予算が 60 秒以下）は `Busy(until=None)` で、本物を走らせない
- `limit_seen`
  - 事後条件: 本物の終了コードが 0 なら None。0 以外なら、次の順で見分け、最初に「決まった」段の結果を返す（後ろの段へ進まない）
    - (1) 標準出力の頭が `gh api -i` の応答の頭（`HTTP/<版> <状態>` の行・ヘッダー・空行）なら、ここで決まる。次の順で照らす
      - (1a) 状態が 200・403・429・422 のどれでもなければ None（(2) と同じ除外。HTTP の状態が分かった時点で対象外の状態を除き、本文の有無によらず (3) の自由文へ落とさない）
      - (1b) `out.whole()` が None でなく（標準出力が欠けていない）、その空行の後に本文があれば、状態が 200（GraphQL の HTTP 200 の誤り）なら本文の JSON の `errors[].message` を改行でつないで `message` にし、ほかの状態なら本文の全文（`out.whole()` の空行の後のすべて）を `message` にして、#4016 の `limit_of(status, headers, message, now)` に渡す。当たれば `limit.seconds`（`hinted` が偽なら `BLOCK_SECONDS`）、当たらなければ None
      - (1c) 空行の後の本文が空（本文の無い応答。`--silent` を外せなかった `-i --silent --paginate` は状態の行とヘッダーだけを出す）か、`out.whole()` が None（標準出力が `HEAD_BYTES + TAIL_BYTES` を越え、本文の途中が欠けた。頭と末尾をつないで JSON として読まない）なら、本文の代わりに (3) と同じ標準エラーの誤りの行を `limit_words` で照らす。当たれば `limit_wait(headers, now)` の `seconds`（`hinted` が偽なら `BLOCK_SECONDS`）、当たらなければ None。空の本文を `limit_of` に渡して None に確定させない（`Retry-After` 付きの 403 で標準エラーに `gh: secondary rate limit (HTTP 403)` が出たら、ヘッダーの秒で共有の書き込みを止める）
    - (2) そうでなく、標準エラーの `gh: … (HTTP <状態>)` の行から状態が分かり、その状態が 403・429・422 のどれでもなければ、ここで None に決まる（`limit_body` の「ほかの状態」と同じ除外。本文が JSON でも非 JSON でも (3) へ落とさないので、`gh: secondary rate limit (HTTP 500)` は止めない）。状態が 403・429・422 で、`out.whole()` が None でなく、その全体が JSON として読めれば、ここで決まる（`out.whole()` が None なら本文が欠けたので (3) へ進む）。`limit_body(status, body)` が真なら `BLOCK_SECONDS`、偽なら None（偽も確定の結果で、(3) の自由文へ落とさない。githubkit を通る書き込みと同じ結果になる）
    - (3) そうでなく（状態が分からない、または 403・429・422 で本文が JSON として読めない）、標準エラーの末尾の行のうち、`gh: `・`GraphQL: `・`HTTP <3 桁>: ` で始まる行（`gh` が誤りを出す形。STD4）のどれかに `limit_words(行)` が当たれば `BLOCK_SECONDS`。状態が 422 と分かるのに本文が取れない（端末の口・`--silent` を外せない組み合わせ・JSON でない）ときもこの段で照らし、行の最上位の誤りの文と `errors[]` の行を区別せずに当てる（本文の欄を分けられないので、止める側へ倒す。受け入れ条件 3 の境界）
    - (4) ほかは None。標準出力の文字列そのものには `limit_words` を当てない（コメントの本文などを写した出力に語があっても当てない）

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
- `--silent` を外して本物へ渡すのは、書き込みと決まり、親の標準出力が端末でなく、`--silent` があり、`--jq`・`-q`・`--template`・`-t`・`--verbose` のどれも無いときだけ。そのうち `-i`・`--include` も無ければ `plan.stdout_shown` を `"none"`、`-i`・`--include` があり `--paginate` が無ければ `"headers"` にする。それ以外は元の引数のまま渡し、`"all"` にする（`gh` は `--silent` と `--jq`・`--template`・`--verbose` の併用を引数の誤りとして送らずに断るので、外すと本来送られない書き込みが送られる。`--paginate` はページごとに状態の行とヘッダーが続くので、`"headers"` の写し分けでは出力の形を変えうる。https://github.com/cli/cli/blob/v2.103.3/pkg/cmd/api/api.go ）。`gh` は `-i --silent` の 422 の本文を捨て、標準エラーには `gh: Validation Failed (HTTP 422)` しか出さない（STD4）ので、`"headers"` で本文を手元へ残して (1b) で照らす。元の引数のままの `-i --silent --paginate` は `limit_seen` の (1c) で、状態とヘッダーと標準エラーの誤りの行で照らす（本文が無いので 422 の `abuse` は見分けない。受け入れ条件 3 の境界）

| 条件 | 結果 |
| --- | --- |
| 方法が `GET`・`HEAD`（大文字小文字を問わない） | 読み取り |
| 方法の指定が無く、フィールド（`-f`・`-F`・`--field`・`--raw-field`）も `--input` も無い | 読み取り（`gh` は GET で送る） |
| エンドポイントが `graphql`（方法が `GET` 以外） | 文書と `operationName` を (d) で読み、`mutation_kind(document, operation_name)` の結果。None なら読み取り |
| 方法が `POST`、または指定が無くフィールドか `--input` がある（`gh` は POST で送る） | `CONTENT` |
| 方法が `PATCH`・`PUT`・`DELETE`・ほかの語 | `WRITE` |

**(d) GraphQL の文書。** `gh` は `--input` があればそのファイルを HTTP の本文にし、`-f`・`-F` のフィールドを URL のクエリの引数へ移す（https://github.com/cli/cli/blob/v2.103.3/pkg/cmd/api/api.go#L339-L349 ）。そこで文書の取り出し元を次の 3 つに分ける。
- `--input` があり、フィールドが 1 つも無い: 本文の JSON の `query` と `operationName` だけを使う
- `--input` が無い: `-f query=<文>`・`-F query=<文>`・`-F query=@<パス>` を文書、`-f`・`-F` の `operationName=<名前>` を `operationName` にする（同じキーが複数あれば実行される操作を断定できないので `CONTENT`）
- `--input` とフィールドを併用している: GitHub が本文と URL のどちらの文書を実行するかを断定できないので、読まずに `CONTENT`・件数 1

パスは「先読みの契約」で読む。読めない・どこにも無いときは `CONTENT`。`mutation_kind` は graphql-core の `parse` で文書を構文として読み（https://spec.graphql.org/October2021/#sec-Language.Operations ）、`operationName` があればその名前の操作、無ければ文書に操作が 1 つだけのときその操作を「実行される操作」とする。決まらない（名前の操作が無い・名前が無く操作が複数・構文の誤り）なら `CONTENT`。実行される操作が `query`（省略形 `{ … }` を含む）なら None、`subscription` は `CONTENT`、`mutation` なら最上位の選択（フラグメントの展開とインラインフラグメントをたどる）のフィールドの名前（別名でなく、`a: createIssue` なら `createIssue`）のどれかが `create`・`add` で始まれば `CONTENT`、ほかは `WRITE`。`fragment` の定義は操作として数えない（`fragment F on Query { … } mutation { … }` は mutation）

**(e) ほかのグループ。** グループとサブコマンドの組で決める。上の行から順に最初に当たる行の結果にする。

| グループ | サブコマンド | 結果 | 件数 |
| --- | --- | --- | --- |
| `auth`・`search`・`help`・`version`・`completion`・`config`・`alias`・`browse`・`status` | どれでも | 読み取り | — |
| `label` | `clone` | `CONTENT`（宛先にラベルを作る・`--force` で書き換える。https://cli.github.com/manual/gh_label_clone ） | 「ラベルの数え方」のとおり |
| `pr` | `revert` | `CONTENT`（取り消しの PR を作る。https://cli.github.com/manual/gh_pr_revert ） | 1 |
| `repo` | `fork` | `CONTENT`（リポジトリを作る） | 1 |
| `release` | `create` | `CONTENT` | 上限の見積もり: 1（リリースの作成）+ A（アセットのアップロード）+ 1（A が 1 以上で `--draft`・`-d` が無いとき、公開の更新。公開に失敗すれば代わりに下書きの削除）+ 1（`--generate-notes` があるとき、リリースノートを作る POST） |
| `release` | `upload` | `CONTENT` | 上限の見積もり: A（`--clobber` があれば 2 × A。同じ名前の既存のアセットを消してから上げる） |
| `pr`・`issue`・`run`・`release`・`repo`・`gist`・`workflow`・`label`・`cache`・`ruleset`・`project`・`secret`・`variable`・`org`・`codespace`・`extension` | `view`・`list`・`status`・`diff`・`checks`・`watch`・`download`・`checkout`・`clone`・`logs` | 読み取り（`label clone` は上の行が先に当たる） | — |
| どれでも | `create`・`comment` | `CONTENT` | 1 |
| `pr` | `review` | `CONTENT` | 1 |
| ほか | 上のどれでもない（`edit`・`close`・`merge`・`ready`・`delete`・グループだけ・知らない語） | `WRITE` | 1 |

- A（アセットの数）: タグの後の位置引数を `gh` と同じに展開した数（https://github.com/cli/cli/blob/v2.103.3/pkg/cmd/release/shared/upload.go の `AssetsFromArgs`）。`#` を含む語は `#` より前をそのまま 1 つのファイルと数え、ほかの語は `glob.glob` で展開した数（`gh` の `GlobPaths` と同じく、展開が空なら `gh` が誤りで終わるので、その語は 1 と数える）
- 計算の起点: `gh release create v1 a.zip b.zip` は 1 + 2 + 1 = 4 件（下書きで作り、2 つ上げ、公開する。https://github.com/cli/cli/blob/v2.103.3/pkg/cmd/release/create/create.go の `draftWhileUploading`）

**ラベルの数え方（`gh label clone`）。** `gh label clone` は元のリポジトリのラベルを件数の上限なしで全件読み、1 つずつ宛先へ作る（https://github.com/cli/cli/blob/v2.103.3/pkg/cmd/label/clone.go の `listLabels(…, Limit: -1)`）。`gh label list` は既定で 30 件までしか返さないので使わない。
- 元のリポジトリ: `clone` の後の最初の位置引数（`[HOST/]OWNER/REPO`。`gh` の `ghrepo.FromFullName` と同じ形）。宛先は `-R`・`--repo` で、数えるのに使わない。3 つに分かれれば先頭をホストにして `--hostname <ホスト>` を付け、2 つなら付けない（`gh` の既定のホストに任せ、`clone` の元の解決と同じにする）。位置引数が無い・形が合わなければ数えずに件数 1（本物の `gh` も引数の誤りで送らずに終わる）
- 読み方: `count_labels` は本物の `gh` を `NDF_GH_GATED=1` で 1 回起動し、`gh api graphql [--hostname <ホスト>] -f query='query($o:String!,$n:String!){repository(owner:$o,name:$n){labels{totalCount}}}' -F o=<OWNER> -F n=<REPO> --jq .data.repository.labels.totalCount` の出力を整数として読む。`totalCount` はページに依らない総数なので、ラベルが何件あっても要求は 1 回で全件を数える。`COUNT_SECONDS` 秒で打ち切る
- 結果の扱い: 1 以上の整数ならその数を件数にする。0 なら件数 1（`hold` の事前条件の `count` ≥ 1 を守る。作るラベルは無いが、`clone` は宛先の読み取りを送るだけなので 1 件分の待ちで足りる）。起動の失敗・終了コードが 0 以外・時間切れ・整数でない出力なら件数 1 とし、標準エラーへ `ndf: ラベルの数を読めず、書き込みを 1 件と数える` を 1 行出す（総数を確かめられないので少なく数えうる。元を読めない `clone` は本物も同じ理由で送らずに終わることが多い）

- 計算量の注意: 引数を 1 回なめるだけ。先読みするファイルは query か `--input` の 1 つだけ。`release` のアセットの glob の展開はローカルのファイルの一覧だけ。`label clone` だけ `gh` の起動 1 回と API の要求 1 回（ページの取得なし）を足す（エージェントがまれに打つコマンドで、書き込みの件数を数えるためにだけ使う）

### 先読みの契約

- 先読みするのは、`gh api graphql` の文書を (d) で読むときの `-F query=@<パス>`・`--input <パス>` だけ。REST の `--input` は方法をフラグで決めるので読まない
- 開く前に除くパス: `-`（標準入力）と、ファイル記述子を指すパス（`os.path.realpath` の前後のどちらかが `/dev/fd/`・`/proc/self/fd/`・`/proc/<数>/fd/` で始まる、または `/dev/stdin`・`/dev/stdout`・`/dev/stderr`。Bash のプロセス置換 `<(…)` を含む）は開かずに（消費せず、共有の読み取り位置も動かさずに）`CONTENT`・件数 1 とし、引数はそのまま本物へ渡す。`os.stat` はこれらの指す先をたどるので、`stat` の結果では判定しない
- ほかのパスは `os.open(path, O_RDONLY | O_NONBLOCK | O_NOCTTY | O_CLOEXEC)` で開き（FIFO でも開くときに止まらない）、開いた記述子を `os.fstat` して、通常のファイルのときだけ読む。`stat` と `open` の間に入れ替わっても、実際に読む記述子の種別で決まる。通常のファイルでない（FIFO・デバイス・ソケット・ディレクトリ）・開けないなら、読まずに閉じて `CONTENT`・件数 1、引数はそのまま
- 通常のファイルなら、`snapshot(path, tmpdir)` がその記述子から 1 回だけ読み、内容を私用の一時ディレクトリ（`tempfile.mkdtemp`、0700。`plan_of` が先読みの要るときだけ作る）のファイルへ書き、分類はその内容で行い、本物へ渡す引数のパスをその一時ファイルへ差し替える（`-F query=@<一時ファイル>`・`--input <一時ファイル>`）。分類した内容と送る内容は同じバイト列になり、待ちの間に元のファイルが変わっても食い違わない
- 写しの後始末: 一時ディレクトリは `Plan.tmpdir` に載る。写しを持つ `Plan` は読み取りでも `execve` せず、`main` が `run_child`（読み取り）か `run_held`（書き込み）で子として走らせ、子が終わった後にディレクトリごと消す（`main` の事後条件）。写しの分類が読み取りでも元の引数へ戻して `execve` はしない（分類した内容と送る内容を同じにする契約を、読み取りでも崩さない）

### 出力の写し方

- 標準出力・標準エラーは、親の側の口が端末でなければパイプで受け、2 つのパイプを別のスレッドで読み、読んだ順に親の同じ口へ書いてから `Ends` へ足す（口ごとの順を保つ）
- 親の側の口が端末なら、その口はパイプにせずに継ぎ、見分けに使わない（`gh` の端末向けの表示を変えない）。端末の口だけに出る誤りは見分けられない（受け入れ条件 3 の境界。Claude Code の Bash は端末を割り当てない）
- `gh api --silent`（`plan.stdout_shown` が `"none"`）: (c) の条件に当たるときだけ、`plan_of` は `--silent` を外した引数を本物へ渡し、`run_child` は本物の標準出力を親へ写さずに `Ends` へだけ足す。利用者に見える出力は `--silent` と同じで、422 の本文の `code: abuse` を見分けられる
- `gh api -i --silent`（`plan.stdout_shown` が `"headers"`）: `plan_of` は `--silent` を外した引数を本物へ渡す。`run_child` は本物の標準出力を `Ends` へすべて足し、親へは先頭から最初の `\r\n` だけの行（ヘッダーの後の空行）までを写し、その後のバイトは写さない。空行が来ないまま子が終われば、来た分をすべて写す（204 などで `gh` が空行の後に何も書かないときも同じ）。`gh` 2.103.3 の `-i` の出力は `-i --silent` の出力の後ろに本文を足しただけで、空行までのバイト列は同じなので（STD4 の偽の API サーバーで実測）、利用者に見える出力は `-i --silent` と同じで、422 の本文の `code: abuse` を (1b) で見分けられる。パイプから読んだ塊の境で行が分かれても、行の頭からの続きとして空行を見分ける（塊ごとに照らして見落とさない）
- ほか（`plan.stdout_shown` が `"all"`）は `--silent` を残し、本文は見分けない（`-i --silent --paginate` の状態の行とヘッダーは今のとおり親へ写り、`limit_seen` の (1c) で標準エラーの誤りの行と合わせて照らす）
- 長い出力: `Ends` は口ごとに頭と末尾だけを持つので、口の出力が `HEAD_BYTES + TAIL_BYTES`（128 KiB）を越えると `whole` が None になり、`limit_seen` は本文を欠けたものとして (1c)・(3) で照らす。本文の欄だけにある 422 の `abuse` はそのとき見分けない（受け入れ条件 3 の境界。GitHub の二次レート制限の応答の本文は数百バイトで掛からない）。全体を持たないのは、コメントの一覧などの大きな出力を書き込みのたびにメモリへ溜めないため
- 親の口へ書くときに読み手が閉じていた（`BrokenPipeError`・`EPIPE`）ら、その口のスレッドは親へ書くのをやめ、子のパイプの読み口を閉じて抜ける。子は次に書いたときに `SIGPIPE`（既定の動作）で終わり、`gh` を直に打ってパイプの先が閉じたときと同じ終わり方になる。`run_child` は子の終わりを待って戻り、`run_held` は `hold` を出てロックを放す。もう一方の口のスレッドは続けて読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `bin/gh` | 本物の `gh` が無い | `gh` が入っていない・`PATH` に包みしか無い | `PATH` をなめた結果が空 | 止める | 標準エラー `ndf: 本物の gh が PATH に無い`、127 | `gh` を入れて打ち直す |
| `bin/gh` | 仮想環境の python が無い・実行できない | `ndf` をまだ一度も打っていない・版を入れ替えた直後・基の python が消えた | `[ -x <python> ]`・`exec` の失敗（`execfail`） | 本物の `gh` へ置き換わる | 無し（`gh` の出力だけ） | 次に `ndf` が仮想環境を作った後は包む |
| `bin/gh` の `$BOOT` | `gh_gate` を import できない | `src` が壊れた・依存（graphql-core など）が仮想環境に無い | `BaseException` | 本物の `gh` へ置き換わる | 標準エラー `ndf: gh の包みを読み込めない（<例外の型>）。順番を取らずに gh を走らせる`、本物の終了コード | `ndf` を打って仮想環境を作り直すと包む |
| `run_held` | 待ちが上限に収まらない（次に送れる時刻が分かる） | 二次レート制限の待ちの終わり・ほかのレーンの予約が並ぶ | `Busy` の `until` が None でない（#4016 の `Busy` を置き換える） | 本物を走らせずに返す | 標準エラー `ndf: GitHub への書き込みの順番を <予算> 秒の内に取れない。<until の時刻 JST> 以後に打ち直す`、1 | 予約は `hold` が取り消す（#4016 の `withdraw`）。時刻の後に打ち直せる |
| `run_held` | 待ちが上限に収まらない（時刻が分からない） | 記録のロック・送信のロックを、上限を `remaining()` にした待ちの内に取れない（残りの予算が 60 秒以下のときのほかのレーンの長い書き込みなど）・予算が 0 以下 | `Busy` の `until` が None | 本物を走らせずに返す | 標準エラー `ndf: GitHub への書き込みの順番を <予算> 秒の内に取れない（ほかの書き込みが送信中）。少し後で打ち直す`、1。時刻は出さない | 同上 |
| `run_held` | 記録を読めない・ロックを取れない | 壊れた JSON・権限・固まったプロセス・上限が `github.write-send-lock`（60 秒。残りの予算がそれより長い）の送信のロックの時間切れ | #4016 の `reserve`・`hold` | #4016 の I5（ロックを持たずに送る） | #4016 の 1 行 | 次の `gh` でまた試す |
| `run_held` | 本物の `gh` を起動できない | 実行の権限が無い・消えた | `subprocess.Popen` の `OSError` | 呼び出し元へ返す | 標準エラー `ndf: gh を起動できない: <理由>`、126 | `hold` の出口で送信のロックを放す（送っていないが、出口の `sent` は延ばす側にしか倒れない） |
| `run_held` | 待ちの途中・走らせている途中で中断された | Ctrl-C・Bash の時間切れ | `KeyboardInterrupt`・シグナル | 子へ同じシグナルを送り、子の終わりを待ってから返す | 子の終了コード（シグナルなら 128 + 番号） | `hold` の出口でロックを放す |
| `run_held` | 写し先の読み手が先に閉じた | `gh … \| head -c 1` | 親の口への書き込みの `BrokenPipeError` | 「出力の写し方」のとおり、子のパイプを閉じて子の終わりを待つ | 子の終了コード（`SIGPIPE` なら 141） | `hold` の出口でロックを放す |
| `run_held` | `block` を書けない | 記録の書き込みの失敗 | #4016 の `block` | 既定値で続ける | #4016 の 1 行 | 次の書き込みの待ちは延びない |
| `plan_of` | `label clone` の元のラベルの数を読めない | 元のリポジトリが無い・権限・ネットワーク・`COUNT_SECONDS` の時間切れ | `count_labels` の起動の失敗・0 以外の終了・時間切れ・整数でない出力 | 件数 1 で続ける | 標準エラー `ndf: ラベルの数を読めず、書き込みを 1 件と数える` | 本物の `gh label clone` も同じ理由で失敗しうる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `github.gh-wait` | 設定値 | `contracts/limits.json` | `interval` 90（`gh` の書き込みが順番を待つ上限の秒。Claude Code の Bash の既定 120 秒から `gh` が走る時間を残す） |
| `BLOCK_SECONDS` | 定数（設定値から読む） | `adapters/gh_gate.py` | `cross-review.secondary-rate-limit` の `routing.backoff` の 1 回目（60 秒。#4016 の決定 8 の `hinted` が偽のとき）。応答のヘッダーが待ちの秒を示すときは使わない（`limit_seen` の (1b)・(1c)） |
| `HEAD_BYTES` | 定数 | `adapters/gh_gate.py` | 65536（口ごとに持つ頭のバイト数。`gh api -i` の応答の頭を読む） |
| `TAIL_BYTES` | 定数 | `adapters/gh_gate.py` | 65536（口ごとに持つ末尾のバイト数。口の出力の全体が `HEAD_BYTES + TAIL_BYTES` 以下なら `Ends.whole` が全体を返す） |
| `COUNT_SECONDS` | 定数 | `adapters/gh_gate.py` | 10（`label clone` のラベルの総数を読む `gh` の打ち切りの秒） |
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
| `plan_of` | #4016 の `write_gate.kind_of`（`httpx.Request` を読む） | #4016 の詳細設計 | 要求を作らない `gh` には使えない。GraphQL の文書の分け方は `kind_of` と同じ `write_rules.mutation_kind(document, operation_name)` を共有する |
| `mutation_kind` | graphql-core（GraphQL の参照実装の Python 版。純 Python・依存無し・MIT） | PyPI と仕様の操作の選び方 | 使う。`fragment` の先置き・複数の操作と `operationName`・別名を自前の字句の読みで漏れなく扱うより、構文木で読む方が確か。import は graphql の文書を読むときだけ（import と 1 回の `parse` で約 0.11 秒。ほかの `gh` の起動を遅くしない） |
| `write_rules` の置き場 | `write_gate.py`・`github.py`（どちらも githubkit を最上位で読む） | 地図の「import の重さ」の実測 | 置かない。読み取りの `gh` が githubkit の約 0.22〜0.25 秒を払わないよう、githubkit を読まないモジュールに分ける |
| 本物の `gh` の探し方 | `shutil.which`・`session_env.should_drop` | `grep -rn "def should_drop" plugins/ndf/src` | `shutil.which` は自分を飛ばせない。python を起動しない速い道（G3）と import の失敗の置き換えでも本物が要るので、`bin/gh` の bash に 1 つだけ置き、`should_drop` の NDF のほかの版の `bin` の条件を写す |
| `run_held` の待ち | #4016 の `WriteGate.within` と `Busy` | #4016 の詳細設計 | 使う（予算による打ち切りと予約の取り消しを持つ）。`Busy` に `until` を足す |
| `limit_seen` | #4016 の `limit_of` | #4016 の詳細設計 | `gh api -i` で状態とヘッダーと本文が読めるときは `limit_of` をそのまま使う（待ちの秒の計算を共有する）。本文が空（`-i --silent --paginate`・本文の無い応答）か欠けた（128 KiB を越えた）なら `limit_of` から切り出した `limit_wait` で待ちの秒を決める。状態とヘッダーが読めないときは `limit_body`・`limit_words` を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `plan_of` | 表 (c)・(e) の各行 | `-R owner/repo` が先頭にある、`--method get`（小文字）、`--method=POST`・`-XPOST`・`-fbody=x`・`--field=x=1` → `CONTENT`、`-X GET -X POST` → `CONTENT`（最後が効く）、`-H 'X: -f' repos/o/r` → 読み取り、`-- -f` の後の `-f` はフィールドでない、`gh pr comment 123 --body '--help'` → `CONTENT`、`gh pr comment --help` → 読み取り、`gh pr revert 1` → `CONTENT`、`gh api graphql --input q.json -f query='query{viewer{login}}'`（本文は mutation）→ `CONTENT`（併用は読まない）、`--input` だけ → 本文の操作で決まる。`--silent` だけ → 外れ `stdout_shown` が `"none"`、`-i --silent`・`--include --silent` → 外れ `"headers"`、端末・`-i --silent --paginate`・`--silent --jq .x`・`--silent --verbose` → 元の引数のまま `"all"` | 表に無いフラグのある `gh api` → `CONTENT` |
| 件数（`plan_of` の `count`） | `gh release create v1 a.zip b.zip` → 4、`--draft` を足す → 3、`--generate-notes` を足す → 5、`gh release upload v1 a.zip` → 1・`--clobber` → 2 | `gh release upload v1 'dist/*.zip'`（一時ディレクトリに 3 つ）→ 3、`'a.zip#表示名'` は展開せず 1、展開が空の語 → 1。`gh label clone o/src` → 偽の `count_labels` が返す 45（30 を越える数）、`gh label clone ghe.example/o/src` → `count_labels` に `--hostname ghe.example` が渡る、総数 0 → 1 | `count_labels` の失敗・時間切れ・整数でない出力 → 1 と 1 行。`count_labels` が本物の `gh` を `label list` でなく `api graphql` の `totalCount` で 1 回だけ起動する |
| `mutation_kind` | query → None、`addComment` → `CONTENT`、`resolveReviewThread` → `WRITE` | `fragment F on Query {…} mutation { addComment … }` → `CONTENT`、`query A {…} mutation B { createIssue … }` に `operationName=B` → `CONTENT`・`A` → None、名前無しで操作が 2 つ → `CONTENT`、`a: createIssue` → `CONTENT`、`createIssue: resolveReviewThread` → `WRITE`、フラグメントの展開の中の `addComment` → `CONTENT` | 構文の誤り → `CONTENT` |
| 先読み（`snapshot`） | 通常のファイルの query の mutation を分け、本物へ渡すパスが一時ファイルで、内容が分類したものと同じ。待ちの間に元のファイルを書き換えても本物が読む内容は変わらない。通常のファイルの query が読み取り → `execve` せずに子で走らせ、子の終わった後に一時ディレクトリが残らない | `--input -`・FIFO・`<(…)`（`/dev/fd/<n>`）→ 開かずに `CONTENT`、FIFO の書き手のデータを本物の `gh` がすべて読む。`--input /dev/fd/0 < 通常のファイル` → 開かずに `CONTENT`、本物の `gh` が先頭から全部を読む。`stat` の後に通常のファイルを FIFO へ入れ替える偽の `os.stat` → 開いた記述子の `fstat` で FIFO と分かり、止まらずに `CONTENT` | ファイルが無い → `CONTENT`。書き込み・読み取り・中断のどれで終わっても一時ディレクトリが残らない |
| `run_held` | 偽の本物の `gh` が 0 で終わり、出力がそのまま写り、終了コードが 0。`hold` が `plan.count` で呼ばれる | 待ちがちょうど `budget` → 走らせない（#4016 の決定 10 の比べ方）。`Busy(until=t)` → 時刻の 1 行、`Busy(until=None)` → 時刻の無い 1 行。ほかのプロセスが送信のロックを持ち続け、取りに行く時点の残りの予算が 80 秒 → 60 秒で I5 の 1 行を出して本物が走る。残りの予算が 30 秒 → 30 秒で `Busy(until=None)`・本物は走らない | `Busy`・起動できない・中断。偽の `gh` が 1 MiB を書く途中で写し先が 1 バイト読んで閉じる → 子が終わり、`run_held` が 5 秒の内に戻り、送信のロックが放されている |
| `limit_seen` | 403 の `secondary rate limit`（標準エラー）、422 の JSON（`was submitted too quickly`・`errors[].code` が `abuse`）、`GraphQL: was submitted too quickly` → `BLOCK_SECONDS` | `-i` の出力の `Retry-After: 120` の 403 → 120 秒、`-i --silent --paginate` の出力（状態の行とヘッダーだけで本文が空）の `Retry-After: 120` の 403 で標準エラーが `gh: secondary rate limit (HTTP 403)` → 120 秒（(1c)。`Retry-After` が無ければ `BLOCK_SECONDS`）、同じ出力で標準エラーに語が無い → None、`-i` の HTTP 200 の GraphQL の `errors[].message` に `was submitted too quickly` → `BLOCK_SECONDS`、`--silent` の 422 の `code: abuse` → `BLOCK_SECONDS`（利用者の標準出力は空）。`-i --silent` の 422 の `code: abuse`（STD4 の実出力: 状態の行・ヘッダー・`\r\n` の空行・本文、標準エラーは `gh: Validation Failed (HTTP 422)`）→ `BLOCK_SECONDS`、利用者の標準出力は空行までで、元の引数の `-i --silent` の出力とバイト列が同じ。空行が 2 つの塊に分かれて届いても同じ。長い応答: 標準出力がちょうど `HEAD_BYTES + TAIL_BYTES` の 422 の `abuse` → `BLOCK_SECONDS`、1 バイト越える同じ形（`whole` が None）→ (1c) で標準エラーの行で照らし、`gh: Validation Failed (HTTP 422)` だけなら None、同じ長さの 403 で `gh: secondary rate limit (HTTP 403)` と `Retry-After: 120` → 120 秒。`-i` の無い同じ長さの 422 → (2) で決めずに (3) へ進む。端末の口・`-i` なしで 422 と分かり本文が無い → 標準エラーの行で照らす。親の標準出力が端末のときは本文を見分けない（偽の端末で確かめる） | 422 の `documentation_url`・`errors[].field`・最上位の `message` だけに語があり、`errors[]` の `message` と `code` に無い → 標準エラーの `gh: …` の行に語が出ても None（(2) で決まり、(3) へ落ちない）。404・500 の非 JSON の本文に `secondary rate limit` → None。標準エラーが `gh: secondary rate limit (HTTP 500)`・`gh: was submitted too quickly (HTTP 404)` で標準出力が非 JSON か空 → None（(2) で状態により決まり、(3) へ落ちない）。`-i --silent --paginate` の `HTTP/2 500` の状態の行とヘッダーだけの出力で標準エラーが `gh: secondary rate limit (HTTP 500)` → None（(1a)）。`-i` の出力の `x-ratelimit-reset` が付き `x-ratelimit-remaining` が正の 403 の `secondary rate limit` → `BLOCK_SECONDS`（reset までの秒にしない）。標準出力のコメントの本文に `secondary rate limit` があり標準エラーに無い → None。終了コード 0 → None |
| `main` | 読み取りで `execve` が本物の `gh` と同じ引数で呼ばれる。読み取りの経路で `sys.modules` に `githubkit`・`ndf.adapters.write_gate`・`ndf.adapters.github` が無い（`graphql` は `gh api graphql` のときだけ） | `NDF_GH_GATED=1` の書き込みが順番を取らない | — |
| `bin/gh`（`test_bin_gh.py`。サブプロセスで起動） | `PYTHONPATH`・`CLAUDE_PLUGIN_ROOT` ほか NDF の環境変数を消した環境から起動して `gh_gate` が動く。`PATH` の先頭に `bin/gh`、ほかの版の NDF の `bin/gh`、本物の順に置くと本物が走る | 作業ディレクトリに `ndf/adapters/gh_gate.py` と `json.py`（読まれたら印のファイルを書く）を置いても読まれない。同じ実体へのシンボリックリンクの包みを飛ばす | 本物が無い → 127。`src` の `gh_gate` を壊すと 1 行を出して本物が走る。python が無い → 本物が走る |

## WriteGate.hold（種類と件数で順番を取る口）

満たす決定: 決定 2・6・7、不変条件 G1・G2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `WriteGate.hold` | `adapters/write_gate.py` | 新設 | `hold(self, kind: str, count: int = 1) -> Generator[None, Any, Any]`（`@contextmanager`） | #4016 の `acquire` の書き込みの手順 (1)〜(5) を、要求でなく種類と件数で行う |
| `WriteGate.reserve` | 同上 | 変更（#4016 の仕様を置き換える） | `reserve(self, kind: str, replacing=None, count: int = 1, remaining=None) -> Reservation` | 予約の後ろの間隔を件数の分だけ空け、`CONTENT` は件数の分をウィンドウに予約する |
| `WriteGate.sent` | 同上 | 変更（同上） | `sent(self, reservation: Reservation, started: Optional[float] = None) -> None` | 一覧の自分の件数分の項目を送り終えた時刻へ置き換え、実際に送り始めた時刻から件数の分の間隔と、送り終えた時刻から間隔 1 つ分の遅いほうまで、最短の送信時刻を延ばす |
| `WriteGate.acquire` | 同上 | 変更（同上） | #4016 のまま | `kind_of(request)` が None ならすぐ通し、ほかは `with self.hold(kind, 1):` で通す |
| `within` の throttler の `hold`・`acquire` | 同上 | 変更（同上） | `hold(self, kind: str, count: int = 1)`・`acquire(self, request)` | #4016 の予算を持つ手順を `hold` に置き、`acquire` は件数 1 で `hold` を呼ぶだけ |
| `Busy` | 同上 | 変更（同上） | `Busy(seconds: float, until: Optional[float] = None)` | `until` は次に送れる時刻（エポック秒）。分からなければ None |
| `mutation_kind` | `adapters/write_rules.py` | 新設（#4016 の `kind_of` から切り出す） | `mutation_kind(document: str, operation_name: Optional[str] = None) -> Optional[str]` | GraphQL の文書の実行される操作を読み取り（None）・書き込み・内容を作る書き込みに分ける |
| `kind_of` | `adapters/write_gate.py` | 変更（同上） | #4016 のまま | `/graphql` の POST は本文の JSON の `query` と `operationName` を `write_rules.mutation_kind` に渡す |

- `WriteGate.hold`
  - 事前条件: `kind` は `WRITE` か `CONTENT`。`count` は 1 以上の整数
  - 事後条件: #4016 の `acquire` の書き込みの事後条件と同じ（送る時刻は待ちの終わり以後かつ最短の送信時刻以後で、送信のロックを持つほかの書き込みと重ならない）。予約は `reserve(kind, …, count=count)` で取る。文脈に入る直前（送信を許した時刻）を `started` として持ち、文脈を出ると `sent(reservation, started)` を呼んで送信のロックを放す（例外でも放す）
  - 件数の意味（決定 7）: 文脈の中で送られる書き込みを `count` 件と数える。文脈の中の要求どうしの間は空けない（本物の `gh` が続けて送る）。次の書き込みは、予約から `count` 件分の間隔の後（`reserve`）で、かつ実際に送り始めた時刻から `count` 件分の間隔の後（`sent`）に送る。予約を取った後に先の書き込みが長引いて実際の送信が後ろへずれても、件数の分の間隔は送る前に消化されない。後続の予約は、#4016 の送る直前の判定で送信のロックの下で最短の送信時刻を読み直すので、予約の時刻が過ぎていてもこの値まで待つ。`CONTENT` は `count` 件をウィンドウの件数に数える（`reserve`・送る直前の判定・`sent` のどれも同じ件数）
  - 単体テストの観点: #4016 の `acquire` のテスト（I1〜I6・決定 10・11）は `hold` に対して同じ形で書き、`acquire` は読み取りを通すことと `hold` へ渡す種類と件数 1 だけを縛る。`hold(CONTENT, 3)` の後、一覧に送り終えた時刻が 3 件あり、`CONTENT` の予約が `github.write-spacing` の 3 倍の後、`WRITE` の予約が 4 倍の後になる。1 時間のウィンドウに 497 件がある記録で `hold(CONTENT, 4)` の予約は後ろから 497 番目の時刻 + 3,600 秒で `next` を進めず、`hold(CONTENT, 3)` は待たない。偽の時計で `hold(CONTENT, 20)` を予約し、続けて単件の `CONTENT` を予約し、両方の予約の時刻が過ぎてから 20 件の文脈が 1 秒で終わる → 単件の送信は 20 件の文脈に入った時刻 + 20 × `github.write-spacing` 以後（送り終えた時刻 + 1 秒ではない）
- `WriteGate.reserve`
  - 事後条件: 共通の順番 `T = max(今, next, blocked_until)`。`CONTENT` の予約の時刻は、時刻の一覧に `count` 件を足しても 1 分・1 時間のどちらのウィンドウの上限も越えない最も早い時刻と `T` の大きいほう（長さ L・上限 N のウィンドウで、一覧に `N - count` 件より多くあれば後ろから `N - count + 1` 番目の時刻 + L。`count` が N 以上なら一覧の最後の時刻 + L）で、一覧へ予約の時刻を `count` 件足す。予約の時刻が `T` なら記録の `next` は `予約 + count × github.write-spacing の秒`、`T` より後（ウィンドウの空きを待つ）なら `next` を変えず、`hold` は予約の時刻まで送信のロックの外で眠ってから予約を置き換えて順番を取り直す。置き換え（`replacing`）と `withdraw` は、一覧から自分の `count` 件を除き、`next` を進めた予約だけ `before` へ戻す
- `WriteGate.sent`
  - 事後条件: `CONTENT` なら一覧の予約の時刻の `count` 件を除き、送り終えた時刻（今）を `count` 件足す。`started` が None なら（`acquire` の経路）`sent_next`・`next` は #4016 のまま。`started` があれば、`sent_next` は `max(sent_next, 今 + github.write-spacing の秒, started + count × github.write-spacing の秒)`、`next` も同じ値以上へ延ばす（#4016 の `sent` が `next` を延ばすのと同じ）
  - 保てる範囲（決定 7）: どの 1 分・1 時間のウィンドウでも、件数の見積もりの合計が上限を越える時刻には送らない。ただし `count` が上限そのものを越える文脈は、ウィンドウが空になってから 1 回でまとめて送るので上限を越える。文脈の中の要求どうしの間隔は保たない。保てるのは、文脈の前後のほかの書き込みとの間隔である
- `Busy`
  - 事後条件: `until` は、待ちが予算に収まらないと分かった時点に次に送れる時刻が分かれば、その時刻を持つ。分かるのは、記録のロックの中で予約までの秒が `remaining()` 以上と決めたとき（`until` = 予約の時刻）と、(4) で最短の送信時刻までの秒が `remaining()` 以上と決めたとき（`until` = 最短の送信時刻と `blocked_until` の大きいほう）。上限を `remaining()` にした記録のロック・送信のロックの時間切れ、ロックを待つ前の予算の切れ、送る直前の予算の切れでは None（ほかのプロセスがいつロックを放すかは分からない）。上限が `LOCK_SECONDS`・`github.write-send-lock` だった時間切れは `Busy` にせず #4016 の I5 で送る（#4016 の「予算による打ち切り」のまま）。`seconds` は #4016 のまま
  - #4016 の `_translating` は今のとおり `seconds` を `retry_after` へ写し、`until` を使わない
- `mutation_kind`
  - 事後条件: 詳細設計の gh_gate の「処理の手順」の (d) のとおり（graphql-core の `parse` で構文として読み、`operation_name` か操作が 1 つだけのときに実行される操作を決める。決まらない・構文の誤りは `CONTENT`。`query` は None、`subscription` は `CONTENT`、`mutation` は最上位の選択のフィールドの名前（別名でない）のどれかが `create`・`add` で始まれば `CONTENT`、ほかは `WRITE`）
  - 副作用: 無い（純粋な関数）。graphql-core の import は関数の中で行う
- `kind_of`
  - 事後条件: `/graphql` の POST は、本文の JSON の `query` と `operationName`（無ければ None）で `mutation_kind` を呼んだ結果。本文が JSON でない・`query` が文字列でないときは #4016 のまま `WRITE`。ほかの方法の扱いは #4016 のまま
  - 単体テストの観点: #4016 の `kind_of` のテストに、`fragment` が先にある mutation → `CONTENT`、`operationName` が query の操作を選ぶ → None を足す

## write_rules の照らし（limit_words・limit_body）と limit_of

満たす決定: 決定 4・5、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `limit_words` | `adapters/write_rules.py` | 新設 | `limit_words(text: str) -> bool` | 1 つの文（誤りの `message`・`gh` の誤りの行）に二次レート制限の語があるかを決める |
| `limit_body` | 同上 | 新設 | `limit_body(status: int, body: str) -> bool` | REST の応答の本文（403・429・422 だけ）が二次レート制限を示すかを、状態ごとに決めた欄だけで決める |
| `limit_of` | `adapters/github.py` | 変更（#4016 の仕様を置き換える） | #4016 のまま（`message` には REST では応答の本文の全文、GraphQL の HTTP 200 の誤りでは `status=200` と誤りの `message` を渡す） | 状態で入力の種類を分け、REST の本文は `limit_body`、GraphQL の誤りの文は `limit_words` で照らし、422 も対象にする |
| `limit_wait` | `adapters/github.py` | 新設（`limit_of` から切り出す） | `limit_wait(headers: Mapping[str, str], now: float) -> Limit` | 応答のヘッダーだけから待ちの秒と `hinted` を決める（#4016 の決定 8 の規則。`limit_of` と `gh_gate` の `limit_seen` の (1c) が使う） |

- `limit_words`
  - 事後条件: 小文字にした `text` に `secondary rate limit` か `submitted too quickly` があるか、`IssueComment.data abuse` のような `<型>.<欄> abuse`（`gh` が 422 の誤りを並べる形。STD4）があれば真
  - 副作用: 無い（純粋な関数）
- `limit_body`
  - 事後条件: `body` を JSON として読む
    - 422: 最上位の `errors` の配列の要素のうち、`message` に `limit_words` が当たるか、`code` がちょうど `abuse` のものが 1 つでもあれば真。ほかの欄（`documentation_url`・`errors[].field`・`errors[].resource`・最上位の `message`）は照らさない。JSON として読めない・`errors` が配列でなければ偽
    - 403・429: JSON の最上位の `message` が文字列なら `limit_words(message)`。JSON として読めなければ `limit_words(body)`（GitHub が HTML や文を返したとき）
    - ほかの状態（200・404・500 など）: 偽。REST の二次レート制限は 403・429・422 だけで返る（STD1・STD4）。GraphQL の HTTP 200 の誤りは本文でなく誤りの文なので、この関数に渡さず、呼び手（`limit_of`）が `limit_words` で照らす
  - 副作用: 無い（純粋な関数）
- `limit_of`
  - 事後条件: #4016 の条件の本文の語の照らしを、`status` が 200（GraphQL の HTTP 200 の誤り。`message` は誤りの文）なら `limit_words(message)`、ほかは `limit_body(status, message)`（`message` は REST の本文の全文）に置き換え、422 で `limit_body` が真のものも二次レート制限とする。GraphQL の誤りが 403・429 の HTTP の拒否で返ったときは REST と同じく本文を渡す。githubkit の `SecondaryRateLimitExceeded` は #4016 のまま。待ちの秒と `hinted` は #4016 の決定 8 のまま（`retry-after` があればその秒で `hinted` 真 → 無くて `x-ratelimit-remaining` が `0` で `x-ratelimit-reset` があれば reset までの秒で `hinted` 真 → どちらでもなければ `hinted` 偽。`x-ratelimit-reset` だけでは真にしない。GitHub は一次の上限の情報として reset をほぼすべての応答に付けるので、一次枠に残量がある二次制限で共有の書き込みの停止を一次枠のリセットまで延ばさない）
  - 二次レート制限と決めたときの待ちの秒と `hinted` は `limit_wait(headers, now)` の結果をそのまま使う
- `limit_wait`
  - 事後条件: `limit_of` の待ちの秒と `hinted` の決め方（上の括弧の中。#4016 の決定 8）をそのまま持つ。二次レート制限かどうかは決めない（呼び手が決めた後に呼ぶ）
  - 副作用: 無い（純粋な関数）
  - 単体テストの観点: `retry-after: 120` → 120 秒・`hinted` 真。`x-ratelimit-remaining: 0` と reset → reset までの秒・`hinted` 真。reset だけで remaining が正 → `hinted` 偽。ヘッダーが空 → `hinted` 偽
- `limit_of`（続き）
  - 呼び手: #4016 の `RetryWithin.__call__`・`_translated` は応答の本文の全文（`exc.response.text`）を、`GitHub.graphql` は誤りの `message` を渡す。`gh_gate` の `limit_seen` は `gh api -i` の出力の空行の後の本文（状態が 200 なら本文の `errors[].message` をつないだ文）を渡す。本文が空のときと、標準出力が `HEAD_BYTES + TAIL_BYTES` を越えて本文が欠けたとき、`limit_seen` は `limit_of` を呼ばず、標準エラーの誤りの行で決めてから `limit_wait` を呼ぶ
  - 単体テストの観点（`limit_body`・`limit_words` は `test_write_rules.py`）: 404・500 の非 JSON の本文に `secondary rate limit` → `limit_body` は偽。`limit_of(200, {}, "was submitted too quickly", now)` → 二次レート制限。`x-ratelimit-reset` があり `x-ratelimit-remaining` が正（例 `4999`）の 403 で本文 `secondary rate limit` → 二次レート制限で `hinted` 偽（待ちは既定の秒。reset までの秒にしない）。`x-ratelimit-remaining: 0` と reset のある同じ 403 → reset までの秒で `hinted` 真
  - 単体テストの観点: PR #4096 の記録の 2 つの 422 の応答の全文（[記録のコメント](https://github.com/devbasex/ai-plugins/pull/4096#issuecomment-7077633494) の「注意」に載る本文。要件書の表の JSON は `errors[]` の要素の抜粋）が二次レート制限になり、`WriteGate.block` が呼ばれる。テストの入力はこの全文で確定する:
    - `{"message":"Validation Failed","errors":[{"resource":"PullRequestReview","code":"abuse","field":"base"}],"documentation_url":"https://docs.github.com/rest/pulls/comments#create-a-reply-for-a-review-comment","status":"422"}`
    - `{"message":"Validation Failed","errors":[{"resource":"PullRequestReviewComment","code":"custom","field":"pull_request_review_thread.base","message":"was submitted too quickly"}],"documentation_url":"https://docs.github.com/rest/pulls/comments#create-a-review-comment-for-a-pull-request","status":"422"}`
    - 最上位の `message` は `Validation Failed` で、`code` は最上位に無い。二次レート制限の印は `errors[]` の要素の中にだけある（最上位の `code`・`message` の判定は定義しない）。`documentation_url` か `errors[].field` だけに `abuse` がある 422 は二次レート制限にならない。`must be part of the diff` の 422 は二次レート制限にならない（#4016 の決定 5 の行の位置の判定が続けて効く）
