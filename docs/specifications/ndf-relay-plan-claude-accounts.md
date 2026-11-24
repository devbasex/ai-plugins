# `ndf plan` の `claude -p` の起動先: 中継の子と同じアカウントで動き、上限のときだけ替え先で 1 回打ち直す

中継の子（オーガナイザー・レーンの conductor）の中で打った `ndf plan` の worker の `claude -p` は、子の環境をそのまま継ぎ、
子と同じ起動先（登録済みアカウント・従量の接続）で動く。起動の前に、中継の子と同じ確かめ（認証の上書き・使用の届け・
従量の接続の照合）を当て、外れれば起動しない。登録済みアカウントの `claude -p` が利用上限に当たったら、そのアカウントを
上限として替え先を選び、同じ呼び出しを 1 回だけ打ち直す。API キーの子（`key:<名前>` と API キーの宣言の `metered`）は、Claude Code
2.1.330 の `claude -p` にキーを安全に渡せる起動の形が無いため起動しない。Claude Code だけが対象である。

例: `work1` で動くレーンの conductor が `ndf plan` を打ち、`work2` も登録している利用者のとき。

| 順 | 起きること |
| --- | --- |
| 1 | worker の `claude -p` は、子から継いだ `CLAUDE_CONFIG_DIR=<保存先>/work1`・`NDF_CLAUDE_ACCOUNT=work1` で起動する。起動の前に worker の作業ディレクトリで認証の上書きを確かめ、`work1` の使用の届けを取り、関所で起動する |
| 2 | `claude -p` が利用上限で終わる。`work1` を上限として替え先を選び（ファイルには何も書かない。ほかの子のラッパーは使用率の API の取得で `work1` の上限を知る）、標準エラーへ `ndf plan: アカウント work1 が利用上限に当たった。work2 で打ち直す` を出す |
| 3 | `work2` の起動の前の用意を済ませ、同じ呼び出しを `work2` の設定ディレクトリで 1 回だけ打ち直す |
| 4 | 打ち直しも上限なら、もう打ち直さずに上限の失敗を返し、計画の打ち直しの待ち（`supervise.limit-wait-max`、最長 3 時間）へ進む。子のアカウントは替えない（子は次の上限か見回りで替わる） |

この文書は、その形の構成要素・常に成り立つ条件・決定と理由・テスト観点を残す。中継のラッパーの子の起動と切り替えは
[ndf-relay-account-config-dir.md](ndf-relay-account-config-dir.md)・[ndf-relay-account-switching.md](ndf-relay-account-switching.md)・
[ndf-relay-metered-and-api-keys.md](ndf-relay-metered-and-api-keys.md) にある。

| 何を読むか | 正本 |
| --- | --- |
| `claude -p` の起動と確かめの口 | `plugins/ndf/src/ndf/adapters/claude.py`（`call_claude`・`RelayChecks`・`set_relay_checks`・`KEY_FORMS`・`launch_form`） |
| 確かめと打ち直しの本体 | `plugins/ndf/src/ndf/domains/relay/plan_claude.py`（`begin`・`retry`） |
| 替え先の環境 | `plugins/ndf/src/ndf/domains/relay/account_choice.py`（`target_env`・`retry_env`・`reverify`） |
| 差し込み | `plugins/ndf/src/ndf/__main__.py` |

## 用語

「登録済みアカウント」「従量の接続」「使用の届け」「起動の前の用意」の定義は用語集（[docs/glossary.md](../glossary.md)）と
[ndf-relay-account-config-dir.md](ndf-relay-account-config-dir.md) の「用語」にある。この文書の「中継から継いだ起動先」は、
環境に `NDF_CLAUDE_ACCOUNT` と `NDF_RELAY_DIR` の両方が空でない値で入っていることを指す（`adapters/claude._inherited`）。

## 背景

- 11 系の `ndf plan` の `claude -p` は子の環境をそのまま継ぐ。子をアカウントの設定ディレクトリで起動するようにした（#3604）ので、
  worker も子と同じアカウントで動き、同じ確かめが要る
- `claude -p` が上限に当たると、計画の打ち直しの待ち（最長 3 時間）へ進んでいた。替え先があっても worker が止まる（#3864 の決定 9）
- `claude -p` は status line を動かさないので、呼び出しごとに替え先を選ぶ入力が無い。上限に当たったときだけ選べば、上限に当たった
  事実と、中継のラッパーが使用率の API で取った記録が入力になる

## 対象範囲

| 含む | 含まない |
| --- | --- |
| 中継から継いだ起動先での `claude -p` の起動の前の確かめ・使用の届け・上限での 1 回の打ち直し | 中継の外で打つ `ndf plan`（確かめも届けも無しで今までどおり起動する） |
| 従量の接続の親からの起動の照合 | `ndf plan` を API キーの規則で動かす経路（#3991） |
| API キーの子を起動しないこと | 認証の失敗での打ち直し・呼び出しごとの選び直し（作らない） |

## 仕様

### 層の向き

`adapters/claude.py` は中継の部品を読まない（層の向きは `contracts` → `adapters` → `core` → `progress` → `domains`）。
`adapters/claude.py` は差し込みの口（Protocol の `RelayChecks` の `begin`・`retry`、モジュールの変数 `relay_checks`、
`set_relay_checks`）だけを持ち、全層より上のエントリポイント `ndf/__main__.py` が起動時に `domains/relay/plan_claude` を
差し込む。口が None（エントリポイントを通らない呼び出しとテスト）なら確かめを当てない。

### 起動の流れ

`call_claude` は、口が差し込まれ、中継から継いだ起動先のときだけ次を行う。そうでなければ今までどおり起動する。

1. 起動の形（`launch_form`。`json`・`stream`・`min-json`・`min-stream`）がキーを渡せる形（`KEY_FORMS`）かを決める。今は空である
2. 前置き（学習の設定・起動のスロット・CLI の版の下限）より先に `begin` を打つ
3. `begin` が返した最終の環境・起動引数・関所で前置きを打って起動し、終わり方によらず後始末を 1 回だけ打つ
4. 結果が `Failure.USAGE_LIMIT` なら `retry` を打ち、替え先の起動があれば 1 回だけ打ち直してその結果を返す

`begin` の確かめは、親の起動先ごとに分かれる。

| 親の `NDF_CLAUDE_ACCOUNT` | 確かめ | 外れたとき |
| --- | --- | --- |
| 登録済みアカウントの名前 | worker の最終の環境・起動引数・作業ディレクトリで `account_dir.auth_overrides` を打つ（`minimal` の起動は `--setting-sources ''` なので、利用者・プロジェクトの設定は見ず、管理の設定と `--settings` だけを見る）。通ったら `account_dir.acquire` で worker 自身の使用の届けを取り、関所で起動して `attach` し、終わった後に `release` で消す。継いだ世代は親が渡した `NDF_ACCOUNT_GENERATION` から読み、`acquire` は設定ディレクトリと世代が今の登録と同じときだけ届けを書く。起動のスロットを取った後、起動の直前に同じ `auth_overrides` を打ち直す（`recheck`。スロットを待つ間に足された `apiKeyHelper`・接続先の上書きで送らない） | 認証の上書き（最初の確かめでも起動の直前の `recheck` でも）: 項目名と「その設定から項目を外す」を入れた `Failure.REFUSED`。届けを取れない（`config_mismatch`・`reregistered`・排他の待ち切れ・書けない）・関所を開けられない: `Failure.LAUNCH` |
| `metered` | 従量の接続の設定を読み直して `account_choice.target_env` でラッパーと同じ合成と照合を当て、その環境と起動引数で起動する。読み直した設定から作った親の接続の識別（`Built.pin`）が環境の `NDF_RELAY_PIN` と同じことを確かめる。起動のスロットを取った後、起動の直前に `reverify` で照合し直す | `metered_overridden`（設定ファイルが宣言と別の接続先・ヘッダーなどを足す）・`metered_changed`（親の起動の後に設定が替わった・`NDF_RELAY_PIN` が無い）で `Failure.REFUSED` |
| `key:<名前>`・API キーの宣言の `metered` | 起動の形が `KEY_FORMS` に無いので起動しない | `key_gate` の `Failure.REFUSED` |

`retry` は、親が登録済みアカウントのときだけ打ち直す。取得の記録の行のうち今のアカウントを上限とした行で、
`account_choice.retry_env` が替え先を選ぶ（何も書かない。[ndf-relay-account-switching.md](ndf-relay-account-switching.md) の `after_limit` の
登録済みアカウントと従量の接続の順。`keys` が偽の間はキーの子を替え先にしない）。登録済みアカウントの替え先は `prepare` の
届けのまま、起動のスロットを取った後の起動の直前に替え先の設定ディレクトリで `auth_overrides` を打ち直し（`recheck`）、
関所で起動し、終わった後（成功・失敗・タイムアウトのどれも）に `settle` を打つ。最初の起動の届けは打ち直しの前に消す。
失敗の返し方は照合の時点で分ける。替え先を選ぶ間（`retry_env`）に替え先が無い・`prepare` か照合が落ちたときは、打ち直さずに
元の上限の失敗（`Failure.USAGE_LIMIT`）を返す。スロットを取った後の `recheck` に落ちたときは、子を起動せずに `Failure.REFUSED` を
返し、起動しなかったので `settle` でなく `release` で替え先の届けを消す。打ち直しも上限なら 2 回目は打たない。
`metered`・`key:`・既定のログインの親は打ち直さない。

### 常に成り立つ条件

| # | 条件 | 守り方 |
| --- | --- | --- |
| P1 | 中継から継いだ起動先の `claude -p` は、親の子と同じ認証・接続先・設定で送る。確かめに外れれば起動しない | `begin` が親ごとの確かめを前置きより先に打つ |
| P2 | worker が動いている間、その登録済みアカウントの再登録・削除は止まる。旧い世代から始めた worker は、親の子の後に登録し直された新しい本人の認証で送らない | `acquire` の届けと世代の照合（I10） |
| P3 | 上限での打ち直しは 1 回だけで、例外でも届けと合成の一時ファイルを残さない | `retry` の後の結果を返すだけにし、後始末を `try`/`finally` で囲む |
| P4 | `ndf plan` のキーの子は、最初の払い出しを見届けられない起動の形でキーを渡さない | `KEY_FORMS` が空の間は起動しない |

## データ・設定

新しいファイルと設定は持たない。使う記録は [ndf-relay-account-config-dir.md](ndf-relay-account-config-dir.md) の使用の届けと、
[ndf-relay-account-switching.md](ndf-relay-account-switching.md) の使用率の取得の記録（読むだけ）、
[ndf-relay-metered-and-api-keys.md](ndf-relay-metered-and-api-keys.md) の合成の一時ファイルである。子の環境の `NDF_RELAY_PIN` は、
ラッパーが `metered` と `key:<名前>` の子にだけ入れ、ほかの子では外す。

## 外部連携

| 相手 | 使い方 |
| --- | --- |
| Claude Code の `claude -p` | Claude Code 2.1.330 は、`call_claude` の 4 つの起動の形のどれでも、標準入力を読み終えるか 3 秒待ち切るまで `apiKeyHelper` を呼ばない（2026-11-22 に実測。#4086）。そのため質問を書く前に払い出しを見届けられず、`KEY_FORMS` は空である |

## 決定と理由

- **環境は子から継ぎ、上限に当たったときだけ替え先で打ち直す。** 今どおり上限が解けるまで待つと、替え先があっても worker が
  最長 3 時間止まる。呼び出しごとの選び直し（10 系の形）は、`claude -p` が status line を動かさないので選ぶ入力が無い
- **子のアカウントは替えない。** 子は次の上限か見回りで替わる。そのため worker と子が別のアカウントで動く間がある
- **中継から継いだ起動先かを `NDF_CLAUDE_ACCOUNT` と `NDF_RELAY_DIR` の両方で決める。** `NDF_CLAUDE_ACCOUNT` だけでは、中継の
  外の端末で利用者が export した指定と区別できず、中継を使わない `ndf plan` が確かめに入って止まる（#4136）
- **起動の直前に確かめ直す（`AccountRun.recheck`）。** 起動のスロットを待つ間に設定が替わると、照合していない宛先へ送る（#4133）。
  従量の接続の親は `reverify`、登録済みアカウントの親と替え先は `auth_overrides` を起動の直前に打ち直し、外れれば `Failure.REFUSED` で起動しない
- **API キーの子は `ndf plan` では起動しない。** 質問を書く前に払い出しを見届けられない形でキーを渡すと、照合の後に入った別の
  資格情報で質問を持つ要求が届いた（実測）。形を `KEY_FORMS` に足すときは、`_talk` が質問を書く前に払い出しの印を
  `relay.key-helper` の `first` まで待ち、期限切れなら質問を 1 文字も書かずにプロセスグループごと止め、それを実際の起動で確かめる
  テストをそろえてから入れる

## セキュリティ

- 理由の文と失敗に、キー・トークン・接続先の値を入れない
- 確かめの範囲と脅威の範囲は中継のラッパーと同じである（[ndf-relay-account-config-dir.md](ndf-relay-account-config-dir.md) の「セキュリティ」）

## 運用

### 既知の限界

- 登録済みアカウントが尽き、退避先が API キーだけのとき、`ndf plan` の worker は API キーへ退避できず、上限の打ち直しの待ちへ
  進む。`key:` の子の中で打った `ndf plan` は待たずに止まる。対話の子は API キーで続く
- 上限の後の打ち直しは、最初の実行が道具で済ませた操作を確かめずに同じプロンプトを最初から打つので、外部への操作が重なり得る（#4143）
- 入れ子の `ndf plan` の `claude -p` は親の環境から `NDF_RELAY_DIR`・`NDF_RELAY_LAUNCH`・`NDF_RELAY_SESSION` を継ぐ。helper の記録は起動引数だけで
  決まるので外側の子に影響しない。内側の hook は会話の ID が違うので、外側の子のバックグラウンドの作業の記録を書かない
  （[ndf-relay-background-wait.md](ndf-relay-background-wait.md)）。質問とマーカーのファイルは書きうる（#4061）

## テスト観点

テストは `plugins/ndf/tests/domains/relay/test_relay_plan_claude.py` と `plugins/ndf/tests/adapters/test_claude_reply.py` にある。

| 観点 | 落ちるべき壊し方 |
| --- | --- |
| 子の環境に `CLAUDE_CONFIG_DIR` があるとき、`claude -p` の環境の `CLAUDE_CONFIG_DIR`・`NDF_CLAUDE_ACCOUNT` が同じ値 | 環境を作り直してアカウントを選び直す |
| 設定ソースを有効にした起動で、worker の作業ディレクトリの `apiKeyHelper` で起動せずに項目名の失敗を返す。`minimal` の起動ではプロジェクトの `apiKeyHelper` で止めず、管理の設定の `apiKeyHelper`・環境の `ANTHROPIC_API_KEY`・既定でない `ANTHROPIC_BASE_URL` で止める | 起動の形ごとの源を見ない |
| `work1` の通常の起動の間 `<保存先>/.leases/work1/` に `running` の届けがあり、終わった後（失敗・タイムアウトでも）に無い。親の子の後に `work1` を登録し直すと `reregistered` で起動しない | 届けを取らない・世代を見ない |
| `metered` の親で、設定ファイルが別の接続先かヘッダーを足すと `metered_overridden`、親の起動の後に `metered.json` か宣言を替えると `metered_changed`、`NDF_RELAY_PIN` が無くても起動しない | 読み直した設定だけで照合する |
| `key:` の親と API キーの宣言の `metered` の親は `key_gate` で起動しない | 起動の形を見ずにキーを渡す |
| `work1` の上限で、取得の記録が `work1` を上限と写していなくても `work1` を除いて `work2` で 1 回だけ打ち直す。打ち直しも上限なら 2 回目を打たない。替え先が無い・既定のログイン・`metered` では打ち直さない | 打ち直さない・2 回以上打ち直す・上限の `work1` で打ち直す |
| 中継の外で export した `NDF_CLAUDE_ACCOUNT` だけの環境では確かめに入らない | `NDF_CLAUDE_ACCOUNT` だけで判定する |

## 関連リンク

- [ndf-relay-account-config-dir.md](ndf-relay-account-config-dir.md)・[ndf-relay-account-switching.md](ndf-relay-account-switching.md)・[ndf-relay-metered-and-api-keys.md](ndf-relay-metered-and-api-keys.md)
- 課題: #3604 の受け入れ条件 11・#3864 の決定 9。このスプリントで直したもの: #4133・#4136。範囲外として起票したもの: #3991・#4143
