# AI Plugins プロジェクト概要

## プロジェクトの目的

Claude Codeプラグインマーケットプレイス（内部用）として、チーム全体でClaude Codeの導入を加速するための事前設定されたプラグインを提供する。

## リポジトリ情報

- **リポジトリ名**: ai-plugins
- **オーナー**: takemi-ohama
- **ライセンス**: MIT
- **URL**: https://github.com/takemi-ohama/ai-plugins

## 配布コンポーネント

1. **MCPインテグレーションスキル**: GitHub、Serena、BigQuery、Notion MCPの自動セットアップ
2. **カスタムスラッシュコマンド**: 共通タスク用の再利用可能なスラッシュコマンド
3. **サブエージェント**: 異なるドメイン向けの専門AIエージェント
4. **プロジェクトフック**: イベントによってトリガーされる自動ワークフロー

## ディレクトリ構造

```
ai-plugins/
├── .claude-plugin/
│   └── marketplace.json          # マーケットプレイスメタデータ
├── plugins/
│   ├── ndf/                      # NDFプラグイン（メイン）
│   ├── mcp-serena/               # Serena MCPプラグイン
│   └── {plugin-name}/            # その他のプラグイン
├── docs/                         # リポジトリ知識
├── AGENTS.md                     # 共通エントリポイント
├── CLAUDE.md                     # Claude Code固有設定
├── KIRO.md                       # Kiro CLI固有設定
└── README.md                     # プロジェクトドキュメント
```

## インストール方法

### マーケットプレイスの追加
```bash
/plugin marketplace add https://github.com/takemi-ohama/ai-plugins
```

### プラグインのインストール
```bash
/plugin install ndf@ai-plugins
```
