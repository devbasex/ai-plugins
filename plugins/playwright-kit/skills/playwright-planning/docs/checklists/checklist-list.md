# Checklist: `list` — 一覧ページ

## 適用条件
同一構造の繰り返し要素 (table / card grid / feed) + ページャ or 無限スクロール + ソート/フィルタ + 各行の詳細リンク.

代表 URL: `/items`, `/users`, `/orders`, `/posts`, `/products`.

## 必須テスト観点

### LST1: Count 三値 (Hendrickson Count) `[EP / Product]`
**最重要**。3 つの状態を必ず別 testcase で検証する。
- **0 件**: 空状態 UI が表示。「データがありません」+ 新規作成 CTA。`expect(page.get_by_text("データがありません")).to_be_visible()`
- **1 件**: 単数表示 / "1 件のうち 1 件" 表記。空状態 UI が出ない
- **多数 (>1000)**: 性能維持 (INP ≤ 200ms)。スクロール / ページネーション動作

### LST2: ページネーション境界 `[BVA / Claims]`
- `?page=0` (out-of-range) → 1 ページ目に丸めるか 404
- `?page=1` (有効最小)
- `?page=last` (有効最大)
- `?page=last+1` → 0 件 + 適切 UI
- `?page=-1` / `?page=abc` / `?page=99999999` → 入力検証
- 件数表示 `1〜20 件 / 全 156 件` のフォーマット
- 最終ページが半端 (例: 156 件で 20/page → 8 ページ目は 16 件)

### LST3: ソート `[Domain / Product]`
- 昇順 / 降順 / null 含む / 同値タイブレーク
- i18n 並び (例: 「ﾐ」「み」「ミ」が同列か)
- 数値 / 文字列 / 日付の型ごとの正しい比較
- ソート列ヘッダーに `aria-sort="ascending|descending|none"`
- ソート後の URL 状態保持 (`?sort=name&order=desc`)

### LST4: フィルタ組合せ (Pairwise) `[Pairwise / Domain]`
- フィルタ次元 ≥ 3 なら All-Pairs で組合せ削減
- 各フィルタの適用 → 件数減少 (filter-monotone)
- 全フィルタ解除 → 元の件数
- フィルタクリアボタン

### LST5: 行操作 (CRUD per row) `[CRUD / Functional]`
- 編集リンク: `get_by_role("row").filter(has_text=対象).get_by_role("link", name="編集")` → 正しい id で /items/{id}/edit へ
- 削除ボタン: 確認 dialog → 削除 → 一覧から消える
- 詳細リンク: `/items/{id}` へ

### LST6: 削除後の即時反映 `[State / Product]`
- 削除 → 一覧件数 -1
- 総件数表示も同期 (例: "全 156 件" → "全 155 件")
- ページャ最終ページで全削除 → 1 ページ手前に遷移

### LST7: 検索クエリ injection `[Risk / Statutes]`
- `'`, `<script>alert(1)</script>`, `' OR '1'='1`, `%27`, `<NUL>`
- いずれも本文に文字列として表示され、JS 実行されない
- DB エラーが画面に出ない

### LST8: 大量データ性能 (Stress) `[Stress / Performance]`
- >10000 行投入 → INP ≤ 200ms
- スクロール時の jank 計測 (`PerformanceObserver` で long task 検出)
- メモリリーク (10 ページ繰り返し → ヒープサイズ計測)

### LST9: 並行更新 (Multi-user) `[Multi-user / Reliability]`
- ユーザ A が編集ダイアログ開いている間に B が削除
- A が保存 → 「対象が存在しません」エラー (404 or specific)
- A が削除 → B のリストにも反映 (リアルタイム or 次更新)

### LST10: URL クエリパラメータ復元 `[State / User]`
- フィルタ + ソート + ページを設定
- リロード → 状態復元
- ブラウザ「戻る」→ 1 つ前の状態
- URL コピー → 別ブラウザで同じ状態を再現

### LST11: 一覧 ↔ 詳細往復 `[State / User]`
- 一覧 → 詳細 → 戻る で:
  - スクロール位置保持
  - 選択行ハイライト保持
  - フィルタ条件保持

### LST12: 空文字列検索 / 全件マッチ / 0 件マッチ `[Domain / Claims]`
- `?q=` (空) → 全件 or 検索無効化
- `?q=*` (ワイルドカード) → 全件
- `?q=明らかに存在しない文字列` → 0 件 + "該当なし" メッセージ + 検索 box 残存

### LST13: テーブル a11y `[Statutes / Automatic]`
- `<table>` には `<caption>` または `aria-label`
- `<thead><tr><th scope="col">` 構造
- `aria-rowcount` / `aria-colcount` (仮想スクロール時)
- `aria-sort` (ソート列)
- 行のキーボード操作 (Up/Down/PageUp/PageDown/Home/End)

### LST14: CSV / Excel エクスポート `[Domain / Product]`
- 文字化け (UTF-8 BOM 有無 / Shift_JIS 必要時)
- 改行 / カンマ / ダブルクォート / 絵文字を含むデータの正しい escape
- 大量行 (>10万) のダウンロード可否
- フィルタ後のエクスポート = フィルタ条件反映

### LST15: 認可 (IDOR) `[Risk / Statutes]`
- ユーザ A の URL `?team=alice` を B が踏む → B 自身のデータのみ
- 削除権限のないユーザに削除ボタン非表示 (`expect(...).to_be_hidden()`)
- 一覧 API を直接叩く (`request.get`) → 403 or 自分のデータのみ

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| EP | LST1 (Count) |
| BVA | LST2 (ページ番号), LST8 (件数) |
| Domain Testing | LST3 (ソート値), LST7 (injection) |
| Pairwise | LST4 (フィルタ組合せ) |
| State Transition | LST6, LST11 (一覧 → 詳細 → 戻る) |
| Risk Testing | LST15 (IDOR) |
| Stress Testing | LST8 |
| Multi-user | LST9 |

## Playwright 実装パターン

```python
# 行絞り込み
row = page.get_by_role("row").filter(has_text="Product 2")
row.get_by_role("button", name="削除").click()
expect(page.get_by_role("dialog")).to_be_visible()
page.get_by_role("button", name="削除を確定").click()
expect(row).to_be_hidden()

# 件数
expect(page.get_by_role("row").nth(0)).to_be_visible()  # ヘッダ
expect(page.get_by_role("row")).to_have_count(21)  # ヘッダ + 20 行

# ソート
page.get_by_role("columnheader", name="価格").click()
expect(page.get_by_role("columnheader", name="価格")).to_have_attribute("aria-sort", "ascending")
```

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y, **特に C1.4 見出し階層 / C1.6 label**), C2 (perf), C3 (sec — IDOR / SQL inj), C8 (console error 自動 FAIL).

## 参考文献

- Hendrickson Cheat Sheet "Count" / "Sorting" / "Position", https://www.ministryoftesting.com/articles/test-heuristics-cheat-sheet
- OWASP WSTG IDOR
- W3C ARIA Authoring Practices "Grid Pattern", https://www.w3.org/WAI/ARIA/apg/patterns/grid/
