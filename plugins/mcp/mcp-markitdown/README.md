# MarkItDown MCP

各種ドキュメント（PDF、Office、画像など）をMarkdownに変換するMCPサーバープラグインです。

## 概要

[Microsoft MarkItDown](https://github.com/microsoft/markitdown) のMCPサーバーラッパーです。HTTP/HTTPS URL、ローカルファイル、Data URIを指定してドキュメントをMarkdownに変換できます。

## 機能

- `convert_to_markdown(uri)` ツールを提供
- 対応フォーマット: PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), 画像, HTML, CSV, JSON, XML など
- HTTP/HTTPS URL、ローカルファイルパス（`file://`）、Data URI に対応

## v2.0.0 へ更新するとき

配布ディレクトリが `plugins/mcp/{shared,claude,codex,kiro}/mcp-markitdown/` から
`plugins/mcp/mcp-markitdown/` へ変わりました。マーケットプレイスの参照先が変わるため、**導入済みの
環境では再インストールが要ります**。Kiro CLI の installer は `dev.kiro/install.sh` へ移りました。

MCP サーバの定義（`.mcp.json`）の内容は変えていません。

## インストール

### Claude Code

```bash
/plugin install mcp-markitdown@ai-plugins
```

### Codex

```bash
codex plugin add mcp-markitdown@ai-plugins
```

### Kiro CLI

Kiro では repository clone 後、対象 plugin の installer を project root で実行します。

```bash
bash plugins/mcp/mcp-markitdown/dev.kiro/install.sh
```

### 必要な環境変数

- 追加の環境変数は不要です。

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
