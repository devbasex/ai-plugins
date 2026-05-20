# 全 role 共通チェックリスト

すべての page role に対して走査する横断項目。
各 role 固有チェックリストと**併用**する (両方適用)。

各項目には `[技法 / oracle 軸]` が併記されている。テスト計画 YAML には適用した技法と oracle を必ず記録する。

## 1. アクセシビリティ (WCAG 2.2 AA)

| # | 観点 | 技法 | oracle | 検査方法 |
|---|------|------|--------|---------|
| C1.1 | axe-core 違反 0 件 | Automatic Checking | Statutes (WCAG) | `scripts/run_a11y_scan.py {url}` (axe-playwright-python). タグ `wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa` |
| C1.2 | キーボードのみで全主要操作可能 | User Testing | Statutes (WCAG 2.1.1) | Tab で操作要素を順に踏破。手順 YAML で記録 |
| C1.3 | フォーカス可視性 | User Testing | Statutes (WCAG 2.4.7, 2.4.11 New 2.2) | フォーカス時の outline / 背景色変化を確認 |
| C1.4 | 見出し階層 (h1 単一 / h2 → h3 順) | Automatic | Statutes (WCAG 1.3.1) | `page.locator("h1").count() == 1` + axe `heading-order` |
| C1.5 | 画像 alt | Automatic | Statutes (WCAG 1.1.1) | axe `image-alt` |
| C1.6 | フォームラベル紐付け | Automatic | Statutes (WCAG 1.3.1, 3.3.2) | axe `label`, `aria-input-field-name` |
| C1.7 | 色のみで情報伝達しない | User Testing | Statutes (WCAG 1.4.1) | リンク色のみで識別される箇所がないか目視 |
| C1.8 | コントラスト比 ≥ 4.5:1 (テキスト) | Automatic | Statutes (WCAG 1.4.3) | axe `color-contrast` |
| C1.9 | ターゲットサイズ ≥ 24×24 px (新 2.2) | Domain Testing | Statutes (WCAG 2.5.8) | bounding box 計測スクリプト |
| C1.10 | aria_snapshot のリグレッション | Automatic | Product (内部一貫性) | `expect(...).to_match_aria_snapshot(...)` |

## 2. パフォーマンス (Core Web Vitals)

LCP / INP / CLS は **field metric** だが、本 Skill では Lab 計測で代替。

| # | 観点 | 技法 | oracle | 検査方法 / 閾値 |
|---|------|------|--------|----------------|
| C2.1 | LCP ≤ 2.5s | Claims | Claims (web.dev) | `scripts/check_cwv.py --metric lcp` |
| C2.2 | CLS ≤ 0.1 | Automatic | Claims | `scripts/check_cwv.py --metric cls` |
| C2.3 | INP ≤ 200ms | User Testing | Claims | INP は field 主体。Lab で `pointer-down → next paint` を計測 |
| C2.4 | TTFB (server response) ≤ 800ms | Automatic | Performance | `page.expect_response` の経過時間 |

## 3. セキュリティ (OWASP Top 10:2025)

E2E から検証可能な範囲。攻撃ではなく**挙動確認**まで。

| # | 観点 | 技法 | oracle | 検査方法 |
|---|------|------|--------|---------|
| C3.1 | A01: Broken Access Control / IDOR | Risk Testing | Statutes (OWASP A01) | 別ロールの id を URL に直接指定 → 403 Forbidden |
| C3.2 | A05: Injection (XSS) sanitize | Risk Testing | Statutes (WSTG-INPV-01) | 入力欄に `<script>alert(1)</script>` → 実行されず本文に文字列として表示 |
| C3.3 | A05: Injection (SQL) | Risk Testing | Statutes (WSTG-INPV-05) | 入力欄に `' OR '1'='1` → エラーや想定外結果が返らない |
| C3.4 | CSRF token 必須 | Risk Testing | Statutes (OWASP CSRF) | hidden token を削除して submit → 403 |
| C3.5 | Cookie 属性 `Secure` `HttpOnly` `SameSite` | Automatic | Statutes (OWASP Session) | `context.cookies()` を取得し属性検査 |
| C3.6 | エラーページに stack trace を露出しない | Claims | Statutes (OWASP A09) | わざと 500 を起こし `Traceback` `at /path/to/...` 等が出ないか |
| C3.7 | open redirect 防止 | Risk Testing | Statutes (WSTG-CLNT-04) | redirect URL に `//evil.example.com` を渡してドメイン外へ飛ばないか |
| C3.8 | Content-Security-Policy 設定 | Automatic | Statutes (OWASP A06) | `expect_response` でレスポンスヘッダ検査 |

## 4. i18n / l10n

| # | 観点 | 技法 | oracle | 検査方法 |
|---|------|------|--------|---------|
| C4.1 | pseudo-localization 文字列 +40% でレイアウト崩壊しない | Domain Testing | Image | テキストを 1.4 倍に膨張させた fixture でテスト |
| C4.2 | RTL (`dir="rtl"`) でレイアウト鏡像化 | Compatibility | Statutes (i18n) | `page.evaluate("document.dir = 'rtl'")` 後の visual regression |
| C4.3 | Unicode 文字 (絵文字 / CJK / 結合 / ゼロ幅) を保持 | Domain Testing | Product | 入力 → 保存 → 再表示で同一文字列 |
| C4.4 | 日付 / 通貨フォーマット (TZ / DST / 千区切り) | Domain Testing | Statutes (Locale) | `Intl.DateTimeFormat` の locale を切替 |
| C4.5 | ハードコード文字列の検出 (英語混入) | Claims | Claims | pseudo-loc fixture で英語残存検出 |

## 5. クロスブラウザ / デバイス

| # | 観点 | 技法 | oracle | 検査方法 |
|---|------|------|--------|---------|
| C5.1 | Chromium / Firefox / WebKit で動作 | Compatibility | Compatibility | pytest project で `--browser firefox --browser webkit` |
| C5.2 | Mobile viewport (Pixel 5 / iPhone 13) | Compatibility | Compatibility | `playwright.devices["iPhone 13"]` で context 作成 |
| C5.3 | Tablet (768〜1024px) のレイアウト | Compatibility | Image | viewport を 768/1024 で撮影し layout 確認 |
| C5.4 | dark / light / reduced-motion 対応 | Domain Testing | Statutes (WCAG 2.3.3) | `page.emulate_media(color_scheme="dark", reduced_motion="reduce")` |

## 6. ネットワーク・エッジケース (Hendrickson: Interruptions)

| # | 観点 | 技法 | oracle | 検査方法 |
|---|------|------|--------|---------|
| C6.1 | offline で適切な表示 | Stress Testing | User | `context.set_offline(True)` |
| C6.2 | slow 3G (50KB/s, 400ms RTT) で操作可能 | Stress Testing | User | CDP `Network.emulateNetworkConditions` |
| C6.3 | 操作中の API abort → エラー表示 | Risk Testing | User | `page.route("**/api/**", lambda r: r.abort("failed"))` |
| C6.4 | リクエストの再送 (retry) | Reliability | History | abort 後にリトライボタンがあれば押す → 成功 |

## 7. エラーハンドリング (`error` role 兼用)

| # | 観点 | 技法 | oracle | 検査方法 |
|---|------|------|--------|---------|
| C7.1 | 404 で status 404 | Claims | Statutes (HTTP semantics) | 存在しない URL を踏む → status |
| C7.2 | 500 でユーザに stack trace を見せない | Risk Testing | Statutes (OWASP A09) | サーバ強制エラー (`?cause_500=1` 等) 後の表示 |
| C7.3 | 401 / 403 の差別化 | Claims | Claims | 未認証 → 401, 認可なし → 403 |
| C7.4 | 復帰導線 (home / search / login) | User Testing | User | エラーページからの脱出ボタン |
| C7.5 | サポート用相関 ID 表示 | Claims | Claims | エラー画面に traceId / requestId |
| C7.6 | エラーログに PII を出さない | Risk Testing | Statutes (GDPR) | サーバログ確認 (E2E では難。bug DB に注記) |

## 8. console / pageerror / network 自動 FAIL

各 testcase 実行中、以下が **1 件でも検出されたら無条件 FAIL**:

| # | 検出 | 自動収集 |
|---|-----|---------|
| C8.1 | `pageerror` (uncaught exception) | runner が exception text を bug report に添付 |
| C8.2 | `console.error` レベル | runner が message を bug report に添付 |
| C8.3 | 5xx HTTP responses | trace network log に記録 |
| C8.4 | resource load failure (404 css/js/img) | console から検出 |

例外: 既知の許容パターンは `config.yaml` の `tolerated_console_errors: [...]` に登録 (理由コメント必須).

## 9. 観察してログのみ取る項目 (FAIL にしない)

| # | 観点 | 理由 |
|---|------|------|
| C9.1 | 3rd party (analytics / chat widget) からのコンソール警告 | 自社制御外 |
| C9.2 | DevTools experimental warning | ブラウザ内部 |

## 10. 追加すべきプロジェクト固有チェック

pytest テスト関数の最後に `expect(...).not_to_contain_text(...)` を並べる (web-first):

```python
# フレームワーク fatal
expect(page.locator("body")).not_to_contain_text("Fatal error")
# ローカライズされた 404 表示
expect(page.locator("body")).not_to_contain_text("Page not found")
```

ページ全体に対する正規表現マッチではなく、locator + 部分一致で判定する。
`Warning:` / `Notice:` のような先頭領域限定の判定が必要なら
`locator: { css: "main, .page-top" }` で範囲を絞る。

## 参考文献

- W3C, "WCAG 2.2", https://w3c.github.io/wcag/requirements/22/
- web.dev Web Vitals, https://web.dev/articles/vitals
- OWASP Top 10:2025, https://owasp.org/Top10/2025/
- OWASP WSTG, https://owasp.org/www-project-web-security-testing-guide/
- Hendrickson Cheat Sheet (Interruptions), https://www.ministryoftesting.com/articles/test-heuristics-cheat-sheet
