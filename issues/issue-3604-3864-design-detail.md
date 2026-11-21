# #3604 #3864 の詳細設計: アカウントの設定ディレクトリでの起動と、利用上限で停めない切り替え（見回り・使い切り・最後の砦）

設計文書 [issue-3604-3864-design.md](./issue-3604-3864-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。別ファイルにした理由は、新設する関数が 2 つ以上あることと、既存の関数のシグネチャを変えることである。シグネチャを変えるのは `accounts.usages`・`account_decide.Usage`・`before_limit`・`after_limit` ほかである。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設。テストのファイルは各節の「単体テストの観点」が使う。単体テストの観点は、設計文書の「テスト設計」の行が縛る振る舞いを言い直さず、関数単位の境界値と異常系を持つ）:

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   ├── account_dir.py *     … 起動の前の用意（確かめ・symlink・.claude.json の同期・書き戻し）
│   ├── account_choice.py *  … 起動できる候補・従量の接続と API キーの候補・起動の前の実効値の確かめ・替え先の環境（ラッパーと ndf plan が共有）
│   ├── session_env.py       … account_env・metered_env・key_env・METERED_FIXED（新設）、child_env（decided を足す）
│   ├── account_registry.py  … shared_config_dir・mark_relogin・generation・provider_on・PROVIDER_ENV・METERED_CHECKED・recovery_keys・key_value（新設）、store_dir（中身を寄せる）、check_decl（検証を足す）・SECRET_ENV（広げる）
│   ├── account_metered.py   … 接続確認の提供元の判定を provider_on へ寄せる
│   ├── accounts.py          … usages・usage_path（変更）、usage_dir・write_usage・held・hold・unhold・see_limit・limits_seen・limit_until・forget（新設）
│   ├── account_decide.py    … Guards・group・order・guard・after_auth（新設）、Usage・before_limit・after_limit（変更）
│   ├── switch_decide.py     … SectionState.auth_pick・guard_pick・safe・spin・spin_seen・spin_held・must_end・end_ready・drift_pick、Spin・Streak・target_key・advance・hit・_spun・_revoked・GUARD_RESUME_PROMPT・AUTH_RESUME_PROMPT・SPIN_REASON（新設）、decide・_limit_or_auth・_restart_or_stop（変更）、SectionState.starts_today・max_starts（削除）
│   ├── observe.py           … tool_pending・turn_done（新設）
│   ├── wrapper.py           … 候補を試す起動・指定の読み直し・見回りの替え・認証の失敗・書き戻し・再起動ループの検出（1 日の起動の回数の記録を消す）
│   ├── hub_decide.py・hub.py・registry.py・cli.py … リレーハブの restart の判断と登録簿から 1 日の起動の回数を消し（registry.Starts を消す）、hub.py は spin_of（NDF_RELAY_SPIN を読む）を足し、cli.py はそれを使って NDF_RELAY_MAX_STARTS を読まない
│   └── cli_account.py       … _acc_list（呼び方だけ）、add・remove（使用の届けと forget）、use（unhold）、api-key-helper（新設）
├── src/ndf/adapters/
│   └── claude.py            … call_claude（認証の上書きの確かめ・上限での打ち直し）
├── src/ndf/domains/statusline/
│   └── statusline.sh        … 使用率の記録
├── contracts/
│   ├── domains/env.json     … child の列の account-set・account-drop、provider の列と 13 行、NDF_RELAY_SPIN の行（NDF_RELAY_MAX_STARTS の行を消す。docs/contracts/env.md はここから生成し直す）
│   ├── schemas/io/relay.result.schema.json … hub ls の starts を消す
│   ├── domains/ndf-files.json … relay-usage の行・relay-auth-held の行・relay-limit-seen の行
│   └── limits.json          … relay.five-hour-guard・relay.weekly-prefer・relay.weekly-guard・relay.spin・relay.auth-hold・relay.limit-unknown・relay.end-wait（新設）、relay.usage-guard・relay.max-starts（削除）
├── skills/development-workflow/references/
│   ├── relay.md             … 上限の表と止まる理由を再起動ループの検出に替える
│   └── relay-accounts.md    … 11 系の振る舞いだけに書き直す
└── tests/
    ├── domains/relay/ … test_account_dir.py *・test_relay_parts.py・test_relay_decide.py・test_relay_wrapper.py・test_relay_wrapper_question.py・test_account_registry.py・test_relay_hub.py・test_relay_cli.py
    ├── domains/statusline/test_statusline_usage.py *・adapters/test_claude_reply.py
    └── domains/test_ndf_settings.py・support/relay_children.py・progress/test_plan_lane_question.py（1 日の起動の上限の参照を外す）
docs/glossary/glossary.json・docs/glossary/ndf-relay.md（リポジトリの根から）
```

依存の向きは下の表の「依存する先」の列のとおりで、循環は無い。`wrapper` と `adapters/claude.py` からの依存は一方向である。設計文書は型の追加が少ないためクラス図を持たない。型は下の各節の「内部のデータと定数」に置く。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/account_dir.py` | 新設 | 登録済みアカウントの設定ディレクトリを起動できる形に用意し、終わりに書き戻す | `account_registry`（保存先・排他・登録の読み・提供元の判定）・`signals`（置き換えの書き込み）・`contracts.facts`（`account-drop` の名前）・`contracts.limits`（排他の待ちの秒。`claude-account-dir.lock`・`claude-accounts.lock`）だけ | モジュール・`Links`・`Prepared`・`Resolved`・`Local` | `is_local`・`link_shared`・`planned_links`・`config_file`・`shared_config_file`・`effective_env`・`auth_overrides`・`check_local`・`check_config`・`check_account`・`sync_config`・`ready`・`prepare`・`acquire`・`gated`・`attach`・`release`・`settle`（新設）。型 `Checked` |
| `src/ndf/domains/relay/account_choice.py` | 新設 | 起動できる候補・従量の接続と API キーの候補と、確定した替え先の子の環境を作る（ラッパーと `ndf plan` が共有） | `account_dir`・`session_env`・`accounts`・`account_decide`・`account_registry`。ラッパーを読まない | モジュール | `candidates`・`fallbacks`・`build`・`verify`・`target_env`・`retry_env`（新設） |
| `src/ndf/domains/relay/session_env.py` | 変更 | 登録済みアカウント・従量の接続・API キーの子の環境を足す | `contracts.facts`・`account_registry`。`account_dir` を読まず、用意の済んだ設定ディレクトリのパスを引数で受ける | モジュール | `account_env`・`metered_env`・`key_env`（新設）、`child_env`（変更） |
| `src/ndf/domains/relay/account_registry.py` | 変更 | 共有の設定ディレクトリの求め方を 1 か所にし、再登録が要る印を書き、復帰先に使える API キーを返す | 今と同じ | モジュール | `shared_config_dir`・`mark_relogin`・`generation`・`provider_on`・`METERED_CHECKED`・`recovery_keys`・`key_value`（新設）、`store_dir`・`check_decl`・`SECRET_ENV`（変更） |
| `src/ndf/domains/relay/account_metered.py` | 変更 | 接続確認の提供元の判定を `provider_on` にそろえる | `account_registry` | モジュール | 既存の判定の 1 行（変更） |
| `src/ndf/domains/relay/accounts.py` | 変更 | 使用率のキャッシュと外した間の記録の読み書き | `account_decide`・`signals`・`contracts.facts` | モジュール | `usage_dir`・`write_usage`・`held_path`・`held`・`hold`・`unhold`・`limits_path`・`limits_seen`・`see_limit`・`limit_until`・`forget`（新設）、`usage_path`・`usages`（変更）、`write_usages`（削除） |
| `src/ndf/domains/relay/account_decide.py` | 変更 | 候補の一覧と閾値を受けて、選ぶ順の組で替え先を決める | 無し（ほかの中継の部品を読まない。時計とファイルを読まない純粋な関数のまま） | モジュール・`Pick`・`Usage`・`Guards` | `group`・`order`・`guard`・`after_auth`（新設）、`Usage`・`before_limit`・`after_limit`（変更） |
| `src/ndf/domains/relay/switch_decide.py` | 変更 | 見回りの替え先・認証の失敗の替え先を手に変え、再起動ループの検出に当たれば次の区間を起動しない手を返す（1 日の起動の回数を入力に持たない） | `account_decide.Pick`（時計とファイルを読まない純粋な関数のまま。再起動ループの検出の `advance`・`hit` も置く） | `SectionState`・`Spin`・`Streak` | `decide`・`_limit_or_auth`・`_restart_or_stop`（変更）、`_guard`・`_spun`・`target_key`・`advance`・`hit`（新設） |
| `src/ndf/domains/relay/observe.py` | 変更 | 会話の記録の最後が結果を待つ Tool の呼び出しかを読む | `adapters.agents` | モジュール | `tool_pending`・`turn_done`（新設） |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 候補を試して起動し、見回りで替え、終わりに書き戻す | `account_choice`・`account_dir`・`session_env`・`accounts`・`account_decide`・`switch_decide`・`observe`・`account_registry` | `Wrapper`・`Section`・`Target`・`Choice`・`Inputs` | `_launch_target`・`_initial`・`_inputs`・`_candidates`・`_fallbacks`・`_pinned_or`・`_auth_pick`・`_drift`・`_guard_pick`・`_safe_point`・`_end_point`・`_on_spin`・`_on_revoked`・`_settle`・`_metered`・`_tell_at_start`（新設）、`_start`・`_state`・`_limit_pick`・`_on_switch`・`_on_resume`・`_loop`・`_act`・`_write_state`・`_end`（変更）、`_count_start`・`_starts_today`・`_on_stop`（削除） |
| `src/ndf/domains/relay/hub_decide.py`・`hub.py`・`registry.py`・`cli.py` | 変更 | リレーハブの `restart` の可否を名前つきのラッパーの数だけで決め、登録簿から起動の回数を消す。`cli.py` はラッパーへ `relay.spin` と `NDF_RELAY_SPIN` の値（`hub.spin_of`）を渡し、`NDF_RELAY_MAX_STARTS` を読まない。`hub.restart`・`spawn` は後継へ渡す環境で `spin_of` を先に確かめる | 今と同じ・`switch_decide.Spin` | `Lanes`・`Context` | `restart`・`spawn`・`_launch`・`hub_context`・ラッパーの設定の組み立て（変更）、`hub.spin_of`（新設）、`Lanes.starts_today`・`max_starts`・`Context.max_starts`・`today`・`registry.Starts`（削除） |
| `src/ndf/domains/relay/cli_account.py` | 変更 | 一覧で今の時刻と世代を渡し、登録・削除・指定で記録を失効させ、API キーの値を `apiKeyHelper` として出す | 今と同じ | モジュール | `_acc_list`（呼び方だけ）・登録・削除・指定（変更）、`_acc_key_helper`（新設） |
| `src/ndf/adapters/claude.py` | 変更 | 登録済みアカウントで動く `claude -p` の前に認証の上書きを確かめ、上限に当たったら替え先で 1 回打ち直す | `account_dir`・`account_choice`（`ndf plan` が中継の部品を読むのはこの 2 つだけ） | モジュール | `call_claude`（変更） |
| `src/ndf/domains/statusline/statusline.sh` | 変更 | 入力の `rate_limits` を、子の環境の `NDF_ACCOUNT_USAGE_FILE` のファイルへ書く | 無し（Python を読まない。ラッパーが解いた書き先を、子の環境の `NDF_ACCOUNT_USAGE_FILE` で受ける） | シェルのスクリプト | `record_usage`（新設） |
| `contracts/domains/env.json`・`docs/contracts/env.md` | 変更 | 子の環境の渡し方と `NDF_RELAY_SPIN` の行（`NDF_RELAY_MAX_STARTS` の行を消す。`env.md` は `ndf check --write-spec` で生成し直し、手で直さない） | — | — | — |
| `contracts/domains/ndf-files.json` | 変更 | 使用率のキャッシュと外した間の記録の置き場 | — | — | — |
| `contracts/limits.json` | 変更 | 閾値 A・B・C・再起動ループの検出の数と秒（`relay.max-starts` を消す）・外す秒数・上限の観測の既定の秒数 | — | — | — |
| `contracts/schemas/io/relay.result.schema.json` | 変更 | `hub ls` の項目の `starts` を消す | — | — | — |
| `skills/development-workflow/references/relay-accounts.md`・`relay.md` | 変更 | 11 系の振る舞いの手順書。`relay.md` は上限の表と止まる理由を再起動ループの検出に替える | — | — | — |
| `docs/glossary/glossary.json`・`docs/glossary/ndf-relay.md` | 変更 | 語の追加・意味の変更・廃止（設計の PR で反映済み。実装で語を直すときだけ触る） | — | — | — |

## 起動の前の用意（`account_dir.py`）

満たす決定: 決定 2・3、受け入れ条件 #3604 の 4〜10・12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `is_local` | `account_dir.py` | 新設 | `(item: str) -> bool` | アカウント固有の項目か（`LOCAL_PREFIXES` の前方一致か `LOCAL_NAMES` の一致） |
| `link_shared` | `account_dir.py` | 新設 | `(shared: Path, account: Path) -> Links` | 共有側に `projects`・`settings.json` が無ければ作り、直下のアカウント固有でない項目を symlink にする。`local_only`・`skipped`・`projects_shared` を返す |
| `config_file`・`shared_config_file` | `account_dir.py` | 新設 | `config_file` は `(config_dir: Path, named: bool) -> Path`、`shared_config_file` は `(environ: Mapping[str, str]) -> Path` | `config_file`: Claude Code が実際に読む設定ファイルの解決（移植元の `config_file` と同じ規則）。`<config_dir>/.config.json` があればそれ（旧形式が先）、無ければ `named`（設定ディレクトリが変数で指定されている）なら `<config_dir>/.claude.json`、偽なら `~/.claude.json`。本人の確かめ・同期・書き戻しはすべてこの関数の結果を使い、`.claude.json` を直に組み立てない。`shared_config_file`: 共有側の設定ファイル。`config_file(account_registry.shared_config_dir(environ), named)`。`named` は `NDF_SHARED_CONFIG_DIR` が空でないか、それが無く `CLAUDE_CONFIG_DIR` があるときに真。`NDF_SHARED_CONFIG_DIR` が空文字（親に `CLAUDE_CONFIG_DIR` が無かった）なら `named` は偽で、既定は `~/.claude.json`（`~/.claude/.config.json` があればそれ） |
| `planned_links` | `account_dir.py` | 新設 | `(shared: Path, account: Path) -> dict[str, Path]` | 書かずに、`link_shared` の後に `projects`・`settings.json` が解ける先を返す（アカウント側が無いか symlink なら `<共有>/<項目>`、実体ならアカウント側の実体）。`ready` と `check_account` が使う |
| `auth_overrides` | `account_dir.py` | 新設 | `(env: Mapping[str, str], config_dir: Path, cwd: Path, argv: Sequence[str], user_settings: Optional[Path] = None) -> list[str]` | 子が実際に受け取る環境（`env`）・起動引数（`argv`）・作業ディレクトリで `effective_env` を打ち、その実効値と出所だけで、登録先の OAuth より上に来る認証の源と、既定でない API の接続先の項目名（値は返さない）を返す（下の「認証の上書き」）。空なら登録先の OAuth で既定の接続先へ送る。`user_settings` は利用者の設定の解けた先（省けば `<config_dir>/settings.json`） |
| `effective_env` | `account_dir.py` | 新設 | `(env: Mapping[str, str], config_dir: Path, cwd: Path, argv: Sequence[str], user_settings: Optional[Path] = None) -> Resolved` | Claude Code が採用する実効値とその出所。変数ごとの実効値と出所、実効の `apiKeyHelper` の値と出所（別のフィールド）、解けない源の名前、読んだ源のファイルの印を返す（下の「実効値の作り方」）。`auth_overrides` と従量の接続の確かめと起動の後の変更の検出が同じ結果を使う |
| `check_local`・`check_config` | `account_dir.py` | 新設 | `check_local` は `(store: Path, account: Path, shared_file: Path) -> Local \| str`、`check_config` は `(local: Local, registered: account_registry.Account) -> Optional[str]` | `check_local`: 下の「実体の確かめ」だけを行う（読まない・書かない）。通れば確かめた実体（実際に使うパスと `(st_dev, st_ino)`）を `Local` で、外れれば理由の符号を返す。`check_account` と `settle` が使い、`sync_config` へはこの `Local` だけを渡す。`check_config`: `local` の設定ファイルの形式と本人の確かめ。`config_file(account, True)` が `.claude.json` でなければ `legacy_config`、JSON の object でなければ `account_unreadable`、`oauthAccount` のメールアドレスと組織が登録と違えば `identity_mismatch`。`check_account` と `settle` が同じ関数を使う |
| `check_account` | `account_dir.py` | 新設 | `(store: Path, account: Path, registered: account_registry.Account, shared: Path, shared_file: Path, env: Mapping[str, str], cwd: Path, argv: Sequence[str]) -> Checked \| str` | `check_local(store, account, shared_file)` を先に行い、通ったときだけ認証ファイルを読んで確かめ、`check_config` を打ち、最後に `effective_env(env, account, cwd, argv, planned_links(shared, account)["settings.json"])` を 1 回打って、その `Resolved` に「当たる条件」を当てる（`auth_overrides` と同じ判定を同じ `Resolved` で行う）。`shared_file` は親の環境から解いた共有側の設定ファイル（下の「共有側の設定ファイルの解き方」）。`env` は子が受け取る環境（`ready`・`prepare` が親の環境から `account-drop` の変数を外し、`account-set` の `CLAUDE_CONFIG_DIR` を入れたもの）で、共有側の解き方には使わない。通れば `Checked(local, resolved)`（`local` は `check_local` の `Local`、`resolved` は打った `effective_env` の結果）、使えなければ理由の符号を返す |
| `sync_config` | `account_dir.py` | 新設 | `(local: Local) -> Optional[str]` | `local` が固定したパスの `.claude.json` の共有する設定の部分を 3 者で突き合わせて両側へ書く。パスを解き直さない。書けなければ理由の符号 |
| `ready` | `account_dir.py` | 新設 | `(store: Path, name: str, environ: Mapping[str, str], cwd: Path, argv: Sequence[str]) -> Probe` | 排他を取らず、書かずに `check_account(…, shared_config_file(environ), …)` を打つ。続けて、`planned_links` の `projects` が共有側の `projects` か（無ければ `link_shared` が作るので真）の比べだけを行う（子を止める前の替え先の確かめに使う）。`projects` がまだ無い初回のアカウントを落とさない。返りの `Probe.reason` は、どれも通れば None、外れれば最初に外れた理由の符号。`Probe.stamps` は、成功でも失敗でも、その判定に使った源の印である。中身は、その名前の `account.json`・`.credentials.json`・設定ファイル（`config_file`）・アカウント側の `projects` と `settings.json` の `os.lstat` と解けた先の `os.stat` である。`check_account` が `effective_env` まで進んだときは、その `Resolved.stamps` も足す。認証ファイルで落ちたなど `effective_env` の前で落ちたときは、前者だけになる。呼び出し元は印を自分で取り直さない |
| `prepare` | `account_dir.py` | 新設 | `(store: Path, name: str, environ: Mapping[str, str], cwd: Path, argv: Sequence[str]) -> Prepared` | アカウントの排他の中で `check_account(…, shared_config_file(environ), …)` → `link_shared` → `projects_shared` の確かめ → `sync_config`（`check_account` の `Checked.local` を渡す）→ `account_registry.secure` → 使用の届けの書き込み（`starting`）を行う。`Prepared.resolved` には `Checked.resolved` を入れる |
| `settle` | `account_dir.py` | 新設 | `(store: Path, name: str, environ: Mapping[str, str], generation: str) -> Optional[str]` | アカウントの排他の中で、世代が今の `account.json` と同じで、`check_local(store, <名前の設定ディレクトリ>, shared_config_file(environ))` が `Local` を返し（排他を取った後に解き直した実体で確かめる）、その `Local` で `check_config` が None（起動時に許した `.claude.json` の形式で、登録の本人のまま）なら、その `Local` で `sync_config` を 1 回打つ。世代が違う・実体の確かめに外れる・形式か本人が違うときは共有側へ反映しない。どの場合も最後に使用の届けを消す。書けなければ理由の符号。引数に届けの ID（`lease: str`）を足す |
| `attach`・`release` | `account_dir.py` | 新設 | `attach` は `(store: Path, name: str, lease: str, child_pid: int) -> Optional[str]`、`release` は `(store: Path, name: str, lease: str) -> None` | `attach`: 登録の排他の中で、届けを `running` にして `child` を書く（下の「使用の届けの状態」）。子を関所で止めたまま、関所を開ける前に呼ぶ。書けなければ理由の符号（`lease_lost`・`lock_timeout`・`write_failed`）を返し、呼び出し元は関所を開けずに子を止めて回収する。`release`: 登録の排他の中で、同期をせずに届けを消す。子の起動に失敗したとき・`attach` に失敗して子を回収した後・`acquire` で取った届けの子が終わった後に呼ぶ |
| `acquire` | `account_dir.py` | 新設 | `(store: Path, name: str, config_dir: Path, generation: str) -> str` | 用意（`prepare`）をせずに、登録の排他の中で 2 つを照合し、一致したときだけ `generation` の `starting` の届けを書いて届けの ID を返す。1 つは `config_dir` の実パスが `<store>/<name>` の実パスと同じこと、もう 1 つは `generation` が今の `account.json` の世代と同じことである。`ndf plan` の通常の `claude -p`（親から継いだ登録済みアカウントの設定ディレクトリを使う）が呼ぶ。名前が登録に無い・パスが違う（`config_mismatch`）・世代が違う（`reregistered`）・排他の待ち切れ・書けないときは `AccountError`。親の子が終わった後に同じ名前が登録し直されても、旧い世代から始めた worker は新しい本人の認証で送らない |
| `gated` | `account_dir.py` | 新設 | `(argv: Sequence[str], fd: int, status_fd: int) -> list[str]` | 子の起動引数を関所で包む。返りは `[sys.executable, "-I", "-c", GATE_CODE, str(fd), str(status_fd), *argv]`。`GATE_CODE` は `fd` から 1 バイトを読み、EOF かほかの値なら claude を起動せずに終了コード 125 で終わる。`b"1"` なら、起動の知らせの書き口 `status_fd` に `FD_CLOEXEC` を立ててから、同じ pid のまま `os.execvp(argv[0], argv)` で claude になる。成功すれば書き口は閉じ、失敗すれば `errno` を 10 進の文字列で書いて終了コード 127 で終わる（下の「起動の関所」の 4）。関所は設定ディレクトリも環境の値も読まない |

共有側の設定ファイルの解き方: `ready`・`prepare`・`settle` はどれも、`shared_config_file(environ)` を 1 回だけ解く。`environ` はラッパーが受け取った親の環境で、`account_env` を重ねる前のものである。解いた結果は `check_account` と `check_local` へ引数で渡す。子の環境（`CLAUDE_CONFIG_DIR` をアカウントの設定ディレクトリへ替えたもの）では解かない。子の環境で解くとアカウント自身の `.claude.json` が返り、共有側の確かめがアカウント自身に当たるためである。入れ子のラッパー（親が登録済みアカウントの子）では、親の環境の `NDF_SHARED_CONFIG_DIR` が共有の設定ディレクトリを指す。そのため同じ式で共有側が解ける。

| 関数 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- |
| `link_shared` | アカウントの排他を持っている（`prepare` の中） | 共有側の直下のアカウント固有でない項目ごとに、アカウント側の同じ名前が「`<共有>/<項目>` を指す symlink」か「実体（`skipped` に名前がある）」のどちらかである。項目ごとに、同じ名前が無ければ symlink を作り `added` を 1 増やす。同じ名前の symlink が `<共有>/<項目>` を指せば何もせず、ほかを指せば付け替えて `added` を 1 増やす。同じ名前の実体は飛ばして `skipped` に名前を入れる。アカウント側の項目が固有でなく、symlink でなく、共有側に無ければ `local_only` に名前を入れる | 共有側の `projects`（0700）・`settings.json`（`{}`、0600）の作成。アカウント側の symlink の作成と付け替え（一時の名前 `<項目>.<pid>.link` から `os.replace`） |
| `check_account` | — | 返りが `Checked` なら、次がすべて成り立つ。実体の確かめに通っている。認証ファイルが読め、アクセストークンとリフレッシュトークンが空でない。`check_config` が None である。つまり、`config_file(account, True)` が `.claude.json` の JSON の object である。`oauthAccount` のメールアドレスと組織は `account.json` と同じである。`account.json` に組織が無ければメールアドレスだけ比べる | 読むだけ（排他を取らず、バックアップも消さない）。実体の確かめに落ちたファイルは開かない。読むファイルは `Local` の固定したパスから開き、開いた後に `os.fstat` の `(st_dev, st_ino)` が `Local` と同じことを確かめる（違えば `not_account_local`）。値は返りにも例外の文にも入れない |
| `sync_config` | アカウントの排他を持っている。`local` は同じ排他の中の `check_local` が返したもの（`prepare` では `check_account` の返りの `Checked.local`、`settle` では排他を取った後の `check_local` の返り） | 両側の `projects`（プロジェクトのパス → 項目の 2 層の葉）と `mcpServers`（名前の 1 層の葉）が同じ値である。同期のバックアップ（`.ndf-shared-base.json`、0600）は、その値とアカウント側の `userID` を持つ。`hasCompletedOnboarding`・`lastOnboardingVersion` はアカウント側に無ければ共有側の値が入る。ほかのキー（`oauthAccount` など）は変わらない | `local` の固定したパス（アカウント側の `.claude.json`、共有側の解決先の通常ファイル）だけを使い、名前を解き直さない。Claude Code 本体と同じ `<固定したパス>.lock` のディレクトリを、アカウント側・共有側の順に取る。両側の固定したパスが同じなら、書かずに `same_file` を返す（二重の排他を取らない）。本体の錠を 2 つとも取った後、読む前に、両側の固定したパスを `os.lstat` し直す。確かめるのは、通常のファイル（symlink でない）であること・`st_nlink` が 1 であること・`(st_dev, st_ino)` が `local` と同じことである。無いことを `local` が記録していれば、まだ無いことを確かめる。1 つでも違えば、錠を返して `not_account_local` を返す。本体の錠を待つ間に、別のレーンの Claude Code や利用者が置き換えたファイルを読み書きしないためである。読むときは、開いたファイルの `os.fstat` でもう一度同じ比べをする。書く手順は下の「書く手順と途中で止まった後」の通りである。置き換えは、固定したパスと同じディレクトリの一時ファイルからの `os.replace` で行う。`os.replace` はリンクを辿らないので、確かめの後に名前が symlink へ替えられても、リンクの先へは書かない。権限は置き換える前のファイルの権限を保つ |
| `prepare` | — | `Prepared.ok` が真なら、受け入れ条件 #3604 の 4・6 が成り立つ。偽なら symlink の作成の途中で止まっていてよい（次の `prepare` が付け替える） | `link_shared`・`sync_config` と、理由が `RELOGIN_REASONS` のとき `account_registry.mark_relogin` |

#### 実体の確かめ（`check_local`。`check_account` の先頭と `settle` の排他の中）

10 系の同期は実パスへ書く。そのため、symlink やハードリンクを辿った先が共有側や別のアカウントだと、共有側の認証を読み書きしたり、同じファイルに二重の排他を取ったりする。実パスと symlink の比べだけでは、ハードリンク（同じ inode を持つ別の名前）を見分けられない。そこで `os.lstat` の `st_nlink` と `(st_dev, st_ino)` も比べる。読む・書く前に次をすべて確かめ、1 つでも外れれば `not_account_local` で飛ばす（読み書きしない）。

| 対象 | 条件 |
| --- | --- |
| アカウントの設定ディレクトリ | symlink でない。実パスが `<保存先の実パス>/<名前>` である |
| 認証ファイル（`.credentials.json`） | 有れば symlink でない通常のファイルで、`st_nlink` が 1、実パスの親がアカウントの設定ディレクトリの実パスである |
| Claude が読む設定ファイル（`config_file(account, True)`） | 同上 |
| 同期のバックアップ（`.ndf-shared-base.json`）と途中の記録（`.ndf-shared-base.next.json`） | 同上（無ければよい） |
| 共有側の設定ファイル（`shared_config_file`） | 共有側では symlink を許す（`~/.claude.json` を dotfiles の symlink にしている利用者がいるため）。名前を `os.path.realpath` で 1 回解き、以後はその解決先だけを使う。解決先の実パスが保存先の下に無い（どのアカウントの設定ディレクトリも指さない）。有れば、`os.lstat` を解決先（リンク自体ではない）に当て、通常のファイル（symlink でない）で、`st_nlink` が 1 で、`(st_dev, st_ino)` が上の 4 つのどれとも違う |

通れば `check_local` は `Local`（「内部のデータと定数」）を返す。以後の読み書きはこの `Local` のパスだけを使い、名前を解き直さない。`st_nlink` が 1 であれば、同じ inode を指す名前はほかに無い。そのため、共有側や別のアカウントの `.credentials.json`・`.claude.json` と同じ実体であることはない。確かめは、`prepare` の `check_account` の先頭と、`settle` が排他を取った後の 2 か所で行う。さらに `sync_config` が本体の錠を取った後に、同じ対象の種類・リンク数・同一性を `Local` と比べ直す。登録の排他は、別のレーンの Claude Code や利用者による置き換えを止めないためである。子が動いている間に、利用者が設定ファイル・バックアップ・共有先を置き換えることがある。そのときも `settle` は、書き戻す前に置き換えた後の実体で確かめ直す。外れれば書き戻さず、使用の届けだけを消して `not_account_local` を返す。Claude Code 自身も `.claude.json` を置き換えで書く。そのため `prepare` と `settle` の間に inode が替わるのは普通のことで、`settle` は `prepare` の `Local` を使わずに解き直す。

#### 認証の上書き（`auth_overrides`・`effective_env`）

認証の優先順位（STD2）で `/login` の OAuth より上に来る源は、環境変数を外しても設定ファイル・起動引数・プロファイルから入る。判定は、Claude Code が採用する実効値とその出所だけで行う。源ごとに値があるかでは判定しない（上位の源の空文字が下位の値を打ち消すため）。`effective_env` が下の「実効値の作り方」で実効値を作り、`auth_overrides` は下の「当たる条件」を実効値に当てる。読む源は次のとおりである。

| 源 | 読むか |
| --- | --- |
| 子の環境（`env`） | 常に（`call_claude` は環境を変えずに起動するので、親から継いだ値もここに入る） |
| 管理の設定のファイル（`/etc/claude-code/managed-settings.json` と `/etc/claude-code/managed-settings.d/*.json`） | 常に（`--setting-sources` に依らない） |
| 管理の設定のサーバーからの配布の控え（`<config_dir>/remote-settings.json`） | 常に |
| 起動引数の `--settings <JSON かファイル>` | `argv` にあるとき。ファイルの相対パスは引数の `cwd`（子が実際に起動する作業ディレクトリ）から解く。呼び出し元のプロセスの作業ディレクトリでは解かない（`ndf plan` は `Popen(cwd=…)` で別のディレクトリを渡すため） |
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

当たる条件（どれか 1 つで当たる。項目名は `<変数か項目>（<出所>）`）:

| 当たる条件 | 返り |
| --- | --- |
| 実効の `env` で、`account-drop` の認証の値の変数（`ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN` ほか）が空でない | 項目名 |
| 実効の `env` で、`PROVIDER_ENV` の変数の `provider_on` が真 | 項目名 |
| 実効の `env` で、`PROVIDER_ENV` の変数の `provider_on` が None（解けない値。例: `CLAUDE_CODE_USE_VERTEX=maybe`）が 1 つでもある | `auth_unresolved`（項目名）。Claude Code がどう読むか分からないので、起動先として確定しない |
| 実効の `apiKeyHelper`（`Resolved.helper`）が空でない | 項目名（`apiKeyHelper（<Resolved.helper_origin>）`。値は出さない） |
| 実効の `env` の `ANTHROPIC_BASE_URL` が空でなく、既定の `https://api.anthropic.com`（末尾の `/` を除いて比べる）でない | 項目名（`ANTHROPIC_BASE_URL（<出所>）`）。登録先の OAuth が選ばれていても、要求と会話はその宛先へ送られるため |
| 実効の `env` の `ANTHROPIC_CUSTOM_HEADERS` が空でない | 項目名（`ANTHROPIC_CUSTOM_HEADERS（<出所>）`）。Claude Code はこの変数の行を要求のヘッダーとして足すので、`Authorization`・`x-api-key` などで認証を、ゲートウェイの振り分けのヘッダーで宛先を替えうる（STD7）。ヘッダーの名前で安全なものを選り分けず、空でなければ当てる。値（ヘッダーの名前も含む）は返り・理由の文・`log.jsonl` に出さない |
| 実効の `env` に `ANTHROPIC_PROFILE` があるか、`ANTHROPIC_FEDERATION_RULE_ID` と `ANTHROPIC_ORGANIZATION_ID` がそろう。または Anthropic の設定ディレクトリの有効なプロファイルの認証の形が `oidc_federation` | 項目名 |

既定でない API の接続先を許す条件: 登録済みアカウントの子では許さない。プロキシやゲートウェイを通す利用者は、従量の接続か既定のログインで動かす。従量の接続では宣言に `ANTHROPIC_BASE_URL` を入れ、下の「従量の実効値の照合」で宣言と照合する。親の環境の `ANTHROPIC_BASE_URL` も、子の環境として実効値に入るので当たる。`account-drop` で黙って外すと、利用者が意図した経路を外れて既定の宛先へ送る。そのため外さずに飛ばし、理由を出す。`HTTPS_PROXY` などの通信の経路の変数は宛先を変えないので見ない。打ち消しと選択の例は次のとおりである。

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

使う場所: 子（`_launch_target`）: 登録済みアカウントの候補で当たれば `auth_overridden`（解けない源は `auth_unresolved`）で飛ばし、次の候補へ進む。理由の 1 行には項目名（例: `apiKeyHelper（利用者の設定）`）だけを出す。`argv` はラッパーが子に渡す実際の起動引数。`ndf plan`（`call_claude`）: 環境の `NDF_CLAUDE_ACCOUNT` が登録済みアカウントの名前なら、起動の直前に認証の上書きを確かめる。打つのは `auth_overrides(env, Path(env["CLAUDE_CONFIG_DIR"]), cwd, argv)` である。渡すのは、最終的に使う `argv`・環境・作業ディレクトリである。`minimal` の起動は `--setting-sources ''` を渡すので、利用者・プロジェクトの設定は読まれず、管理の設定と `--settings` だけを見る。当たれば `claude -p` を起動せず、項目名と直し方（その設定から項目を外す）を入れた失敗で返す。worker の作業ディレクトリはラッパーと違いうるので、プロジェクトの設定はその作業ディレクトリで見る。Claude apps gateway のログインは保存の形が公開されておらず、ここでは見ない（「未確認のまま残ること」）

#### 起動の後の設定の変更

Claude Code は利用者・`--settings`・管理・プロジェクトの設定の `env` を、起動の後も実行中のセッションへ反映する。反映は、保存で実効の `env` が変わるたびに行われる（STD8）。起動の前の確かめだけでは、動いている間に共有の `settings.json` へ API キーや接続先を足した変更を扱えない。固定はしない。実行中も認証・接続先を固定できる渡し方が無いためである。`--settings` で認証の変数を空文字にしても、管理の設定はその上に来る。`apiKeyHelper` を `--settings` で打ち消せるかは文書に無い。`--setting-sources` から `user` を外せば利用者の設定を読まなくなるが、利用者のほかの設定（許可・hooks・プラグイン）まで失う。そのため変更を検出し、次の切れ目で替える。

| 場面 | 動作 |
| --- | --- |
| 見回りのたび（登録済みアカウント・従量の接続・API キーの子） | 起動のときの `Resolved.stamps` の源のファイルを `os.stat` だけで比べる（ファイルは開かない。CLI の起動・API の呼び出しは増えない）。API キーの子は、加えて `api-keys.json` の印を比べる |
| 印が変わった | その回だけ `effective_env` を同じ `env`・`cwd`・`argv` で打ち直し、登録済みアカウントなら `auth_overrides`、従量の接続なら「従量の実効値の照合」、API キーなら `account_choice.verify` の照合を当てて、印を更新する。API キーの子は、登録簿にそのキーが無いか使い方が `off` になったときも当たったとする |
| 打ち直しで当たり、今が API キーの子 | 子を残さない（`stalled` にしない）。登録簿のキーと会話を、許していない宛先へ送り続けないためである。当たった時点から子の端末へ入力を送らず、上限・認証の失敗の判断より先に、止める条件（ラッパーの節の `_end_point`）が揃った時点で子を止める。宛先・ヘッダー・資格情報が足されたときは、止める条件を待たずにその見回りで子を止める（道具の実行の途中でも、その結果を使う次の要求は利用者の入力なしに出て、変更後の宛先へキーと会話を送るため）。替え先があればそこで `--resume`、替え先が無ければ後継を起動せずに `Ended("account_unavailable", 理由)` で会話を送らずに終える（`switch_decide` の `_revoked`、ラッパーの節の「API キーの子の停止」） |
| 打ち直しで当たった | 画面に 1 行（`ndf-relay: 動いている間に <項目名> が足された。次の区切りでアカウントを替える`）と `log.jsonl` に `{"kind": "auth_drift", "account", "items"}` を 1 回出す。`Section.drift_at` を立て、`_state` は `auth_pick` に `after_auth` の結果を入れる（`accounts.hold` はしない。アカウントの不具合ではないため）。替え先の確定と子の止め方は認証の失敗と同じ（下の「子を止める前の替え先の確定」の `auth` の行）で、今のアカウントも同じ確かめで落ちる。替え先が無ければ、登録済みアカウントと従量の接続の子は子を残して `stalled` の 1 行を出す。API キーの子は上の行のとおり、替え先の有無によらず `_revoked` の経路で止める。あわせて、その世代の使用率のファイル（`accounts.usage_path`）を消し、次の判断はそのアカウントを「使用率の分からない候補」として行う（変更の保存から検出までに別の本人の使用率が書かれうるため。API キーとクラウド事業者の接続は `rate_limits` が無く書かれない（STD5）。検出までの間に変更後の資格情報と宛先で送られる要求は、設計文書の「残る穴」と「人へ残す判断」が持つ） |
| 打ち直しで当たらない | 何もしない |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check_account` | `no_credentials` | 認証ファイルが無い・読めない・JSON でない・アクセストークンが空 | 認証ファイルの読み | 呼び出し元へ返す | 呼び出し元のラッパーが 1 行 | 何も書かない。登録し直せば次の起動で通る |
| `check_account` | `no_refresh` | リフレッシュトークンが空 | 同上 | 呼び出し元へ返す（`prepare` が印を立てる） | 同上と再登録の知らせ | `ndf relay account add <名前>` の後に通る |
| `check_account` | `account_unreadable` | アカウント側の `.claude.json` が無い・JSON の object でない | `check_config` | 呼び出し元へ返す | 同上 | 何も書かない（`check_account` は読むだけで、`prepare` もバックアップを消さない）。登録し直すと、登録のコマンドが登録の排他の中でバックアップと途中の記録を消すので、次の同期は最初の同期になる |
| `check_account` | `identity_mismatch` | `oauthAccount` が登録と別の本人 | `check_config` | 呼び出し元へ返す（`prepare` が印を立てる） | 同上と再登録の知らせ | 印のほかは何も書かない。登録し直した後は同上 |
| `settle` | `identity_mismatch`・`legacy_config`・`account_unreadable`・`not_account_local` | 子が動いている間に、アカウント側の設定ファイルが別の本人の `.claude.json` に替わった・旧形式の `.config.json` が置かれた・壊れた。設定ファイル・バックアップ・共有先が置き換えられ、排他の中の確かめに外れた（`not_account_local`） | 排他の中の `check_config`・`check_local` | 共有側へ反映せず、使用の届けを消して返す | 画面に 1 行と `log.jsonl` に 1 行 | 何も書かない。次の `prepare` が確かめ直し、同じ理由なら起動を飛ばし、`RELOGIN_REASONS` なら印を立てる |
| `check_account` | `not_account_local` | 実体の確かめに外れた（symlink・ハードリンク・共有側や別のアカウントを指す・共有側の設定と同じ実体） | 実パス・`st_nlink`・`(st_dev, st_ino)` の比べ | 読み書きの前に返す | 呼び出し元のラッパーが 1 行（項目の名前だけ。参照先のパスは出さない） | 何も書かない。利用者が symlink・ハードリンクを独立した実体に戻せば通る |
| `check_account` | `auth_unresolved`・`auth_overridden` | 認証の上書きを確かめる源が読めない・解けない・実効の提供元の変数の値が解けない（`auth_unresolved`）。`auth_overrides` が空でない（既定でない `ANTHROPIC_BASE_URL` を含む。`auth_overridden`） | `effective_env`・`provider_on`・`auth_overrides` | 呼び出し元へ返す | 同上（源の名前、または項目名と出所だけ） | その設定を直すか項目を外せば次の起動で通る |
| `check_account` | `legacy_config` | アカウント側に旧形式の `.config.json` がある | `config_file` | 呼び出し元へ返す（`prepare` が印を立てる） | 同上と再登録の知らせ | `ndf relay account add <名前>` が旧ファイルを `backups/.config.json.<UTC の時刻>` へ移してから登録するので、その後に通る |
| `sync_config` | `same_file` | 両側の設定ファイルの実パスが同じ | 実パスの比べ | 書かずに返す（`prepare` は起動を止めない。本人の確かめは通っている） | `log.jsonl` に 1 行 | — |
| `sync_config` | `not_account_local` | 本体の錠を待つ間に、固定したパスの種類・リンク数・`(st_dev, st_ino)` が `Local` と変わった | 錠を取った後の `os.lstat` と、開いた後の `os.fstat` | 錠を返し、読み書きせずに返す（`prepare` は起動を飛ばす。`settle` は書き戻さず使用の届けを消す） | 呼び出し元のラッパーが 1 行（項目の名前だけ） | 何も書かない。次の `prepare` が解き直して確かめる |
| `settle` | `reregistered` | 起動したときの登録の世代と今の `account.json` の世代が違う | 世代の比べ | 書き戻さずに返す | `log.jsonl` に 1 行 | 新しい登録の次の起動の前の同期でそろう |
| `link_shared` | `OSError` | 共有側の作成・symlink の作成と付け替えの失敗 | `os.mkdir`・`os.symlink`・`os.replace` | 呼び出し元へ返す（`prepare` が `io_error` にする） | 呼び出し元のラッパーが 1 行 | 一時の名前の symlink を消す。次の `prepare` で作り直す |
| `prepare` | `projects_not_shared` | アカウント側の `projects` が実体で、共有側の `projects` を指さない | `link_shared` の後の実パスの比べ | 呼び出し元へ返す | 同上 | 実体を消さない（I2）。利用者が中身を共有側へ移して消せば通る |
| `prepare` | `lock_timeout`・`io_error` | アカウントの排他を `claude-accounts.lock` の秒で取れない・symlink の作成・共有側の作成・権限の補正の `OSError` | `account_registry.locked`・`link_shared`・`secure` | 呼び出し元へ返す | 同上 | 次の候補へ。次のセグメントで再び試す |
| `sync_config` | `shared_unreadable`・`lock_busy` | 共有側の `.claude.json` が JSON の object でない・本体の排他を `claude-account-dir.lock` の秒で取れない | 共有側の読み・`_claude_lock` | 同期せずに返す（`prepare` は起動を止めない） | `log.jsonl` に 1 行 | 次の同期でそろえる |
| `sync_config` | `write_failed` | 一時ファイルの書き込み・置き換えの `OSError` | `_write_json` | 同上 | 同上 | 一時ファイルを消す。次の同期でそろえる |
| `settle` | 上の 3 つ・`lock_timeout` | 同上 | 同上 | 呼び出し元へ返す | 画面に 1 行と `log.jsonl` に 1 行（E4 の失敗） | 次の起動の前の同期でそろえる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `sync_config` | 途中の記録があり、その `userID` がアカウント側と同じで、最初の同期のものか `userID` がバックアップと同じ | 「書く手順と途中で止まった後」の規則で側ごとに基準を選んでから、下の葉の規則で突き合わせる。読めない・`userID` が違う途中の記録は、一時ファイルと一緒に無いものとして消す |
| `sync_config` | 上の行に当たらず、バックアップが無い・読めない・`userID` が違う | 最初の同期。両側を共有側の値にそろえる（アカウント側にだけある葉も消す） |
| `sync_config` | 上の行に当たらず、片側に `projects`・`mcpServers` のキーそのものが無い | そのキーを空の object として読み、下の葉の規則で突き合わせる（キーの欠落は「その下の葉をすべて消した」と同じ） |
| `sync_config` | 葉の値が、共有側・アカウント側のどちらもバックアップと同じ | そのまま |
| `sync_config` | 片側だけがバックアップと違う（値を変えた・葉を消した・葉を足した） | 違う側の状態（消したなら両側から消す） |
| `sync_config` | 両側がバックアップと違い、両側が同じ状態（同じ値・どちらも消した） | その状態 |
| `sync_config` | 両側がバックアップと違い、状態が違う（片側が消し、もう片側が変えたときを含む） | 共有側の状態（共有側が消していれば消す、変えていればその値） |

葉の「状態」は値か「無い」のどちらかで、比べは JSON の値の等しさで行う。たとえば、バックアップに `mcpServers.a` だけがあるとする。アカウント側で `a` を消して `mcpServers` が `{}` かキーごと無いとき、共有側が `a` を変えていなければ両側から `a` が消える。共有側が同時に `a` を変えていれば共有側の `a` が両側に残る。最初の同期の判断は葉の規則より先に行い、アカウント側のキーの欠落を最初の同期の理由にしない。繰り返しの終わる条件: `link_shared` は共有側とアカウント側の直下を 1 回ずつ数え上げる。再帰しない

#### 書く手順と途中で止まった後

値の比べで書き込みの進み具合を推し量ると、同期の後に利用者が値を前の A へ戻した操作と、書けていない側とを区別できない。そこで、書き込みの進み具合は一時ファイルが残っているかで記録し、値の比べは利用者の変更の判定にだけ使う。書く手順（1 回の書き込みを「回」と呼び、回ごとに乱数の識別子 `<回>`（`secrets.token_hex(8)`）を作る）:

1. 両側へ書く値 B を作り、2 つの一時ファイルに書いて `fsync` する。一時ファイルは、アカウント側の `.claude.json.ndf-sync.<回>.tmp` と、共有側の解決先の隣の `<名前>.ndf-sync.<回>.tmp` である。回ごとに名前が違うので、前の回の一時ファイルを上書きしない。`.claude.json` の前方一致なので `link_shared` は張らない
2. 途中の記録（`.ndf-shared-base.next.json`、0600）を置き換えで書く。中身は B・`userID`・`<回>`・2 つの一時ファイルのパス・側ごとの基準（`base.account`・`base.shared`。それぞれ値の object か `"first"`（最初の同期をやり直す）か `"B"`（B と同じ））である。通常の回では両側の基準は前のバックアップ A（無ければ `"first"`）
3. アカウント側の一時ファイルを固定したパスへ `os.replace` する
4. 共有側の一時ファイルを固定したパスへ `os.replace` する
5. 途中の記録をバックアップ（`.ndf-shared-base.json`）へ `os.replace` する
6. 途中の記録が指さない `*.ndf-sync.*.tmp`（前の回の残り）を消す

`os.replace` は一時ファイルの名前を消す。そのため途中の記録があるときは、値に依らず次のように決まる。途中の記録が指すある側の一時ファイルが無ければ、その側は B に置き換え済みである。あれば、まだ置き換えていない。途中の記録が指さない一時ファイルは、どの判定にも使わずに消してよい（1 の途中で止まった回の残りか、6 の前に止まった前の回の残り）。途中の記録が無ければ、残った一時ファイルを消して前の同期のまま扱う。途中の記録があるときの次の同期（復旧）: 側ごとの基準を下の表で決め、上の葉の規則を側ごとの基準で当てて新しい値 B' を作る。続けて、上の 1〜6 を新しい `<回>` で行う。2 で書く途中の記録の `base.account`・`base.shared` には、表で決めた基準そのものを入れる（一時ファイルの有無から決め直さない）。前の回の一時ファイルは、新しい途中の記録が置き換えで書かれた後の 6 で初めて消す。そのため、復旧のどの位置で再び止まっても、そのとき残っている途中の記録と、それが指す一時ファイルの有無だけで同じ表が当たる。新しい途中の記録を書く前に止まれば前の回の記録と一時ファイルがそのまま残り、書いた後に止まれば新しい回の基準が記録に入っている。同期を終えた時点で途中の記録と一時ファイルは無く、バックアップが両側と同じ値を持つ。

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

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `LOCAL_PREFIXES`・`LOCAL_NAMES` | 定数 | `account_dir.py` | 順に `(".credentials.json", ".claude.json", ".oauth_refresh.lock")`（10 系と同じ。名前は `account_registry` の `CRED_FILE`・`CONFIG_FILE` から引く）・`(".config.json", "backups", "policy-limits.json", "policy-limits.json.stamp.json", "remote-settings.json", "mcp-needs-auth-cache.json", ".ndf-statusline-auth.json", "account.json", "usage.json", ".ndf-shared-base.json", ".ndf-shared-base.next.json")`（10 系に途中の記録を足す。`account.json`・`usage.json`・`.ndf-shared-base.json`・`.ndf-shared-base.next.json` は `account_registry` の定数から引く） |
| `SHARED_KEYS`・`ONBOARDING_KEYS` | 定数 | `account_dir.py` | `("projects", "mcpServers")`・`("hasCompletedOnboarding", "lastOnboardingVersion")` |
| `LOCK_STALE`・`RELOGIN_REASONS` | 定数 | `account_dir.py` | 順に 10 秒（本体と同じく、これより古い `<実パス>.lock` は残骸として除く）・`("no_refresh", "identity_mismatch", "legacy_config")` |
| 登録の世代 | 設定値 | `account.json` の `generation` | 登録・再登録のたびに新しい乱数の 16 進 32 字（`secrets.token_hex(16)`）。無い旧い記録は空文字の世代として扱う |
| 使用の届け | 設定値 | `<保存先>/.leases/<名前>/<届けの ID>.json` | `{"generation", "state", "owner", "child"}`。`state` は `starting`（用意の済んだ後・子の起動の前）か `running`（子の起動の後）。`owner` と `child` はそれぞれ `{"pid", "start"}`（`start` は `/proc/<pid>/stat` の開始時刻。pid の使い回しと区別する）で、`child` は `starting` の間 null。ID は `secrets.token_hex(8)` で、`Prepared.lease` か `acquire` の返りが持ち、1 つのラッパーが同じ名前で順に起動しても起動ごとに別である。下の「使用の届けの状態」 |
| `REASONS` | 定数 | `account_dir.py` | 理由の符号 → 画面の 1 行の文（値を含まない。例: `no_refresh` → `リフレッシュトークンが空。ndf relay account add {name} で登録し直す`） |
| `Links`・`Checked` | 型 | `account_dir.py` | `Links` は `added: int`・`skipped: list[str]`・`local_only: list[str]`・`projects_shared: bool`。`Checked` は `local: Local`・`resolved: Resolved`（`check_account` の成功の返り。`auth_overrides` の判定に使った `effective_env` の結果） |
| `Resolved` | 型 | `account_dir.py` | `env: dict[str, str]`・`origin: dict[str, str]`・`helper: Optional[str]`・`helper_origin: Optional[str]`・`unresolved: list[str]`・`stamps: dict[str, Optional[tuple]]`。`env` は実効値、`origin` は変数ごとの出所。`helper` は実効の `apiKeyHelper` の値（シェルで実行する文字列。無ければ None）で、照合にだけ使い、画面・`log.jsonl`・理由の文・例外の文に出さない（利用者の helper はキーを直に含みうる）。`helper_origin` はその出所（`--settings`・`管理の設定（…）`・`利用者の設定` など。`helper` が None なら None）で、項目名に添えるのはこちらだけ。`unresolved` は解けない源の名前、`stamps` は読んだ源のファイルごとの `(st_mtime_ns, st_ino, st_size)`（無ければ None）。型は順に `dict[str, str]`・`dict[str, str]`・`Optional[str]`・`Optional[str]`・`list[str]`・`dict[str, Optional[tuple]]` |
| `Local` | 型 | `account_dir.py` | `account: Path`・`config: Path`・`shared: Path`・`ids: dict[str, Optional[tuple[int, int]]]`。`account` はアカウントの設定ディレクトリの実パス（`<保存先の実パス>/<名前>`）、`config` はアカウント側の Claude が読む設定ファイル（`config_file(account, True)`）の実パス（無ければ作る先のパス）、`shared` は共有側の解決先、`ids` は `check_local` が確かめた項目ごとの `(st_dev, st_ino)`（無ければ None）。型は順に `Path`・`Path`・`Path`・`dict[str, Optional[tuple[int, int]]]` |
| `DEFAULT_API_URL`・同期の一時ファイル・管理の設定の置き場 | 定数 | `account_dir.py` | 順に `"https://api.anthropic.com"`（登録済みアカウントの子に許す唯一の `ANTHROPIC_BASE_URL`）・`SYNC_TMP_FORMAT = "{path}.ndf-sync.{round}.tmp"`（`<固定したパス>.ndf-sync.<回>.tmp`。`<回>` は書き込みの回ごとの乱数）・`MANAGED_FILES = (Path("/etc/claude-code/managed-settings.json"),)`・`MANAGED_DIR = Path("/etc/claude-code/managed-settings.d")`・`REMOTE_SETTINGS = "remote-settings.json"`（`LOCAL_NAMES` の名前から引く）。テストは引数で差し替える |
| `Prepared` | 型 | `account_dir.py` | `ok: bool`・`reason: Optional[str]`・`config_dir: Path`・`links: Links`・`sync: Optional[str]`・`generation: str`（用意したときの登録の世代。`settle` へ渡す）・`resolved: Optional[Resolved]`（`check_account` が返した `Checked.resolved`。ラッパーが `Section.resolved` に控え、起動の後の変更の検出に使う）・`lease: Optional[str]`（書いた使用の届けの ID。`attach`・`release`・`settle` へ渡す） |
| `Probe` | 型 | `account_dir.py` | `name: str`・`reason: Optional[str]`（None は起動できる、文字列は値を含まない理由の符号）・`stamps: dict[str, Optional[tuple]]`（その判定に使った源のファイルごとの `(st_mtime_ns, st_ino, st_size)`。無ければ None）。`ready` と `account_choice.fallbacks` の返りで、成功でも失敗でも判定と印を一緒に持つ。ラッパーの `LaunchCache` はこの 2 つをそのまま控える |
| 排他と関所の待ち | 設定値 | `contracts/limits.json` の `claude-account-dir.lock`・`claude-accounts.lock`・`relay.gate-exec` | 本体の排他 2 秒と登録の排他 30 秒（どちらも既にある）。関所を開けてから `execvp` の成否の知らせを待つ上限 10 秒（新設） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じアカウントを使う 2 つのレーンの `prepare`・`settle` | アカウントの設定ディレクトリ | `account_registry.locked(store, name)`（filelock） | `link_shared` は何度打っても同じ形になる。中断した付け替えは一時の名前を次に消して作り直す |
| `sync_config` と動いている claude | 両側の `.claude.json` | 本体と同じ `<実パス>.lock` のディレクトリ（`os.mkdir` で取る。10 秒より古ければ消して取り直す） | 置き換えで書くので、途中で止まっても前の中身が残る |
| 登録のコマンド（再登録・削除）と `prepare`、その名前で動いている子 | `account.json`・認証ファイル・アカウントの設定ディレクトリの全体 | 同じ `account_registry.locked`。使用の届け（上の「使用の届けの状態」）。`account add`・`account remove` は登録の排他の中で、生きている届けが 1 つでもあれば何も変えずに止める（`アカウント <名前> を動いているレーンが使っている。その子を /exit で終えてから登録し直す`、終了コード 1）。`prepare`・`acquire`・`attach`・`release`・`settle` は同じ排他の中で届けを書き換えるので、確かめと書き込みの間に割り込まれない | 再登録はバックアップ・途中の記録・アカウント側の一時ファイルを消すので、次の同期は最初の同期になる（バックアップを消すのは登録のコマンドだけ）。子が動いている間は同じパスを別の登録に替えないので、旧い子の保存が新しい登録を上書きしない。`settle` は届けの世代と `account.json` の世代を比べ、違えば書き戻さない（`reregistered`）。届けの残骸（`owner` も `child` も死んでいる）は次の登録のコマンドが消して進む |

#### 使用の届けの状態

届けは、その名前の設定ディレクトリを使うプロセスが生きている間、登録・削除のコマンドを止めるためにある。届けを書くのは `prepare` か `acquire`、子の pid を足すのは `attach`、消すのは `settle` か `release` である（`account_dir.py` に新設。どれも登録の排他の中で書く）。関所は Python の起動を 1 回足す（数十ミリ秒）。CLI の起動（claude）と API の呼び出しは増えない。登録済みアカウントの設定ディレクトリを使わない起動先（既定のログイン・`metered`・`key:<名前>`）では関所を通さない。

| 状態 | 入る時点 | 生きているとみなす条件 | 出る時点 |
| --- | --- | --- | --- |
| `starting` | `prepare` が ok を返す直前か `acquire`（`owner` は呼んだプロセス＝ラッパーか `ndf plan`） | `owner` の pid が生きていて開始時刻が同じ。この状態の間、設定ディレクトリを使うのは `owner` だけである（子は下の関所で止まっていて claude になっていない） | 関所で止めた子の pid を `attach` で書けたら `running` へ。子の起動か `attach` に失敗したら、子を回収してから `release` で消す（同期は打たない。claude は起動していない） |
| `running` | `attach` の後 | `owner` か `child` のどちらかの pid が、開始時刻も同じで生きている | 子の終わりの `settle`（書き戻しの後に消す）か、`acquire` で取った届けなら `release`。`owner` が先に死んでも、`child` が生きている間は消さない |

起動の関所（ラッパーの対話の子と `ndf plan` の `claude -p` で同じ手順）: 子が claude として設定ディレクトリを使い始める前に、その pid を届けに確定する。

1. `owner` が `os.pipe()` を 2 組作る。開ける合図の読み口 `r` と書き口 `w`、起動の知らせの読み口 `sr` と書き口 `sw` である
2. 子を `account_dir.gated(argv, r, sw)` の起動引数で、`r` と `sw` だけを渡して起動する（ラッパーは `PtyProcess.spawn(…, pass_fds=(r, sw))`、`call_claude` は `Popen(…, pass_fds=(r, sw))`）。`owner` は起動の直後に自分の `r` と `sw` を閉じる。子は関所で 1 バイトを待ち、まだ claude ではない
3. `owner` が `attach(store, name, lease, 子の pid)` を打つ。`child.start` は関所の開始時刻で、`execvp` の後も pid と開始時刻は変わらない
4. `attach` が None なら `w` へ `b"1"` を書いて閉じ、`sr` を EOF まで読む（待ちの上限は `contracts/limits.json` の `relay.gate-exec`）。何も読まずに EOF なら `execvp` が通り、子は同じ pid のまま claude になった。`errno` の文字列を読んだら起動の失敗で、`waitpid` で子を回収してから `release` を打つ（ラッパーは次の候補へ、`call_claude` は `Failure.LAUNCH` で返す。理由の 1 行は `launch_failed` と `errno` の名前だけ）。待ち切れたら 5 と同じく扱う
5. `attach` が理由を返したら、`w` に書かずに閉じ、子へ `SIGKILL` を送って `waitpid` で回収してから `release` を打つ。子の起動の失敗として扱う（ラッパーは次の候補へ、`call_claude` は `Failure.LAUNCH` で返す）。理由の 1 行には符号だけを出す
6. `owner` が 3〜4 の間に死ねば `w` が閉じ、関所は EOF を読んで claude を起動せずに終わる。届けは `starting` か `running` のまま `owner` も `child` も死ぬので残骸になる

- 残骸: 上の条件を満たさない届けだけを残骸とし、登録・削除のコマンドと次の `prepare` が消して進む。ラッパーだけが終わって claude が残った届けは `child` が生きているので残骸にせず、登録・削除は止まったままになる（文は「アカウント <名前> を動いているプロセス（pid <child>）が使っている。終えてから登録し直す」）。`starting` の届けを `owner` だけで判定してよいのは、上の関所で、`attach` の前の子が claude になれないためである
- `ndf plan` の通常の `claude -p`: 環境の `NDF_CLAUDE_ACCOUNT` が登録済みアカウントの名前なら、`call_claude` は認証の上書きの確かめに通った後、`acquire(store, name, Path(env["CLAUDE_CONFIG_DIR"]), 継いだ世代)` で worker 自身の届けを取り、関所で起動して `attach` する。`claude -p` の終わりを確かめた後（`communicate` が返った後、タイムアウトなら `process_group.stop_group` で組を止めて回収した後）に `release` を打つ。同期は打たない（同じ設定ディレクトリを使う親の子の `settle` と、次の `prepare` が同期する）。継いだ世代は、親が渡した `NDF_ACCOUNT_USAGE_FILE` のファイル名の部分（`usage/<名前>/<世代>.json` の `<世代>`。`_` は空の世代）で、そのパスが `accounts.usage_path(状態の保存先, 名前, 世代)` と同じときだけ使う。変数が無い・形が違うときは `acquire` を打たずに `Failure.LAUNCH` で返す。`acquire` が `AccountError` なら `claude -p` を起動せずに `Failure.LAUNCH` で返す。こうして、親の対話の子が先に終わって自分の届けを消しても、worker が動いている間は登録・削除が止まる

`.claude.json` の同期が伝えるもの: `projects` の葉には、プロジェクトの信頼（`hasTrustDialogAccepted`）・許可した Tool（`allowedTools`）・`.mcp.json` のサーバーの許可（`enabledMcpjsonServers`・`disabledMcpjsonServers`）が含まれる。`mcpServers` の値には、認証のヘッダーや環境変数が入りうる。同期はこれらを葉ごとにそのまま両側へ伝える。同じ人の別のアカウントで設定と許可を続けるのが共有の目的で、10 系も同じである。そのため、あるアカウントの子で与えた信頼と許可は、共有側と、次に用意するほかのアカウントの子へ伝わる。この伝播を許すことを「人へ残す判断」に置く。同期のバックアップは、アカウントの設定ディレクトリ（0700）の中に 0600 で置く。`mcpServers` の値を含むので、画面・`log.jsonl`・結果へ出さない（出すのは葉の数と理由の符号だけ）。削除のコマンド（`account remove`）はバックアップも一緒に消す。

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
| `effective_env`・`auth_overrides` | 上書きが無ければ空 | 実効値で 1 件ずつ（子の環境・管理の設定のファイルと `managed-settings.d`・サーバーからの配布の控え・`--settings` の JSON とファイル・利用者・プロジェクトの設定の `apiKeyHelper` と `env` の `ANTHROPIC_API_KEY`・`CLAUDE_CODE_USE_ANTHROPIC_AWS`、設定の `env` の `ANTHROPIC_PROFILE`、`oidc_federation` の有効なプロファイル）で出所付きの項目名が返る。`--setting-sources ''` の `argv` では利用者・プロジェクトの設定の `apiKeyHelper` で当たらず、管理の設定と `--settings` では当たる。「打ち消しと選択の例」の 5 行がそれぞれの判定になる（利用者の `CLAUDE_CODE_USE_BEDROCK=1` を管理の `""` で打ち消すと当たらず、`--settings` の `1` が利用者の `0` の上に来ると当たる。控えがポリシーの項目を持てば `managed-settings.d` の `apiKeyHelper` で当たらず、控えに無い `env` の変数はファイルから当たる）。プロジェクトの設定の `env` の `CLAUDE_CODE_OAUTH_TOKEN` は当たらない（Claude Code が無視する）。`ANTHROPIC_BASE_URL` が `https://api.anthropic.com/` なら当たらず、子の環境か利用者の設定の別の URL なら当たる。利用者の設定の `env` か子の環境の `ANTHROPIC_CUSTOM_HEADERS`（`Authorization: Bearer x`）で `ANTHROPIC_CUSTOM_HEADERS（利用者の設定）` が当たり、返りにヘッダーの名前も値も無い。`--settings` のファイルの相対パスは引数の `cwd` から解き、テストの作業ディレクトリに同じ名前の別のファイルがあっても読まない | 返りと例外の文に値が出ない。読めない管理の設定・値の欠けた `--settings` で `auth_unresolved`。実効の `CLAUDE_CODE_USE_VERTEX=maybe` で `auth_unresolved` |
| 起動の後の変更 | 動いている間に源のファイルが変わらなければ、見回りで `stat` だけを打ち、ファイルを開かない | 共有の `settings.json` の `env` に `ANTHROPIC_API_KEY` を足すと、次の見回りで 1 行と `auth_drift` の行が出て、替え先の `work2` で `--resume`。その世代の使用率のファイルが消える。替え先が無ければ子を残して `stalled`。`hold` されない。`key:key-main` の子で、設定ファイルの `env` に `ANTHROPIC_BASE_URL=https://gateway.example.com` を足すか、`api-key-use off` を打つと、次の見回りで当たる。登録済みの替え先があればそちらで `--resume`、無ければ止めてよい時点で子を止め、`account_unavailable` で終わり、キーの子を起動し直さない | 上書きでない変更（`model` の変更）では何もしない |
| `planned_links`・`ready` | `projects` が無い初回のアカウントで None（共有側の `projects` になると解く） | アカウント側の `settings.json` が実体なら、その中の `apiKeyHelper` で `auth_overridden` | `projects` が実体なら `projects_not_shared`。何も書かない（ディレクトリの中身と更新時刻が変わらない） |
| `sync_config` | 共有側の値がアカウント側へ写り、`oauthAccount` は変わらない | 片側だけの変更・両側の変更・葉の削除・バックアップが無い・`userID` が違う。アカウント側で最後の `mcpServers` の葉を消す（`{}` とキーの欠落の 2 通り）と共有側からも消え、共有側が同じ葉を同時に変えていれば共有側の値が両側に残る。片側が消し、もう片側が変えた葉は共有側の状態。両側が同じ値へ変えた葉はその値 | 本体の排他が取れない・最初の置き換えで書けないときに理由を返し、両側の中身が変わらない。アカウント側・共有側・バックアップの各置き換えの直前で失敗させ、そのまま次の同期を打つと、「書く手順と途中で止まった後」の表の値にそろう。止めた後に共有側の同じ葉を変えてから次の同期を打つと共有側の値になる。旧い基準 A から両側を B へ置き換えた後、途中の記録をバックアップへ移す前に止め、アカウント側だけを C に変えて次の同期を打つと、両側が C になる。同じ時点で止め、アカウント側だけを A に戻すと両側が A に、アカウント側でその葉を消すと両側から消える。バックアップが無い最初の同期でアカウント側を置き換えた後に止め、アカウント側の葉を変えて次の同期を打つと、その値が両側に入る。最初の同期で途中の記録を書いた後、アカウント側の前に止めると、次の同期は最初の同期をやり直す。一時ファイルだけが残り途中の記録が無ければ、一時ファイルが消え前の同期のまま。復旧の再中断: アカウント側を置き換えた後・共有側の前に止め（共有側は A のまま）、次の同期の復旧を、新しい途中の記録を書く前・書いた直後・前の回の一時ファイルを消す前・新しい回の各置き換えの直前でそれぞれもう一度止めてから次の同期を打つと、どれも共有側が B になり、アカウント側の B が A に戻らない。同じ位置で止めた後に共有側の同じ葉を C に変えると C が両側に入る。両側の実パスが同じなら `same_file` で書かない。バックアップが 0600 |
| `prepare` | ok で `config_dir` が `<保存先>/<名前>` の絶対パス、使用の届けがある | 2 回続けて打っても同じ形 | 理由ごとに ok が偽、`RELOGIN_REASONS` だけ `needs_relogin` が真。共有側の `.credentials.json` の中身と更新時刻が変わらない。理由の文に値が出ない |
| `settle` | アカウント側の変更が共有側へ移り、使用の届けが消える | 変更が無ければ書かない | 書けないときに理由の符号。世代が違えば書き戻さず `reregistered`。`prepare` の後に、アカウント側の `.claude.json` を共有側を指す symlink・別のアカウントのファイルへのハードリンクに替える、または `NDF_SHARED_CONFIG_DIR` の先の設定ファイルをアカウント側へのハードリンクに替えてから `settle` を打つと、`not_account_local` で両方の中身が変わらず、使用の届けは消える。`prepare` の後に、アカウント側へ通常ファイルの `.config.json` を置く・アカウント側の `.claude.json` を別の本人の `oauthAccount` を持つ通常ファイルへ替えてから `settle` を打つと、`legacy_config`・`identity_mismatch` で共有側の中身が変わらず、使用の届けは消える |
| 登録のコマンドと使用の届け | 届けが無ければ再登録・削除ができる | `owner` も `child` も死んだ届け・`owner` が死んだ `starting` の届けは消して進む。pid が同じでも開始時刻が違う（使い回された pid）届けは死んだとみなす | 生きている届けがあれば `account add`・`account remove` が何も変えずに終了コード 1。ラッパーを `SIGKILL` で終え、子の claude が残っている間も `account add`・`account remove` が止まり、子を終えた後に通る。子の起動に失敗すると届けが消える。`ndf plan` の打ち直しの `claude -p` が動いている間も止まり、終わった後（失敗・例外でも）に届けが消える。再登録の後、旧い世代の `settle` が新しい登録へ書き戻さない。関所: 子を関所で止めたまま `owner` を `SIGKILL` で終えると、子は claude を起動せずに終わり（偽の claude が呼ばれない）、届けは残骸として消される。`attach` を失敗させると子が `SIGKILL` で回収され、偽の claude が呼ばれず、届けが消える。`claude` を `PATH` から外すと、関所の `execvp` の失敗が起動の知らせで返り、ラッパーは次の候補へ進み、`call_claude` は `Failure.LAUNCH` を返し、どちらも届けが消える。通常の起動: 親の対話の子が終わって自分の届けを消した後も、`ndf plan` の通常の `claude -p` が動いている間は `account add`・`account remove` が止まり、終わった後（失敗・タイムアウトでも）に通る。`acquire` が失敗すると `claude -p` を起動しない。親の子が終わった後に `work1` を登録し直すと、旧い世代の `NDF_ACCOUNT_USAGE_FILE` を継いだ worker の `acquire` が `reregistered` で落ち、`claude -p` を起動しない。`CLAUDE_CONFIG_DIR` が `<保存先>/work1` でなければ `config_mismatch` で落ちる |

## 子の環境（`session_env.py`・子の環境の表）

満たす決定: 決定 1・8、受け入れ条件 #3604 の 1〜3・11、#3864 の 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `child_env` | `session_env.py` | 変更 | `(environ, state_dir, account: Optional[str] = None, decided: bool = False) -> dict[str, str]` | `decided` が真なら `account` をそのまま `NDF_CLAUDE_ACCOUNT` にする（None なら外す）。偽のときだけ今どおり `accounts.chosen` を引く。ラッパーの `_start` は常に `decided=True` で、起動先の確定の結果（`Target.account`）を渡す。指定か環境変数の名前の用意に落ちて既定のログインへ移った子（`decided=True` で `account=None`）は `NDF_CLAUDE_ACCOUNT` を持たない（今の `decided=False` の出力では名前が入る。この差が意図した変更で、設計文書のテスト設計の #3604 の 3 が縛る） |
| `account_env` | `session_env.py` | 新設 | `(env: Mapping[str, str], config_dir: Path, usage_file: Path) -> dict[str, str]` | `child_env` の出力に、`account-set` の変数を入れ、`account-drop` の変数を外す。事前条件: `env` は `child_env` の出力（`NDF_CLAUDE_ACCOUNT` が入っている）。`config_dir` は `account_dir.prepare` が ok を返したパス。`usage_file` はラッパーが `accounts.usage_path(state_dir, name, generation)` で解いた絶対パス。事後条件: `CLAUDE_CONFIG_DIR` が `config_dir` である。`NDF_SHARED_CONFIG_DIR` は、`env` にあればその値、無ければ `env` の `CLAUDE_CONFIG_DIR`（無ければ空文字）である。`CLAUDE_CODE_PLUGIN_CACHE_DIR` は、`env` にあればその値である。無ければ `<共有の設定ディレクトリ>/plugins` で、共有の設定ディレクトリは `account_registry.shared_config_dir(env)` である。`NDF_ACCOUNT_USAGE_FILE` が `usage_file` である。`account-drop` の名前が 1 つも無い |
| `metered_env` | `session_env.py` | 新設 | `(env: Mapping[str, str], extra: Mapping[str, str], saved: bool) -> dict[str, str]` | `CLAUDE_CONFIG_DIR` を共有の設定ディレクトリへ戻し、`account-drop` の変数を外し、保存した設定なら提供元の秘密の変数（`account_registry.SECRET_ENV`）を外し、従量の接続の設定を重ね、最後にラッパーが確定する変数を入れ直す。事前条件: `extra` は `_metered` が検証を通したもの（下の「従量の接続の設定の検証」）。事後条件: `env` に `NDF_SHARED_CONFIG_DIR` があれば、その値が空でなければ `CLAUDE_CONFIG_DIR` へ入れ、空なら `CLAUDE_CONFIG_DIR` を外す。どちらでも `NDF_SHARED_CONFIG_DIR` と `NDF_ACCOUNT_USAGE_FILE` は外す。無ければ `CLAUDE_CONFIG_DIR` は親のまま。`account-drop` の名前を外す。`saved` なら `account_registry.SECRET_ENV`（下の「提供元ごとの照合する変数」の秘密の変数。保存の時点で拒む変数と同じ 1 つの定義）を外す。次に `extra` を重ね、最後に `NDF_CLAUDE_ACCOUNT=metered` と、上で決めた `CLAUDE_CONFIG_DIR`（または無いこと）を入れ直す。`METERED_FIXED` の変数の値は `extra` に依らない |
| `key_env` | `session_env.py` | 新設 | `(env: Mapping[str, str], key: str) -> dict[str, str]` | `metered_env(env, {}, saved=True)` と同じく共有の設定ディレクトリへ戻して `account-drop` と `SECRET_ENV` を外し、`NDF_CLAUDE_ACCOUNT=key:<key>` を入れる。キーの値は受けない（起動引数の `--settings` の `apiKeyHelper` で渡す。`account_choice.target_env` が引数を足す）。親の `ANTHROPIC_BASE_URL`・`ANTHROPIC_CUSTOM_HEADERS` は黙って外さない（`account_choice.verify` が許す宛先と比べ、当たればそのキーを候補から外して理由を出す） |
| `METERED_FIXED` | `session_env.py` | 新設 | 定数（表から引く） | ラッパーが確定し、従量の接続の設定が持ってはいけない変数: `child` が `set`・`drop`・`account-set` の変数すべて（`NDF_CLAUDE_ACCOUNT`・`CLAUDE_CONFIG_DIR`・`NDF_SHARED_CONFIG_DIR`・`CLAUDE_CODE_PLUGIN_CACHE_DIR`・`NDF_ACCOUNT_USAGE_FILE` ほか）と、名前が `NDF_` で始まる変数 |

どちらも純粋な関数で、ファイルを読まない。値の組は `facts.ids("env", child="account-set")` と `facts.ids("env", child="account-drop")` から引く。名前をコードに書かない。値は `_set_values` と同じ形の対応の辞書で作る

#### 提供元の選択の変数と有効な値（`account_registry.PROVIDER_ENV`・`provider_on`）

Claude Code が接続先を切り替える提供元の選択の変数は、`CLAUDE_CODE_USE_BEDROCK`・`CLAUDE_CODE_USE_VERTEX`・`CLAUDE_CODE_USE_FOUNDRY`・`CLAUDE_CODE_USE_ANTHROPIC_AWS`・`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`・`CLAUDE_CODE_USE_MANTLE` の 6 つである（STD7）。子の環境の表では、この 6 行に `"provider": true` を付ける。`account_registry.PROVIDER_ENV` は `facts.ids("env", provider=True)` で引く。親の環境から外す（`account-drop`）・実効の設定の確かめ（`auth_overrides`）・従量の接続の検証（下）は、すべてこの 1 つの分類を使う。`provider_on` は `account_registry.py` に新設し、シグネチャは `(value: Optional[str]) -> Optional[bool]` である。真偽の解釈は Claude Code（STD7）にそろえる。`1`・`true`・`yes`・`on`（大文字小文字を問わない）なら真、`None`・空文字・`0`・`false`・`no`・`off` なら偽、それ以外は None（解けない値）。既存の接続確認もこの関数に置き換え、両者の判定を 1 つにする。今の接続確認は `account_metered.py` の `CLAUDE_CODE_USE_BEDROCK in (None, "", "0")` で、`false` を有効と読む。この食い違いもなくす。

#### 従量の接続の設定の検証（`wrapper._metered`）

環境変数の宣言（`NDF_SUPERVISE_CLAUDE_FALLBACK`）と保存した設定（`metered.json`）の両方に、起動の前に同じ検証を当てる。1 つでも外れれば設定なしとして扱い、`_tell_at_start` が理由の 1 行（変数の名前だけ。値は出さない）を出す（`load_metered` が読めないときも同じ）。設定を直せば次の起動で効く。

| 検証 | 外れる例 |
| --- | --- |
| 構文。環境変数の宣言は `shlex.split` が通り、すべての語が `KEY=VALUE` で `KEY` が `[A-Z_][A-Z0-9_]*` | 閉じない引用符・`=` の無い語（今の `fallback_env` は黙って捨てる。捨てずに壊れた設定にする） |
| 空でない | 定義されているが空・空白だけ |
| ラッパーが確定する変数を持たない | `CLAUDE_CONFIG_DIR`・`NDF_CLAUDE_ACCOUNT`・`NDF_` で始まる変数（`METERED_FIXED`） |
| 提供元を 1 つに選べる | `PROVIDER_ENV` のうち `provider_on` が真のものがちょうど 1 つで、ほかの `PROVIDER_ENV` の値はどれも偽（None の値があれば外れる）。または、どれも真でなく `ANTHROPIC_API_KEY` が空白でない値で入っている（環境変数の宣言だけ。保存した設定は今どおり資格情報の変数を持てない）。`CLAUDE_CODE_USE_BEDROCK=0` だけ・`ANTHROPIC_API_KEY=` だけ・`CLAUDE_CODE_USE_VERTEX=maybe` は外れる |

接続の設定に許す変数は、上の 4 つの検証を通るものすべてである（Bedrock の地域・モデル・プロファイルの変数など）。保存した設定の `check_decl` にも 3 つ目と 4 つ目を足し、`account metered` の保存の時点で弾く。保存した設定が拒む秘密の変数は、下の `SECRET_ENV`（広げた定義）である。宣言の検証は `extra` の中だけを見る。ほかの源の `env` から、別の提供元・資格情報・接続先の変数が入ることがある。源は、共有側の `settings.json`・管理の設定・プロジェクトの設定・`--settings` である。入ると、会話を送る宛先と課金先が宣言から変わる。そのため、従量の子へ切り替える前に `account_dir.effective_env` を打ち、下の「従量の実効値の照合」をすべて確かめる。切り替える前とは、`_inputs` が `metered` を候補に入れるときと、`_launch_target` が `metered` を試すときである。引数は、`metered_env` の出力・共有の設定ディレクトリ・ラッパーの作業ディレクトリ・子の `argv` である。外れれば `metered_overridden`（変数の名前と出所だけ）で `metered` を候補から外し、1 行を出す。

#### 提供元ごとの照合する変数（`account_registry.METERED_CHECKED`・`SECRET_ENV`）

照合するのは、提供元の選択・認証の値・資格情報とアカウントの選択・接続先・要求に足すヘッダーの 5 組である。照合と理由の文に出すのは変数の名前と出所だけで、値は出さない。秘密の変数（`SECRET_ENV`）は 3 か所が同じ定数を引く。保存した設定の `check_decl` が拒み、保存した設定の起動で `metered_env` が親から外す。下の照合では、設定ファイルからの再注入を見る。

| 組 | 変数 | 秘密か |
| --- | --- | --- |
| 提供元の選択 | `PROVIDER_ENV` の 6 つ | いいえ |
| 認証の値 | `ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN`・`ANTHROPIC_PROFILE`・`ANTHROPIC_FEDERATION_RULE_ID`・`ANTHROPIC_ORGANIZATION_ID` | 前の 3 つ |
| AWS（Bedrock・Mantle・`CLAUDE_CODE_USE_ANTHROPIC_AWS`）の資格情報とアカウントの選択 | `AWS_BEARER_TOKEN_BEDROCK`・`AWS_ACCESS_KEY_ID`・`AWS_SECRET_ACCESS_KEY`・`AWS_SESSION_TOKEN`・`ANTHROPIC_AWS_API_KEY`・`AWS_PROFILE`・`AWS_CONFIG_FILE`・`AWS_SHARED_CREDENTIALS_FILE`・`ANTHROPIC_AWS_WORKSPACE_ID`・`CLAUDE_CODE_SKIP_BEDROCK_AUTH`・`CLAUDE_CODE_SKIP_MANTLE_AUTH`・`CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH` | `AWS_BEARER_TOKEN_BEDROCK`・AWS のキーの 3 つ・`ANTHROPIC_AWS_API_KEY` |
| Google Cloud（Vertex・`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`）の資格情報とアカウントの選択 | `GOOGLE_APPLICATION_CREDENTIALS`・`ANTHROPIC_VERTEX_PROJECT_ID`・`ANTHROPIC_GOOGLE_CLOUD_PROJECT`・`ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID`・`CLAUDE_CODE_SKIP_VERTEX_AUTH`・`CLAUDE_CODE_SKIP_ANTHROPIC_GOOGLE_CLOUD_AUTH` | いいえ |
| Foundry の資格情報とアカウントの選択 | `ANTHROPIC_FOUNDRY_API_KEY`・`ANTHROPIC_FOUNDRY_AUTH_TOKEN`・`ANTHROPIC_FOUNDRY_RESOURCE`・`CLAUDE_CODE_SKIP_FOUNDRY_AUTH` | 前の 2 つ |
| 接続先 | `ANTHROPIC_BASE_URL`・`ANTHROPIC_BEDROCK_BASE_URL`・`ANTHROPIC_BEDROCK_MANTLE_BASE_URL`・`ANTHROPIC_AWS_BASE_URL`・`ANTHROPIC_VERTEX_BASE_URL`・`ANTHROPIC_GOOGLE_CLOUD_BASE_URL`・`ANTHROPIC_FOUNDRY_BASE_URL`・`AWS_REGION`・`AWS_DEFAULT_REGION`・`CLOUD_ML_REGION`・`ANTHROPIC_GOOGLE_CLOUD_LOCATION` | いいえ |
| 要求に足すヘッダー（認証と振り分け） | `ANTHROPIC_CUSTOM_HEADERS` | はい（`Authorization` などの資格情報を含みうる。保存した設定は持てず、環境変数の宣言なら持てる） |

提供元ごとに分けず、どの提供元の宣言でも全部の組を照合する（使わない提供元の変数でも、設定ファイルから入れば Claude Code が別の宛先を選びうるため）。今の `SECRET_ENV`（AWS のキーの 3 つと認証の値の 3 つ）は、上の「秘密か」の列の変数すべてに広げる。名前の一覧は実装の PR で手元の Claude Code の環境変数の文書（STD7）と照らし、足りなければこの表へ足す。

#### 従量の実効値の照合

期待値は `metered_env` の出力で、実効値は `effective_env` の結果である。期待値は設定ファイルを重ねる前の子の環境で、宣言の値と、環境変数の宣言なら親から継いだ値を含む。

| 確かめ | 外れる例 |
| --- | --- |
| 実効の環境で `provider_on` が真の `PROVIDER_ENV` の変数が、宣言の提供元と同じ 1 つだけ（API キーの宣言なら 0 個）で、ほかの `PROVIDER_ENV` の変数の `provider_on` はどれも偽（None が 1 つでもあれば外れる。宣言の検証と同じ） | API キーの宣言に、共有側の `settings.json` の `env` の `CLAUDE_CODE_USE_BEDROCK=1` が加わる。API キーの宣言に設定ファイルの `CLAUDE_CODE_USE_VERTEX=maybe` が加わる |
| `METERED_CHECKED` の提供元の選択の外の変数ごとに、実効値の有無と値が期待値と同じ（期待値に無い変数は実効値にも無いか空文字。期待値にある変数は同じ値） | 管理の設定の `env` の `ANTHROPIC_AUTH_TOKEN`。保存した Bedrock の設定（`AWS_PROFILE=prod`）に、親の環境か設定ファイルの `AWS_BEARER_TOKEN_BEDROCK` が加わる（親の値は `metered_env` が外すので、残れば設定ファイルからの再注入）。設定ファイルの `env` が `AWS_PROFILE` を別の名前に替える。外した AWS のキーを設定ファイルが入れ直す。設定ファイルの `ANTHROPIC_BEDROCK_BASE_URL`・`AWS_REGION` で宣言と別の宛先へ向く。設定ファイルの `env` が宣言に無い `ANTHROPIC_CUSTOM_HEADERS` を足す |
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
| 既にある行（`CLAUDE_CONFIG_DIR`・`NDF_SHARED_CONFIG_DIR`・`ANTHROPIC_API_KEY`） | 設定値 | 同上 | 前の 2 つに `"child": "account-set"`、`ANTHROPIC_API_KEY` に `"child": "account-drop"` を足す |
| `CLAUDE_CODE_PLUGIN_CACHE_DIR`・`NDF_ACCOUNT_USAGE_FILE` の行 | 設定値 | 同上 | 新設。`area: accounts`・`set_by: ndf`・`type: path`・`secret: false`・`child: account-set`。`NDF_ACCOUNT_USAGE_FILE` は status line が使用率を書くファイルの絶対パス |
| 新設の認証の行（`CLAUDE_CODE_OAUTH_TOKEN`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_SCOPES`・`CLAUDE_CODE_USE_BEDROCK`・`CLAUDE_CODE_USE_VERTEX`・`CLAUDE_CODE_USE_FOUNDRY`・`CLAUDE_CODE_USE_ANTHROPIC_AWS`・`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`・`CLAUDE_CODE_USE_MANTLE`・`ANTHROPIC_PROFILE`・`ANTHROPIC_FEDERATION_RULE_ID`・`ANTHROPIC_ORGANIZATION_ID`） | 設定値 | 同上 | どれも `area: accounts`・`set_by: user`・`child: account-drop`。`CLAUDE_CODE_OAUTH_TOKEN`・`ANTHROPIC_AUTH_TOKEN` は `type: string`・`secret: true`。`CLAUDE_CODE_OAUTH_SCOPES` と `ANTHROPIC_PROFILE`・`ANTHROPIC_FEDERATION_RULE_ID`・`ANTHROPIC_ORGANIZATION_ID`（認証の優先順位でログインより上に来るプロファイルの源）は `type: string`・`secret: false`。`CLAUDE_CODE_USE_*` の 6 つは `type: flag`・`secret: false`・`provider: true`（提供元の選択の変数） |

### 既存の部品の再利用

`account_env`・`metered_env`: 確かめた既存の部品は `account_login.auth_env`・`training_cache.CLAUDE_FOREIGN_AUTH`（`grep -n "AUTH_ENV\|CLAUDE_FOREIGN_AUTH" src/ndf/domains/relay/account_login.py src/ndf/adapters/training_cache.py`）。結果: 使えない。`auth_env` はログイン用の仮ディレクトリを作る環境で、`CLAUDE_FOREIGN_AUTH` は 13 変数を全部は持たない。名前は表から引く。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `account_env` | 4 変数が入り、`account-drop` の 13 変数が外れる（親の `CLAUDE_CODE_USE_ANTHROPIC_AWS`・`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD` も外れる） | 親に `CLAUDE_CONFIG_DIR` が無い（`NDF_SHARED_CONFIG_DIR` が空文字）・親が登録済みアカウントの子（親の `NDF_SHARED_CONFIG_DIR` を引き継ぐ）・親に `CLAUDE_CODE_PLUGIN_CACHE_DIR` がある | 表の行を 1 つ外したテストの表で落ちる |
| `key_env` | `CLAUDE_CONFIG_DIR` が共有の設定ディレクトリ、13 変数と `SECRET_ENV` が無く、`NDF_CLAUDE_ACCOUNT` が `key:key-main` | 親が登録済みアカウントの子でも共有の設定ディレクトリへ戻る | 親の `ANTHROPIC_API_KEY` が子に残らない |
| `metered_env` | `CLAUDE_CONFIG_DIR` が共有の設定ディレクトリ、13 変数が無く、`extra` が入る | 保存した設定で `SECRET_ENV` の変数（AWS のキー・`AWS_BEARER_TOKEN_BEDROCK` ほか）が無い・環境変数の設定で AWS のキーが残る・親がアカウントの子でなければ `CLAUDE_CONFIG_DIR` は親のまま・`NDF_SHARED_CONFIG_DIR` が空なら外す・`NDF_ACCOUNT_USAGE_FILE` が無い | `extra` に `NDF_CLAUDE_ACCOUNT`・`CLAUDE_CONFIG_DIR` を入れても子の値は `metered` と共有の設定ディレクトリ |
| `_metered` の検証 | Bedrock の宣言が通る | `ANTHROPIC_API_KEY` だけの環境変数の宣言が通る。`CLAUDE_CODE_USE_BEDROCK=1` に `CLAUDE_CODE_USE_VERTEX=0` を添えた宣言が通る | 閉じない引用符・`=` の無い語・空・`CLAUDE_CONFIG_DIR` や `NDF_` で始まる変数・提供元が無い・2 つの提供元を持つ・`CLAUDE_CODE_USE_BEDROCK=0` だけ・`ANTHROPIC_API_KEY=` だけ・提供元の値が解けない宣言が、それぞれ設定なしとして扱われ `_tell_at_start` が 1 行 |
| `provider_on` | `1`・`TRUE`・`yes`・`On` が真 | `0`・`false`・`no`・`off`・空・None が偽 | それ以外が None。`account_metered` の接続確認が同じ判定を使う（`CLAUDE_CODE_USE_BEDROCK=false` を確かめの対象にしない） |
| 従量の実効の設定の確かめ | 共有側の設定に上書きが無ければ `metered` で起動する | 共有側の `settings.json` の `env` が宣言と同じ提供元・同じ値なら通る | API キーの宣言に共有側の `CLAUDE_CODE_USE_BEDROCK=1`、Bedrock の宣言に管理の設定の `apiKeyHelper` が加わると、`metered` を候補から外して 1 行を出す（子を止めない）。API キーの宣言に設定ファイルの `CLAUDE_CODE_USE_VERTEX=maybe`、保存した Bedrock の設定（`AWS_PROFILE`）に親の環境の `AWS_BEARER_TOKEN_BEDROCK`（`metered_env` が外し、子に無い）と設定ファイルの `AWS_BEARER_TOKEN_BEDROCK`・別の `AWS_PROFILE`・外した AWS のキー、設定ファイルの `ANTHROPIC_BEDROCK_BASE_URL`・`AWS_REGION`が加わると、それぞれ外れる。宣言に同じ `ANTHROPIC_BASE_URL` があれば通る。保存の時点の `check_decl` が `AWS_BEARER_TOKEN_BEDROCK`・`ANTHROPIC_FOUNDRY_API_KEY` を拒む |

## 登録の保存先（`account_registry.py`）

満たす決定: 決定 1・3、受け入れ条件 #3604 の 1・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `shared_config_dir` | `account_registry.py` | 新設 | `(environ: Mapping[str, str]) -> Path` | 共有の設定ディレクトリ。`NDF_SHARED_CONFIG_DIR` があればその値（空なら `~/.claude`）、無ければ `signals.claude_config_root(environ)` |
| `store_dir` | `account_registry.py` | 変更 | 変わらない | `NDF_ACCOUNTS_DIR` が無ければ `shared_config_dir(environ) / "ndf" / "accounts"`。振る舞いは今と同じ |
| `mark_relogin`・`generation` | `account_registry.py` | 新設 | `mark_relogin` は `(store: Path, name: str) -> None`、`generation` は `(store: Path, name: str) -> str` | `mark_relogin`: 呼ぶ側が持つアカウントの排他の中で、`account.json` の `needs_relogin` を真にして置き換えで書く。事前条件: `account_registry.locked(store, name)` を持っている（`account_dir.prepare` の中から呼ぶ）。副作用: `account.json` の書き換え（0600）。ほかの項目は変えない。`generation`: `account.json` の `generation`（無ければ空文字） |
| 登録（`account add`） | `cli_account.py`・`account_registry.py` | 変更 | 変わらない | 登録の排他の中で、使用の届けを確かめ（生きていれば止める）、旧形式の `.config.json` を `backups/` へ移し、新しい `generation` を書く。成功の後、`accounts.forget(state_dir, name, keep=新しい世代)` で外した間と上限の観測から旧い世代の項目を消す |
| 削除（`account remove`） | 同上 | 変更 | 変わらない | 使用の届けを確かめ（生きていれば止める）、今の削除に加えて `accounts.forget(state_dir, name, keep=None)` を打つ |
| `recovery_keys`・`key_value` | `account_registry.py` | 新設 | `recovery_keys` は `(store: Path) -> list[str]`、`key_value` は `(store: Path, name: str) -> str` | `recovery_keys`: 登録簿の使い方の設定が `off` でなければ、登録したキーの名前を名前の順に返す。`off`・登録簿が無い・壊れているときは空（決定 12）。`key_value`: 登録簿からキーの値を返す。無ければ `AccountError`。呼ぶのは `api-key-helper` だけ |

世代の照合: 使用率のキャッシュのパス、外した間と上限の観測の項目は世代を持つ。読む側は今の `account.json` の世代と違うものを無いものとして読む。再登録の後に旧いセッションの status line やラッパーが遅れて書いても、旧い世代のパスか項目へ入るので、新しい登録の判断へ戻らない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `mark_relogin` | `AccountError`・`OSError` | `account.json` が無い・壊れている・書けない | `_read`・`write` | 呼び出し元へ返す（`prepare` は理由を変えずに起動を飛ばす） | ラッパーの 1 行は元の理由のまま | 次の起動でもう一度試す |

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
| `usage_dir`・`usage_path` | `accounts.py` | 新設・変更 | `usage_dir` は `(state_dir: Path) -> Path`、`usage_path` は `(state_dir: Path, name: str, generation: str) -> Path` | `usage_dir`: `<状態の保存先>/relay/accounts/usage`（ファイルの表の `relay-usage` の `path` の親から）。`usage_path`: アカウント 1 つ・登録の世代 1 つの使用率のキャッシュのパス（`usage/<名前>/<世代>.json`。世代が空文字なら `usage/<名前>/_.json`）。status line へは `NDF_ACCOUNT_USAGE_FILE` でこの結果を渡す |
| `usages` | `accounts.py` | 変更 | `(state_dir: Path, now: float, generations: Mapping[str, str]) -> list[account_decide.Usage]` | 登録済みアカウントごとに今の世代のファイルだけを名前の順に読む。窓ごとにリセットの時刻を過ぎていれば 0% にし、5 時間と 7 日の使用率と 7 日のリセットの時刻を返す。使用率（一覧の表示）は窓の最大、上限は過ぎていない窓のどれかが 100 以上。最後に `limits_seen` を重ね、上限の観測が続く名前を上限にする。使用率の行が無い（ファイルが無い・有効な窓が無い）名前に上限の観測が続いていれば、`Usage(name, None, None, None, None, True)`（使用率は分からないが上限）の行を作る（判断の入力の唯一の経路）。事後条件: 返す名前は登録済みアカウント（`generations` のキー）だけである。読めないファイル・形の違うファイル・窓を 1 つも持たないファイル（未観測）は返さない。返さない名前は使用率の分からないアカウント（組 1）になり、0% として読まない。窓を持つファイルは、窓がすべてリセットの時刻を過ぎていても返す。過ぎた窓は 0%（受け入れ条件 #3864 の 3）で、その窓の次のリセットの時刻は分からないので、7 日の窓が過ぎていれば `seven_resets_at` は None とする。そのため、最後に記録した窓がすべて切れたアカウントは、5 時間と 7 日が 0% の組 0 になる（観測した窓が切れた＝リセットの後で使っていない）。ただし、上限の観測が続く名前は、ファイルが無くても `Usage(name, None, None, None, None, True)` で返す。status line が無い経路で、上限の行を見たアカウントを「使用率の分からない候補」として選び直さないためである |
| `write_usage` | `accounts.py` | 新設 | `(path: Path, windows: Mapping[str, tuple[float, float]], now: float) -> None` | status line と同じ形・同じ合わせ方（下の「書き込みの規則」）で 1 つの記録を書く（テストが使う。今の `write_usages` は消す） |
| `held_path`・`held` | `accounts.py` | 新設 | `held_path` は `(state_dir: Path) -> Path`、`held` は `(state_dir: Path, now: float, generations: Mapping[str, str]) -> dict[str, float]` | `held_path`: `<状態の保存先>/relay/accounts/held.json`。`held`: 外す終わりが `now` より後で、世代が今の登録と同じ名前と終わりの時刻 |
| `hold`・`unhold` | `accounts.py` | 新設 | `hold` は `(state_dir: Path, name: str, generation: str, until: float, now: float) -> None`、`unhold` は `(state_dir: Path, name: str) -> None` | `hold`: 錠の中で読み、終わった項目を消し、`name` の下の `generation` の項目だけを `{"until"}` で書いて置き換える。ほかの世代の項目は変えない（下の「世代ごとの項目」）。`hold`・`unhold`・`see_limit` の副作用: 書くファイルの `.lock` の錠（`signals.held`、待ちは `relay.signal-lock`）と置き換えの書き込み。`unhold`: 錠の中で `held.json` から `name` の項目を世代を問わず消す。`account use <名前>` が指定を書いた後に打つ。副作用は `hold` と同じ錠と置き換え |
| `limits_path`・`limits_seen`・`see_limit` | `accounts.py` | 新設 | `see_limit(state_dir, name, generation, seen_at: float, until: float, window: Optional[str], window_resets_at: Optional[float])`・`limits_seen(state_dir, now, generations, windows: Mapping[str, Mapping[str, tuple[float, float]]]) -> dict[str, tuple[float, float]]` | 上限の観測（`<状態の保存先>/relay/accounts/limited.json`、`{"<名前>": {"<世代>": {"seen_at", "until", "window", "window_resets_at"}}}`。`window` は上限の窓を特定できたときの窓の名前で、特定できなければ null。`window_resets_at` は見た時刻のキャッシュでのその窓の `resets_at`（窓を特定した上で、キャッシュにその窓があったときだけ。無ければ null）。`until` は解除の推定の時刻で、窓の識別には使わない）。ラッパーが上限の行を初めて見たときに書く。`limits_seen` は下の「解除」に当たらない観測を返す（`windows` は名前ごとの今のキャッシュの窓）。`see_limit` の副作用は `hold` と同じ錠と置き換え |
| `limit_until` | `accounts.py` | 新設 | `(line: str, windows: Mapping[str, tuple[float, float]], seen_at: float, tz: tzinfo) -> tuple[float, Optional[str], Optional[float]]` | 上限の行と使用率のキャッシュから `until`・窓の名前・見た時刻のその窓の `resets_at` を決める（下の「`until` の決め方」）。時計を読まない |
| `forget` | `accounts.py` | 新設 | `(state_dir: Path, name: str, keep: Optional[str]) -> None` | `held.json` と `limited.json` の `name` の下の、`keep`（今の世代。削除なら None）でない世代の項目を消し、`usage/<名前>/` の `keep` でないファイルを消す（登録・削除の成功の後に打つ） |

- `account_decide.Usage` の型の変更（決定 5）: `Usage(name, percent, five, seven, seven_resets_at, limited)` にする。`percent`・`five`・`seven` は `Optional[float]`（窓が無い・使用率が分からないとき None）、`seven_resets_at` は `Optional[float]`。`percent` が None の行は `limited` が真の行だけ。`order`・`before_limit`・`guard`・`after_limit` は `limited` が真の行を上限として扱い、「使用率の分からない候補」に入れない。一覧（`_acc_list`）は `percent` が None なら `-` と出す
- 世代ごとの項目: `held.json`・`limited.json` は名前の下に世代ごとの項目を持つ（`{"work1": {"<世代>": {…}}}`）。書き手（`hold`・`see_limit`）は自分の世代の項目だけを書き、ほかの世代の項目を上書き・削除しない。`forget` は今の世代を残して消す。読み手は今の `account.json` の世代の項目だけを読む。そのため、再登録の後に旧い世代のラッパーが遅れて書いても新しい世代の項目は残り、新旧の書き込みがどちらの順でも新しい世代の上限・外した間が失われない。旧い世代の項目は、次の `forget` と、終わりの時刻を過ぎた項目を消す書き込みで消える

#### 上限の観測

上限の行を見た時点から解除までの根拠を、`limited.json` の 1 つにする。指定（`_pinned_or`）・替え先の候補（`order`）・同じアカウントへの復帰（`after_limit`）・リセット待ちは、すべて `usages` を通してこれを読む。`_limit_pick` が持っていた「キャッシュのファイルの更新時刻が上限を見た時刻より古ければ上限」の補正は、これに置き換えて消す。

| 場面 | 規則 |
| --- | --- |
| 書く時点 | 登録済みアカウントの子の画面に上限の行を初めて見た見回り（`Section.limit_at` を立てる時）に 1 回 |
| `until` の決め方 | 上から順に、最初に決まったもの。(1) 上限の行にリセットの時刻（`resets 3pm (Asia/Tokyo)` などの `resets` の後の時刻）があれば、`seen_at` より後で最も近いその時刻。窓の名前は行の語（`session` は `five_hour`、`weekly`・`week` は `seven_day`）から、無ければ `resets_at` がその時刻と同じ時（時と分）の窓、それも無ければ null。(2) 行が窓の語だけを持ち、その窓がキャッシュで見た時刻にリセットを過ぎていなければ、その窓の `resets_at`。(3) キャッシュのリセットを過ぎていない窓のうち使用率が 100 以上のものがあれば、その中で `resets_at` の最も遅い窓（どれも塞いでいるので、遅いほうまで使えない）。(4) それ以外（窓が無い・どの窓も 100 未満の古い記録）は、`five_hour` がリセットを過ぎていなければその `resets_at`、無ければ `seen_at + relay.limit-unknown` の秒。(4) は推定なので、窓の名前と `window_resets_at` は null（推定が外れて実は 7 日の窓の上限なら、`until` の後の `--resume` が上限の行をもう一度見て、新しい観測で決め直す）。(1)〜(3) で窓を特定したときは、`window_resets_at` に見た時刻のキャッシュのその窓の `resets_at` を入れる（(2)(3) ではその窓の値、(1) では同じ名前の窓がキャッシュにあればその値） |
| 解除 | `now ≥ until`、または観測の窓が入れ替わったとき。入れ替わりは、`window` と `window_resets_at` がどちらも null でなく、キャッシュの同じ名前の窓の `resets_at` が `window_resets_at` より後で、その窓の使用率が 100 未満のときだけである。比べるのは観測した窓の識別値（`window_resets_at`）で、推定の `until` とは比べない。同じ窓の値（92% から 93% への増加など）・書き込みの時刻・別の名前の窓の入れ替わり（7 日の上限を 5 時間の窓の入れ替わりで解くなど）では解かない。`window` か `window_resets_at` が null なら `now ≥ until` だけで解く |
| 古い記録・遅れて届いた入力 | 見た時刻より前の窓の値は、上限でないとは読まない（観測が続く間は上限）。上限より前の入力が遅れて書かれても、その窓の `resets_at` は `until` 以下なので解除に当たらない。`resets_at` が `until` より後の窓は、リセットの後に Claude Code が返した値だけが持つ |
| 従量の接続・既定のログイン | 書かない（名前で除けるのは登録済みアカウントだけ） |

`relay.limit-unknown` は 5 時間の窓の長さ（18000 秒）で、余白を足さない。リセットの時刻の行の形は、実装の PR で手元の上限の画面から確かめ、`limit_until` のテストの入力に使う（形が違えば (1) を飛ばして (2) 以降で決まる）。

#### 書き込みの規則（status line と `write_usage`）

同じアカウントの 2 つの status line が別々の時点の入力を受けて前後して終わっても、新しい値を古い値で戻さない。書き手は `<ファイル>.lock` のディレクトリを `mkdir` で取り、前の中身と窓ごとに合わせてから置き換える。錠が取れなければ、その回は書かない。10 秒より古い錠は残骸として消して取り直す。窓ごとに上の行から順に当て、最初に当たった行で決める。

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
| `usages`・`held` | 読めない・形が違う | 書き込みの途中・手で壊した | 読み | 既定値で続ける（`usages` はそのファイルを飛ばし、`held` は外した間が無いとして読む） | 無し | 次の status line の更新か次の `hold` で直る |
| `hold`（ラッパーの `_auth_pick` から） | 錠を取れない・書けない | ほかのレーンが同時に書いている・ディスク | 錠と置き換え | 既定値で続ける（記録せず替え先の判断は続ける。今の判断では今のアカウントを候補から除く） | `log.jsonl` に 1 行 | 次の認証の失敗で再び書く |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `relay-usage` の行 | 設定値 | `contracts/domains/ndf-files.json` | `path: "usage/<名前>/<世代>.json"`・`writer: "NDF の status line（statusline.sh）"`・`keys: ["windows", "recorded_at"]` |
| `relay-auth-held`・`relay-limit-seen` の行 | 設定値 | 同上 | 新設。`location: relay-accounts`・`path: held.json`・`path: limited.json`（順に）・`kind: measured`・`tracked: false`・`writer: "中継のラッパー（M3b）"`・`ends: replaced` |
| `relay.five-hour-guard`・`relay.weekly-prefer`・`relay.weekly-guard` | 設定値 | `contracts/limits.json` | 順に `{"summary": "閾値 A。5 時間の窓の使用率がこれ以上のアカウントを使い切りの組に入れ、見回りで替える（百分率。#3864）", "count": 85}`・`{"summary": "閾値 B。7 日の窓の使用率がこれより下のアカウントを余裕の組に入れる。カットポイントと替え先の選び方だけに効く（百分率）", "count": 70}`・`{"summary": "閾値 C。7 日の窓の使用率がこれ以上のアカウントを使い切りの組に入れ、見回りで替える（百分率）", "count": 90}` |
| `relay.usage-guard` | 設定値 | 同上 | 消す（閾値 A・C が置き換える）。`relay.max-starts` も消し、`relay.spin` を足す（「次の手の判断」） |
| `relay.auth-hold`・`relay.limit-unknown` | 設定値 | `contracts/limits.json` | `{"summary": "認証の失敗で、そのアカウントを替え先と指定の候補から外す秒（#3864）", "seconds": 3600}`・`{"summary": "上限の行を見たアカウントの使用率の記録に有効な窓が無いとき、上限として扱う秒（5 時間の窓の長さ）", "seconds": 18000}` |
| `relay.end-wait` | 設定値 | `contracts/limits.json` | `{"summary": "API キーの子でキーの削除・使い方の off を検出してから、止められる時点が分からなくても子を止めるまでの秒（決定 12）", "seconds": 60}` |
| 窓の名前 | 定数 | `account_registry.WINDOWS` | `("five_hour", "seven_day")`（既にある） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 別のアカウント・同じアカウントの 2 つの status line | 使用率のキャッシュ（別のアカウントならファイルが別、同じアカウントなら同じ 1 ファイル） | 別のアカウントは取らない。同じアカウントは `<ファイル>.lock` のディレクトリ（取れなければその回は書かない） | 置き換えで書く。同じアカウントでは錠の中で読み直し、「書き込みの規則」で窓ごとに合わせるので、遅れて終わった古い入力が新しい値を下げない |
| 2 つのラッパーの `hold`・`see_limit`、登録のコマンドの `forget`、`account use` の `unhold` | `held.json`・`limited.json` | `signals.held` の錠 | 読み直してから書くので、ほかの名前を消さない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `usage_dir`・`held_path`・`write_usage`・`hold` | `accounts_dir`・`facts.value("locations", …)`・`signals.replace_json`・`signals.held` | ファイルを読んで確かめた | 使う。パスはファイルの表から引く |
| `held` | `accounts.pinned` の読み方 | 同上 | 同じ形で読み、壊れていれば空として扱う（`pinned` と違い止めない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `usages` | 5 時間と 7 日の使用率が窓ごとに返り、使用率は大きいほう | リセットの時刻を過ぎた窓が 0%・5 時間の窓が無いと `five` が None・100 ちょうどで上限・7 日のリセットの時刻が返る。窓が 2 つともリセットを過ぎたファイルは 0%・0%・`seven_resets_at` が None の行で返り、`group` が組 0。窓を 1 つも持たないファイル（空の `windows`）とファイルの無い名前は返らず組 1 | 壊れたファイルを飛ばす・`metered.json` を読まない・旧い世代のファイルを読まない |
| `write_usage`・`usages` | 書いた値を読み戻せ、ほかのアカウントのファイルが変わらない | 同じ窓で使用率の小さい古い入力を後から書いても下がらない。`resets_at` の古い入力を後から書いても前の窓のまま。新しい窓は使用率が小さくても採る。前の窓が失効した後に、それよりさらに古い `resets_at`・既に失効した `resets_at` の入力を書いても前の値のまま | 錠が取られていればその回は書かず、前の中身が残る |
| `held`・`hold`・`unhold` | 書いた名前が終わりまで返る | 終わりちょうどの時刻で返らない・終わった名前が次の書き込みで消える・旧い世代の項目が返らない・`unhold` で消える。再登録の後、新しい世代の `hold` の後に旧い世代の `hold` を打っても、旧い世代の `hold` の後に新しい世代の `hold` を打っても、新しい世代の項目が残って `held` が返す。`see_limit` も同じ 2 つの順で新しい世代の観測が残る。`forget(keep=新しい世代)` は旧い世代の項目だけを消す | 壊れたファイルで空を返す |
| `see_limit`・`limits_seen`・`usages` | 上限の行の後、古い 92% の記録を持つ名前が上限として返る | `until` を過ぎると上限でない。観測の窓の `resets_at` が `window_resets_at` より後の 100 未満の値に替わると、`until` の前でも上限でない。(4) の推定で窓が null の観測は、5 時間の窓の `resets_at` が `until` より後の 92% に替わっても上限のまま。`seven_day` の観測は、`five_hour` の窓が入れ替わっても上限のまま。上限の行を見た後に同じ窓が 92% から 93% に増えても、上限より前の入力が遅れて書かれても上限のまま。使用率のファイルが無い名前に観測だけがあると `Usage(name, None, None, None, None, True)` が返り、`until` を過ぎると行が返らない | 壊れたファイルで観測が無いとして読む |
| `limit_until` | 行の `resets 3pm` が `until` になる | 行の時刻が無く `weekly` の語だけなら `seven_day` の `resets_at` で、窓は `seven_day`・`window_resets_at` はその値。古い記録で 7 日が 90%・5 時間が 80% なら (4) で `five_hour` の `resets_at` が `until` になり、窓と `window_resets_at` は null。100 以上の窓が 2 つなら遅いほう。窓が無ければ `seen_at + 18000` で窓は null | 行の時刻が読めなければ次の規則へ進む |

## 替えるかの判断（`account_decide.py`）

満たす決定: 決定 5・6・8・11、受け入れ条件 #3864 の 4〜7・9・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Guards`・`group` | `account_decide.py` | 新設 | `Guards` は 型 `(five: float, prefer: float, seven: float)`、`group` は `(u: Optional[Usage], g: Guards) -> Optional[int]` | `Guards`: 閾値 A・B・C（呼ぶ側が `limits.json` から作って渡す）。`group`: 選ぶ順の組の番号。0 余裕の組（`five` が None か A より下、かつ `seven` が None か B より下）・1 使用率の分からないもの（`u` が None）・2 B 越えの組（`five` が A より下か None、`seven` が B 以上 C より下）・3 使い切りの組（`five` が A 以上か `seven` が C 以上）。上限（`limited`）は None |
| `order` | `account_decide.py` | 新設 | `(exclude: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> list[str]` | `candidates` から `exclude` と上限のものを除き、組の番号 → `seven_resets_at` の早い順（None は後）→ `seven` の大きい順 → 名前の順に並べる |
| `before_limit` | `account_decide.py` | 変更 | `(current: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> Pick` | カットポイントの判断。今が従量の接続（`metered`・`key:`）なら `order` の先頭へ戻す。今の組より番号の小さい組の候補があれば `order` の先頭へ替え、無ければ `keep`（切り替えの余裕。同じ組・候補が無いときも）。今が従量の接続で `order` が空なら `keep`。今のアカウントの使用率が分からない・既定のログインなら `keep`（守り 3 と見回りに任せる）。今が上限なら組 4 として比べる |
| `guard` | `account_decide.py` | 新設 | `(current: Optional[str], rows: list[Usage], candidates: list[str], g: Guards) -> Pick` | 見回りの判断。今が従量の接続で `order` が空でなければ先頭へ `switch`（戻る）。今が登録済みアカウントで組 3 にあり、`order` の先頭が組 0〜2 なら先頭へ替える。それ以外は `keep`。今が既定のログイン・使用率の分からないアカウントなら `keep` |
| `after_limit` | `account_decide.py` | 変更 | `(current: Optional[str], rows: list[Usage], candidates: list[str], fallbacks: list[str], g: Guards) -> Pick` | 今のアカウントが候補にあり上限が解けていて、`order` が空か、`order` の先頭より組の番号が大きくなければ同じアカウント（上限は上限の観測を重ねた後で見る。登録が 1 つだけで上限が解けたときもここに当たる）。そうでなければ `order` の先頭（組 3 も入る）、無ければ `fallbacks`（今の種類を除く）の先頭、それも無ければ `wait_reset`（呼ぶ側が見回りのたびに入力を読み直し、解けたアカウントが現れれば最初の場合に当たる） |
| `after_auth` | `account_decide.py` | 新設 | `(current: Optional[str], rows: list[Usage], candidates: list[str], fallbacks: list[str], g: Guards) -> Pick` | `order` の先頭（今が従量の接続でも同じ）、無ければ `fallbacks`（今の種類を除く）の先頭、それも無ければ `stalled` |

- `candidates` は、呼ぶ側（`account_choice.candidates`）が渡す起動できる登録済みアカウントの名前である。再登録が要るもの・外した間のもの・`account_dir.ready` に落ちたものを除く。`fallbacks` は `account_choice.fallbacks` が渡す従量の接続の候補（`metered`、続けて `key:<名前>` の名前の順）。`rows` は上限の観測を重ねた使用率。判断の関数は候補の外の名前を返さない
- `Pick.kind` に `stalled`（認証の失敗で替え先が無い）を足す。`reason` は値を含まない文で、組の番号と閾値を名で書く（`work1 の 5 時間 86% が閾値 A 85% 以上。work2（余裕の組）へ替える`）

### 処理の手順

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
| `after_limit` | 使用率の分からない `work2` へ替える | 閾値の内側のものが組 3 より先・候補が組 3 だけでも替える・上限が解けた今のアカウント（組 0）に戻る・登録が `work1` の 1 つだけで `order` が空のとき、`work1` の上限が解けると `work1` で同じ会話を続ける（解けるまでは `wait_reset`）・今のアカウントの行が `Usage(name, None, None, None, None, True)` なら戻らない | 候補が無ければ `metered`、無ければ `key:k1`、無ければ `wait_reset`。今が `metered` なら `key:k1`、それも無ければ `wait_reset`。上限が解けていても今のアカウントが `candidates` に無い（外した間・再登録が要る）なら同じアカウントへ戻らない |
| `after_auth` | `work2` へ替える | 既定のログイン（今が None）でも替える。今が `metered` で登録済みの候補 `work1` があれば `work1` へ替える | 候補が無く `fallbacks` が空で `stalled`、今が `metered` で候補もキーも無ければ `stalled` |
| ラッパーの `tried`（決定 6） | `key:k1` が認証に失敗すると `key:k2` で `--resume` | `key:k2` で応答が 1 つ返ると `tried` が空になる | 登録済みアカウントが使えず `key:k1`・`key:k2` がどちらも失効しているとき、`key:k1` → `key:k2` の後に `stalled` で子を残し、直し方の 1 行に 2 つの名前が並ぶ。`key:k1` を 2 回起動しない |

## 次の手の判断（`switch_decide.py`）

満たす決定: 決定 6・8・11・13、受け入れ条件 #3864 の 6・7、不変条件 I14・I15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `SectionState.auth_pick`・`SectionState.guard_pick`・`safe` | `switch_decide.py` | 新設 | `SectionState.auth_pick` は フィールド `Optional[Pick]`（既定は None）、`SectionState.guard_pick`・`safe` は フィールド `Optional[Pick]`・`bool`（既定は None・偽） | `SectionState.auth_pick`: 画面に認証の失敗の行が出たときの替え先の判断。今の `auth_failed` は消す。`SectionState.guard_pick`・`safe`: 見回りの判断（`account_decide.guard` の `switch`）と、止めてよい時点か（呼ぶ側が `observe.tool_pending`・静かな時間・質問から決める） |
| `SectionState.spin`・`spin_seen`・`spin_held` | `switch_decide.py` | 新設 | フィールド `bool`・`frozenset[str]`・`bool`（既定は偽・空・偽） | 再起動ループの検出の入力。ラッパーが下の関数で作る（「ラッパー」の節の「再起動ループの検出」）。`spin` は「今の区間を終えて、数える起動で次の区間を始めると数が `count` に達する」、`spin_seen` は続きの中で起動した起動先、`spin_held` は検出に当たった後の保持（以後の区切りでも起動しない。検出に当たった区間が起動から `limit.seconds` に達したら下ろす。「ラッパー」の節の「再起動ループの検出」）。`starts_today`・`max_starts` は消し、判断は起動の回数を入力に持たない（I15） |
| `Spin`・`Streak` | `switch_decide.py` | 新設 | 型 `(count: int, seconds: float)`・`(count: int, seen: frozenset[str])` | `relay.spin` の数と秒（秒は `NDF_RELAY_SPIN` で替え、0 なら検出しない）・短い区間の続き（短い区間の数と、続きの中で起動した起動先） |
| `target_key` | `switch_decide.py` | 新設 | `(account: Optional[str]) -> str` | 起動先を見分ける値。登録済みアカウントの名前・`metered`・`key:<キーの名前>` はそのまま、既定のログイン（None）は `DEFAULT_TARGET`（`"(default)"`。登録の名前に使えない括弧を含むので重ならない） |
| `advance`・`hit` | `switch_decide.py` | 新設 | `advance` は `(streak: Streak, seconds: float, kind: str, current: str, target: str, limit: Spin) -> Streak`、`hit` は `(streak: Streak, limit: Spin) -> bool` | `advance`: 終わった区間（長さ `seconds`、起動先 `current`）と次の区間を起動する手（`kind` は `switch`・`restart`・`handover`・`account`、起動先 `target`）で続きを進める（下の処理の手順）。`hit`: `limit.seconds` が 0 より大きく、`streak.count` が `limit.count` 以上 |
| `_spun` | `switch_decide.py` | 新設 | `(state: SectionState, kind: str, account: Optional[str]) -> bool` | 次の区間を起動する手を返す前に当てる。`spin_held` が真なら真。`spin` が真なら、`kind` が `account` で `target_key(account)` が `spin_seen` に無い（続きの中で初めての起動先への替え）ときだけ偽、ほかは真。`account` の手の `account` は判断の関数の替え先そのものではなく、ラッパーが子を止める前に確定した `Choice` の先頭（`_pinned_or` と `ready` の後。「ラッパー」の節の「再起動ループの検出」の「起動先ごとの判定」）。ラッパーも同じ関数を起動先の 1 つずつへ当てる |
| `SPIN_REASON`・`GUARD_RESUME_PROMPT`・`AUTH_RESUME_PROMPT` | `switch_decide.py` | 新設 | `SPIN_REASON`・`GUARD_RESUME_PROMPT` は `SPIN_REASON` は 定数 `str`、`GUARD_RESUME_PROMPT` は 定数 `str`、`AUTH_RESUME_PROMPT` は 定数 `str` | `SPIN_REASON`・`GUARD_RESUME_PROMPT`: `SPIN_REASON`: `起動から間もない区間が続いた（再起動ループ）。次の区間を起動しない`。`GUARD_RESUME_PROMPT`: `利用上限の手前でアカウントを替えた。中断したところから続ける`（従量の接続から戻るときは `利用上限が解けたアカウントへ戻した。中断したところから続ける`）。`AUTH_RESUME_PROMPT`: `認証の失敗でアカウントを替えた。中断したところから続ける` |
| `decide` | `switch_decide.py` | 変更 | 変わらない | 次のセグメントのマーカーの `switch` と文脈の上限の `handover` も、`_spun` が真なら `Step("spin", "<元の理由>。<SPIN_REASON>")` を返す。`Step.kind` に `spin`・`revoked` を足し、`stop` は返さない（ラッパーの `_on_stop` を消す）。最初に `_revoked` を呼び、None でなければそれを返す（次のセグメントのマーカー・上限・認証の失敗・見回り・子の生き死にの判断より先） |
| `SectionState.must_end`・`end_ready`・`drift_pick` | `switch_decide.py` | 新設 | フィールド `bool`・`bool`・`Optional[Pick]`（既定は偽・偽・None） | `must_end` は API キーの子で起動の後の設定の変更（キーの削除・使い方の `off`・宛先・ヘッダー・資格情報の追加）が当たったこと。一度立てばそのセグメントの間は下ろさない。`end_ready` はラッパーの `_end_point` の値（止める条件が揃った）。`drift_pick` は止める条件が揃った見回りでラッパーの `_revoked_pick` が取り直した替え先の判断（`after_auth` の結果に止める直前の確かめを通したもの。`end_ready` が偽の間は None） |
| `_revoked` | `switch_decide.py` | 新設 | `(state: SectionState) -> Optional[Step]` | `must_end` が偽なら None。真なら、`end_ready` が偽のあいだは `Step("wait", "API キーの設定が変わった。止められる時点を待つ")`（上限の `wait_reset`・認証の失敗の `account` を返さない）。`end_ready` が真なら、`drift_pick` が `switch` で `_spun(state, "account", account)` が偽なら `Step("account", reason, AUTH_RESUME_PROMPT, account)`、`switch` で `_spun` が真なら `Step("spin", "<理由>。<SPIN_REASON>")`、それ以外（替え先が無い）は `Step("revoked", reason)`。`account`・`spin`・`revoked` のどれも同じ `end_ready` を通ってから返るので、再起動ループの検出の有無で止める条件が変わらない。`_end_point` が偽の間は区間を終えず、子が 0 以外で終わっても `_restart_or_stop` より先に当たるので同じキーを `restart` で起動し直さない |
| `_limit_or_auth` | `switch_decide.py` | 変更 | 変わらない | `must_end` が真のときは呼ばれない（`decide` が先に `_revoked` を返す）。上限の判断を先に見る（今と同じ）。`switch` なら `Step("account", reason, RESUME_PROMPT, account)` で、回数で `stop` を返さない（最後の砦は回数で止めない）。`wait_reset` なら子を残して待つ。次に `auth_pick` が `switch` なら `Step("account", reason, AUTH_RESUME_PROMPT, account)`、`stalled` で子が生きていれば `Step("stalled", reason)`。`account` を返す場面で `_spun(state, "account", account)` が真なら、代わりに `Step("spin", "<理由>。<SPIN_REASON>")` |
| `_restart_or_stop` | `switch_decide.py` | 変更 | 変わらない | 回数で `stop` を返さない。`_spun(state, "restart", None)` が真なら `Step("spin", "<why>。<SPIN_REASON>")`、偽なら今どおり `Step("restart", why)` |
| `_guard` | `switch_decide.py` | 新設 | `(state: SectionState) -> Optional[Step]` | `decide` の中で、次のセグメントのマーカーの判断の後・子の生き死にの判断の前に呼ぶ。`guard_pick` が `switch` のとき、`safe` なら `Step("account", reason, GUARD_RESUME_PROMPT, account)`（`_spun` が真なら `spin`）、そうでなければ `Step("wait", "閾値を越えた。止めてよい時点を待つ")`。それ以外は None |

#### リレーハブ・設定の読み・契約・手順書（決定 13）

| 対象 | 変更 |
| --- | --- |
| `hub_decide.Lanes`・`restart` | `starts_today`・`max_starts` を消す。`restart` は名前つきのラッパーの数（`max_named`）だけで断る。式は今と同じく、対象を除いた稼働の本数 `others = running − (target_running なら 1、そうでなければ 0)` に起動する 1 本を足し、`others + 1 > max_named` なら断る。上限ちょうどで動いている対象の入れ替えは本数が変わらないので通り、止まっている対象を起動して上限を超えるときだけ断る |
| `hub.Context`・`spawn`・`restart`、`registry.Starts` | `Context.max_starts`・`today` を消す。`spawn` は登録簿の項目に `starts` を書かず、`restart` は読まず増やさず、書き戻す項目から前の版の `starts` を外す。`registry.Starts` と、ラッパーの状態のファイルの `starts`（`_write_state`）を消す。`restart` は `hub_decide.restart` が進めると判断した後、対象を止める（`_await_exit`）前に、後継へ渡す環境 `spawn_env(ctx.environ, ctx.state_dir, name, entry)` を 1 回作り、その環境で `spin_of` を打つ。`spawn_env` は `NDF_RELAY_SPIN` を常に返りの環境に入れる（`environ` に無ければ空文字。名前・役割を足すときの空の値を落とす規則の外にする）。tmux の `new-session` は渡した項目だけを `-e` で上書きするので、項目を落とすと、既存の tmux サーバーのグローバル環境に残る値（不正な値もありうる）を後継が読み、事前の確かめと食い違う。空文字を明示して渡すと、事前の確かめと後継がどちらも空（上限の設定の値）を使う。`EnvError` なら対象を止めずに `Outcome(False, <理由の 1 行>, "precondition")` を返す。通れば同じ環境を `_launch` へ渡し、`_launch` は環境を作り直さない。`spawn` も tmux のセッションを作る前に同じ確かめを打つ。例: `NDF_RELAY_SPIN=abc ndf relay restart lane-a` は `lane-a` を止めずに断る |
| `hub.spin_of` | 新設。`(environ: Mapping[str, str]) -> switch_decide.Spin`。`cli.py` のラッパーの設定の組み立てと `hub.restart`・`spawn` が同じ関数を使う（`cli.py` は `hub` を読むが逆は読まないので `hub.py` に置く）。`count` は `limits.value("relay.spin", "count")`。秒は `NDF_RELAY_SPIN` が無いか空なら `limits.seconds("relay.spin")`、0 以上の数ならその値（0 は検出を切る）、数でない・負なら `limits.EnvError`（今の `from_env` の誤りと同じく、ラッパーを起動せずに理由の 1 行で止める）。ラッパーの設定の組み立てと `hub_context` は `NDF_RELAY_MAX_STARTS` を読まず、設定されていても誤りにしない |
| `contracts/limits.json`・`contracts/domains/env.json` | `contracts/limits.json`: `relay.max-starts` を消し、`"relay.spin": {"summary": "再起動ループの検出。起動から seconds 秒未満で次の区間へ移った区間が count 続いたら次の区間を起動しない（NDF_RELAY_SPIN で秒を替え、0 で切る。#3864）", "count": 3, "seconds": 120}` を足す。`contracts/schemas/io/relay.result.schema.json` は `hub ls` の項目の `starts` を消す `contracts/domains/env.json`: `NDF_RELAY_MAX_STARTS` の行を消し、`{"id": "NDF_RELAY_SPIN", "area": "relay", "set_by": "user", "type": "seconds", "secret": false, "meaning": "再起動ループとみなす区間の秒。0 で検出を切る。無ければ上限の設定の値"}` を足す。`docs/contracts/env.md` は `ndf check --write-spec` で生成し直す（ファイルの先頭に「手で直さない」とある） |
| `relay.md` | 「上限」の表の「子を起動し直す回数」の行を、再起動ループの検出（既定は 3 つ・120 秒、変え方は `relay.spin` と `NDF_RELAY_SPIN`、超えたときは次の区間を起動せず、子が動いていれば中継を続け、子が終わっていればラッパーが子の終了コードで終わる）の行に替える。下の「数えるのは…」の段落を、数える区間（区切り・起動し直し・引継ぎ・続きの中で既に起動した起動先への替え）・数えない替え（初めての起動先）・1 日の上限が無いこと・`ndf relay restart` が名前つきのラッパーの数だけで断ることに替え、`NDF_RELAY_MAX_STARTS` の文を `NDF_RELAY_SPIN` の値の規則（0 以上の数。0 で切る。数でない値と負の値は誤り）に替える。「落ちたときの続け方」の表の最後の行の「起動し直す回数の上限に当たったときも」を「再起動ループの検出で、子が終わった後に止まったときも（終了コードは子の終了コード）」に替え、子が動いている間に出る `SPIN_LINE` の行（状態はセッションが動いたまま、続け方は原因を直してから `/exit` し `ndf relay` で起動し直す）を足す |

### 処理の手順（`advance`）

区間は、子を 1 回起動してから、次の区間を起動する手を決めた時点までである。`kind` の `account` は、見回り・上限の後・認証の失敗の後・設定の変更・従量の接続からの戻りのアカウントを替える手のすべてで、カットポイントの `switch` はアカウントが替わっても区切りとして数える。

| 終わった区間と次の手 | 返る続き |
| --- | --- |
| `limit.seconds` が 0 か、`seconds` が `limit.seconds` 以上（短くない） | `Streak(0, {current, target})`（続きを空にして今の起動先だけにし、次の起動先を足す） |
| 短く、`kind` が `account` で、`target` が `seen` に無い | `Streak(count, seen ∪ {target})`（数えず、続きも切らない） |
| 短く、それ以外（`switch`・`restart`・`handover`、`seen` にある起動先への `account`） | `Streak(count + 1, seen ∪ {target})`（数を足すときも次の起動先を足す。区切り・引継ぎで起動先が変わった後にその起動先へ戻る替えを数えるため） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `SectionState.auth_pick`・`guard_pick` | `SectionState.limit_pick` | ファイルを読んで確かめた | 同じ形のフィールドにする |
| `GUARD_RESUME_PROMPT`・`AUTH_RESUME_PROMPT` | `RESUME_PROMPT` | 同上 | 使えない。文が利用上限に当たって替えたと書く |
| `_guard` の手 | `account`（`_on_resume`） | 同上 | 同じ手を使う（子を止めて同じ会話を `--resume`） |
| `Spin`・`Streak`・`advance`・`hit` | `registry.Starts`・10 系の再起動ループの判定（`docs/specifications/ndf-relay-segment-restart.md` の「上限と止め方」） | ファイルを読んで確かめた | `Starts` は日ごとの回数で使えないので消す。10 系の判定（直前 2 つと今の区間がどれも `NDF_RELAY_SPIN` 未満なら `spin`、1 つ目のセッションも数える）と同じ数と秒にし、初めての起動先への替えを数えない規則だけを足す |
| `hub.spin_of` | `limits.from_env` | ファイルを読んで確かめた | 使えない。0 以下を誤りにするので、0 で切る `NDF_RELAY_SPIN` を読めない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_limit_or_auth` | `auth_pick` が `switch` で `account` の手と `AUTH_RESUME_PROMPT` | 起動の回数の入力は無く、上限の `switch`（`metered` を含む）と認証の失敗の `switch` は `account` の手（`stop`・`wait_reset` にしない）。上限の行と認証の失敗が同時なら上限が先。`spin` が真で替え先が `spin_seen` に無ければ `account`、あれば `spin` | `stalled` で子が終わっていれば今の終わりの判断へ |
| `_guard` | `guard_pick` が `switch` で `safe` なら `account` の手と `GUARD_RESUME_PROMPT` | `safe` が偽なら `wait`・次のセグメントのマーカーがあればカットポイントの判断が先・質問があれば転送が先 | `guard_pick` が `keep` なら None |
| `_revoked`（決定 12） | `must_end` と `end_ready` が真で `drift_pick` が `switch` なら `account`、替え先が無ければ `revoked` | `must_end` が真で `end_ready` が偽なら、上限の `switch`・`wait_reset` や `auth_pick` の `switch` が同時にあっても `wait`。`end_ready` が真で `_spun` が真なら `spin`。`must_end` が真なら次のセグメントのマーカーがあっても `_revoked` が先 | `must_end` が偽なら None |
| `decide`・`_restart_or_stop`（決定 13・I15） | 子が 0 以外で終わると `restart`、マーカーで `switch`、文脈の上限で `handover` | `spin` が真なら 3 つとも `spin`。`spin_held` が真なら初めての起動先への `account` も `spin` | どの入力でも `stop` を返さない |
| `advance`・`hit`（決定 13） | 119 秒・119 秒・119 秒の区間の後の `restart` で数が 3 になり `hit`（1 つ目のセグメントも数える） | 119・121・119 秒では当たらない（長い区間で数え直す）。120 秒ちょうどは短くない。`work1`→`work2`→`work3`→`metered` の 120 秒未満の `account` は数が 0 のまま。`work1`→`work2`→`work1`→`work2`→`work1` の短い `account` は戻る替えごとに 1 足し、3 つ目の戻りで `hit`。`seen` の中身: `Streak(0, {work1})` から 300 秒の `work1` の後に `work2` への `account` で `Streak(0, {work1, work2})`、続けて 60 秒の `work2` の後に `work1` への `account` で `Streak(1, {work1, work2})`（`work1` は `seen` にあるので数える）。短い区切りで `work1` から `work2` に移った `Streak(1, {work1, work2})` から、121 秒の `work2` の後に `work3` への `account` で `Streak(0, {work2, work3})`（旧い集合を捨てる）。`Streak(1, {work1})` から短い `restart`（`work1` のまま）で `Streak(2, {work1})`。続けて短い `switch` で `work2` へ移る手は、`advance` が `Streak(3, {work1, work2})` を返して `hit` が真になる（見回りの `spin` が真で `decide` が `spin` を返し、`work2` を起動しない）。これとは別に初期値から始める例として、`Streak(0, {work1})` から短い `switch` で `work2` へ移ると `Streak(1, {work1, work2})`（数を足す分岐でも次の起動先を足す）。`seconds` が 0 の `Spin` では当たらない | `target_key(None)` が登録の名前の形と重ならない |
| `hub_decide.restart`・`hub.restart`（`test_relay_hub.py`） | 同じ名前を 1 日に 30 回 `restart` しても、名前つきのラッパーの数の上限に当たらなければ進める | 上限 4 本で 4 本が動いているとき、動いている対象の `restart` は進める（対象を除いて 3 本、足して 4 本）。前の版の `starts` を持つ登録簿の項目を `restart` すると、書き戻した項目に `starts` が無い | 上限 4 本で対象を除く 4 本が動き、対象が止まっているとき、対象の `restart` は断る（足して 5 本）。`NDF_RELAY_SPIN=abc` の環境の `restart` は `precondition` で断り、動いている対象を止めない（tmux の `kill` を呼ばない）。同じ環境の `spawn` は tmux のセッションを作らない |
| `hub.spin_of`・`hub.spawn_env`（`test_relay_hub.py`） | `NDF_RELAY_SPIN` が無ければ `Spin(3, 120)`。`NDF_RELAY_SPIN=30` の環境から作ると返りに `NDF_RELAY_SPIN=30` | `0` で `seconds` が 0、`30` で 30。`environ` に `NDF_RELAY_SPIN` が無くても、返りに空文字の `NDF_RELAY_SPIN` があり、偽の tmux の `start` が受ける `-e` に `NDF_RELAY_SPIN=` が載る | `abc`・`-1` で `EnvError`。`NDF_RELAY_MAX_STARTS=abc` があっても誤りにならない |

## NDF の status line（`statusline.sh`）

満たす決定: 決定 4、受け入れ条件 #3864 の 1・2・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_usage` | `statusline.sh` | 新設 | シェル関数（引数無し。`$input` と環境を読む） | `NDF_ACCOUNT_USAGE_FILE` が空でない絶対パスで、入力の `rate_limits` に有効な `five_hour`・`seven_day` の窓（`used_percentage` と `resets_at` が数）が 1 つ以上あれば、そのパスへ書く。事後条件: ファイルの中身は設計文書の「使用率のキャッシュ」の形。合わせ方は詳細設計の「書き込みの規則」と同じ。合わせた `windows` が前と同じなら書かない（`recorded_at` だけの更新はしない）。有効な窓が 0 個なら書かず、前の記録も変えない。0 個とは、`rate_limits` が無い・`spend_limit` だけ・どちらの窓も欠けるときである。`spend_limit` は Claude apps gateway の支出の上限で、利用上限の窓として読まない。副作用: 置き場のディレクトリの作成・`<ファイル>.lock` の錠・`<ファイル>.$$` への書き込み・`mv -f`。すべて標準エラーを捨て、失敗しても表示の処理へ進む。書き先はラッパーが契約（ファイルの表の `relay-usage`）から解いて子の環境の `NDF_ACCOUNT_USAGE_FILE` で渡す。シェルは保存先の規則・名前の検証・世代を持たない。状態の保存先を `NDF_STATE_HOME`・`XDG_STATE_HOME` で替えても、書き手と読み手が同じパスを使う。従量の接続・既定のログインの子と、ラッパーの外の claude はこの変数を持たないので書かない。 |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_usage` | jq が無い・入力が JSON でない・書けない | 環境・権限・ディスク | jq の終了コード・`mkdir`・書き込み・`mv` の失敗 | 何もしない（書きかけの一時ファイルは消す） | 表示は今と同じ。終了コード 0 | 次の更新 |

### 既存の部品の再利用

`record_usage`: 確かめた既存の部品は 同じファイルの窓の大きさの記録（`window_file` を一時ファイルから `mv` で置く処理）（ファイルを読んで確かめた）。結果: 同じ書き方にする。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `record_usage` | 2 つの窓が書かれる | 5 時間の窓が無い・同じ値で書かない・2 つのアカウントを同時に書いて両方残る。同じファイルへ、新しい入力（使用率 100）の後に古い入力（同じ `resets_at` で 40）の処理を遅れて終わらせても 100 が残る | `NDF_ACCOUNT_USAGE_FILE` が無い・`rate_limits` が無い・`spend_limit` だけ・`{}` のとき書かない。置き場に書けない・jq が無い（`PATH` から外す）とき出力が今と同じで終了コード 0 |
| 書き手と読み手のパス | ラッパーを `NDF_STATE_HOME` で既定と別の状態の保存先にして起動すると、子の `NDF_ACCOUNT_USAGE_FILE` がその保存先の `usage/<名前>/<世代>.json` で、status line が書いた値を `accounts.usages` が読む | — | — |

## ラッパー（`wrapper.py`）

満たす決定: 決定 1・3・5〜8・11〜13、受け入れ条件 #3604 の 1・7・8・9、#3864 の 4〜10、不変条件 I15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Target` | `wrapper.py` | 新設 | 型 `(account: Optional[str], config_dir: Optional[Path], generation: str, reason: str, skipped: tuple[str, ...], lease: Optional[str], env: dict[str, str], argv: list[str], resolved: Optional[Resolved], settings_file: Optional[Path])` | 確定した起動先（None は既定のログイン）・用意の済んだ設定ディレクトリ・用意したときの登録の世代・使用の届けの ID・起動の理由（`first`・`cut`・`guard`・`limit`・`auth`・`revoked`）・飛ばした名前・子に渡す最終の環境と最終の起動引数（`account_choice.target_env` の返りそのもの。既定のログインは `child_env(…, account=None, decided=True)` と受けた `argv`）・その組で打った `effective_env` の結果（`Section.resolved` へ控える）・`--settings` の合成で書いた一時ファイル（下の「`--settings` の合成と引用」。子の終わりに消す）。`_start` はこの `env`・`argv` だけで子を起動し、環境と引数を作り直さない。`_launch_target` が`Target` は、登録済みなら `Target(名前, 設定ディレクトリ, 世代, …, env=返りの環境, argv=返りの起動引数, resolved=Prepared.resolved)`、`metered` なら `Target("metered", None, …, env=返りの環境, argv=返りの起動引数, resolved=返りの Resolved)`、`key:<名前>` なら `Target("key:<名前>", None, …, env=返りの環境, argv=返りの起動引数, resolved=返りの Resolved, settings_file=返りの一時ファイル)`（`argv` には `target_env` が `--settings` の `apiKeyHelper` を足してあり、`_start` はこの `argv` で起動する）。`_launch_target` の事後条件: 返りの `account` が登録済みなら `config_dir` は ok の用意の済んだパスで、`generation` はそのときの登録の世代。`metered` なら `_metered` が None でない。None なら既定のログイン。返りの `account` は `choice.names` か `choice.fallback` のどれかで、それ以外の登録済みアカウントを列挙しない |
| `Choice` | `wrapper.py` | 新設 | 型 `(names: tuple[str, ...], fallback: Optional[str])` | 起動の要求。`names` は順に試す名前（登録済みアカウントか `metered`）、`fallback` は尽きたときの起動先（None は既定のログイン）。「未選択」は `names` が空で `fallback` が None、「既定のログインに確定」も同じ形、「登録先の指定」は `names` に名前がある形で、未選択のときに登録済みの候補を列挙しない`_launch_target` が試す前に当てる除外: 名前の種類ごとに当てる（先頭も除外する）。登録済みアカウントは `_candidates` にあり、`usages` で上限でないものだけを残す。`metered` は `_metered` が None でなく、`tried` に無いものだけを残す（上限の観測は登録済みアカウントにしか無いので当てない）。`key:<名前>` は `account_registry.recovery_keys` にその名前があり、`tried` に無いものだけを残す。続けて、残った名前ごとに再起動ループの検出の判定（下の「再起動ループの検出」の「起動先ごとの判定」）を当て、当たった名前も `prepare`・`target_env` を打たずに飛ばす。 |
| `Section.auth_at`・`auth_told`・`inputs`・`resolved`・`drift_at`・`choice`・`input_at` | `wrapper.py` | 新設 | フィールド `Optional[float]`・`bool`・`Optional[Inputs]`・`Optional[Resolved]`・`Optional[float]`・`Optional[Choice]`・`Optional[float]` | 認証の失敗を確かめた時刻・直し方の 1 行を出したか・判断の入力の控え・起動のときの実効値と印・起動の後の変更を検出した時刻・子を止める前に確定した次の起動順・ラッパーが子の端末へ最後に入力（利用者の入力の転送・最初のプロンプト・回答）を送った時刻 |
| `Inputs`・`UsageCache`・`LaunchCache` | `wrapper.py` | 新設 | `Inputs`・`UsageCache` は `Inputs` は 型 `(usage: UsageCache, launch: dict[str, LaunchCache], metered)`、`UsageCache` は 型 `(stamp: tuple, valid_until: float, rows)`、`LaunchCache` は 型 `(stamps: dict[str, Optional[tuple]], result: Optional[str])` | `Inputs`・`UsageCache`: `Inputs`: 判断の入力。使用率の控え（`UsageCache`）と、名前ごとの起動できるかの控え（`LaunchCache`）を別々に持ち、別々に失効させる。`UsageCache`: 使用率と上限の観測を重ねた行。印は、登録済みアカウントごとに実際に読む世代のファイル `usage/<名前>/<世代>.json`、それに `held.json`・`limited.json` の `(st_mtime_ns, st_ino, st_size)`（無ければ None）。`valid_until` は窓のリセット・外した間と上限の観測の終わりの最小。`usage/` ディレクトリ自体の更新時刻は入れ子のファイルの置き換えで変わらないので使わない。`LaunchCache`: 1 つの名前の判定の控え。`ready`（登録済みアカウント）か `fallbacks`（`metered`・`key:<名前>`）が返した `Probe` の `stamps` と `reason` をそのまま持つ（None は起動できる、文字列は理由の符号）。成功した名前も落ちた名前も同じく控える。印の中身は返した側が決める（`ready` は「関数の仕様」の `ready` の行、`fallbacks` は `metered.json`・`api-keys.json` と `verify` の `Resolved.stamps`）。ラッパーは印の比べに `os.stat`（`lstat` を含む）だけを打ち、源のファイルを開かない |
| `_inputs` | `wrapper.py` | 新設 | `(self, sec: Section, now: float, need_candidates: bool) -> Inputs` | 使用率の控えは、印が同じで `now < valid_until` なら使い、使用率の中身を開かない。違えば使用率だけを読み直す（`ready` を打たない）。起動できるかの控えは、`need_candidates` が真のとき（判断が候補を要るとき。下の「見回りの読み取り」）だけ名前ごとに印を `stat` で比べ、変わった名前にだけ `ready`（従量の接続と API キーは `verify`）を打ち直す。落ちた名前も同じく印で見るので、上限待ちの途中で別のアカウントの共有リンクや `settings.json` を直すと、次の判断でその名前が候補に戻る。止める直前には、選んだ 1 つの候補へ `ready` をもう一度打つ（下の「子を止める前の替え先の確定」） |
| `_candidates`・`_fallbacks` | `wrapper.py` | 新設 | `_candidates` は `(self, now: float) -> list[str]`、`_fallbacks` は `(self, now: float) -> list[str]` | `_candidates`: 登録済みアカウントのうち、再登録が要らず、外した間でなく、`account_dir.ready` の `Probe.reason`（`LaunchCache.result`）が None（起動できる）ものの名前。上限のものは `order` が除く。`_fallbacks`: `account_choice.fallbacks(store, environ, self._metered(), cwd, 子の argv, self.tried)` を呼び、返った `Probe` をすべて名前ごとの `LaunchCache` に控える。返すのは `reason` が None の名前で、`metered`、続けて `account_registry.recovery_keys` の `key:<名前>` の順である。控えの印が変わらない名前には `fallbacks` を打ち直さない（`_inputs` と同じ規則） |
| `_metered` | `wrapper.py` | 新設 | `(self) -> Optional[tuple[dict[str, str], bool]]` | 従量の接続の設定（変数の組と、保存した設定か）。無い・読めない・検証に外れれば None |
| `_pinned_or` | `wrapper.py` | 新設 | `(self, account: Optional[str], reason: str, current: Optional[str], now: float) -> Optional[str]` | 起動の理由が `cut`・`guard`・`limit`・`auth`・`revoked` のときだけ当てる（`first` は `_initial`）。指定があり、候補にあり、`usages`（上限の観測を重ねたもの）で上限でなく、理由が `guard`・`auth`・`revoked` なら今のアカウントでなく、さらに「組 0〜2 にある」か「指定を除いた `order` に組 0〜2 の候補が無い」なら指定、そうでなければ `account`。指定が無い・壊れているときも `account`（壊れた指定は今と同じく `AccountError` で止まる）。候補に無いとは、再登録が要る・外した間・登録が無い・用意できないこと。理由が `guard`・`auth` で指定が今のアカウントなら同じ認証で起動し直さない。例: 指定が 5 時間 86%（組 3）で、ほかに 10% の候補があれば、その候補 |
| `_initial` | `wrapper.py` | 新設 | `(self, now: float) -> Choice` | 最初のセグメントの要求。環境変数 `NDF_CLAUDE_ACCOUNT` の名前 → 指定の名前の順に、`_candidates` にあり上限でない名前だけを `names` に入れる（環境変数の名前が使えなければ指定を読む）。`fallback` は None |
| `_launch_target` | `wrapper.py` | 新設 | `(self, choice: Choice, reason: str, now: float, argv: list[str]) -> Optional[Target]` | `choice.names` を順に試す（同じ名前を 2 回試さない。`fallback` の 1 回を除く）。先に、`Choice` の行の除外を当てる。除外に当たった名前は `prepare`・`target_env` を打たずに 1 行を出して次へ進む。照合（`verify`）はここで打たず、`target_env` の中の 1 回に任せる（`_fallbacks` と同じ条件で、`_candidates` を `metered`・`key:` に当てない）。残った名前はどれも `account_choice.target_env(child_env(…, account=名前, decided=True), 名前, store, state_dir, cwd, argv)`（`argv` は子に渡す実際の起動引数、`cwd` は子の作業ディレクトリ）が `Built` を返したものを、その返りの環境・起動引数・`Prepared` を詰めた `Target`（上の `Target` の行）で返す。理由の符号が返れば（登録済みは中の `prepare` が使えない）、その符号の文で 1 行を出して次へ進む（一時ファイルは `target_env` が消してある）。尽きたら `choice.fallback`。理由が `first`・`cut` で `fallback` が登録済みならもう一度 `prepare` し、落ちれば既定のログイン。理由が `limit`・`auth` なら `fallback`（今のアカウント）の `prepare` が落ちれば None（起動しない。既定のログインへ落とさない）。理由が `revoked` なら `fallback` を試さず、`names` が尽きれば None（止めたキーにも既定のログインにも落とさない）。`fallback` にも同じ判定を当てる（既定のログインへの `fallback` も `DEFAULT_TARGET` で当てる）。飛ばした名前のうち 1 つ以上が判定で飛び、ほかに起動できる起動先が無ければ、None の代わりに定数 `SPUN` を返す。事前条件: 子が止まっている（呼ぶのは `_start` だけ。上限・認証の失敗の後の替え先は「子を止める前の替え先の確定」で決めてある）。副作用: `account_dir.prepare`（symlink・同期・印・使用の届け）と、飛ばした名前ごとの画面の 1 行と `log.jsonl` の `account_dir` の行 |
| `_auth_pick` | `wrapper.py` | 新設 | `(self, sec: Section, screen: str) -> Optional[Pick]` | 認証の失敗を確かめた（下の「認証の失敗の確かめ」）最初の見回りで、今の登録済みアカウントを `accounts.hold` し、`after_auth` を返す。副作用: `accounts.hold`（`until = now + limits.seconds("relay.auth-hold")`、今の世代）。替え先が無く直し方の 1 行をまだ出していなければ出して `auth_told` を立てる（同じセグメントで 1 回）。直し方の 1 行の文（`AUTH_FIX`）は、既定のログインなら `claude の中で /login を打つ`、登録済みアカウントなら `この子を /exit で終えてから ndf relay account add <名前> で登録し直す`（子が動いている間は登録のコマンドが止まる）、従量の接続なら `ndf relay account check metered で設定を確かめる` |
| `_drift` | `wrapper.py` | 新設 | `(self, sec: Section) -> Optional[Pick]` | 起動の後の設定の変更の検出（`account_dir` の「起動の後の設定の変更」）。`Section.resolved`（起動のときの `Resolved`）の印を `stat` で比べ（API キーの子は `api-keys.json` の印も）、変わったときだけ確かめ直す。当たれば `Section.drift_at` を立てて `after_auth` の結果を返す（`hold` しない）。`_auth_pick` の結果によらず毎回呼ぶ（認証の失敗の行が出ていても設定の変更の検査を省かない）。今が `key:<名前>` で当たったときは、替え先の有無によらず `Section.must_end` と `must_end_at` を立て（セグメントの間は下ろさない）、当たった項目に宛先・ヘッダー・資格情報の追加があれば `end_now` も立て、その時点で子の端末への入力の転送を止める（下の「API キーの子の停止」）。キーの子では、この返りを替え先に使わない（替え先は止める時点で `_revoked_pick` が取り直す。`_drift` は印が変わった見回りでしか結果を返さないため）。`_state` はほかの子の結果を `auth_pick` に入れる |
| `_revoked_pick` | `wrapper.py` | 新設 | `(self, sec: Section, now: float) -> Optional[Pick]` | `must_end` の子の替え先。止める条件が揃った見回りで、`_inputs(sec, now, need_candidates=True)` から `after_auth` を取り直し、起動の理由 `revoked` の行（下の「起動の理由ごとの起動順」）で `Choice` を作って、下の「子を止める前の替え先の確定」の (1)〜(3) を通す。通った `Choice` を `Section.choice` に控え、先頭を替え先にした `switch` を返す。(3) で外れたときは子を止めない代わりに作り直し、`names` が空になれば None を返す（`_revoked` が `revoked` を返して子を止める。替え先の確かめの失敗で子を残さない）。(2) で除いて空になったときは、除く前の先頭を替え先にした `switch` を返す（`_revoked` の `_spun` が真になり `spin`） |
| `_settle`・`_tell_at_start` | `wrapper.py` | 新設 | `_settle` は `(self, sec: Section) -> None`、`_tell_at_start` は `(self) -> None` | `_settle`: 終わった子のアカウントが登録済みなら `account_dir.settle` を打ち、失敗の理由を 1 行出す。`_start` が前のセグメントがあれば、`_end`（変更）が今のセグメントがあれば今の止まり方の前に打つ。`_tell_at_start`: 再登録が要るアカウントを 1 つにつき 1 行、従量の接続の設定が壊れていれば 1 行出す（画面だけ。トークン・メールアドレス・パスは出さない）。`_loop`（変更）が最初のセグメントの前に呼ぶ |
| `_state` | `wrapper.py` | 変更 | 変わらない | `drift = self._drift(sec)` と `auth = self._auth_pick(sec, screen)` を別々に打つ（片方の結果で他方を省かない）。`auth_failed` の代わりに、キーの子では `auth_pick=auth`、ほかの子では `auth_pick=auth or drift` を渡す。`must_end=sec.must_end`・`end_ready=self._end_point(sec)`（`must_end` が偽なら打たずに偽）を渡す。`drift_pick` は、`end_ready` が真の見回りでだけ `self._revoked_pick(sec, now)` を打って渡し、それ以外は None（`_revoked` は `end_ready` が偽の間 `drift_pick` を読まない）。`guard_pick=self._guard_pick(sec, now)`・`safe=self._safe_point(sec)` を渡す。`starts_today`・`max_starts` を渡さず、再起動ループの検出の `spin`・`spin_seen`・`spin_held` を渡す。替えの手の替え先は、子を止める前に確定した `Choice` の先頭を入れる（下の「再起動ループの検出」の「起動先ごとの判定」） |
| `_limit_pick` | `wrapper.py` | 変更 | 変わらない | 上限の行を初めて見た見回りで、今が登録済みアカウントなら `accounts.limit_until(上限の行, 今のアカウントの窓, now, 地域の時間帯)` で `until`・窓の名前・`window_resets_at` を決めて `accounts.see_limit` を書く。`_inputs` の使用率・候補・従量の接続を渡して `after_limit` を呼ぶ。ファイルの更新時刻による補正は消す（上限の観測が持つ）。画面に上限の行が無くても、今のアカウントのキャッシュの窓が 100 以上で、子の出力が `relay.quiet` の秒止まっていれば、上限の行を見たときと同じく上限の観測を書いて `after_limit` を呼ぶ（行の文言が変わっても最後の砦を働かせる。決定 8） |
| `_on_switch` | `wrapper.py` | 変更 | 変わらない | `before_limit` に候補と `Guards` を渡し、下の「起動の理由ごとの起動順」の `cut` の行で `Choice` を作る。`keep` のとき、今の起動先が既定のログイン（None）なら既定のログインのまま（登録済みアカウントへ移さない）。今が登録済みアカウントで候補に無い（外した間・再登録が要る・用意できない）ときだけ `order` の先頭へ替える |
| `_on_resume` | `wrapper.py` | 変更 | 変わらない | 手が `account` のときは、子を止める前に確定して `Section.choice` に控えた `Choice`（下の「起動の理由ごとの起動順」）をそのまま使う。`Section.must_end` が真なら起動の理由を `revoked` にして `_launch_target` へ渡す（尽きたら `Ended("account_unavailable")`）。`_pinned_or`・`order` を組み直さない。起動の回数を数えない（`_count_start` を消す）。`restart` は今のアカウントのまま起動し直す |
| `_start` | `wrapper.py` | 変更 | 変わらない | 前のセグメントがあれば `_settle`。`_launch_target` が None なら子を起動せずに `Ended("account_unavailable", 理由)` を返す。`SPUN` なら子を起動せずに `Ended("spin", "<理由>。<SPIN_REASON>")` を返す（`_end` が `end` の行に `cause: spin` を書く）（会話を送らない。会話の記録は共有側の `projects` に残り、直した後のラッパーの起動で同じ会話を `--resume` で続けられる）。`_launch_target(req.choice, req.reason, now)` に子の起動引数（`--resume` などを足した後）を渡し、確定した `Target` の `env`・`argv`・作業ディレクトリでそのまま子を起動する（環境を作り直さず、起動先の種類で分けない。照合した組と起動した組が同じになる）。登録済みなら上の「起動の関所」で起動し、`account_dir.attach(store, name, target.lease, 子の pid)` が通ってから関所を開ける。`Section.account`・`section` の行の `account`・`account` の行（下の「ログと記録の出力」）を同じ `Target` から 1 回だけ書き、`Section.resolved` に `target.resolved` を控える。子の起動が成功したら `Section.started_at` を書き、続きを進める（下の「再起動ループの検出」）。子の起動か `attach` に失敗したら子を回収して `account_dir.release(store, name, target.lease)` を打ち、`target.settings_file` を消す。子の終わり（`_settle`・`_end`）でも `target.settings_file` を消す |
| `_guard_pick`・`_safe_point` | `wrapper.py` | 新設 | `_guard_pick` は `(self, sec: Section, now: float) -> Optional[Pick]`、`_safe_point` は `(self, sec: Section) -> bool` | `_guard_pick`: 上限の行も認証の失敗も無い見回りで、`_inputs` の使用率・候補・`Guards` で `account_decide.guard` を呼ぶ。今が組 3 で `guard` が組 0〜2 の替え先を返したときだけ `switch` で、`_safe_point` が真になるまで子を止めない。`switch` なら子を止める前の確定（下）と同じく替え先へ `ready` を打ち直す。`_inputs` の印が変わらず `valid_until` 前なら控えの判断を返す。`_safe_point`: 次がすべて成り立つときだけ真。子の出力が `relay.quiet`（5 秒）止まっている。回答待ちの質問が無い。`observe.turn_done(会話の記録)` が真（最後の記録が応答の終わり）。その応答の終わりの記録の時刻が、ラッパーが子の端末へ最後に入力を送った時刻（`Section.input_at`）より後。`turn_done` が None（読めない）なら偽 |
| `_end_point`・`Section.must_end`・`end_now`・`must_end_at` | `wrapper.py` | 新設 | `_end_point` は `(self, sec: Section) -> bool`、`Section.must_end`・`end_now`・`must_end_at` は フィールド `bool`・`bool`・`Optional[float]`（既定は偽・偽・None） | `_end_point`: API キーの子を止める条件（`Section.must_end` が真のときだけ打つ）。次のどれかで真。`Section.end_now` が真（宛先・ヘッダー・資格情報の追加。待たない）。子が終わっている。`_safe_point(sec)` が真。`clock() − Section.must_end_at` が `relay.end-wait`（60 秒）以上（進行中と区別できない場面も含め、子の状態によらず待ちを終える）。子の出力が `relay.quiet` 止まっていて、さらに次のどれかが成り立つ: 会話の記録の最後が `Section.input_at` より後の API の失敗の記録（`isApiErrorMessage` が真の `assistant` の記録。キーの `off` の直後の要求が認証の失敗で終わり、子が入力待ちに戻った場面）、回答待ちの質問がある（入力を転送しないので答えが届かない）、`observe.tool_pending` が真（道具の実行の許可待ち。入力を転送しないので答えが届かない）、`turn_done` が `(False, None)` で会話の記録の `(st_size, st_mtime_ns)` が `relay.quiet` の間変わっていない（利用者が Esc で応答を中断して入力待ちに戻った場面。最後の記録は `user` か終了理由の無い `assistant`）、`turn_done` が None（記録が読めない）。どれでもなければ、子は応答の途中なので待つ。待ちは `relay.end-wait` で必ず終わる。キーの削除・`off` ではキーは変更前の宛先にしか送られないので、この待ちは許していない宛先へ送る経路にならない。`Section.must_end`・`end_now`・`must_end_at`: `must_end` は `_drift` がキーの子で当たったこと。真の間、ラッパーは利用者の入力・最初のプロンプト・質問の回答を子の端末へ送らない（下の「API キーの子の停止」）。`end_now` は当たった変更に宛先・ヘッダー・資格情報の追加が含まれること（止める条件を待たない）。`must_end_at` は `must_end` を立てた時刻（`clock`。`relay.end-wait` の起点） |
| `Section.started_at`・`ended_at`・`Wrapper.streak`・`spin_held`・`spin_told`、`Settings.spin` | `wrapper.py` | 新設 | `Section.started_at`・`ended_at` は フィールド `float`・`Optional[float]`、`Wrapper.streak`・`spin_held`・`spin_told`、`Settings.spin` は `Streak`・`bool`・`bool`・`Spin` | `Section.started_at`・`ended_at`: 子を起動した時刻と、次の区間を起動する手を初めて決めた時刻（どちらも `clock`。区間の始まりと終わり）。`Wrapper.streak`・`spin_held`・`spin_told`、`Settings.spin`: 短い区間の続き（メモリだけ）・検出に当たった後の保持（今の区間が `spin.seconds` に達したら下ろす）・`SPIN_LINE` を出したか・`hub.spin_of` の値。`Settings.max_starts`・`Wrapper.starts`・`_count_start`・`_starts_today`・`WallClock.today` を消す |
| `_act` | `wrapper.py` | 変更 | 変わらない | 手が `switch`・`restart`・`handover`・`account` で `Section.ended_at` が None なら、今の時刻を書いてから動きを打つ（子を止める待ちは区間に入れない） |
| `_on_spin`・`_on_revoked` | `wrapper.py` | 新設 | `_on_spin` は `(self, step: Step, sec: Section) -> Optional[Ended]`、`_on_revoked` は `(self, step: Step, sec: Section) -> Optional[Ended]` | `_on_spin`: `_ACTIONS` の `spin`（`stop` と `_on_stop` を外す）。下の「再起動ループの検出」の表の `spin` の行。`_on_revoked`: `_ACTIONS` の `revoked`。子が生きていれば止め（`_on_resume` と同じ止め方。下の「API キーの子の停止」の止め方）、後継を起動せずに `Ended("account_unavailable", "<理由>。会話を送らずに止めた。直してから ndf relay で続ける")` を返す。子が終わっていても同じ（今のキーで起動し直さない） |
| `Ended`・`_write_state` | `wrapper.py` | 変更 | `Ended` は 型に `code: Optional[int]`（既定は None）を足す、`_write_state` は 変わらない | `Ended`: 止まったときの終了コード。`cli._ended` は 1 行を出した後、`code` が None でなければそれを、None なら停止理由の種類の終了コードを返す。`_write_state`: 状態のファイルに `starts` を書かない |

#### 子を止める前の替え先の確定

上限と認証の失敗の替え先は、子を止める前に決める。判断の関数へ渡す `candidates` は、`_inputs` が `account_dir.ready` で確かめた名前だけである。控えを読んだ後に設定・認証・リンクが変わっても、控えの印は変わらない。そのため、`switch` の替え先が登録済みアカウントなら、子を止める直前にその 1 つへ `ready` をもう一度打つ。`metered` なら、従量の実効の設定の確かめをもう一度当てる。外れればその名前を候補から外した控えで判断をやり直し、子を止めない。`ready` は認証の上書きも、`link_shared` の後の `projects`・`settings.json` も確かめる。そのため、止める時点で起動できると分かっている。止めた後の `prepare` が競合（ほかのレーンの再登録・排他の待ち切れ）で落ちたときの扱いは、起動の理由ごとに決める。起動の理由ごとの起動順: 次の表で `Choice` を作り、`Section.choice` に控える。手順は (1) 表で `names` を作る、(2) `names` から再起動ループの検出で当たる名前を除く（「再起動ループの検出」の「起動先ごとの判定」）、(3) 残った先頭へ止める直前の `ready` を打つ、の順である。(3) で外れれば、その名前を候補から外した控えで (1) から作り直し、作り直した `names` にも (2) を当ててから、その先頭へ (3) を打つ。除いた後の先頭だけを確かめ、除く前の先頭（当たって起動しない名前）へは打たない。例: 続きの数が 2 で、`names` が `(work1, work3)`、`work1` が `seen` にあり `work3` が無いとき、(2) で `work1` を除き、(3) は `work3` へ打つ。`work3` が控えの後に使えなくなっていれば (3) で外れ、子を止めずに作り直す。残りが空になれば `spin` が返り、子を止めない。子を止めた後の `_on_resume`・`_on_switch` は控えた `Choice` をそのまま `_launch_target` へ渡す。`P` は `_pinned_or(判断の結果, 理由, 今のアカウント)`、`order` は判断に使った控えの `order(今のアカウント, …)`（今を除く）である。

| 起動の理由 | 今のアカウントの扱い | `Choice` の `names` | 尽きたときの `fallback` |
| --- | --- | --- | --- |
| `first`（最初） | — | 環境変数の名前 → 指定（使えるものだけ。`_initial`） | 既定のログイン。名前が 1 つも無ければ登録済みの候補を試さずに既定のログイン |
| `cut`（カットポイント） | `keep` なら今の起動先（既定のログインなら `names` は空）。`switch` なら除く | `keep`: `(今,)`。`switch`: `P` → `order` の残り | 既定のログイン |
| `guard`（見回りの替え・従量の接続からの戻り） | 除く | `P` → `order` の残り（閾値の内側のものだけ。戻りは上限でないものすべて） | 今のアカウント（替え先の `prepare` が落ちたら替えずに同じ会話を続け、次の見回りでもう一度判断する） |
| `limit`（上限の後） | 上限が解けて `after_limit` が同じアカウントを返したときだけ先頭。それ以外は除く | 同じアカウント、または `P` → `order` の残り → `_fallbacks`（今の種類と `tried` を除く） | 今のアカウント（同じ会話を同じ認証で `--resume` し、次の上限の行で `after_limit` が `wait_reset` を返して、最も早く解けるアカウントを待つ）。今のアカウントの `prepare` も落ちれば、既定のログインへは落とさず、起動しないで止める |
| `auth`（認証の失敗の後） | 常に除く（登録済みは外した間、従量の接続と API キーは `tried`） | `P` → `order` の残り → `_fallbacks`（同上） | 今のアカウント（認証の失敗の行がもう一度出て `stalled` になり、子を残して直し方を出す）。今のアカウントの `prepare` も落ちれば同上 |
| `revoked`（API キーの子の停止。`_revoked_pick`） | 常に除く（今のキーを `tried` に入れる。止める待ちの間に `off` から戻しても、同じキーで起動し直さない） | `P` → `order` の残り → `_fallbacks`（今のキーを除く） | 無し。`names` が尽きれば `_launch_target` が None を返し、`_start` が `Ended("account_unavailable", 理由)` で終える（止めたキーにも既定のログインにも落とさない） |

`wait_reset`・`stalled` の手では子を止めないので、`_launch_target` を呼ばない。`limit`・`auth` で今のアカウントも用意できないときは、別の認証で会話を送らないことを優先する。`_launch_target` が None を返し、`_start` が `Ended("account_unavailable", 理由)` で終える。画面に 1 行を出し、`log.jsonl` の `end` の行に理由の符号を出す。画面の 1 行は `ndf-relay: 替え先と今のアカウント <名前> のどちらも用意できない（<理由の文>）。会話を送らずに止めた。直してから ndf relay で続ける` である。今のアカウントが既定のログインなら `prepare` は無いので、このときは今どおり既定のログインで起動する。今のアカウントが `metered` で設定が使えなくなっていれば、同じく止める。

#### 認証の失敗の確かめ

画面の文字列（`observe.auth_failed`）は引き金だけに使い、それだけで外した間の記録・子の停止・替え先の起動をしない。次をすべて満たしたときに、実行中の claude 自体の認証の失敗と確かめる。

| 条件 | 根拠 |
| --- | --- |
| 画面の末尾（最後の入力欄より後）に `AUTH_FAILED` の行がある | 引き金。入力欄より前の行（利用者の入力のエコー・応答の引用・古い表示）は見ない |
| 子の会話の記録（`observe.transcript_path`）の最後の記録が、claude が書く API の失敗の記録（`isApiErrorMessage` が真の `assistant` の記録）で、資格情報そのものの失敗を示す（下の `CREDENTIAL_FAILURE`） | モデルの応答や利用者の入力に同じ文字列があっても、その記録は API の失敗の記録ではない |
| その記録の時刻がセグメントの起動より後で、その後に利用者の記録が無い | 前のセグメントの失敗や、直した後の古い表示で動かない |

`CREDENTIAL_FAILURE`: 既存の `AUTH_FAILED` は HTTP 403 も拾う。403 には、認証は通った資格情報に対するモデル・組織の権限の制限（API の `permission_error`）が含まれる。これをログインの失敗として扱うと、使えるアカウントを 1 時間外し、別のアカウントや従量の接続へ会話を送る。そのため、記録の本文が次のどれかに当たるときだけ資格情報の失敗とする。画面の `AUTH_FAILED` は引き金のままとし、403 の行でも会話の記録を読む。記録が 403 の権限の制限なら何もしない。確かめられない（記録が読めない・形が違う）ときも何もしない（今の `stalled` も出さない）。`isApiErrorMessage` の名前と形、401 と 403 の記録の本文の形は、実装の PR で手元の会話の記録から確かめる。違えば実装を止めて設計へ戻す（「未確認のまま残ること」）。

| 当たる | 当たらない |
| --- | --- |
| HTTP 401、または API の失敗の型が `authentication_error` | HTTP 403 の `permission_error`（モデルや組織の権限の制限。替えずに今どおり画面に残す） |
| 本文が資格情報の失効・取り消しを示す（`OAuth token has expired`・`OAuth token revoked`・`invalid x-api-key`・`Please run /login`） | 本文に `403` だけがあり、上の型・文が無い |

#### 再起動ループの検出

規則は「次の手の判断」の節の `advance` の処理の手順と設計文書の決定 13 にある。続き（`Wrapper.streak`）はラッパーのプロセスごとにメモリだけに持ち、リレーハブの `restart` で起動し直したラッパーは空から始まる。待つ手（`wait`・`wait_reset`・`stalled`）は区間を終えないので、待った時間も区間の長さに入る。`今` は `target_key(Section.account)` である。

| 時点 | すること |
| --- | --- |
| 最初の `_start` の成功 | `streak = Streak(0, {target_key(Target.account)})`（1 つ目のセグメントも数える） |
| 見回りごとの `_state`（と保持の解除） | `spin = hit(advance(streak, (ended_at が有ればそれ、無ければ clock()) − started_at, "restart", 今, 今, spin), spin)`・`spin_seen = streak.seen`・`spin_held` を渡す。続きは進めない。保持の解除: `spin_held` が真で、今の区間の長さ（`clock() − started_at`）が `spin.seconds` 以上になったら `spin_held` を下ろし、`spin_told` も下ろす。今の区間はもう短い区間にならず、次の区間の起動で `advance` が続きを空にするためである。下ろした後は、上限の後・認証の失敗の後・見回りの替え・区切りが通常どおり働く（上限の画面のまま残る子も、守り 3 が `spin.seconds`（既定 120 秒）の内に替える） |
| 起動先ごとの判定（子を止める前と、止めた後の `_launch_target`） | 起動先 `n` で次の区間を起動すると当たるかを、`_spun(state, kind, n)` と同じ規則で決める。`state` の `spin`・`spin_seen`・`spin_held` は今の見回りの値（止めた後は `ended_at − started_at` で求め直した値）、`kind` は手の種類（`account` か `restart`）。子を止める前は、`Choice` の `names` から当たる名前を除いた後に、残った先頭へ止める直前の `ready` を打ち（上の「子を止める前の替え先の確定」の (1)〜(3)。作り直しも同じ順）、通った先頭を `Step.account` に入れて `decide` を打つ。残りが空なら判断の関数の替え先を入れたまま打つので `_spun` が真になり `spin` が返る（子を止めない）。子を止めた後は、`_launch_target` が除外の後に名前ごと・`fallback` に同じ判定を当てる。例: 続きが `Streak(2, {work1, work2})` で `work3` への替えを許したが、止めた後に `work3` の `prepare` が競合で落ち、`fallback` の `work1` へ移ると、`work1` は `seen` にあるので当たり、起動せずに `SPUN` になる。逆に、判断の関数の替え先が `seen` にある `work1` でも、`_pinned_or` の指定が `seen` に無い `work3` を先頭にすれば当たらず、`work3` で起動する |
| 次の区間の `_start` の成功 | `streak = advance(streak, ended_at − started_at, 手の種類, 前の区間の起動先, target_key(Target.account), spin)`。`_launch_target` が `Choice` の先頭と違う起動先へ移っても、実際の起動先で数える |
| `spin` の手（`_on_spin`） | `Section.must_end` が真なら、この手は `_revoked` が `_end_point` の揃った後にだけ返す（下の「API キーの子の停止」）。子を残さず、その場で子を止め、後継を起動せずに `Ended("spin", "<理由>。<SPIN_REASON>")` を返す（決定 12 の、許していない宛先へ送り続けない規則を、再起動ループの検出より先にする）。それ以外で子が生きていれば `spin_held` を立て、最初の 1 回だけ画面に `SPIN_LINE`（`ndf-relay: 起動から <秒> 秒未満で次の区間へ移った区間が <数> つ続いた（再起動ループ）。次の区間を起動しない。原因を直してから /exit し、ndf relay で起動し直す`。`_show_halt` と同じ書き方）と、`log.jsonl` に `{"kind": "stop", "cause": "spin", "reason"}` を書いて、子を止めずに中継を続ける（1 回だけかは `spin_told` で見る。保持を下ろすと `spin_told` も下ろす）。子が終わっていれば `Ended("spin", 理由, code=子の終了コード)` を返し、`_end` が `end` の行に `cause: spin` を書く |

#### API キーの子の停止

API キーの子（`key:<名前>`）で `_drift` が当たる（キーの削除・使い方の `off`・宛先・ヘッダー・資格情報の追加）と、次の順に進める。上限・認証の失敗・再起動ループの検出・次のセグメントのマーカーのどれと重なっても、この順を先にする（決定 12・13。下の表の後の見回りは `_revoked`・`_end_point` の行が持つ。`key:key-main` の子で `ndf relay account api-key-use off` を打った直後の例は、ラッパーの単体テストの観点の決定 12 の行が持つ）。

| 時点 | すること |
| --- | --- |
| 当たった見回り | `Section.must_end`・`must_end_at` を立て、宛先・ヘッダー・資格情報の追加なら `end_now` も立てる（`end_now` ならこの見回りで `_end_point` が真になり、下の `_end_point` が真の行へ進む）。以後このセグメントの間、利用者の入力・最初のプロンプト・質問の回答を子の端末へ送らない。ラッパーが控えている未送の入力も送らずに捨て、画面に 1 回 `ndf-relay: API キーの設定が変わったので入力を子へ送らない。子が止まるのを待つ（入力は替えた後に打ち直す）` を出す。捨てた入力は後継にも渡さない（変更後の宛先へ会話を送る経路を残さない） |
| `_end_point` が真 | `_revoked_pick` がその見回りで替え先を取り直し（起動の理由 `revoked`。今のキーは除く）、`_revoked` が 1 つの手を返す。替え先があり再起動ループの検出に当たらなければ `account`（子を止めて替え先で `--resume`）、当たれば `spin`（`_on_spin` が子を止めて `Ended("spin")`）、替え先が無ければ `revoked`（`_on_revoked` が子を止めて `Ended("account_unavailable")`）。3 つとも同じ `_end_point` を通る。止め方は 3 つとも同じで、`_stop_if_clear` の `held`（ロックを取れない・質問のファイルが在る）で見送らず、`_stop_child` で子を止める（`must_end` の間は答えが届かないので、質問を残しても待ちが終わらない）。道具の実行の途中で止めた場合も、会話の記録に残った分から `--resume` で続ける |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_launch_target` | 用意の失敗・登録の保存先が読めない | `account_dir` の理由。`AccountError`・`OSError` | `prepare` の返り・`names`・`load` | 次の候補へ。保存先が読めなければ既定値で続ける（候補を空として既定のログインへ。理由が `limit`・`auth` で今が登録済みアカウントなら None） | 画面に 1 行（`ndf-relay: アカウント <名前> で起動しない（<理由の文>）。次の候補で起動する`）と `log.jsonl` の `account_dir` の行。保存先が読めなければ`log.jsonl` に 1 行 | 次のセグメントで再び試す |
| `_start` | `account_unavailable` | 理由が `limit`・`auth` で、替え先も今のアカウントも用意できない | `_launch_target` の None | 子を起動せずに止める | 画面に 1 行と `log.jsonl` の `end` の行。ラッパーは停止の終了コード | 直してからラッパーを起動し直せば、同じ会話を `--resume` で続ける |
| `_settle` | 書き戻しの失敗 | `account_dir.settle` の理由 | 返り | 既定値で続ける | 画面に 1 行と `log.jsonl` に 1 行 | 次の起動の前の同期 |

### 処理の手順

- 見回りの読み取り: 見回り（`relay.tick` の 0.5 秒ごと）のたびに取るのは、今のアカウントの使用率の控えの印（`stat` だけ）である。起動できるかの控えの印は、判断が候補を要るときだけ取る。要るのは、今が使い切りの組か従量の接続にあるとき・上限か認証の失敗を確かめたとき・カットポイント・`wait_reset` で待つ間（使用率の控えが変わったときと `valid_until` を過ぎたとき）である。使用率の更新は使用率の控えだけを失効させ、全候補の認証と設定を読み直さない。ファイルを開く回数は、使用率では status line の書き込みと解除の時刻の数に、起動できるかでは利用者がその名前の設定・認証・リンクを変えた回数に比例する。見回りの回数とレーン数・登録数の積には比例しない（`stat` の回数は、判断が候補を要る見回りでレーン数 × 登録数 × 印のファイルの数）

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_launch_target` | `{"kind": "account_dir", "account", "reason", "local_only", "skipped", "added"}` （`prepare` の理由の符号・`local_only` と `skipped` の名前・`added` の数。`_settle` も `settle` の理由の符号を同じ行で書く） | `log.jsonl` | トークン・メールアドレス・`.claude.json` の値・参照先のパス |
| `_start` | 子の起動が成功した後に 1 回、`{"kind": "account", "from": 前のセグメントのアカウント, "account": Target.account, "reason": Target.reason, "skipped": Target.skipped}`。前と同じアカウントで起動したときも書く（受け入れ条件はこの行の `from` と `account` の違いで切り替えを見る） | `log.jsonl` | 同上 |
| `_auth_pick`・`_limit_pick`・`_on_switch` | `account` の行を書かない（判断は起動ではない。回答待ちのガードで子を止めなかったとき・起動に失敗したときに切り替えたと記録しないため） | — | — |

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
| `_start`・`_launch_target` | 指定の `work2` で子の環境が `account_env` の形（偽の起動で環境を受け取る）。`work2`・`metered`・`key:key-main` のそれぞれで、偽の起動が受け取る環境と起動引数が、`Target.env`・`Target.argv`（`target_env` の返り）と同じで、`key:key-main` の起動引数に `--settings` の helper がある | 指定が使えないとき判断の結果・すべて飛ばして既定のログイン | 理由ごとに 1 行と `account_dir` の行が出て次の候補で起動する |
| `_initial`・`child_env` | 環境変数 `work1` が使えれば `work1` | 環境変数も指定も無く、登録済みアカウントが 2 つあるとき、既定のログインで起動し `prepare` が 1 回も呼ばれない。環境変数の `work1` が外した間で指定が `work2` なら `work2`。環境変数の `work1` を飛ばして既定のログインへ着地したとき、子の `NDF_CLAUDE_ACCOUNT` が無く、`account` の行と `section` の行の `account` が None | 先頭の名前が外した間・再登録が要る・上限のとき `prepare` が呼ばれない |
| `_on_switch`・`_pinned_or` | 動いている間に指定を替えると次のセグメントがそのアカウント | 指定が上限・外した間・再登録が要るとき判断の結果。`keep` の今のアカウントが外した間なら `order` の先頭。指定が使える（上限でも外した間でも再登録が要る間でもない）が 5 時間 86%（組 3）で、ほかに 10%（組 0）の候補があれば、カットポイント・見回り・上限の後・認証の失敗の後のどれでも 10% の候補（受け入れ条件 #3864 の 8 と同じ期待値。指定の変更だけでは組 3 の指定を先にしない）。指定もほかも組 3 なら指定。環境変数も指定も無く既定のログインで動く子が、登録済みアカウントが 2 つある状態でカットポイントに来ると、既定のログインのまま次のセグメントを起動し `prepare` が呼ばれない | 壊れた指定で今と同じく止まる |
| `_on_resume`・`_pinned_or` | 上限の行の後、指定の `work1`（古い 92% の記録）が選ばれず `work2` で `--resume` | 上限の行を見た `work1` が `--resume` で選び直されない（同じアカウントで起動し直しを繰り返さない）。見回りが余裕の `work3` を選び、指定が組 2 の `work2` のとき、止める前に確定した `Choice` が `work2` を先頭にし、`_on_resume` が同じ `Choice` で起動する（確定と起動の順が同じ）。認証の失敗で指定が今のアカウント `work1` のとき、`work1` で起動し直さない | — |
| `_limit_pick` | 使用率の分からない `work2` で `--resume` | すべて上限で従量の接続・従量の接続も無くリセット待ち。status line の無い経路で、上限の観測の `until` を過ぎると同じアカウントで `--resume` | — |
| 子を止める前の確定 | 替え先の `work2` が用意できれば子を止めて `work2`。`projects` がまだ無い初回の `work2` へも替える | 唯一の替え先の `projects` が実体のとき、上限では従量の接続（設定があれば）かリセット待ちで子を止めない。認証の失敗では `stalled` で子を残す。唯一の替え先の実体の `settings.json` に `apiKeyHelper` があるときも同じ。控えを読んだ後に `work2` の `.credentials.json` を消すと、止める直前の `ready` で外れて子を止めない | 止めた後に `prepare` が競合で落ちたとき、上限・認証の失敗では既定のログインへ落ちず今のアカウントで起動する。今のアカウントの `prepare` も落ちれば、子を起動せず `account_unavailable` で止まり、既定のログインの起動が呼ばれない |
| `_auth_pick` | 会話の記録の最後が 401 の API の失敗の記録で、`work2` で `--resume`、`held.json` に `work1` | 既定のログインの認証の失敗でも替える。`OAuth token revoked` の 403 でも替える | 利用者の入力のエコー・モデルの応答の引用・前のセグメントの古い表示に `API Error: 401` があっても、`held.json` が変わらず子を止めない。会話の記録の最後が 403 の `permission_error`（モデル・組織の権限の制限）なら、画面に `API Error: 403` があっても `held.json` が変わらず子を止めない。替え先が無ければ直し方の 1 行がセグメントで 1 回、ログインの形ごとの文 |
| `_inputs` | 上限の行が出たまま 1000 回見回っても、ファイルの印が変わらなければ使用率と登録のファイルを開くのは 1 回 | status line が `usage/<名前>/<世代>.json` を置き換えで書く（`usage/` の更新時刻が変わらない）と使用率だけを 1 回読み直し、`ready` は打たない。解除の時刻を過ぎると 1 回読み直す。上限待ちの間に、`projects_not_shared` で落ちていた `work2` の `projects` を共有側への symlink に直すと、次の判断で `work2` にだけ `ready` が打たれ、`work2` で `--resume` する。`work3` の `settings.json` から `apiKeyHelper` を消しても同じ。`ready` が落ちた名前の控えにも、`ready` が返した `Probe.stamps` が入り、ラッパーは源のファイルを開かない（偽の `effective_env` と偽の `open` の呼び出しが `ready` の中の分だけ）。設定ファイルの `ANTHROPIC_BASE_URL` で落ちた `metered` も、その行を消すと次の判断で候補に戻る | 今が組 0 の見回りでは起動できるかの印を取らない |
| `account` の行 | 替えて起動が成功したら 1 行 | 回答待ちのガードで子を止めなかったとき行が無い。替え先の用意に落ちて次の候補で起動したとき、行の `account` が実際の起動先で `skipped` に名前 | 子の起動に失敗したとき行が無い |
| `_settle`・`_end` | セグメントの終わりとラッパーの終わりに 1 回ずつ書き戻す | 既定のログインと従量の接続では打たない | 書けないとき 1 行で続ける |
| `_tell_at_start` | 再登録が要る 2 つで 2 行 | 無ければ出さない | 従量の接続の設定が壊れていると 1 行 |
| `_end_point`・`_on_revoked`・`_state`（決定 12） | `key:k1` の子で使い方を `off` にした直後の要求が 401 で終わり入力待ちに戻ると、`relay.quiet` の後に子を止めて `account_unavailable` で終わる（利用者が止めなくても終わる） | `must_end` の後に利用者が打った入力・質問の回答が子の端末へ送られない。回答待ちの質問が残ったままでも `relay.quiet` の後に止まる。子が 0 以外で終わっても同じキーで起動し直さない。認証の失敗の行が出ている見回りでも `_drift` が打たれ `must_end` が立つ。上限の行が同時に出ても `wait_reset` で子を残さない。`off` の後、子が応答の途中の間は止めずに待ち、終わった後に止まる。`ANTHROPIC_BASE_URL` を足すと、子が道具の実行の途中でもその見回りで止まり、子の端末へ入力が送られない。`off` の後に利用者が Esc で応答を中断して入力待ちに戻ると `relay.quiet` の後に止まる。許可待ちのまま `relay.quiet` の後に止まる。`work2` が登録済みなら、`_drift` が当たった見回りで止まらなくても、止まる見回りで `work2` で `--resume` する（`account_unavailable` にならない）。`work2` の `prepare` が止めた後に落ち、止める待ちの間に `off` から戻していても、`key:k1` で起動し直さず `account_unavailable` で終わる | 会話の記録が読めなければ `relay.quiet` の後に止まる。どの条件にも当たらなくても `relay.end-wait` の後に止まる |
| `_on_resume`・`_on_spin`（決定 13・I15） | 設計文書のテスト設計の決定 13・I15 の 3 行に加え、状態のファイルに `starts` が無い | `spin` で中継を続けた後のマーカーでも起動せず、`stop`（`spin`）の行は 1 回だけ。`_launch_target` が先頭と違う起動先へ移ったときは実際の起動先で数える。`Streak(2, {work1, work2})` で `work3` への替えの後、`work3` の `prepare` が落ちて `fallback` の `work1` へ移る場面では `work1` を起動せず `end`（`spin`）。判断の関数の替え先が `work1`（`seen` にある）でも指定の `work3` が先頭なら `work3` で起動する。`spin_held` の後に今の区間が 120 秒に達すると、上限の行で `work3` へ替わる。`key:k1` の子で使い方を `off` にし、替えの手が `spin` に替わったときも、止めてよい時点で子を止めて `end`（`spin`）で終わる | `wait_reset` で待った時間も区間の長さに入る（待った後の `account` は短い区間にならない） |

## 子の画面と記録の観測（`observe.py`）

満たす決定: 決定 11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `tool_pending`・`turn_done` | `observe.py` | 新設 | `tool_pending` は `(path: Path, size: int) -> Optional[bool]`、`turn_done` は `(path: Path, size: int) -> Optional[tuple[bool, Optional[float]]]` | `tool_pending`: 会話の記録の末尾 `size` バイト（`relay.transcript-tail`）を後ろから読み、最後の `assistant` の記録の `tool_use` の `id` に対応する `tool_result` が後ろに無ければ真、あれば・`tool_use` が無ければ偽。記録が無い・読めない・形が違うときは None。`turn_done`: 応答が終わったか。末尾の完全な行のうち最後の会話の記録（`user`・`assistant`）が、`stop_reason` が `end_turn`（か `stop_sequence`）の `assistant` の記録で、`tool_pending` が偽なら `(True, その記録の時刻)`。最後の記録が `user`（入力か `tool_result`）・`stop_reason` が `tool_use` か無い `assistant`（応答の途中）なら `(False, None)`。読めない・形が違うときは None。前の応答が残ったまま次の応答を待つ場面（利用者の入力の後・`tool_result` の後で、モデルがまだ出力を始めていない）では、最後の記録が `user` なので偽になる。入力が会話の記録に書かれる前でも、ラッパーが入力を送った時刻（`Section.input_at`）が前の応答の終わりより後なので、`_safe_point` は偽になる |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `tool_pending`・`turn_done` | 読めない・形が違う | 書き込みの途中・記録の形の変更 | 読み・JSON の解釈 | None を返す（止めてよい時点にしない） | 無し | 次の見回りで読み直す |

### 既存の部品の再利用

`tool_pending`・`turn_done`: 確かめた既存の部品は `observe.transcript_path`・文脈の量を読む末尾の読み（ファイルを読んで確かめた）。結果: 使う。末尾 `relay.transcript-tail` バイトを読む同じ処理に載せる。

### 単体テストの観点

`tool_pending`: `tool_use` の後に `tool_result` が無ければ真、あれば偽、最後が本文だけの応答なら偽、末尾で切れた行は飛ばし、ファイルが無ければ None。`turn_done`: 最後が `end_turn` の `assistant` なら真と時刻。最後が利用者の入力・`tool_result`・`stop_reason` の無い `assistant` なら偽。会話の記録の形は実装の PR で手元の記録から確かめ、違えば設計へ戻す（設計文書の「未確認のまま残ること」）。

## 共有の替え先（`account_choice.py`）

満たす決定: 決定 6・8・9・12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `candidates`・`fallbacks` | `account_choice.py` | 新設 | `candidates` は `(store: Path, state_dir: Path, now: float, cwd: Path, argv: list[str]) -> list[str]`、`fallbacks` は `(store: Path, environ: Mapping[str, str], metered: Optional[tuple[dict[str, str], bool]], cwd: Path, argv: list[str], tried: frozenset[str]) -> list[account_dir.Probe]` | `candidates`: 登録済みアカウントのうち、再登録が要らず、外した間でなく、`account_dir.ready` の `Probe.reason` が None のもの（ラッパーの `_candidates` は、`LaunchCache` の控えの `result` が None の名前を同じ規則で選ぶ）。`fallbacks`: `metered` があれば `metered`、続けて `recovery_keys` の `key:<名前>` を並べ、`tried`（この復帰の続きで試して失敗した接続先。決定 6）を除き、どれも `build` と `verify` を当てる。返りは、当てた名前すべての `Probe` を同じ順で並べたものである。通ったものは `reason` が None、外れたものは符号を持つ。`stamps` は `metered.json`・`api-keys.json` の印と、`verify` が返した `Resolved.stamps`（`build` で落ちて `verify` を打たなければ前者だけ）。呼び出し元は `reason` が None のものを候補にし、全部を `LaunchCache` に控える。ファイルを書かない（`--settings` のファイルを合成した JSON は照合用の起動引数に文字列のまま置き、`effective_env` へ渡すだけで、子の起動引数には使わない） |
| `Built` | `account_choice.py` | 新設 | 型 `(env: dict[str, str], argv: list[str], prepared: Optional[account_dir.Prepared], resolved: Optional[account_dir.Resolved], settings_file: Optional[Path])` | 確定した替え先の、子に渡す最終の環境・最終の起動引数・用意の結果（登録済みのとき）・その組で打った `effective_env` の結果・`--settings` の合成で書いた一時ファイル（無ければ None）。ラッパーの `Target` と `ndf plan` の起動はこの組をそのまま使う |
| `build` | `account_choice.py` | 新設 | `(env: Mapping[str, str], target: str, store: Path, state_dir: Path, cwd: Path, argv: list[str]) -> tuple[dict[str, str], list[str]] \| str` | 従量の接続と API キーの子の、最終の環境と照合用の起動引数を作る（書かない）。`metered` は `metered_env`、`key:<名前>` は `key_env` と下の「`--settings` の合成」。`cwd` は子が実際に起動する作業ディレクトリで、`argv` の `--settings` のファイルの相対パスはここから解く。作れなければ理由の符号 |
| `verify` | `account_choice.py` | 新設 | `(env: Mapping[str, str], argv: list[str], cwd: Path, config_dir: Path, target: str, expected: Mapping[str, str]) -> tuple[Optional[str], account_dir.Resolved]` | 最終の環境・起動引数・作業ディレクトリで `account_dir.effective_env` を 1 回だけ打ち、その結果に下の「起動の前の実効値の確かめ」を当てる。返りは（理由、打った `Resolved`）で、理由は通れば None、外れれば `metered_overridden`・`key_overridden`（項目名と出所だけ）。照合とキャッシュの印（`LaunchCache` の `Resolved.stamps`）と `Built.resolved` は、どれもこの 1 回の `Resolved` から取り、`effective_env` を打ち直さない |
| `target_env` | `account_choice.py` | 新設 | `(env: Mapping[str, str], target: str, store: Path, state_dir: Path, cwd: Path, argv: list[str]) -> Built \| str` | 確定した替え先の子の環境と最終の起動引数。登録済みアカウントは `account_dir.prepare(…, cwd, argv)` の後に `account_env`（`prepare` が認証の上書きを確かめる。`Built.resolved` は `Prepared.resolved`）。`metered`・`key:<名前>` は `build` の後に、下の「`--settings` の合成と引用」のとおり一時ファイルを書いた最終の起動引数で `verify` を打ち、外れれば一時ファイルを消して理由の符号を返す。使えないときの返りは、値を含まない理由の符号である。`prepare` の `Prepared.reason`、`build` の `metered_unavailable`（`_metered` と同じ検証に外れる・設定が無い）・`key_missing`（登録簿にその名前が無い・登録簿が壊れている）・`key_off`（使い方が `off`）・`key_unresolved`、`verify` の `metered_overridden`・`key_overridden`・`key_unresolved` のどれかを返す。呼び出し元はこの符号で診断と `Failure` の分類を決め、照合を打ち直さない。照合の項目名と出所（値は含まない）は、`target_env` が `log.jsonl` の `account_dir` の行へ書く。ラッパーと `ndf plan`（通常の `key:`・`metered` の起動と打ち直し）はどちらもこの関数だけで替え先を作り、返りの `env`・`argv` と同じ `cwd` で起動する |
| `retry_env` | `account_choice.py` | 新設 | `(env: Mapping[str, str], state_dir: Path, now: float, cwd: Path, argv: list[str]) -> Optional[tuple[Built, str]]` | `ndf plan` の打ち直しの環境（返りの `str` は替え先の名前）。`env` の `NDF_CLAUDE_ACCOUNT` の上限の観測を `see_limit` で書き（`until` は `limit_until` の (4)）、`after_limit(現在, rows, candidates, fallbacks, Guards)` が `switch` なら `target_env` の `Built` を返す。`wait_reset` か、`target_env` が理由の符号を返したら None（符号は `stderr` の 1 行に出す） |

#### `--settings` の合成と引用

API キーの子の起動引数には、`apiKeyHelper` を 1 つの `--settings` として足す。

| 子に渡す前の `argv` | 合成 |
| --- | --- |
| `--settings` が無い | `["--settings", <JSON>]` を足す。`<JSON>` は `{"apiKeyHelper": <helper の文字列>}` |
| `--settings <JSON の文字列>` が 1 つある | その object に `apiKeyHelper` を足した 1 つの JSON へ置き換える。元の object が `apiKeyHelper` を持てば `key_overridden`（`apiKeyHelper（--settings）`）で、その候補を使わない |
| `--settings <ファイル>` が 1 つある | ファイルを読み、上と同じく `apiKeyHelper` を足す（元のファイルは書き換えない）。相対パスは `build` の `cwd`（子の作業ディレクトリ）から解く。子の起動引数へは合成した JSON を文字列で展開せず、下の一時ファイルに書いてそのパスを渡す（元のファイルの `env` やフックの引数にある秘密を、`ps` や `/proc/<pid>/cmdline` で読める argv へ移さない）。照合（`build`・`fallbacks`）では、子へ渡さない照合用の起動引数に JSON の文字列のまま置き、ファイルを書かない。読めない・object でなければ `key_unresolved` |
| `--settings` が 2 つ以上ある | `key_unresolved`（Claude Code の重ね方が文書に無い） |

合成の一時ファイル（`--settings <ファイル>` を合成したときだけ。`target_env` が書く）:

| 項目 | 決まり |
| --- | --- |
| 置き場 | `<状態の保存先>/settings/<owner の pid>-<owner の開始時刻>-<secrets.token_hex(8)>.json`。ディレクトリは 0700 で作り（あれば 0700 に直す）、ファイルは `os.open(…, O_WRONLY \| O_CREAT \| O_EXCL \| O_NOFOLLOW, 0o600)` で作って書く。共有の設定ディレクトリや作業ディレクトリには置かない |
| 照合 | 書いた後、その絶対パスに替えた最終の起動引数で `verify` を打つ（照合した引数と起動する引数が同じ）。外れれば消して理由の符号を返す |
| 寿命 | `Built.settings_file`（ラッパーは `Target.settings_file`）が持ち、子が終わるまで残す（Claude Code は動いている間も `--settings` のファイルの変更を読み直すため、起動の直後に消さない）。消すのは起動した側で、ラッパーは子の起動の失敗・`attach` の失敗・`_settle`・`_end`、`ndf plan` は `call_claude` の `finally` |
| 残骸 | `target_env` は書く前に、同じディレクトリのファイルのうち、次の 2 つをどちらも満たすものだけを消す（ラッパーや `ndf plan` が `SIGKILL` で終わり、子も終わった後の分）。1 つは、名前の `owner` の pid と開始時刻のプロセスが生きていないこと。もう 1 つは、`/proc/<pid>/cmdline` の要素にそのファイルの絶対パスを持つプロセスが無いこと（パスは乱数を含むので、起動引数に持つプロセスがそのファイルを使う子である）。起動元だけが終わって子の claude が残っている間は消さない。`/proc` を読めないときは消さない |
| 出さないもの | 中身とパスを画面・`log.jsonl`・理由の文に出さない（出すのは理由の符号だけ） |

`<helper の文字列>` は Claude Code がシェルで実行するので、`shlex.join([<ndf の実行ファイルの絶対パス>, "relay", "account", "api-key-helper", <名前>])` で作る。実行ファイルのパスに空白や引用符があっても 1 語になる。名前は登録簿の名前の形（英小文字・数字・`-`）に限るが、同じく引用する。`--settings` の値は子の起動の引数の 1 要素として渡し、シェルを通さない。作業ディレクトリの基準: `build`・`verify`（`effective_env`）・子の実際の起動は、どれも同じ `cwd`（ラッパーは子の作業ディレクトリ、`ndf plan` は `Popen(cwd=…)` に渡す値。None なら `call_claude` を呼んだプロセスの作業ディレクトリを絶対パスにしたもの）を使う。`--settings` やプロジェクトの設定の相対パスを、呼び出し元のプロセスの作業ディレクトリでは解かない。

#### 起動の前の実効値の確かめ（`verify`）

`expected` は `build` が作った最終の環境（設定ファイルを重ねる前の値）である。登録簿の API キーの子では、次をすべて確かめる。照合に使うのは 1 回の `effective_env` の `Resolved` だけで、helper は値（`Resolved.helper`）と出所（`Resolved.helper_origin`）を別々に見る。登録簿のキーに許す宛先: 既定の `DEFAULT_API_URL`（`https://api.anthropic.com`）だけである。登録簿（`api-keys.json`）はキーに宛先を結び付ける欄を持たないので、親の環境や設定ファイルから継いだゲートウェイ・プロキシの宛先へ登録簿のキーと会話を送らない。ゲートウェイを通すキーは、宛先とキーを同じ宣言に書く従量の接続（環境変数の宣言の `ANTHROPIC_API_KEY` と `ANTHROPIC_BASE_URL`）で使う。そのため、宛先の照合は `expected`（親から継いだ値を含む）ではなく、この固定の宛先と比べる。

| 確かめ | 外れる例 |
| --- | --- |
| `Resolved.helper_origin` が `--settings` で、`Resolved.helper` が合成した `<helper の文字列>` と同じ | 管理の設定の `apiKeyHelper` が勝つ（管理の設定は `--settings` より上）・合成の後に別の `--settings` が来る |
| 実効の `env` の `PROVIDER_ENV` の `provider_on` がどれも偽（None があれば外れる） | 共有側・管理の設定の `env` の `CLAUDE_CODE_USE_BEDROCK=1` |
| 実効の `env` の「認証の値」の組（`ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN`・`ANTHROPIC_PROFILE`・`ANTHROPIC_FEDERATION_RULE_ID`・`ANTHROPIC_ORGANIZATION_ID`）がどれも空か無い（STD2 で `apiKeyHelper` より上か同じ段に来る資格情報と、別の認証の選択） | 管理の設定の `env` の `ANTHROPIC_AUTH_TOKEN`・設定ファイルの `ANTHROPIC_PROFILE` |
| 実効の `env` の `ANTHROPIC_BASE_URL` が空か無いか、`DEFAULT_API_URL`（末尾の `/` を除いて比べる）。`expected` にあっても同じ | 親の環境の `ANTHROPIC_BASE_URL=https://gateway.example.com`（`key_env` は外さないので `expected` にも入るが、許す宛先と違うので外れる）・設定ファイルの `env` の `ANTHROPIC_BASE_URL` |
| 実効の `env` の `ANTHROPIC_CUSTOM_HEADERS` が空か無い。`expected` にあっても同じ | 親の環境・設定ファイルの `env` の `ANTHROPIC_CUSTOM_HEADERS`（`Authorization` などで認証や振り分けを替えうる） |
| `METERED_CHECKED` のうち上の 4 つの外の変数（クラウド事業者の資格情報・選択・接続先）ごとに、実効値が `expected` と同じ（`expected` に無ければ実効値にも無いか空文字） | 設定ファイルの `env` が `AWS_PROFILE` を足す |
| `unresolved` が空 | 読めない管理の設定 |

理由の 1 行には項目名と出所（`ANTHROPIC_BASE_URL（子の環境）` など）だけを出し、helper・ヘッダー・接続先の値は出さない。従量の接続（`metered`）の子では、`account_dir` の節の「従量の実効値の照合」を `verify` が当てる（同じ関数）。どちらも、`argv` は `build` の後の最終の起動引数で、子と `ndf plan` の `claude -p` のどちらでも同じ値を渡す。`fallbacks` が返した名前は、この確かめに通っている。止める直前（「子を止める前の替え先の確定」）と `target_env` でもう一度打つので、間の設定の変更も拾う。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `verify`・`build` | `metered_overridden`・`key_overridden`・`key_unresolved` | 設定ファイル・`--settings` から別の helper・提供元・資格情報・接続先が入る。`--settings` が読めない・2 つ以上ある（`key_unresolved`） | `effective_env` の結果・`argv` の解釈 | その候補を外す | 画面に 1 行（項目名と出所だけ）と `log.jsonl` に 1 行 | その設定から項目を外せば次の判断で戻る |
| `target_env` | 理由の符号 | `prepare` の失敗・`build` の失敗（キーが無い・`off`・従量の接続の設定が使えない）・`verify` の外れ | 返り | 呼び出し元が次の候補へ（ラッパー）か、符号を入れた失敗で返す（`ndf plan`） | 呼び出し元の 1 行（符号の文。値を含まない） | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `verify`・`build` の引用 | `account_dir.effective_env`・従量の実効値の照合・`shlex.join` | この文書の `account_dir` の節・標準ライブラリ | 使う。API キーの照合の行を足すだけで、源の読み方は持たない。引用は `shlex.join` を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `build`・`verify` | `key-main` で `--settings` の JSON の `apiKeyHelper` が `shlex.join` の文字列になり、実効の helper の出所が `--settings` | 子の `argv` に既に `--settings '{"model": "x"}'` があれば 1 つにまとまり `model` も残る。ndf の実行ファイルのパスに空白があっても helper の文字列が 1 語の引用になる | 管理の設定の `apiKeyHelper`・管理の設定の `env` の `CLAUDE_CODE_USE_BEDROCK=1`・`ANTHROPIC_AUTH_TOKEN`・`ANTHROPIC_BASE_URL` のそれぞれで `key_overridden`。`--settings` が 2 つで `key_unresolved`。`--settings` の helper の出所は同じで値だけが違う（合成の後に別の値へ替わる）と `key_overridden`。親の環境の `ANTHROPIC_BASE_URL=https://gateway.example.com` で `key_overridden`（`https://api.anthropic.com/` なら通る）。親の環境か設定ファイルの `ANTHROPIC_CUSTOM_HEADERS` で `key_overridden` で、理由の文にヘッダーの値が無い。`verify` の返りの `Resolved` が、`effective_env` を 1 回だけ打った結果（偽の `effective_env` の呼び出し回数が 1）で、その `stamps` が `LaunchCache` の印に入る |
| `build`・`target_env`（ファイルの `--settings`） | 元の `--settings` がファイルなら、子の起動引数の `--settings` の値が `<状態の保存先>/settings/` の下の 0600 のファイルのパスで、起動引数のどの要素にも元のファイルの `env` の値が無い。元のファイルは変わらない | `build` の `cwd` を呼び出し元の作業ディレクトリと別にし、両方に同じ相対パスの別の中身のファイルを置くと、`cwd` の側を読む。子の終わり・起動の失敗の後に一時ファイルが無い。`owner` が死に、そのパスを起動引数に持つプロセスも無い一時ファイルは次の `target_env` が消す。`owner` が死んでも子が生きている一時ファイルは消さない | `verify` に外れると一時ファイルが残らない。`fallbacks` はファイルを書かない |
| `fallbacks` | `metered` と `key:k1` の `Probe` が順に返り、どちらも `reason` が None | `tried` に `key:k1` があれば返らない | 照合に外れた `metered` の `Probe` は `reason` が `metered_overridden` で、`stamps` に `verify` の `Resolved.stamps` がある。登録簿に無いキーは `key_missing` で、`stamps` は `api-keys.json` の印だけ |
| `target_env` | ラッパーと `ndf plan` の打ち直しが同じ環境と起動引数を受け取る | — | `verify` に外れれば `key_overridden`・`metered_overridden` の符号で、子も `claude -p` も起動しない。登録簿に無いキーで `key_missing`、使い方が `off` で `key_off` が返り、どの符号にもキーの値が無い。偽の `effective_env` の呼び出し回数が、符号を受けた呼び出し元の診断の後も 1 |

## `ndf plan` の `claude -p`（`adapters/claude.py`。環境は子から継ぎ、認証の上書きを確かめ、上限で 1 回打ち直す）

満たす決定: 決定 1・8・9・12、受け入れ条件 #3604 の 11、不変条件 I10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `call_claude`（起動の前の確かめと届け） | `adapters/claude.py` | 変更 | 変わらない | 環境の作り方は変えない。`call_claude` が起動する `claude -p` の環境を、`tests/adapters/test_claude_reply.py` に足すテストで縛る。縛るのは、親の環境の `CLAUDE_CONFIG_DIR`・`NDF_CLAUDE_ACCOUNT` をそのまま持つことである。足すのは起動の前の 1 つの確かめだけである。環境の `NDF_CLAUDE_ACCOUNT` が登録済みアカウントの名前なら、起動する直前に認証の上書きを確かめ、当たれば起動しない。打つのは `account_dir.auth_overrides(env, Path(env["CLAUDE_CONFIG_DIR"]), cwd, argv)` である。渡すのは、最終的に使う `argv`・環境・作業ディレクトリ（起動方法ごとに組み立てた後の値）である。`argv` の `--setting-sources` が読む源を決める。そのため `minimal` の起動（`--setting-sources ''`）では、利用者・プロジェクトの設定を見ない。実際には読まれない `apiKeyHelper` で、計画の実行を拒否しない。管理の設定と `--settings` はどの起動でも見る。確かめに通ったら、`account_dir.acquire` で worker 自身の使用の届けを取り、「使用の届けの状態」の関所で起動して、終わりを確かめた後に `release` で消す。CLI の起動と API の呼び出しは増えず、増えるのは設定ファイルの読み取りと届けの書き込み、関所の Python の起動 1 回だけである。 |
| `call_claude`（`key:` の親） | `adapters/claude.py` | 変更 | 変わらない | API キーの親からの通常の起動（決定 12）: 環境の `NDF_CLAUDE_ACCOUNT` が `key:<名前>` なら、親の子の認証は起動引数の `--settings` の helper にだけあり、環境からは継げない。そのまま起動すると、worker は共有側のログインなど別の認証で送るか、認証で止まる。そこで `call_claude` は、`account_choice.target_env(env, "key:<名前>", store, 状態の保存先, cwd, argv)` で、ラッパーと同じ合成と照合を当てた `Built` を作り、その `env`・`argv`・同じ `cwd` で `claude -p` を起動する（`cwd` は `Popen(cwd=…)` に渡す値。None なら呼んだプロセスの作業ディレクトリの絶対パス）。理由の符号（`key_missing`・`key_off`・`key_overridden`・`key_unresolved`）が返れば、`claude -p` を起動せずに、その符号を入れた失敗（`Failure.REFUSED`）で返す。符号から診断の文を作り、照合を打ち直さない。`Built.settings_file` は `finally` で消す。 |
| `call_claude`（`metered` の親） | `adapters/claude.py` | 変更 | 変わらない | 従量の接続の親からの通常の起動（決定 1・8）: 環境の `NDF_CLAUDE_ACCOUNT` が `metered` なら、`call_claude` は同じく `target_env(env, "metered", store, 状態の保存先, cwd, argv)` を打つ。期待値は、親が確定したのと同じ従量の接続の設定（保存した `metered.json` か環境変数の宣言）から `build` が作る環境である。`verify` は、worker の最終の環境・起動引数・作業ディレクトリで「従量の実効値の照合」を当てる。worker の作業ディレクトリのプロジェクトの設定や `--settings` が接続先・ヘッダー・資格情報を替えれば、`metered_overridden` で `claude -p` を起動せずに `Failure.REFUSED` で返す。設定が使えなければ `metered_unavailable` で同じく返す。通れば `Built.env`・`Built.argv` と同じ `cwd` で起動する。 |
| `call_claude`（上限での打ち直し） | `adapters/claude.py` | 変更 | 変わらない | 上限での打ち直し（決定 9）: 2 つの条件がそろえば、`account_choice.retry_env(env, 状態の保存先, now, cwd, argv)` を呼ぶ。`call_claude` の結果が `Failure.USAGE_LIMIT` であることと、環境の `NDF_CLAUDE_ACCOUNT` が登録済みアカウントの名前であることである。環境が返れば、その環境と最終の起動引数（`target_env` が合成と確かめを済ませたもの）で同じ呼び出しを 1 回だけ打ち直し、その結果を返す。登録済みアカウントへの打ち直しでは、通常の起動と同じ関所で `claude -p` を起動し、関所を開ける前に `account_dir.attach` を打ち、終わった後（成功・失敗・タイムアウトのどれも）に `account_dir.settle` を打つ（届けは `target_env` の中の `prepare` が書く）（「使用の届けの状態」の起動の関所）。起動か `attach` に失敗すれば子を回収して `release` を打つ。最初の呼び出しの届けは、打ち直しの前に `release` で消す。後始末は `try`/`finally` で囲み、例外でも届けを残さない。打ち直しも上限なら、もう打ち直さずに上限の失敗を返し、計画の打ち直しの待ちへ進む。None なら今どおり上限の失敗を返す。`NDF_CLAUDE_ACCOUNT` が無い・`metered`・`key:` のときは打ち直さない（`key:` の通常の起動は上の段落の合成と照合を通る）。打ち直しの前に `stderr` へ 1 行（`ndf plan: アカウント <名前> が利用上限に当たった。<替え先> で打ち直す`）を出し、キーの値は出さない。 |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `call_claude` | 認証の上書き | 設定ファイル・起動引数の上書き | `auth_overrides` | `claude -p` を起動せずに失敗で返す | 項目名と直し方を入れた失敗 | その設定を直せば次の呼び出しで通る |
| `call_claude` | 打ち直しの用意の失敗 | `retry_env` が None（替え先が無い・`prepare` か `verify` の失敗） | 返り | 打ち直さずに上限の失敗を返す | 今の上限の失敗と同じ | 計画の打ち直しの待ちへ |
| `call_claude` | 後始末 | 例外・タイムアウト | `finally` | 使用の届け（通常の起動は `release`、打ち直しは `settle`、子の起動の前なら `release`）と `Built.settings_file` を消す。例外でも届けを残さない | 無し | — |
| `call_claude` | 使用の届けを取れない | `acquire` の `AccountError`（名前が登録に無い・継いだ設定ディレクトリか世代が今の登録と違う・排他の待ち切れ・書けない）・継いだ世代が解けない | `acquire` | `claude -p` を起動せずに `Failure.LAUNCH` で返す | 理由の符号を入れた失敗 | 次の呼び出しで再び試す |
| `call_claude` | 関所を開けられない | `attach` が理由を返す | `attach` の返り | 子を `SIGKILL` で回収し、`release` してから `Failure.LAUNCH` で返す | 同上 | 同上 |
| `call_claude` | `key:`・`metered` の親から起動できない | `target_env` が理由の符号を返す | 返り | `claude -p` を起動せずに `Failure.REFUSED` で返す | 理由の符号を入れた失敗（キーの値は出さない） | キーを登録し直すか、照合に外れた設定を直せば次の呼び出しで通る |

### 既存の部品の再利用

打ち直し: 確かめた既存の部品は `account_choice.retry_env`・`target_env`（この文書の「共有の替え先」）。結果: 使う。ラッパーと同じ替え先の作り方にする。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `call_claude` | 親の環境の `CLAUDE_CONFIG_DIR` が起動の環境に同じ値で入る | `NDF_CLAUDE_ACCOUNT` も同じ値。既定のログイン・`metered` では `auth_overrides` を打たない。`minimal` の起動（`--setting-sources ''`）では、作業ディレクトリの `.claude/settings.json` に `apiKeyHelper` があっても起動する | 設定ソースを有効にした起動で、worker の作業ディレクトリの `.claude/settings.json` に `apiKeyHelper` があると、`claude -p` を起動せずに項目名を入れた失敗を返す（偽の起動が呼ばれない）。`minimal` の起動でも、管理の設定の `apiKeyHelper`・環境の `ANTHROPIC_API_KEY`・環境か管理の設定の既定でない `ANTHROPIC_BASE_URL`では起動しない |
| `call_claude` の使用の届けと `key:` の親 | `work1` の環境の通常の起動で、`claude -p` が動いている間 `<保存先>/.leases/work1/` に `running` の届けがあり、終わった後に無い。`key:key-main` の環境で、偽の起動が受け取る起動引数に `--settings` の helper の文字列があり、環境に `ANTHROPIC_API_KEY` が無く、作業ディレクトリが `Popen(cwd=…)` の値 | 既定のログイン・`metered` では届けを取らず関所を通さない。タイムアウトで組を止めた後に届けが消える | `key:` で登録簿にキーが無い・親の環境に既定でない `ANTHROPIC_BASE_URL` があると、偽の起動が呼ばれずに失敗を返し、失敗に `key_missing`・`key_overridden` の符号が入る。`metered` の親で、worker の作業ディレクトリの `.claude/settings.json` の `env` が宣言と別の `ANTHROPIC_BASE_URL` か `ANTHROPIC_CUSTOM_HEADERS` を足すと、偽の起動が呼ばれずに `metered_overridden` の失敗を返す |
| `call_claude` の打ち直し（決定 9） | `work1` の環境で上限の失敗が返ると、`limited.json` に `work1` が書かれ、`work2` の設定ディレクトリの環境で 1 回だけ打ち直し、その結果を返す | 打ち直しも上限なら 2 回目を打たずに上限の失敗を返す。替え先が無ければ打ち直さない。既定のログイン・`metered` では打ち直さない | 替え先の `prepare` が落ちれば打ち直さずに上限の失敗を返す |

## 一覧と API キーのコマンド（`cli_account.py`）

満たす決定: 決定 4・12、受け入れ条件 #3604 の 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_acc_key_helper`・`_acc_list`・登録・削除・指定 | `cli_account.py` | 新設・変更 | `_acc_key_helper` は `(args) -> int`、`_acc_list`・登録・削除・指定 は 変わらない | `_acc_key_helper`: `ndf relay account api-key-helper <名前>` は、`_acc_key_helper` として新設する。`account_registry.key_value(store, 名前)` の値を標準出力へ 1 行出して、終了コード 0 を返す。無い名前・壊れた登録簿・使い方が `off` のときは何も出さず終了コード 1 を返し、標準エラーに理由（名前だけ）を出す。`off` を打った後に Claude Code が helper を呼び直すと、動いている子もキーを受け取らない（呼び直す時期は Claude Code が決める。止める主な経路は、ラッパーの見回りが `api-keys.json` の変更を検出して子を替える「起動の後の設定の変更」である）。Claude Code の `apiKeyHelper` が呼ぶためのサブコマンドで、一覧や `--help` に値を出さない。テストは `test_account_registry.py` で、値が標準出力だけに出て、誤りの文に値が入らないことを見る。使い方を `off` にした後は何も出さず終了コード 1 になることも見る。`_acc_list`・登録・削除・指定: `_acc_list` は `accounts.usages(state_dir, time.time(), 登録の世代)` を渡す。`account use <名前>` は指定を書いた後に `accounts.unhold(state_dir, 名前)` を打つ（利用者が直して指定したアカウントを、外した間の残りで飛ばさない）。登録・削除の変更は「登録の保存先」の節にある。ほかは変えない。`state` は今の `_state` のまま（`再登録が要る` は `mark_relogin` の後に出る）。テストは `test_account_registry.py` の一覧の行で見る。 |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_acc_key_helper` | `AccountError`・使い方が `off` | 無い名前・壊れた登録簿・`api-key-use off` の後 | `key_value`・使い方の読み | 何も出さずに止める | 標準エラーに名前だけの理由、終了コード 1（Claude Code の `apiKeyHelper` の失敗になり、子の認証の失敗として決定 6 が扱う） | 登録し直せば次の呼び出しで通る |
| 登録・削除 | 使用の届けがある | 動いている子・`ndf plan` | 届けの確かめ | 何も変えずに止める | 「登録の保存先」の節の文、終了コード 1 | 子を終えてから打ち直す |

### 既存の部品の再利用

`_acc_key_helper`: 確かめた既存の部品は 登録簿の読み（`account_registry` の API キーの関数）（ファイルを読んで確かめた）。結果: `key_value` を足して使う。

## 手順書（`relay-accounts.md`）

満たす決定: 決定 10、受け入れ条件 #3864 の 12〜14

書き直しの中身は設計文書の決定 10 の「選んだ案」にある。API キーの節には、決定 12 の「リリースの時点の知らせ」の内容（既存の `always`・`near-limit` の設定でいつ課金が始まるかと、`off` で止める操作、`off` が動いている子に止めてよい時点で効くこと）を書く。同じ内容を `CHANGELOG.md` のその版の「利用者向けの変化」に 1 行で書く。書いた後に、手順書の振る舞いの主張を 1 つずつ、この文書の関数と照らす。文言のテストは書かない。
