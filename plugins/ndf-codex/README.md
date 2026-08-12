# NDF Codex Plugin

Codex CLI 向けの NDF プラグインです。PR 運用、レビュー、cross-review、実装計画、仕様書化、Playwright テスト運用、Docker container access、GitHub 操作補助などの Codex 用 skills と、Codex 終了時の任意 Slack 通知 hook を提供します。

## インストール

Codex で marketplace を追加し、`ndf` をインストールします。

```bash
codex plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

## 同梱内容

- `.codex-plugin/plugin.json`: Codex plugin manifest
- `skills/`: Codex 向けに公開する NDF skills
- `hooks/hooks.json`: Codex Stop hook
- `scripts/`: hook と skill から利用する同梱スクリプト

Claude Code 専用の agents、statusline 自動設定、transcript retention 自動設定は含めません。Codex runtime が読むファイルはこの `plugins/ndf-codex` 配下だけで完結します。

## 暗黙起動を抑止する Skill

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
~/.codex/plugins/cache/ai-plugins/ndf/5.0.0/skills/deploy/SKILL.md を読んで、その手順どおりに qa/staging へ deploy PR を作成してください。

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
# ~/.codex/plugins/cache/ai-plugins/ndf/5.0.0/skills/deploy/SKILL.md
```

そのため「`deploy` の SKILL.md を探して読んで」のような曖昧な依頼は、Codex のファイル探索がワークスペース内に限られる状況では失敗しえます。**抑止した Skill は `$<skill 名>` が展開されない**ので、`codex plugin list` で実体パスを確認し、絶対パスを渡してください。

```bash
codex plugin list | grep 'ndf@ai-plugins'
# => ndf@ai-plugins  installed, enabled  5.0.0  <path>
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

## Slack 通知

Codex 版の Stop hook は `NDF_CODEX_SLACK_NOTIFY=true` が設定されている場合だけ Slack 通知を送ります。通知を使う場合は、利用プロジェクト側で以下の環境変数を設定します。

```bash
NDF_CODEX_SLACK_NOTIFY=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>
```

`SLACK_USER_MENTION` は任意です。機密値は `.env` などで管理し、リポジトリへコミットしないでください。

Codex の hook は初回実行前に Codex 側の hooks trust 設定が必要になる場合があります。`/hooks` で対象 hook を確認し、利用するプロジェクトで明示的に有効化してください。

## 検証

Codex plugin schema を検証できる CLI が利用できる場合は、Codex 側の validate / install smoke を実行してください。CLI に検証コマンドが無い環境では、manifest JSON と参照パスの存在を確認します。

```bash
python3 -m json.tool plugins/ndf-codex/.codex-plugin/plugin.json >/dev/null
python3 -m json.tool plugins/ndf-codex/hooks/hooks.json >/dev/null
test -d plugins/ndf-codex/skills
test -d plugins/ndf-codex/scripts
```

install smoke を行う場合は、別の一時プロジェクトで marketplace から `ndf@ai-plugins` を追加し、代表 skill が読み込まれることと Stop hook が trust 対象として表示されることを確認します。

## 開発者向け

`skills/` と `scripts/` は `plugins/ndf-shared` から生成される commit 対象の生成物です。Skill や共通スクリプトを変更する場合は `plugins/ndf-shared` を編集し、build を実行します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
```
