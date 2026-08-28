# Runtime Plugin Distribution 仕様

## 概要

AI Plugins marketplace は、Claude Code / Codex / Kiro CLI 向けに runtime 別の plugin 配布物を提供する。

NDF plugin と playwright-kit plugin は、3 runtime 分をプラグインごとに 1 ディレクトリへまとめる。Skill の実体は `skills/` の 1 箇所だけで、どの runtime へ配るかは `manifests/*-skills.txt` と各 manifest の `skills` 配列が決める。runtime 固有のファイルは名前空間ディレクトリへ分ける。MCP plugin は runtime ごとの配布ディレクトリを持つ構成を当面維持する。

## 対象範囲

対象 runtime:

| runtime | marketplace / 導入方式 | NDF 配布物 | MCP 配布物 |
|---|---|---|---|
| Claude Code | `.claude-plugin/marketplace.json` | `plugins/ndf` | `plugins/mcp/*` |
| Codex | `.agents/plugins/marketplace.json` | `plugins/ndf` | `plugins/mcp/*` |
| Kiro CLI | installer | `plugins/ndf` | `plugins/mcp/*` |

## NDF Plugin 配布仕様

NDF plugin の plugin name は全 runtime で `ndf` を維持し、配布物は `plugins/ndf` の 1 ディレクトリにまとめる。

| runtime | manifest / installer | 主な内容 |
|---|---|---|
| Claude Code | `plugins/ndf/.claude-plugin/plugin.json` | `agents/`、`hooks/claude.json`、`skills` 配列（27 個） |
| Codex | `plugins/ndf/.codex-plugin/plugin.json` | `hooks/codex.json`、`skills` 配列（25 個） |
| Kiro CLI | `plugins/ndf/dev.kiro/install.sh` | `.kiro/agents/ndf.json`、`.kiro/steering/ndf-policies.md`、`.kiro/skills/` symlink、prompts、任意 hook |

`plugins/ndf/manifests/{claude,codex,kiro}-skills.txt` が runtime ごとの配布 Skill 一覧を定義する。
Claude Code と Codex は manifest と同じ内容を各 plugin.json の `skills` 配列へ書き、Kiro CLI は
installer が `kiro-skills.txt` を読んで symlink を張る。Skill の実体は `plugins/ndf/skills/` だけで、
runtime ごとの複製は無い。

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

Claude 用配布物は `.claude-plugin/plugin.json` と `.mcp.json` を持つ。Codex 用配布物は `.codex-plugin/plugin.json` と `.mcp.json` を持つ。Kiro 用配布物は `.mcp.json` と `install.sh` を持つ。

Kiro MCP installer は対象 project の `.mcp.json` へ MCP server 設定を merge する。hooks や skills を持つ MCP plugin では、必要に応じて `.kiro/agents/default.json` や `.kiro/skills/` も更新する。

NDF installer が生成する agent は `.kiro/agents/ndf.json` であり、MCP installer が更新する `.kiro/agents/default.json` とは別である。NDF と Kiro MCP plugin を併用する場合、MCP server 設定は `ndf.json` へ写す必要がある（`plugins/ndf/README.md`「旧バージョンからの移行」）。MCP installer 側の出力先統一は未対応。

## Marketplace

Claude Code marketplace は `.claude-plugin/marketplace.json` で管理する。各 entry の `source` は Claude 用配布ディレクトリを指す。

Codex marketplace は `.agents/plugins/marketplace.json` で管理する。各 entry の `source.path` は Codex 用配布ディレクトリを指す。

Kiro CLI は repository root の marketplace manifest ではなく、`plugins/ndf/dev.kiro/install.sh` と `plugins/mcp/*/dev.kiro/install.sh` を導入入口とする。

## Build / Validation

runtime 配布物の同期は `scripts/build-runtime-plugins.sh` で行う。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
```

`--check` は生成先と shared source の差分を比較し、drift がある場合に非 0 で終了する。

`.claude-plugin/marketplace.json` と `plugins/<family>-codex/.codex-plugin/plugin.json` は生成物では
なく手で更新する。この 2 つは build の対象外で drift 検査に掛からないため、版数と Skill 数は
`validate-runtime-plugins.sh` の突き合わせ検査で担保する。description から Skill 数を読み取れない
場合もエラーとして扱う（記述を消すことで検査が素通りするのを防ぐ）。

総合検証は `scripts/validate-runtime-plugins.sh` で行う。

```bash
bash scripts/validate-runtime-plugins.sh
```

検証内容:

| 領域 | 確認内容 |
|---|---|
| 生成物 | `build-runtime-plugins.sh --check` |
| JSON | marketplace、plugin manifest、`.mcp.json` の parse |
| marketplace | Claude / Codex source path と runtime manifest の存在 |
| Skill manifest | shared skill と runtime skill の存在 |
| MCP runtime | shared MCP plugin に対応する claude / codex / kiro 配布先の存在 |
| Claude Code | `claude plugin validate` が使える環境では NDF と marketplace を検証 |
| Kiro CLI | NDF installer と MCP installer の `--dry-run` |
| 版数・Skill 数 | Claude 版 `plugin.json` の `version` を基準に、Codex 版 `version`、marketplace と両 plugin.json の description 内 `(vX.Y.Z)`、description の Skill 数と `manifests/<runtime>-skills.txt` の実数を突き合わせる |
| docs | `scripts/check-markdown-links.py` による local link 検証 |

## CI

`.github/workflows/runtime-plugin-validate.yml` は runtime 配布物の build check、manifest validation、Markdown link check を実行する。

`.github/workflows/runtime-plugin-smoke.yml` は runtime ごとの非認証 container smoke test を実行する。

`.github/workflows/runtime-plugin-authenticated-smoke.yml` は protected environment から secret を受け取り、手動で認証付き smoke test を実行する。

container smoke test の詳細は [Runtime Plugin Container Smoke Test 仕様](runtime-plugin-container-smoke.md) を参照する。

## セキュリティ

- 認証情報、API token、secret、private key は repository に含めない。
- `.mcp.json` と README では `${ENV_NAME}` placeholder を使い、secret 実値を書かない。
- runtime smoke test は host の credential directory を mount しない。
- Kiro installer と MCP installer は project 配下の設定ファイルだけを更新する。
- Codex / Claude / Kiro の hook script は runtime 別配布物に分離し、payload 差異を混同しない。

## 運用

NDF Skill を変更する場合:

1. `plugins/ndf/skills/<skill>/` を編集する。
2. 必要なら `plugins/ndf/manifests/*-skills.txt` と、各 plugin.json の `skills` 配列を更新する。
3. `bash scripts/build-runtime-plugins.sh` を実行する。
4. `bash scripts/validate-runtime-plugins.sh` を実行する。

MCP plugin を変更する場合:

1. `plugins/mcp/<plugin-name>/` を編集する。
2. `bash scripts/build-runtime-plugins.sh` を実行して runtime 配布先へ反映する。
3. `bash scripts/validate-runtime-plugins.sh` を実行する。

runtime 配布先を直接編集した場合、`build-runtime-plugins.sh --check` で shared source との drift として検出される。例外的な runtime 固有ファイルは build script の生成ルールに含める。

## テスト観点

| 観点 | 確認方法 |
|---|---|
| shared source と runtime 配布物の同期 | `bash scripts/build-runtime-plugins.sh --check` |
| marketplace / manifest / MCP config | `bash scripts/validate-runtime-plugins.sh` |
| Claude Code install smoke | `bash scripts/runtime-smoke-test.sh --runtime claude` |
| Codex install smoke | `bash scripts/runtime-smoke-test.sh --runtime codex` |
| Kiro CLI install smoke | `bash scripts/runtime-smoke-test.sh --runtime kiro` |
| 旧パス残存 | docs / scripts / plugin 配布物に旧 `plugins/ndf` や `plugins/mcp-*` 配置を案内する参照が残っていないこと |

## 関連リンク

- [Runtime Plugin Container Smoke Test 仕様](runtime-plugin-container-smoke.md)
- [NDF 知識構造・Kiro CLI 仕様](ndf-knowledge-and-kiro.md)
- [NDF Plugin リファレンス](../ndf-plugin-reference.md)
- [NDF Claude README](../../plugins/ndf/README.md)
- [NDF Codex README](../../plugins/ndf/README.md)
- [NDF Kiro README](../../plugins/ndf/README.md)
- [NDF shared README](../../plugins/ndf/README.md)
