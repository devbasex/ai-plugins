# #4059 の詳細設計: エージェントが打つ gh も NDF と同じ順番の記録で間を空ける

設計文書 [issue-4059-design.md](./issue-4059-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。#4016 の部品（`WriteGate`・`limit_of`）は [issue-4016-design-detail.md](./issue-4016-design-detail.md) にあり、この文書は足す・置き換える関数だけを書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設。`write_gate.py` は #4016 が新設する）:

```text
plugins/ndf/
├── bin/gh *
├── contracts/limits.json
├── src/ndf/adapters/
│   ├── gh_gate.py *
│   ├── github.py
│   └── write_gate.py
└── tests/adapters/
    ├── test_gh_gate.py *
    ├── test_github.py
    └── test_write_gate.py
docs/glossary/glossary.json, docs/glossary.md
```

依存の向き: `bin/gh` → `adapters/gh_gate.py` → `adapters/write_gate.py`・`adapters/github.py`（`limit_words` だけ）・`domains/relay/session_env.py`（`should_drop` だけ）。`github.py` は `write_gate.py` を読み、`gh_gate.py` を読まない。`write_gate.py` は `gh_gate.py` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `bin/gh` | 新設 | 名前 `gh` で引かれ、NDF の仮想環境の python で `gh_gate` を起動する | `gh_gate` | シェルのスクリプト | — |
| `src/ndf/adapters/gh_gate.py` | 新設 | `gh` の引数から書き込みの種類を決め、書き込みなら順番を取ってから本物の `gh` を走らせる | `write_gate`・`github`・`session_env` | モジュール | `main`・`kind_of_argv`・`real_gh`・`run_held`・`Tail`（新設） |
| `src/ndf/adapters/write_gate.py` | 変更（#4016 の新設に足す） | 種類を受けて順番を取る口を足す | #4016 のとおり | `WriteGate` | `WriteGate.hold`（新設）、`WriteGate.acquire`・`within` の throttler の `acquire`（#4016 の仕様を置き換える） |
| `src/ndf/adapters/github.py` | 変更（#4016 の変更に足す） | 二次レート制限の語の照らしを 1 か所にする | — | モジュール | `limit_words`（新設）、`limit_of`（#4016 の仕様を置き換える） |
| `contracts/limits.json` | 変更 | `gh` の書き込みが待つ上限 | — | — | `github.gh-wait`（追加） |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更 | 用語を足し、意味を変える | — | — | 「gh の包み」（追加）、「二次レート制限」（意味の変更） |

## bin/gh（入口）

満たす決定: 決定 1、受け入れ条件 4・6

- bash のスクリプトで、実体の位置（`bin/ndf` と同じくシンボリックリンクをたどる）からプラグインの根を求める
- `NDF_GH_GATED` が空でなければ、python を起動せずに本物の `gh` へ置き換わる（G3。入れ子の速い道）。本物の探し方は `gh_gate.real_gh` と同じ規則を bash で行う（`PATH` を前から見て、実体が自分と同じファイル、または NDF のほかの版の `bin/gh` を飛ばす）
- それ以外は NDF の仮想環境の python（hook の登録と同じ `${XDG_CACHE_HOME:-$HOME/.cache}/<プラグイン名>/venv/<版>/bin/python`。`bin/ndf` が作る）で `-m ndf.adapters.gh_gate "$@"` を `exec` する。python が無い・実行できなければ本物の `gh` へ置き換わる（NDF の不調で `gh` を止めない。hook の入口の IH4 と同じ扱い）。仮想環境を作るのは `bin/ndf` だけで、`bin/gh` は作らない（`gh` のたびに uv を起こさない）

## gh_gate（gh の包み）

満たす決定: 決定 1・3・4・6、受け入れ条件 1〜5、不変条件 G1〜G3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `main` | `adapters/gh_gate.py` | 新設 | `main(argv: list[str], environ: Mapping[str, str] = os.environ) -> int` | 本物の `gh` を探し、読み取りなら置き換わり、書き込みなら `run_held` の終了コードを返す |
| `kind_of_argv` | 同上 | 新設 | `kind_of_argv(argv: Sequence[str], read_file: Callable[[str], Optional[str]] = _read_text) -> Optional[str]` | `gh` の引数を読み取り（None）・`WRITE`・`CONTENT` に分ける |
| `real_gh` | 同上 | 新設 | `real_gh(path: str, me: Path) -> Optional[Path]` | `PATH` から本物の `gh` を探す。自分と NDF のほかの版の `bin/gh` を飛ばす |
| `run_held` | 同上 | 新設 | `run_held(gh: Path, argv: list[str], kind: str, gate: WriteGate, budget: float, environ: Mapping[str, str]) -> int` | `gate.within(...).hold(kind)` の中で本物の `gh` を子として走らせ、出力を写しながら末尾を持ち、二次レート制限なら `gate.block` を呼ぶ |
| `Tail` | 同上 | 新設（型） | `class Tail`（`feed(data: bytes) -> None`・`text() -> str`） | 写した出力の末尾 `TAIL_BYTES` を持つ |

- `main`
  - 事後条件: 本物が見つからなければ標準エラーへ `ndf: 本物の gh が PATH に無い` を出して 127。`kind_of_argv` が None なら `os.execve(gh, ["gh", *argv], environ)` で置き換わる（戻らない）。書き込みなら `run_held(..., budget=limits.get("github.gh-wait").interval)` の終了コード
  - 副作用: 書き込みのときだけ、`machine_gate()` の記録とロック（#4016）を使う
- `kind_of_argv`
  - 事前条件: `argv` は `gh` の後ろの引数（`gh` 自身を含まない）
  - 事後条件: 下の「処理の手順」の表のとおり。どの行にも当たらなければ `WRITE`
  - 副作用: `gh api graphql` の query が `-F query=@<ファイル>` か `--input <ファイル>` のときだけ、`read_file` でそのファイルを読む（`-` は標準入力なので読まず、`CONTENT` とする）
- `real_gh`
  - 事後条件: `path` を `os.pathsep` で分け、前から `<要素>/gh` が実行できるファイルで、実体（`os.path.realpath`）が `me` の実体と違い、要素が `session_env.should_drop(要素, 自分の根, None)` に当たらない最初のもの。無ければ None
- `run_held`
  - 事前条件: `kind` は `WRITE` か `CONTENT`。`budget` は秒（> 0）
  - 事後条件: `Busy` を受けたら本物を走らせずに、標準エラーへ待てない 1 行を出して 1 を返す。それ以外は本物の `gh` の終了コードをそのまま返す。本物の終了コードが 0 以外で `limit_words(tail.text())` が真なら、`hold` の出口より前に `gate.block(BLOCK_SECONDS)` を呼んでいる
  - 副作用: 子の環境に `NDF_GH_GATED=1` を足す。標準入力は子へそのまま渡す（継ぐ）。標準出力・標準エラーは、親の側が端末でなければパイプで受け、読んだ順に親の同じ口へ書いてから `Tail` へ足す（2 つのパイプを別のスレッドで読み、口ごとの順を保つ）。親の側が端末なら、その口はパイプにせずに継ぎ、見分けに使わない（`gh` の端末向けの表示を変えない）

### 処理の手順

`kind_of_argv` は、先頭から `--repo`・`-R` とその値、`--hostname` とその値を除いた最初の 2 語を「グループ」「サブコマンド」とする。`--help`・`-h`・`--version` がどこかにあれば読み取り。

| グループ | 条件 | 結果 |
| --- | --- | --- |
| `api` | `-X`・`--method` の値が `GET`・`HEAD`（大文字小文字を問わない） | 読み取り |
| `api` | 方法の指定が無く、`-f`・`-F`・`--field`・`--raw-field`・`--input` のどれも無い | 読み取り（`gh` は GET で送る） |
| `api` | エンドポイントが `graphql` で、query が `mutation` で始まらない（先頭の空白とコメントを除く。#4016 の `kind_of` と同じ読み方） | 読み取り |
| `api` | エンドポイントが `graphql` の mutation で、最初のフィールドの名前が `create`・`add` で始まる、または query を読めない | `CONTENT` |
| `api` | エンドポイントが `graphql` のほかの mutation | `WRITE` |
| `api` | 方法が `POST`、または指定が無く上のフィールドの引数がある（`gh` は POST で送る） | `CONTENT` |
| `api` | 方法が `PATCH`・`PUT`・`DELETE` | `WRITE` |
| `auth`・`search`・`help`・`version`・`completion`・`config`・`alias`・`browse`・`status` | — | 読み取り |
| ほか | サブコマンドが `view`・`list`・`status`・`diff`・`checks`・`watch`・`download`・`checkout`・`clone`・`logs` | 読み取り |
| ほか | サブコマンドが `create`・`comment`・`review` | `CONTENT` |
| ほか | 上のどれでもない（`edit`・`close`・`merge`・`ready`・`delete`・グループだけ・知らない語） | `WRITE` |

- 計算量の注意: 引数を 1 回なめるだけ。query のファイルは 1 つだけ読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `main` | 本物の `gh` が無い | `gh` が入っていない・`PATH` に包みしか無い | `real_gh` が None | 止める | 標準エラー `ndf: 本物の gh が PATH に無い`、127 | `gh` を入れて打ち直す |
| `run_held` | 待ちが上限に収まらない | 二次レート制限の待ちの終わり・ほかのレーンの書き込みが並ぶ | `within(...).hold` の `Busy`（#4016 の決定 10） | 本物を走らせずに返す | 標準エラー `ndf: GitHub への書き込みの順番を <秒> 秒待てない（待ちの終わり <時刻 JST>）。その後で打ち直す`、1 | 予約は `hold` が取り消す（#4016 の `withdraw`）。時刻の後に打ち直せる |
| `run_held` | 記録を読めない・ロックを取れない | 壊れた JSON・権限・固まったプロセス | #4016 の `reserve`・`hold` | #4016 の I5（ロックを持たずに送る） | #4016 の 1 行 | 次の `gh` でまた試す |
| `run_held` | 本物の `gh` を起動できない | 実行の権限が無い・消えた | `subprocess.Popen` の `OSError` | 呼び出し元へ返す | 標準エラー `ndf: gh を起動できない: <理由>`、126 | `hold` の出口で送信のロックを放す（送っていないが、出口の `sent` は延ばす側にしか倒れない） |
| `run_held` | 待ちの途中・走らせている途中で中断された | Ctrl-C・Bash の時間切れ | `KeyboardInterrupt`・シグナル | 子へ同じシグナルを送り、子の終わりを待ってから返す | 子の終了コード（シグナルなら 128 + 番号） | `hold` の出口でロックを放す |
| `run_held` | `block` を書けない | 記録の書き込みの失敗 | #4016 の `block` | 既定値で続ける | #4016 の 1 行 | 次の書き込みの待ちは延びない |
| `bin/gh` | 仮想環境の python が無い | `ndf` をまだ一度も打っていない・版を入れ替えた直後 | `[ -x <python> ]` | 本物の `gh` へ置き換わる | 無し（`gh` の出力だけ） | 次に `ndf` が仮想環境を作った後は包む |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `github.gh-wait` | 設定値 | `contracts/limits.json` | `interval` 90（`gh` の書き込みが順番を待つ上限の秒。Claude Code の Bash の既定 120 秒から `gh` が走る時間を残す） |
| `BLOCK_SECONDS` | 定数（設定値から読む） | `adapters/gh_gate.py` | `cross-review.secondary-rate-limit` の `routing.backoff` の 1 回目（60 秒。#4016 の決定 8 の `hinted` が偽のとき） |
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
| `main` | 本物の `gh` が無いときの 1 行 | 標準エラー | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `kind_of_argv` | #4016 の `write_gate.kind_of`（`httpx.Request` を読む） | #4016 の詳細設計 | 要求を作らない `gh` には使えない。graphql の query の読み方（mutation の最初のフィールド）は `kind_of` と同じ関数を共有する（`write_gate.mutation_kind(query)` に切り出す） |
| `real_gh` | `shutil.which`・`session_env.should_drop` | `grep -rn "def should_drop" plugins/ndf/src` | `shutil.which` は自分を飛ばせないので使わない。NDF のほかの版の `bin/` の判定は `should_drop` を使う |
| `run_held` の待ち | #4016 の `WriteGate.within` と `Busy` | #4016 の詳細設計 | 使う（予算による打ち切りと予約の取り消しを持つ） |
| `run_held` の見分け | #4016 の `limit_of` | #4016 の詳細設計 | 応答の形を持たない `gh` には使えないので、語の照らしを `limit_words` に切り出して共有する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `kind_of_argv` | 表の各行 | `-R owner/repo` が先頭にある、`--method get`（小文字）、`-F query=@file` の mutation、`--input -` | query のファイルが無い → `CONTENT` |
| `real_gh` | 包み・ほかの版の包み・本物の順で本物 | 同じ実体へのシンボリックリンクの包み | 本物が無い → None |
| `run_held` | 偽の本物の `gh` が 0 で終わり、出力がそのまま写り、終了コードが 0 | 待ちがちょうど `budget` → 走らせない（#4016 の決定 10 の比べ方） | `Busy`・起動できない・中断・403・422・GraphQL の各出力で `block` |
| `main` | 読み取りで `execve` が本物の `gh` と同じ引数で呼ばれる | `NDF_GH_GATED=1` の書き込みが順番を取らない | 本物が無い → 127 |

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
