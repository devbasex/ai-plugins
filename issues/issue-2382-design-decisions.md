# #2382 の設計の決定

設計文書 `issues/issue-2382-design.md` の「決定の記録」の節を分けたもの。

## 決定の記録

### 決定 1: 翻訳のキーの誤りを型検査で落とすため、国際化に i18next と react-i18next を使い、日本語の翻訳のファイルからキーの型を作る

`i18next` と `react-i18next` を依存に足す。`ja.json` を `CustomTypeOptions` の `resources` に渡し、`t` が受けるキーを翻訳のファイルのキーに絞る（受け入れ条件 12）。埋め込み（`{{plan}}`）・複数形・既定の言語への戻りは i18next が持つ。初期化は翻訳のファイルを同期で渡し、描く前に終える。言語の判定の追加のパッケージ（`i18next-browser-languagedetector`）は足さず、`navigator.languages` から翻訳のある言語を選ぶ 10 行ほどの関数で決める（前提 5）。

自作の `t` 関数は採らなかった。キーの型は数十行で書けるが、埋め込みと複数形と戻りを自分で持ち続けることになる。carul が同じ i18next を採っており、運用の知見を写せる。

根拠: Value 5 / スプリント Value 5・スプリント Value 6（MVV 版 4・スプリント MVV 44cd096d）

### 決定 2: 整形の依存を足さないため、日時・数・金額は `Intl` で整形し、金額は通貨と組の型でだけ受ける

`Intl.DateTimeFormat`（`timeZone` を渡す）と `Intl.NumberFormat`（`style: "currency"`）で足りる。`Money = { amount: number; currency: string }` を受ける `formatMoney` だけを置き、数だけを受ける金額の整形を作らない（受け入れ条件 15）。Node 24 で `2026-11-02T00:00:00Z` が `ja-JP`・`Asia/Tokyo` で `2026/11/2 9:00:00`、`en-US`・`America/New_York` で `11/1/2026, 7:00:00 PM`、`JPY` で `￥1,235`、`USD` で `$1,234.50` になることを確かめた。

日時のライブラリ（date-fns-tz など）は採らなかった。必要な整形は `Intl` が持ち、依存の費用だけが増える。

根拠: スプリント Value 5・スプリント Value 6（MVV 版 4・スプリント MVV 44cd096d）

### 決定 3: 今の TypeScript 7 と組める検査にするため、直に書いた文言の検査は oxc-parser で源を読む自前のスクリプトにする

`web/scripts/check-source.ts` が `oxc-parser` で `.ts`・`.tsx` を解析し、「入出力の契約」の 5 つの規則で落とす。ESLint の `eslint-plugin-i18next` の `no-literal-string` は、TypeScript の源を読むのに `typescript-eslint` が要り、その peerDependencies は `typescript: '>=4.8.4 <6.1.0'` で `factory/` の `^7.0.2` と組めない（2026-11-02 に `npm view` で確かめた）。`oxc-parser` は TypeScript に依存せず、Node `>=22.12.0` で動く。スクリプトは Node 24 が型を剥がして直に打つ。

ESLint を TypeScript 6 で別に動かす案は採らなかった。1 つのロックファイルに TypeScript の 2 つの版が入り、型検査と検査で源の読み方が食い違う。

根拠: Value 3・Value 6（MVV 版 4）

### 決定 4: ダミーと本物の切り替えを 1 か所にするため、Vite の mode `dummy` で起動の組み立てが選ぶ

`main.tsx` だけが `import.meta.env.MODE === "dummy"` を見て、ダミーの `Auth`・`Api`・`ConnectionSource` を組む。画面は `useServices()` から受けるだけで、どちらかを知らない（受け入れ条件 17）。ダミーの場面（`normal` / `empty` / `error`）とつながりの状態（`online` / `offline` / `pending`）は、ダミーで起動したときだけ URL の検索の値（`?scenario=`・`?connection=`）から決める。

画面の設定（`FactoryConfig`）に環境変数を 1 つ足す案は採らなかった。確定仕様は画面へ埋め込む変数を 4 つに限っており（accounts-and-plans「環境変数」）、mode なら本番のビルドで値が定数になり、分岐ごと落とせる（決定 5）。

根拠: スプリント Value 2・スプリント Value 4（MVV 版 4・スプリント MVV 44cd096d）

### 決定 5: ダミーを本番へ入れないため、ダミーは mode の分岐の中の動的な import だけで読み、成果物に印が無いことを検査する

本番のビルドでは `import.meta.env.MODE` が `"production"` に置き換わり、分岐とその中の `import("./dummy/index.ts")` が死んだコードになる。落ちたことを、ダミーデータに置いた印の文字列 `ndf-factory-dummy-data` を `dist/` で探す `check:bundle` で確かめる（受け入れ条件 18）。ビルドが分岐を落とさなかったときは、`vite.config.ts` の `resolve.alias` で本番だけ `src/dummy/index.ts` を空の module へ置き換える形へ移る（未確認 2）。

ダミーを別の workspace に置く案は採らなかった。`Api` の型を共有するために workspace の間の参照が増え、画面と同じ差分でダミーを直せなくなる。

根拠: スプリント Value 4 / C1（MVV 版 4・スプリント MVV 44cd096d）

### 決定 6: 画面専用の経路を作らないため、見本の詳細は `Api` に配列で受け渡す `sprintRunDetails` を足し、本物の実装は既存の一覧の経路から組む

`sprintRunDetails(accountId, sprintRunIds: string[]): Promise<SprintRunSummary[]>` を足す（スプリント Value 2「1 件のときも要素 1 個の配列」）。契約に詳細の経路はまだ無く、本物の実装は `GET /accounts/{a}/sprint-runs` の 50 件から ID で選ぶ。API と契約は変えない（要求の「境界」）。#2172 が詳細の経路を足したら本物の実装だけを差し替え、画面は変わらない。一覧は既存の `sprintRuns` をそのまま使う（前提 6 の「契約がある操作はその形」）。

API に詳細の経路を足す案は採らなかった。経路の形は #2172 の設計が決めており、先取りになる。

根拠: スプリント Value 2・スプリント Value 4（MVV 版 4・スプリント MVV 44cd096d）

### 決定 7: 地図と経路が食い違わないため、経路の定義を `screens.ts` の画面の地図から作り、確定仕様の表との一致をテストで見る

`App.tsx` は `screens.ts` の `available` の行から `<Route>` を作り、`NavMenu` は `nav` の行から行き先を作る。経路を 2 か所に書かない（受け入れ条件 4）。人が読む地図（役割・動線・確定仕様の機能との対応）は確定仕様の文書に置き、表の 1 列目の経路の集合と `screens.ts` の集合が一致するかを Vitest で見る。照らすのは経路という識別子の集合で、文言ではない（`AGENTS.md` の「`.md` の文言を照合するテストを書かない」に当たらない）。部品の一覧（受け入れ条件 6）も、`src/ui/` のファイル名の集合と文書の表の 1 列目を同じ形で照らす。

文書を `screens.ts` から生成する案は採らなかった。役割ごとの動線と機能との対応はコードに持たない情報で、生成の仕組みと手書きの部分が 1 つの文書に混ざる。

根拠: Value 7・Value 6（MVV 版 4）

### 決定 8: 見た目の値を持ち運べる形にするため、トークンは Design Tokens Format Module の JSON に置き、起動時に CSS の変数へ写す

`src/ui/tokens.json` を `$value`・`$type`・`$description` の形で書き、`tokens.ts` の純粋な関数が `--color-…`・`--space-…`・`--font-…` の宣言へ写す。`main.tsx` が描く前に 1 度 `<style>` へ入れる。部品の CSS は 1 つの `ui.css` に `ui-` の接頭辞のクラスで書き、値はすべて変数で引く。ビルドの手順（Style Dictionary など）を足さないため、生成物とその食い違いの検査が要らない。

Tailwind CSS と CSS-in-JS は採らなかった。前者はトークンの正を設定ファイルへ移し、後者は実行時の依存を足す。どちらも 17 個の部品の規模に見合わない。

根拠: スプリント Value 6（MVV 版 4・スプリント MVV 44cd096d）

### 決定 9: 後続の課題が確定仕様と同じ場所で読めるため、画面の地図と部品の一覧は `docs/specifications/` に新しい 2 本として置く（未決 3）

`docs/specifications/ndf-factory-screen-map.md` と `ndf-factory-ui-components.md` を作る。後続の課題（#2172・#2173・#2347）は `ndf-factory-*.md` を読んで画面を足すため、同じ並びにあれば見落とさない。この課題の `plan-to-spec` は 3 本目を作らず、この 2 本へ書き足す。

`factory/web/` の下に置く案は採らなかった。地図は確定仕様の機能を引くため、確定仕様と離れると参照の向きが 2 つの木にまたがる。

根拠: Value 7（MVV 版 4）

### 決定 10: 配置を実ブラウザで測るため、E2E は Playwright の Chromium で 3 つの幅を測り、`npm run check` の外の CI の手順にする

jsdom は配置を計算せず、はみ出しと手数（受け入れ条件 5・9）を測れない。`@playwright/test` を開発の依存に足し、`webServer` でダミーの mode の開発サーバーを立てて、360×740・768×1024・1280×800 の 3 つの project で回す。ブラウザの導入が要るため `npm run check` へは入れず、CI の `factory-check` に `npx playwright install --with-deps chromium` と `npm run e2e -w web` の 2 手順を足す。

Vitest のブラウザのモードは採らなかった。Playwright を中で使うのは同じで、経路を開いて押す E2E の書き方は Playwright の方が直である。

根拠: スプリント Value 3 / Value 3（MVV 版 4・スプリント MVV 44cd096d）

### 決定 11: 3 つの代表の幅で崩さないため、ブレークポイントを 768 px（一覧を表へ）と 1024 px（案内を横へ）の 2 つにする

前提 2 の 360・768・1280 px の間に境を置く。768 px 未満は一覧をカードに積み、名前と値を縦に並べる。1024 px 未満はメニューのボタンで案内を開き、行き先まで 2 手で届く（受け入れ条件 5）。メディアクエリは CSS の変数を使えないため、境の値は `ui.css` に書き、部品の一覧の文書に載せる。

幅ごとに別の部品を作る案は採らなかった。同じ部品が CSS だけで形を変える方が、画面のコードが幅を知らずに済む。

根拠: スプリント Value 3・スプリント Value 1（MVV 版 4・スプリント MVV 44cd096d）

### 決定 12: #2383 が中身を足しても部品を変えないため、つながりの状態を 3 つの値の組にし、ブラウザの実装は時刻を持たない

`ConnectionState = { online, lastReceivedAt, pendingOperations }` とし、部品はオフラインと確定待ちを別々に出す（オフラインで確定待ちがある場面も表せる）。ブラウザの実装は `online` だけを持ち、`lastReceivedAt` は `null`、`pendingOperations` は 0 を返す。時刻と確定待ちの数を作るのは #2383 で、同じ `ConnectionSource` の実装を差し替える（前提 8）。承認の操作の部品は `online` が偽なら押せず、「つながってから確定します」を添える（スプリント Value 3）。

3 つの値の和（`online` / `offline` / `pending`）の型は採らなかった。つながってすぐの確定待ちと、オフラインの確定待ちを分けられない。

根拠: スプリント Value 3（MVV 版 4・スプリント MVV 44cd096d）

### 決定 13: 既存のテストを書き換えずに通すため、`App` は `connection`・`settings`・翻訳のファイルが無いときに既定を使い、日本語の文言を移すときに文を変えない

`App.test.tsx` は `services` に `auth` と `api` だけを渡し、日本語の文で要素を探す。`App` は無い値をブラウザのつながり・ブラウザから決めた設定・`ja.json` で埋める。既存の画面の文言は、翻訳のファイルへ移すときに文を 1 字も変えない（受け入れ条件 19）。jsdom の `navigator.language` は `en-US` で、数の整形（`125,000,000`）は今の `ja-JP` と同じ文になる。

テストの側に包む部品を足す案は採らなかった。受け入れ条件 19 は既存のテストが書き換えた画面でも通ることを求めている。

根拠: Value 1（MVV 版 4）
