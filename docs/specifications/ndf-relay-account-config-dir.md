# 中継の登録済みアカウントの子: アカウントの設定ディレクトリで起動し、会話・設定を共有側とそろえる

中継のラッパー（`ndf relay`。`ndf-claude` と `ndf relay spawn` が動かす）は、登録済みアカウントで起動する子の claude に、
そのアカウントの設定ディレクトリを `CLAUDE_CONFIG_DIR` で渡し、認証の変数を外す。設定ディレクトリの中の会話の記録・
設定・プラグインは共有の設定ディレクトリへの symlink にし、`.claude.json` の共有する部分は起動の前に同期して、
セグメントの終わりに書き戻す。そのため、アカウントを替えても同じ会話を `--resume` で開ける。使えないアカウントは
起動の前に確かめて飛ばし、次の候補で起動する。Claude Code だけが対象で、Linux だけで動く（macOS は認証を Keychain に
置くので、登録の時点で止まる）。

例: 登録済みアカウントが `work1` と `work2` で、`ndf relay account use work2` で指定した利用者のとき。

| 順 | 起きること |
| --- | --- |
| 1 | ラッパーは次のセグメントの前に指定を読み直し、`work2` の起動の前の用意（`account_dir.prepare`）を打つ。アカウントの排他の中で、使えるかを確かめ、共有側の項目への symlink を張り、`.claude.json` の `projects`・`mcpServers` を共有側とそろえ、使用の届けを書く |
| 2 | 子は `CLAUDE_CONFIG_DIR=<保存先>/work2`・`NDF_CLAUDE_ACCOUNT=work2`・`NDF_SHARED_CONFIG_DIR=<親の CLAUDE_CONFIG_DIR>` で、認証の 13 変数を持たずに起動する。関所で止めた子の pid を届けに書いてから、claude になる |
| 3 | 子は `work2` の認証で動き、会話の記録は symlink の先の共有側の `projects` に書かれる |
| 4 | セグメントが終わると、ラッパーは `work2` 側で変わった `.claude.json` の葉を共有側へ書き戻し、届けを消す |
| 5 | 次に `work1` へ替えても、`work1` の設定ディレクトリの `projects` は同じ共有側を指すので、`--resume <ID>` が同じ会話を開く |

この文書は、その形の構成要素・常に成り立つ条件・データの形・決定と理由・テスト観点を残す。替えるかの判断は
[ndf-relay-account-switching.md](ndf-relay-account-switching.md)、従量の接続と API キーの子は
[ndf-relay-metered-and-api-keys.md](ndf-relay-metered-and-api-keys.md)、`ndf plan` の `claude -p` は
[ndf-relay-plan-claude-accounts.md](ndf-relay-plan-claude-accounts.md) にある。

| 何を読むか | 正本 |
| --- | --- |
| 登録・指定・一覧のコマンドの打ち方と画面の文 | [`development-workflow` の `references/relay-accounts.md`](../../plugins/ndf/skills/development-workflow/references/relay-accounts.md) |
| 子の環境の変数ごとの渡し方 | [`contracts/domains/env.json`](../../plugins/ndf/contracts/domains/env.json) の `child` の列（生成物は [`docs/contracts/env.md`](../../plugins/ndf/docs/contracts/env.md)） |
| 起動の前の用意・同期・書き戻し・使用の届け・関所 | `plugins/ndf/src/ndf/domains/relay/account_dir.py`・`account_sync.py`・`account_files.py` |
| 子の環境の組み立て | `plugins/ndf/src/ndf/domains/relay/session_env.py` |
| 登録の保存先・登録の世代・再登録が要る印 | `plugins/ndf/src/ndf/domains/relay/account_registry.py`・`account_register.py` |
| 待ちの上限 | [`contracts/limits/`](../../plugins/ndf/contracts/limits/) の `claude-accounts.lock`・`claude-account-dir.lock`・`relay.gate-exec` |

## 用語

「登録済みアカウント」「既定のログイン」「再登録が要る」「従量の接続」「起動の前の用意」「セグメント」「カットポイント」
の定義は用語集（[docs/glossary.md](../glossary.md)）が正である。この文書で使うほかの語は次のとおりである。

| 語 | 意味 |
| --- | --- |
| 共有の設定ディレクトリ | 子へ渡す前の `CLAUDE_CONFIG_DIR`（無ければ `~/.claude`）。入れ子のラッパーでは親の `NDF_SHARED_CONFIG_DIR` が指す（`account_registry.shared_config_dir`） |
| アカウントの設定ディレクトリ | `<保存先>/<名前>`。保存先は `NDF_ACCOUNTS_DIR`、無ければ `<共有の設定ディレクトリ>/ndf/accounts`（`account_registry.store_dir`） |
| 登録の世代 | 登録・再登録のたびに `account.json` の `generation` へ書く乱数（`secrets.token_hex(16)`）。使用率の取得の記録と外した間は世代ごとに持ち、再登録の前の記録を新しい登録に効かせない |
| 使用の届け | 登録済みアカウントの設定ディレクトリを使うプロセスが生きている間、その名前の再登録・削除を止める記録（`<保存先>/.leases/<名前>/<届けの ID>.json`） |
| 関所 | 子を claude として起動する前に 1 バイトの合図を待たせる小さな Python の起動（`account_dir.gated`）。合図の前に、子の pid を使用の届けへ書く |
| 認証の上書き | 環境変数を外しても、設定ファイル・起動引数・プロファイルから入り、登録先の OAuth より上に来る認証の源と、既定でない接続先 |

## 背景

- 11.0.0 の作り直し（#3304）で、中継のアカウントの切り替えのうち登録のコマンドと替えるかの判断の純粋な関数だけを移し、
  判断から出る起動（アカウントの設定ディレクトリで子を起動する）を移さなかった。登録したアカウントを選んでも子は元の
  ログインで起動していた（#3604）。判断へ入る観測（使用率）も移していなかった（#3864）。どちらか片方だけを直しても
  切り替えは働かないので、1 つのスプリントで直した
- ラッパーは会話の記録の大きさを自分の `CLAUDE_CONFIG_DIR` の `projects` から読む（`observe.transcript_path`）。子の会話の
  記録が共有側に無いと、文脈の量による切り替えも働かない
- 10 系は 2026-10 まで、同じ symlink と 3 者の同期の形（`claude_account_dir.py`）で運用していた。11 系はこれを
  `account_dir.py` へ移し、ハードリンク・差し替え・途中の停止への確かめを足した

## 対象範囲

| 含む | 含まない |
| --- | --- |
| ラッパーが登録済みアカウントで子を起動するときの環境・起動の前の用意・書き戻し・使用の届け・関所 | 替えるかの判断と使用率（[ndf-relay-account-switching.md](ndf-relay-account-switching.md)） |
| 起動の前の確かめ（使えないアカウントの理由と、再登録が要る印） | 従量の接続と API キーの子の環境（[ndf-relay-metered-and-api-keys.md](ndf-relay-metered-and-api-keys.md)） |
| 登録・削除のコマンドが使用の届けと登録の世代を扱うこと | `ndf plan` の `claude -p` の確かめ（[ndf-relay-plan-claude-accounts.md](ndf-relay-plan-claude-accounts.md)） |
| 認証の上書きの確かめ（起動の前） | ラッパーの外で直に打つ `claude` |

## 仕様

### 構成要素

| 要素 | 責務 |
| --- | --- |
| 起動の前の用意（`account_dir.py`） | 使えないアカウントの判定（`check_account`）、共有の項目への symlink（`link_shared`）、同期（`sync_config`）、使用の届け（`prepare`・`acquire`・`attach`・`release`・`settle`）、関所（`gated`）、認証の上書きの判定（`effective_env`・`auth_overrides`）。アカウントの排他の中で動く |
| 同期の本体（`account_sync.py`） | `.claude.json` の `projects`・`mcpServers` の 3 者の突き合わせと、途中で止まった後の復旧 |
| 認証ファイルの読み書き（`account_files.py`） | 認証ファイルと保留の更新を、確かめた実体の記述子からだけ読み書きする（`pin_dir`・`read_auth`・`replace_auth`・`write_pending`・`apply_pending`・`refresh_lock`） |
| 子の環境（`session_env.py`） | 子の環境の表の `account-set`・`account-drop` の行から、登録済みアカウントの子の環境を作る（`account_env`）。既定のログインの子は `child_env`（`decided=True` で起動先を確定した名前だけを渡す） |
| 登録の保存先（`account_registry.py`・`account_register.py`） | 共有の設定ディレクトリの求め方（`shared_config_dir`）、登録の世代（`generation`・`legacy_generation`）、再登録が要る印（`mark_relogin`）、使用の届けの読み（`live_leases`・`refuse_in_use`）、登録と削除（`unregister`・`remove_account`） |
| ラッパー（`wrapper.py`・`wrapper_launch.py`・`launch_target.py`） | 候補を順に試して起動し、使えない名前を 1 行で知らせて次へ進み、セグメントの終わりとラッパーの終わりに書き戻す（`_launch_target`・`_settle`）。起動するたびに再登録が要るアカウントを 1 つにつき 1 行知らせる（`_tell_at_start`） |

### 常に成り立つ条件

| # | 条件 | 守り方 |
| --- | --- | --- |
| I1 | symlink の参照先は共有の設定ディレクトリの直下の項目だけで、共有側の認証ファイル（`.credentials.json`）を読み書きしない | アカウント固有の項目（`LOCAL_PREFIXES`・`LOCAL_NAMES`）を symlink の対象から外す |
| I2 | アカウント側の実体の項目を上書き・移動・削除しない | 同じ名前の実体があれば symlink を張らずに飛ばす（`skipped`）。アカウント側にだけある実体は `local_only` として名前だけを `log.jsonl` に残す |
| I3 | 登録済みアカウントで起動する子の `projects` は、共有の設定ディレクトリの `projects` と同じ実体である | 外れれば `projects_not_shared` で起動せず、次の候補へ移る |
| I4 | 登録済みアカウントの子の環境に認証の 13 変数（`account-drop`）が無い。実効の設定にも登録先の OAuth より上の認証の源と既定でない接続先が無い | 子の環境の表の `account-drop` の行から外す。設定に上書きがあれば `auth_overridden`（解けない源は `auth_unresolved`）でその候補を飛ばす |
| I5 | トークン・キーの値を画面・記録・結果に出さない | 理由は決まった文（`account_dir.REASONS`）と項目の名前・出所だけで出す |
| I10 | 子が動いている間、その名前の登録を替えず消さない。外した間と使用率の取得の記録は今の登録の世代のものだけが判断に効く | 登録・削除のコマンドが使用の届けを見て止める。世代の違う記録を読む側が捨てる |

番号は設計の通し番号を保つ（I6〜I9・I11〜I16 は [ndf-relay-account-switching.md](ndf-relay-account-switching.md) と
[ndf-relay-metered-and-api-keys.md](ndf-relay-metered-and-api-keys.md) にある）。

### 子の環境

子の環境の表（`contracts/domains/env.json`）の `child` の列は、`drop`（渡さない）・`set`（NDF が値を入れる）に加え、
`account-set`（登録済みアカウントの子で NDF が値を入れる）・`account-drop`（登録済みアカウントと従量の接続の子で
渡さない）を持つ。名前はコードに書かず、`facts.ids("env", child=…)` で表から引く。

| 変数 | 表の `child` | 既定のログインの子 | 登録済みアカウントの子 |
| --- | --- | --- | --- |
| `NDF_CLAUDE_ACCOUNT` | `set` | 無し | `<名前>` |
| `CLAUDE_CONFIG_DIR` | `account-set` | 親の値のまま | `<保存先>/<名前>` の絶対パス |
| `NDF_SHARED_CONFIG_DIR` | `account-set` | 親の値のまま | 親にあれば親の値、無ければ子へ渡す前の `CLAUDE_CONFIG_DIR`（無ければ空文字） |
| `CLAUDE_CODE_PLUGIN_CACHE_DIR` | `account-set` | 親の値のまま | 親にあればその値、無ければ `<共有の設定ディレクトリ>/plugins` |
| `NDF_ACCOUNT_GENERATION` | `account-set` | 親の値のまま | 起動を確かめた登録の世代（空文字は世代の無い登録）。子の `ndf plan` がリースと上限の後の替え先の判断に使う |
| `NDF_RELAY_PIN` | `account-set` | 親の値のまま | 外す（値が None の `account-set` は「外す」と読む） |
| `CLAUDE_CODE_OAUTH_TOKEN`・`CLAUDE_CODE_OAUTH_SCOPES`・`ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・提供元の選択の 6 つ（`CLAUDE_CODE_USE_BEDROCK`・`_VERTEX`・`_FOUNDRY`・`_ANTHROPIC_AWS`・`_ANTHROPIC_GOOGLE_CLOUD`・`_MANTLE`。表の `provider` の印）・`ANTHROPIC_PROFILE`・`ANTHROPIC_FEDERATION_RULE_ID`・`ANTHROPIC_ORGANIZATION_ID`（認証の 13 変数） | `account-drop` | 親の値のまま | 外す |

既定のログインの子は 2 通りに分ける。指定も環境変数も無い子は今までと同じ環境である。指定か環境変数に名前があるのに、
その用意に落ちて既定のログインへ移った子は `NDF_CLAUDE_ACCOUNT` を持たない（`child_env(…, decided=True)`）。子の変数を
実際の認証と一致させ、`ndf plan` が別の名前の使用の届けを取り、別の名前を上限として替え先を選ばないためである。

### 起動の前の確かめ

`check_account` は、読む前に実体を確かめ（`check_local`）、通ったときだけ認証ファイルと設定ファイルを読む。どれも
読むだけで、排他を取らず、バックアップを消さない。当たれば起動せず、理由を画面に 1 行・`log.jsonl` に 1 行出し、次の候補
（指定 → 判断の関数の候補 → 理由ごとの起動先）で起動する。

| 理由の符号 | 当たる条件 | 再登録が要る印 |
| --- | --- | --- |
| `no_credentials` | 認証ファイルが無い・読めない・JSON でない・アクセストークンが空 | 立てない |
| `no_refresh` | リフレッシュトークンが空 | 立てる |
| `account_unreadable` | アカウント側の `.claude.json` が無い・JSON の object でない | 立てない |
| `identity_mismatch` | アカウント側の `oauthAccount` のメールアドレスと組織が `account.json` と違う（組織が無ければメールアドレスだけ比べる） | 立てる |
| `legacy_config` | アカウント側に旧形式の `.config.json` がある（Claude Code が先に読む） | 立てる |
| `not_account_local` | 設定ディレクトリ・認証ファイル・設定ファイル・同期のバックアップ・途中の記録・保留の更新が、symlink・ハードリンク（`st_nlink` が 1 でない）・別の実体（`(st_dev, st_ino)` の違い）で、共有側か別のアカウントを指す。共有側の設定ファイルが保存先の下を指す | 立てない |
| `auth_overridden`・`auth_unresolved` | 実効の設定に認証の上書きがある・源が読めない（下の「認証の上書き」） | 立てない |
| `projects_not_shared` | アカウント側の `projects` が実体で、共有側の `projects` を指さない（`prepare`） | 立てない |
| `not_registered` | 登録が無い | 立てない |

印を立てる 3 つの理由は `account_dir.RELOGIN_REASONS`（`no_refresh`・`identity_mismatch`・`legacy_config`）で、
`prepare` がアカウントの排他の中で `account_registry.mark_relogin` を打ち、`account.json` の `needs_relogin` を真にする。
一覧（`ndf relay account list`）の `state` は `再登録が要る` になり、切り替えの候補から外れる。ラッパーは起動するたびに、
再登録が要るアカウントを 1 つにつき 1 行（`ndf-relay: アカウント <名前> は再登録が要る。ndf relay account add <名前> で登録し直す`）知らせる。

実体の確かめは `prepare` の `check_account` の先頭と、`settle` が排他を取った後の 2 か所で行う。共有側の設定ファイルは
dotfiles への symlink を許すため、名前を 1 回 `realpath` で解き、以後はその解決先だけを使う。`sync_config` は Claude Code
本体の錠を取った後に、自分が読み書きする両側の設定ファイルだけをもう一度 `os.lstat` で比べる。Claude Code 自身も
`.claude.json` を置き換えで書くので、`prepare` と `settle` の間に inode が替わるのは普通のことで、`settle` は解き直す。

### 認証の上書き

`effective_env` は、子が実際に受け取る環境・起動引数・作業ディレクトリから、Claude Code が採る実効値とその出所を作る。
源ごとに値の有無を見るのではなく、上位の源の空文字が下位の値を打ち消すことも含めて実効値で判定する。

| 源 | 読むか |
| --- | --- |
| 子の環境 | 常に |
| 管理の設定（`/etc/claude-code/managed-settings.json`・`managed-settings.d/*.json`）と、サーバーからの配布の控え（`<設定ディレクトリ>/remote-settings.json`） | 常に（`--setting-sources` に依らない） |
| 起動引数の `--settings <JSON かファイル>` | `argv` にあるとき。相対パスは子の作業ディレクトリから解く |
| 利用者の設定・プロジェクトの設定（`<cwd>/.claude/settings.json`・`settings.local.json`） | `--setting-sources` が無いか、その一覧に `user`・`project`・`local` があるとき |
| Anthropic のプロファイル（`ANTHROPIC_CONFIG_DIR`、無ければ `~/.config/anthropic`） | 常に |

`env` は変数ごとに 管理の設定 > `--settings` > ローカル > プロジェクト > 利用者 > 子の環境 の順で最上位の値を採る。
プロジェクトとローカルの `env` は、Claude Code が無視する `CLAUDE_CONFIG_DIR`・`CLAUDE_CODE_OAUTH_TOKEN`・
`CLAUDE_CODE_OAUTH_SCOPES` を重ねない。`apiKeyHelper` は、管理の設定がポリシーの項目を 1 つでも持てば選ばれた管理の源の
値だけを、持たなければ `--settings` > ローカル > プロジェクト > 利用者 の最上位を採る。

`auth_overrides` は、次のどれかで当たり、項目名（`<変数か項目>（<出所>）`）だけを返す。

- 実効の `env` で、認証の値の変数（`account-drop` のうち `secret` が真のもの）が空でない
- 提供元の選択の変数の `provider_on` が真（`1`・`true`・`yes`・`on`）。解けない値（例: `maybe`）は `auth_unresolved`
- 実効の `apiKeyHelper` が空でない（値は出さない）
- 実効の `ANTHROPIC_BASE_URL` が空でなく、既定の `https://api.anthropic.com`（末尾の `/` を除いて比べる）でない
- 実効の `ANTHROPIC_CUSTOM_HEADERS` が空でない（名前で安全なものを選り分けない。ヘッダーの名前も値も出さない）
- `ANTHROPIC_PROFILE` がある、`ANTHROPIC_FEDERATION_RULE_ID` と `ANTHROPIC_ORGANIZATION_ID` がそろう、または有効な
  プロファイルの認証の形が `oidc_federation`

読めない管理の設定・`--settings` のファイル・プロファイル、値の欠けた `--settings`・`--setting-sources` は
`auth_unresolved` で飛ばす。読めない利用者・プロジェクトの設定は無いものとする。サーバーからの配布の控えが無い初回は
上書きとせずに進める。`HTTPS_PROXY` などの通信の経路の変数は宛先を変えないので見ない。

### 起動の前の用意と書き戻し

`prepare` はアカウントの排他（`account_registry.locked`。`<保存先>/.locks/<名前>.lock`）の中で次を順に行う。どれかで
落ちれば `Prepared.ok` が偽で、理由の符号を返す（symlink の作成の途中で止まっていてよい。次の `prepare` が付け替える）。

1. 更新の途中でないことを確かめる。`<設定ディレクトリ>/.oauth_refresh.lock` があれば、何も読み書きせずに排他を放し、
   0.1 秒おきに取り直してやり直す（上限は `relay.oauth-refresh` と `claude-accounts.lock` の秒の和。超えれば残骸として続ける）
2. 保留の更新があれば当てる（`apply_pending`。使用率の取得がトークンを更新し、登録の排他を取れずに控えた分）
3. `check_account` で確かめる
4. `link_shared` で、共有側の直下のアカウント固有でない項目すべてへ symlink を張る（固定の一覧にしない）。共有側に
   `projects`（0700）と `settings.json`（`{}`、0600）が無ければ先に作る。参照先の違う symlink は付け替え、中断した付け替えの
   残り（`<項目>.<数字>.link`）は消してから張る
5. `projects` が共有側を指すことを確かめる
6. `sync_config` で `.claude.json` の共有する部分をそろえる
7. 権限を補正し（`account_registry.secure`）、使用の届けを `starting` で書く

`sync_config` は、共有側・アカウント側・同期のバックアップ（`.ndf-shared-base.json`）の 3 者で、`projects`（プロジェクトの
パス → 項目の 2 層の葉）と `mcpServers`（名前の 1 層の葉）を葉ごとに突き合わせる。Claude Code 本体と同じ
`<固定したパス>.lock` のディレクトリを、アカウント側・共有側の順に取る（`claude-account-dir.lock` の 2 秒。10 秒より古い
錠は残骸として除く）。

| 葉の状態 | 採る値 |
| --- | --- |
| 両側がバックアップと同じ | そのまま |
| 片側だけがバックアップと違う（値を変えた・消した・足した） | 違う側の状態（消したなら両側から消す） |
| 両側がバックアップと違い、同じ状態 | その状態 |
| 両側がバックアップと違い、状態が違う | 共有側の状態 |
| 最初の同期・バックアップが読めない・`userID` が違う | 共有側の値にそろえる（アカウント側にだけある葉も消す） |

`hasCompletedOnboarding`・`lastOnboardingVersion` は、アカウント側に無ければ共有側の値を入れる。`oauthAccount` などの
ほかのキーは変えない。書くときは、回ごとの乱数の名前の一時ファイル（`<固定したパス>.ndf-sync.<回>.tmp`）を 2 つ書き、
途中の記録（`.ndf-shared-base.next.json`）に側ごとの基準を書いてから、アカウント側 → 共有側 → バックアップの順に
`os.replace` する。途中で止まっても、次の同期は途中の記録と、それが指す一時ファイルの有無だけで側ごとの基準を決めて
やり直す（利用者が値を前へ戻した操作と、書けていない側とを取り違えない）。両側の実パスが同じなら書かずに `same_file`。

書き戻し（`settle`）は、セグメントの終わり（子が終わった後・次の子を起動する前）とラッパーの終わりに 1 回ずつ、終わった子の
アカウントが登録済みのときだけ打つ。排他の中で、届けの世代が今の `account.json` と同じで、解き直した実体の確かめと形式・
本人の確かめ（`check_config`）に通るときだけ `sync_config` を打つ。外れれば共有側へ反映しない（`reregistered`・
`not_account_local`・`identity_mismatch`・`legacy_config`・`account_unreadable`）。どの場合も最後に使用の届けを消す。
書けなければ理由を 1 行出し、次の起動の前の同期でそろえる。

### 使用の届けと関所

| 状態 | 入る時点 | 生きているとみなす条件 | 出る時点 |
| --- | --- | --- | --- |
| `starting` | `prepare` が ok を返す直前か `acquire`（`owner` はラッパーか `ndf plan`） | `owner` の pid が、`/proc/<pid>/stat` の開始時刻も同じで生きている | `attach` で子の pid を書けたら `running`。子の起動か `attach` に失敗したら、子を回収してから `release` で消す |
| `running` | `attach` の後 | `owner` か `child` のどちらかが、開始時刻も同じで生きている | 子の終わりの `settle`（書き戻しの後に消す）か、`acquire` で取った届けなら `release` |

関所の手順は、ラッパーの対話の子と `ndf plan` の `claude -p` で同じである。`owner` が `os.pipe()` を 2 組作り、子を
`gated(argv, r, sw)` の起動引数で起動する。子は 1 バイトを待ち、まだ claude ではない。`owner` が `attach` で子の pid を
届けに書けたら `b"1"` を送り、子は同じ pid のまま `os.execvp` で claude になる。`execvp` の失敗は `errno` が知らせの
パイプで返る（待ちの上限は `relay.gate-exec` の 10 秒）。`attach` が落ちれば合図を送らずに子を `SIGKILL` で回収して
`release` を打つ。`owner` が合図の前に死ねば、子は EOF を読んで claude を起動せずに終わる。そのため、`starting` の届けは
`owner` だけで生き死にを決めてよい。既定のログイン・`metered`・`key:<名前>` の子は関所を通さない。

登録（`ndf relay account add`）と削除（`account remove`）は、同じ名前の取得の排他（`accounts.fetch_lock`）を待ってから
登録の排他を取り、その中で生きている届けが 1 つでもあれば何も変えずに止める（`refused`。終了コード 1）。ラッパーだけが
終わって claude が残った届けは `child` が生きているので止めたままにする。`owner` も `child` も死んだ届けは残骸として消して
進む。登録は旧形式の `.config.json` を `backups/.config.json.<Unix 秒>`（同じ名前があれば `-1`・`-2`…を足す）へ移し、
新しい `generation` を書き、バックアップ・途中の記録・アカウント側の一時ファイルを消す（次の同期は最初の同期になる）。
成功の後、旧い世代の外した間と取得の記録を消す（`accounts.forget`）。削除は登録の
ディレクトリを `<保存先>/.removing/<名前>/<世代>` へ 1 回の `os.replace` で移して一覧から外してから中身を消す。

## データ・設定

| パス | 形 | 書き手 |
| --- | --- | --- |
| `<保存先>/<名前>/account.json`（0600） | `name`・`email`・`registered_at`・`needs_relogin`・`generation`・任意の `org_id`・`org_name`・`capacity`。`generation` を持たない旧い登録は `"legacy-"` ＋ `sha256(<登録簿の実パス>\0<名前>)` の先頭 32 字を世代とする（`legacy_generation`） | 登録のコマンド。`needs_relogin` だけは `prepare` と使用率の取得も立てる |
| `<保存先>/<名前>/.ndf-shared-base.json`・`.ndf-shared-base.next.json`（0600） | 同期のバックアップ（`projects`・`mcpServers` の値と `userID`）と途中の記録（B・`userID`・回・一時ファイルのパス・側ごとの基準） | `sync_config` |
| `<保存先>/<名前>/.ndf-oauth-pending.json`（0600） | 保留の更新（`generation`・POST に使った資格情報の組の SHA-256・`claudeAiOauth`） | 使用率の取得（[ndf-relay-account-switching.md](ndf-relay-account-switching.md) の「トークンの更新」） |
| `<保存先>/.leases/<名前>/<届けの ID>.json` | `{"generation", "state", "owner": {"pid", "start"}, "child": {"pid", "start"} か null}`。ID は `secrets.token_hex(8)` で起動ごとに別 | `prepare`・`acquire`・`attach`。消すのは `settle`・`release` |
| `<保存先>/.locks/<名前>.lock` | 登録の排他（filelock） | 登録のコマンドとラッパー |

アカウント固有の項目（symlink を張らない）: 前方一致の `.credentials.json`・`.claude.json`・`.oauth_refresh.lock`、名前の
一致の `.config.json`・`backups`・`policy-limits.json`・`policy-limits.json.stamp.json`・`remote-settings.json`・
`mcp-needs-auth-cache.json`・`.ndf-statusline-auth.json`・`account.json`・`usage.json`・`.ndf-shared-base.json`・
`.ndf-shared-base.next.json`・`.ndf-oauth-pending.json`。

`account.json` の `capacity` は、スロットの大きさの設定である。スロットの大きさは、1 つのスロット（5 時間のクォータ・週のクォータ）の使用率が 0% から 100% になるまでに使える量（USD 換算）で、利用者が `ndf relay account capacity` でアカウントごとに書く。一覧（`capacity_declared`）に出すだけで、替え先の選び方には使わない。

## 外部連携

| 相手 | 使い方 |
| --- | --- |
| Claude Code の `CLAUDE_CONFIG_DIR` | 複数のアカウントを設定ディレクトリで分ける形は公式の手順にある。トークンを `CLAUDE_CODE_OAUTH_TOKEN` で渡す形と違い、コネクタと Remote Control が使え、更新も claude 自身が行う |
| Claude Code の `.claude.json` の排他 | 本体と同じ `<実パス>.lock` のディレクトリを `os.mkdir` で取る（10 秒より古ければ残骸）。本体の形が変わると追随が要る |
| Claude Code の `--resume` | `projects` を共有側への symlink にした別の設定ディレクトリから、同じ会話を開ける（2026-11-21、Claude Code 2.1.330 で実測。記録は共有側の同じファイルへ書き足された） |

## 決定と理由

- **子へアカウントの設定ディレクトリを渡し、認証の変数を外す。** 選んだアカウントの認証で子を動かし、会話の記録を
  1 か所に保つためである。`CLAUDE_CONFIG_DIR` だけを替えると会話の記録がアカウントごとに分かれ、`--resume` が前の会話を
  見つけない。トークンを環境変数で渡すと、コネクタと Remote Control が使えず、更新を NDF が持つことになる
- **共有の項目は固定の一覧でなく、アカウント固有でないものすべてへ symlink を張る。** 選んだ項目だけを共有すると、後から
  増えた項目（skills など）がアカウントを替えると見えなくなる。コピーで共有すると会話の記録が 2 か所に分かれる
- **`.claude.json` は 3 者で葉ごとに突き合わせ、両側で変わったら共有側を採る。** 2 者の比べでは片側の変更が消える。
  `projects` の葉のプロジェクトの信頼と Tool・MCP の許可も共有側とほかのアカウントへ伝わる。同じ人の別のアカウントで
  設定と許可を続けるのが共有の目的である
- **使えないアカウントは起動の前に確かめて飛ばす。** 確かめずに起動すると、会話の記録を共有できないアカウントでは起動した
  時点で会話が分かれる。起動の前に NDF がトークンを更新して直すことはしない（claude が起動の後に自分で更新する）
- **認証の上書きは、変数を外すだけでなく実効の設定で判定する。** 設定ファイル・起動引数から入る源は、環境変数を外しても
  登録先のログインより上に来る。既定でない `ANTHROPIC_BASE_URL` と `ANTHROPIC_CUSTOM_HEADERS` は、会話の送り先と認証を
  替えうるので黙って外さずに飛ばす（利用者が意図した経路を外れて既定の宛先へ送らない）
- **使用の届けと関所で、子が動いている間の再登録・削除を止める。** 旧い子の保存が新しい登録を上書きしないためである。
  関所により、届けに pid を書く前の子は claude になれない

## セキュリティ

- 脅威の範囲は、うっかりの利用者（自分の設定や symlink の付け方を誤る）と、権限の無い人（保存先や設定ディレクトリに書けない、
  同じマシンの別の利用者）だけである（#2773 と同じ線）。同じ利用者の権限でファイルを差し替える操作と、管理の設定を書く
  管理者は範囲の外で、備えを足さない
- 共有側の `.credentials.json` を読み書きしない（I1）。認証ファイルはアカウントの設定ディレクトリの中の、確かめた実体の
  記述子からだけ開く（`O_NOFOLLOW`・`O_NONBLOCK`・`fstat` の照合）
- 理由の文・`log.jsonl`・例外の文に、トークン・キー・メールアドレス・ヘッダーの値・参照先のパスを入れない。同期の
  バックアップは `mcpServers` の値を含むので 0600 で置き、画面・`log.jsonl` には葉の数と理由の符号だけを出す
- 保存先のディレクトリは 0700、中のファイルは 0600 に保つ

## 運用

- 利用者の操作は `ndf relay account add <名前>`（登録・再登録）・`use`・`show`・`clear`・`list`・`remove` で、手順は手順書が正である
- 再登録が要るアカウントは、子を `/exit` で終えてから `ndf relay account add <名前>` で登録し直す。子が動いている間は
  登録・削除のコマンドが止まる
- 共有の `settings.json` に `apiKeyHelper` を置いている利用者、親の環境か設定ファイルに `ANTHROPIC_CUSTOM_HEADERS`・既定でない
  `ANTHROPIC_BASE_URL` を置いている利用者は、登録済みアカウントで起動できない（理由の 1 行で知らせ、次の候補へ移る）

### 既知の限界

- サーバーから配布される管理の設定は、そのアカウントで一度取るまで控えが無く、初回の起動では確かめられない
- Claude apps gateway のログインは保存の形が公開されておらず、認証の上書きの確かめで見ない
- 起動の後に登録済みアカウントの子の設定ファイルへ認証や接続先を足した変更は、次の起動の前の確かめまで検出しない（#4137）。
  キーの子だけは動いている間も検出する（[ndf-relay-metered-and-api-keys.md](ndf-relay-metered-and-api-keys.md)）
- symlink の共有をやめた製品（claude-switch）の理由（symlink 切れ・skill の消失）が 11 系の claude で起きるかは確かめていない。
  `log.jsonl` の `local_only` と飛ばした項目の行から見る

## テスト観点

テストは `plugins/ndf/tests/domains/relay/` の `test_relay_parts.py`・`test_relay_wrapper_launch.py`・`test_account_dir.py`・
`test_account_sync.py`・`test_account_prepare.py`・`test_account_registry.py` にある。

| 観点 | 落ちるべき壊し方 |
| --- | --- |
| 指定 `work2` の子の `CLAUDE_CONFIG_DIR` が `<保存先>/work2` の絶対パス、`NDF_CLAUDE_ACCOUNT` が `work2`、`NDF_SHARED_CONFIG_DIR` が親の `CLAUDE_CONFIG_DIR`（無ければ空文字）。入れ子では親の `NDF_SHARED_CONFIG_DIR` を引き継ぐ | 入れない・相対パスにする・入れ子で書き換える |
| 親に 13 変数があっても登録済みアカウントの子に無い。共有の `settings.json` の `apiKeyHelper`・`env` の `ANTHROPIC_API_KEY`・`ANTHROPIC_PROFILE`・`ANTHROPIC_CUSTOM_HEADERS`、親の環境の `ANTHROPIC_CUSTOM_HEADERS` でその候補を飛ばし、理由の文に値が無い | 表の行を外す・設定ファイルを見ない・ヘッダーを見ない |
| 指定も環境変数も無い子の環境が今までの `child_env` の出力と同じ。用意に落ちて既定のログインへ移った子は `NDF_CLAUDE_ACCOUNT` を持たない | 既定のログインにも `account-drop` を当てる・飛ばした名前を残す |
| 用意の後、共有側の項目すべてへの symlink があり、後から増えた項目にも張り、`.credentials.json` には張らない。`projects`・`settings.json` が無ければ作る | 固定の一覧で張る・認証ファイルを symlink にする |
| 2 つのアカウントの設定ディレクトリから辿った会話の記録の実パスが同じ | `projects` をアカウント側の実体にする |
| 用意の後、アカウント側の `projects`・`mcpServers` が共有側と同じで `oauthAccount` は前のまま。片側だけの変更・両側の変更・削除・途中で止めた後の復旧が表のとおり | 丸ごと置き換える・2 者で比べる・アカウント側を採る |
| 理由ごとに起動せず、画面と `log.jsonl` に 1 行ずつ出て次の候補で起動する。`RELOGIN_REASONS` の 3 つだけで `needs_relogin` が立つ | 理由を 1 つ外す・すべての理由で印を立てる |
| アカウント側にだけある実体は名前が `log.jsonl` に残り、実体は残る | 実体を消す・移す・上書きする |
| 共有側の `.credentials.json` の中身と更新時刻が変わらず、理由と記録に値が出ない | 共有側の認証ファイルを読み書きする |
| 子が動いている間（ラッパーを `SIGKILL` で終えた後も）の `account add`・`remove` が止まり、再登録の後に旧い世代の書き戻しが新しい登録へ入らない。関所の `attach` の失敗・`owner` の死・`execvp` の失敗で偽の claude が呼ばれず届けが消える | 届けを見ない・世代を比べない・関所を開けてから届けを書く |

## 関連リンク

- [ndf-relay-account-switching.md](ndf-relay-account-switching.md)（替えるかの判断・使用率・認証の失敗・再起動ループの検出）
- [ndf-relay-metered-and-api-keys.md](ndf-relay-metered-and-api-keys.md)（従量の接続と API キーの子）
- [ndf-relay-plan-claude-accounts.md](ndf-relay-plan-claude-accounts.md)（`ndf plan` の `claude -p`）
- [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md)（セグメントとカットポイント）
- 課題: #3604（アカウントの設定ディレクトリでの起動）・#3864（利用上限で停めない切り替え）。範囲外として起票したもの:
  #4137（起動の後の設定の変更の検出）
