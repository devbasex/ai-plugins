## 何が起きたか

2026-11-20、NDF 11.0.8 で `ndf refactor run` を打ったところ、claude と codex が一度も起動せず、採った項目が 0 件になった。学習の設定を確かめた記録（`~/.local/state/ndf/training.json`）が無く、起動の口が起動を断っていたためである。この直し（記録が無ければその場で確かめる・始める前の確かめで止める・断った理由を見送りの理由に出す）は PR #3914 で main に入った。

その直しの後も、**アカウントが入力を学習に使う設定（On）なら、NDF は claude と codex を起動しない。** 利用者は自分でブラウザを開いて設定を Off にし、`ndf training check` を打ち直すまで、cross-review・cross-refactoring などの工程が止まる。利用者は同じ日に、この場合は NDF が設定を自動で Off にしてから起動を続けるよう求めた。

## 種類

機能追加（今の約束は「学習の設定を読むだけで書き換えない」で、書き換えて起動を続ける振る舞いは約束に無い）

## 今の姿

NDF は、LLM の CLI（claude・codex）へ作業を渡す前に、そのアカウントが入力を学習に使わない設定かを確かめる。学習に使う設定のアカウントへコードを渡さないためである。

- 確かめるのは `ndf training check` と、記録が無いか期限切れ（24 時間）のときの起動の口（`ensure`。#3914 で足した）の 2 か所。どちらも設定を読むだけで、結果を `~/.local/state/ndf/training.json` に時刻つきで記録する（[training.py](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/src/ndf/adapters/training.py) の冒頭、[commands.md の `ndf training`](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/docs/contracts/commands.md#ndf-training)）
- 読む先は公開されていない API である
  - claude: `GET https://api.anthropic.com/api/oauth/account/settings` の `grove_enabled`（claude.ai の「Help improve Claude」）
  - codex: `GET https://chatgpt.com/backend-api/settings/user` の `settings.training_allowed`（ChatGPT の「Improve the model for everyone」）と、Codex の環境の学習の `codex_training_allowed`・`codex_training_allowed_v2`。どれかが true なら学習に使う設定と読む
- 起動の口（`launch_slot.gate` が呼ぶ `refusal`）は、記録が学習に使う設定・確かめられない・期限切れなら起動を断り、理由に `ndf training check` を載せる
- kiro と agy は確かめる口が無いので、確かめずに起動する
- 10 系（#1597）は、SessionStart の hook で同じ API へ書き込み、設定を Off にしていた。11.0.0 の作り直しで読むだけになった（[#3395 の決定の表](https://github.com/devbasex/ai-plugins/blob/main/issues/issue-3395-design-decisions.md) の 3391-5 で「今より後退する」と記録）

### 書き込みを確かめた結果（2026-11-20）

利用者の承認を得て、両方のアカウントで今の値 false をもう一度 false で送った（true は送っていない）。送った後に読み直し、false のままだった。

| 担当 | 送ったもの | 応答 | 形の出所 |
| --- | --- | --- | --- |
| claude | `PATCH https://api.anthropic.com/api/oauth/account/settings`、本文 `{"grove_enabled": false}`、読むときと同じ見出し（`Authorization: Bearer`・`anthropic-beta: oauth-2025-04-20`） | 202、本文 `{"grove_enabled": false}` | Claude Code 本体（2.1.329）が Grove の通知で同じ呼び出しを送っている（`updateGroveSettings`）。10 系も同じ形 |
| codex | `PATCH https://chatgpt.com/backend-api/settings/account_user_setting?feature=<キー>&value=false` をキーごとに 1 回（3 キー）、読むときと同じ見出し | 3 回とも 200、本文 `{"<キー>": false}` | codex の本体は読む口しか持たない。10 系（#1597 の決定 14）が 2026-10-02 に実物で確かめた形 |

確かめていないこと: true から false へ実際に変わること（アカウントを一度 On にする必要があるため行っていない）。

## 何を足すか・変えるか

| | 今 | 足した後 |
| --- | --- | --- |
| 確かめた結果が学習に使う設定（true） | 起動を断り、「設定を Off にしてから確かめ直す」と出す | NDF がそのアカウントの設定を false へ書き込み、読み直して false なら記録を書いて起動を続ける |
| 書き込みに失敗した・読み直しても true | — | 今までどおり断る。理由と、手で直す場所（claude.ai・ChatGPT の設定の画面）を出す |
| 記録と出力 | 担当・真偽値・読んだ場所・時刻 | 書き換えたときは、書き換えた時刻・前の値・後の値（codex はキーごと）を足す |
| 学習に使わない設定（false）・確かめられない | 書き込まない | 変えない（書き込みを送らない） |

### 今の利用者への影響と互換

- 学習に使う設定のアカウントで NDF を動かすと、利用者の操作なしにアカウントの設定が Off に変わる。この変更はアカウントの全体（ブラウザの claude.ai・ChatGPT を含む）に効く
- `ndf training check` の結果の `items` に、書き換えの項目が足される。今の項目は変えない（コマンドは内部扱いで、予告なく変えてよい）

## なぜ困るか

学習に使う設定のアカウントでは、NDF の工程が claude・codex を起動する箇所ごとに止まる。利用者は止まった理由を読み、ブラウザで 2 つのサービスの設定画面を探して Off にし、確かめ直してから工程を打ち直す必要がある。人のいない実行（並列の計画のレーン・Factory の実行）では、人が気づくまで止まったままになる。

## 目的

- 学習に使う設定のアカウントでも、NDF が設定を Off にしてから claude・codex を起動し、工程が止まらない
- 書き換えたことが、時刻と前後の値つきで後から確かめられる
- 書き換えられないときは、今までどおりコードを渡さずに止まり、利用者が何をすればよいかがわかる

## 対象範囲

含む:
- `ndf training check` と、起動の口のその場の確かめ（`ensure`）で、学習に使う設定を Off へ書き換えて確かめ直す
- 書き換えの記録（`training.json`）と、結果 JSON の出力
- 書き換えに失敗したときの断る理由と、手で直す場所の案内
- 書き換えの振る舞いに合わせた、今の約束の文書（`training.py` の冒頭・`commands.md`・用語集の `ndf-training-optout` の語）の更新

含まない:
- #3907 の 1〜3（PR #3914 で main に入った）と 4（1 を入れたので不要になった）
- kiro と agy の学習の設定（確かめる口が無い）
- OAuth でない接続（API キー・Bedrock・Vertex など）と、ChatGPT でないログインの codex（学習の設定が無い）
- 10 系の SessionStart の hook での書き換えを戻すこと
- 学習の設定を On へ戻す手段

## 受け入れ条件

- [ ] 1. claude の設定が学習に使う設定（`grove_enabled` が true）のとき、`ndf training check --runtime claude` は `grove_enabled` を false へ書き込み、読み直して false なら `status` が `ok` で終わる。記録と出力の claude の項目に、書き換えた時刻・前の値 true・後の値 false が載る
- [ ] 2. codex の 3 つのキーのどれかが true のとき、`ndf training check --runtime codex` は true だったキーだけを false へ書き込み、読み直して 3 つとも false なら `status` が `ok` で終わる。記録と出力に、書き換えたキーごとの前後の値と時刻が載る
- [ ] 3. 記録が無いか期限切れで、起動の口がその場で確かめた結果が学習に使う設定のとき、1・2 と同じく書き換えて記録し、起動を断らずに続ける
- [ ] 4. 書き込みの応答が 2xx でない・通信に失敗した・読み直しても true のときは、記録に学習に使う設定を残し、起動を断る（`ndf training check` は `stopped`）。理由に、失敗の中身と、手で Off にする場所（claude.ai の設定のプライバシー・ChatGPT の設定のデータコントロール）と、Off にした後に打つ `ndf training check` が載る
- [ ] 5. 学習に使わない設定（false）と、確かめられないとき（トークンが無い・OAuth でない接続・応答の形が違う）は、書き込みを 1 回も送らない。結果は今と同じ
- [ ] 6. 記録・出力・理由の文に、トークンとアカウントの識別子が含まれない
- [ ] 7. 今の `ndf training check` と起動の口のテストが変えずに通る（書き換えに当たらない場合の振る舞いが変わらない）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`ndf training check` の結果の項目を足す（今の項目は変えない。コマンドは内部扱い） |
| データ | `training.json` の担当の項目に書き換えの記録を足す。移行は要らない（足した項目が無い記録も今のまま読む） |
| 既存の振る舞い | 学習に使う設定のアカウントで、起動を断る代わりにアカウントの設定を書き換えて起動を続ける |

## 用語

| 用語 | 意味 |
| --- | --- |
| 学習の設定の書き換え | 確かめた結果が学習に使う設定のとき、NDF がそのアカウントの学習の設定を false へ書き込むこと。用語集の 10 系の意味（SessionStart で書き換える）を、この振る舞いへ改める |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/adapters -q`（書き込みの API は httpx の差し替えで応答を作る） |
| 静的解析・型検査 | `bash scripts/check-lint.sh` と `python3 scripts/check-ndf-contracts.py` |
| 手動確認 | リリースの後、利用者の手元で `ndf training check` を打ち、学習に使わない設定のアカウントで書き込みを送らずに `ok` で終わることを確かめる |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 書き換えを利用者が止める手段を持たせるか（10 系は環境変数 `NDF_TRAINING_OPTOUT=0`） | 設計（設計承認ゲートで利用者が見る） | 設計 Pull Request |

## リクエスト（原文）

> ### 5. 学習の設定を自動で Off にする（2026-11-20 の利用者の追加）
>
> `training check` の結果が `training: true`（入力を学習に使う設定）のとき、断る代わりに、その担当のアカウントの設定を学習に使わない側へ書き換えてから確かめ直し、`training: false` になれば記録を書いて起動を続ける。
>
> - 今の設計は「読むだけで書き換えない」（`plugins/ndf/src/ndf/adapters/training.py` の冒頭）。この方針を変える
> - 書き込む先は公開されていない API（claude: `api.anthropic.com/api/oauth/account/settings` の `grove_enabled`、codex: `chatgpt.com/backend-api/settings/user` の `training_allowed` ほか 2 つ）。書き込みの形と通るかは未確認。設計の最初に確かめる
> - 書き換えたことを、時刻と前後の値つきで記録と出力に残す。書き換えに失敗したら今までどおり断り、理由と手で直す場所を示す
> - 1〜3 は即時修正（PR は `Refs #3907`）で先に入れ、5 は設計を通して作る

（書き手: 起票者（利用者の追加を起票者が書いた）、日付: 2026-11-20）

> 対象は #3907 の本文の「5. 学習の設定を自動で Off にする」だけ（1〜3 は PR #3914 で main に入った）。最初に、公開されていない API（claude の account/settings の grove_enabled、codex の settings/user の training_allowed ほか）へ書き込めるか・書き込みの形を確かめ、確かめた結果を要求仕様と設計に書く。アカウントの設定を本当に書き換える確かめは、利用者の承認を得てから行う。

（書き手: AI の起動指示（計画 p8-5 のレーン ltrain の `/goal`）、日付: 2026-11-20）

> 書き込みの確かめは「同じ値を書く」。claude・codex とも今の false をもう一度 false で送り、通るかと応答の形だけを確かめ、送った後に読み直して false のままかを見る。true にはしない。

（書き手: 利用者（オーガナイザーが中継）、日付: 2026-11-20 21:10 JST ごろ）

<details>
<summary>書き直す前の本文（1〜4 を含む起票時の全文）</summary>

## 何が起きたか

NDF 11.0.8 で `ndf refactor run` を実行したところ、claude と codex が一度も起動しなかった。採った項目は 0 件、見送った項目は 11 件。報告では、見送りの理由は 11 件とも「担当が結果を出さずに終わった」になっていた。

起動しなかった実際の原因は、学習の設定を確かめた記録（`~/.local/state/ndf/training.json`）が無いことだった。記録が無いと `training_cache.refusal` が起動を断る。そのため、確認の対象外の kiro だけが提案を出した。

| 確かめたこと | 結果 |
|---|---|
| `~/.local/state/ndf/training.json` | 存在しない |
| `~/.local/state/ndf/` ができた時刻 | `ndf relay install --replace-old` で 10.27.77 から 11.0.8 へ入れ替えた直後 |
| 10.27.77 | `training_cache.py` が無い（学習の確認が無い） |
| `training_cache.refusal("claude")` を直接呼んだ結果 | 「学習に使わない設定と確かめられていないので claude を起動しない（バックアップが無い）」 |
| `ndf training check --runtime claude` と `--runtime codex` を実行した後 | 両方とも `training: false` で記録された |

10 系から上げた利用者は、記録が無い状態から始まる。そのため、この利用者はみな同じ状態になる。

## 直してほしいこと

### 1. 記録が無ければ自動で作る（主）

`refusal` の理由が「バックアップが無い」または「期限切れ」で、その担当に確かめる口があるとき（`CHECKED_RUNTIMES`）は、起動を断らずにその場で `training check` を実行する。

- 結果が `training: false` なら、記録を書いて起動を続ける。
- 結果が `training: true` のとき、または確かめられなかったときだけ、今までどおり断る。理由もはっきり出す。

### 2. 始める前の確認で止める

`refactor run`（および claude・codex を担当に使う他のコマンド）は、最初に担当の `refusal` を確かめる。1 で直せない理由が残っていれば、提案や計画を流す前に `precondition` で止める。今は kiro の提案と計画まで進めてから、全部の項目を見送る。

### 3. 起動を断った理由を見送りの理由に残す

`drive.py` の `_code` は、`launch.run` の結果が None であれば、状態が `refused` や `no_slot` でも「担当が結果を出さずに終わった」（`phase_decide.py:46`）として見送る。`AgentStatus.state` と `detail` を見送りの理由と完了の報告に書く。報告から原因がわからないと、利用者は担当の不具合と誤解する。

### 4. 入れ替えのときに知らせる（1 を入れない場合）

`ndf relay install --replace-old` と、10 系からの入れ替えで、`ndf training check` が必要だと表示する。

### 5. 学習の設定を自動で Off にする（2026-11-20 の利用者の追加）

`training check` の結果が `training: true`（入力を学習に使う設定）のとき、断る代わりに、その担当のアカウントの設定を学習に使わない側へ書き換えてから確かめ直し、`training: false` になれば記録を書いて起動を続ける。

- 今の設計は「読むだけで書き換えない」（`plugins/ndf/src/ndf/adapters/training.py` の冒頭）。この方針を変える
- 書き込む先は公開されていない API（claude: `api.anthropic.com/api/oauth/account/settings` の `grove_enabled`、codex: `chatgpt.com/backend-api/settings/user` の `training_allowed` ほか 2 つ）。書き込みの形と通るかは未確認。設計の最初に確かめる
- 書き換えたことを、時刻と前後の値つきで記録と出力に残す。書き換えに失敗したら今までどおり断り、理由と手で直す場所を示す
- 1〜3 は即時修正（PR は `Refs #3907`）で先に入れ、5 は設計を通して作る

## 再現

1. 10 系から 11.0.8 へ入れ替える（`~/.local/state/ndf/training.json` が無い状態）
2. `ndf refactor run --pr <PR> --branch <branch> --base main --scope <dir> --required test`
3. 状態の `plan_fallback` が true になる。すべての項目が `add-tests: 担当が結果を出さずに終わり、…` で見送られる。claude の担当の出力ファイルは 1 つも作られない

</details>

