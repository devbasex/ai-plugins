# #2171 の設計: 定義と経路と関数の形

[設計文書](issue-2171-design.md) から分けた「入出力の契約」の節である。機械可読な定義そのものは実装で `factory/contracts/` に置き、ここではその中身の形と、変わる約束・互換性・検査の手段を決める。

## 入出力の契約

### 置き場所と互換性

| 約束 | 置き場所 | 変わり方 | 互換性 |
| --- | --- | --- | --- |
| 進捗の事象・実行ジョブの指示・受け口の応答の定義 | `factory/contracts/schemas/*.schema.json`（JSON Schema 2020-12） | 新設 | 新設のため壊す呼び出し側は無い |
| 生成した型 | `factory/contracts/src/generated/*.ts`（`@ndf-factory/contracts` から import） | 新設 | 同上 |
| 受け口の経路 | `factory/contracts/openapi/ingest.yaml`（OpenAPI 3.1） | 新設 | 同上 |
| アカウントの経路 | `factory/contracts/openapi/accounts.yaml` | 変えない | 受け入れ条件 21 |
| 公開の関数 | `factory/api/src/index.ts` | export を足す | 既存の export は名前も型も変えない |
| 確定仕様の事象の形 | `docs/specifications/ndf-factory-service-boundary.md` | `step.recorded` に任意の鍵を 1 つ、正本の表に任意の列を 1 つ足す（決定 7） | 足すのは任意の鍵と列だけで、既存の 10 種類の意味は変わらない。送り手・読み手の実装はまだ無い |

検査の手段は `npm run lint:contracts`（Spectral。参照切れと OpenAPI の規則）と `npm run check -w contracts`（型のずれと定義の正しさ）で、どちらも根の `npm run check` に入る。

### 進捗の事象（`progress-event.schema.json`）

最上位は 10 種類の `oneOf` で、各種類は共通の列を `allOf` で取り込み、`unevaluatedProperties: false` で余分な列を落とす。`payload` は種類ごとに `additionalProperties: false`。生成した型は `ProgressEvent`（10 種類の共用）と、種類ごとの `JobStartedEvent` などになる。

**共通の列**（確定仕様の「進捗の正本」の 9 列から、受け口が作る `order_key` を除いた 8 列）:

| 鍵 | 型 | 必須 | 規則 |
| --- | --- | --- | --- |
| `sprint_run_id` | 文字列 | 必須 | `^[A-Za-z0-9_-]{1,128}$`（`#`・`:` を含まない。決定 6） |
| `idempotency_key` | 文字列 | 必須 | 1〜300 字。出所ごとの形は照合（下の「出所ごとの照合」）が見る |
| `type` | 文字列 | 必須 | 10 種類のどれか（種類ごとの `const`） |
| `at` | 文字列 | 必須 | `format: date-time` かつ末尾 `Z`（UTC） |
| `source` | 文字列 | 必須 | `runner` / `github` / `api` / `control` |
| `job_id` | 文字列か `null` | 必須（値は `null` を許す） | `^[A-Za-z0-9_-]{1,128}$`。`github`・`api` は `null` |
| `schema_version` | 数 | 必須 | `const: 1` |
| `payload` | オブジェクト | 必須 | 種類ごと |

**種類ごとの `payload`**（確定仕様の「進捗の事象の種類と受け口」の `payload` の列と 1 対 1。前提 9 で鍵の名前と型を決める）:

| 種類 | 鍵（型、任意のものは「任意」） |
| --- | --- |
| `job.started` | `ndf_version`（文字列）、`start`（`new` / `resume`） |
| `phase.changed` | `phase`（文字列。#2172 が集合を決めるまで列挙にしない）、`issues`（1 以上の整数の並び） |
| `step.recorded` | `step`（文字列）、`exit_code`（整数）、`seconds`（0 以上の数）、`summary`（文字列）、`pull_request`（任意。`repo`（`owner/name`）・`number`（1 以上の整数）。決定 7） |
| `attention` | `reason`（文字列）、`summary`（文字列） |
| `gate.reached` | `kind`（`gate1` / `gate2` / `decision`）、`pr_url`（URI か `null`）、`material`（文字列。承認資料の本文） |
| `gate.answered` | `approval_request_id`（文字列）、`kind`（`gate1` / `gate2` / `decision`）、`decision`（`approved` / `rejected`。`kind` が `decision` のときは鍵を持たず、ほかでは必須）、`answered_by_account_id`（文字列）。本文は持たない |
| `usage.recorded` | `model`（文字列）、`input_tokens`・`output_tokens`（0 以上の整数）、`cost_usd`（0 以上の数）、`step`（文字列） |
| `job.finished` | `result`（`gate` / `stopped` / `done` / `failed`）、`summary`（文字列） |
| `pr.updated` | `repo`（`owner/name`）、`number`（1 以上の整数）、`state`（`open` / `closed` / `merged`） |
| `ci.updated` | `repo`（`owner/name`）、`pr_number`（1 以上の整数）、`name`（文字列）、`conclusion`（文字列か `null`。`null` は「まだ終わっていない」） |

**大きさの上限は 256 KiB（262,144 バイト）で、定義の外で見る**（決定 8）。`runner`・`api` の経路は要求の本文の UTF-8 のバイト数、`github` の経路と `appendControlEvent` は事象を JSON にしたバイト数で見る。JSON Schema の `maxLength` は字の数で、バイトを数えられないためである。

### 実行ジョブの指示（`run-job-instruction.schema.json`）

確定仕様の「実行ジョブの指示」の 11 の入力を、環境変数の名前のまま `properties` に置く。値はどれも文字列（環境変数と、#2189 の受け取りの応答で同じ形にするため）。生成した型は `RunJobInstruction`。

| 入力 | 規則 |
| --- | --- |
| `SPRINT_RUN_ID`・`JOB_ID` | 必須。事象の共通の列と同じ形 |
| `REPO` | 必須。`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$` |
| `NDF_VERSION` | 必須。1 字以上 |
| `START` | 必須。`new` / `resume` |
| `ISSUES` | `START` が `new` のとき必須。`^[1-9][0-9]*(,[1-9][0-9]*)*$`（カンマで区切った番号） |
| `APPROVAL_REQUEST_ID` | `START` が `resume` のとき必須 |
| `INGEST_URL` | 必須。`format: uri`。`runner` の経路（`POST /ingest/events`）の URL そのもの |
| `INGEST_TOKEN_SECRET_ARN` | 必須。`^arn:aws[a-z-]*:secretsmanager:` |
| `EXECUTION_MODE` | 必須。`local` / `byok` / `managed` |
| `MODEL_CREDENTIAL_SECRET_ARN` | `EXECUTION_MODE` が `byok` のとき必須、`local`・`managed` のとき持たない |

必須の違いは `allOf` の中の `if`/`then` で書く。生成した型は条件を表せず任意の鍵になるため、条件は定義の検査（`validateRunJobInstruction`）で縛る。`additionalProperties: false`。

### 受け口の応答（`ingest-response.schema.json`）

| 応答 | 本文 | いつ |
| --- | --- | --- |
| 202 | `{"accepted": true}` | 残した・同じ冪等の鍵が既にある・`github` で扱わないか宛先が引けない。どれも同じ本文で、重複かどうかを明かさない |
| 400 | `{"error": {"code": "invalid_event", "message": "<位置と規則>"}}` | 大きさの上限を超えた・JSON でない・定義に合わない。`message` は最初の失敗の `instancePath` と規則の名前だけで、値を含めない |
| 401 | `{"error": {"code": "unauthorized", "message": "認証できません"}}` | 理由によらずこの本文（I30） |
| 403 | `{"error": {"code": "forbidden", "message": "この事象を書く権限がありません"}}` | 理由によらずこの本文（I30） |
| 503 | `{"error": {"code": "ingest_unavailable", "message": "時間をおいて送り直してください"}}` | 保存・答えの読み出し・秘密の読み出しの失敗。送り手は同じ事象のまま送り直す |

エラーの本文の形は #2170 の `ApiError` の `{"error": {"code", "message"}}` に揃え、`factory/api/src/errors.ts` の `ApiError` を継いだ 4 つのクラスで返す。

### 受け口の経路（`ingest.yaml`）

| 経路 | 出所 | 認証の手段 | 要求の本文 | 応答 |
| --- | --- | --- | --- | --- |
| `POST /ingest/events` | `runner` | `Authorization: Bearer <実行ジョブの token>`（Lambda で検証） | 進捗の事象 1 件 | 202 / 400 / 401 / 403 / 503 |
| `POST /ingest/api/events` | `api` | API Gateway の IAM 認可（SigV4）と、Lambda での呼び出し元のロールの照合 | 進捗の事象 1 件（`gate.answered`） | 202 / 400 / 401 / 403 / 503 |
| `POST /ingest/github` | `github` | `X-Hub-Signature-256`（Lambda で検証）。`X-GitHub-Event`・`X-GitHub-Delivery` を要る | GitHub の Webhook の本文（形は GitHub のもの。定義は `type: object` だけ） | 202 / 401 / 503 |

`github` の経路は 400 と 403 を返さない。GitHub は 2xx 以外を配達の失敗として記録し続けるため、本文の形の違い・扱わない種類は 202 で保存しない（前提 7）。`X-GitHub-Event`・`X-GitHub-Delivery` が無いものは 202 で保存しない（署名が合った後に限る）。OpenAPI の `securitySchemes` は、`runner` を `http`（`bearer`）、`api` を `apiKey` の形を借りずに説明文で IAM 認可と書き、`github` は経路の説明に署名の見出しを書く。

### 出所ごとの照合（I25〜I28）

照合は契約の定義の検査を通った事象に当てる。上から順に見て、最初に当たった失敗で 403 を返す。

| 出所 | 照らすこと |
| --- | --- |
| すべて | 事象の `source` が経路の出所と一致する（I25） |
| `runner` | 種類が `gate.answered`・`pr.updated`・`ci.updated` でない（I26）。`job_id` と `sprint_run_id` が token の `sub` と `sprint_run_id` と一致する。冪等の鍵が `job.finished` なら `<job_id>:finished`、ほかなら `<job_id>:<seq>`（`seq` は `^[1-9][0-9]*$`）（I27） |
| `api` | 呼び出し元のロールが `INGEST_API_CALLER_ROLE_ARN` のロールである（決定 12。違えば 403、IAM の文脈が無ければ 401）。種類が `gate.answered`（I26）。`job_id` が `null`。冪等の鍵が `api:<payload.approval_request_id>`。保存済みの答えを読み、`null` なら 403、事象の `sprint_run_id`・`payload` の `kind`・`answered_by_account_id`・`decision`（無いことも含めて）が答えと一致する（I28） |
| `github` | 翻訳した事象だけを残すため、照合で落ちる形を作らない（翻訳の表） |
| `control` | 種類が `job.finished`。冪等の鍵が `<job_id>:finished` |

### GitHub の翻訳

| `X-GitHub-Event` | 事象 | `payload` の出所 | `at` | 宛先の `pr_ref` |
| --- | --- | --- | --- | --- |
| `pull_request` | `pr.updated` | `repo` = `repository.full_name`、`number` = `pull_request.number`、`state` = `pull_request.merged` が真なら `merged`、ほかは `pull_request.state` | `pull_request.updated_at` | `<full_name の小文字>#<number>` |
| `check_run` | `ci.updated` | `repo` = `repository.full_name`、`pr_number` = 宛先を引けた `check_run.pull_requests[].number`、`name` = `check_run.name`、`conclusion` = `check_run.conclusion` | `check_run.completed_at`、無ければ `check_run.started_at` | `check_run.pull_requests[]` の各番号 |
| ほか（`ping` など） | 残さない | — | — | — |

冪等の鍵は `github:<X-GitHub-Delivery>`、`job_id` は `null`、`source` は `github`、`schema_version` は `1`。宛先は GSI `pr_ref-index` を引き、得たスプリントの実行がちょうど 1 つのときだけ残す。0 か 2 つ以上、`at` に使う時刻が無い、翻訳した事象が定義に合わないときは 202 で保存しない。署名は `sha256=` の後の 16 進を、Webhook の秘密による本文の HMAC-SHA256 と `timingSafeEqual` で比べる（長さが違えば比べずに 401）。

### 実行ジョブの token

**形は JWS の compact（JWT）で、署名は EdDSA（Ed25519）である**（決定 5）。

| 部分 | 鍵 | 値 |
| --- | --- | --- |
| 見出し | `alg` | `EdDSA` だけ |
| 見出し | `typ` | `ndf-run-job+jwt` |
| 見出し | `kid` | 発行の鍵の識別子（公開鍵の集合の `kid` と照らす） |
| 本体 | `iss` | `ndf-factory/run-control` |
| 本体 | `aud` | `ndf-factory/run-job` |
| 本体 | `sub` | 実行ジョブの識別子（`job_id`） |
| 本体 | `sprint_run_id` | スプリントの実行の識別子 |
| 本体 | `iat`・`exp` | 発行と期限（秒）。期限の長さは発行側（#2186）が決める |

`account_id` は載せない（I31）。

**鍵の置き場**:

| 秘密 | 中身 | 読む側 | 変数 |
| --- | --- | --- | --- |
| 公開鍵の集合 | `{"keys": [<Ed25519 の公開鍵の JWK。kid・alg: EdDSA つき>, …]}` | 受け口・#2173 | `RUN_JOB_TOKEN_VERIFY_KEYS_SECRET_ARN`（この課題が足す） |
| 発行の鍵 | `<Ed25519 の秘密鍵の JWK。kid つき>` | 実行の制御（#2186）だけ | #2186 が名前を決めて足す |

**関数**（`factory/api/src/index.ts` から export）:

| 名前 | 入力 | 出力 | 失敗の形 |
| --- | --- | --- | --- |
| `verifyRunJobToken(token)` | token の文字列（`Bearer ` を除いたもの） | `Promise<{ jobId; sprintRunId; expiresAt }>`。公開鍵の集合は `RUN_JOB_TOKEN_VERIFY_KEYS_SECRET_ARN` から読み、5 分ごとに読み直す | `RunJobTokenError`（`code` が `malformed` / `bad_signature` / `expired` / `unknown_key`）。呼ぶ側が 401 にする。秘密を読めない失敗は `RunJobTokenError` でない例外（呼ぶ側が 503 にする） |
| `createRunJobTokenVerifier(keys)` | 公開鍵の JWK の並び | `(token) => Promise<{ jobId; sprintRunId; expiresAt }>` | 同上 |
| `signRunJobToken(claims, key)` | `claims` = `{ jobId; sprintRunId; expiresAt: Date }`（ほかの鍵を型で受けない）、`key` = 発行の秘密鍵と `kid` | `Promise<string>` | 鍵が Ed25519 でなければ例外 |
| `createRunJobTokenKit()` | 無し | テストの鍵の組で作った `{ sign(claims, opts?), verify, otherKeySign, publicJwks }`。鍵はメモリの中だけ | — |

`jose` の失敗の `code` を `RunJobTokenError` の 4 つへ読み替える（`ERR_JWS_INVALID`・`ERR_JWT_INVALID`・`ERR_JWT_CLAIM_VALIDATION_FAILED`・`ERR_JOSE_ALG_NOT_ALLOWED` は `malformed`、`ERR_JWS_SIGNATURE_VERIFICATION_FAILED` は `bad_signature`、`ERR_JWT_EXPIRED` は `expired`、`ERR_JWKS_NO_MATCHING_KEY` は `unknown_key`）。読み替えの表は実装で全部の `code` を走らせて確かめる。

### 直の追記と保存済みの答えの口

| 名前 | 入力 | 出力 | 失敗の形 |
| --- | --- | --- | --- |
| `appendControlEvent(event)` | `JobFinishedEvent`（`source` は `control`） | `Promise<{ written: boolean }>`。`false` は同じ鍵が既にある | 定義に合わない・`control` の照合に合わない → `ProgressEventError`（`code` が `invalid_event` / `forbidden`）。保存の失敗はそのまま投げる |
| `SavedAnswerReader`（型） | `approvalRequestId` | `Promise<SavedAnswer \| null>`。`SavedAnswer` は `{ approvalRequestId; sprintRunId; kind; decision?; answeredByAccountId }`。`null` は未保存 | 読めないときは投げる（受け口が 503 にする） |
| `createIngestApp(deps)` | `{ verifier; store; savedAnswers; webhookSecret; apiCallerRoleArn; log? }` | Hono の app | — |

**#2173 の差し替え**: `factory/api/src/handlers/ingest.ts` は `savedAnswers` に `factory/api/src/progress/saved-answers.ts` の `savedAnswersFromEnv()` を渡す。この課題では常に投げる既定（`api` の経路は 503）を返し、#2173 が同じファイルの中身を本物の読み出しに替える。受け口の他のファイルは変えずに済む。

### 環境変数（`factory/.env.example` に足す）

| 名前 | 中身 |
| --- | --- |
| `PROGRESS_EVENTS_TABLE_NAME` | 事象の表の名前（GSI `pr_ref-index` を持つ） |
| `PROGRESS_IDEMPOTENCY_TABLE_NAME` | 冪等の鍵の表の名前 |
| `RUN_JOB_TOKEN_VERIFY_KEYS_SECRET_ARN` | token の公開鍵の集合の置き場 |
| `GITHUB_APP_WEBHOOK_SECRET_ARN` | GitHub App の Webhook の秘密の置き場 |
| `INGEST_API_CALLER_ROLE_ARN` | `api` の出所として認める #2173 のサービスのロールの ARN |

値は書かない（I6）。ログは 1 要求 1 行の JSON で `route`・`type`（読めたときだけ）・`status`・`sprint_run_id`（読めたときだけ）の 4 列に限る。

### 確定仕様を直す箇所（決定 7）

同じ Pull Request で `docs/specifications/ndf-factory-service-boundary.md` を次のように直す。

| 節 | 直し方 |
| --- | --- |
| 「進捗の正本」の列の表 | `pr_ref` の行を足す（文字列・許す・`step.recorded` が Pull Request を指すときだけ `<owner/name の小文字>#<番号>`。GitHub の Webhook の宛先を引く鍵） |
| 「進捗の事象の種類と受け口」の `step.recorded` の行 | `payload` に「このステップが作った・更新した Pull Request（リポジトリと番号。任意）」を足す |
| 「進捗の事象の種類と受け口」の `github` の行の後 | GitHub の Webhook の宛先のスプリントの実行は、`pr_ref` が同じ事象の `sprint_run_id` で引き、ちょうど 1 つに決まらなければ 202 で保存しない、の 1 文を足す |
