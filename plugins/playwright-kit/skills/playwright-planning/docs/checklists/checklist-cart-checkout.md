# Checklist: `cart` / `checkout` — カート / チェックアウト / 決済

## 適用条件
商品行 + 数量 + 合計 + 配送 + 支払い + 確認 + 完了.

代表 URL: `/cart`, `/checkout`, `/checkout/payment`, `/order/confirm`, `/order/{id}`.

## 必須テスト観点

### CK1: カート Count `[EP / Hendrickson Count]`
- 0 件 (空カート) → 「カートは空です」+ 商品検索 CTA
- 1 件 (単数表示)
- max 個数 (在庫上限 / システム上限)
- 在庫超過 (`requested > stock`) → 警告 + max 値に丸め

### CK2: 価格再計算 `[Decision Table / Product]`
入力分岐の決定表:

| 数量変更 | クーポン | 送料無料閾値 | 期待挙動 |
|---------|---------|-------------|---------|
| ✓       | -       | -           | 即時再計算 (loading 表示) |
| -       | 適用    | -           | 割引表示 + 合計再計算 |
| -       | -       | 超過        | 送料 0 表示 |
| -       | 適用    | 適用後超過   | 送料 0 + クーポン両方反映 |

### CK3: 通貨 / 税率 / 表示 `[Domain / Statutes]`
- 通貨選択 (USD/JPY/EUR) で全価格再計算
- 税込 / 税抜表示 (locale 別 JP は税込、US は税抜が多い)
- 軽減税率 / 標準税率の混在
- 端数処理 (切捨/四捨五入) ポリシー一致

### CK4: クーポン `[Decision Table / Claims]`
- 有効期限内
- 期限切れ → エラー
- 併用不可フラグ → 既適用との競合
- 最低金額未達 → 「あと N 円で適用可能」
- ユーザ単位上限 → N 回目以降エラー
- 大文字小文字 / 全半角の許容

### CK5: 総額 0 円 / 総割引が総額超過 `[BVA / Edge case]`
**重要 edge case**.
- 100% off クーポンで合計 0 円 → 決済 step skip / 即完了 (仕様による)
- 割引が商品代を超過 → 0 円で確定 (マイナスにしない)
- ポイント全額利用 + クーポン併用

### CK6: 在庫変動 (Multi-user) `[Multi-user / Reliability]`
- カートに入れた商品が他ユーザに買い切られる
- checkout 直前のチェック: 「在庫切れ」 + 削除 / 数量調整選択
- 競合状態でも 二重販売しない (DB トランザクション or pessimistic lock)

### CK7: リロードでカート保持 `[State / User]`
- ログイン前に商品追加 → ログイン後にカート merge
- ログアウト → 別ユーザログイン: ゲストカートの扱い (引き継ぐ / 破棄)
- 別タブで同じユーザがカート操作 → 同期

### CK8: 住所バリデーション `[Domain / Statutes]`
- 郵便番号 → 自動補完 (国別: JP `123-4567`, US `12345-6789`, UK `SW1A 1AA`)
- 海外住所: state / province / region の必須化
- PO Box 拒否 (配送業者制限)
- 同一住所でも 文字種違い (全角/半角) で別住所扱いされない

### CK9: クレジットカード validation `[BVA / Statutes / PCI]`
- Luhn check (ブラウザ側 `<input pattern>` + サーバ側)
- 期限切れ (前月)
- CVV 3 桁 (Visa/MC) / 4 桁 (Amex)
- 3D Secure challenge (フェイク環境で OK)
- 失敗 → エラー表示 + 別カード再試行
- **PCI DSS**: カード番号を JS log / sentry breadcrumb / E2E trace に出さない (検査必須)

### CK10: 部分支払い `[Decision Table / Functional]`
- gift card + credit card (ハイブリッド)
- ポイント部分使用
- 残額 0 円なら gift card のみで完了

### CK11: 完了画面リロードで二重課金しない `[State / Reliability]`
- POST/Redirect/GET パターン
- idempotency key (Stripe 等の API 仕様準拠)
- リロードしても order id が変わらない

### CK12: 注文確認メール `[Functional / i18n]`
- 送信成功
- 件名 / 本文の i18n
- 添付 PDF (領収書) の内容検証
- メール内リンクの正当性 (token 付き / 期限)

### CK13: ネットワーク中断 `[Interruptions / Reliability]`
- 決済 API 呼出中に offline
- 「決済処理中エラー」 + 再開可能
- DB に「決済中」状態のレコードが残る → 監視で回収

### CK14: PCI DSS 関連 `[Statutes / Risk]`
- カード番号入力フォームは Iframe (PCI scope 削減 — Stripe Elements 等)
- E2E trace に PAN (カード番号) が含まれていないことを確認
- console / network logs で PAN 検出を grep

### CK15: 注文確認 URL の IDOR `[Risk / Statutes]`
- alice の注文確認 URL `/order/123` を bob が踏む → 403 / 404
- token 付き URL `/order/123?token=...` で他人がアクセス不可
- 注文番号がシーケンシャルでない (推測困難)

### CK16: キャンセル / 返品フロー `[State / Functional]`
- キャンセル可能期限 (発送前)
- キャンセル後の在庫戻し
- 返品申請 → 配送ラベル生成 → 返金

### CK17: 配送選択 `[Domain / Decision Table]`
- 標準 / 速達 / 店舗受取 / 翌日配送 (時間指定)
- エリア外の配送方法は選択肢から消える
- 送料が選択に応じて再計算

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| Decision Table | CK2, CK4, CK10, CK17 |
| BVA | CK1, CK5 (金額境界), CK9 (CVV 桁数) |
| State Transition | CK11 (cart→checkout→paid→shipped→delivered/cancelled), CK16 |
| Multi-user | CK6 |
| Risk | CK13, CK14, CK15 |
| Interruptions | CK13 |
| Domain | CK3, CK8, CK17 |

## Playwright 実装パターン

```python
# CK14: PCI DSS — trace.zip にカード番号がないか確認
# テスト終了時に
import zipfile
import re
with zipfile.ZipFile("trace.zip") as zf:
    for name in zf.namelist():
        content = zf.read(name).decode("utf-8", errors="ignore")
        # 16 桁数字パターン (テストカード番号など)
        if re.search(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b", content):
            pytest.fail(f"PAN detected in trace: {name}")

# CK11: 完了画面リロード
page.goto(complete_url)
page.reload()
expect(page).to_have_url(complete_url)  # 完了画面のまま
expect(page.get_by_text("ご注文ありがとうございます")).to_be_visible()
order_count_after = api.get("/api/orders").json()["count"]
assert order_count_before + 1 == order_count_after  # 二重登録なし
```

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y form), C3 (sec — IDOR / CSRF / open redirect), C6 (offline), C7 (4xx 5xx), C8 (console — 特に PAN 検出).
`form` checklist の FM1〜FM10 も準用 (checkout は複数ステップ form).
`auth` checklist (購入時のゲスト → 登録 / ログインフロー).

## 参考文献

- PCI DSS v4.0
- Stripe API "Idempotent requests"
- testvox "E-commerce checkout testing"
- OWASP ASVS V8 (Data Protection), V9 (Communication)
- ISO/IEC/IEEE 29119-3 § Test Items (Order entity)
