# #1576: relay: 登録済みアカウントの claude が 8 時間で認証切れにならず、/status に実際のアカウントが出るようにする（置き場を CLAUDE_CONFIG_DIR にする）

正は課題の本文（#1576）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何が起きているか（実例）
>
> 2026-09-30 から翌朝にかけて、relay（`relay.py`）の下の作業が認証まわりで繰り返し止まった。
>
> | 時刻（JST） | 起きたこと | 記録 |
> | --- | --- | --- |
> | 16:21・翌 00:22 | アクセストークンの期限（8 時間）で `401 OAuth access token has expired`。区間が切れ、アカウントが替わった | #1575 |
> | 終日 | `/status` が `Auth token: CLAUDE_CODE_OAUTH_TOKEN` としか出さず、どのアカウントで動いているか分からない | 利用者の報告 |
> | 終日 | statusline が実際と別のアカウント（共有の `~/.claude/.credentials.json` の持ち主）を示す | #1575 |
> | 翌 08:11 | 手で `/login` を打つと共有の認証が書き換わり、別の claude の表示も変わる | 利用者の報告 |
>
> 原因は 1 つである。#1389 と #1523 は、選んだアカウントのアクセストークンを環境変数 `CLAUDE_CODE_OAUTH_TOKEN` で子の claude へ渡す形を採った。この形では、Claude Code はトークンを `refreshToken: null` の認証として扱うため、**自分で更新できず、email と契約の種類も知らない**。
>
> ## 期待する振る舞い
>
> - 登録済みアカウントで起動した claude は、8 時間を超えて動いても認証切れで止まらない
> - `/status` と statusline に、実際に接続しているアカウント（email・組織・契約の種類）が出る
> - アカウントを替えても、会話（`--resume`）・プラグイン・設定・claude.ai のコネクタは今と同じに使える
> - 手の `/login` や別の claude が共有の認証を書き換えても、relay の下の claude のアカウントは変わらない
>
> ## 確定案
>
> **アカウントの置き場 `~/.claude/ndf/accounts/<名前>/` そのものを、子の `CLAUDE_CONFIG_DIR` にする。** 置き場には認証に要るものだけを実体で持ち、ほかは共有の `~/.claude/` への symlink にする。トークンの環境変数は渡さない。
>
> ```
> ~/.claude/ndf/accounts/ohama-personal/      ← 子の CLAUDE_CONFIG_DIR
> ├── .credentials.json        実体（claude が自分で更新する）
> ├── .claude.json             実体（oauthAccount を持つ）
> ├── account.json usage.json  実体（NDF が書く。今のまま）
> ├── projects -> ~/.claude/projects          会話の記録は共有
> ├── plugins -> ~/.claude/plugins
> ├── settings.json -> ~/.claude/settings.json
> └── …（ほかの共有物も symlink）
> ```
>
> | # | 決めること | 中身 | 根拠（実験） |
> | --- | --- | --- | --- |
> | 1 | 子の環境 | `CLAUDE_CONFIG_DIR=<置き場>` と `NDF_CLAUDE_ACCOUNT=<名前>` を渡す。`CLAUDE_CODE_OAUTH_TOKEN` と `CLAUDE_CODE_OAUTH_SCOPES` は渡さない | E2・E3・E7 |
> | 2 | 認証ファイルは置き場の実体 | `.credentials.json` を symlink にしない。コピーも作らない（登録の 1 か所だけ） | E1 |
> | 3 | 共有するものは symlink | 共有の `~/.claude/` にあるもののうち、アカウント固有の一覧に無いものを、起動のたびに足りない分だけ symlink にする。`ndf` も symlink にする（子の中の `store_dir()` が同じ置き場を指す） | E4・E5・E7 |
> | 4 | アカウント固有のもの（symlink にしない） | `.credentials.json` `.claude.json` `backups/` `policy-limits.json` `policy-limits.json.stamp.json` `remote-settings.json` `mcp-needs-auth-cache.json` `.ndf-statusline-auth.json` `.oauth_refresh.lock*` | E3・E7 |
> | 5 | NDF の排他ファイルの置き場を変える | `accounts/<名前>.lock` をやめ、`accounts/.locks/<名前>.lock` などの置き場の隣に来ないパスにする | E7・E10・E11・本体のコード |
> | 6 | 動いている claude のトークンは claude が更新する | NDF は区間の途中のトークンに触れない。`UsageWatch` の「動いている区間は更新しない」と 401 の後の切り替えは要らなくなる | E1・E12 |
> | 7 | NDF の更新は使っていないアカウントだけ | 候補を選ぶときの使用量の取得のために残す。claude と同じ排他（置き場の中の `.oauth_refresh.lock` のディレクトリ）を取り、一時ファイルからの rename で書く | E9・E12・本体のコード |
> | 8 | `.claude.json` の共有する部分 | `projects`（信頼・`.mcp.json` の許可・ローカルの MCP サーバー）と利用者の `mcpServers` は、共有の `~/.claude.json` を正とする。起動の前に置き場へ写し、終わった後に変わった分だけ書き戻す。ほか（`oauthAccount`・キャッシュ）は置き場ごと | E3・E8 |
> | 9 | 従量の接続（Bedrock）は変えない | 共有の `~/.claude/` のまま起動する。`settings.json` の `env` に負ける件は #1543 で別に直す | — |
> | 10 | 再開の文を理由ごとに変える | `RESUME_TEXT`（`relay_lib/claude.py`）が認証切れでも「利用上限で」と書く件（#1575 の 2）をここで直す | #1575 |
> | 11 | 移行 | 登録し直さない。既存の置き場に symlink を足し、排他ファイルを移すだけ | E7 |
>
> #1389 の決定 1（設定ディレクトリは分けない）と #1523 の決定 1（トークンとスコープを環境変数で渡す）を置き換える。どちらも「会話の記録と設定が置き場へ移り `--resume` できない」を理由に退けていたが、共有物を symlink にすると成り立つ（E3）。
>
> ## 実験の記録（2026-10-01 08:00〜08:45 JST、Claude Code 2.1.285〜2.1.286、ndf 10.17.50）
>
> アカウントは ohama-personal（Max）と nyle-team（Team）。トークンの期限切れは `expiresAt` を過去へ書き換えて作った（本物の 8 時間の経過ではない）。
>
> | # | 試したこと | 結果 |
> | --- | --- | --- |
> | E1 | `.credentials.json` を登録への symlink にした設定ディレクトリで、期限切れのトークンで `claude -p` | claude が自分で更新した（残り 8.0 時間）。**リフレッシュトークンも変わる**（回転）。**書き込みは一時ファイルからの rename で、symlink は実体に置き換わった** |
> | E2 | tmux で対話の claude を 2 つ（2 アカウント）起動して `/status` | `Login method: Claude Max account`・`Email: takemi.ohama@gmail.com` と、`Claude Team account`・`Organization: Nyle`・`Email: takemi_ohama@nyle.co.jp`。statusline も修正なしで正しい |
> | E3 | 同時に 3 本（2 アカウント混在）の `claude -p`、片方で始めた会話をもう片方で `--resume`、`claude mcp list` | すべて応答。`--resume` で前の会話の内容を答えた。コネクタはアカウントごとに出た（claude.ai の行が 6 と 21） |
> | E4 | `.claude.json` を symlink にして `claude -p` | symlink は残り、参照先が書き直された（claude が実パスを解決する） |
> | E5 | `settings.json` を symlink にして対話で `/model sonnet` | symlink は残り、参照先に `model: sonnet` が入った |
> | E6 | 共有の `.claude.json` をそのまま写した設定ディレクトリ | `/status` の email が共有側のものになる。**email は `.claude.json` の `oauthAccount` から出る** |
> | E7 | 登録の置き場そのものを `CLAUDE_CONFIG_DIR` にし、symlink を 23 件足して、期限切れで同時 3 本 | **NDF の排他（`<名前>.lock`）を握った直後は 3 本とも更新できず、1 本が `Failed to refresh OAuth token: another Claude Code process is refreshing it or exited mid-refresh` で落ちた**。子の中の `store_dir()` は `ndf` の symlink 越しに同じ置き場を返した |
> | E8 | `oauthAccount` を消した `.claude.json` | `claude auth status` は取り直さない。`claude -p` を 1 回打つと、トークンからプロフィールを取り直して正しい email を書いた |
> | E9 | E7 の置き場で、NDF の排他を握らず 1 分以上おいて `claude -p` | 更新できた。排他は置き場の中の `.oauth_refresh.lock`（ディレクトリ）と `.oauth_refresh.lock.owner`（pid 入りの JSON）で、握っていたのは約 0.5 秒 |
> | E10・E11 | NDF の排他を握ったまま、または握った直後に 1 本・3 本 | 毎回失敗。`.oauth_refresh.lock` を作って 10 ミリ秒で消している |
> | E12 | NDF の排他が 60 秒以上古い状態で、期限切れで同時 3 本 | 3 本とも成功。1 本が排他を 0.6 秒握って 1 回だけ更新した |
> | E13 | 期限切れで `claude mcp list` | 推論を呼ばずに更新された（残り 8.0 時間） |
>
> E7・E10・E11 の原因は本体のコードで確かめた。Claude Code は更新のとき、設定ディレクトリの中の `.oauth_refresh.lock` に加えて、**旧形式の排他 `<設定ディレクトリの実パス>.lock`** も取る（どちらも 60 秒で stale）。置き場 `accounts/ohama-personal/` の旧形式の排他は `accounts/ohama-personal.lock` で、NDF の排他ファイルと同じパスになる。NDF が触ってから 60 秒は更新が通らない（確定案の 5）。
>
> 実験の後、置き場へ足した symlink と実験用のコピーは消した。3 アカウントの登録は `relay.py account list` で読める。
>
> ## 採らなかった形
>
> | 形 | 採らない理由 |
> | --- | --- |
> | 今の形（トークンとスコープを環境変数で渡す） | claude がトークンを更新できず、8 時間で必ず 401 になる。`/status` にアカウントが出ない |
> | 認証ファイルだけを実体にした別ディレクトリ（コピー） | 更新でリフレッシュトークンが変わるため、登録側のコピーが無効になる（E1） |
> | 認証ファイルを登録への symlink にする | claude の書き込みで symlink が実体に置き換わる（E1） |
> | 共有の `~/.claude/.credentials.json` をその場で差し替える | 同じディレクトリを読むすべての claude（ほかのコンテナを含む）のアカウントが同時に替わる。呼び出しごとに別のアカウントを選べない |
>
> ## 前提と、まだ確かめていないこと
>
> - 前提: 振る舞いの基準は Claude Code 2.1.286 である。更新の排他の名前と、`.claude.json`・`settings.json` の symlink の扱いは本体の内部の実装で、公開の文書には無い
> - 未確認 1: 本物の 8 時間の経過（サーバー側で失効したトークン）での更新。実験は `expiresAt` の書き換えで、トークン自体は有効だった
> - 未確認 2: NDF が更新して書いた認証ファイルを、動いている claude が読み直すこと（本体のコードでは、排他の中で読み直して `accessToken` が変わっていれば更新済みとして扱う）
> - 未確認 3: 確定案の 8 の書き戻しと、動いている claude の `.claude.json` の書き込みとの取り合い
> - 未確認 4: relay の下で `/login` を打つと、その置き場の認証が別のアカウントに書き換わる。`account.json` の email と食い違ったときの扱い
> - 未確認 5: 共有の `~/.claude/` に後から増えた項目と、claude が置き場に新しく作る項目（`agents/` `output-styles/` など）の扱い
>
> ## 受け入れ条件（案）
>
> - [ ] 1. 登録済みアカウントで起動した区間の claude で `/status` を打つと、そのアカウントの email と契約の種類が出る（実物で 2 アカウント）
> - [ ] 2. 子の環境に `CLAUDE_CODE_OAUTH_TOKEN` と `CLAUDE_CODE_OAUTH_SCOPES` が無く、`CLAUDE_CONFIG_DIR` が置き場を指す（ラッパーと `supervise.py` の両方。単体テスト）
> - [ ] 3. `expiresAt` が過去の置き場で claude を起動すると、claude がトークンを更新して応答する。NDF の排他を握っている間でも通る（実物で 1 回）
> - [ ] 4. 同じ置き場で同時に 3 本起動しても、3 本とも応答し、更新は 1 回だけ起きる（実物で 1 回）
> - [ ] 5. アカウントを替えた次の区間で、前の区間の会話を `--resume` で続けられる
> - [ ] 6. claude.ai のコネクタが、そのアカウントのものだけ `✔ Connected` で並ぶ（#1523 の受け入れ条件 1 を保つ）
> - [ ] 7. 共有の `~/.claude/.credentials.json` を読まず、書かない。手の `/login` の後も、区間の claude のアカウントが変わらない
> - [ ] 8. 共有の `settings.json` と `projects/` への書き込みが、置き場ではなく共有側に入る（symlink が実体に置き換わらない）
> - [ ] 9. 既に登録済みのアカウントを登録し直さずに効く
> - [ ] 10. 従量の接続で起動する子の環境に、アカウント由来の変数と置き場の `CLAUDE_CONFIG_DIR` が残らない
> - [ ] 11. 認証切れで区間を切り替えたときの再開の文が「利用上限で」にならない
> - [ ] 12. 既存の全体テストが通る
>
> ## 関連
>
> - #1575（8 時間ごとの認証切れ・再開の文・statusline）: 期待する動きの 1・2・4 はこの形で要らなくなり、3（再開の文）は確定案の 10 に含める。この課題の配布で閉じる
> - #1389（複数アカウントの自動切り替え）の決定 1、#1523（コネクタ）の決定 1: この課題で置き換える
> - #1543（従量の接続が `settings.json` の `env` に負ける）: 別に直す。2026-10-01 02 時台（JST）の従量の区間でも `API Error (jp.anthropic.claude-opus-5-5): 400 The provided model identifier is invalid` で再発した
> - #1382（statusline のアカウント表示）: 修正なしで正しく出る（E2）

（解釈:

- **「登録済みアカウントで起動した claude」** は、`lib/claude_accounts.py` の `account_env()` で登録済みアカウントの環境を組み立てて起動するすべての claude を指す。ラッパー（`relay_lib/`）が起動するセッションの claude と、`supervise.py`（`supervise_lib/claude.py`）が起動する claude -p の両方である。原文の「区間」は、ラッパーが起動する claude の 1 回の起動（用語集の「セッション」）を指す
- **原文の「置き場」「アカウントの置き場」は `accounts/<名前>/` を指す。** 用語集の「アカウントの置き場」は、それを並べた親の `accounts/` である。引用より後の節では、`accounts/<名前>/` を「アカウントの設定ディレクトリ」、ラッパーを通さずに起動した claude が使う `${CLAUDE_CONFIG_DIR:-~/.claude}` を「共有の設定ディレクトリ」と書く
- **原文の `E1`〜`E13` は実験の番号である。** 引用より後の節では実験を「実験 1」〜「実験 13」と書き、`E<番号>` はドメインイベントだけを指す
- **確定案は、利用者が実験で決めた「採る形」である。** 要求はこの形を前提に置き、満たすべき振る舞いを受け入れ条件にする。確定案の表の各行をどう実現するか（共有するものの一覧の持ち方・排他の新しいパス・`.claude.json` を写して書き戻す手順）は `design` が決める
- 「8 時間を超えて動いても認証切れで止まらない」は、「アクセストークンの期限が切れた状態から、claude が自分でトークンを更新して応答する」と読む（受け入れ条件 3・4）。本物の 8 時間の経過は配布の後に確かめる（未決 1）
- 「今と同じに使える」は、会話の `--resume`（受け入れ条件 5）・claude.ai のコネクタ（6）・共有の設定とプラグイン（8）・プロジェクトの信頼と MCP サーバー（19）が、アカウントを替える前と同じに効くことと読む）

## 目的

- 登録済みアカウントで起動した claude が、アクセストークンの期限（8 時間）をまたいでも認証切れで止まらない。利用者が `/login` を打ち直したり、認証切れのたびにアカウントが替わったりしない
- 利用者と AI が、動いている claude の接続先のアカウント（email・組織・契約の種類）を、`/status`・statusline・`claude auth status` から読める
- #1389 と #1523 で得た性質を保つ: 共有の設定ディレクトリの `.credentials.json` に触れない・アカウントを替えた先で `--resume` できる・プラグインと設定は 1 か所・claude.ai のコネクタが読み込まれる

## 調べたこと（2026-10-01、要求の工程で足したもの）

原文の実験の記録に加えて、今のコードと手元の環境を読んで確かめた。どれもトークンの値には触れていない。

- **今の子の環境**: `lib/claude_accounts.py` の `_account_env()` が `CLAUDE_CODE_OAUTH_TOKEN`・`CLAUDE_CODE_OAUTH_SCOPES`・`NDF_CLAUDE_ACCOUNT` を足す。ラッパー（`relay_lib/claude.py` の `section_env()`）と `supervise.py`（`supervise_lib/claude.py`）はどちらもこの 1 か所を通る
- **今の排他**: `_locked()` が `locks.exclusive(<アカウントの置き場>/<名前>)` を呼び、`accounts/<名前>.lock` を作る。手元のアカウントの置き場には、登録の無い名前の `nyle.lock` も残っている（登録は `nyle-personal`・`nyle-team`・`ohama-personal` の 3 つ）
- **今の再開の文**: `relay_lib/claude.py` の `resume_input()` は切り替えの理由を受け取らず、未達の `/goal` が無ければ固定の `RESUME_TEXT`（「利用上限でアカウントを替えた。…」）を返す。画面の 1 行は理由ごとに分かれている（`relay_lib/switch.py` の `_SWITCH_MESSAGES`）
- **NDF の SessionStart hook が `settings.json` を置き換えで書く**: `ensure-retention.sh`（47 行目）と `statusline-switch.sh`（103 行目）は、`${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json` を一時ファイルからの `mv` で書く。一時ディレクトリで同じ操作（symlink のパスへ `mv`）を打つと、**symlink は実体に置き換わり、参照先のファイルは変わらなかった**。アカウントの設定ディレクトリを `CLAUDE_CONFIG_DIR` にすると、この 2 本が共有の `settings.json` への symlink を実体に置き換える（確定案に無い。受け入れ条件 15）
- **`CLAUDE_CONFIG_DIR` から場所を導く NDF のコード**は、`lib/claude_accounts.py` の `store_dir()` のほかに、`relay_lib/common.py`（relay の置き場）・`lib/transcript_agents.py`（会話の記録の親）・`statusline.sh`（`.ndf-statusline-auth.json`）・`hooks/claude.json`（ランチャーの位置）・上の 2 本の hook にある。子の中ではどれもアカウントの設定ディレクトリの symlink 越しに読むことになる
- **共有の設定ディレクトリの直下には約 30 項目ある**（手元の環境）。確定案の 4 の一覧に無い NDF のファイルは `.ndf-retention-checked` `.ndf-retention.lock` `.ndf-statusline.lock` `ndf-statusline.sh` の 4 つである
- **手元の共有の `~/.claude.json` は symlink である**（`/persistent/group/.claude.json` を指す。大きさは約 164 KB）。`~/.claude.json` は `~/.claude/` の外にある
- **入れ子の起動がある**: セッションの claude（アカウント A の設定ディレクトリが `CLAUDE_CONFIG_DIR`）の中から `supervise.py` を呼ぶと、その環境を元に別のアカウント B か従量の接続の claude -p を起動する。このとき元の環境の `CLAUDE_CONFIG_DIR` は共有の設定ディレクトリを指していない

## 前提

- 前提 1: 振る舞いの基準は Claude Code 2.1.286 である。トークンを更新するときの排他の名前（`.oauth_refresh.lock`・`<設定ディレクトリの実パス>.lock`）、`.claude.json` と `settings.json` の symlink の扱い、`oauthAccount` の取り直しは本体の内部の実装で、公開の文書には無い。将来の版で変わったときは、受け入れ条件 1・3・4・8 の実測の手順で見つける
- 前提 2: 採る形は原文の確定案である（アカウントの設定ディレクトリを子の `CLAUDE_CONFIG_DIR` にし、トークンの環境変数を渡さない）。原文の「採らなかった形」は設計で選び直さない
- 前提 3: 登録が 1 つ以下で、アカウントの環境を組み立てずに起動するとき（#1389 の受け入れ条件 9）と、ラッパーを通さない起動（`command claude`）は変えない。どちらも共有の設定ディレクトリのまま動く
- 前提 4: 従量の接続（Bedrock・API キー）は共有の設定ディレクトリで起動する。従量の接続が共有の `settings.json` の `env` に負ける件は #1543 で直し、ここでは扱わない
- 前提 5: 動作環境は今のラッパーと同じ（擬似端末と symlink が使える Linux と macOS）である。symlink を作れないファイルシステムは対象にしない
- 前提 6: 実物のアカウントを使う確かめ（受け入れ条件 1・3・4・5・6・7・8 の実物の分）は、認証ファイルを claude に読ませ、期限を書き換える操作を含む。利用者か、利用者が許した conductor が行う。トークンの値を表示・記録しない
- 前提 7: 設計の承認（承認ゲート 1）と本番への配布の承認（承認ゲート 2）は人が行う。この変更は認証の渡し方を変え、認証ファイルの扱いを変えるため、MVV 判定に任せない
- 前提 8: 配布の後も、利用者が再起動するまでは古い版のラッパーが古い形（トークンを環境変数で渡し、`accounts/<名前>.lock` を取る）で動き続ける。新しい版と古い版が同じアカウントを同時に使う間は、実験 7 の失敗（古い版が `<名前>.lock` に触れてから 60 秒は claude の更新が通らない）が起こりうる。これは古い版のラッパーを再起動すれば消える

## 対象範囲

含む:
- 登録済みアカウントの子の環境の組み立て（ラッパーと `supervise.py` が共有する 1 か所）と、起動の前のアカウントの設定ディレクトリの用意（共有するものへの symlink・`.claude.json` の共有する部分）
- NDF のアカウントごとの排他ファイルの置き場の変更と、既存の登録の移行
- NDF がトークンを更新する条件の変更（動いている claude のアカウントは更新しない・claude の更新と同時に走らない）
- 従量の接続へ移るときに、アカウント由来の変数と `CLAUDE_CONFIG_DIR` を外す処理
- 切り替えの理由ごとの再開の文（#1575 の 2）
- 子の中で `CLAUDE_CONFIG_DIR` の下の共有ファイルへ書く NDF の hook（`ensure-retention.sh`・`statusline-switch.sh`）が、symlink を実体に置き換えないようにすること
- アカウントの登録の削除（`relay.py account remove`）で、共有の設定ディレクトリの中身を消さないこと
- 認証の渡し方を書いた文書（`development-workflow` の `references/relay.md`・`supervise_lib/plan.py` の説明・`lib/claude_accounts.py` の冒頭）と、用語集の「アカウントのスコープ」の意味の更新
- #1389 の決定 1 と #1523 の決定 1 を置き換えた記録（採った形と理由）

含まない:
- 従量の接続の起動の形を変えること、#1543 の修正
- アカウントの選び方・使用率の読み方・切り替えの閾値・上限の検知の変更
- アカウントの登録（`account add`）の手順の変更
- statusline のスクリプトの修正（実験 2 で、修正なしで正しく出る）
- 登録が 1 つ以下のときと、ラッパーを通さない起動の振る舞い
- 動いている古い版のラッパーを新しい形へ切り替えること（前提 8）
- Claude Code 本体の排他や書き込みの振る舞いを変えること

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | セッションか呼び出しのアカウントを選んだ | ラッパーのセッションの起動・`supervise.py` の呼び出しの起動かやり直し | 使えるアカウントが無い → 従量の接続か待ち（#1389 のまま） | — |
| E2 | 既存の登録を新しい形へ移した（排他ファイルを移した） | 新しい版が、そのアカウントの置き場を初めて使った | 古い版が同じアカウントを使っている → 前提 8。移せない → 起動を止めず、古い排他ファイルを取らない（受け入れ条件 9・13） | — |
| E3 | アカウントの設定ディレクトリを用意した（足りない symlink を足し、`.claude.json` の共有する部分を写した） | E1 | 同じ名前の実体が既にある・symlink を作れない → 実体を消さない。会話の記録が共有側へ入らない状態では、そのアカウントで起動しない（受け入れ条件 17・18）。ほかの項目の扱いは未決 5 | E1・E2 |
| E4 | 子の環境を組み立てた（`CLAUDE_CONFIG_DIR` と `NDF_CLAUDE_ACCOUNT` を足し、トークンとスコープの変数を外した） | E3 | — | E3 |
| E5 | 子の claude が起動し、アカウントの設定ディレクトリの認証ファイルで認証した | E4 | 認証ファイルが無い・壊れている → E9 | E4 |
| E6 | 子の claude がトークンを更新した | 期限が近いか切れている（判断するのは Claude Code） | 別のプロセスが更新している → 排他の後に読み直して続ける（Claude Code の振る舞い）。リフレッシュトークンを断られた → E9 | 同じアカウントで NDF の更新（E8）が走っていない（受け入れ条件 13・14） |
| E7 | 子の claude が共有するもの（会話の記録・設定・プラグイン）へ書いた | 会話・`/model` などの設定の変更・NDF の hook | symlink が実体に置き換わった → 共有側に入らず、次のアカウントのセッションから見えない（受け入れ条件 8・15 で防ぐ） | E3 |
| E8 | NDF が、動いている claude の無いアカウントのトークンを更新した | 候補を選ぶときの使用量の取得で、期限が近いか 401 だった | 更新を断られた → `needs_relogin` にして次の候補（#1389 のまま）。claude の更新の排他を取れない → 更新せず、その回の使用量は読めなかったものとして扱う | E6 と同時に走らない |
| E9 | 認証が通らず、子の claude の応答が終わった（`authentication_failed`） | E5・E6 の失敗 | — （次の候補へ替える。そのアカウントを `needs_relogin` にするかは未決 6） | E5 |
| E10 | アカウントを替え、次のセッションを `--resume` で起動した | 上限の検知・切り替えの閾値・E9・上限が外れた | 会話の記録が見つからない → `--resume` が通らない（受け入れ条件 5 で防ぐ） | E1〜E5 が新しいアカウントで再び起きる |
| E11 | 切り替えの理由に合う再開の文を入力した | E10（未達の `/goal` が無いとき） | — | E10 |
| E12 | セッションが終わり、`.claude.json` の共有する部分の変わった分を共有側へ書き戻した | 子の claude の終了 | セッションの間に共有側が別の claude に書き換えられていた → 取り合い（未決 3）。書けない → 記録を残して続ける | E3 |
| E13 | 従量の接続へ移った | 登録済みアカウントがすべて上限 | — | 元の環境のアカウント由来の変数と、アカウントの設定ディレクトリを指す `CLAUDE_CONFIG_DIR` が子へ残らない（受け入れ条件 10） |
| E14 | 利用者が手で `/login` を打った | 利用者 | 共有の設定ディレクトリの claude で打った → 共有の認証だけが替わる（受け入れ条件 7）。登録済みアカウントの claude の中で打った → そのアカウントの設定ディレクトリの認証が別のアカウントに書き換わる（未決 4） | — |
| E15 | アカウントの登録を外した | `relay.py account remove` | symlink の先を消した → 共有の会話の記録と設定が失われる（受け入れ条件 17 で防ぐ） | — |

- 引き金の無いイベント: 無い
- 失敗の経路の無いイベント: E4・E11・E13 は入力がそろっていれば失敗しない。E9 の後の扱いは未決 6 に回した
- ほかのイベントの順序を仮定しているイベント: E6 と E8（同時に走らない）は受け入れ条件 13・14、E13（元の環境）は受け入れ条件 10、E12（共有側が変わっていない）は未決 3 に移した

## 用語

| 用語 | 意味 |
| --- | --- |
| アカウントの設定ディレクトリ | 登録済みアカウントごとのディレクトリ（アカウントの置き場の `<名前>/`）。認証ファイルと登録の記録を実体で持つ。この変更の後は、そのアカウントで起動する claude の `CLAUDE_CONFIG_DIR` になる |
| 共有の設定ディレクトリ | ラッパーを通さずに起動した claude が使う設定ディレクトリ（`${CLAUDE_CONFIG_DIR:-~/.claude}`）。会話の記録・プラグイン・設定の正を持つ。登録済みアカウントの claude の中では、アカウントの設定ディレクトリの symlink の参照先になる |

## 受け入れ条件

番号 1〜12 は、原文の「受け入れ条件（案）」の同じ番号の条件を、観測できる形へ書き直したものである。13 以降は要求の工程で足した。「実物」と書いた条件は前提 6 のとおりに確かめる。

- [ ] 1. 登録済みアカウント A の環境（`account_env()` が組み立てたもの）で `claude auth status` を打つと、`email` が A の `account.json` の email と一致し、契約の種類が出る。tmux で起動したセッションの claude へ `/status` を打つと、画面に同じ email と `Login method` の行（契約の種類）が出る（実物で、契約の種類が違う 2 アカウント）
- [ ] 2. `account_env()` が登録済みアカウントの環境を組み立てると、`CLAUDE_CODE_OAUTH_TOKEN` と `CLAUDE_CODE_OAUTH_SCOPES` が無く（元の環境にあっても外れる）、`CLAUDE_CONFIG_DIR` がそのアカウントの設定ディレクトリを指し、`NDF_CLAUDE_ACCOUNT` がその名前になる。ラッパーと `supervise.py` が組み立てる環境は、この 4 つの変数について同じになる（単体テスト）
- [ ] 3. `.credentials.json` の `expiresAt` が過去のアカウントの設定ディレクトリで、NDF がそのアカウントの排他を握っている間に `claude -p` を起動すると、応答が返り、`expiresAt` が未来の時刻になる（実物で 1 回）
- [ ] 4. `expiresAt` が過去の同じアカウントで `claude -p` を同時に 3 本起動すると、3 本とも応答を返し、`Failed to refresh OAuth token` が出ない。続けて打つ 4 本目も応答を返す（更新が重なってリフレッシュトークンが無効になっていない。実物で 1 回）
- [ ] 5. アカウント A のセッションで始めた会話を、アカウント B に替えた次のセッションで `--resume` すると、前の会話の内容を踏まえた応答が返る（実物で 1 回）。アカウントの設定ディレクトリの `projects` は、共有の設定ディレクトリの `projects` を指す symlink である（単体テスト）
- [ ] 6. 登録済みアカウントの環境で `claude mcp list` を打つと、そのアカウントの claude.ai のコネクタが 1 件以上 `✔ Connected` で並ぶ。コネクタの構成が違う 2 つのアカウント（実験 3 の 6 行と 21 行）で打つと、claude.ai の行数がアカウントごとの数になる（実物で 2 アカウント。#1523 の受け入れ条件 1 を保つ）
- [ ] 7. ラッパーと `supervise.py` は、共有の設定ディレクトリの `.credentials.json` を読まず、書かない（単体テスト）。共有の設定ディレクトリの claude で利用者が `/login` を打って別のアカウントへ替えても、動いているセッションの環境で打つ `claude auth status` の email は変わらない（実物で 1 回）
- [ ] 8. 登録済みアカウントのセッションで `/model` を変え、会話を 1 往復した後も、アカウントの設定ディレクトリの `settings.json` と `projects` は symlink のままで、変更と会話の記録は共有の設定ディレクトリの側に入っている（実物で 1 回）
- [ ] 9. `.credentials.json`・`account.json`・`usage.json` だけを持つ今の形のアカウントの設定ディレクトリ（`accounts/<名前>.lock` が残っているものを含む）で、登録し直さずに受け入れ条件 2 が成り立ち、足りない symlink が起動のときに足される。2 回目の起動では何も足さず、既にあるものを書き換えない（単体テスト）
- [ ] 10. 従量の接続で起動する子の環境に、トークンとスコープの変数が無く、`NDF_CLAUDE_ACCOUNT` は `metered` になる。元の環境がアカウント A のセッションの中（`CLAUDE_CONFIG_DIR` が A の設定ディレクトリ）のときも、子の `CLAUDE_CONFIG_DIR` は共有の設定ディレクトリを指すか、変数が無い（単体テスト。#1389 の I16 を保つ）
- [ ] 11. 認証が通らなかったこと（`authentication_failed`）を理由に替えた次のセッションの最初の入力は、認証を理由とする文であり、利用上限を理由とする文ではない。利用上限・切り替えの閾値で替えたときの文は、それぞれの理由を示す。未達の `/goal` があるときは、理由に依らず今と同じく `/goal` を入れ直す（単体テスト）
- [ ] 12. 既存の全体テストが通る
- [ ] 13. NDF は、アカウントの設定ディレクトリの実パスに `.lock` を付けたパス（`accounts/<名前>.lock`）を作らず、開かない。新しい形で 1 度起動した後、登録済みの名前の `accounts/<名前>.lock` は残っていない（単体テスト）
- [ ] 14. NDF は、claude が動いているアカウント（ラッパーの今のセッションのアカウントと、`supervise.py` の実行中の呼び出しのアカウント）のトークンを更新せず、その `.credentials.json` を書かない。NDF がトークンを更新するのは、Claude Code と同じ排他（アカウントの設定ディレクトリの中の `.oauth_refresh.lock`）を取れたときだけで、取れなければ更新の宛先を呼ばない。書き込みは一時ファイルからの置き換えで行う（単体テスト）
- [ ] 15. `settings.json` が共有側への symlink であるアカウントの設定ディレクトリを `CLAUDE_CONFIG_DIR` にして `ensure-retention.sh` と `statusline-switch.sh ensure` を打つと、symlink は symlink のままで、書いた値は共有の設定ディレクトリの `settings.json` に入る（単体テスト）
- [ ] 16. 元の環境がアカウント A のセッションの中のときに、`supervise.py` がアカウント B の環境を組み立てると、B の設定ディレクトリに足される symlink は共有の設定ディレクトリの実体を指す（A の設定ディレクトリを経由しない）。アカウントの置き場（`store_dir()`）は、共有の設定ディレクトリから求めたものと同じ実体になる（単体テスト）
- [ ] 17. アカウントの設定ディレクトリを用意するときも、登録を外す（`relay.py account remove`）ときも、共有の設定ディレクトリのファイルとディレクトリを消さない。アカウントの設定ディレクトリに同じ名前の実体が既にある項目は、消さず、上書きしない（単体テスト）
- [ ] 18. アカウントの設定ディレクトリの `projects` が共有の設定ディレクトリの `projects` を指していないとき、そのアカウントで claude を起動しない（次の候補へ移る）。理由が画面の 1 行と `log.jsonl` に残る（単体テスト）
- [ ] 19. 登録済みアカウントの環境で起動する前の、アカウントの設定ディレクトリの `.claude.json` には、共有側の `.claude.json` の `projects` と `mcpServers` が入っており、`oauthAccount` はそのアカウントのままである。セッションの中で足した `projects` の項目（プロジェクトの信頼など）は、セッションの終わった後に共有側の `.claude.json` に入っている。書き戻しは `projects` と `mcpServers` のほかのキーを変えず、共有側の `.claude.json` が symlink なら symlink のまま残す（単体テスト）
- [ ] 20. 子の claude の応答が `authentication_failed` で終わったとき、ラッパーは今と同じく別の候補へ替えて続ける（1 つのセッションにつき 1 度だけ）。候補が無ければ今と同じ理由（`auth`）で止まる（単体テスト。既存の振る舞いの退行を見る）
- [ ] 21. 登録が 1 つ以下のときは、アカウントの環境を組み立てず、子の `CLAUDE_CONFIG_DIR` を変えない（単体テスト。#1389 の受け入れ条件 9 を保つ）
- [ ] 22. トークンの値が、子の環境変数・引数・画面・`log.jsonl` に現れない（単体テスト。#1389 のまま）
- [ ] 23. `references/relay.md`・`supervise_lib/plan.py` の説明・`lib/claude_accounts.py` の冒頭・用語集の「アカウントのスコープ」が、採った形（トークンとスコープを環境変数で渡さない）と一致する。#1389 の決定 1 と #1523 の決定 1 を置き換えたことと理由が、設計の決定の記録にある

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 登録済みアカウントのセッションは、アクセストークンの期限（8 時間）をまたいでも認証切れで終わらない（受け入れ条件 3。本物の 8 時間は未決 1）。アカウントの設定ディレクトリの用意に失敗しても、ラッパーは止まらず次の候補か従量の接続へ移る |
| 性能・拡張性 | 起動の前の用意（symlink の確認と `.claude.json` の写し）は、ネットワークへの要求と LLM の呼び出しを足さない。手元の環境（共有の設定ディレクトリの直下が約 30 項目、共有側の `.claude.json` が約 164 KB）で 1 秒以内に終わる |
| 運用・保守性 | セッションの起動の記録（`log.jsonl`）のアカウントの名前は今のまま残す。用意で足せなかった項目と書き戻せなかった事実を `log.jsonl` に残す。トークンの値と `.claude.json` の中身は記録しない |
| 移行性 | 登録し直さない。アカウントの設定ディレクトリの既存の 3 ファイル（`.credentials.json`・`account.json`・`usage.json`）の形を変えない（古い版へ戻しても登録を読める）。古い版のラッパーとの並走は前提 8 |
| セキュリティ | トークンを環境変数・引数・記録・画面に出さない。共有の設定ディレクトリの `.credentials.json` に触れない。アカウントの設定ディレクトリは 0700、その中の認証ファイルと `.claude.json` は 0600 を保つ。symlink の参照先は共有の設定ディレクトリの直下の項目だけにする |
| システム環境 | 基準は Claude Code 2.1.286（前提 1）。動作環境は前提 5 |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `relay.py account` のコマンドと出力は変わらない。**子の環境変数が変わる**: `CLAUDE_CODE_OAUTH_TOKEN` と `CLAUDE_CODE_OAUTH_SCOPES` が無くなり、`CLAUDE_CONFIG_DIR` がアカウントの設定ディレクトリを指す。`NDF_CLAUDE_ACCOUNT` は今のまま。利用者の hook やスクリプトが `$CLAUDE_CONFIG_DIR` の下を読むときは、symlink 越しに共有側を読む |
| データ | アカウントの設定ディレクトリに symlink と Claude Code のアカウント固有のファイルが増える。排他ファイルの場所が変わる。会話の記録・プラグイン・設定は共有の設定ディレクトリに 1 か所のまま。共有側の `.claude.json` へ、セッションの終わりに NDF が書き戻す |
| 既存の振る舞い | 動いているセッションのトークンを Claude Code が更新する。`UsageWatch` の「動いているセッションのトークンは更新しない」規則と、8 時間ごとの 401 の後の切り替えが要らなくなる。`/status` と statusline に実際のアカウントが出る。再開の文が理由ごとに変わる。#1575 はこの変更の配布で閉じる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_claude_accounts.py plugins/ndf/scripts/tests/test_relay_account.py -q`、全体は `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`、`python3 plugins/ndf/scripts/instructions-check.py --root .`、`python3 plugins/ndf/scripts/glossary.py check --diff origin/develop` |
| 実物の確かめ | 受け入れ条件 1・3・4・5・6・7・8 の実物の分。原文の実験 1・2・3・7・12 と同じ手順を、実装した `account_env()` の環境で打ち直す（tmux の中の claude へ `/status` と `/model`、`claude -p`・`claude auth status`・`claude mcp list`）。期限切れは `expiresAt` を過去へ書き換えて作る。前提 6 のとおり、利用者か、利用者が許した conductor が行う |
| リリース後テスト | 未決 1。登録済みアカウントのセッションを 8 時間を超えて動かし、`log.jsonl` に認証を理由とする切り替えが無いことを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 子の環境の組み立てとアカウントの設定ディレクトリの用意は `lib/claude_accounts.py` の 1 か所に置き、ラッパーと `supervise.py` に分けない（`AGENTS.md`）。アカウントの置き場のファイルを書くのは、今と同じくこのモジュールと claude 自身だけにする |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`.md` の文言を照合するテストを書かない。外部コマンドと Claude Code の振る舞いは、書く前に実行して確かめる |
| テスト戦略 | 環境の組み立て・symlink の用意・排他・移行・再開の文は、偽のアカウントの置き場（`tests/account_fake.py`）と偽の claude（`tests/fixtures/relay_fake_claude.py`）で単体テストにする。Claude Code 本体の振る舞い（更新・`/status`・コネクタ・symlink への書き込み）は単体テストにできないため、実物の確かめで見る |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、#1389 の不変条件（共有の `.credentials.json` に触れない・トークンを記録しない・従量の接続の環境にアカウント由来の変数を混ぜない）を保つ |
| 確認してから行う | 実物のアカウントの認証ファイルを使う確かめ（共通原則の C1。前提 6）。認証の渡し方の変更（C2）と、`~/.claude/` の外にある共有の `~/.claude.json` へ NDF が書き戻すこと（C6）は、設計の承認で人が見る（前提 7） |
| 行わない | 共有の設定ディレクトリの `.credentials.json` の読み書き。共有の設定ディレクトリのファイルの削除と移動。アカウントの設定ディレクトリの実体の削除。トークンを環境変数で渡す形へ戻すこと。従量の接続の起動の形の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 本物の 8 時間の経過（サーバー側で失効したアクセストークン）で、claude が自分で更新すること（原文の未確認 1）。実験は `expiresAt` の書き換えで、トークン自体は有効だった | conductor（リリース後テストで実測） | 配布の後のリリース後テスト |
| 2. NDF が更新して書いた認証ファイルを、動いている claude が読み直すこと（原文の未確認 2）。受け入れ条件 14 で NDF は動いている claude のアカウントを更新しないが、別のコンテナで動く claude は NDF から見えない | `design`（実物で確かめる。前提 6） | 設計の承認の前 |
| 3. `.claude.json` の書き戻しと、動いている別の claude の `.claude.json` への書き込みとの取り合い（原文の未確認 3）。セッションの間に共有側で変わったキーを古い値で上書きしない手順が要る。共有の `~/.claude.json` は `~/.claude/` の外にあるため、NDF が書くこと自体を設計の承認で示す | `design` | 設計の承認の前 |
| 4. 登録済みアカウントの claude の中で `/login` を打つと、アカウントの設定ディレクトリの認証が別のアカウントに書き換わる（原文の未確認 4）。`account.json` の email と食い違ったときに、候補から外すか・知らせるだけか・登録を直すか | `design` | 設計の承認の前 |
| 5. 共有の設定ディレクトリに後から増えた項目と、claude がアカウントの設定ディレクトリに新しく作る項目（`agents/`・`output-styles/` など）の扱い（原文の未確認 5）。受け入れ条件 17・18 の範囲で、`projects` のほかの項目を足せなかったときに起動するかを決める | `design` | 設計の承認の前 |
| 6. 子の claude が `authentication_failed` で終わったアカウントを `needs_relogin` にするか。今は 401 の後に NDF がトークンを更新してから判断するが、動いている claude のアカウントを NDF が更新しない形（受け入れ条件 14）では、判断の材料が変わる | `design` | 設計の承認の前 |
| 7. アカウント固有のもの（symlink にしない項目）の一覧の確定。原文の確定案の 4 の一覧に、NDF のファイル（`.ndf-retention-checked`・`.ndf-retention.lock`・`.ndf-statusline.lock`・`ndf-statusline.sh`）をどちらへ入れるか | `design` | 設計の承認の前 |
