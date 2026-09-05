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
├── dev.agy/                     # agy 用（マニフェスト・hooks.json・symlink）
│   ├── plugin.json
│   └── hooks.json
└── README.md                    # プラグイン説明
```

4 ランタイムが同じディレクトリを読みます。読む対象はマニフェストと installer が決めるため、
公開される Skill と hook はランタイムごとに異なります。`dev.kiro` と `dev.agy` は Agent Plugins
仕様 §8.2 が定めるクライアント拡張ディレクトリです。

**ルート直下の `plugin.json` は、agy へ配るためには置きません。** 置くと Codex がそちらを
優先して読み、`skills/` の実体を全件配ってしまいます（`plugins/ndf` で実測）。agy 向けの
定義は `dev.agy/plugin.json` へ置きます。

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
- **接尾辞**: 開発版は `-dev.<連番>`、公開前の確認版は `-rc.<連番>`。付け方と外し方は
  AGENTS.md の「版の付け方と開発版の配布」にある

**配布のチャネルは 2 つある。** `main` が正式版、`develop` が開発版です。ここに書く手順は
版数を書き換えるところまでで、**常用する利用者へ届くのは `main` を進めた時点**です。

```bash
git push origin develop:main   # 正式版として公開する
```

進める判断と承認は `/ndf:release` が扱います。**承認を得ないまま実行しないでください。**
`main` へ入った時点で、常用する利用者の `marketplace update` に載ります。

**バージョン更新時の手順**:
1. `plugin.json`のバージョンをインクリメント
2. 変更内容をドキュメント化
3. `plugins/ndf/README.md` の「v&lt;版&gt; へ更新するとき」の節を開き、**本文をその版の変更内容へ書き直す**。見出しの版数だけを置き換えて、本文を前の版の説明のまま残さない
4. `CHANGELOG.md` の先頭へその版の節を足す。書式は Keep a Changelog に従い、変更点を
   `追加` / `変更` / `修正` / `削除` へ分類して 1〜2 行で書く。判断の理由は `CLAUDE.md` の側に置く
5. Skill の数が増減した場合は、`README.md` と `plugins/ndf/README.md` に書かれた数を書き直す
6. `python3 scripts/check-doc-staleness.py --root .` を実行し、説明文書に残った古い版数を
   出力の行番号のとおりに直す
7. 破壊的変更がある場合は明示
8. テストを実行
9. **正式版として `main` を進めた後、リリースタグを打つ**

```bash
claude plugin tag plugins/ndf --dry-run   # 打つ内容を確認する
claude plugin tag plugins/ndf --push      # ndf--v<版> を作って origin へ送る
```

`claude plugin tag` は `{プラグイン名}--v{版}` の形でタグを作り、**`plugin.json` の版と
マーケットプレイスの項目が食い違っていないか**を打つ前に検査します。タグは利用者が過去の版へ
戻るときの目印になります（「利用者が過去の版へ戻る」）。

**`.claude-plugin/marketplace.json` に `version` フィールドは置きません。** 版を持つのは
`plugins/<名前>/.claude-plugin/plugin.json` だけです。マーケットプレイス側の `description`
に書かれた `(vX.Y.Z)` は読み手向けの記載で、取得する版を決める値ではありません。

`scripts/validate-runtime-plugins.sh` が突き合わせるのは、説明文書に書かれた Skill の数と、
**版数を書いた 15 箇所**です。定義ファイルと更新案内の見出しが 8 箇所、説明文書の本文が
7 箇所あります（`README.md` の概要とプラグイン一覧表、`AGENTS.md` の 2 箇所、
`plugins/ndf/README.md` の Kiro と Codex の確認例 3 種類）。**記載を消しても検査は通りません。**
一覧は AGENTS.md の「検査が突き合わせる 15 箇所」にあります。

**検査が見るのは版数そのものの一致までで、記載の中身までは見ません。** 更新案内の本文が
その版の変更内容を説明しているかは機械では判定できません。手順 3 が、その本文を人が
読み直す機会にあたります。接尾辞の付け忘れ・外し忘れも検査では捕まりません。

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

## 利用者が過去の版へ戻る

**版数を書き換えても、過去の版のコードには戻りません。** `plugin.json` の `version` は
更新の判定に使う識別子で、どのコードを取るかは**取得元の git ref** が決めます。
`claude plugin install` に版を指定する手段はありません。

戻す手段は 2 つあり、どちらも**利用者の側の操作**です。

### 取得元ごとリリースタグへ固定する

マーケットプレイスの項目は `./plugins/ndf` のような相対パスで実体を指すため、リポジトリを
過去のタグへ固定すれば、その時点のプラグインが入ります。

```bash
claude plugin marketplace add devbasex/ai-plugins@<タグ>
```

**最初のタグは `ndf--v9.5.0` です**（手元のタグは `git tag -l` で確かめられます）。
それより前の版（9.4.0 以前）はタグを打っていないため、**タグでは戻せません**。戻すなら、
その版のコミットを調べて ref に指定します。

**同じ取得元の他のプラグインも同時に過去の状態になります。** `playwright-kit` や `mcp-*` を
最新のまま使いたい場合は次の方法を採ります。

### 対象のプラグインだけを固定する

別名のマーケットプレイスを 1 つ用意し、`git-subdir` で対象のディレクトリと ref を直接指します。

```json
{
  "name": "ai-plugins-pinned",
  "owner": {"name": "takemi-ohama"},
  "plugins": [
    {"name": "ndf",
     "source": {"source": "git-subdir",
                "url": "https://github.com/devbasex/ai-plugins.git",
                "path": "plugins/ndf",
                "ref": "<タグ>"}}
  ]
}
```

`ref` はブランチまたはタグ、`sha` は 40 文字のコミット。両方あるときは `sha` が効きます。
この形が `claude plugin validate` を通ることは確認済みです。

この定義を読み込ませて導入します。**JSON ファイルのパスを直接渡せます**（実機で確認）。

```bash
claude plugin marketplace add <この JSON のパス> --scope local
claude plugin install ndf@ai-plugins-pinned
```

**`--scope local` を付けます。** 固定は一時的な操作なので、利用者全体の設定へ残しません。
戻すときは `claude plugin marketplace remove ai-plugins-pinned` です。名前が `ai-plugins` と
違うため、通常の取得元は消えません。

**固定した版と最新版を同時に有効にしないでください。** どちらの `/ndf:*` が使われるかが
定まりません。切り替えるときは、先に一方を無効にします。

**名前は必ず変えます。** `ai-plugins` のまま追加すると、利用者の取得元が置き換わります
（AGENTS.md の「版の付け方と開発版の配布」）。

## 検証とテスト

### ローカルテスト

**ローカルのディレクトリをマーケットプレイスとして追加しない。** `marketplace.json` の `name`
がリポジトリと同じであるため、追加すると**利用者のグローバルな取得元がそのディレクトリへ
上書きされる**（`--scope local` を指定しても起きる）。続けて `marketplace remove` すると
clone と導入記録まで消える。

手元で確かめる手段は 2 つある。

```bash
# Kiro CLI: installer が任意のディレクトリへ導入する。取得元を書き換えない
bash plugins/ndf/dev.kiro/install.sh --project <検証用ディレクトリ> --yes

# Claude Code: 1 つのプラグインをディレクトリから読み込む
claude --plugin-dir plugins/ndf
```

**通常の取得経路で確かめたい場合は、開発版のチャネル（`develop`）へ出す。** 版数へ
`-dev.<連番>` を付けて公開し、検証に参加する利用者が `#develop` を付けた登録で取得します。
常用する利用者は `main` を見ているため、この公開では届きません（AGENTS.md の「版の付け方と
開発版の配布」）。

### Runtime plugin 検証

プラグインを変更した場合は、以下を実行します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/validate-runtime-plugins.sh
```

`scripts/` 自体を変更した場合は、その検査のテストも実行します。

```bash
uv run --with pytest pytest scripts/tests -q
```

`--with pytest` を省くと `Failed to spawn: pytest` で終わります。リポジトリの根に uv の
対象プロジェクト（`pyproject.toml`）が無く、`pytest` が環境にも入っていないためです。
`plugins/ndf/skills/*/tests/` の既存のテストも同じ形で実行します。

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
- `plugins/<名前>/.claude-plugin/plugin.json` の `version` を更新（**版を持つのはここだけ**。
  `marketplace.json` に `version` フィールドは置かない）
- 版数を書いた説明文書の記載（`description` の `(vX.Y.Z)`、更新案内の見出し）を揃える。
  これは読み手向けの記載で、取得する版は変えない
- 取得元が自動更新されるとは限らない。利用者側で `claude plugin marketplace update` を実行する
- Claude Codeを再起動
