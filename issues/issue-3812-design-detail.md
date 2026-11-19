# #3812 の詳細設計: 中継のプラグインの更新

設計文書 [issue-3812-design.md](./issue-3812-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── limits.json
│   └── domains/locations.json
├── src/ndf/
│   ├── adapters/claude.py
│   └── domains/relay/
│       ├── plugin_update.py   *
│       ├── wrapper.py
│       ├── cli.py
│       ├── session_env.py
│       └── hub_decide.py
├── skills/development-workflow/references/
│   ├── relay.md
│   └── relay-accounts.md
└── tests/domains/relay/
    ├── test_relay_plugin_update.py   *
    ├── test_relay_wrapper.py
    └── test_relay_decide.py
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `src/ndf/domains/relay/plugin_update.py` | 新設 | 導入元の選び方と更新の結果の読み取り（純粋な関数）と、錠の中で 2 つのコマンドを打つ手順 | `ndf.adapters.claude`・`ndf.domains.relay.signals`・`ndf.identity` |
| `src/ndf/adapters/claude.py` | 変更 | `claude plugin` のサブコマンドを 1 回打つ関数を足す | `subprocess` |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 最初のセグメントの前と `switch` の子を止めた後に更新を呼び、記録と端末の 1 行を出す | `plugin_update`（型だけ） |
| `src/ndf/domains/relay/cli.py` | 変更 | 更新の手を組んで `Settings` へ渡す | `plugin_update`・`session_env`・`limits` |
| `src/ndf/domains/relay/session_env.py` | 変更 | `claude plugin` のコマンドへ渡す環境を作る関数を足す | `ndf.contracts.facts` |
| `src/ndf/domains/relay/hub_decide.py` | 変更 | バージョンの混ざりをメジャーバージョンで判定する | 無し |
| `contracts/limits.json` | 変更 | `relay.plugin-update`（120 秒）を足し、`relay.handover-prepare`・`relay.runtime-install` を消す | 無し |
| `contracts/domains/locations.json` | 変更 | 錠の保存先 `relay-plugin-update`（`root: config`・`path: ndf/plugin-update.lock`）を足す | 無し |
| `skills/development-workflow/references/relay.md` | 変更 | 更新・記録・ラッパーのバージョンの変わり方を 11 系の振る舞いへ直す | 無し |
| `skills/development-workflow/references/relay-accounts.md` | 変更 | 「ラッパーの入れ替え」への参照を、11 系の振る舞い（次のセグメントの起動から読む）へ直す | 無し |
| `tests/domains/relay/test_relay_plugin_update.py` | 新設 | `plugin_update` の単体のテスト | `plugin_update` |
| `tests/domains/relay/test_relay_wrapper.py` | 変更 | 更新を呼ぶ位置と順・失敗で止まらないことのテスト | `wrapper` |
| `tests/domains/relay/test_relay_decide.py` | 変更 | `mixed` のメジャーバージョンの判定のテスト | `hub_decide` |

用語集の「プラグインの更新」は設計 Pull Request で足す（`docs/glossary/glossary.json` と生成した `docs/glossary.md`・`docs/glossary/ndf-relay.md`）。実装では触らない。

## クラスと関数の構造

型の関係は設計文書の「構造」のクラス図を指す。

| 保存先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `src/ndf/domains/relay/plugin_update.py` | モジュール | `Origin`（新設）・`Updated`（新設）・`find_origin`（新設）・`read_update`（新設）・`major_changed`（新設）・`Updater`（新設。`__call__`） |
| `src/ndf/adapters/claude.py` | モジュール | `Ran`（新設） |
| `src/ndf/adapters/claude.py` | モジュール | `run_plugin`（新設） |
| `src/ndf/domains/relay/wrapper.py` | `Settings` | フィールド `update`（新設） |
| `src/ndf/domains/relay/wrapper.py` | `Wrapper` | `_loop`（変更）・`_on_switch`（変更）・`_update_plugin`（新設） |
| `src/ndf/domains/relay/cli.py` | モジュール | `wrapper_settings`（変更） |
| `src/ndf/domains/relay/session_env.py` | モジュール | `tool_env`（新設） |
| `src/ndf/domains/relay/hub_decide.py` | モジュール | `mixed`（変更）・`_major`（新設） |

## 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `run_plugin` | `src/ndf/adapters/claude.py` | 新設 | `run_plugin(args: Sequence[str], timeout: float, env: Mapping[str, str], cwd: Path, run=subprocess.run) -> Ran` | `claude plugin <args>` を標準入力なしで `cwd` で 1 回打ち、終了コードと標準出力を返す（project・local のスコープは作業ディレクトリで決まるため、ラッパーの作業ディレクトリで打つ） | `args` は `plugin` の後ろの語 | 起動できない・時間切れは `Ran(None, "", <理由>)`。例外を上げない | 外部コマンド（Claude Code の CLI。`~/.claude/plugins/` を書き換えうる） |
| `find_origin` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `find_origin(listed: Optional[Sequence[Mapping[str, Any]]], root: Path) -> Origin \| str` | 導入済みの一覧から、`ndf@` で始まる ID で `installPath` を解決したパスが `root` を解決したパスと等しい 1 件を選ぶ | 無し | 当たれば `Origin`、無ければ理由の文（`str`） | 無し（純粋。パスの解決だけ） |
| `read_update` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `read_update(market: Ran, plugin: Optional[Ran]) -> Updated` | 2 つのコマンドの結果から更新の結果を決める | `plugin` は `market` が成功したときだけ渡す | `Updated` の `result` が 4 つのどれか | 無し（純粋） |
| `Updater.__call__` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `__call__(self) -> Updated` | 錠を取り、導入元を（初回だけ）読み、2 つのコマンドを順に打って結果を返す | 生成時に `root`・`cwd`・`env`・`timeout`・`lock`・`run` を受ける | 例外を上げない。導入元を一度読めたら保持する | 錠のファイル、外部コマンド |
| `Wrapper._update_plugin` | `src/ndf/domains/relay/wrapper.py` | 新設 | `_update_plugin(self) -> Optional[Ended]` | 更新の手を呼んで秒を測り、`plugin_update` の行を書き、`failed` なら端末に 1 行出す。メジャーバージョンが変わったら端末に 1 行出して `Ended("precondition", ...)` を返す | 子が動いていない | `log.jsonl` に 1 行増える | `log.jsonl`・端末 |
| `major_changed` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `major_changed(own: str, got: Updated) -> bool` | 更新の後のバージョン（`got.after`）のメジャーがラッパーのバージョン（`own`）のメジャーと違うか | 無し | `got.after` が None なら偽 | 無し（純粋） |
| `Wrapper._loop` | `src/ndf/domains/relay/wrapper.py` | 変更 | 変わらない | 最初の `_start` の前に `_update_plugin` を呼び、`Ended` が返れば起動せずに `_end` で終える | — | — | — |
| `Wrapper._on_switch` | `src/ndf/domains/relay/wrapper.py` | 変更 | 変わらない | `_stop_child` → `_update_plugin` → `_start` の順にする（`_replace` を使わない）。`_update_plugin` が `Ended` を返せば起動せずにそれを返す | — | 次の子は更新の後に起動する | — |
| `wrapper_settings` | `src/ndf/domains/relay/cli.py` | 変更 | 変わらない | `Updater(root=ident.root, cwd=<ラッパーの作業ディレクトリ>, env=session_env.tool_env(environ), timeout=limits.seconds("relay.plugin-update"), lock=<設定の保存先>/ndf/plugin-update.lock)` を `Settings.update` に入れる。設定の保存先は `signals` の既存の関数（`CLAUDE_CONFIG_DIR` か `~/.claude`）で求める | — | — | 無し（組むだけ） |
| `tool_env` | `src/ndf/domains/relay/session_env.py` | 新設 | `tool_env(environ: Mapping[str, str]) -> dict[str, str]` | `child: drop` の変数と NDF の `src` の `PYTHONPATH` を外した環境を返す（`set` の値は入れない） | 無し | `child_env` と同じ外し方 | 無し |
| `mixed` | `src/ndf/domains/relay/hub_decide.py` | 変更 | 変わらない | バージョンが無い（旧い NDF）か `_major` の違うラッパーだけを `other` に数える | 無し | 理由の文の形は今と同じ | 無し |
| `_major` | `src/ndf/domains/relay/hub_decide.py` | 新設 | `_major(version: Optional[str]) -> Optional[str]` | バージョンの最初の `.` の前を返す（バージョンが無ければ None） | 無し | — | 無し |

値の型:

| 型 | フィールド |
| --- | --- |
| `Ran`（`NamedTuple`。`adapters/claude.py` に置き、`plugin_update` が使う） | `code: Optional[int]`・`stdout: str`・`error: str` |
| `Origin`（`NamedTuple`） | `plugin_id: str`（例 `ndf@ai-plugins`）・`marketplace: str`・`scope: str` |
| `Updated`（`NamedTuple`） | `result: str`・`before: Optional[str]`・`after: Optional[str]`・`reason: str` |
| `Settings.update` | `Optional[Callable[[], Updated]]`。既定は None（更新しない。既存のテストはこのまま通る） |

打つコマンド（2026-11-19 に Claude Code 2.1.328 で形を確かめた）:

| ステップ | `args` |
| --- | --- |
| 導入元を読む | 既存の `list_plugins`（`claude plugin list --json`）を使う |
| マーケットプレイス | `["marketplace", "update", <marketplace>, "--json"]` |
| NDF | `["update", <plugin_id>, "-y", "--json", "--scope", <scope>]` |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `run_plugin` | 起動できない | `claude` が PATH に無い・`OSError` | `subprocess.run` の例外 | `Ran(None, "", "起動できない: <例外の要約>")` を返す | 無し（呼び出し元が決める） | 次のカットポイントで打ち直す |
| `run_plugin` | 時間切れ | ネットワークの遅れ・大きな複製 | `subprocess.TimeoutExpired` | `Ran(None, "", "<秒> 秒で終わらない")` を返す | 無し | 同上 |
| `find_origin` | 一覧を読めない | `list_plugins` が None | `listed is None` | 理由の文を返す（`Updater` が `failed` にする） | 端末の 1 行 | 次の更新で読み直す（導入元を保持しない） |
| `find_origin` | 一致する導入が無い | `--plugin-dir`・開発のチェックアウト・`@inline` | 走査で当たらない | 理由の文を返す（`Updater` が `skipped` にする） | 無し（`log.jsonl` だけ） | 次の更新で読み直す |
| `read_update` | マーケットプレイスの更新の失敗 | 終了コードが 0 でない・`code` が None・JSON の `outcome` が `ok` でない | `market` の値 | `Updated("failed", None, None, <理由>)`。NDF の更新は打たない | 端末の 1 行 | 次のカットポイントで打ち直す |
| `read_update` | NDF の更新の失敗 | 終了コードが 0 でない・標準出力に JSON として読める行が無い（読むのは JSON として読める最後の行）・`outcome` が `failed` | `plugin` の値 | `Updated("failed", ...)`。理由は `failureCode` と `message` の 1 行目（パスを含まない形に切る） | 端末の 1 行 | 同上 |
| `Updater.__call__` | 錠を取れない | 別のラッパーが上限を超えて更新している | `signals.held` が偽 | `Updated("failed", None, None, "別のラッパーの更新を <秒> 秒待っても終わらない")` | 端末の 1 行 | 同上 |
| `Updater.__call__` | 予期しない例外 | 錠のファイルを作れない（`OSError`）など | `except OSError` | `failed` に畳む | 端末の 1 行 | 同上 |
| `Wrapper._update_plugin` | メジャーバージョンが変わった | 上がったバージョンのリリース | `major_changed` | 次の子を起動せずに止める | `ndf-relay: メジャーバージョンが <前> から <後> に上がった。次のセグメントを起動しない。claude を打ち直す`。ラッパーは停止理由 `precondition`（終了コード 3） | 利用者が `claude` を打ち直すと新しいバージョンのラッパーで始まる |
| `Wrapper._update_plugin` | 更新の手が `failed` を返した | 上のどれか | `result == "failed"` | 記録と端末の 1 行を出し、そのまま次の子を起動する（既定値で続ける） | `ndf-relay: プラグインを更新できなかった（<理由>）。今のバージョンで次のセグメントを起動する`。ラッパーの終了コードは変わらない | 無し |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `read_update` | `market.code == 0` かつ最後の行の JSON の `outcome == "ok"`、`plugin.code == 0` かつ `outcome == "ok"`、`updateOutcome == "up_to_date"` | `up_to_date`（`before`・`after` は `oldVersion`・`newVersion`） |
| `read_update` | 同上で `updateOutcome` が `up_to_date` 以外、または `oldVersion != newVersion` | `updated` |
| `read_update` | `market` が失敗 | `failed`（`plugin` は見ない） |
| `read_update` | `market` が成功、`plugin` が失敗 | `failed` |
| `Updater.__call__` | 導入元が未定で `find_origin` が `Origin` を返した | 保持して 2 つのコマンドへ進む |
| `Updater.__call__` | 導入元が未定で、一覧は読めたが一致が無い | `skipped`。コマンドを打たない |
| `Updater.__call__` | 導入元が未定で、一覧を読めない | `failed`。コマンドを打たない |
| `mixed` | 他のラッパーのバージョンが None | 断る |
| `mixed` | 他のラッパーの `_major` が自分と違う | 断る |
| `mixed` | すべて同じ `_major`（バージョンの残りは違ってよい） | 進める |

- 繰り返しの終わる条件: `find_origin` は一覧の要素を 1 度だけ走査する
- 計算量の注意: 無し（一覧は数十件）

## 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `relay.plugin-update` | 設定値（`seconds`） | `contracts/limits.json` | 120（コマンド 1 回ずつと錠の待ちの上限） |
| `relay-plugin-update` | 保存先（`root: config`・`path: ndf/plugin-update.lock`） | `contracts/domains/locations.json` | — |
| `MAJOR_CHANGED_LINE` | 定数（端末の 1 行のテンプレート） | `src/ndf/domains/relay/wrapper.py` | `ndf-relay: メジャーバージョンが {before} から {after} に上がった。次のセグメントを起動しない。claude を打ち直す` |
| `UPDATE_FAILED_LINE` | 定数（端末の 1 行のテンプレート） | `src/ndf/domains/relay/wrapper.py` | `ndf-relay: プラグインを更新できなかった（{reason}）。今のバージョンで次のセグメントを起動する` |
| `RESULTS` | 定数（`updated`・`up_to_date`・`skipped`・`failed`） | `src/ndf/domains/relay/plugin_update.py` | — |
| `relay.handover-prepare`・`relay.runtime-install` | 設定値 | `contracts/limits.json` | 消す（読み手 0 件） |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ設定の保存先（`~/.claude`）を使う複数のラッパーの更新（devbase で共有するコンテナを含む） | `~/.claude/plugins/`（Claude Code が書く） | `<設定の保存先>/ndf/plugin-update.lock` を `signals.held(lock, timeout)` で取る | 2 本目は `up_to_date` で終わる。途中で落ちても錠は `flock` なのでプロセスの終わりで解ける |
| 別のコンテナのラッパー（`~/.claude` を共有する devbase） | 同上 | 上と同じ錠（`flock` が共有の方式で効くかは設計文書の「未確認のまま残ること」） | 同上 |
| 利用者が別の端末で打つ `claude plugin update` | 同上 | 取らない | 同上 |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `Wrapper._update_plugin` | `{"kind": "plugin_update", "result", "from", "to", "reason", "seconds"}`（`at`・`section` は既存の `_log` が付ける） | `<状態の保存先>/relay/wrappers/<名前>/log.jsonl` | 導入先のパス・コマンドの全出力・環境変数 |
| `Wrapper._update_plugin` | `failed` のときの 1 行 | 端末（`Settings` の `out`） | 同上 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `run_plugin` | `adapters.claude.list_plugins` | `grep -n "def list_plugins" plugins/ndf/src/ndf/adapters/claude.py` | 一覧はそのまま使う。更新は打つ引数と上限が違うので、`subprocess.run` を包む同じ形の関数を隣に足す |
| `find_origin` | `adapters.claude.disabled_plugins`（一覧から ID を選ぶ） | 同上 | 使えない（有効な旧い NDF を選ぶ関数で、導入先の一致を見ない） |
| `Updater.__call__` の錠 | `signals.held` | `grep -n "def held" plugins/ndf/src/ndf/domains/relay/signals.py` | 使う |
| `tool_env` | `session_env.child_env` | `grep -n "def child_env" plugins/ndf/src/ndf/domains/relay/session_env.py` | 外し方の部分を共通の内部関数へ切り出して両方から呼ぶ。`child_env` は `NDF_CLAUDE_ACCOUNT` を入れるので、そのままは使わない |
| 10 系の `update_plugin` | `git show 976513356^:plugins/ndf/scripts/relay_lib/claude.py` | 読んだ | 打つコマンドの並びだけを参考にし、コードは写さない（11 系の判断と手の分け方で書き直す） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `find_origin` | `installPath` が根と一致する `ndf@ai-plugins` を選ぶ | 同じ ID が user と project の 2 つのスコープにあり、一致するほうを選ぶ。`installPath` が symlink 越しでも解決して一致する | 一覧が None・空・`ndf@` が無い・一致しない（`@inline`）で理由の文 |
| `read_update` | `up_to_date` と `updated`（実測の JSON の行を入力にする） | 出力の前に人向けの行があり、最後の行だけが JSON | 終了コード 1 と `failureCode`・`code` が None・JSON でない最後の行・`market` の失敗で `plugin` を見ない |
| `Updater.__call__` | 2 回呼ぶと一覧は 1 回だけ読む。コマンドが順に 2 つ打たれる | 1 回目に一致が無ければ 2 回目に読み直す | 錠を持たれている間は上限で `failed`、コマンドを打たない |
| `Wrapper._update_plugin`（`Wrapper` 経由） | 最初の起動の前と `switch` の後に 1 回ずつ呼ばれ、`plugin_update` の行が増える | `account`・`restart` では呼ばれない。`Settings.update` が None なら呼ばれず行も増えない | `failed` でも次の子が起動し、端末に 1 行出る。別のメジャーへの `updated` では次の子を起動せず `precondition` で終わる |
| `major_changed` | `11.0.7` → `11.1.0` は偽 | `after` が None は偽 | `11.0.7` → `12.0.0` は真 |
| `mixed` | 同じメジャーでバージョンの違うラッパーを断らない | `11.0.0-dev.4` と `11.1.0` を同じメジャーと読む | バージョンが None・`10.17.51` と `11.0.8` で断る |
