# Kiro CLI の運用と制限

Kiro CLI 固有の詳細です。導入の手順そのものは
[README の「Kiro CLI」](../README.md#kiro-cli)にあります。

## Kiro CLI の運用

### 主なオプション

| オプション | 内容 |
| --- | --- |
| `--project PATH` | 現在のディレクトリではなく PATH へ導入する（`--scope workspace` のみ有効） |
| `--scope workspace\|global` | `workspace`（既定）はプロジェクトの `.kiro/`、`global` は `~/.kiro/` へ導入する |
| `--set-default` | `kiro-cli` の既定エージェントを `ndf` に切り替える（オプトイン） |
| `-y`, `--yes` | `--set-default` の確認プロンプトを省略する |
| `--with-slack` | stop フックに Slack 通知を追加する |
| `--with-codex` | Codex MCP サーバ設定（`ndf.json` の `mcpServers.codex`）と、Codex CLI 直接実行用プロンプトを追加する |
| `--dry-run` | 書き込みを行わず実行内容を表示する |

### 既定エージェントの切り替え

Kiro の既定エージェントは組み込みの `kiro_default` です。`kiro-cli chat` を素で起動する限り、NDF のフック・外部 AI 連携・`resources` は読み込まれません。既定として使いたい場合は `--set-default` を付けます。

```bash
bash plugins/ndf/dev.kiro/install.sh --set-default
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

常時適用したい指示は steering へ置きます。steering はエージェント選択に依存せず読み込まれるため、既定エージェントを書き換えない運用でも効きます。`.kiro/steering/ndf-policies.md` は `plugins/ndf/skills/ndf-policies/SKILL.md` から生成されるため、直接編集しないでください。

`ndf-policies` は steering の生成元としてのみ使うため、`.kiro/skills/` へは symlink しません。Kiro は `.kiro/skills/*/SKILL.md` と `.kiro/steering/**/*.md` の両方を文脈へ読み込むため、両方に置くと同じ内容が 2 回注入されます。`plugins/ndf/manifests/kiro-skills.txt` には引き続き載せます（`plugins/ndf/skills/ndf-policies/SKILL.md` が steering の生成元だからです）。

`--project` で別ディレクトリへ導入する場合も `--set-default` は正しく動きます。`kiro-cli` は workspace エージェントを cwd 配下の `.kiro/agents/` からのみ検出するため、installer は `kiro-cli` を導入先（`--scope workspace` なら `--project` のパス、`--scope global` なら `$HOME`）で実行します。`kiro-cli agent set-default` はエージェントが見つからなくても終了コード 0 を返すので、installer は実行後に `agent list` で反映を検証し、切り替わっていなければ失敗させます。

### 旧バージョンからの移行

v4 系の installer は `.kiro/agents/default.json` を生成していました。この設定は Kiro の既定エージェントにならず、フックも `resources` も無効のままでした。エージェント名を `ndf` に変えたため、再インストールが必要です。

旧 installer が張った `.kiro/skills/ndf-policies` の symlink は、リンク先が現在のプラグイン配下でなくても再インストール時に削除します（削除するのはリンク自体だけで、リンク先の実体には触れません）。`.kiro/skills/ndf-policies` が symlink ではなく実体のディレクトリやファイルだった場合は、利用者が置いたものの可能性があるため installer は削除せず警告を出します。二重注入を避けるため、内容を確認のうえ手動で退避または削除してください。別の checkout パスから導入した環境でも、steering との二重注入が再インストール 1 回で解消されます。

```bash
# 1. 再インストール（旧 default.json は自動でバックアップ・移行されます）
bash plugins/ndf/dev.kiro/install.sh --with-slack

# 2. 必要なら既定エージェントを切り替える
bash plugins/ndf/dev.kiro/install.sh --set-default

# 3. 移行を確認したらバックアップを削除する
rm .kiro/agents/default.json.bak
```

`.kiro/agents/default.json` は再インストール時に必ず `.kiro/agents/default.json.bak` へバックアップされます。そのうえで installer は次のように振る舞います。

| 旧 `default.json` | `ndf.json` | 振る舞い |
| --- | --- | --- |
| 旧版 NDF installer の生成物（`name` が `default`、`description` が旧テンプレートと完全一致、かつ旧 `resources` の `skill://.kiro/skills/**/SKILL.md` または `agentSpawn` フックの `CLAUDE.ndf.md` 検査を持つ） | なし | `ndf.json` へ自動移行する（`default.json` は残らない）。独自に追記した `mcpServers` / フック / 独自キーは下表のマージで保持される |
| 同上 | あり | 自動移行しない（`ndf.json` の設定を失わないため）。`default.json` は残るので、必要な設定を写したうえで削除する |
| NDF 以外が管理している（利用者が作成したものなど） | 問わない | 自動移行しない。勝手に移行すると利用者の設定を壊すため、バックアップと移行手順の案内のみを行う |

`--dry-run` では上記の移行を含め一切の書き込みを行いません（旧設定を検出したことだけ表示します）。

Kiro 用 MCP プラグインの installer（`plugins/mcp/<プラグイン名>/dev.kiro/install.sh`）は `.kiro/agents/default.json` を更新します。自動移行後に MCP installer を実行すると `default.json` が再び作られるため、`mcpServers` を `.kiro/agents/ndf.json` へ写してください。写し替えは一度だけで済みます。`install.sh` を再実行しても、写した `mcpServers` は保持されます。

### 再インストール時に保持される設定

`install.sh` は `.kiro/agents/ndf.json` を毎回テンプレートから再生成しますが、上書きするのは installer が管理するキーだけです。既存ファイルにある利用者管理の設定は読み取ってマージし直します。

| 区分 | キー | 再実行時の扱い |
| --- | --- | --- |
| installer 管理 | `name` / `description` / `tools` / `resources` / `hooks.agentSpawn` | テンプレートから再生成する（上書き） |
| installer 管理 | `hooks.stop` | `--with-slack` の有無で生成・削除する |
| installer 管理 | `mcpServers.codex` | `--with-codex` の有無で生成・削除する |
| 利用者管理 | 上記以外の `mcpServers` エントリ、`hooks` の項目、トップレベルキー | そのまま引き継ぐ |

引き継いだ項目は実行ログに `利用者管理の設定を引き継ぎました: mcpServers.bigquery` のように表示します。再生成の前に `.kiro/agents/ndf.json.bak` へバックアップも取るため、意図しない結果になった場合は差し戻せます。

`mcpServers.codex` だけは installer 管理です。`--with-codex` を付けずに再実行すると削除されるため、Codex MCP を使う場合は `--with-codex` を付けたまま運用してください。

## Kiro CLI の制限

### `allowed-tools` は事前承認にならない

Skill frontmatter の `allowed-tools` は、プロジェクト配置（`.kiro/skills/`）では事前承認として機能しません（[kirodotdev/Kiro#6055](https://github.com/kirodotdev/Kiro/issues/6055)）。`allowed-tools: execute_bash` を持つ Skill でも `Command execute_bash is rejected because it matches one or more rules on the denied list` になります。

Kiro では、Skill の実行時にツール利用の確認が入る前提で操作してください。NDF の Skill 本文は「無確認で実行される」前提を持ちません。

### プラグイン機構がない

Kiro CLI には Skill・フック・外部連携・常時指示をまとめて配布する仕組みがありません（[kirodotdev/Kiro#8578](https://github.com/kirodotdev/Kiro/issues/8578)）。Kiro IDE の Powers は CLI では使えないため、`install.sh` による導入を継続します。

