# NDF Kiro Plugin

Kiro CLI 向けの NDF 配布物です。`plugins/ndf-shared` から生成された `skills/`、Kiro agent template、workflow prompt、通知用 script を同梱します。

## インストール

リポジトリ root で実行します。

```bash
# 基本（Skills + steering + agentSpawn hook）
bash plugins/ndf-kiro/install.sh

# Slack通知も有効化
bash plugins/ndf-kiro/install.sh --with-slack

# Slack通知 + Kiro側 Codex MCP 設定も生成
bash plugins/ndf-kiro/install.sh --with-slack --with-codex
```

installer は `.kiro/agents/ndf.json` を生成します。導入後の起動方法は次のとおりです。

```bash
kiro-cli chat --agent ndf
```

動作確認だけを行う場合:

```bash
bash plugins/ndf-kiro/install.sh --dry-run
```

installer は `.claude-plugin/plugin.json` を読みません。公開 Skill は build 済みの `plugins/ndf-kiro/skills/` を source として `.kiro/skills/` へ symlink します。

### 主なオプション

| オプション | 内容 |
| --- | --- |
| `--project PATH` | 現在のディレクトリではなく PATH へ導入する（`--scope workspace` のみ有効） |
| `--scope workspace\|global` | `workspace`（既定）はプロジェクトの `.kiro/`、`global` は `~/.kiro/` へ導入する |
| `--set-default` | `kiro-cli` の既定エージェントを `ndf` に切り替える（オプトイン） |
| `-y`, `--yes` | `--set-default` の確認プロンプトを省略する |
| `--with-slack` | stop フックに Slack 通知を追加する |
| `--with-codex` | Codex CLI 直接実行用プロンプトを追加する |
| `--dry-run` | 書き込みを行わず実行内容を表示する |

### 既定エージェントの切り替え

Kiro の既定エージェントは組み込みの `kiro_default` です。`kiro-cli chat` を素で起動する限り、NDF のフック・外部 AI 連携・`resources` は読み込まれません。既定として使いたい場合は `--set-default` を付けます。

```bash
bash plugins/ndf-kiro/install.sh --set-default
```

利用者の既存設定を無断で奪わないよう、`--set-default` は明示指定したときだけ動作します。実行前に現在の既定エージェントを表示し、対話端末では確認を取ります。元に戻す場合は次のとおりです。

```bash
kiro-cli agent set-default kiro_default
```

`--scope workspace`（既定）で導入したエージェントはそのプロジェクトでしか見つかりません。既定を `ndf` にしたまま別のディレクトリで `kiro-cli chat` を起動すると `user defined default ndf not found. Falling back to in-memory default` になります。どこでも既定として使いたい場合は `--scope global` と併用してください。

### 導入スコープ

| スコープ | Skills | 常時指示 | エージェント定義 |
| --- | --- | --- | --- |
| `workspace`（既定） | `.kiro/skills/` | `.kiro/steering/ndf-policies.md` | `.kiro/agents/ndf.json` |
| `global` | `~/.kiro/skills/` | `~/.kiro/steering/ndf-policies.md` | `~/.kiro/agents/ndf.json` |

常時適用したい指示は steering へ置きます。steering はエージェント選択に依存せず読み込まれるため、既定エージェントを書き換えない運用でも効きます。`.kiro/steering/ndf-policies.md` は `plugins/ndf-shared/skills/ndf-policies/SKILL.md` から生成されるため、直接編集しないでください。

### 旧バージョンからの移行

v4 系の installer は `.kiro/agents/default.json` を生成していました。この設定は Kiro の既定エージェントにならず、フックも `resources` も無効のままでした。エージェント名を `ndf` に変えたため、再インストールが必要です。

```bash
# 1. 再インストール（旧 default.json は自動でバックアップされます）
bash plugins/ndf-kiro/install.sh --with-slack

# 2. 旧設定に独自の追記があれば .kiro/agents/ndf.json へ写す
#    差分の確認例
diff .kiro/agents/default.json.bak .kiro/agents/ndf.json

# 3. 旧設定を削除する
rm .kiro/agents/default.json .kiro/agents/default.json.bak

# 4. 必要なら既定エージェントを切り替える
bash plugins/ndf-kiro/install.sh --set-default
```

Kiro 用 MCP プラグインの installer（`plugins/mcp/kiro/*/install.sh`）は `.kiro/agents/default.json` を更新します。MCP を併用する場合は、`mcpServers` を `.kiro/agents/ndf.json` へ写してください。

## Kiro CLI の制限

### `allowed-tools` は事前承認にならない

Skill frontmatter の `allowed-tools` は、プロジェクト配置（`.kiro/skills/`）では事前承認として機能しません（[kirodotdev/Kiro#6055](https://github.com/kirodotdev/Kiro/issues/6055)）。`allowed-tools: execute_bash` を持つ Skill でも `Command execute_bash is rejected because it matches one or more rules on the denied list` になります。

Kiro では、Skill の実行時にツール利用の確認が入る前提で操作してください。NDF の Skill 本文は「無確認で実行される」前提を持ちません。

### プラグイン機構がない

Kiro CLI には Skill・フック・外部連携・常時指示をまとめて配布する仕組みがありません（[kirodotdev/Kiro#8578](https://github.com/kirodotdev/Kiro/issues/8578)）。Kiro IDE の Powers は CLI では使えないため、`install.sh` による導入を継続します。

## 実機検証の記録

kiro-cli **2.16.1** / 検証日 **2026-08-07**（ランタイム規約の調査）、**2026-08-08**（本変更の導入方式の検証）。

`docs/specifications/ndf-skill-inventory.md`（Skill 棚卸台帳）は本ブランチ時点で未作成のため、検証結果はここに記録します。台帳への転記は台帳作成後に行います。

| 検証項目 | 結果 | 根拠 |
| --- | --- | --- |
| シンボリックリンク経由の Skill を認識するか | 認識する（[#6401](https://github.com/kirodotdev/Kiro/issues/6401) は 2.16.1 で再現せず） | 実体ディレクトリとリンクを並べ、両方が一覧・読み取りとも成功 |
| 起動時に Skill 本文を読み込むか | 読み込まない（[#6680](https://github.com/kirodotdev/Kiro/issues/6680) は 2.16.1 で再現せず） | 「ファイルを読まずに本文中のマーカーを出力せよ」に対し「本文なし」と応答 |
| `description` 一致で自動発動するか | 発動する（[#5867](https://github.com/kirodotdev/Kiro/issues/5867) は 2.16.1 で再現せず） | `skill://` 指定を削除した状態で、該当依頼に対し `docker-container-access/SKILL.md` を自ら読みに行った |
| プロジェクト配置で `allowed-tools` が事前承認になるか | **ならない**（[#6055](https://github.com/kirodotdev/Kiro/issues/6055)） | `allowed-tools: execute_bash` を持つ検査用 Skill が denied list で拒否された |
| `install.sh` 後に `kiro-cli agent list` へ現れるか | 現れる | `ndf  Workspace  NDF統合開発エージェント（Kiro CLI用）` |
| `--agent ndf` で agentSpawn フックが動くか | 動く | `[NDF] CLAUDE.ndf.md が検出されました…` が文脈へ注入された。`kiro_default` では注入されない |
| `--set-default` で既定が切り替わるか | 切り替わる | `agent list` の `*` が `ndf` へ移り、素の `kiro-cli chat` でも agentSpawn フックが動いた |
| `--scope global` で `~/.kiro/` へ配置されるか | 配置される | `~/.kiro/{skills,steering,prompts,agents}` が生成され、プロジェクト外でも `Global` として一覧に出た |
| steering がエージェント選択に依存せず読まれるか | 読まれる | `kiro_default` の `/context show` にも `.kiro/steering/**/*.md` の一致として現れた |

コンテキスト占有率（Skill 23 個の配布物、`/context show` の `Context files total`）:

| 構成 | 一致ファイル数 | 占有率 | 文脈ファイルの合計文字数 |
| --- | --- | --- | --- |
| 変更前 `default` エージェント | 26（`ndf-policies/SKILL.md` が二重） | 0.2% | 112,598 |
| 変更後 `ndf` エージェント | 26（重複なし、steering が 1 件増） | 0.2% | 112,621 |
| 参考: 組み込み `kiro_default` | 25 | 0.2% | - |

`resources` の二重登録は解消しましたが、代わりに steering ファイルが 1 件増えるため、総量はほぼ変わりません（+23 文字は生成ヘッダ 2 行分）。`/context show` の表示は 0.1% 刻みのため、この差は表示上変化しません。

`--scope global` でプロジェクト側に Skill がない状態では 24 ファイル / 0.1% でした。

## 開発

Skill を変更する場合は `plugins/ndf-shared/skills/` を編集し、runtime plugin を再生成します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
```
