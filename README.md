# AI Plugins

Claude Code / Codex / Kiro CLI向けのスキル・MCP設定を共有するための内部マーケットプレイスです。

## 概要

このマーケットプレイスは、チーム全体でAI開発ツール（Claude Code / Codex / Kiro CLI）の導入を加速するための事前設定されたプラグインを提供します。

**NDFプラグイン v8.4.0** は、同じ `ndf@ai-plugins` という名前で Claude Code / Codex / Kiro CLI へ配布されるランタイム別プラグインです。共通ソースは `plugins/ndf-shared/` に集約し、利用者が install する配布物は `plugins/ndf-claude/` / `plugins/ndf-codex/` / `plugins/ndf-kiro/` に分かれています。

- **公開Skills**: Claude Code向け core 27個、Kiro向け core 26個、Codex向け core 25個に分離。
- **元Skills（30個）**:
  - PR/レビューワークフロー (7): pr, pr-tests, fix, pr-review, cherry-pick-pr, deploy, merged
  - 開発方法論 (5): development-workflow, requirements-design, tdd-cycle, refactoring, quality-gates
  - 原則・ガイドライン (9): ndf-policies, implementation-plan, plan-to-spec, investigation-rules, problem-solving, logging-guidelines, markdown-writing, issue-plan-strategy, ml-model-structure
  - データ分析・品質・環境 (4): qa-security-scan, docker-container-access, google-auth, official-skills-autoloader
  - 外部サービス連携 (1): google-drive
  - AIクロスレビュー (2): cross-review, external-ai
  - 運用 (2): skill-stats, statusline
- **8つの専門エージェント**: director, data-analyst, corder, researcher, qa, debugger, devops-engineer, code-reviewer
- **自動フック**: SessionStart (transcript保持期間を最低90日に保つ) + Stop (AI要約生成+Slack通知)
- **外部AI委譲**: `/ndf:external-ai` skill + `corder` エージェント経由で Codex / Gemini CLI をバックグラウンド実行 (v4.0.0 で Codex MCP サーバは廃止)
- **AIクロスレビュー強化**: `/ndf:cross-review` は codex/gemini 両方に PR レビューを委譲し、Gemini の進捗 heartbeat、`--focus` / `--extra-instructions-file`、PR 種別別の自動レビュー観点テンプレートに対応
- **Kiro CLI対応**: `plugins/ndf-kiro/install.sh` によるワンコマンドセットアップ
- **MCPプラグイン**: `plugins/mcp/shared/` を編集元とし、Claude / Codex / Kiro 向け配布物を `plugins/mcp/{claude,codex,kiro}/` に生成

## 利用方法

### Claude Code

#### 1. マーケットプレイスの追加

```bash
/plugin marketplace add https://github.com/devbasex/ai-plugins
```

#### 2. プラグインのインストール

```bash
# NDFプラグイン（オールインワン統合プラグイン）
/plugin install ndf@ai-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

ローカルで検証する場合:

```bash
codex plugin marketplace add ./local/path/to/ai-plugins
codex plugin add ndf@ai-plugins
```

### Kiro CLI

#### 1. リポジトリをクローン

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
```

#### 2. インストーラーを実行

```bash
# 基本（Skills + agentSpawnフックのみ）
bash plugins/ndf-kiro/install.sh

# Slack通知も有効化
bash plugins/ndf-kiro/install.sh --with-slack

# 全部入り（Slack + Codex CLI 連携）
bash plugins/ndf-kiro/install.sh --with-slack --with-codex
```

インストーラーは `plugins/ndf-kiro/skills/` から `.kiro/skills/` への symlink、`.kiro/steering/ndf-policies.md`、`.kiro/agents/ndf.json` を生成します。

#### 3. Slack通知の設定（オプション）

`.env` に以下を設定：
```
SLACK_CHANNEL_ID=C0123456789
SLACK_BOT_TOKEN=xoxb-...
SLACK_USER_MENTION=<@U0123456789>
```

#### 4. 起動

```bash
kiro-cli chat --agent ndf
```

既定エージェントとして使いたい場合は `bash plugins/ndf-kiro/install.sh --set-default` を実行します。

詳細は [KIRO.md](./KIRO.md) を参照。

### 利用可能なプラグイン

| プラグイン名 | バージョン | 説明 | 詳細 |
|------------|----------|------|------|
| **ndf** | 8.4.0 | Claude Code / Codex / Kiro CLI 向けに runtime 別配布物を提供する NDF プラグイン。8個の専門エージェント（Claude版）、公開Skills（Claude Code向け core 27個、Kiro向け core 26個、Codex向け core 25個）、Claude SessionStart/Stopフック、Codex/Kiro向け通知・実行補助を提供。v4.0.0 で Codex MCP サーバを廃止し、`/ndf:external-ai` skill + `corder` エージェント経由の CLI 直接実行に一本化。 | [Claude](./plugins/ndf-claude/README.md) / [Codex](./plugins/ndf-codex/README.md) / [Kiro](./plugins/ndf-kiro/README.md) |
| **playwright-kit** | 1.0.0 | Playwright による E2E テストの計画・実装・証跡管理を提供するプラグイン。ページ役割からのテスト計画、動画 / trace 付きスクリプト実装、レポート生成と Drive 保管、playwright_kit ランタイム（init、a11y / CWV スキャン）の 4 Skill。NDF v7.0.0 で分離。 | [Claude](./plugins/playwright-kit-claude/README.md) |

### NDF v8.4.0 の主な変更

**`/ndf:markdown-writing` に敬意ある表現のルールを追加し、図表ガイドを読む位置を明示しました。**

| 変えたこと | 内容 |
| --- | --- |
| 表現のルール（新ルール 4） | 文書は関係者本人が読む前提で書く。**強い否定語・過剰な装飾語・根拠の曖昧な断定**を、対象物の状態と次にやること・数値・エビデンスへ置き換える |
| セルフチェック | 上記 3 種を検出する grep をチェック手順へ追加 |
| 図表ガイドの参照位置 | `01-diagram-guide.md` を図表ルールの冒頭で**手順として読ませる**。記法と横幅の上限は SKILL.md へ書かず、ガイドだけが持つ構成にした |

**あわせて `/ndf:pr` の完了報告を手順へ組み込みました。** 報告が手順の外（末尾の独立節）に
あり、既存 PR を更新する経路からは「終了報告」の一語しか導線がなかったため、報告が省略され
たり、PR URL が Markdown リンクになって画面に番号しか出ない状態になっていました。

| 変えたこと | 内容 |
| --- | --- |
| 報告の位置 | `### 6. 完了報告` として手順に組み込み、末尾の独立節は削除。既存 PR を更新しただけの経路からも参照する |
| 報告の形 | 埋めるだけのテンプレートと、値の取得コマンド（`gh pr view --json …` / `git log` / `git diff --stat`）を提示 |
| PR URL | **生の URL をそのまま書く**。Markdown リンクにすると番号しか表示されず、URL を取り出せない |

**`/ndf:cross-review` の実行で見つかった 3 件も直しました。**

| 直したこと | 変更 |
| --- | --- |
| 差分外の行を指すインラインで指摘が丸ごと消える | 422 (`Line could not be resolved`) はレビュー本体ごと落とす。差分外は body に書き、422 時はインラインを body へ移して再投稿する |
| 投稿に失敗すると前ラウンドの結果で判定が続く | launcher が起動時に前ラウンドの result / payload を削除し、投稿失敗時も `post_error` 付きの result.json を書かせる |
| `monitor.py` が実行権限を持たない | 実体 (`lib/monitor.py`) へのシムに実行権限を付与。`wait-review.sh` 経由も含めて `Permission denied` で止まらなくなった |

### NDF v8.3.0 の主な変更

**`/ndf:cross-refactoring` の公開（push）の責務を進行側へ一本化しました。**
生成物の同期を pre-push で検査するリポジトリでは、「実装担当は編集元だけを触る」という
範囲ルールと衝突して、あらゆる push が落ちる状態でした。

| 直したこと | 変更 |
| --- | --- |
| 実装担当の push が範囲ルールと衝突する | **実装担当は push しない**。公開するのは進行側だけで、検証を通した後に行う |
| 生成物の同期を進行側が実行する手段がない | **`--sync-command` を新設**。push の直前に進行側が実行し、差分はどの改善項目にも属さないコミットとして積む |
| 適用に失敗した項目が次ラウンドで再採用される | 項目別の失敗とラウンド全体の取り消しの両方から対象外（`deferred_items`）へ記録する |

```bash
/ndf:cross-refactoring 130 --scope src/services \
  --sync-command "bash scripts/build-runtime-plugins.sh" \
  --baseline-test "pytest -q"
```

**互換性（破壊的）**: 適用・修正のプロンプトから push の指示が外れました。`merge-apply` /
`merge-fix` が成功・失敗のどちらでも検証後に push します。生成物を持つリポジトリでは
`--sync-command` を指定してください。

### NDF v8.2.0 の主な変更

**`/ndf:cross-refactoring` を実機検証で見つかった 9 件の不具合について修正しました。**
提案フェーズは設計どおり動いていましたが、適用結果の検証で失敗した項目を取り消す経路が
破綻し、進行を続行できない状態でした。

| 直したこと | 変更 |
| --- | --- |
| 取り消しが他項目のコミットと競合する | 範囲を新しい順に全て戻し、残す項目を積み直す。分離できない位置関係のときはラウンド全件へ退避する |
| 取り消し失敗を握り潰して進行する | 中断を**終了コード 4** で表し、「全件失敗」（2）と区別する |
| 適用結果が状態に残らない | 項目ごとの判定を**その都度**保存する（`rounds[].apply_progress`） |
| 未検証の変更が公開されたまま残る | 取り消しへ着手する**前**に `pending_push` を立て、次の実行で再送信する |
| 範囲外の変更を検証しない | `--scope` を適用・修正の検証にも効かせる。生成物の同期は**進行側の責務**へ分離 |
| 提案の記録が次ラウンドで上書きされる | 提案の結果ファイル名にもラウンド番号を入れる |
| gemini が配置した手順書を読めない | 作業ディレクトリへ読み取り除外を無効にする設定を置く |
| 語彙の許容値をプロンプトが列挙しない | 検証側が持つ語彙集合をプロンプトへ機械的に列挙する |
| 初期化が CLI の認証を確認しない | `init` が参加 CLI の認証状態を確認し、未認証なら中断する |

**互換性**: 提案の結果ファイル名が `<ランタイム>-propose-rf<ID>-r<ラウンド>-result.json` へ
変わります。`--scope` には**現状固定テストの置き場所も含めてください**（検証に効くため）。

### NDF v8.1.0 の主な変更

**多ランタイム・リファクタリング収束ループ `/ndf:cross-refactoring` を追加しました。**

`/ndf:cross-review` がレビューを収束させるのと同じ発想で、リファクタリングを収束させます。
`refactoring` Skill が持っていなかった 2 つ — **何を直すかの発見**と**直した結果の他者検証** —
を、複数の CLI へ役割を分けることで補います。

```bash
/ndf:cross-refactoring 130 --scope src/services --baseline-test "pytest -q"
```

| 役割 | 担当 |
|---|---|
| 提案・レビュー | 全ランタイム − ホストの 3 者 |
| 適用 | 全ランタイム − gemini の 3 者（claude / codex / kiro）から輪番で 1 者 |

- ホストは提案とレビューに参加しないため、**実装した者と評価する者が同一モデルになりません**
- ホストと同じランタイムが適用担当になる場合も、サブエージェントではなく **CLI プロセス**として
  起動します。ホストセッションの作業文脈に差分やレビュー本文が載りません
- レビューは**提案ラウンドの差分全体**に対して 1 回だけ回します。改善項目ごとに回すと CLI の
  起動回数が採用件数に比例して膨らむためです（1 ラウンド 33 回 → 9 回）
- 収束しない改善項目は**項目単位で取り消します**。同じラウンドで合意済みの項目は残ります
- `--model <ランタイム>=<モデル>` でモデルを固定でき、実行主体はコミットのトレーラーと
  レビューコメントに残ります。`report --metrics` がランタイム × モデルで集計します

あわせて、収束ループの共通層を
[`plugins/ndf-shared/skills/cross-review/scripts/lib/`](./plugins/ndf-shared/skills/cross-review/scripts/lib/README.md)
へ切り出しました。`/ndf:cross-review` の挙動と既存テストは変わりません。

`/ndf:external-ai` は Kiro CLI と `claude -p` の非対話実行手順を追加し、対象 CLI が 4 つに
なりました。

### NDF v8.0.0 の主な変更（非互換）

**構造改善の Skill を `/ndf:refactoring` へ改名しました。** 引数と手順は変わりません。

| 旧コマンド | 移行先 |
|---|---|
| `/ndf:safe-refactoring` | `/ndf:refactoring` |

`safe-` を外したのは、`/refactoring` で一意に決まり、入力が短くなるためです。対応表は
`ndf-policies` にあり、v9.0.0 で削除します。

**あわせて、分岐・反復・定数の表現を決める観点を統合しました。** リファクタリングの起点となる
兆候（コードスメル）に 3 件を追加し、判断材料を参照資料として持たせています。

| 追加した観点 | 置き換え先 |
|---|---|
| 業務ルールの埋め込み（料率・区分・しきい値が制御構文に埋まっている） | 対応表への置き換え |
| 一件ずつの反復（往復回数や実行時間が件数に比例する） | 一括処理への置き換え |
| 検証のない外部化（設定・マスタにスキーマ・版・検証がない） | スキーマと版を与え、読み込み境界で検証する |

判断材料は
[references/data-representation.md](./plugins/ndf-shared/skills/refactoring/references/data-representation.md)
にあります。「分岐が多いから表にする」ではなく**変化するから表にする**、という切り分けを置き、
ガード節・静的に網羅性を検査できる分岐・逐次依存のループ・閉じた状態集合の列挙型は「そのままで
よい」ものとして明示しています。具体的な手段は言語ごとに 1 ファイルへ分けており（`references/lang-python.md` /
`lang-javascript.md` / `lang-typescript.md` /
[`lang-php.md`](./plugins/ndf-shared/skills/refactoring/references/lang-php.md)）、
SKILL.md が対象言語のファイルだけを読ませます。他言語の内容はコンテキストに載りません。
記載のない言語でも判断材料はそのまま使えます。

### NDF v7.0.0 の主な変更（非互換）

**ブラウザ自動テストの 4 Skill を `playwright-kit` プラグインへ分離しました。** Skill 名は
変わらないため、`/playwright-` まで打てば従来どおり候補に出ます。変わるのはプラグイン接頭辞だけです。

| 旧コマンド | 移行先 |
|---|---|
| `/ndf:playwright-planning` | `/playwright-kit:playwright-planning` |
| `/ndf:playwright-authoring` | `/playwright-kit:playwright-authoring` |
| `/ndf:playwright-evidence` | `/playwright-kit:playwright-evidence` |
| `/ndf:playwright-kit-ops` | `/playwright-kit:playwright-kit-ops` |

利用するには `playwright-kit` を別途インストールしてください（導入手順は
[playwright-kit の README](./plugins/playwright-kit-claude/README.md) にランタイム別で記載）。

```bash
# Claude Code
/plugin install playwright-kit@ai-plugins
# Codex
codex plugin add playwright-kit@ai-plugins
# Kiro CLI
bash plugins/playwright-kit-kiro/install.sh
```

**Skill の `description` を圧縮しました。** 挙動は変わりません。

| 指標 | v6.1.0 | v7.0.0（NDF 単独） |
|---|---:|---:|
| `description` の 1 個あたり平均 | 237 | **148** |
| Claude Code 初期一覧の合計 | 7,772 | **4,990** |
| frontmatter 合計 | 13,017 | **7,578** |

Skill の `name` と `description` は起動時の一覧としてコンテキストへ常時注入され、その予算は
**プラグイン横断で共有されます**。この開発環境の実測では、公式プラグイン 35 Skill と NDF の
合計が 14,485 文字ありました。NDF の取り分を下げるため、次の 3 つを行いました。

- トリガ語の書式を `Triggers: 'a', 'b'` から `Use when …（a・b）` へ変更（旧書式は廃止）
- 使う場面が限られ frontmatter が大きい playwright 系をプラグインへ分離
- `allowed-tools` は**削っていません**。調査の結果これは利用制限ではなく事前承認（確認プロンプトの
  スキップ）で、外すと手順のたびに承認を求められるためです

実測で分かったこととして、`Triggers:` の列挙は `description` 末尾にあるため暗黙起動に届きにくく、
用途文へ埋め込んだ方が安定して起動します（詳細は
[docs/specifications/ndf-skill-inventory.md](docs/specifications/ndf-skill-inventory.md)）。

### NDF v6.1.0 の主な変更

**開発方法論の Skill を 5 個追加しました。** 既存のコマンド名・引数・挙動は変わりません。

| 追加した Skill | 役割 |
|---|---|
| `/ndf:development-workflow` | 変更を 4 モード（`light` / `standard` / `architecture` / `legacy-refactor`）へ分類し、必要な工程だけへ振り分ける |
| `/ndf:requirements-design` | 曖昧な要求を、観測可能で検証できる受け入れ条件へ変換する |
| `/ndf:tdd-cycle` | 「失敗するテスト → 通す最小実装 → 整理」のサイクルを定義する |
| `/ndf:refactoring` | コードスメル起点の構造改善と、テストが乏しい既存コード向けの現状固定テスト（v6.1.0 当時の名称は `/ndf:safe-refactoring`） |
| `/ndf:quality-gates` | 完了宣言の前に、実行コマンド・終了コード・実行時刻・対象範囲を証跡として要求する |

全変更にフル工程を課さないことを設計の中心に置いています。文言修正や設定変更は
`light` モードとして軽い経路だけを通り、モードの判定基準は `development-workflow` の
1 箇所だけが持ちます。

既存 6 Skill をこのレイヤーへ接続しました。`implementation-plan` は受け入れ条件・不変条件・
互換性・切り戻し手順・完了の定義をプラン書式へ追加、`problem-solving` は修正前の再現テストを
必須化、`pr-review` は仕様適合とコード品質の二段構成へ再編、`pr-tests` は限定的な検証と
全体テストを区別して証跡を要求、`plan-to-spec` はドメイン用語・不変条件・公開インタフェース・
設計判断の結論を確定仕様へ引き継ぎ、`investigation-rules` は `problem-solving` との境界を
明記しました。

あわせて `upstream-skills.lock.yaml` を追加し、Skill の設計で参照した外部リポジトリと固定
コミットを記録しました。上流の文章は転用しておらず、工程の分け方と判断基準だけを参照して
書き下ろしているため、配布物へ同梱する告知は持ちません。転用が生じた場合はこの記録を起点に
告知を用意します。

### NDF v6.0.0 の主な変更（非互換）

**`/ndf:review` を `/ndf:pr-review` へ改名しました。** 引数と挙動は変わりません。

| 旧コマンド | 移行先 |
|---|---|
| `/ndf:review` | `/ndf:pr-review` |

理由は、`review` が `code-review`（Claude Code 組み込み）/ `security-review` /
`cross-review` の末尾要素で、`/` メニューで `review` と打つと候補に埋もれるためです
（[#83](https://github.com/devbasex/ai-plugins/issues/83)）。`/pr-rev` まで打てば一意に
決まり、`/ndf:pr` `/ndf:pr-tests` とも接頭辞が揃います。

あわせて Skill の**命名規約**に「ランタイム組み込みや主要プラグインの Skill 名の末尾要素に
しない」を追加し、`scripts/check-skill-frontmatter.py` が既知の外部 Skill 名との衝突を
警告するようにしました。配布先の環境に何が入っているかは検査時点で分からないため、
この検査は手動更新の一覧による best-effort です。

v5.0.0 で載せた旧コマンド名の対応表は、予告どおり `ndf-policies` から削除しました。
v4.20.1 以前から移行する場合は v5.0.0 の `ndf-policies` を参照してください。

### NDF v5.0.0 の主な変更（非互換）

Skill を利用実績にもとづいて棚卸し、**49 個から 29 個へ整理**しました。旧コマンド名から新コマンド名への対応表は `ndf-policies` skill に 1 リリース分だけ載せています（v6.0.0 で削除）。

- **統合（-11）**: 重複していた Skill を、利用実績の多い側の名前を残して統合しました。`/ndf:review-branch` → `/ndf:review --branch`、`/ndf:review-pr-comments` `/ndf:resolve-pr-comments` → `/ndf:fix`、`/ndf:clean` `/ndf:sync-main` → `/ndf:merged`、`/ndf:branch-fix-strategy` → `/ndf:cherry-pick-pr`、`/ndf:codex` `/ndf:gemini` → `/ndf:external-ai`、ブラウザ自動テスト 9 個 → 4 個。
- **削除（-9）**: 起動実績がなく、現在のモデルの標準能力か汎用コマンドで足りるものを削除しました（`/ndf:git-gh-operations` `/ndf:python-execution` など）。このうち `/ndf:sync-main` は内容を `/ndf:merged` へ吸収しているため移行先があります。移行先を用意せず消したのは 8 件です。
- **自然文で発動するようになりました**: `merged` / `pr` / `pr-tests` から明示指示専用の設定を外しました。取り消しの難しい手順の前には対象を提示して確認を取ります。`review` も同じ設定にしましたが、Claude Code では組み込みの `code-review` が同じ用途を持つため自然文では選ばれません。`/ndf:review` で明示的に起動してください。
- **frontmatter 規約と機械検査**: 発動判定に必要な情報を `description` へ集約し、`scripts/check-skill-frontmatter.py` で CI 検査します（規約は `plugins/ndf-shared/skills/README.md`）。
- **Kiro CLI**: エージェント名が `default` → `ndf` に変わりました。`install.sh` の再実行が必要です。`--set-default` と `--scope workspace|global` を追加し、常時指示を `.kiro/steering/` へ移しました。
- **Codex**: 明示指示専用の Skill に `agents/openai.yaml` を生成し、暗黙起動を抑止します。プラグイン配布の Skill は抑止すると `$<skill 名>` も効かないため、起動するには SKILL.md のパスを示します（`plugins/ndf-codex/README.md`）。

### NDF v4.20.1 の主な変更

- Kiro CLI 版のエージェント定義に `tools` を宣言しました。未宣言のままでは Kiro CLI がツールを1つも持たないエージェントとして読み込むため、skill が SKILL.md を読むことも git / gh を実行することもできませんでした。
- runtime smoke test に、生成された Kiro エージェント定義が `tools` を宣言しているかの検査を追加しました。従来はファイルの生成有無しか見ておらず、この欠落を検出できませんでした。

### NDF v4.20.0 の主な変更

- `markdown-writing` skill を、体裁ルールから**第三者可読性のルール**へ拡張しました。適用対象に仕様書・PR 本文・調査レポート・レビューコメントを追加しています。
- 説明文にテーブル名・カラム名などの内部識別子や、会話中に作ったローカル略語を持ち込まないルールを追加しました。「何のために」「何をやったか」の説明で識別子を使うと、書いた側は説明した気になり読み手には伝わらないためです。
- 検討過程の痕跡（案A / Option A 等）と変更履歴（「以前は〜だったが変更した」）を本文に残さないルール、否定的な結論にエビデンスを必須とするルール、個人情報・認証情報を文書に含めないルールを追加しました。
- 書き終えた後の grep セルフチェックとチェックリストを整備しました。

### NDF v4.19.0 の主な変更

- `plan-to-spec` skill を追加し、実装完了後の plan を `docs/` 配下の確定仕様書へ移動・リライト・レビューする標準フローを定義しました。
- 完了報告テンプレートを追加し、元 plan、確定仕様書、レビュー結果、検証内容を一貫した形式で報告できるようにしました。

## 開発ガイドライン

### プラグイン開発

#### ディレクトリ構造

```
ai-plugins/
├── .agents/
│   └── plugins/
│       └── marketplace.json      # Codexマーケットプレイスメタデータ
├── .claude-plugin/
│   └── marketplace.json          # Claude Codeマーケットプレイスメタデータ
├── plugins/
│   ├── ndf-shared/               # NDF共通編集元（直接installしない）
│   ├── ndf-claude/               # Claude Code版NDF配布物
│   ├── ndf-codex/                # Codex版NDF配布物
│   ├── ndf-kiro/                 # Kiro CLI版NDF配布物/installer
│   └── mcp/
│       ├── shared/               # MCPプラグイン共通編集元
│       ├── claude/               # Claude Code版MCP配布物
│       ├── codex/                # Codex版MCP配布物
│       └── kiro/                 # Kiro CLI版MCP配布物/installer
├── README.md
└── CLAUDE.md                     # AIエージェント向けガイドライン
```

#### Runtime plugin の検証

共通ソースや runtime 配布物を変更した場合は、生成物同期と manifest / link 検証を実行します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/validate-runtime-plugins.sh
```

実ランタイムのインストール経路を確認する場合は、Docker コンテナ内で smoke test を実行します。

```bash
bash scripts/runtime-smoke-test.sh
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
bash scripts/runtime-smoke-test.sh --runtime kiro
```

ローカル hook を使う場合は以下を実行します。

```bash
bash scripts/install-dev-hooks.sh
```

#### 新しいプラグインの作成手順

**1. プラグインディレクトリを作成:**

```bash
mkdir -p plugins/{plugin-name}/{.claude-plugin,commands,agents,skills}
```

**2. `plugin.json` を作成:**

```json
{
  "name": "plugin-name",
  "version": "1.0.0",
  "description": "プラグインの説明",
  "author": {
    "name": "作者名",
    "url": "https://github.com/username"
  },
  "skills": [
    {
      "path": "skills/skill-name/SKILL.md"
    }
  ]
}
```

**3. プロジェクトスキルを作成（オプション）:**

`skills/{skill-name}/SKILL.md` を作成：

```markdown
---
name: スキル名
description: スキルの説明（自動起動のキーワードを含める）
---

# スキル名

スキルの詳細説明とドキュメント...
```

**4. `marketplace.json` に登録:**

`.claude-plugin/marketplace.json` に追加：

```json
{
  "name": "ai-plugins",
  "owner": {
    "name": "takemi-ohama",
    "email": "takemi.ohama@example.com"
  },
  "plugins": [
    {
      "name": "plugin-name",
      "source": "./plugins/plugin-name",
      "description": "プラグインの簡単な説明"
    }
  ]
}
```

**5. README.md を作成:**

`plugins/{plugin-name}/README.md` を作成し、以下を含める：
- プラグインの概要
- インストール手順（マーケットプレイス追加を含む）
- 使用方法
- トラブルシューティング

**6. テストとコミット:**

```bash
# ローカルでテスト
/plugin marketplace add file:///path/to/ai-plugins
/plugin install plugin-name@ai-plugins

# 動作確認後、コミット
git add .
git commit -m "Add plugin-name plugin"
git push
```

#### 開発のベストプラクティス

**実施すること:**
- ✅ セマンティックバージョニング（MAJOR.MINOR.PATCH）に従う
- ✅ `plugin.json` に完全なメタデータを含める
- ✅ YAMLフロントマター付きの `SKILL.md` を作成
- ✅ 包括的なドキュメント（README.md）を提供
- ✅ 環境変数で認証情報を管理
- ✅ `.env` を `.gitignore` に追加
- ✅ インストール手順をテスト
- ✅ プラグイン追加時は `marketplace.json` を更新

**してはいけないこと:**
- ❌ 機密トークンや認証情報をコミット
- ❌ ドキュメントをスキップ
- ❌ バージョンインクリメントを忘れる
- ❌ 一貫性のない命名規則を使用

### マーケットプレイス管理

#### プラグインの更新

```bash
# 1. プラグインファイルを修正
# 2. plugin.json のバージョンをインクリメント
vim plugins/{plugin-name}/.claude-plugin/plugin.json

# 3. 変更をコミット
git add plugins/{plugin-name}
git commit -m "Update plugin-name to v1.1.0"
git push
```

ユーザーは Claude Code UI から更新を確認できます。

#### プラグインの削除

```bash
# 1. marketplace.json から削除
vim .claude-plugin/marketplace.json

# 2. オプションでプラグインディレクトリを削除
rm -rf plugins/{plugin-name}

# 3. 変更をコミット
git add .
git commit -m "Remove plugin-name from marketplace"
git push
```

#### バージョン管理ルール

セマンティックバージョニング（`MAJOR.MINOR.PATCH`）に従います：

- **MAJOR**: 破壊的変更（後方互換性なし）
- **MINOR**: 後方互換性のある新機能追加
- **PATCH**: バグフィックスのみ

例：
- `1.0.0 → 1.0.1`: バグ修正
- `1.0.1 → 1.1.0`: 新機能追加
- `1.1.0 → 2.0.0`: 破壊的変更

### リファレンス

#### 公式ドキュメント

- [Claude Code ドキュメント](https://docs.claude.com/en/docs/claude-code)
- [プラグインマーケットプレイス](https://code.claude.com/docs/ja/plugin-marketplaces)
- [プラグイン開発ガイド](https://docs.claude.com/en/docs/claude-code/plugins)
- [スキルドキュメント](https://docs.claude.com/en/docs/claude-code/skills)
- [MCP仕様](https://modelcontextprotocol.io)

#### MCPサーバー公式リポジトリ

- [GitHub MCP](https://github.com/github/github-mcp-server)
- [Serena MCP](https://github.com/oraios/serena)
- [Notion MCP](https://mcp.notion.com)
- [BigQuery MCP](https://github.com/ergut/mcp-server-bigquery)
- [DBHub MCP](https://github.com/bytebase/dbhub)
- [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)
- [AWS Documentation MCP](https://github.com/awslabs/aws-documentation-mcp-server)

#### プロジェクト内ドキュメント

- [CLAUDE.md](./CLAUDE.md) - AIエージェント向けガイドライン（Claude Code）
- [KIRO.md](./KIRO.md) - AIエージェント向けガイドライン（Kiro CLI）
- [docs/specifications/](./docs/specifications/) - 完了済みplan/issue由来の確定仕様
- [LICENSE](./LICENSE) - MITライセンス

## コントリビューション

1. このリポジトリをフォーク
2. 新しいプラグインを作成または既存のものを改善
3. プルリクエストを送信
4. 新しいプラグインを追加する場合は `marketplace.json` を更新

## サポート

問題が発生した場合：
1. 各プラグインの README.md を確認
2. 公式ドキュメントを参照
3. このリポジトリにイシューを開く
4. プラグイン作者に連絡（`plugin.json` を参照）

## ライセンス

MIT License - 詳細は [LICENSE](./LICENSE) ファイルを参照

---

**作成者:** takemi-ohama - https://github.com/takemi-ohama
