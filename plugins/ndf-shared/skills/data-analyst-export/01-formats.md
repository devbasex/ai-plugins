# エクスポート形式詳細

## CSV出力

### export-csv.js

JSON配列をCSV形式に変換します。

**機能**:
- 自動ヘッダー生成
- カスタムデリミタ（カンマ、タブ、セミコロン）
- 引用符エスケープ
- UTF-8 BOM対応（Excel互換）

**使用例**:
```bash
# 基本的な使用
node export-csv.js data.json output.csv

# タブ区切り
node export-csv.js data.json output.tsv --delimiter="\t"

# Excel互換（BOM付き）
node export-csv.js data.json output.csv --bom
```

**入力データ形式** (input.json):
```json
[
  {"id": 1, "name": "Product A", "price": 1000},
  {"id": 2, "name": "Product B", "price": 2000}
]
```

**出力** (output.csv):
```csv
id,name,price
1,Product A,1000
2,Product B,2000
```

## JSON出力

### export-json.js

CSV/配列データをJSON形式に変換します。

**機能**:
- Pretty-print（整形出力）
- 圧縮出力
- 配列またはオブジェクト形式

**使用例**:
```bash
# Pretty-print
node export-json.js data.csv output.json --pretty

# 圧縮
node export-json.js data.csv output.json --compact
```

## Excel出力

### export-excel.js

JSON配列をExcelファイル(.xlsx)に変換します。

**機能**:
- 複数シート作成
- ヘッダー書式設定
- セルの書式設定（数値、通貨、日付）
- 列幅自動調整

**使用例**:
```bash
# 単一シート
node export-excel.js data.json output.xlsx

# 複数シート（各シートのデータはdata.jsonに含む）
node export-excel.js multi-sheet-data.json output.xlsx
```

**multi-sheet-data.json の形式**:
```json
{
  "Sheet1": [
    {"id": 1, "name": "Item 1"}
  ],
  "Sheet2": [
    {"id": 2, "name": "Item 2"}
  ]
}
```

## Markdownテーブル出力

### export-markdown.js

JSON配列をMarkdownテーブルに変換します。

**機能**:
- GitHub Flavored Markdown形式
- 列の自動整列
- 見出し行の区切り

**使用例**:
```bash
node export-markdown.js data.json output.md
```

**出力例**:
```markdown
| id | name | price |
|----|------|-------|
| 1 | Product A | 1000 |
| 2 | Product B | 2000 |
```

## トラブルシューティング

### Q: Excelで日本語が文字化けする

A: UTF-8 BOMを付与してください:
```bash
node export-csv.js data.json output.csv --bom
```

### Q: 大きなデータでメモリ不足

A: ストリーミング処理に変更:
```javascript
// ストリーミング版のスクリプトを使用
node export-csv-stream.js large-data.json output.csv
```

### Q: Excelの行数制限（1048576行）を超える

A: 複数ファイルに分割:
```javascript
// 100万行ごとに分割
node export-excel.js data.json output --split 1000000
// output-1.xlsx, output-2.xlsx, ... が生成される
```
