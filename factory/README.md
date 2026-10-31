# Neural Development Factory

NDF を Web UI から操作できる Software Factory のサービスとして提供するときのコードを置くディレクトリ。GitHub のリポジトリを取り込み、NDF の開発ワークフローのスプリントを並列に動かし、一覧と詳細で進捗を見せ、承認ゲートで知らせる。

## プラグインとの関係

- プラグインとしての NDF（`plugins/` の下）とは別のディレクトリに置く。サービスは NDF をマーケットプレイスから導入して使い、`plugins/` のファイルをパスで読み込まない
- `plugins/` の下と `.claude-plugin/marketplace.json` は、このディレクトリを参照しない。どのプラグインの配布物にも、このディレクトリは入らない
- 境界は `python3 scripts/check-service-boundary.py` が見る（CI のジョブ `service-boundary-check`）

## ライセンス

このディレクトリのライセンスは [LICENSE](./LICENSE) が持つ。今は根と同じ MIT License である。ライセンスを差し替えるときは、この `LICENSE` と根の `README.md` の「ライセンス」の節だけを書き換える。プラグインの配布物と検査は影響を受けない。

## インフラと秘密

CDK のスタック・ネットワーク・秘密の管理は別の private リポジトリが持つ。このリポジトリにはインフラの定義・秘密・`.env` の実ファイルを置かない（境界の検査が落とす）。インフラから受け取る変数の名前は [.env.example](./.env.example) にあり、値は書かない。秘密は Secrets Manager の ARN だけを受ける。

## 置くもの

| ディレクトリ | 中身 | 作る Issue |
| --- | --- | --- |
| `contracts/` | 進捗の事象・実行ジョブの指示・受け口の応答の JSON Schema（`contracts/schemas/`）と、そこから生成した型（workspace `@ndf-factory/contracts`）。API の経路の形は `contracts/openapi/`（アカウントは `accounts.yaml`、進捗の受け口は `ingest.yaml`、スプリントの実行とランナー向けの経路は `runs.yaml`） | #2170・#2171 |
| `web/` | ログイン・リポジトリの登録・スプリントの一覧と詳細・承認・プランと請求の画面 | #2170・#2172〜#2174・#2186 |
| `api/` | 画面からの要求を受け、アカウントで認可して各集約の持ち主へ渡す処理 | #2170〜#2174・#2186 |
| `runner/` | 実行ジョブのコンテナ。リポジトリの取り込み・NDF の導入・conductor の起動・進捗の事象の送信 | #2186 |

## パッケージの管理

TypeScript のコードは npm の workspaces で束ね、どの子課題も次の規則に従う。

- 道具は npm（Node に同梱）。pnpm と yarn は使わない。理由は、手元・CI・ランナーのコンテナのどこでも追加の導入なしに使え、依存の版を 1 つのロックファイルで揃えられるため（確定仕様は [docs/specifications/ndf-factory-accounts-and-plans.md](../docs/specifications/ndf-factory-accounts-and-plans.md) の「背景」）
- Node の版は [.node-version](./.node-version) の 1 行で決め、`package.json` の `engines` と CI の `setup-node` が同じ値を使う
- ロックファイルは `factory/package-lock.json` の 1 つだけ。workspace ごとに置かない。依存は `factory/` で `npm install -w <workspace> <パッケージ>` で足す
- 新しいディレクトリ（`runner/` など）を足すときは、根の `package.json` の `workspaces` に足し、`typecheck`（`tsc --noEmit`）・`test`（`vitest run`）・`check`（2 つを順に）の 3 つの script を置き、根の `check` に足す
- 検査は `factory/` で次のコマンドを打つ。CI のジョブ `factory-check`（`.github/workflows/factory-check.yml`）は `npm ci` の後に `npm run check` を打つ

| コマンド | 中身 |
| --- | --- |
| `npm ci` | ロックファイルどおりに依存を入れる |
| `npm run check` | 契約の検査と、すべての workspace の型検査とテスト |
| `npm run check -w contracts` / `npm run check -w web` / `npm run check -w api` / `npm run check -w runner` | 1 つの workspace の型検査とテスト。`contracts` は生成した型が定義と食い違えば落ちる |
| `npm run generate -w contracts` | `contracts/schemas/` の定義から `contracts/src/generated/` の型を作り直す。定義を変えたら打ってコミットする |
| `npm run lint:contracts` | `contracts/openapi/` の OpenAPI を Spectral（`spectral:oas`）で検査する。`contracts/schemas/` への参照切れも落とす |
| `npm run build -w web` | 画面の資産を `web/dist/` に作る |

## API の入口

インフラが Lambda へつなぐ handler は次の 5 つで、束ね方（esbuild など）はインフラが決める。

- `api/src/handlers/api.ts` の `handler`: 画面向けの API（API Gateway の HTTP API、payload 2.0）
- `api/src/handlers/github-oidc.ts` の `handler`: GitHub の OAuth を Cognito の OIDC の IdP として受ける仲立ち。`/oidc/github/{proxy+}` を認可なしで通す
- `api/src/handlers/ingest.ts` の `handler`: 進捗の受け口（API Gateway の HTTP API、payload 2.0）。経路ごとに認証の手段が 1 つで、事象の `source` が経路と食い違えば 403 を返す

| 経路 | 出所 | 認証の手段 |
| --- | --- | --- |
| `POST /ingest/events` | `runner` | `Authorization: Bearer <実行ジョブの token>`。Lambda が公開鍵の集合で確かめる。API Gateway の認可は付けない |
| `POST /ingest/api/events` | `api` | API Gateway の IAM 認可（SigV4）。Lambda が呼び出し元のロールを `INGEST_API_CALLER_ROLE_ARN` と照らす |
| `POST /ingest/github` | `github` | `X-Hub-Signature-256`。Lambda が GitHub App の Webhook の秘密で確かめる。API Gateway の認可は付けない |

- `api/src/handlers/run-jobs.ts` の `handler`: ランナー向けの API（`/run-jobs/*`、`INGEST_URL` と同じ origin）。実行ジョブの token で認証し、導入の token の渡し直しと状態の置き場の署名つき URL を返す。API Gateway の認可は付けない
- `api/src/handlers/run-control.ts` の `handler`: HTTP を持たない。EventBridge の「ECS Task State Change」（ランナーのクラスタ）と 5 分ごとの起動（見回り）を受ける

実行の表（`RUNS_TABLE_NAME`）は区分の鍵 `pk`・並びの鍵 `sk` と、GSI `gsi1`（区分の鍵 `gsi1pk`・並びの鍵 `gsi1sk`、射影は全部）を持つ。ランナーのイメージは `runner/Dockerfile` で、`factory/` を文脈にして作る。タスク定義は形態ごとに 2 つで、`managed`（`RUNNER_TASK_DEFINITION_ARN`）の役割だけが Bedrock を呼べ、`byok`（`RUNNER_BYOK_TASK_DEFINITION_ARN`）の役割はキーの置き場の読み出しと `AssumeRole` だけを持つ。どちらの役割も S3 の権限を持たない。

進捗の事象の表（`PROGRESS_EVENTS_TABLE_NAME`）は区分の鍵 `sprint_run_id`・並びの鍵 `order_key` と、区分の鍵 `pr_ref` の GSI `pr_ref-index`（射影に `sprint_run_id` を含める）を持つ。冪等の鍵の表（`PROGRESS_IDEMPOTENCY_TABLE_NAME`）は区分の鍵 `idempotency_key` だけを持ち、TTL を置かない。

ほかの子課題は `api/src/index.ts` から export したものだけを使う。要求の利用者は `requireCaller`、アカウントの文脈は `resolveFromRequest`（関数としては `resolveAccount`）、使える量は `getLimits`・`judgeLimits`、プランの変更は `setPlan` で得る。実行ジョブの token は `signRunJobToken` で作り、受け口で `verifyRunJobToken` で確かめる。テストでは `createRunJobTokenKit` で鍵の組をメモリの中に作る。実行の制御は `job.finished` を `appendControlEvent` で直に書く。契約の型は `@ndf-factory/contracts` か `api/src/index.ts` から import する。

全体の設計（構成要素・実行基盤・進捗の正本・構成要素の間の契約）は [docs/specifications/ndf-factory-service-boundary.md](../docs/specifications/ndf-factory-service-boundary.md) にある。
