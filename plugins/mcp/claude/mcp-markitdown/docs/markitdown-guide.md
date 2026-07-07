# MarkItDown MCP ガイド

MarkItDown MCPサーバーで各種ドキュメントをMarkdown形式に変換します。

## 対応フォーマット

- PDF
- Microsoft Office（Word, Excel, PowerPoint）
- 画像（OCR）
- HTML
- その他のドキュメント形式

## 主要ツール

- `convert_to_markdown` - ファイルをMarkdownに変換

## 使用例

```
# PDFをMarkdownに変換
mcp__plugin_mcp-markitdown_markitdown__convert_to_markdown uri="file:///path/to/document.pdf"

# Excelファイルを変換
mcp__plugin_mcp-markitdown_markitdown__convert_to_markdown uri="file:///path/to/data.xlsx"
```

## 使い分け

- scannerエージェントがPDF/Office/画像ファイルの読み取りに使用
- Read toolで直接読めないファイル形式の変換に有用
