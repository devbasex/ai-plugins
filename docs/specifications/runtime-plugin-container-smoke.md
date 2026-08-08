# Runtime Plugin Container Smoke Test 仕様

## 概要

runtime 分離後の Claude Code / Codex / Kiro 向け plugin 配布物を、ホスト環境を汚染しない Docker コンテナ内で smoke test する仕様。

入口は `scripts/runtime-smoke-test.sh` である。runtime ごとに専用 container image を build し、コンテナ内の空 project に `ndf` と代表 MCP plugin を導入して、Skill / MCP / hook / agent config の配置と非認証・認証付き smoke を検証する。

## 対象範囲

対象 runtime:

| runtime | 配布物 | adapter |
|---|---|---|
| Claude Code | `plugins/ndf-claude`, `plugins/mcp/claude/*` | `tests/runtime-smoke/adapters/claude.sh` |
| Codex | `plugins/ndf-codex`, `plugins/mcp/codex/*` | `tests/runtime-smoke/adapters/codex.sh` |
| Kiro CLI | `plugins/ndf-kiro`, `plugins/mcp/kiro/*` | `tests/runtime-smoke/adapters/kiro.sh` |

代表 MCP plugin として `mcp-bigquery` を install smoke の対象にする。主要 MCP plugin の manifest / installer 検証は `scripts/validate-runtime-plugins.sh` と PR Test Plan で補完する。

## 実行仕様

開発者向け入口は以下のコマンドである。

```bash
bash scripts/runtime-smoke-test.sh
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
bash scripts/runtime-smoke-test.sh --runtime kiro
```

`--runtime` は `claude`、`codex`、`kiro`、`all` を受け付ける。省略時は `all` で全 runtime を順に実行する。

`scripts/runtime-smoke-test.sh` は Docker を必須とし、以下の image を build する。

| image | Dockerfile |
|---|---|
| `ai-plugins-runtime-smoke-base` | `tests/runtime-smoke/Containerfile.base` |
| `ai-plugins-runtime-smoke-claude` | `tests/runtime-smoke/Containerfile.claude` |
| `ai-plugins-runtime-smoke-codex` | `tests/runtime-smoke/Containerfile.codex` |
| `ai-plugins-runtime-smoke-kiro` | `tests/runtime-smoke/Containerfile.kiro` |

コンテナ内では以下のパスを使用する。

| パス | 用途 |
|---|---|
| `/workspace/ai-plugins` | `.git`、runtime config、`tmp` を除外してコピーした repository |
| `/tmp/runtime-home` | runtime CLI 用の隔離 HOME |
| `/tmp/runtime-project` | plugin install 対象の空 project |
| `/tmp/runtime-artifacts` | コンテナ内 artifact 出力先 |
| `/tmp/runtime-secrets` | secret mode 用 tmpfs |

repository コピー後、`/workspace/ai-plugins` は `chmod -R a-w` で読み取り専用にする。

## Runtime Adapter

adapter は runtime 固有の CLI 差分を閉じ込める。

### Claude Code

`tests/runtime-smoke/adapters/claude.sh` は以下を実行する。

- `claude --version`
- `claude plugin validate` による `plugins/ndf-claude` と `.claude-plugin/marketplace.json` の検証
- local marketplace 追加
- `ndf@ai-plugins` と `mcp-bigquery@ai-plugins` の install
- plugin list 取得
- plugin files、MCP config、hook fixture、認証付き smoke、host contamination の assertion

### Codex

`tests/runtime-smoke/adapters/codex.sh` は以下を実行する。

- `codex --version`
- local marketplace 追加
- `ndf@ai-plugins` と `mcp-bigquery@ai-plugins` の install
- plugin list 取得
- plugin files、MCP config、hook fixture、認証付き smoke、host contamination の assertion

### Kiro CLI

`tests/runtime-smoke/adapters/kiro.sh` は Kiro CLI が利用可能な場合は `kiro-cli --help` を version log として記録する。Kiro CLI がコンテナ内で利用できない場合も、installer fallback を使って smoke を継続する。

Kiro adapter は以下を実行する。

- `plugins/ndf-kiro/install.sh --project /tmp/runtime-project --with-slack`
- NDF installer の idempotency 確認
- `plugins/mcp/kiro/mcp-bigquery/install.sh --project /tmp/runtime-project`
- MCP installer の idempotency 確認
- plugin files、MCP config、hook fixture、認証付き smoke、host contamination の assertion

## Assertion

共通 assertion は `tests/runtime-smoke/assertions/` に置く。

| assertion | 内容 |
|---|---|
| `assert-plugin-files.sh` | runtime 側の install 先に plugin manifest、Skill、hook、agent / prompt / MCP runtime link が存在することを確認する |
| `assert-mcp-config.sh` | `mcp-bigquery` config に `BIGQUERY_PROJECT`、`BIGQUERY_LOCATION`、`BIGQUERY_DATASET`、`BIGQUERY_KEY_FILE` の placeholder があり、secret 実値や `/tmp/runtime-secrets` が混入していないことを確認する |
| `assert-hook-fixtures.sh` | fixture payload で Claude / Codex / Kiro の hook script を非認証実行できることを確認する |
| `assert-kiro-agent.sh` | Kiro の `ndf` エージェント定義と steering を検査する。`kiro-cli` が使える場合は `agent list` に `ndf` が現れること、`--set-default` で既定が切り替わることも確認し、確認後に既定を元へ戻す。文脈ファイルの合計文字数が予算内であることも検査する |
| `assert-authenticated-smoke.sh` | `--with-secrets` が有効な場合に、利用可能な runtime / BigQuery secret で認証付き smoke を実行する |
| `assert-no-host-contamination.sh` | `HOME` と project が `/tmp/runtime-*` 配下であり、repo root や host-like credential path が汚染されていないことを確認する |

hook fixture は `tests/runtime-smoke/fixtures/hook-session-start.json` と `tests/runtime-smoke/fixtures/hook-stop.json` を使用する。

## Secret Mode

`--with-secrets` は以下を受け付ける。

| mode | 仕様 |
|---|---|
| `off` | secret を注入せず、認証付き項目を skip する。PR CI の標準 |
| `auto` | allowlist 対象 secret がある場合だけ注入し、runtime で使える secret がなければ skip する |
| `required` | allowlist 対象 secret が存在しない場合、または runtime で使える secret がない場合に失敗する |

`--keep-container` は local debug 専用である。`--with-secrets=auto|required` と同時指定した場合、実行前に失敗する。

raw secret として扱う環境変数:

| 種別 | 環境変数 |
|---|---|
| runtime | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` |
| AWS | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` |
| BigQuery | `BIGQUERY_PROJECT`, `BIGQUERY_LOCATION`, `BIGQUERY_DATASET` |
| Redash | `REDASH_URL`, `REDASH_API_KEY` |

file secret として扱う環境変数:

| 環境変数 | allowlist key |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | `google-application-credentials` |
| `BIGQUERY_KEY_FILE` | `bigquery-key-file` |

`--secret-file KEY=PATH` で追加 file secret を指定できる。`KEY` は `tests/runtime-smoke/secrets-files.allowlist` にある `google-application-credentials`、`bigquery-key-file`、`aws-credentials`、`aws-config` のいずれかであり、`^[A-Za-z0-9_.-]+$` に一致する必要がある。

secret は image layer に焼き込まない。コンテナ起動後、`--tmpfs /tmp/runtime-secrets:rw,noexec,nosuid,nodev,size=1m` 上へ注入する。raw secret は `docker exec -i` の stdin から `/tmp/runtime-secrets/raw-env` に書き込み、adapter 実行時に同じコンテナ内で source する。file secret は tar stream 経由で `/tmp/runtime-secrets/<key>` へ配置し、permission を `0444` にする。

artifact 収集対象は `/tmp/runtime-artifacts` だけである。`/tmp/runtime-secrets` は artifact に含めない。

## Artifact

artifact は既定で `tmp/runtime-smoke/<runtime>/` に出力する。`--artifact-dir PATH` で変更できる。

| artifact | 内容 |
|---|---|
| `smoke.log` | adapter 実行ログ |
| `version.log` | runtime CLI version / help 出力 |
| `generated-tree.txt` | `/tmp/runtime-project` の生成物一覧 |
| `junit.xml` | GitHub Actions artifact 用の最小 JUnit XML |
| `authenticated-smoke.log` | 認証付き smoke の実行または skip 結果 |

`write_junit` は runtime ごとに `tests="1"`、`failures="0"`、`skipped="0"` の JUnit XML を出力する。adapter が失敗した場合は wrapper が非 0 で終了し、GitHub Actions の job failure として扱う。

## CI

非認証 smoke は `.github/workflows/runtime-plugin-smoke.yml` で実行する。

| 項目 | 仕様 |
|---|---|
| trigger | `pull_request`、`main` / `release/**` への `push` |
| 対象変更 | `plugins/**`、`scripts/runtime-smoke-test.sh`、`tests/runtime-smoke/**`、runtime smoke workflow |
| matrix | `claude`, `codex`, `kiro` |
| command | `bash scripts/runtime-smoke-test.sh --runtime "${{ matrix.runtime }}" --with-secrets=off` |
| artifact | `tmp/runtime-smoke/<runtime>` |

認証付き smoke は `.github/workflows/runtime-plugin-authenticated-smoke.yml` で手動実行する。

| 項目 | 仕様 |
|---|---|
| trigger | `workflow_dispatch` |
| environment | `runtime-smoke` |
| runtime input | `all`, `claude`, `codex`, `kiro` |
| secret mode | `--with-secrets=auto` |
| artifact | `tmp/runtime-smoke` |

authenticated workflow は `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、BigQuery 関連 secret を protected environment から受け取る。`BIGQUERY_KEY_FILE_JSON` は workflow 内で一時ファイルに書き出し、`--secret-file bigquery-key-file=...` として wrapper に渡す。

## セキュリティ

- host の `$HOME`、`~/.claude`、`~/.codex`、`~/.kiro`、`~/.ssh`、`~/.aws`、`~/.config` はコンテナへ mount しない。
- Docker image layer に secret を含めない。
- `--with-secrets=auto|required` と `--keep-container` は併用できない。
- secret file key は allowlist と文字種で検証する。
- MCP config assertion は secret 実値と `/tmp/runtime-secrets` の混入を拒否する。
- host contamination assertion は repo root に `.claude`、`.codex`、`.kiro`、`.mcp.json` が生成されていないことを確認する。

## テスト観点

| 観点 | 確認方法 |
|---|---|
| 生成物同期 | `bash scripts/build-runtime-plugins.sh --check` |
| manifest / link 検証 | `bash scripts/validate-runtime-plugins.sh` |
| Claude smoke | `bash scripts/runtime-smoke-test.sh --runtime claude` |
| Codex smoke | `bash scripts/runtime-smoke-test.sh --runtime codex` |
| Kiro smoke | `bash scripts/runtime-smoke-test.sh --runtime kiro` |
| secret mode 拒否 | `bash scripts/runtime-smoke-test.sh --runtime claude --with-secrets=auto --keep-container` が非 0 になること |
| 旧参照残存 | `plugins/ndf` や `plugins/mcp-*` の旧配置参照が残っていないこと |

## 関連リンク

- [Runtime smoke README](../../tests/runtime-smoke/README.md)
- [runtime smoke wrapper](../../scripts/runtime-smoke-test.sh)
- [runtime plugin validation](../../scripts/validate-runtime-plugins.sh)
- [Runtime plugin smoke workflow](../../.github/workflows/runtime-plugin-smoke.yml)
- [Runtime plugin authenticated smoke workflow](../../.github/workflows/runtime-plugin-authenticated-smoke.yml)
- [Runtime Plugin Distribution 仕様](runtime-plugin-distribution.md)
