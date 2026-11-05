# #2384 の設計: 機能から起こした契約の操作の一覧と形

[設計文書の本体](issue-2384-design.md) の「入出力の契約」の詳細である。正は `factory/contracts/openapi/` の定義で、この文書は実装の前に形を決めるための一覧である。

## 操作の種類と形

利用者向けの操作は、次の 6 つの種類のどれかに当たる。種類は経路の形とメソッドから決まり、操作の表の生成が `kind` として書く。

| 種類 | メソッド | 入力 | 成功の応答 | 要素の数の検査 |
| --- | --- | --- | --- | --- |
| 一覧（`list`） | `GET` | 条件の query（0 個以上） | 200 と値の配列 | しない |
| 指定の取得（`select`） | `GET` | 鍵の query を繰り返す（`?sprint_run_id=a&sprint_run_id=b`） | 200 と要素の結果の配列 | 鍵の値の数 |
| 作成（`create`） | `POST` | 本文の配列 | 200 と要素の結果の配列 | 本文の要素の数 |
| 更新（`update`） | `PATCH` / `PUT` | 本文の配列 | 200 と要素の結果の配列 | 本文の要素の数 |
| 削除（`delete`） | `DELETE` | 鍵の query を繰り返す | 200 と要素の結果の配列 | 鍵の値の数 |
| 操作（`action`） | `POST`（経路の末尾が動詞） | 本文の配列 | 200 と要素の結果の配列 | 本文の要素の数 |

- **経路の変数はアカウントの ID（`{accountId}`）だけ**である。アカウントに属するものは `/accounts/{accountId}/<もの>` に置く。アカウントそのものと利用者に属するもの（`/me`・`/accounts`・`/teams`・`/delegations`）は頭に置く
- **鍵の query の名前は、要素の鍵の項目の名前と同じ**にする。本文の要素と query の鍵が同じ語で読める
- **鍵を持たない操作**（アカウントにつき 1 つしか無いもの。キーの持ち込み・取り込みの候補）は、要素が必ず 1 個である。2 個以上は同じ鍵の重複として 400 になる
- **一覧の応答を要素の結果で包まない。** 条件で集めた値は、すべて読める値だけで組むためである。条件が指すアカウントを解決できなければ、要求全体が 403 になる
- **成功の状態は、要素がすべて失敗しても 200 にする。** 要求全体を断るのは下の「要求全体の誤り」だけである

契約の各操作は、OpenAPI の拡張に次の 3 つを持つ。

| 拡張 | 持つ操作 | 値 |
| --- | --- | --- |
| `x-ndf-access` | 利用者向けのすべての操作 | `read` / `write` / `human`（操作の扱い）。ランナー向けの経路と受け口は持たない。持つかどうかで利用者向けかを判定する |
| `x-ndf-keys` | 要素を持つ種類（一覧を除く 5 つ） | 要素の鍵の項目の名前の配列。鍵を持たない操作は空の配列 |
| `x-ndf-id` | 新しい識別子を作る作成 | 成功の値の中の、作った識別子の項目の名前 |

## 要素の結果

```json
[
  { "ok": true, "value": { "sprint_run_id": "sr_1", "state": "running" } },
  { "ok": false, "status": 403, "error": { "code": "forbidden", "message": "このアカウントを見る権限がありません" } },
  { "ok": true, "value": { "sprint_run_id": "sr_3", "state": "running" } }
]
```

- 応答の配列は、入力（鍵の query の並び・本文の要素の並び）と同じ順・同じ数である
- 失敗の要素は、今の誤りの状態（400・403・404・409・422・503）と本文（`code`・`message`・`details`）をそのまま持つ。画面は状態で文言を分けているため、状態を要素の結果に残す
- 認可の無い要素は `forbidden` だけを持ち、その要素のデータを含めない。無い実行と他人の実行を同じ本文の 403 にする今の規則は、要素の単位でも変えない。この規則は、スプリントの画面の確定仕様（ [ndf-factory-sprint-views.md](../docs/specifications/ndf-factory-sprint-views.md) ）の I12 である
- 要素の形の誤り（席の種類が知らない値など）は、その要素だけを 400 の結果にする

## 要求全体の誤り

| 状態 | `code` | いつ |
| --- | --- | --- |
| 400 | `invalid_request` | 本文が JSON の配列でない |
| 400 | `empty_items` | 要素が 0 個 |
| 400 | `too_many_items` | 要素が 101 個以上。`details.limit` に 100 を入れる |
| 400 | `duplicate_key` | 同じ鍵の要素が 2 つある。`details.key` に鍵の項目の名前を入れる |
| 401 | `unauthorized` | 資格情報が無い・無効・期限切れ・取り消し済み |
| 403 | `forbidden` | 経路のアカウントを解決できない（今の 403 のまま） |
| 403 | `human_only` | 委任の資格情報で、扱い `human` の操作を呼んだ |
| 403 | `scope_insufficient` | 範囲が読み取りだけの委任の資格情報で、扱い `write` の操作を呼んだ |
| 503 | `audit_unavailable` | 操作の記録を追記できなかった |

検査の順は、資格情報 → 操作の扱い（`human_only`・`scope_insufficient`）→ 要素の数と重複 → 経路のアカウントの解決である（本体の「処理の流れ」）。

## 操作の一覧

鍵の列の `+` は複合の鍵、`—` は鍵を持たない操作を表す。画面の列は `factory/web/src/api.ts` の `Api` のメソッドで、`—` は画面に無い操作である。

### 扱いごとの数

| 扱い | 操作 | 数 |
| --- | --- | --- |
| read | 1・3・8・9・11・15・20・21・22・23・26・27・32・33・35 | 15 |
| write | 4・10・12・13・14・18・19・24・25・28・29・30 | 12 |
| human | 2・5・6・7・16・17・31・34・36・37 | 10 |

画面の `Api` のメソッドは 34（今の 31 と委任の 3）で、画面に無い操作は 32〜34 の 3 つである。

### アカウント（`accounts.yaml`）

| # | operationId | 種類 | メソッドと経路 | 要素か条件 | 鍵 | 扱い | 画面 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `getMe` | 一覧 | `GET /me` | — | — | read | `me` |
| 2 | `createTeam` | 作成 | `POST /teams` | `{name, seat_type, idempotency_key}` | `idempotency_key` | human | `createTeam` |
| 3 | `getAccount` | 指定の取得 | `GET /accounts` | `?account_id=` | `account_id` | read | `account` |
| 4 | `setExecutionMode` | 更新 | `PATCH /accounts` | `{account_id, execution_mode}` | `account_id` | write | `setExecutionMode` |
| 5 | `addSeat` | 作成 | `POST /accounts/{accountId}/seats` | `{user_id, seat_type}` | `user_id` | human | `addSeat` |
| 6 | `changeSeat` | 更新 | `PATCH /accounts/{accountId}/seats` | `{user_id, seat_type}` | `user_id` | human | `changeSeat` |
| 7 | `removeSeat` | 削除 | `DELETE /accounts/{accountId}/seats` | `?user_id=` | `user_id` | human | `removeSeat` |

`getAccount` と `setExecutionMode` は、要素ごとにアカウントを解決する（経路にアカウントを持たないため）。解決できない要素は `forbidden` の結果になる。`createTeam` の成功の値は今のアカウントの詳細で、`x-ndf-id` は `account_id` である。

### 取り込み・キー・スプリント（`runs.yaml`）

| # | operationId | 種類 | メソッドと経路 | 要素か条件 | 鍵 | 扱い | 画面 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 8 | `getInstallUrl` | 一覧 | `GET /accounts/{accountId}/github/install-url` | — | — | read | `installUrl` |
| 9 | `getAuthorizeUrl` | 一覧 | `GET /accounts/{accountId}/github/authorize-url` | — | — | read | `authorizeUrl` |
| 10 | `postRepositoryCandidates` | 操作 | `POST /accounts/{accountId}/github/candidates` | `{code, state}` | — | write | `repositoryCandidates` |
| 11 | `listRepositories` | 一覧 | `GET /accounts/{accountId}/repositories` | — | — | read | `repositories` |
| 12 | `registerRepository` | 作成 | `POST /accounts/{accountId}/repositories` | `{proof, repository_id}` | `repository_id` | write | `registerRepository` |
| 13 | `updateRepository` | 更新 | `PATCH /accounts/{accountId}/repositories` | `{repository_id, max_concurrent_runs}` | `repository_id` | write | `updateRepository` |
| 14 | `removeRepository` | 削除 | `DELETE /accounts/{accountId}/repositories` | `?repository_id=` | `repository_id` | write | `removeRepository` |
| 15 | `getModelCredential` | 一覧 | `GET /accounts/{accountId}/model-credential` | — | — | read | `modelCredential` |
| 16 | `putModelCredential` | 更新 | `PUT /accounts/{accountId}/model-credential` | `{kind, api_key}` か `{kind, role_arn, region}` | — | human | `saveModelCredential` |
| 17 | `deleteModelCredential` | 削除 | `DELETE /accounts/{accountId}/model-credential` | — | — | human | `deleteModelCredential` |
| 18 | `postIssueCandidates` | 操作 | `POST /accounts/{accountId}/issue-candidates` | `{repository_id, exclude}` | `repository_id` | write | `issueCandidates` |
| 19 | `startSprintRun` | 作成 | `POST /accounts/{accountId}/sprint-runs` | `{repository_id, issues, idempotency_key}` | `idempotency_key` | write | `startSprintRun` |
| 20 | `listSprintRuns` | 一覧 | `GET /accounts/{accountId}/sprint-runs` | `?repository_id=`（省くとアカウントの新しい順の 50 件） | — | read | `sprintRuns` |
| 21 | `getSprintRunDetail` | 指定の取得 | `GET /accounts/{accountId}/sprint-runs/detail` | `?sprint_run_id=` | `sprint_run_id` | read | `sprintRunDetails` |
| 22 | `getStepDetail` | 指定の取得 | `GET /accounts/{accountId}/sprint-runs/steps` | `?sprint_run_id=`（1 つ）と `?step_key=` | `step_key` | read | `stepDetails` |
| 23 | `getBacklog` | 指定の取得 | `GET /accounts/{accountId}/backlogs` | `?repository_id=` | `repository_id` | read | `backlogs` |
| 24 | `cancelSprintRun` | 操作 | `POST /accounts/{accountId}/sprint-runs/cancel` | `{sprint_run_id}` | `sprint_run_id` | write | `cancelSprintRun` |

- 一覧の 4 つ（8・9・11・15）の応答は、今の本文を要素 1 個の配列で包む（`[{url}]`・`[{registered, kind, …}]`）。`listRepositories` は今の `repositories` の配列をそのまま返す
- `getStepDetail` は 1 つの実行の中の工程の鍵を要素にする。実行の ID は条件で、他人の実行なら要求全体が 403 になる
- `postIssueCandidates` は言語モデルを呼び、費用が掛かるため、読むだけでも扱いを `write` にする
- `registerRepository` は、同じリポジトリの 2 回目の登録が今と同じ結果（登録済みの値）になるため、冪等の鍵を持たない
- `startSprintRun` の `x-ndf-id` は `sprint_run_id` である

### 開発ループ（`loops.yaml`）

| # | operationId | 種類 | メソッドと経路 | 要素か条件 | 鍵 | 扱い | 画面 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | `startLoop` | 作成 | `POST /accounts/{accountId}/loops` | `{repository_id, name, milestones, budget?, idempotency_key}` | `idempotency_key` | write | `startLoop` |
| 26 | `listLoops` | 一覧 | `GET /accounts/{accountId}/loops` | `?repository_id=` | — | read | `loops` |
| 27 | `getLoop` | 指定の取得 | `GET /accounts/{accountId}/loops/detail` | `?loop_id=` | `loop_id` | read | `loopDetails` |
| 28 | `updateLoopBudget` | 更新 | `PATCH /accounts/{accountId}/loops` | `{loop_id, budget}` | `loop_id` | write | `updateLoopBudget` |
| 29 | `cancelLoop` | 操作 | `POST /accounts/{accountId}/loops/cancel` | `{loop_id}` | `loop_id` | write | `cancelLoop` |
| 30 | `startNextLap` | 操作 | `POST /accounts/{accountId}/loops/next-lap` | `{loop_id}` | `loop_id` | write | `requestNextLap` |
| 31 | `answerLoopPending` | 操作 | `POST /accounts/{accountId}/loops/answers` | `{loop_id, issue, decision, pr?}` | `loop_id` + `issue` | human | `answerLoop` |

`getLoop` の無いループは、今の 404 のまま要素の結果（`loop_not_found`）になり、画面はその要素を捨てる（今の `loopDetails` の振る舞い）。`answerLoopPending` の成功の値は `{loop_id, pending}` である。`startLoop` の `x-ndf-id` は `loop_id` である。

### オーガナイザー（`organizer.yaml`）

| # | operationId | 種類 | メソッドと経路 | 要素か条件 | 鍵 | 扱い | 画面 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 32 | `listRepositoryApprovalRequests` | 指定の取得 | `GET /accounts/{accountId}/approval-requests` | `?repository_id=` | `repository_id` | read | — |
| 33 | `listRepositoryReleases` | 一覧 | `GET /accounts/{accountId}/releases` | `?repository_id=`（1 つ）と `?limit=` | — | read | — |
| 34 | `resolveStalledRelease` | 操作 | `POST /accounts/{accountId}/releases/resolve` | `{repository_id, seq, choice, body?}` | `repository_id` + `seq` | human | — |

`listRepositoryApprovalRequests` の成功の値は `{repository_id, organizer, items}` である。止まった配布を解く操作は、オーガナイザーの確定仕様（I66）で画面の経路だけで受けると決まっているため、扱いを `human` にする。

### 委任（`delegations.yaml`、新設）

| # | operationId | 種類 | メソッドと経路 | 要素か条件 | 鍵 | 扱い | 画面 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 35 | `listDelegations` | 一覧 | `GET /delegations` | — | — | read | `delegations` |
| 36 | `createDelegation` | 作成 | `POST /delegations` | `{client_name, scope, expires_in_seconds, idempotency_key}` | `idempotency_key` | human | `createDelegation` |
| 37 | `revokeDelegation` | 削除 | `DELETE /delegations` | `?credential_id=` | `credential_id` | human | `revokeDelegation` |

- `listDelegations` の値は `{credential_id, client_name, scope, created_at, expires_at, revoked_at, state}` の配列である。自分が発行したものだけを、新しい順で 100 件まで返す。`state` は `active` / `expired` / `revoked` である
- `createDelegation` の成功の値は `{delegation, credential}` で、`credential` は値（`ndfd_<資格情報の ID>_<秘密>`）である。同じ冪等の鍵の 2 回目は `credential` を `null` にする。`x-ndf-id` は `delegation.credential_id` である
- `revokeDelegation` は、期限切れと取り消し済みの資格情報でも何も変えずに成功を返す。他人の資格情報は `forbidden` の結果になる

## 操作の記録へ写す鍵

記録の `target_ids` に写してよい項目の名前は、次の一覧だけである。一覧に無い項目は、要求と応答のどこにあっても記録に写さない（I9）。

| 鍵の名前 | 何の識別子か |
| --- | --- |
| `account_id` | アカウント（経路の `{accountId}` を含む） |
| `user_id` | 席の利用者 |
| `repository_id` | リポジトリ |
| `sprint_run_id` | スプリントの実行 |
| `step_key` | 工程 |
| `loop_id` | 開発ループ |
| `issue` | 課題の番号 |
| `seq` | まとめた配布の連番 |
| `credential_id` | 委任の資格情報 |
| `idempotency_key` | 作成の冪等の鍵 |

写す値は、要素ごとの `x-ndf-keys` の項目の値と、作成の成功の値の `x-ndf-id` の項目の値である。対応の検査は、すべての操作の `x-ndf-keys` と `x-ndf-id` がこの一覧に入っていることを確かめる。この一覧に、チームの名前・クライアントの名前・課題の題と本文・モデルの認証情報・GitHub の `code`・`state`・`proof` は入らない。

## MCP のツール

| 項目 | 規則 |
| --- | --- |
| 名前 | `operationId` のまま |
| 説明 | 契約の `summary` と `description`。扱い `human` の操作は、頭に「人だけが確定できる。MCP からは常に断られる。画面で行う」を足す |
| 入力の定義 | オブジェクト。経路に `{accountId}` があれば `account_id`（必須）。一覧と指定の取得と削除は query の項目（鍵は配列）。作成・更新・操作は `items`（要素の定義の配列。1〜100 個） |
| 呼び出し | 入力から経路・query・本文を組み、`Authorization` をそのまま付けて同じアプリへ内部の要求として送る |
| 結果 | `structuredContent` に `{status, body}`（応答の状態と本文）。`content` に同じ値の JSON の文字列を 1 つ。`isError` は状態が 400 以上のとき真 |
| 対象 | 扱いによらず 37 の操作すべて。扱い `human` のツールは常に 403（`human_only`）を返す。登録しておくのは、エージェントが断られた理由と、人が画面で行うことを読めるようにするためである |

ツールの定義は起動のたびに操作の表から作る。手で書いたツールの定義を持たない。

## 画面の API クライアントの対応

`Api` のメソッドの名前と引数と戻り値は変えない。画面のコードを変えずに、契約の形を切り替えるためである。

| メソッドの形 | 要求の組み方 | 応答のほどき方 |
| --- | --- | --- |
| 1 件を作る・変える・消す・操作する（`startSprintRun`・`cancelLoop` など） | 要素 1 個の配列で送る。冪等の鍵が要る作成は `crypto.randomUUID()` で付ける | 要素の結果が成功なら値を返し、失敗なら要素の状態と本文で `ApiFailure` を投げる |
| 配列で受ける取得（`sprintRunDetails`・`stepDetails`・`backlogs`・`loopDetails`） | 鍵の query を繰り返して 1 回で送る | `sprintRunDetails`・`stepDetails`・`backlogs` は最初の失敗の要素で `ApiFailure` を投げる。`loopDetails` は `loop_not_found` の要素を捨てる（今の振る舞いのまま） |
| 一覧（`sprintRuns`・`loops`・`repositories` など） | 条件の query で送る | 今の戻り値の形へ包み直す（`{sprint_runs: [...]}` など） |

要求は操作の表の `operationId` から組み、経路の文字列をソースに直に書かない。ソースの検査（`check-source.ts`）に、経路の文字列を直に組んだ要求を見つける規則を足す（受け入れ条件 1）。
