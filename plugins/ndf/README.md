# NDF Plugin

PR 運用、レビュー、調査、実装計画、仕様書化、開発方法論（要求定義・テスト駆動・構造改善・
完了判定）、Docker container access、statusline、外部 AI 委譲、Slack 通知を提供します。

配布物は `plugins/ndf/` の 1 ディレクトリにまとまっています。Skill の実体は `skills/` の
1 箇所だけで、どのランタイムへ配るかは `manifests/*-skills.txt` が決めます。

| ランタイム | 公開 Skill | マニフェスト |
| --- | --- | --- |
| Claude Code | 45 個 | `.claude-plugin/plugin.json` |
| Codex | 43 個 | `.codex-plugin/plugin.json` |
| Kiro CLI | 44 個 | `dev.kiro/install.sh`（プラグイン機構が無いため installer で導入） |
| agy | 43 個 | `dev.agy/plugin.json`（取得元の登録が無いため clone から導入） |

## レイアウト

```text
plugins/ndf/
├── .claude-plugin/plugin.json   # Claude Code のマニフェスト
├── .codex-plugin/plugin.json    # Codex のマニフェスト
├── skills/                      # 配布 Skill の唯一の実体（45 個）
├── skills/README.md             # Skill 執筆の規約
├── manifests/                   # ランタイム別の配布 Skill 一覧
├── agents/                      # Claude Code のサブエージェント定義（8 個）
├── hooks/claude.json            # Claude Code の PreToolUse / SessionStart / Stop hook
├── hooks/codex.json             # Codex の PreToolUse / SessionStart / Stop hook
├── scripts/                     # hook と Skill から呼ぶスクリプト
├── dev.kiro/                    # Kiro CLI の installer・エージェント定義・プロンプト
├── dev.agy/                     # agy のマニフェスト・hook 定義・配布 Skill への symlink
└── README.md
```

`dev.kiro` と `dev.agy` は Agent Plugins 仕様 §8.2 が定めるクライアント拡張ディレクトリです。

`dev.agy/skills/` は `manifests/agy-skills.txt` から生成する symlink です
（`scripts/build-runtime-plugins.sh`）。`dev.agy/agents` と `dev.agy/scripts` は
`agents/` と `scripts/` を指し、実体を 4 ランタイムで共有します。**agy は配る Skill を絞る
手段を利用者側の設定にしか持たない**ため、絞り込みはここへ何を並べるかで表します。

**`skills/` に置く Skill は、少なくとも 1 つの manifest へ載せます。** 配らない Skill の
置き場所（`optional-skills/`）は v10.5.0 で無くしました。どこからも起動できない Skill を
置き続ける理由が、公開数を一定に保つことだけだったためです。

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
# => NDF統合開発エージェント（Kiro CLI用 / v10.9.0）
```

### agy

agy にも取得元の登録がありません。clone した `dev.agy` を直接導入します。**更新の副コマンドが
無い**ため、新しい版へは入れ直します（`install` は上書きするが、消したファイルは実体に残る）。

```bash
agy plugin install plugins/ndf/dev.agy                               # 初回
agy plugin uninstall ndf && agy plugin install plugins/ndf/dev.agy   # 新しい版へ
```

導入すると `manifests/agy-skills.txt` に載る Skill 43 個と、エージェント 8 個、hook 1 個が
`~/.gemini/config/plugins/ndf/` へ複製されます。symlink は実体へ解決されて複製されるため、
clone を消しても導入した内容は残ります。

**hook は複製されるだけで、agy はそれを読み込みません**（agy 1.1.26 で実測）。読む先は
`~/.gemini/config/hooks.json` の 1 か所だけです。次を実行して差し込みます。**冪等で、他の
項目には触れません**（`--dry-run` で内容を確認でき、`--uninstall` で外せます）。

```bash
bash plugins/ndf/dev.agy/install-hooks.sh
```

```bash
agy plugin list
# => {"imports":[{"name":"ndf","source":"antigravity","components":["skills","agents","hooks"]}]}
```

## v10.9.0 へ更新するとき

**ビジネス文書を作る工程が加わりました。** 既存の 4 モードの判定と工程は変わりません。
変更点の一覧は [CHANGELOG.md](../../CHANGELOG.md) にあります。

| 変わったこと | 中身 |
| --- | --- |
| **モードが 5 つになりました** | `documentation` が加わりました。**読み手へ渡すビジネス文書**（提案資料・稟議書・定例報告・指標の定義・運用マニュアル・説明資料）を作るときに当たります |
| **工程が 2 つ増えました** | `素材の収集と出典の確定`（`設計` の後）と `体裁レビュー`（`配布` の後）です。**どちらも `documentation` でだけ必須です** |
| Skill が 4 個増えました | `document-systems` / `document-sources` / `document-drafting` / `layout-review` |
| `release` に出力の形が 4 つ増えました | スライド / 文書 / 表計算 / ページ |
| `design` に体裁設計が入りました | `references/layout-<出力の形>.md` 4 本 |
| `quality-gates` に `documentation` の行が加わりました | 必須の段は 3 で、追加で**事実確認**を求めます |

### `README.md` と `docs/` の変更はこれまでどおり `light` です

**判定を分けるのは読み手です。** リポジトリを読む人へ向けた文書は `documentation` に当たり
ません。当たるのは、リポジトリの外にいる読み手へ渡す文書だけです。

### 承認の関門は 2 つのままです

企画承認は設計 Pull Request のマージ、制作物承認は本番の提出先への操作へ写像しました。
**新しい関門は作っていません。** `design-approved` のラベル名と `design/` の接頭辞も
変えていません。

### 盤面を使っている場合は値を足してください

**進行を GitHub Projects へ記録している場合、盤面の単一選択へ値を足す必要があります。**
リポジトリの変更では完結せず、足りない値は書き込み時に弾かれます。

| フィールド | 足す値 |
| --- | --- |
| 進行 | `素材の収集と出典の確定`（`設計` の次）/ `体裁レビュー`（`配布` の次） |
| モード | `documentation` |

**盤面の宣言（`.ndf/projects.json`）が無いリポジトリでは何もしなくて構いません。**

### 文書の提出先を使うときは宣言が要ります

`documentation` の配布（生成と提出）は `.ndf/document.json` を読みます。**無ければ提出の
工程が止まるだけで、他の工程は動きます。** 形は
`development-workflow/references/document-destinations.md` にあります。

## Playwright テストについて

v7.0.0 で Playwright による E2E テストの 4 Skill を **`playwright-kit` プラグイン**へ分離しました。
Skill 名は変わらないため `/playwright-` まで打てば候補に出ますが、別途インストールが要ります。
移行の経緯は [README.md](../../README.md) の「NDF v7.0.0 の主な変更（非互換）」にあります。

```bash
/plugin install playwright-kit@ai-plugins             # Claude Code
codex plugin add playwright-kit@ai-plugins            # Codex
bash plugins/playwright-kit/dev.kiro/install.sh       # Kiro CLI
```

## Hooks

### 作業ツリー運用（4 ランタイム共通）

開発の変更を、リポジトリを clone したディレクトリ（主ディレクトリ）ではなく `.worktrees/` の
作業ツリーの中で行う運用を支えます。**編集は止めません。** 案内が出ても操作は成立します。

| 起きること | 担う hook | Claude Code | Codex | Kiro CLI | agy |
| --- | --- | --- | --- | --- | --- |
| 主ディレクトリの保護対象パスを編集しようとすると案内が出る | tool 実行前 | `PreToolUse` | `PreToolUse` | — | `PreToolUse` |
| 作業ツリーで作業する旨の案内がプロンプトごとに出る | プロンプト送信時 | — | — | `userPromptSubmit` | — |
| 主ディレクトリに残った未コミット変更が提示される | セッション開始時 | `SessionStart` | `SessionStart` | `agentSpawn` | `PreInvocation` |
| 主ディレクトリのブランチが稼働中の作業ツリーへ追従する | セッション開始時 | `SessionStart` | `SessionStart` | `agentSpawn` | `PreInvocation` |

Kiro CLI に tool 実行前の案内が無いのは、この事象でモデルへ案内を渡す手段が終了コード 2 に
限られ、それが tool の実行を拒否するためです。拒否しない方針のもとでは置けないため、パスを
見ない案内をプロンプト送信時の hook が担います。

**agy の 2 つは `install-hooks.sh` を実行するまで届きません。** agy はプラグインの
`hooks.json` を読み込まないためです（「インストール / agy」を参照）。

**agy は案内を作る時点と渡せる時点が離れています。** tool 実行前の hook がモデルへ文言を返す
口は拒否のときにしか働かないため、案内はセッションの控えへ積み、次のモデル呼び出しの前に
`injectSteps` で渡します。セッション開始時にあたる事象も持たないため、モデル呼び出しの通し番号が
0 のときを開始時として扱います。

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
組み込みの既定（上記と同じ一覧に `.agents/` `.serena/` を加えたもの）を使います。
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

`/ndf:pr-review <PR番号> agy` や `/ndf:cross-review` で agy 委譲を使う場合は、利用環境に
Antigravity CLI をインストールしてログインします。ログインの手順は初回の対話起動にあり、
`agy models` が終了コード 0 で終われば認証済みです。

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy          # 初回だけ。ブラウザでログインする
agy models   # 認証の確認
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
~/.codex/plugins/cache/ai-plugins/ndf/10.9.0/skills/deploy/SKILL.md を読んで、その手順どおりに qa/staging へ deploy PR を作成してください。

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
# ~/.codex/plugins/cache/ai-plugins/ndf/10.9.0/skills/deploy/SKILL.md
```

そのため「`deploy` の SKILL.md を探して読んで」のような曖昧な依頼は、Codex のファイル探索がワークスペース内に限られる状況では失敗しえます。**抑止した Skill は `$<skill 名>` が展開されない**ので、`codex plugin list` で実体パスを確認し、絶対パスを渡してください。

```bash
codex plugin list | grep 'ndf@ai-plugins'
# => ndf@ai-plugins  installed, enabled  10.9.0  <path>
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

## Kiro CLI の詳細

installer の主なオプション・既定エージェントの切り替え・導入スコープ・旧バージョンからの
移行・再インストール時に保持される設定と、Kiro CLI 側の制限は
[docs/kiro-cli.md](docs/kiro-cli.md) にあります。

## 実機検証の記録

kiro-cli の実機検証と、Skill 数が文脈量へ与える影響の実測は
[docs/field-test-records.md](docs/field-test-records.md) にある。
**その時点の実測であり、以後の構成変更には追随しない。**

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
