# MarkItDown MCP

各種ドキュメント（PDF、Office、画像など）をMarkdownに変換するMCPサーバープラグインです。

## 概要

[Microsoft MarkItDown](https://github.com/microsoft/markitdown) のMCPサーバーラッパーです。HTTP/HTTPS URL、ローカルファイル、Data URIを指定してドキュメントをMarkdownに変換できます。

## 機能

- `convert_to_markdown(uri)` ツールを提供
- 対応フォーマット: PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), 画像, HTML, CSV, JSON, XML など
- HTTP/HTTPS URL、ローカルファイルパス（`file://`）、Data URI に対応

## インストール

```bash
/plugin install mcp-markitdown@ai-plugins
```

## 使用例

```bash
# URLからドキュメントを変換
mcp__plugin_mcp-markitdown_markitdown__convert_to_markdown uri="https://example.com/document.pdf"

# ローカルファイルを変換
mcp__plugin_mcp-markitdown_markitdown__convert_to_markdown uri="file:///path/to/document.docx"
```

## ndf:scannerエージェントとの連携

MarkItDown MCPは、NDFプラグインの`ndf:scanner`エージェントと連携して使用することを推奨します。

```bash
Task(
  subagent_type="ndf:scanner",
  prompt="Convert /path/to/document.pdf to Markdown and summarize key points.",
  description="Convert and summarize PDF"
)
```

## 注意事項

- Python環境が必要です（`uvx` 経由で自動インストール）
- 大きなファイルの変換には時間がかかる場合があります

## 参考リンク

- [microsoft/markitdown](https://github.com/microsoft/markitdown) - 本家リポジトリ
- [markitdown-mcp (PyPI)](https://pypi.org/project/markitdown-mcp/) - PyPIパッケージ
