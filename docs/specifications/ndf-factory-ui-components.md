# Neural Development Factory: 画面の部品

Neural Development Factory の Web の画面を組む部品の一覧と使い分け・デザインのトークン・端末の幅・文言と書式の決まりを定める。画面を足す課題は、ここにある部品だけで画面を組み、部品・整形・API クライアントの仕組みに手を入れずに済ませる。どの画面がどの経路にあるかは [画面の地図](ndf-factory-screen-map.md) にある。

## 概要

- **部品は `factory/web/src/ui/` にあり、1 つのファイルに 1 つの部品を置く。** 下の「部品の一覧」の 1 列目の集合と `src/ui/` の部品のファイル名の集合は、テスト（`factory/web/src/docs.test.ts`）が一致を確かめる。部品を足したら一覧に行を足す
- **見た目の値はデザインのトークンの名前で引く。** 部品の CSS（`src/ui/ui.css`）は `ui-` の接頭辞のクラスで書き、色・文字・余白は CSS の変数で引く
- **画面のコードに表示の文言を直に書かない。** 文言は翻訳のファイル（`src/i18n/ja.json`）のキーで引き、検査（`npm run check:source -w web`）が直に書いた文言を落とす
- **画面は `Api` のインタフェースだけを通してデータを読む。** ダミーの実装を import してよいのは起動の組み立て（`src/main.tsx`）だけである

## 用語

定義は [用語集](../glossary.md) が正である（文脈 `ndf-factory`）。この文書で使う語は「デザインのトークン」「翻訳のファイル」「ダミーの API クライアント」「つながりの状態」「言語と地域の設定」「見本の画面」である。

## 部品の一覧

| 部品 | 使う場面 | 使わない場面 |
| --- | --- | --- |
| `AppShell` | ログインの後の画面の枠（見出しの帯・ナビゲーション・つながりの状態）。経路の定義の layout route として 1 つだけ置く | 画面の中。ログインの画面・ログインの戻り（token が無いまま `api.me()` を呼ぶことになる） |
| `NavMenu` | 枠の中の主な行き先。行き先は画面の地図の `nav` の行から作る | 画面の中の行き来（画面の中では `Link` を使う） |
| `PageHeader` | 画面の題（`h1`）と画面全体に掛かる操作（ログアウト・確かめる など）。1 画面に 1 つ | 区画の見出し（`Section` を使う） |
| `Section` | 見出し（`h2`）つきのまとまり | 見出しの無い並び |
| `DataList` | 同じ形の行の一覧。行を押して詳細へ移るときは `rowHref` を渡す | 名前と値の組（`DetailList`）。行ごとに操作のボタンが並ぶ一覧（`ul` と `Button`） |
| `DetailList` | 1 件の名前と値の組 | 複数の行の比較（`DataList`） |
| `StatusBadge` | 状態の札。色の調子（中立・情報・成功・注意・危険）と文言を必ず組で渡す | 操作（押せる見た目にしない） |
| `ApprovalActions` | 承認する・差し戻すの 2 つの操作。`online` が偽なら押せず「つながってから確定します」を添える | 取り消せる操作（`Button` を使う） |
| `SearchField` | 一覧の絞り込み | 送信するフォームの入力（`TextField`） |
| `EmptyState` | 0 件・見つからないとき。次の操作があれば `action` に渡す | 失敗（`ErrorState`） |
| `LoadingState` | 読み込み中 | 操作の後の待ち（ボタンを押せなくする） |
| `ErrorState` | 読み込みの失敗。やり直せるなら `onRetry` を渡す | 操作の失敗（`Alert`） |
| `ConnectionStatus` | つながりの状態の 3 つの見せ方（オンライン・最後に受け取った時刻つきのオフライン・つながってから確定する操作の数）。枠が出す | 画面の中（枠が 1 か所で出す） |
| `Button` | ボタン。主（`primary`）は画面に 1 つ、危険（`danger`）は消す・外す。`type` の既定は `button` | 別の画面へ移る（`Link` を使う） |
| `TextField` | ラベルつきの 1 行の入力。誤りの文は `error` に渡す | 一覧の絞り込み（`SearchField`） |
| `SelectField` | ラベルつきの選択。選択肢は `option` を子に渡す | 2 つだけの切り替えで、すぐ反映する操作（`Button` 2 つ） |
| `Alert` | 操作の結果の文（`role="alert"`）。調子は `danger`（既定）・`info`・`success` | 読み込みの失敗（`ErrorState`） |

部品の振る舞いのテストは `factory/web/src/ui/ui.test.tsx` にある。

## デザインのトークン

`src/ui/tokens.json` に Design Tokens Format Module の形（`$value`・`$type`・`$description`）で置く。起動の組み立てが描く前に 1 度だけ CSS の変数へ写し、`<style>` へ入れる。名前は入れ子の名前を `-` でつなぐ（`color.primary` → `--color-primary`）。値の参照（`{color.base}`）は写すときに解く。

| 群 | 変数 | 中身 |
| --- | --- | --- |
| 色 | `--color-base`・`--color-surface`・`--color-text`・`--color-muted`・`--color-border`・`--color-primary`・`--color-on-primary` | 地・帯・本文・補足・線・主の操作 |
| 色の調子 | `--color-{neutral,info,success,warning,danger}` と `-bg` | 状態の札・操作の結果の文・つながりの状態 |
| 余白 | `--space-{1,2,3,4,6,8}` | 4・8・12・16・24・32 px |
| 文字 | `--font-family`・`--font-mono`・`--font-size-{sm,md,lg,xl}`・`--font-weight-{regular,bold}`・`--font-line-height` | 書体・大きさ・太さ・行の高さ |
| 角の丸み | `--radius-{sm,md,pill}` | 4・8・999 px |
| 大きさ | `--size-touch`・`--size-content` | 押せる大きさの下限 44 px・本文の幅の上限 1080 px |

## 端末の幅

| 幅 | 代表 | 形 |
| --- | --- | --- |
| 768 px 未満 | スマートフォン（360 px） | `DataList` は 1 行を 1 枚のカードに積む。`DetailList` は名前と値を縦に積む。ナビゲーションはメニューのボタンで開く |
| 768 px 以上 1024 px 未満 | タブレット（768 px） | `DataList` は列の表。`DetailList` は名前と値を横に並べる。ナビゲーションはメニューのボタンで開く |
| 1024 px 以上 | PC（1280 px） | ナビゲーションを見出しの帯に横に並べる |

境は 768 px と 1024 px の 2 つで、`ui.css` のメディアクエリに書く（メディアクエリは CSS の変数を使えない）。部品は CSS だけで形を変え、画面のコードは幅を知らない。ログインの後の画面は 3 つの代表の幅のどれでも横にはみ出さない（E2E が確かめる）。

## 文言と書式

- 文言は `src/i18n/ja.json` のキーで引く（`useTranslation` の `t`）。キーの型は日本語の翻訳のファイルから作り、無いキーは型検査で落ちる
- 言語を足すときは翻訳のファイルを 1 つ足し、`src/i18n/index.ts` の `bundledResources` に並べるだけで、画面のコードは変えない。訳の無いキーは日本語で出る
- 言語と地域の設定は、今はブラウザの言語と時刻帯から決める。ブラウザの言語に翻訳のファイルが無ければ日本語で出す
- 日時は API から UTC の ISO 8601 で受け、`useFormat().dateTime` で設定の時刻帯で出す。数は `number`、金額は通貨と組の `Money`（`{ amount, currency }`）を `money` で出す。数だけの金額は整形の関数へ渡せない
- 検査（`npm run check:source -w web`）は `src/**/*.{ts,tsx}` を読み、次の規則で落とす。テスト（`*.test.*`）・ダミー（`src/dummy/`）・翻訳のファイルは見ず、コメントも見ない。終了コードは 0（無し）・1（見つけた）・2（構文解析できない）

| 規則 | 落とすもの |
| --- | --- |
| `jsx-text` | 文字を含む JSX の文 |
| `jsx-attr` | 文字を含む文字列を `aria-label`・`title`・`placeholder`・`alt`・`label` に直に渡す |
| `jsx-expr` | JSX の子に置いた、文字を含む文字列・テンプレート |
| `non-ascii` | ASCII の外の文字を含む文字列・テンプレート（JSX の外も） |
| `dummy-import` | `src/main.tsx` 以外から `dummy/` を import する |

## API クライアントとダミー

- 画面は `useServices()` の `api`（`Api` のインタフェース）だけを通してデータを読む。足す操作は配列で受け渡す（1 件のときも要素 1 個の配列）
- ダミーの実装は `src/dummy/` にあり、`Api`・`Auth`・つながりの元のダミーとダミーデータを持つ。操作を足したらダミーにも同じ名前の関数を足す
- 起動の組み立て（`src/main.tsx`）が Vite の mode `dummy` のときだけダミーを動的に読む。本番のビルドでは分岐ごと落ち、`npm run check:bundle -w web` が成果物にダミーデータの印 `ndf-factory-dummy-data` が無いことを確かめる

## 検査

| コマンド（`factory/` で打つ） | 中身 |
| --- | --- |
| `npm run check -w web` | 型検査 → Vitest → `check:source` → `vite build` → `check:bundle` |
| `npm run e2e -w web` | ダミーで起動した画面を Playwright の Chromium で 360×740・768×1024・1280×800 の 3 つの幅で開き、横のはみ出しとナビゲーションの手数を測る。初めて打つ前に `npx playwright install chromium` が要る |

## 関連リンク

- [画面の地図](ndf-factory-screen-map.md)
- [Design Tokens Format Module](https://www.designtokens.org/tr/drafts/format/)
