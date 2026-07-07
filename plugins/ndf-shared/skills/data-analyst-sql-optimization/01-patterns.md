# SQL最適化パターン詳細

## 1. N+1クエリ削減

**問題**: ループ内で繰り返しSELECT文を実行

**解決**: JOINまたはサブクエリで1回のクエリに統合

```sql
-- ❌ Bad: ループで実行（N+1クエリ）
SELECT * FROM users WHERE id = ?;  -- N回実行

-- ✅ Good: 1回のクエリで取得
SELECT u.*, o.order_count
FROM users u
LEFT JOIN (
  SELECT user_id, COUNT(*) as order_count
  FROM orders
  GROUP BY user_id
) o ON u.id = o.user_id;
```

## 2. インデックス活用

**問題**: WHERE句の列にインデックスがない

**解決**: 適切なインデックスを作成

```sql
-- インデックス作成
CREATE INDEX idx_orders_status_created ON orders(status, created_at);

-- ✅ インデックス効率化のためWHERE句の順序を調整
SELECT * FROM orders
WHERE status = 'completed'
AND created_at > '2023-01-01';
```

**インデックス作成の指針**:
- WHERE句で頻繁に使用される列
- JOIN条件の列
- ORDER BY句の列
- カーディナリティ（値の多様性）が高い列

## 3. JOIN最適化

**問題**: 不要な大規模テーブルのJOIN

**解決**: 必要な列のみ取得、結合順序の最適化

```sql
-- ❌ Bad: SELECT *
SELECT * FROM orders o
JOIN products p ON o.product_id = p.id
JOIN users u ON o.user_id = u.id;

-- ✅ Good: 必要な列のみ
SELECT o.id, o.total, p.name, u.email
FROM orders o
JOIN products p ON o.product_id = p.id
JOIN users u ON o.user_id = u.id;
```

## 4. ウィンドウ関数活用

**問題**: 複雑なサブクエリの入れ子

**解決**: ROW_NUMBER(), RANK()等のウィンドウ関数を使用

```sql
-- ❌ Bad: サブクエリの入れ子
SELECT u.name,
  (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) as order_count,
  (SELECT SUM(total) FROM orders o WHERE o.user_id = u.id) as order_total
FROM users u;

-- ✅ Good: ウィンドウ関数で1回のスキャン
SELECT DISTINCT u.name,
  COUNT(o.id) OVER (PARTITION BY u.id) as order_count,
  SUM(o.total) OVER (PARTITION BY u.id) as order_total
FROM users u
LEFT JOIN orders o ON u.id = o.user_id;
```

## 5. EXISTS vs IN

**問題**: サブクエリでINを使用

**解決**: EXISTSに変更（多くの場合高速）

```sql
-- ❌ 遅い場合がある
SELECT * FROM users
WHERE id IN (SELECT user_id FROM orders WHERE total > 1000);

-- ✅ 通常より高速
SELECT * FROM users u
WHERE EXISTS (
  SELECT 1 FROM orders o
  WHERE o.user_id = u.id AND o.total > 1000
);
```

## 6. LIMIT活用

**問題**: 全件取得後にアプリ側でフィルタ

**解決**: SQLでLIMIT/OFFSETを使用

```sql
-- ✅ ページング
SELECT * FROM orders
ORDER BY created_at DESC
LIMIT 20 OFFSET 0;  -- 1ページ目
```

## 7. 計算列のインデックス

**問題**: WHERE句で関数を列に適用

**解決**: 計算済み列を作成してインデックス

```sql
-- ❌ Bad: 関数適用でインデックス無効
SELECT * FROM users WHERE YEAR(created_at) = 2024;

-- ✅ Good: 範囲検索でインデックス有効
SELECT * FROM users
WHERE created_at >= '2024-01-01'
AND created_at < '2025-01-01';
```

## パフォーマンス測定

### 改善前後の比較

1. **実行時間測定**
2. **スキャンバイト数確認**（BigQuery）
3. **実行計画比較**: `EXPLAIN SELECT ...;`

### 目標指標

- **実行時間**: 50%以上削減
- **スキャンバイト数**: 70%以上削減
- **インデックス使用**: EXPLAINでtype=ref以上
