# #4101: コードが読み書きする環境変数がすべて環境変数の契約に載り、載せ漏れを検査が落とす

正は課題 #4101 の本文で、この文書はその写しに調べた結果を足したものである。「何が起きたか」「受け入れ条件」は本文の同じ節の写しで、本文から作り直す。手で直さない。

NDF のコード（`plugins/ndf/src`）が読み書きする環境変数を、環境変数の契約 `plugins/ndf/contracts/domains/env.json` にすべて載せる。各行は意味・書き手・読み手・子へ渡すか（`child`）を持つ。契約に無い名前の読み出しと、読み手も書き手も無い契約の行を `ndf check` が落とす。

## 何が起きたか

`plugins/ndf/src` に現れる `NDF_` で始まる名前のうち 34 件が、環境変数の契約に無い（2026-11-22、`origin/main` の 428f91f9e で数えた）。

```bash
git grep -ohE "NDF_[A-Z0-9_]+" -- plugins/ndf/src | LC_ALL=C sort -u   # 63 件
# 契約の id（65 件）と比べて、契約に無いもの 34 件
```

1 件ずつ確かめると、34 件は次の 4 つに分かれる（一覧は下の「名前の一覧」）。

| 分類 | 件数 | 例 |
| --- | --- | --- |
| NDF が読む環境変数 | 17 | `NDF_RUN_ID`・`NDF_WORKER_KIND`・`NDF_METRICS_DIR`・`NDF_RELAY`（うち `NDF_RELAY_LAUNCH` は #4062 が載せる） |
| NDF が書いて、NDF の外のプログラムが読む環境変数 | 9 | テスト環境の compose の定義へ渡す `NDF_SLOT`・`NDF_PORT_<役割>`、公開のコマンドへ渡す `NDF_EXPOSE_URL` |
| 環境変数でない名前 | 7 | Python の定数の名前 `NDF_SCHEMA`・古い案内の印 `NDF_PLUGIN_GUIDE_START`・GitHub Actions の secret の名前 `NDF_RELEASE_TOKEN` |
| 名前の一部 | 1 | f 文字列の頭 `NDF_PORT_` |

`NDF_PORT_HTTP` は `NDF_PORT_<役割>` の例として docstring にだけ現れ、上の 9 件の 1 つに数える。

調べる中で、ほかに 3 つが分かった。

- **`NDF_` で始まらない名前にも載せ漏れがある。** 読み出しの受け手（`os.environ`・引数の `environ`・`env`・`ctx.env`）と名前の定数を解いて数えると、契約に無い名前は 31 件で、そのうち 16 件が `NDF_` で始まらない（`GITHUB_TOKEN`・`XDG_CACHE_HOME`・`CLAUDE_CODE_OAUTH_TOKEN` など）。lracct のブランチは、これに `ANTHROPIC_CONFIG_DIR` を足す
- **今の検査は名前の字を直に `os.environ` へ渡す読み方しか見ない。** 検査 `spec-io`（`plugins/ndf/src/ndf/checks/spec_docs.py` の `env_reads`）は、`os.environ[...]`・`os.environ.get(...)`・`os.getenv(...)`・`... in os.environ` の名前の字だけを見る。名前を定数に置く書き方（`ENV_RUN_ID = "NDF_RUN_ID"` を 14 か所）と、`environ`・`env` の引数で受ける書き方はすり抜ける
- **契約に読み手も書き手も無い行が 4 つある。** `NDF_RUNTIME_SESSION`・`NDF_CLAUDE_API_KEY_NAME`・`NDF_CLAUDE_API_KEY_GENERATION`・`NDF_CLAUDE_KEY_ACCOUNT` は、`plugins/ndf` の下のコード・手順書・ほかの契約のどこにも現れない（`git grep -lw <名前> -- plugins/ndf ':!plugins/ndf/docs' ':!plugins/ndf/contracts/domains/env.json'` が 0 件）

## 種類

不具合の修正（契約の載せ漏れと、それを落とす検査の穴）。検査の規則が 1 つ増え、契約の列が 1 つ増える。

## 対象範囲

含む:

- 「名前の一覧」で「載せる」とした名前の `env.json` の行と、読み手も書き手も無い 4 行の削除
- 環境変数の読み書きを見る検査（今の `spec-io` の環境変数の部分を広げる）
- 検査が通るためのコードの直し（名前を写した最上位の定数を外し、名前の組は契約から引く）
- 生成文書 `plugins/ndf/docs/contracts/env.md` と、検査の規則の表 `plugins/ndf/src/ndf/checks/README.md`

含まない:

- 中継の 3 つの変数（`NDF_RELAY_DIR`・`NDF_RELAY_LAUNCH`・`NDF_RELAY_SESSION`）の行と、ラッパーが渡す名前と契約を結ぶテスト（#4062・#4049）
- 接続の提供元と認証の変数の行の形（`provider` の列・`child` の `account-set`・`account-drop`）。中継のアカウントの切り替え（lracct。#3604・#3864）が決める。lracct の後にまだ載っていない名前は、この課題が lracct の形で載せる
- 名前が式で決まる読み出し（関数の引数で名前を受ける `limits.from_env(environ, name, ...)` など）の名前の追跡。名前の字が現れる所（呼ぶ側の字・ほかの契約の `env` の欄）で確かめる
- `NDF_LOOP_EXCLUDE` の読み手が無いこと（契約は「開発ループの選定から外す」と書くが、読むコードが無い。範囲の外として列挙する）
- Python の定数の名前（`NDF_SCHEMA` など）の改名

## 前提

- 前提 1: #4062 の実装の後に実装する。#4062 は `NDF_RELAY_LAUNCH` の行を足し、`NDF_RELAY_DIR`・`NDF_RELAY_SESSION` の行を `child: drop` にする
- 前提 2: lracct（#3604・#3864）の実装の後に実装する。lracct は `env.json` に `provider` の列と 13 行・`NDF_RELAY_SPIN` の行を足し、`NDF_RELAY_MAX_STARTS` の行を消し、`child` に `account-set`・`account-drop` を足す
- 前提 3: 検査 `const-fact`（`plugins/ndf/src/ndf/checks/facts.py`）は、事実の表の `id` と同じ字を持つ最上位の定数を落とす。`env.json` に行を足すと、その名前を写した定数が落ちる（2026-11-22 に写しで確かめた。`NDF_RUN_ID` の行を足すと `run_log.py:29 ENV_RUN_ID` が `table-str` で落ちる）
- 前提 4: NDF は普及のフェーズの前で、環境変数の名前と契約の形を変えても互換の経路を持たない（MVV 版 11 の Value 4）

## 名前の一覧

列の意味: **読み手**は値を読むコード（`plugins/ndf/src/ndf/` からの相対）、**書き手**は値を入れる者（`set_by` の語彙: user・ndf・runtime・ci・service）、**子へ**は中継が起動する子のセッションへの渡し方（`child`。空はそのまま渡す）。

### 載せる: NDF が読む環境変数（16 件。`NDF_RELAY_LAUNCH` は #4062）

| 名前 | 読み手 | 書き手 | 子へ |
| --- | --- | --- | --- |
| `NDF_DOCKER_COMMAND` | `domains/worktree/container.py`（コンテナの実行系のコマンド。既定は `docker`） | user | |
| `NDF_LAUNCH_SLOT_PARENT` | `slot_pool.py`（子孫が親のスロットを受け継ぐ） | ndf（`slot_pool` がスロットを持つ起動の子へ） | |
| `NDF_METRICS_DIR` | `domains/crossrefactor/allocation.py`（記録の根の差し替え） | user | |
| `NDF_PARALLEL_POLL` | `progress/plan/run.py`（計画を読み直す間隔の秒） | user | |
| `NDF_RELAY` | `domains/relay/claude_args.py`（`0` ならラッパーを挟まない） | user | |
| `NDF_RUN_ID` | `adapters/run_log.py`（子が親の実行を `parent_id` で指す） | ndf（`run_log` が子の起動へ） | drop |
| `NDF_RUN_LOG` | `adapters/run_log.py`（子が親と同じ記録へ書く） | ndf（同上） | drop |
| `NDF_RUN_LOG_SINK` | `adapters/run_log.py`（`file`・`stderr`） | user | |
| `NDF_SLACK_API_BASE` | `adapters/notify.py`（Slack の API の送信先。テストの相手） | user | |
| `NDF_STAGE_INPUT` | `progress/invoke.py`（script の作業が受ける工程の入力の JSON） | ndf（`invoke` が script の作業へ） | drop |
| `NDF_STAGE_NOTES` | 工程の script の作業（変更要求の指摘の本文） | ndf（同上） | drop |
| `NDF_STATUSLINE_AUTH_CACHE` | `domains/statusline/statusline.sh`（認証の表示のキャッシュの保存先） | user | |
| `NDF_TEST_SLOTS_DIR` | `testing/slots.py`（テストのスロットの保存先） | user | |
| `NDF_TEST_SLOT_HELD` | `testing/slots.py`・`adapters/launch_slot.py`（スロットの中の子は取らない） | ndf（`testing/slots` がスロットの中の子へ） | |
| `NDF_WORKER_KIND` | `domains/hooks/guards/full_test.py`（worker の中で全体テストを止める） | ndf（`core/worker.py` が worker の起動の間） | drop |
| `NDF_WORKTREE_BASE` | `core/converge/location.py`（収束ループの worktree の保存先） | user | |

### 載せる: NDF が書いて、外のプログラムが読む環境変数（9 件）

| 名前 | 読み手 | 書き手 | 子へ |
| --- | --- | --- | --- |
| `NDF_ENVIRONMENT`・`NDF_SLOT`・`NDF_WORKTREE`・`NDF_SHARED_NETWORK` | 利用者のテスト環境の compose の定義 | ndf（`domains/worktree/testenv_decide.py`） | |
| `NDF_PORT_<役割>` | 同上（`${NDF_PORT_HTTP}` のように役割ごと） | ndf（同上） | |
| `NDF_EXPOSE_URL`・`NDF_EXPOSE_HOST`・`NDF_EXPOSE_ENVIRONMENT`・`NDF_EXPOSE_SLOT` | 設定の `open_command`・`close_command` | ndf（`domains/worktree/testenv_sweep.py`） | |

### 載せる: `NDF_` で始まらない名前（17 件。この課題が 12 件・lracct が 5 件）

| 名前 | 読み手 | 書き手 | 載せる課題 |
| --- | --- | --- | --- |
| `GH_REPO` | `domains/hooks/strict.py`（gh が `-R` の代わりに読む対象のリポジトリ） | user | この課題 |
| `GITHUB_ACTIONS` | `testing/slots.py`（CI の中ではスロットを取らない） | ci | この課題 |
| `GITHUB_TOKEN` | `adapters/github.py`（`GH_TOKEN` が無いときの GitHub のトークン） | user | この課題 |
| `PWD` | `domains/relay/cli.py` | runtime | この課題 |
| `SLACK_USER_MENTION` | `adapters/notify.py`（通知でメンションする利用者） | user | この課題 |
| `XDG_CACHE_HOME` | `domains/hooks/env.py`・`domains/relay/session_env.py` | user | この課題 |
| `CODEX_API_KEY`・`OPENAI_API_KEY` | `adapters/training_cache.py`（Codex の認証の上書き） | user | この課題 |
| `ANTHROPIC_MODEL`・`AWS_PROFILE`・`AWS_REGION` | `domains/relay/account_metered.py`（Bedrock の従量の接続の設定） | user | この課題 |
| `ANTHROPIC_CONFIG_DIR` | lracct が足す `domains/relay/account_dir.py`（lracct のブランチ `sprint/3604-3864-relay-accounts` の 428 行目） | user | この課題 |
| `ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN`・`CLAUDE_CODE_USE_BEDROCK`・`CLAUDE_CODE_USE_FOUNDRY`・`CLAUDE_CODE_USE_VERTEX` | `adapters/training.py`・`adapters/training_cache.py` | user | lracct（lracct のブランチの `env.json` に 5 件ともある） |

### 載せない: 環境変数でない名前（8 件）

| 名前 | 何か |
| --- | --- |
| `NDF_COMMAND`・`NDF_MARK` | `domains/statusline/settings.py` の Python の定数の名前 |
| `NDF_SCHEMA` | `progress/definition.py` の Python の定数の名前 |
| `NDF_SETTINGS` | `contracts/limits.py`・`contracts/version.py` の Python の定数の名前 |
| `NDF_SRC` | `domains/relay/session_env.py` の Python の定数の名前 |
| `NDF_PLUGIN_GUIDE_START` | 古い NDF が指示書に書いた案内の印（`domains/project/measure_repo.py` が探す字） |
| `NDF_RELEASE_TOKEN` | GitHub Actions の secret の名前。NDF へは `GH_TOKEN` として入る（`.github/workflows/release-cd.yml`） |
| `NDF_PORT_` | `NDF_PORT_<役割>` を作る f 文字列の頭 |

### 消す: 読み手も書き手も無い契約の行（4 件）

`NDF_RUNTIME_SESSION`・`NDF_CLAUDE_API_KEY_NAME`・`NDF_CLAUDE_API_KEY_GENERATION`・`NDF_CLAUDE_KEY_ACCOUNT`。実装の時点で読み手か書き手ができていれば消さない（受け入れ条件 5 の検査が決める）。

## 受け入れ条件

1. 「名前の一覧」の「載せる」の名前（lracct が載せたものを含む）が `env.json` の行にあり、`area`・`set_by`・`type`・`secret`・`meaning` を持つ。`meaning` は読み手と書き手を書く。一覧で「子へ」が `drop` の行は `child: drop` を持つ
2. `env.json` に無い名前を、次のどの書き方で読んでも `ndf check` が落とす。名前の字を `os.environ` へ渡す（今の検査）・`environ`・`env`・`ctx.env` の受け手へ渡す・同じモジュールか `ndf` のモジュールの最上位の定数に置いて渡す・最上位の定数の組を回す変数で渡す
3. `plugins/ndf/src` の Python で、字の全体が `NDF_` で始まる名前の形の文字列（辞書の鍵・`NAME=値` の頭・f 文字列の頭を含む）が `env.json` に無ければ、`ndf check` が落とす。環境変数でない字は、理由を添えて検査の規則のデータに挙げたものだけを通す
4. `plugins/ndf/src`・`plugins/ndf/bin` のシェルのファイルが `$NDF_…`・`${NDF_…` で読む名前と、`NDF_…=`・`export NDF_…=` で書く名前が `env.json` に無ければ、`ndf check` が落とす
5. `env.json` の `NDF_` で始まる行が、コード（2〜4 で見る所）にも、ほかの契約の環境変数の欄（`locations.json` の `env`・`entrypoints.json` の `env`）にも現れなければ、`ndf check` が落とす（現れることだけを見て、値を読むか書くかは見ない）。その結果、上の 4 行が消える
6. `ndf check` が `origin/main` で違反なしで通る（`const-fact` を含む）
7. `git grep -ohE "NDF_[A-Z0-9_]+" -- plugins/ndf/src | LC_ALL=C sort -u` から `env.json` の `id` を除いた残りの各名前が、Python の識別子・`not_env` に挙げた字・文の中の語（secret の名前）・族の例のどれかであり、実装の PR の本文にその表を載せる（2026-11-22 の `origin/main` では「載せない」の 8 件と `NDF_PORT_HTTP`。lracct の後は識別子 `NDF_EXE` が加わる）
8. 中継の子の環境（`session_env.child_env`）は、`child: drop` の行の変数を親から受け継がない。スロットの 2 つの変数（`NDF_LAUNCH_SLOT_PARENT`・`NDF_TEST_SLOT_HELD`）は今のとおり受け継ぐ
9. 生成文書 `plugins/ndf/docs/contracts/env.md` が `env.json` から作り直されている（`spec-docs` が通る）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 環境変数の契約の行が増え、4 行が消える。`env.json` に列が 1 つ増える。コマンドの引数と結果 JSON は変わらない |
| データ | 変わらない |
| 既存の振る舞い | 中継の子のセッションが、NDF の実行の記録・作業種別・工程の入力の 5 つの変数を親から受け継がなくなる（`child: drop`）。リレーハブが tmux で起動するラッパーへ、足した行のうち秘密でない変数の値が明示で渡る（`relay/tmux.py` の `new_session_args` は契約の `secret` が偽の行をすべて `-e` で渡す）。ほかは変わらない |
| これから足す環境変数 | 契約に行を足さないと `ndf check` が落ちる。設計中の #4103（`NDF_RELAY_LAUNCH_GEN`）・#4059（`NDF_GH_GATED`）・#3702（`NDF_WORK_PROMPT`）の実装も行を足す |

## 用語

| 用語 | 意味 |
| --- | --- |
| 環境変数の契約 | NDF が読む環境変数と子のプロセスへ渡す環境変数の表（`plugins/ndf/contracts/domains/env.json`）。生成文書 `docs/contracts/env.md` の正本 |
| 読み出しの受け手 | 環境変数の名前で値を引く対象。`os.environ` と、環境を引数で受ける `environ`・`env`・`ctx.env` |
| 子のセッション | 中継のラッパーが起動する claude。環境は `session_env.child_env` が契約の `child` の列で作る |
| 名前の組 | 複数の環境変数の名前をまとめた最上位の定数（`TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")` など） |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 開発者が環境変数を読むコードを書いた | 実装 | — | — |
| E2 | `ndf check` がコードの読み書きと契約を比べた | 手元か CI の検査 | 契約に無い名前・読み手の無い行を違反として出し、終了コード 1 | E1 |
| E3 | 開発者が契約へ行を足した | E2 の違反 | — | E2 |
| E4 | `ndf check --write-spec` が生成文書を作り直した | E3 | `spec-docs` が差分を違反として出す | E3 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 検査 | `ndf check`（CI の既存のジョブ。PR の CI の結果で決める） |
| テスト | 検査の新しい規則のテスト（落ちる書き方ごとに 1 つ）と `session_env.child_env` のテスト。PR の CI の結果で決める |
| 受け入れ条件 7 | 上の `git grep` を打って比べる |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 約 30 件 | 課題の本文の題 | 34 件。うち載せるのは 25 件（#4062 の 1 件を除く） |
| S2 | 対象は `NDF_` で始まる名前 | 課題の本文の事象 | `NDF_` で始まらない名前も載せる（この課題が 12 件、lracct が 5 件）。検査が名前の頭で分けないため（受け入れ条件 2） |
| S3 | 検査で落とすのは「契約に無い読み出し」 | 課題の本文の期待する結果 | 書き出し（受け入れ条件 3）と、読み手の無い行（受け入れ条件 5）も落とす |

## リクエスト（原文）

- 「環境変数として読む名前はすべて契約に載り、意味・書き手・読み手・子へ渡すか（`child`）を持つ」
- 「契約に無い環境変数の読み出しを検査で落とす」

## 由来

- #4061・#4062・#4064 の設計（PR #4095）で中継の 3 変数を契約へ載せる中で見つけた（範囲の外）
- 関連: #4062・#3857。全体の方針: #4069
