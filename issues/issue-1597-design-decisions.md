# #1597: 学習の設定の確認と書き換え — 決定・テスト設計・未確認

設計の本体は [issue-1597-design.md](issue-1597-design.md) にある。この文書は、その設計で選んだ結論と理由、
テスト設計、未確認のまま残ることを持つ。

## 決定の記録

### 決定 1: 確認と書き換えを 1 本のスクリプトの 2 つの副命令に置き、標準ライブラリだけで `python3` から動かす

`scripts/training-optout.py` に `check` と `session-start` を置く。名前は実験版を引き継ぎ、利用側が実験版の例で書いた
呼び出しが、置き場を直すだけでそのまま動く。2 つの副命令は同じ読み取り（`claude_training.read_setting`）を使うため、
1 本にまとめると読み取りの経路が 1 つに決まる。

`hook.py` の副命令にしない。`hook.py` は SessionStart が用意する環境の python（`$HOME/.cache/ndf/roots…/bin/python`）で動き、
その環境は同じ SessionStart の hook が作るため、最初の起動では無いことがある。relay の hook と同じく `python3` で直に動かせば、
環境の用意を待たない。書き換えを独立の hook のスクリプト（`.sh`）にする案は、トークンの読みと JSON の判定をシェルで書き直す
ことになり、`check` と読み取りの経路が 2 つに分かれるため採らない。

根拠: Value 6 / Value 4（MVV 版 2）

### 決定 2: Anthropic の応答を読むのは `lib/claude_training.py` だけにし、HTTP と認証の部品は既存のものを使う

`account/settings` は公開の API でなく形が変わりうるため、応答の鍵を読む場所を 1 か所（腐敗防止層）に限る。形が変わったときに
直す場所が 1 つで済み、識別子を含む他の鍵を読まずに捨てることもここで守れる（I5）。

HTTP は `claude_usage._http` を公開名 `http_json` にして使う（`method` と本文を足す）。従量の接続の判定は
`claude_accounts.FOREIGN_AUTH_ENV` を、認証ファイルの読みは `claude_accounts` に足す `oauth_in(config_dir)` を使う。
同じ OAuth の宛先へ同じ見出しで送る経路と、同じ認証ファイルを読む経路を、Skill やスクリプトごとに分けないためである。
実験版のように `urllib` を直に呼ぶ形は、打ち切りと失敗の読み方が `claude_usage` と別に増えるため採らない。

根拠: Value 6（MVV 版 2）

### 決定 3: 書き換えの契機は SessionStart の `startup` と `resume` にする

`claude --resume` も新しいプロセスであり、`resume` を外すと、再開だけで作業する利用者のアカウントは一度も書き換わらない。
学習の設定はアカウント単位の値で、セッションの途中に Web の設定画面で戻されることもあるため、プロセスを起こすたびに確かめる。
`clear` と `compact` は同じプロセスの中の区切りで、プロセスの開始で確かめた値が残るため含めない。

matcher が同じ `startup|resume` の群（relay の hook がある群）へ足し、群を増やさない。PreToolUse など他の契機は足さない
（要求の「確認してから行う」）。

根拠: Mission / C6（MVV 版 2）

### 決定 4: 書き換えを外す手段は環境変数 `NDF_TRAINING_OPTOUT=0` にし、プロジェクトの `.ndf/` には置かない

学習の設定はアカウント単位の値で、リポジトリの運用ではない。`.ndf/` に置くと、同じアカウントで開くリポジトリごとに
結果が変わり、どれか 1 つで外しても他のリポジトリの起動が書き換える。環境変数なら、利用者は `~/.claude/settings.json` の
`env` に書いて恒久にでき（hook へ届くことを 2026-10-02 に実測）、relay のアカウントの設定ディレクトリごとにも変えられる。
NDF の他の止め方（`NDF_PLAN_HINT=0`・`NDF_SLEEP_GUARD=0` など）と同じ形にそろう。

外した状態では読み取りも送らない（受け入れ条件）。`check` は環境変数を読まない。利用側の規則が確認を要るときに、書き換えを
外したことで確認まで止まると規則を守れないためである。

根拠: Value 5 / C6 / C7（MVV 版 2）

### 決定 5: `PATCH` の後に読み直さず、次の起動の読み取りで確かめる

`PATCH` は `202`（受け付けた）を返すため、直後に読み直しても反映の前の値を読むことがあり、成功を失敗と知らせうる。
反映は次の起動の読み取りが確かめる。反映されていなければ、次の起動がもう一度送り、もう一度知らせるため、繰り返す知らせで
利用者に分かる。知らせの文面は「Off にした」とし、受け付けたことを伝える。待って読み直す案は、hook の時間（決定 9）の
大半を待ちに使うため採らない。

根拠: Value 2 / Value 3（MVV 版 2）

### 決定 6: 利用者への知らせは `systemMessage` だけで出す

`systemMessage` は利用者の画面に出て、モデルの文脈には入らない。書き換えの知らせはモデルの判断に要らず、`additionalContext` や
素の標準出力（SessionStart では文脈へ入る）に出すと、セッションごとにトークンを使う。知らせが無いとき（既に false・無効化・
従量の接続）は何も出さない。`claude -p` の worker では知らせを読む人がいないが、同じアカウントの対話のセッションで同じ知らせが
出るため、別の経路（ログのファイル）を足さない。ログのファイルを作らないことで、トークンと識別子が残る置き場も増やさない。

根拠: Value 2 / C6（MVV 版 2）

### 決定 7: 失敗の知らせを間引かず、起動のたびに出す

受け入れ条件は失敗が利用者に分かることを求める。間引くには、知らせた時刻を残す状態のファイルが要り、ローカルの状態と
その排他が増える。同じ失敗が続く利用者（トークンのスコープが足りないなど）には、知らせの文面が止め方（`NDF_TRAINING_OPTOUT=0`）を示す。

根拠: Value 2 / 上位の原則（MVV 版 2）

### 決定 8: 従量の接続では何も送らず、何も知らせない

API キー・Bedrock・Vertex の起動には学習の設定が無い（要求の前提 6）。この起動で認証ファイルのトークンを使って読み書きすると、
その起動が使っていないアカウントの設定を変える。知らせも出さない。失敗ではなく、確かめる対象が無いためである。`check` は
`training: null` と理由を返し、「学習に使わない」とは扱わない。判定の変数は relay が子から外す組（`FOREIGN_AUTH_ENV`）と同じにする。

根拠: C3 / Value 6（MVV 版 2）

### 決定 9: hook の `timeout` を 10 秒、HTTP の打ち切りを 1 回 3 秒、キーチェーンを 2 秒にする

`GET` と `PATCH` の最大 6 秒にキーチェーンの 2 秒を足して 8 秒で、`python3` の起動を足しても 10 秒に収まる。10 秒は
既存の hook の多く（PreToolUse・worktree-session の Codex 版）と同じ値である。`claude_usage` の打ち切り（10 秒）を使わないのは、
1 回で hook の時間を使い切るためである。

根拠: Value 2（MVV 版 2）

### 決定 10: macOS のキーチェーンは、`CLAUDE_CONFIG_DIR` が無いときの既定の名前だけを読む

macOS の Claude Code は認証ファイルの代わりにキーチェーンへ置くため、読まなければ macOS の利用者は起動のたびに失敗の知らせを
受ける。読むのは `security find-generic-password -s "Claude Code-credentials" -w` の 1 回だけにする。`CLAUDE_CONFIG_DIR` を
使う起動でのキーチェーンの名前は確かめていないため、推測した名前を読まずに「確かめられない」とする。

根拠: Value 3 / C1（MVV 版 2）

### 決定 11: 期限切れのトークンでも送り、トークンの更新も期限の事前判定もしない

トークンの更新は Claude Code が行う（要求の「含まない」）。`claude_accounts` の更新の経路を使うと、認証ファイルへ書くことになり、
要求の「確認してから行う」に当たる。期限を事前に判定して送らない案は、Claude Code が SessionStart の前に更新しているかを
確かめていないため採らない。送って 401 なら失敗として知らせる。

根拠: C1（MVV 版 2）

### 決定 12: 実験版の `check` の契約をそのまま本体へ移し、実験版を消す

出力の形・終了コード（0 / 1 / 3）・`reason` の語を変えない。PR #1606 がマージされていれば、実装の Pull Request で
`experimental/training-optout.py`・そのテスト・README の節を消し、台帳の行の「行き先」に `scripts/training-optout.py` を書く。
マージされていなければ、実験版を作らずに本体へ置き、台帳には行き先を書いた行を足す（要求の前提 4）。

`reason` のうち実験版の「読めない（<例外の型名>）」は「通信の失敗」に変える。`http_json` が通信の失敗を状態 0 にまとめて返し、
型名を区別しないためである。

根拠: Value 9 / Value 7（MVV 版 2）

### ゲート 1 の扱い

承認ゲート 1 は MVV の判定に任せず、人が承認する。OAuth のトークン（秘密）を読んで外部へ送り（C1）、利用者のアカウントの
設定と、それに伴う会話の保存期間を変える（C3。要求の前提 7）ためである。

根拠: C1 / C3（MVV 版 2）

## テスト設計

応答は差し替える（`NDF_TRAINING_SETTINGS_URL` で手元の偽の宛先へ向ける）。実物の API を呼ばない。

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| `check`: false → `ok`・0・`training: false` | `grove_enabled: false` を返す宛先で `check` | 終了コードか `status` を true と同じにすると落ちる |
| `check`: true → `stopped`・`training: true` | `grove_enabled: true` を返す宛先で `check`。終了コード 1 | true を ok と扱うと落ちる |
| `check`: 認証が無い・401・403・通信・真偽値でない → `stopped`・null・`reason`（I1） | 5 つの場合それぞれで `check`。終了コード 3 | どれか 1 つで `training` を false にすると落ちる。文字列 `"false"` を真偽値と読むと落ちる |
| `check`: codex / kiro / agy → `unsupported`・`stopped` | `--runtime` を 3 つ並べて `check` | 1 つでも `training` を false にすると落ちる |
| `check` の置き場と実験版の片付け | `experimental/training-optout.py` が無く、`scripts/training-optout.py` があり、台帳の行に行き先がある | 実験版を残すか、台帳の行き先を空にすると落ちる |
| 書き換え: true → `PATCH` が `{"grove_enabled": false}` で 1 回・知らせが出る（I2） | 偽の宛先が受けた要求を数え、本文と方法を見る。標準出力の `systemMessage` | `PATCH` を 2 回送るか、本文の値を変えるか、知らせを消すと落ちる |
| 書き換え: false → `PATCH` を送らず知らせない（I2） | 偽の宛先の `PATCH` の数が 0、標準出力が空 | false でも送ると落ちる |
| 書き換え: 認証が無い・401・403・通信・`PATCH` が 2xx 以外 → 終了コード 0・失敗の知らせ（I6） | 5 つの場合それぞれで `session-start`。`claude_training` の関数が例外を投げる場合も足す | 例外を外へ出すか、知らせを出さないと落ちる |
| 書き換え: 従量の接続 → 送らず 0（I4） | `FOREIGN_AUTH_ENV` の 4 つのそれぞれを立て、偽の宛先の要求の数が 0、標準出力が空 | 判定の変数を 1 つ外すと落ちる |
| 書き換え: 無効化 → 読みも書きも送らない（I3） | `NDF_TRAINING_OPTOUT=0` で偽の宛先の要求の数が 0 | 読み取りの後に無効化を見る順にすると落ちる |
| hook の定義が SessionStart に載り validate が通る | `claude.json` の SessionStart の `startup\|resume` の群に `training-optout.py session-start` があり、`timeout` が 10。`claude plugin validate .` の終了コード 0 | 群の matcher を `startup` だけにするか、hook を消すと落ちる |
| 秘密が出ない（I5） | トークンと応答（識別子の鍵・`grove_enabled` 以外の鍵）に目印の文字列を入れ、`check` と `session-start` の全経路の標準出力・標準エラーに目印が無い | `reason` に応答の本文か例外の文言を写すと落ちる |
| 既存の SessionStart の hook が変わらない | 既存のテスト（`test_relay.py`・`test_ensure_retention.py` ほか）がそのまま通る | 既存の hook の command か matcher を変えると落ちる |
| 送り先は `api.anthropic.com` だけ（I7） | 差し替えの変数が無いときの宛先が `https://api.anthropic.com/api/oauth/account/settings` | 宛先を別のホストにすると落ちる |
| 打ち切りの合計（非機能・性能） | 応答を返さない偽の宛先で `session-start` が 7 秒以内に終わり 0 | 打ち切りを外すか延ばすと落ちる |
| codex / kiro / agy で書き換えない（非機能・システム環境） | `hooks/codex.json`・`dev.agy/hooks.json` に `training-optout` が無い | 他のランタイムの hook に足すと落ちる |
| 利用者への説明 | 文言を照合するテストは書かない（`AGENTS.md`）。`plugins/ndf/README.md` の節をレビューで見る | — |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| true → false の書き換え | 実物のアカウントで未確認（要求の前提 8）。リリース後テストで、設定画面で On にしてから `claude` を起動し、Off に戻って知らせが出ることを見る |
| 起動の時点のトークンの期限 | Claude Code が SessionStart の hook より前に期限切れのトークンを更新しているかを確かめていない。更新していなければ、期限の切れた後の最初の起動で 401 の知らせが出て、次の起動で書き換わる（決定 11）。リリース後テストで、期限を過ぎた認証ファイルで起動して見る |
| `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token`）のスコープ | 長期のトークンが `user:profile` を持たなければ、読み取りが 403 になり、起動のたびに失敗の知らせが出る。手元の認証ファイルのトークンは `user:profile` を持つことだけを確かめた |
| macOS のキーチェーン | 名前（`Claude Code-credentials`）と、`security` が確認の画面を出さずに読めるかを、macOS で確かめていない。画面が出れば 2 秒で打ち切り「確かめられない」になる |
| Team / Enterprise のアカウント | 組織が学習の設定を決める場合の `GET` と `PATCH` の応答を確かめていない。拒まれれば書き換えの失敗として知らせる（要求の「含まない」） |
| 会話の保存期間の変化 | 学習の設定を Off にすると保存期間も変わる（要求の前提 7）。これを含めて書き換えてよいかは、承認ゲート 1 で利用者が決める |
