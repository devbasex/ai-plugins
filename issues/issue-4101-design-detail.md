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
│   ├── identity.py・slot_pool.py
│   ├── progress/{invoke,plan/run}.py
│   └── testing/{slots,isolation}.py  … 名前の定数を外して字にする（組は facts.ids）
└── tests/
    ├── checks/test_spec_env.py *
    └── domains/relay/test_session_env.py
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
| ほかのファイル（下の「名前の定数の外し方」の表） | 変更 | 名前の定数を外して字にするか、組を契約から引く | `contracts.facts`（組を引く所だけ） | モジュール | 定数の削除と参照の置き換えだけ |

## 規則 `spec-env`（`checks/spec_env.py`）

満たす決定: 決定 1・3・5・6、受け入れ条件 2〜5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `findings` | `spec_env.py` | 新設 | `(model: SpecModel, scope: Scope, conf: dict[str, Any]) -> list[Finding]` | 4 つの見方の違反を、読み出し → 字 → シェル → 逆向きの順に返す |
| `module_names` | `spec_env.py` | 新設 | `(scope: Scope) -> dict[str, dict[str, str \| tuple[str, ...]]]` | モジュール名ごとに、最上位の文字列の定数と、文字列だけの組（tuple・list・set・frozenset の呼び出し）の定数の表を作る |
| `reads` | `spec_env.py` | 新設 | `(tree: ast.Module, module: str, names: dict[...], receivers: frozenset[str]) -> list[tuple[int, str]]` | 読み出しの（行, 名前）を返す。名前は字・同じモジュールの定数・import した `ndf` の定数・組を回す変数から解き、解けないものは返さない |
| `literals` | `spec_env.py` | 新設 | `(tree: ast.Module) -> list[tuple[int, str]]` | 字の全体が `NDF_[A-Z0-9_]+` の文字列、`NDF_…=` の頭、`NDF_` で始まる f 文字列の頭を（行, 名前）で返す |
| `shell_names` | `spec_env.py` | 新設 | `(text: str) -> list[tuple[int, str]]` | シェルの本文の `NDF_` の読み出しと書き込みの（行, 名前）を返す。見るファイルは `shell_roots` の下の `.sh` と、`launchers.json` に載る拡張子の無いファイル |
| `unused` | `spec_env.py` | 新設 | `(model: SpecModel, seen: set[str]) -> list[str]` | `NDF_` で始まる行のうち、`seen` にもほかの契約の `env` の欄にも無い `id` を返す |
| `env_reads` | `spec_env.py` | 移す | 今のまま | `spec_docs` から移す（呼ぶ側は `reads` に置き換わるので、移した後に `reads` の特別な場合として消してよい） |
| `check` | `spec_docs.py` | 変更 | 今のまま | `--only spec-env` と規則の一覧に `spec-env` を足し、`spec_env.findings` の違反を `spec-env` として返す |
| `io_findings` | `spec_docs.py` | 変更 | 今のまま | 環境変数の部分（`_env_findings`）を外す |

- `findings`
  - 事前条件: `model.env` が契約の行を持つ。`conf` は `rules.json` の `spec.env`
  - 事後条件: 違反は `Finding("spec-env", <パス>, <行>, "<頭>: <名前> …")`。頭は `read:`・`literal:`・`shell:`・`unused:`。`unused:` のパスは `contracts/domains/env.json`、行は 1
  - 副作用: シェルのファイルを読む（`Scope.sources` の本文。新しくディスクを読まない）
- 名前の照らし: 名前 `n` は、`n` が行の `id` に等しいか、`<` を含む `id` の頭（`<` の前）で始まるとき契約にある。`literals` の f 文字列の頭は、族の頭と等しいか族の頭で始まるとき契約にある

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `reads` | 受け手が `receivers` のどれか（`os.environ`・`environ`・`env`・`ctx.env` を点つなぎの名前で比べる）で `.get`・`.pop`・`.setdefault` の第 1 引数・添字・`in` と `not in` の左辺 | 読み出しとして名前を解く |
| `reads` | 呼び出しが `os.getenv` | 同上 |
| `reads` | 名前の式が字 | その字 |
| `reads` | 名前の式が `Name` で、同じモジュールの最上位の定数 | 定数の値 |
| `reads` | 名前の式が `Name` か `Attribute` で、`from ndf… import X`・`from ndf… import X as Y`・`import ndf… as m` の先の最上位の定数 | 定数の値 |
| `reads` | 名前の式が `Name` で、同じ関数の中で代入し直されている（引数・`=`・`:=`・`for` の変数のどれか） | 返さない（最上位の定数と取り違えない） |
| `reads` | 名前の式が `for` か内包の変数で、回す対象が上の 3 つで解ける組か、その場に書いた字の組（`for n in ("A", "B")`） | 組の要素のすべて |
| `reads` | ほか（関数の引数・式の結果・`facts.ids` の返り） | 返さない |
| `literals` | 文字列の定数の全体が `^NDF_[A-Z0-9_]+$` | 名前 |
| `literals` | 文字列の定数が `^NDF_[A-Z0-9_]+=` で始まる | `=` の前 |
| `literals` | f 文字列の最初の部品が `NDF_` で始まる文字列 | その部品（`=` があれば前まで） |
| `literals` | docstring（モジュール・クラス・関数の最初の式の文字列） | 見ない |
| `shell_names` | 行頭が `#` のコメントと、空白の後の `#` から行末まで | 見ない |
| `shell_names` | `$NDF_X`・`${NDF_X}`・`${NDF_X:-…}`・`${#NDF_X}` | 読み出し |
| `shell_names` | 行頭（空白を除く）か `export `・`local ` の後の `NDF_X=` | 書き込み（同じく契約と照らす） |
| 族の照らし | 字が `<` を含む `id` の頭に `[A-Z0-9_]+` が続く形 | 族の行の名前 |
| 族の照らし | f 文字列の最初の部品が族の頭と等しい | 族の行の名前 |
| 族の照らし | 字が族の頭そのもの・f 文字列の最初の部品が頭より長い | 族に数えない（契約に無い名前として照らす） |

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
| `module_names` | `checks/facts.py` の `consts`・`lit` | `grep -n "def consts\|def lit" src/ndf/checks/facts.py` | 使う（最上位の定数と組の値を同じ規則で取る） |
| `reads` | `spec_docs.env_reads`・`_env_name` | `grep -n "def env_reads" src/ndf/checks/spec_docs.py` | 受け手の比べを広げて置き換える |
| `unused` | `spec_model.SpecModel.locations`・`public_ids` | `grep -n "env" src/ndf/checks/spec_model.py` | 使う（`locations.json` と `entrypoints.json` の `env` を集める） |
| `shell_names` | 無し（シェルの構文解析のライブラリ） | `uv pip list` に bashlex などが無い | 読み出し `\$\{?#?(NDF_[A-Z0-9_]+)` と書き込み `^\s*(?:export\s+|local\s+)?(NDF_[A-Z0-9_]+)=` の 2 つの正規表現で足りる（名前だけを取る） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `reads` | 受け手 4 つ × 名前の出所 5 つ（字・同じモジュールの定数・import した定数と別名・最上位の組・その場の組）で契約に無い名前を返す。`not in os.environ` も返す | 契約にある名前・族の名前（`NDF_PORT_HTTP`）は違反にならない。関数の引数で受けた名前と、関数の中で代入し直した変数は返さない。`NDF_` で始まらない名前も返す | import の先が `ndf` の外 |
| `literals` | 辞書の鍵 `{"NDF_X": …}`・`"NDF_X=1"`・`f"NDF_X_{r}"` を返す | docstring の `NDF_X` は返さない。`not_env` の字は違反にならない | — |
| `shell_names` | `${NDF_X}`・`$NDF_X`・`${NDF_X:-y}`・`${#NDF_X}`・`export NDF_X=1` | `# $NDF_X` と `x=1 # $NDF_X` のコメントは返さない | — |
| `unused` | どこにも現れない `NDF_X` の行を返す | `locations.json` の `env` にだけ現れる行は返さない。`NDF_` で始まらない行は返さない | — |
| `findings` | 今の `origin/main` に行と直しを足したツリーで違反なし | — | `spec.env` が無い `rules.json` で `config_broken` |

## 契約の行（`contracts/domains/env.json`）

満たす決定: 決定 2・3・4・6、受け入れ条件 1・5・8

足す 37 行の値。`secret` は `GITHUB_TOKEN`・`OPENAI_API_KEY`・`CODEX_API_KEY` だけ `true`。`meaning` は読み手と書き手を書き、無いときの扱いを添える（#4062 の行と同じ書き方）。

| `id` | `area` | `set_by` | `type` | `child` | `group` |
| --- | --- | --- | --- | --- | --- |
| `NDF_DOCKER_COMMAND` | worktree | user | string | | |
| `NDF_LAUNCH_SLOT_PARENT` | runtime | ndf | path | | `test-isolation` |
| `NDF_METRICS_DIR` | records | user | path | | |
| `NDF_PARALLEL_POLL` | progress | user | seconds | | |
| `NDF_RELAY` | relay | user | string | | |
| `NDF_RUN_ID` | records | ndf | string | drop | |
| `NDF_RUN_LOG` | records | ndf | path | drop | |
| `NDF_RUN_LOG_SINK` | records | user | string | | |
| `NDF_SLACK_API_BASE` | notify | user | string | | |
| `NDF_STAGE_INPUT` | progress | ndf | string | drop | |
| `NDF_STAGE_NOTES` | progress | ndf | string | drop | |
| `NDF_STATUSLINE_AUTH_CACHE` | records | user | path | | |
| `NDF_TEST_SLOTS_DIR` | testing | user | path | | |
| `NDF_TEST_SLOT_HELD` | testing | ndf | path | | `test-isolation` |
| `NDF_WORKER_KIND` | hooks | ndf | string | drop | |
| `NDF_WORKTREE_BASE` | worktree | user | path | | |
| `NDF_ENVIRONMENT` | worktree | ndf | string | | |
| `NDF_SLOT` | worktree | ndf | integer | | |
| `NDF_WORKTREE` | worktree | ndf | path | | |
| `NDF_SHARED_NETWORK` | worktree | ndf | string | | |
| `NDF_PORT_<ROLE>` | worktree | ndf | integer | | |
| `NDF_EXPOSE_URL` | worktree | ndf | string | | |
| `NDF_EXPOSE_HOST` | worktree | ndf | string | | |
| `NDF_EXPOSE_ENVIRONMENT` | worktree | ndf | string | | |
| `NDF_EXPOSE_SLOT` | worktree | ndf | integer | | |
| `GH_REPO` | hooks | user | string | | |
| `GITHUB_ACTIONS` | ci | ci | flag | | |
| `GITHUB_TOKEN` | ci | user | string | | `github-token`（`GH_TOKEN` の後） |
| `PWD` | relay | runtime | path | | |
| `SLACK_USER_MENTION` | notify | user | string | | |
| `XDG_CACHE_HOME` | identity | user | path | | |
| `OPENAI_API_KEY` | accounts | user | string | | `codex-auth` |
| `CODEX_API_KEY` | accounts | user | string | | `codex-auth` |
| `ANTHROPIC_MODEL` | accounts | user | string | lracct の決まり | |
| `AWS_PROFILE` | accounts | user | string | lracct の決まり | |
| `AWS_REGION` | accounts | user | string | lracct の決まり | |
| `ANTHROPIC_CONFIG_DIR` | accounts | user | path | lracct の決まり | |

「lracct の決まり」は、lracct のマージの後の `child` の語彙（`account-drop` か空）を、lracct の設計の「子の環境」の規則で当てる。既存の `CLAUDE_PLUGIN_ROOT`・`NDF_CALLER_PLUGIN_ROOT`・`NDF_STATE_HOME` の行に `group: "test-isolation"` を足す（`testing/isolation.py` の `REMOVED_ENV` の順）。既存の `GH_TOKEN` の行に `group: "github-token"` を足し、`GITHUB_TOKEN` より前に置く（`facts.ids` は表の順で返し、今の `TOKEN_ENV` の優先の順が `GH_TOKEN` → `GITHUB_TOKEN` である）。消す 4 行は要求仕様の「消す」のとおり。`columns.area.summary` に `testing` を、`columns` に `group` を足す（`facts.rows` は設定に無い列で絞ると `FactsError` を出す）。

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
| `testing/isolation.py` | `REMOVED_ENV`（`slots.HELD_ENV`・`slot_pool.PARENT_ENV` を含む組） | `facts.ids("env", group="test-isolation")` |
| `adapters/cli.py` | `ENV_RUN_ID`・`ENV_RUN_LOG`・`ENV_RUN_LOG_SINK` の公開し直し（import と `__all__`） | 外す。使う側（`tests/adapters/test_cli_run_log.py` など）は字にする |
| `domains/records/cli.py` | `invoke.INPUT_ENV` の import | 字 |
| `domains/release/ndf_settings.py` | `github_mod.TOKEN_ENV` の参照 | `facts.ids("env", group="github-token")`（設定の手順へ渡す環境から 2 つのトークンを外す振る舞いは変えない） |
| `adapters/github.py` | `TOKEN_ENV` | `facts.ids("env", group="github-token")` |
| `adapters/training_cache.py` | `CODEX_FOREIGN_AUTH` | `facts.ids("env", group="codex-auth")`（Claude の認証の組は lracct が直す） |

`relay/signals.py` の `LAUNCH_ENV` は #4062 の実装が外す（設計文書の決定 7）。外した後に `grep -rn "_ENV = \"" src/ndf` で残りが 0 件かを確かめる（`NDF_` 以外の名前の定数で、契約に載る名前のもの）。テストが定数を import していれば（`tests/adapters/test_run_log.py` など）、テストも字にする。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `session_env.child_env` | `NDF_RUN_ID` ほか `child: drop` の 5 つを入れた環境を渡すと、返りに無い | 利用者の設定（`NDF_RUN_LOG_SINK`）とスロットの 2 つは返りに残る | — |
| `github` のトークンの選び | `GH_TOKEN` と `GITHUB_TOKEN` の両方があれば `GH_TOKEN` | `GH_TOKEN` が空なら `GITHUB_TOKEN` | — |
| `ndf_settings` の設定の手順の環境 | 2 つのトークンがどちらも外れる | — | — |
| `testing.isolation` | 5 つの変数がどちらも外れる（今のテストのまま） | — | — |
