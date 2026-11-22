# #4172 の詳細設計: gh の包みのファイル記述子・フラグの値・mutation の数・終了コード

設計文書 [issue-4172-design.md](./issue-4172-design.md) の決定を、#4059 の詳細設計（[issue-4059-design-detail.md](./issue-4059-design-detail.md)）の関数の粒度へ下ろした文書である。#4059 の詳細設計の関数のうち、契約が変わるものだけを書く。ここに無い関数と節は #4059 の詳細設計のままである。#4059 の実装の PR で、#4059 の詳細設計の該当する行をこの文書のとおりに書き換える（設計文書の「#4059 の設計を置き換える箇所」）。

## 地図

触るファイルは #4059 の詳細設計の地図と同じで、新しいファイルは無い。変わるのは次の関数である。

| パス | クラスまたはモジュール | 変わる関数と型 | 決定 |
| --- | --- | --- | --- |
| `src/ndf/adapters/gh_gate.py` | モジュール | `run_child`（副作用）・`plan_of`（フラグの値）・`run_held`（`limit_seen` へ終了コードを渡す）・`limit_seen`（シグネチャ） | 決定 1・2・4 |
| `src/ndf/adapters/write_rules.py` | モジュール | `Writes`（新設の型）・`mutation_write`（`mutation_kind` を置き換える） | 決定 3 |
| `src/ndf/adapters/write_gate.py` | `WriteGate` | `write_of`（#4016 の `kind_of` を置き換える）・`WriteGate.acquire`・`within` の throttler の `acquire` | 決定 3 |

依存の向きは #4059 のまま（`write_gate.py` は `write_rules.py` を読み、`write_rules.py` はほかの NDF のモジュールを読まない）。`Writes` は `write_rules.py` に置き、`gh_gate.py` と `write_gate.py` が読む。

## gh_gate

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `run_child` | `adapters/gh_gate.py` | 変更（#4059 の仕様を置き換える） | #4059 のまま | 本物の `gh` を、包みが受け継いだ記述子を継いで子として走らせる |
| `plan_of` | 同上 | 変更（同上） | #4059 のまま | フラグを「フラグの値」の規則で読み、`Plan` を決める。GraphQL の書き込みの件数を `mutation_write` から取る |
| `run_held` | 同上 | 変更（同上） | #4059 のまま | `limit_seen` に `run_child` の終了コードを渡す |
| `limit_seen` | 同上 | 変更（同上） | `limit_seen(plan: Plan, code: int, out: Ends, err: Ends, now: float) -> Optional[float]` | 本物の `gh` の終了コードと出力から二次レート制限を見分け、待つ秒を返す（違えば None） |

- `run_child`
  - 副作用（#4059 の「副作用」の起動の 1 文を置き換える）: `subprocess.Popen(..., restore_signals=True, close_fds=False)` で起動する。包みが受け継いだ記述子（Bash のプロセス置換の `/dev/fd/<n>`・`<n><` のリダイレクト）は本物へ継がれ、`--input`・`-F <名前>=@<パス>`・`--body-file`・`--notes-file` などのどのフラグに渡したパスも本物が開ける。包みが開く記述子（順番の記録とロックのファイル・出力のパイプ・先読みの写し）は Python の既定の継がれない記述子で作り、`os.set_inheritable` を呼ばない。ほかは #4059 のまま（子の環境に `NDF_GH_GATED=1`・標準入力を継ぐ・出力は「出力の写し方」のとおり）
  - 事後条件: #4059 のまま
- `plan_of`
  - 事後条件: #4059 の「処理の手順」のとおりで、フラグは下の「フラグの値」で読む。(c) の表の `graphql` の行は `mutation_write(document, operation_name)` の結果で、None なら読み取り、`Writes(kind, count)` なら `Plan.kind = kind`・`Plan.count = count`。(d) で文書を読まずに `CONTENT` とする場合（`--input` とフィールドの併用・読めないパス・ファイル記述子を指すパス・同じキーが複数）は件数 1
- `run_held`
  - 事後条件（#4059 の「`limit_seen(...)` が秒を返したら」を置き換える）: `run_child` が返した終了コードを `code` として `limit_seen(plan, code, out, err, now)` を呼ぶ。秒を返したら `hold` の出口より前に `gate.block(秒)` を呼ぶ。終了コードによらず `limit_seen` を呼び、0 の扱いは `limit_seen` が決める
- `limit_seen`
  - 事後条件（#4059 の「本物の終了コードが 0 なら None」の 1 文を置き換える）: `code == 0` なら、出力を照らさずに None。0 以外（シグナルの 128 + 番号を含む）なら #4059 の (1)〜(4) のとおり
  - 副作用: 無い

### フラグの値（`plan_of` の (b)・(c)・(e) が使う規則）

#4059 の (c) の「同じフラグが複数あれば、`--method` は最後の値が効く」の箇条を、次の規則に置き換える。語を前から読む規則（結合形・値として消費する語・`--`・短いフラグの並び）は #4059 のまま。

| フラグの型 | 対象 | 1 つの指定の値 | 複数の指定 | 指定が無いとき |
| --- | --- | --- | --- | --- |
| ブール値 | `gh api` の `-i`・`--include`・`--paginate`・`--slurp`・`--silent`・`--verbose`。`release create` の `-d`・`--draft`・`--generate-notes`。`release upload` の `--clobber` | `--x`・`-x`（並びの中の `-x` を含む）は真。`--x=<値>`・`-x=<値>` は `<値>` が `1`・`t`・`T`・`TRUE`・`true`・`True` なら真、`0`・`f`・`F`・`FALSE`・`false`・`False` なら偽（Go の `strconv.ParseBool`） | 最後の指定の値 | 偽 |
| 文字列 | `--method`・`-X`・`--jq`・`-q`・`--template`・`-t` | 次の語か `=` の後の値 | 最後の指定の値 | 空 |
| ヘルプ | `--help`・`-h` | `--help`・`-h`・`--help=<真>` だけをヘルプとして数える。`--help=<偽>` は数えない。`-h=<値>` は数えない（本物はヘルプを出すが、書き込みの側へ倒す） | ヘルプとして数える指定が 1 つでもあり、最後の `--help=<値>` が偽でない | ヘルプでない |

- ブール値の `<値>` が上の 12 の語のどれでもなければ、本物の `gh` は送らずに引数の誤りで終わるので、引数を読めないものとして `CONTENT`・件数 1 にし、引数はそのまま本物へ渡す（#4059 の「読めない語は書き込みの側へ倒す」）
- `--jq`・`--template` は値が空なら指定なしとする（`gh` の併用の誤りの判定と同じ）
- (b) のヘルプの読み取りは、ヘルプとして数える指定がちょうど 1 つで、ほかの語の条件が #4059 のとおりのときに限る
- (e) の件数の式は、`--draft`・`-d`・`--generate-notes`・`--clobber` の「あるとき」を「値が真のとき」、「無いとき」を「値が偽のとき」と読む

### `--silent` の外し方（#4059 の (c) の 118 行と「出力の写し方」の条件を置き換える）

| 条件（すべて満たす） | 本物へ渡す引数 | `plan.stdout_shown` |
| --- | --- | --- |
| 書き込み・親の標準出力が端末でない・`--silent` の値が真・`--verbose` の値が偽・`--jq` と `--template` の値が空・`--include` の値が偽 | `--silent` と `--silent=<値>` の語をすべて除く | `"none"` |
| 同上で `--include` の値が真・`--paginate` の値が偽 | 同上 | `"headers"` |
| ほか（`--silent` の値が偽を含む） | 元の引数のまま | `"all"` |

- 除くのは `--silent` のフラグとして読めた語だけで、ほかのフラグの値として消費した語（`-f body=--silent`・`-H '--silent'`）は除かない
- `"none"`・`"headers"` の写し分けと、`limit_seen` の (1b)・(1c) は #4059 のまま

### 異常系の処理

#4059 の表に次の 1 行を足す。ほかの行は #4059 のまま。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `plan_of` | ブール値のフラグの値が読めない | `--silent=yes` など | 「フラグの値」の 12 の語に無い | `CONTENT`・件数 1、引数はそのまま | 本物の `gh` の引数の誤り（`invalid argument "yes" for "--silent" flag`）と終了コード 1 | 値を直して打ち直す |

### 単体テストの観点

#4059 の表に足す行。ほかの行は #4059 のまま。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `run_child`（`test_bin_gh.py`。`bash -c` から包みを起動） | `gh api graphql --input <(…)`・`-F query=@<(…)` の書き込みの文書で、偽の本物の `gh` が中身を読む。`gh pr comment 1 --body-file <(…)` も同じ | `--input /proc/self/fd/<n>` を `<n><` のリダイレクトで渡しても読む。偽の本物の `gh` が持つ記述子に、`WriteGate` の記録とロックのファイル・出力のパイプが無い | — |
| `plan_of`（フラグの値） | `--silent=false` の書き込み → 引数のまま・`"all"`。`--silent --silent=false` → 同じ。`--silent=false --silent` → `--silent` の語を 2 つとも除き `"none"` | `--silent --jq .x --jq=` → 除いて `"none"`。`--silent --jq=` の前の `--jq .x` だけ → `"all"`。`--silent --verbose=false -i=true` → `"headers"`。`--silent -i --paginate=false` → `"headers"`。`-f body=--silent --silent` → 値の `--silent` は残る。`gh api -X POST repos/o/r/issues --help=false` → `CONTENT`。`… --help` → 読み取り。`… -h=false` → `CONTENT`。`gh release create v1 a.zip --draft=false` → 4、`--draft=true` → 3、`--draft --draft=false` → 4、`--generate-notes=false` → 足さない。`gh release upload v1 a.zip --clobber=false` → 1 | `--silent=yes` → `CONTENT`・件数 1・引数のまま |
| `limit_seen` | `code=1` の 403 の `gh: secondary rate limit (HTTP 403)` → `BLOCK_SECONDS` | `code=0` で同じ標準エラー → None。`code=0` で `-i` の 403 と `Retry-After: 120` の出力 → None | — |
| `run_held` | 偽の本物の `gh` が終了コード 0 で、標準エラーに `gh: secondary rate limit (HTTP 403)` を出す → `gate.block` が呼ばれない | 同じ出力で終了コード 1 → `gate.block` が呼ばれる | — |

## write_rules の GraphQL の件数（Writes・mutation_write）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Writes` | `adapters/write_rules.py` | 新設（型） | `NamedTuple(kind: str, count: int)` | 1 つの要求か 1 回の `gh` が送る書き込みの種類（`WRITE`・`CONTENT`）と件数（1 以上） |
| `mutation_write` | 同上 | 新設（#4059 の `mutation_kind` を置き換える） | `mutation_write(document: str, operation_name: Optional[str] = None) -> Optional[Writes]` | GraphQL の文書の実行される操作を、読み取り（None）か、書き込みの種類と件数に分ける |

- `mutation_write`
  - 事後条件: 実行される操作の決め方は #4059 の (d) のまま（graphql-core の `parse`、`operation_name` か操作が 1 つだけのとき）
    - 決まらない・構文の誤り・`subscription` → `Writes(CONTENT, 1)`
    - `query`（省略形を含む）→ None
    - `mutation` → 最上位の選択から下の「フィールドの集め方」で集めた応答のキーの数を `count` にする（0 なら 1）。集めたフィールドの名前（別名でない）のどれかが `create`・`add` で始まれば `kind` は `CONTENT`、ほかは `WRITE`
  - フィールドの集め方（GraphQL の仕様の CollectFields に倣う。スキーマを使わない）
    - フィールドは応答のキー（別名、無ければ名前）ごとに 1 件。同じ応答のキーの 2 つ目以降は数えない（`a: addComment` と `b: addComment` は 2 件、別名の無い `addComment` が 2 つなら 1 件）
    - インラインフラグメントは型の条件を見ずにたどる。フラグメントの展開は、同じ名前の定義をたどる。たどった名前は覚えておき、2 度目はたどらない（循環でも止まる）。定義の無い名前は飛ばす
    - 選択（フィールド・インラインフラグメント・フラグメントの展開）に `@skip(if: true)` か `@include(if: false)` のリテラルがあれば、その選択を飛ばす。`if` が変数なら飛ばさない（多い側に数える）
  - 副作用: 無い（純粋な関数）。graphql-core の import は関数の中で行う（#4059 のまま）
  - 計算量の注意: 文書の大きさに比例する。フラグメントの定義は 1 度ずつしかたどらない

### 単体テストの観点（`test_write_rules.py`。#4059 の `mutation_kind` の行を置き換える）

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| query → None、`addComment` だけ → `Writes(CONTENT, 1)`、`resolveReviewThread` だけ → `Writes(WRITE, 1)`、`addComment` と `resolveReviewThread` → `Writes(CONTENT, 2)` | `a: addComment b: addComment` → 2。別名の無い `addComment` が 2 つ → 1。`fragment F on Mutation { addComment … } mutation { ...F ... on Mutation { closeIssue … } }` → 2。`@skip(if: true)` のフィールド → 数えない、`@include(if: $v)` → 数える。`fragment F on Mutation { ...F addComment … } mutation { ...F }` → 1 で止まる。`operationName` で選んだ mutation だけを数える（ほかの操作のフィールドを数えない）。`createIssue: resolveReviewThread` → `WRITE`（#4059 のまま） | 構文の誤り → `Writes(CONTENT, 1)` |

## WriteGate の件数（write_of・acquire）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write_of` | `adapters/write_gate.py` | 変更（#4016 の `kind_of` を置き換える。#4059 の `kind_of` の行も置き換える） | `write_of(request: httpx.Request) -> Optional[Writes]` | 要求を読み取り（None）か、書き込みの種類と件数に分ける |
| `WriteGate.acquire` | 同上 | 変更（#4059 の行を置き換える） | #4016 のまま | `write_of(request)` が None ならすぐ通し、ほかは `with self.hold(w.kind, w.count):` で通す |
| `within` の throttler の `acquire` | 同上 | 変更（同上） | #4059 のまま | 同上（予算を持つ `hold` を件数で呼ぶ） |

- `write_of`
  - 事後条件: `/graphql` の POST は、本文の JSON の `query` と `operationName` で `mutation_write` を呼んだ結果。本文が JSON でない・`query` が文字列でないときは `Writes(WRITE, 1)`（#4016 の `WRITE` のまま）。ほかの方法は #4016 の `kind_of` の種類を件数 1 で返す（読み取りは None）
  - 単体テストの観点: #4016 の `kind_of` のテストを `write_of` へ移し、返り値を `Writes` で縛る。最上位の mutation のフィールドが 2 つの `/graphql` の POST → `Writes(CONTENT, 2)` で、`acquire` が `hold(CONTENT, 2)` を呼ぶ
- 呼び手の読み替え: #4016 と #4059 の文書で `kind_of(request)` の種類を使う箇所（`acquire`・`_translating` の例外の分類など）は `write_of(request)` の `kind` を使う。None の扱いは同じ
