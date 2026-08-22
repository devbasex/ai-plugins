# NDF Kiro Plugin

Kiro CLI 向けの NDF 配布物です。`plugins/ndf-shared` から生成された `skills/`、Kiro agent template、workflow prompt、通知用 script を同梱します。

## バージョンの確認

Kiro 配布物は `plugin.json` を持たないため、版数は次の 2 か所で確認する。

```bash
# 配布物の版数（build-runtime-plugins.sh が Claude 版 plugin.json から生成）
cat plugins/ndf-kiro/VERSION

# 導入済みプロジェクトの版数
python3 -c "import json;print(json.load(open('.kiro/agents/ndf.json'))['description'])"
# => NDF統合開発エージェント（Kiro CLI用 / v8.6.0）
```

`install.sh` は実行時にも `NDF バージョン: <版数>` を表示する。


## Playwright テストについて

v7.0.0 で Playwright による E2E テストの 4 Skill を **`playwright-kit` プラグイン**へ分離しました。
Skill 名は変わらないため `/playwright-` まで打てば従来どおり候補に出ますが、プラグインを別途
インストールする必要があります。

```bash
bash plugins/playwright-kit-kiro/install.sh
```

移行先の対応表は予告どおり v8.0.0 で `ndf-policies` から削除したため、`.kiro/steering/ndf-policies.md`
にも含まれません。リポジトリ root の [README.md](../../README.md) の
「NDF v7.0.0 の主な変更（非互換）」を参照してください。

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
| `--with-codex` | Codex MCP サーバ設定（`ndf.json` の `mcpServers.codex`）と、Codex CLI 直接実行用プロンプトを追加する |
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

`ndf-policies` は steering の生成元としてのみ使うため、`.kiro/skills/` へは symlink しません。Kiro は `.kiro/skills/*/SKILL.md` と `.kiro/steering/**/*.md` の両方を文脈へ読み込むため、両方に置くと同じ内容が 2 回注入されます。`plugins/ndf-shared/manifests/kiro-skills.txt` には引き続き載せます（`plugins/ndf-kiro/skills/ndf-policies/SKILL.md` が steering の生成元だからです）。

`--project` で別ディレクトリへ導入する場合も `--set-default` は正しく動きます。`kiro-cli` は workspace エージェントを cwd 配下の `.kiro/agents/` からのみ検出するため、installer は `kiro-cli` を導入先（`--scope workspace` なら `--project` のパス、`--scope global` なら `$HOME`）で実行します。`kiro-cli agent set-default` はエージェントが見つからなくても終了コード 0 を返すので、installer は実行後に `agent list` で反映を検証し、切り替わっていなければ失敗させます。

### 旧バージョンからの移行

v4 系の installer は `.kiro/agents/default.json` を生成していました。この設定は Kiro の既定エージェントにならず、フックも `resources` も無効のままでした。エージェント名を `ndf` に変えたため、再インストールが必要です。

旧 installer が張った `.kiro/skills/ndf-policies` の symlink は、リンク先が現在のプラグイン配下でなくても再インストール時に削除します（削除するのはリンク自体だけで、リンク先の実体には触れません）。`.kiro/skills/ndf-policies` が symlink ではなく実体のディレクトリやファイルだった場合は、利用者が置いたものの可能性があるため installer は削除せず警告を出します。二重注入を避けるため、内容を確認のうえ手動で退避または削除してください。別の checkout パスから導入した環境でも、steering との二重注入が再インストール 1 回で解消されます。

```bash
# 1. 再インストール（旧 default.json は自動でバックアップ・移行されます）
bash plugins/ndf-kiro/install.sh --with-slack

# 2. 必要なら既定エージェントを切り替える
bash plugins/ndf-kiro/install.sh --set-default

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

Kiro 用 MCP プラグインの installer（`plugins/mcp/kiro/*/install.sh`）は `.kiro/agents/default.json` を更新します。自動移行後に MCP installer を実行すると `default.json` が再び作られるため、`mcpServers` を `.kiro/agents/ndf.json` へ写してください。写し替えは一度だけで済みます。`install.sh` を再実行しても、写した `mcpServers` は保持されます。

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
| `--project` で別ディレクトリへ導入したとき `--set-default` が効くか | 効く（修正後） | 修正前は導入先以外の cwd から実行すると `Failed to set default agent: No agent with name ndf found` になり、しかも終了コード 0 で「変更しました」と表示していた。修正後は導入先で `kiro-cli` を実行し、`agent list` で反映を検証する |
| `--scope global --set-default` が効くか | 効く | `$HOME` で `kiro-cli` を実行し `agent list` の `*` が `ndf` へ移った。検証後に `kiro-cli agent set-default kiro_default` で復旧し、`~/.kiro` の `find` 比較で検証前と一致することを確認 |
| `--scope global` で `~/.kiro/` へ配置されるか | 配置される | `~/.kiro/{skills,steering,prompts,agents}` が生成され、プロジェクト外でも `Global` として一覧に出た |
| steering がエージェント選択に依存せず読まれるか | 読まれる | `kiro_default` の `/context show` にも `.kiro/steering/**/*.md` の一致として現れた |
| 再インストールで利用者管理の設定が残るか | 残る | `mcpServers.bigquery` / `hooks.userPromptSubmit` / `toolsSettings` を書き足してから再実行し、すべて残ることを確認。ログに `利用者管理の設定を引き継ぎました: hooks.userPromptSubmit, mcpServers.bigquery, toolsSettings` |
| `--with-codex` を外した再実行の挙動 | `mcpServers.codex` だけ消える | 同じ再実行で `mcpServers.bigquery` は残った。`codex` は installer 管理のため |
| 既存 `ndf.json` が壊れた JSON のとき | テンプレートから再生成する | `WARN: 既存の … を読めないため引き継ぎません` を出して続行し、`.bak` は残る |
| `kiro-cli agent set-default` の保存先 | `~/.local/share/kiro-cli/data.sqlite3`（マシン全体の設定） | 実行した cwd に `.kiro/settings.json` は生成されず、`find ~/.kiro ~/.aws` にも差分が出なかった |
| 既定エージェントが cwd 依存で復旧できるか | 導入先から実行すれば復旧できる | 対象プロジェクト限定の workspace エージェントを既定にした状態では、別 cwd からの `set-default` が `No agent with name … found` になりつつ終了コード 0 を返し、既定が戻らなかった |

コンテキスト占有率を `kiro-cli chat --agent <名前> --no-interactive '/context show'` で実測しました。測定用プロジェクトには本リポジトリの `AGENTS.md` と `README.md` を置き、`install.sh --project <測定用ディレクトリ>` で配布物を導入しています。**下表は Kiro manifest が 21 個だった時点の測定値**で、4 構成を比較するために同一プロジェクトで測ったものです（`ndf-policies` は `.kiro/steering/` へ回すため `.kiro/skills/` に並ぶのは 20 個）。`一致ファイル数` と `合計文字数` は `/context show` が列挙したファイルを数え上げた値、`占有率` は `Context files total` の表示値です。

| 構成 | 一致ファイル数 | `ndf-policies` の注入回数 | 占有率 | 文脈ファイルの合計文字数 |
| --- | --- | --- | --- | --- |
| 変更前 `default` エージェント | 26 | 2（`resources` + Skill） | 0.6% | 125,723 |
| 本 PR 初版 `ndf` エージェント | 26 | 2（Skill + steering） | 0.6% | 125,746 |
| 修正後 `ndf` エージェント | 25 | 1（steering のみ） | 0.6% | 125,562 |
| 参考: 組み込み `kiro_default` | 25 | 1（steering のみ） | 0.6% | 125,562 |

`resources` の二重登録を解消しただけでは、代わりに steering が 1 件増えるためファイル数は 26 のまま減りませんでした。`ndf-policies` を `.kiro/skills/` へ symlink しない変更を加えて、はじめて 26 → 25 に減っています。ただし `ndf-policies/SKILL.md` は 184 文字しかないため、合計文字数の削減は 125,746 → 125,562（-184 文字）にとどまり、`/context show` の表示（0.1% 刻み）は 0.6% のまま変わりません。重複解消の目的は表示上の占有率低減ではなく、同じ指示が 2 回注入される状態を解消することです。

`ndf-policies` を Skill として置かなくても機能は落ちません。`user-invocable: false` で本文の参照を前提としない Skill であり、内容は steering として常時読み込まれるためです。

なお 2026-08-07 に別プロジェクトで測った 0.2% / 112,598 文字という値は、測定用プロジェクトの `AGENTS.md` / `README.md` が異なるため本表とは比較できません。上表は 4 構成すべてを同一プロジェクトで測り直した値です。

その後、ブラウザ自動テストの 3 個を 3 ランタイムへ配布する変更で Kiro manifest は
**24 個**（`.kiro/skills/` に並ぶのは 23 個）になりました。同じ手順で測り直した現行
構成の値は次のとおりです。

| 構成 | 一致ファイル数 | `ndf-policies` の注入回数 | 占有率 | 文脈ファイルの合計文字数 |
| --- | --- | --- | --- | --- |
| 現行 `ndf` エージェント（manifest 24 個） | 26 | 1（steering のみ） | 0.9% | 139,182 |

Skill が 3 個増えても `ndf-policies` の注入は 1 回のままです。占有率が 0.6% から
0.9% へ上がったのは、Skill が 3 個増えたことに加え、測定用プロジェクトへ置いた
`AGENTS.md` / `README.md` が棚卸の記載追加でその間に大きくなったためです（同一
プロジェクトでの前後比較ではないため、この差分だけを Skill 増加の影響として
読まないこと）。

## 開発

Skill を変更する場合は `plugins/ndf-shared/skills/` を編集し、runtime plugin を再生成します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
```
