# Runtime Plugin Distribution 仕様

## 概要

AI Plugins marketplace は、Claude Code / Codex / Kiro CLI / agy へ同じ plugin を配布する。

NDF plugin と playwright-kit plugin は、runtime 分をプラグインごとに 1 ディレクトリへまとめる（NDF は 4 runtime、playwright-kit は 3 runtime）。Skill の実体は `skills/` の 1 箇所だけで、どの runtime へ配るかは `manifests/*-skills.txt` と各 manifest の `skills` 配列が決める。runtime 固有のファイルは名前空間ディレクトリへ分ける。MCP plugin も同じく 1 ディレクトリにまとめ、サーバ定義は `.mcp.json` の 1 箇所だけを持つ。

## 対象範囲

対象 runtime:

| runtime | marketplace / 導入方式 | NDF 配布物 | MCP 配布物 |
|---|---|---|---|
| Claude Code | `.claude-plugin/marketplace.json` | `plugins/ndf` | `plugins/mcp/*` |
| Codex | `.claude-plugin/marketplace.json` | `plugins/ndf` | `plugins/mcp/*` |
| Kiro CLI | installer | `plugins/ndf` | `plugins/mcp/*` |
| agy | `agy plugin install <clone>/plugins/ndf/dev.agy` | `plugins/ndf` | — |

## NDF Plugin 配布仕様

NDF plugin の plugin name は全 runtime で `ndf` を維持し、配布物は `plugins/ndf` の 1 ディレクトリにまとめる。

| runtime | manifest / installer | 主な内容 |
|---|---|---|
| Claude Code | `plugins/ndf/.claude-plugin/plugin.json` | `agents/`、`hooks/claude.json`、`skills` 配列（27 個） |
| Codex | `plugins/ndf/.codex-plugin/plugin.json` | `hooks/codex.json`、`skills` 配列（25 個） |
| Kiro CLI | `plugins/ndf/dev.kiro/install.sh` | `.kiro/agents/ndf.json`、`.kiro/steering/ndf-policies.md`、`.kiro/skills/` symlink、prompts、任意 hook |
| agy | `plugins/ndf/dev.agy/plugin.json` | `dev.agy/hooks.json`、`dev.agy/skills/` の symlink、`agents` と `scripts` への symlink |

`plugins/ndf/manifests/{claude,codex,kiro,agy}-skills.txt` が runtime ごとの配布 Skill 一覧を
定義する。Claude Code と Codex は manifest と同じ内容を各 plugin.json の `skills` 配列へ書き、
Kiro CLI は installer が `kiro-skills.txt` を読んで symlink を張る。agy は Skill を絞る手段を
利用者側の設定にしか持たないため、`scripts/build-runtime-plugins.sh` が `agy-skills.txt` から
`dev.agy/skills/` の symlink を生成する。Skill の実体は `plugins/ndf/skills/` だけで、runtime
ごとの複製は無い。

**ルート直下の `plugin.json` は置かない。** 置くと Codex が `.codex-plugin/plugin.json` より
優先して読み、`skills` 配列ではなく `skills/` の実体を全件配る（`plugins/ndf` で実測）。agy の
プラグインの目印はディレクトリ直下の `plugin.json` であるため、agy 向けの定義は
`dev.agy/plugin.json` へ置く。

どの manifest にも載せない Skill は `plugins/ndf/optional-skills/` へ置く。`skills/` を配布 Skill の
実体だけに保つことで、絞り込みの結果によらず公開数が変わらない。

`dev.kiro` は Agent Plugins 1.0.0 §8.2 が定めるクライアント拡張ディレクトリである。

**Skill 内のパス参照は runtime ごとに書き換えない。** Skill が呼ぶスクリプトは Skill ディレクトリ
起点（`$SKILL_DIR/scripts`、隣の Skill へは `$SKILL_DIR/../<Skill 名>/`、プラグインルート直下へは
`$SKILL_DIR/../../scripts/`）で参照する。配布ディレクトリの形が runtime ごとに違っても、Skill
ディレクトリからの位置は変わらないためである。Kiro CLI が `.kiro/skills/` へ張った symlink 越し
でも `..` は解決先を経由して届く。

### Skill ディレクトリの解決

Skill ディレクトリ起点で書いた Skill は、候補を順に試し、`scripts/` を持つ最初のものを
絶対パスで採る。

| 順 | 候補 | 当たる runtime |
|---|---|---|
| 1 | `${CLAUDE_PLUGIN_ROOT}/skills/<Skill 名>` | Claude Code |
| 2 | エージェントが `<この Skill のディレクトリ>` を置き換えたパス | Codex |
| 3 | `.kiro/skills/<Skill 名>` | Kiro CLI（workspace scope） |
| 4 | `$HOME/.kiro/skills/<Skill 名>` | Kiro CLI（global scope） |

2 は runtime が自動で展開するものではない。SKILL.md のコメントが、実行前にこのプレース
ホルダを実際のパスへ置き換えるようエージェントへ指示する。置き換えなかった場合はこの候補が
外れるだけで、別の場所を読むことはない。

2 を `.kiro` より先に見るのは、Kiro の設定を持つリポジトリで Codex や Claude Code を
動かしたときに、別 runtime の Skill を選ばないためである。

`${CLAUDE_PLUGIN_ROOT}` はシングルクォートで囲んで代入し、値が `$` で始まっていたら
「置き換えられなかった」と判断して候補から外す。シェルに展開させないため、未定義の変数を
読まずに済み `set -u` でも落ちない。

採用した候補は `cd … && pwd` で絶対パスへ直す。Kiro CLI の候補は相対パスであり、そのまま
持ち回ると `cross-review` のように途中で worktree へ移る Skill で参照が外れる。

どれも当たらなければメッセージを出して止める。黙って別の場所を読むことはない。

根拠にした実測（Claude Code 2.1.250 / Codex CLI 0.149.0）は次のとおり。

| runtime | SKILL.md 内の `${CLAUDE_PLUGIN_ROOT}` | シェルの環境変数 | Skill のパスをモデルへ渡すか |
|---|---|---|---|
| Claude Code | プラグインルートの絶対パスへ展開する | 置かない | 渡す（絶対パス） |
| Codex | 展開しない | 置かない | 渡す（絶対パス） |
| Kiro CLI | 展開しない | 置かない | 渡す（`.kiro/skills/<Skill 名>` の相対パス） |

scripts は `plugins/ndf/scripts` に 1 箇所だけ置き、3 runtime が同じ実体を読む。

## MCP Plugin 配布仕様

MCP plugin も `plugins/mcp/<plugin-name>` の 1 ディレクトリにまとめる。サーバ定義は `.mcp.json` の
1 箇所だけで、3 runtime が同じファイルを読む。

| runtime | 読むもの | 導入方式 |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` と `.mcp.json`（自動探索） | Claude marketplace |
| Codex | `.codex-plugin/plugin.json` の `mcpServers: "./.mcp.json"` | Codex marketplace |
| Kiro CLI | `dev.kiro/install.sh` が `.mcp.json` を読んで導入先へ合成する | installer |

ルートの `plugin.json`（Agent Plugins 形式）は置かない。Agent Plugins 1.0.0 の `mcp.json` は
stdio サーバに `type` / `command` / `args` / `env` / `cwd` しか許さず（`additionalProperties: false`）、
10 個中 6 個が使っている `envFile` を表現できないためである。

各 runtime で plugin name は同一にする。例: `mcp-bigquery`、`mcp-redash`、`mcp-serena`。

配布ディレクトリは `.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、`.mcp.json`、
`dev.kiro/install.sh` を持つ。hook や Skill を持つプラグインは `hooks/` と `skills/` も持つ。

Kiro MCP installer は対象 project の `.mcp.json` へ MCP server 設定を merge する。hooks や skills を持つ MCP plugin では、必要に応じて `.kiro/agents/default.json` や `.kiro/skills/` も更新する。hook の command に含まれるプラグインルートのプレースホルダは、installer が張った symlink の絶対パスへ置き換える。

NDF installer が生成する agent は `.kiro/agents/ndf.json` であり、MCP installer が更新する `.kiro/agents/default.json` とは別である。NDF と Kiro MCP plugin を併用する場合、MCP server 設定は `ndf.json` へ写す必要がある（`plugins/ndf/README.md`「旧バージョンからの移行」）。MCP installer 側の出力先統一は未対応。

## Marketplace

marketplace 定義は `.claude-plugin/marketplace.json` の 1 つだけである。各 entry の `source` は
プラグインの配布ディレクトリを指す。

Codex は専用の定義（`.agents/plugins/marketplace.json`）が無ければこの定義へフォールバックする。
Codex が要求する `policy` / `category` / `interface` は同じ entry に含める。Claude Code はこれらを
読み込み時に無視し、`claude plugin validate` は warning 付きで通る。

Kiro CLI は repository root の marketplace manifest ではなく、`plugins/ndf/dev.kiro/install.sh` と `plugins/mcp/*/dev.kiro/install.sh` を導入入口とする。

## Build / Validation

生成物の同期は `scripts/build-runtime-plugins.sh` で行う。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
```

生成対象は 2 つだけである。

| 生成物 | 対象 | 内容 |
|---|---|---|
| `plugins/<family>/skills/<Skill 名>/agents/openai.yaml` | Skill を配るプラグイン | Codex の暗黙起動を抑える policy。codex 用 manifest に載り frontmatter が `disable-model-invocation: true` の Skill だけ |
| `plugins/mcp/<plugin-name>/dev.kiro/install.sh` | MCP plugin | Kiro CLI 向け installer。内容はプラグイン名に依存しない |

`--check` は生成先との差分を比較し、drift がある場合に非 0 で終了する。

marketplace 定義と各 plugin manifest は生成物ではなく手で更新する。build の対象外で drift 検査に
掛からないため、版数と Skill 数、`skills` 配列と manifest の一致は `validate-runtime-plugins.sh` の
突き合わせ検査で担保する。description から Skill 数を読み取れない場合もエラーとして扱う
（記述を消すことで検査が素通りするのを防ぐ）。

総合検証は `scripts/validate-runtime-plugins.sh` で行う。

```bash
bash scripts/validate-runtime-plugins.sh
```

検証内容:

| 領域 | 確認内容 |
|---|---|
| 生成物 | `build-runtime-plugins.sh --check` |
| JSON | marketplace、plugin manifest、`.mcp.json` の parse |
| marketplace | source path と plugin manifest の存在、Codex が要求する `policy` / `category` / `interface` |
| Skill manifest | manifest に載る Skill の実在と、`skills/` にあってどの manifest にも載らない Skill の検出 |
| MCP | `.mcp.json`、両 manifest、`dev.kiro/install.sh` の存在と、Codex manifest の `mcpServers` 指定 |
| Claude Code | `claude plugin validate` が使える環境では NDF と marketplace を検証 |
| Kiro CLI | NDF installer と MCP installer の `--dry-run` |
| 版数・Skill 数 | Claude 版 `plugin.json` の `version` を基準に、Codex 版 `version`、marketplace と両 plugin.json の description 内 `(vX.Y.Z)`、description の Skill 数と `manifests/<runtime>-skills.txt` の実数を突き合わせる |
| docs | `scripts/check-markdown-links.py` による local link 検証 |

## CI

`.github/workflows/runtime-plugin-validate.yml` は生成物の build check、Skill frontmatter check、manifest validation、Markdown link check を実行する。

`.github/workflows/runtime-plugin-smoke.yml` は runtime ごとの非認証 container smoke test を実行する。

`.github/workflows/runtime-plugin-authenticated-smoke.yml` は protected environment から secret を受け取り、手動で認証付き smoke test を実行する。

container smoke test の詳細は [Runtime Plugin Container Smoke Test 仕様](runtime-plugin-container-smoke.md) を参照する。

## Agent Plugins 形式との関係

[Agent Plugins](https://github.com/agentplugins/agent-plugins-spec) は Amazon / Cursor / Microsoft /
OpenAI / Vercel が策定するプラグイン形式である。本リポジトリの配布構成はこの形式を全面採用
しては**いない**。採った点と採らなかった点を、判断の根拠とともに残す。

### 採った点

| 項目 | 内容 |
|---|---|
| ルートマニフェスト（§5） | `playwright-kit` にだけ置く。Skill 4 個を 3 runtime へ同じだけ配り hook を持たないため、絞り込みを持たない形式で足りる |
| `skills/` の固定位置（§6.1） | 全プラグインが従う。Skill の実体は `skills/` の 1 箇所だけに置く |
| クライアント拡張ディレクトリ（§8.2） | Kiro CLI 向けのファイルを `dev.kiro/` へ置く。`dev.kiro` は kiro.dev の逆ドメイン |

### 採らなかった点

| 項目 | 理由 |
|---|---|
| `ndf` へのルートマニフェスト | 配布 Skill が Claude Code 27 / Codex 25 と異なり、hook も持つ。ルートマニフェストは `skills/` を全件公開して hook を持てない（§6.1、マニフェストは 9 項目のみ） |
| MCP プラグインへのルートマニフェスト | 下記のとおり `mcp.json` が現行の設定を表現できず、ルートマニフェストを置くと Codex がそちらを優先するため |
| `mcp.json`（§7.2） | スキーマが stdio サーバに `type` / `command` / `args` / `env` / `cwd` しか許さない（`additionalProperties: false`）。MCP プラグイン 10 個のうち 6 個が使う `envFile` を表現できず、2 個が使う `type: "http"` も無い。かわりに Claude Code 形式の `.mcp.json` を 1 つ置き、Codex は `.codex-plugin/plugin.json` の `mcpServers` から参照する |

### runtime の対応状況（実測）

| runtime | 版数 | ルートマニフェスト |
|---|---|---|
| Codex CLI | 0.149.0 | 読む（`plugin.json` > `.codex-plugin` > `.claude-plugin` の順） |
| Claude Code | 2.1.250 | 読まない（`.claude-plugin/plugin.json` のみ） |
| Kiro CLI | 2.19.1 | 読まない（CLI にプラグイン機構が無い。Kiro IDE 1.0.288 は対応済み） |

Kiro CLI がルートマニフェストを読むようになった時点で、`dev.kiro/install.sh` の役割は Kiro 側の
導入コマンドへ移せる。

## セキュリティ

- 認証情報、API token、secret、private key は repository に含めない。
- `.mcp.json` と README では `${ENV_NAME}` placeholder を使い、secret 実値を書かない。
- runtime smoke test は host の credential directory を mount しない。
- Kiro installer と MCP installer は project 配下の設定ファイルだけを更新する。
- Codex / Claude の hook 定義は `hooks/codex.json` と `hooks/claude.json` に分け、payload 差異を混同しない。

## 運用

NDF Skill を変更する場合:

1. `plugins/ndf/skills/<skill>/` を編集する。
2. 必要なら `plugins/ndf/manifests/*-skills.txt` と、各 plugin.json の `skills` 配列を更新する。
3. `bash scripts/build-runtime-plugins.sh` を実行する。
4. `bash scripts/validate-runtime-plugins.sh` を実行する。

MCP plugin を変更する場合:

1. `plugins/mcp/<plugin-name>/` を編集する。
2. `bash scripts/build-runtime-plugins.sh` を実行する（Kiro installer の生成）。
3. `bash scripts/validate-runtime-plugins.sh` を実行する。

生成物（`agents/openai.yaml` と `dev.kiro/install.sh`）を直接編集した場合、
`build-runtime-plugins.sh --check` で drift として検出される。

## テスト観点

| 観点 | 確認方法 |
|---|---|
| 生成物の同期 | `bash scripts/build-runtime-plugins.sh --check` |
| marketplace / manifest / MCP config | `bash scripts/validate-runtime-plugins.sh` |
| Claude Code install smoke | `bash scripts/runtime-smoke-test.sh --runtime claude` |
| Codex install smoke | `bash scripts/runtime-smoke-test.sh --runtime codex` |
| Kiro CLI install smoke | `bash scripts/runtime-smoke-test.sh --runtime kiro` |
| 旧パス残存 | docs / scripts / plugin に `plugins/<family>-{shared,claude,codex,kiro}` や `plugins/mcp/{shared,claude,codex,kiro}` を案内する参照が残っていないこと |

## 関連リンク

- [Runtime Plugin Container Smoke Test 仕様](runtime-plugin-container-smoke.md)
- [NDF 知識構造・Kiro CLI 仕様](ndf-knowledge-and-kiro.md)
- [NDF Plugin リファレンス](../ndf-plugin-reference.md)
- [NDF プラグイン README](../../plugins/ndf/README.md)
- [playwright-kit プラグイン README](../../plugins/playwright-kit/README.md)
- [Agent Plugins 仕様](https://github.com/agentplugins/agent-plugins-spec)
