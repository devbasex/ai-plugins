# #3604 #3864 の詳細設計: アカウントの設定ディレクトリでの起動と、利用上限で停めない切り替え（見回り・使い切り・最後の砦）

設計文書 [issue-3604-3864-design.md](./issue-3604-3864-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。別ファイルにした理由は、新設する関数が 2 つ以上あることと、既存の関数のシグネチャを変えることである。シグネチャを変えるのは `accounts.usages`・`account_decide.Usage`・`before_limit`・`after_limit` ほかである。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   ├── account_dir.py *     … 起動の前の用意（確かめ・symlink・.claude.json の同期・書き戻し）
│   ├── account_choice.py *  … 起動できる候補・従量の接続と API キーの候補・替え先の環境（ラッパーと ndf plan が共有）
│   ├── session_env.py       … account_env・metered_env・key_env・METERED_FIXED（新設）、child_env（decided を足す）
│   ├── account_registry.py  … shared_config_dir・mark_relogin・generation・provider_on・PROVIDER_ENV・METERED_CHECKED・recovery_keys・key_value（新設）、store_dir（中身を寄せる）、check_decl（検証を足す）・SECRET_ENV（広げる）
│   ├── account_metered.py   … 接続確認の提供元の判定を provider_on へ寄せる
│   ├── accounts.py          … usages・usage_path（変更）、usage_dir・write_usage・held・hold・unhold・see_limit・limits_seen・forget（新設）
│   ├── account_decide.py    … Guards・group・order・guard・after_auth（新設）、Usage・before_limit・after_limit（変更）
│   ├── switch_decide.py     … SectionState.auth_pick・guard_pick・safe・switches_today・max_switches、GUARD_RESUME_PROMPT・AUTH_RESUME_PROMPT（新設）、_limit_or_auth（変更）
│   ├── observe.py           … tool_pending（新設）
│   ├── wrapper.py           … 候補を試す起動・指定の読み直し・見回りの替え・認証の失敗・書き戻し
│   └── cli_account.py       … _acc_list（呼び方だけ）、add・remove（使用の届けと forget）、use（unhold）、api-key-helper（新設）
├── src/ndf/adapters/
│   └── claude.py            … call_claude（認証の上書きの確かめ・上限での打ち直し）
├── src/ndf/domains/statusline/
│   └── statusline.sh        … 使用率の記録
├── contracts/
│   ├── domains/env.json     … child の列の account-set・account-drop、provider の列と 13 行
│   ├── domains/ndf-files.json … relay-usage の行・relay-auth-held の行・relay-limit-seen の行
│   └── limits.json          … relay.five-hour-guard・relay.weekly-prefer・relay.weekly-guard・relay.max-switches・relay.auth-hold・relay.limit-unknown（新設）、relay.usage-guard（削除）
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

依存の向きは次の表のとおりで、循環は無い。`wrapper` と `adapters/claude.py` からの依存は一方向である。

| モジュール | 依存する先 |
| --- | --- |
| `wrapper` | `account_choice`・`account_dir`・`session_env`・`accounts`・`account_decide`・`switch_decide`・`observe`・`account_registry` |
| `adapters/claude.py` | `account_choice`・`account_dir`（`ndf plan` が中継の部品を読むのはこの 2 つだけ） |
| `account_choice` | `account_dir`・`session_env`・`accounts`・`account_decide`・`account_registry`。ラッパーを読まない |
| `account_dir` | `account_registry`（保存先・排他・登録の読み・提供元の判定）と `signals`（置き換えの書き込み）と `contracts.facts`（`account-drop` の名前）だけ |
| `account_decide`・`switch_decide` | 時計とファイルを読まない純粋な関数のまま。`account_decide` はほかの中継の部品を読まない |
| `session_env` | `account_dir` を読まない。用意の済んだ設定ディレクトリのパスを引数で受ける |
| status line | Python を読まない。ラッパーが解いた書き先を、子の環境の `NDF_ACCOUNT_USAGE_FILE` で受ける |

設計文書は型の追加が少ないためクラス図を持たない。型は下の各節の「内部のデータと定数」に置く。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/account_dir.py` | 新設 | 登録済みアカウントの設定ディレクトリを起動できる形に用意し、終わりに書き戻す | `account_registry`・`signals`・`contracts.limits`・`contracts.facts` | モジュール・`Links`・`Prepared`・`Resolved`・`Local` | `is_local`・`link_shared`・`planned_links`・`config_file`・`shared_config_file`・`effective_env`・`auth_overrides`・`check_local`・`check_config`・`check_account`・`sync_config`・`ready`・`prepare`・`settle`（新設） |
| `src/ndf/domains/relay/account_choice.py` | 新設 | 起動できる候補・従量の接続と API キーの候補と、確定した替え先の子の環境を作る（ラッパーと `ndf plan` が共有） | `account_dir`・`session_env`・`accounts`・`account_decide`・`account_registry` | モジュール・`Fallback` | `candidates`・`fallbacks`・`target_env`・`retry_env`（新設） |
| `src/ndf/domains/relay/session_env.py` | 変更 | 登録済みアカウント・従量の接続・API キーの子の環境を足す | `contracts.facts`・`account_registry` | モジュール | `account_env`・`metered_env`・`key_env`（新設）、`child_env`（変更） |
| `src/ndf/domains/relay/account_registry.py` | 変更 | 共有の設定ディレクトリの求め方を 1 か所にし、再登録が要る印を書き、復帰先に使える API キーを返す | 今と同じ | モジュール | `shared_config_dir`・`mark_relogin`・`generation`・`provider_on`・`METERED_CHECKED`・`recovery_keys`・`key_value`（新設）、`store_dir`・`check_decl`・`SECRET_ENV`（変更） |
| `src/ndf/domains/relay/account_metered.py` | 変更 | 接続確認の提供元の判定を `provider_on` にそろえる | `account_registry` | モジュール | 既存の判定の 1 行（変更） |
| `src/ndf/domains/relay/accounts.py` | 変更 | 使用率のキャッシュと外した間の記録の読み書き | `account_decide`・`signals`・`contracts.facts` | モジュール | `usage_dir`・`write_usage`・`held_path`・`held`・`hold`・`unhold`・`limits_path`・`limits_seen`・`see_limit`・`forget`（新設）、`usage_path`・`usages`（変更）、`write_usages`（削除） |
| `src/ndf/domains/relay/account_decide.py` | 変更 | 候補の一覧と閾値を受けて、選ぶ順の組で替え先を決める | 無し | モジュール・`Pick`・`Usage`・`Guards` | `group`・`order`・`guard`・`after_auth`（新設）、`Usage`・`before_limit`・`after_limit`（変更） |
| `src/ndf/domains/relay/switch_decide.py` | 変更 | 見回りの替え先・認証の失敗の替え先を手に変え、替えた回数の上限を見る | `account_decide.Pick` | `SectionState` | `_limit_or_auth`・`_guard`（変更・新設） |
| `src/ndf/domains/relay/observe.py` | 変更 | 会話の記録の最後が結果を待つ Tool の呼び出しかを読む | `adapters.agents` | モジュール | `tool_pending`（新設） |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 候補を試して起動し、見回りで替え、終わりに書き戻す | 上の各部品 | `Wrapper`・`Section`・`Target`・`Choice`・`Inputs` | `_launch_target`・`_initial`・`_inputs`・`_candidates`・`_fallbacks`・`_pinned_or`・`_auth_pick`・`_guard_pick`・`_safe_point`・`_count_switch`・`_settle`・`_metered`・`_tell_at_start`（新設）、`_start`・`_state`・`_limit_pick`・`_on_switch`・`_on_resume`・`_loop`・`_end`（変更） |
| `src/ndf/domains/relay/cli_account.py` | 変更 | 一覧で今の時刻と世代を渡し、登録・削除・指定で記録を失効させ、API キーの値を `apiKeyHelper` として出す | 今と同じ | モジュール | `_acc_list`（呼び方だけ）・登録・削除・指定（変更）、`_acc_key_helper`（新設） |
| `src/ndf/adapters/claude.py` | 変更 | 登録済みアカウントで動く `claude -p` の前に認証の上書きを確かめ、上限に当たったら替え先で 1 回打ち直す | `account_dir`・`account_choice` | モジュール | `call_claude`（変更） |
| `contracts/domains/env.json` | 変更 | 子の環境の渡し方 | — | — | — |
| `contracts/domains/ndf-files.json` | 変更 | 使用率のキャッシュと外した間の記録の置き場 | — | — | — |
| `contracts/limits.json` | 変更 | 閾値 A・B・C・替えた回数の上限・外す秒数・上限の観測の既定の秒数 | — | — | — |
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
| `config_file` | `account_dir.py` | 新設 | `(config_dir: Path, named: bool) -> Path` | Claude Code が実際に読む設定ファイルの解決（移植元の `config_file` と同じ規則）。`<config_dir>/.config.json` があればそれ（旧形式が先）、無ければ `named`（設定ディレクトリが変数で指定されている）なら `<config_dir>/.claude.json`、偽なら `~/.claude.json`。本人の確かめ・同期・書き戻しはすべてこの関数の結果を使い、`.claude.json` を直に組み立てない |
| `shared_config_file` | `account_dir.py` | 新設 | `(environ: Mapping[str, str]) -> Path` | 共有側の設定ファイル。`config_file(account_registry.shared_config_dir(environ), named)`。`named` は `NDF_SHARED_CONFIG_DIR` が空でないか、それが無く `CLAUDE_CONFIG_DIR` があるときに真。`NDF_SHARED_CONFIG_DIR` が空文字（親に `CLAUDE_CONFIG_DIR` が無かった）なら `named` は偽で、既定は `~/.claude.json`（`~/.claude/.config.json` があればそれ） |
| `planned_links` | `account_dir.py` | 新設 | `(shared: Path, account: Path) -> dict[str, Path]` | 書かずに、`link_shared` の後に `projects`・`settings.json` が解ける先を返す（アカウント側が無いか symlink なら `<共有>/<項目>`、実体ならアカウント側の実体）。`ready` と `check_account` が使う |
| `auth_overrides` | `account_dir.py` | 新設 | `(env: Mapping[str, str], config_dir: Path, cwd: Path, argv: Sequence[str], user_settings: Optional[Path] = None) -> list[str]` | 子が実際に受け取る環境（`env`）・起動引数（`argv`）・作業ディレクトリで `effective_env` を打ち、その実効値と出所だけで、登録先の OAuth より上に来る認証の源と、既定でない API の接続先の項目名（値は返さない）を返す（下の「認証の上書き」）。空なら登録先の OAuth で既定の接続先へ送る。`user_settings` は利用者の設定の解けた先（省けば `<config_dir>/settings.json`） |
| `effective_env` | `account_dir.py` | 新設 | `(env: Mapping[str, str], config_dir: Path, cwd: Path, argv: Sequence[str], user_settings: Optional[Path] = None) -> Resolved` | Claude Code が採用する実効値とその出所。変数ごとの実効値と出所、実効の `apiKeyHelper` の出所、解けない源の名前、読んだ源のファイルの印を返す（下の「実効値の作り方」）。`auth_overrides` と従量の接続の確かめと起動の後の変更の検出が同じ結果を使う |
| `check_local` | `account_dir.py` | 新設 | `(store: Path, account: Path, shared_file: Path) -> Local \| str` | 下の「実体の確かめ」だけを行う（読まない・書かない）。通れば確かめた実体（実際に使うパスと `(st_dev, st_ino)`）を `Local` で、外れれば理由の符号を返す。`check_account` と `settle` が使い、`sync_config` へはこの `Local` だけを渡す |
| `check_config` | `account_dir.py` | 新設 | `(local: Local, registered: account_registry.Account) -> Optional[str]` | `local` の設定ファイルの形式と本人の確かめ。`config_file(account, True)` が `.claude.json` でなければ `legacy_config`、JSON の object でなければ `account_unreadable`、`oauthAccount` のメールアドレスと組織が登録と違えば `identity_mismatch`。`check_account` と `settle` が同じ関数を使う |
| `check_account` | `account_dir.py` | 新設 | `(store: Path, account: Path, registered: account_registry.Account, shared: Path, shared_file: Path, env: Mapping[str, str], cwd: Path, argv: Sequence[str]) -> Checked \| str` | `check_local(store, account, shared_file)` を先に行い、通ったときだけ認証ファイルを読んで確かめ、`check_config` を打ち、最後に `effective_env(env, account, cwd, argv, planned_links(shared, account)["settings.json"])` を 1 回打って、その `Resolved` に「当たる条件」を当てる（`auth_overrides` と同じ判定を同じ `Resolved` で行う）。`shared_file` は親の環境から解いた共有側の設定ファイル（下の「共有側の設定ファイルの解き方」）。`env` は子が受け取る環境（`ready`・`prepare` が親の環境から `account-drop` の変数を外し、`account-set` の `CLAUDE_CONFIG_DIR` を入れたもの）で、共有側の解き方には使わない。通れば `Checked(local, resolved)`（`local` は `check_local` の `Local`、`resolved` は打った `effective_env` の結果）、使えなければ理由の符号を返す |
| `sync_config` | `account_dir.py` | 新設 | `(local: Local) -> Optional[str]` | `local` が固定したパスの `.claude.json` の共有する設定の部分を 3 者で突き合わせて両側へ書く。パスを解き直さない。書けなければ理由の符号 |
| `ready` | `account_dir.py` | 新設 | `(store: Path, name: str, environ: Mapping[str, str], cwd: Path, argv: Sequence[str]) -> Optional[str]` | 排他を取らず、書かずに `check_account(…, shared_config_file(environ), …)` を打つ。返りが `Checked` なら成功として扱い（`Checked` は捨てる）、理由の符号ならそれを返す。続けて、`planned_links` の `projects` が共有側の `projects` か（無ければ `link_shared` が作るので真）の比べだけを行う（子を止める前の替え先の確かめに使う）。どれも通れば None。`projects` がまだ無い初回のアカウントを落とさない |
| `prepare` | `account_dir.py` | 新設 | `(store: Path, name: str, environ: Mapping[str, str], cwd: Path, argv: Sequence[str]) -> Prepared` | アカウントの排他の中で `check_account(…, shared_config_file(environ), …)` → `link_shared` → `projects_shared` の確かめ → `sync_config`（`check_account` の `Checked.local` を渡す）→ `account_registry.secure` → 使用の届けの書き込み（`starting`）を行う。`Prepared.resolved` には `Checked.resolved` を入れる |
| `settle` | `account_dir.py` | 新設 | `(store: Path, name: str, environ: Mapping[str, str], generation: str) -> Optional[str]` | アカウントの排他の中で、世代が今の `account.json` と同じで、`check_local(store, <名前の設定ディレクトリ>, shared_config_file(environ))` が `Local` を返し（排他を取った後に解き直した実体で確かめる）、その `Local` で `check_config` が None（起動時に許した `.claude.json` の形式で、登録の本人のまま）なら、その `Local` で `sync_config` を 1 回打つ。世代が違う・実体の確かめに外れる・形式か本人が違うときは共有側へ反映しない。どの場合も最後に使用の届けを消す。書けなければ理由の符号。引数に届けの ID（`lease: str`）を足す |
| `attach` | `account_dir.py` | 新設 | `(store: Path, name: str, lease: str, child_pid: int) -> None` | 登録の排他の中で、届けを `running` にして `child` を書く（下の「使用の届けの状態」）。子の起動に成功した直後に呼ぶ |
| `release` | `account_dir.py` | 新設 | `(store: Path, name: str, lease: str) -> None` | 登録の排他の中で、同期をせずに届けを消す。子の起動に失敗したときに呼ぶ |

共有側の設定ファイルの解き方: `ready`・`prepare`・`settle` はどれも、`shared_config_file(environ)` を 1 回だけ解く。`environ` はラッパーが受け取った親の環境で、`account_env` を重ねる前のものである。解いた結果は `check_account` と `check_local` へ引数で渡す。子の環境（`CLAUDE_CONFIG_DIR` をアカウントの設定ディレクトリへ替えたもの）では解かない。子の環境で解くとアカウント自身の `.claude.json` が返り、共有側の確かめがアカウント自身に当たるためである。入れ子のラッパー（親が登録済みアカウントの子）では、親の環境の `NDF_SHARED_CONFIG_DIR` が共有の設定ディレクトリを指す。そのため同じ式で共有側が解ける。

- `link_shared`
  - 事前条件: アカウントの排他を持っている（`prepare` の中）
  - 事後条件: 共有側の直下のアカウント固有でない項目ごとに、アカウント側の同じ名前が「`<共有>/<項目>` を指す symlink」か「実体（`skipped` に名前がある）」のどちらかである
  - 副作用: 共有側の `projects`（0700）・`settings.json`（`{}`、0600）の作成。アカウント側の symlink の作成と付け替え（一時の名前 `<項目>.<pid>.link` から `os.replace`）
- `check_account`
  - 事後条件: 返りが `Checked` なら、次がすべて成り立つ。実体の確かめに通っている。認証ファイルが読め、アクセストークンとリフレッシュトークンが空でない。`check_config` が None である。つまり、`config_file(account, True)` が `.claude.json` の JSON の object である。`oauthAccount` のメールアドレスと組織は `account.json` と同じである。`account.json` に組織が無ければメールアドレスだけ比べる
  - 副作用: 読むだけ（排他を取らず、バックアップも消さない）。実体の確かめに落ちたファイルは開かない。読むファイルは `Local` の固定したパスから開き、開いた後に `os.fstat` の `(st_dev, st_ino)` が `Local` と同じことを確かめる（違えば `not_account_local`）。値は返りにも例外の文にも入れない
- `sync_config`
  - 事前条件: アカウントの排他を持っている。`local` は同じ排他の中の `check_local` が返したもの（`prepare` では `check_account` の返りの `Checked.local`、`settle` では排他を取った後の `check_local` の返り）
  - 事後条件: 両側の `projects`（プロジェクトのパス → 項目の 2 層の葉）と `mcpServers`（名前の 1 層の葉）が同じ値である。同期のバックアップ（`.ndf-shared-base.json`、0600）は、その値とアカウント側の `userID` を持つ。`hasCompletedOnboarding`・`lastOnboardingVersion` はアカウント側に無ければ共有側の値が入る。ほかのキー（`oauthAccount` など）は変わらない
  - 副作用: `local` の固定したパス（アカウント側の `.claude.json`、共有側の解決先の通常ファイル）だけを使い、名前を解き直さない。Claude Code 本体と同じ `<固定したパス>.lock` のディレクトリを、アカウント側・共有側の順に取る。両側の固定したパスが同じなら、書かずに `same_file` を返す（二重の排他を取らない）。本体の錠を 2 つとも取った後、読む前に、両側の固定したパスを `os.lstat` し直す。確かめるのは、通常のファイル（symlink でない）であること・`st_nlink` が 1 であること・`(st_dev, st_ino)` が `local` と同じことである。無いことを `local` が記録していれば、まだ無いことを確かめる。1 つでも違えば、錠を返して `not_account_local` を返す。本体の錠を待つ間に、別のレーンの Claude Code や利用者が置き換えたファイルを読み書きしないためである。読むときは、開いたファイルの `os.fstat` でもう一度同じ比べをする。書く手順は下の「書く手順と途中で止まった後」の通りである。置き換えは、固定したパスと同じディレクトリの一時ファイルからの `os.replace` で行う。`os.replace` はリンクを辿らないので、確かめの後に名前が symlink へ替えられても、リンクの先へは書かない。権限は置き換える前のファイルの権限を保つ
- `prepare`
  - 事後条件: `Prepared.ok` が真なら、受け入れ条件 #3604 の 4・6 が成り立つ。偽なら symlink の作成の途中で止まっていてよい（次の `prepare` が付け替える）
  - 副作用: `link_shared`・`sync_config` と、理由が `RELOGIN_REASONS` のとき `account_registry.mark_relogin`

#### 実体の確かめ（`check_local`。`check_account` の先頭と `settle` の排他の中）

10 系の同期は実パスへ書く。そのため、symlink やハードリンクを辿った先が共有側や別のアカウントだと、共有側の認証を読み書きしたり、同じファイルに二重の排他を取ったりする。実パスと symlink の比べだけでは、ハードリンク（同じ inode を持つ別の名前）を見分けられない。そこで `os.lstat` の `st_nlink` と `(st_dev, st_ino)` も比べる。読む・書く前に次をすべて確かめ、1 つでも外れれば `not_account_local` で飛ばす（読み書きしない）。

| 対象 | 条件 |
| --- | --- |
| アカウントの設定ディレクトリ | symlink でない。実パスが `<保存先の実パス>/<名前>` である |
| 認証ファイル（`.credentials.json`） | 有れば symlink でない通常のファイルで、`st_nlink` が 1、実パスの親がアカウントの設定ディレクトリの実パスである |
| Claude が読む設定ファイル（`config_file(account, True)`） | 同上 |
| 同期のバックアップ（`.ndf-shared-base.json`）と途中の記録（`.ndf-shared-base.next.json`） | 同上（無ければよい） |
| 共有側の設定ファイル（`shared_config_file`） | 共有側では symlink を許す（`~/.claude.json` を dotfiles の symlink にしている利用者がいるため）。名前を `os.path.realpath` で 1 回解き、以後はその解決先だけを使う。解決先の実パスが保存先の下に無い（どのアカウントの設定ディレクトリも指さない）。有れば、`os.lstat` を解決先（リンク自体ではない）に当て、通常のファイル（symlink でない）で、`st_nlink` が 1 で、`(st_dev, st_ino)` が上の 4 つのどれとも違う |

通れば `check_local` は `Local` を返す。以後の読み書きはこの `Local` のパスだけを使い、名前を解き直さない。

| フィールド | 型 | 中身 |
| --- | --- | --- |
| `account` | `Path` | アカウントの設定ディレクトリの実パス（`<保存先の実パス>/<名前>`） |
| `config` | `Path` | アカウント側の Claude が読む設定ファイル（`config_file(account, True)`）の実パス。無ければ作る先のパス |
| `shared` | `Path` | 共有側の解決先 |
| `ids` | `dict[str, Optional[tuple[int, int]]]` | 項目ごとの `(st_dev, st_ino)`。無ければ None |

`st_nlink` が 1 であれば、同じ inode を指す名前はほかに無い。そのため、共有側や別のアカウントの `.credentials.json`・`.claude.json` と同じ実体であることはない。確かめは、`prepare` の `check_account` の先頭と、`settle` が排他を取った後の 2 か所で行う。さらに `sync_config` が本体の錠を取った後に、同じ対象の種類・リンク数・同一性を `Local` と比べ直す。登録の排他は、別のレーンの Claude Code や利用者による置き換えを止めないためである。子が動いている間に、利用者が設定ファイル・バックアップ・共有先を置き換えることがある。そのときも `settle` は、書き戻す前に置き換えた後の実体で確かめ直す。外れれば書き戻さず、使用の届けだけを消して `not_account_local` を返す。Claude Code 自身も `.claude.json` を置き換えで書く。そのため `prepare` と `settle` の間に inode が替わるのは普通のことで、`settle` は `prepare` の `Local` を使わずに解き直す。

#### 認証の上書き（`auth_overrides`・`effective_env`）

認証の優先順位（STD2）で `/login` の OAuth より上に来る源は、環境変数を外しても設定ファイル・起動引数・プロファイルから入る。判定は、Claude Code が採用する実効値とその出所だけで行う。源ごとに値があるかでは判定しない（上位の源の空文字が下位の値を打ち消すため）。`effective_env` が下の「実効値の作り方」で実効値を作り、`auth_overrides` は下の「当たる条件」を実効値に当てる。

読む源:

| 源 | 読むか |
| --- | --- |
| 子の環境（`env`） | 常に（`call_claude` は環境を変えずに起動するので、親から継いだ値もここに入る） |
| 管理の設定のファイル（`/etc/claude-code/managed-settings.json` と `/etc/claude-code/managed-settings.d/*.json`） | 常に（`--setting-sources` に依らない） |
| 管理の設定のサーバーからの配布の控え（`<config_dir>/remote-settings.json`） | 常に |
| 起動引数の `--settings <JSON かファイル>` | `argv` にあるとき |
| 利用者の設定（`user_settings`。省けば `<config_dir>/settings.json`） | `argv` に `--setting-sources` が無いか、その一覧に `user` があるとき |
| プロジェクトの設定（`<cwd>/.claude/settings.json`・`<cwd>/.claude/settings.local.json`） | 同上の `project`・`local` |
| Anthropic のプロファイル（`ANTHROPIC_CONFIG_DIR`、無ければ `~/.config/anthropic`） | 常に |

実効値の作り方（STD6・STD8）:

| 項目 | 選び方 |
| --- | --- |
| `env` の各変数 | 変数ごとに、その変数を定義している源のうち最も優先の高いものの値を採る。空文字も値として採る（下位の値を打ち消す）。優先は 管理の設定 > `--settings` > プロジェクトのローカル > プロジェクト > 利用者 > 子の環境（設定の `env` は親から継いだ値を上書きする）。プロジェクトとローカルの `env` は、Claude Code が無視する変数（`CLAUDE_CONFIG_DIR`・`CLAUDE_CODE_OAUTH_TOKEN`・`CLAUDE_CODE_OAUTH_SCOPES` ほか。STD8）を重ねない |
| 管理の設定の中の `env` | `managedSourcesBehavior` に依らず、変数ごとに管理の源を上位から埋める（サーバーからの配布の控え > 管理の設定のファイル）。ファイルの中では `managed-settings.json` の後に `managed-settings.d/*.json` を名前の順に重ね、後のファイルが同じ変数を置き換える |
| 管理の設定の中の `apiKeyHelper` | 選ばれた管理の源（ポリシーの項目を 1 つでも持つ最上位の源。読めない管理の設定のファイルも「ある」として数える）の値だけを見る。選ばれた源に無ければ、下位の管理の源の値は採らない（`apiKeyHelper` は最上位の源だけから読む項目。`managedSourcesBehavior` が `merge` でも同じ） |
| `apiKeyHelper` の全体 | 管理の設定で決まればその値。管理の設定のどの源もポリシーの項目を持たなければ、`--settings` > ローカル > プロジェクト > 利用者 の最上位の値 |
| 出所 | 実効値を採った源の名前（`管理の設定（サーバーの控え）`・`管理の設定（managed-settings.d/20-x.json）`・`--settings`・`利用者の設定`・`プロジェクトの設定`・`子の環境` など）。項目名に添える |

`Resolved` は次のフィールドを持つ。

| フィールド | 型 | 中身 |
| --- | --- | --- |
| `env` | `dict[str, str]` | 実効値 |
| `origin` | `dict[str, str]` | 変数ごとの出所 |
| `helper` | `Optional[str]` | 実効の `apiKeyHelper` の出所。無ければ None |
| `unresolved` | `list[str]` | 解けない源の名前 |
| `stamps` | `dict[str, Optional[tuple]]` | 読んだ源のファイルごとの `(st_mtime_ns, st_ino, st_size)`。無ければ None |

当たる条件（どれか 1 つで当たる。項目名は `<変数か項目>（<出所>）`）:

| 当たる条件 | 返り |
| --- | --- |
| 実効の `env` で、`account-drop` の認証の値の変数（`ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN` ほか）が空でない | 項目名 |
| 実効の `env` で、`PROVIDER_ENV` の変数の `provider_on` が真 | 項目名 |
| 実効の `env` で、`PROVIDER_ENV` の変数の `provider_on` が None（解けない値。例: `CLAUDE_CODE_USE_VERTEX=maybe`）が 1 つでもある | `auth_unresolved`（項目名）。Claude Code がどう読むか分からないので、起動先として確定しない |
| 実効の `apiKeyHelper` が空でない | 項目名（`apiKeyHelper（<出所>）`） |
| 実効の `env` の `ANTHROPIC_BASE_URL` が空でなく、既定の `https://api.anthropic.com`（末尾の `/` を除いて比べる）でない | 項目名（`ANTHROPIC_BASE_URL（<出所>）`）。登録先の OAuth が選ばれていても、要求と会話はその宛先へ送られるため |
| 実効の `env` に `ANTHROPIC_PROFILE` があるか、`ANTHROPIC_FEDERATION_RULE_ID` と `ANTHROPIC_ORGANIZATION_ID` がそろう。または Anthropic の設定ディレクトリの有効なプロファイルの認証の形が `oidc_federation` | 項目名 |

既定でない API の接続先を許す条件: 登録済みアカウントの子では許さない。プロキシやゲートウェイを通す利用者は、従量の接続か既定のログインで動かす。従量の接続では宣言に `ANTHROPIC_BASE_URL` を入れ、下の「従量の実効値の照合」で宣言と照合する。親の環境の `ANTHROPIC_BASE_URL` も、子の環境として実効値に入るので当たる。`account-drop` で黙って外すと、利用者が意図した経路を外れて既定の宛先へ送る。そのため外さずに飛ばし、理由を出す。`HTTPS_PROXY` などの通信の経路の変数は宛先を変えないので見ない。

打ち消しと選択の例:

| 設定 | 実効値 | 判定 |
| --- | --- | --- |
| 利用者の設定の `env` に `CLAUDE_CODE_USE_BEDROCK=1`、管理の設定のファイルの `env` に `CLAUDE_CODE_USE_BEDROCK=""` | `""`（出所は管理の設定） | 当たらない。起動する |
| 利用者の設定の `env` に `CLAUDE_CODE_USE_BEDROCK=0`、`--settings` の `env` に `CLAUDE_CODE_USE_BEDROCK=1` | `1`（出所は `--settings`） | 当たる |
| サーバーの控えがポリシーの項目（`env` だけ）を持ち、`managed-settings.d/20-x.json` に `apiKeyHelper` がある | `apiKeyHelper` は無し（選ばれた源は控えで、下位の管理の源から採らない） | 当たらない |
| 同じ控えの `env` に `ANTHROPIC_API_KEY` が無く、`managed-settings.json` の `env` にある | `ANTHROPIC_API_KEY` はファイルの値（`env` は変数ごとに下位から埋める） | 当たる |
| 子の環境に `ANTHROPIC_BASE_URL=https://proxy.example.com` | その値（出所は子の環境） | 当たる |

解けない源の扱い:

| 場面 | 扱い |
| --- | --- |
| 管理の設定のファイル・`managed-settings.d`・`--settings` のファイルがあるが読めない・JSON の object でない | `auth_unresolved`（項目名は源の名前）で飛ばす。Claude Code も読めない管理の設定では起動しないか、より厳しい値で動くので、通して起動しない |
| `argv` の `--settings`・`--setting-sources` の値が解けない（値が欠ける） | 同上 |
| サーバーからの配布の控えが無い（そのアカウントで一度も取っていない） | 起動の前には分からない。控えが無いことを上書きとはせずに進め、取った後の次の起動から見る（「未確認のまま残ること」） |
| macOS の plist・Windows のレジストリ | 中継の対応は Linux だけなので見ない |

- 子（`_launch_target`）: 登録済みアカウントの候補で当たれば `auth_overridden`（解けない源は `auth_unresolved`）で飛ばし、次の候補へ進む。理由の 1 行には項目名（例: `apiKeyHelper（利用者の設定）`）だけを出す。`argv` はラッパーが子に渡す実際の起動引数
- `ndf plan`（`call_claude`）: 環境の `NDF_CLAUDE_ACCOUNT` が登録済みアカウントの名前なら、起動の直前に認証の上書きを確かめる。打つのは `auth_overrides(env, Path(env["CLAUDE_CONFIG_DIR"]), cwd, argv)` である。渡すのは、最終的に使う `argv`・環境・作業ディレクトリである。`minimal` の起動は `--setting-sources ''` を渡すので、利用者・プロジェクトの設定は読まれず、管理の設定と `--settings` だけを見る。当たれば `claude -p` を起動せず、項目名と直し方（その設定から項目を外す）を入れた失敗で返す。worker の作業ディレクトリはラッパーと違いうるので、プロジェクトの設定はその作業ディレクトリで見る
- Claude apps gateway のログインは保存の形が公開されておらず、ここでは見ない（「未確認のまま残ること」）

#### 起動の後の設定の変更

Claude Code は利用者・`--settings`・管理・プロジェクトの設定の `env` を、起動の後も実行中のセッションへ反映する。反映は、保存で実効の `env` が変わるたびに行われる（STD8）。起動の前の確かめだけでは、動いている間に共有の `settings.json` へ API キーや接続先を足した変更を扱えない。

固定はしない。実行中も認証・接続先を固定できる渡し方が無いためである。`--settings` で認証の変数を空文字にしても、管理の設定はその上に来る。`apiKeyHelper` を `--settings` で打ち消せるかは文書に無い。`--setting-sources` から `user` を外せば利用者の設定を読まなくなるが、利用者のほかの設定（許可・hooks・プラグイン）まで失う。そのため変更を検出し、次の切れ目で替える。

| 場面 | 動作 |
| --- | --- |
| 見回りのたび（登録済みアカウントと従量の接続の子） | 起動のときの `Resolved.stamps` の源のファイルを `os.stat` だけで比べる（ファイルは開かない。CLI の起動・API の呼び出しは増えない） |
| 印が変わった | その回だけ `effective_env` を同じ `env`・`cwd`・`argv` で打ち直し、登録済みアカウントなら `auth_overrides`、従量の接続なら「従量の実効値の照合」を当てて、印を更新する |
| 打ち直しで当たった | 画面に 1 行（`ndf-relay: 動いている間に <項目名> が足された。次の区切りでアカウントを替える`）と `log.jsonl` に `{"kind": "auth_drift", "account", "items"}` を 1 回出す。`Section.drift_at` を立て、`_state` は `auth_pick` に `after_auth` の結果を入れる（`accounts.hold` はしない。アカウントの不具合ではないため）。替え先の確定と子の止め方は認証の失敗と同じ（下の「子を止める前の替え先の確定」の `auth` の行）で、今のアカウントも同じ確かめで落ちるので、替え先が無ければ子を残して `stalled` の 1 行を出す |
| 打ち直しで当たらない | 何もしない |

制約（「人へ残す判断」に置く）: 変更を保存してから子を止めるまでの要求は、変更後の資格情報と接続先で送られる。この間は、見回りの間隔と回答待ちの間である。変更後の資格情報が別の claude.ai のアカウントなら、その間に status line が受けた使用率として、その本人の値がこの名前の記録へ入りうる。API キーとクラウド事業者の接続では `rate_limits` が無いので書かれない（STD5）。検出したときは、ラッパーがその世代の使用率のファイルを消す（`accounts.usage_path`）。次の判断は「使用率の分からない候補」として行う。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check_account` | `no_credentials` | 認証ファイルが無い・読めない・JSON でない・アクセストークンが空 | 認証ファイルの読み | 呼び出し元へ返す | 呼び出し元のラッパーが 1 行 | 何も書かない。登録し直せば次の起動で通る |
| `check_account` | `no_refresh` | リフレッシュトークンが空 | 同上 | 呼び出し元へ返す（`prepare` が印を立てる） | 同上と再登録の知らせ | `ndf relay account add <名前>` の後に通る |
| `check_account` | `account_unreadable` | アカウント側の `.claude.json` が無い・JSON の object でない | `check_config` | 呼び出し元へ返す | 同上 | 何も書かない（`check_account` は読むだけで、`prepare` もバックアップを消さない）。登録し直すと、登録のコマンドが登録の排他の中でバックアップと途中の記録を消すので、次の同期は最初の同期になる |
| `check_account` | `identity_mismatch` | `oauthAccount` が登録と別の本人 | `check_config` | 呼び出し元へ返す（`prepare` が印を立てる） | 同上と再登録の知らせ | 印のほかは何も書かない。登録し直した後は同上 |
| `settle` | `identity_mismatch`・`legacy_config`・`account_unreadable` | 子が動いている間に、アカウント側の設定ファイルが別の本人の `.claude.json` に替わった・旧形式の `.config.json` が置かれた・壊れた | 排他の中の `check_config` | 共有側へ反映せず、使用の届けを消して返す | 画面に 1 行と `log.jsonl` に 1 行 | 何も書かない。次の `prepare` が同じ理由で起動を飛ばし、`RELOGIN_REASONS` なら印を立てる |
| `check_account` | `not_account_local` | 実体の確かめに外れた（symlink・ハードリンク・共有側や別のアカウントを指す・共有側の設定と同じ実体） | 実パス・`st_nlink`・`(st_dev, st_ino)` の比べ | 読み書きの前に返す | 呼び出し元のラッパーが 1 行（項目の名前だけ。参照先のパスは出さない） | 何も書かない。利用者が symlink・ハードリンクを独立した実体に戻せば通る |
| `check_account` | `auth_unresolved` | 認証の上書きを確かめる源が読めない・解けない・実効の提供元の変数の値が解けない | `effective_env`・`provider_on` | 呼び出し元へ返す | 同上（源の名前だけ） | その設定を直せば次の起動で通る |
| `settle` | `not_account_local` | 子が動いている間に設定ファイル・バックアップ・共有先が置き換えられ、排他の中の確かめに外れた | `check_local` | 書き戻さず、使用の届けを消して返す | 画面に 1 行と `log.jsonl` に 1 行 | 何も書かない。直した後の次の `prepare` が確かめ直す |
| `check_account` | `legacy_config` | アカウント側に旧形式の `.config.json` がある | `config_file` | 呼び出し元へ返す（`prepare` が印を立てる） | 同上と再登録の知らせ | `ndf relay account add <名前>` が旧ファイルを `backups/.config.json.<UTC の時刻>` へ移してから登録するので、その後に通る |
| `check_account` | `auth_overridden` | `auth_overrides` が空でない（既定でない `ANTHROPIC_BASE_URL` を含む） | `auth_overrides` | 呼び出し元へ返す | 同上（項目名と出所だけ） | その設定から項目を外せば次の起動で通る |
| `sync_config` | `same_file` | 両側の設定ファイルの実パスが同じ | 実パスの比べ | 書かずに返す（`prepare` は起動を止めない。本人の確かめは通っている） | `log.jsonl` に 1 行 | — |
| `sync_config` | `not_account_local` | 本体の錠を待つ間に、固定したパスの種類・リンク数・`(st_dev, st_ino)` が `Local` と変わった | 錠を取った後の `os.lstat` と、開いた後の `os.fstat` | 錠を返し、読み書きせずに返す（`prepare` は起動を飛ばす。`settle` は書き戻さず使用の届けを消す） | 呼び出し元のラッパーが 1 行（項目の名前だけ） | 何も書かない。次の `prepare` が解き直して確かめる |
| `settle` | `reregistered` | 起動したときの登録の世代と今の `account.json` の世代が違う | 世代の比べ | 書き戻さずに返す | `log.jsonl` に 1 行 | 新しい登録の次の起動の前の同期でそろう |
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
| `sync_config` | 途中の記録があり、その `userID` がアカウント側と同じで、最初の同期のものか `userID` がバックアップと同じ | 「書く手順と途中で止まった後」の規則で側ごとに基準を選んでから、下の葉の規則で突き合わせる。読めない・`userID` が違う途中の記録は、一時ファイルと一緒に無いものとして消す |
| `sync_config` | 上の行に当たらず、バックアップが無い・読めない・`userID` が違う | 最初の同期。両側を共有側の値にそろえる（アカウント側にだけある葉も消す） |
| `sync_config` | 上の行に当たらず、片側に `projects`・`mcpServers` のキーそのものが無い | そのキーを空の object として読み、下の葉の規則で突き合わせる（キーの欠落は「その下の葉をすべて消した」と同じ） |
| `sync_config` | 葉の値が、共有側・アカウント側のどちらもバックアップと同じ | そのまま |
| `sync_config` | 片側だけがバックアップと違う（値を変えた・葉を消した・葉を足した） | 違う側の状態（消したなら両側から消す） |
| `sync_config` | 両側がバックアップと違い、両側が同じ状態（同じ値・どちらも消した） | その状態 |
| `sync_config` | 両側がバックアップと違い、状態が違う（片側が消し、もう片側が変えたときを含む） | 共有側の状態（共有側が消していれば消す、変えていればその値） |

葉の「状態」は値か「無い」のどちらかで、比べは JSON の値の等しさで行う。たとえば、バックアップに `mcpServers.a` だけがあるとする。アカウント側で `a` を消して `mcpServers` が `{}` かキーごと無いとき、共有側が `a` を変えていなければ両側から `a` が消える。共有側が同時に `a` を変えていれば共有側の `a` が両側に残る。最初の同期の判断は葉の規則より先に行い、アカウント側のキーの欠落を最初の同期の理由にしない。

- 繰り返しの終わる条件: `link_shared` は共有側とアカウント側の直下を 1 回ずつ数え上げる。再帰しない

#### 書く手順と途中で止まった後

値の比べで書き込みの進み具合を推し量ると、同期の後に利用者が値を前の A へ戻した操作と、書けていない側とを区別できない。そこで、書き込みの進み具合は一時ファイルが残っているかで記録し、値の比べは利用者の変更の判定にだけ使う。

書く手順（1 回の書き込みを「回」と呼び、回ごとに乱数の識別子 `<回>`（`secrets.token_hex(8)`）を作る）:

1. 両側へ書く値 B を作り、2 つの一時ファイルに書いて `fsync` する。一時ファイルは、アカウント側の `.claude.json.ndf-sync.<回>.tmp` と、共有側の解決先の隣の `<名前>.ndf-sync.<回>.tmp` である。回ごとに名前が違うので、前の回の一時ファイルを上書きしない。`.claude.json` の前方一致なので `link_shared` は張らない
2. 途中の記録（`.ndf-shared-base.next.json`、0600）を置き換えで書く。中身は B・`userID`・`<回>`・2 つの一時ファイルのパス・側ごとの基準（`base.account`・`base.shared`。それぞれ値の object か `"first"`（最初の同期をやり直す）か `"B"`（B と同じ））である。通常の回では両側の基準は前のバックアップ A（無ければ `"first"`）
3. アカウント側の一時ファイルを固定したパスへ `os.replace` する
4. 共有側の一時ファイルを固定したパスへ `os.replace` する
5. 途中の記録をバックアップ（`.ndf-shared-base.json`）へ `os.replace` する
6. 途中の記録が指さない `*.ndf-sync.*.tmp`（前の回の残り）を消す

`os.replace` は一時ファイルの名前を消す。そのため途中の記録があるときは、値に依らず次のように決まる。途中の記録が指すある側の一時ファイルが無ければ、その側は B に置き換え済みである。あれば、まだ置き換えていない。途中の記録が指さない一時ファイルは、どの判定にも使わずに消してよい（1 の途中で止まった回の残りか、6 の前に止まった前の回の残り）。途中の記録が無ければ、残った一時ファイルを消して前の同期のまま扱う。

途中の記録があるときの次の同期（復旧）: 側ごとの基準を下の表で決め、上の葉の規則を側ごとの基準で当てて新しい値 B' を作る。続けて、上の 1〜6 を新しい `<回>` で行う。2 で書く途中の記録の `base.account`・`base.shared` には、表で決めた基準そのものを入れる（一時ファイルの有無から決め直さない）。前の回の一時ファイルは、新しい途中の記録が置き換えで書かれた後の 6 で初めて消す。そのため、復旧のどの位置で再び止まっても、そのとき残っている途中の記録と、それが指す一時ファイルの有無だけで同じ表が当たる。新しい途中の記録を書く前に止まれば前の回の記録と一時ファイルがそのまま残り、書いた後に止まれば新しい回の基準が記録に入っている。

| 側の状態（途中の記録が指す一時ファイル） | その側の基準 |
| --- | --- |
| 一時ファイルが無い（置き換え済み） | B。以後のその側の B との違いは、すべて止まった後の利用者や Claude Code の変更 |
| 一時ファイルがあり、記録の基準が値の object（前のバックアップ A か、前の復旧が決めた基準） | 記録の基準（置き換えていないので、その側の基準との違いは止まる前からの変更） |
| アカウント側の一時ファイルがあり、記録の基準が `"first"` | 最初の同期をやり直す（共有側の値にそろえ、アカウント側にだけある葉も消す。止まる前と同じ判断） |
| 共有側の一時ファイルがあり、記録の基準が `"first"` か `"B"` | B（最初の同期の B は共有側の値から作るので、置き換えの前の共有側は B と同じ） |

たとえば、アカウント側を B に置き換えた後・共有側の前に止まり、共有側が A のままだとする。復旧は、アカウント側の基準を B、共有側の基準を A とし、新しい途中の記録に `base.account = B`・`base.shared = A` を書く。その直後にもう一度止まっても、次の復旧は前の回の一時ファイルの有無ではなく新しい記録の基準を読むので、共有側の A を「利用者が B から A へ戻した」とは読まない。

| 止まった時点 | 次の同期が採る値 |
| --- | --- |
| 途中の記録を書く前 | 前の同期と同じ判断をやり直す |
| 途中の記録を書いた後、アカウント側の前 | 両側の基準は A。前の同期と同じ判断をやり直す |
| アカウント側を置き換えた後、共有側の前 | アカウント側の基準は B、共有側の基準は A。共有側が A のままなら B を採る。止まった後に利用者が共有側の同じ葉を変えていれば共有側の値を採る |
| 共有側を置き換えた後、途中の記録をバックアップへ移す前 | 両側の基準は B。止まった後にアカウント側だけを C に変えれば C が両側へ移る。アカウント側だけを A に戻せば A が両側へ移る（B を再び当てない）。アカウント側だけで葉を消せば両側から消える |
| 最初の同期で、アカウント側を置き換えた後、共有側の前 | 両側の基準は B。止まった後にアカウント側で葉を変えれば、その値が両側へ移る |

同期を終えた時点で途中の記録と一時ファイルは無く、バックアップが両側と同じ値を持つ。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `LOCAL_PREFIXES` | 定数 | `account_dir.py` | `(".credentials.json", ".claude.json", ".oauth_refresh.lock")`（10 系と同じ。名前は `account_registry` の `CRED_FILE`・`CONFIG_FILE` から引く） |
| `LOCAL_NAMES` | 定数 | `account_dir.py` | `(".config.json", "backups", "policy-limits.json", "policy-limits.json.stamp.json", "remote-settings.json", "mcp-needs-auth-cache.json", ".ndf-statusline-auth.json", "account.json", "usage.json", ".ndf-shared-base.json", ".ndf-shared-base.next.json")`（10 系に途中の記録を足す。`account.json`・`usage.json`・`.ndf-shared-base.json`・`.ndf-shared-base.next.json` は `account_registry` の定数から引く） |
| `SHARED_KEYS` | 定数 | `account_dir.py` | `("projects", "mcpServers")` |
| `ONBOARDING_KEYS` | 定数 | `account_dir.py` | `("hasCompletedOnboarding", "lastOnboardingVersion")` |
| `LOCK_STALE` | 定数 | `account_dir.py` | 10 秒（本体と同じく、これより古い `<実パス>.lock` は残骸として除く） |
| `RELOGIN_REASONS` | 定数 | `account_dir.py` | `("no_refresh", "identity_mismatch", "legacy_config")` |
| 登録の世代 | 設定値 | `account.json` の `generation` | 登録・再登録のたびに新しい乱数の 16 進 32 字（`secrets.token_hex(16)`）。無い旧い記録は空文字の世代として扱う |
| 使用の届け | 設定値 | `<保存先>/.leases/<名前>/<届けの ID>.json` | `{"generation", "state", "owner", "child"}`。`state` は `starting`（用意の済んだ後・子の起動の前）か `running`（子の起動の後）。`owner` と `child` はそれぞれ `{"pid", "start"}`（`start` は `/proc/<pid>/stat` の開始時刻。pid の使い回しと区別する）で、`child` は `starting` の間 null。下の「使用の届けの状態」 |
| `REASONS` | 定数 | `account_dir.py` | 理由の符号 → 画面の 1 行の文（値を含まない。例: `no_refresh` → `リフレッシュトークンが空。ndf relay account add {name} で登録し直す`） |
| `Links` | 型 | `account_dir.py` | `added: int`・`skipped: list[str]`・`local_only: list[str]`・`projects_shared: bool` |
| `Resolved` | 型 | `account_dir.py` | `env: dict[str, str]`（実効値）・`origin: dict[str, str]`（変数ごとの出所）・`helper: Optional[str]`（実効の `apiKeyHelper` の出所）・`unresolved: list[str]`（解けない源の名前）・`stamps`（読んだ源のファイルの印） |
| `Local` | 型 | `account_dir.py` | `account: Path`・`config: Path`・`shared: Path`（共有側の解決先）・`ids: dict[str, Optional[tuple[int, int]]]`（`check_local` が確かめた項目ごとの `(st_dev, st_ino)`） |
| `DEFAULT_API_URL` | 定数 | `account_dir.py` | `"https://api.anthropic.com"`（登録済みアカウントの子に許す唯一の `ANTHROPIC_BASE_URL`） |
| 同期の一時ファイル | 定数 | `account_dir.py` | `SYNC_TMP_FORMAT = "{path}.ndf-sync.{round}.tmp"`（`<固定したパス>.ndf-sync.<回>.tmp`。`<回>` は書き込みの回ごとの乱数） |
| 管理の設定の置き場 | 定数 | `account_dir.py` | `MANAGED_FILES = (Path("/etc/claude-code/managed-settings.json"),)`・`MANAGED_DIR = Path("/etc/claude-code/managed-settings.d")`・`REMOTE_SETTINGS = "remote-settings.json"`（`LOCAL_NAMES` の名前から引く）。テストは引数で差し替える |
| `Prepared` | 型 | `account_dir.py` | `ok: bool`・`reason: Optional[str]`・`config_dir: Path`・`links: Links`・`sync: Optional[str]`・`generation: str`（用意したときの登録の世代。`settle` へ渡す）・`resolved: Optional[Resolved]`（`check_account` が返した `Checked.resolved`。ラッパーが `Section.resolved` に控え、起動の後の変更の検出に使う）・`lease: Optional[str]`（書いた使用の届けの ID。`attach`・`release`・`settle` へ渡す） |
| `Checked` | 型 | `account_dir.py` | `local: Local`・`resolved: Resolved`（`check_account` の成功の返り。`auth_overrides` の判定に使った `effective_env` の結果） |
| 本体の排他の待ち | 設定値 | `contracts/limits.json` の `claude-account-dir.lock` | 2 秒（既にある） |
| 登録の排他の待ち | 設定値 | `contracts/limits.json` の `claude-accounts.lock` | 30 秒（既にある） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じアカウントを使う 2 つのレーンの `prepare`・`settle` | アカウントの設定ディレクトリ | `account_registry.locked(store, name)`（filelock） | `link_shared` は何度打っても同じ形になる。中断した付け替えは一時の名前を次に消して作り直す |
| `sync_config` と動いている claude | 両側の `.claude.json` | 本体と同じ `<実パス>.lock` のディレクトリ（`os.mkdir` で取る。10 秒より古ければ消して取り直す） | 置き換えで書くので、途中で止まっても前の中身が残る |
| 登録のコマンド（再登録）と `prepare` | `account.json`・認証ファイル | 同じ `account_registry.locked` | 再登録はバックアップ・途中の記録・アカウント側の一時ファイルを消すので、次の同期は最初の同期になる（バックアップを消すのは登録のコマンドだけ） |
| 登録のコマンド（再登録・削除）と、その名前で動いている子 | アカウントの設定ディレクトリの全体 | 使用の届け（上の「使用の届けの状態」）。`account add`・`account remove` は登録の排他の中で、生きている届けが 1 つでもあれば何も変えずに止める（`アカウント <名前> を動いているレーンが使っている。その子を /exit で終えてから登録し直す`、終了コード 1）。`prepare`・`attach`・`release`・`settle` は同じ排他の中で届けを書き換えるので、確かめと書き込みの間に割り込まれない | 子が動いている間は同じパスを別の登録に替えないので、旧い子の保存が新しい登録を上書きしない。`settle` は届けの世代と `account.json` の世代を比べ、違えば書き戻さない（`reregistered`）。届けの残骸（`owner` も `child` も死んでいる）は次の登録のコマンドが消して進む |

#### 使用の届けの状態

届けは、その名前の設定ディレクトリを使うプロセスが生きている間、登録・削除のコマンドを止めるためにある。届けを書くのは `prepare`、子の pid を足すのは `attach`、消すのは `settle` か `release` である（`account_dir.py` に新設。どれも登録の排他の中で書く）。

| 状態 | 入る時点 | 生きているとみなす条件 | 出る時点 |
| --- | --- | --- | --- |
| `starting` | `prepare` が ok を返す直前（`owner` は呼んだプロセス＝ラッパーか `ndf plan`） | `owner` の pid が生きていて開始時刻が同じ | 子の起動に成功したら `attach(store, name, lease, child_pid)` で `running` へ。起動に失敗したら `release(store, name, lease)` で消す（同期は打たない。子は何も書いていない） |
| `running` | `attach` の後 | `owner` か `child` のどちらかの pid が、開始時刻も同じで生きている | 子の終わりの `settle`（書き戻しの後に消す）。`owner` が先に死んでも、`child` が生きている間は消さない |

- 残骸: 上の条件を満たさない届けだけを残骸とし、登録・削除のコマンドと次の `prepare` が消して進む。ラッパーだけが終わって claude が残った届けは `child` が生きているので残骸にせず、登録・削除は止まったままになる（文は「アカウント <名前> を動いているプロセス（pid <child>）が使っている。終えてから登録し直す」）
- 届けの ID は `secrets.token_hex(8)` で、`Prepared.lease` が持つ。1 つのラッパーが同じ名前で順に起動しても、届けは起動ごとに別である
- 子が起動の直後に死に、`attach` の前にラッパーも死ねば `starting` のまま `owner` が死んでいるので残骸になる
- `ndf plan` の打ち直し（決定 9）: `account_choice.target_env` の中の `prepare` が届けを書き、`call_claude` が `claude -p` を起動した後に `attach`、`claude -p` が終わった後（成功・失敗・タイムアウトのどれも）に `settle` を打つ。起動に失敗すれば `release` を打つ。`call_claude` の後始末は `try`/`finally` で囲み、例外でも届けを残さない

`.claude.json` の同期が伝えるもの: `projects` の葉には次が含まれる。

- プロジェクトの信頼（`hasTrustDialogAccepted`）
- 許可した Tool（`allowedTools`）
- `.mcp.json` のサーバーの許可（`enabledMcpjsonServers`・`disabledMcpjsonServers`）

`mcpServers` の値には、認証のヘッダーや環境変数が入りうる。同期はこれらを葉ごとにそのまま両側へ伝える。同じ人の別のアカウントで設定と許可を続けるのが共有の目的で、10 系も同じである。そのため、あるアカウントの子で与えた信頼と許可は、共有側と、次に用意するほかのアカウントの子へ伝わる。この伝播を許すことを「人へ残す判断」に置く。同期のバックアップは、アカウントの設定ディレクトリ（0700）の中に 0600 で置く。`mcpServers` の値を含むので、画面・`log.jsonl`・結果へ出さない（出すのは葉の数と理由の符号だけ）。削除のコマンド（`account remove`）はバックアップも一緒に消す。

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
| `config_file`・`shared_config_file` | `.claude.json` を返す | `.config.json` があればそれが先・`NDF_SHARED_CONFIG_DIR` が空で `~/.claude.json`・`CLAUDE_CONFIG_DIR` だけがあれば `<それ>/.claude.json` | — |
| `check_account` | 使えるアカウントで `Checked` が返り、`Checked.local` が `check_local` の返りと同じパスと `ids`、`Checked.resolved` が同じ引数の `effective_env` と同じ。`ready` はそのとき None、`prepare` の `Prepared.resolved` は `Checked.resolved` | `account.json` に組織が無いときメールアドレスだけ比べる | 理由ごとに 1 件ずつ（認証ファイルが無い・JSON でない・アクセストークンが空・リフレッシュトークンが空・`.claude.json` が壊れている・本人が違う・旧形式の `.config.json` がある）。設定ディレクトリ・認証ファイル・設定ファイル・バックアップを、共有側を指す symlink・別のアカウントを指す symlink・共有側や別のアカウントの `.credentials.json` へのハードリンクにしたそれぞれで `not_account_local` になり、参照先のファイルの中身と更新時刻が変わらない（開いていない）。共有側の設定ファイルをアカウント側の `.claude.json` へのハードリンクにしても `not_account_local`。`ready` と `prepare` が `check_account` へ渡す共有側の設定ファイルが、親の環境から解いたもの（子の環境の `CLAUDE_CONFIG_DIR` のアカウント自身の `.claude.json` でない）で、親に `CLAUDE_CONFIG_DIR` が無いとき・入れ子のラッパーのときも同じ。`check_account` の前後で認証ファイル・バックアップのディレクトリの中身と更新時刻が変わらない（`account_unreadable`・`identity_mismatch` でもバックアップが残る） |
| `check_local`・`sync_config` | 共有側の `.claude.json` が dotfiles への symlink でも、解決先の通常ファイルへ書き、リンクは残る | `check_local` の後、本体の錠を取る前に、アカウント側の `.claude.json` を別の内容のファイルへ置き換える・共有側の名前を別のファイルへの symlink に替える・解決先をハードリンクにする（テストは錠の取得を差し替えて間に入る）と、`not_account_local` で、置き換えた後のファイルの中身と更新時刻が変わらない | 共有側の解決先が symlink の連なりの先でディレクトリ・ほかのアカウントの下なら `not_account_local` |
| `effective_env`・`auth_overrides` | 上書きが無ければ空 | 実効値で 1 件ずつ（子の環境・管理の設定のファイルと `managed-settings.d`・サーバーからの配布の控え・`--settings` の JSON とファイル・利用者・プロジェクトの設定の `apiKeyHelper` と `env` の `ANTHROPIC_API_KEY`・`CLAUDE_CODE_USE_ANTHROPIC_AWS`、設定の `env` の `ANTHROPIC_PROFILE`、`oidc_federation` の有効なプロファイル）で出所付きの項目名が返る。`--setting-sources ''` の `argv` では利用者・プロジェクトの設定の `apiKeyHelper` で当たらず、管理の設定と `--settings` では当たる。「打ち消しと選択の例」の 5 行がそれぞれの判定になる（利用者の `CLAUDE_CODE_USE_BEDROCK=1` を管理の `""` で打ち消すと当たらず、`--settings` の `1` が利用者の `0` の上に来ると当たる。控えがポリシーの項目を持てば `managed-settings.d` の `apiKeyHelper` で当たらず、控えに無い `env` の変数はファイルから当たる）。プロジェクトの設定の `env` の `CLAUDE_CODE_OAUTH_TOKEN` は当たらない（Claude Code が無視する）。`ANTHROPIC_BASE_URL` が `https://api.anthropic.com/` なら当たらず、子の環境か利用者の設定の別の URL なら当たる | 返りと例外の文に値が出ない。読めない管理の設定・値の欠けた `--settings` で `auth_unresolved`。実効の `CLAUDE_CODE_USE_VERTEX=maybe` で `auth_unresolved` |
| 起動の後の変更 | 動いている間に源のファイルが変わらなければ、見回りで `stat` だけを打ち、ファイルを開かない | 共有の `settings.json` の `env` に `ANTHROPIC_API_KEY` を足すと、次の見回りで 1 行と `auth_drift` の行が出て、替え先の `work2` で `--resume`。その世代の使用率のファイルが消える。替え先が無ければ子を残して `stalled`。`hold` されない | 上書きでない変更（`model` の変更）では何もしない |
| `planned_links`・`ready` | `projects` が無い初回のアカウントで None（共有側の `projects` になると解く） | アカウント側の `settings.json` が実体なら、その中の `apiKeyHelper` で `auth_overridden` | `projects` が実体なら `projects_not_shared`。何も書かない（ディレクトリの中身と更新時刻が変わらない） |
| `sync_config` | 共有側の値がアカウント側へ写り、`oauthAccount` は変わらない | 片側だけの変更・両側の変更・葉の削除・バックアップが無い・`userID` が違う。アカウント側で最後の `mcpServers` の葉を消す（`{}` とキーの欠落の 2 通り）と共有側からも消え、共有側が同じ葉を同時に変えていれば共有側の値が両側に残る。片側が消し、もう片側が変えた葉は共有側の状態。両側が同じ値へ変えた葉はその値 | 本体の排他が取れない・最初の置き換えで書けないときに理由を返し、両側の中身が変わらない。アカウント側・共有側・バックアップの各置き換えの直前で失敗させ、そのまま次の同期を打つと、「書く手順と途中で止まった後」の表の値にそろう。止めた後に共有側の同じ葉を変えてから次の同期を打つと共有側の値になる。旧い基準 A から両側を B へ置き換えた後、途中の記録をバックアップへ移す前に止め、アカウント側だけを C に変えて次の同期を打つと、両側が C になる。同じ時点で止め、アカウント側だけを A に戻すと両側が A に、アカウント側でその葉を消すと両側から消える。バックアップが無い最初の同期でアカウント側を置き換えた後に止め、アカウント側の葉を変えて次の同期を打つと、その値が両側に入る。最初の同期で途中の記録を書いた後、アカウント側の前に止めると、次の同期は最初の同期をやり直す。一時ファイルだけが残り途中の記録が無ければ、一時ファイルが消え前の同期のまま。復旧の再中断: アカウント側を置き換えた後・共有側の前に止め（共有側は A のまま）、次の同期の復旧を、新しい途中の記録を書く前・書いた直後・前の回の一時ファイルを消す前・新しい回の各置き換えの直前でそれぞれもう一度止めてから次の同期を打つと、どれも共有側が B になり、アカウント側の B が A に戻らない。同じ位置で止めた後に共有側の同じ葉を C に変えると C が両側に入る。両側の実パスが同じなら `same_file` で書かない。バックアップが 0600 |
| `prepare` | ok で `config_dir` が `<保存先>/<名前>` の絶対パス、使用の届けがある | 2 回続けて打っても同じ形 | 理由ごとに ok が偽、`RELOGIN_REASONS` だけ `needs_relogin` が真。共有側の `.credentials.json` の中身と更新時刻が変わらない。理由の文に値が出ない |
| `settle` | アカウント側の変更が共有側へ移り、使用の届けが消える | 変更が無ければ書かない | 書けないときに理由の符号。世代が違えば書き戻さず `reregistered`。`prepare` の後に、アカウント側の `.claude.json` を共有側を指す symlink・別のアカウントのファイルへのハードリンクに替える、または `NDF_SHARED_CONFIG_DIR` の先の設定ファイルをアカウント側へのハードリンクに替えてから `settle` を打つと、`not_account_local` で両方の中身が変わらず、使用の届けは消える。`prepare` の後に、アカウント側へ通常ファイルの `.config.json` を置く・アカウント側の `.claude.json` を別の本人の `oauthAccount` を持つ通常ファイルへ替えてから `settle` を打つと、`legacy_config`・`identity_mismatch` で共有側の中身が変わらず、使用の届けは消える |
| 登録のコマンドと使用の届け | 届けが無ければ再登録・削除ができる | `owner` も `child` も死んだ届け・`owner` が死んだ `starting` の届けは消して進む。pid が同じでも開始時刻が違う（使い回された pid）届けは死んだとみなす | 生きている届けがあれば `account add`・`account remove` が何も変えずに終了コード 1。ラッパーを `SIGKILL` で終え、子の claude が残っている間も `account add`・`account remove` が止まり、子を終えた後に通る。子の起動に失敗すると届けが消える。`ndf plan` の打ち直しの `claude -p` が動いている間も止まり、終わった後（失敗・例外でも）に届けが消える。再登録の後、旧い世代の `settle` が新しい登録へ書き戻さない |

## 子の環境（`session_env.py`・子の環境の表）

満たす決定: 決定 1・8、受け入れ条件 #3604 の 1〜3・11、#3864 の 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `child_env` | `session_env.py` | 変更 | `(environ, state_dir, account: Optional[str] = None, decided: bool = False) -> dict[str, str]` | `decided` が真なら `account` をそのまま `NDF_CLAUDE_ACCOUNT` にする（None なら外す）。偽のときだけ今どおり `accounts.chosen` を引く。ラッパーの `_start` は常に `decided=True` で、起動先の確定の結果（`Target.account`）を渡す |
| `account_env` | `session_env.py` | 新設 | `(env: Mapping[str, str], config_dir: Path, usage_file: Path) -> dict[str, str]` | `child_env` の出力に、`account-set` の変数を入れ、`account-drop` の変数を外す |
| `metered_env` | `session_env.py` | 新設 | `(env: Mapping[str, str], extra: Mapping[str, str], saved: bool) -> dict[str, str]` | `CLAUDE_CONFIG_DIR` を共有の設定ディレクトリへ戻し、`account-drop` の変数を外し、保存した設定なら提供元の秘密の変数（`account_registry.SECRET_ENV`）を外し、従量の接続の設定を重ね、最後にラッパーが確定する変数を入れ直す |
| `key_env` | `session_env.py` | 新設 | `(env: Mapping[str, str], key: str) -> dict[str, str]` | `metered_env(env, {}, saved=True)` と同じく共有の設定ディレクトリへ戻して `account-drop` と `SECRET_ENV` を外し、`NDF_CLAUDE_ACCOUNT=key:<key>` を入れる。キーの値は受けない（起動引数の `--settings` の `apiKeyHelper` で渡す。`account_choice.target_env` が引数を足す） |
| `METERED_FIXED` | `session_env.py` | 新設 | 定数（表から引く） | ラッパーが確定し、従量の接続の設定が持ってはいけない変数: `child` が `set`・`drop`・`account-set` の変数すべて（`NDF_CLAUDE_ACCOUNT`・`CLAUDE_CONFIG_DIR`・`NDF_SHARED_CONFIG_DIR`・`CLAUDE_CODE_PLUGIN_CACHE_DIR`・`NDF_ACCOUNT_USAGE_FILE` ほか）と、名前が `NDF_` で始まる変数 |

- `account_env`
  - 事前条件: `env` は `child_env` の出力（`NDF_CLAUDE_ACCOUNT` が入っている）。`config_dir` は `account_dir.prepare` が ok を返したパス。`usage_file` はラッパーが `accounts.usage_path(state_dir, name, generation)` で解いた絶対パス
  - 事後条件: `CLAUDE_CONFIG_DIR` が `config_dir` である。`NDF_SHARED_CONFIG_DIR` は、`env` にあればその値、無ければ `env` の `CLAUDE_CONFIG_DIR`（無ければ空文字）である。`CLAUDE_CODE_PLUGIN_CACHE_DIR` は、`env` にあればその値である。無ければ `<共有の設定ディレクトリ>/plugins` で、共有の設定ディレクトリは `account_registry.shared_config_dir(env)` である。`NDF_ACCOUNT_USAGE_FILE` が `usage_file` である。`account-drop` の名前が 1 つも無い
- `metered_env`
  - 事前条件: `extra` は `_metered` が検証を通したもの（下の「従量の接続の設定の検証」）
  - 事後条件: `env` に `NDF_SHARED_CONFIG_DIR` があれば、その値が空でなければ `CLAUDE_CONFIG_DIR` へ入れ、空なら `CLAUDE_CONFIG_DIR` を外す。どちらでも `NDF_SHARED_CONFIG_DIR` と `NDF_ACCOUNT_USAGE_FILE` は外す。無ければ `CLAUDE_CONFIG_DIR` は親のまま。`account-drop` の名前を外す。`saved` なら `account_registry.SECRET_ENV`（下の「提供元ごとの照合する変数」の秘密の変数。保存の時点で拒む変数と同じ 1 つの定義）を外す。次に `extra` を重ね、最後に `NDF_CLAUDE_ACCOUNT=metered` と、上で決めた `CLAUDE_CONFIG_DIR`（または無いこと）を入れ直す。`METERED_FIXED` の変数の値は `extra` に依らない
- どちらも純粋な関数で、ファイルを読まない。値の組は `facts.ids("env", child="account-set")` と `facts.ids("env", child="account-drop")` から引く。名前をコードに書かない。値は `_set_values` と同じ形の対応の辞書で作る

#### 提供元の選択の変数と有効な値（`account_registry.PROVIDER_ENV`・`provider_on`）

Claude Code が接続先を切り替える提供元の選択の変数は、次の 6 つである（STD7）。

- `CLAUDE_CODE_USE_BEDROCK`・`CLAUDE_CODE_USE_VERTEX`・`CLAUDE_CODE_USE_FOUNDRY`
- `CLAUDE_CODE_USE_ANTHROPIC_AWS`・`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`・`CLAUDE_CODE_USE_MANTLE`

子の環境の表では、この 6 行に `"provider": true` を付ける。`account_registry.PROVIDER_ENV` は `facts.ids("env", provider=True)` で引く。親の環境から外す（`account-drop`）・実効の設定の確かめ（`auth_overrides`）・従量の接続の検証（下）は、すべてこの 1 つの分類を使う。

`provider_on` は `account_registry.py` に新設し、シグネチャは `(value: Optional[str]) -> Optional[bool]` である。真偽の解釈は Claude Code（STD7）にそろえる。`1`・`true`・`yes`・`on`（大文字小文字を問わない）なら真、`None`・空文字・`0`・`false`・`no`・`off` なら偽、それ以外は None（解けない値）。既存の接続確認もこの関数に置き換え、両者の判定を 1 つにする。今の接続確認は `account_metered.py` の `CLAUDE_CODE_USE_BEDROCK in (None, "", "0")` で、`false` を有効と読む。この食い違いもなくす。

#### 従量の接続の設定の検証（`wrapper._metered`）

環境変数の宣言（`NDF_SUPERVISE_CLAUDE_FALLBACK`）と保存した設定（`metered.json`）の両方に、起動の前に同じ検証を当てる。1 つでも外れれば設定なしとして扱い、`_tell_at_start` が理由の 1 行（変数の名前だけ。値は出さない）を出す。

| 検証 | 外れる例 |
| --- | --- |
| 構文。環境変数の宣言は `shlex.split` が通り、すべての語が `KEY=VALUE` で `KEY` が `[A-Z_][A-Z0-9_]*` | 閉じない引用符・`=` の無い語（今の `fallback_env` は黙って捨てる。捨てずに壊れた設定にする） |
| 空でない | 定義されているが空・空白だけ |
| ラッパーが確定する変数を持たない | `CLAUDE_CONFIG_DIR`・`NDF_CLAUDE_ACCOUNT`・`NDF_` で始まる変数（`METERED_FIXED`） |
| 提供元を 1 つに選べる | `PROVIDER_ENV` のうち `provider_on` が真のものがちょうど 1 つで、ほかの `PROVIDER_ENV` の値はどれも偽（None の値があれば外れる）。または、どれも真でなく `ANTHROPIC_API_KEY` が空白でない値で入っている（環境変数の宣言だけ。保存した設定は今どおり資格情報の変数を持てない）。`CLAUDE_CODE_USE_BEDROCK=0` だけ・`ANTHROPIC_API_KEY=` だけ・`CLAUDE_CODE_USE_VERTEX=maybe` は外れる |

接続の設定に許す変数は、上の 4 つの検証を通るものすべてである（Bedrock の地域・モデル・プロファイルの変数など）。保存した設定の `check_decl` にも 3 つ目と 4 つ目を足し、`account metered` の保存の時点で弾く。保存した設定が拒む秘密の変数は、下の `SECRET_ENV`（広げた定義）である。

宣言の検証は `extra` の中だけを見る。ほかの源の `env` から、別の提供元・資格情報・接続先の変数が入ることがある。源は、共有側の `settings.json`・管理の設定・プロジェクトの設定・`--settings` である。入ると、会話を送る宛先と課金先が宣言から変わる。そのため、従量の子へ切り替える前に `account_dir.effective_env` を打ち、下の「従量の実効値の照合」をすべて確かめる。切り替える前とは、`_inputs` が `metered` を候補に入れるときと、`_launch_target` が `metered` を試すときである。引数は、`metered_env` の出力・共有の設定ディレクトリ・ラッパーの作業ディレクトリ・子の `argv` である。外れれば `metered_overridden`（変数の名前と出所だけ）で `metered` を候補から外し、1 行を出す。

#### 提供元ごとの照合する変数（`account_registry.METERED_CHECKED`・`SECRET_ENV`）

照合するのは、提供元の選択・認証の値・資格情報とアカウントの選択・接続先の 4 組である。秘密の変数（`SECRET_ENV`）は 3 か所が同じ定数を引く。保存した設定の `check_decl` が拒み、保存した設定の起動で `metered_env` が親から外す。下の照合では、設定ファイルからの再注入を見る。

| 組 | 変数 | 秘密か |
| --- | --- | --- |
| 提供元の選択 | `PROVIDER_ENV` の 6 つ | いいえ |
| 認証の値 | `ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN`・`ANTHROPIC_PROFILE`・`ANTHROPIC_FEDERATION_RULE_ID`・`ANTHROPIC_ORGANIZATION_ID` | 前の 3 つ |
| AWS（Bedrock・Mantle・`CLAUDE_CODE_USE_ANTHROPIC_AWS`）の資格情報とアカウントの選択 | `AWS_BEARER_TOKEN_BEDROCK`・`AWS_ACCESS_KEY_ID`・`AWS_SECRET_ACCESS_KEY`・`AWS_SESSION_TOKEN`・`ANTHROPIC_AWS_API_KEY`・`AWS_PROFILE`・`AWS_CONFIG_FILE`・`AWS_SHARED_CREDENTIALS_FILE`・`ANTHROPIC_AWS_WORKSPACE_ID`・`CLAUDE_CODE_SKIP_BEDROCK_AUTH`・`CLAUDE_CODE_SKIP_MANTLE_AUTH`・`CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH` | `AWS_BEARER_TOKEN_BEDROCK`・AWS のキーの 3 つ・`ANTHROPIC_AWS_API_KEY` |
| Google Cloud（Vertex・`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`）の資格情報とアカウントの選択 | `GOOGLE_APPLICATION_CREDENTIALS`・`ANTHROPIC_VERTEX_PROJECT_ID`・`ANTHROPIC_GOOGLE_CLOUD_PROJECT`・`ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID`・`CLAUDE_CODE_SKIP_VERTEX_AUTH`・`CLAUDE_CODE_SKIP_ANTHROPIC_GOOGLE_CLOUD_AUTH` | いいえ |
| Foundry の資格情報とアカウントの選択 | `ANTHROPIC_FOUNDRY_API_KEY`・`ANTHROPIC_FOUNDRY_AUTH_TOKEN`・`ANTHROPIC_FOUNDRY_RESOURCE`・`CLAUDE_CODE_SKIP_FOUNDRY_AUTH` | 前の 2 つ |
| 接続先 | `ANTHROPIC_BASE_URL`・`ANTHROPIC_BEDROCK_BASE_URL`・`ANTHROPIC_BEDROCK_MANTLE_BASE_URL`・`ANTHROPIC_AWS_BASE_URL`・`ANTHROPIC_VERTEX_BASE_URL`・`ANTHROPIC_GOOGLE_CLOUD_BASE_URL`・`ANTHROPIC_FOUNDRY_BASE_URL`・`AWS_REGION`・`AWS_DEFAULT_REGION`・`CLOUD_ML_REGION`・`ANTHROPIC_GOOGLE_CLOUD_LOCATION` | いいえ |

提供元ごとに分けず、どの提供元の宣言でも全部の組を照合する（使わない提供元の変数でも、設定ファイルから入れば Claude Code が別の宛先を選びうるため）。今の `SECRET_ENV`（AWS のキーの 3 つと認証の値の 3 つ）は、上の「秘密か」の列の変数すべてに広げる。名前の一覧は実装の PR で手元の Claude Code の環境変数の文書（STD7）と照らし、足りなければこの表へ足す。

#### 従量の実効値の照合

期待値は `metered_env` の出力で、実効値は `effective_env` の結果である。期待値は設定ファイルを重ねる前の子の環境で、宣言の値と、環境変数の宣言なら親から継いだ値を含む。

| 確かめ | 外れる例 |
| --- | --- |
| 実効の環境で `provider_on` が真の `PROVIDER_ENV` の変数が、宣言の提供元と同じ 1 つだけ（API キーの宣言なら 0 個）で、ほかの `PROVIDER_ENV` の変数の `provider_on` はどれも偽（None が 1 つでもあれば外れる。宣言の検証と同じ） | API キーの宣言に、共有側の `settings.json` の `env` の `CLAUDE_CODE_USE_BEDROCK=1` が加わる。API キーの宣言に設定ファイルの `CLAUDE_CODE_USE_VERTEX=maybe` が加わる |
| `METERED_CHECKED` の提供元の選択の外の変数ごとに、実効値の有無と値が期待値と同じ（期待値に無い変数は実効値にも無いか空文字。期待値にある変数は同じ値） | 管理の設定の `env` の `ANTHROPIC_AUTH_TOKEN`。保存した Bedrock の設定（`AWS_PROFILE=prod`）に、親の環境か設定ファイルの `AWS_BEARER_TOKEN_BEDROCK` が加わる（親の値は `metered_env` が外すので、残れば設定ファイルからの再注入）。設定ファイルの `env` が `AWS_PROFILE` を別の名前に替える。外した AWS のキーを設定ファイルが入れ直す。設定ファイルの `ANTHROPIC_BEDROCK_BASE_URL`・`AWS_REGION` で宣言と別の宛先へ向く |
| `helper` が None で、`unresolved` が空 | プロジェクトの設定の `apiKeyHelper`・読めない管理の設定 |

宣言に無い宛先の変更では切り替えない。プロキシやゲートウェイを使う従量の接続は、宣言（環境変数の宣言か保存した設定の `env`）に `ANTHROPIC_BASE_URL` などを入れれば、期待値と同じなので通る。同じ規則は起動の後の変更の検出でも、従量の子の見回りで `stat` の印が変わったときに当てる。

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
| `CLAUDE_CODE_USE_BEDROCK`・`CLAUDE_CODE_USE_VERTEX`・`CLAUDE_CODE_USE_FOUNDRY`・`CLAUDE_CODE_USE_ANTHROPIC_AWS`・`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`・`CLAUDE_CODE_USE_MANTLE` の行 | 設定値 | 同上 | 新設。`area: accounts`・`set_by: user`・`type: flag`・`secret: false`・`child: account-drop`・`provider: true`（提供元の選択の変数） |
| `ANTHROPIC_PROFILE`・`ANTHROPIC_FEDERATION_RULE_ID`・`ANTHROPIC_ORGANIZATION_ID` の行 | 設定値 | 同上 | 新設。`area: accounts`・`set_by: user`・`type: string`・`secret: false`・`child: account-drop`（認証の優先順位でログインより上に来るプロファイルの源） |
| `NDF_ACCOUNT_USAGE_FILE` の行 | 設定値 | 同上 | 新設。`area: accounts`・`set_by: ndf`・`type: path`・`secret: false`・`child: account-set`。status line が使用率を書くファイルの絶対パス |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `account_env`・`metered_env` | `account_login.auth_env`・`training_cache.CLAUDE_FOREIGN_AUTH` | `grep -n "AUTH_ENV\|CLAUDE_FOREIGN_AUTH" src/ndf/domains/relay/account_login.py src/ndf/adapters/training_cache.py` | 使えない。`auth_env` はログイン用の仮ディレクトリを作る環境で、`CLAUDE_FOREIGN_AUTH` は 13 変数を全部は持たない。名前は表から引く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `account_env` | 4 変数が入り、`account-drop` の 13 変数が外れる（親の `CLAUDE_CODE_USE_ANTHROPIC_AWS`・`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD` も外れる） | 親に `CLAUDE_CONFIG_DIR` が無い（`NDF_SHARED_CONFIG_DIR` が空文字）・親が登録済みアカウントの子（親の `NDF_SHARED_CONFIG_DIR` を引き継ぐ）・親に `CLAUDE_CODE_PLUGIN_CACHE_DIR` がある | 表の行を 1 つ外したテストの表で落ちる |
| `key_env` | `CLAUDE_CONFIG_DIR` が共有の設定ディレクトリ、13 変数と `SECRET_ENV` が無く、`NDF_CLAUDE_ACCOUNT` が `key:key-main` | 親が登録済みアカウントの子でも共有の設定ディレクトリへ戻る | 親の `ANTHROPIC_API_KEY` が子に残らない |
| `metered_env` | `CLAUDE_CONFIG_DIR` が共有の設定ディレクトリ、13 変数が無く、`extra` が入る | 保存した設定で `SECRET_ENV` の変数（AWS のキー・`AWS_BEARER_TOKEN_BEDROCK` ほか）が無い・環境変数の設定で AWS のキーが残る・親がアカウントの子でなければ `CLAUDE_CONFIG_DIR` は親のまま・`NDF_SHARED_CONFIG_DIR` が空なら外す・`NDF_ACCOUNT_USAGE_FILE` が無い | `extra` に `NDF_CLAUDE_ACCOUNT`・`CLAUDE_CONFIG_DIR` を入れても子の値は `metered` と共有の設定ディレクトリ |
| `_metered` の検証 | Bedrock の宣言が通る | `ANTHROPIC_API_KEY` だけの環境変数の宣言が通る。`CLAUDE_CODE_USE_BEDROCK=1` に `CLAUDE_CODE_USE_VERTEX=0` を添えた宣言が通る | 閉じない引用符・`=` の無い語・空・`CLAUDE_CONFIG_DIR` や `NDF_` で始まる変数・提供元が無い・2 つの提供元を持つ・`CLAUDE_CODE_USE_BEDROCK=0` だけ・`ANTHROPIC_API_KEY=` だけ・提供元の値が解けない宣言が、それぞれ設定なしとして扱われ `_tell_at_start` が 1 行 |
| `provider_on` | `1`・`TRUE`・`yes`・`On` が真 | `0`・`false`・`no`・`off`・空・None が偽 | それ以外が None。`account_metered` の接続確認が同じ判定を使う（`CLAUDE_CODE_USE_BEDROCK=false` を確かめの対象にしない） |
| 従量の実効の設定の確かめ | 共有側の設定に上書きが無ければ `metered` で起動する | 共有側の `settings.json` の `env` が宣言と同じ提供元・同じ値なら通る | API キーの宣言に共有側の `CLAUDE_CODE_USE_BEDROCK=1`、Bedrock の宣言に管理の設定の `apiKeyHelper` が加わると、`metered` を候補から外して 1 行を出す（子を止めない）。API キーの宣言に設定ファイルの `CLAUDE_CODE_USE_VERTEX=maybe`、保存した Bedrock の設定（`AWS_PROFILE`）に親の環境の `AWS_BEARER_TOKEN_BEDROCK`（`metered_env` が外し、子に無い）と設定ファイルの `AWS_BEARER_TOKEN_BEDROCK`・別の `AWS_PROFILE`・外した AWS のキー、設定ファイルの `ANTHROPIC_BEDROCK_BASE_URL`・`AWS_REGION`が加わると、それぞれ外れる。宣言に同じ `ANTHROPIC_BASE_URL` があれば通る。保存の時点の `check_decl` が `AWS_BEARER_TOKEN_BEDROCK`・`ANTHROPIC_FOUNDRY_API_KEY` を拒む |
| `child_env` | 既定のログインの子が今と同じ（受け入れ条件 #3604 の 3） | `decided=True` で `account=None` なら、環境変数と指定に名前があっても `NDF_CLAUDE_ACCOUNT` が無い | — |

## 登録の保存先（`account_registry.py`）

満たす決定: 決定 1・3、受け入れ条件 #3604 の 1・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `shared_config_dir` | `account_registry.py` | 新設 | `(environ: Mapping[str, str]) -> Path` | 共有の設定ディレクトリ。`NDF_SHARED_CONFIG_DIR` があればその値（空なら `~/.claude`）、無ければ `signals.claude_config_root(environ)` |
| `store_dir` | `account_registry.py` | 変更 | 変わらない | `NDF_ACCOUNTS_DIR` が無ければ `shared_config_dir(environ) / "ndf" / "accounts"`。振る舞いは今と同じ |
| `mark_relogin` | `account_registry.py` | 新設 | `(store: Path, name: str) -> None` | 呼ぶ側が持つアカウントの排他の中で、`account.json` の `needs_relogin` を真にして置き換えで書く |
| `generation` | `account_registry.py` | 新設 | `(store: Path, name: str) -> str` | `account.json` の `generation`（無ければ空文字） |
| 登録（`account add`） | `cli_account.py`・`account_registry.py` | 変更 | 変わらない | 登録の排他の中で、使用の届けを確かめ（生きていれば止める）、旧形式の `.config.json` を `backups/` へ移し、新しい `generation` を書く。成功の後、`accounts.forget(state_dir, name)` で外した間と上限の観測からその名前を消す |
| 削除（`account remove`） | 同上 | 変更 | 変わらない | 使用の届けを確かめ（生きていれば止める）、今の削除に加えて `accounts.forget` を打つ |
| `recovery_keys` | `account_registry.py` | 新設 | `(store: Path) -> list[str]` | 登録簿の使い方の設定が `off` でなければ、登録したキーの名前を名前の順に返す。`off`・登録簿が無い・壊れているときは空（決定 12） |
| `key_value` | `account_registry.py` | 新設 | `(store: Path, name: str) -> str` | 登録簿からキーの値を返す。無ければ `AccountError`。呼ぶのは `api-key-helper` だけ |

- 世代の照合: 使用率のキャッシュのパス、外した間と上限の観測の項目は世代を持つ。読む側は今の `account.json` の世代と違うものを無いものとして読む。再登録の後に旧いセッションの status line やラッパーが遅れて書いても、旧い世代のパスか項目へ入るので、新しい登録の判断へ戻らない

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
| 登録・削除 | 再登録で `generation` が変わり、`held.json` と `limited.json` から名前が消え、旧い世代の使用率のキャッシュが読まれない | 旧形式の `.config.json` が `backups/` へ移り、新しい `.claude.json` の本人で `check_account` が通る | 生きている使用の届けがあると止まり、何も変わらない。再登録の後に旧い世代で `hold` と使用率の書き込みをしても、新しい登録が外されず、上限とも読まれない |
| `recovery_keys`・`key_value` | 使い方が `near-limit` で 2 つのキーの名前が名前の順 | 使い方が `off` で空・登録簿が無くて空 | 登録簿が壊れていて空。`key_value` は無い名前で `AccountError`、値は誤りの文に入らない |

## 使用率と外した間の記録（`accounts.py`・ファイルの表・上限の値）

満たす決定: 決定 4・6、受け入れ条件 #3864 の 1〜3・6・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `usage_dir` | `accounts.py` | 新設 | `(state_dir: Path) -> Path` | `<状態の保存先>/relay/accounts/usage`（ファイルの表の `relay-usage` の `path` の親から） |
| `usage_path` | `accounts.py` | 変更 | `(state_dir: Path, name: str, generation: str) -> Path` | アカウント 1 つ・登録の世代 1 つの使用率のキャッシュのパス（`usage/<名前>/<世代>.json`。世代が空文字なら `usage/<名前>/_.json`）。status line へは `NDF_ACCOUNT_USAGE_FILE` でこの結果を渡す |
| `usages` | `accounts.py` | 変更 | `(state_dir: Path, now: float, generations: Mapping[str, str]) -> list[account_decide.Usage]` | 登録済みアカウントごとに今の世代のファイルだけを名前の順に読む。窓ごとにリセットの時刻を過ぎていれば 0% にし、5 時間と 7 日の使用率と 7 日のリセットの時刻を返す。使用率（一覧の表示）は窓の最大、上限は過ぎていない窓のどれかが 100 以上。最後に `limits_seen` を重ね、上限の観測が続く名前を上限にする。使用率の行が無い（ファイルが無い・有効な窓が無い）名前に上限の観測が続いていれば、`Usage(name, None, None, None, None, True)`（使用率は分からないが上限）の行を作る（判断の入力の唯一の経路） |
| `write_usage` | `accounts.py` | 新設 | `(path: Path, windows: Mapping[str, tuple[float, float]], now: float) -> None` | status line と同じ形・同じ合わせ方（下の「書き込みの規則」）で 1 つの記録を書く（テストが使う。今の `write_usages` は消す） |
| `held_path` | `accounts.py` | 新設 | `(state_dir: Path) -> Path` | `<状態の保存先>/relay/accounts/held.json` |
| `held` | `accounts.py` | 新設 | `(state_dir: Path, now: float, generations: Mapping[str, str]) -> dict[str, float]` | 外す終わりが `now` より後で、世代が今の登録と同じ名前と終わりの時刻 |
| `hold` | `accounts.py` | 新設 | `(state_dir: Path, name: str, generation: str, until: float, now: float) -> None` | 錠の中で読み、終わった名前を消し、`name` を `{"until", "generation"}` で書いて置き換える |
| `unhold` | `accounts.py` | 新設 | `(state_dir: Path, name: str) -> None` | 錠の中で `held.json` から `name` を消す。`account use <名前>` が指定を書いた後に打つ |
| `limits_path`・`limits_seen`・`see_limit` | `accounts.py` | 新設 | `see_limit(state_dir, name, generation, seen_at: float, until: float, window: Optional[str])`・`limits_seen(state_dir, now, generations, windows: Mapping[str, Mapping[str, tuple[float, float]]]) -> dict[str, tuple[float, float]]` | 上限の観測（`<状態の保存先>/relay/accounts/limited.json`、`{"<名前>": {"seen_at", "until", "window", "generation"}}`。`window` は `until` を取った窓の名前、取れなければ null）。ラッパーが上限の行を初めて見たときに書く。`limits_seen` は下の「解除」に当たらない観測を返す（`windows` は名前ごとの今のキャッシュの窓） |
| `limit_until` | `accounts.py` | 新設 | `(line: str, windows: Mapping[str, tuple[float, float]], seen_at: float, tz: tzinfo) -> tuple[float, Optional[str]]` | 上限の行と使用率のキャッシュから `until` と窓の名前を決める（下の「`until` の決め方」）。時計を読まない |
| `forget` | `accounts.py` | 新設 | `(state_dir: Path, name: str) -> None` | `held.json` と `limited.json` から `name` を消し、`usage/<名前>/` の今の世代でないファイルを消す（登録・削除の成功の後に打つ） |

- `usages` の事後条件: 返す名前は登録済みアカウント（`generations` のキー）だけである。読めないファイル・形の違うファイル・有効な窓が 1 つも無いファイルは返さない。有効な窓とは、`five_hour`・`seven_day` のうちリセットの時刻を過ぎていないものである。返さない名前は使用率の分からないアカウントになり、0% として読まない。ただし、上限の観測が続く名前は、ファイルが無くても `Usage(name, None, None, None, None, True)` で返す。status line が無い経路で、上限の行を見たアカウントを「使用率の分からない候補」として選び直さないためである
- `account_decide.Usage` の型の変更（決定 5）: `Usage(name, percent, five, seven, seven_resets_at, limited)` にする。`percent`・`five`・`seven` は `Optional[float]`（窓が無い・使用率が分からないとき None）、`seven_resets_at` は `Optional[float]`。`percent` が None の行は `limited` が真の行だけ。`order`・`before_limit`・`guard`・`after_limit` は `limited` が真の行を上限として扱い、「使用率の分からない候補」に入れない。一覧（`_acc_list`）は `percent` が None なら `-` と出す
- `hold`・`unhold`・`see_limit` の副作用: 書くファイルの `.lock` の錠（`signals.held`、待ちは `relay.signal-lock`）と置き換えの書き込み

#### 上限の観測

上限の行を見た時点から解除までの根拠を、`limited.json` の 1 つにする。指定（`_pinned_or`）・替え先の候補（`order`）・同じアカウントへの復帰（`after_limit`）・リセット待ちは、すべて `usages` を通してこれを読む。`_limit_pick` が持っていた「キャッシュのファイルの更新時刻が上限を見た時刻より古ければ上限」の補正は、これに置き換えて消す。

| 場面 | 規則 |
| --- | --- |
| 書く時点 | 登録済みアカウントの子の画面に上限の行を初めて見た見回り（`Section.limit_at` を立てる時）に 1 回 |
| `until` の決め方 | 上から順に、最初に決まったもの。(1) 上限の行にリセットの時刻（`resets 3pm (Asia/Tokyo)` などの `resets` の後の時刻）があれば、`seen_at` より後で最も近いその時刻。窓の名前は行の語（`session` は `five_hour`、`weekly`・`week` は `seven_day`）から、無ければ `resets_at` がその時刻と同じ時（時と分）の窓、それも無ければ null。(2) 行が窓の語だけを持ち、その窓がキャッシュで見た時刻にリセットを過ぎていなければ、その窓の `resets_at`。(3) キャッシュのリセットを過ぎていない窓のうち使用率が 100 以上のものがあれば、その中で `resets_at` の最も遅い窓（どれも塞いでいるので、遅いほうまで使えない）。(4) それ以外（窓が無い・どの窓も 100 未満の古い記録）は、`five_hour` がリセットを過ぎていなければその `resets_at`、無ければ `seen_at + relay.limit-unknown` の秒で、窓の名前は `five_hour`（(4) の推定が外れて実は 7 日の窓の上限なら、`until` の後の `--resume` が上限の行をもう一度見て、新しい観測で決め直す。日の起動の上限が回数を縛る） |
| 解除 | `now ≥ until`、または観測の `window` の窓が入れ替わったとき（キャッシュのその窓の `resets_at` が `until` より後で、使用率が 100 未満）。同じ窓の値（92% から 93% への増加など）や、書き込みの時刻では解かない。`window` が null なら `now ≥ until` だけで解く |
| 古い記録・遅れて届いた入力 | 見た時刻より前の窓の値は、上限でないとは読まない（観測が続く間は上限）。上限より前の入力が遅れて書かれても、その窓の `resets_at` は `until` 以下なので解除に当たらない。`resets_at` が `until` より後の窓は、リセットの後に Claude Code が返した値だけが持つ |
| 従量の接続・既定のログイン | 書かない（名前で除けるのは登録済みアカウントだけ） |

`relay.limit-unknown` は 5 時間の窓の長さ（18000 秒）で、余白を足さない。リセットの時刻の行の形は、実装の PR で手元の上限の画面から確かめ、`limit_until` のテストの入力に使う（形が違えば (1) を飛ばして (2) 以降で決まる）。

#### 書き込みの規則（status line と `write_usage`）

同じアカウントの 2 つの status line が別々の時点の入力を受けて前後して終わっても、新しい値を古い値で戻さない。書き手は `<ファイル>.lock` のディレクトリを `mkdir` で取り、前の中身と窓ごとに合わせてから置き換える。錠が取れなければ、その回は書かない。10 秒より古い錠は残骸として消して取り直す。

窓ごとに上の行から順に当て、最初に当たった行で決める。

| 窓ごとの比べ | 書く値 |
| --- | --- |
| 新しい入力の `resets_at` が書く時刻で既に過ぎている | 前の値（前が無ければその窓を書かない）。失効した入力でキャッシュを巻き戻さない |
| 前の記録に無い | 新しい値 |
| 新しい `resets_at` が前より前 | 前の値（古い入力。前の窓が失効していても、それより古い窓へ戻さない） |
| 新しい `resets_at` が前より後 | 新しい値（新しい窓） |
| `resets_at` が同じ | 使用率の大きいほう（同じ窓の使用率は減らない） |

合わせた結果が前の `windows` と同じなら書かない。ファイルの `recorded_at` は書き込みの時刻だけを表し、判断には使わない。窓の鮮度と同一性は窓ごとの `resets_at` が表し、上限の観測の解除はこれだけで決める。そのため、上限が解けた後に同じ値が続いて書かれなくても、新しい窓の `resets_at` を持つ最初の入力が書かれれば解除が分かる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `usages` | 読めない・形が違う | 書き込みの途中・手で壊した | ファイルごとの読み | 既定値で続ける（そのファイルを飛ばす） | 無し | 次の status line の更新で直る |
| `held` | 読めない・形が違う | 手で壊した | 読み | 既定値で続ける（外した間が無いとして読む） | 無し | 次の `hold` が置き換える |
| `hold` | 錠を取れない・書けない | ほかのレーンが同時に書いている・ディスク | 錠と置き換え | 既定値で続ける（記録せず替え先の判断は続ける。今の判断では今のアカウントを候補から除く） | `log.jsonl` に 1 行 | 次の認証の失敗で再び書く |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `relay-usage` の行 | 設定値 | `contracts/domains/ndf-files.json` | `path: "usage/<名前>/<世代>.json"`・`writer: "NDF の status line（statusline.sh）"`・`keys: ["windows", "recorded_at"]` |
| `relay-auth-held` の行 | 設定値 | 同上 | 新設。`location: relay-accounts`・`path: held.json`・`kind: measured`・`tracked: false`・`writer: "中継のラッパー（M3b）"`・`ends: replaced` |
| `relay-limit-seen` の行 | 設定値 | 同上 | 新設。`location: relay-accounts`・`path: limited.json`・`kind: measured`・`tracked: false`・`writer: "中継のラッパー（M3b）"`・`ends: replaced` |
| `relay.five-hour-guard` | 設定値 | `contracts/limits.json` | `{"summary": "閾値 A。5 時間の窓の使用率がこれ以上のアカウントを使い切りの組に入れ、見回りで替える（百分率。#3864）", "count": 85}` |
| `relay.weekly-prefer` | 設定値 | 同上 | `{"summary": "閾値 B。7 日の窓の使用率がこれより下のアカウントを余裕の組に入れる。カットポイントと替え先の選び方だけに効く（百分率）", "count": 70}` |
| `relay.weekly-guard` | 設定値 | 同上 | `{"summary": "閾値 C。7 日の窓の使用率がこれ以上のアカウントを使い切りの組に入れ、見回りで替える（百分率）", "count": 90}` |
| `relay.max-switches` | 設定値 | 同上 | `{"summary": "1 つのラッパーが 1 日にアカウントを替えて起動し直す回数の上限（30 分に 1 回）。替えた起動は relay.max-starts に数えない", "attempts": 48}` |
| `relay.usage-guard` | 設定値 | 同上 | 消す（閾値 A・C が置き換える） |
| `relay.auth-hold` | 設定値 | `contracts/limits.json` | `{"summary": "認証の失敗で、そのアカウントを替え先と指定の候補から外す秒（#3864）", "seconds": 3600}` |
| `relay.limit-unknown` | 設定値 | 同上 | `{"summary": "上限の行を見たアカウントの使用率の記録に有効な窓が無いとき、上限として扱う秒（5 時間の窓の長さ）", "seconds": 18000}` |
| 窓の名前 | 定数 | `account_registry.WINDOWS` | `("five_hour", "seven_day")`（既にある） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 別のアカウントの 2 つの status line | 使用率のキャッシュ | 取らない。ファイルが別 | 置き換えで書く |
| 同じアカウントの 2 つのレーンの status line | 同じ 1 ファイル | `<ファイル>.lock` のディレクトリ（取れなければその回は書かない） | 錠の中で読み直し、「書き込みの規則」で窓ごとに合わせるので、遅れて終わった古い入力が新しい値を下げない |
| 2 つのラッパーの `hold`・`see_limit`、登録のコマンドの `forget`、`account use` の `unhold` | `held.json`・`limited.json` | `signals.held` の錠 | 読み直してから書くので、ほかの名前を消さない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `usage_dir`・`held_path` | `accounts_dir`・`facts.value("locations", …)` | ファイルを読んで確かめた | 使う。パスはファイルの表から引く |
| `write_usage`・`hold` | `signals.replace_json`・`signals.held` | 同上 | 使う |
| `held` | `accounts.pinned` の読み方 | 同上 | 同じ形で読み、壊れていれば空として扱う（`pinned` と違い止めない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `usages` | 5 時間と 7 日の使用率が窓ごとに返り、使用率は大きいほう | リセットの時刻を過ぎた窓が 0%・5 時間の窓が無いと `five` が None・100 ちょうどで上限・7 日のリセットの時刻が返る。有効な窓が 0 個（`rate_limits` が `spend_limit` だけ・空の object）のファイルは返さない | 壊れたファイルを飛ばす・`metered.json` を読まない・旧い世代のファイルを読まない |
| `write_usage`・`usages` | 書いた値を読み戻せ、ほかのアカウントのファイルが変わらない | 同じ窓で使用率の小さい古い入力を後から書いても下がらない。`resets_at` の古い入力を後から書いても前の窓のまま。新しい窓は使用率が小さくても採る。前の窓が失効した後に、それよりさらに古い `resets_at`・既に失効した `resets_at` の入力を書いても前の値のまま | 錠が取られていればその回は書かず、前の中身が残る |
| `held`・`hold`・`unhold` | 書いた名前が終わりまで返る | 終わりちょうどの時刻で返らない・終わった名前が次の書き込みで消える・旧い世代の項目が返らない・`unhold` で消える | 壊れたファイルで空を返す |
| `see_limit`・`limits_seen`・`usages` | 上限の行の後、古い 92% の記録を持つ名前が上限として返る | `until` を過ぎると上限でない。観測の窓の `resets_at` が `until` より後の 100 未満の値に替わると、`until` の前でも上限でない。上限の行を見た後に同じ窓が 92% から 93% に増えても、上限より前の入力が遅れて書かれても上限のまま。使用率のファイルが無い名前に観測だけがあると `Usage(name, None, None, None, None, True)` が返り、`until` を過ぎると行が返らない | 壊れたファイルで観測が無いとして読む |
| `limit_until` | 行の `resets 3pm` が `until` になる | 行の時刻が無く `weekly` の語だけなら `seven_day` の `resets_at`。古い記録で 7 日が 90%・5 時間が 80% でも 7 日の窓を選ばず、`five_hour` の `resets_at`。100 以上の窓が 2 つなら遅いほう。窓が無ければ `seen_at + 18000` | 行の時刻が読めなければ次の規則へ進む |

## 替えるかの判断（`account_decide.py`）

満たす決定: 決定 5・6・8・11、受け入れ条件 #3864 の 4〜7・9・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Guards` | `account_decide.py` | 新設 | 型 `(five: float, prefer: float, seven: float)` | 閾値 A・B・C（呼ぶ側が `limits.json` から作って渡す） |
| `group` | `account_decide.py` | 新設 | `(u: Optional[Usage], g: Guards) -> Optional[int]` | 選ぶ順の組の番号。0 余裕の組（`five` が None か A より下、かつ `seven` が None か B より下）・1 使用率の分からないもの（`u` が None）・2 B 越えの組（`five` が A より下か None、`seven` が B 以上 C より下）・3 使い切りの組（`five` が A 以上か `seven` が C 以上）。上限（`limited`）は None |
| `order` | `account_decide.py` | 新設 | `(exclude: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> list[str]` | `candidates` から `exclude` と上限のものを除き、組の番号 → `seven_resets_at` の早い順（None は後）→ `seven` の大きい順 → 名前の順に並べる |
| `before_limit` | `account_decide.py` | 変更 | `(current: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> Pick` | カットポイントの判断。今が従量の接続（`metered`・`key:`）なら `order` の先頭へ戻す。今の組より番号の小さい組の候補があれば `order` の先頭へ替え、無ければ `keep`（切り替えの余裕） |
| `guard` | `account_decide.py` | 新設 | `(current: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> Pick` | 見回りの判断。今が従量の接続で `order` が空でなければ先頭へ戻す。今が登録済みアカウントで組 3 にあり、`order` の先頭が組 0〜2 なら先頭へ替える。それ以外は `keep`。今が既定のログイン・使用率の分からないアカウントなら `keep` |
| `after_limit` | `account_decide.py` | 変更 | `(current: Optional[str], rows: list[Usage], candidates: list[str], fallbacks: list[str], g: Guards) -> Pick` | 今のアカウントが候補にあり上限が解けていて、`order` の先頭より組の番号が大きくなければ同じアカウント。そうでなければ `order` の先頭、無ければ `fallbacks`（今の種類を除く）の先頭、それも無ければ `wait_reset` |
| `after_auth` | `account_decide.py` | 新設 | `(current: Optional[str], rows: list[Usage], candidates: list[str], fallbacks: list[str], g: Guards) -> Pick` | `order` の先頭、無ければ `fallbacks`（今の種類を除く）の先頭、それも無ければ `stalled` |

- `candidates` は、呼ぶ側（`account_choice.candidates`）が渡す起動できる登録済みアカウントの名前である。再登録が要るもの・外した間のもの・`account_dir.ready` に落ちたものを除く。`fallbacks` は `account_choice.fallbacks` が渡す従量の接続の候補（`metered`、続けて `key:<名前>` の名前の順）。`rows` は上限の観測を重ねた使用率。判断の関数は候補の外の名前を返さない
- `Pick.kind` に `stalled`（認証の失敗で替え先が無い）を足す。`reason` は値を含まない文で、組の番号と閾値を名で書く（`work1 の 5 時間 86% が閾値 A 85% 以上。work2（余裕の組）へ替える`）

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `before_limit` | 今が従量の接続で `order` が空でない | `switch`（先頭） |
| `before_limit` | 今が従量の接続で `order` が空 | `keep` |
| `before_limit` | 今のアカウントの使用率が分からない・既定のログイン | `keep`（守り 3 と見回りに任せる） |
| `before_limit` | `order` の先頭の組の番号が今の組より小さい（今が上限なら組 4 として比べる） | `switch`（先頭） |
| `before_limit` | それ以外（同じ組・候補が無い） | `keep`（切り替えの余裕） |
| `guard` | 今が従量の接続で `order` が空でない | `switch`（先頭。戻る） |
| `guard` | 今が組 3 で `order` の先頭が組 0〜2 | `switch`（先頭） |
| `guard` | それ以外 | `keep` |
| `after_limit` | 今のアカウントが `candidates` にあり、上限でなく（上限の観測を重ねた後）、`order` の先頭の組の番号以下 | `switch`（同じアカウント） |
| `after_limit` | `order` が空でない | `switch`（先頭。組 3 も入る） |
| `after_limit` | `order` が空・`fallbacks` に今の種類でないものがある | `switch`（その先頭） |
| `after_limit` | それ以外 | `wait_reset`（呼ぶ側が見回りのたびに入力を読み直し、解けたアカウントが現れれば上の行に当たる） |
| `after_auth` | `order` が空でない（今が従量の接続でも同じ） | `switch`（先頭） |
| `after_auth` | `order` が空・`fallbacks` に今の種類でないものがある | `switch`（その先頭） |
| `after_auth` | それ以外 | `stalled` |

- 従量の接続の認証の失敗: 登録済みの候補があればそちらへ替える。従量の接続と API キーは外した間（`held.json`）に記録しない（名前で外すのは登録済みアカウントだけ）。代わりに、ラッパーが「試して失敗した接続先」（`Wrapper.tried: set[str]`）を持ち、`fallbacks` から除いて渡す
- 試して失敗した接続先の規則:
  - 入れる時点: 認証の失敗を確かめたとき、今の起動先が `metered`・`key:<名前>` ならその名前を入れる（登録済みアカウントは `held.json` が同じ働きをする）
  - 空にする時点: 替えて起動した子が、起動の後に API の失敗でない `assistant` の記録を 1 つ書いたとき（その接続先で応答が返った）。ラッパーの終わりでも消える（状態の保存先に残さない）
  - 尽きたとき: `order` が空で、`fallbacks` から `tried` を除くと空なら `stalled` とし、子を残す。直し方の 1 行に、この続きで失敗した接続先を名前の順にすべて並べ、名前ごとの直し方（従量の接続なら `ndf relay account check metered`、API キーなら `ndf relay account add-api-key <名前>` での登録し直し、登録済みアカウントなら `AUTH_FIX`）を添える。同じセグメントで 1 回
  - たとえば、登録済みアカウントがすべて外した間で、`key:k1`・`key:k2` が両方失効しているとき、`key:k1` の失敗で `key:k2` へ、`key:k2` の失敗で `tried` が `{key:k1, key:k2}` になり、`key:k1` へ戻らずに `stalled` になる

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `order`・`group` | `_best` | ファイルを読んで確かめた | `_best` は使用率の行がある名前だけを、窓の最大の 1 つの値で並べるので、組とリセットの時刻を扱えない。`order` に置き換えて `_best` を消す |
| `guard`・`after_auth` | `after_limit` | 同上 | 同じ `order` を使い、今のアカウントの上限が解けたかの分岐を持たない別の関数にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `group` | 5 時間 40%・7 日 30% が組 0、7 日 75% が組 2、5 時間 86% が組 3、7 日 91% が組 3 | 5 時間ちょうど 85 が組 3・7 日ちょうど 70 が組 2・ちょうど 90 が組 3・5 時間の窓が無いプランで 7 日 30% が組 0・`u` が None で組 1 | 上限の行が None |
| `order` | 組 0 → 組 1 → 組 2 → 組 3 | 同じ組で 7 日のリセットの早いほうが先・同じリセットなら 7 日の使用率の大きいほう・`exclude` と上限のものが入らない・`Usage(name, None, None, None, None, True)` の名前が入らない | 候補の外の行を返さない |
| `before_limit` | 今が組 3（5 時間 92%）で組 0 の `work2` へ替える | 今が組 2 で組 0 があれば替える・今が組 0 で同じ組 0 のリセットの早いものがあっても替えない・今が従量の接続で戻る・候補が無ければ `keep` | 候補に無いアカウントへ替えない |
| `guard` | 今が組 3 で組 0 の候補へ替える | 今が組 2（7 日 75%）では替えない・候補が組 3 だけなら替えない（使い切り）・組 1（分からない）へは替える・今が `key:k1` で組 3 の `work1` があれば戻る | 今が既定のログインで替えない |
| `after_limit` | 使用率の分からない `work2` へ替える | 閾値の内側のものが組 3 より先・候補が組 3 だけでも替える・上限が解けた今のアカウント（組 0）に戻る・今のアカウントの行が `Usage(name, None, None, None, None, True)` なら戻らない | 候補が無ければ `metered`、無ければ `key:k1`、無ければ `wait_reset`。今が `metered` なら `key:k1`、それも無ければ `wait_reset`。上限が解けていても今のアカウントが `candidates` に無い（外した間・再登録が要る）なら同じアカウントへ戻らない |
| `after_auth` | `work2` へ替える | 既定のログイン（今が None）でも替える。今が `metered` で登録済みの候補 `work1` があれば `work1` へ替える | 候補が無く `fallbacks` が空で `stalled`、今が `metered` で候補もキーも無ければ `stalled` |
| ラッパーの `tried`（決定 6） | `key:k1` が認証に失敗すると `key:k2` で `--resume` | `key:k2` で応答が 1 つ返ると `tried` が空になる | 登録済みアカウントが使えず `key:k1`・`key:k2` がどちらも失効しているとき、`key:k1` → `key:k2` の後に `stalled` で子を残し、直し方の 1 行に 2 つの名前が並ぶ。`key:k1` を 2 回起動しない |

## 次の手の判断（`switch_decide.py`）

満たす決定: 決定 6・8・11、受け入れ条件 #3864 の 6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `SectionState.auth_pick` | `switch_decide.py` | 新設 | フィールド `Optional[Pick]`（既定は None） | 画面に認証の失敗の行が出たときの替え先の判断。今の `auth_failed` は消す |
| `SectionState.guard_pick`・`safe` | `switch_decide.py` | 新設 | フィールド `Optional[Pick]`・`bool`（既定は None・偽） | 見回りの判断（`account_decide.guard` の `switch`）と、止めてよい時点か（呼ぶ側が `observe.tool_pending`・静かな時間・質問から決める） |
| `SectionState.switches_today`・`max_switches` | `switch_decide.py` | 新設 | フィールド `int`（既定は 0・48） | 今日アカウントを替えて起動し直した回数と上限（`relay.max-switches`） |
| `GUARD_RESUME_PROMPT` | `switch_decide.py` | 新設 | 定数 `str` | `利用上限の手前でアカウントを替えた。中断したところから続ける`（従量の接続から戻るときは `利用上限が解けたアカウントへ戻した。中断したところから続ける`） |
| `AUTH_RESUME_PROMPT` | `switch_decide.py` | 新設 | 定数 `str` | `認証の失敗でアカウントを替えた。中断したところから続ける` |
| `_limit_or_auth` | `switch_decide.py` | 変更 | 変わらない | 上限の判断を先に見る（今と同じ）。`switch` なら、`switches_today` が `max_switches` 以上で `wait_reset`、そうでなければ `Step("account", reason, RESUME_PROMPT, account)`（`starts_today` を見ない）。次に `auth_pick` が `switch` なら同じ回数の確かめの後に `Step("account", reason, AUTH_RESUME_PROMPT, account)`、回数に当たれば `stalled`。`stalled` で子が生きていれば `Step("stalled", reason)` |
| `_guard` | `switch_decide.py` | 新設 | `(state: SectionState) -> Optional[Step]` | `decide` の中で、次のセグメントのマーカーの判断の後・子の生き死にの判断の前に呼ぶ。`guard_pick` が `switch` で `switches_today` が上限より下のとき、`safe` なら `Step("account", reason, GUARD_RESUME_PROMPT, account)`、そうでなければ `Step("wait", "閾値を越えた。止めてよい時点を待つ")`。それ以外は None |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `SectionState.auth_pick`・`guard_pick` | `SectionState.limit_pick` | ファイルを読んで確かめた | 同じ形のフィールドにする |
| `GUARD_RESUME_PROMPT`・`AUTH_RESUME_PROMPT` | `RESUME_PROMPT` | 同上 | 使えない。文が利用上限に当たって替えたと書く |
| `_guard` の手 | `account`（`_on_resume`） | 同上 | 同じ手を使う（子を止めて同じ会話を `--resume`） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_limit_or_auth` | `auth_pick` が `switch` で `account` の手と `AUTH_RESUME_PROMPT` | `starts_today` が `max_starts` でも替える（替えた起動を起動の上限に数えない）。`switches_today` が `max_switches` で上限は `wait_reset`・認証の失敗は `stalled`（`stop` にしない）。上限の行と認証の失敗が同時なら上限が先 | `stalled` で子が終わっていれば今の終わりの判断へ |
| `_guard` | `guard_pick` が `switch` で `safe` なら `account` の手と `GUARD_RESUME_PROMPT` | `safe` が偽なら `wait`・`switches_today` が上限なら None・次のセグメントのマーカーがあればカットポイントの判断が先・質問があれば転送が先 | `guard_pick` が `keep` なら None |

## NDF の status line（`statusline.sh`）

満たす決定: 決定 4、受け入れ条件 #3864 の 1・2・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_usage` | `statusline.sh` | 新設 | シェル関数（引数無し。`$input` と環境を読む） | `NDF_ACCOUNT_USAGE_FILE` が空でない絶対パスで、入力の `rate_limits` に有効な `five_hour`・`seven_day` の窓（`used_percentage` と `resets_at` が数）が 1 つ以上あれば、そのパスへ書く |

- 事後条件: ファイルの中身は設計文書の「使用率のキャッシュ」の形。合わせ方は詳細設計の「書き込みの規則」と同じ。合わせた `windows` が前と同じなら書かない（`recorded_at` だけの更新はしない）。有効な窓が 0 個なら書かず、前の記録も変えない。0 個とは、`rate_limits` が無い・`spend_limit` だけ・どちらの窓も欠けるときである。`spend_limit` は Claude apps gateway の支出の上限で、利用上限の窓として読まない
- 副作用: 置き場のディレクトリの作成・`<ファイル>.lock` の錠・`<ファイル>.$$` への書き込み・`mv -f`。すべて標準エラーを捨て、失敗しても表示の処理へ進む
- 書き先はラッパーが契約（ファイルの表の `relay-usage`）から解いて子の環境の `NDF_ACCOUNT_USAGE_FILE` で渡す。シェルは保存先の規則・名前の検証・世代を持たない。状態の保存先を `NDF_STATE_HOME`・`XDG_STATE_HOME` で替えても、書き手と読み手が同じパスを使う。従量の接続・既定のログインの子と、ラッパーの外の claude はこの変数を持たないので書かない

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
| `record_usage` | 2 つの窓が書かれる | 5 時間の窓が無い・同じ値で書かない・2 つのアカウントを同時に書いて両方残る。同じファイルへ、新しい入力（使用率 100）の後に古い入力（同じ `resets_at` で 40）の処理を遅れて終わらせても 100 が残る | `NDF_ACCOUNT_USAGE_FILE` が無い・`rate_limits` が無い・`spend_limit` だけ・`{}` のとき書かない。置き場に書けない・jq が無い（`PATH` から外す）とき出力が今と同じで終了コード 0 |
| 書き手と読み手のパス | ラッパーを `NDF_STATE_HOME` で既定と別の状態の保存先にして起動すると、子の `NDF_ACCOUNT_USAGE_FILE` がその保存先の `usage/<名前>/<世代>.json` で、status line が書いた値を `accounts.usages` が読む | — | — |

## ラッパー（`wrapper.py`）

満たす決定: 決定 1・3・5〜8・11・12、受け入れ条件 #3604 の 1・7・8・9、#3864 の 4〜10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Target` | `wrapper.py` | 新設 | 型 `(account: Optional[str], config_dir: Optional[Path], generation: str, reason: str, skipped: tuple[str, ...], lease: Optional[str])` | 確定した起動先（None は既定のログイン）・用意の済んだ設定ディレクトリ・用意したときの登録の世代・使用の届けの ID・起動の理由（`first`・`cut`・`limit`・`auth`）・飛ばした名前 |
| `Choice` | `wrapper.py` | 新設 | 型 `(names: tuple[str, ...], fallback: Optional[str])` | 起動の要求。`names` は順に試す名前（登録済みアカウントか `metered`）、`fallback` は尽きたときの起動先（None は既定のログイン）。「未選択」は `names` が空で `fallback` が None、「既定のログインに確定」も同じ形、「登録先の指定」は `names` に名前がある形で、未選択のときに登録済みの候補を列挙しない |
| `Section.auth_at`・`auth_told`・`inputs`・`resolved`・`drift_at` | `wrapper.py` | 新設 | フィールド `Optional[float]`・`bool`・`Optional[Inputs]`・`Optional[Resolved]`・`Optional[float]` | 認証の失敗を確かめた時刻・直し方の 1 行を出したか・判断の入力の控え・起動のときの実効値と印・起動の後の変更を検出した時刻 |
| `Inputs` | `wrapper.py` | 新設 | 型 `(stamp: tuple, valid_until: float, rows, candidates, metered)` | 判断の入力（使用率・起動できる候補・従量の接続の設定）と、読んだときのファイルの印と、次に入力が変わる時刻（窓のリセット・外した間と上限の観測の終わりの最小）。印は、登録済みアカウントごとに実際に読む世代のファイル `usage/<名前>/<世代>.json` と `account.json`、それに `held.json`・`limited.json`・`metered.json`・`api-keys.json` の、`(st_mtime_ns, st_ino, st_size)` の組（無ければ None）。`usage/` ディレクトリ自体の更新時刻は入れ子のファイルの置き換えで変わらないので使わない |
| `_inputs` | `wrapper.py` | 新設 | `(self, sec: Section, now: float) -> Inputs` | 控えの印が今のファイルの印と同じで `now < valid_until` なら控えを返し、使用率・登録の中身を開かない。違えば読み直す。候補の確かめ（`account_dir.ready`）は読み直したときだけ名前ごとに 1 回。`ready` が依存する設定・認証・リンクは印に入れず、替え先を決めて子を止める直前に、選んだ 1 つの候補へ `ready` をもう一度打つ（下の「子を止める前の替え先の確定」） |
| `_candidates` | `wrapper.py` | 新設 | `(self, now: float) -> list[str]` | 登録済みアカウントのうち、再登録が要らず、外した間でなく、`account_dir.ready` が None（起動できる）ものの名前。上限のものは `order` が除く |
| `_fallbacks` | `wrapper.py` | 新設 | `(self, now: float) -> list[str]` | `account_choice.fallbacks(store, environ, self._metered(), cwd, 子の argv, self.tried)` を呼ぶ。`_metered` が None でなければ `metered`、続けて `account_registry.recovery_keys` の `key:<名前>` のうち、`account_choice.verify` に通り `tried` に無いものを返す |
| `_metered` | `wrapper.py` | 新設 | `(self) -> Optional[tuple[dict[str, str], bool]]` | 従量の接続の設定（変数の組と、保存した設定か）。無い・読めない・検証に外れれば None |
| `_pinned_or` | `wrapper.py` | 新設 | `(self, account: Optional[str], now: float) -> Optional[str]` | 指定があり、候補にあり、`usages`（上限の観測を重ねたもの）で上限でなく、組 0〜2 にあるか、`order` の先頭が組 3 以上（閾値の内側の候補がほかに無い）なら指定、そうでなければ `account` |
| `_initial` | `wrapper.py` | 新設 | `(self, now: float) -> Choice` | 最初のセグメントの要求。環境変数 `NDF_CLAUDE_ACCOUNT` の名前 → 指定の名前の順に、`_candidates` にあり上限でない名前だけを `names` に入れる（環境変数の名前が使えなければ指定を読む）。`fallback` は None |
| `_launch_target` | `wrapper.py` | 新設 | `(self, choice: Choice, reason: str, now: float) -> Optional[Target]` | `choice.names` を順に試し、すべての名前に `_candidates` と上限の除外を当ててから（先頭も除外する）、登録済みアカウントは `account_dir.prepare(…, cwd, argv)`（`argv` は子に渡す実際の起動引数）が ok のものを返す。尽きたら `choice.fallback`。理由が `limit`・`auth` で `fallback` の `prepare` も落ちれば None（起動しない）。飛ばしたものは画面に 1 行 |
| `_auth_pick` | `wrapper.py` | 新設 | `(self, sec: Section, screen: str) -> Optional[Pick]` | 認証の失敗を確かめた（下の「認証の失敗の確かめ」）最初の見回りで、今の登録済みアカウントを `accounts.hold` し、`after_auth` を返す |
| `_drift` | `wrapper.py` | 新設 | `(self, sec: Section) -> Optional[Pick]` | 起動の後の設定の変更の検出（`account_dir` の「起動の後の設定の変更」）。`Section.resolved`（起動のときの `Resolved`）の印を `stat` で比べ、変わったときだけ確かめ直す。当たれば `Section.drift_at` を立てて `after_auth` の結果を返し（`hold` しない）、`_state` はそれを `auth_pick` に入れる |
| `_settle` | `wrapper.py` | 新設 | `(self, sec: Section) -> None` | 終わった子のアカウントが登録済みなら `account_dir.settle` を打ち、失敗の理由を 1 行出す |
| `_tell_at_start` | `wrapper.py` | 新設 | `(self) -> None` | 再登録が要るアカウントを 1 つにつき 1 行、従量の接続の設定が壊れていれば 1 行出す |
| `_loop` | `wrapper.py` | 変更 | 変わらない | 最初のセグメントの前に `_tell_at_start` を呼ぶ |
| `_state` | `wrapper.py` | 変更 | 変わらない | `auth_failed` の代わりに `auth_pick=self._auth_pick(sec, screen) or self._drift(sec)` を渡す。`guard_pick=self._guard_pick(sec, now)`・`safe=self._safe_point(sec)`・`switches_today`・`max_switches` を渡す |
| `_limit_pick` | `wrapper.py` | 変更 | 変わらない | 上限の行を初めて見た見回りで、今が登録済みアカウントなら `accounts.limit_until(上限の行, 今のアカウントの窓, now, 地域の時間帯)` で `until` と窓の名前を決めて `accounts.see_limit` を書く。`_inputs` の使用率・候補・従量の接続を渡して `after_limit` を呼ぶ。ファイルの更新時刻による補正は消す（上限の観測が持つ） |
| `_on_switch` | `wrapper.py` | 変更 | 変わらない | `before_limit` に候補と `Guards` を渡す。`keep` で今のアカウントが候補に無い（外した間・再登録が要る・用意できない）なら `order` の先頭へ替える。次の要求は `Choice((_pinned_or(pick.account),) + order の残り, fallback=None)` |
| `_on_resume` | `wrapper.py` | 変更 | 変わらない | 手が `account` のときだけ `Choice((_pinned_or(step.account),) + order の残り + _fallbacks, fallback=今のアカウント)` で起動する。今のアカウントと違う起動先なら `_count_switch` を打ち、`_count_start` を打たない。`restart` は今のアカウントのまま `_count_start` |
| `_start` | `wrapper.py` | 変更 | 変わらない | 前のセグメントがあれば `_settle`。`_launch_target` が None なら子を起動せずに `Ended("account_unavailable", 理由)` を返す（会話を送らない。会話の記録は共有側の `projects` に残り、直した後のラッパーの起動で同じ会話を `--resume` で続けられる）。`_launch_target(req.choice, req.reason)` で確定した `Target` だけから、`child_env(…, account=target.account, decided=True)` を作り、登録済みなら `account_env(…, usage_path(state_dir, name, target.generation))`、`metered` なら `metered_env` を重ねる。子の起動が成功した後に、登録済みなら `account_dir.attach(store, name, target.lease, 子の pid)` を打ち、`Section.account`・`section` の行の `account`・`account` の行（下の「ログと記録の出力」）を同じ `Target` から 1 回だけ書く。子の起動に失敗したら `account_dir.release(store, name, target.lease)` を打つ |
| `_end` | `wrapper.py` | 変更 | 変わらない | 今のセグメントがあれば `_settle` を打ってから今の止まり方へ |
| `_guard_pick` | `wrapper.py` | 新設 | `(self, sec: Section, now: float) -> Optional[Pick]` | 上限の行も認証の失敗も無い見回りで、`_inputs` の使用率・候補・`Guards` で `account_decide.guard` を呼ぶ。`switch` なら子を止める前の確定（下）と同じく替え先へ `ready` を打ち直す。`_inputs` の印が変わらず `valid_until` 前なら控えの判断を返す |
| `_safe_point` | `wrapper.py` | 新設 | `(self, sec: Section) -> bool` | 子の出力が `relay.quiet` の秒止まり、回答待ちの質問が無く、`observe.tool_pending(会話の記録)` が偽のときだけ真。`tool_pending` が None（読めない）なら偽 |
| `_count_switch` | `wrapper.py` | 新設 | `(self) -> None` | 今日アカウントを替えて起動し直した回数を 1 増やす（`_count_start` と同じ日ごとの記録の、別の数） |

- `_launch_target`
  - 事前条件: 子が止まっている（呼ぶのは `_start` だけ）。上限・認証の失敗の後の要求なら、子を止める前に `_inputs` の候補（`account_dir.ready` を通ったもの）で替え先を決めてある
  - 事後条件: 返りの `account` が登録済みなら `config_dir` は ok の用意の済んだパスで、`generation` はそのときの登録の世代。`metered` なら `_metered` が None でない。None なら既定のログイン。返りの `account` は `choice.names` か `choice.fallback` のどれかで、それ以外の登録済みアカウントを列挙しない
  - 副作用: `account_dir.prepare`（symlink・同期・印・使用の届け）、飛ばした名前ごとの画面の 1 行と `log.jsonl` の `account_dir` の行
- `_auth_pick`
  - 副作用: `accounts.hold`（`until = now + limits.seconds("relay.auth-hold")`、今の世代）。替え先が無く直し方の 1 行をまだ出していなければ出して `auth_told` を立てる（同じセグメントで 1 回）

#### 子を止める前の替え先の確定

上限と認証の失敗の替え先は、子を止める前に決める。判断の関数へ渡す `candidates` は、`_inputs` が `account_dir.ready` で確かめた名前だけである。控えを読んだ後に設定・認証・リンクが変わっても、控えの印は変わらない。そのため、`switch` の替え先が登録済みアカウントなら、子を止める直前にその 1 つへ `ready` をもう一度打つ。`metered` なら、従量の実効の設定の確かめをもう一度当てる。外れればその名前を候補から外した控えで判断をやり直し、子を止めない。`ready` は認証の上書きも、`link_shared` の後の `projects`・`settings.json` も確かめる。そのため、止める時点で起動できると分かっている。止めた後の `prepare` が競合（ほかのレーンの再登録・排他の待ち切れ）で落ちたときの扱いは、起動の理由ごとに決める。

| 起動の理由 | `Choice` の `names` | 尽きたときの `fallback` |
| --- | --- | --- |
| `first`（最初） | 環境変数の名前 → 指定（使えるものだけ。`_initial`） | 既定のログイン。名前が 1 つも無ければ登録済みの候補を試さずに既定のログイン |
| `cut`（カットポイント） | `_pinned_or(before_limit の結果)` → `order` の残り | 既定のログイン |
| `guard`（見回りの替え・従量の接続からの戻り） | `_pinned_or(guard の結果)` → `order` の残り（閾値の内側のものだけ。戻りは上限でないものすべて） | 今のアカウント（替え先の `prepare` が落ちたら替えずに同じ会話を続け、次の見回りでもう一度判断する） |
| `limit`（上限の後） | `_pinned_or(after_limit の結果)` → `order` の残り → `_fallbacks`（今の種類を除く） | 今のアカウント（同じ会話を同じ認証で `--resume` し、次の上限の行で `after_limit` が `wait_reset` を返して、最も早く解けるアカウントを待つ）。今のアカウントの `prepare` も落ちれば、既定のログインへは落とさず、起動しないで止める |
| `auth`（認証の失敗の後） | `after_auth` の結果 → `order` の残り → `_fallbacks`（同上） | 今のアカウント（認証の失敗の行がもう一度出て `stalled` になり、子を残して直し方を出す）。今のアカウントの `prepare` も落ちれば同上 |

`wait_reset`・`stalled` の手では子を止めないので、`_launch_target` を呼ばない。

`limit`・`auth` で今のアカウントも用意できないときは、別の認証で会話を送らないことを優先する。`_launch_target` が None を返し、`_start` が `Ended("account_unavailable", 理由)` で終える。画面に 1 行を出し、`log.jsonl` の `end` の行に理由の符号を出す。画面の 1 行は `ndf-relay: 替え先と今のアカウント <名前> のどちらも用意できない（<理由の文>）。会話を送らずに止めた。直してから ndf relay で続ける` である。今のアカウントが既定のログインなら `prepare` は無いので、このときは今どおり既定のログインで起動する。今のアカウントが `metered` で設定が使えなくなっていれば、同じく止める。

#### 認証の失敗の確かめ

画面の文字列（`observe.auth_failed`）は引き金だけに使い、それだけで外した間の記録・子の停止・替え先の起動をしない。次をすべて満たしたときに、実行中の claude 自体の認証の失敗と確かめる。

| 条件 | 根拠 |
| --- | --- |
| 画面の末尾（最後の入力欄より後）に `AUTH_FAILED` の行がある | 引き金。入力欄より前の行（利用者の入力のエコー・応答の引用・古い表示）は見ない |
| 子の会話の記録（`observe.transcript_path`）の最後の記録が、claude が書く API の失敗の記録（`isApiErrorMessage` が真の `assistant` の記録）で、資格情報そのものの失敗を示す（下の `CREDENTIAL_FAILURE`） | モデルの応答や利用者の入力に同じ文字列があっても、その記録は API の失敗の記録ではない |
| その記録の時刻がセグメントの起動より後で、その後に利用者の記録が無い | 前のセグメントの失敗や、直した後の古い表示で動かない |

`CREDENTIAL_FAILURE`: 既存の `AUTH_FAILED` は HTTP 403 も拾う。403 には、認証は通った資格情報に対するモデル・組織の権限の制限（API の `permission_error`）が含まれる。これをログインの失敗として扱うと、使えるアカウントを 1 時間外し、別のアカウントや従量の接続へ会話を送る。そのため、記録の本文が次のどれかに当たるときだけ資格情報の失敗とする。

| 当たる | 当たらない |
| --- | --- |
| HTTP 401、または API の失敗の型が `authentication_error` | HTTP 403 の `permission_error`（モデルや組織の権限の制限。替えずに今どおり画面に残す） |
| 本文が資格情報の失効・取り消しを示す（`OAuth token has expired`・`OAuth token revoked`・`invalid x-api-key`・`Please run /login`） | 本文に `403` だけがあり、上の型・文が無い |

画面の `AUTH_FAILED` は引き金のままとし、403 の行でも会話の記録を読む。記録が 403 の権限の制限なら何もしない。確かめられない（記録が読めない・形が違う）ときも何もしない（今の `stalled` も出さない）。`isApiErrorMessage` の名前と形、401 と 403 の記録の本文の形は、実装の PR で手元の会話の記録から確かめる。違えば実装を止めて設計へ戻す（「未確認のまま残ること」）。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_launch_target` | 用意の失敗 | `account_dir` の理由 | `prepare` の返り | 次の候補へ | 画面に 1 行（`ndf-relay: アカウント <名前> で起動しない（<理由の文>）。次の候補で起動する`）と `log.jsonl` の `account_dir` の行 | 次のセグメントで再び試す |
| `_launch_target` | 登録の保存先が読めない | `AccountError`・`OSError` | `names`・`load` | 既定値で続ける（候補を空として既定のログインへ。理由が `limit`・`auth` で今が登録済みアカウントなら None） | `log.jsonl` に 1 行 | 同上 |
| `_start` | `account_unavailable` | 理由が `limit`・`auth` で、替え先も今のアカウントも用意できない | `_launch_target` の None | 子を起動せずに止める | 画面に 1 行と `log.jsonl` の `end` の行。ラッパーは停止の終了コード | 直してからラッパーを起動し直せば、同じ会話を `--resume` で続ける |
| `_auth_pick` | 外した間を記録できない | `accounts.hold` の錠・書き込みの失敗 | `hold` | 既定値で続ける（今の判断では今のアカウントを候補から除く） | `log.jsonl` に 1 行 | 次の認証の失敗で再び書く |
| `_settle` | 書き戻しの失敗 | `account_dir.settle` の理由 | 返り | 既定値で続ける | 画面に 1 行と `log.jsonl` に 1 行 | 次の起動の前の同期 |
| `_metered` | 設定が壊れている | `load_metered` の理由 | 読み | 設定なしとして扱う | `_tell_at_start` が 1 行 | 設定を直せば次の起動で効く |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_launch_target` | 試す名前が `metered` で設定がある | `Target("metered", None, …)` |
| `_launch_target` | 試す名前が登録済みで、候補にあり、上限でなく、`prepare` が ok | `Target(名前, 設定ディレクトリ, 世代, …)` |
| `_launch_target` | 試す名前が候補に無い（外した間・再登録が要る・登録が無い）か上限 | `prepare` を打たずに 1 行を出して次の名前（先頭の名前も同じ） |
| `_launch_target` | 試す名前の `prepare` が使えない | 1 行を出して次の名前 |
| `_launch_target` | 試す名前が `metered` で設定が無い | 1 行を出して次の名前 |
| `_launch_target` | 試す名前が `key:<名前>` で `recovery_keys` にある | `Target("key:<名前>", None, …)`。子の起動引数に `account_choice.target_env` が `--settings` の `apiKeyHelper` を足す |
| `_launch_target` | 試す名前が尽きた・理由が `first`・`cut` | `choice.fallback`（登録済みならもう一度 `prepare`。落ちれば既定のログイン） |
| `_launch_target` | 試す名前が尽きた・理由が `limit`・`auth` | `choice.fallback`（今のアカウント）の `prepare`。落ちれば None（起動しない。既定のログインへ落とさない） |
| `_pinned_or` | 指定が無い・壊れている | `account`（壊れた指定は今と同じく `AccountError` で止まる） |
| `_pinned_or` | 指定が候補に無い（再登録が要る・外した間・登録が無い・用意できない）か上限（上限の観測を含む） | `account` |
| `_pinned_or` | それ以外 | 指定 |
| `_guard_pick` | 今が組 3 で、`guard` が組 0〜2 の替え先を返す | `switch`。`_safe_point` が真になるまで子を止めない |
| `_limit_pick` | 画面に上限の行が無く、今のアカウントのキャッシュの窓が 100 以上で、子の出力が `relay.quiet` の秒止まっている | 上限の行を見たときと同じく上限の観測を書いて `after_limit`（行の文言が変わっても最後の砦を働かせる。決定 8） |

- 繰り返しの終わる条件: `_launch_target` は名前の並びを 1 回ずつ試し、同じ名前を 2 回試さない（`fallback` の 1 回を除く）
- 見回りの読み取り: 見回りのたびに読むのは `_inputs` の印だけである。見回りの判断 `guard` と上限の後の判断は、同じ控えを使う。印は `stat` で取る。対象は、登録済みアカウントごとの使用率の世代のファイルと `account.json` の 2 つである。ほかに `held.json`・`limited.json`・`metered.json`・`api-keys.json` の 4 つを取る。印が変わらず `valid_until` 前なら、使用率・登録・従量の接続のファイルを開かない。リセットを何時間待っても、ファイルを開く回数は status line の書き込み・登録の変更・解除の時刻の数に比例する。見回りの回数とレーン数・登録数の積には比例しない

直し方の 1 行の文（`AUTH_FIX`）:

| ログインの形 | 文 |
| --- | --- |
| 既定のログイン | `claude の中で /login を打つ` |
| 登録済みアカウント | `この子を /exit で終えてから ndf relay account add <名前> で登録し直す`（子が動いている間は登録のコマンドが止まる） |
| 従量の接続 | `ndf relay account check metered で設定を確かめる` |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_launch_target` | `{"kind": "account_dir", "account", "reason", "local_only", "skipped", "added"}` | `log.jsonl` | トークン・メールアドレス・パス |
| `_start` | 子の起動が成功した後に 1 回、`{"kind": "account", "from": 前のセグメントのアカウント, "account": Target.account, "reason": Target.reason, "skipped": Target.skipped}`。前と同じアカウントで起動したときも書く（受け入れ条件はこの行の `from` と `account` の違いで切り替えを見る） | `log.jsonl` | 同上 |
| `_auth_pick`・`_limit_pick`・`_on_switch` | `account` の行を書かない（判断は起動ではない。回答待ちのガードで子を止めなかったとき・起動に失敗したときに切り替えたと記録しないため） | — | — |
| `_tell_at_start` | 再登録が要るアカウントの名前の 1 行 | 画面 | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `Target`・`Section.auth_at`・`auth_told` | `Launch`・`Section.limit_at` | ファイルを読んで確かめた | 同じ形の型とフィールドにする |
| `_launch_target` | `accounts.chosen`・`account_dir.prepare` | 同上 | `prepare` を使う。候補を順に試す部品は無いので足す |
| `_candidates` | `account_registry.names`・`load`・`accounts.held` | ファイルを読んで確かめた | 使う |
| `_metered` | `account_registry.fallback_env`・`load_metered`・`declared_env` | 同上 | 使う。保存した設定かは `declared_env` が None かで決める |
| `_pinned_or`・`_initial` | `accounts.pinned`・`accounts.chosen` | 同上 | `pinned` を使う。`chosen` は環境変数と指定の一方しか返さず、使えない名前の次を読めないので、ラッパーは `_initial` で環境変数 → 指定の順に読む（`chosen` はラッパーの外の `child_env(decided=False)` のために残す） |
| `_auth_pick` | `_limit_pick` の形・`observe.auth_failed`・`observe.transcript_path` | 同上 | 同じ形で書く。画面の照合は引き金だけにし、会話の記録で確かめる |
| `_settle`・`_tell_at_start` | `_say`・`_log` | 同上 | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_start`・`_launch_target` | 指定の `work2` で子の環境が `account_env` の形（偽の起動で環境を受け取る） | 指定が使えないとき判断の結果・すべて飛ばして既定のログイン | 理由ごとに 1 行と `account_dir` の行が出て次の候補で起動する |
| `_initial`・`child_env` | 環境変数 `work1` が使えれば `work1` | 環境変数も指定も無く、登録済みアカウントが 2 つあるとき、既定のログインで起動し `prepare` が 1 回も呼ばれない。環境変数の `work1` が外した間で指定が `work2` なら `work2`。環境変数の `work1` を飛ばして既定のログインへ着地したとき、子の `NDF_CLAUDE_ACCOUNT` が無く、`account` の行と `section` の行の `account` が None | 先頭の名前が外した間・再登録が要る・上限のとき `prepare` が呼ばれない |
| `_on_switch`・`_pinned_or` | 動いている間に指定を替えると次のセグメントがそのアカウント | 指定が上限・外した間・再登録が要るとき判断の結果。`keep` の今のアカウントが外した間なら `order` の先頭 | 壊れた指定で今と同じく止まる |
| `_on_resume`・`_pinned_or` | 上限の行の後、指定の `work1`（古い 92% の記録）が選ばれず `work2` で `--resume` | 上限の行を見た `work1` が `--resume` で選び直されず、1 日の起動の上限まで繰り返さない | — |
| `_limit_pick` | 使用率の分からない `work2` で `--resume` | すべて上限で従量の接続・従量の接続も無くリセット待ち。status line の無い経路で、上限の観測の `until` を過ぎると同じアカウントで `--resume` | — |
| 子を止める前の確定 | 替え先の `work2` が用意できれば子を止めて `work2`。`projects` がまだ無い初回の `work2` へも替える | 唯一の替え先の `projects` が実体のとき、上限では従量の接続（設定があれば）かリセット待ちで子を止めない。認証の失敗では `stalled` で子を残す。唯一の替え先の実体の `settings.json` に `apiKeyHelper` があるときも同じ。控えを読んだ後に `work2` の `.credentials.json` を消すと、止める直前の `ready` で外れて子を止めない | 止めた後に `prepare` が競合で落ちたとき、上限・認証の失敗では既定のログインへ落ちず今のアカウントで起動する。今のアカウントの `prepare` も落ちれば、子を起動せず `account_unavailable` で止まり、既定のログインの起動が呼ばれない |
| `_auth_pick` | 会話の記録の最後が 401 の API の失敗の記録で、`work2` で `--resume`、`held.json` に `work1` | 既定のログインの認証の失敗でも替える。`OAuth token revoked` の 403 でも替える | 利用者の入力のエコー・モデルの応答の引用・前のセグメントの古い表示に `API Error: 401` があっても、`held.json` が変わらず子を止めない。会話の記録の最後が 403 の `permission_error`（モデル・組織の権限の制限）なら、画面に `API Error: 403` があっても `held.json` が変わらず子を止めない。替え先が無ければ直し方の 1 行がセグメントで 1 回、ログインの形ごとの文 |
| `_inputs` | 上限の行が出たまま 1000 回見回っても、ファイルの印が変わらなければ使用率と登録のファイルを開くのは 1 回 | status line が `usage/<名前>/<世代>.json` を置き換えで書く（`usage/` の更新時刻が変わらない）と 1 回読み直す。解除の時刻を過ぎると 1 回読み直す | — |
| `account` の行 | 替えて起動が成功したら 1 行 | 回答待ちのガードで子を止めなかったとき行が無い。替え先の用意に落ちて次の候補で起動したとき、行の `account` が実際の起動先で `skipped` に名前 | 子の起動に失敗したとき行が無い |
| `_settle`・`_end` | セグメントの終わりとラッパーの終わりに 1 回ずつ書き戻す | 既定のログインと従量の接続では打たない | 書けないとき 1 行で続ける |
| `_tell_at_start` | 再登録が要る 2 つで 2 行 | 無ければ出さない | 従量の接続の設定が壊れていると 1 行 |
| `_guard_pick`・`_safe_point`（決定 11） | `work1` の 5 時間が 86% になり `work2` が余裕の組なら、止めてよい時点で `work2` へ `--resume`（偽の起動で `GUARD_RESUME_PROMPT` を受け取る） | 会話の記録の最後が `tool_result` の無い `tool_use` の間・質問の回答待ちの間は止めない。`work2` も組 3 なら替えない。従量の接続の子で `work1` の 5 時間の窓が解けると、カットポイントを待たずに `work1` へ戻る | 会話の記録が読めないとき子を止めない |
| `_on_resume`・`_count_switch`（決定 11） | アカウントを替えた起動が `relay.max-starts` を増やさず、替えた回数が 1 増える | 48 回目の後は見回りで替えず、上限の後は `wait_reset` で `stop` にならない | — |
| `_limit_pick`（決定 8） | すべてが上限で従量の接続も API キーも無いとき子を残し、`work2` の 5 時間の窓が先に解けると `work2` で `--resume` | 画面に上限の行が無くても、キャッシュの窓が 100 以上で静かなら上限として替える | — |
| `_launch_target`・`_fallbacks`（決定 12） | 従量の接続が無く `key-main` があると `key:key-main` で起動し、起動引数に `--settings` の `apiKeyHelper`、環境に `ANTHROPIC_API_KEY` が無い | 使い方が `off` ならキーを試さない | 起動引数・`log.jsonl`・画面にキーの値が無い |

## 子の画面と記録の観測（`observe.py`）

満たす決定: 決定 11

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `tool_pending` | `observe.py` | 新設 | `(path: Path, size: int) -> Optional[bool]` | 会話の記録の末尾 `size` バイト（`relay.transcript-tail`）を後ろから読み、最後の `assistant` の記録の `tool_use` の `id` に対応する `tool_result` が後ろに無ければ真、あれば・`tool_use` が無ければ偽。記録が無い・読めない・形が違うときは None |

単体テストの観点: `tool_use` の後に `tool_result` が無ければ真、あれば偽、最後が本文だけの応答なら偽、末尾で切れた行は飛ばし、ファイルが無ければ None。会話の記録の形は実装の PR で手元の記録から確かめ、違えば設計へ戻す（設計文書の「未確認のまま残ること」）。

## 共有の替え先（`account_choice.py`）

満たす決定: 決定 6・8・9・12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `candidates` | `account_choice.py` | 新設 | `(store: Path, state_dir: Path, now: float, cwd: Path, argv: list[str]) -> list[str]` | 登録済みアカウントのうち、再登録が要らず、外した間でなく、`account_dir.ready` が None のもの（ラッパーの `_candidates` が起動可否の控えとともに呼ぶ） |
| `fallbacks` | `account_choice.py` | 新設 | `(store: Path, environ: Mapping[str, str], metered: Optional[tuple[dict[str, str], bool]], cwd: Path, argv: list[str], tried: frozenset[str]) -> list[str]` | `metered` があれば `metered`、続けて `recovery_keys` の `key:<名前>` を並べ、`tried`（この復帰の続きで試して失敗した接続先。決定 6）を除き、どれも `target_env` と同じ組み立てと `verify` を当てて通ったものだけを返す。ファイルを書かない |
| `build` | `account_choice.py` | 新設 | `(env: Mapping[str, str], target: str, store: Path, state_dir: Path, argv: list[str]) -> tuple[dict[str, str], list[str]] \| str` | 従量の接続と API キーの子の、最終の環境と最終の起動引数を作る（書かない）。`metered` は `metered_env`、`key:<名前>` は `key_env` と下の「`--settings` の合成」。作れなければ理由の符号 |
| `verify` | `account_choice.py` | 新設 | `(env: Mapping[str, str], argv: list[str], cwd: Path, config_dir: Path, target: str, expected: Mapping[str, str]) -> Optional[str]` | 最終の環境・起動引数・作業ディレクトリで `account_dir.effective_env` を 1 回打ち、下の「起動の前の実効値の確かめ」を当てる。通れば None、外れれば `metered_overridden`・`key_overridden`（項目名と出所だけ） |
| `target_env` | `account_choice.py` | 新設 | `(env: Mapping[str, str], target: str, store: Path, state_dir: Path, cwd: Path, argv: list[str]) -> Optional[tuple[dict[str, str], list[str], Optional[account_dir.Prepared]]]` | 確定した替え先の子の環境と最終の起動引数。登録済みアカウントは `account_dir.prepare` の後に `account_env`（`prepare` が認証の上書きを確かめる）。`metered`・`key:<名前>` は `build` の後に `verify` を打ち、外れれば None。ラッパーと `ndf plan` の打ち直しはどちらもこの関数だけで替え先を作る |
| `retry_env` | `account_choice.py` | 新設 | `(env: Mapping[str, str], state_dir: Path, now: float, cwd: Path, argv: list[str]) -> Optional[tuple[dict[str, str], list[str], str, Optional[account_dir.Prepared]]]` | `ndf plan` の打ち直しの環境。`env` の `NDF_CLAUDE_ACCOUNT` の上限の観測を `see_limit` で書き（`until` は `limit_until` の (4)）、`after_limit(現在, rows, candidates, fallbacks, Guards)` が `switch` なら `target_env` を返す。`wait_reset` なら None |

#### `--settings` の合成と引用

API キーの子の起動引数には、`apiKeyHelper` を 1 つの `--settings` として足す。

| 子に渡す前の `argv` | 合成 |
| --- | --- |
| `--settings` が無い | `["--settings", <JSON>]` を足す。`<JSON>` は `{"apiKeyHelper": <helper の文字列>}` |
| `--settings <JSON の文字列>` が 1 つある | その object に `apiKeyHelper` を足した 1 つの JSON へ置き換える。元の object が `apiKeyHelper` を持てば `key_overridden`（`apiKeyHelper（--settings）`）で、その候補を使わない |
| `--settings <ファイル>` が 1 つある | ファイルを読み、上と同じく足した JSON の文字列へ置き換える（ファイルは書き換えない）。読めない・object でなければ `key_unresolved` |
| `--settings` が 2 つ以上ある | `key_unresolved`（Claude Code の重ね方が文書に無い） |

`<helper の文字列>` は Claude Code がシェルで実行するので、`shlex.join([<ndf の実行ファイルの絶対パス>, "relay", "account", "api-key-helper", <名前>])` で作る。実行ファイルのパスに空白や引用符があっても 1 語になる。名前は登録簿の名前の形（英小文字・数字・`-`）に限るが、同じく引用する。`--settings` の値は子の起動の引数の 1 要素として渡し、シェルを通さない。

#### 起動の前の実効値の確かめ（`verify`）

`expected` は `build` が作った最終の環境（設定ファイルを重ねる前の値）である。登録簿の API キーの子では、次をすべて確かめる。

| 確かめ | 外れる例 |
| --- | --- |
| 実効の `apiKeyHelper` の出所が `--settings` で、値が合成した `<helper の文字列>` と同じ | 管理の設定の `apiKeyHelper` が勝つ（管理の設定は `--settings` より上）・合成の後に別の `--settings` が来る |
| 実効の `env` の `PROVIDER_ENV` の `provider_on` がどれも偽（None があれば外れる） | 共有側・管理の設定の `env` の `CLAUDE_CODE_USE_BEDROCK=1` |
| 実効の `env` の `ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN` が空か無い（STD2 で `apiKeyHelper` より上か同じ段に来る資格情報） | 管理の設定の `env` の `ANTHROPIC_AUTH_TOKEN` |
| `METERED_CHECKED` のうち提供元の選択と上の 3 つの外の変数ごとに、実効値が `expected` と同じ（`expected` に無ければ実効値にも無いか空文字） | 設定ファイルの `env` の `ANTHROPIC_BASE_URL`・`ANTHROPIC_PROFILE` |
| `unresolved` が空 | 読めない管理の設定 |

従量の接続（`metered`）の子では、`account_dir` の節の「従量の実効値の照合」を `verify` が当てる（同じ関数）。どちらも、`argv` は `build` の後の最終の起動引数で、子と `ndf plan` の `claude -p` のどちらでも同じ値を渡す。`fallbacks` が返した名前は、この確かめに通っている。止める直前（「子を止める前の替え先の確定」）と `target_env` でもう一度打つので、間の設定の変更も拾う。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `verify` | `metered_overridden`・`key_overridden` | 設定ファイル・`--settings` から別の helper・提供元・資格情報・接続先が入る | `effective_env` の結果 | その候補を外す | 画面に 1 行（項目名と出所だけ）と `log.jsonl` に 1 行 | その設定から項目を外せば次の判断で戻る |
| `build`・`verify` | `key_unresolved` | `--settings` が読めない・2 つ以上ある | `argv` の解釈 | 同上 | 同上 | 同上 |
| `target_env` | None | `prepare` の失敗・`verify` の外れ | 返り | 呼び出し元が次の候補へ | 呼び出し元の 1 行 | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `verify` | `account_dir.effective_env`・従量の実効値の照合 | この文書の `account_dir` の節 | 使う。API キーの照合の行を足すだけで、源の読み方は持たない |
| `build` の引用 | `shlex.join` | 標準ライブラリ | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `build`・`verify` | `key-main` で `--settings` の JSON の `apiKeyHelper` が `shlex.join` の文字列になり、実効の helper の出所が `--settings` | 子の `argv` に既に `--settings '{"model": "x"}'` があれば 1 つにまとまり `model` も残る。ndf の実行ファイルのパスに空白があっても helper の文字列が 1 語の引用になる | 管理の設定の `apiKeyHelper`・管理の設定の `env` の `CLAUDE_CODE_USE_BEDROCK=1`・`ANTHROPIC_AUTH_TOKEN`・`ANTHROPIC_BASE_URL` のそれぞれで `key_overridden`。`--settings` が 2 つで `key_unresolved` |
| `fallbacks` | `metered` と `key:k1` が順に返る | `tried` に `key:k1` があれば返らない | 照合に外れた `metered` が返らない |
| `target_env` | ラッパーと `ndf plan` の打ち直しが同じ環境と起動引数を受け取る | — | `verify` に外れれば None で、子も `claude -p` も起動しない |

## `ndf plan` の `claude -p`（`adapters/claude.py`。環境は子から継ぎ、認証の上書きを確かめ、上限で 1 回打ち直す）

満たす決定: 決定 1・9、受け入れ条件 #3604 の 11

環境の作り方は変えない。`call_claude` が起動する `claude -p` の環境を、`tests/adapters/test_claude_reply.py` に足すテストで縛る。縛るのは、親の環境の `CLAUDE_CONFIG_DIR`・`NDF_CLAUDE_ACCOUNT` をそのまま持つことである。足すのは起動の前の 1 つの確かめだけである。環境の `NDF_CLAUDE_ACCOUNT` が登録済みアカウントの名前なら、起動する直前に認証の上書きを確かめ、当たれば起動しない。打つのは `account_dir.auth_overrides(env, Path(env["CLAUDE_CONFIG_DIR"]), cwd, argv)` である。渡すのは、最終的に使う `argv`・環境・作業ディレクトリ（起動方法ごとに組み立てた後の値）である。`argv` の `--setting-sources` が読む源を決める。そのため `minimal` の起動（`--setting-sources ''`）では、利用者・プロジェクトの設定を見ない。実際には読まれない `apiKeyHelper` で、計画の実行を拒否しない。管理の設定と `--settings` はどの起動でも見る。CLI の起動と API の呼び出しは増えず、増えるのは設定ファイルの読み取りだけである。

上限での打ち直し（決定 9）: 2 つの条件がそろえば、`account_choice.retry_env(env, 状態の保存先, now, cwd, argv)` を呼ぶ。`call_claude` の結果が `Failure.USAGE_LIMIT` であることと、環境の `NDF_CLAUDE_ACCOUNT` が登録済みアカウントの名前であることである。環境が返れば、その環境と最終の起動引数（`target_env` が合成と確かめを済ませたもの）で同じ呼び出しを 1 回だけ打ち直し、その結果を返す。登録済みアカウントへの打ち直しでは、`claude -p` の起動の後に `account_dir.attach`、終わった後に `account_dir.settle` を打つ（「使用の届けの状態」）。打ち直しも上限なら、もう打ち直さずに上限の失敗を返し、計画の打ち直しの待ちへ進む。None なら今どおり上限の失敗を返す。`NDF_CLAUDE_ACCOUNT` が無い・`metered`・`key:` のときは打ち直さない。打ち直しの前に `stderr` へ 1 行（`ndf plan: アカウント <名前> が利用上限に当たった。<替え先> で打ち直す`）を出し、キーの値は出さない。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `call_claude` | 親の環境の `CLAUDE_CONFIG_DIR` が起動の環境に同じ値で入る | `NDF_CLAUDE_ACCOUNT` も同じ値。既定のログイン・`metered` では `auth_overrides` を打たない。`minimal` の起動（`--setting-sources ''`）では、作業ディレクトリの `.claude/settings.json` に `apiKeyHelper` があっても起動する | 設定ソースを有効にした起動で、worker の作業ディレクトリの `.claude/settings.json` に `apiKeyHelper` があると、`claude -p` を起動せずに項目名を入れた失敗を返す（偽の起動が呼ばれない）。`minimal` の起動でも、管理の設定の `apiKeyHelper`・環境の `ANTHROPIC_API_KEY`・環境か管理の設定の既定でない `ANTHROPIC_BASE_URL`では起動しない |
| `call_claude` の打ち直し（決定 9） | `work1` の環境で上限の失敗が返ると、`limited.json` に `work1` が書かれ、`work2` の設定ディレクトリの環境で 1 回だけ打ち直し、その結果を返す | 打ち直しも上限なら 2 回目を打たずに上限の失敗を返す。替え先が無ければ打ち直さない。既定のログイン・`metered` では打ち直さない | 替え先の `prepare` が落ちれば打ち直さずに上限の失敗を返す |

## 一覧と API キーのコマンド（`cli_account.py`）

満たす決定: 決定 4・12、受け入れ条件 #3604 の 9

`_acc_list` は `accounts.usages(state_dir, time.time(), 登録の世代)` を渡す。`account use <名前>` は指定を書いた後に `accounts.unhold(state_dir, 名前)` を打つ（利用者が直して指定したアカウントを、外した間の残りで飛ばさない）。登録・削除の変更は「登録の保存先」の節にある。ほかは変えない。`state` は今の `_state` のまま（`再登録が要る` は `mark_relogin` の後に出る）。テストは `test_account_registry.py` の一覧の行で見る。

`ndf relay account api-key-helper <名前>` は、`_acc_key_helper` として新設する。`account_registry.key_value(store, 名前)` の値を標準出力へ 1 行出して、終了コード 0 を返す。無い名前・壊れた登録簿では何も出さず終了コード 1 を返し、標準エラーに理由（名前だけ）を出す。Claude Code の `apiKeyHelper` が呼ぶためのサブコマンドで、一覧や `--help` に値を出さない。テストは `test_account_registry.py` で、値が標準出力だけに出て、誤りの文に値が入らないことを見る。

## 手順書（`relay-accounts.md`）

満たす決定: 決定 10、受け入れ条件 #3864 の 12〜14

書き直しの中身は設計文書の決定 10 の「選んだ案」にある。書いた後に、手順書の振る舞いの主張を 1 つずつ、この文書の関数と照らす。文言のテストは書かない。
