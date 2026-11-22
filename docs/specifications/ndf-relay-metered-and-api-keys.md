# 中継の従量の接続と API キー: 登録済みアカウントが尽きた間の退避先と、キーの子を許した宛先から外さない仕組み

中継のラッパー（`ndf relay`）は、利用上限か認証の失敗で使える登録済みアカウントが無い間、同じ会話を従量の接続
（Bedrock などのクラウド事業者の接続、または API キー）で続け、上限の解けた登録済みアカウントが現れたら止めてよい時点で
戻る。退避先の順は、従量の接続の設定（環境変数の宣言 `NDF_SUPERVISE_CLAUDE_FALLBACK`、無ければ保存した設定）→ API キーの
登録簿のキー（使い方が `off` でないもの。名前の順）である。API キーの値は環境変数で渡さず、起動引数の `--settings` の
`apiKeyHelper` で渡す。キーで動く子（キーの子）は、設定の変更・401・helper の拒否を検出したら子を残さずに止め、替え先が
無ければ会話を送らずに終わる。Claude Code だけが対象である。

例: 登録済みアカウント `work1`・`work2` がどちらも上限で、従量の接続の設定が無く、API キー `key-main` を
`ndf relay account api-key-use near-limit` にしている利用者のとき。

| 順 | 起きること |
| --- | --- |
| 1 | 上限の後の判断が登録済みの替え先を見つけられず、退避先の `key:key-main` を選ぶ。画面と `log.jsonl` に 1 行出す |
| 2 | 子は共有の設定ディレクトリで、認証の 13 変数と提供元の秘密の変数を持たずに起動する。起動引数に `--settings '{"apiKeyHelper": "<ndf> relay account api-key-helper key-main --generation <世代> --pin … --record … --id …", "hooks": {"ConfigChange": […]}}'` が付き、環境に `ANTHROPIC_API_KEY` は無い |
| 3 | Claude Code が helper を呼び、helper が照合に通ってキーを 1 行出す。ラッパーはその払い出し（記録の `out: true` の行）を見届けてから、控えていた最初のプロンプトを子へ送る |
| 4 | 利用者が `ndf relay account api-key-use off` を打つ。次の見回りで当たり、止めてよい時点か 60 秒の早い方で子を止める |
| 5 | 替え先が無いので、後継を起動せずに `account_unavailable` で終わる（会話を送らない）。`work1` の上限が解けた後に、利用者が起動し直して同じ会話を `--resume` で続ける |

この文書は、その形の構成要素・常に成り立つ条件・データの形・入出力の契約・決定と理由・テスト観点を残す。いつ退避するかの
判断は [ndf-relay-account-switching.md](ndf-relay-account-switching.md)、登録済みアカウントの子は
[ndf-relay-account-config-dir.md](ndf-relay-account-config-dir.md)、`ndf plan` の `claude -p` は
[ndf-relay-plan-claude-accounts.md](ndf-relay-plan-claude-accounts.md) にある。

| 何を読むか | 正本 |
| --- | --- |
| 登録・使い方の設定・確かめのコマンドの打ち方と画面の文、課金が始まる条件と止め方 | [`development-workflow` の `references/relay-accounts.md`](../../plugins/ndf/skills/development-workflow/references/relay-accounts.md) |
| 宣言の検証・提供元の確定・照合する変数・API キーの登録簿 | `plugins/ndf/src/ndf/domains/relay/account_registry.py`（`parse_declared`・`metered_kind`・`METERED_CHECKED`・`SECRET_ENV`・`recovery_keys`・`key_value`） |
| 従量の接続と API キーの確かめと保存 | `plugins/ndf/src/ndf/domains/relay/account_metered.py` |
| 替え先の環境・`--settings` の合成・起動の前の実効値の確かめ・起動の後の変更の検出 | `plugins/ndf/src/ndf/domains/relay/account_choice.py`（`fallbacks`・`build`・`verify`・`target_env`・`drift`）・`session_env.py`（`metered_env`・`key_env`） |
| helper の記録・払い出しの上限・設定への固定・`config-gate` | `plugins/ndf/src/ndf/domains/relay/key_helper.py`・`cli_account.py`（`_acc_key_helper`・`_acc_config_gate`） |
| キーの子の停止 | `plugins/ndf/src/ndf/domains/relay/wrapper_stop.py`（`_drift`・`_key_auth`・`_end_point`・`_stop_revoked`・`_on_revoked`） |
| 待ちの上限 | [`contracts/limits.json`](../../plugins/ndf/contracts/limits.json) の `relay.key-helper`・`relay.end-wait`・`relay.child-exit`・`claude-api-keys.check` |

## 用語

「従量の接続」「登録済み API キー」「API キーの登録簿」「API キーの使い方の設定」「残高切れ」「サブスクリプション」
「API クレジット」の定義は用語集（[docs/glossary.md](../glossary.md)）が正である。この文書で使うほかの語は次のとおりである。

| 語 | 意味 |
| --- | --- |
| 保存した設定 | `ndf relay account add-bedrock` で確かめて保存した従量の接続の設定（`<保存先>/metered.json`）。資格情報の変数を持てない |
| 環境変数の宣言 | 環境変数 `NDF_SUPERVISE_CLAUDE_FALLBACK` の `KEY=VALUE` の並び。あれば保存した設定より先に使う |
| API キーの宣言 | 環境変数の宣言のうち、提供元の選択が無く `ANTHROPIC_API_KEY` を持つもの（`metered_kind` が `API_KEY_KIND`、`"api-key"`） |
| キーの子 | API キーで動く子。登録簿の API キーの子（`NDF_CLAUDE_ACCOUNT=key:<名前>`）と、API キーの宣言の従量の接続の子（`metered`）の 2 つ。クラウド事業者の従量の接続の子は入らない |
| helper | `ndf relay account api-key-helper`。登録簿か宣言からキーの値を標準出力へ 1 行出すだけのサブコマンドで、Claude Code が `apiKeyHelper` として呼ぶ |
| helper の ID | ラッパーが起動ごとに作る乱数（`secrets.token_hex(16)`）。払い出しの記録の行を起動ごとに見分ける |

## 背景

- 11 系の中継は、上限の後に替え先が無ければ同じアカウントのリセットを待つだけだった。2026-11-21 の設計承認ゲートで利用者が
  「停まった後は人の入力無しで別のアカウントか API・Bedrock で自動で復帰する」と指示し、従量の接続と API キーを上限の後の
  復帰先にした（#3864 の決定 8・12）
- API キーの登録簿のコマンド（`add-api-key`・`api-key-monthly`・`api-key-use`）は 10 系にあり、`supervise.py` の worker の
  `claude -p` を「全アカウントの週の上限が近いときだけ」API キーで動かす規則（近い上限の規則）に使っていた。11 系はこの規則を
  持たず（`ndf plan` の経路は #3991）、登録簿を復帰先としてだけ使う
- 登録するキーは、サブスクリプション（Max・Ultra・Team）にリンクした Console 組織のキーを想定している。その組織の API クレジットは
  利用上限と別枠だが、使い切った後や買ったクレジットでは利用料が掛かる
- 対話の claude を環境の `ANTHROPIC_API_KEY` で起動すると「Detected a custom API key in your environment / Do you want to use
  this API key?」の問いが出て、切り替えた子が回答待ちで止まる。`--settings` の `apiKeyHelper` なら問いは出ない（2026-11-21、
  Claude Code 2.1.330 で実測）
- v11.0.15 の #4116（上限で Bedrock で再開し、`--model` を推論プロファイルへ差し替える）は、この経路の従量の接続の退避へ
  置き換えた。差し替えの振る舞いは保つ

## 対象範囲

| 含む | 含まない |
| --- | --- |
| 従量の接続の設定の検証・子の環境・起動の前と起動の後の照合 | いつ従量の接続へ替えるか・いつ戻るか（[ndf-relay-account-switching.md](ndf-relay-account-switching.md)） |
| API キーの登録簿とコマンド・キーの子の起動と停止・helper | `ndf plan` を API キーの規則（近い上限の規則・月の額・使用額の帳簿）で動かす経路（#3991） |
| 入れ子の `ndf plan` の helper の扱い | `ndf plan` のキーの子（[ndf-relay-plan-claude-accounts.md](ndf-relay-plan-claude-accounts.md)。今は起動しない） |

## 仕様

### 構成要素

| 要素 | 責務 |
| --- | --- |
| 登録の保存先（`account_registry.py`） | 宣言の検証（`parse_declared`）・提供元の確定（`metered_kind`・`provider_on`）・照合する変数（`METERED_CHECKED`）・秘密の変数（`SECRET_ENV`）・ラッパーが確定する変数（`METERED_FIXED`）・登録簿の読み（`read_book`・`recovery_keys`・`key_value`・`use_on`） |
| 確かめと保存（`account_metered.py`） | `add-bedrock`・`add-api-key` の確かめと保存、`api-key-monthly`・`api-key-use`、`check` |
| 子の環境（`session_env.py`） | 従量の接続の子（`metered_env`）とキーの子（`key_env`）の環境 |
| 共有の替え先（`account_choice.py`） | 退避先の候補（`fallbacks`）・子の環境と最終の起動引数（`build`・`target_env`）・起動の前の実効値の確かめ（`verify`）・起動の後の変更の検出（`drift`）・`ndf plan` の確かめ直し（`reverify`）。ラッパーと `ndf plan` が共有する |
| helper（`key_helper.py`・`cli_account.py`） | 払い出しの記録・排他・上限・拒否と失敗の印・設定への固定の照合（`gate_view`）と、`config-gate` の hook |
| ラッパー（`wrapper_stop.py`・`wrapper_launch.py`） | キーの子の記録の作成と払い出しの見届け、設定の変更・401・helper の失敗の検出と停止 |

### 常に成り立つ条件

| # | 条件 | 守り方 |
| --- | --- | --- |
| I4 | 従量の接続の子の環境に認証の 13 変数（`account-drop`）が無い | `metered_env`・`key_env` が外してから設定を重ねる |
| I9 | 従量の接続の子（キーの子を含む）の `CLAUDE_CONFIG_DIR` は共有の設定ディレクトリである | 親が登録済みアカウントの子なら `NDF_SHARED_CONFIG_DIR` から戻し、空なら外す |
| K1 | API キーの値を `ANTHROPIC_API_KEY` として子の環境に渡さず、起動引数・画面・記録に出さない。登録簿のキーは環境変数のどこにも出さない。例外は利用者が環境変数の宣言（`NDF_SUPERVISE_CLAUDE_FALLBACK`）に書いた API キーで、その平文は宣言の変数のまま子とその道具へ渡る（#4145） | helper だけが標準出力へ出す。記録の行は ID・時刻・出したかだけ |
| K2 | キーの子は、照合した宛先・資格情報・helper と違う設定で会話とキーを送り続けない | 起動の前に照合し、helper が払い出しのたびに設定への固定を照らし、`config-gate` が反映の前に拒み、見回りが検出して止める。替え先が無くても子を残さない |
| K3 | 1 つの helper の ID でキーを出すのは、どの 60 秒の間でも 3 回までで、拒んだ後と拒否の印の後は 0 回である | 照合と追記を記録の 1 つの錠の中で行う |

### 従量の接続の設定

従量の接続の設定は、環境変数の宣言があればそれだけ、無ければ保存した設定（`account_registry.fallback_env`）である。
起動の前に同じ検証を当て、1 つでも外れれば設定なしとして扱い、ラッパーの起動で理由の 1 行（変数の名前だけ）を出す。

| 検証 | 外れる例 |
| --- | --- |
| 構文。`shlex.split` が通り、すべての語が `KEY=VALUE` で `KEY` が `[A-Z_][A-Z0-9_]*` | 閉じない引用符・`=` の無い語 |
| 空でない | 空・空白だけ |
| ラッパーが確定する変数（`METERED_FIXED`。子の環境の表の `child` が `set`・`drop`・`account-set` の変数と、`NDF_` で始まる変数）を持たない | `CLAUDE_CONFIG_DIR`・`NDF_CLAUDE_ACCOUNT` |
| 提供元を 1 つに選べる（`metered_kind` が None でない） | `CLAUDE_CODE_USE_BEDROCK=0` だけ・`ANTHROPIC_API_KEY=` だけ・`CLAUDE_CODE_USE_VERTEX=maybe`・提供元と API キーの混在・API キーと別の認証の値・資格情報の選択（`ANTHROPIC_PROFILE`・`AWS_PROFILE`）・API キーと `ANTHROPIC_CUSTOM_HEADERS` の併用 |

`metered_kind` は、提供元の選択の 6 変数のうち `provider_on` が真のものがちょうど 1 つで、ほかがどれも偽で、
`ANTHROPIC_API_KEY` を持たなければその変数の名前を返す。どれも真でなく、`ANTHROPIC_API_KEY` が空白でない値で入り、ほかの
照合する変数が接続先の変数（`API_KEY_WITH`）と偽の提供元の選択だけなら `API_KEY_KIND` を返す。保存した設定の保存の時点
（`check_decl`）も、ラッパーが確定する変数と秘密の変数（`SECRET_ENV`）を拒む。

照合する変数（`METERED_CHECKED`）は 6 つの組で、どの提供元の宣言でも全部を照合する（使わない提供元の変数でも、設定ファイル
から入れば Claude Code が別の宛先を選びうるため）。

| 組 | 変数 | 秘密（`SECRET_ENV`） |
| --- | --- | --- |
| 提供元の選択 | 6 つの `CLAUDE_CODE_USE_*` | いいえ |
| 認証の値 | `ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_OAUTH_TOKEN`・`ANTHROPIC_PROFILE`・`ANTHROPIC_FEDERATION_RULE_ID`・`ANTHROPIC_ORGANIZATION_ID` | 前の 3 つ |
| AWS の資格情報と選択 | `AWS_BEARER_TOKEN_BEDROCK`・`AWS_ACCESS_KEY_ID`・`AWS_SECRET_ACCESS_KEY`・`AWS_SESSION_TOKEN`・`ANTHROPIC_AWS_API_KEY`・`AWS_PROFILE`・`AWS_CONFIG_FILE`・`AWS_SHARED_CREDENTIALS_FILE`・`ANTHROPIC_AWS_WORKSPACE_ID`・`CLAUDE_CODE_SKIP_BEDROCK_AUTH`・`CLAUDE_CODE_SKIP_MANTLE_AUTH`・`CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH` | `AWS_BEARER_TOKEN_BEDROCK`・AWS のキーの 3 つ・`ANTHROPIC_AWS_API_KEY` |
| Google Cloud の資格情報と選択 | `GOOGLE_APPLICATION_CREDENTIALS`・`ANTHROPIC_VERTEX_PROJECT_ID`・`ANTHROPIC_GOOGLE_CLOUD_PROJECT`・`ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID`・`CLAUDE_CODE_SKIP_VERTEX_AUTH`・`CLAUDE_CODE_SKIP_ANTHROPIC_GOOGLE_CLOUD_AUTH` | いいえ |
| Foundry の資格情報と選択 | `ANTHROPIC_FOUNDRY_API_KEY`・`ANTHROPIC_FOUNDRY_AUTH_TOKEN`・`ANTHROPIC_FOUNDRY_RESOURCE`・`CLAUDE_CODE_SKIP_FOUNDRY_AUTH` | 前の 2 つ |
| 接続先 | `ANTHROPIC_BASE_URL`・`ANTHROPIC_BEDROCK_BASE_URL`・`ANTHROPIC_BEDROCK_MANTLE_BASE_URL`・`ANTHROPIC_AWS_BASE_URL`・`ANTHROPIC_VERTEX_BASE_URL`・`ANTHROPIC_GOOGLE_CLOUD_BASE_URL`・`ANTHROPIC_FOUNDRY_BASE_URL`・`AWS_REGION`・`AWS_DEFAULT_REGION`・`CLOUD_ML_REGION`・`ANTHROPIC_GOOGLE_CLOUD_LOCATION` | いいえ |
| 要求に足すヘッダー | `ANTHROPIC_CUSTOM_HEADERS` | はい（保存した設定と API キーの宣言は持てない） |

### 従量の接続の子の環境

`metered_env` は、`CLAUDE_CONFIG_DIR` を共有の設定ディレクトリへ戻し（親に `NDF_SHARED_CONFIG_DIR` があり空でなければその値、
空なら外す。親に `NDF_SHARED_CONFIG_DIR` が無ければ親の `CLAUDE_CONFIG_DIR` をそのまま保つ）、`NDF_SHARED_CONFIG_DIR`・
`NDF_ACCOUNT_USAGE_FILE` と認証の 13 変数を外す。保存した設定と API キーの宣言では、照合する変数（`METERED_CHECKED`）を秘密の
変数に限らずすべて親から外す（期待値をこの出力から作るので、保存した設定に無い親の `AWS_PROFILE`・接続先・地域を期待値に入れず、
起動の前の照合を別の資格情報や宛先のまま通さない）。API キーの宣言では宣言の `ANTHROPIC_API_KEY` を重ねない（helper で渡す）。続けて設定を重ね、最後に
`NDF_CLAUDE_ACCOUNT=metered` と決めた `CLAUDE_CONFIG_DIR` を入れ直す。

設定に `ANTHROPIC_MODEL` があれば、起動引数の `--model <値>`・`--model=<値>` を外し、オプションの最後（`--` があればその前）に
`--model <設定の値>` を置く（Claude Code は `--model` を環境変数より先に読むので、外さないと推論プロファイルが使われない）。

切り替える前（候補に入れるときと起動するとき）に、`account_dir.effective_env` で子の最終の環境・起動引数・作業ディレクトリの
実効値を求め、期待値（`metered_env` の出力）と照合する。外れれば `metered_overridden` で候補から外し、1 行を出す（子は止めない）。

| 確かめ | 外れる例 |
| --- | --- |
| `provider_on` が真の提供元の選択が宣言と同じ 1 つだけ（API キーの宣言なら 0 個）で、ほかは偽 | API キーの宣言に共有側の `settings.json` の `CLAUDE_CODE_USE_BEDROCK=1` が加わる |
| 提供元の選択の外の照合する変数ごとに、実効値が期待値と同じ（期待値に無ければ実効値にも無いか空文字） | 管理の設定の `ANTHROPIC_AUTH_TOKEN`・設定ファイルの `AWS_BEARER_TOKEN_BEDROCK`・別の `AWS_PROFILE`・宣言と別の接続先・宣言に無い `ANTHROPIC_CUSTOM_HEADERS` |
| 実効の `apiKeyHelper` が無い（API キーの宣言では NDF の helper の文字列で、出所が `--settings`）で、解けない源が無い | プロジェクトの設定の `apiKeyHelper`・読めない管理の設定 |
| API キーの宣言では、実効の `NDF_SUPERVISE_CLAUDE_FALLBACK` が期待値と同じ | 設定ファイルの `env` が宣言を別の値に替える |

宣言に接続先を入れたプロキシ・ゲートウェイは、期待値と同じなので通る。

### API キーの登録簿とコマンド

登録簿は `<保存先>/api-keys.json`（0600）で、無ければ登録 0 本・使い方 `off` として動く。書くときは一時ファイルへ 0600 で
書いて置き換える。

| コマンド | 振る舞い |
| --- | --- |
| `account add-api-key <名前> --account <アカウント> [--key-file <パス>] [--monthly <ドル>]` | キーの値は標準入力の 1 行目か `--key-file` で受け、引数では受けない。Messages API へ `max_tokens` 1 で 1 回要求を打って確かめ（宛先は `https://api.anthropic.com/v1/messages`（`account_metered.CHECK_URL`）に固定し、環境変数では変えない。`account check <名前>` も同じ関数（`check_api_key`）で保存済みのキーを同じ宛先へ送る。テストは宛先を変えず、`transport` の注入で応答を差し替える。リダイレクトは追わない（`follow_redirects=False`。転送先へキーを送らない）。モデル `NDF_API_KEY_CHECK_MODEL`、既定 `claude-haiku-4-5`、上限 `claude-api-keys.check` の 30 秒）、通れば保存する。登録のたびに新しい世代（UUID 版 4）を書く。確かめの結果は `ok`・`auth_failed`（401 か `authentication_error`）・`credit_low`（`error.message` に "credit balance" と "too low"。残高切れ）・`network`・`unclassified` |
| `account api-key-use [off\|always\|near-limit] [--weekly-at <百分率>]` | 使い方を読む・書く。11 系の中継は、`off` でなければ（`always`・`near-limit` のどちらでも）登録したキーを上限と認証の失敗の後の復帰先に使う（`use_on`）。動いているキーの子にも効く |
| `account api-key-monthly <名前> <ドル>` | 月の額を書く（`0` で外す）。一覧に出すだけ |
| `account list`・`check <名前>`・`remove <名前>` | 一覧（キーの値を含まない）・確かめ（状態を記録しない）・外す |

**11 系に無い 10 系の規則。** 10 系の起動先の決定（`launch_rules.py`）は、週の使用率（週のクォータとモデル別の週のクォータの
最大）が閾値 T（`weekly_at`）を越えたら、使用の割合（直近 7 日の使用額 ÷ 週の利用枠。週の利用枠は月の額 × 7 ÷ 30）の小さい
キーで worker を起動し、残高切れのキーをその起動の中でだけ外していた。11 系の中継と `ndf plan` はこの規則を持たない。
`weekly_at` と月の額は登録簿に残るが、11 系の判断はどれも読まず、使用額の帳簿も持たない（#3991。10 系だけの語の扱いは #4140）。

### キーの子の起動

キーの子は、共有の設定ディレクトリで、認証の 13 変数と秘密の変数を外して起動する（`key_env`。`NDF_CLAUDE_ACCOUNT=key:<名前>`）。
親の `ANTHROPIC_BASE_URL`・`ANTHROPIC_CUSTOM_HEADERS` は黙って外さず、`verify` が比べて外れればその候補を外す。登録簿のキーに
許す宛先は既定の `https://api.anthropic.com` だけである（登録簿はキーに宛先を結び付けないので、継いだゲートウェイへ送らない。
ゲートウェイを通すキーは API キーの宣言に宛先と一緒に書く）。

起動引数には、`apiKeyHelper` と設定の変更の関所の hook を 1 つの `--settings` として足す（`build`）。

| 子に渡す前の起動引数 | 合成 |
| --- | --- |
| `--settings` が無い | `--settings '{"apiKeyHelper": <helper>, "hooks": {"ConfigChange": [{"hooks": [<hook>]}]}}'` を足す |
| `--settings <JSON>` が 1 つ | その object に足す。元が `apiKeyHelper` を持てば `key_overridden`。既存の hook は残し、`hooks.ConfigChange` の末尾に 1 組足す |
| `--settings <ファイル>` が 1 つ | 読んで同じ規則で足し、合成を `<状態の保存先>/settings/<owner の pid>-<開始時刻>-<乱数>.json`（0600）に書いてそのパスを渡す（元のファイルの秘密を起動引数へ移さない）。子が終わるまで残し、起動した側が消す |
| `--settings` が 2 つ以上・読めない | `key_unresolved` |

- helper の文字列は `shlex.join([<ndf の絶対パス>, "relay", "account", "api-key-helper", <名前>, "--generation", <世代>])`
  （API キーの宣言なら `api-key-helper metered <指紋>`。指紋は検証した宣言のキーの SHA-256）に、`--pin <固定のファイル>` と、
  ラッパーの起動では `--record <記録先> --id <helper の ID>` を続けたものである
- hook は `{"type": "command", "command": <config-gate の文字列>, "timeout": <relay.key-helper の gate>, "onFailure": "block"}` である。
  `timeout` は合成のたびに `contracts/limits.json` の `relay.key-helper` の `gate`（今は 5 秒）から読み、固定値を持たない。
  認証と宛先の変更を拒む `config-gate` の待ち時間は、契約の値の変更に追随する。`onFailure` が効く
  Claude Code 2.1.295（`account_choice.GATE_FLOOR`）より古い・版を読めないときは `key_gate` で起動しない。`disableAllHooks`・
  管理の設定の `allowManagedHooksOnly`・`--bare`・`--safe-mode`・`CLAUDE_CODE_SIMPLE`・`CLAUDE_CODE_SAFE_MODE` で hook が働かない
  ときも `key_gate` で起動しない
- `verify` は、最終の起動引数で 1 回の `effective_env` を打ち、helper の出所が `--settings` で値が合成した文字列と同じこと、
  提供元の選択がどれも偽、認証の値の組が空、接続先が既定、ヘッダーが空、ほかの照合する変数が期待値と同じ、解けない源が無いことを
  確かめる。外れれば `key_overridden` で候補から外す
- 通ったら、照合した設定ファイルごとの比べる範囲（`gate_view`。`env` の照合する変数・`NDF_SUPERVISE_CLAUDE_FALLBACK`・
  `CLAUDE_CODE_SIMPLE`・`CLAUDE_CODE_SAFE_MODE` と、`apiKeyHelper`・`disableAllHooks`・`allowManagedHooksOnly`）の SHA-256 を
  固定のファイル（`<状態の保存先>/settings/<stem>.pin.json`、0600）に書いてから子を起動する
- ラッパーは起動の前に、中継の保存先の `key-helper.jsonl`（0600）を作り直し、失敗の印 `key-helper.fail` を消す。作れなければ
  `key_record` でその起動先を飛ばす
- **最初の払い出しまで送らない。** 子を起動してから、記録に今の helper の ID の `out: true` の行が現れるまで、最初のプロンプト・
  利用者の入力・質問の回答を子の端末へ送らずに控える。現れたら、画面が 1 秒静かになったところで控えた順に送る（入力欄を描く前の
  入力は捨てられる）。`relay.key-helper` の `first`（30 秒）を過ぎても現れなければ、設定の当たりとして子を止め、控えた入力を捨てる。
  Claude Code が NDF の helper を呼ばずに別の資格情報を使う起動で、会話を照合していない宛先へ送らないためである

### helper

helper は呼ばれるたびに、記録を `O_RDWR | O_APPEND | O_NOFOLLOW` で開き（作らない）、通常のファイル・リンク数 1・所有が自分・0600
を確かめ、`flock` を取る（待ちは `relay.key-helper` の `lock`、0.5 秒）。同じ錠の中で、固定のファイルとの照合・払い出しの上限の
照合・1 行の追記を済ませ、`out: true` の行を書いて錠を放した後にだけキーを出す。

| 出さない条件 | 記録の行 |
| --- | --- |
| 同じ ID に拒否の印（`deny`）か `out: false` の行がある・直近 60 秒に `out: true` が 3 行ある（`relay.key-helper` の `seconds`・`attempts`） | `out: false` |
| 使い方が `off`・登録簿に無い・世代が起動のときと違う・宣言が無い・指紋が違う | `out: false` |
| 照合した設定ファイルの今の比べる範囲が固定のファイルと違う・読めない | `out: false`・`cause: pin` |
| 記録を開けない・確かめに外れる・錠を取れない・追記が落ちる | 記録に書けないので、失敗の印へ `{"id", "by": "helper", "cause"}` を足す |

どれも終了コード 1 で、値は出さない。記録先と ID は起動引数（`--record`・`--id`）だけから決める（設定の `env` で書き換えられる
`NDF_RELAY_DIR` は使わない）。入れ子の `ndf plan` の helper は `--record`・`--id` を持たないので、外側の記録を書かず、外側の
払い出しの枠を使わない。

`config-gate` は Claude Code の `ConfigChange` の hook として、実行中のセッションへ設定の変更を反映する前に呼ばれる。標準入力の
`source` が `skills` か `policy_settings`（拒めない）なら通し、ほかは `file_path` の今の比べる範囲が固定のファイルの値と違うか、
読めないときに拒む（終了コード 2）。拒んだら記録へ `{"id", "gate": <source>}` を足す。宛先に関わらない変更（許可の追加・skills の
編集など）は通し、子も止めない。

### キーの子の停止

| 検出 | 止める時点 |
| --- | --- |
| 登録簿のキーの削除・同じ名前の登録し直し（世代の違い）・使い方の `off`（`_drift`） | 止めてよい時点か `relay.end-wait`（60 秒）の早い方。キーは変更前の宛先にしか送られないため |
| ほかの送信先や認証を変え得る不一致（宛先・ヘッダー・資格情報・提供元の選択・`apiKeyHelper` の追加・変更・削除。API キーの宣言では宣言との照合の外れすべて）（`_drift`） | 検出した見回り（道具の実行の途中でも待たない。結果を使う次の要求は利用者の入力なしに出るため） |
| 記録の `out: false`（`cause: pin` を除く）・記録の異常・失敗の印の `by: helper`・401 の確かめ（`_key_auth`） | 検出した見回り。認証の失敗として扱い、今の起動先を `tried` に入れる。401 のときは記録へ拒否の印を足す |
| 記録の `gate` の行・`cause: pin`・失敗の印の `by: gate`・`first` の経過（設定の当たり。`_key_auth`） | 検出した見回り。`tried` に入れない |

検出は、見回りごとに起動のときの源のファイルと `api-keys.json` の印を `os.stat` で比べ、変わった回だけ実効値を求め直す（ファイル
を開くのは変わった回だけ）。当たった見回りから、利用者の入力・最初のプロンプト・質問の回答を子へ送らず、控えた入力を捨てる。

止めるときは、先に旧子を止めて終わりを確かめてから替え先を選ぶ（`_stop_revoked`）。質問の hook と同じロックの中で旧子の起動 ID を
外し、質問のファイルを消す。終わりや外したことを確かめられなければ `refused` で終わり、後継を起動しない。確かめられたら、同じ
見回りで替え先を取り直し（今のキーは除く。同じ名前の登録し直しなら新しい世代のその名前を残す）、替え先があり再起動ループの検出に
当たらなければ `--resume` で続け、当たれば `spin`、替え先が無ければ `account_unavailable` で終わる。子を残したまま待つ経路は無い。
認証の失敗で止めたときは、失敗した接続先と直し方を並べた 1 行を出す。設定の当たりで止めたときは「API キーの子の送信先・認証の設定
（<出所>）が起動のときと変わったので止めた。設定を戻すか、ndf relay で起動し直す」の 1 行を出す。検出から旧子の終わりまでは
`relay.child-exit` の 5 秒以内で、記録の印の変化を読むまでの 0.5 秒と合わせて 5.5 秒である。

キーの子の起動ごとに、ラッパーは子の環境の `NDF_RELAY_PIN` に親の接続の識別を入れる（`metered` は宣言の提供元・出所・照合する
変数の期待値から作った SHA-256、`key:<名前>` は `key:<名前>:<世代>`）。入れ子の `ndf plan` が親と同じ接続で送ることを確かめる値で
ある（[ndf-relay-plan-claude-accounts.md](ndf-relay-plan-claude-accounts.md)）。

## データ・設定

| パス | 形 | 書き手 |
| --- | --- | --- |
| `<保存先>/metered.json`（0600） | `version`・`provider`・`env`・`details`・`verified_at` | `add-bedrock` |
| `<保存先>/api-keys.json`（0600） | `version`・`use`（`off`・`always`・`near-limit`、既定 `off`）・`weekly_at`・`keys.<名前>.{key, account, added_at, generation, monthly_usd}` | `add-api-key`・`api-key-use`・`api-key-monthly`・`remove` |
| `<中継の保存先>/key-helper.jsonl`（0600） | `{"id", "at", "out"[, "cause"]}`・`{"id", "deny": true}`・`{"id", "gate"}` の行。キーの値も指紋も書かない | helper・`config-gate`・ラッパー（拒否の印） |
| `<中継の保存先>/key-helper.fail`（0600） | `{"id", "by": "helper" か "gate", "cause"}` の行 | helper・`config-gate` |
| `<状態の保存先>/settings/<stem>.json`・`<stem>.pin.json`・`<stem>.pin.ok`（0600） | 合成した `--settings`・照合した設定への固定・`ndf plan` の払い出しの印 | `target_env`・helper。`owner` も使うプロセスも無くなったものだけを次の `target_env` が消す |

世代を持たない 10 系の登録簿の行は復帰先にならない（`key_generation` が拒む）。`ndf relay account add-api-key` で登録し直す。

| 上限の値 | 値 | 使い方 |
| --- | --- | --- |
| `relay.key-helper` | `seconds: 60`・`attempts: 3`・`lock: 0.5`・`gate: 5`・`first: 30` | 払い出しの上限・記録の錠・hook の `timeout`・最初の払い出しの待ち |
| `relay.end-wait` | `seconds: 60` | キーの削除・`off` の後に止めてよい時点を待つ上限 |
| `relay.child-exit` | `seconds: 5` | 子を止めた後に終わりを確かめる上限 |
| `claude-api-keys.check` | `seconds: 30` | `add-api-key` の確かめの要求の上限 |

## 外部連携

| 相手 | 使い方 |
| --- | --- |
| Claude Code の `apiKeyHelper` | 認証の源の 1 つ。対話のセッションは起動の時点で 1 回と、既定の 5 分の有効期間ごとに最大 1 回呼ぶ。管理の設定の `apiKeyHelper` は `--settings` より上に来る |
| Claude Code の `ConfigChange` の hook と `onFailure: "block"` | 実行中の設定の変更を反映の前に拒む。2.1.295 以降で `onFailure` が効く |
| Messages API（`POST https://api.anthropic.com/v1/messages`） | `add-api-key` の確かめだけに使う |
| Bedrock など | 従量の接続の設定の提供元。`ndf relay account check metered` で呼べるかを確かめる |

## 決定と理由

- **API キーを上限の後の復帰先にだけ取り込む。** 範囲外のままでは、API キーしか持たない利用者は登録済みアカウントが尽きると
  待つ。#3991 の近い上限の規則・月の額・使用額の帳簿は、停まらないことに要らない
- **キーは環境変数でなく `--settings` の `apiKeyHelper` で渡す。** 環境のキーは対話の claude が使うかを問い、切り替えた子が
  回答待ちで止まる。値も子のプロセスの環境に残る。API キーの宣言のキーも同じ helper で渡す
- **使い方の `off` 以外の値（`always`・`near-limit`）は「復帰先に使う」の意味を兼ねる。** 10 系でこの値を設定していた利用者は、
  追加の操作なしに上限と認証の失敗の後の復帰先としてキーが使われ、利用料が掛かり始める（手順書と利用者向けの変化で知らせた）
- **キーの子は替え先が無くても子を残さない。** 子を残すと Claude Code が helper を呼び直し続け、止まる時点が無い。設定が変わった
  キーの子を残すと、登録簿のキーと会話を許していない宛先へ送り続ける
- **キーを出す側で設定を照合し、最初の払い出しを見届けてから会話を渡す。** Claude Code が読む初期の設定を照合済みの内容に固定する
  起動引数は無い。`--setting-sources` で源を外すと利用者の許可・hooks・プラグインも失う
- **登録簿のキーには既定の宛先だけを許す。** 登録簿はキーに宛先を結び付けないので、親から継いだゲートウェイへ黙って送らない
- **従量の接続の設定が提供元を 1 つに選べないときは設定なしとする。** 同じ宣言を Bedrock として起動するか、候補から外すかが
  分かれるためである

## セキュリティ

- 秘密（キー・クラウド事業者の資格情報）に触れ、認証の経路を変える（レッドラインの C1・C2）。キーの値は helper の標準出力だけに
  出し、`ANTHROPIC_API_KEY` として子の環境へ渡さず、起動引数・画面・`log.jsonl`・記録・理由の文に出さない。登録簿のキーは
  環境変数のどこにも出ない。環境変数の宣言（`NDF_SUPERVISE_CLAUDE_FALLBACK`）に書いた API キーは、この保証の範囲の外で、
  宣言の変数の平文のまま子とその道具へ渡る（#4145。避けるには登録簿へ `add-api-key` で入れる）
- 脅威の範囲は、うっかりの利用者と権限の無い人だけである（#2773）。同じ利用者の権限で記録・失敗の印・固定のファイルを差し替える
  操作、設定ファイルの `env` で helper の登録簿の場所を差し替える操作、管理の設定を書く管理者は範囲の外である
- 管理の設定（`policy_settings`）の変更は `config-gate` で拒めず、次の払い出しの helper の照合と見回りの検出で当たる。検出までに
  送られる要求は残る
- 照合の直後に設定ファイルへ別の宛先を足すと、子の起動から 0.14 秒で新しい宛先が `HEAD /api/hello` を 1 件受けた（認証の
  ヘッダー・キー・会話は無い。2026-11-22、Claude Code 2.1.330）。利用者がレッドライン C2 の残りとして受け入れた
- 照合の後に設定を元へ戻す 2 回の保存が、Claude Code の読みと helper の呼び出しの間に収まる場合は当たらない（数百ミリ秒の間に
  意図して書き換える操作で、脅威の外）

## 運用

- 課金を止めるには `ndf relay account api-key-use off` を打つ。動いている子には次の見回りで効き、止めてよい時点か 60 秒で
  止まり、替え先が無ければ会話を送らずに終わる
- 従量の接続の設定は `ndf relay account check metered` で確かめる。設定が壊れていれば、ラッパーの起動で 1 行知らせて退避先から
  外れる

### 既知の限界

- helper が記録にも失敗の印にも行を書く前に落ちる（共有の venv の入れ替えの最中の import の失敗など）と、ラッパーは helper の
  失敗として観測できない。キーは出ないので漏れは無い（#4139・#4006）
- 起動の前の確かめが外れたときの理由の 1 行に、外れた項目名と出所が出ない（`key_overridden`・`metered_overridden`。#4129）
- 環境変数で宣言した従量の API キーの平文は `NDF_SUPERVISE_CLAUDE_FALLBACK` に残り、子とその道具へ渡る（#4145）
- 親の接続の識別（`NDF_RELAY_PIN`）は照合する変数だけを比べ、設定のほかの変数（`HTTPS_PROXY`・`PATH` など）の差し替えを見ない（#4144）
- キーの候補があると、ラッパーの見回りのたびに `claude` の実パスを探し直す（#4146）
- 旧子を止めた後に起動先の NDF を確かめ直すので、止める直前の確かめを通った替え先が NDF の更新で外れることがある（#4124）
- 中継の子が、信頼済みでない作業場所で信頼の問いに答え待ちで止まる（キーの子に限らない。#4066）

## テスト観点

テストは `plugins/ndf/tests/domains/relay/` の `test_relay_wrapper_keys.py`・`test_relay_key_helper.py`・`test_relay_key_recovery.py`・
`test_relay_wrapper_fallback.py`・`test_relay_metered_fallback.py`・`test_relay_wrapper_gate.py`・`test_account_metered.py`・
`test_relay_parts.py` にある。

| 観点 | 落ちるべき壊し方 |
| --- | --- |
| すべての登録済みアカウントが上限で従量の接続の設定があると、従量の接続で同じ会話を続け、子の `CLAUDE_CONFIG_DIR` が共有の設定ディレクトリ、保存した設定なら AWS のキーが無い。上限が解けると止めてよい時点で戻る | アカウントの設定ディレクトリのまま起動する・AWS のキーを残す・カットポイントまで戻らない |
| 設定が `CLAUDE_CONFIG_DIR`・`NDF_CLAUDE_ACCOUNT` を持つ・壊れている・空・提供元を選べないときは設定なし。共有側の `settings.json` の別の提供元・管理の設定の `apiKeyHelper`・設定ファイルの資格情報と接続先で `metered` を外す | 宣言の変数がラッパーの値を上書きする・設定ファイルからの再注入を見ない |
| 従量の接続の設定の `ANTHROPIC_MODEL` が起動引数の `--model` を置き換え、`--resume` の位置は変わらない | `--model` を残す |
| 使い方が `near-limit` の `key-main` で `key:key-main` の子が起動し、起動引数に `apiKeyHelper` の `--settings` があり、環境に `ANTHROPIC_API_KEY` が無い。helper がキーの値だけを出す。`off` なら使わず、`off` の後の helper は何も出さない | キーを環境変数で渡す・`off` でも使う・値を画面か記録に出す |
| 動いているキーの子で `off` を打つと止めてよい時点か 60 秒で止まり、替え先が無ければ `account_unavailable`。設定ファイルへ既定でない `ANTHROPIC_BASE_URL` を足すと、道具の実行の途中でも検出した見回りで止まる。宣言した宛先の置き換え・管理の設定の `apiKeyHelper` の置き換えでも止まる | 宛先の変更で止めてよい時点を待つ・替え先が無いとき子を残す |
| 指紋の合わない宣言の従量の子と、401 を受けた `key:k1` の子が、検出から 5.5 秒以内に止まり、替え先が無ければ直し方の 1 行と `account_unavailable`。helper がキーを出すのは 60 秒に 3 回までで、10 本同時に呼んでも超えない。設定ファイルの `env` で `NDF_RELAY_DIR` を替えても、helper は起動引数の記録先にだけ書く | `stalled` で残す・照合と記録を別の錠で行う・記録先を環境変数から決める |
| `config-gate` が比べる範囲の変わった保存を拒み、許可の追加と skills の編集は通す | 宛先の変更を通す・関係の無い変更で止める |

## 関連リンク

- [ndf-relay-account-switching.md](ndf-relay-account-switching.md)（いつ退避し、いつ戻るか）
- [ndf-relay-account-config-dir.md](ndf-relay-account-config-dir.md)（認証の上書きの判定 `effective_env`・脅威の範囲）
- [ndf-relay-plan-claude-accounts.md](ndf-relay-plan-claude-accounts.md)（`ndf plan` のキーの子と親の接続の識別）
- 課題: #3864 の決定 8・12。範囲外として起票したもの: #3991・#4066・#4124・#4129・#4139・#4140・#4144・#4145・#4146
