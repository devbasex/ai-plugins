---
name: markdown-writing
description: |
  Markdown文書作成時の重要なルールを提供します。図表はmermaid/plantUMLを使用（ASCII ART禁止）、300行超の文書は順序prefix付きで分割。

  重要ルール:
  - 図表: mermaid/plantUML使用（ASCII ARTは禁止、ツリー構造のみ例外）
  - 文書長: 概ね300行以内、超える場合は分割
  - 分割時: ディレクトリ作成+順序prefix（01-, 02-, ...）

  Triggers: "Markdown作成", "ドキュメント作成", "文書作成", "図を描く", "mermaid", "create document", "write docs"
allowed-tools:
  - Read
  - Write
  - Edit
---

# Markdown Writing Skill

## 重要ルール

### 1. 図表作成ルール

**mermaid または plantUML を使用**（ASCII ART禁止、ツリー除く）

```mermaid
graph TD
    A[開始] --> B{条件判定}
    B -->|Yes| C[処理A]
    B -->|No| D[処理B]
```

### 2. 文書の長さと分割ルール

| ページ数 | 対応 |
|---------|-----|
| ~300行 | そのまま |
| 301~600行 | 2ファイルに分割 |
| 600行以上 | セクションごとに分割 |

**分割時のファイル名**: 順序prefix（01-, 02-, ...）+ ケバブケース

```
docs/feature-guide/
├── 01-introduction.md
├── 02-installation.md
└── 03-usage.md
```

## チェックリスト

- [ ] 図表はmermaid/plantUML使用（ツリー除く）
- [ ] ファイル長は300行以内（超える場合は分割）
- [ ] 分割時は順序prefix使用（01-, 02-, ...）

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-diagram-guide.md` | mermaid/plantUML記法、よくある間違い |

## 関連リソース

- [Mermaid公式ドキュメント](https://mermaid.js.org/)
- [PlantUML公式ドキュメント](https://plantuml.com/)
