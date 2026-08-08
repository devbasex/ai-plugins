# Checklist: `form` — 申込フォーム (複数ステップ)

## 適用条件
進捗インジ (Step 1/N) + 戻る/次へ + 確認画面 + 送信 + 完了画面.
入力分岐がある (国別 / 法人個人 / オプション).

代表 URL: `/contact`, `/signup`, `/apply`, `/subscribe`.

> ⚠️ **重要**: form の入力分岐はコードを読んで Decision Table を作る対象。
> 経験で書かない。フォーム実装コード (HTML / JS / サーバ側 validator) を読んで分岐表を
> 自前で作り、その後 ``@pytest.mark.parametrize`` で各 row を test 関数として展開する。
> 4 軸以上は Python 標準の ``itertools`` + 簡易 Pairwise で削減した上で
> ``parametrize`` する運用を推奨。

## 必須テスト観点

### FM1: 各ステップの直接 URL アクセス `[State / User]`
- `/form/step1` 直接 → step1 表示 (新規)
- `/form/step2` 直接 (step1 未完了) → step1 にリダイレクト
- `/form/confirm` 直接 (step3 未完了) → step1 にリダイレクト
- `/form/complete` 直接 (送信前) → step1 にリダイレクト

### FM2: ブラウザ「戻る」ボタン `[State / User]`
- step3 で「戻る」 → step2 表示、入力値保持
- step2 で「戻る」 → step1 表示、入力値保持
- step1 で「戻る」 → 元のページ (フォーム外)
- 完了画面で「戻る」 → step3 ではなく完了画面のまま (キャッシュ汚染防止)

### FM3: 入力分岐 (Decision Table) `[Decision Table 必須]`
**コードを読んで全分岐を網羅**。例:

```
| 国 | 顧客種別 | 配送 | 期待挙動 |
|----|---------|------|---------|
| JP | 個人    | 標準 | 郵便番号必須, 配送料 500円 |
| JP | 個人    | 速達 | 郵便番号必須, 配送料 1500円 |
| JP | 法人    | 標準 | 会社名必須, 請求書住所必須, 送料 0円 |
| US | 個人    | 標準 | ZIP必須, 州必須, 送料 海外要見積 |
| US | 法人    | 標準 | 会社名必須, EIN 必須, 送料 海外要見積 |
| EU | 個人    | 標準 | VAT 任意, GDPR 同意必須 |
```

各行を別 testcase として作成。ルール ≥ 7 なら Classification Tree + Pairwise で削減.

### FM4: 確認画面 ↔ 編集ステップ往復 `[Product / User]`
- step3 で「step1 を編集」リンク → step1 表示、入力値保持
- 編集後「次へ」を順次踏む or 「確認画面に戻る」ジャンプリンク
- 編集後の確認画面に編集内容が反映

### FM5: 二重送信防止 `[Risk / Reliability]`
- 「送信」ボタンを高速 2 回クリック
- ボタンが disable される (`expect(button).to_be_disabled()`)
- サーバ側 nonce / idempotency key で 2 件目を拒否
- 結果として登録は 1 件のみ

### FM6: 完了画面でリロード → 二重登録しない `[State / Reliability]`
- 完了画面で F5 / Ctrl+R
- 期待: 完了画面のまま (POST/Redirect/GET パターン)
- 二重登録されない

### FM7: セッションタイムアウト中の継続 `[Interruptions / Reliability]`
- step2 で 30 分以上放置 (セッション timeout)
- 次の操作で「セッション切れ」案内 + 中間データ保持 (中間保存があれば)
- 復活フロー: ログイン or 再開リンク

### FM8: 必須項目を JS で除去 `[Risk / Statutes]`
- DevTools で `required` 削除 / `disabled` 解除
- submit → サーバ側 422 (二重防御)

### FM9: step skip 攻撃 `[Risk / State]`
- step1 完了後、URL を `/form/complete` に直接書き換え
- 期待: step1 (or 必要な未完ステップ) に戻される
- 内部状態 (session) で進捗管理されているか

### FM10: PII (個人情報) を URL / log に出力しない `[Statutes / GDPR]`
- ステップ間遷移は POST or session storage (URL クエリで送らない)
- ブラウザ履歴 / referrer に氏名 / メール / 電話番号が残らない
- サーバアクセスログにも PII が出ない (E2E では難 → bug DB 注記)

### FM11: Pairwise (オプション組合せ) `[Pairwise / Domain]`
オプション 4 軸以上は All-Pairs:

```
factor1: { 国: [JP, US, EU] }
factor2: { 配送: [標準, 速達, 店舗] }
factor3: { 支払い: [カード, 銀振, PayPal] }
factor4: { 顧客: [個人, 法人] }
```
全 54 通り → All-Pairs で ~12 通り。``@pytest.mark.parametrize`` で 12 行展開して書く.

### FM12: ファイル添付 `[Domain / Statutes]`
- 単一ファイル / 複数ファイル / 添付ゼロ
- 合計サイズ上限境界 (max-1 / max / max+1)
- 拡張子フィルタ (許可 / 拒否)
- 同名ファイルの上書き or 拒否

### FM13: 完了通知メール `[Functional / Statutes]`
- メール送信成功
- 件名 / 本文の i18n
- HTML escape (XSS 防止)
- 添付 PDF があれば内容検証

### FM14: 「登録済みメール」エラー `[Risk / Statutes]`
- 既存メールアドレスで signup → エラー表示
- ⚠️ ユーザ列挙攻撃に使えない曖昧な文言: 「登録できません」(NIST 800-63B 推奨)
- 確認メール送信方式 (登録の有無を答えない方針) も検討

### FM15: a11y (form 構造) `[Statutes / Automatic]`
- `<fieldset><legend>` でグルーピング
- `aria-invalid="true"` + `aria-describedby` でエラー紐付け
- フォーカス順序が論理的 (Tab で step1 → 次へボタン → step2 ...)
- スクリーンリーダーで step 進捗を読み上げる (`aria-live` or `role="status"`)

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| Decision Table | **FM3 必須** |
| Classification Tree + Pairwise | FM11 |
| State Transition | FM1, FM2 (step1→2→3→complete) |
| Risk Testing | FM5, FM8, FM9, FM10 |
| BVA | FM12 (ファイルサイズ) |
| Domain Testing | FM12 (ファイル種別) |
| Use Case | フォーム全体 (アクター × ゴール) |
| Claims | FM4, FM13, FM14 |

## Playwright 実装パターン

```python
# step skip 攻撃
page.goto("/form/step1")
page.get_by_label("メール").fill("test@example.com")
page.get_by_role("button", name="次へ").click()
# step2 で URL 直接書き換え
page.goto("/form/complete")
expect(page).to_have_url(re.compile(r"/form/step\d"))  # step に戻される

# 二重送信防止
button = page.get_by_role("button", name="送信")
# 2 連打
button.click()
expect(button).to_be_disabled()  # 即 disable

# 完了画面リロード
page.goto("/form/complete")
page.reload()
expect(page).to_have_url(re.compile(r"/form/complete"))  # まだ完了画面
```

## 共通チェックリスト併用

`checklist-common.md` の C1 (a11y form), C3 (CSRF / XSS), C6 (interruption), C7 (404 step).

## 参考文献

- ISTQB CTFL 4.2.2 (Decision Table)
- Grimm/Grochtmann (1993), "Classification Tree Method"
- OWASP ASVS V2.1 (Authentication, including signup)
- NIST SP 800-63B (パスワード / ユーザ列挙)
- W3C ARIA APG "Form Pattern"
