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
指定は置き場の `pinned.json` に書き、区間の起動のたびに読む。指定が使えれば選ぶ順の組に依らずそれで起動し（`account` の行の`reason` は `pinned`）、使えない（上限・認証の失敗・再登録が要る）間は今の選び方で替えて続け、使えるようになった後の区間の起動で
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
名前   識別           5 時間  7 日  モデル別の週  支出上限  枠の大きさ     残り  5時間枠  組                    状態
team   a@example.com  50%     3%    Fable 58%     no        210 / 1,100    105   有       five_hour_first（次）  使える
gmail  b@example.com  -       2%    -             no        210 / 1,100*   -     無       weekly_only            使える
```

**アカウントは選ぶ順の組の順に選ぶ（#2930）。** 5 時間の枠は使わなければ消えるため、5 時間の枠のあるアカウントを先に
使い、週だけの枠のアカウントへは後で回る。「組」の列が組の名前、「5時間枠」の列が使用率の応答に 5 時間の枠があるか
（有・無）、`（次）` が次に選ばれるアカウントである。上限にある・起動できないアカウントの組は `-` になる。

| 順 | 組 | 入るアカウント | 組の中の並べ方 |
| --- | --- | --- | --- |
| 1 | `five_hour_first` | 5 時間の枠があり、週の使用率が閾値 T 未満 | リセットまで持つものを先に、その中で 5 時間の枠のリセットの早い順。持たないものは使い切るまでの時間の長い順 |
| 2 | `weekly_only` | 5 時間の枠が無く、週の使用率が閾値 T 未満 | 組 1 と同じ並べ方を週の枠で |
| 3 | `over_threshold` | 週の使用率が閾値 T 以上 | 週の使用率の小さい順 |
| 4 | `usage_unknown` | 使用率を読めない | 名前の順 |

週の使用率は、週の枠とモデル別の週の枠（`limits[]` の `weekly_scoped`）の使用率の最大である。使い切るまでの時間は、
今の枠の使用率 ÷ 枠の開始（リセット時刻 − 枠の長さ）からの経過で速さを見積もり、残りをその速さで割る。使用率が 0 なら
リセットまで持つ。閾値 T は `account api-key-use --weekly-at <百分率>` で宣言し（既定 85%）、API キーへ逃がす条件と同じ値を使う。
`NDF_ACCOUNT_SWITCH_AT` は読まない。

**今のアカウントが最良の候補と同じ組にあれば替えない（切り替えの余裕）。** 替えるのは組が変わったときだけである
（5 時間の枠がリセットされたアカウントが組 1 へ戻った・今のアカウントの週の使用率が T を超えた・今のアカウントが上限に当たった）。
選び直すのは区切り（ラッパーの区間の起動）と `supervise.py` の起動のたびで、戻るためだけに使用率を取りに行ったり区切りを早めたりしない。
復帰先や戻り待ちの印は持たず、その時点の観測だけで決める。

「枠の大きさ」と「残り」の列は表示だけで、選び方には使わない。枠の大きさは `account capacity` の宣言（`*` 付き）→
`.credentials.json` の `rateLimitTier` から引く対応表の順に決まる。

| `rateLimitTier` | 5 時間の枠（USD） | 週の枠（USD） |
| --- | ---: | ---: |
| `default_claude_max_20x` | 210 | 1,100 |
| `default_claude_max_5x` | 52.5 | 640（仮の値） |

支出上限は
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
| 起動 | `account use` の指定が使えればそれを使う。無ければ区間ごとに選ぶ順の組で選び直し、今のアカウントが最良の候補と同じ組にあれば替えない | 起動したときのアカウント（`NDF_CLAUDE_ACCOUNT`）から始め、起動のたびに同じ規則で選び直す |
| 利用上限（5 時間・7 日・支出上限） | 子の応答が上限で終わると（`StopFailure` hook の `limit.json`）、次の区間を別のアカウントで `claude --resume <会話> "<最初の入力>"` として起動する。最初の入力は `ndf-next` があればそれ、無ければ未達の `/goal <条件>`、それも無ければ切り替えの理由に合う定型の文（例: `利用上限でアカウントを替えた。中断したところから続ける`）。**バックグラウンドの作業が残っている間は子を終えない** | 待たずに別のアカウントで同じ呼び出しをやり直す |
| 認証が通らなかった（401） | 子の応答が認証の失敗で終わると、登録済みアカウントならそのアカウントを 1 時間候補から外し、上限のときと同じ順（使える登録済みアカウント → 従量の接続の宣言）で次の区間を起動する（最初の入力は `認証が通らなかったためアカウントを替えた。中断したところから続ける`）。既定のログインでも同じく替え、登録が 1 つ以下でも替えた先を以後の区間で使い続ける。従量の接続の 401 では替えない。替え先が無ければ、ログインの形に合う直し方を 1 行出して子を残す（既定のログイン: claude の中で `/login`。登録済みアカウント: `relay.py account add <名前>`。従量の接続: `relay.py account check metered` で宣言を確かめる）。1 つの区間につき 1 度だけ | 結果が認証の失敗なら、そのアカウントを外して同じ呼び出しをやり直す |
| 組が変わった（週の使用率が T を超えた・5 時間の枠がリセットされた） | **次のカットポイントで**選び直して替える（`account` の行の `reason` は `rule`）。区間の間は使用率を取りに行かない | 次の起動で替える |
| すべて上限 | 宣言があれば従量の接続へ移る。無ければ 1 行を出して子を残す | 宣言があれば従量の接続へ移る。無ければ今と同じ上限待ち |
| 従量の接続で動いている間 | 次のカットポイントで、候補のアカウント（組を問わない）があれば戻す | 起動のたびに戻れるかを確かめる |

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
| `NDF_ACCOUNT_CHECK_INTERVAL` | 300 | 使用量の控えの寿命（秒）。控えの中のリセット時刻を過ぎていれば、寿命の中でも取り直す |

替えるたびに画面へ `ndf-relay:` の 1 行（例: `利用上限（five_hour）に達したため、アカウントを work1 から work2（b@example.com）へ替えて続ける`）
が出て、`log.jsonl` に `account` の行が残る。行には起動先の決定の規則の名前（`rule`）・組（`account_rule`）・根拠の値
（`evidence`。週の使用率・5 時間の使用率・リセット時刻・閾値 T など）が載る。

## worker の claude -p を API キーで動かす

**サブスクリプション（Max・Ultra・Team）にリンクした Console 組織の API キーを登録すると、`supervise.py` が起動する
`claude -p`（work・worker・判定・遅れの判定・Pull Request）を月の API クレジットで動かせる。** API クレジットは
利用上限（5 時間・週）と別枠なので、使った分だけ利用上限の消費を逃がせる。ラッパーが起こす対話のセッションには使わない
（対話の Claude Code は API クレジットで払えない）。cross-review の担当の CLI にも使わない。Console 組織の作成・
リンク・キーの発行は利用者が Console で行う。

```bash
printf '%s\n' "$KEY" | python3 ~/.claude/ndf/relay.py account add-api-key key-main --account main --monthly 200   # 確かめてから保存する
python3 ~/.claude/ndf/relay.py account add-api-key key-gmail --account gmail --key-file ~/gmail.key
python3 ~/.claude/ndf/relay.py account api-key-monthly key-gmail 100   # 登録済みのキーへ月の額を宣言する（0 で外す）
python3 ~/.claude/ndf/relay.py account api-key-use near-limit --weekly-at 85   # 使い方（off・always・near-limit）と閾値 T
python3 ~/.claude/ndf/relay.py account list                 # キーごとに月の額・直近 7 日の使用額・週の利用枠
python3 ~/.claude/ndf/relay.py account check key-main       # 確かめ直す（状態は記録しない）
python3 ~/.claude/ndf/relay.py account remove key-gmail
```

- **キーの値は標準入力の 1 行目か `--key-file` で渡す。** 端末から打つと表示しない入力で聞く。引数では受けない（`ps` と
  シェルの履歴に残るため）。値はアカウントの置き場の `api-keys.json`（0600）にだけ置き、画面・進捗ログ・`account list`・
  使用量の帳簿には出ない
- **登録の前に Messages API へ `max_tokens` 1 の要求を 1 回打って確かめる。** 認証の失敗・残高切れ・通信の失敗なら、理由
  （`reason` は `auth_failed`・`credit_low`・`network`・`unclassified`）を出して終了コード 1 で終わり、何も保存しない
- **1 つの登録済みアカウントに結び付けられるキーは 1 本である。** 名前を変えれば 2 本以上を登録でき、同じ名前で登録し直すと
  置き換わる。登録済みアカウントの名前・`metered` と重なる名前は `name_conflict`、登録に無いアカウントは `not_registered`、
  別のキーが結び付いたアカウントは `account_taken` で断る（どれも終了コード 1）。`account add` も API キーの名前を断る
- **月の額（`--monthly`・`api-key-monthly`）は、月に付与されるクレジットの額（ドル）である。** 宣言の無いキーは
  `near-limit` で選ばれない（一覧の「枠の大きさ」の列が `月 未宣言`）。登録し直さずに足せる。正の数でなければ終了コード 2
- 組織に買ったクレジットか自動チャージがあると、月の API クレジットを使い切った後もそちらから払われる。NDF は検出しない
  ので、費用の上限は Console の workspace の spend limit で決める（登録のときにも 1 行出る）

**使い方の宣言**は置き場の `api-keys.json` の `use` に、閾値 T は `weekly_at` に 1 つだけ持ち、無い・読めないときは `off`・85% として動く。

| 宣言 | API キーで起動する条件 |
| --- | --- |
| `off`（既定） | 起動しない。起動の環境・アカウントの選び方・上限の後の切り替えは登録が無いときと同じ |
| `always` | 外していないキーがあれば、すべての呼び出しで。名前の順の先頭を選ぶ |
| `near-limit` | 再登録が要るものを除いた登録済みアカウントの週の使用率がすべて閾値 T 以上のときだけ、週の利用枠の残るキーで。使用率を読めないアカウントが 1 つでもあれば使わない。5 時間の上限では使わず、解除を待つか別のアカウントで起動する |

**週の利用枠**は月の額 × 7 ÷ 30 で、直近 7 日（判定の時刻から 7×24 時間前まで）の使用額がこれ未満のキーだけを使う。
使用額は使用量の帳簿のうち、そのキーの名前（`api_key` の列）が載った行の `cost_usd` の合計で、判定のたびに読み直す。
付与日・残高は持たないので、月が替わっても登録し直しは要らない。2 本以上あれば、使用の割合（直近 7 日の使用額 ÷ 週の利用枠）の
最も小さいキーを選ぶ。どれか 1 つのアカウントの週の枠がリセットされると、次の起動ではキーを使わない（控えのリセット時刻を
過ぎたら取り直すため）。near-limit の条件がそろってもキーを使えないときは、アカウントの選び方へ戻り、規則の名前でわけを示す。

| 規則の名前 | わけ |
| --- | --- |
| `near_limit_key` | キーで起動した |
| `near_limit_ledger_unreadable` | 帳簿を読めない |
| `near_limit_no_declared_key` | 月の額を宣言したキーが無い |
| `near_limit_budget_used_up` | 宣言のあるキーがすべて週の利用枠を使い切った |

子の環境には `ANTHROPIC_API_KEY` に選んだ 1 本を入れ、OAuth の
トークン・`ANTHROPIC_AUTH_TOKEN`・Bedrock と Vertex の変数・従量の接続の宣言は外す。`ANTHROPIC_BASE_URL` が
`https://api.anthropic.com` の外（ゲートウェイなど）を指すときは、キーを別のサービスへ送らないよう、登録済みのキーを使わずに今の認証で起動する。

| 起動の結果 | 扱い |
| --- | --- |
| 残高切れ（"credit balance" と "too low"）・認証の失敗（401）・利用上限・429 | その呼び出しでだけキーを外し、次のキー、無ければアカウントの選び方で同じ呼び出しを起動し直す。状態は記録せず、次の呼び出しではまた候補に入る |

ステップは止まらない。進捗ログには、使うキーが替わったときの `account` の行（`to_key`・`rule`・`evidence`）、near-limit の
条件がそろってキーを使えなかったときの `account` の行（`reason` が `launch_rule`）、残高切れ・認証の失敗の
`api_key` の行（キーの名前と理由）が残る。どれも決定が変わったときだけ書く。旧版が書いた `api-key-state.json` は読まず、消さない。
`api-keys.json` が壊れていれば、キーを使わずに動き、最初の呼び出しの前に 1 行出す。
