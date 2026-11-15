# 認証・認可チェックリスト

## 認証テスト

### ログイン機能

- [ ] **正しい認証情報でログイン成功**
- [ ] **誤った認証情報でログイン失敗**
- [ ] **パスワード忘れ機能**
  - リセットリンクの有効期限
  - リセット後の古いリンク無効化

### セッション管理

- [ ] **ログアウト後にセッション無効化**
- [ ] **セッションタイムアウト**
  - アイドルタイムアウト（15-30分推奨）
  - 絶対タイムアウト（8-24時間推奨）
- [ ] **同時ログインの制限**
  - 必要に応じて制限を実装
  - 新規ログイン時に既存セッションを無効化

### トークン管理（JWT）

- [ ] **トークン有効期限**
  - アクセストークン: 15分-1時間
  - リフレッシュトークン: 7-30日
- [ ] **トークンリフレッシュ**
  - リフレッシュトークンのローテーション
- [ ] **署名検証**
  - 強力な秘密鍵の使用
  - アルゴリズムの明示的指定

```javascript
// ✅ Good: JWT検証
const jwt = require('jsonwebtoken');

function verifyToken(token) {
  return jwt.verify(token, process.env.JWT_SECRET, {
    algorithms: ['HS256'],  // アルゴリズムを明示
    issuer: 'your-app',
    audience: 'your-users'
  });
}
```

## 認可テスト

### ロールベースアクセス制御

- [ ] **管理者のみアクセス可能なリソース**
  - /admin/* へのアクセス制限
  - 管理機能の認可チェック
- [ ] **一般ユーザーの権限制限**
  - 他ユーザーのデータへのアクセス禁止
  - 機能制限の適用

### リソース所有者チェック

- [ ] **自分のリソースのみ編集・削除可能**

```javascript
// ✅ Good: 所有者チェック
async function updateResource(req, res) {
  const resource = await Resource.findByPk(req.params.id);

  if (!resource) {
    return res.status(404).json({ error: 'Not found' });
  }

  // 所有者または管理者のみ許可
  if (resource.userId !== req.user.id && req.user.role !== 'admin') {
    return res.status(403).json({ error: 'Forbidden' });
  }

  await resource.update(req.body);
  res.json(resource);
}
```

## テストケース例

### 認証バイパステスト

```
1. 認証なしで保護されたエンドポイントにアクセス
   → 401 Unauthorized が返ること

2. 無効なトークンでアクセス
   → 401 Unauthorized が返ること

3. 期限切れトークンでアクセス
   → 401 Unauthorized が返ること

4. 改ざんされたトークンでアクセス
   → 401 Unauthorized が返ること
```

### 権限昇格テスト

```
1. 一般ユーザーで管理者APIにアクセス
   → 403 Forbidden が返ること

2. ユーザーAでユーザーBのリソースを編集
   → 403 Forbidden が返ること

3. URLのIDを変更して他ユーザーのデータにアクセス
   → 403 Forbidden が返ること
```

## 実装チェックリスト

### 必須実装

- [ ] パスワードはbcrypt/Argon2でハッシュ化
- [ ] セッション/トークンにはセキュアなCookie設定
- [ ] すべてのAPIエンドポイントに認証ミドルウェア
- [ ] 認可チェックは各リソースアクセス時に実行
- [ ] レート制限を実装

### 推奨実装

- [ ] 多要素認証（MFA）
- [ ] パスワード強度チェック
- [ ] ログイン試行回数制限
- [ ] セキュリティログの記録
