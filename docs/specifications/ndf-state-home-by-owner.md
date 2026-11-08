# NDF の状態の置き場: 持ち主ごとに 3 つの根へ分ける

NDF がリポジトリの外に書く状態は、持ち主（チェックアウト・利用者・コンテナのプロセス）ごとに 3 つの根へ分けて置く。
コンテナを作り直しても耐久ワークフローの記録と台帳が消えず、同じパスの別のチェックアウトの記録が混ざらない。
旧の版の置き場（`~/.local/state/ndf`）の台帳と記録は読まず、更新したときに `state-home.py migrate` で一度だけ移す。

例: devbase の `ai-plugins-dev-1` と `ai-plugins-dev-2` は `~/.claude`（volume `devbase_home_personal`）を共有し、`/work` は別の
volume（`devbase_work_1`・`_2`）で、どちらにも `/work/ai-plugins` がある。両方で並列の計画 `p3` を流したとき:

| 状態 | dev-1 の置き場 | dev-2 の置き場 | 結果 |
| --- | --- | --- | --- |
| 耐久の記録 `dbos/`・プランの状態 `sv/` | dev-1 の `/work/ai-plugins/.git/ndf/state` | dev-2 の `/work/ai-plugins/.git/ndf/state` | 鍵は同じ絶対パスから作られるが、ファイルが別の volume にあるので混ざらない。コンテナを作り直しても残る |
| 問いの台帳 `ask.jsonl`・MVV の記録など | `~/.claude/ndf/state`（共有） | 同じ | 追記だけなので、両方の行が 1 つの台帳に集まり集計に使える |
| relay・待ちの通知・guard・context | dev-1 の `~/.local/state/ndf` | dev-2 の `~/.local/state/ndf` | pid を持つため、コンテナごとに別のまま |

| 何を | 正本 |
| --- | --- |
| 3 つの根の解決・利用者の根に置く台帳の一覧（`USER_KINDS`）・台帳の移し方 | [`scripts/lib/state_home.py`](../../plugins/ndf/scripts/lib/state_home.py) の冒頭の説明と関数 |
| チェックアウトの記録（`dbos/`・`sv/`）の移し方 | [`scripts/lib/state_checkout.py`](../../plugins/ndf/scripts/lib/state_checkout.py) |
| シェルの同じ解決（`stages`） | [`skills/development-workflow/scripts/lib/workflow-common.sh`](../../plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh) の `ndf_user_state_home` |
| 利用者向けの移行の手順 | [NDF の README](../../plugins/ndf/README.md) の Slack 通知の節の後（「NDF がリポジトリの外に書く状態は」） |

この文書が扱うのは、決定の理由、常に成り立つ条件、`state-home.py` の入出力の契約、テスト観点である。

## 背景

すべての状態を `~/.local/state/ndf` に置いていた。devbase では `~/.local` は volume ではなくコンテナの書き込み層にあり、
EC2 の再起動や `docker stop` → `start` では残るが、コンテナを作り直すと消える。2026-11-07 の `ai-plugins-dev-1` では、
並列の計画 `p3` の耐久の記録・プランの状態 `sv/`（361 MB）・台帳など合計約 720 MB がここにあった。消えると `p3` は最初からになる。

永続の `~/.claude`（`/persistent/group/.claude` への symlink）へ丸ごと移すと、`~/.claude` を共有するコンテナの間で 2 つが混線する。

| 混線 | 起きること |
| --- | --- |
| パスで鍵を作る記録 | `parallel_run.py` の鍵は `record_identity(<メインの絶対パス>, <計画名>)`、`sv/` も絶対パスから鍵を作る。別の volume の同じパスのチェックアウトが同じファイルになり、再開が他方の進行を拾う |
| pid | relay の登録簿と hub の持ち主（`relay_lib/hub.py` の `hub.json`）を、別のコンテナの pid で判定する。pid の名前空間はコンテナごとに別 |

## 決定と理由

| # | 決定 | 理由 |
| ---: | --- | --- |
| 1 | 持ち主ごとに 3 つの根へ分ける。チェックアウトの記録は `<git の共通ディレクトリ>/ndf/state`、追記だけの台帳は `~/.claude/ndf/state`、pid とセッションを持つ状態は `~/.local/state/ndf` | 鍵がパスから作られる記録はチェックアウトに、追記だけの台帳は利用者に、pid はコンテナに属する。持ち主の寿命と共有の範囲に置き場を合わせると、上の 2 つの混線が起きない。git の共通ディレクトリは worktree の間で共有され、`/work` の volume で永続する |
| 2 | `NDF_STATE_HOME` か `$XDG_STATE_HOME/ndf` を指定すると、3 つとも その 1 つの根になる。各置き場の個別の上書き（`NDF_DBOS_DIR`・`NDF_SV_STATE_DIR`・`NDF_METRICS_DIR`・`NDF_MVV_STATE_DIR`・`CLAUDE_PLUGIN_DATA` など）は呼ぶ側が先に見る | テストの隔離と明示の指定は、1 つの値で全部を動かせるほうが漏れが無い |
| 3 | `CLAUDE_CONFIG_DIR` を利用者の根の決め方に使わない | relay がアカウントごとの子へ別の値を渡すため、同じ利用者の台帳が割れる |
| 4 | 旧の置き場を読まない。更新したら各コンテナで一度 `state-home.py migrate` を打って移す | 2026-11-08 の利用者の判断「一度このマシンのコンテナで全て対処すれば、毎回チェックする必要がなくなる」。旧を読み続けると、読む側すべてに新旧の分岐が残る |
| 5 | 動いている耐久ワークフロー（旧の `dbos/` のロックが 1 つでも持たれている）があれば、台帳も含めて何も移さずに止まる。鍵は付け替えない | 動いている記録を移すと、走っているプロセスと移した記録が分かれる。鍵と耐久ワークフローの ID は旧の置き場のパスからできており、付け替えると記録の中身と食い違う。代わりに移行の前の queue は `--resume` で続けず新しく流す |
| 6 | 台帳は足し合わせ、旧は消さずに `<名前>.migrated` として残す。次の migrate が、新に中身がすべて入った `.migrated` を消す | 旧の版のプロセスが名前を外した後も開いていた fd へ書き足すことがある。残しておけば次の回が拾える |
| 7 | 解決は言語ごとに 1 箇所（Python は `lib/state_home.py`、シェルは `ndf_user_state_home`）。`find_git_dir` も `state_home` に置く | hook から schema を読まずに使え、置き場の規則が呼ぶ側に散らない |

## 仕様

### 3 つの根の解決

| 根 | 関数 | 解決の順 |
| --- | --- | --- |
| チェックアウト | `checkout_home(start, env)` | 明示の根 → `start`（省けば作業ディレクトリ）から `.git` を上へ探した git の共通ディレクトリの `ndf/state` → git の外ならコンテナの根 |
| 利用者 | `user_home(env)` | 明示の根 → `$HOME/.claude/ndf/state` |
| コンテナのプロセス | `container_home(env)` | 明示の根 → `$HOME/.local/state/ndf` |

明示の根は `NDF_STATE_HOME`、無ければ `$XDG_STATE_HOME/ndf` である（空の値は無いものとする）。`find_git_dir` は `git` を起動せず、
`.git` のディレクトリかファイル（`gitdir:`）と `commondir` を読む。

| 根 | 置くもの |
| --- | --- |
| チェックアウト | `dbos/`（耐久の記録）・`sv/`（プランの状態） |
| 利用者 | `USER_KINDS`: `ask.jsonl`・`mvv-gate.jsonl`・`project-mvv.jsonl`・`project-mvv-signals.jsonl`・`gate-rejections.jsonl`・`project-mvv/`・`metrics/`・`usage/`・`checks/`・`stages/`・`estimate/`・`test-run/`（`estimate`・`test-run` は git の外の分。git の中では `<git の共通ディレクトリ>/ndf/estimate`・`ndf/test-run` に置く） |
| コンテナのプロセス | `relay/`・`wait-notify/`・`guards/`・`context/` |

### 移行（`migrate`）

`state_checkout.migrate_all` が `state_home.migrate` にチェックアウトの記録の移し方を渡す。

| 順 | 起きること |
| --- | --- |
| 1 | `NDF_STATE_HOME` を指定していれば `none` で終わる。`XDG_STATE_HOME` を指定していれば、移し先を `$XDG_STATE_HOME/ndf` にし、旧の版が XDG を見ずに固定の旧の根へ書いていた台帳（`FIXED_KINDS`）だけを移す（チェックアウトの記録は移さない）。XDG の根が旧の根と同じなら `none` |
| 2 | 利用者の根のロック `.migrate.lock` を待たずに取る。取れなければ（別のコンテナの migrate が動いている）`busy` |
| 3 | 旧の `dbos/` の `*.lock` を 1 つずつ待たずに取る。持たれているものが 1 つでもあれば `busy`（`held` にロックのパス） |
| 4 | 止まった前回の migrate の残り（名前を外した `<名前>.migrating-<n>`・一時ファイル `.<名前>.tmp`）を元の台帳へ戻す |
| 5 | 台帳を利用者の根へ足し合わせる。`.jsonl` は旧を `<名前>.migrated`（既にあれば `-<n>`）へ改名してから、新に無い行（同じ行は数で比べる）を足し、時刻（`at`）の順に並べて書き直す。ほかのファイルは新に無ければ移し、同じ名前があれば旧に残す（`kept`） |
| 6 | 打った作業ツリーのチェックアウトの根へ `dbos/`・`sv/` を移す。作業ツリーの外なら移さず `checkout.reason` に理由を書く |

チェックアウトの記録の移し方（`state_checkout`）:

- `dbos/` は鍵ごとに、移し元と移し先の同じ鍵の `.lock` を待たずに取り、持っている間に移し元の WAL を本体へ書き戻し
  （`PRAGMA wal_checkpoint(TRUNCATE)`）、本体 `<鍵>.sqlite` だけを移し先の一時名へ写して名前を替え、移し元を消す。
  ロックが取れない・移し先に同じ鍵がある・WAL を書き戻せないときは移さず `kept` に理由を書く
- `sv/` はプランごとのディレクトリを移す。移し先に同じ名前があれば旧に残す

### 常に成り立つ条件

- **旧の置き場の台帳・耐久の記録・プランの状態を、実行時に読まない。** 読むのは `migrate` だけである
- **動いている耐久ワークフローの記録を動かさない。** 旧の `dbos/` のロックが 1 つでも持たれていれば何も移さない。移すときも鍵ごとに両方のロックを持つ
- **途中で止まっても記録を失わない。** 台帳は名前を外したものと一時ファイルが次の回で戻り、耐久の記録は完全な本体が旧か新の一方に残る
- **台帳の行を 2 度足さない。** 何度打っても、新に既にある行は足さない
- **台帳の権限を広げない。** 書き直した台帳は、移し元と移し先の厳しい方の権限になる（`ask.jsonl` の 0600 を保つ）
- **コンテナの状態を動かさない。** `relay/`・`wait-notify/`・`guards/`・`context/` は旧の置き場に残る

## 入出力の契約（`state-home.py`）

| 副命令 | 出力（統一 JSON レポートの 1 行） |
| --- | --- |
| `path` | `metrics` に `checkout`・`user`・`container` の 3 つのパス |
| `migrate` | `metrics.result` が `migrated`（移した）・`none`（移すものが無い・明示の根を使う）なら `status` は `ok`、`kept`（移し先に同じ名前があって旧に残したものがある）・`busy`（ロックで止まった）なら `stopped`。`items` に止めたロック・旧に残した台帳・移した／残した `dbos`・`sv` を 1 件ずつ載せる。`metrics` に `from`・`to`・`moved`・`checkout`・`dbos_moved`・`dbos_kept`・`sv_moved`・`sv_kept`・`note` |

## 運用

- NDF を更新したら、各コンテナで動いているプラン（`supervise.py`・`parallel.py run`）を止めてから、主のリポジトリの作業ツリーで
  `state-home.py migrate` を一度打つ。打ち方は NDF の README にある
- 旧の版のまま動いているセッションは、更新されるまで旧の置き場へ書き続ける。更新した後にもう一度打てば、その分も移る
- `~/.cache/ndf`・`/tmp/ndf*`・`mcp-serena` の置き場、`context/` の判定の記録の扱い、外のサービスの実行ジョブでの保存はこの仕様の範囲の外で、#2606 が扱う

## テスト観点

- 環境変数が無いとき、`dbos`・`sv` は作業ディレクトリのチェックアウトの `<git の共通ディレクトリ>/ndf/state`、台帳は `~/.claude/ndf/state`、relay などは `~/.local/state/ndf` に解決されること
- 同じパスの別のチェックアウト（別の volume）の記録が混ざらないこと
- 旧の置き場の台帳・耐久の記録・プランの状態を読まず、`migrate` が一度にすべて移し、動いている耐久ワークフローがあれば何も移さずに止まること
- `NDF_STATE_HOME`・`XDG_STATE_HOME`・各置き場の個別の上書きが効くこと
- `migrate` が台帳を時刻の順に足し合わせ、権限を保ち、旧を `.migrated` として残し、コンテナの状態を動かさず、打ち直しても足し直さないこと
- 単体テストは [`scripts/tests/test_state_home.py`](../../plugins/ndf/scripts/tests/test_state_home.py) にある

## 関連リンク

- [#2766](https://github.com/devbasex/ai-plugins/issues/2766)（この仕様の起点）・[#2606](https://github.com/devbasex/ai-plugins/issues/2606)（置き場を寿命で分ける）
- [実行をまたいで残る状態の持ち主と片付け](ndf-run-state-owner-and-sweep.md)
