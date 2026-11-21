# #3604 #3864 の詳細設計: アカウントの設定ディレクトリでの起動と、使用率・認証の失敗による切り替え

設計文書 [issue-3604-3864-design.md](./issue-3604-3864-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。別ファイルにした理由は、新設する関数が 2 つ以上あることと、既存の関数のシグネチャを変えることである。シグネチャを変えるのは `accounts.usages`・`account_decide.before_limit`・`after_limit` ほかである。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   ├── account_dir.py *     … 起動の前の用意（確かめ・symlink・.claude.json の同期・書き戻し）
│   ├── session_env.py       … account_env・metered_env（新設）
│   ├── account_registry.py  … shared_config_dir・mark_relogin（新設）、store_dir（中身を寄せる）
│   ├── accounts.py          … usages・usage_path（変更）、usage_dir・write_usage・held・hold（新設）
│   ├── account_decide.py    … order・after_auth（新設）、before_limit・after_limit（変更）
│   ├── switch_decide.py     … SectionState.auth_pick・AUTH_RESUME_PROMPT（新設）、_limit_or_auth（変更）
│   ├── wrapper.py           … 候補を試す起動・指定の読み直し・認証の失敗・書き戻し
│   └── cli_account.py       … _acc_list（呼び方だけ）
├── src/ndf/domains/statusline/
│   └── statusline.sh        … 使用率の記録
├── contracts/
│   ├── domains/env.json     … child の列の account-set・account-drop と 6 行
│   ├── domains/ndf-files.json … relay-usage の行・relay-auth-held の行
│   └── limits.json          … relay.auth-hold
├── skills/development-workflow/references/
│   └── relay-accounts.md    … 11 系の振る舞いだけに書き直す
└── tests/
    ├── domains/relay/test_account_dir.py *
    ├── domains/relay/test_relay_parts.py
    ├── domains/relay/test_relay_decide.py
    ├── domains/relay/test_relay_wrapper.py
    ├── domains/relay/test_relay_wrapper_question.py
    ├── domains/relay/test_account_registry.py
    ├── domains/statusline/test_statusline_usage.py *
    └── adapters/test_claude_reply.py
docs/glossary/glossary.json・docs/glossary/ndf-relay.md（リポジトリの根から）
```

依存の向き: `wrapper` → `account_dir`・`session_env`・`accounts`・`account_decide`・`switch_decide`・`account_registry` の一方向。`account_dir` は `account_registry`（保存先・排他・登録の読み）と `signals`（置き換えの書き込み）だけを読み、ラッパーを読まない。`account_decide` と `switch_decide` は時計とファイルを読まない純粋な関数のままで、`account_decide` はほかの中継の部品を読まない。`session_env` は `account_dir` を読まず、用意の済んだ設定ディレクトリのパスを引数で受ける。status line は Python を読まず、子の環境の `NDF_RELAY_DIR` から置き場を求める。循環は無い。設計文書は型の追加が少ないためクラス図を持たず、型は下の各節の「内部のデータと定数」に置く。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/account_dir.py` | 新設 | 登録済みアカウントの設定ディレクトリを起動できる形に用意し、終わりに書き戻す | `account_registry`・`signals`・`contracts.limits` | モジュール・`Links`・`Prepared` | `is_local`・`link_shared`・`shared_config_file`・`check_account`・`sync_config`・`prepare`・`settle`（新設） |
| `src/ndf/domains/relay/session_env.py` | 変更 | 登録済みアカウントと従量の接続の子の環境を足す | `contracts.facts`・`account_registry` | モジュール | `account_env`・`metered_env`（新設） |
| `src/ndf/domains/relay/account_registry.py` | 変更 | 共有の設定ディレクトリの求め方を 1 か所にし、再登録が要る印を書く | 今と同じ | モジュール | `shared_config_dir`・`mark_relogin`（新設）、`store_dir`（変更） |
| `src/ndf/domains/relay/accounts.py` | 変更 | 使用率のキャッシュと外した間の記録の読み書き | `account_decide`・`signals`・`contracts.facts` | モジュール | `usage_dir`・`write_usage`・`held_path`・`held`・`hold`（新設）、`usage_path`・`usages`（変更）、`write_usages`（削除） |
| `src/ndf/domains/relay/account_decide.py` | 変更 | 候補の一覧を受けて替え先を決める | 無し | モジュール・`Pick` | `order`・`after_auth`（新設）、`before_limit`・`after_limit`（変更） |
| `src/ndf/domains/relay/switch_decide.py` | 変更 | 認証の失敗の替え先を手に変える | `account_decide.Pick` | `SectionState` | `_limit_or_auth`（変更） |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 候補を試して起動し、終わりに書き戻す | 上の各部品 | `Wrapper`・`Section`・`Target` | `_launch_target`・`_candidates`・`_pinned_or`・`_auth_pick`・`_settle`・`_metered`・`_tell_at_start`（新設）、`_start`・`_state`・`_limit_pick`・`_on_switch`・`_on_resume`・`_loop`・`_end`（変更） |
| `src/ndf/domains/relay/cli_account.py` | 変更 | 一覧で今の時刻を渡す | 今と同じ | モジュール | `_acc_list`（呼び方だけ） |
| `src/ndf/domains/statusline/statusline.sh` | 変更 | `rate_limits` を使用率のキャッシュへ書く | jq | シェル | `record_usage`（新設のシェル関数） |
| `contracts/domains/env.json` | 変更 | 子の環境の渡し方 | — | — | — |
| `contracts/domains/ndf-files.json` | 変更 | 使用率のキャッシュと外した間の記録の置き場 | — | — | — |
| `contracts/limits.json` | 変更 | 外す秒数 | — | — | — |
| `skills/development-workflow/references/relay-accounts.md` | 変更 | 11 系の振る舞いの手順書 | — | — | — |
| `docs/glossary/glossary.json`・`docs/glossary/ndf-relay.md` | 変更 | 語の追加・意味の変更・廃止（設計の PR で反映済み。実装で語を直すときだけ触る） | — | — | — |

テストのファイルは各節の「単体テストの観点」が使う。

## 起動の前の用意（`account_dir.py`）

満たす決定: 決定 2・3、受け入れ条件 #3604 の 4〜10・12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `is_local` | `account_dir.py` | 新設 | `(item: str) -> bool` | アカウント固有の項目か（`LOCAL_PREFIXES` の前方一致か `LOCAL_NAMES` の一致） |
| `link_shared` | `account_dir.py` | 新設 | `(shared: Path, account: Path) -> Links` | 共有側に `projects`・`settings.json` が無ければ作り、直下のアカウント固有でない項目を symlink にする。`local_only`・`skipped`・`projects_shared` を返す |
| `shared_config_file` | `account_dir.py` | 新設 | `(environ: Mapping[str, str]) -> Path` | 共有側の `.claude.json` のパス。`<共有>/.config.json` があればそれ、共有の設定ディレクトリが `CLAUDE_CONFIG_DIR`（または `NDF_SHARED_CONFIG_DIR`）で指定されていれば `<共有>/.claude.json`、無ければ `~/.claude.json` |
| `check_account` | `account_dir.py` | 新設 | `(account: Path, registered: account_registry.Account) -> Optional[str]` | 認証ファイルと `.claude.json` を確かめ、使えなければ理由の符号を返す |
| `sync_config` | `account_dir.py` | 新設 | `(shared_file: Path, account: Path) -> Optional[str]` | `.claude.json` の共有する設定の部分を 3 者で突き合わせて両側へ書く。書けなければ理由の符号 |
| `prepare` | `account_dir.py` | 新設 | `(store: Path, name: str, environ: Mapping[str, str]) -> Prepared` | アカウントの排他の中で `check_account` → `link_shared` → `projects_shared` の確かめ → `sync_config` → `account_registry.secure` を行う |
| `settle` | `account_dir.py` | 新設 | `(store: Path, name: str, environ: Mapping[str, str]) -> Optional[str]` | アカウントの排他の中で `sync_config` を 1 回打つ。書けなければ理由の符号 |

- `link_shared`
  - 事前条件: アカウントの排他を持っている（`prepare` の中）
  - 事後条件: 共有側の直下のアカウント固有でない項目ごとに、アカウント側の同じ名前が「`<共有>/<項目>` を指す symlink」か「実体（`skipped` に名前がある）」のどちらかである
  - 副作用: 共有側の `projects`（0700）・`settings.json`（`{}`、0600）の作成。アカウント側の symlink の作成と付け替え（一時の名前 `<項目>.<pid>.link` から `os.replace`）
- `check_account`
  - 事後条件: 返りが None なら、認証ファイルが読めてアクセストークンとリフレッシュトークンが空でない。`.claude.json` は JSON の object である。`oauthAccount` のメールアドレスと組織は `account.json` と同じである（`account.json` に組織が無ければメールアドレスだけ比べる）
  - 副作用: 読むだけ。値は返りにも例外の文にも入れない
- `sync_config`
  - 事前条件: アカウントの排他を持っている
  - 事後条件: 両側の `projects`（プロジェクトのパス → 項目の 2 層の葉）と `mcpServers`（名前の 1 層の葉）が同じ値である。同期のバックアップ（`.ndf-shared-base.json`）は、その値とアカウント側の `userID` を持つ。`hasCompletedOnboarding`・`lastOnboardingVersion` はアカウント側に無ければ共有側の値が入る。ほかのキー（`oauthAccount` など）は変わらない
  - 副作用: Claude Code 本体と同じ `<実パス>.lock` のディレクトリをアカウント側・共有側の順に取り、実パスの隣の一時ファイルから置き換える（権限を保つ）
- `prepare`
  - 事後条件: `Prepared.ok` が真なら、受け入れ条件 #3604 の 4・6 が成り立つ。偽なら symlink の作成の途中で止まっていてよい（次の `prepare` が付け替える）
  - 副作用: `link_shared`・`sync_config` と、理由が `RELOGIN_REASONS` のとき `account_registry.mark_relogin`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check_account` | `no_credentials` | 認証ファイルが無い・読めない・JSON でない・アクセストークンが空 | 認証ファイルの読み | 呼び出し元へ返す | 呼び出し元のラッパーが 1 行 | 何も書かない。登録し直せば次の起動で通る |
| `check_account` | `no_refresh` | リフレッシュトークンが空 | 同上 | 呼び出し元へ返す（`prepare` が印を立てる） | 同上と再登録の知らせ | `ndf relay account add <名前>` の後に通る |
| `check_account` | `account_unreadable` | アカウント側の `.claude.json` が無い・JSON の object でない | `.claude.json` の読み | 呼び出し元へ返す | 同上 | 同期のバックアップを消す（次の同期を最初の同期として扱う） |
| `check_account` | `identity_mismatch` | `oauthAccount` が登録と別の本人 | 同上 | 呼び出し元へ返す（`prepare` が印を立てる） | 同上と再登録の知らせ | 同上 |
| `link_shared` | `OSError` | 共有側の作成・symlink の作成と付け替えの失敗 | `os.mkdir`・`os.symlink`・`os.replace` | 呼び出し元へ返す（`prepare` が `io_error` にする） | 呼び出し元のラッパーが 1 行 | 一時の名前の symlink を消す。次の `prepare` で作り直す |
| `prepare` | `projects_not_shared` | アカウント側の `projects` が実体で、共有側の `projects` を指さない | `link_shared` の後の実パスの比べ | 呼び出し元へ返す | 同上 | 実体を消さない（I2）。利用者が中身を共有側へ移して消せば通る |
| `prepare` | `lock_timeout` | アカウントの排他を `claude-accounts.lock` の秒で取れない | `account_registry.locked` | 呼び出し元へ返す | 同上 | 次の候補へ。次のセグメントで再び試す |
| `prepare` | `io_error` | symlink の作成・共有側の作成・権限の補正の `OSError` | `link_shared`・`secure` | 呼び出し元へ返す | 同上 | 同上 |
| `sync_config` | `shared_unreadable` | 共有側の `.claude.json` が JSON の object でない | 共有側の読み | 同期せずに返す（`prepare` は起動を止めない） | `log.jsonl` に 1 行 | 次の同期でそろえる |
| `sync_config` | `lock_busy` | 本体の排他を `claude-account-dir.lock` の秒で取れない | `_claude_lock` | 同上 | 同上 | 同上 |
| `sync_config` | `write_failed` | 一時ファイルの書き込み・置き換えの `OSError` | `_write_json` | 同上 | 同上 | 一時ファイルを消す。次の同期でそろえる |
| `settle` | 上の 3 つ・`lock_timeout` | 同上 | 同上 | 呼び出し元へ返す | 画面に 1 行と `log.jsonl` に 1 行（E4 の失敗） | 次の起動の前の同期でそろえる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `link_shared` | アカウント側に同じ名前が無い | symlink を作り `added` を 1 増やす |
| `link_shared` | 同じ名前の symlink が `<共有>/<項目>` を指す | 何もしない |
| `link_shared` | 同じ名前の symlink がほかを指す | 付け替えて `added` を 1 増やす |
| `link_shared` | 同じ名前の実体がある | 飛ばして `skipped` に名前 |
| `link_shared` | アカウント側の項目が固有でなく、symlink でなく、共有側に無い | `local_only` に名前 |
| `sync_config` | 葉が片側だけ変わった（バックアップと比べて） | 変わった側の値 |
| `sync_config` | 葉が両側で変わった | 共有側の値 |
| `sync_config` | バックアップが無い・`userID` が違う・アカウント側にキーが無い | 共有側の値にそろえる（最初の同期） |
| `sync_config` | 葉がどちらかで消えた（バックアップにあり片側に無い） | 消えた側に合わせて消す（片側だけ変わった扱い） |

- 繰り返しの終わる条件: `link_shared` は共有側とアカウント側の直下を 1 回ずつ数え上げる。再帰しない

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `LOCAL_PREFIXES` | 定数 | `account_dir.py` | `(".credentials.json", ".claude.json", ".oauth_refresh.lock")`（10 系と同じ。名前は `account_registry` の `CRED_FILE`・`CONFIG_FILE` から引く） |
| `LOCAL_NAMES` | 定数 | `account_dir.py` | `(".config.json", "backups", "policy-limits.json", "policy-limits.json.stamp.json", "remote-settings.json", "mcp-needs-auth-cache.json", ".ndf-statusline-auth.json", "account.json", "usage.json", ".ndf-shared-base.json")`（10 系と同じ。`account.json`・`usage.json`・`.ndf-shared-base.json` は `account_registry` の定数から引く） |
| `SHARED_KEYS` | 定数 | `account_dir.py` | `("projects", "mcpServers")` |
| `ONBOARDING_KEYS` | 定数 | `account_dir.py` | `("hasCompletedOnboarding", "lastOnboardingVersion")` |
| `LOCK_STALE` | 定数 | `account_dir.py` | 10 秒（本体と同じく、これより古い `<実パス>.lock` は残骸として除く） |
| `RELOGIN_REASONS` | 定数 | `account_dir.py` | `("no_refresh", "identity_mismatch")` |
| `REASONS` | 定数 | `account_dir.py` | 理由の符号 → 画面の 1 行の文（値を含まない。例: `no_refresh` → `リフレッシュトークンが空。ndf relay account add {name} で登録し直す`） |
| `Links` | 型 | `account_dir.py` | `added: int`・`skipped: list[str]`・`local_only: list[str]`・`projects_shared: bool` |
| `Prepared` | 型 | `account_dir.py` | `ok: bool`・`reason: Optional[str]`・`config_dir: Path`・`links: Links`・`sync: Optional[str]` |
| 本体の排他の待ち | 設定値 | `contracts/limits.json` の `claude-account-dir.lock` | 2 秒（既にある） |
| 登録の排他の待ち | 設定値 | `contracts/limits.json` の `claude-accounts.lock` | 30 秒（既にある） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じアカウントを使う 2 つのレーンの `prepare`・`settle` | アカウントの設定ディレクトリ | `account_registry.locked(store, name)`（filelock） | `link_shared` は何度打っても同じ形になる。中断した付け替えは一時の名前を次に消して作り直す |
| `sync_config` と動いている claude | 両側の `.claude.json` | 本体と同じ `<実パス>.lock` のディレクトリ（`os.mkdir` で取る。10 秒より古ければ消して取り直す） | 置き換えで書くので、途中で止まっても前の中身が残る |
| 登録のコマンド（再登録）と `prepare` | `account.json`・認証ファイル | 同じ `account_registry.locked` | 再登録はバックアップを消すので、次の同期は最初の同期になる |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `prepare` | 理由の符号・`local_only` と `skipped` の名前・`added` の数（ラッパーが `log.jsonl` の `account_dir` の行へ書く） | `log.jsonl` | トークン・メールアドレス・`.claude.json` の値・参照先のパス |
| `settle` | 理由の符号 | 同上 | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `is_local`・`link_shared`・`sync_config`・`check_account` | 11 系の中継の symlink・同期の処理 | `git grep -n "symlink_to\|os.symlink\|mcpServers" -- plugins/ndf/src` | 当たりは `account_registry.detach`（外すだけ）と無関係なモジュールで、使える部品は無い。10 系の `claude_account_dir.py` を移す |
| `prepare`・`settle` | `account_registry.locked`・`secure` | ファイルを読んで確かめた | 使う |
| 本体の排他（`_claude_lock`） | filelock（LIB3） | 設計文書の外部の調査 | 使えない。filelock はファイルの錠で、本体の `<実パス>.lock` のディレクトリと互いに待てない |
| `_write_json` | `signals.replace_text` | ファイルを読んで確かめた | 使えない。`replace_text` は symlink の実パスを解かず、権限を保たない。実パスの隣へ書く小さな関数を置く |
| `shared_config_file` | `adapters/training_cache.py` の `.claude.json` の求め方 | `grep -n "CLAUDE_STATE" src/ndf/adapters/training_cache.py` | 求め方は同じだが、`NDF_SHARED_CONFIG_DIR` と `.config.json` を見ない。`account_registry.shared_config_dir` の上に置く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `is_local` | `.credentials.json`・`.claude.json.tmp.1`・`account.json` が真、`projects`・`settings.json` が偽 | `.oauth_refresh.lock` の前方一致 | — |
| `link_shared` | 共有側の項目すべてに symlink、共有側に `projects`・`settings.json` が作られる、`.credentials.json` に symlink が無い | 後から共有側に増えた項目にも張る。参照先の違う symlink を付け替える | 同じ名前の実体を上書きしない（`skipped`）。アカウント側だけの実体が `local_only` に入り残る |
| `check_account` | 使えるアカウントで None | `account.json` に組織が無いときメールアドレスだけ比べる | 理由ごとに 1 件ずつ（認証ファイルが無い・JSON でない・アクセストークンが空・リフレッシュトークンが空・`.claude.json` が壊れている・本人が違う） |
| `sync_config` | 共有側の値がアカウント側へ写り、`oauthAccount` は変わらない | 片側だけの変更・両側の変更・葉の削除・バックアップが無い・`userID` が違う | 本体の排他が取れない・書けないときに理由を返し、両側の中身が変わらない |
| `prepare` | ok で `config_dir` が `<保存先>/<名前>` の絶対パス | 2 回続けて打っても同じ形 | 理由ごとに ok が偽、`no_refresh`・`identity_mismatch` だけ `needs_relogin` が真。共有側の `.credentials.json` の中身と更新時刻が変わらない。理由の文に値が出ない |
| `settle` | アカウント側の変更が共有側へ移る | 変更が無ければ書かない | 書けないときに理由の符号 |

## 子の環境（`session_env.py`・子の環境の表）

満たす決定: 決定 1・8、受け入れ条件 #3604 の 1〜3・11、#3864 の 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `account_env` | `session_env.py` | 新設 | `(env: Mapping[str, str], config_dir: Path) -> dict[str, str]` | `child_env` の出力に、`account-set` の変数を入れ、`account-drop` の変数を外す |
| `metered_env` | `session_env.py` | 新設 | `(env: Mapping[str, str], extra: Mapping[str, str], saved: bool) -> dict[str, str]` | `CLAUDE_CONFIG_DIR` を共有の設定ディレクトリへ戻し、`account-drop` の変数を外し、保存した設定なら AWS のキーを外し、従量の接続の設定を重ねる |

- `account_env`
  - 事前条件: `env` は `child_env` の出力（`NDF_CLAUDE_ACCOUNT` が入っている）。`config_dir` は `account_dir.prepare` が ok を返したパス
  - 事後条件: `CLAUDE_CONFIG_DIR` が `config_dir` である。`NDF_SHARED_CONFIG_DIR` は、`env` にあればその値、無ければ `env` の `CLAUDE_CONFIG_DIR`（無ければ空文字）である。`CLAUDE_CODE_PLUGIN_CACHE_DIR` は、`env` にあればその値、無ければ `<共有の設定ディレクトリ>/plugins`（`account_registry.shared_config_dir(env)`）である。`account-drop` の名前が 1 つも無い
- `metered_env`
  - 事後条件: `env` に `NDF_SHARED_CONFIG_DIR` があれば、その値が空でなければ `CLAUDE_CONFIG_DIR` へ入れ、空なら `CLAUDE_CONFIG_DIR` を外す。どちらでも `NDF_SHARED_CONFIG_DIR` は外す。無ければ `CLAUDE_CONFIG_DIR` は親のまま。`account-drop` の名前が無い。`saved` なら `account_registry.AWS_KEY_ENV` が無い。最後に `extra` を重ねる（`extra` の変数は外した後に入る）
- どちらも純粋な関数で、ファイルを読まない。値の組は `facts.ids("env", child="account-set")` と `facts.ids("env", child="account-drop")` から引き、名前をコードに書かない。値は `_set_values` と同じ形の対応の辞書で作る

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `account_env` | `KeyError` | 表の `account-set` に、値の辞書に無い名前が足された | 値の辞書の引き | 止める（`_set_values` と同じく、表とコードの食い違いはテストで落とす） | トレースバック（実装の不具合） | 表かコードを直す |

### 内部のデータと定数（子の環境の表の行）

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `columns.child.summary` | 設定値 | `contracts/domains/env.json` | 設計文書の「入出力のコントラクト」の文 |
| `CLAUDE_CONFIG_DIR` の行 | 設定値 | 同上 | `"child": "account-set"` を足す |
| `NDF_SHARED_CONFIG_DIR` の行 | 設定値 | 同上 | `"child": "account-set"` を足す |
| `CLAUDE_CODE_PLUGIN_CACHE_DIR` の行 | 設定値 | 同上 | 新設。`area: accounts`・`set_by: ndf`・`type: path`・`secret: false`・`child: account-set` |
| `ANTHROPIC_API_KEY` の行 | 設定値 | 同上 | `"child": "account-drop"` を足す |
| `CLAUDE_CODE_OAUTH_TOKEN`・`ANTHROPIC_AUTH_TOKEN` の行 | 設定値 | 同上 | 新設。`area: accounts`・`set_by: user`・`type: string`・`secret: true`・`child: account-drop` |
| `CLAUDE_CODE_OAUTH_SCOPES` の行 | 設定値 | 同上 | 新設。`secret: false`、ほかは上と同じ |
| `CLAUDE_CODE_USE_BEDROCK`・`CLAUDE_CODE_USE_VERTEX`・`CLAUDE_CODE_USE_FOUNDRY` の行 | 設定値 | 同上 | 新設。`area: accounts`・`set_by: user`・`type: flag`・`secret: false`・`child: account-drop` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `account_env`・`metered_env` | `account_login.auth_env`・`training_cache.CLAUDE_FOREIGN_AUTH` | `grep -n "AUTH_ENV\|CLAUDE_FOREIGN_AUTH" src/ndf/domains/relay/account_login.py src/ndf/adapters/training_cache.py` | 使えない。`auth_env` はログイン用の仮ディレクトリを作る環境で、`CLAUDE_FOREIGN_AUTH` は 7 変数を全部は持たない。名前は表から引く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `account_env` | 3 変数が入り、7 変数が外れる | 親に `CLAUDE_CONFIG_DIR` が無い（`NDF_SHARED_CONFIG_DIR` が空文字）・親が登録済みアカウントの子（親の `NDF_SHARED_CONFIG_DIR` を引き継ぐ）・親に `CLAUDE_CODE_PLUGIN_CACHE_DIR` がある | 表の行を 1 つ外したテストの表で落ちる |
| `metered_env` | `CLAUDE_CONFIG_DIR` が共有の設定ディレクトリ、7 変数が無く、`extra` が入る | 保存した設定で AWS のキーが無い・環境変数の設定で AWS のキーが残る・親がアカウントの子でなければ `CLAUDE_CONFIG_DIR` は親のまま・`NDF_SHARED_CONFIG_DIR` が空なら外す | — |
| `child_env` | 既定のログインの子が今と同じ（受け入れ条件 #3604 の 3） | — | — |

## 登録の保存先（`account_registry.py`）

満たす決定: 決定 1・3、受け入れ条件 #3604 の 1・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `shared_config_dir` | `account_registry.py` | 新設 | `(environ: Mapping[str, str]) -> Path` | 共有の設定ディレクトリ。`NDF_SHARED_CONFIG_DIR` があればその値（空なら `~/.claude`）、無ければ `signals.claude_config_root(environ)` |
| `store_dir` | `account_registry.py` | 変更 | 変わらない | `NDF_ACCOUNTS_DIR` が無ければ `shared_config_dir(environ) / "ndf" / "accounts"`。振る舞いは今と同じ |
| `mark_relogin` | `account_registry.py` | 新設 | `(store: Path, name: str) -> None` | 呼ぶ側が持つアカウントの排他の中で、`account.json` の `needs_relogin` を真にして置き換えで書く |

- `mark_relogin`
  - 事前条件: `account_registry.locked(store, name)` を持っている（`account_dir.prepare` の中から呼ぶ）
  - 副作用: `account.json` の書き換え（0600）。ほかの項目は変えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `mark_relogin` | `AccountError` | `account.json` が無い・壊れている | `_read` | 呼び出し元へ返す（`prepare` は理由を変えずに起動を飛ばす） | ラッパーの 1 行は元の理由のまま | 次の起動でもう一度試す |
| `mark_relogin` | `OSError` | 書けない | `write` | 同上 | 同上 | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `shared_config_dir` | `store_dir` の中の求め方・`identity._legacy_accounts_dir` | ファイルを読んで確かめた | `store_dir` から取り出して 1 か所にする。`identity` の旧い保存先の求め方は退避の判定用で、変えない |
| `mark_relogin` | `set_capacity` の読み書きの形 | ファイルを読んで確かめた | 同じ形（`_read` → 書き換え → `write`）で書く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `shared_config_dir` | `CLAUDE_CONFIG_DIR` の値 | `NDF_SHARED_CONFIG_DIR` が空・どちらも無い | — |
| `mark_relogin` | 真になり、一覧の `state` が `再登録が要る` | ほかの項目が変わらない | `account.json` が壊れていると `AccountError` |

## 使用率と外した間の記録（`accounts.py`・ファイルの表・上限の値）

満たす決定: 決定 4・6、受け入れ条件 #3864 の 1〜3・6・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `usage_dir` | `accounts.py` | 新設 | `(state_dir: Path) -> Path` | `<状態の保存先>/relay/accounts/usage`（ファイルの表の `relay-usage` の `path` の親から） |
| `usage_path` | `accounts.py` | 変更 | `(state_dir: Path, name: str) -> Path` | アカウント 1 つの使用率のキャッシュのパス |
| `usages` | `accounts.py` | 変更 | `(state_dir: Path, now: float) -> list[account_decide.Usage]` | `usage/` の `<名前>.json` を名前の順に読み、窓ごとにリセットの時刻を過ぎていれば 0%、使用率は窓の最大、上限は過ぎていない窓のどれかが 100 以上 |
| `write_usage` | `accounts.py` | 新設 | `(state_dir: Path, name: str, windows: Mapping[str, tuple[float, float]], recorded_at: float) -> None` | status line と同じ形で 1 アカウントの記録を書く（テストが使う。今の `write_usages` は消す） |
| `held_path` | `accounts.py` | 新設 | `(state_dir: Path) -> Path` | `<状態の保存先>/relay/accounts/held.json` |
| `held` | `accounts.py` | 新設 | `(state_dir: Path, now: float) -> dict[str, float]` | 外す終わりが `now` より後の名前と終わりの時刻 |
| `hold` | `accounts.py` | 新設 | `(state_dir: Path, name: str, until: float, now: float) -> None` | 錠の中で読み、終わった名前を消し、`name` を `until` で書いて置き換える |

- `usages` の事後条件: 返す名前は `account_registry.valid_name` に当たるもの（`metered` と形の違う名前のファイルは読まない）。読めないファイル・形の違うファイルは返さない（使用率の分からないアカウントになる）
- `hold` の副作用: `<held.json>.lock` の錠（`signals.held`、待ちは `relay.signal-lock`）と置き換えの書き込み

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `usages` | 読めない・形が違う | 書き込みの途中・手で壊した | ファイルごとの読み | 既定値で続ける（そのファイルを飛ばす） | 無し | 次の status line の更新で直る |
| `held` | 読めない・形が違う | 手で壊した | 読み | 既定値で続ける（外した間が無いとして読む） | 無し | 次の `hold` が置き換える |
| `hold` | 錠を取れない・書けない | ほかのレーンが同時に書いている・ディスク | 錠と置き換え | 既定値で続ける（記録せず替え先の判断は続ける。今の判断では今のアカウントを候補から除く） | `log.jsonl` に 1 行 | 次の認証の失敗で再び書く |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `relay-usage` の行 | 設定値 | `contracts/domains/ndf-files.json` | `path: "usage/<名前>.json"`・`writer: "NDF の status line（statusline.sh）"`・`keys: ["windows", "recorded_at"]` |
| `relay-auth-held` の行 | 設定値 | 同上 | 新設。`location: relay-accounts`・`path: held.json`・`kind: measured`・`tracked: false`・`writer: "中継のラッパー（M3b）"`・`ends: replaced` |
| `relay.auth-hold` | 設定値 | `contracts/limits.json` | `{"summary": "認証の失敗で、そのアカウントを替え先と指定の候補から外す秒（#3864）", "seconds": 3600}` |
| 窓の名前 | 定数 | `account_registry.WINDOWS` | `("five_hour", "seven_day")`（既にある） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 別のアカウントの 2 つの status line | 使用率のキャッシュ | 取らない。ファイルが別 | 置き換えで書く |
| 同じアカウントの 2 つのレーンの status line | 同じ 1 ファイル | 取らない。後に書いたほうが残る | どちらも同じアカウントの値なので、後の値が新しい |
| 2 つのラッパーの `hold` | `held.json` | `signals.held` の錠 | 読み直してから書くので、ほかの名前を消さない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `usage_dir`・`held_path` | `accounts_dir`・`facts.value("locations", …)` | ファイルを読んで確かめた | 使う。パスはファイルの表から引く |
| `write_usage`・`hold` | `signals.replace_json`・`signals.held` | 同上 | 使う |
| `held` | `accounts.pinned` の読み方 | 同上 | 同じ形で読み、壊れていれば空として扱う（`pinned` と違い止めない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `usages` | 2 つの窓の大きいほうが使用率 | リセットの時刻を過ぎた窓が 0%・5 時間の窓が無い・100 ちょうどで上限 | 壊れたファイルを飛ばす・`metered.json` を読まない |
| `write_usage`・`usages` | 書いた値を読み戻せ、ほかのアカウントのファイルが変わらない | — | — |
| `held`・`hold` | 書いた名前が終わりまで返る | 終わりちょうどの時刻で返らない・終わった名前が次の書き込みで消える | 壊れたファイルで空を返す |

## 替えるかの判断（`account_decide.py`）

満たす決定: 決定 5・6・8、受け入れ条件 #3864 の 4〜7・9・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `order` | `account_decide.py` | 新設 | `(current: Optional[str], rows: list[Usage], candidates: list[str]) -> list[str]` | 今のアカウントを除く候補を、使用率の分かる上限でないもの（使用率・名前の順）→ 使用率の分からないもの（名前の順）に並べる。上限のものは入れない |
| `before_limit` | `account_decide.py` | 変更 | `(current: Optional[str], rows: list[Usage], threshold: float, candidates: list[str]) -> Pick` | 今の判断を候補の中に限る。今が `metered` なら `order` の先頭へ戻す（無ければ `keep`） |
| `after_limit` | `account_decide.py` | 変更 | `(current: Optional[str], rows: list[Usage], candidates: list[str], metered: bool) -> Pick` | 今のアカウントの上限が解けていれば同じアカウント。そうでなければ `order` の先頭、無く `metered` が真で今が `metered` でなければ `metered`、それも無ければ `wait_reset` |
| `after_auth` | `account_decide.py` | 新設 | `(current: Optional[str], rows: list[Usage], candidates: list[str], metered: bool) -> Pick` | `order` の先頭、無く `metered` が真で今が `metered` でなければ `metered`、それも無ければ `stalled` |

- `candidates` は呼ぶ側が渡す、使える登録済みアカウントの名前（再登録が要るもの・外した間のものを除いたもの）。判断の関数は候補の外の名前を返さない
- `Pick.kind` に `stalled`（認証の失敗で替え先が無い）を足す。`reason` は値を含まない文

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `before_limit` | 今が `metered` で `order` が空でない | `switch`（先頭） |
| `before_limit` | 今が `metered` で `order` が空 | `keep` |
| `before_limit` | 今のアカウントの使用率が分からない | `keep`（今と同じ） |
| `before_limit` | 閾値より下で上限でない | `keep` |
| `before_limit` | 閾値以上か上限で、候補に閾値より下のものがある | `switch`（最も空いたもの） |
| `after_limit` | 今のアカウントの行が上限でない | `switch`（同じアカウント） |
| `after_limit` | `order` が空でない | `switch`（先頭） |
| `after_limit` | `order` が空・`metered` が真・今が `metered` でない | `switch`（`metered`） |
| `after_limit` | それ以外 | `wait_reset` |
| `after_auth` | `order` が空でない | `switch`（先頭） |
| `after_auth` | `order` が空・`metered` が真・今が `metered` でない | `switch`（`metered`） |
| `after_auth` | それ以外 | `stalled` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `order` | `_best` | ファイルを読んで確かめた | `_best` は使用率の行がある名前だけを並べるので、使用率の分からない候補を扱えない。`order` に置き換え、`before_limit` の閾値より下の選び方だけ `_best` を残す |
| `after_auth` | `after_limit` | 同上 | 同じ `order` を使い、今のアカウントの上限が解けたかの分岐を持たない別の関数にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `order` | 分かるもの（使用率の順）→ 分からないもの（名前の順） | 同じ使用率は名前の順・上限のものが入らない・今のアカウントが入らない | 候補の外の行を返さない |
| `before_limit` | 92% と 10% で 10% へ替える | 閾値ちょうど・今が `metered` で戻る・今が `metered` で候補が無い | 候補に無いアカウントへ替えない |
| `after_limit` | 使用率の分からない `work2` へ替える | 分かる上限でないものが先・上限が解けた今のアカウント | 候補が無く `metered` が真で `metered`、偽で `wait_reset`、今が `metered` で `wait_reset` |
| `after_auth` | `work2` へ替える | 既定のログイン（今が None）でも替える | 候補が無く `metered` で `metered`、無ければ `stalled`、今が `metered` で `stalled` |

## 次の手の判断（`switch_decide.py`）

満たす決定: 決定 6、受け入れ条件 #3864 の 6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `SectionState.auth_pick` | `switch_decide.py` | 新設 | フィールド `Optional[Pick]`（既定は None） | 画面に認証の失敗の行が出たときの替え先の判断。今の `auth_failed` は消す |
| `AUTH_RESUME_PROMPT` | `switch_decide.py` | 新設 | 定数 `str` | `認証の失敗でアカウントを替えた。中断したところから続ける` |
| `_limit_or_auth` | `switch_decide.py` | 変更 | 変わらない | 上限の判断を先に見る（今と同じ）。次に `auth_pick` が `switch` なら、日の起動の上限を見てから `Step("account", reason, AUTH_RESUME_PROMPT, account)`。`stalled` で子が生きていれば `Step("stalled", reason)` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `SectionState.auth_pick` | `SectionState.limit_pick` | ファイルを読んで確かめた | 同じ形のフィールドにする |
| `AUTH_RESUME_PROMPT` | `RESUME_PROMPT` | 同上 | 使えない。文が利用上限で替えたと書く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_limit_or_auth` | `auth_pick` が `switch` で `account` の手と `AUTH_RESUME_PROMPT` | 日の起動の上限で `stop`・上限の行と認証の失敗が同時なら上限が先 | `stalled` で子が終わっていれば今の終わりの判断へ |

## NDF の status line（`statusline.sh`）

満たす決定: 決定 4、受け入れ条件 #3864 の 1・2・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_usage` | `statusline.sh` | 新設 | シェル関数（引数無し。`$input` と環境を読む） | `NDF_CLAUDE_ACCOUNT` と `NDF_RELAY_DIR` がどちらもあり、名前が `^[a-z0-9][a-z0-9_-]{0,31}$` に当たり `metered` でなく、入力に `rate_limits` があれば、`${NDF_RELAY_DIR%/wrappers/*}/accounts/usage/<名前>.json` へ書く |

- 事後条件: ファイルの中身は設計文書の「使用率のキャッシュ」の形。前の中身の `windows` と同じなら書かない（`recorded_at` だけの更新はしない）
- 副作用: 置き場のディレクトリの作成・`<ファイル>.$$` への書き込み・`mv -f`。すべて標準エラーを捨て、失敗しても表示の処理へ進む
- 置き場は `NDF_RELAY_DIR`（ラッパーのやり取りの保存先 `<状態の保存先>/relay/wrappers/<名前>`）から求める。状態の保存先の求め方（`identity._state_home` とプラグイン名）を、シェルに 2 つ目として持たないためである。ラッパーの外の claude は `NDF_RELAY_DIR` を持たないので書かない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_usage` | jq が無い・入力が JSON でない | 環境 | jq の終了コード | 何もしない | 表示は今と同じ。終了コード 0 | 次の更新 |
| `record_usage` | 書けない | 権限・ディスク | `mkdir`・書き込み・`mv` の失敗 | 一時ファイルを消して何もしない | 同上 | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `record_usage` | 同じファイルの窓の大きさの記録（`window_file` を一時ファイルから `mv` で置く処理） | ファイルを読んで確かめた | 同じ書き方にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `record_usage` | 2 つの窓が書かれる | 5 時間の窓が無い・同じ値で書かない・2 つのアカウントを同時に書いて両方残る | `NDF_RELAY_DIR` が無い・名前が `metered`・`rate_limits` が無いと書かない。置き場に書けない・jq が無い（`PATH` から外す）とき出力が今と同じで終了コード 0 |

## ラッパー（`wrapper.py`）

満たす決定: 決定 1・3・5〜8、受け入れ条件 #3604 の 1・7・8・9、#3864 の 4〜10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Target` | `wrapper.py` | 新設 | 型 `(account: Optional[str], config_dir: Optional[Path])` | 起動するアカウントと、用意の済んだ設定ディレクトリ |
| `Section.auth_at`・`auth_told` | `wrapper.py` | 新設 | フィールド `Optional[float]`・`bool` | 認証の失敗の行を見た時刻・直し方の 1 行を出したか |
| `_candidates` | `wrapper.py` | 新設 | `(self, now: float) -> list[str]` | 登録済みアカウントのうち、再登録が要らず、外した間でないものの名前 |
| `_metered` | `wrapper.py` | 新設 | `(self) -> Optional[tuple[dict[str, str], bool]]` | 従量の接続の設定（変数の組と、保存した設定か）。無い・読めなければ None |
| `_pinned_or` | `wrapper.py` | 新設 | `(self, account: Optional[str], now: float) -> Optional[str]` | 指定があり、候補にあり、上限でなければ指定、そうでなければ `account` |
| `_launch_target` | `wrapper.py` | 新設 | `(self, first: Optional[str], now: float) -> Target` | `first` → `order` の順の候補 → 既定のログインの順に試し、登録済みアカウントは `account_dir.prepare` が ok のものを返す。飛ばしたものは画面と `log.jsonl` に 1 行 |
| `_auth_pick` | `wrapper.py` | 新設 | `(self, sec: Section, screen: str) -> Optional[Pick]` | 画面に認証の失敗の行があれば、最初に見た見回りで今の登録済みアカウントを `accounts.hold` し、`after_auth` を返す |
| `_settle` | `wrapper.py` | 新設 | `(self, sec: Section) -> None` | 終わった子のアカウントが登録済みなら `account_dir.settle` を打ち、失敗の理由を 1 行出す |
| `_tell_at_start` | `wrapper.py` | 新設 | `(self) -> None` | 再登録が要るアカウントを 1 つにつき 1 行、従量の接続の設定が壊れていれば 1 行出す |
| `_loop` | `wrapper.py` | 変更 | 変わらない | 最初のセグメントの前に `_tell_at_start` を呼ぶ |
| `_state` | `wrapper.py` | 変更 | 変わらない | `auth_failed` の代わりに `auth_pick=self._auth_pick(sec, screen)` を渡す |
| `_limit_pick` | `wrapper.py` | 変更 | 変わらない | `usages(state_dir, now)`・`_candidates`・`_metered` を渡して `after_limit` を呼ぶ。今のアカウントのキャッシュのファイルが上限を見た時刻より古ければ上限として読む（今と同じ考えを、アカウントのファイルの更新時刻で） |
| `_on_switch` | `wrapper.py` | 変更 | 変わらない | `before_limit` に候補を渡し、`_pinned_or(pick.account)` を次のセグメントのアカウントにする |
| `_on_resume` | `wrapper.py` | 変更 | 変わらない | 手が `account` のときだけ `_pinned_or(step.account)` を使う。`restart` は今のアカウントのまま |
| `_start` | `wrapper.py` | 変更 | 変わらない | 前のセグメントがあれば `_settle`。`_launch_target(req.account)` で決めたアカウントで `child_env` を作り、登録済みなら `account_env`、`metered` なら `metered_env` を重ねる。`Section.account` と `section` の行の `account` には決めたアカウントを入れる |
| `_end` | `wrapper.py` | 変更 | 変わらない | 今のセグメントがあれば `_settle` を打ってから今の止まり方へ |

- `_launch_target`
  - 事前条件: 子が止まっている（呼ぶのは `_start` だけ）
  - 事後条件: 返りの `account` が登録済みなら `config_dir` は ok の用意の済んだパス。`metered` なら `_metered` が None でない。None なら既定のログイン
  - 副作用: `account_dir.prepare`（symlink・同期・印）、画面の 1 行、`log.jsonl` の `account_dir` の行
- `_auth_pick`
  - 副作用: `accounts.hold`（`until = now + limits.seconds("relay.auth-hold")`）。替え先が無く直し方の 1 行をまだ出していなければ出して `auth_told` を立てる（同じセグメントで 1 回）

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_launch_target` | 試す名前が `metered` で設定がある | `Target("metered", None)` |
| `_launch_target` | 試す名前が登録済みで `prepare` が ok | `Target(名前, 設定ディレクトリ)` |
| `_launch_target` | 試す名前が登録済みで `prepare` が使えない | 1 行を出して次の名前 |
| `_launch_target` | 試す名前が登録に無い・`metered` で設定が無い | 1 行を出して次の名前 |
| `_launch_target` | 試す名前が尽きた | `Target(None, None)`（既定のログイン） |
| `_pinned_or` | 指定が無い・壊れている | `account`（壊れた指定は今と同じく `AccountError` で止まる） |
| `_pinned_or` | 指定が候補に無い（再登録が要る・外した間・登録が無い）か上限 | `account` |
| `_pinned_or` | それ以外 | 指定 |

- 繰り返しの終わる条件: `_launch_target` は名前の並び（先頭・候補・既定のログイン）を 1 回ずつ試し、同じ名前を 2 回試さない

直し方の 1 行の文（`AUTH_FIX`）:

| ログインの形 | 文 |
| --- | --- |
| 既定のログイン | `claude の中で /login を打つ` |
| 登録済みアカウント | `ndf relay account add <名前> で登録し直す` |
| 従量の接続 | `ndf relay account check metered で設定を確かめる` |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_launch_target` | 用意の失敗 | `account_dir` の理由 | `prepare` の返り | 次の候補へ | 画面に 1 行（`ndf-relay: アカウント <名前> で起動しない（<理由の文>）。次の候補で起動する`）と `log.jsonl` の `account_dir` の行 | 次のセグメントで再び試す |
| `_launch_target` | 登録の保存先が読めない | `AccountError`・`OSError` | `names`・`load` | 既定値で続ける（候補を空として既定のログインへ） | `log.jsonl` に 1 行 | 同上 |
| `_auth_pick` | 外した間を記録できない | `accounts.hold` の錠・書き込みの失敗 | `hold` | 既定値で続ける（今の判断では今のアカウントを候補から除く） | `log.jsonl` に 1 行 | 次の認証の失敗で再び書く |
| `_settle` | 書き戻しの失敗 | `account_dir.settle` の理由 | 返り | 既定値で続ける | 画面に 1 行と `log.jsonl` に 1 行 | 次の起動の前の同期 |
| `_metered` | 設定が壊れている | `load_metered` の理由 | 読み | 設定なしとして扱う | `_tell_at_start` が 1 行 | 設定を直せば次の起動で効く |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_launch_target` | `{"kind": "account_dir", "account", "reason", "local_only", "skipped", "added"}` | `log.jsonl` | トークン・メールアドレス・パス |
| `_auth_pick`・`_limit_pick`・`_on_switch` | 替えたときの `{"kind": "account", "reason"}`（今と同じ形） | `log.jsonl` | 同上 |
| `_tell_at_start` | 再登録が要るアカウントの名前の 1 行 | 画面 | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `Target`・`Section.auth_at`・`auth_told` | `Launch`・`Section.limit_at` | ファイルを読んで確かめた | 同じ形の型とフィールドにする |
| `_launch_target` | `accounts.chosen`・`account_dir.prepare` | 同上 | `prepare` を使う。候補を順に試す部品は無いので足す |
| `_candidates` | `account_registry.names`・`load`・`accounts.held` | ファイルを読んで確かめた | 使う |
| `_metered` | `account_registry.fallback_env`・`load_metered`・`declared_env` | 同上 | 使う。保存した設定かは `declared_env` が None かで決める |
| `_pinned_or` | `accounts.pinned`・`accounts.chosen` | 同上 | `pinned` を使う。`chosen` は最初のセグメントだけ（環境変数が先）で今のまま |
| `_auth_pick` | `_limit_pick` の形・`observe.auth_failed` | 同上 | 同じ形で書く |
| `_settle`・`_tell_at_start` | `_say`・`_log` | 同上 | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_start`・`_launch_target` | 指定の `work2` で子の環境が `account_env` の形（偽の起動で環境を受け取る） | 指定が使えないとき判断の結果・すべて飛ばして既定のログイン | 理由ごとに 1 行と `account_dir` の行が出て次の候補で起動する |
| `_on_switch`・`_pinned_or` | 動いている間に指定を替えると次のセグメントがそのアカウント | 指定が上限・外した間・再登録が要るとき判断の結果 | 壊れた指定で今と同じく止まる |
| `_limit_pick` | 使用率の分からない `work2` で `--resume` | すべて上限で従量の接続・従量の接続も無くリセット待ち | — |
| `_auth_pick` | `work2` で `--resume`、`held.json` に `work1` | 既定のログインの認証の失敗でも替える | 替え先が無ければ直し方の 1 行がセグメントで 1 回、ログインの形ごとの文 |
| `_settle`・`_end` | セグメントの終わりとラッパーの終わりに 1 回ずつ書き戻す | 既定のログインと従量の接続では打たない | 書けないとき 1 行で続ける |
| `_tell_at_start` | 再登録が要る 2 つで 2 行 | 無ければ出さない | 従量の接続の設定が壊れていると 1 行 |

## `ndf plan` の `claude -p`（`adapters/claude.py`。変えない）

満たす決定: 決定 9、受け入れ条件 #3604 の 11

コードは変えない。`call_claude` が起動する `claude -p` の環境を、`tests/adapters/test_claude_reply.py` に足すテストで縛る。縛るのは、親の環境の `CLAUDE_CONFIG_DIR`・`NDF_CLAUDE_ACCOUNT` をそのまま持つことである。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `call_claude` | 親の環境の `CLAUDE_CONFIG_DIR` が起動の環境に同じ値で入る | `NDF_CLAUDE_ACCOUNT` も同じ値 | — |

## 一覧のコマンド（`cli_account.py`）

満たす決定: 決定 4、受け入れ条件 #3604 の 9

`_acc_list` は `accounts.usages(state_dir, time.time())` を渡す。ほかは変えない。`state` は今の `_state` のまま（`再登録が要る` は `mark_relogin` の後に出る）。テストは `test_account_registry.py` の一覧の行で見る。

## 手順書（`relay-accounts.md`）

満たす決定: 決定 10、受け入れ条件 #3864 の 12〜14

書き直しの中身は設計文書の決定 10 の「選んだ案」にある。書いた後に、手順書の振る舞いの主張を 1 つずつ、この文書の関数と照らす。文言のテストは書かない。
