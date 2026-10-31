# Neural Development Factory: 起票の依頼と起票の代行

実行ジョブの中で別のリポジトリの修正が要るとわかったとき、ランナーの権限を広げずに相手先へ届ける経路を定める。ランナーが**起票の依頼**（`issue.requested`）を進捗の事象として送り、サービスの**起票の代行**が起票してよい先へ起票するか、下書きを人へ回し、**起票の依頼の結果**（`issue.request_resolved`）を残す。進捗の事象の形・受け口の照合（I45）・`appendIssueRequestResolved` は [契約の定義と型・進捗の受け口・実行ジョブの token](ndf-factory-contracts-and-progress.md)（以下「受け口の文書」）に、種類と受け手は [サービス全体の契約](ndf-factory-service-boundary.md) にある。

## 用語

定義は [用語集](../glossary.md) が正である（文脈 `ndf-factory`）。この文書で使う語は「起票の依頼」「起票の依頼の結果」「起票の代行」「下書き（起票の依頼）」「進捗の事象」「実行ジョブの token」「スプリントの実行」「ランナー」である。

## 背景

利用者は、申告する Pull Request を実行のリポジトリに限る照合を認めたうえで、別のリポジトリの修正が要る場合の経路を求め、起票はサービスが代わりに行い、ランナーは依頼を送るだけにし、権限の無い先は下書きを人へ回し、ランナーの token は 1 リポジトリのまま広げないと決めた（設計 Pull Request [#2233](https://github.com/devbasex/ai-plugins/pull/2233) の承認ゲート 1、2026-10-31）。

- **別のリポジトリへの起票は、ランナーが依頼を進捗の事象として送り、サービスが代わりに起票する。** 依頼を事象にすれば、token の検証・冪等の鍵・送り直しがそのまま使え、依頼と結果が実行の列に並ぶ。受け口は起票先が token の `repo` と同じ依頼だけを拒み（実行のリポジトリへはランナーが自分の導入の token で起票する）、起票してよいかの判定は受け口の外の起票の代行が行う。受け口が登録と GitHub を読むと、照合を token の値だけで閉じる境目が崩れる
- **下書きは承認の依頼にせず、結果の事象を知らせとして回す。** 承認を求める種類はゲート 1・ゲート 2 の 2 つだけで（全体の契約の I11）、下書きの起票はスプリントの進行を止める答えを要らない
- **相手先の流儀の判定は #2239 が持ち、ここは判定を呼ぶ口（`FilingStyleResolver`）だけを持つ。** NDF の側と判定が 2 つになると、片方だけ直したときに食い違う。起票に使う token は Issues の書き込みだけのまま広げない
- **作成の要求の失敗はやり直さない。** GitHub の Issue の作成は冪等の鍵を持たず、作ったのに応答が届かなかったときに 2 件目を作らないため
- **起票する先を、依頼の実行のアカウントの登録と、公開範囲が広がらない向きに限る。** ほかの利用者のリポジトリへ書かないためで、ほかのアカウントの同意で起票する形は同意の記録と取り消しの経路が要り、利用者の答えの範囲を超える。上限は 1 つの実行の暴走を止めるためで、日や起票先ごとの数は数えない

## 対象範囲

- **含む**: `factory/api/src/issue-requests/`（判定と流れ・台帳・秘密らしい値・流儀の判定の口）、`factory/api/src/handlers/issue-requests.ts`、GitHub の口の `createIssue`・`repositoryVisibility`、ランナーの翻訳の `issue.requested`、NDF の `lib/runner_issue_request.py`（`issue-file.py create` から呼ぶ）
- **含まない**: 相手先の流儀（課題追跡の宣言・Issue テンプレート・外部チケット）を読む判定、下書きを受けた人への知らせの送り方と見せ方、作った Issue の番号を依頼の元の課題へ書き戻すこと、ストリームと表などインフラの定義（private リポジトリが作る）

## 仕様

### 集約

| 集約 | 持ち主（書き換えてよいもの） | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| 起票の依頼の台帳 | 起票の代行（`factory/api/src/issue-requests/`） | スプリントの実行ごとの台帳 | 依頼（中身の要約値ごと） | 状態（`filing`・`filed`・`drafted`）・起票した数 |

**起票の代行は、ほかの集約を読むだけで書かない。** スプリントの実行（アカウントを知る）と登録（導入を知る）は #2186 の表を読む。書くのは自分の台帳と、進捗の記録への直の追記（`issue.request_resolved` だけ）である。台帳の依頼の行と起票した数の行は同じ集約で、1 つのトランザクションで書く。

### 構成要素

| 要素 | 場所 | 責務 |
| --- | --- | --- |
| 起票の代行 | `factory/api/src/issue-requests/`（`resolve.ts`・`ledger.ts`・`memory-ledger.ts`・`secrets.ts`・`style.ts`） | 記録された `issue.requested` 1 件を判定し、起票してよい先へは GitHub の口で起票し、そうでない先は下書きへ回し、結果を直に追記する（「起票の依頼と起票の代行」）。`memory-ledger.ts` はテスト用の偽物 |
| 起票の代行の handler | `factory/api/src/handlers/issue-requests.ts` | 事象の表の DynamoDB Streams のうち、`type` が `issue.requested` の新しい項目を 1 件ずつ起票の代行へ渡す。投げた項目だけを `batchItemFailures` で返し、ストリームがやり直す |

### 判定と流れ

実行ジョブの中で NDF が開発対象リポジトリと別のリポジトリへの起票を決めると、`issue-file.py create` が承認資料の代わりにランナーの渡したファイル（`NDF_ISSUE_REQUEST_FILE`、作業のディレクトリの外）へ `{"repo", "title", "body"}` の 1 行を足して 0 で終える。ランナーは伸びた行を `issue.requested` にして送る（値はそのまま写し、題か本文が上限を超える行は送らずに `attention`（`reason` は `issue_request_too_large`）にする）。ランナーの外では変数が無いため、NDF は今どおり承認資料を書いて止まる。

受け口は依頼を記録して 202 を返すだけで、GitHub もほかの集約も読まない。起票の代行は事象の表のストリームから依頼を受け、次の順で判定する。外へ出す前に安く確かめられるものから並べ、起票した数は起票の直前に増やす。

| 順 | 確かめること | 合わなければ（`reason`） |
| --- | --- | --- |
| 1 | 題と本文に秘密らしい値（AWS のアクセスキー・PEM の秘密鍵の始まりの行・GitHub の token・Anthropic の API キー・JWT の形）が無い | `secret_suspected` |
| 2 | 依頼の `sprint_run_id` からサービスの表で引いたアカウントの登録に、名前が大文字と小文字を問わず一致するものがある（ほかのアカウントの登録は引かない） | `not_registered` |
| 3 | 流儀の判定の口（`FilingStyleResolver`）が GitHub Issues を返す。#2239 が入るまでの既定は「GitHub Issues へ、テンプレートを使わず、依頼の題と本文のまま」 | `unsupported_tracker` |
| 4 | 起票先の公開範囲を、1 リポジトリと `metadata: read` だけに絞った導入の token で読める。起票先は実行のリポジトリより広く見えない（広さは private < internal < public。AI が書いた本文は非公開のコードや設定を含みうる） | 読めない → `not_installed`、公開範囲が広がる → `visibility` |
| 5 | 起票した数を上限（`ISSUE_REQUEST_LIMIT_PER_RUN`、既定 10）より少ないときだけ増やせる。下書きは数に入れない | `limit_exceeded` |
| 6 | 起票先の 1 リポジトリ（数の ID）と `issues: write` だけに絞った導入の token で Issue を作れる | token を作れない → `not_installed`、作成の要求の失敗 → `filing_failed`（どちらも数を 1 つ戻す） |

- **同じ中身の依頼から Issue は 1 件だけ作る。** 台帳の行（`RUN#<sprint_run_id>` / `ISSUE#<要約値>`）を条件つきで書いた依頼だけが判定へ進み、同じ中身の別の依頼（送り直し・再開のジョブ）は最初の結果を `duplicate_of` つきで写す。最初の依頼が起票の途中（`filing`）なら投げてやり直させる。要約値は `{"body", "repo"（小文字）, "title"}` を鍵の順にそろえた JSON の sha256 である
- **作成の要求の後はやり直さない。** 要求を出す直前に行へ `attempted_at` を書き、それがある `filing` の行を流し直すと作り直さずに `filing_failed` にする。GitHub が作ったのに応答が届かなかったとき 2 件目を作らないためで、GitHub の Issue の作成は冪等の鍵を持たない
- **読み出しの失敗（表・GitHub の公開範囲）は投げてやり直させる。** 行は自分の `request_key` の `filing` のまま残り、やり直しは続きから進む
- **下書きは結果の事象で人へ回る。** `outcome` が `drafted` の `issue.request_resolved` を #2173 が知らせとして扱い、#2172 が依頼の題と本文を写せる形で見せる。承認の依頼にはしない
- **ログは種類・依頼の鍵・結果・理由・写したかの 5 列だけで、題・本文・token を出さない**

### 常に成り立つ条件

受け口の文書の I44・I45・I50 に続き、台帳の条件を置く。

| # | 集約 | 条件 | 破れそうなときの扱い |
| --- | --- | --- | --- |
| I46 | 起票の依頼の台帳 | サービスが Issue を作るのは、依頼の実行が属するアカウントの登録に名前の一致するものがあり、その導入で token を作れ、流儀の判定が GitHub Issues を返し、公開範囲が広がらず、秘密らしい値が無く、起票した数が上限より少ないときだけである | どれかが欠ければ起票せず、`drafted` と理由を残す |
| I47 | 起票の依頼の台帳 | 起票に使う導入の token は、起票先の 1 リポジトリ（数の ID）と `issues: write` だけで作り、GitHub の口の中で 1 回使って捨てる。保存しない・返さない・ランナーへ渡さない | GitHub の口の `createIssue` は Issue の参照だけを返す |
| I48 | 起票の依頼の台帳 | 1 つのスプリントの実行で、起票先・題・本文の等しい依頼から作る Issue は 1 件までで、どの依頼にも結果はちょうど 1 件ある | 台帳の行を条件つきで書いてから GitHub を呼ぶ。結果の冪等の鍵は `<依頼の鍵>:resolved` |
| I49 | 起票の依頼の台帳 | 1 つのスプリントの実行で起票した数は上限（`ISSUE_REQUEST_LIMIT_PER_RUN`）以下 | 数の行の条件つきの増加が通らなければ起票せず `limit_exceeded` |

## データ・設定

### 起票の依頼の台帳

DynamoDB の表（`ISSUE_REQUESTS_TABLE_NAME`）。区分の鍵 `pk`・並びの鍵 `sk`、読み出しは強い整合で、GSI は持たない。**時系列の扱いは「今の値の上書き」である。** 過去は進捗の記録（`issue.requested` と `issue.request_resolved`）が持ち、上書きで失うのは `filing` の時刻だけで、結果の事象の `at` から足りる。

| 行 | `pk` | `sk` | 列 |
| --- | --- | --- | --- |
| 依頼 | `RUN#<sprint_run_id>` | `ISSUE#<要約値>` | `state`（`filing` / `filed` / `drafted`）・`request_key`（最初の依頼の冪等の鍵）・`issue_repo`・`issue_number`・`issue_url`（`filed` のとき）・`reason`（`drafted` のとき）・`attempted_at`（Issue の作成の要求を出す直前に書く）・`updated_at` |
| 起票した数 | `RUN#<sprint_run_id>` | `FILED_COUNT` | `count`（I49） |

依頼の行は `attribute_not_exists(pk)` つきの `Put` で作る。数の増加と `attempted_at` は `TransactWrite` 1 回で書き、数の行の条件（`count < 上限`）で取り消されたときだけ上限として読む。

| 機能 | 依頼の行 | 数の行 | 進捗の記録 | スプリントの実行・登録 |
| --- | --- | --- | --- | --- |
| 依頼の受け付け | — | — | C（`issue.requested`） | — |
| 起票 | C・U | U | C（`issue.request_resolved`） | R |
| 下書き | C・U | — | C（`issue.request_resolved`） | R |

### 環境変数

| 名前 | 使う側 | 意味 |
| --- | --- | --- |
| `ISSUE_REQUESTS_TABLE_NAME` | 起票の代行 | 起票の依頼の台帳の表の名前 |
| `ISSUE_REQUEST_LIMIT_PER_RUN` | 起票の代行 | スプリントの実行ごとにサービスが作る Issue の上限（1 以上の整数。無い・形が違えば 10）。10 は実測に基づかない初期値で、本番の結果の事象の `limit_exceeded` の数で見直す |

## 外部連携

| 渡す形 | 中身 |
| --- | --- |
| 起票の代行 | `factory/api/src/handlers/issue-requests.ts` の `handler`（DynamoDB Streams、部分的なバッチの失敗の報告を使う）。事象の表のストリーム（新しい項目の像）を `type` が `issue.requested` の項目に絞って渡す。起票の依頼の台帳の表（区分の鍵 `pk`・並びの鍵 `sk`）を作る。やり直しの回数と行き先はインフラが決める |

## セキュリティ

- **起票の代行が起票する先は、依頼の実行のアカウントが登録し App を入れたリポジトリだけで、使う token は 1 リポジトリと `issues: write` だけである**（I46・I47）。サービスが利用者に代わって外へ書く条件を変えることは認可の変更のため、人が承認ゲートで見る
- 依頼の本文・題・token はログ・例外の文に出さない。ストリームの handler がやり直しを記録するときも依頼の鍵だけを出す

## テスト観点

テストは vitest（`factory/api/src/issue-requests/*.test.ts`・`github/app.test.ts`・`handlers/issue-requests.test.ts`・`runner/src/runner.test.ts`）と pytest（`plugins/ndf/scripts/tests/test_issue_file.py`）にある。台帳が DynamoDB へ送る形は `aws-sdk-client-mock` で確かめる。

| 観点 | 縛る振る舞い |
| --- | --- |
| 起票の代行（I46〜I50） | 偽の登録と GitHub の口で、同じアカウントが登録し導入に入った先へ依頼の題と本文の Issue が 1 件でき、結果が参照を持つこと。登録が無い・ほかのアカウントだけが登録はどちらも `not_registered`、導入が起票先を含まないと `not_installed`。流儀の判定が GitHub Issues 以外なら `unsupported_tracker`。起票先が実行のリポジトリより広く見える（公開と非公開・社内、社内と非公開）と `visibility`。同じ中身の依頼を別の `seq`・別のジョブで流すと Issue は 1 件で `duplicate_of` を持ち、同じ依頼を 2 回流しても結果は 1 件。`attempted_at` のある行は作り直さず `filing_failed`。上限 2 で 3 件目が `limit_exceeded`、GitHub が断ると数が戻ること。秘密らしい値で `secret_suspected`、ログに題と本文が無いこと。本物の GitHub の口が token の作成の要求に `repository_ids` 1 つと `{ issues: "write" }` だけを送り、token を返さないこと |
| ランナーと NDF の起票の依頼 | ランナーが依頼の行を `issue.requested` にし、上限を超える行を `attention`、形の違う行を送らないこと。`issue-file.py create` が `NDF_ISSUE_REQUEST_FILE` があり起票先が別のときだけ 1 行を足して 0 で終え、承認資料を書かないこと（pytest） |

## 関連リンク

- 課題: [#2216](https://github.com/devbasex/ai-plugins/issues/2216)（要求と受け入れ条件の正本）、相手先の流儀の判定の [#2239](https://github.com/devbasex/ai-plugins/issues/2239)
- 設計 Pull Request: [#2233](https://github.com/devbasex/ai-plugins/pull/2233)
- 受け口の文書: [ndf-factory-contracts-and-progress.md](ndf-factory-contracts-and-progress.md)、サービス全体の契約: [ndf-factory-service-boundary.md](ndf-factory-service-boundary.md)
