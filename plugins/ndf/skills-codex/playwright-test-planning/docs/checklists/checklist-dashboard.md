# Checklist: `dashboard` — ダッシュボード

## 適用条件
KPI カード + 複数チャート + 期間 / dimension フィルタ + drill-down リンク + エクスポート.

代表 URL: `/dashboard`, `/analytics`, `/reports`, `/admin`.

## 必須テスト観点

### DB1: API レスポンスとチャート表示の数値一致 `[Product / Claims]`
- 各 widget の API 完了後にチャート要素を読み取る
- API の `total` と画面表示の合計が一致
- 各 widget の合計を足し合わせると親 KPI と一致 (内部一貫性)
- `expect_response("**/api/widgets/...")` で取得 → DOM 値と比較

### DB2: 期間フィルタの全 widget 同期 `[State / Product]`
- 期間 (今日 / 今週 / 今月 / カスタム範囲) を切替
- すべての widget が新期間で再計算
- ロード中の skeleton 表示 + 完了通知

### DB3: Count 三値 (期間別) `[EP / Hendrickson Count]`
- 0 件期間 (新サービス / 古い期間)
- 1 件期間 (境界)
- 大量データ期間 (>10 万行)
各で widget が崩壊しない.

### DB4: タイムゾーン `[Domain / Statutes]`
- UTC / ユーザローカル / サーバローカル のうち、どれが基準か仕様で定義
- 期間境界 (例: 月初 0:00 JST) で正しく区切る
- DST 切替日 (米国 3 月 2 週日) の挙動
- 日付表示は `Intl.DateTimeFormat` で locale 別

### DB5: drill-down `[Product / User]`
- KPI カードクリック → 詳細画面 (or 詳細表)
- 詳細の数値が KPI と一致
- 戻るリンクで dashboard 状態 (期間 / フィルタ) 復元

### DB6: キャッシュ TTL と「リアルタイム」表記 `[Claims / Reliability]`
- 「リアルタイム」と表記しているなら 5 秒以内更新
- 「直近 1 時間」なら 1 時間以内更新
- データ更新時刻の表示 (`as of 2026-04-25 14:32:01 JST`)
- 自動リフレッシュ (5 分間隔等) があれば確認

### DB7: a11y (チャート) `[Statutes / User]`
- グラフの代替表示 (テーブル切替) があるか
- 色のみで情報を伝えない (パターン / ラベル併用)
- スクリーンリーダーが KPI 値を読み上げる (`aria-label` + `role="img"` + `aria-describedby`)
- キーボード操作で全 widget へ到達

### DB8: 色覚多様性 `[Statutes / User]`
- Protanopia / Deuteranopia / Tritanopia シミュレーション (`page.emulate_media` 拡張 or プラグイン)
- 凡例が色のみに依存しない (パターン or テキスト)
- コントラスト比 ≥ 4.5:1

### DB9: テーブル代替表示 `[Statutes / User]`
- チャート → テーブル切替ボタン
- テーブルにアクセスできる (a11y) → スクリーンリーダー対応

### DB10: エクスポート `[Functional / Product]`
- CSV / Excel / PDF / PNG 各形式
- 内容が画面と一致 (フィルタ条件反映)
- 大量データの DL タイムアウト
- 文字化け (UTF-8 BOM)

### DB11: 並行ロード時の race `[Multi-user / Reliability]`
- フィルタ A 即フィルタ B (ロード完了前)
- 結果が「最後に指定した B」になる (race 解決)
- 古いリクエストのレスポンスで上書きされない (`AbortController` or リクエスト ID)

### DB12: 数値の単位 / 桁区切り / 通貨 i18n `[Domain / Statutes]`
- `1,234.56` (en-US) vs `1.234,56` (de-DE) vs `1 234,56` (fr-FR)
- 通貨記号: `$1,234` / `1.234 €` / `¥1,234`
- 大きい数値: `1.2K`, `1.5M`, `3.4B` の単位丸め

### DB13: フィルタ組合せ (Pairwise) `[Pairwise]`
期間 × dimension × フィルタ × チャート種が ≥ 3 次元なら All-Pairs.

### DB14: 認可 (見せていい widget) `[Risk / Statutes]`
- 一般ユーザに admin 専用 widget が表示されない
- API 直接 `GET /api/admin-metrics` → 403
- データの粒度制御 (組織単位 / 部署単位 / 個人単位)

### DB15: empty state per widget `[EP / User]`
- 各 widget が独立して空状態 UI
- 「データがありません」+ CTA (期間変更 / データ追加)

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| Domain Testing | DB4 (TZ), DB12 (i18n) |
| State Transition | DB2 (フィルタ遷移) |
| Pairwise | DB13 |
| Claims | DB1, DB6, DB10 |
| Multi-user | DB11 |
| Risk | DB14 |
| EP / Count | DB3, DB15 |

## Playwright 実装パターン

```python
# DB1: API と DOM の数値一致
with page.expect_response("**/api/dashboard/revenue") as info:
    page.goto("/dashboard")
api_total = info.value.json()["total"]
dom_total = float(page.get_by_role("region", name="売上").get_by_test_id("total").inner_text().replace(",", ""))
assert api_total == dom_total, f"API={api_total} DOM={dom_total}"

# DB11: race 解決
page.get_by_role("button", name="今週").click()
page.get_by_role("button", name="今月").click()  # 即時切替
# 期待: 「今月」のデータで描画される
expect(page.get_by_test_id("active-period")).to_have_text("今月")
```

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y, **C1.7 色のみ**), C2 (perf, dashboard 重い), C8 (console error).

## 参考文献

- UK Gov Analysis Function "Dashboard testing", https://analysisfunction.civilservice.gov.uk/policy-store/data-visualisation-testing-dashboards-for-design-and-accessibility/
- A11Y Collective "Accessible charts"
- W3C ARIA APG "Live Region" (auto refresh)
