# Runtime plugin container smoke test 設計

## 関連リンク

- 親計画: `issues/agent-runtime-plugin-split.md`
- 対象 runtime: Claude Code / Codex / Kiro
- 対象 plugin: `ndf`, `mcp-bigquery`, `mcp-serena`, 主要 `mcp-*`

## 概要

ランタイム分離後の plugin は、静的な manifest 検証だけでは不十分である。Claude / Codex / Kiro の実 CLI を軽量コンテナへインストールし、ホスト環境を汚染せずに以下を smoke test する。

- marketplace を追加できる
- `ndf@ai-plugins` を install できる
- `mcp-bigquery@ai-plugins` などの MCP plugin を同じ名前で install できる
- install 後に Skill / MCP / hook / agents または Kiro agent config が runtime の期待する場所へ配置される
- hook script は fixture payload で実行できる
- MCP config は runtime の設定として認識でき、必要な環境変数名が secret を出さずに確認できる

## 方針

### 1. ホスト環境を汚染しない

各 smoke test は Docker コンテナ内で実行する。ホストの `$HOME`、`~/.claude`、`~/.codex`、`~/.kiro`、既存 plugin cache は mount しない。

```text
host repo
└── docker run
    ├── /workspace/ai-plugins          # repo copy。必要なら read-only mount + container 内 copy
    ├── /tmp/runtime-home              # HOME
    ├── /tmp/runtime-cache             # CLI cache
    └── /tmp/runtime-project           # install 対象の空 project
```

コンテナは毎回破棄する。テスト後にホストへ残すものは JUnit / log / artifact だけにする。

開発環境または信頼済み CI に secret が存在する場合は、許可リストに載せた環境変数と認証ファイルだけをコンテナ内の一時ディレクトリへコピーして認証付きテストを実行する。ホストの credential directory 全体や SSH agent は mount しない。

### 2. secret があれば認証付き smoke まで実行する

必須 smoke は secret がない環境でも通る非認証検証を含む。加えて、開発環境または信頼済み CI に必要な secret が存在する場合は、同じコンテナ内で認証付き smoke まで実行する。

- CLI install と `--version`
- marketplace add / list
- plugin install / list
- install cache 内の `SKILL.md`, hook config, agent config, `.mcp.json` または runtime 相当 config の存在確認
- hook script の fixture payload 実行
- MCP server command の解決確認、または `--help` / dry-run 相当
- secret が存在する場合の実 Skill invocation
- secret が存在する場合の MCP handshake / sandbox query

secret が不足している場合は、認証付き項目を skip として記録し、非認証 smoke の結果だけで合否を判定する。secret が存在するのに認証付き項目が失敗した場合は失敗扱いにする。

ブラウザ認証が必要な runtime は、非対話コンテナでは認証完了できないため、認証 URL / device code / login prompt の表示または認証待ち状態まで到達すれば合格とする。ただし、この fallback を使えるのは、その runtime に非ブラウザ認証手段がない場合、または該当 secret が未提供の場合だけとする。API key などの secret が存在するのに runtime が認証できず browser prompt へ落ちた場合は失敗扱いにする。ブラウザ認証済み profile をホストからコピーすることは原則禁止する。

### 3. secret コピーのルール

secret は `scripts/runtime-smoke-test.sh --with-secrets=auto` で検出し、許可リストに合致するものだけを container build context とは別の一時ディレクトリへコピーする。Docker image layer に secret を焼き込まない。

許可する環境変数:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_SESSION_TOKEN`
- MCP plugin ごとの sandbox env。例: `BIGQUERY_PROJECT_ID`, `REDASH_URL`, `REDASH_API_KEY`

許可するファイル:

- `$GOOGLE_APPLICATION_CREDENTIALS` が指す service account / ADC file
- 明示指定された `--secret-file name=/path/to/file`
- `tests/runtime-smoke/secrets-files.allowlist` に載せた path

禁止事項:

- host の `$HOME` 全体 mount
- `~/.ssh`, `~/.aws`, `~/.config`, `~/.claude`, `~/.codex`, `~/.kiro` の directory mount
- secret 値の log / JUnit / artifact 出力
- secret を Dockerfile の `ARG` / `ENV` / image layer に残す

コピーされた secret は container 内で `/tmp/runtime-secrets/` に置き、test 終了時に削除する。ファイル secret は container 内のコピー先へ環境変数を必ず再設定する。

例:

```bash
# host
GOOGLE_APPLICATION_CREDENTIALS=/Users/me/.config/gcloud/application_default_credentials.json

# container
cp "$GOOGLE_APPLICATION_CREDENTIALS" /tmp/runtime-secrets/google-credentials.json
export GOOGLE_APPLICATION_CREDENTIALS=/tmp/runtime-secrets/google-credentials.json
```

AWS は profile 名だけでは認証できないため、基本は `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` の環境変数方式を使う。profile を使う必要がある場合は、`--secret-file aws-credentials=/path/to/credentials` と `--secret-file aws-config=/path/to/config` で単一ファイルとして明示コピーし、container 内で `AWS_SHARED_CREDENTIALS_FILE=/tmp/runtime-secrets/aws-credentials` と `AWS_CONFIG_FILE=/tmp/runtime-secrets/aws-config` を再設定する。`~/.aws` directory mount は引き続き禁止する。

### 4. runtime adapter で CLI 差分を閉じ込める

CLI コマンド名、設定ディレクトリ、plugin cache path は runtime ごとに変わる。テスト本体は共通 assertion を持ち、runtime 固有操作は adapter script に分離する。

```text
tests/runtime-smoke/
├── README.md
├── Containerfile.base
├── Containerfile.claude
├── Containerfile.codex
├── Containerfile.kiro
├── fixtures/
│   ├── hook-stop.json
│   ├── hook-session-start.json
│   └── mcp-env.example
├── secrets/
│   ├── collect-secrets.sh
│   └── redact-logs.sh
├── adapters/
│   ├── claude.sh
│   ├── codex.sh
│   └── kiro.sh
└── assertions/
    ├── assert-plugin-files.sh
    ├── assert-hook-fixtures.sh
    ├── assert-mcp-config.sh
    └── assert-no-host-contamination.sh
```

## 実行コマンド

開発者向け入口は 1 つにする。

```bash
# 全 runtime
bash scripts/runtime-smoke-test.sh

# runtime 単位
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
bash scripts/runtime-smoke-test.sh --runtime kiro

# secret があれば認証付き項目も実行
bash scripts/runtime-smoke-test.sh --runtime claude --with-secrets=auto
bash scripts/runtime-smoke-test.sh --runtime codex --with-secrets=auto
bash scripts/runtime-smoke-test.sh --runtime kiro --with-secrets=auto

# debug
bash scripts/runtime-smoke-test.sh --runtime codex --keep-container
```

`--keep-container` はローカル debug 専用で、CI では禁止する。

## コンテナ設計

### base image

`node:22-bookworm-slim` を基本にする。Claude Code は npm install 経由で導入でき、Codex / Kiro の installer 実行にも `curl`, `git`, `bash`, `jq`, `ca-certificates` が必要になるため、これらを共通依存にする。

```Dockerfile
FROM node:22-bookworm-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends bash ca-certificates curl git jq \
  && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp/runtime-home
WORKDIR /workspace/ai-plugins
```

Codex は standalone installer を主経路にする。npm install は fallback として adapter に閉じ込め、通常 CI の期待値にはしない。

Kiro は公式 installer を使う。CLI がコンテナで非対話 install できない場合でも、Kiro installer fallback と生成物検証は必須にする。

### ネットワーク

CLI install、npm package 解決、認証付き smoke のため、container build / smoke では network を許可する。ただし host の credential directory や SSH agent は mount せず、許可リストに載せた secret だけを一時コピーする。

### version pinning

初期実装では最新安定版を使い、各 smoke の冒頭で `claude --version`, `codex --version`, `kiro-cli version` をログへ出す。破壊的変更が多い場合は `tests/runtime-smoke/versions.env` で pin できるようにする。

## runtime 別 smoke

### Claude Code

目的: Claude marketplace 経由で `ndf` と MCP plugin が install でき、Claude 固有の Skill / hook / agents が入ることを確認する。

手順:

```bash
npm install -g @anthropic-ai/claude-code
claude --version
claude plugin validate /workspace/ai-plugins/plugins/ndf-claude
claude plugin validate /workspace/ai-plugins/.claude-plugin/marketplace.json
```

install smoke:

- local marketplace として `/workspace/ai-plugins` を追加する
- `ndf@ai-plugins` を install する
- `mcp-bigquery@ai-plugins` を install する
- plugin list または cache directory で install 済み plugin を確認する

assertion:

- `ndf` cache に `.claude-plugin/plugin.json` がある
- `skills/*/SKILL.md` がある
- `agents/*.md` がある
- `hooks/hooks.json` がある
- MCP plugin cache に `.mcp.json` がある
- `.mcp.json` に secret 実値がなく、`${...}` 形式の env placeholder だけがある
- hook script を fixture payload で実行して exit code 0 を確認する
- `ANTHROPIC_API_KEY` または利用可能な Claude 認証情報がある場合、代表 Skill を 1 つ実行する
- Claude がブラウザ認証だけを要求する場合、login prompt / 認証 URL 表示まで到達することを確認する。ただし `ANTHROPIC_API_KEY` などの非ブラウザ認証 secret が存在する場合は、browser prompt へ落ちた時点で失敗扱いにする

### Codex

目的: Codex marketplace 経由で `ndf` と MCP plugin が install でき、Codex 固有の Skill / hook が入ることを確認する。

手順:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh
codex --version
codex plugin marketplace add /workspace/ai-plugins
codex plugin add ndf@ai-plugins
codex plugin add mcp-bigquery@ai-plugins
```

assertion:

- `ndf` cache に `.codex-plugin/plugin.json` がある
- `skills/*/SKILL.md` がある
- `hooks/hooks.json` がある
- Codex plugin list に `ndf` と `mcp-bigquery` が出る
- MCP plugin の config が Codex の plugin manifest から参照できる
- hook script を Codex fixture payload で実行して exit code 0 を確認する
- `OPENAI_API_KEY` または Codex が利用可能な認証情報がある場合、代表 Skill を 1 つ実行する
- Codex がブラウザ認証だけを要求する場合、login prompt / 認証 URL 表示まで到達することを確認する。ただし `OPENAI_API_KEY` などの非ブラウザ認証 secret が存在する場合は、browser prompt へ落ちた時点で失敗扱いにする

注意:

Codex plugin / marketplace コマンドは experimental のため、adapter で CLI 出力と cache path の差分を吸収する。CLI に install smoke 用の dry-run / list が追加された場合は adapter のみ更新する。

### Kiro

目的: Kiro 版 `ndf` installer と MCP plugin installer または marketplace install が、空 project に Kiro 用 config を生成できることを確認する。

手順:

```bash
curl -fsSL https://cli.kiro.dev/install | bash
kiro-cli version
bash /workspace/ai-plugins/plugins/ndf-kiro/install.sh --project /tmp/runtime-project
bash /workspace/ai-plugins/plugins/mcp/kiro/mcp-bigquery/install.sh --project /tmp/runtime-project
```

Kiro が plugin marketplace install を提供する場合は、installer fallback に加えて以下も smoke 対象にする。

```bash
kiro-cli plugin marketplace add /workspace/ai-plugins
kiro-cli plugin install mcp-bigquery@ai-plugins
```

assertion:

- `/tmp/runtime-project/.kiro/agents/default.json` が生成される
- `/tmp/runtime-project/.kiro/skills/*/SKILL.md` が生成される
- Kiro prompts が期待パスへ生成される
- Kiro MCP config に `mcp-bigquery` が追加される
- 生成された config に secret 実値がない
- installer を 2 回実行しても重複定義を作らない
- Kiro が利用可能な認証情報または sandbox MCP credential がある場合、代表 Skill / MCP handshake を実行する
- Kiro がブラウザ認証だけを要求する場合、login prompt / 認証 URL 表示まで到達することを確認する。ただし非ブラウザ認証 secret が存在する場合は、browser prompt へ落ちた時点で失敗扱いにする

## テストマトリクス

| ID | runtime | 対象 | 必須 | 内容 |
|---|---|---|---|---|
| RST-001 | all | isolation | ○ | HOME / cache / project が `/tmp/runtime-*` 配下で、host HOME を参照しない |
| RST-002 | all | CLI | ○ | runtime CLI を install し `--version` を取得する |
| RST-003 | all | marketplace | ○ | local marketplace を追加できる |
| RST-004 | all | NDF install | ○ | `ndf@ai-plugins` または Kiro installer で NDF を導入できる |
| RST-005 | all | MCP install | ○ | `mcp-bigquery@ai-plugins` 相当を導入できる |
| RST-006 | all | Skill | ○ | install 後に主要 `SKILL.md` が runtime 側に存在する |
| RST-007 | Claude | agents | ○ | `agents/*.md` が install cache に存在する |
| RST-008 | Kiro | agents | ○ | `.kiro/agents/default.json` が生成される |
| RST-009 | Claude/Codex | hooks | ○ | hook config が存在し、fixture payload で script が成功する |
| RST-010 | Kiro | hooks | ○ | Kiro agentSpawn hook 相当の設定が生成される |
| RST-011 | all | MCP config | ○ | MCP config が存在し、env placeholder が secret 実値になっていない |
| RST-012 | all | idempotency | ○ | install を 2 回実行しても重複登録しない |
| RST-013 | all | no contamination | ○ | host HOME / repo root に runtime config を作らない |
| RST-014 | all | secret copy | 条件付き必須 | 開発環境または信頼済み CI に secret がある場合、許可リストに従って container へ一時コピーし、container 内 path へ env を再設定する |
| RST-015 | all | authenticated skill run | 条件付き必須 | 認証情報がある場合は代表 Skill を実際に呼ぶ |
| RST-016 | all | real MCP handshake | 条件付き必須 | sandbox credential がある MCP は handshake / sandbox query まで確認する |
| RST-017 | all | browser auth fallback | ○ | ブラウザ認証しかない、または secret が未提供の場合は login prompt / 認証 URL 表示まで到達する。secret がある場合の browser prompt fallback は失敗扱い |

## CI 設計

### 必須 workflow

`.github/workflows/runtime-plugin-smoke.yml`

- trigger: pull_request
- target: runtime 分離関連ファイルが変わった場合
- job:
  - `runtime-smoke-claude`
  - `runtime-smoke-codex`
  - `runtime-smoke-kiro`
- artifact:
  - CLI version
  - install log
  - plugin list
  - generated config tree
  - JUnit XML

`pull_request` の必須 workflow では secret を渡さず、非認証 smoke のみを実行する。PR の変更コード上で secret を扱うと漏洩リスクが高いため、repository / environment secrets が設定されていても `pull_request` では `--with-secrets=auto` を無効にする。

認証付き smoke は次の信頼済み context だけで実行する。

- `workflow_dispatch` + protected environment
- default branch への merge 後 workflow
- maintainer が明示実行する local command

secret が未設定の場合は非認証 smoke のみを実行し、認証付き項目は skip として JUnit に記録する。

### 任意 workflow

`.github/workflows/runtime-plugin-authenticated-smoke.yml`

- trigger: workflow_dispatch
- protected environment: `runtime-smoke`
- secrets:
  - `ANTHROPIC_API_KEY` または Claude Code が要求する認証方式
  - `OPENAI_API_KEY`
  - MCP sandbox credential
- 内容:
  - 実際の Skill invocation
  - MCP handshake
  - Slack / external notification は dry-run のみ
  - ブラウザ認証 runtime は認証準備完了まで

## 完了の定義

- [ ] `scripts/runtime-smoke-test.sh` で runtime を選択できる
- [ ] Claude / Codex / Kiro の container image が分かれている
- [ ] smoke は host HOME と credential directory を mount しない
- [ ] secret が存在する場合は許可リストに従って container へ一時コピーされ、container 内 path へ env が再設定される
- [ ] `ndf@ai-plugins` の実 install を確認する
- [ ] `mcp-bigquery@ai-plugins` 相当の実 install を全 runtime で確認する
- [ ] Skill / MCP / hook / agents または Kiro agent config の assertion がある
- [ ] hook script は fixture payload で非認証実行できる
- [ ] secret が存在する場合は認証付き Skill / MCP smoke が実行される
- [ ] ブラウザ認証しかない runtime、または secret 未提供時だけ、認証準備完了までを合格条件にする
- [ ] CI で smoke test log と JUnit を artifact 化する
- [ ] secret 値が log / JUnit / artifact に出力されない
