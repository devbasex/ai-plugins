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
| `contracts/` | 進捗の事象・実行ジョブの指示・API の形の機械可読な定義。アカウントの API の形は `contracts/openapi/accounts.yaml` | #2170・#2171 |
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
| `npm run check -w web` / `npm run check -w api` | 1 つの workspace の型検査とテスト |
| `npm run lint:contracts` | `contracts/openapi/` の OpenAPI を Spectral（`spectral:oas`）で検査する |
| `npm run build -w web` | 画面の資産を `web/dist/` に作る |

## API の入口

インフラが Lambda へつなぐ handler は次の 2 つで、束ね方（esbuild など）はインフラが決める。

- `api/src/handlers/api.ts` の `handler`: 画面向けの API（API Gateway の HTTP API、payload 2.0）
- `api/src/handlers/github-oidc.ts` の `handler`: GitHub の OAuth を Cognito の OIDC の IdP として受ける仲立ち。`/oidc/github/{proxy+}` を認可なしで通す

ほかの子課題は `api/src/index.ts` から export したものだけを使う。要求の利用者は `requireCaller`、アカウントの文脈は `resolveFromRequest`（関数としては `resolveAccount`）、使える量は `getLimits`・`judgeLimits`、プランの変更は `setPlan` で得る。

全体の設計（構成要素・実行基盤・進捗の正本・構成要素の間の契約）は [docs/specifications/ndf-factory-service-boundary.md](../docs/specifications/ndf-factory-service-boundary.md) にある。
