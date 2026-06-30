# Agent runtime plugin split: Claude / Kiro / Codex 分離計画

## 関連リンク

- 調査対象: `plugins/ndf/`, `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`, `.kiro/`, `scripts/install-kiro.sh`
- 背景調査: Claude Code / Codex / Kiro は Skill 本文を共有しやすい一方、plugin manifest、hooks、agents、marketplace、Kiro agent config はランタイム固有である
- テスト設計: `issues/runtime-plugin-container-test-plan.md`

## 概要

現在 `plugins/ndf/` に Claude Code / Kiro CLI / Codex 向けの配布物が混在している。これを以下の構成へ完全移行する。

```text
plugins/
├── ndf-shared/                 # 配布対象ではない共通ソース
│   ├── skills/
│   ├── agents-source/
│   ├── hooks-source/
│   └── scripts/
├── ndf-claude/                 # Claude Code plugin
│   ├── .claude-plugin/plugin.json
│   ├── skills/
│   ├── agents/
│   ├── hooks/
│   └── README.md
├── ndf-codex/                  # Codex plugin
│   ├── .codex-plugin/plugin.json
│   ├── skills/
│   ├── hooks/
│   └── README.md
└── ndf-kiro/                   # Kiro CLI 配布/生成用
    ├── install.sh
    ├── agents/default.json.template
    ├── prompts/
    ├── skills/
    └── README.md

plugins/mcp/
├── shared/
│   ├── mcp-bigquery/
│   ├── mcp-aws-docs/
│   └── ...
├── claude/
│   ├── mcp-bigquery/
│   ├── mcp-aws-docs/
│   └── ...
├── codex/
│   ├── mcp-bigquery/
│   ├── mcp-aws-docs/
│   └── ...
└── kiro/
    ├── mcp-bigquery/
    ├── mcp-aws-docs/
    └── ...
```

ゴールは、各ランタイムの配布単位を明確に分けつつ、Skill 本文と共通スクリプトの重複管理を避けること。

重要: `ndf-shared` はランタイムから参照される依存先ではなく、**開発時の編集元**である。Claude / Codex / Kiro が実行時に読むのは、それぞれ `ndf-claude` / `ndf-codex` / `ndf-kiro` 配下に build で生成・同期されたファイルだけとする。

MCP プラグインも同じ原則を適用する。`plugins/mcp/shared/` は編集元であり、各ランタイムが install するのは `plugins/mcp/claude/*` / `plugins/mcp/codex/*` / `plugins/mcp/kiro/*` の生成物だけとする。

## 問題・背景

現行構成では以下が `plugins/ndf/` に同居している。

| ランタイム | 現行ファイル |
|---|---|
| Claude Code | `.claude-plugin/plugin.json`, `agents/`, `hooks/hooks.json`, `skills/` |
| Codex | `.codex-plugin/plugin.json`, `hooks/codex-hooks.json`, `skills-codex/` |
| Kiro CLI | `scripts/install-kiro.sh` が Claude manifest を読んで `.kiro/skills/` / `.kiro/agents/default.json` を生成 |
| 共通/任意 | `skills-optional/`, `scripts/`, `README.md`, `CHANGELOG.md` |

この構成には次の問題がある。

- Kiro が `.claude-plugin/plugin.json` を source of truth として読むため、Claude 公開セット変更が Kiro に暗黙影響する
- Codex は `.codex-plugin/plugin.json` と `skills-codex/` を持つため、すでに半分だけ分離されている
- hooks のイベント、trust、payload、env はランタイムごとに異なる
- Claude agents と Kiro agent config は概念が異なる
- marketplace が `.claude-plugin/marketplace.json` と `.agents/plugins/marketplace.json` で分かれているのに、source が同一 `plugins/ndf` を指している
- README / CHANGELOG / docs の説明で「NDF = 全ランタイム全部入り」になり、保守境界が見えづらい

また、現行の MCP プラグイン (`plugins/mcp-bigquery`, `plugins/mcp-aws-docs`, `plugins/mcp-serena` など) は Claude Code 向け `.claude-plugin/plugin.json` と `.mcp.json` を持つ構成であり、Codex / Kiro 向けの配布単位がない。今後は MCP プラグインも `mcp-bigquery@ai-plugins` のような同一 plugin 名で各ランタイムから install できるようにする。

## 設計方針

### 1. 配布単位はランタイム別に分ける

| 配布単位 | 対象 | source of truth |
|---|---|---|
| `ndf-claude` | Claude Code plugin marketplace | `.claude-plugin/plugin.json` |
| `ndf-codex` | Codex plugin marketplace | `.codex-plugin/plugin.json` |
| `ndf-kiro` | Kiro CLI local installer / agent config | `ndf-kiro/agents/default.json.template` |
| `ndf-shared` | 共通ソース。直接 install しない | `skills/`, `scripts/`, templates |

`plugins/ndf` は最終的に廃止する。互換期間を設ける場合は `plugins/ndf/README.md` に移行先だけを記載する薄い stub とするが、marketplace source からは外す。

MCP プラグインもランタイム別配布単位に分ける。ただし plugin 名は全ランタイムで維持する。

| plugin 名 | Claude source | Codex source | Kiro source |
|---|---|---|---|
| `mcp-bigquery` | `./plugins/mcp/claude/mcp-bigquery` | `./plugins/mcp/codex/mcp-bigquery` | `./plugins/mcp/kiro/mcp-bigquery` |
| `mcp-aws-docs` | `./plugins/mcp/claude/mcp-aws-docs` | `./plugins/mcp/codex/mcp-aws-docs` | `./plugins/mcp/kiro/mcp-aws-docs` |
| `mcp-serena` | `./plugins/mcp/claude/mcp-serena` | `./plugins/mcp/codex/mcp-serena` | `./plugins/mcp/kiro/mcp-serena` |

`mcp-*` の directory はランタイム別に分かれるが、marketplace 上の plugin name は同じにする。これにより Claude / Codex / Kiro のいずれでも `mcp-bigquery@ai-plugins` という名前で導入できる。

### 2. Skill は共通ソースから生成する

`ndf-shared/skills/` を編集元とし、ランタイム別の公開セットは manifest で定義する。

```text
plugins/ndf-shared/
├── skills/
│   ├── pr/
│   ├── cross-review/
│   └── ...
└── manifests/
    ├── claude-skills.txt
    ├── codex-skills.txt
    └── kiro-skills.txt
```

生成スクリプトは `scripts/build-runtime-plugins.sh` として作成し、以下を行う。

- `plugins/ndf-claude/skills/` を `claude-skills.txt` から同期
- `plugins/ndf-codex/skills/` を `codex-skills.txt` から同期
- `plugins/ndf-kiro/skills/` を `kiro-skills.txt` から同期
- 共通スクリプトが必要な Skill は Skill ディレクトリごと同期する
- 生成先の `.venv`, `.pytest_cache`, runtime artifact は除外する

生成物は marketplace install 時に必要なので commit 対象とする。`ndf-shared` が正、ランタイム別 `skills/` は生成物として扱う。

ランタイムは `ndf-shared` を直接参照しない。plugin install 後の cache 配置では sibling directory の存在や相対パスが保証されないため、各 runtime plugin は単体で完結している必要がある。

### 2.1 開発時の build フロー

Skill や共通スクリプトを変更する開発者は、必ず `ndf-shared` を編集してから runtime plugin を build する。

```bash
# 1. 共通ソースを編集
vim plugins/ndf-shared/skills/<skill-name>/SKILL.md

# 2. ランタイム別 plugin へ同期
bash scripts/build-runtime-plugins.sh

# 3. 生成物が最新か確認
bash scripts/build-runtime-plugins.sh --check
```

build 後の配置は以下になる。

```text
plugins/ndf-shared/skills/pr/SKILL.md      # 編集元
plugins/ndf-claude/skills/pr/SKILL.md      # Claude Code が読む生成物
plugins/ndf-codex/skills/pr/SKILL.md       # Codex が読む生成物
plugins/ndf-kiro/skills/pr/SKILL.md        # Kiro installer が読む生成物
```

生成物は commit 対象である。利用者が plugin を install するときに build を要求しないため、PR には `ndf-shared` の変更と runtime plugin 配下の生成結果を必ず含める。

`scripts/build-runtime-plugins.sh --check` は CI 相当の検証で使用し、`ndf-shared` と runtime plugin 配下の生成物に差分がある場合は失敗させる。

MCP プラグインも同じ build/check 対象に含める。

```text
plugins/mcp/shared/mcp-bigquery/          # 編集元
plugins/mcp/claude/mcp-bigquery/          # Claude Code が install する生成物
plugins/mcp/codex/mcp-bigquery/           # Codex が install する生成物
plugins/mcp/kiro/mcp-bigquery/            # Kiro が install する生成物
```

`scripts/build-runtime-plugins.sh` は NDF だけでなく MCP プラグインも同期する。`--check` は NDF と MCP の両方で生成物の未同期を検出する。

### 2.2 開発者 hook と CI

build 漏れを防ぐため、ローカル hook と CI の両方を設定する。

hook では原則としてファイルを書き換えない。commit / push の途中で生成物を自動更新すると staged 状態と working tree がズレるため、hook は check / validate に限定する。生成物が古い場合は失敗させ、開発者が明示的に build して生成物を stage する。

```bash
# 生成物を更新する明示コマンド
bash scripts/build-runtime-plugins.sh
git add plugins/ndf-shared plugins/ndf-claude plugins/ndf-codex plugins/ndf-kiro

# commit 前の確認
bash scripts/build-runtime-plugins.sh --check
```

ローカル hook は `scripts/install-dev-hooks.sh` で任意導入できるようにする。

| hook | 実行内容 | 目的 |
|---|---|---|
| `pre-commit` | `bash scripts/build-runtime-plugins.sh --check` | `ndf-shared` と runtime plugin 生成物の同期漏れを commit 前に止める |
| `pre-push` | `bash scripts/validate-runtime-plugins.sh` | plugin validate、manifest、リンク、生成物同期を push 前に確認する |

`pre-commit` は working tree だけでなく staged 状態も検査する。`build-runtime-plugins.sh --check` 後に runtime 生成物へ未ステージ差分が残っている場合、つまり開発者が build 後に `git add` を忘れている場合は失敗させる。少なくとも `git diff --name-only -- plugins/ndf-claude plugins/ndf-codex plugins/ndf-kiro plugins/mcp/claude plugins/mcp/codex plugins/mcp/kiro` と `git diff --cached --name-only -- ...` の両方を見て、生成元と生成先の staged set が不整合な commit を止める。

CI では hook と同等以上の検証を必須にする。ローカル hook は任意導入だが、CI は最終防衛線として必須にする。

| CI job | 実行内容 |
|---|---|
| `runtime-plugin-build-check` | `bash scripts/build-runtime-plugins.sh --check` |
| `runtime-plugin-validate` | `bash scripts/validate-runtime-plugins.sh` |
| `markdown-link-check` | docs / README / plugin README のローカルリンク検証 |

`scripts/validate-runtime-plugins.sh` は少なくとも以下を実行する。

```bash
bash scripts/build-runtime-plugins.sh --check
claude plugin validate plugins/ndf-claude
claude plugin validate .claude-plugin/marketplace.json
bash plugins/ndf-kiro/install.sh --help
# Codex plugin は利用可能な CLI / schema 検証手段がある場合に実行。
# CLI が無い環境では manifest 必須キーと参照パス存在チェックに fallback する。
```

### 3. hooks / agents / installers は共有しない

hooks と agents はランタイム固有にする。

| 種別 | 方針 |
|---|---|
| Claude agents | `ndf-claude/agents/` に配置 |
| Claude hooks | `ndf-claude/hooks/hooks.json` に配置 |
| Codex hooks | `ndf-codex/hooks/hooks.json` に配置 |
| Kiro agent config | `ndf-kiro/agents/default.json.template` に配置 |
| Kiro installer | `ndf-kiro/install.sh` に移動 |

共通ロジックが必要な場合は `ndf-shared/scripts/` に置き、各ランタイムからコピーまたは相対参照ではなく同梱コピーする。plugin install 後に別 plugin のパスへ依存しないため。

### 4. marketplace はランタイム別 source に変更する

| marketplace | 変更 |
|---|---|
| `.claude-plugin/marketplace.json` | `ndf` source を `./plugins/ndf-claude` へ変更。MCP plugin は `./plugins/mcp/claude/<plugin-name>` を指す |
| `.agents/plugins/marketplace.json` | `ndf` source path を `./plugins/ndf-codex` へ変更。MCP plugin は `./plugins/mcp/codex/<plugin-name>` を指す |
| Kiro | Kiro 用 marketplace または installer registry で `./plugins/mcp/kiro/<plugin-name>` を指す。NDF は `plugins/ndf-kiro/install.sh` を README / KIRO.md から案内 |

plugin name は互換性を優先して `ndf` のまま維持する。ディレクトリ名だけをランタイム別にする。

MCP plugin name も互換性を維持する。例: `mcp-bigquery` は Claude / Codex / Kiro のどの marketplace でも `mcp-bigquery` として登録する。

### 5. docs はランタイム別に入口を分ける

ルート README は marketplace 全体の案内に絞る。詳細は以下へ分離する。

| ドキュメント | 内容 |
|---|---|
| `plugins/ndf-claude/README.md` | Claude Code での使い方、agents、hooks、skills |
| `plugins/ndf-codex/README.md` | Codex plugin での使い方、hooks trust、skills |
| `plugins/ndf-kiro/README.md` | Kiro CLI での install、agent config、prompts |
| `plugins/ndf-shared/README.md` | 開発者向け。共通ソースと生成手順 |
| `docs/ndf-plugin-reference.md` | ランタイム分離後の全体リファレンス |

## 移行後のインストール方法

分離後もユーザー向けの plugin 名は `ndf` を維持する。インストール方法はランタイムごとに明確に分ける。

### Claude Code

Claude Code は `.claude-plugin/marketplace.json` を入口とし、marketplace 上の `ndf` が `plugins/ndf-claude` を指す。MCP plugin は同じ marketplace 上で `plugins/mcp/claude/<plugin-name>` を指す。

```bash
# marketplace を追加
/plugin marketplace add https://github.com/devbasex/ai-plugins

# NDF Claude 版をインストール
/plugin install ndf@ai-plugins

# MCP plugin も同じ名前でインストール
/plugin install mcp-bigquery@ai-plugins
/plugin install mcp-serena@ai-plugins
```

ローカル検証時:

```bash
/plugin marketplace add /path/to/ai-plugins
/plugin install ndf@ai-plugins
```

検証コマンド:

```bash
claude plugin validate plugins/ndf-claude
claude plugin validate .claude-plugin/marketplace.json
```

Claude 版に含めるもの:

- `.claude-plugin/plugin.json`
- `agents/`
- `hooks/hooks.json`
- Claude 公開対象の `skills/`
- Claude 版 README

### Codex

Codex は `.agents/plugins/marketplace.json` を入口とし、marketplace 上の `ndf` が `plugins/ndf-codex` を指す。MCP plugin は同じ marketplace 上で `plugins/mcp/codex/<plugin-name>` を指す。

```bash
# marketplace を追加
codex plugin marketplace add https://github.com/devbasex/ai-plugins

# NDF Codex 版をインストール
codex plugin add ndf@ai-plugins

# MCP plugin も同じ名前でインストール
codex plugin add mcp-bigquery@ai-plugins
codex plugin add mcp-serena@ai-plugins
```

Claude / Codex の marketplace URL は同一 repository root を指定し、各 CLI が自 runtime 用 manifest (`.claude-plugin/marketplace.json` または `.agents/plugins/marketplace.json`) を解決する前提にする。この前提は `scripts/runtime-smoke-test.sh --runtime claude|codex` で必ず検証する。CLI が root URL から正しい manifest を解決できない場合は、ユーザー向け docs と adapter を runtime 固有の marketplace URL / path 指定へ切り替える。

ローカル検証時:

```bash
codex plugin marketplace add /path/to/ai-plugins
codex plugin add ndf@ai-plugins
```

Codex 版に含めるもの:

- `.codex-plugin/plugin.json`
- `hooks/hooks.json`
- Codex 公開対象の `skills/`
- Codex 版 README

Codex hooks は初回実行前に Codex 側の trust / hooks 設定が必要になる場合があるため、`plugins/ndf-codex/README.md` に有効化手順を必ず記載する。

### Kiro CLI

Kiro CLI では NDF 本体はリポジトリを clone して `plugins/ndf-kiro/install.sh` を実行する方式にする。Kiro は `.kiro/agents/default.json` と `.kiro/skills/` をプロジェクトに生成する。

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins

# 基本（Skills + agentSpawn hook）
bash plugins/ndf-kiro/install.sh

# Slack通知も有効化
bash plugins/ndf-kiro/install.sh --with-slack

# Codex CLI 直接実行用の補助 prompt も追加
bash plugins/ndf-kiro/install.sh --with-slack --with-codex
```

ローカル開発中は同じコマンドを作業ツリーで実行する。

```bash
bash plugins/ndf-kiro/install.sh --dry-run
bash plugins/ndf-kiro/install.sh
```

MCP plugin は Kiro からも同じ plugin 名で導入できることを目標にする。Kiro に marketplace install 機構がある場合は以下の名前を維持する。

```bash
kiro-cli plugin marketplace add https://github.com/devbasex/ai-plugins
kiro-cli plugin install mcp-bigquery@ai-plugins
kiro-cli plugin install mcp-serena@ai-plugins
```

Kiro CLI に plugin marketplace install がない、または運用上 installer 方式に寄せる場合は、Kiro 用 MCP installer を提供する。

```bash
bash plugins/mcp/kiro/mcp-bigquery/install.sh
bash plugins/mcp/kiro/mcp-serena/install.sh
```

いずれの方式でも、ユーザー向けの plugin 名は `mcp-bigquery@ai-plugins` のまま説明する。installer 方式の場合も README では「Kiro 版 `mcp-bigquery@ai-plugins` を installer で導入する」と表現し、Claude / Codex と名前を揃える。

Kiro 版に含めるもの:

- `install.sh`
- `agents/default.json.template`
- `prompts/`
- Kiro 公開対象の `skills/`
- Kiro 版 README

`plugins/ndf-kiro/install.sh` は `.claude-plugin/plugin.json` を読まない。Kiro の公開 Skill は `plugins/ndf-shared/manifests/kiro-skills.txt` または `plugins/ndf-kiro/manifest` を source of truth とする。

### インストール方法のドキュメント配置

| 場所 | 記載内容 |
|---|---|
| `README.md` | Claude / Codex / Kiro の最短インストール手順 |
| `plugins/ndf-claude/README.md` | Claude 版の詳細、agents、hooks、statusline、Slack |
| `plugins/ndf-codex/README.md` | Codex 版の詳細、hooks trust、公開 Skill |
| `plugins/ndf-kiro/README.md` | Kiro 版の詳細、installer、agent config、prompts |
| `plugins/mcp/<runtime>/<plugin-name>/README.md` | 各ランタイムでの MCP plugin インストールと必要な環境変数 |
| `KIRO.md` | 開発者が Kiro CLI でこの repo を使うための手順 |

## 修正対象

### 新規作成

- `plugins/ndf-shared/`
- `plugins/ndf-claude/`
- `plugins/ndf-codex/`
- `plugins/ndf-kiro/`
- `plugins/mcp/shared/`
- `plugins/mcp/claude/`
- `plugins/mcp/codex/`
- `plugins/mcp/kiro/`
- `scripts/build-runtime-plugins.sh`
- `scripts/validate-runtime-plugins.sh`
- `scripts/install-dev-hooks.sh`
- `.githooks/pre-commit`
- `.githooks/pre-push`
- `.github/workflows/runtime-plugin-validate.yml`
- `tests/runtime-smoke/`
- `scripts/runtime-smoke-test.sh`
- `docs/specifications/runtime-plugin-separation.md`

### 変更

- `.claude-plugin/marketplace.json`
- `.agents/plugins/marketplace.json`
- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `KIRO.md`
- `docs/project-overview.md`
- `docs/plugin-development-guide.md`
- `docs/ndf-plugin-reference.md`

### 削除または stub 化

- `plugins/ndf/`
- 既存 `plugins/mcp-*`（`plugins/mcp/claude/*` へ移動し、必要なら README stub のみにする）
- ルート `scripts/install-kiro.sh`（`plugins/ndf-kiro/install.sh` へ移動）
- `.kiro/` 生成物を repo に残すかどうかは別途判断。基本はテンプレート管理へ寄せる

## PR 分割計画

release branch: `release/runtime-plugin-split`
base branch: `main`

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `feature/runtime-split-shared-builder` | `ndf-shared` と生成スクリプトを追加し、現行 `plugins/ndf` からランタイム別生成物を作れる状態にする | なし | ○ |
| 2 | `feature/runtime-split-claude` | `ndf-claude` を作成し、Claude marketplace を `ndf-claude` source に切り替える | PR1 | × |
| 3 | `feature/runtime-split-codex` | `ndf-codex` を作成し、Codex marketplace を `ndf-codex` source に切り替える | PR1 | ○ |
| 4 | `feature/runtime-split-kiro` | `ndf-kiro` を作成し、Kiro installer / docs / templates を移動する | PR1 | ○ |
| 5 | `feature/runtime-split-docs-cleanup` | README / AGENTS / docs / specs をランタイム分離後の表現に更新し、`plugins/ndf` を stub 化または削除する | PR2, PR3, PR4 | × |
| 6 | `feature/runtime-split-mcp-plugins` | MCP plugin を `plugins/mcp/shared|claude|codex|kiro` に分離し、各 marketplace / installer から同一 plugin 名で導入できるようにする | PR1 | ○ |
| 7 | `feature/runtime-split-validation` | validate script、開発者 hook、CI、リンク検証、plugin validate を追加・実行する | PR2, PR3, PR4, PR6 | ○ |
| 8 | `feature/runtime-split-container-smoke` | Claude / Codex / Kiro を軽量コンテナに実インストールし、plugin install、Skill、MCP、hook、agents の smoke test を追加する | PR7 | × |

単一 PR では差分が大きく、marketplace source 変更とディレクトリ移動が混在してレビュー困難になるため、release branch + 個別 PR 方式を採用する。

## タスク分解

### Task 1: 共通ソース `ndf-shared` を作る

- **対象ファイル:** `plugins/ndf-shared/**`, `scripts/build-runtime-plugins.sh`
- **変更内容:**
  - 現行 `plugins/ndf/skills/` を `plugins/ndf-shared/skills/` へ移動
  - 現行 `plugins/ndf/scripts/` の共通利用部分を `plugins/ndf-shared/scripts/` へ移動
  - Claude / Codex / Kiro の公開 Skill リストを `plugins/ndf-shared/manifests/*.txt` として作成
  - 生成スクリプトでランタイム別 `skills/` を再生成できるようにする
  - 開発者向け README に「編集元は `ndf-shared`、runtime plugin 配下は build 生成物、生成物も commit 必須」と明記する

### Task 2: Claude 版 `ndf-claude` を作る

- **対象ファイル:** `plugins/ndf-claude/**`, `.claude-plugin/marketplace.json`
- **変更内容:**
  - `.claude-plugin/plugin.json`、`agents/`、`hooks/hooks.json`、Claude 用 `README.md` を配置
  - `skills/` は `ndf-shared` から生成したものを commit
  - `.claude-plugin/marketplace.json` の `ndf.source` を `./plugins/ndf-claude` に変更
  - `claude plugin validate plugins/ndf-claude` を通す

### Task 3: Codex 版 `ndf-codex` を作る

- **対象ファイル:** `plugins/ndf-codex/**`, `.agents/plugins/marketplace.json`
- **変更内容:**
  - `.codex-plugin/plugin.json`、`hooks/hooks.json`、Codex 用 `README.md` を配置
  - 現行 `skills-codex/` を `skills/` として配置
  - `.agents/plugins/marketplace.json` の `ndf.source.path` を `./plugins/ndf-codex` に変更
  - Codex plugin validation / install smoke を実施できる手順を docs に記載

### Task 4: Kiro 版 `ndf-kiro` を作る

- **対象ファイル:** `plugins/ndf-kiro/**`, `KIRO.md`, `README.md`
- **変更内容:**
  - `scripts/install-kiro.sh` を `plugins/ndf-kiro/install.sh` へ移動
  - installer が `.claude-plugin/plugin.json` を読まないようにし、`ndf-kiro/manifest` または `ndf-shared/manifests/kiro-skills.txt` を読む
  - `.kiro/agents/default.json` はテンプレート生成物として扱い、必要なら repo root の `.kiro/` を削除または開発用サンプルにする
  - Kiro 用 prompts を `plugins/ndf-kiro/prompts/` に移す

### Task 5: 旧 `plugins/ndf` の扱いを決める

- **対象ファイル:** `plugins/ndf/**`, docs
- **変更内容:**
  - marketplace source から外した後、`plugins/ndf` を削除する
  - 互換期間が必要な場合は README だけの stub にする
  - stub にする場合も `.claude-plugin` / `.codex-plugin` は残さない。誤 install を避けるため

### Task 6: MCP plugin をランタイム別に分離する

- **対象ファイル:** `plugins/mcp-*`, `plugins/mcp/**`, `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`, docs
- **変更内容:**
  - 既存 `plugins/mcp-*` を `plugins/mcp/shared/<plugin-name>` に移動する
  - Claude 版を `plugins/mcp/claude/<plugin-name>` に生成する
  - Codex 版を `plugins/mcp/codex/<plugin-name>` に生成する
  - Kiro 版を `plugins/mcp/kiro/<plugin-name>` に生成する
  - `.claude-plugin/marketplace.json` は `mcp-bigquery` などの source を `plugins/mcp/claude/<plugin-name>` に変更する
  - `.agents/plugins/marketplace.json` は同じ plugin 名で `plugins/mcp/codex/<plugin-name>` を指すエントリを追加する
  - Kiro 版は marketplace install または installer 方式のどちらでも、ユーザー向け名称を `mcp-bigquery@ai-plugins` に揃える
  - 各 MCP plugin README に Claude / Codex / Kiro の導入方法と必要な環境変数を記載する

### Task 7: docs と検証を更新する

- **対象ファイル:** `README.md`, `AGENTS.md`, `CLAUDE.md`, `KIRO.md`, `docs/*.md`, `scripts/validate-runtime-plugins.sh`, `scripts/install-dev-hooks.sh`, `.githooks/*`, `.github/workflows/runtime-plugin-validate.yml`
- **変更内容:**
  - ルート README のディレクトリ構造を更新
  - `docs/ndf-plugin-reference.md` をランタイム別構成に更新
  - `docs/specifications/ndf-knowledge-and-kiro.md` の Kiro パスを更新
  - plugin validate、リンク検証、生成物差分検証をまとめたスクリプトを追加
  - `pre-commit` / `pre-push` hook を `.githooks/` に追加
  - `scripts/install-dev-hooks.sh` で `git config core.hooksPath .githooks` を設定できるようにする
  - GitHub Actions で build check / validate / Markdown link check を必須検証として追加

### Task 8: 実ランタイム smoke test をコンテナで追加する

- **対象ファイル:** `tests/runtime-smoke/**`, `scripts/runtime-smoke-test.sh`, `.github/workflows/runtime-plugin-smoke.yml`
- **変更内容:**
  - Claude / Codex / Kiro の CLI をそれぞれ隔離された軽量コンテナへインストールする
  - コンテナ内 HOME、CLI 設定、plugin cache、作業 repo をホストから分離する
  - ローカル作業ツリーを marketplace source として追加し、`ndf` と主要 `mcp-*` plugin を実際に install する
  - Skill、MCP、hook、agents / Kiro agent config が runtime plugin 配下から参照できることを確認する
  - secret がない環境では install / config / hook payload / MCP config の非認証 smoke を必須にする
  - 開発環境または信頼済み CI に secret が存在する場合は、許可リストに従って `--rm --tmpfs /tmp/runtime-secrets` コンテナへ注入し、認証付き Skill / MCP smoke まで実行する
  - secret 注入時は `--keep-container` を禁止し、secret と認証済み runtime cache の残存を防ぐ
  - `pull_request` CI では secret を渡さず、非認証 smoke のみを実行する
  - ブラウザ認証しかできない runtime、または `--with-secrets=off` の非認証 smoke では、login prompt / 認証 URL 表示まで到達すれば合格とする
  - 詳細は `issues/runtime-plugin-container-test-plan.md` に従う

## 影響範囲

| 領域 | 影響 |
|---|---|
| Claude Code users | インストール名は `ndf` のまま維持。source directory のみ変更 |
| Codex users | marketplace source が `plugins/ndf-codex` に変わる。plugin name は `ndf` 維持 |
| Kiro users | install コマンドが `bash plugins/ndf-kiro/install.sh` に変わる |
| plugin maintainers | Skill 編集元が `plugins/ndf-shared/skills` に変わる |
| MCP plugin users | Claude / Codex / Kiro のいずれでも `mcp-bigquery@ai-plugins` のような同一名で導入できる |
| docs | NDF が単一ディレクトリ全部入りではなく、ランタイム別配布物として説明される |
| CI runtime | 追加のコンテナ smoke test により、ネットワーク利用と CLI インストール時間が増える |

## 互換性方針

- plugin name は `ndf` を維持する
- Claude marketplace / Codex marketplace の表示名も `NDF` を維持する
- 旧 `plugins/ndf` パスを直接参照する docs / scripts は全て置換する
- plugin cache に残る旧構成との互換は保証しない。新規 install / update を前提にする
- Kiro は公式 marketplace ではなく installer ベースのため、README / KIRO.md の移行案内を明確にする
- MCP plugin name は全ランタイムで維持する (`mcp-bigquery`, `mcp-serena` など)
- 旧 `plugins/mcp-*` パスを直接参照する docs / scripts は全て置換する

## リスクと対策

| リスク | 対策 |
|---|---|
| 共通 Skill と生成先 Skill が乖離する | `scripts/build-runtime-plugins.sh --check` を追加し、生成差分があれば失敗 |
| marketplace source 変更で install が壊れる | Claude / Codex それぞれ validate と local install smoke を実施 |
| Kiro が Claude manifest 依存から外れて公開 Skill 数がずれる | `kiro-skills.txt` を明示し、installer で存在チェック |
| hooks の payload 差異を混同する | hooks はランタイム別ディレクトリに完全分離 |
| docs が旧 `plugins/ndf` を案内し続ける | `rg "plugins/ndf|skills-codex|install-kiro"` を検証スクリプトに含める |
| docs が旧 `plugins/mcp-*` を案内し続ける | `rg "plugins/mcp-[a-z]" README.md AGENTS.md docs plugins scripts` を検証スクリプトに含める |
| MCP plugin 名がランタイム間でズレる | marketplace 生成/検証で Claude / Codex / Kiro の plugin name 一致をチェックする |
| Kiro の MCP plugin install 方式が未確定 | Kiro marketplace が使えない場合の installer fallback を各 README に明記する |
| Git 履歴が追いづらくなる | 大きな移動は `git mv` を使い、PR を機能単位に分ける |
| hook が勝手に生成物を書き換えて staged 状態を壊す | hook では build を実行せず `--check` のみにする |
| hook 未導入の開発者が build 漏れを push する | CI の `runtime-plugin-build-check` で必ず失敗させる |
| CLI 実インストール smoke がホスト環境を汚染する | Docker コンテナ内に HOME / cache / config を閉じ込め、ホストの credential directory は mount しない |
| CLI や marketplace 機能が experimental で破壊的変更を受ける | smoke test は install 手順を fixture 化し、CLI version をログ出力する。破壊的変更時は plan 側ではなく runtime 別 adapter を更新する |
| PR CI で secret が漏洩する | `pull_request` では secret を渡さず、認証付き smoke は protected workflow / default branch / local の信頼済み context だけで実行する |
| 認証が必要な機能を CI で実行できない | secret がある場合は許可リストに従って `--rm --tmpfs /tmp/runtime-secrets` container へ注入して認証付き smoke を実行する。`auto` では secret がない場合は skip として記録し、`required` では失敗扱いにする |
| ブラウザ認証が非対話コンテナで完了できない | ブラウザ認証しかできない runtime、または `--with-secrets=off` の非認証 smoke では login prompt / 認証 URL 表示まで到達すれば合格とする。secret がある場合の browser prompt fallback は失敗扱いにする |

## テスト計画

詳細なテスト設計は `issues/runtime-plugin-container-test-plan.md` に分離する。この親計画では以下を完了条件として追跡する。

- [ ] `bash scripts/build-runtime-plugins.sh --check`
- [ ] `bash scripts/validate-runtime-plugins.sh`
- [ ] `bash scripts/runtime-smoke-test.sh --runtime claude`
- [ ] `bash scripts/runtime-smoke-test.sh --runtime codex`
- [ ] `bash scripts/runtime-smoke-test.sh --runtime kiro`
- [ ] `.github/workflows/runtime-plugin-validate.yml` が build check / validate / link check を実行する
- [ ] `.github/workflows/runtime-plugin-smoke.yml` が軽量コンテナで runtime smoke を実行する
- [ ] `rg "plugins/ndf($|[^-])|plugins/ndf/" README.md AGENTS.md CLAUDE.md KIRO.md docs plugins scripts .claude-plugin .agents` で旧 NDF パス残存を確認
- [ ] `rg "plugins/mcp-[a-z]" README.md AGENTS.md CLAUDE.md KIRO.md docs plugins scripts .claude-plugin .agents` で旧 MCP パス残存を確認

## 完了の定義

- [ ] Claude / Codex / Kiro の配布単位が別ディレクトリになっている
- [ ] `plugins/ndf` が削除または README stub のみに縮退している
- [ ] marketplace source がランタイム別ディレクトリを指している
- [ ] Kiro installer が Claude manifest に依存していない
- [ ] 共通 Skill の編集元と生成先の同期チェックがある
- [ ] MCP plugin が `plugins/mcp/shared|claude|codex|kiro` に分離されている
- [ ] Claude / Codex / Kiro の各導入手順で `mcp-bigquery@ai-plugins` が同じ名前で使える
- [ ] README / docs が新構成を案内している
- [ ] 各ランタイムの検証手順が通っている
- [ ] 各ランタイムの実インストール smoke test がコンテナ内で通っている
