# AI Plugins プロジェクト概要

## プロジェクトの目的

Claude Code / Codex プラグインマーケットプレイス（内部用）として、チーム全体でAI開発ツールの導入を加速するための事前設定されたプラグインを提供する。

## リポジトリ情報

- **リポジトリ名**: ai-plugins
- **オーナー**: devbasex
- **ライセンス**: MIT
- **URL**: https://github.com/devbasex/ai-plugins

## デプロイコンポーネント

1. **MCPインテグレーションスキル**: GitHub、Serena、BigQuery、Notion MCPの自動セットアップ
2. **カスタムスラッシュコマンド**: 共通タスク用の再利用可能なスラッシュコマンド
3. **サブエージェント**: 異なるドメイン向けの専門AIエージェント
4. **プロジェクトフック**: イベントによってトリガーされる自動ワークフロー

## ディレクトリ構造

```
ai-plugins/
├── .claude-plugin/
│   └── marketplace.json          # マーケットプレイス定義（Claude Code・Codex 共通）
├── plugins/
│   ├── ndf/                      # NDF（Claude Code・Codex 共通の単一ディレクトリ）
│   ├── playwright-kit/           # playwright-kit（Claude Code・Codex 共通の単一ディレクトリ）
│   └── mcp/
│       └── mcp-*/                # MCPプラグイン10個（Claude Code・Codex 共通）
├── docs/                         # リポジトリ知識
├── AGENTS.md                     # 共通エントリポイント
├── CLAUDE.md                     # Claude Code固有設定
├── KIRO.md                       # 相手役として起動される Kiro CLI の説明
└── README.md                     # プロジェクトドキュメント
```

## インストール方法

**取得元は既定ブランチ（`main`）です。** 開発版のチャネル（`develop`）は 10.27.75 の後でやめました。入れていた利用者の戻し方は
[versioning-and-distribution.md の「開発版から正式版へ戻す」](versioning-and-distribution.md#開発版から正式版へ戻す)にあります。

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
