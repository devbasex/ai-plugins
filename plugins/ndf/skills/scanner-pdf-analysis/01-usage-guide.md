# PDF解析 使用ガイド

## スクリプト詳細

### analyze-pdf.py

PDFを解析し、構造化されたデータを抽出します。

**機能**:
- PyPDF2: テキスト抽出、メタデータ取得
- tabula-py: テーブル抽出（Java必要）
- pdfplumber: 高精度なレイアウト解析

**コード概要**:
```python
import PyPDF2
import tabula
import pdfplumber

def analyze_pdf(pdf_path):
    # メタデータ取得
    with open(pdf_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        page_count = len(reader.pages)

        # テキスト抽出
        text = ''.join([page.extract_text() for page in reader.pages])

    # テーブル抽出
    tables = tabula.read_pdf(pdf_path, pages='all')

    # pdfplumberでレイアウト解析
    with pdfplumber.open(pdf_path) as pdf:
        # セクション識別（フォントサイズで判定）
        sections = extract_sections(pdf)

    return {
        'page_count': page_count,
        'text': text,
        'tables': tables,
        'sections': sections
    }
```

## ライブラリの使い分け

| ライブラリ | 用途 | 特徴 |
|-----------|------|------|
| PyPDF2 | テキスト抽出、メタデータ | 軽量、基本機能 |
| tabula-py | テーブル抽出 | 高精度、Java必要 |
| pdfplumber | レイアウト解析 | 座標情報取得可能 |
| camelot-py | テーブル抽出（代替） | 高精度、複雑な表対応 |

## トラブルシューティング

### Q: テキストが抽出できない

A: 画像ベースPDFの可能性があります。OCR（Tesseract）を使用してください:
```bash
pip install pytesseract
# OCRでテキスト抽出
python scripts/analyze-pdf.py --ocr document.pdf
```

### Q: テーブルが正しく抽出されない

A: 複数の方法を試してください:
1. tabula-py（Java必要）
2. pdfplumber（Python純正）
3. camelot-py（高精度）

### Q: 日本語が文字化けする

A: エンコーディングを指定:
```python
text = extract_text(pdf_path, encoding='utf-8')
```

### Q: メモリ不足

A: ページ範囲を指定して処理:
```python
# 特定のページのみ処理
tables = tabula.read_pdf(pdf_path, pages='1-10')
```
