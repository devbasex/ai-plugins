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

開発環境または信頼済み CI に secret が存在する場合は、許可リストに載せた環境変数と認証ファイルだけをコンテナへ注入して認証付きテストを実行する。ホストの credential directory 全体や SSH agent は mount しない。ファイル secret は `--tmpfs /tmp/runtime-secrets:rw,noexec,nosuid,nodev,size=1m` を付けた `--rm` コンテナへ host wrapper から `docker cp` で注入し、終了時に tmpfs とコンテナを破棄する。DooD 環境では host bind mount が期待通り動かない場合があるため、bind mount は必須方式にしない。

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

`--with-secrets` は次の 3 モードにする。

| mode | 用途 | secret 不足時 |
|---|---|---|
| `off` | PR CI / 非認証 smoke | 認証付き項目を実行しない |
| `auto` | ローカル任意実行 | 認証付き項目を skip として JUnit に記録 |
| `required` | protected CI / release 前確認 | 失敗扱い |

secret が存在するのに認証付き項目が失敗した場合は、`auto` / `required` のどちらでも失敗扱いにする。

ブラウザ認証が必要な runtime は、非対話コンテナでは認証完了できないため、認証 URL / device code / login prompt の表示または認証待ち状態まで到達すれば合格とする。ただし、この fallback を使えるのは、`--with-secrets=off`、またはその runtime に非ブラウザ認証手段がない場合だけとする。`--with-secrets=auto|required` で API key などの secret が存在するのに runtime が認証できず browser prompt へ落ちた場合は失敗扱いにする。ブラウザ認証済み profile をホストからコピーすることは原則禁止する。

ブラウザ認証準備の smoke はハングさせない。adapter は `timeout 30s <login command>` のように上限時間付きで実行し、stdout / stderr に認証 URL、device code、login prompt のいずれかが出たことを assertion してからプロセスを終了する。`timeout` の終了コード自体は、期待する認証待ち出力が得られていれば成功扱いにしてよい。

### 3. secret 注入のルール

secret は `scripts/runtime-smoke-test.sh --with-secrets=auto|required` で検出し、許可リストに合致するものだけを container build context とは別に注入する。Docker image layer に secret を焼き込まない。

許可する raw 環境変数:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_SESSION_TOKEN`
- MCP plugin ごとの sandbox env。例: `BIGQUERY_PROJECT`, `BIGQUERY_LOCATION`, `BIGQUERY_DATASET`, `REDASH_URL`, `REDASH_API_KEY`

許可するファイルパス環境変数:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `BIGQUERY_KEY_FILE`

ファイルパス環境変数は host 側のコピー元 path としてだけ読む。container へ raw host path を渡してはいけない。host wrapper が `docker cp` で `/tmp/runtime-secrets/<key>` へ注入した後、container 内 env を `/tmp/runtime-secrets/<key>` へ再設定する。

raw 環境変数 secret も `docker run -e` / `docker exec -e` / command string では渡さない。`docker inspect` や host 側 process argv に secret 値が残るのを避けるため、secret なしで起動した container に対し、`docker exec -i` の stdin で tmpfs 上の `/tmp/runtime-secrets/raw-env` へ shell-escaped な env file として注入する。adapter 実行時は同じ container 内でこの env file を source し、実行後は wrapper が container を破棄する。

MCP plugin の env 名は、各 runtime plugin の実 `.mcp.json` / manifest / README から生成または検証する。例として `mcp-bigquery` は現行 `.mcp.json` が `BIGQUERY_PROJECT`, `BIGQUERY_LOCATION`, `BIGQUERY_DATASET`, `BIGQUERY_KEY_FILE` を参照するため、smoke test でもこの名前を使う。ただし `BIGQUERY_KEY_FILE` はファイルパス環境変数として扱い、container 内 path へ再設定する。README と `.mcp.json` の env 名が食い違う場合は、テスト側で alias せず、plugin 側の docs / manifest mismatch として失敗させる。

許可するファイル:

- `$GOOGLE_APPLICATION_CREDENTIALS` が指す service account / ADC file
- `$BIGQUERY_KEY_FILE` が指す service account key file
- 明示指定された `--secret-file key=/path/to/file`
- `tests/runtime-smoke/secrets-files.allowlist` に載せた path

禁止事項:

- host の `$HOME` 全体 mount
- `~/.ssh`, `~/.aws`, `~/.config`, `~/.claude`, `~/.codex`, `~/.kiro` の directory mount
- secret 値の log / JUnit / artifact 出力
- secret を Dockerfile の `ARG` / `ENV` / image layer に残す
- `--secret-file` の `key` に path separator、空文字、未許可文字、allowlist 外の名前を許すこと
- `--with-secrets=auto|required` と `--keep-container` の併用

`--secret-file` の `key` は `^[A-Za-z0-9_.-]+$` に一致し、かつ `tests/runtime-smoke/secrets-files.allowlist` に載る固定キーだけ許可する。`key` はコピー先 basename としてのみ扱い、`/` や `..` を含む値は拒否する。ファイル permission は container 内で `0444` にし、artifact 収集対象から `/tmp/runtime-secrets/**` を明示除外する。

ファイル secret は container 内の tmpfs `/tmp/runtime-secrets/` に置き、test 終了時に削除する。`docker run --rm --tmpfs /tmp/runtime-secrets:rw,noexec,nosuid,nodev,size=1m` を必須にし、異常終了時も wrapper が `docker rm -f` を実行する。`--keep-container` は secret 注入モードでは即エラーにする。ファイル secret は container 内のコピー先へ環境変数を必ず再設定する。

例:

```bash
# host
GOOGLE_APPLICATION_CREDENTIALS=/Users/me/.config/gcloud/application_default_credentials.json

# host wrapper
cid="$(docker run -d --rm \
  --tmpfs /tmp/runtime-secrets:rw,noexec,nosuid,nodev,size=1m \
  runtime-smoke:claude sleep infinity)"
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
docker cp "$GOOGLE_APPLICATION_CREDENTIALS" "$cid:/tmp/runtime-secrets/google-credentials.json"
docker exec "$cid" chmod 0444 /tmp/runtime-secrets/google-credentials.json

{
  printf 'export ANTHROPIC_API_KEY=%q\n' "$ANTHROPIC_API_KEY"
} | docker exec -i "$cid" sh -c 'cat > /tmp/runtime-secrets/raw-env && chmod 0444 /tmp/runtime-secrets/raw-env'

docker exec \
  -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/runtime-secrets/google-credentials.json \
  "$cid" bash -lc 'source /tmp/runtime-secrets/raw-env && exec /workspace/ai-plugins/tests/runtime-smoke/adapters/claude.sh'
```

AWS は profile 名だけでは認証できないため、基本は `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` の環境変数方式を使う。profile を使う必要がある場合は、`--secret-file aws-credentials=/path/to/credentials` と `--secret-file aws-config=/path/to/config` で単一ファイルとして明示注入し、container 内で `AWS_SHARED_CREDENTIALS_FILE=/tmp/runtime-secrets/aws-credentials` と `AWS_CONFIG_FILE=/tmp/runtime-secrets/aws-config` を再設定する。`~/.aws` directory mount は引き続き禁止する。

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

# protected CI / release 前確認では secret 必須
bash scripts/runtime-smoke-test.sh --runtime claude --with-secrets=required

# debug
bash scripts/runtime-smoke-test.sh --runtime codex --keep-container
```

`--keep-container` はローカル debug 専用で、CI では禁止する。`--with-secrets=auto|required` と同時指定された場合は、secret や認証済み runtime cache の残存を避けるため実行前に失敗させる。

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

CLI install、npm package 解決、認証付き smoke のため、container build / smoke では network を許可する。ただし host の credential directory や SSH agent は mount せず、許可リストに載せた secret だけを `--rm --tmpfs /tmp/runtime-secrets:rw,noexec,nosuid,nodev,size=1m` コンテナへ注入する。

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
- Claude がブラウザ認証だけを要求する場合、login prompt / 認証 URL 表示まで到達することを確認する。ただし `--with-secrets=auto|required` で `ANTHROPIC_API_KEY` などの非ブラウザ認証 secret が存在する場合は、browser prompt へ落ちた時点で失敗扱いにする

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
- Codex がブラウザ認証だけを要求する場合、login prompt / 認証 URL 表示まで到達することを確認する。ただし `--with-secrets=auto|required` で `OPENAI_API_KEY` などの非ブラウザ認証 secret が存在する場合は、browser prompt へ落ちた時点で失敗扱いにする

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
- Kiro がブラウザ認証だけを要求する場合、login prompt / 認証 URL 表示まで到達することを確認する。ただし `--with-secrets=auto|required` で非ブラウザ認証 secret が存在する場合は、browser prompt へ落ちた時点で失敗扱いにする

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
| RST-014 | all | secret injection | 条件付き必須 | 開発環境または信頼済み CI に secret がある場合、許可リストに従って `--rm --tmpfs /tmp/runtime-secrets` container へ注入し、container 内 path へ env を再設定する |
| RST-015 | all | authenticated skill run | 条件付き必須 | 認証情報がある場合は代表 Skill を実際に呼ぶ |
| RST-016 | all | real MCP handshake | 条件付き必須 | sandbox credential がある MCP は handshake / sandbox query まで確認する |
| RST-017 | all | browser auth fallback | ○ | `--with-secrets=off`、または非ブラウザ認証手段がない場合のみ login prompt / 認証 URL 表示まで到達する。`auto|required` で secret がある場合の browser prompt fallback は失敗扱い |

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
- default branch への merge 後 workflow。secret が期待される場合は `--with-secrets=required`
- maintainer が明示実行する local command

secret が未設定の場合、`--with-secrets=auto` では非認証 smoke のみを実行し、認証付き項目は skip として JUnit に記録する。`--with-secrets=required` では secret 不足を失敗扱いにする。

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
  - ブラウザ認証 runtime は、非ブラウザ認証手段がない場合だけ認証準備完了まで

## PR 分割計画

release branch: `release/runtime-plugin-container-smoke`
base branch: `release/runtime-plugin-split` (親計画の PR7 merge 後。親計画が main へ merge 済みなら `main`)

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `feature/runtime-smoke-harness` | `scripts/runtime-smoke-test.sh`、共通 Containerfile、artifact/JUnit 出力、runtime 選択 CLI を追加する | 親計画 PR7 | ○ |
| 2 | `feature/runtime-smoke-adapters` | Claude / Codex / Kiro adapter を追加し、CLI install、marketplace add、plugin install、version 取得を runtime ごとに閉じ込める | PR1 | ○ |
| 3 | `feature/runtime-smoke-assertions` | plugin cache、Skill、MCP config、hook fixture、Kiro agent config、host contamination の共通 assertion を追加する | PR1 | ○ |
| 4 | `feature/runtime-smoke-secrets` | `--with-secrets=off|auto|required`、allowlist、tmpfs secret 注入、redaction、`--keep-container` 併用拒否を実装する | PR1 | ○ |
| 5 | `feature/runtime-smoke-ci` | 非認証 PR workflow と protected authenticated workflow を追加し、artifact / JUnit を収集する | PR2, PR3, PR4 | × |
| 6 | `feature/runtime-smoke-docs` | README とテスト運用ドキュメントを更新し、local / CI / secret 付き実行手順を整理する | PR5 | × |

単一 PR では Docker 実行基盤、runtime adapter、secret 注入、CI 権限設計が混在し、レビュー観点が分散する。特に secret 注入はセキュリティレビューを独立させる必要があるため、release branch + 個別 PR 方式を採用する。

実行開始条件:

- 親計画 `issues/agent-runtime-plugin-split.md` の PR7 (`feature/runtime-split-validation`) までが `release/runtime-plugin-split` に merge 済みであること
- `plugins/ndf-claude`, `plugins/ndf-codex`, `plugins/ndf-kiro`, `plugins/mcp/claude|codex|kiro/*` の runtime 別配布物が存在し、smoke test の install 対象として使えること
- 上記が満たされるまでは `release/runtime-plugin-container-smoke` と個別 PR branch を作成しない

## タスク分解

### Task 1: smoke test 共通 harness を作る

- **対象ファイル:** `scripts/runtime-smoke-test.sh`, `tests/runtime-smoke/README.md`, `tests/runtime-smoke/Containerfile.base`, `tests/runtime-smoke/fixtures/**`
- **変更内容:**
  - `--runtime claude|codex|kiro|all`、`--with-secrets=off|auto|required`、`--keep-container`、artifact 出力先を受け付ける入口を作る
  - repo を container 内 `/workspace/ai-plugins`、HOME を `/tmp/runtime-home`、project を `/tmp/runtime-project` に分離する
  - JUnit XML、install log、version log、generated config tree を artifact 化する

### Task 2: runtime adapter を追加する

- **対象ファイル:** `tests/runtime-smoke/Containerfile.claude`, `tests/runtime-smoke/Containerfile.codex`, `tests/runtime-smoke/Containerfile.kiro`, `tests/runtime-smoke/adapters/*.sh`
- **変更内容:**
  - Claude / Codex / Kiro の CLI install と `--version` 取得を adapter に閉じ込める
  - local marketplace の追加、`ndf@ai-plugins`、`mcp-bigquery@ai-plugins` 相当の install を実行する
  - ブラウザ認証 runtime は timeout 付きで login prompt / 認証 URL / device code の表示を assertion する

### Task 3: 共通 assertion を追加する

- **対象ファイル:** `tests/runtime-smoke/assertions/*.sh`, `tests/runtime-smoke/fixtures/hook-*.json`, `tests/runtime-smoke/fixtures/mcp-env.example`
- **変更内容:**
  - install 後の Skill / MCP / hook / agents または Kiro agent config の存在を確認する
  - `.mcp.json` や runtime config に secret 実値が出ていないことを確認する
  - hook script を fixture payload で実行し、exit code 0 を確認する
  - host HOME、credential directory、repo root への runtime config 汚染がないことを確認する

### Task 4: secret 注入と redaction を実装する

- **対象ファイル:** `tests/runtime-smoke/secrets/**`, `tests/runtime-smoke/secrets-files.allowlist`, `scripts/runtime-smoke-test.sh`
- **変更内容:**
  - raw 環境変数と file secret を allowlist で検出する
  - file secret は `docker cp` で tmpfs `/tmp/runtime-secrets/` へ注入し、container 内 path へ env を再設定する
  - raw secret は `docker exec -i` の stdin で `/tmp/runtime-secrets/raw-env` へ書き込み、adapter 実行時に source する
  - `--with-secrets=auto|required` と `--keep-container` の併用を拒否する
  - log / JUnit / artifact から secret 値と `/tmp/runtime-secrets/**` を除外する

### Task 5: CI workflow を追加する

- **対象ファイル:** `.github/workflows/runtime-plugin-smoke.yml`, `.github/workflows/runtime-plugin-authenticated-smoke.yml`
- **変更内容:**
  - `pull_request` では secret なしの非認証 smoke のみを実行する
  - `workflow_dispatch` + protected environment で認証付き smoke を実行できるようにする
  - default branch merge 後の release 前確認では、必要に応じて `--with-secrets=required` を使える構成にする

### Task 6: ドキュメントと運用手順を更新する

- **対象ファイル:** `README.md`, `docs/ndf-plugin-reference.md`, `issues/agent-runtime-plugin-split.md`, `tests/runtime-smoke/README.md`
- **変更内容:**
  - local 実行、CI 実行、secret 付き実行、debug 実行の手順を整理する
  - secret を渡せない PR CI と protected authenticated smoke の責務を明記する
  - 親計画の Task 8 完了条件から本 plan の smoke test 完了条件へリンクする

## 完了の定義

- [ ] `scripts/runtime-smoke-test.sh` で runtime を選択できる
- [ ] Claude / Codex / Kiro の container image が分かれている
- [ ] smoke は host HOME と credential directory を mount しない
- [ ] secret が存在する場合は許可リストに従って `--rm --tmpfs /tmp/runtime-secrets` container へ注入され、container 内 path へ env が再設定される
- [ ] `--with-secrets=auto|required` と `--keep-container` の併用が拒否される
- [ ] `ndf@ai-plugins` の実 install を確認する
- [ ] `mcp-bigquery@ai-plugins` 相当の実 install を全 runtime で確認する
- [ ] Skill / MCP / hook / agents または Kiro agent config の assertion がある
- [ ] hook script は fixture payload で非認証実行できる
- [ ] secret が存在する場合は認証付き Skill / MCP smoke が実行される
- [ ] ブラウザ認証しかない runtime、または `--with-secrets=off` の非認証 smoke だけ、認証準備完了までを合格条件にする
- [ ] CI で smoke test log と JUnit を artifact 化する
- [ ] secret 値が log / JUnit / artifact に出力されない
