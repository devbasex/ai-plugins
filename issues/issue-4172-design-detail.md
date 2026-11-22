# #4172 の詳細設計: gh の包みのファイル記述子・フラグの値・mutation の数・終了コード

設計文書 [issue-4172-design.md](./issue-4172-design.md) の決定を、#4059 の詳細設計（[issue-4059-design-detail.md](./issue-4059-design-detail.md)）の関数の粒度へ下ろした文書である。#4059 の詳細設計の関数のうち、契約が変わるものだけを書く。ここに無い関数と節は #4059 の詳細設計のままである。#4059 の実装の PR で、#4059 の詳細設計の該当する行をこの文書のとおりに書き換える（設計文書の「#4059 の設計を置き換える箇所」）。

## 地図

触るファイルは #4059 の詳細設計の地図と同じで、新しいファイルは無い。変わるのは次の関数である。

| パス | クラスまたはモジュール | 変わる関数と型 | 決定 |
| --- | --- | --- | --- |
| `src/ndf/adapters/gh_gate.py` | モジュール | `run_child`（副作用）・`plan_of`（フラグの値・シグネチャ）・`main`（`plan_of` へ渡す引数）・`gh_version`（新設）・`run_held`（`limit_seen` へ終了コードを渡す）・`limit_seen`（シグネチャ） | 決定 1・2・4 |
| `src/ndf/adapters/write_rules.py` | モジュール | `Writes`（新設の型）・`mutation_write`（`mutation_kind` を置き換える） | 決定 3 |
| `src/ndf/adapters/write_gate.py` | `WriteGate` | `write_of`（#4016 の `kind_of` を置き換える）・`WriteGate.acquire`・`within` の throttler の `acquire` | 決定 3 |

依存の向きは #4059 のまま（`write_gate.py` は `write_rules.py` を読み、`write_rules.py` はほかの NDF のモジュールを読まない）。`Writes` は `write_rules.py` に置き、`gh_gate.py` と `write_gate.py` が読む。

## gh_gate

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `run_child` | `adapters/gh_gate.py` | 変更（#4059 の仕様を置き換える） | #4059 のまま | 本物の `gh` を、包みが受け継いだ記述子を継いで子として走らせる |
| `main` | 同上 | 変更（同上） | #4059 のまま | `plan_of` に `forced_tty` と `version` を渡す（ほかは #4059 のまま） |
| `plan_of` | 同上 | 変更（同上） | `plan_of(argv: Sequence[str], stdout_tty: bool, snapshot: Callable[[str, Path], Optional[Path]] = _snapshot, count_labels: Callable[[str], int] = _count_labels, forced_tty: bool = False, version: Callable[[], Optional[tuple[int, int, int]]] = lambda: None) -> Plan` | フラグを「フラグの値」の規則で読み、`Plan` を決める。GraphQL の書き込みの件数を `mutation_write` から取る。`--silent` を外すかを、`forced_tty` と本物の `gh` の版で決める |
| `gh_version` | 同上 | 新設 | `gh_version(real: Path, environ: Mapping[str, str]) -> Optional[tuple[int, int, int]]` | 本物の `gh` の版を、実体のファイルごとに 1 度だけ `gh --version` で読んで覚え、返す（読めなければ None。読めなかった結果も覚える） |
| `run_held` | 同上 | 変更（同上） | #4059 のまま | `limit_seen` に `run_child` の終了コードを渡す |
| `limit_seen` | 同上 | 変更（同上） | `limit_seen(plan: Plan, code: int, out: Ends, err: Ends, now: float) -> Optional[float]` | 本物の `gh` の終了コードと出力から二次レート制限を見分け、待つ秒を返す（違えば None） |

- `run_child`
  - 副作用（#4059 の「副作用」の起動の 1 文を置き換える）: `subprocess.Popen(..., restore_signals=True, close_fds=False)` で起動する。包みが受け継いだ記述子（Bash のプロセス置換の `/dev/fd/<n>`・`<n><` のリダイレクト）は本物へ継がれ、`--input`・`-F <名前>=@<パス>`・`--body-file`・`--notes-file` などのどのフラグに渡したパスも本物が開ける。包みが開く記述子（順番の記録とロックのファイル・出力のパイプ・先読みの写し）は Python の既定の継がれない記述子で作り、`os.set_inheritable` を呼ばない。ほかは #4059 のまま（子の環境に `NDF_GH_GATED=1`・標準入力を継ぐ・出力は「出力の写し方」のとおり）
  - 事後条件: #4059 のまま
- `main`
  - 事後条件（#4059 の `plan_of` の呼び方に足す）: `forced_tty=environ.get("GH_FORCE_TTY", "") != ""`、`version=lambda: gh_version(real, environ)` を渡す。`gh` は `GH_FORCE_TTY` が空でなければ値によらず標準出力を端末として扱う（go-gh の `term.FromEnv`）
- `gh_version`
  - 実体の識別: `os.stat(real)`（リンクをたどる）の `st_dev`・`st_ino`・`st_size`・`st_mtime_ns` と `os.path.realpath(real)` の 5 つの値。覚えの記録は `gate_path().parent / "gh-version.json"`（#4016 の `gate_path` と同じディレクトリ）で、5 つの値と結果を 1 組だけ持つ。結果は次の 3 つのどれか
    - `{"version": [<数>, <数>, <数>]}`: 読めた版
    - `{"version": null, "unparsed": true}`: 終了コード 0 で、標準出力の 1 行目が `^gh version (\d+)\.(\d+)\.(\d+)` に合わない（`gh version DEV` など）。同じ実体では変わらないので、時刻を持たない
    - `{"version": null, "failed_at": <time.time() の秒>}`: 終了コードが 0 以外・10 秒の時間切れ・起動できない（`OSError`）。一時的な失敗とみなす
  - 事後条件（記録で決まるとき。`gh` を起動しない）: 記録の 5 つの値が今の実体と同じで、結果が読めた版ならその版、`unparsed` なら None、`failed_at` から `VERSION_RETRY_SECONDS`（3600 秒。`gh_gate.py` のモジュールの定数）が経っていなければ None を返す
  - 事後条件（読みに行くとき）: 記録が無い・読めない・5 つの値が違う・`failed_at` から `VERSION_RETRY_SECONDS` 以上経ったときは、同じディレクトリの `gh-version.lock` を `fcntl.flock(LOCK_EX | LOCK_NB)` で取りに行き、取れなければ 0.1 秒おきに取り直す。取れたら記録を読み直し、ほかの包みが書いた記録で上の「記録で決まるとき」に当たればそれを返す（`gh` を起動しない）。当たらなければ `subprocess.run([real, "--version"], stdin=DEVNULL, capture_output=True, timeout=10, env={**environ, "NDF_GH_GATED": "1"})` を 1 回走らせ、結果を上の 3 つのどれかで記録へ書いてからロックを放し、版か None を返す
  - 並行の契約: 記録が無いまま複数の包みが同時に呼んでも、`gh --version` を走らせるのはロックを先に取った 1 つだけで、ほかはその記録を読む。ロックを待つのは最大 10 秒（`gh --version` の時間切れと同じ値。持ち主は 10 秒で `subprocess.run` を終える）で、取れなければ `gh` を起動せず、記録も書かずに None を返す（その書き込みは `--silent` を外さない）。ロックのファイルを開けない（ディレクトリが書けない）ときはロックなしで 1 回走らせる
  - 待つ位置: `gh_version` は `main` が `run_held` を呼ぶ前の `plan_of` の中で呼ばれるので、版の読みとロックの待ちは、順番の保持（#4016 の `hold`）の前で起き、順番を持ったまま待たない。順番の予算（`github.gh-wait`）にも数えない
  - 副作用: 記録を書くときは同じディレクトリの一時ファイルへ書いて `os.replace` で置き換える（ロックを取らずに記録を読む包みが壊れた記録を読まない）。書けなくても例外を出さずに結果を返す
  - 起動の回数: 呼ばれるのは `plan_of` が `--silent` を外す前提を満たしたときだけで（読み取りと、外さない書き込みでは呼ばない）、`gh --version` の起動は、本物の `gh` の実体が変わったときに 1 回（並行でも 1 回）と、一時的な失敗の後は `VERSION_RETRY_SECONDS` ごとに最大 1 回だけ増える。開発版（`unparsed`）では実体が変わるまで再び起動しない。1 回の起動の待ちは最大 10 秒
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
| ブール値 | `gh api` の `-i`・`--include`・`--paginate`・`--slurp`・`--silent`・`--verbose`・`--allow-escape-sequences`。`release create` の `-d`・`--draft`・`--generate-notes`。`release upload` の `--clobber` | `--x`・`-x`（並びの中の `-x` を含む）は真。`--x=<値>`・`-x=<値>` は `<値>` が `1`・`t`・`T`・`TRUE`・`true`・`True` なら真、`0`・`f`・`F`・`FALSE`・`false`・`False` なら偽（Go の `strconv.ParseBool`） | 最後の指定の値 | 偽 |
| 文字列 | `--method`・`-X`・`--jq`・`-q`・`--template`・`-t` | 次の語か `=` の後の値 | 最後の指定の値 | 空 |
| ヘルプ | `--help`・`-h` | `--help`・`-h`（並びの中の `-h` を含む）は真。`--help=<値>` はブール値と同じ規則で読む。`-h=<値>` は値によらず偽（本物はヘルプを出すが、書き込みの側へ倒す） | 最後の指定の値（`--help`・`-h`・`--help=<値>`・`-h=<値>` を区別せず、最後に現れた 1 つ） | 偽 |

- ブール値の `<値>` が上の 12 の語のどれでもなければ、本物の `gh` は送らずに引数の誤りで終わるので、引数を読めないものとして `CONTENT`・件数 1 にし、引数はそのまま本物へ渡す（#4059 の「読めない語は書き込みの側へ倒す」）
- `--jq`・`--template` は値が空なら指定なしとする（`gh` の併用の誤りの判定と同じ）
- (b) のヘルプの読み取り（#4059 の (b) を置き換える）
  - `gh api`: (c) でフラグとして読めた語（ほかのフラグの値として消費した語と `--` の後の語を除く）で、ヘルプの最後の指定の値が真なら読み取り、偽なら (c) の表のとおり。#4059 の (b) の「残る語がグループ・サブコマンド・ヘルプのフラグ 1 つだけ」の語数の制限は `gh api` には掛けない。本物の `gh` 2.103.3 は、引数を読めればエンドポイント・`-X POST`・フィールドがあってもヘルプを出して要求を送らない（`gh api --hostname nonexistent.invalid -X POST repos/o/r/issues --help`・`-f a=b --help`・`-ih`・`--help=false --help` はヘルプを出して 0、`--help --help=false` は要求へ進む）。表に無いフラグ・読めないブール値があれば (c) のとおり `CONTENT`・件数 1
  - `gh api` のほか: #4059 の (b) の語数の制限のまま。ヘルプのフラグとして数えるのは `--help`・`-h`・`--help=<真>` の 1 語だけで、`--help=<偽>`・`-h=<値>` はほかの語として残る（その語があればヘルプでない）
  - 書き込みの側へ倒す組み合わせ: `-h=<値>` と、`-h --help=false` のように最後が `--help=<偽>` の指定は、本物ではヘルプになるが読み取りにしない（ヘルプを書き込みと誤っても順番を待つだけで済み、逆は書き込みが順番の外へ出る）
- (e) の件数の式は、`--draft`・`-d`・`--generate-notes`・`--clobber` の「あるとき」を「値が真のとき」、「無いとき」を「値が偽のとき」と読む

### `--silent` の外し方（#4059 の (c) の 118 行と「出力の写し方」の条件を置き換える）

外す前提（次の 7 つをすべて満たす）: 書き込みと決まった・親の標準出力が端末でない（`stdout_tty` が偽）・`forced_tty` が偽（`GH_FORCE_TTY` が空か無い）・`--silent` の値が真・`--verbose` の値が偽・`--jq` と `--template` の値が空・`version()` が None でない。前提を満たさなければ、`--include`・`--paginate` の値によらず元の引数のまま渡し、`plan.stdout_shown` は `"all"`（`--silent` の値が偽のときを含む）。`version()` は前の 6 つを満たしたときだけ呼ぶ。

前提を満たすときは、`--include` と `--paginate` の値で次のどれか 1 行に決まる。

| `--include`（`-i`）の値 | `--paginate` の値 | 本物へ渡す引数 | `plan.stdout_shown` |
| --- | --- | --- | --- |
| 偽 | 問わない | 「外した引数」 | `"none"` |
| 真 | 偽 | 「外した引数」 | `"headers"` |
| 真 | 真 | 元の引数のまま | `"all"` |

- 「外した引数」: 元の引数から `--silent` と `--silent=<値>` の語をすべて除く。`version()` が `(2, 97, 0)` 以上なら、さらに `--allow-escape-sequences` を 1 語、フラグの列の末尾へ足す。末尾は、(c) で `--` の語として読めた語（ほかのフラグの値として消費した `--` を除く）があればその直前、無ければ引数の最後である。`(2, 97, 0)` より前の版には足さない
- 末尾へ足す理由: `gh` は同じブール値のフラグの最後の指定の値を使う（「フラグの値」）。`api` の直後へ足すと、利用者が後ろに書いた `--allow-escape-sequences=false` が勝ち、`--silent` のときは捨てて 0 で終わった書き込みが、送った後の本文の検査で 1 になる。末尾なら足した真が最後の指定になる。`--` の後の語は位置引数なので、`--` より後ろへは足さない。`gh api` はエンドポイントの後ろのフラグも読む（cobra の既定）
- 除くのは `--silent` のフラグとして読めた語だけで、ほかのフラグの値として消費した語（`-f body=--silent`・`-H '--silent'`）は除かない
- 版で分ける理由: `--allow-escape-sequences` と、JSON でない本文の端末のエスケープの検査（`iostreams.CopyGuardedContent`）は、どちらも `gh` 2.97.0 で入った（2.96.0 と 2.97.0 の `pkg/cmd/api/api.go` の差分。https://github.com/cli/cli/security/advisories/GHSA-3m3g-3wcr-px46 ）。2.96.0 にフラグを渡すと `unknown flag: --allow-escape-sequences` の引数の誤りで、要求を送らずに終了コード 1 になる（実測。`--hostname nonexistent.invalid` でも接続の誤りでなく引数の誤りが出る）。2.97.0 より前の版には検査が無いので、`--silent` を除くだけで終了コードは `--silent` のときと同じになる
- 版が読めない（`version()` が None）ときは外さない。本文の `code: abuse` は見分けられないが（#4059 の `"all"` のまま、(3) の標準エラーの行で照らす）、書き込みは直に打ったときと同じに通る
- `forced_tty` で外さない理由: `GH_FORCE_TTY` が空でなければ、親の標準出力がパイプでも `gh` は端末として扱い、`--silent` が無いときだけページャー（`GH_PAGER`・設定の `pager`・`PAGER`）を起こす（`IOStreams.StartPager` は `!IsStdoutTTY()` で起こさない。https://github.com/cli/cli/blob/v2.103.3/pkg/iostreams/iostreams.go ）。外すと、元は起きなかったページャーが未検査の本文を受け取る（実測: `GH_FORCE_TTY=1` と `GH_PAGER` を設定し標準出力をファイルへ向けた `gh api rate_limit` はページャーを起こしてファイルが 0 バイト、`--silent` 付きは起こさない）。ページャーの設定は `gh` の設定ファイルも見るので、包みは設定を読まずに `GH_FORCE_TTY` だけで決める
- `--allow-escape-sequences` を足す理由: `gh` 2.103.3 は `--silent` のとき本文の書き込み先を `io.Discard` にし、JSON でない本文を `io.Copy` でそのまま捨てる。`--silent` を外すと書き込み先が標準出力になり、同じ本文が `iostreams.CopyGuardedContent` を通って、端末のエスケープを含む本文で `the response contains terminal escape sequences` の誤りと終了コード 1 になる（書き込みは送られて成功している）。`--allow-escape-sequences` を付けると、この分岐は `io.Copy` に戻り、`--silent` のときと同じ終了コードになる（https://github.com/cli/cli/blob/v2.103.3/pkg/cmd/api/api.go#L534-L548 ）。包みは本文を親へ写さない（`"none"`）か空行の後を写さない（`"headers"`）ので、エスケープは利用者の端末へ届かない
- 本文の出力の処理がほかに変わらない条件: `--silent` が変えるのは本文の書き込み先（`io.Discard` かどうか）と、ページャーを起こすか（`!opts.Silent`）の 2 つだけである（api.go の 333〜351 行）。ページャーは親の標準出力が端末でなく `GH_FORCE_TTY` が空という前提で起きない。`--jq`・`--template` は前提で空、JSON の本文の色付け（`jsoncolor.Write`）と `--paginate` の配列の継ぎ（`paginatedArrayReader`）は書き込み先によらず同じ読み方で、誤りも同じに出る。`--slurp` は書き込み先を `jsonArrayWriter` で包むので、`--silent` があっても `io.Discard` でなく、外しても分岐は変わらない
- `"none"`・`"headers"` の写し分けと、`limit_seen` の (1b)・(1c) は #4059 のまま
- #4059 の (c) の値を取らないフラグの一覧に `--allow-escape-sequences` を足し、上の「フラグの値」の表のブール値のフラグとして読む（今は表に無いフラグで `CONTENT`・件数 1・`"all"` になる。利用者が付けたときも、`=false` を含めて上の規則のまま末尾へ足す。最後の指定が真になるだけで、本文は親へ写さない。2.97.0 より前の版で利用者が付けたときは、足さずに本物の `gh` が直に打ったときと同じ引数の誤りで断る）

### 異常系の処理

#4059 の表に次の 3 行を足す。ほかの行は #4059 のまま。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `plan_of` | ブール値のフラグの値が読めない | `--silent=yes` など | 「フラグの値」の 12 の語に無い | `CONTENT`・件数 1、引数はそのまま | 本物の `gh` の引数の誤り（`invalid argument "yes" for "--silent" flag`）と終了コード 1 | 値を直して打ち直す |
| `gh_version` | 本物の `gh` の版が読めない | `gh --version` が 0 以外・10 秒の時間切れ・1 行目が `gh version <数>.<数>.<数>` の形でない（開発版） | `subprocess.run` の結果と正規表現 | None を返し、結果を実体の 5 つの値と一緒に記録する（形が合わないのは `unparsed`、ほかは `failed_at`）。`plan_of` は `--silent` を外さない | 無し（書き込みは元の引数のまま走り、`gh` の出力だけ） | `unparsed` は同じ実体では読み直さない。`failed_at` は `VERSION_RETRY_SECONDS`（3600 秒）が経った後の、外す前提を満たした書き込みで 1 回だけ読み直す |
| `gh_version` | 版を読むロックを取れない | ほかの包みが `gh --version` を走らせている・固まったプロセスがロックを持つ | `gh-version.lock` の `flock` を 10 秒取り直しても取れない | 起動せず、記録を書かずに None を返す。`plan_of` は `--silent` を外さない | 無し（同上） | 次の外す前提を満たした書き込みで、記録を読むかロックを取り直す |

### 単体テストの観点

#4059 の表に足す行。ほかの行は #4059 のまま。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `run_child`（`test_bin_gh.py`。`bash -c` から包みを起動） | `gh api graphql --input <(…)`・`-F query=@<(…)` の書き込みの文書で、偽の本物の `gh` が中身を読む。`gh pr comment 1 --body-file <(…)` も同じ | `--input /proc/self/fd/<n>` を `<n><` のリダイレクトで渡しても読む。偽の本物の `gh` が持つ記述子に、`WriteGate` の記録とロックのファイル・出力のパイプが無い | — |
| `plan_of`（フラグの値） | `--silent=false` の書き込み → 引数のまま・`"all"`。`--silent --silent=false` → 同じ。`--silent=false --silent` → `--silent` の語を 2 つとも除き、引数の最後に `--allow-escape-sequences` を足して `"none"`（ここから先の行の `version` は `(2, 103, 3)`） | `--silent --jq .x --jq=` → 外して `"none"`。`--silent --jq=` の前の `--jq .x` だけ → `"all"`。`--silent --verbose=false -i=true` → 外して `"headers"`。`--silent -i --paginate=false` → 外して `"headers"`。`--silent -i --paginate` → 元の引数のまま `"all"`。`--silent --paginate`（`-i` なし）→ 外して `"none"`。`-f body=--silent --silent` → 値の `--silent` は残る。`… --silent -- x` の形では足した `--allow-escape-sequences` は `--` の直前。`gh api --allow-escape-sequences=false --silent -X POST <パス> -f a=b` → 引数の最後が足した `--allow-escape-sequences` で、利用者の `=false` の語は残る。`-H -- --silent` の値の `--` があっても引数の最後へ足す。版（`--silent` の書き込み・親の標準出力がパイプ）: `version` が `(2, 97, 0)` → 外して足す、`(2, 96, 0)` → 外すが足さずに `"none"`、None → 元の引数のまま `"all"`。`forced_tty=True` → `version` を呼ばずに元の引数のまま `"all"`。`--silent` の無い書き込み・読み取り → `version` を呼ばない。ヘルプ（どれも `gh api -X POST repos/o/r/issues` に続ける）: `--help` → 読み取り。`-ih` → 読み取り。`-f a=b --help` → 読み取り。`--help=false --help` → 読み取り。`--help=false` → `CONTENT`。`--help --help=false` → `CONTENT`。`-h --help=false` → `CONTENT`（本物はヘルプ。書き込みの側へ倒す）。`-h=false` → `CONTENT`。`-H --help` の値の `--help` と `-- --help` の `--` の後の `--help` → 数えず `CONTENT`。`gh pr comment --help --help=false` → `CONTENT`、`gh pr comment --help=true` → 読み取り（`gh api` のほかは語数の制限のまま）。件数（#4059 の (e) の式。アセット 1 つ）: `gh release create v1 a.zip --draft=false` → 3（作成 1 + アップロード 1 + 公開 1）、`--draft=true` → 2（作成とアップロード）、`--draft --draft=false` → 3、`--draft=false --draft` → 2、`--generate-notes=false` → 足さない。`gh release upload v1 a.zip --clobber=false` → 1 | `--silent=yes` → `CONTENT`・件数 1・引数のまま。`--help=yes` → `CONTENT`・件数 1・引数のまま |
| `run_child`・`run_held`（`--silent` を外した書き込みの終了コード。`test_bin_gh.py`。STD4 の偽の API サーバーへ本物の `gh` 2.103.3 を向ける） | POST に `Content-Type: text/plain` で端末のエスケープ（`\x1b[31m`）を含む本文を 201 で返す。`gh api --silent -X POST <パス> -f a=b` を、直に打ったときと包みを通したときで終了コードを比べ、どちらも 0。包みの利用者の標準出力は空 | 同じ本文で `-i --silent`（`"headers"`）→ どちらも 0、利用者の標準出力はヘッダーと空行まで。NUL を含む非 JSON の本文 → どちらも 0 | 偽の API サーバーが受けた POST の数が、直に打ったときと包みを通したときで 1 ずつ（包みが失敗と見て送り直さない）。`--allow-escape-sequences=false --silent` の書き込み → 直に打ったときと包みを通したときがどちらも 0 |
| `run_child`・`run_held`（フラグの無い版と端末の強制。`test_bin_gh.py`） | 偽の本物の `gh` が `gh version 2.96.0` を名乗り、`--allow-escape-sequences` を受けたら `unknown flag: --allow-escape-sequences` で要求を送らずに 1 で終わる。`gh api --silent -X POST <パス> -f a=b` を包みを通して打つ → 終了コード 0、偽の API サーバーが受けた POST が 1、偽の本物が受けた引数に `--silent` も `--allow-escape-sequences` も無い。偽の本物が `gh version DEV` を名乗る → 元の引数のまま走り 0 | `GH_FORCE_TTY=1` と `GH_PAGER`（起きたら印のファイルを書く）を設定し、包みの標準出力をファイルへ向けて `gh api --silent -X POST <パス> -f a=b` を本物の `gh` 2.103.3 で打つ → 印のファイルが無く、偽の本物が受けた引数に `--silent` が残る | — |
| `gh_version` | 偽の本物の `gh` の `--version` が `gh version 2.96.0 (2026-07-02)` → `(2, 96, 0)`。続けて呼ぶと偽の本物を起動しない（起動の回数を数える） | 偽の本物を書き換えて `st_mtime_ns` か大きさが変わる → 起動して読み直す。記録のディレクトリが書けない → 版を返す | 1 行目が `gh version DEV`（終了コード 0）→ None、続けて呼んでも起動しない。偽の本物を書き換えると起動する。終了コード 1・10 秒を越える・起動できない → None、続けて呼んでも起動しない。`failed_at` を `VERSION_RETRY_SECONDS` 前へずらした記録では 1 回起動する。記録が無い状態で 4 つのプロセスから同時に呼ぶ → 偽の本物の起動は 1 回で、4 つとも同じ版を返す。ほかのプロセスが `gh-version.lock` を持ち続ける → 約 10 秒で起動せずに None、記録を書かない |
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
