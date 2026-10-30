# #2170 の設計の決定の記録

[設計文書](issue-2170-design.md) から分けた決定の記録である。

## 決定の記録

### 決定 1: 後続の子課題が同じ形でコードを足せるよう、パッケージの管理を npm の workspaces にし、ロックファイルを `factory/` に 1 つ置く

npm は Node に同梱され、手元（npm 11.19.1）・CI の `setup-node`・ランナーのコンテナのどこでも追加の導入なしに使える。手元に pnpm は入っておらず、使うには CI とランナーとこの環境のそれぞれで導入の手順が増える。workspaces で `web`・`api`（後に `contracts`・`runner`）を束ねれば、#2171 が生成する型を各 workspace が名前で参照でき、依存の版は 1 つのロックファイルで揃う。一時ディレクトリで `web`・`api` の 2 つの workspace に TypeScript と Vitest を入れ、`npm run check -w api` が通り、型の誤りで 1 になることを確かめた。

pnpm は導入の速さと厳しい依存の解決が利点だが、`factory/` の依存の数ではその差より導入の手順が増える費用が大きい。workspace ごとに別のロックファイルを置く形は、同じ依存の版が workspace の間でずれ、`contracts` の型を共有するたびに参照の形を各所で書く。

根拠: Value 3 / Value 4（MVV 版 3）

### 決定 2: 手元と CI で同じ版と同じコマンドで検査できるよう、Node 24・TypeScript の `tsc --noEmit`・Vitest にし、各 workspace の `check` を 1 つのコマンドにする

各 workspace の `package.json` に `typecheck`（`tsc --noEmit`）・`test`（`vitest run`）・`check`（2 つを順に）を置き、`factory/` で `npm run check -w web` と `npm run check -w api` が受け入れ条件 16 の「1 つのコマンド」になる。根の `npm run check` は契約の検査と両方の `check` を走らせ、CI はこれを打つ。Vitest は Vite と設定を共有でき、画面と API で同じテストの道具になる。Node の版は `factory/.node-version` の 1 行に置き、`engines` と CI の `setup-node`（`node-version-file`）が同じファイルを読む。

Node 22 は Lambda で長く使えるが、手元で確かめたのは 24 で、24 の方がサポートの期間が長い。Lambda に 24 が無いと分かれば 1 行で 22 へ下げる（未確認のまま残ること）。Jest は ESM と TypeScript の設定を足す手間があり、Vite と設定を共有できない。

根拠: Value 3 / Value 4（MVV 版 3）

### 決定 3: Cognito が GitHub を OIDC の IdP として受けられるよう、`factory/api/` に GitHub の仲立ちを置き、GitHub の OAuth の秘密は受け渡すだけにする

Cognito の組み込みの IdP に GitHub は無く、GitHub の OAuth は ID token を返さないため、OIDC の IdP として直に登録できない。仲立ちは OIDC の 5 つの経路を持つ小さな Lambda で、インフラではなくサービスのコードなので `factory/api/` に置く（前提 2）。Cognito の IdP の登録には GitHub の OAuth App のクライアントの ID と秘密を入れ、仲立ちは Cognito から受けたそれを GitHub へ渡すだけにする。サービスが持つ秘密は ID token の署名の鍵 1 つになり、`GITHUB_OIDC_SIGNING_KEY_SECRET_ARN` で受ける。`sub` は GitHub の数の利用者 ID にし、利用者が変えられるログイン名とメールを使わない。

仲立ちが GitHub の OAuth の秘密を Secrets Manager から読む形は、同じ秘密を Cognito とサービスの 2 か所に置くことになる。Cognito の前に別の認証の層を立てる形は、依頼の「Cognitoによる認証」から外れる。

根拠: C1 / C2 / Value 5（MVV 版 3）

### 決定 4: 1 人が個人とチームの両方のアカウントを持てるよう、`account_id` を token に載せず、要求の経路から API が解決する

利用者は個人のアカウント 1 つと、席を持つチームのいくつかに属する。token に `account_id` を 1 つ載せると、別のアカウントとして操作するたびにログインし直すことになり、載せるには Cognito の token を作る前の Lambda をインフラに足す必要がある。経路を `/accounts/{account_id}/…` にし、`resolveAccount` が利用者の ID とその ID から文脈を返せば、#2171・#2186・#2174 の経路も同じ形で書ける。経路にアカウントの ID が無い要求は、その利用者の個人のアカウントとして扱う。要求の未決の 4 行目と 5 行目はこの決定で閉じる。要求の S4（認可の token から `account_id` が取れる）は、検証を通った token の利用者と経路を `resolveAccount` へ渡して `account_id` を得る形で満たし、確定仕様の「受け持つ課題」の表もこの形へ直す。

要求ごとに `X-Factory-Account` のようなヘッダーで選ぶ形は、経路と認可の対象が離れ、画面のリンクからどのアカウントの画面かが読めない。

根拠: Value 6（MVV 版 3）

### 決定 5: 初めてのログインで個人のアカウントが必ず 1 つだけできるよう、API が最初の要求で利用者と個人のアカウントを 1 つのトランザクションで作る

`GET /me`（とアカウントの解決）が利用者の行を読み、無ければ「利用者の行が無いこと」を条件に、利用者・個人のアカウント・履歴の行を 1 つのトランザクションで書く。並べて 2 回来ても条件で片方だけが通り、失敗した側は読み直して同じ ID を返す（I12、受け入れ条件 12）。作れなかったときは 503 を返し、次の要求で作り直す（要求の E2）。

Cognito のログインの後の Lambda で作る形は、インフラがその Lambda をつなぐ必要があり、作成に失敗するとログインそのものが失敗する。メールアドレスでまとめる形は前提 3 が退けている。

根拠: Value 6 / C3（MVV 版 3）

### 決定 6: 他人に成りすまして席へ入れないよう、席は利用者の ID で指し、メールアドレスを使わず保存もしない

チームの持ち主は、相手が自分のアカウントの画面から写して渡した利用者の ID（Cognito の `sub`）を入れて席を足す。メールで指すと、片方の IdP でメールを偽れたときに別人が席に入る（前提 3 と同じ理由）。ID で指すなら、メールを保存する理由が無く、ログに出す恐れも消える。

メールで指して確認のメールを送る形は、招待のメール（前提 5 で範囲外）が要る。

根拠: C2 / C3 / Value 2（MVV 版 3）

### 決定 7: 使える量をコードの分岐でなく 1 か所で変えられるよう、`limits.ts` の 1 つの表にプランと席の種類の 5 つの組を置き、初めの値は案としてゲート 1 に出す

表の鍵は `free`・`pro`・`max`・`team:standard`・`team:premium` で、値は同時に動かせるスプリントの実行の数と月の token の上限の 2 つ。問い合わせはアカウントの文脈から鍵を作って引くだけで、表に無い鍵は断る側へ倒す（I19）。値を変えるのは表の 1 行で、型が鍵の漏れを落とす。初めの値は設計文書の「使える量の初めの値」の案で、実測が無いため #2174 の台帳ができた後に見直す。

DynamoDB の行に置く形は、値の変更をコードの差分と Pull Request で追えなくなり、値を変える経路と権限を別に作る必要がある。

根拠: Value 3 / Value 7（MVV 版 3）

### 決定 8: 請求が過去の席とプランを追えるよう、DynamoDB の 1 つの表に今の値の行と追記だけの履歴の行を置き、同じトランザクションで書く

利用者・アカウント・席・履歴の行を `pk`・`sk` の 1 つの表に置き、利用者が席を持つチームの一覧は GSI 1 つで引く。今の値は上書きするが、変えるたびに履歴の行を同じトランザクションで足すため、過去を失わない（I20）。表を 1 つにすると、インフラへ渡す表の定義が 1 つで済み、条件つきのトランザクションが 1 つの表の中で閉じる。

今の値を持たず履歴だけから導く形は、毎回の認可で履歴を読み直すことになり、要求ごとの読み出しが増える。履歴を持たず上書きだけの形は、#2174 が席の日数を数えられない。

根拠: Value 6 / C3（MVV 版 3）

### 決定 9: 画面の判定だけで認可しないよう、API の Lambda の中で `aws-jwt-verify` が access token を毎回検証する

`aws-jwt-verify` は AWS が出す Cognito の token の検証のライブラリで、署名・発行元（利用者プールから作る）・`client_id`・期限・`token_use` を 1 回の呼び出しで見る。公開鍵の一覧を外から渡せるため、テストの中で作った鍵で、401 になる 6 つの入力（テスト設計の受け入れ条件 4 の行）を本物の Cognito なしで確かめられる（実測で確かめた）。受けるのは access token だけで、ID token は `token_use` の違いで 401 にする。API Gateway の JWT の認可をインフラが足しても、Lambda の中の検証は外さない。

`jose` で検証を組み立てる形は、Cognito に固有の照合（`token_use`・`client_id`）を自分で書くことになる。API Gateway の認可だけに任せる形は、受け入れ条件 4 をこのリポジトリのテストで縛れない。

根拠: C2 / Value 3（MVV 版 3）

### 決定 10: 画面に秘密を置かずにログインできるよう、`oidc-client-ts` で認可コードと PKCE を使い、token は sessionStorage に置く

画面は Cognito の管理されたログインへ `identity_provider`（`Google` / `GitHub`）を付けて送り、IdP の選択の画面を飛ばす。戻りで code を token に換えるのは PKCE で、クライアントの秘密を画面に持たない。token はタブを閉じれば消える sessionStorage に置き、ログアウトで消してから Cognito のログアウトへ送る（受け入れ条件 3）。

Amplify の認証は同じことができるが、依存が大きく、使うのはこの 1 つの機能だけである。localStorage はタブを閉じても token が残る。

根拠: C2 / Value 1（MVV 版 3）

### 決定 11: 経路の割り振りとテストを短く書けるよう、API の入口に Hono を使う

Hono は Lambda の HTTP API の handler をそのまま出せ、テストでは `app.request()` で Lambda を立てずに経路を呼べる。#2171・#2186・#2174 も同じ入口に経路を足し、認証と解決の中間処理を共有する。

経路の割り振りを自分で書く形は、中間処理とエラーの形を各課題が書き足すことになる。

根拠: Value 4 / Value 6（MVV 版 3）

### 決定 12: `factory/` を変える Pull Request で必ず結果が出るよう、CI のジョブは絞り込まずに起動し、変更が無ければ検査を省いて成功で終える

`.github/workflows/factory-check.yml` の `pull_request` は `paths` で絞らず、ジョブ `factory-check` の最初の手順で、基点との差分に `factory/` かこのワークフローがあるかを見る。無ければ以降を省いて成功で終える。あれば `.node-version` の Node で `npm ci` と `npm run check` を打つ。GitHub は `paths` で除外した実行を成功として扱わないため、後で必須のチェックにしたときに対象外の Pull Request が止まる（`pytest.yml` の冒頭の注釈と同じ理由）。必須への追加は利用者の作業のため、`scripts/required-checks-allow.json` の `awaiting` に理由つきで置く。

既存の `runtime-plugin-validate.yml` へジョブを足す形は、プラグインの検証と `factory/` の検査が同じワークフローの失敗として並び、どちらが落ちたかをワークフローの名前から読めない。

根拠: Value 2 / Value 4（MVV 版 3）

### 決定 13: 画面と API の形を実装より先に固めるため、API の OpenAPI を `factory/contracts/openapi/accounts.yaml` に置き、Spectral で検査する

確定仕様は API の形の機械可読な定義を `factory/contracts/` に置くとしている。アカウントの経路はこの課題が最初に置くため、この課題がファイルを作り、根の `npm run lint:contracts`（`spectral lint` の `spectral:oas` の規則）で検査する。#2171 が `contracts/` の形を決めるとき、このファイルを取り込む（未確認のまま残ること）。

`factory/api/` の中に置く形は、#2171 の後で場所を移すことになる。

根拠: Value 7（MVV 版 3）

### 決定 14: 規約が認める形だけで Claude を呼べるよう、実行の形態をアカウントの値にし、既定を `local` にし、Free で `managed` を断る

claude.ai のログインをサービスが預かる形は、Anthropic の Agent SDK の文書が事前の承認なしに認めていない。そこで 2026-10-30 の利用者の決定で、ランナーが Claude を呼ぶ方法をアカウントごとに `local`（手元のランナー）・`byok`（キーの持ち込み）・`managed`（サービスの Bedrock）の 3 つから選ぶ形にした（PR #2190 https://github.com/devbasex/ai-plugins/pull/2190 の確定仕様の「実行の形態」）。この課題は形態の値と選べる条件だけを持ち、アカウントの行の `execution_mode` に置き、`resolveAccount` の文脈と `GET /accounts/{account_id}` で返す。#2186・#2189・#2174 は同じ文脈から形態を読み、形態ごとの実行と課金を受け持つ。

新しいアカウントの形態は `local` にする（I24）。`local` はサービスが Claude の認証情報も token の費用も持たず、Free のまま何も足さずに使える唯一の形である。`byok` は持ち込むキーの登録（#2186）が先に要り、`managed` は Free では選べない。プランが `free` のアカウントは `managed` を持たない（I21）。Free の月の token の上限（ゲート 1 で承認した 2,000,000）はスプリントを 1 本も通せない値で、`managed` を Free に置いても使えないためである。形態を `managed` へ変える要求は 409 `managed_not_allowed_on_free` で断り、`setPlan` で `free` へ下げるとき（#2174 の支払いの失敗など）に `managed` なら、同じトランザクションで `local` へ変えて履歴の行を足す。月の token の上限は `managed` だけで照らし（I23）、Free の行は月の token の値を持たない。

既定を `managed` にする形は、Free の新しいアカウントが I21 を初めから破る。形態をアカウントでなくリポジトリの登録かスプリントの実行ごとに持つ形は、確定仕様が値の持ち主をアカウントの集約に決めており、同じアカウントの中で課金の扱いが分かれる。Free で `managed` を許して月の token を小さく掛ける形は、利用者の決定（Free は ① と ② だけ）から外れる。

根拠: C2 / Value 5 / Value 7（MVV 版 3）
