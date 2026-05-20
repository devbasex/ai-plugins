# Checklist: `lp` — Landing Page

## 適用条件
ドメインルート / `/lp/*` `/campaign/*` `/about` 等、外部到達ページ。
ナビ + ヒーロー + 説明セクション + CTA。SEO meta tag が充実。
ページ深度 1。機能リンクは少数 (1〜3)。

## 必須テスト観点

### LP1: ヒーロー領域レンダリング `[Claims / Claims]`
- LCP < 2.5s (`scripts/check_cwv.py --metric lcp`)
- ヒーロー画像/動画の poster が空白でない
- WebP / AVIF fallback が動作

### LP2: CTA 配置と動作 `[Claims / Purpose]`
- above-the-fold / mid-scroll / page-end の 3 箇所に CTA があるか
- CTA: `get_by_role("button", name=...)` または `get_by_role("link", name=...)` で取得
- `expect(cta).to_be_visible()` + `cta.click()` → 期待遷移
- コントラスト比 ≥ 4.5:1 (axe-core が検出)

### LP3: ナビゲーション全リンク死活 `[Claims / Statutes]`
- `page.get_by_role("navigation").get_by_role("link").all()` で全リンク列挙
- 各 href を curl で叩き status ≤ 399
- 内部リンクは Playwright で踏み、ページ崩壊しないか
- 外部リンクは `target="_blank" rel="noopener noreferrer"` を持つ (axe `target-blank-security`)

### LP4: SEO meta tag 充足 `[Claims / Statutes]`
- `<title>` (≤ 60 字)
- `<meta name="description">` (≤ 160 字)
- `<meta property="og:title" / og:description / og:image / og:url>`
- `<link rel="canonical">`
- 構造化データ (`<script type="application/ld+json">`) の妥当性 (Google Rich Results Test 相当)

### LP5: レスポンシブ `[Compatibility / Image]`
- viewport: 320 / 768 / 1024 / 1920 で visual regression
- `expect(page).to_have_screenshot("lp-{viewport}.png", mask=[広告/動的領域])`
- mobile viewport meta tag (`<meta name="viewport" content="width=device-width">`)

### LP6: アクセシビリティ強化 `[Automatic / Statutes]`
- axe-core `wcag2aa + wcag22aa` violations = 0
- 見出し階層: h1 単一 + h2 連続 + h3 順次
- ランドマーク: `<header><nav><main><footer>` 構造
- 全 image に有意な alt または `alt=""` (装飾)

### LP7: Cookie 同意バナー (GDPR/CCPA) `[Statutes / Claims]`
- 初回訪問でバナー表示
- 「同意」「拒否」両方の選択肢
- 拒否時に tracking script (gtag / facebook pixel) がロードされないこと
- network log で確認 (`page.on("request", ...)`)

### LP8: フォーム CTA (お問い合わせ / サインアップ) `[Claims / Purpose]`
- フォーム遷移先が 200 OK
- 送信成功画面の表示
- 詳細は `form` checklist に従う (兼ね適用)

### LP9: 第三者 widget (chat / video / map) の遅延ロード `[Performance / Reliability]`
- LP 初回 LCP に widget が影響しないか
- widget のロード失敗時に LP 全体が壊れないか (`route` で widget host を `abort`)

### LP10: スクロール時の CLS `[Claims / Image]`
- 画像 / iframe / web font の遅延差し替えで layout shift しない (`width/height` 属性 + aspect-ratio CSS)
- CLS < 0.1 (`scripts/check_cwv.py --metric cls`)

### LP11: 内部 anchor link `[Functional / Product]`
- `#section1` の click で smooth scroll
- focus が anchor 先に移動する (a11y)

### LP12: print stylesheet `[Statutes / 限定]`
- `page.emulate_media(media="print")` で重要セクションが見える
- 装飾だけが消える

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| Claims Testing | LP1, LP2, LP4, LP10 (主張の検証) |
| Domain Testing | LP5 (viewport partition) |
| Automatic Checking | LP6 (axe-core) |
| Risk Testing | LP3 (外部リンク nofollow / opener) |
| Compatibility | LP5 |

## 自動化境界

| 自動化容易 | 手動寄り |
|-----------|---------|
| LP1 (LCP), LP3 (リンク死活), LP4 (meta), LP6 (axe), LP10 (CLS) | LP2 (CTA の感情訴求 — Charisma), LP5 (レイアウト主観評価) |

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y), C2 (perf), C5 (cross-browser), C7 (404 ページ) を全項目走査。

## 参考文献

- web.dev "Optimize LCP", https://web.dev/articles/optimize-lcp
- Unbounce, "CTA Placement"
- Schema.org, "Structured Data"
- WCAG 2.2 (4.1.2 Name, Role, Value)
