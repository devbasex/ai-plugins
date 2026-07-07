# SQL最適化 Before/After実例集

## 例1: N+1クエリの削減

**Before**:
```sql
-- ループで実行（N+1クエリ）
SELECT * FROM users WHERE id = ?;  -- N回実行
```

**After**:
```sql
-- 1回のクエリで取得
SELECT u.*, o.order_count
FROM users u
LEFT JOIN (
  SELECT user_id, COUNT(*) as order_count
  FROM orders
  GROUP BY user_id
) o ON u.id = o.user_id;
```

**改善**: N+1回 → 1回のクエリ、大幅な高速化

## 例2: インデックス活用

**Before**:
```sql
SELECT * FROM orders
WHERE created_at > '2023-01-01'
AND status = 'completed';
-- インデックスなし、フルスキャン
```

**After**:
```sql
-- インデックス作成
CREATE INDEX idx_orders_status_created ON orders(status, created_at);

-- 同じクエリがインデックスを使用
SELECT * FROM orders
WHERE status = 'completed'
AND created_at > '2023-01-01';
-- ORDER BY の順序を逆にしてインデックス効率化
```

**改善**: フルスキャン → インデックススキャン、10倍以上高速化

## 例3: ウィンドウ関数活用

**Before**:
```sql
-- サブクエリの入れ子
SELECT u.name,
  (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) as order_count,
  (SELECT SUM(total) FROM orders o WHERE o.user_id = u.id) as order_total
FROM users u;
-- usersの各行でordersを2回スキャン
```

**After**:
```sql
-- ウィンドウ関数で1回のスキャン
SELECT u.name,
  COUNT(o.id) OVER (PARTITION BY u.id) as order_count,
  SUM(o.total) OVER (PARTITION BY u.id) as order_total
FROM users u
LEFT JOIN orders o ON u.id = o.user_id;
-- 1回のJOINで完結
```

**改善**: 2N回スキャン → 1回のJOIN、大幅な高速化

## 例4: 不要なDISTINCTの削除

**Before**:
```sql
SELECT DISTINCT u.id, u.name
FROM users u
JOIN orders o ON u.id = o.user_id;
-- DISTINCTで重複排除（コスト高）
```

**After**:
```sql
SELECT u.id, u.name
FROM users u
WHERE EXISTS (
  SELECT 1 FROM orders o WHERE o.user_id = u.id
);
-- DISTINCTなしで同じ結果
```

## 例5: BigQuery向け最適化

**Before**:
```sql
SELECT *
FROM `project.dataset.large_table`
WHERE DATE(timestamp) = '2024-01-15';
-- フルスキャン（高コスト）
```

**After**:
```sql
SELECT *
FROM `project.dataset.large_table`
WHERE timestamp >= '2024-01-15'
AND timestamp < '2024-01-16';
-- パーティション利用（低コスト）
```

**改善**: パーティションプルーニングで90%以上のコスト削減

## トラブルシューティング

### Q: 最適化したのに遅い

A: 以下を確認:
- インデックスが実際に使用されているか（EXPLAIN確認）
- 統計情報が最新か（ANALYZE TABLE実行）
- データ量が想定通りか

### Q: どのパターンを適用すべきか分からない

A: 以下の順で確認:
1. EXPLAINで実行計画を確認
2. フルスキャンがあればインデックス作成
3. N+1パターンがあればJOINに統合
4. サブクエリが複雑ならウィンドウ関数検討

### Q: インデックスを作成したら書き込みが遅くなった

A: インデックスの見直しが必要:
- 使用頻度の低いインデックスを削除
- 複合インデックスで統合できないか検討
