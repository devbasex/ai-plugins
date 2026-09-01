# NDF Plugin

PR 運用、レビュー、調査、実装計画、仕様書化、開発方法論（要求定義・テスト駆動・構造改善・
完了判定）、Docker container access、statusline、外部 AI 委譲、Slack 通知を提供します。

配布物は `plugins/ndf/` の 1 ディレクトリにまとまっています。Skill の実体は `skills/` の
1 箇所だけで、どのランタイムへ配るかは `manifests/*-skills.txt` が決めます。

| ランタイム | 公開 Skill | マニフェスト |
| --- | --- | --- |
| Claude Code | 32 個 | `.claude-plugin/plugin.json` |
| Codex | 30 個 | `.codex-plugin/plugin.json` |
| Kiro CLI | 31 個 | `dev.kiro/install.sh`（プラグイン機構が無いため installer で導入） |

## レイアウト

```text
plugins/ndf/
├── .claude-plugin/plugin.json   # Claude Code のマニフェスト
├── .codex-plugin/plugin.json    # Codex のマニフェスト
├── skills/                      # 配布 Skill の唯一の実体（32 個）
├── skills/README.md             # Skill 執筆の規約
├── optional-skills/             # どの配布先にも載せない Skill（4 個）
├── manifests/                   # ランタイム別の配布 Skill 一覧
├── agents/                      # Claude Code のサブエージェント定義（8 個）
├── hooks/claude.json            # Claude Code の PreToolUse / SessionStart / Stop hook
├── hooks/codex.json             # Codex の PreToolUse / SessionStart / Stop hook
├── scripts/                     # hook と Skill から呼ぶスクリプト
├── dev.kiro/                    # Kiro CLI の installer・エージェント定義・プロンプト
└── README.md
```

`dev.kiro` は Agent Plugins 仕様 §8.2 が定めるクライアント拡張ディレクトリです。

`optional-skills/` には、どの `manifests/*-skills.txt` にも載せない Skill を置きます
（`google-auth` / `google-drive` / `ml-model-structure` / `skill-stats`）。`skills/` を配布
Skill の実体だけに保つことで、マニフェストの絞り込みによらず公開数が変わりません。

## インストール

**以下は正式版（`main`）の手順です。** 検証中の開発版は `develop` に載ります。取得元へ
`#develop` を足す形で、手順は
[リポジトリ README の「開発版を試す」](../../README.md#開発版を試す開発者向け)にあります。

### Claude Code

```bash
/plugin marketplace add https://github.com/devbasex/ai-plugins
/plugin install ndf@ai-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

### Kiro CLI

Kiro CLI にはプラグイン機構が無いため、installer が `.kiro/` へ配置します。リポジトリ root で
実行してください。

```bash
# 基本（Skills + steering + agentSpawn hook）
bash plugins/ndf/dev.kiro/install.sh

# Slack 通知も有効化
bash plugins/ndf/dev.kiro/install.sh --with-slack

# Slack 通知 + Kiro 側 Codex MCP 設定も生成
bash plugins/ndf/dev.kiro/install.sh --with-slack --with-codex

# 書き込みを行わず内容だけ確認
bash plugins/ndf/dev.kiro/install.sh --dry-run
```

導入後は `kiro-cli chat --agent ndf` で起動します。installer は `manifests/kiro-skills.txt` に
載る Skill だけを `.kiro/skills/` へ symlink します。版数は `.claude-plugin/plugin.json` から
読み取り、実行時に `NDF バージョン: <版数>` として表示します。

導入済みプロジェクトの版数は次で確認できます。

```bash
python3 -c "import json;print(json.load(open('.kiro/agents/ndf.json'))['description'])"
# => NDF統合開発エージェント（Kiro CLI用 / v9.4.0）
```

## v9.4.0 へ更新するとき

**v9.4.0 では Skill の数は変わりませんでした（v9.3.0 と同じ 31 / 29 / 30）。**
`cross-review` の収束判定が変わります（#33 / #37）。

中断したレビューを再開したとき、その時点で Resolve されていない指摘は、修正の工程を 1 度
通すまで収束しません。これまでは再開したラウンドで両者が承認すると、前のラウンドの指摘を
修正の工程へ通さないまま収束し、最後の一括処理だけが受け皿になっていました。

あわせて、手順を飛ばして次のラウンドへ進めなくなります。前のラウンドが修正必須の判定だった
のに修正の記録が無い場合と、対象の指摘が未解決のまま残っている場合は、次のラウンドの開始が
終了コード 5 で止まります。**増えるラウンドは最大 1 回**で、残りはこれまでどおりです。

`fix` は、そのラウンドの投稿数ではなく Pull Request 上の未解決の指摘を数え直します。

```bash
# Claude Code
/plugin marketplace update ai-plugins
/plugin install ndf@ai-plugins

# Codex
codex plugin marketplace upgrade ai-plugins
codex plugin add ndf@ai-plugins

# Kiro CLI
bash <プラグインのパス>/dev.kiro/install.sh
```

hook は**リポジトリ側に `.ndf/worktree.json` があるときだけ動きます**。置かなければ
これまでと同じ挙動のままです。書き方は上の「Hooks」節にあります。

Codex では、hook の初回実行前に `~/.codex/config.toml` の `[hooks.state]` で対象 hook を
有効化してください（`enabled = true`）。

## Playwright テストについて

v7.0.0 で Playwright による E2E テストの 4 Skill を **`playwright-kit` プラグイン**へ分離しました。
Skill 名は変わらないため `/playwright-` まで打てば従来どおり候補に出ますが、プラグインを別途
インストールする必要があります。

```bash
# Claude Code
/plugin install playwright-kit@ai-plugins
# Codex
codex plugin add playwright-kit@ai-plugins
# Kiro CLI
bash plugins/playwright-kit/dev.kiro/install.sh
```

移行先の対応表は予告どおり v8.0.0 で `ndf-policies` から削除しました。リポジトリ root の
[README.md](../../README.md) の「NDF v7.0.0 の主な変更（非互換）」を参照してください。

## Hooks

### 作業ツリー運用（3 ランタイム共通）

開発の変更を、リポジトリを clone したディレクトリ（主ディレクトリ）ではなく `.worktrees/` の
作業ツリーの中で行う運用を支えます。**編集は止めません。** 案内が出ても操作は成立します。

| 起きること | 担う hook | Claude Code | Codex | Kiro CLI |
| --- | --- | --- | --- | --- |
| 主ディレクトリの保護対象パスを編集しようとすると案内が出る | tool 実行前 | `PreToolUse` | `PreToolUse` | — |
| 作業ツリーで作業する旨の案内がプロンプトごとに出る | プロンプト送信時 | — | — | `userPromptSubmit` |
| 主ディレクトリに残った未コミット変更が提示される | セッション開始時 | `SessionStart` | `SessionStart` | `agentSpawn` |
| 主ディレクトリのブランチが稼働中の作業ツリーへ追従する | セッション開始時 | `SessionStart` | `SessionStart` | `agentSpawn` |

Kiro CLI に tool 実行前の案内が無いのは、この事象でモデルへ案内を渡す手段が終了コード 2 に
限られ、それが tool の実行を拒否するためです。拒否しない方針のもとでは置けないため、パスを
見ない案内をプロンプト送信時の hook が担います。

**この仕組みはリポジトリ側の宣言ファイル `.ndf/worktree.json` があるときだけ動きます。**
宣言が無いリポジトリでは、いずれの hook も何も出力せず終了コード 0 で終わります。

宣言ファイルは `/ndf:worktree` を起動すると手順 0 で作られます。手で作るなら次を実行します。

```bash
bash <プラグインのパス>/scripts/worktree-setup.sh init
```

```json
{
  "version": 1,
  "guard": {
    "allow_paths": ["issues/", "docs/", ".claude/", ".codex/", ".kiro/", ".ndf/", ".gitignore"]
  }
}
```

`guard.allow_paths` は、主ディレクトリで編集しても案内を出さないパスです。省略すると
組み込みの既定（上記と同じ一覧に `.agents/` `.gemini/` `.serena/` を加えたもの）を使います。
空の配列を書くと「何も許可しない」という指定になります。

手順は `/ndf:worktree` にあります。

### その他

Claude Code の SessionStart hook（`hooks/claude.json`）は上記に加えて次を行います。

- `~/.claude/settings.json` の `cleanupPeriodDays` を 90 日以上に保つ
- statusline 未設定時に NDF 標準 statusline を設定する

Claude Code の Stop hook は終了時に Slack 通知スクリプトを実行します。通知に必要な環境変数が
未設定の場合は送信せず終了します。

Codex の Stop hook（`hooks/codex.json`）は `NDF_CODEX_SLACK_NOTIFY=true` が設定されている
場合だけ Slack 通知を送ります。**Codex の hook は Codex 側で明示的に有効化するまで実行され
ません。** `~/.codex/config.toml` の `[hooks.state]` に対象 hook の `enabled = true` が要ります。
`/hooks` で対象 hook を確認し、利用するプロジェクトで有効化してください。

Kiro CLI では installer が `.kiro/agents/ndf.json` の `hooks` を生成します。

## Slack 通知

利用プロジェクト側で以下の環境変数を設定します。

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>
# Codex のみ
NDF_CODEX_SLACK_NOTIFY=true
```

`SLACK_USER_MENTION` は任意です。機密値は `.env` などで管理し、リポジトリへコミットしないで
ください。

## 外部 AI 委譲

`/ndf:external-ai` skill または `corder` エージェントから外部 AI 委譲を使う場合は、利用環境に
Codex CLI をインストールしてログインします。

```bash
npm install -g @openai/codex
codex login
```

`/ndf:pr-review <PR番号> gemini` や `/ndf:cross-review` で Gemini 委譲を使う場合は、利用環境に
Gemini CLI をインストールしてログインします。

```bash
npm install -g @google/gemini-cli
gemini
```

## Codex の暗黙起動抑止

取り消しが難しい以下 2 個の Skill は、`skills/<name>/agents/openai.yaml` の `policy.allow_implicit_invocation: false` によって **Codex の暗黙起動 (モデルが自分で選んで起動する経路) を抑止**しています。共有 Skill の frontmatter が `disable-model-invocation: true` のものが対象で、`scripts/build-runtime-plugins.sh` が自動生成します。

| Skill | 内容 |
|-------|------|
| `cherry-pick-pr` | 環境ブランチへの cherry-pick PR 作成 |
| `deploy` | 環境ブランチ (qa/staging, release/v2 等) への deploy PR 作成 |

`merged` / `pr` / `pr-tests` / `pr-review` は日常的に自然文で依頼されるため、v5.0.0 で暗黙起動を許可しました。代わりに、取り消しの難しい手順 (push、PR 作成、ブランチ・worktree の削除) の直前に対象を提示して同意を得ることを各 Skill の本文で必須化しています。

### 利用者への影響と起動方法

**プラグイン Skill では、抑止すると `$<skill 名>` による明示起動も効かなくなります。**
起動する手段は SKILL.md のパスを示して読ませることだけです。

| 起動経路 | 抑止後の挙動 |
|----------|-------------|
| 暗黙起動 (モデルが自分で選ぶ) | **起動しない**。セッションの skill 一覧 (`## Skills` の `### Available skills`) に載らない |
| 明示起動 `$deploy` | **展開されない**。抑止していない Skill (`$markdown-writing` 等) は展開されるが、抑止した Skill は `$` を書いても本文が注入されない |
| 名前だけの自然文依頼 (`deploy skill を実行して`) | **起動しない**。一覧に無いため拒否され、別の Skill で代替されることがある |
| SKILL.md の絶対パスを示す | **起動する**。通常のファイル読み取りとして読み込まれ、本文どおり実行される |

```text
# 動く: 実体パスを示して読ませる
~/.codex/plugins/cache/ai-plugins/ndf/9.4.0/skills/deploy/SKILL.md を読んで、その手順どおりに qa/staging へ deploy PR を作成してください。

# 動かない: 明示起動 ($ は展開されない)
$deploy qa/staging

# 動かない: 名前だけで起動を依頼する
deploy skill を実行してください。
```

対話モード (`codex` を引数なしで起動) では `/skills` で Skill 一覧と有効・無効を確認できます。

パスを打つ手間はあるが、`deploy` と `cherry-pick-pr` は環境ブランチへ書き込む取り消しの
難しい操作なので、この摩擦は意図した設計として受け入れる。Claude Code では
`disable-model-invocation: true` + `/ndf:deploy` のスラッシュコマンドで同じ役割を果たす。

### プラグイン Skill のファイル探索に関する注意

marketplace 経由でインストールした場合、Skill の実体は **ワークスペース外**の Codex プラグインキャッシュに置かれます。

```text
$CODEX_HOME/plugins/cache/<marketplace>/<plugin>/<version>/skills/<skill>/SKILL.md
# 既定 ($CODEX_HOME=~/.codex) の例:
# ~/.codex/plugins/cache/ai-plugins/ndf/9.4.0/skills/deploy/SKILL.md
```

そのため「`deploy` の SKILL.md を探して読んで」のような曖昧な依頼は、Codex のファイル探索がワークスペース内に限られる状況では失敗しえます。**抑止した Skill は `$<skill 名>` が展開されない**ので、`codex plugin list` で実体パスを確認し、絶対パスを渡してください。

```bash
codex plugin list | grep 'ndf@ai-plugins'
# => ndf@ai-plugins  installed, enabled  9.4.0  <path>
```

抑止していない Skill（`markdown-writing` など）はキャッシュ配下でも `$<skill 名>` で解決するため、そちらは `$` 起動が使えます。

### 実機検証結果 (codex-cli 0.146.1 / gpt-5.5)

`.agents/skills/` 配下に検証用 Skill (`probe-explicit` = 本プラグインと同じ `openai.yaml` を配置 / `probe-open` = 抑止なし) を置き、`codex exec` で確認した結果です。表中のパスは検証時点 (プラグイン v4.20.1) の実測値をそのまま載せています。

| 検証 | 内容 | 結果 |
|------|------|------|
| 暗黙起動の抑止 (ワークスペース) | `.agents/skills/` の probe に対し「Available skills のうち probe で始まるものを列挙」と依頼 | `probe-open` のみ。`probe-explicit` は **載らない**。エラー・警告は出ない |
| 明示起動 (ワークスペース) | `codex exec '$probe-explicit'` | **起動した**。セッションログに `<skill><name>probe-explicit</name><path>…</path>` + SKILL.md 本文が注入される |
| 暗黙起動の抑止 (プラグイン) | v5.0.0 インストール後に skill 一覧を列挙 | 配布 23 個のうち **21 個**。`deploy` / `cherry-pick-pr` は載らない |
| 明示起動 (プラグイン・抑止なし) | `codex exec '$markdown-writing'` | **展開された**。SKILL.md 本文が注入される |
| 明示起動 (プラグイン・抑止あり) | `codex exec '$deploy'` / `codex exec '$cherry-pick-pr'` | **展開されない**。「利用可能 skill 一覧に見当たらない」と返る |

`.agents/skills/` に置いた Skill は抑止しても `$` で起動できますが、**プラグインとして
配布した Skill は抑止すると `$` も効きません**。この差は codex-cli 0.146.1 で実測した
もので、公式ドキュメントには記載がありません。

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

## 検証

```bash
bash scripts/validate-runtime-plugins.sh
claude plugin validate plugins/ndf
python3 -m json.tool plugins/ndf/.codex-plugin/plugin.json >/dev/null
bash plugins/ndf/dev.kiro/install.sh --dry-run >/dev/null
```

## 開発者向け

Skill の実体は `skills/` の 1 箇所だけです。ランタイムごとの複製はありません。Skill を変更したら
上記の検証を実行してください。frontmatter の規約は `skills/README.md` にあり、
`python3 scripts/check-skill-frontmatter.py` で検査します。
