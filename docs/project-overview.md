# AI Plugins プロジェクト概要

## プロジェクトの目的

Claude Code / Codex プラグインマーケットプレイス（内部用）として、チーム全体でAI開発ツールの導入を加速するための事前設定されたプラグインを提供する。

## リポジトリ情報

- **リポジトリ名**: ai-plugins
- **オーナー**: devbasex
- **ライセンス**: MIT
- **URL**: https://github.com/devbasex/ai-plugins

## 配布コンポーネント

1. **MCPインテグレーションスキル**: GitHub、Serena、BigQuery、Notion MCPの自動セットアップ
2. **カスタムスラッシュコマンド**: 共通タスク用の再利用可能なスラッシュコマンド
3. **サブエージェント**: 異なるドメイン向けの専門AIエージェント
4. **プロジェクトフック**: イベントによってトリガーされる自動ワークフロー

## ディレクトリ構造

```
ai-plugins/
├── .agents/
│   └── plugins/
│       └── marketplace.json      # Codexマーケットプレイスメタデータ
├── .claude-plugin/
│   └── marketplace.json          # Claude Codeマーケットプレイスメタデータ
├── plugins/
│   ├── ndf/                      # NDFプラグイン（メイン）
│   │   ├── skills/               # 全Skill実体（Claude Code/Kiroはmanifest配列で公開対象を指定）
│   │   ├── skills-codex/         # Codex向け公開Skill（実ディレクトリ）
│   │   └── skills-optional/      # ランタイム別除外候補リスト
│   ├── mcp-serena/               # Serena MCPプラグイン
│   └── {plugin-name}/            # その他のプラグイン
├── docs/                         # リポジトリ知識
├── AGENTS.md                     # 共通エントリポイント
├── CLAUDE.md                     # Claude Code固有設定
├── KIRO.md                       # Kiro CLI固有設定
└── README.md                     # プロジェクトドキュメント
```

## インストール方法

### Codex
```bash
codex plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

### Claude Code: マーケットプレイスの追加
```bash
/plugin marketplace add https://github.com/devbasex/ai-plugins
```

### Claude Code: プラグインのインストール
```bash
/plugin install ndf@ai-plugins
```
