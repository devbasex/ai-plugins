# NDF Plugin

PR 運用、レビュー、調査、実装計画、仕様書化、開発方法論（要求定義・テスト駆動・リファクタリング・
完了判定）、Docker container access、statusline、外部 AI 委譲、Slack 通知を提供します。

リリース対象は `plugins/ndf/` の 1 ディレクトリにまとまっています。Skill の実体は `skills/` の
1 箇所だけで、どのランタイムへ配るかは `manifests/skills.json` が決めます。導入先のランタイムは Claude Code と
Codex の 2 つです。Kiro CLI と agy へは配らず、cross-review / cross-refactoring の相手役として起動するだけです。

| ランタイム | 公開 Skill | マニフェスト |
| --- | --- | --- |
| Claude Code | 49 個 | `.claude-plugin/plugin.json` |
| Codex | 45 個 | `.codex-plugin/plugin.json` |

**11.0.0 で中身を作り直しました。** 10 系の `scripts/` の下のスクリプトは無くなり、Skill と hook が呼ぶ処理は
すべて 1 つのエントリポイント `bin/ndf` のサブコマンドになりました（下の「ndf コマンド」）。10 系の README は
[ndf--v10.27.77 のタグ](https://github.com/devbasex/ai-plugins/blob/ndf--v10.27.77/plugins/ndf/README.md)にあります。

## レイアウト

```text
plugins/ndf/
├── .claude-plugin/plugin.json   # Claude Code のマニフェスト
├── .codex-plugin/plugin.json    # Codex のマニフェスト（manifests/skills.json から作る）
├── bin/ndf                      # サブコマンドのエントリポイント（ndf <サブコマンド>）
├── bin/ndf-claude               # NDF を読み込んだ claude の起動
├── skills/                      # 配る Skill の唯一の実体（49 個）
├── skills/AUTHORING.md          # Skill 執筆の規約
├── manifests/skills.json        # Skill ごとの配るランタイムの表
├── agents/                      # Claude Code のサブエージェント定義（専門 8 個と、3 層の定義 3 個（supervisor 2・worker 1））
├── hooks/claude.json            # Claude Code の hook の登録
├── hooks/codex.json             # Codex の hook の登録
├── contracts/                   # 設定と入出力のスキーマ・guard と上限の表
├── docs/contracts/              # コントラクトのページ（contracts/ から生成する）
├── src/ndf/                     # サブコマンドと hook の実装
├── tests/                       # プラグインのテスト
└── README.md
```

**`skills/` に置く Skill は、`manifests/skills.json` の行に載せます。** 表に無い Skill はどのランタイムへも配りません。

## インストール

**取得元は既定ブランチ（`main`）です。** 正式版は `main` から切った版上げの Pull Request のマージで出ます。開発版のチャネル（`#develop`）は 10.27.75 の後でやめました。入れていた利用者が `main` へ戻す手順は
[docs/versioning-and-distribution.md の「開発版から正式版へ戻す」](../../docs/versioning-and-distribution.md#開発版から正式版へ戻す)にあります。

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

### agy・Kiro CLI へのデプロイの終了

agy と Kiro CLI へは配りません。最後に配ったのは v10.27.4 です。cross-review と cross-refactoring は、
ホスト（Claude Code・Codex）から相手役として CLI プロセスで起動します。

前のバージョンで入れた手元は、こちらからは消しません。残すか消すかを選べます。

| ランタイム | 残すとき | 消すとき |
| --- | --- | --- |
| agy | 導入の写し（`~/.gemini/config/plugins/ndf/`）だけで動き続ける。新しいバージョンは届かない | 写しが残るうちに `bash ~/.gemini/config/plugins/ndf/install-hooks.sh --uninstall` で `~/.gemini/config/hooks.json` の項目を外し、`agy plugin uninstall ndf` を打つ |
| Kiro CLI | `.kiro/agents/ndf.json` の hook は clone の `plugins/ndf/scripts/` を呼ぶ。11.0.0 でその保存先は無くなったため、clone を `ndf--v10.27.4` のタグへ戻して使う | 前の installer は `.kiro/agents/ndf.json` に利用者が足した `mcpServers`・hook・その他のキーを引き継いでいたため、消す前に `cp .kiro/agents/ndf.json ndf.json.bak` で退避し、残したい項目を使い続けるエージェント（`.kiro/agents/` の別のファイル）へ写す。`ndf` を既定のエージェントにしたなら `kiro-cli agent set-default kiro_default` で戻し、`.kiro/agents/ndf.json`・`.kiro/steering/ndf-policies.md`・`.kiro/prompts/` の NDF の 5 本（`--with-codex` で入れたなら `codex.md` も）・`.kiro/skills/` の NDF への symlink（mcp-* を入れたなら `.kiro/mcp_runtime/` と `.kiro/agents/default.json` の該当の項目も）を消す |

## 更新情報

バージョンごとの変更と移行の手順は [CHANGELOG.md](https://github.com/devbasex/ai-plugins/blob/main/CHANGELOG.md) の `[ndf <バージョン>]` のセクションにあります。
10 系から 11.0.0 へ上げるときは、11.0.0 のセクションの手順（動いている計画とラッパーを止めてから更新する）に従ってください。

## Playwright テストについて

v7.0.0 で Playwright による E2E テストの 4 Skill を **`playwright-kit` プラグイン**へ分離しました。
Skill 名は変わらないため `/playwright-` まで打てば候補に出ますが、別途インストールが要ります。
移行の経緯は [README.md](../../README.md) の「NDF v7.0.0 の主な変更（非互換）」にあります。

```bash
/plugin install playwright-kit@ai-plugins             # Claude Code
codex plugin add playwright-kit@ai-plugins            # Codex
```

## ndf コマンド

Skill・hook・CI が呼ぶ処理は `bin/ndf` のサブコマンドです。`$NDF` は NDF のプラグインの保存先で、ランタイムの導入記録が指す
ディレクトリです（`$CLAUDE_PLUGIN_ROOT` は hook の中でしか値を持ちません）。キャッシュの最も新しいバージョンを選ばないのは、
タグに固定して前のバージョンへ戻した後も、キャッシュに新しいバージョンのディレクトリが残るためです。Claude Code では
導入記録（`plugins/installed_plugins.json` の `installPath`）から次のように引いて打ちます。

```bash
NDF=$(python3 -c 'import json, os, sys; home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"); print(json.load(open(os.path.join(home, "plugins", "installed_plugins.json")))["plugins"]["ndf@ai-plugins"][0]["installPath"])')
"$NDF/bin/ndf" --help                  # サブコマンドの一覧
"$NDF/bin/ndf" worktree --help         # サブコマンドごとの使い方
```

Codex では `"${CODEX_HOME:-$HOME/.codex}"/plugins/cache/*/ndf/` の下の、導入したバージョンのディレクトリを指定します
（前のバージョンへ戻したときは、最も新しいディレクトリでなく戻したバージョンのディレクトリを選びます）。

結果は 1 行の JSON（`tool`・`status`・`summary`・`items`・`metrics`）で返ります。サブコマンドごとの引数と結果の形は
[docs/contracts/commands.md](docs/contracts/commands.md)、終了コードは [docs/contracts/exit-codes.md](docs/contracts/exit-codes.md) にあります。

| 主なサブコマンド | すること |
| --- | --- |
| `worktree` | 作業場所（`.worktrees/<ブランチ>`）の用意・一覧・片付けと、設定 `.ndf/worktree.json` の作成（`init`） |
| `relay` | カットポイントで claude を起動し直すラッパー（中継）の導入（`install`）・起動・停止・一覧 |
| `statusline` | NDF の statusline の導入（`ensure`）・切り替え（`set`）・復元（`restore`） |
| `training` | アカウントが入力を学習に使わない設定かを確かめる（`check`） |
| `notify` | 送信待ち通知を Slack へ送る（`send`） |
| `hooks` | hook の計測（`stats`）・予算の検査（`check-budget`）・登録の生成（`render`） |
| `contracts` | 設定と入出力の表の検査（`check`）・結果 JSON の出力（`emit`） |
| `settings` | 利用先の設定を新しいキーへ書き換える（`migrate`） |

10 系の機能のうち、11.0.0 で `bin/ndf` にエントリポイントが無いもの（用語集の検査・指示書の検査・待ち通知の一部・会話の保存期間の
保持など 8 機能）は 11.x で移します（#3791「入れ替えで未作成だった 8 機能を 11 のエントリポイントへ移す」）。

## Hooks

hook は Claude Code と Codex のどちらも `hooks/entry.py` の 1 つのエントリポイントから呼ばれ、事象と Tool が当たる guard を順に動かします。
guard の一覧と順序は `contracts/hooks/guards.json`、閾値は `contracts/limits/`（1 方針 1 ファイル）にあります。

| guard | 事象 | すること |
| --- | --- | --- |
| `merge-production` | PreToolUse（Bash） | 直に打つ `gh pr merge` が自動で本番へ届くなら止める |
| `context-limit` | PreToolUse・PostToolUse | 文脈の量が判断開始の値（既定 450,000）以上なら利用者の入力ごとに 1 度知らせ、運用上限（既定 600,000。`NDF_CONTEXT_LIMIT`）以上なら引継ぎに要る呼び出し（引継ぎ文書の Read・Write・Edit、TaskStop、`ndf records handoff check` と `ndf relay context record` だけの Bash）を除いて止め続ける。サブエージェントは見ない |
| `heredoc` | PreToolUse（Bash） | 区切りを引用符で囲まないヒアドキュメントの本文にコマンド置換があれば止める |
| `sleep` | PreToolUse（Bash） | 前景のループの中の `sleep` と前景の長い `sleep` の待ちを止め、Monitor か背景の起動を勧める |
| `process-wait` | PreToolUse（Bash） | `pgrep -f` / `pkill -f` をループで繰り返す待ちを止め、完了マーカーのファイルか Monitor で待つよう勧める |
| `full-test` | PreToolUse（Bash） | worker の中で、設定したテストの suite の全体の実行を止める |
| `script-chain` | PreToolUse（Bash） | `ndf` のサブコマンドをシェルの演算子やループでつないで待つ呼び出しを止める |
| `read-repeat` | PreToolUse（Read） | 同じファイルの同じ範囲を続けて読むのを止め、Grep を勧める |
| `ask-forward` | PreToolUse（AskUserQuestion） | 質問を中継とオーガナイザーへ渡し、待ちの知らせを送信待ちへ書く |
| `stop-wait` | Stop | 最後の応答が AskUserQuestion を使わずに本文で回答を待つなら書き直しを求める |
| `relay-busy` | SessionStart・PreToolUse | 中継の下で、バックグラウンドの作業と監視の開始を記録する。作業の開始を記録できないときは、その呼び出しを拒否する |
| `relay-marker` | Stop | 中継の下で、残るバックグラウンドの作業の件数を記録し、最後の応答の `ndf-next` のブロックを中継へ渡す（作業が残る間は渡さずに応答を続けさせる） |
| `turn`・`session`・`unattended` | UserPromptSubmit・SessionStart ほか | 数え手の数え直し・hook の予算の検査・人のいない実行の報告 |

**guard を止めるのは環境変数 `NDF_HOOK_OFF` だけです。** 名前をカンマでつなぐか、`all` ですべてを止めます
（例 `NDF_HOOK_OFF=sleep,read-repeat`）。

Codex の hook は Codex 側で明示的に有効化するまで実行されません。`~/.codex/config.toml` の `[hooks.state]` に対象 hook の
`enabled = true` が要ります。`/hooks` で対象 hook を確認し、利用するプロジェクトで有効化してください。

### worktree 運用

開発の変更を `.worktrees/` の worktree の中で行い、リポジトリを clone したディレクトリ（メインディレクトリ）では行わない運用を
支えます。設定ファイル `.ndf/worktree.json` は `/ndf:worktree` を起動すると手順 0 で作られます。手で作るなら次を実行します。

```bash
"$NDF/bin/ndf" worktree init
```

```json
{
  "version": 1,
  "guard": {
    "allow_paths": ["issues/", "docs/", ".claude/", ".codex/", ".kiro/", ".ndf/", ".gitignore"]
  }
}
```

`guard.allow_paths` は、メインディレクトリで編集しても案内を出さないパスです。書き込み先の判断は `ndf worktree guard`、
メインディレクトリに残った未コミットの変更の案内は `ndf worktree session` が出します。手順は `/ndf:worktree` にあります。

### 学習の設定を確かめる

`ndf training check` は、アカウントの「入力を学習に使う」設定を確かめます。学習に使う設定（On）なら、その設定を Off へ書き換え、
読み直して Off になったかを確かめます。結果は 1 行の JSON で、終了コードが 0（すべて学習に使わない）のときだけ入力を渡してよい、
という使い方をします。`--runtime` は `claude` と `codex` です。

```bash
"$NDF/bin/ndf" training check --runtime claude
```

- 書き換えはアカウント全体に効きます（ブラウザの claude.ai・ChatGPT の設定も Off になります）。書き換えを止める手段はありません。
  学習に使う設定のままにしておきたいアカウントは、claude・codex を NDF から起動しないでください
- 書き換えたときは、書き換えたキー・前の値・後の値・時刻が、結果の `items` の `rewrites` と、状態の保存先の `training.json` に残ります
- 書き換えに失敗したとき（書き込みが受け付けられない・読み直しても On のまま）は、起動を断ります。理由の文に、手で Off にする場所
  （claude は claude.ai の設定の「プライバシー」、codex は ChatGPT の設定の「データコントロール」と Codex の設定）と、
  Off にした後に打つ `ndf training check --runtime <担当>` が載ります

NDF が claude・codex を起動するとき、確かめた記録が無いか期限切れなら、その場で同じ確認（書き換えを含む）をして記録します。
書き換えられなかったか確かめられなかったときだけ起動を断り、`ndf refactor run`・`ndf cross-review run` は提案やレビューを流す前に
`precondition` で止まります。

## Slack 通知

利用者の回答か承認が無いと進まない時点でだけ、Slack へ知らせます。11.0.0 では、選択式の質問（`AskUserQuestion`）と、
応答が本文で回答を待って終わったとき（`Stop`）に、hook が知らせを送信待ちへ書き、送り手が切り離して Slack へ送ります。
送れなかった知らせは残り、次の起動で送り直します。

利用プロジェクト側で以下の環境変数を設定します（`.env` からも読みます。既に環境にある値は上書きしません）。

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>
```

| 変数 | 意味 |
| --- | --- |
| `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` | 必須。無ければ何も送りません |
| `SLACK_USER_MENTION` | 任意。メンション付きで送ります |

機密値は `.env` などで管理し、リポジトリへコミットしないでください。10 系の待ち通知のうち、ツールの権限確認・Codex の
通知の切り替え・Redmine の URL などは 11.x で移します（#3791）。

## 外部 AI 委譲

`/ndf:external-ai` skill または `corder` エージェントから外部 AI 委譲を使う場合は、利用環境に
Codex CLI をインストールしてログインします。

```bash
npm install -g @openai/codex
codex login
```

`/ndf:pr-review <PR番号> agy` や `/ndf:cross-review --include agy` で agy 委譲を使う場合は、利用環境に
Antigravity CLI をインストールしてログインします。ログインの手順は初回の対話起動にあり、
`agy models` が終了コード 0 で終われば認証済みです。

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy          # 初回だけ。ブラウザでログインする
agy models   # 認証確認
```

## Codex の暗黙起動抑止

取り消しが難しい以下 2 個の Skill は、`skills/<name>/agents/openai.yaml` の `policy.allow_implicit_invocation: false` によって **Codex の暗黙起動 (モデルが自分で選んで起動する経路) を抑止**しています。対象は Codex へ配る Skill のうち frontmatter が `disable-model-invocation: true` のもので、`ndf manifests render` が `manifests/skills.json` から作ります。

| Skill | 内容 |
|-------|------|
| `cherry-pick-pr` | 環境ブランチへの cherry-pick PR 作成 |
| `deploy` | 環境ブランチ (qa/staging, release/v2 等) への deploy PR 作成 |

**プラグイン Skill では、抑止すると `$<skill 名>` による明示起動も効かなくなります。** 起動する手段は SKILL.md の
絶対パスを示して読ませることだけです。実体は Codex のプラグインキャッシュ（ワークスペースの外）にあります。

```text
$CODEX_HOME/plugins/cache/<marketplace>/<plugin>/<version>/skills/<skill>/SKILL.md
# 既定 ($CODEX_HOME=~/.codex) の例:
# ~/.codex/plugins/cache/ai-plugins/ndf/11.0.35/skills/deploy/SKILL.md
```

実体のパスは `codex plugin list` で確かめます。

```bash
codex plugin list | grep 'ndf@ai-plugins'
# => ndf@ai-plugins  installed, enabled  11.0.35  <path>
```

Claude Code では `disable-model-invocation: true` と `/ndf:deploy` のスラッシュコマンドで同じ役割を果たします。

## 実機検証の記録

kiro-cli の実機検証と、Skill 数がコンテキスト量へ与える影響の実測は
[docs/field-test-records.md](docs/field-test-records.md) にある。
**その時点の実測であり、以後の構成変更には追随しない。**

## 変更するとき

Skill の実体は `skills/` の 1 箇所だけです。ランタイムごとのコピーはありません。変更したら
[CONTRIBUTING.md の「手元での検証」](../../CONTRIBUTING.md#手元での検証)の検証を実行してください。
frontmatter の規約は [skills/AUTHORING.md](skills/AUTHORING.md) にあり、
`python3 scripts/check-skill-frontmatter.py` でチェックします。
