# Checklist: `item` — 詳細ページ

## 適用条件
URL に `/{resource}/{id}` パターン。単一エンティティ表示。
編集 / 削除 / 一覧へ戻る / 関連リンク / breadcrumbs.

代表 URL: `/items/123`, `/users/u_abc`, `/orders/o_xyz`.

## 必須テスト観点

### IT1: 存在しない id `[Domain / Claims]`
- `/items/999999` (大きい数値)
- `/items/0` / `/items/-1` (境界値)
- `/items/abc` (型不一致)
- `/items/<script>alert(1)</script>` (injection)
- 期待: 404 ステータス + 専用 404 ページ (status 200 で擬似 404 にしない)
- `expect(page.locator("h1")).to_contain_text("見つかりません")`

### IT2: 削除済み id `[State / Claims]`
- 一度削除した id を再アクセス → 410 Gone or 404 (仕様準拠)
- 「削除されました」メッセージ + 復元 / 一覧導線

### IT3: 他ユーザ所有の id (IDOR) `[Risk / Statutes]`
**Severity S1 候補**. OWASP Top 10 A01.
- alice でログイン → `/items/789` (= bob 所有) を直接 URL
- 期待: 403 Forbidden / または 404 (情報漏洩防止のため 404 で隠す方針もあり、仕様確認)
- 編集 / 削除ボタンは表示されない or 押下で 403

### IT4: 形式不正 id `[Domain / Statutes]`
- 大きすぎる id (Int max+1 / UUID 不正)
- SQL injection 試験: `1; DROP TABLE items;--`
- path traversal: `../../etc/passwd`
- いずれも 400 / 404 で早期返却

### IT5: 戻るリンクの状態復元 `[State / User]`
- 一覧 → 詳細 → 「戻る」リンク
- 一覧の フィルタ / ソート / ページ番号が復元される
- ブラウザ「戻る」ボタンでも同じ挙動

### IT6: 編集 / 削除権限の制御 `[Risk / Statutes]`
- read-only ユーザ: 編集 / 削除ボタンが**非表示** (`expect(...).to_be_hidden()`)
- 自分のデータ: 編集 / 削除ボタン表示
- 直接 `/items/123/edit` URL を踏ませた場合: 403 Forbidden
- API 直接 `PATCH /items/123` も同様に 403

### IT7: 関連リソース (画像 / PDF / 動画) `[Functional / Reliability]`
- リソース URL の死活 (200 / 404)
- 大ファイルの遅延ロード (lazy)
- 画像 fallback (broken image icon ではなく適切な代替)
- PDF / 動画は `<iframe>` or `<object>` で表示 → MIME type 検証

### IT8: 表示値の型保持 `[Domain / Product]`
- 日付: ユーザ TZ で正しく表示 (UTC ↔ JST 変換ミスがないか)
- 数値: 桁区切り (1,234 / 1.234 / 1 234) の locale 切替
- 通貨記号: ¥ / $ / € の位置 (前後)
- 数量単位: kg / L / mm の正しい表示

### IT9: SEO / ブックマーク `[Claims / Statutes]`
- `<title>` がエンティティ名を含む (`{title} — Site Name`)
- `<link rel="canonical" href="/items/123">` (重複防止)
- `<meta property="og:title" og:image>` で SNS シェア対応
- 構造化データ (Product / Article / etc.) `<script type="application/ld+json">`

### IT10: 削除後の挙動 `[State / Functional]`
- 詳細から削除 → 一覧へ自動遷移
- 一覧から該当行が消える
- 削除直後にブラウザ「戻る」 → 「削除済み」メッセージ (キャッシュ汚染防止)

### IT11: 編集後の表示反映 `[State / Product]`
- 編集して保存 → 詳細に戻る (or 同画面で更新)
- 表示値が編集内容と一致
- 一覧に戻る → 一覧の表示も同期

### IT12: 共有 URL / クリップボード `[Functional / User]`
- 「URL コピー」ボタンがあれば押下 → クリップボード検証 (`page.evaluate("navigator.clipboard.readText()")`)
- 共有された URL を別ユーザが踏むと適切な認可

### IT13: 関連エンティティへのナビ `[Product / User]`
- 「同じカテゴリの他商品」「関連記事」リンクの整合性
- リンク先 id が実在
- 自エンティティへの自己参照リンクがない

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| Domain Testing | IT1, IT4 (id partition) |
| Risk Testing | IT3, IT6 (IDOR / 認可) |
| State Transition | IT2 (active → deleted), IT10, IT11 |
| Claims Testing | IT9 (SEO 主張), IT8 (型保持仕様) |
| User Testing | IT5 (戻る期待), IT12 |

## Playwright 実装パターン

```python
# IDOR テスト (alice で bob のデータ id にアクセス)
page.goto(f"/items/{bob_owned_item_id}")
expect(page.locator("body")).to_contain_text("403")  # or 404
expect(page.get_by_role("button", name="編集")).to_be_hidden()

# 直接編集 URL
response = page.goto(f"/items/{bob_owned_item_id}/edit")
assert response.status == 403

# API 直接
api = playwright.request.new_context(base_url=base_url, storage_state="alice.json")
resp = api.patch(f"/api/items/{bob_owned_item_id}", data={"name": "hacked"})
assert resp.status == 403
```

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y, h1 単一), C3 (sec, IDOR / XSS sanitize), C7 (404), C8 (console).

## 参考文献

- OWASP WSTG IDOR, https://owasp.org/www-project-web-security-testing-guide/
- OWASP ASVS V8.2.5 Authorization
- RFC 9110 HTTP Semantics
- Schema.org Product / Article
