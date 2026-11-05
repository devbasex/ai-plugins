# 利用上限でアカウントを替えて続ける

[relay.md](relay.md)（ラッパーでカットポイントを自動にする）から分けた、利用上限でラッパーが claude のアカウントを替えて続ける規約である。

**claude のアカウントを 2 つ以上登録しておくと、利用上限で止まらずに別のアカウントで続く。** 端末で打てば
`claude auth login` が URL を示し、認可コードの貼り付けを待つ（1 回で終わる）。端末でなければ 2 回に分かれる（下の
「AI が代わりに打つとき」）。**Linux（コンテナを含む）だけで使える。** macOS の
claude はログインの資格情報を Keychain に置き、設定ディレクトリの `.credentials.json` を書かないため、macOS の
`account add` は登録の前に止まる。1 つのメールアドレスで複数の組織（個人と Team など）に属していれば、組織ごとに別の
アカウントとして登録できる（ログインのときに組織を選ぶ。同じメールアドレスと組織の 2 つ目は拒む）。

```bash
python3 ~/.claude/ndf/relay.py account add work1   # 専用の設定ディレクトリで claude auth login が動く
python3 ~/.claude/ndf/relay.py account add work2
python3 ~/.claude/ndf/relay.py account list        # 推論を呼ばずに残量を出す（--json もある）
python3 ~/.claude/ndf/relay.py account capacity work2 - 900   # 週の枠の大きさだけを 900 USD と宣言する（- - で外す）
python3 ~/.claude/ndf/relay.py account remove work2
```

**`account use <名前>` で、動いているラッパーでも次の区間の起動（`ndf-next` の再起動・起動の直後）から使うアカウントを指定できる**（`python3 ~/.claude/ndf/relay.py account use work1`）。
指定は置き場の `pinned.json` に書き、区間の起動のたびに読む。指定が使えれば閾値や残りの量に依らずそれで起動し（`account` の行の`reason` は `pinned`）、使えない（上限・認証の失敗・再登録が要る）間は今の選び方で替えて続け、使えるようになった後の区間の起動で
戻る。`--clear` で外すと今の選び方へ戻る。登録に無い名前と `再登録が要る` 名前は終了コード 1（`reason` は `not_registered`・`needs_relogin`）で断る。同じ置き場を使うラッパーすべてに効く。`account list` は名前に `（指定）`（`--json` は `pinned`）、`relay.py ls` は表の下の 1 行と `metrics.pinned_account` で示す。動いているラッパーは [relay.md](relay.md) の「ラッパーの入れ替え」で新しい版へ替わった後の区間から読む。

**`account` の副命令はすべて `--yes`（確認を飛ばす）と `--json`（結果を JSON 1 つで標準出力へ返す）を受ける。**
対話で聞く値（名前・プロファイル・地域・モデル・確認）はすべて引数でも渡せ、揃っていれば何も聞かない。標準入力が
端末でなければ入力を待たず、足りない引数の名前（`--json` なら `missing` と `candidates`）を示して終了コード 2 で終わる。

## AI が代わりに打つとき

conductor・worker は Bash から打つ（標準入力は端末でない）。人が要るのは、ブラウザでの認可と、画面に出た認可コードを
AI へ返すことだけである。

```bash
python3 ~/.claude/ndf/relay.py account add work2 --json
# → {"ok": true, "stage": "awaiting_code", "url": "https://…", "expires_at": "…"}。URL を利用者へ渡す
printf '%s\n' '<認可コード>' | python3 ~/.claude/ndf/relay.py account add work2 --code - --json
# → {"ok": true, "stage": "registered", "email": "…"}
```

1 回目は `claude auth login` を待たせたまま（待機中のログイン）認可の URL を返す。登録の途中の状態は置き場の
`.pending-<名前>/` に残り、10 分を過ぎると次の `account` の呼び出しで捨てる（2 回目は `expired` で 1）。認可コードは
`--code -`（標準入力の 1 行）で渡す。引数に載せると `ps` と打った側の記録に残る。

```text
名前   識別           5 時間        7 日         モデル別の週        支出上限      枠の大きさ     残り  状態
work1  a@example.com  50%（04:59）  3%（09-29）  Fable 58%（09-29）  達していない  210 / 1,100    105   使える
work2  b@example.com  40%（05:40）  0%（09-30）  -                   達していない  52.5 / 900*    31.5  使える
```

**アカウントは残りの量（USD 換算）の大きい順に選ぶ。** 残りの量は、5 時間の枠・週の枠・モデル別の週の枠（`limits[]` の
`weekly_scoped`）ごとの「枠の大きさ ×（1 − 使用率 / 100）」の最小である。枠の大きさは `account capacity` の宣言（`*` 付き）
→ `.credentials.json` の `rateLimitTier` から引く対応表の順に決まる。モデル別の週の枠の大きさは週の枠と同じとする。

| `rateLimitTier` | 5 時間の枠（USD） | 週の枠（USD） |
| --- | ---: | ---: |
| `default_claude_max_20x` | 210 | 1,100 |
| `default_claude_max_5x` | 52.5 | 640（仮の値） |

表に無い tier（Team premium など）で宣言も無いアカウントは、枠の大きさが `-` になり、残りの量の分かるアカウントの後ろへ
使用率の小さい順で並ぶ。使用率が切り替えの閾値以上のアカウントは、閾値未満のアカウントより後に試す。支出上限は
`extra_usage.spend_limit_reached` が真のときだけ達したとする。`spend.percent` ≥ 100 か `spend.severity` = `critical`
（追加の利用の予算の使い切りと見られる）は判定に使わず、生の旗が真でなければ支出上限の列に `no（追加の利用は上限）` と
補足する（`--json` は `spend_full`）。生の旗が偽でも spend が 100%・critical を返すことがあり、解除の時刻を持たないため、
判定に使うと上限から外れなくなる（#2036）。

識別の列は、同じメールアドレスの登録が 2 件以上あるときだけ `a@example.com（Team A）` のように組織名を添える。
従量の接続の宣言があれば、最後に `metered` の行（識別は `Bedrock（<プロファイル>・<地域>・<モデル>）` か
`環境変数（<変数の名前>）`、状態は `保存した宣言`・`環境変数の宣言`・`壊れている（宣言なしとして扱う）` のどれか）が出る。

状態は `使える`・`上限（<リセット時刻>）`・`支出上限`・`残量不明`・`認証の失敗`・`再登録が要る` の 6 つ。`再登録が要る` は
同じ名前で `account add` し直す。`認証の失敗` は子の応答が認証の失敗で終わったアカウントで、後の残量の取得が通るか 1 時間で
`使える` へ戻る。

認証ファイルのリフレッシュトークンが空のアカウント（claude がトークンの更新を断られると空にする）は、アクセストークンが
期限内でも `再登録が要る` になり、切り替え先に選ばれない。ラッパーは起動のたびに、`再登録が要る` アカウントを 1 つにつき
1 行（`ndf-relay: アカウント <名前> は再登録が要る。python3 ~/.claude/ndf/relay.py account add <名前> で登録し直す`）で
知らせる。`account list` は認証ファイルを確かめないので、ラッパーの起動か切り替えの前は `使える` と出ることがある。
同じアカウントを既定のログイン（`~/.claude`）と登録済みアカウントの両方で別々にログインしない（1 アカウント = 1 ログイン）。
別々のログインは別々のリフレッシュトークンを持ち、片方の更新が断られると、そちらだけが使えなくなる。

**アカウントの設定ディレクトリが、そのアカウントで起動する claude の `CLAUDE_CONFIG_DIR` になる。** 置き場は
`${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/accounts/`（0700）で、アカウントごとの `<名前>/`（0700）に認証ファイル
（`.credentials.json`）・`.claude.json`・NDF の記録（0600）を実体で持ち、ほかは共有の設定ディレクトリ（`~/.claude`）の直下への
symlink にする。会話の記録（`projects`）・設定（`settings.json`）・プラグインは共有側の 1 か所に入るので、アカウントを替えても
`--resume` と設定がそのまま効く。子へはトークンもスコープも渡さず、claude が自分の認証ファイルでトークンを更新する（8 時間で
認証切れにならない）。`/status`・statusline・`claude auth status` には、そのアカウントの email と契約の種類が出る。共有の
`~/.claude/.credentials.json` は読み書きしないので、素の claude で `/login` を打っても、ラッパーの下の claude のアカウントは
替わらない。

| 子の環境の変数 | 値 |
| --- | --- |
| `CLAUDE_CONFIG_DIR` | アカウントの設定ディレクトリ（`<置き場>/<名前>`） |
| `NDF_CLAUDE_ACCOUNT` | アカウントの名前 |
| `NDF_SHARED_CONFIG_DIR` | 元の `CLAUDE_CONFIG_DIR`（無かったら空文字）。子の中の `supervise.py` が共有の設定ディレクトリを求める |
| `CLAUDE_CODE_PLUGIN_CACHE_DIR` | `<共有>/plugins`（元にあればその値）。子が導入したプラグインの記録を共有側のパスにする |
| `CLAUDE_CODE_OAUTH_TOKEN`・`CLAUDE_CODE_OAUTH_SCOPES` | 渡さない（元の環境にあっても外す） |

起動の前に、ラッパーと `supervise.py` はアカウントの設定ディレクトリを用意する。共有側に増えた項目の symlink を足し、
共有の `~/.claude.json` の `projects`（プロジェクトの信頼・MCP サーバーの許可）と `mcpServers` をアカウント側の `.claude.json` へ
コピーする（`oauthAccount` はアカウントごとのまま）。ラッパーは、セッションの切れ目と終了時に、アカウント側で変わった分を共有の
`~/.claude.json` へ書き戻す。claude.ai のコネクタは、claude が認証ファイルのスコープを自分で読んで取りに行く。従量の接続の子は
共有の設定ディレクトリで起動する（`CLAUDE_CONFIG_DIR` を元の値へ戻す）。

次の表に当たるアカウントでは claude を起動せず、次の候補へ移る。理由は画面の 1 行（`ndf-relay: アカウント <名前> を使わない（<理由>）`）と
`log.jsonl` の `account_dir` の行に残る。

| 理由 | 直し方 |
| --- | --- |
| 認証ファイルが別のアカウントのものになっている（アカウントの claude の中で `/login` を打った） | `account add <名前>` で登録し直す |
| 会話の記録の置き場 `projects` が共有の設定ディレクトリを指していない | アカウントの設定ディレクトリの `projects` の中身を共有の `~/.claude/projects/` へ移し、`projects` を消す（次の起動で symlink になる） |
| アカウント側の `.claude.json` が壊れている | 下の 2 つのどちらか |

アカウント側の `.claude.json` が JSON として読めないときは、NDF は直さない。アカウントの設定ディレクトリの
`backups/.claude.json.backup.<ミリ秒>` のうち JSON として読める新しいものを `.claude.json` へ `cp` するか、`.claude.json` を
消す（`oauthAccount` は claude が次の応答で取り直し、最初の案内の印は共有側からコピーされる）。どちらの後も、次の起動で共有側の
`projects` と `mcpServers` にそろう。

**`/mcp` で OAuth 認証した MCP サーバー（claude.ai のコネクタでないもの）は、アカウントごとに認証が要る。** そのトークンは
認証ファイルに入るため、共有側で済ませた認証はアカウントの子に届かない。アカウントごとに 1 度、そのアカウントの対話の
セッションで `/mcp` から認証する。対話を持たない claude -p の worker は認証し直せないので、認証の済んでいないアカウントで
動く worker はそのサーバーの Tool を使えない。登録し直すと、そのアカウントでもう 1 度認証が要る。

同じ名前で登録し直しても、アカウントの設定ディレクトリの symlink と、claude がアカウント側に作った項目（`plans` など）は残る。
共有側に無くアカウント側にだけある項目は、アカウントを替えた先から見えない。`account_dir` の行の `local_only` に名前が出るので、
共有したいものは共有の `~/.claude/` へ移す（次の起動で symlink になる）。登録を外しても（`account remove`）共有側は消えない。

| いつ | ラッパー | `supervise.py`（プランの claude -p） |
| --- | --- | --- |
| 起動 | `account use` の指定が使えればそれを使う。無ければ区間ごとに、上限に達していないアカウントのうち残りの量の最も大きいものを選ぶ。今のアカウントが閾値未満なら替えず、閾値を超えていても候補の残りの量が今以下なら替えない | 起動したときのアカウント（`NDF_CLAUDE_ACCOUNT`）で呼ぶ |
| 利用上限（5 時間・7 日・支出上限） | 子の応答が上限で終わると（`StopFailure` hook の `limit.json`）、次の区間を別のアカウントで `claude --resume <会話> "<最初の入力>"` として起動する。最初の入力は `ndf-next` があればそれ、無ければ未達の `/goal <条件>`、それも無ければ切り替えの理由に合う定型の文（例: `利用上限でアカウントを替えた。中断したところから続ける`）。**バックグラウンドの作業が残っている間は子を終えない** | 待たずに別のアカウントで同じ呼び出しをやり直す |
| 認証が通らなかった（401） | 子の応答が認証の失敗で終わると、登録済みアカウントならそのアカウントを 1 時間候補から外し、上限のときと同じ順（使える登録済みアカウント → 従量の接続の宣言）で次の区間を起動する（最初の入力は `認証が通らなかったためアカウントを替えた。中断したところから続ける`）。既定のログインでも同じく替え、登録が 1 つ以下でも替えた先を以後の区間で使い続ける。従量の接続の 401 では替えない。替え先が無ければ、ログインの形に合う直し方を 1 行出して子を残す（既定のログイン: claude の中で `/login`。登録済みアカウント: `relay.py account add <名前>`。従量の接続: `relay.py account check metered` で宣言を確かめる）。1 つの区間につき 1 度だけ | 結果が認証の失敗なら、そのアカウントを外して同じ呼び出しをやり直す |
| 使用率が閾値を超えた | 定期の確認（推論なし）が 1 行を出し、**次のカットポイントで**替える | — |
| すべて上限 | 宣言があれば従量の接続へ移る。無ければ 1 行を出して子を残す | 宣言があれば従量の接続へ移る。無ければ今と同じ上限待ち |
| 従量の接続で動いている間 | 定期の確認で上限の外れたアカウントを見つけたら、次のカットポイントで戻す | 起動のたびに戻れるかを確かめる |

**従量の接続（Bedrock）は `account add-bedrock` で登録する。** AWS のプロファイルは先に `aws configure sso` などで
作っておく。候補（`aws configure list-profiles`）が 1 つなら確認だけ、複数なら番号で選ぶ。地域はプロファイルの `region`、
モデルは Bedrock の推論プロファイルのうち Claude のものから選ぶ。保存の前に `aws sts get-caller-identity` と
`aws bedrock-runtime converse`（最大 1 トークン。1 回分の費用が掛かる）で呼べるかを確かめ、呼べなければ理由（`認証切れ`・
`権限が無い`・`地域で使えない`・`モデルが有効でない`）と AWS のエラーの種類の名前を出して保存しない。macOS でも使える。

```bash
python3 ~/.claude/ndf/relay.py account add-bedrock        # 端末で選ぶ
python3 ~/.claude/ndf/relay.py account add-bedrock --profile bedrock-dev --model <モデルの ID> --yes --json
python3 ~/.claude/ndf/relay.py account check metered      # 今の宣言で呼べるかを確かめる
python3 ~/.claude/ndf/relay.py account remove metered     # 保存した宣言を外す
```

宣言はアカウントの置き場の `metered.json`（0600）に保存し、次の起動から効く。シェルの設定ファイルは書き換えない。
保存するのは変数の名前と値（`CLAUDE_CODE_USE_BEDROCK`・`AWS_PROFILE`・`AWS_REGION`・`ANTHROPIC_MODEL`）だけで、AWS の鍵は
`~/.aws/` に残る。保存した宣言で起動する子からは、環境の AWS の鍵の変数を外す。宣言は 1 つだけで、置き換えは確認
（`--yes`）が要る。保存先が壊れていれば、ラッパーは区間 1 の起動で、`supervise.py` は最初の呼び出しの前に 1 行出し、
宣言なしとして扱う。

**環境変数で上書きする場合は `NDF_SUPERVISE_CLAUDE_FALLBACK` を定義する**（`KEY=VALUE` を空白区切り。値はリポジトリに
書かない）。定義されていれば（空でも）保存した宣言は読まない。Bedrock 以外（API キーなど）もこの形で宣言する。

```bash
export NDF_SUPERVISE_CLAUDE_FALLBACK='CLAUDE_CODE_USE_BEDROCK=1 AWS_PROFILE=<名前> AWS_REGION=us-east-1'
```

従量の接続で起動する子にはアカウントの設定ディレクトリを渡さず、アカウントで起動する子には宣言の変数を渡さない（混ぜない）。記録と画面に
出るのは変数の名前だけである。**登録が 1 つ以下なら今と同じ**（ラッパーは上限で何もしない。`supervise.py` は宣言を
呼び出しごとに 1 度だけ試してから上限待ち）。切り替えに LLM は使わない。

| 設定 | 既定 | 意味 |
| --- | --- | --- |
| `NDF_ACCOUNT_CHECK_INTERVAL` | 300 | 使用量の取得先を 1 アカウントにつき何秒に 1 回まで呼ぶか。定期の確認の間隔も兼ねる |
| `NDF_ACCOUNT_SWITCH_AT` | 90 | 閾値（%）。100 以上なら閾値では替えない |

替えるたびに画面へ `ndf-relay:` の 1 行（例: `利用上限（five_hour）に達したため、アカウントを work1 から work2（b@example.com）へ替えて続ける`）
が出て、`log.jsonl` に `account` の行が残る。
