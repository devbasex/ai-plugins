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

## 版と配布

版数の付け方（セマンティックバージョニングと接尾辞）、正式版と開発版の配布、版を上げるときの手順、
版数を書いた 15 箇所は [versioning-and-distribution.md](versioning-and-distribution.md) にあります
（版数の扱いの正本）。

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

利用者が過去の版へ戻る手順は
[versioning-and-distribution.md の「利用者が過去の版へ戻る」](versioning-and-distribution.md#利用者が過去の版へ戻る)
にあります。

## 既存プラグインへ Skill を足す

NDF（`plugins/ndf/`）へ Skill を 1 個足すときに触る箇所は 15 あります。**チェックは食い違いを
指摘しますが、直す順序と、チェックが見ない箇所は教えません。** 表の上から順に進めます。
frontmatter と命名の書き方は [AUTHORING.md](../plugins/ndf/skills/AUTHORING.md) にあります。

**`build-runtime-plugins.sh` を先に実行します。** 生成物が無いと `validate-runtime-plugins.sh`
は先頭の `--check` で止まり、`skills` 配列と `description` の食い違いを指摘するところまで進みません。

| # | 触る箇所 | 書く値 | 取りこぼしを拾うチェック |
| ---: | --- | --- | --- |
| 1 | `plugins/ndf/skills/<名前>/SKILL.md` | 実体 | `check-skill-frontmatter.py`（形だけ） |
| 2 | `plugins/ndf/manifests/<ランタイム>-skills.txt` | 配るランタイムの分だけ名前を 1 行 | `validate-runtime-plugins.sh`（どの manifest にも無いときだけ。配る先は判断） |
| 3 | `plugins/ndf/.claude-plugin/plugin.json` / `.codex-plugin/plugin.json` の `skills` 配列 | `./skills/<名前>`（manifest に載せたランタイムの分） | `validate-runtime-plugins.sh` |
| 4 | `bash scripts/build-runtime-plugins.sh` の生成物 | `dev.agy/skills/<名前>`（agy へ配るとき）、`skills/<名前>/agents/openai.yaml`（`disable-model-invocation: true` のとき）をコミット | `build-runtime-plugins.sh --check` |
| 5 | `README.md` の概要「公開Skills」とプラグイン一覧表の `ndf` 行（2 行） | `Claude Code向け core 45個` の形で 4 ランタイム | `check-doc-staleness.py` |
| 6 | `README.md` の「元Skills（N個）」 | 実体の数 | `check-doc-staleness.py` |
| 7 | `README.md` のカテゴリ内訳 | `- 運用 (2): skill-stats, statusline` の形。名前と括弧の数 | `check-doc-staleness.py`（合計だけ。どのカテゴリに入れるかは判断） |
| 8 | `plugins/ndf/README.md` の配布先の表 4 行 | `\| Claude Code \| 45 個 \|` の形 | `check-doc-staleness.py` |
| 9 | `plugins/ndf/README.md` のレイアウト図 | `唯一の実体（45 個）` | `check-doc-staleness.py` |
| 10 | `plugin.json` 3 本（claude・codex・`dev.agy`）の `description` | `45 focused NDF skills` の形 | `validate-runtime-plugins.sh` |
| 11 | `.claude-plugin/marketplace.json` の `ndf` の `description` | 10 と同じ形（Claude Code の数） | `validate-runtime-plugins.sh` |
| 12 | `scripts/tests/test_agy_distribution.py` の `EXPECTED_COUNTS` | 4 ランタイムの数 | `pytest`（件数の assert だけで、直す場所を名指ししない） |
| 13 | `plugins/ndf/README.md` の agy の節「Skill N 個」 | agy の数 | **拾わない**（#611） |
| 14 | `plugins/ndf/README.md` の「Codex の暗黙起動抑止」の数と表 | `disable-model-invocation: true` のときだけ | **拾わない**（#611） |
| 15 | `docs/ndf-plugin-reference.md` の数 | 4 ランタイムの数と実体の数 | **拾わない**（#611） |

**数の形は 3 通りあります。** `README.md` は `core 45個`（空白なし）、`plugins/ndf/README.md` は
`45 個`、`plugin.json` は `45 focused NDF skills` です。数だけを機械的に置き換えると、どれかに
当たりません。数はランタイムごとに manifest の行数から決めます。

```bash
for r in claude codex kiro agy; do
  printf '%s ' "$r"; grep -cvE '^[[:space:]]*(#|$)' "plugins/ndf/manifests/$r-skills.txt"
done
ls -d plugins/ndf/skills/*/ | wc -l   # 実体の数（6・9）
```

最後に次を実行し、すべて終了コード 0 で終わることを確かめます。

```bash
bash scripts/build-runtime-plugins.sh --check
bash scripts/validate-runtime-plugins.sh
python3 scripts/check-doc-staleness.py --root .
python3 scripts/check-skill-frontmatter.py
uv run --with pytest --with pytest-xdist pytest scripts/tests -q -n auto
```

## 既存プラグインの削除

1. `.claude-plugin/marketplace.json` から該当プラグインの項目を削除
2. 必要ならプラグインディレクトリを削除
   ```bash
   rm -rf plugins/{plugin-name}
   ```
3. 作業ブランチでコミットし、`develop` 宛の Pull Request を作成（`main` / `develop` へ直接
   push しない。規則は [AGENTS.md](../AGENTS.md) の「Git運用ルール」）
   ```bash
   git checkout -b feature/remove-{plugin-name}
   git add .claude-plugin/marketplace.json plugins/{plugin-name}
   git commit -m "Update: {plugin-name} をマーケットプレイスから外す"
   git push origin feature/remove-{plugin-name}
   gh pr create --base develop
   ```

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
常用する利用者は `main` を見ているため、この公開では届きません（[versioning-and-distribution.md](versioning-and-distribution.md) の
「開発版を試す」）。

### Runtime plugin 検証

プラグインを変更した場合は、以下を実行します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/validate-runtime-plugins.sh
```

実ランタイムのインストール経路を Docker コンテナ内で確かめる smoke test の手順は
[tests/runtime-smoke/README.md](../tests/runtime-smoke/README.md) にあります。

`scripts/` 自体を変更した場合は、そのチェックのテストも実行します。

```bash
uv run --with pytest --with pytest-xdist pytest scripts/tests -q -n auto
```

`--with pytest` を省くと `Failed to spawn: pytest` で終わります。`-n auto` は `pytest-xdist` の指定で、コア数だけ並列に回します（`--with pytest-xdist` を省くと `-n` を解釈できずに終わります）。リポジトリの根に uv の
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
