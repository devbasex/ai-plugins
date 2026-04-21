# プラグイン開発ガイド

## マーケットプレイスの構造

### marketplace.json

プラグインマーケットプレイスの中心となる設定ファイル：

```json
{
  "name": "ai-plugins",
  "owner": {
    "name": "takemi-ohama",
    "url": "https://github.com/takemi-ohama"
  },
  "plugins": [
    {
      "name": "ndf",
      "source": "./plugins/ndf"
    }
  ]
}
```

## プラグイン構造

各プラグインは以下の構造を持ちます：

```
plugins/{plugin-name}/
├── .claude-plugin/
│   └── plugin.json              # プラグインメタデータ（必須）
├── commands/                    # スラッシュコマンド（オプション）
│   └── *.md
├── agents/                      # サブエージェント（オプション）
│   └── *.md
├── skills/                      # プロジェクトスキル（オプション）
│   └── {skill-name}/
│       └── SKILL.md
├── hooks/                       # プロジェクトフック（オプション）
│   └── hooks.json
└── README.md                    # プラグイン説明
```

## plugin.json の作成

**必須フィールド**:
- `name`: プラグイン名（ケバブケース）
- `version`: セマンティックバージョニング（MAJOR.MINOR.PATCH）
- `description`: プラグインの説明
- `author`: 作成者情報

**例**:
```json
{
  "name": "example-plugin",
  "version": "1.0.0",
  "description": "Example plugin for demonstration",
  "author": {
    "name": "Your Name",
    "url": "https://github.com/yourname"
  },
  "keywords": ["example", "demo"],
  "commands": ["./commands/example.md"],
  "agents": ["./agents/example-agent.md"]
}
```

## バージョン管理

**セマンティックバージョニング**:
- **MAJOR**: 破壊的変更
- **MINOR**: 後方互換性のある新機能
- **PATCH**: バグフィックス

**バージョン更新時の手順**:
1. `plugin.json`のバージョンをインクリメント
2. 変更内容をドキュメント化
3. 破壊的変更がある場合は明示
4. テストを実行

## ドキュメント要件

各プラグインに必要なドキュメント:
- README.md: プラグインの概要、インストール方法、使用方法
- 各機能の説明とサンプルコード
- トラブルシューティングガイド
- 必要な環境変数や認証情報の説明

## 新しいプラグインの追加

1. **既存プラグインを参考に構造を理解**
   ```bash
   ls -la plugins/ndf/
   cat plugins/ndf/.claude-plugin/plugin.json
   ```

2. **ディレクトリ構造を作成**
   ```bash
   mkdir -p plugins/{plugin-name}/{.claude-plugin,commands,agents,skills}
   ```

3. **plugin.jsonを作成** - 必須フィールドをすべて含める

4. **プラグインコンテンツを実装** - スキル、コマンド、エージェントを追加

5. **marketplace.jsonに登録**

6. **ドキュメント作成** - README.md、使用例、トラブルシューティング

7. **テスト** - ローカルでプラグインをテスト

8. **コミット & PR作成**
   ```bash
   git checkout -b feature/add-{plugin-name}
   git add .
   git commit -m "Add {plugin-name} plugin"
   git push origin feature/add-{plugin-name}
   ```

## 既存プラグインの更新

1. 現在の状態を確認（plugin.json、README.md）
2. 変更を実施
3. plugin.jsonのバージョンをインクリメント
4. ドキュメント更新
5. テスト
6. コミット & PR作成

## 検証とテスト

### ローカルテスト

```bash
# マーケットプレイス追加（Claude Codeで）
/plugin marketplace add /path/to/ai-plugins

# プラグインインストール
/plugin install {plugin-name}@ai-plugins
```

### 検証チェックリスト

- [ ] marketplace.jsonが正しい形式
- [ ] 各plugin.jsonが必須フィールドを含む
- [ ] バージョン番号が適切
- [ ] ドキュメントが完全
- [ ] 機密情報が含まれていない
- [ ] プラグインが正常にインストールできる
- [ ] 各機能が動作する

## トラブルシューティング

**Q: marketplace.jsonが認識されない**
- `.claude-plugin/marketplace.json`の配置を確認
- JSON形式の検証

**Q: プラグインがインストールできない**
- plugin.jsonの必須フィールドを確認
- パスが正しいか確認（相対パス）

**Q: バージョン更新が反映されない**
- plugin.jsonとmarketplace.jsonの両方を更新
- Claude Codeを再起動
