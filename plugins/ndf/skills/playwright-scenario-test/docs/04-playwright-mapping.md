# 04. Playwright API → page role / 観点 マッピング

「経験で API を選ぶ」のではなく、**page role × 観点 → 使う Playwright API** を一意に固定するための表。
本 Skill のロケーター戦略・assertion 戦略・debug ツール選択は本書に従う。

## 1. ロケーター優先順位 (公式推奨)

ユーザの操作意図を反映する a11y セマンティクスを最優先。
**この順序を守ることがスキル全体の根幹。** CSS/XPath は禁忌。

| 優先度 | API (Python) | 用途 | コメント |
|---|---|---|---|
| 1 | `page.get_by_role(role, name=)` | 主要操作要素 (button/link/heading/textbox/listitem/dialog/tab/row/cell ...) | a11y role + accessible name |
| 2 | `page.get_by_label(text)` | フォーム要素 (`<label for>` / `aria-labelledby`) | フォーム第一選択 |
| 3 | `page.get_by_placeholder(text)` | label のない検索 box 等 | label 推奨だが placeholder のみのケース |
| 4 | `page.get_by_text(text)` | 表示文字列 (主にアサーション) | 操作には不安定な場合あり |
| 5 | `page.get_by_alt_text(text)` | 画像 | |
| 5 | `page.get_by_title(text)` | `title` 属性 | |
| 6 | `page.get_by_test_id(id)` | `data-testid` | 本番 DOM 変動を吸収する最終手段 |
| 7 | `page.locator(css_or_xpath)` | 上記で取れない場合のみ | **使用時はコメントで理由を明記** |

絞り込み・連結:

```python
# filter
locator.filter(has_text="...", has_not_text="...", has=..., has_not=...)
# 位置
locator.first / locator.last / locator.nth(i)
# AND / OR
locator.and_(...)  # 両方マッチ
locator.or_(...)   # どちらかマッチ
# チェイン
page.get_by_role("listitem").filter(has_text="Product 2") \
    .get_by_role("button", name="Add to cart").click()
# iframe
page.frame_locator("#my-iframe").get_by_role("button", name="Sign in").click()
# trace 用ラベル (1.50+)
locator.describe("Subscribe button")
```

## 2. Page role × ロケーター マトリクス

| page role | 主要要素 | 第一優先 | 第二優先 | フォールバック |
|---|---|---|---|---|
| `lp` | hero / nav / CTA | `get_by_role("link"/"button", name=...)` | `get_by_text` | testid |
| `list` | table / card / row | `get_by_role("row"/"listitem").filter(has_text=...)` | `get_by_role("rowheader"/"cell")` | nth |
| `item` | 見出し / アクションボタン | `get_by_role("heading", level=1)` + `get_by_role("button", name=...)` | label | testid |
| `edit` | 個別入力 + 保存ボタン | `get_by_label(...)` → `get_by_role("button", name="保存")` | placeholder | testid |
| `form` | 多項目入力 + step | `get_by_label` 一択 + `get_by_role("textbox", name=...)` | placeholder | testid |
| `search` | 検索 box / 結果 | `get_by_role("searchbox")` または `get_by_placeholder` | label | css |
| `dashboard` | region scope + widget | `get_by_role("region", name=...)` でスコープ → 内部 role | testid | text |
| `auth` | login / sign-in | `get_by_label("Email" / "Password")` + `get_by_role("button", name="Sign in")` | placeholder | testid |
| `cart` | 行 + 数量 + 削除 | `get_by_role("listitem").filter(has_text=商品名).get_by_role("button", name="削除")` | role=row | testid |
| `checkout` | 段階 region | `get_by_role("region", name="配送先")` でスコープ → label | placeholder | testid |
| `wizard` | step + Next | `get_by_role("button", name="次へ"/"Next")` + `get_by_role("status")` | text | testid |
| `modal` | dialog scope | `get_by_role("dialog", name=...)` → 内部 role | text | testid |

## 3. Assertion 戦略 (web-first assertion)

`time.sleep` / `wait_for_selector` / `locator.is_visible()` は禁止。
すべて `expect(...)` で auto-retry させる。

| 検査内容 | API | 例 |
|---|---|---|
| 要素表示 | `expect(locator).to_be_visible()` | save ボタン表示 |
| 非表示 | `expect(locator).to_be_hidden()` | エラー表示が消える |
| テキスト | `expect(locator).to_have_text(...)` | エラーメッセージ完全一致 |
| 部分一致 | `expect(locator).to_contain_text(...)` | 件数表示 |
| 個数 | `expect(locator).to_have_count(N)` | 一覧件数 |
| 値 | `expect(locator).to_have_value(...)` | input の値 |
| URL | `expect(page).to_have_url(...)` | 遷移先 |
| タイトル | `expect(page).to_have_title(...)` | SEO |
| 属性 | `expect(locator).to_have_attribute(...)` | aria-disabled |
| クラス | `expect(locator).to_have_class(...)` | active 状態 |
| ARIA snapshot | `expect(locator).to_match_aria_snapshot(...)` | 構造のリグレッション |
| Screenshot | `expect(page).to_have_screenshot(...)` | visual regression |

`timeout`, `useInnerText` 等のオプションは config 1 箇所で管理。

## 4. Network / API 操作

「画面で値を確認する」を「API レスポンスを直接アサート」に置き換え、UI flakiness を排除する。

| 操作 | API | 適用例 |
|---|---|---|
| リクエスト傍受 / モック | `page.route(url, handler)` | 条件で `route.fulfill / abort / continue` |
| HAR 録画 | `context.route_from_har(path, update=True, update_mode="minimal")` | 録画時に固定 fixture 化 |
| HAR 再生 | `context.route_from_har(path, update=False, not_found="abort")` | 安定化 |
| レスポンス待機 | `with page.expect_response(predicate) as info: ...` | API 完了 → 画面アサート |
| API テスト | `playwright.request.new_context(base_url=..., extra_http_headers=...)` | UI を介さず login / seed |
| 画像/3rd party 遮断 | `page.route("**/*.{png,jpg}", lambda r: r.abort())` | 速度向上 |

### page role × Network 戦略

| role | 推奨 | 理由 |
|---|---|---|
| `lp` | 画像 / tracker abort | 速度向上 / 外部依存除去 |
| `list` / `dashboard` | `expect_response` で API 完了待機 | DOM 待機より早く確実 |
| `auth` | `request.new_context().post("/login")` で `storage_state` 化 | 全 testcase で再利用 |
| `search` | `expect_response` でクエリ検証 | "0 件" もアサート可 |
| `edit` / `form` | submit を `route.fulfill` で固定 422 化 | エラーパスを安定化 |

## 5. デバッグ / 不具合調査支援

### codegen — 録画→コード生成

```bash
# 基本
playwright codegen https://example.com

# Python sync 出力
playwright codegen --target python ...

# device emulation
playwright codegen --device "iPhone 13" example.com

# 認証 storage 保存
playwright codegen --save-storage=auth.json example.com

# 認証済み状態から再開
playwright codegen --load-storage=auth.json example.com
```

本 Skill では `scripts/record_scenario.py` がラッパーを提供し、codegen の Python 出力をそのまま pytest テスト関数として利用する。
**「経験で書く」を「録画→生成→pytest テスト」に置換** する。

### Trace Viewer

```bash
# 失敗時のみ trace 残す (推奨)
pytest --tracing retain-on-failure

# trace を開く
playwright show-trace test-results/<test>/trace.zip

# リモート URL
https://trace.playwright.dev/?trace=<URL>
```

bug report に **trace.zip の playwright.dev リンク** を必ず付与する (`scripts/upload_evidence.py --kind trace --public`)。

### page.pause()

開発時のみ。CI には流さない。`headless=False` 必須。

### screenshot mask

```python
page.screenshot(
    path="...",
    full_page=True,
    mask=[page.locator(".clock"), page.get_by_test_id("avatar")],
    mask_color="#FF00FF",
    animations="disabled",
    caret="hide",
)
```

dashboard / list の動的領域 (時計 / 広告) を除外して visual regression を安定化.

## 6. アクセシビリティ / 性能 / 互換

### a11y — `axe-playwright-python`

```python
from axe_playwright_python.sync_playwright import Axe

def test_a11y(page):
    page.goto("/dashboard")
    axe = Axe()
    results = axe.run(page)
    assert results.violations_count == 0, results.generate_report()
```

公式 Locator の `to_match_aria_snapshot('''- heading "todos" - textbox ...''')` で a11y 構造のリグレッション検出も可能。pytest テストでは ``expect(page.locator('main')).to_match_aria_snapshot(...)`` のように直接使う。

### Core Web Vitals

```python
def measure_lcp(page):
    return page.evaluate("""() => new Promise(r => {
        new PerformanceObserver(list => {
            const e = list.getEntries().at(-1);
            r(e.startTime);
        }).observe({type:'largest-contentful-paint', buffered:true});
    })""")
```

`scripts/check_cwv.py` が LCP/INP/CLS を一括計測.

### モバイル / メディア

```python
iphone = playwright.devices["iPhone 13"]
context = browser.new_context(**iphone)         # viewport+UA+touch+DPR
page.emulate_media(color_scheme="dark", reduced_motion="reduce")
context.set_offline(True)
context.set_geolocation({"latitude": 35.68, "longitude": 139.69})
```

## 7. その他重要 API

| 機能 | API | 用途 |
|---|---|---|
| URL 待機 | `page.wait_for_url("/dashboard*", wait_until="networkidle")` | `expect_navigation` の代替 |
| Popup | `with page.expect_popup() as info: ...; popup = info.value` | OAuth / 印刷 |
| iframe | `page.frame_locator("#frame").get_by_*` | 決済 widget / YouTube |
| dialog | `page.on("dialog", lambda d: d.dismiss())` | confirm/alert 自動応答 |
| download | `with page.expect_download() as d: ...; d.value.save_as(path)` | CSV / PDF DL 検証 |
| console | `page.on("console", lambda msg: ...)` | JS エラー検出 |
| pageerror | `page.on("pageerror", lambda exc: ...)` | uncaught exception 自動 FAIL |
| storage_state | `context.storage_state(path)` / `new_context(storage_state=path)` | login 1 回, 全 test 再利用 |

### 必須リスナー (本 Skill 標準)

``pwk_evidence`` fixture が次のリスナーを自動付与する (autouse 相当):

```python
console_msgs = []
errors = []
page.on("console", lambda msg: console_msgs.append(msg))
page.on("pageerror", lambda exc: errors.append(str(exc)))
```

`pageerror` が 1 件でも検出されたら **そのテストは無条件 FAIL**。bug report に exception を添付.

## 8. Skill 設計判断: pytest-playwright vs `@playwright/test`

| 機能 | Python (`pytest-playwright`) | Node (`@playwright/test`) | 本 Skill 採用 |
|---|---|---|---|
| fixture | pytest fixture | `test.use({...})` | Python (既存資産) |
| web-first assertion | `expect(locator).to_be_visible()` | 同等 | 共通 |
| retry | `pytest --reruns N` | `retries: 2` | Python |
| 並列 | `pytest -n auto` | builtin worker | Python |
| trace | `--tracing retain-on-failure` | 同等 | Python |
| merge-reports | `pytest_terminal_summary` で集計 | builtin | Python (`playwright_kit/pytest_report.py`) |

**結論**: Python `pytest-playwright` を採用。理由は (1) overlay (旧名 HUD) / 字幕焼き込み / accessibility / web_vitals / Drive 連携を pytest plugin として一体化済み、(2) `def test_xxx(page, pwk_role_admin): ...` を直接書く設計のため pytest fixture / marker と相性が良い、(3) merge-reports は `pytest_terminal_summary` hook で同等機能を提供.

## 参考文献

- Playwright Python Docs, https://playwright.dev/python/docs/intro
- Playwright Locators, https://playwright.dev/python/docs/locators
- Playwright Mock APIs, https://playwright.dev/python/docs/mock
- Playwright Trace Viewer, https://playwright.dev/docs/trace-viewer
- Playwright Codegen, https://playwright.dev/docs/codegen
- Playwright API Testing, https://playwright.dev/python/docs/api-testing
- axe-playwright-python, https://github.com/pamelafox/axe-playwright-python
- web-vitals.js, https://github.com/GoogleChrome/web-vitals
