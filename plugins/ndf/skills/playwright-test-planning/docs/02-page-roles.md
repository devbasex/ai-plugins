# 02. Page Role 分類

ページを役割で分類する。**URL パターンや SEO 構造ではなく、ユーザの目的とテスト観点で分類** する。
1 ページが複数 role を兼ねる場合は両方の checklist を適用する (例: 一覧 + 検索)。

各 role には次の 4 セクションを定義する:
- **識別ヒューリスティック**: そのページが当該 role かを判定する特徴
- **代表 URL パターン**: よく見るパス
- **必須 checklist**: `docs/checklists/checklist-{role}.md` への参照
- **代表的 oracle**: FEW HICCUPPS のうち主に使う軸

## 全 role 一覧

| role | 短い説明 | 識別の主シグナル |
|---|---|---|
| `lp` | Landing Page (外部到達) | nav 中心 + CTA 多 + 機能 < 5 |
| `list` | 一覧 | 同一構造の繰り返し要素 + ページャ |
| `item` | 詳細 | URL に id / 単一エンティティ |
| `edit` | 編集 | プリフィル + save/cancel |
| `form` | 申込フォーム (複数ステップ) | progress indicator + 確認画面 |
| `search` | 検索 | search box + 結果 list + ファセット |
| `dashboard` | ダッシュボード | KPI カード + 複数チャート + 期間フィルタ |
| `auth` | 認証 | login form / logout / 2FA |
| `cart` | カート | 商品行 + 数量変更 + 合計 |
| `checkout` | チェックアウト | 配送/支払い段階 + 確認 |
| `modal` | モーダル | `role="dialog"` + 背景 overlay |
| `wizard` | ウィザード | step インジ + 戻る/次へ |
| `error` | エラーページ | 4xx/5xx + 復帰導線 |
| `settings` | 設定 / プロフィール | 個人設定の保存フォーム群 |

## 各 role 詳細

### `lp` — Landing Page (外部到達ページ)
**識別**: ドメインルート (`/`) または `/lp/*` `/campaign/*`。
ナビ + ヒーロー + 複数の説明セクション + CTA。SEO meta tag が充実。
ページ深度 1。機能リンク (form 等) は 1〜3 個に集中。

**代表 URL**: `/`, `/lp/2026-spring`, `/about`, `/pricing`, `/features`.

**必須 checklist**: [`checklists/checklist-lp.md`](checklists/checklist-lp.md)

**代表的 oracle**: Claims (主張), Image (ブランド), Statutes (a11y / GDPR バナー).

---

### `list` — 一覧ページ
**識別**: 同一構造の要素 (table / card / feed) が繰り返し描画される。
ページャ or 無限スクロール。ソート/フィルタ UI。各行に詳細リンク。

**代表 URL**: `/items`, `/users`, `/orders`, `/posts`, `/products`.

**必須 checklist**: [`checklists/checklist-list.md`](checklists/checklist-list.md)

**代表的 oracle**: Product (内部一貫性 — 件数と表示の一致), Statutes (テーブル a11y), Claims (フィルタ仕様).

---

### `item` — 詳細ページ
**識別**: URL に `/{resource}/{id}`。breadcrumbs。編集 / 削除 / 戻るリンク。

**代表 URL**: `/items/123`, `/users/u_abc`, `/orders/o_xyz`.

**必須 checklist**: [`checklists/checklist-item.md`](checklists/checklist-item.md)

**代表的 oracle**: Product (一覧との値一致), Statutes (IDOR / 認可), History (過去版で動いた操作).

---

### `edit` — 編集ページ
**識別**: 既存値プリフィル + Save / Cancel ボタン。dirty 検知 (`beforeunload`)。CSRF token。

**代表 URL**: `/items/123/edit`, `/items?Cmd=Edit&ItemID=123`.

**必須 checklist**: [`checklists/checklist-edit.md`](checklists/checklist-edit.md)

**代表的 oracle**: Claims (validation 仕様), Product (保存後の値整合), Statutes (CSRF / a11y errors).

---

### `form` — 申込フォーム (複数ステップ)
**識別**: 進捗インジ (Step 1/N) + 戻る/次へ + 確認画面 + 送信 + 完了画面。
入力分岐がある (国別 / 法人個人 / オプション)。**コードを読みながら Decision Table を作る対象**。

**代表 URL**: `/contact`, `/signup`, `/apply`, `/subscribe`.

**必須 checklist**: [`checklists/checklist-form.md`](checklists/checklist-form.md)

**代表的 oracle**: Claims (分岐ロジック仕様), Product (確認画面と送信値の一致), User (ステップ間ナビゲーション期待).

---

### `search` — 検索ページ
**識別**: search box + 結果 list + ファセット + 件数表示 + ハイライト + サジェスト.

**代表 URL**: `/search`, `/?q=...`, `/find`.

**必須 checklist**: [`checklists/checklist-search.md`](checklists/checklist-search.md)

**代表的 oracle**: Claims (relevance 順位), Product (件数と表示の整合), Statutes (XSS / SQL inj sanitize).

---

### `dashboard` — ダッシュボード
**識別**: KPI カード + 複数チャート + 期間/dimension フィルタ + drill-down.

**代表 URL**: `/dashboard`, `/analytics`, `/reports`, `/admin`.

**必須 checklist**: [`checklists/checklist-dashboard.md`](checklists/checklist-dashboard.md)

**代表的 oracle**: Product (合計と内訳の一致), Claims (リアルタイム表記), Statutes (色覚多様性 / a11y).

---

### `auth` — 認証
**識別**: email/username + password。SSO ボタン / Remember me / Forgot password / 2FA.

**代表 URL**: `/login`, `/signin`, `/register`, `/forgot-password`, `/auth/callback`.

**必須 checklist**: [`checklists/checklist-auth.md`](checklists/checklist-auth.md)

**代表的 oracle**: Statutes (OWASP ASVS / NIST 800-63B), Claims (パスワードポリシー), History (セッション再発行).

---

### `cart` / `checkout` — カート / 決済
**識別**: 商品行 + 数量 + 合計 + 配送 + 支払い + 確認 + 完了.

**代表 URL**: `/cart`, `/checkout`, `/checkout/payment`, `/order/confirm`.

**必須 checklist**: [`checklists/checklist-cart-checkout.md`](checklists/checklist-cart-checkout.md)

**代表的 oracle**: Claims (税/送料計算), Product (価格再計算と表示の整合), Statutes (PCI DSS), History (在庫変動).

---

### `modal` / `wizard` — モーダル / ウィザード
**識別**: `role="dialog"` + `aria-modal="true"` + overlay + close. wizard は内部に step.

**代表**: 削除確認 dialog, onboarding wizard, 設定 modal.

**必須 checklist**: [`checklists/checklist-modal-wizard.md`](checklists/checklist-modal-wizard.md)

**代表的 oracle**: Statutes (W3C ARIA APG dialog pattern), User (Esc キーで閉じる期待), Product (wizard 状態保持).

---

### `error` — エラーページ
**識別**: HTTP 4xx/5xx 応答。"Page not found" / "Server error" / "Maintenance".

**代表 URL**: 404 / 500 / 503 / 429 / 401 / 403.

**必須 checklist**: 一覧未整備 — checklists/checklist-common.md の「エラーハンドリング」節参照

**代表的 oracle**: Claims (status code 仕様), Product (実 status と画面の一致), Statutes (PII 露出禁止).

---

### `settings` — 設定 / プロフィール
**識別**: 個人 / 組織設定の保存フォーム群。アバター。退会導線。

**代表 URL**: `/settings/profile`, `/settings/notifications`, `/account`.

**必須 checklist**: 「edit」を流用 + checklists/checklist-common.md のセキュリティ節 (再認証要求).

**代表的 oracle**: Claims (即時反映表記), Statutes (OWASP ASVS V8 Data Protection), History (退会後のデータ扱い).

## 識別の自動化

`playwright-kit-ops/scripts/classify_page_role.py` は次のヒューリスティックで role を推定する:

```
入力: target_url, [既ログイン storage_state]
処理:
  1. page.goto(url)
  2. accessibility tree を抽出 (page.accessibility.snapshot())
  3. role 集計:
       - article >= 2 + listitem >= 2 → list 候補
       - role=heading lvl=1 + role=link >= 5 + role=button (CTA) → lp 候補
       - role=textbox + role=button name="Sign in"|"Login" → auth 候補
       - role=dialog → modal
       - role=textbox >= 3 + 進捗 (`step` text or aria-label) → form
       - role=table or role=grid → list (table 系)
       - getByLabel("Email") + getByLabel("Password") → auth
  4. URL pattern (id 含むなど) で補強
出力: 推定 role + 信頼度 + 代替候補
```

これにより、AI が「経験で role を決める」のではなく、a11y tree という客観的事実から決まる。

## 兼ね role の扱い

1 ページが複数 role を兼ねる場合 (例: 検索結果 = list + search):

```python
@pytest.mark.page_role("search", "list")
def test_search_result(page, pwk_role_user):
    ...
```

このとき各 role の checklist 全項目を走査する。重複項目は片方で OK。

## 参考文献

- James Bach, "Heuristic Test Strategy Model" v6.3 (Operations / Users)
- Nielsen Norman Group, "Page Types and Templates"
- W3C, "ARIA in HTML", https://www.w3.org/TR/html-aria/
- W3C WAI-ARIA APG, https://www.w3.org/WAI/ARIA/apg/patterns/
