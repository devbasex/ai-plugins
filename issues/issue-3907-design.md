# 学習の設定の確かめ: 学習に使う設定なら Off へ書き換えてから claude・codex を起動する（#3907）

アカウントが入力を学習に使う設定（On）だと、NDF は claude・codex の起動を断り、利用者がブラウザで Off にして確かめ直すまで工程が止まる。確かめの 1 か所（`settle`）で、学習に使う設定なら true の設定だけを false へ書き込んで読み直す。false なら記録して起動を続け、そうでなければ今までどおり断って手で直す場所を示す。書き換えは時刻と前後の値つきで記録と出力に残り、書き換えを止める手段は持たせない。採らなかった案と 10 系（#1597）の実装から採ったものは「決定の記録」に、HTTP の 202 の意味と Claude Code 本体の書き込みは「外部の調査」にある。

## 今の姿

何が起きたか・目的・受け入れ条件は要求仕様（[issue-3907-requirements.md](./issue-3907-requirements.md)、正は #3907 の本文）にある。この文書は「どう解くか」と「なぜその解き方か」だけを書く。今の確かめの 2 か所・読む API・書き込みを確かめた結果の表は、要求仕様の「今の姿」にある。

```mermaid
flowchart LR
  CHK["ndf training check<br/>（check）"] --> RS["読む<br/>（read_setting）"]
  GATE["起動の口<br/>（launch_slot.gate・participants.unready）"] -- "記録が無いか期限切れ" --> ENS["その場の確かめ<br/>（ensure → _read_now）"]
  ENS --> RS
  RS -- "GET" --> API[("claude: oauth/account/settings<br/>codex: settings/user")]
  RS --> SAVE[("training.json<br/>（save）")]
  GATE -- "記録が新しい" --> REF["記録を読んで判定<br/>（refusal）"]
  SAVE --> REF
  REF -. "学習に使う設定 → 断る" .-> X(("起動しない"))
```

変更の位置は「読む」の 1 か所である。今は読んだ結果をそのまま記録し、学習に使う設定（`training` が true）なら `check` は `stopped`（`refused`）で、起動の口は `refusal` が断る。

設計に要る今の構造:

| 部品 | 置き場 | 今の振る舞い |
| --- | --- | --- |
| 読み手 | `training.py` の `read_claude`・`read_codex`（`Endpoint` の `interpret` が真偽値を 1 つ返す） | 担当ごとの見出しで `GET` を 1 回送り、学習に使うかを 1 つの真偽値にまとめる。codex は 3 つのキーのどれかが true なら true |
| 確かめの入口 | `training.py` の `check`（サブコマンド）と `_read_now`（`ensure` の既定の読み手） | どちらも `read_setting` を呼ぶ。`ensure` はテストで `read` を差し替え、テストは `_read_now` も差し替える |
| 記録 | `training_cache.py` の `Reading`・`save`・`_cached_verdict` | 担当ごとに `runtime`・`training`・`reason`・`checked_at`・`context`（認証の文脈のハッシュ）を書く。学習に使う設定の記録は、期限内なら確かめ直さずに断る |
| 時間の上限 | `contracts/limits.json` | `claude-training.call`・`codex-training.call`（1 回 3 秒。説明は「読み書き」）。`codex-training.budget`（7 秒）はどこからも読まれていない（10 系の hook の時間の名残り） |

## 文脈と制約

- 書き込む先は公開されていない API である。形は 2026-11-20 に同じ値（false → false）で確かめた（要求仕様の「書き込みを確かめた結果」）。true → false の実際の変化は確かめていない
- 起動の口は httpx を読まずに済むよう、記録を読む規則（`training_cache.py`）と外部へ送る接続（`training.py`）を分けている（#3391 の 3391-5）。書き込みは接続の側に置く
- 今のテスト（`test_training.py`・`test_training_ensure.py`）を変えずに通す（受け入れ条件 7）。テストは `training.ensure(..., read=...)` と `training._read_now` を差し替え、`Reading(runtime, training, reason)` の 3 引数で記録を作る。HTTP のモックはどの URL にも同じ応答を返す
- トークンとアカウントの識別子は見出しにだけ置き、記録・出力・理由の文に含めない（今の約束。受け入れ条件 6）
- 用語集の `ndf-training-optout` は 10 系の振る舞い（セッションの開始で書き換える・環境変数で止める）のままである

## 解き方の全体像

```mermaid
flowchart LR
  CHK["ndf training check"] --> ST["確かめて書き換える<br/>（settle）"]
  GATE["起動の口"] -- "記録が無いか期限切れ" --> ENS["ensure → _read_now"] --> ST
  ST -- "1. GET" --> API[("学習の設定の API")]
  ST -- "2. true のキーだけ PATCH" --> API
  ST -- "3. GET で読み直す" --> API
  ST --> SAVE[("training.json<br/>rewrites を足す")]
  SAVE --> REF["refusal"]
  REF -. "書き換えられなかった → 断る<br/>理由・手で直す場所・check" .-> X(("起動しない"))
```

- `check` と起動の口のその場の確かめは、同じ `settle` を通る。`settle` は読み、学習に使う設定なら true のキーだけを false へ書き込み、1 回だけ読み直して結果を決める
- 読み直して false なら、記録は `training: false` になり、`rewrites` に書き換えたキーごとの前の値・後の値・時刻が残る。`check` は `ok`、起動の口は起動を続ける
- 書き込みが通らない・読み直しても true・読み直せないときは、記録に `training: true` と失敗の中身を残す。`check` は `stopped`（`refused`）、起動の口は断り、どちらも手で Off にする場所と `ndf training check` を示す
- 学習に使わない設定と、確かめられないとき（トークンが無い・OAuth でない接続・応答の形が違う）は、書き込みを送らない。結果は今と同じ

## 入出力のコントラクト

### `ndf training check` の結果の `items`

要素に任意の項目 `rewrites` を足す。書き込みが 1 つでも受け付けられたときだけ載り、今の 4 項目は変えない。スキーマ（`training.result.schema.json`）は要素が閉じているので、`rewrites` を `properties` に足す（`required` には足さない）。

| 項目 | 型 | 中身 |
| --- | --- | --- |
| `rewrites` | 配列 | 書き込みが受け付けられた（2xx の）キーごとに 1 要素。受け付けられなかったキーは載らず、失敗は `reason` に載る |
| `rewrites[].key` | string | 書き換えたキー（claude は `grove_enabled`、codex は `training_allowed`・`codex_training_allowed`・`codex_training_allowed_v2` のどれか） |
| `rewrites[].before` | boolean | 書き込む前に読んだ値（常に true） |
| `rewrites[].after` | boolean か null | 読み直した値。読み直せなかった・応答にそのキーが無ければ null |
| `rewrites[].at` | string | 書き込みを送った時刻（ISO 8601、UTC、秒まで） |

例（codex の 2 つのキーが true だったとき）:

```json
{"runtime": "codex", "training": false, "reason": "chatgpt/settings/user.training_allowed（Off へ書き換えた: training_allowed・codex_training_allowed_v2）", "checked_at": "2026-11-21T01:00:03+00:00",
 "rewrites": [{"key": "training_allowed", "before": true, "after": false, "at": "2026-11-21T01:00:01+00:00"},
              {"key": "codex_training_allowed_v2", "before": true, "after": false, "at": "2026-11-21T01:00:02+00:00"}]}
```

`training.json` の担当の項目も同じ `item()` から作るので、同じ `rewrites` を持つ（ほかに今の `context`）。`rewrites` の無い今の記録はそのまま読める（読む側は `rewrites` を見ない）。

### 結果の `status` と理由の文

| 場合 | `check` の結果 | 起動の口 | 理由の文の中身 |
| --- | --- | --- | --- |
| 学習に使わない設定 | `ok`（今と同じ） | 起動する | 今と同じ |
| 書き換えて読み直したら false | `ok`。`summary` に担当と「Off へ書き換えた」 | 起動する | 項目の `reason` に書き換えたキー |
| 書き込みが 2xx でない・通信の失敗 | `stopped`（`refused`） | 断る | 失敗したキーと `HTTP <状態>` か `通信の失敗（<例外の型名>）`、手で Off にする場所、`ndf training check --runtime <担当>` |
| 読み直しても true | 同上 | 断る | `書き換えた後も学習に使う設定のまま: <キー>`、場所、コマンド |
| 読み直せない | 同上 | 断る | `書き換えた後に読み直せない（<理由>）`、場所、コマンド |
| 確かめられない | `stopped`（`precondition`。今と同じ） | 断る（今と同じ） | 今と同じ |

手で Off にする場所は、担当ごとの定数（`training_cache.py` の `MANUAL_OFF`）の 1 か所に置く。`check` の `summary` と起動の口の理由（`refusal`）は、どちらもそこから文を作る。

| 担当 | 手で Off にする場所 |
| --- | --- |
| claude | claude.ai の設定の「プライバシー」の「Help improve Claude」 |
| codex | ChatGPT の設定の「データコントロール」の「すべての人のためにモデルを改善する」と、Codex の設定のデータの学習（環境を含める設定） |

## 処理の流れ

```mermaid
sequenceDiagram
  participant C as check / _read_now
  participant S as settle
  participant A as 学習の設定の API
  participant F as training.json
  C->>S: settle(runtime, env, client, now)
  S->>A: GET（読む）
  alt 確かめられない・すべて false
    S-->>C: Reading（今と同じ。書き込みなし）
  else true のキーがある
    loop true のキーごと
      S->>A: PATCH key=false
    end
    S->>A: GET（読み直す。1 回だけ・待たない）
    S-->>C: Reading（training・reason・rewrites）
  end
  C->>F: save（rewrites を含む項目）
```

- codex は true のキーごとに 1 回ずつ送り、1 つが失敗しても残りのキーは送る。読み直しは全部を送った後の 1 回で、結果は読み直しと送った結果から決める
- `settle` は例外を出さない（今の読み手と同じ）。書き換えを送ったときは、実行の記録（`run_log.record`）に担当・キー・結果を 1 行残す（実行の記録を開いているときだけ書かれる）

## 決定の記録

### 決定 1: check と起動の口が同じ振る舞いになるよう、書き換えを読み手の上の 1 つの関数（`settle`）に置く

- **質問**: 書き換え（読む → 書く → 読み直す）をどこに置くか
- **選んだ案**: `training.py` に `settle(runtime, env, client, now) -> Reading` を新設し、`check` と `_read_now`（`ensure` の既定の読み手）の両方が呼ぶ。`read_claude`・`read_codex`・`read_setting` は読むだけのまま残す。`ensure` の差し替え口 `read` は「1 つの担当を確かめて書き換える関数」の意味に変わる。名前と形は変えない
- 採らなかった案: `ensure` の中だけで書き換える（`ndf training check` が書き換えず、受け入れ条件 1・2 を満たさない）
- 採らなかった案: 記録を読む規則（`training_cache.refusal`）で書き換える（起動の口が新しい記録でも httpx を読む。記録の読み手と外部への接続を分けた 3391-5 の境目が崩れる）
- 採らなかった案: 書き換えのサブコマンド（`ndf training optout`）を別に足す（利用者か工程が打つまで起動の口が断り続け、要求の「工程が止まらない」を満たさない）
- 採らなかった案: `read_setting` 自体に書き込みを足す（読むだけの関数を呼ぶ今のテストが、HTTP のモックの応答しだいで書き込みを送るようになり、「読む」と「書く」が名前から読めなくなる）
- **代償**: `_read_now` は名前に反して書き込みも送る。テストが `_read_now` を差し替えているため名前を変えられない（受け入れ条件 7）。docstring に「確かめて書き換える」と書く
- 根拠: Value 9 / Value 11（MVV バージョン 1）

### 決定 2: 書き換えが本当に効いたかで起動を決めるよう、書き込みの後に 1 回だけ読み直し、待たない

- **質問**: 書き込みの成否を何で決めるか。読み直すなら待つか
- **選んだ案**: 書き込みの応答は 2xx かどうかだけを見て、本文は読まない。全部を送った後に `GET` を 1 回送り、そこで読んだ値で決める。待ちも読み直しの繰り返しも入れない
- 採らなかった案: 10 系の決定 5 のとおり読み直さず、次の起動で確かめる（今回の要求は読み直して false なら起動を続けるので、読み直さないと学習に使う設定のまま起動しうる）
- 採らなかった案: 書き込みの応答の本文（claude・codex とも書いた値を返した）で決める（応答は受け付けを示すだけで、保存されたかは示さない。claude は 202 を返し、HTTP の 202 は処理が終わっていないことを表す。外部の調査の STD1）
- 採らなかった案: 読み直して true なら数秒待って読み直す（反映の遅れがあるかは確かめておらず、待つ秒の根拠が無い。起動の口の時間が延びる）
- **代償**: 書き込みが遅れて反映される API なら、書き換えは効いたのに起動を断る。断った記録は期限内なら確かめ直されない（決定 6）ので、利用者が `ndf training check` を打ち直すまで断り続ける
- 根拠: Value 13 / 上位の原則（MVV バージョン 1）。STD1

### 決定 3: 書き換える範囲を学習に使う設定に絞るため、true だったキーだけを、確かめた形で 1 キーずつ書き込む

- **質問**: 何をどの形で書き込むか
- **選んだ案**: claude は本文 `{"grove_enabled": false}` を `PATCH https://api.anthropic.com/api/oauth/account/settings` で送る。codex は true だったキーごとに `PATCH https://chatgpt.com/backend-api/settings/account_user_setting?feature=<キー>&value=false`。見出しは読むときと同じものを使い、リダイレクトを辿らない。codex は 1 つのキーが失敗しても残りを送る
- 採らなかった案: codex の 3 つのキーを常に全部書く（false のキーへの書き込みは要らず、受け入れ条件 2 は true のキーだけを求める）
- 採らなかった案: codex で最初の失敗で止める（送れたキーだけでもアカウントは学習に使わない側へ寄り、送った結果は `rewrites` と理由に残るので、止める利点が無い）
- 採らなかった案: codex の `settings/user` へ JSON の本文で書く（形を確かめていない）
- **代償**: codex の書き込みは最大 3 回になる
- 作り方: 自前で作る
- 比べた候補: PRD1 / PRD2 / PRD3
- 根拠: Value 13（MVV バージョン 1）。PRD1・PRD3

### 決定 4: 後から確かめられるよう、書き換えをキーごとの `rewrites` として記録と出力の同じ項目に載せる

- **質問**: 書き換えた時刻と前後の値をどこにどの形で残すか
- **選んだ案**: `Reading` に任意の項目 `rewrites`（キー・前の値・後の値・時刻の並び）を足し、`item()` が `training.json` と結果の `items` の両方へ同じ形で出す。書き込みが受け付けられたキーだけを載せ、後の値は読み直した値（読めなければ null）。あわせて実行の記録（`run_log.record`）に 1 行残す
- 採らなかった案: 担当ごとに `rewritten_at`・`before`・`after` を 1 つずつ持つ（codex はキーが 3 つあり、受け入れ条件 2 のキーごとの値を表せない）
- 採らなかった案: 書き換えの履歴を別のファイルへ追記する（保存先と片付けが 1 つ増える。後から見るための履歴は、30 日残る実行の記録が持つ）
- 採らなかった案: 受け入れられなかったキーも `rewrites` に載せる（アカウントの値を変えていない行が混ざり、「何を書き換えたか」が読めなくなる。失敗は理由の文が持つ）
- **代償**: `training.json` の `rewrites` は次の確かめ（期限は 24 時間）で上書きされる。実行の記録の外で打った `ndf training check` の書き換えは、その結果 JSON と次の確かめまでの記録にしか残らない
- 根拠: Value 8 / 受け入れ条件 1・2・6（MVV バージョン 1）

### 決定 5: 書き換えられないときに利用者が次にすることが分かるよう、学習に使う設定として記録し、手で直す場所を 1 か所の定数から示す

- **質問**: 書き換えに失敗したとき、記録の値と理由の文をどうするか
- **選んだ案**: 記録は `training: true`、`reason` に失敗の中身（キー・HTTP の状態か例外の型名・読み直した結果）。`check` は今と同じ `stopped`（`refused`）で終える。`summary` には担当ごとの理由・手で Off にする場所・`ndf training check --runtime <担当>` を載せる。起動の口の理由（`_cached_verdict` の学習に使う設定の文）も、記録の `reason` と同じ場所とコマンドを載せる。場所は `training_cache.py` の `MANUAL_OFF` に担当ごとに 1 つだけ置く
- 採らなかった案: 失敗を「確かめられない」（`training: null`・`precondition`）にする（受け入れ条件 4 は学習に使う設定として残すことを求める。読めたのに読めないと伝えることにもなる）
- 採らなかった案: 場所の文を `reason` に書き込んで記録に残す（今の学習に使う設定の記録・テストの記録は `reason` に場所を持たず、起動の口の文が記録しだいで場所を欠く）
- **代償**: 画面の項目の名前はサービスが変えうるので、定数を追って直す手間が残る
- 根拠: Value 1 / Value 11（MVV バージョン 1）

### 決定 6: 書き込みを 1 日に何度も送らないよう、期限内の学習に使う設定の記録では起動の口が確かめ直さない今の規則を保つ

- **質問**: 書き換えに失敗した記録があるとき、次の起動の口でもう一度書き換えを試すか
- **選んだ案**: 試さない。起動の口がその場で確かめるのは、記録が無いか期限切れのときだけ（今の `ensure` のまま）。失敗の後は、利用者が手で Off にして `ndf training check` を打つ（理由の文がそう示す）
- 採らなかった案: 学習に使う設定の記録でも起動のたびに書き換えを試す（スコープが足りない・組織が決めている設定のように直らない失敗では、起動のたびに書き込みを送り続ける。今の `test_ensure_does_not_recheck_a_fresh_refusal` が縛る振る舞いを変える）
- **代償**: 一時的な失敗（通信の失敗）でも、利用者が `ndf training check` を打つまで最長 24 時間断る
- 根拠: Value 2 / 受け入れ条件 7（MVV バージョン 1）

### 決定 7: 余白を重ねないよう、書き込みにも 1 回 3 秒の上限を使い、使われていない全体の上限（`codex-training.budget`）を消す

- **質問**: 起動の口で書き込みと読み直しが増える分の時間の上限をどうするか
- **選んだ案**: `PATCH` と読み直しの `GET` にも、読むときと同じ `claude-training.call`・`codex-training.call`（1 回 3 秒。説明は既に「読み書き」）を使う。最悪の時間は claude が 3 回で 9 秒、codex が 5 回で 15 秒で、学習に使う設定のときだけ、記録の期限（24 時間）に 1 回かかる。どこからも読まれていない `codex-training.budget`（7 秒）は `limits.json` から消す
- 採らなかった案: 担当ごとの全体の上限で打ち切る（10 系の予算は hook の 10 秒に収めるためだったが、今の起動の口には外側の時間の上限が無い。7 秒で打ち切ると codex の書き込みが途中で止まり、一部のキーだけが変わる）
- 採らなかった案: 書き込み用の上限のキーを別に足す（値が同じ 3 秒で、同じ事実を 2 か所に持つ）
- 採らなかった案: `codex-training.budget` を残す（読まれない値が「確かめ全体の上限 7 秒」と書き、最悪 15 秒の実際と食い違う）
- **代償**: 書き換えの起きる起動は、最悪で claude 9 秒・codex 15 秒遅れる
- 根拠: Value 2 / Value 6（MVV バージョン 1）

### 決定 8: 止めても断られ続けるだけなので、書き換えを止める手段は持たせない

- **質問**: 書き換えを利用者が止める手段（10 系の `NDF_TRAINING_OPTOUT=0`）を持たせるか（要求仕様の「未決」）
- **選んだ案**: 持たせない。止めたときの振る舞いは「今までどおり断る」で、学習に使う設定のままでは NDF は claude・codex を起動できず、止めて得られるものが無い。書き換えは `check` か起動の口が claude・codex へ作業を渡すときだけ起き、10 系のようにセッションを開くたびには起きない
- 採らなかった案: 環境変数 `NDF_TRAINING_OPTOUT=0` で止める（10 系の形。止めた利用者は断られ続けるだけで、分岐と、それを縛るテストと、用語集の語が増える）
- 採らなかった案: `ndf training check --read-only` を足す（読むだけの確かめは記録しないと起動の口が使えず、記録すれば今の `check` と同じ断りになる。使う場面が無い）
- 採らなかった案: `.ndf/` の設定に置く（学習の設定はアカウントの値でリポジトリの運用ではない。10 系の決定 4 と同じ理由）
- **代償**: アカウントを学習に使う設定のままにしておきたい利用者は、claude・codex を NDF から起動しないこと（担当に選ばない）でしか書き換えを避けられない
- 根拠: Value 1 / Value 11 / C3（MVV バージョン 1）

### 決定 9: 今の部品の上に足すため、10 系（#1597）の実装を写さず、確かめた形と理由だけを採る

- **質問**: 10 系の書き換えの実装と設計（`codex_training.py`・`claude_training.py`・`training_common.py`、#1597 の決定）から何を採るか
- **選んだ案**: 次の表のとおり。コードは写さず、今の `training.py` の読み手（httpx・`Endpoint`・見出しの組み立て）に書き込みを足す
- 採らなかった案: 10 系のスクリプト（標準ライブラリの `urllib`）を戻す（11 系は httpx とテストの差し替えの形を持ち、同じことを 2 通りの部品で持つことになる）
- **代償**: 10 系で確かめた形のうち、codex の `training_allowed` の true → false（2026-10-02）は 10 系の記録だけが根拠になる
- 根拠: Value 11 / Value 14（MVV バージョン 1）

| 10 系 | 扱い | 理由 |
| --- | --- | --- |
| 決定 3: SessionStart の `startup`・`resume` で書き換える | 採らない | 今の契機は `check` と起動の口で、claude・codex へ作業を渡すときだけ書き換えれば足りる |
| 決定 4: `NDF_TRAINING_OPTOUT=0` で止める | 採らない | 決定 8 |
| 決定 5: `PATCH` の後に読み直さない | 採らない | 決定 2 |
| 決定 7: 失敗を起動のたびに知らせる | 採らない | 失敗は記録に残り、起動の口が理由に出す。書き込みは決定 6 で 1 日 1 回まで |
| 決定 8: OAuth でない接続では送らない | 採る | 今の読み手が既に「確かめられない」にしており、書き込みは読めた後にしか起きない |
| 決定 9: 1 回 3 秒 | 採る | 決定 7。全体の予算は採らない |
| 決定 11: 期限切れのトークンでも送る | 採らない | 今の読み手は期限切れを「確かめられない」にしており（`_claude_token`）、読めないので書かない |
| 決定 14: codex は問い合わせの文字列の `feature` と `value` で 1 キーずつ送る | 採る | 決定 3。2026-11-20 にも同じ形が通った |
| `patch_reason`: 2xx を成功とする | 採る | 決定 2。受け付けの判定にだけ使う |

### 決定 10: 用語集を今の振る舞いに合わせるため、`ndf-training-optout` のコンテキストと 3 語を直し、止める手段の語を消す

- **質問**: 用語集の 10 系の語をどこまで直すか
- **選んだ案**: コンテキストの意味と「学習の設定の確認」「学習の設定の書き換え」を今の振る舞いへ改め、`pending_source` をこの設計に替える。「書き換えの無効化」は消す（決定 8 で手段が無い）。「OAuth でない接続」は意味の中の 10 系の識別子（`claude_accounts.FOREIGN_AUTH_ENV`）を今の `training_cache.CLAUDE_FOREIGN_AUTH` へ直す
- 採らなかった案: 「書き換え」の語だけを直す（コンテキストの意味が「セッションの開始で Off にする」のまま残り、同じコンテキストの語どうしが食い違う）
- 採らなかった案: 「書き換えの無効化」を廃止した語（`deprecated`）として残す（置き換える生きた語が無く、廃止の対応が作れない）
- **代償**: 確定仕様の無い語（`pending_source` が 10 系の文書を指す語）が残る。「学習の設定」は今の振る舞いと食い違わないので触らない
- 根拠: Value 14（MVV バージョン 1）

## 影響と危険

- **働く範囲**: NDF を使うすべてのリポジトリで働く。学習の設定はアカウントの値なので、書き換えはそのアカウントのブラウザの claude.ai・ChatGPT にも効く
- **プロジェクトごとに違うもの**: 無い（設定も引数も足さない。決定 8）
- **当たるモード**: claude・codex を起動するすべての工程と `ndf training check`。工程は cross-review・cross-refactoring・外部 AI への委譲・worker など
- **壊れうる箇所と扱い**
  - 学習に使う設定のアカウントでは、利用者の操作なしにアカウントの設定が Off に変わる。claude は Off にすると会話の保存期間も変わると 10 系の要求（#1597 の前提 7）が記録している。「人へ残す判断」に載せる
  - 反映の遅れる API なら、効いた書き換えを失敗として断る（決定 2）。「未確認のまま残ること」に置く
  - 結果のスキーマに項目を足すので、全コマンドの出力をスキーマで確かめるテスト（`tests/cli/test_every_output.py`）が `rewrites` を持つ出力も通ることを確かめる
  - `commands.md` はスキーマから生成する（`ndf check --write-spec`）。手で直さない

### 代表のリポジトリでの振る舞い

| 代表のリポジトリ | 設定の要点 | 直した後に起きること | この種類で扱えなくなること |
| --- | --- | --- | --- |
| Web サービス | 学習の設定はアカウントの値で、リポジトリの設定を読まない | 学習に使う設定のアカウントでも、cross-review などが claude・codex を起動して進む | 無し |
| モバイルアプリ | 同上 | 同上 | 無し |
| 業務文書だけ | 同上 | 文書の工程が claude・codex を起動するときに同じく進む | 無し |

### ai-plugins だけの事情

- 無し（学習の設定はアカウントの値で、このリポジトリの運用で決めた値を使わない）

## 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| 学習の設定の確認 | 学習の設定を読み、学習に使う設定なら書き換えて読み直し、結果を記録すること（`ndf training check` と、記録が無いか期限切れのときの起動の口） | 意味の変更 |
| 学習の設定の書き換え | 確かめた結果が学習に使う設定のとき、true だったキーだけを false へ書き込むこと。書き込みの後に読み直して結果を決める | 意味の変更 |
| 書き換えの無効化 | （10 系の `NDF_TRAINING_OPTOUT=0`。決定 8 で手段を持たない） | 廃止（用語集から消す） |

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 1. claude の書き換え | `grove_enabled` が true → `PATCH` → 読み直して false の応答で、`check --runtime claude` が `ok`、記録と出力の項目の `rewrites` に `grove_enabled`・true・false・時刻が載る。送った本文が `{"grove_enabled": false}` で見出しが読むときと同じ | 書き込みを送らないようにする・読み直しを抜いて書き込みの応答で決める・`rewrites` を記録か出力の片方にしか載せない |
| 2. codex の書き換え | 3 つのうち 2 つが true のとき、その 2 キーだけに `feature=<キー>&value=false` が送られ、読み直して 3 つとも false なら `ok`、`rewrites` に 2 要素 | 3 キーを全部送る・1 キー目の失敗で残りを送らない・キーごとでなく 1 要素にまとめる |
| 3. 起動の口のその場の確かめ | 記録が無いとき、`_read_now`（差し替えない）が HTTP のモックで true → 書き換え → false を受け、起動が続き、記録に `rewrites` が残る | `_read_now` が `settle` でなく読むだけの関数を呼ぶ |
| 4. 失敗のとき断る | 書き込みが 403・通信の失敗・読み直しても true・読み直しが 500 の 4 つで、記録が `training: true`、`check` が `stopped`（`refused`）、`summary` と `refusal` の理由に失敗の中身・`MANUAL_OFF` の場所・`ndf training check --runtime <担当>` が載る | 失敗を `training: null` にする・場所かコマンドを理由から落とす・失敗を `ok` にする |
| 5. 書き込みを送らない場合 | false のとき・トークンが無い・OAuth でない接続・応答の形が違うときに、モックが受けた要求が `GET` だけ（または 0 件） | true でない読み取りでも書き込みを送る |
| 6. 秘密を含めない | 書き換えた場合と失敗した場合の記録・出力・理由の文に、トークンとアカウントの識別子（`account_id`）が現れない | 理由の文に見出しや応答の本文をそのまま入れる |
| 7. 今のテスト | `test_training.py`・`test_training_ensure.py` を変えずに通す | `Reading` の 3 引数の形・`ensure` の `read`・`_read_now` の名前と形を変える |
| スキーマ | `rewrites` を持つ `check` の出力が `training.result.schema.json` を通り、項目の足りない `rewrites` の要素が通らない | `rewrites` をスキーマに足し忘れる・要素を開いたままにする |

詳細設計は [issue-3907-design-detail.md](./issue-3907-design-detail.md) にある（新設する関数が 2 つ以上で、`Reading` と `Endpoint` の形を変えるため）。

## 設計の結果

| 課題 | 扱い | 統合先 | 触るファイル |
| --- | --- | --- | --- |
| #3907 | 実装する | — | `plugins/ndf/src/ndf/adapters/training.py`、`plugins/ndf/src/ndf/adapters/training_cache.py`、`plugins/ndf/contracts/schemas/io/training.result.schema.json`、`plugins/ndf/contracts/limits.json`、`plugins/ndf/docs/contracts/commands.md`、`plugins/ndf/README.md`、`plugins/ndf/tests/adapters/test_training_optout.py`（新設） |

用語集は決定 10 のとおり、この設計の Pull Request で直した。実装では触らない（`docs/glossary/glossary.json` と、そこから作る `docs/glossary.md`・`docs/glossary/ndf-training-optout.md`）。

## 人へ残す判断

| 判断 | 当たるレッドライン | なぜ当たるか |
| --- | --- | --- |
| 学習に使う設定のアカウントで、利用者の操作なしにアカウントの学習の設定を Off へ書き換え、止める手段を持たせない（決定 8） | C3 | アカウントの設定は利用者のデータで、ブラウザの claude.ai・ChatGPT にも効き、claude では会話の保存期間も変わりうる |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| true → false の実際の変化 | claude の `grove_enabled` と codex の `codex_training_allowed`・`codex_training_allowed_v2` は false → false だけを確かめた（2026-11-20）。codex の `training_allowed` の true → false は 10 系が 2026-10-02 に確かめた。リリースの後、利用者が設定画面で On にしてから `ndf training check` を打つと分かる |
| 書き込みの反映の遅れ | claude は 202 を返す。直後の読み直しで新しい値が返るかは、false → false では分からない。上と同じ確かめで、読み直しの `after` が false かで分かる |
| 手で直す場所の項目の名前 | codex の 2 つのキーと Codex の設定の画面の項目の対応は名前からの推定（10 系の未確認と同じ）。画面の名前はサービスが変えうる |
| Team・Enterprise のアカウント | 組織が学習の設定を決めるアカウントで書き込みが拒まれるかは確かめていない。拒まれれば決定 5 の失敗として断る |

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | 書き込みの応答の状態（202）が何を約束するかで、読み直すかの判断が変わる |
| 主要な製品 | 調べる | 書き込む先は公開されていない API で、同じ API を書いている製品の形だけが根拠になる |
| ライブラリとサービス | 調べない | 送るのは HTTP の要求 3〜5 回で、プロジェクトが既に使う httpx（0.28.1、`uv.lock`）で足りる。比べる候補になる層が無い |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | RFC 9110 の 15.3.3「202 Accepted」 | 202 は要求を処理のために受け付けたが処理は終わっていないことを表し、後で処理されないこともありうる | 書き込みの応答では成否を決めず、読み直しで決める（決定 2） | https://www.rfc-editor.org/rfc/rfc9110.txt（2026-11-20） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | Claude Code 2.1.329 | Grove の通知から `updateGroveSettings` で `oauth/account/settings` へ書き込み、失敗を `api_grove_settings_update` として記録する | claude の書き込みの口と形（決定 3） | 本体の `strings` に `updateGroveSettings failed`・`api_grove_settings_update` があること（`~/.local/share/claude/versions/2.1.329`、2026-11-20）と、要求仕様の「書き込みを確かめた結果」 |
| PRD2 | codex（CLI） | 学習の設定を読む口だけを持ち、書き込まない | codex の書き込みの形は CLI から採れず、10 系の実測と 2026-11-20 の確かめに頼る | 要求仕様の「書き込みを確かめた結果」 |
| PRD3 | NDF 10 系（#1597） | SessionStart の hook で読んで `PATCH` し、読み直さない。codex は問い合わせの文字列で 1 キーずつ送る | 採るものと採らないものは決定 9 | `issues/old/issue-1597-design-decisions.md`・`git show 976513356^:plugins/ndf/scripts/lib/codex_training.py`（2026-11-20） |

### 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| Claude Code 本体の書き込みの本文と見出し | 本体のバイナリを前後の文字つきで `grep` | 120 秒で終わらず打ち切った。形は 2026-11-20 の確かめ（要求仕様）で代える |
| claude・ChatGPT の設定画面の項目の名前の公式の説明 | 試していない | 定数の文は 10 系の記録と要求仕様の語から作る。未確認のまま残ること |

調べた量: 候補 4 件・取得の試み 4 回。
