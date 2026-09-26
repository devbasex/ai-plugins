# NDF Plugin

PR 運用、レビュー、調査、実装計画、仕様書化、開発方法論（要求定義・テスト駆動・リファクタリング・
完了判定）、Docker container access、statusline、外部 AI 委譲、Slack 通知を提供します。

配布物は `plugins/ndf/` の 1 ディレクトリにまとまっています。Skill の実体は `skills/` の
1 箇所だけで、どのランタイムへ配るかは `manifests/*-skills.txt` が決めます。

| ランタイム | 公開 Skill | マニフェスト |
| --- | --- | --- |
| Claude Code | 47 個 | `.claude-plugin/plugin.json` |
| Codex | 43 個 | `.codex-plugin/plugin.json` |
| Kiro CLI | 44 個 | `dev.kiro/install.sh`（プラグイン機構が無いため installer で導入） |
| agy | 43 個 | `dev.agy/plugin.json`（取得元の登録が無いため clone から導入） |

## レイアウト

```text
plugins/ndf/
├── .claude-plugin/plugin.json   # Claude Code のマニフェスト
├── .codex-plugin/plugin.json    # Codex のマニフェスト
├── skills/                      # 配布 Skill の唯一の実体（47 個）
├── skills/AUTHORING.md          # Skill 執筆の規約
├── manifests/                   # ランタイム別の配布 Skill 一覧
├── agents/                      # Claude Code のサブエージェント定義（専門 8 個と、3 層の定義 3 個（supervisor 2・worker 1））
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
[docs/versioning-and-distribution.md の「開発版を試す」](../../docs/versioning-and-distribution.md#開発版を試す)にあります。

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
# => NDF統合開発エージェント（Kiro CLI用 / v10.17.30）
```

### agy

agy にも取得元の登録がありません。clone した `dev.agy` を直接導入します。**更新の副コマンドが
無い**ため、新しい版へは入れ直します（`install` は上書きするが、消したファイルは実体に残る）。

```bash
agy plugin install plugins/ndf/dev.agy                               # 初回
agy plugin uninstall ndf && agy plugin install plugins/ndf/dev.agy   # 新しい版へ
```

導入すると `manifests/agy-skills.txt` に載る Skill 43 個と、エージェント 11 個（専門 8 個と、3 層の定義 3 個（supervisor 2・worker 1））、hook 1 個が
`~/.gemini/config/plugins/ndf/` へコピーされます。symlink は実体へ解決されてコピーされるため、
clone を消しても導入した内容は残ります。

**hook はコピーされるだけで、agy はそれを読み込みません**（agy 1.1.26 で実測）。読む先は
`~/.gemini/config/hooks.json` の 1 か所だけです。次を実行して差し込みます。**冪等で、他の
項目には触れません**（`--dry-run` で内容を確認でき、`--uninstall` で外せます）。

```bash
bash plugins/ndf/dev.agy/install-hooks.sh
```

```bash
agy plugin list
# => {"imports":[{"name":"ndf","source":"antigravity","components":["skills","agents","hooks"]}]}
```

## v10.17.30 へ更新するとき

- Add: 決定 17 の試行を実験版に置き、結果を記録する（#1142 ミッション 2 の L0 の前）（#1238）
- new mission が書く検査のプランの PR 本文は、課題を閉じる語を含まず、課題を「関連: #番号」として並べる（#1239）
- mcp-redash の操作は `/redash <add|list|remove|status>` の 1 本で行う（#1250）
- 以前の 4 Skill の呼び方からの移行手順は README に載っている（#1250）
- 計画書の雛形を plan_skeleton.py で作れる（#1251）
- シナリオを lint_scenario.py で静的に検査できる。playwright-authoring には app_ready.sh も入る（#1251）
- playwright-kit-ops のスクリプトは標準出力へ JSON を出す（#1251）
- 文書に終了コードの表がある（#1251）
- ミッション m1142b の課題を develop へ取り込む。（#1255）
- 関連: #1142（#1255）
- Docs: #1142 の設計の追加（汎用の処理を外部ライブラリへ・決定 19・20・ミッション 2b）（#1257）
- Add: 計画の実行とキューを置き換えるライブラリの候補を実験版で試し、結果を記録する（#1142 の不足 i）（#1258）
- 無し（検査の修正だけ）（#1260）
- `scripts/token-usage.py` は、版を接尾辞の数字の大きさの順に並べる（`10.17.30-dev.9` の次に `10.17.30-dev.10`）（#1261）
- Fix: 行数の上限の例外リストを 1 項目 1 ファイルへ分け、並列の計画の衝突を無くす（#1262）
- Add: 試行 T2（hook を 1 本の Python と tree-sitter-bash へまとめる成り立ちと所要）（#1264）
- supervise.py の run では、claude -p の worker が「結果: 判断が要る」か「結果: できなかった」で終えると、そのステップは失敗になります。（#1265）

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

### worktree 運用（4 ランタイム共通）

開発の変更を `.worktrees/` の worktree の中で行い、リポジトリを clone したディレクトリ
（メインディレクトリ）では行わない運用を支えます。**編集は止めません。** 案内が出ても操作は成立します。

| 起きること | 担う hook | Claude Code | Codex | Kiro CLI | agy |
| --- | --- | --- | --- | --- | --- |
| メインディレクトリの保護対象パスを編集しようとすると案内が出る | tool 実行前 | `PreToolUse` | `PreToolUse` | — | `PreToolUse` |
| worktree で作業する旨の案内がプロンプトごとに出る | プロンプト送信時 | — | — | `userPromptSubmit` | — |
| メインディレクトリに残った未コミット変更が提示される | セッション開始時 | `SessionStart` | `SessionStart` | `agentSpawn` | `PreInvocation` |
| メインディレクトリのブランチが稼働中の worktree へ追従する（既定では動かさない。worktree の設定の `follow_branch: true` で有効にする） | セッション開始時 | `SessionStart` | `SessionStart` | `agentSpawn` | `PreInvocation` |

Kiro CLI に tool 実行前の案内が無いのは、この事象でモデルへ案内を渡す手段が終了コード 2 に
限られ、それが tool の実行を拒否するためです。拒否しない方針のもとでは置けないため、パスを
見ない案内をプロンプト送信時の hook が担います。

**agy の 2 つは `install-hooks.sh` を実行するまで届きません。** agy はプラグインの
`hooks.json` を読み込まないためです（「インストール / agy」を参照）。

**agy は案内を作る時点と渡せる時点が離れています。** tool 実行前の hook がモデルへ文言を返す
口は拒否のときにしか働かないため、案内はセッションの記録へ積み、次のモデル呼び出しの前に
`injectSteps` で渡します。セッション開始時にあたる事象も持たないため、モデル呼び出しの通し番号が
0 のときを開始時として扱います。

**この仕組みはリポジトリ側の設定ファイル `.ndf/worktree.json` があるときだけ動きます。**
設定が無いリポジトリでは、いずれの hook も何も出力せず終了コード 0 で終わります。

設定ファイルは `/ndf:worktree` を起動すると手順 0 で作られます。手で作るなら次を実行します。

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

`guard.allow_paths` は、メインディレクトリで編集しても案内を出さないパスです。省略すると
組み込みの既定（上記と同じ一覧に `.agents/` `.serena/` を加えたもの）を使います。
空の配列を書くと「何も許可しない」という指定になります。

手順は `/ndf:worktree` にあります。

### 待ちの問い合わせと長い会話を止める（Claude Code だけ）

`scripts/token-guard.sh` が PreToolUse の `Bash` / `Read` / `Skill` / `Agent` で動き、3 つを
止めます。止めたときは、代わりの手段を理由の欄に出します。

| 止めるもの | 止め方 | 上限 |
| --- | --- | --- |
| 前景の `sleep` の待ち（`while` / `until` のループの本体、または上限を超える秒数） | `NDF_SLEEP_GUARD=0` | `NDF_SLEEP_MAX_SEC`（既定 5） |
| 変わらないファイルの同じ範囲を続けて読む Read | `NDF_READ_REPEAT_GUARD=0` | `NDF_READ_REPEAT_LIMIT`（既定 3） |
| 文脈が上限を超えた conductor が工程へ入る起動（1 度だけ止め、新しい会話で打つコマンドを `ndf-next` のブロックで示させる。ラッパーの下では止め続ける） | `NDF_CONTEXT_GUARD=0` | `NDF_CONTEXT_LIMIT`（既定 200000） |
| 寿命 5 分の supervisor（`ndf:supervisor`）が、文脈を最初の呼び出しの 1.5 倍以上に伸ばしたまま `cross-review` / `cross-refactoring` を起動する（止め続け、`結果: スイッチポイント` で返させる） | `NDF_SUPERVISOR_CUT_GUARD=0` | `NDF_SUPERVISOR_CUT_RATIO`（既定 1.5） |

| ランタイム | 待ち方 | 会話を切る |
| --- | --- | --- |
| Claude Code | hook ＋ 規約 | hook ＋ 再開コマンド |
| Codex | 規約だけ | 再開コマンドだけ |
| Kiro CLI | 規約だけ | 再開コマンドだけ |
| agy | 規約だけ | 再開コマンドだけ |

規約は `skills/development-workflow/references/waiting.md`（待ち方）と
`skills/development-workflow/references/context-window.md`（会話を切る）にあります。

### その他

Claude Code の SessionStart hook（`hooks/claude.json`）は上記に加えて次を行います。

- `~/.claude/settings.json` の `cleanupPeriodDays` を 90 日以上に保つ
- statusline 未設定時に NDF 標準 statusline を設定する
- カットポイントで claude を起動し直すラッパー（`scripts/relay.py`）のコピーが在れば今の版で置き直す（`relay.py startup`。
  版は後退させない）。10.17.4〜10.17.6 が自動で足した alias の管理ブロックが残っていれば 1 度だけ知らせる。
  **シェルの設定は書かない。** ラッパーを入れる・外すのは `/ndf:install-wrapper`（Claude Code だけ）

Claude Code の Stop・Notification・PermissionRequest hook と `AskUserQuestion` の PreToolUse hook は、
利用者の回答か承認を待つときだけ Slack へ知らせます（下の「Slack 通知」）。ラッパーの下（`NDF_RELAY_DIR` がある）では、最後の応答の
`ndf-next` のブロックをラッパーのシグナルファイルへ写します（`relay.py mark`）。`AskUserQuestion` の PreToolUse /
PostToolUse hook は、ラッパーの下で質問の表示中のシグナルファイルを作る・消します（ラッパーが質問の答えを代わりに
送らないため）。好きな時点で切り替えるのは `/ndf:restart` です。ラッパーの始め方・止め方・上限は
`skills/development-workflow/references/relay.md` にあります。

Codex の Stop・PermissionRequest hook（`hooks/codex.json`）は `NDF_CODEX_SLACK_NOTIFY=true` が設定されている
場合だけ Slack 通知を送ります。**Codex の hook は Codex 側で明示的に有効化するまで実行され
ません。** `~/.codex/config.toml` の `[hooks.state]` に対象 hook の `enabled = true` が要ります。
`/hooks` で対象 hook を確認し、利用するプロジェクトで有効化してください。

Kiro CLI では installer が `.kiro/agents/ndf.json` の `hooks` を生成します。

## Slack 通知

利用者の回答か承認が無いと進まない時点でだけ、Slack へ知らせます。応答が終わっても、待っていなければ
送りません。たとえば応答が「この設計でマージしてよいですか。」で終わると、次の本文が届きます。

```text
【承認待ち】[ai-plugins] この設計でマージしてよいですか。
セッション: https://claude.ai/code/session_01AbCdEf
host: devbase-01 / cwd: /work/ai-plugins
PR: https://github.com/devbasex/ai-plugins/pull/1150
```

同じ応答が「設計 PR を出しました。レビューの結果を待ちます。」で終わった場合は、何も送りません。

### 送る時点

| ランタイム | 時点 | 印 |
| --- | --- | --- |
| Claude Code | ツールの権限確認（`Notification` の `permission_prompt`） | 【承認待ち】 |
| Claude Code | 計画の承認（`ExitPlanMode` の `PermissionRequest`） | 【承認待ち】 |
| Claude Code | 選択式の問い（`AskUserQuestion`）・MCP の入力フォーム（`elicitation_dialog` / `elicitation_url_dialog`） | 【回答待ち】 |
| Claude Code・Codex・Kiro | 応答が文で回答か承認を求めて終わる（`Stop` / `stop`） | 【回答待ち】か【承認待ち】 |
| Codex | ツールの実行の承認（`PermissionRequest`） | 【承認待ち】 |

Codex の選択式の問いと、Kiro の承認の画面・選択式の問いは、捉える hook が無いため送りません。

- **文で求めているかは、応答の最後の 3 行の形で決めます。** 問いの形（`？` `ですか` `ますか` など）、
  依頼の形（`ください` `お願いします` `よければ` など）、利用者の返事を待つと述べる文（`承認を待っています` など）が
  あれば待ちです。コード・引用・表・見出しは見ません。承認の語（`承認` `マージ` `進めて` `てよいですか` など）を
  含めば【承認待ち】、含まなければ【回答待ち】です。語の並びは `scripts/lib/wait_notice.py` にあります
- **同じ待ちは 1 回だけ送ります。** `AskUserQuestion` や `ExitPlanMode` の後に届く `permission_prompt` は
  同じ待ちとして送りません。放置の通知（`idle_prompt`）は捉えません
- **非対話の `claude -p`（`CLAUDE_CODE_ENTRYPOINT` が `sdk-` で始まる）では送りません。** Codex の `codex exec` と
  Kiro の非対話の実行は対話と見分けられないため、問いの形で終われば通知が出ることがあります
- 本文の 1 行目は種類の印・リポジトリ名・求めている文（200 字まで）です。要約は作りません

### 復帰先と関連 URL

| 場合 | 載る行 |
| --- | --- |
| Claude Code の Remote Control 中・クラウドのセッション | `セッション: https://claude.ai/code/<ID>` |
| Claude Code のそれ以外 | `再開: claude --resume <session_id>` |
| Codex | `再開: codex resume <session_id>` |
| Kiro | `再開: kiro-cli chat --resume-id <ID>`。ID が無ければ `再開: kiro-cli chat --resume`（cwd で打つ） |

どの場合も `host: <ホスト名> / cwd: <cwd>` の行を足します。

- 【回答待ち】には、応答に出た issue（GitHub の `/issues/<n>` と `#<n>`）と Redmine の URL を 3 件まで載せます。
  `#<n>` は `origin` が GitHub を指すときだけ URL にします
- 【承認待ち】には PR の URL を先頭に載せます。応答に無ければ、現在のブランチの PR を `gh pr view` で補います
- Redmine は `REDMINE_URL` のホストの URL と `Redmine #<n>` を採ります。`REDMINE_URL` が無ければ載せません

### 設定

利用プロジェクト側で以下の環境変数を設定します。`.env` は cwd から git のトップまで上へ探し、無ければ
プラグインの置き場から上へ探します。既に環境にある値は上書きしません。

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>
# Codex のみ
NDF_CODEX_SLACK_NOTIFY=true
```

| 変数 | 意味 |
| --- | --- |
| `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` | 必須。無ければ何も送りません |
| `SLACK_USER_MENTION` | 任意。メンション付きを送って通知を鳴らし、メンション無しを送り直してから前者を消します |
| `NDF_CODEX_SLACK_NOTIFY` | Codex だけ必須。`true` のときだけ Codex で動きます |
| `NDF_SLACK_NOTIFY_DONE` | 任意。`true` なら待ちでない応答の終わりも【完了】として送ります（本文は最後の段落の先頭 200 字） |
| `REDMINE_URL` | 任意。Redmine の URL を見分けるホスト（例 `https://redmine.example.com`） |
| `DEBUG_SLACK_NOTIFY` | 任意。`true` なら `~/.claude/logs/wait-notify-<日付>.log` へ判定の理由を書きます |

機密値は `.env` などで管理し、リポジトリへコミットしないでください。通知の記録（同じ待ちを 2 度送らないための
直前の 1 件）は `${XDG_STATE_HOME:-~/.local/state}/ndf/wait-notify/` にセッションごとに置き、7 日で消します。

**Kiro は `install.sh --with-slack` を打ち直してください。** 通知の入口は `scripts/wait-notify.py` の 1 本です。
`stop` hook が配布物に無い `scripts/slack-notify.js` を指す `.kiro/agents/ndf.json` では、通知が届きません。

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

取り消しが難しい以下 2 個の Skill は、`skills/<name>/agents/openai.yaml` の `policy.allow_implicit_invocation: false` によって **Codex の暗黙起動 (モデルが自分で選んで起動する経路) を抑止**しています。共有 Skill の frontmatter が `disable-model-invocation: true` のものが対象で、`scripts/build-runtime-plugins.sh` が自動生成します。

| Skill | 内容 |
|-------|------|
| `cherry-pick-pr` | 環境ブランチへの cherry-pick PR 作成 |
| `deploy` | 環境ブランチ (qa/staging, release/v2 等) への deploy PR 作成 |

`merged` / `pr` / `pr-tests` / `pr-review` は日常的に自然文で依頼されるため、v5.0.0 で暗黙起動を許可しました。代わりに、実行前確認が要ると決まった手順 (push、PR 作成) の直前に対象を提示して同意を得ることを各 Skill の本文で必須化しています。要否は `skills/AUTHORING.md` の「実行前確認の要否を決める 3 つの問い」が決めます。

**`merged` のブランチ・worktree の削除は止まりません。** どれも事後に戻せる (ハッシュからの復元・Restore branch) ため実行前確認を置かず、消した対象と戻し方を作業完了報告へ載せます。止まるのは `git` が拒んだ対象だけです。`pr` の push と PR 作成の同意は残ります。

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
~/.codex/plugins/cache/ai-plugins/ndf/10.17.30/skills/deploy/SKILL.md を読んで、その手順どおりに qa/staging へ deploy PR を作成してください。

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
# ~/.codex/plugins/cache/ai-plugins/ndf/10.17.30/skills/deploy/SKILL.md
```

そのため「`deploy` の SKILL.md を探して読んで」のような曖昧な依頼は、Codex のファイル探索がワークスペース内に限られる状況では失敗しえます。**抑止した Skill は `$<skill 名>` が展開されない**ので、`codex plugin list` で実体パスを確認し、絶対パスを渡してください。

```bash
codex plugin list | grep 'ndf@ai-plugins'
# => ndf@ai-plugins  installed, enabled  10.17.30  <path>
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

kiro-cli の実機検証と、Skill 数がコンテキスト量へ与える影響の実測は
[docs/field-test-records.md](docs/field-test-records.md) にある。
**その時点の実測であり、以後の構成変更には追随しない。**

## 変更するとき

Skill の実体は `skills/` の 1 箇所だけです。ランタイムごとのコピーはありません。変更したら
[CONTRIBUTING.md の「手元での検証」](../../CONTRIBUTING.md#手元での検証)の検証を実行してください。
frontmatter の規約は [skills/AUTHORING.md](skills/AUTHORING.md) にあり、
`python3 scripts/check-skill-frontmatter.py` でチェックします。
