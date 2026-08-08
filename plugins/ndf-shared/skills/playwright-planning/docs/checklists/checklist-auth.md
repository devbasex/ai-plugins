# Checklist: `auth` — 認証 (login / logout / 2FA / password reset)

## 適用条件
email/username + password。SSO ボタン / Remember me / Forgot password / 2FA.

代表 URL: `/login`, `/signin`, `/register`, `/forgot-password`, `/auth/callback`.

## 必須テスト観点

### AU1: 正常ログイン `[State / Functional]`
- 有効資格情報 → ログイン成功
- セッション cookie 発行 (`Secure HttpOnly SameSite`)
- ダッシュボード or 指定 redirect 先へ遷移
- URL に session id を**含めない** (cookie に格納)

### AU2: 不正資格情報 — ユーザ列挙防止 `[Risk / Statutes]`
- 不存在メール `wrong@example.com` + 適当 pw → 「メールまたはパスワードが正しくありません」
- 存在メール `valid@example.com` + 不正 pw → **同じ文言** (列挙防止)
- 「このメールは登録されていません」「パスワードが違います」のような切り分け表示は **NG**
- NIST 800-63B 推奨: 識別不能なメッセージ

### AU3: 同一エラー応答時間 (timing attack 対策) `[Risk / Statutes]`
- 不存在メールと存在メールでレスポンス時間に有意差がない
- パスワード hash 計算を**両方で実行** (短絡しない)
- E2E では `expect_response` の経過時間を 100 件平均で比較

### AU4: アカウントロックアウト `[Decision Table / Statutes]`
- 連続失敗 N 回 → ロック (例: 5 回)
- ロック中は正しい資格情報でも拒否
- ロック解除: メール経由 / 一定時間経過 / 管理者
- rate limit ヘッダ (`Retry-After`)

### AU5: パスワード強度ポリシー `[Claims / Statutes]`
NIST SP 800-63B 推奨:
- 最低 8 文字 (推奨 15 文字以上)
- 漏洩済 PW DB (HaveIBeenPwned) チェック (任意)
- 文字種強制 (大文字 / 数字 / 記号) は**非推奨** (覚えにくくなる)
- 過度な複雑性ルールは UX 低下の根拠あり

仕様で定義された境界を BVA でテスト.

### AU6: パスワードリセット URL `[Risk / Statutes]`
- リセットリンクは単一使用 (use once)
- 有効期限 (例: 1 時間)
- HTTPS only
- リンク内 token は十分なエントロピー (256 bit)
- 期限切れリンクで再発行可能

### AU7: 2FA フロー `[State / Decision Table]`
- TOTP / SMS / メール / Email magic link
- 正しいコード → ログイン成功
- 不正コード N 回 → ロック
- 回復コード (recovery code) は単回利用
- 2FA 一時的バイパス (Remember this device 30 日) の管理

### AU8: ログイン後のセッション ID 再発行 `[Risk / Statutes]`
- ログイン**前** session id (匿名) と**後** session id (認証済) が**異なる**
- session fixation 攻撃対策 (OWASP)
- E2E では `context.cookies()` で前後比較

### AU9: ログアウト `[State / Functional]`
- ログアウトでサーバ側セッション無効化
- 戻るボタンで前ページに戻れない (cache-control: no-store)
- 別タブのセッションも無効化 (server-side session)
- 全セッション無効化オプション (パスワード変更時)

### AU10: Remember me と再認証 `[Risk / Claims]`
- Remember me で長期 cookie (例: 30 日)
- 自動ログイン後、**重要操作**は再認証要求 (パスワード変更 / 削除)
- step-up authentication: 高権限操作のみ追加認証

### AU11: Cookie 属性 `[Statutes / Automatic]`
- `Secure` (HTTPS のみ送信)
- `HttpOnly` (JS から読めない — XSS で session 盗難防止)
- `SameSite=Lax` または `Strict` (CSRF 軽減)
- `Path=/` (適切な scope)
- `Domain` 属性: ワイルドカードドメインに注意

### AU12: CSRF token (login form にも) `[Statutes / Automatic]`
- ログインフォームにも CSRF token (login CSRF 攻撃)
- token なしで POST `/login` → 403

### AU13: SSO redirect URL whitelist `[Risk / Statutes]`
- OAuth callback URL は事前登録された whitelist のみ受理
- `?redirect_uri=https://evil.example.com` で外部ドメインへ飛ばない
- open redirect 防止

### AU14: 並行セッション管理 `[State / Functional]`
- デバイス一覧の表示 (現在ログイン中のセッション)
- リモートログアウト (他デバイスを強制ログアウト)
- 異常検知 (新地域からのログイン → メール通知)

### AU15: HSTS / referrer policy `[Statutes / Automatic]`
- `Strict-Transport-Security` ヘッダ
- `Referrer-Policy: strict-origin-when-cross-origin`
- 認証関連ページでの `Referer` 漏洩防止

## 適用すべきテスト技法

| 技法 | 適用箇所 |
|------|---------|
| Decision Table | AU4 (失敗回数 × ロック), AU7 (2FA 分岐) |
| Risk Testing | AU2, AU3, AU6, AU8, AU13 |
| BVA | AU5 (パスワード長境界) |
| State Transition | AU7, AU9, AU14 |
| Claims | AU5, AU10 |

## Playwright 実装パターン

```python
# AU1: storage_state を保存して全テストで再利用
api = playwright.request.new_context(base_url=base_url)
api.post("/api/login", data={"email": "alice@test", "password": "..."})
api.storage_state(path="alice.json")

# 後続テストでは
context = browser.new_context(storage_state="alice.json")
page = context.new_page()
page.goto("/dashboard")  # ログイン済

# AU2: ユーザ列挙防止
for email in ["nonexistent@test", "valid@test"]:
    page.goto("/login")
    page.get_by_label("メール").fill(email)
    page.get_by_label("パスワード").fill("wrongpass")
    page.get_by_role("button", name="ログイン").click()
    expect(page.get_by_role("alert")).to_have_text("メールまたはパスワードが正しくありません")  # 同じ文言

# AU8: session id 再発行
ctx_before = context.cookies()
session_before = next(c for c in ctx_before if c["name"] == "session_id")["value"]
# login
session_after = next(c for c in context.cookies() if c["name"] == "session_id")["value"]
assert session_before != session_after, "session fixation 対策不備"
```

## 共通チェックリスト併用

`checklist-common.md` の C3 (sec — 全項目), C8 (console error 自動 FAIL).

## 参考文献

- OWASP ASVS v5.0 V2 (Authentication), V3 (Session Management)
- OWASP Authentication Cheat Sheet
- OWASP Session Management Cheat Sheet
- OWASP CSRF Prevention Cheat Sheet/
- NIST SP 800-63B "Digital Identity Guidelines"
- RFC 6265 "HTTP State Management Mechanism" (cookies)
