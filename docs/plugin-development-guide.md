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
      "source": "./plugins/ndf",
      "description": "Claude Code plugin (v9.3.0): ... 31 focused NDF skills ...",
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity",
      "interface": {
        "displayName": "NDF"
      }
    }
  ]
}
```

marketplace 定義はこの 1 つだけです。Codex は専用の定義を持たず、この定義へフォールバックして
同じ `source` を読みます。`policy` / `category` / `interface` は Codex が要求する項目で、同じ entry へ
含めます（Claude Code はこれらを読み込み時に無視し、`claude plugin validate` は warning 付きで通ります）。

Kiro CLI は marketplace を読まないため、installer で導入します。

```bash
bash plugins/ndf/dev.kiro/install.sh
bash plugins/playwright-kit/dev.kiro/install.sh
bash plugins/mcp/<プラグイン名>/dev.kiro/install.sh
```

## プラグイン構造

各プラグインは以下の構造を持ちます：

```
plugins/{plugin-name}/
├── plugin.json                  # Agent Plugins 形式のルートマニフェスト（条件を満たす場合のみ）
├── .claude-plugin/
│   └── plugin.json              # Claude Code のマニフェスト（必須）
├── .codex-plugin/
│   └── plugin.json              # Codex のマニフェスト（ルートマニフェストを置かない場合）
├── commands/                    # スラッシュコマンド（オプション）
│   └── *.md
├── agents/                      # サブエージェント（オプション）
│   └── *.md
├── skills/                      # Skill の実体（オプション）
│   └── {skill-name}/
│       └── SKILL.md
├── manifests/                   # ランタイム別の配布 Skill 一覧（Skill を配る場合）
│   └── {claude,codex,kiro}-skills.txt
├── hooks/                       # フック（オプション）
│   ├── claude.json              # Claude Code 用
│   └── codex.json               # Codex 用
├── dev.kiro/                    # Kiro CLI 用（installer など）
│   └── install.sh
└── README.md                    # プラグイン説明
```

3 ランタイムが同じディレクトリを読みます。読む対象はマニフェストと installer が決めるため、
公開される Skill と hook はランタイムごとに異なります。`dev.kiro` は Agent Plugins 仕様 §8.2 が
定めるクライアント拡張ディレクトリです。

ルートの `plugin.json`（Agent Plugins 形式）は、`skills/` を全件公開してよく hook も持たない
プラグインにだけ置きます。判断の基準は
[Runtime Plugin Distribution 仕様](specifications/runtime-plugin-distribution.md) を参照してください。

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
3. `plugins/ndf/README.md` の「v&lt;版&gt; へ更新するとき」の節を開き、**本文をその版の変更内容へ書き直す**。見出しの版数だけを置き換えて、本文を前の版の説明のまま残さない
4. Skill の数が増減した場合は、`README.md` と `plugins/ndf/README.md` に書かれた数を書き直す
5. 破壊的変更がある場合は明示
6. テストを実行

`scripts/validate-runtime-plugins.sh` が突き合わせるのは、説明文書に書かれた Skill の数と、
更新案内の**見出しの版数**までです。見出しの版数が `plugin.json` の版から遅れると検査が
落ちるため、版を上げるときは必ずこの節へ触ることになります。ただし本文がその版の変更内容を
説明しているかは機械では判定できません。手順 3 が、その本文を人が読み直す機会にあたります。

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
   mkdir -p plugins/{plugin-name}/{.claude-plugin,.codex-plugin,commands,agents,skills,manifests,hooks,dev.kiro}
   ```

   使わないディレクトリは作らなくて構いません。Skill を配らないなら `skills/` と
   `manifests/`、hook が無いなら `hooks/`、Kiro CLI へ配らないなら `dev.kiro/` は不要です。

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

### Runtime plugin 検証

プラグインを変更した場合は、以下を実行します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/validate-runtime-plugins.sh
```

`scripts/` 自体を変更した場合は、その検査のテストも実行します。

```bash
uv run pytest scripts/tests -q
```

ローカル hook は任意で導入できます。

```bash
bash scripts/install-dev-hooks.sh
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
