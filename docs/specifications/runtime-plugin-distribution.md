# Runtime Plugin Distribution 仕様

## 概要

AI Plugins marketplace は、Claude Code / Codex / Kiro CLI 向けに runtime 別の plugin 配布物を提供する。

NDF plugin は runtime ごとの manifest、hook、agent、installer 差分を持つため、共通編集元と配布先を分離する。MCP plugin も runtime ごとに Claude Code / Codex / Kiro CLI 用の配布ディレクトリを持ち、同じ plugin 名で導入できる構成にする。

## 対象範囲

対象 runtime:

| runtime | marketplace / 導入方式 | NDF 配布物 | MCP 配布物 |
|---|---|---|---|
| Claude Code | `.claude-plugin/marketplace.json` | `plugins/ndf-claude` | `plugins/mcp/claude/*` |
| Codex | `.agents/plugins/marketplace.json` | `plugins/ndf-codex` | `plugins/mcp/codex/*` |
| Kiro CLI | installer | `plugins/ndf-kiro` | `plugins/mcp/kiro/*` |

共通編集元:

| 種別 | パス | 用途 |
|---|---|---|
| NDF shared | `plugins/ndf-shared` | runtime 配布物へ同期する Skill / scripts / manifest の編集元 |
| MCP shared | `plugins/mcp/shared/*` | runtime 別 MCP plugin 生成元 |

## NDF Plugin 配布仕様

NDF plugin の plugin name は全 runtime で `ndf` を維持する。旧 `plugins/ndf` 配布物は廃止し、runtime 別ディレクトリを配布単位とする。

| runtime | manifest / installer | 主な内容 |
|---|---|---|
| Claude Code | `plugins/ndf-claude/.claude-plugin/plugin.json` | Claude Code agents、hooks、skills、scripts |
| Codex | `plugins/ndf-codex/.codex-plugin/plugin.json` | Codex 用 skills、hooks、scripts |
| Kiro CLI | `plugins/ndf-kiro/install.sh` | `.kiro/agents/ndf.json`、`.kiro/steering/ndf-policies.md`、`.kiro/skills/` symlink、prompts、任意 hook |

`plugins/ndf-shared/manifests/{claude,codex,kiro}-skills.txt` は runtime ごとの配布 Skill 一覧を定義する。`scripts/build-runtime-plugins.sh` はこの manifest を読み、`plugins/ndf-shared/skills` から各 runtime の `skills/` へ同期する。

Skill 内のパス参照の書き換えは、runtime ごとに次のとおりである。

| runtime | 書き換え | 対象 |
|---|---|---|
| Codex | なし | — |
| Kiro CLI | `plugins/ndf-kiro` を既定 root とする参照に変換 | `statusline` |

`fix` / `cross-review` / `cross-refactoring` はプラグインルート起点をやめ、Skill ディレクトリ
起点（`$SKILL_DIR/scripts`、隣の Skill へは `$SKILL_DIR/../<Skill 名>/`）で参照する。配布
ディレクトリの形が runtime ごとに違っても、Skill 直下の `scripts/` の位置は変わらないため
書き換えが要らない。Kiro CLI が `.kiro/skills/` へ張った symlink 越しでも `..` は解決先を
経由して届く。Codex 向けの書き換えは対象が 0 になり、生成物が共通編集元と一致する。

`statusline` だけは Skill 直下ではなくプラグインルート直下の `scripts/` を呼ぶ。Claude Code と
Kiro CLI へ配っており、Kiro にはプラグインルートを示す環境変数が無いため、この 1 個だけ
書き換えが残る。

scripts は `plugins/ndf-shared/scripts` から `plugins/ndf-{runtime}/scripts` へ同期する。

## MCP Plugin 配布仕様

MCP plugin は `plugins/mcp/shared/<plugin-name>` を編集元とし、runtime 別に以下へ配布する。

| runtime | 配布先 | 導入方式 |
|---|---|---|
| Claude Code | `plugins/mcp/claude/<plugin-name>` | Claude marketplace |
| Codex | `plugins/mcp/codex/<plugin-name>` | Codex marketplace |
| Kiro CLI | `plugins/mcp/kiro/<plugin-name>` | `install.sh` |

各 runtime で plugin name は同一にする。例: `mcp-bigquery`、`mcp-redash`、`mcp-serena`。

Claude 用配布物は `.claude-plugin/plugin.json` と `.mcp.json` を持つ。Codex 用配布物は `.codex-plugin/plugin.json` と `.mcp.json` を持つ。Kiro 用配布物は `.mcp.json` と `install.sh` を持つ。

Kiro MCP installer は対象 project の `.mcp.json` へ MCP server 設定を merge する。hooks や skills を持つ MCP plugin では、必要に応じて `.kiro/agents/default.json` や `.kiro/skills/` も更新する。

NDF installer が生成する agent は `.kiro/agents/ndf.json` であり、MCP installer が更新する `.kiro/agents/default.json` とは別である。NDF と Kiro MCP plugin を併用する場合、MCP server 設定は `ndf.json` へ写す必要がある（`plugins/ndf-kiro/README.md`「旧バージョンからの移行」）。MCP installer 側の出力先統一は未対応。

## Marketplace

Claude Code marketplace は `.claude-plugin/marketplace.json` で管理する。各 entry の `source` は Claude 用配布ディレクトリを指す。

Codex marketplace は `.agents/plugins/marketplace.json` で管理する。各 entry の `source.path` は Codex 用配布ディレクトリを指す。

Kiro CLI は repository root の marketplace manifest ではなく、`plugins/ndf-kiro/install.sh` と `plugins/mcp/kiro/*/install.sh` を導入入口とする。

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

1. `plugins/ndf-shared/skills/<skill>/` を編集する。
2. 必要なら `plugins/ndf-shared/manifests/*-skills.txt` を更新する。
3. `bash scripts/build-runtime-plugins.sh` を実行する。
4. `bash scripts/validate-runtime-plugins.sh` を実行する。

MCP plugin を変更する場合:

1. `plugins/mcp/shared/<plugin-name>/` を編集する。
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
- [NDF Claude README](../../plugins/ndf-claude/README.md)
- [NDF Codex README](../../plugins/ndf-codex/README.md)
- [NDF Kiro README](../../plugins/ndf-kiro/README.md)
- [NDF shared README](../../plugins/ndf-shared/README.md)
