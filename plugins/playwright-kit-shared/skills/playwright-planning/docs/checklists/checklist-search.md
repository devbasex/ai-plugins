# Checklist: `search` — 検索ページ

## 適用条件
search box + 結果 list + ファセット + 件数表示 + ハイライト + サジェスト.

代表 URL: `/search`, `/?q=...`, `/find`.

兼ね role: 検索結果ページは `list` も兼ねるため、`list` checklist も併用.

## 必須テスト観点

### SR1: zero results UX `[EP / User]`
**最重要**. 検索の 23%は 0 件 (UX 影響大).
- 「該当なし」メッセージ表示
- 検索 box に入力値が残る (再修正用)
- 提案 (typo 補正 / 関連キーワード)
- フィルタリセットボタン
- 全件表示への導線

### SR2: クエリ injection `[Risk / Statutes]`
- SQL: `' OR '1'='1`, `1; DROP TABLE--`, `UNION SELECT ...`
- NoSQL: `{"$gt": ""}`, `{"$ne": null}`
- XSS: `<script>alert(1)</script>`, `<img src=x onerror=alert(1)>`
- Command injection: `; cat /etc/passwd`
- いずれもエラーや想定外結果を返さない、画面に sanitize された文字列として表示

### SR3: 全マッチ / 空クエリ `[Domain / Claims]`
- `?q=` (空) → 全件 or 検索無効化
- `?q=*` (ワイルドカード) → 全件 or リテラル `*` 検索
- `?q=%20` (スペースのみ) → 0 件 or 全件 (仕様確認)
- `?q=長すぎる文字列 (> 1000 文字)` → 切り詰め or 422

### SR4: 部分一致 / 完全一致 / fuzzy `[Claims / Product]`
仕様で定義された検索方式を逐一検証.
- 部分一致: `q=appl` で `apple`, `application` がヒット
- 完全一致 (`"...."` で囲む等): `q="apple pie"` で完全一致のみ
- fuzzy: `q=aple` で `apple` がヒット (typo tolerance)
- 前方一致: `q=app*` で `apple`, `application` (suffix なし)

### SR5: 並び替え `[Domain / Claims]`
- relevance / 新着 / 人気 / 価格 (昇/降)
- 並び替え後の件数は変わらない
- 各並び順で先頭 10 件を取得 → 順序が仕様通り
- relevance 順位は「主張」(Claims) — 上位ほどキーワード一致度が高いか目視

### SR6: ファセット組合せ (Pairwise) `[Pairwise / Domain]`
ファセット ≥ 3 次元なら All-Pairs.
```
ブランド × カテゴリ × 価格帯 × 在庫 = 全 N 通り → All-Pairs で ~M 通り
```
各組合せで:
- 結果が必ず減少 (filter-monotone)
- 0 件 でも UI 崩壊しない
- ファセット解除で元件数

### SR7: ページネーション `[BVA / list 流用]`
list checklist LST2 を準用.
- out-of-range (`?page=99999`) で 0 件 + 適切 UI
- 検索条件 + ページ番号が URL に保持

### SR8: 同義語 / typo tolerance `[Claims / User]`
- 「シャツ」「しゃつ」「シャツ」(全角半角) で同じ結果
- 「Shirt」「shirt」「SHIRT」(case insensitive)
- 設定された同義語辞書: 仕様書 SPEC-SEARCH-001 と一致

### SR9: 検索ログのプライバシー `[Statutes / GDPR]`
- 検索クエリに PII (氏名 / メール) が含まれる場合の扱い
- ログ保管期間 / 匿名化 / 第三者送信 (Google Analytics など)
- E2E では確認困難 → bug DB に注記 / セキュリティチームに確認依頼

### SR10: 結果と詳細の値一致 `[Product]`
- 検索結果に表示される値 (タイトル / 価格 / 在庫) と詳細ページの値が一致
- インデックス遅延 (検索 vs 実 DB) の許容範囲

### SR11: ハイライト `[Functional / User]`
- 検索キーワードが結果中で `<mark>` ハイライトされる
- HTML エスケープ (XSS 防止)
- 大文字小文字を問わず

### SR12: サジェスト / オートコンプリート `[Functional / User]`
- `q=appl` の途中で候補表示
- 候補クリックで検索実行 (or 入力欄に補完)
- キーボード操作 (↑↓Enter) 対応 (a11y)
- `aria-autocomplete="list"` + `aria-controls`

### SR13: 高度検索 (advanced search) `[Functional / User]`
- 複数フィールド: タイトル + カテゴリ + 期間
- AND / OR / NOT 演算子
- フィールド指定 (`title:apple`)

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| EP | SR1 (zero / 1 / many), SR3 |
| Domain Testing | SR2 (clean/inj), SR3, SR8 |
| Pairwise | SR6 (ファセット) |
| Claims | SR4, SR5, SR8 |
| Risk | SR2, SR9 |
| BVA | SR7 |

## Playwright 実装パターン

```python
# zero results
page.goto("/search?q=明らかに存在しない検索語abc123")
expect(page.get_by_text(re.compile("該当なし|0 件"))).to_be_visible()
expect(page.get_by_role("searchbox")).to_have_value("明らかに存在しない検索語abc123")

# XSS sanitize
page.get_by_role("searchbox").fill("<script>alert(1)</script>")
page.get_by_role("button", name="検索").click()
# 期待: alert が出ない + 文字列として表示
expect(page.locator("body")).to_contain_text("<script>")

# expect_response でファセット結果検証
with page.expect_response("**/api/search?**brand=apple**") as info:
    page.get_by_role("checkbox", name="apple").check()
resp = info.value
data = resp.json()
assert data["count"] < page.original_count  # filter-monotone
```

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y, searchbox), C3 (XSS / SQL inj), C8 (console error).
`list` checklist の LST2 (ページネーション), LST7 (injection 観点) も準用.

## 参考文献

- Algolia "Search UX Best Practices"
- OWASP WSTG INPV (Input Validation)
- W3C ARIA APG "Combobox Pattern" (autocomplete)
- Nielsen Norman Group "Search Patterns"
