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
| `src/ndf/adapters/claude.py` | 変更 | `claude plugin` のサブコマンドを 1 回打つ関数を足し、`list_plugins` が環境を受け取れるようにする | `subprocess` |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 最初のセグメントの前と `switch` の子を止めた後に更新を呼び、更新しない手の前に起動先を確かめ、記録と端末の 1 行を出す。メジャーバージョンの違いで止まる前に `relay.root` を向け直す | `plugin_update`（型だけ）・`install`（`follow`） |
| `src/ndf/domains/relay/cli.py` | 変更 | 更新の手を組んで `Settings` へ渡す | `plugin_update`・`session_env`・`limits` |
| `src/ndf/domains/relay/session_env.py` | 変更 | `claude plugin` のコマンドへ渡す環境を作る関数を足す | `ndf.contracts.facts` |
| `src/ndf/domains/relay/hub_decide.py` | 変更 | バージョンの混ざりをメジャーバージョンで判定する | 無し |
| `contracts/limits.json` | 変更 | `relay.plugin-update`（120 秒）を足し、`relay.handover-prepare`・`relay.runtime-install` を消す | 無し |
| `contracts/domains/locations.json` | 変更 | 錠の保存先 `relay-plugin-update`（`root: config`・`path: ndf/plugin-update.lock`）を足す | 無し |
| `skills/development-workflow/references/relay.md` | 変更 | 更新・記録・ラッパーのバージョンの変わり方を 11 系の振る舞いへ直す | 無し |
| `skills/development-workflow/references/relay-accounts.md` | 変更 | 「ラッパーの入れ替え」への参照を、11 系の振る舞い（次のセグメントの起動から読む）へ直す | 無し |
| `tests/domains/relay/test_relay_plugin_update.py` | 新設 | `plugin_update` の単体のテスト | `plugin_update` |
| `tests/domains/relay/test_relay_wrapper.py` | 変更 | 更新と確認を呼ぶ位置と順・失敗で止まらないこと・メジャーバージョンの違いで止まってから打ち直すまでのテスト | `wrapper`・`install` |
| `tests/domains/relay/test_relay_decide.py` | 変更 | `mixed` のメジャーバージョンの判定のテスト | `hub_decide` |

用語集の「プラグインの更新」は設計 Pull Request で足す（`docs/glossary/glossary.json` と生成した `docs/glossary.md`・`docs/glossary/ndf-relay.md`）。実装では触らない。

## クラスと関数の構造

型の関係は設計文書の「構造」のクラス図を指す。

| 保存先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `src/ndf/domains/relay/plugin_update.py` | モジュール | `Origin`（新設）・`Target`（新設）・`Updated`（新設）・`find_origin`（新設）・`find_target`（新設）・`read_update`（新設）・`major_changed`（新設）・`Updater`（新設。`__call__`・`confirm`） |
| `src/ndf/adapters/claude.py` | モジュール | `Ran`（新設） |
| `src/ndf/adapters/claude.py` | モジュール | `run_plugin`（新設）・`list_plugins`（変更） |
| `src/ndf/domains/relay/wrapper.py` | `Settings` | フィールド `update`・`confirm`（新設） |
| `src/ndf/domains/relay/wrapper.py` | `Wrapper` | `_loop`（変更）・`_on_switch`（変更）・`_on_resume`（変更）・`_update_plugin`（新設）・`_confirm_plugin`（新設）・`_stop_for_major`（新設） |
| `src/ndf/domains/relay/cli.py` | モジュール | `wrapper_settings`（変更） |
| `src/ndf/domains/relay/session_env.py` | モジュール | `tool_env`（新設） |
| `src/ndf/domains/relay/hub_decide.py` | モジュール | `mixed`（変更）・`_major`（新設） |

## 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `run_plugin` | `src/ndf/adapters/claude.py` | 新設 | `run_plugin(args: Sequence[str], timeout: float, env: Mapping[str, str], cwd: Path, run=subprocess.run) -> Ran` | `claude plugin <args>` を標準入力なしで `cwd`・`env` で 1 回打ち、終了コードと標準出力を返す（project・local のスコープは作業ディレクトリで決まるため、ラッパーの作業ディレクトリで打つ） | `args` は `plugin` の後ろの語 | 起動できない・時間切れは `Ran(None, "", <理由>)`。例外を上げない | 外部コマンド（Claude Code の CLI。`~/.claude/plugins/` を書き換えうる） |
| `list_plugins` | `src/ndf/adapters/claude.py` | 変更 | `list_plugins(run=subprocess.run, cwd: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> Optional[list[dict]]` | 今の振る舞いに、`env` を `subprocess.run` へ渡す引数を足す（省けば今と同じくこのプロセスの環境）。上限は今の `PLUGIN_LIST_TIMEOUT`（30 秒）のまま | 無し | 今と同じ | 外部コマンド |
| `find_origin` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `find_origin(listed: Optional[Sequence[Mapping[str, Any]]], root: Path) -> Origin \| str` | 導入済みの一覧のうち、`enabled` が真で `ndf@` で始まる ID の行から、`installPath` を解決したパスが `root` を解決したパスと等しい行を選ぶ。ちょうど 1 件のときだけ `Origin` にする | 無し | 1 件なら `Origin`、0 件・2 件以上・一覧が None なら理由の文（`str`） | 無し（純粋。パスの解決だけ） |
| `find_target` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `find_target(listed: Optional[Sequence[Mapping[str, Any]]], origin: Origin) -> Target` | 一覧から `id` と `scope` が `origin` と同じ行を 1 件選び、その `version` と `installPath` を起動先にする | 無し | 一覧が None・該当が 1 件でない・`version` か `installPath` が文字列でなければ `Target(None, None)` | 無し（純粋） |
| `read_update` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `read_update(market: Ran, plugin: Optional[Ran]) -> Updated` | 2 つのコマンドの結果から更新の結果を決める（`target` は `Target(None, None)` のまま返し、`Updater` が埋める） | `plugin` は `market` が成功したときだけ渡す | `Updated` の `result` が `updated`・`up_to_date`・`failed` のどれか（「処理の手順」の表で重ならない） | 無し（純粋） |
| `Updater.__call__` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `__call__(self) -> Updated` | 錠を取り、導入元を（決まるまで）読み、2 つのコマンドを順に打ち、続けて一覧を読み直して起動先を `target` に入れて返す。錠を取れなかったときも、コマンドを打たずに起動先だけは読み直す | 生成時に `root`・`cwd`・`env`・`timeout`・`lock`・`run`・`listed` を受ける | 例外を上げない。導入元を一度決めたら保持する | 錠のファイル、外部コマンド |
| `Updater.confirm` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `confirm(self) -> Target` | 錠を取らず、更新のコマンドも打たずに、一覧を読み直して起動先を返す（更新しない手が使う）。導入元が未定なら先に `find_origin` で決める | 同上 | 導入元が決まらない・一覧を読めなければ `Target(None, None)`。例外を上げない | 外部コマンド（読むだけ） |
| `Wrapper._update_plugin` | `src/ndf/domains/relay/wrapper.py` | 新設 | `_update_plugin(self) -> Optional[Ended]` | 更新の手を呼んで秒を測り、`plugin_update` の行を書き、`failed` なら端末に 1 行出す。起動先のメジャーバージョンが変わっていれば `_stop_for_major` を返す | 子が動いていない | `log.jsonl` に 1 行増える | `log.jsonl`・端末 |
| `Wrapper._confirm_plugin` | `src/ndf/domains/relay/wrapper.py` | 新設 | `_confirm_plugin(self) -> Optional[Ended]` | 確認の手を呼び、`result: "skipped"`（理由「同じ会話を続ける手では更新しない」）と `launched` を持つ `plugin_update` の行を書く。起動先のメジャーバージョンが変わっていれば `_stop_for_major` を返す | 子が動いていない | `log.jsonl` に 1 行増える | `log.jsonl`・端末 |
| `Wrapper._stop_for_major` | `src/ndf/domains/relay/wrapper.py` | 新設 | `_stop_for_major(self, target: Target) -> Ended` | `install.follow(environ, target.root, target.version)` で `relay.root` を起動先へ向け直し、向け直せたか（戻り値か、`relay.version` が既に起動先のバージョン以上か）で端末の 1 行を選んで出し、`Ended("precondition", ...)` を返す | `target.version`・`target.root` が None でない | 複製があれば `relay.root` が `target.root` を指す | `relay.root`・`relay.version`・端末 |
| `major_changed` | `src/ndf/domains/relay/plugin_update.py` | 新設 | `major_changed(own: str, target: Target) -> bool` | 起動先のバージョン（`target.version`）のメジャーがラッパーのバージョン（`own`）のメジャーと違うか | 無し | `target.version` が None なら偽（起動先が不明なら止めない。設計の I8） | 無し（純粋） |
| `Wrapper._loop` | `src/ndf/domains/relay/wrapper.py` | 変更 | 変わらない | 最初の `_start` の前に、`Settings.first_resume` が偽なら `_update_plugin`、真なら `_confirm_plugin` を呼ぶ（利用者が `--resume` で続けた会話の途中で更新しない。設計の決定 2）。`Ended` が返れば起動せずに `_end` で終える | — | — | — |
| `Wrapper._on_switch` | `src/ndf/domains/relay/wrapper.py` | 変更 | 変わらない | `_stop_child` → `_update_plugin` → `_start` の順にする（`_replace` を使わない）。`_update_plugin` が `Ended` を返せば起動せずにそれを返す | — | 次の子は更新の後に起動する | — |
| `Wrapper._on_resume` | `src/ndf/domains/relay/wrapper.py` | 変更 | 変わらない | `_stop_child` → `_confirm_plugin` → `_start` の順にする（`_replace` を使わない）。`_confirm_plugin` が `Ended` を返せば起動せずにそれを返す | — | 更新のコマンドを打たない | — |
| `wrapper_settings` | `src/ndf/domains/relay/cli.py` | 変更 | 変わらない | `Updater(root=ident.root, cwd=<ラッパーの作業ディレクトリ>, env=session_env.tool_env(environ), timeout=limits.seconds("relay.plugin-update"), lock=<設定の保存先>/ndf/plugin-update.lock)` を組み、その `__call__` を `Settings.update`、`confirm` を `Settings.confirm` に入れる。導入の一覧（`list_plugins`）・2 つのコマンド（`run_plugin`）・確認の一覧は、どれも同じ `cwd` と同じ `env`（したがって同じ設定の保存先 `CLAUDE_CONFIG_DIR`）で打つ。設定の保存先は `signals` の既存の関数（`CLAUDE_CONFIG_DIR` か `~/.claude`）で求める | — | — | 無し（組むだけ） |
| `tool_env` | `src/ndf/domains/relay/session_env.py` | 新設 | `tool_env(environ: Mapping[str, str]) -> dict[str, str]` | `child: drop` の変数と NDF の `src` の `PYTHONPATH` を外した環境を返す（`set` の値は入れない。`CLAUDE_CONFIG_DIR` は外さない） | 無し | `child_env` と同じ外し方 | 無し |
| `mixed` | `src/ndf/domains/relay/hub_decide.py` | 変更 | 変わらない | バージョンが無い（旧い NDF）か `_major` の違うラッパーだけを `other` に数える | 無し | 理由の文の形は今と同じ | 無し |
| `_major` | `src/ndf/domains/relay/hub_decide.py` | 新設 | `_major(version: Optional[str]) -> Optional[str]` | バージョンの最初の `.` の前を返す（バージョンが無ければ None） | 無し | — | 無し |

値の型:

| 型 | フィールド |
| --- | --- |
| `Ran`（`NamedTuple`。`adapters/claude.py` に置き、`plugin_update` が使う） | `code: Optional[int]`・`stdout: str`・`error: str` |
| `Origin`（`NamedTuple`） | `plugin_id: str`（例 `ndf@ai-plugins`）・`marketplace: str`・`scope: str` |
| `Target`（`NamedTuple`） | `version: Optional[str]`・`root: Optional[Path]`（次の子が読む起動先。分からなければ両方 None） |
| `Updated`（`NamedTuple`） | `result: str`・`before: Optional[str]`・`after: Optional[str]`・`reason: str`・`target: Target` |
| `Settings.update` | `Optional[Callable[[], Updated]]`。既定は None（更新しない。既存のテストはこのまま通る） |
| `Settings.confirm` | `Optional[Callable[[], Target]]`。既定は None（確かめない。既存のテストはこのまま通る） |

打つコマンド（2026-11-19 に Claude Code 2.1.328 で形を確かめた）:

| ステップ | `args` |
| --- | --- |
| 導入元を読む・起動先を確かめる | `list_plugins`（`claude plugin list --json`）に `cwd` と `env` を渡す |
| マーケットプレイス | `["marketplace", "update", <marketplace>, "--json"]` |
| NDF | `["update", <plugin_id>, "-y", "--json", "--scope", <scope>]` |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `run_plugin` | 起動できない | `claude` が PATH に無い・`OSError` | `subprocess.run` の例外 | `Ran(None, "", "起動できない: <例外の要約>")` を返す | 無し（呼び出し元が決める） | 次のカットポイントで打ち直す |
| `run_plugin` | 時間切れ | ネットワークの遅れ・大きな複製 | `subprocess.TimeoutExpired` | `Ran(None, "", "<秒> 秒で終わらない")` を返す | 無し | 同上 |
| `find_origin` | 一覧を読めない | `list_plugins` が None | `listed is None` | 理由の文を返す（`Updater` が `failed` にする） | 端末の 1 行 | 次の更新で読み直す（導入元を保持しない） |
| `find_origin` | 一致する導入が無い | `--plugin-dir`・開発のチェックアウト・`@inline`・無効にした導入 | 走査で当たらない | 理由の文を返す（`Updater` が `skipped` にする） | 無し（`log.jsonl` だけ） | 次の更新で読み直す |
| `find_origin` | 一致する導入が 2 件以上 | 同じバージョンを複数のスコープに入れ、どれも同じキャッシュのパスを返す | 走査で 2 件目に当たる | 理由の文「同じ導入先を複数のスコープが指す」を返す（`Updater` が `skipped` にする） | 無し（`log.jsonl` だけ） | 次の更新で読み直す。利用者がスコープを 1 つにすれば更新される |
| `read_update` | マーケットプレイスの更新の失敗 | 終了コードが 0 でない・`code` が None・JSON の `outcome` が `ok` でない | `market` の値 | `Updated("failed", None, None, <理由>)`。NDF の更新は打たない | 端末の 1 行 | 次のカットポイントで打ち直す |
| `read_update` | NDF の更新の失敗 | 終了コードが 0 でない・標準出力に JSON として読める行が無い（読むのは JSON として読める最後の行）・`outcome` が `ok` でない | `plugin` の値 | `Updated("failed", ...)`。理由は `failureCode` と `message` の 1 行目（パスを含まない形に切る） | 端末の 1 行 | 同上 |
| `read_update` | 結果が矛盾する・知らない値 | 「処理の手順」の表の `updated`・`up_to_date` のどちらにも当たらない組（`up_to_date` なのにバージョンが違う、`updated` なのに同じ、知らない `updateOutcome`、バージョンが文字列でない） | 表の照合 | `Updated("failed", <読めた old>, <読めた new>, "読めない結果: updateOutcome=<値>")` | 端末の 1 行 | 同上（起動先は `Updater` が一覧で確かめる） |
| `Updater.__call__` | 錠を取れない | 別のラッパーが上限を超えて更新している | `signals.held` が偽 | 更新のコマンドを打たず、起動先だけ一覧で確かめて `Updated("failed", None, None, "別のラッパーの更新を <秒> 秒待っても終わらない", <target>)` | 端末の 1 行 | 同上 |
| `Updater.__call__` | 予期しない例外 | 錠のファイルを作れない（`OSError`）など | `except OSError` | `failed` に畳み、起動先は一覧で確かめる | 端末の 1 行 | 同上 |
| `Updater.__call__`・`Updater.confirm` | 起動先を確かめられない | 更新の後の一覧が読めない・導入元の行が消えた | `find_target` が `Target(None, None)` | 更新の結果はそのまま、`target` を不明にする | `failed` のときの 1 行が「不明なバージョンで次のセグメントを起動する」になる | 子を起動する（設計の I8。メジャーバージョンの違いを見逃しうることは設計の「未確認のまま残ること」） |
| `Wrapper._stop_for_major` | メジャーバージョンが変わった | 上がったバージョンのリリース（自分の更新でも、別のレーンの更新でも） | `major_changed` | `relay.root` を起動先へ向け直し、次の子を起動せずに止める | 設計文書の「端末の 1 行」の 2 つのどちらか。ラッパーは停止理由 `precondition`（終了コード 3） | 利用者が `claude` を打ち直すと、複製 `relay.py` が向け直した `relay.root` から新しいバージョンのラッパーを起動する。複製が無ければ案内の `bin/ndf relay install` の後に打ち直す |
| `Wrapper._update_plugin` | 更新の手が `failed` を返した | 上のどれか | `result == "failed"` | 記録と端末の 1 行を出し、メジャーバージョンの判定を通してから次の子を起動する | `ndf-relay: プラグインを更新できなかった（<理由>）。<launched か「不明なバージョン」> で次のセグメントを起動する`。ラッパーの終了コードは変わらない | 無し |

## 処理の手順

`read_update` の分類（上から順に当て、最初に当たった行で決める。行どうしは条件が重ならない）。許す値は 2026-11-19 の実測で見た `outcome` の `ok`・`failed` と `updateOutcome` の `up_to_date`、および新しいバージョンへ上げたときの `updateOutcome` である。新しいバージョンへ上げたときの値は、手元に新しいバージョンが無く実測していない。実装の前に、手元のマーケットプレイスで古いバージョンを入れてから更新して値を確かめ（`AGENTS.md` の「外部コマンドの挙動は書く前に実行して確かめる」）、下の `<上げたときの値>` を実測の値で書く。

| # | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| 1 | `market.code != 0`・`code` が None・最後の行の JSON の `outcome != "ok"` | `failed`（`plugin` は見ない） |
| 2 | `plugin.code != 0`・`code` が None・JSON の行が無い・`outcome != "ok"` | `failed` |
| 3 | `oldVersion`・`newVersion` のどちらかが文字列でない | `failed`（読めない結果） |
| 4 | `updateOutcome == "up_to_date"` かつ `oldVersion == newVersion` | `up_to_date`（`before`・`after` は `oldVersion`・`newVersion`） |
| 5 | `updateOutcome == <上げたときの値>` かつ `oldVersion != newVersion` | `updated` |
| 6 | 上のどれにも当たらない（`up_to_date` なのにバージョンが違う・上げたのにバージョンが同じ・知らない `updateOutcome`） | `failed`（読めない結果。`before`・`after` は読めた値） |

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `Updater.__call__` | 導入元が未定で `find_origin` が `Origin` を返した | 保持して 2 つのコマンドへ進み、続けて起動先を確かめる |
| `Updater.__call__` | 導入元が未定で、一覧は読めたが一致が 0 件か 2 件以上 | `skipped`・起動先は不明。コマンドを打たない |
| `Updater.__call__` | 導入元が未定で、一覧を読めない | `failed`・起動先は不明。コマンドを打たない |
| `Updater.__call__` | 錠を取れない | `failed`。コマンドを打たず、導入元が決まっていれば起動先を確かめる |
| `Wrapper._update_plugin`・`_confirm_plugin` | 起動先のバージョンがあり、メジャーがラッパーと違う | `_stop_for_major`（更新の結果が `failed` でも止まる） |
| `Wrapper._update_plugin`・`_confirm_plugin` | 起動先が不明、または同じメジャー | 次の子を起動する |
| `mixed` | 他のラッパーのバージョンが None | 断る |
| `mixed` | 他のラッパーの `_major` が自分と違う | 断る |
| `mixed` | すべて同じ `_major`（バージョンの残りは違ってよい） | 進める |

- 繰り返しの終わる条件: `find_origin`・`find_target` は一覧の要素を 1 度だけ走査する
- 計算量の注意: 無し（一覧は数十件）

## 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `relay.plugin-update` | 設定値（`seconds`） | `contracts/limits.json` | 120（更新のコマンド 1 回ずつと錠の待ちの上限。導入の一覧は既存の `PLUGIN_LIST_TIMEOUT` の 30 秒） |
| `relay-plugin-update` | 保存先（`root: config`・`path: ndf/plugin-update.lock`） | `contracts/domains/locations.json` | — |
| `MAJOR_CHANGED_LINE` | 定数（端末の 1 行のテンプレート） | `src/ndf/domains/relay/wrapper.py` | `ndf-relay: メジャーバージョンが {own} から {launched} に変わった。次のセグメントを起動しない。claude を打ち直す` |
| `MAJOR_CHANGED_INSTALL_LINE` | 定数（端末の 1 行のテンプレート。複製が無い・向け直せないとき） | `src/ndf/domains/relay/wrapper.py` | `ndf-relay: メジャーバージョンが {own} から {launched} に変わった。次のセグメントを起動しない。{root}/bin/ndf relay install を打ってから claude を打ち直す` |
| `UPDATE_FAILED_LINE` | 定数（端末の 1 行のテンプレート） | `src/ndf/domains/relay/wrapper.py` | `ndf-relay: プラグインを更新できなかった（{reason}）。{launched} で次のセグメントを起動する`（`launched` が不明なら「不明なバージョン」） |
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
| `Wrapper._update_plugin`・`_confirm_plugin` | `{"kind": "plugin_update", "result", "from", "to", "launched", "reason", "seconds"}`（`at`・`section` は既存の `_log` が付ける） | `<状態の保存先>/relay/wrappers/<名前>/log.jsonl` | 導入先のパス・コマンドの全出力・環境変数 |
| `Wrapper._update_plugin` | `failed` のときの 1 行 | 端末（`Settings` の `out`） | 同上 |
| `Wrapper._stop_for_major` | メジャーバージョンが変わったときの 1 行 | 端末（`Settings` の `out`） | コマンドの全出力・環境変数（導入先のパスは複製が無いときの案内だけに出す） |

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
| `find_origin` | `installPath` が根と一致する `ndf@ai-plugins` を選ぶ | 同じ ID が user と project の 2 つのスコープにあり、`installPath` が一致するほうだけを選ぶ。`installPath` が symlink 越しでも解決して一致する。一致する行が `enabled: false` なら選ばない | 一覧が None・空・`ndf@` が無い・一致しない（`@inline`）で理由の文。同じバージョンを 2 つのスコープに入れ、両方の `installPath` が根と一致するときは選ばずに理由の文 |
| `find_target` | 導入元と同じ `id`・`scope` の行の `version`・`installPath` を返す | 別のスコープに同じ ID の行があっても、導入元のスコープの行を返す | 一覧が None・該当の行が無い・`version` が文字列でないで `Target(None, None)` |
| `read_update` | `up_to_date` と `updated`（実測の JSON の行を入力にする。`updated` は実装の前に実測した値） | 出力の前に人向けの行があり、最後の行だけが JSON | 終了コード 1 と `failureCode`・`code` が None・JSON でない最後の行・`market` の失敗で `plugin` を見ない。`up_to_date` なのにバージョンが違う・上げたのにバージョンが同じ・知らない `updateOutcome`・バージョンが無いで `failed` |
| `Updater.__call__` | 2 回呼ぶと導入元の一覧は 1 回だけ読む。コマンドが順に 2 つ打たれ、その後に確認の一覧が読まれて `target` が入る。一覧と 2 つのコマンドが同じ `cwd`・`env` で打たれる | 1 回目に一致が無ければ 2 回目に読み直す。NDF の更新の出力が JSON でない（`failed`）でも、確認の一覧の版が `target` に入る | 錠を持たれている間は上限で `failed`、更新のコマンドを打たず、確認の一覧だけ読む |
| `Updater.confirm` | 錠を取らず、更新のコマンドを打たずに一覧だけを読んで `Target` を返す | 導入元が未定なら先に決める | 一覧を読めなければ `Target(None, None)` |
| `Wrapper._update_plugin`・`_confirm_plugin`（`Wrapper` 経由） | 最初の起動の前と `switch` の後に更新が 1 回ずつ呼ばれ、`plugin_update` の行が増える | `account`・`restart` と `first_resume` が真の最初の起動では更新が呼ばれず、確認だけが呼ばれる。`Settings.update`・`confirm` が None なら呼ばれず行も増えない | `failed` でも次の子が起動し、端末に 1 行出る。確認した起動先が別のメジャーなら、更新が `failed` でも・更新しない手でも次の子を起動せず `precondition` で終わる。起動先が不明なら起動し `launched` が null |
| `Wrapper._stop_for_major`（`Wrapper` 経由） | `ndf relay install` で偽の `CLAUDE_CONFIG_DIR` に複製を置いた状態で止まると、`relay.root` が起動先の導入先を指す。続けて複製を打つと起動先の `bin/ndf` が呼ばれる | 複製が無いときは向け直さず、`bin/ndf relay install` を添えた 1 行を出す | — |
| `major_changed` | `11.0.7` → `11.1.0` は偽 | `target.version` が None は偽 | `11.0.7` → `12.0.0` は真 |
| `mixed` | 同じメジャーでバージョンの違うラッパーを断らない | `11.0.0-dev.4` と `11.1.0` を同じメジャーと読む | バージョンが None・`10.17.51` と `11.0.8` で断る |
