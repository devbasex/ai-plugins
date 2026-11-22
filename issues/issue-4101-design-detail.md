# #4101 の詳細設計: 環境変数の検査 `spec-env`・契約の行・名前の定数の外し方

[設計文書](./issue-4101-design.md)の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方と、別ファイルにする条件は `design` の `references/detailed-design.md` にある。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/domains/env.json        … 列 group、37 行を足し 4 行を消す、area の語彙に testing
├── docs/contracts/env.md             … ndf check --write-spec で作り直す
├── src/ndf/
│   ├── checks/
│   │   ├── spec_env.py *             … 規則 spec-env（4 つの見方）
│   │   ├── spec_docs.py              … env_reads・_env_findings を spec_env へ移す。check が spec_env を呼ぶ
│   │   ├── rules.json                … spec.rules に spec-env、spec.env（receivers・not_env・shell_roots）
│   │   └── README.md                 … 規則の表に spec-env、spec-io から環境変数の文を外す
│   ├── adapters/{run_log,cli,github,training_cache,launch_slot}.py
│   ├── core/{worker,converge/location}.py
│   ├── domains/{crossrefactor/allocation,hooks/strict,hooks/guards/full_test,worktree/container,records/cli,release/ndf_settings}.py
│   ├── domains/relay/hub.py          … spawn_env が child: detach-drop の行を外す
│   ├── domains/relay/tmux.py         … new_session_args が起動行の頭に env -u（drop・detach-drop の名前）を置く
│   ├── identity.py・slot_pool.py
│   ├── progress/{invoke,plan/run}.py
│   └── testing/{slots,isolation}.py  … 名前の定数を外して字にする（組は facts.ids）
└── tests/
    ├── checks/test_spec_env.py *
    ├── checks/test_spec_docs.py      … 環境変数の部分のテストを test_spec_env.py へ移す
    ├── domains/relay/{test_session_env,test_relay_parts}.py
    └── cli/test_cli.py・testing/_real_state.py … isolation.removed_env() を使う
```

依存の向き: `checks/spec_env.py` は `checks/model.py`（`Finding`・`Scope`）と `checks/spec_model.py`（`SpecModel`）だけを読み、`spec_docs.py` が `spec_env.py` を呼ぶ。`spec_env.py` は `spec_docs.py` を読まないので輪は無い。製品のコードの側は `ndf.contracts.facts` を読むだけで、`checks` を読まない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/checks/spec_env.py` | 新設 | コードの環境変数の読み書きを契約と比べる | `checks.model`・`checks.spec_model` | モジュール | `findings`・`module_names`・`reads`・`literals`・`shell_names`・`unused`（新設）、`env_reads`（`spec_docs` から移す） |
| `src/ndf/checks/spec_docs.py` | 変更 | 規約の生成物と表の検査 | `checks.spec_env` | モジュール | `check`（変更）、`io_findings`（変更）、`env_reads`・`_env_findings`・`_env_name`・`_is_environ`（削除） |
| `src/ndf/checks/rules.json` | 変更 | 検査の規則のデータ | — | — | — |
| `contracts/domains/env.json` | 変更 | 環境変数の契約 | — | — | — |
| `adapters/github.py` | 変更 | GitHub のトークンを組から引く | `contracts.facts` | モジュール | `TOKEN_ENV` を削除、読む所が `facts.ids("env", group="github-token")` |
| `adapters/training_cache.py` | 変更 | Codex の認証の組を契約から引く | `contracts.facts` | モジュール | `CODEX_FOREIGN_AUTH` を削除（lracct の後に `CLAUDE_FOREIGN_AUTH`・`CLAUDE_TOKEN_ENV` が残れば同じ） |
| `domains/relay/hub.py` | 変更 | 切り離して起動するレーンの環境から `detach-drop` の行を外す | `contracts.facts` | モジュール | `spawn_env`（変更） |
| `domains/relay/tmux.py` | 変更 | レーンの起動行で、tmux のサーバーのグローバル環境から継承した `drop`・`detach-drop` の変数を外す | `contracts.facts` | モジュール | `new_session_args`（変更） |
| `tests/checks/test_spec_docs.py` | 変更 | 環境変数の部分のテストを `test_spec_env.py` へ移す | — | — | 下の「既存のテストの移し替え」 |
| ほかのファイル（下の「名前の定数の外し方」の表） | 変更 | 名前の定数を外して字にするか、組を契約から引く | `contracts.facts`（組を引く所だけ） | モジュール | 定数の削除と参照の置き換えだけ |

## 規則 `spec-env`（`checks/spec_env.py`）

満たす決定: 決定 1・3・5・6、受け入れ条件 2〜5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `findings` | `spec_env.py` | 新設 | `(model: SpecModel, scope: Scope, conf: dict[str, Any]) -> list[Finding]` | 4 つの見方の違反を、読み出し → 字 → シェル → 逆向きの順に返す |
| `module_names` | `spec_env.py` | 新設 | `(scope: Scope) -> dict[str, dict[str, str \| tuple[str, ...]]]` | モジュール名ごとに、最上位の文字列の定数と、文字列だけの組（tuple・list・set・frozenset の呼び出し。1 要素の組を含む）の定数の表を作る |
| `contract_id` | `spec_env.py` | 新設 | `(name: str, ids: frozenset[str], fstring_head: bool = False) -> str \| None` | 名前が当たる契約の行の `id` を返す（当たらなければ None）。族の行では族の `id`（`NDF_PORT_<ROLE>`）を返す。4 つの見方と `unused` はすべてこの関数で照らす |
| `reads` | `spec_env.py` | 新設 | `(tree: ast.Module, module: str, names: dict[...], receivers: frozenset[str]) -> list[tuple[int, str]]` | 読み出しの（行, 名前）を返す。名前は字・同じモジュールの定数・import した `ndf` の定数・組を回す変数から解き、解けないものは返さない |
| `literals` | `spec_env.py` | 新設 | `(tree: ast.Module) -> list[tuple[int, str]]` | 字の全体が `NDF_[A-Z0-9_]+` の文字列、`NDF_…=` の頭、`NDF_` で始まる f 文字列の頭を（行, 名前）で返す |
| `shell_names` | `spec_env.py` | 新設 | `(text: str) -> list[tuple[int, str]]` | シェルの本文の `NDF_` の読み出しと書き込みの（行, 名前）を返す。見るファイルは `shell_roots` の下の `.sh` と、`launchers.json` に載る拡張子の無いファイル |
| `unused` | `spec_env.py` | 新設 | `(model: SpecModel, seen: set[str]) -> list[str]` | `NDF_` で始まる行のうち、`seen` にもほかの契約の `env` の欄にも無い `id` を返す。`seen` は 4 つの見方で見えた名前を `contract_id` で照らした `id` の集まり（族の行は族の `id` で入る）で、ほかの契約の `env` の欄の名前も `contract_id` で照らしてから比べる |
| `env_reads` | `spec_env.py` | 移す | 今のまま | `spec_docs` から移す（呼ぶ側は `reads` に置き換わるので、移した後に `reads` の特別な場合として消してよい） |
| `check` | `spec_docs.py` | 変更 | 今のまま | `--only spec-env` と規則の一覧に `spec-env` を足し、`spec_env.findings` の違反を `spec-env` として返す |
| `io_findings` | `spec_docs.py` | 変更 | 今のまま | 環境変数の部分（`_env_findings`）を外す |

- `findings`
  - 事前条件: `model.env` が契約の行を持つ。`conf` は `rules.json` の `spec.env`
  - 事後条件: 違反は `Finding("spec-env", <パス>, <行>, "<頭>: <名前> …")`。頭は `read:`・`literal:`・`shell:`・`unused:`。`unused:` のパスは `contracts/domains/env.json`、行は 1
  - 副作用: シェルのファイルを読む（`Scope.sources` の本文。新しくディスクを読まない）
- 名前の照らし（`contract_id`）: 照らし方はこの 1 つだけで、設計文書の決定 3 と同じである。
  - 通常の字 `n`（読み出し・字・シェルの名前）: `n` が行の `id` に等しければその `id`。`<` を含む `id` の頭 `h`（`<` の前）について `n` が `h` の後に `[A-Z0-9_]+` が 1 字以上続く形（`re.fullmatch(re.escape(h) + "[A-Z0-9_]+", n)`）ならその族の `id`。ほかは None
  - f 文字列の頭: 最初の部品が `=` を含めば（`f"NDF_CONTEXT_POLICY={v}"`）、`=` の前を通常の字として上の規則で照らす。`=` を含まなければ（`fstring_head=True`）、部品が族の頭 `h` と等しいときだけ族の `id`。ほかは None（`f"NDF_X_{r}"` の頭 `NDF_X_` は族でなく、契約に無い名前として違反になる）
  - 当たらない例: `"NDF_PORT_"`（頭そのもの）、`f"NDF_PORT_X_{r}"`（最初の部品が頭より長い）、`"NDF_PORT_http"`（小文字）
  - 見えた名前は、`contract_id` が返した `id`（族なら族の `id`）で `seen` に入る。契約に無い名前は `seen` に入れず違反にする

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `reads` | 受け手が `receivers` のどれか（`os.environ`・`environ`・`env`・`ctx.env` を点つなぎの名前で比べる）で `.get`・`.pop`・`.setdefault` の第 1 引数・添字・`in` と `not in` の左辺 | 読み出しとして名前を解く |
| `reads` | 呼び出しが `os.getenv` | 同上 |
| `reads` | 名前の式が字 | その字 |
| `reads` | 名前の式が `Name` で、同じモジュールの最上位の定数 | 定数の値 |
| `reads` | 名前の式が `Name` か `Attribute` で、`from ndf… import X`・`from ndf… import X as Y`・`import ndf… as m` の先の最上位の定数 | 定数の値 |
| `reads` | 名前の式が `Name` で、読み出しを囲む `for` 文の本体か内包（`[… for n in X]` の要素と `if`）の中にあり、その `for` の変数で、回す対象が上の 3 つで解ける組か、その場に書いた字の組（`for n in ("A", "B")`・`for n in ("A",)`）で、その本体の中で同じ名前に代入し直していない | 組の要素のすべて。この行を次の行より先に当てる（`for name in NAMES: env.get(name)` を解く） |
| `reads` | 名前の式が `Name` で、上の行に当たらず、同じ関数の中のどこかで束縛されている（引数・`=`・`+=`・`:=`・`for` の変数・`with … as`・`except … as`・`import`。ループの外での同名の読み出しや、解けない組を回す `for` を含む） | 返さない。モジュールの定数に戻さない（最上位の定数と取り違えない） |
| `reads` | ほか（関数の引数・式の結果・`facts.ids` の返り） | 返さない |
| `literals` | 文字列の定数の全体が `^NDF_[A-Z0-9_]+$` | 名前 |
| `literals` | 文字列の定数が `^NDF_[A-Z0-9_]+=` で始まる | `=` の前 |
| `literals` | f 文字列の最初の部品が `NDF_` で始まる文字列 | 部品が `=` を含めば `=` の前を通常の字として照らす。含まなければ部品を f 文字列の頭として照らす（`contract_id(…, fstring_head=True)`） |
| `literals` | f 文字列（`ast.JoinedStr`）の中の文字列の部品（`ast.Constant`） | 通常の字として見ない。走査は `JoinedStr` に当たったら最初の部品だけを上の行で扱い、子の `Constant` へ降りない（`f"NDF_PORT_{role}"` の `NDF_PORT_` を頭そのものの字として二度数えない） |
| `literals` | docstring（モジュール・クラス・関数の最初の式の文字列） | 見ない |
| `shell_names` | 行を左から 1 字ずつ読み、単一引用 `'…'`・二重引用 `"…"`・`\` の後の 1 字を引用の中として追う。引用の外で、行頭か空白の直後の `#` から行末まで | コメントとして見ない。引用の中の `#`（`printf "# %s\n" "$NDF_X"`）と `${#NDF_X}`・`$#` の `#` はコメントにしない |
| `shell_names` | コメントを除いた残りの `$NDF_X`・`${NDF_X}`・`${NDF_X:-…}`・`${#NDF_X}`（引用の中も含む） | 読み出し |
| `shell_names` | コメントを除いた残りの、語の頭（行頭・空白・`;`・`&`・`\|`・`(` の直後）の `NDF_X=` | 書き込み（同じく契約と照らす）。1 行のすべての当たりを拾う（`export A=1 NDF_X=2`・`local a NDF_X=1`・`NDF_A=1 NDF_B=2 cmd`） |
| 族の照らし | 字が `<` を含む `id` の頭に `[A-Z0-9_]+` が続く形 | 族の行の名前 |
| 族の照らし | f 文字列の最初の部品が族の頭と等しい | 族の行の名前 |
| 族の照らし | 字が族の頭そのもの・f 文字列の最初の部品が頭より長い | 族に数えない（`contract_id` が None を返し、契約に無い名前として違反になる） |

- 繰り返しの終わる条件: 構文木の走査は 1 ファイル 1 回。`module_names` は全モジュールを 1 回走査して表を作り、`reads` は表を引くだけ
- 計算量の注意: `Source.tree` は検査の全体で 1 回だけ作られる（`checks/model.py`）。新しく構文木を作らない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `findings` | `rules.json` に `spec.env` が無い・形が違う | 規則のデータの壊れ | `findings` の頭 | 止める | `config_broken`、終了コード 2（`checks/run.py` の既存の扱い） | 直して打ち直せる |
| `reads` | import の先のモジュールが `scope.code()` に無い | `ndf` の外の import | 名前の解き | 解けない読み出しとして返さない | 無し | — |
| `shell_names` | 本文が読めない | 文字コード | `Source.text` | 既存の `Source` の扱い（`invalid`） | 終了コード 2 | 直して打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `spec.env.receivers` | 設定値 | `checks/rules.json` | `["os.environ", "environ", "env", "ctx.env"]` |
| `spec.env.not_env` | 設定値 | `checks/rules.json` | `{"NDF_PLUGIN_GUIDE_START": "古い NDF が指示書に書いた案内の印。measure_repo が利用者のリポジトリで探す字で、環境変数でない"}` |
| `spec.env.shell_roots` | 設定値 | `checks/rules.json` | `["src", "bin"]` |
| `ENV` | 定数（規則の名前 `spec-env`） | `spec_env.py` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `module_names` | `checks/facts.py` の `consts`・`lit` | `grep -n "def consts\|def lit\|def _strings" src/ndf/checks/facts.py` | そのままは使わない。`lit` の組は `_strings` が 2 要素以上を求め（`const-fact` の `table-set` の条件）、`NAMES = ("UNDECLARED",)` が読めずにすり抜ける。`spec_env.py` に `_names(node)` を置き、文字列の定数と、文字列だけの 1 要素以上の組（tuple・list・set と `frozenset(…)`・`set(…)`・`tuple(…)` の呼び出し）を読む。最上位の代入の取り出しは `consts` と同じ形（`Assign`・`AnnAssign` の大文字の名前）。`facts.lit` は `const-fact` のために変えない |
| `reads` | `spec_docs.env_reads`・`_env_name` | `grep -n "def env_reads" src/ndf/checks/spec_docs.py` | 受け手の比べを広げて置き換える |
| `unused` | `spec_model.SpecModel.locations`・`public_ids` | `grep -n "env" src/ndf/checks/spec_model.py` | 使う（`locations.json` と `entrypoints.json` の `env` を集める） |
| `shell_names` | 無し（シェルの構文解析のライブラリ） | `uv pip list` に bashlex などが無い | 引用を追ってコメントを除く小さな走査（上の処理の手順）と、読み出し `\$\{?#?(NDF_[A-Z0-9_]+)`・書き込み `(?:^\|[\s;&\|(])(NDF_[A-Z0-9_]+)=` の 2 つの正規表現（`finditer` で 1 行のすべての当たり）で足りる（名前だけを取る） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `reads` | 受け手 4 つ × 名前の出所 5 つ（字・同じモジュールの定数・import した定数と別名・最上位の組・その場の組）で契約に無い名前を返す。`not in os.environ` も返す。`for name in NAMES: env.get(name)` と `[env.get(n) for n in NAMES]` を組の全要素に解く | 1 要素の組（`NAMES = ("UNDECLARED",)` を回す・`for n in ("UNDECLARED",)`）も解いて返す。契約にある名前・族の名前（`NDF_PORT_HTTP`）は違反にならない。関数の引数で受けた名前と、関数の中で代入し直した変数は返さない。ループの外で同じ名前を読む・関数の中で同名に別の値を代入する場合は、モジュールの同名の定数に戻さず返さない。`NDF_` で始まらない名前も返す | import の先が `ndf` の外 |
| `literals` | 辞書の鍵 `{"NDF_X": …}`・`"NDF_X=1"`・`f"NDF_X_{r}"` を返す | docstring の `NDF_X` は返さない。`not_env` の字は違反にならない。`f"NDF_PORT_{role}"` は族の行に当たり、頭の部品 `NDF_PORT_` を通常の字としてもう一度返さない（違反 0 件）。`"NDF_PORT_"` と `f"NDF_PORT_X_{r}"` は違反になる | — |
| `shell_names` | `${NDF_X}`・`$NDF_X`・`${NDF_X:-y}`・`${#NDF_X}`・`export NDF_X=1` | `# $NDF_X` と `x=1 # $NDF_X` のコメントは返さない。`printf "# %s\n" "$NDF_X"` と `echo '# x' $NDF_X` は `NDF_X` を返す。`export A=1 NDF_X=2` と `NDF_A=1 NDF_B=2 cmd` は 2 つ目以降も返す | — |
| `unused` | どこにも現れない `NDF_X` の行を返す | `locations.json` の `env` にだけ現れる行は返さない。`NDF_` で始まらない行は返さない。族の行 `NDF_PORT_<ROLE>` は、字 `NDF_PORT_HTTP` だけ・f 文字列 `f"NDF_PORT_{r}"` だけが現れるときどちらも返さない。`"NDF_PORT_"` だけが現れるときは返す | — |
| `contract_id` | `NDF_RUN_ID` → `NDF_RUN_ID`、`NDF_PORT_HTTP` → `NDF_PORT_<ROLE>`、f 文字列の頭 `NDF_PORT_` → `NDF_PORT_<ROLE>` | `NDF_PORT_` の字・f 文字列の頭 `NDF_PORT_X_`・`NDF_PORT_http` は None | — |
| `findings` | 今の `origin/main` に行と直しを足したツリーで違反なし | — | `spec.env` が無い `rules.json` で `config_broken` |

## 契約の行（`contracts/domains/env.json`）

満たす決定: 決定 2・3・4・6、受け入れ条件 1・5・8

足す 37 行の値。`secret` は `GITHUB_TOKEN`・`OPENAI_API_KEY`・`CODEX_API_KEY` だけ `true`。`meaning` は読み手と書き手を書き、無いときの扱いを添える（#4062 の行と同じ書き方）。

| `id` | `area` | `set_by` | `type` | `child` | `group` | `-e` |
| --- | --- | --- | --- | --- | --- | --- |
| `NDF_DOCKER_COMMAND` | worktree | user | string | | | 渡る |
| `NDF_LAUNCH_SLOT_PARENT` | runtime | ndf | path | detach-drop | `test-isolation` | 渡らない |
| `NDF_METRICS_DIR` | records | user | path | | | 渡る |
| `NDF_PARALLEL_POLL` | progress | user | seconds | | | 渡る |
| `NDF_RELAY` | relay | user | string | | | 渡る |
| `NDF_RUN_ID` | records | ndf | string | drop | | 渡らない |
| `NDF_RUN_LOG` | records | ndf | path | drop | | 渡らない |
| `NDF_RUN_LOG_SINK` | records | user | string | | | 渡る |
| `NDF_SLACK_API_BASE` | notify | user | string | detach-drop | | 渡らない |
| `NDF_STAGE_INPUT` | progress | ndf | string | drop | | 渡らない |
| `NDF_STAGE_NOTES` | progress | ndf | string | drop | | 渡らない |
| `NDF_STATUSLINE_AUTH_CACHE` | records | user | path | | | 渡る |
| `NDF_TEST_SLOTS_DIR` | testing | user | path | | | 渡る |
| `NDF_TEST_SLOT_HELD` | testing | ndf | path | detach-drop | `test-isolation` | 渡らない |
| `NDF_WORKER_KIND` | hooks | ndf | string | drop | | 渡らない |
| `NDF_WORKTREE_BASE` | worktree | user | path | | | 渡る |
| `NDF_ENVIRONMENT` | worktree | ndf | string | | | 渡る |
| `NDF_SLOT` | worktree | ndf | integer | | | 渡る |
| `NDF_WORKTREE` | worktree | ndf | path | | | 渡る |
| `NDF_SHARED_NETWORK` | worktree | ndf | string | | | 渡る |
| `NDF_PORT_<ROLE>` | worktree | ndf | integer | | | 渡る |
| `NDF_EXPOSE_URL` | worktree | ndf | string | | | 渡る |
| `NDF_EXPOSE_HOST` | worktree | ndf | string | | | 渡る |
| `NDF_EXPOSE_ENVIRONMENT` | worktree | ndf | string | | | 渡る |
| `NDF_EXPOSE_SLOT` | worktree | ndf | integer | | | 渡る |
| `GH_REPO` | hooks | user | string | detach-drop | | 渡らない |
| `GITHUB_ACTIONS` | ci | ci | flag | | | 渡る |
| `GITHUB_TOKEN` | ci | user | string | | `github-token`（`GH_TOKEN` の後） | 渡らない |
| `PWD` | relay | runtime | path | | | 渡る |
| `SLACK_USER_MENTION` | notify | user | string | | | 渡る |
| `XDG_CACHE_HOME` | identity | user | path | | | 渡る |
| `OPENAI_API_KEY` | accounts | user | string | | `codex-auth` | 渡らない |
| `CODEX_API_KEY` | accounts | user | string | | `codex-auth` | 渡らない |
| `ANTHROPIC_MODEL` | accounts | user | string | | | 渡る |
| `AWS_PROFILE` | accounts | user | string | | | 渡る |
| `AWS_REGION` | accounts | user | string | | | 渡る |
| `ANTHROPIC_CONFIG_DIR` | accounts | user | path | | | 渡る |

`-e` の列は契約の列でなく、リレーハブが切り離して起動するレーンへ `-e` で値を渡すかの結果である（`secret` が偽で、`child` が `drop`・`detach-drop` でなければ渡る。設計文書の決定 4）。

`ANTHROPIC_MODEL`・`AWS_PROFILE`・`AWS_REGION`・`ANTHROPIC_CONFIG_DIR` の `child` は空（親の値をそのまま渡す）に決める。lracct の `account-drop` は「ログインより上に来る認証の源」の 13 変数で、親と子のアカウントが違う子（登録済みアカウント・従量・API キー）でまとめて外す。この 4 つは認証の値でも提供元の選択でもないので `account-drop` に入れない。`AWS_PROFILE`・`AWS_REGION` は提供元の選択（`CLAUDE_CODE_USE_BEDROCK` ほか。`account-drop`）が有効なときだけ効き、`ANTHROPIC_CONFIG_DIR` は lracct の `auth_overrides` が「常に」確かめるプロファイルの置き場で、外すと確かめる場所と Claude Code が読む場所が既定の `~/.config/anthropic` へ一緒に移るだけで守りは増えない。`account-drop` に入れると lracct の 13 変数の組とテスト（「表の `account-drop` の行を 1 つ外す」で落ちるテスト）が変わる。子ごとの値は次のとおりで、lracct のマージの後に `test_session_env.py` で固定する。

| 子 | `AWS_PROFILE`・`AWS_REGION`・`ANTHROPIC_MODEL` | `ANTHROPIC_CONFIG_DIR` |
| --- | --- | --- |
| 既定のログイン（`child_env` だけ） | 親の値 | 親の値 |
| 登録済みアカウント（`account_env`） | 親の値（提供元の選択が外れるので `AWS_*` は効かない） | 親の値 |
| 従量の Bedrock の宣言（`metered_env`） | 宣言の値（`account_metered` の `AWS_PROFILE`・`AWS_REGION`・`ANTHROPIC_MODEL` が親の値に重なる）。宣言に無い変数は親の値 | 親の値 |
| 従量の API キーの宣言（`metered_env` で `metered_kind` が `API_KEY_KIND`） | `AWS_PROFILE`・`AWS_REGION` は無い（lracct の `METERED_CHECKED` をすべて外す）。`ANTHROPIC_MODEL` は親の値 | 親の値 |
| 登録簿の API キー（`key_env`） | 親の値（`key_env` が外すのは `account-drop` と秘密の `SECRET_ENV` だけ） | 親の値 |

登録済みアカウントの子が Bedrock の親の `ANTHROPIC_MODEL`（Bedrock のモデルの ID）を受け継ぐのは今と lracct の後の振る舞いのままで、この課題は変えない。テストは、親に 4 つを置き、親と違うアカウントの子それぞれで上の表の値になることを確かめる（`account-drop` に入れる変更で落ちる）。

既存の `CLAUDE_PLUGIN_ROOT`・`NDF_CALLER_PLUGIN_ROOT`・`NDF_STATE_HOME` の行に `group: "test-isolation"` を足す（`testing/isolation.py` の `REMOVED_ENV` の順）。既存の `GH_TOKEN` の行に `group: "github-token"` を足し、`GITHUB_TOKEN` より前に置く（`facts.ids` は表の順で返し、今の `TOKEN_ENV` の優先の順が `GH_TOKEN` → `GITHUB_TOKEN` である）。消す 4 行は要求仕様の「消す」のとおり。`columns.area.summary` に `testing` を、`columns.child.summary` に `detach-drop`（設計文書の「データ構造」の文）を、`columns` に `group` を足す（`facts.rows` は設定に無い列で絞ると `FactsError` を出す）。`hub.spawn_env` は `child_env` の返りから `facts.ids("env", child="detach-drop")` の名前を外してから名前・役割・計画の名前を足す（`tool_env`・`child_env` は `detach-drop` を見ない）。`tmux.new_session_args` は、起動行 `line` の頭に `env` と、`facts.ids("env", child="drop")` と `facts.ids("env", child="detach-drop")` の名前ごとの `-u <名前>` を置く（表の順、名前だけで値は置かない）。tmux の `new-session` は変数を外す指定を持たず、稼働中のサーバーがグローバル環境に持つ値は `-e` と関係なく新しいセッションへ継承されるので、外すのはセッションの中で最初に動くこのコマンドにする。`GH_REPO` を `detach-drop` にするのは、ハブのリポジトリの指定がレーンの作業ディレクトリのリポジトリと違うとき、レーンの中の `gh pr` の既定の対象がずれ、`gh pr merge` を strict hook が止めるためである（設計文書の決定 4）。

## 名前の定数の外し方

満たす決定: 決定 2、受け入れ条件 6

| ファイル | 外す定数 | 置き換え |
| --- | --- | --- |
| `adapters/run_log.py` | `ENV_RUN_ID`・`ENV_RUN_LOG`・`ENV_RUN_LOG_SINK` | 使う所に `"NDF_RUN_ID"` などの字 |
| `core/converge/location.py` | `BASE_ENV` | 字 |
| `domains/crossrefactor/allocation.py` | `METRICS_ENV` | 字 |
| `domains/hooks/strict.py` | `_GH_REPO` | 字 |
| `domains/worktree/container.py` | `DOCKER_ENV` | 字 |
| `identity.py` | `WORKER_KIND_ENV` | 字。`core/worker.py`・`domains/hooks/guards/full_test.py` の `identity.WORKER_KIND_ENV` も字 |
| `progress/invoke.py` | `INPUT_ENV`・`NOTES_ENV` | 字 |
| `progress/plan/run.py` | `POLL_ENV` | 字 |
| `slot_pool.py` | `PARENT_ENV` | 字 |
| `testing/slots.py` | `HELD_ENV`・`CI_ENV`・`SLOTS_DIR_ENV` | 字。`adapters/launch_slot.py` の `slots.HELD_ENV` も字 |
| `testing/isolation.py` | `REMOVED_ENV`（`slots.HELD_ENV`・`slot_pool.PARENT_ENV` を含む組） | 関数 `removed_env() -> tuple[str, ...]` を置き、`tuple(facts.ids("env", group="test-isolation"))` を返す。最上位の定数（`REMOVED_ENV = tuple(facts.ids(…))`）にはしない（import の時に契約を読み、`isolation` を import するだけの経路が契約の壊れで止まる）。参照元の `tests/cli/test_cli.py`（`state_home` の fixture）と `tests/testing/_real_state.py`（`forbid_real_state`）の `for name in isolation.REMOVED_ENV:` を `for name in isolation.removed_env():` にする。どちらも名前の字を書かず、組は契約からだけ引く |
| `adapters/cli.py` | `ENV_RUN_ID`・`ENV_RUN_LOG`・`ENV_RUN_LOG_SINK` の公開し直し（import と `__all__`） | 外す。使う側（`tests/adapters/test_cli_run_log.py` など）は字にする |
| `domains/records/cli.py` | `invoke.INPUT_ENV` の import | 字 |
| `domains/release/ndf_settings.py` | `github_mod.TOKEN_ENV` の参照 | `facts.ids("env", group="github-token")`（設定の手順へ渡す環境から 2 つのトークンを外す振る舞いは変えない） |
| `adapters/github.py` | `TOKEN_ENV` | `facts.ids("env", group="github-token")` |
| `adapters/training_cache.py` | `CODEX_FOREIGN_AUTH` | `facts.ids("env", group="codex-auth")`（Claude の認証の組は lracct が直す） |

`relay/signals.py` の `LAUNCH_ENV` は #4062 の実装が外す（設計文書の決定 7）。外した後に `grep -rn "_ENV = \"" src/ndf` で残りが 0 件かを確かめる（`NDF_` 以外の名前の定数で、契約に載る名前のもの）。テストが名前の 1 つの定数を import していれば（`tests/adapters/test_run_log.py` など）、テストも字にする。名前の組の定数（`REMOVED_ENV`・`TOKEN_ENV`・`CODEX_FOREIGN_AUTH`）を使うテストは字の組にせず、製品のコードと同じ関数か `facts.ids("env", group=…)` で引く（字の組にすると `const-fact` の外で契約の写しができる）。

### 既存のテストの移し替え（`tests/checks/test_spec_docs.py`）

`spec_docs.env_reads` を消し、`io_findings` から環境変数の部分を外すと、次のテストが旧関数と旧規則の名前・指摘の文を期待して落ちる。同じ PR で移す。

| 今のテスト | 扱い |
| --- | --- |
| `test_env_reads_finds_the_four_forms_and_skips_names_in_variables` | `test_spec_env.py` へ移し、`spec_env.reads` を呼ぶ。今の 4 形式（添字・`.get`・`os.getenv`・`in`）が返ることと、関数の中で代入した変数（`name = 'E'` の後の `os.environ.get(name)`）を返さないことを保つ |
| `test_io_findings_pins_env_messages_for_code_only` | `test_spec_env.py` へ移し、`spec_env.findings` の違反として `("spec-env", <パス>, <行>, "read: UNKNOWN_B …")` を期待する。製品のコード（`src/ndf`）だけを見て `tests/` の同じ本文を見ないことを保つ |
| `test_io_findings_orders_commands_then_env_then_launchers` | 環境変数の行（`zz_env.py` と `環境変数 NOPE …` の期待）を外し、サブコマンド → 起動スクリプトの並びだけを確かめる |
| `_row(d, "spec-io")` を消すテスト（189 行目）・`test_io_findings_of_the_real_model_is_empty` | 変えない（`spec-io` の規則は残る）。`entrypoints.json` の規則の一覧に `spec-env` の行が要るなら同じ形で足す |
| `Copy` のフィクスチャ（`COPIED` に `src` が無い）を通るテスト。`test_the_plugin_has_no_findings_and_render_is_stable`・`test_hand_edit_of_a_page_is_caught_and_write_spec_restores_it` ほか、`copy.findings()` の違反の全体を比べるもの | `Copy.findings` が `spec_docs.check` の返りから `rule == "spec-env"` の違反を除いて返す。写しにはコードが無く、逆向きの検査 (4) が `locations.json`・`entrypoints.json` の `env` に載らない `NDF_` の行を `unused:` で落とすため、写しの上の `spec-env` の結果は意味を持たない。`spec-env` は `test_spec_env.py` が自分の写しのソースで確かめ、実物のツリーの 0 件は CI の `ndf check` が確かめる。`spec_docs.check` は `spec_env.findings` を今の設計のまま呼ぶ |

`Copy` の扱いで採らなかった案は 2 つある。`COPIED` に `src`（4.5 MB）を足す案は、`copy` を使う 50 余りのテストのそれぞれで `src` を写し、`run.collect` が全モジュールの構文木を作るので、テストの所要時間が大きく増える。`spec_env.findings` を `spec_docs.check` から外して `run.check` から単体で呼ぶ案は、契約のモデルの読み込み（`spec_model.load`、2026-11-22 の手元の計測で 1 回 0.95 秒）が `ndf check` の 1 回ごとに 2 回になる。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `session_env.child_env` | `NDF_RUN_ID` ほか `child: drop` の 5 つを入れた環境を渡すと、返りに無い | 利用者の設定（`NDF_RUN_LOG_SINK`）と `detach-drop` の 3 つ（スロットの 2 つと `NDF_SLACK_API_BASE`）は返りに残る（親が待つ直接の子） | — |
| `session_env` の lracct の子（lracct の後） | 親に `AWS_PROFILE`・`AWS_REGION`・`ANTHROPIC_MODEL`・`ANTHROPIC_CONFIG_DIR` を置き、親と違うアカウントの子（`account_env`・`metered_env` の Bedrock と API キーの宣言・`key_env`）の値が「契約の行」の子ごとの表のとおり | 4 行のどれかに `account-drop` を付けると落ちる | — |
| `github` のトークンの選び | `GH_TOKEN` と `GITHUB_TOKEN` の両方があれば `GH_TOKEN` | `GH_TOKEN` が空なら `GITHUB_TOKEN` | — |
| `ndf_settings` の設定の手順の環境 | 2 つのトークンがどちらも外れる | — | — |
| `testing.isolation` | 5 つの変数がどちらも外れる（今のテストのまま）。`removed_env()` が契約の `test-isolation` の組と同じ名前・同じ順を返す | — | — |
| `hub.spawn_env` と `tmux.new_session_args` | `NDF_RUN_ID` ほか `drop` の 5 つと `detach-drop` の 4 つ（`NDF_LAUNCH_SLOT_PARENT`・`NDF_TEST_SLOT_HELD`・`NDF_SLACK_API_BASE`・`GH_REPO`）を入れた環境を `spawn_env` に渡すと返りに無く、その返りを `new_session_args` に渡した引数に 9 つの値（工程の入力の JSON の字など）が現れず、`-e` にも 9 つの名前が無い。起動行は `env -u <名前> …` で始まり、`drop`・`detach-drop` の行の名前をすべて含む | `NDF_RUN_LOG_SINK` は `-e` で渡る | — |
| 前からある tmux サーバーの上のレーン（実物の tmux、`test_relay_parts.py` の `test_the_wrapper_session_has_no_status_line` と同じ形で専用のソケット） | 9 つの変数を値つきで持つ環境で `new-session` を打ってサーバーを先に立て、その上へ `new_session_args("t2", <リポジトリ B>, ["sh", "-c", "env > <ファイル>"], spawn_env({…, "GH_REPO": "<owner>/<リポジトリ A>"}, …))` でレーンを起動すると、書き出した環境に 9 つのどの名前も無い | `NDF_RUN_LOG_SINK` のような `-e` で渡る行の値は書き出した環境に有る | — |
