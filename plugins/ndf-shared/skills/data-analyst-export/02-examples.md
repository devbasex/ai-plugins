# エクスポート実装例

## 例1: BigQueryクエリ結果をCSVにエクスポート

```javascript
// クエリ実行（BigQuery MCP使用）
const results = await bigquery.query("SELECT * FROM dataset.table LIMIT 1000");

// CSVに変換
const fs = require('fs');
const exportCSV = require('./scripts/export-csv.js');

exportCSV(results, 'query-results.csv');
console.log('✅ CSVにエクスポート完了: query-results.csv');
```

## 例2: 分析結果をExcelレポートに出力

```javascript
// 複数の分析結果を取得
const salesData = await bigquery.query("SELECT * FROM sales");
const productData = await bigquery.query("SELECT * FROM products");

// 複数シートでExcel出力
const data = {
  "Sales": salesData,
  "Products": productData
};

exportExcel(data, 'monthly-report.xlsx');
console.log('✅ Excelレポート作成完了: monthly-report.xlsx');
```

## 例3: ドキュメントにMarkdownテーブル挿入

```javascript
// クエリ結果を取得
const topProducts = await bigquery.query(
  "SELECT name, sales FROM products ORDER BY sales DESC LIMIT 10"
);

// Markdownテーブルに変換
const markdown = exportMarkdown(topProducts);

// README.mdに挿入
const fs = require('fs');
const readme = fs.readFileSync('README.md', 'utf-8');
const updatedReadme = readme.replace('<!-- TOP_PRODUCTS -->', markdown);
fs.writeFileSync('README.md', updatedReadme);

console.log('✅ README.mdにテーブルを挿入しました');
```

## 例4: 日付付きファイル名でエクスポート

```javascript
const today = new Date().toISOString().split('T')[0];

// 日付付きファイル名
exportCSV(data, `sales-${today}.csv`);
exportExcel(data, `report-${today}.xlsx`);
```

## 例5: 大容量データの分割エクスポート

```javascript
const CHUNK_SIZE = 100000;

async function exportLargeData(query, outputPrefix) {
  let offset = 0;
  let fileIndex = 1;
  let hasMore = true;

  while (hasMore) {
    const results = await bigquery.query(
      `${query} LIMIT ${CHUNK_SIZE} OFFSET ${offset}`
    );

    if (results.length === 0) {
      hasMore = false;
      break;
    }

    const filename = `${outputPrefix}-${fileIndex}.csv`;
    exportCSV(results, filename);
    console.log(`✅ ${filename} 作成完了 (${results.length}行)`);

    offset += CHUNK_SIZE;
    fileIndex++;
  }

  console.log(`✅ 全${fileIndex - 1}ファイル作成完了`);
}

// 使用
await exportLargeData(
  "SELECT * FROM large_table",
  "output/large-data"
);
```

## 例6: フィルタ付きエクスポート

```javascript
// 特定の条件でフィルタしてエクスポート
const results = await bigquery.query(`
  SELECT *
  FROM sales
  WHERE date >= '2024-01-01'
  AND region = 'APAC'
`);

// ファイル名に条件を含める
exportCSV(results, 'sales-2024-apac.csv');
exportJSON(results, 'sales-2024-apac.json', { pretty: true });
```
