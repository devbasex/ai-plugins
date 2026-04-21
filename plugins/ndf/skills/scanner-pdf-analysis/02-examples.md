# PDF解析 実装例

## 例1: 技術仕様書の分析

```python
# 技術仕様書から要件を抽出
result = analyze_pdf('spec.pdf', extract_tables=True)

# テーブル（要件一覧）をCSVに保存
for i, table in enumerate(result['tables']):
    table.to_csv(f'requirements_{i}.csv', index=False)

# 要約をMarkdownに保存
with open('spec-summary.md', 'w') as f:
    f.write(f"# 仕様書要約\n\n")
    f.write(f"ページ数: {result['page_count']}\n\n")
    f.write(f"## 抽出要件\n\n")
    for i, table in enumerate(result['tables']):
        f.write(f"### 要件テーブル {i+1}\n\n")
        f.write(table.to_markdown())
        f.write("\n\n")
```

## 例2: 論文の要約

```python
# 論文PDFを読み込み
result = analyze_pdf('research-paper.pdf', summarize=True)

# 重要なセクションを抽出
sections_of_interest = ['Abstract', 'Introduction', 'Conclusion']
summary = []

for section in result['sections']:
    if section['title'] in sections_of_interest:
        summary.append(f"## {section['title']}\n{section['text']}\n")

# 要約を保存
with open('paper-summary.md', 'w') as f:
    f.write('\n'.join(summary))
```

## 例3: 請求書からデータ抽出

```python
# 請求書PDFからテーブル抽出
result = analyze_pdf('invoice.pdf', extract_tables=True)

# 最初のテーブル（請求明細）を取得
invoice_items = result['tables'][0]

# CSVに変換
invoice_items.to_csv('invoice-items.csv', index=False)

# 合計金額を計算
total = invoice_items['金額'].sum()
print(f"合計金額: {total}円")
```

## 例4: 複数PDFの一括処理

```python
import glob

def batch_analyze(pdf_dir, output_dir):
    for pdf_path in glob.glob(f'{pdf_dir}/*.pdf'):
        result = analyze_pdf(pdf_path)

        # ファイル名を取得
        filename = os.path.basename(pdf_path).replace('.pdf', '')

        # 結果を保存
        with open(f'{output_dir}/{filename}-analysis.md', 'w') as f:
            f.write(f"# {filename} 分析結果\n\n")
            f.write(f"ページ数: {result['page_count']}\n\n")
            f.write(f"## テキスト\n{result['text'][:1000]}...\n")

# 使用
batch_analyze('documents/', 'analysis/')
```

## 例5: OCRを使用した画像PDF処理

```python
import pytesseract
from pdf2image import convert_from_path

def analyze_scanned_pdf(pdf_path):
    # PDFを画像に変換
    images = convert_from_path(pdf_path)

    text = []
    for i, image in enumerate(images):
        # OCRでテキスト抽出
        page_text = pytesseract.image_to_string(image, lang='jpn')
        text.append(f"--- Page {i+1} ---\n{page_text}")

    return '\n'.join(text)

# 使用
text = analyze_scanned_pdf('scanned-document.pdf')
print(text)
```

## 出力テンプレート

```markdown
# [ファイル名] 分析結果

## 概要
- ページ数: XX
- テーブル数: XX
- 作成日: YYYY-MM-DD

## 重要ポイント
1. [ポイント1]
2. [ポイント2]
3. [ポイント3]

## 抽出テーブル

### テーブル1 (ページ X)
| 列1 | 列2 | 列3 |
|-----|-----|-----|
| A   | B   | C   |

## セクション構造
1. はじめに (p.1)
2. 背景 (p.3)
3. 方法 (p.7)
4. 結果 (p.15)
5. 結論 (p.23)

## 全文テキスト
[抽出されたテキスト...]
```
