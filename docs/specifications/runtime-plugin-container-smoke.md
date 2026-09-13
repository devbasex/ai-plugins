# Runtime Plugin Container Smoke Test 仕様

## 概要

runtime 分離後の Claude Code / Codex / Kiro 向け plugin 配布物を、ホスト環境を汚染しない Docker コンテナ内で smoke test する仕様。

入口は `scripts/runtime-smoke-test.sh` である。runtime ごとに専用 container image を build し、コンテナ内の空 project に `ndf` と代表 MCP plugin を導入して、Skill / MCP / hook / agent config の配置と非認証・認証付き smoke を検証する。

## 対象範囲

対象 runtime:

| runtime | 配布物 | adapter |
|---|---|---|
| Claude Code | `plugins/ndf`, `plugins/mcp/*` | `tests/runtime-smoke/adapters/claude.sh` |
| Codex | `plugins/ndf`, `plugins/mcp/*` | `tests/runtime-smoke/adapters/codex.sh` |
| Kiro CLI | `plugins/ndf`, `plugins/mcp/*` | `tests/runtime-smoke/adapters/kiro.sh` |

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
- `claude plugin validate` による `plugins/ndf` と `.claude-plugin/marketplace.json` の検証
- local marketplace 追加
- `ndf@ai-plugins` と `mcp-bigquery@ai-plugins` の install
- plugin list 取得
- plugin files、MCP config、hook fixture、認証付き smoke、host contamination の assertion
- hooks 定義の検査（`assert-hook-definitions.sh claude`）

### Codex

`tests/runtime-smoke/adapters/codex.sh` は以下を実行する。

- `codex --version`
- local marketplace 追加
- `ndf@ai-plugins` と `mcp-bigquery@ai-plugins` の install
- plugin list 取得
- plugin files、MCP config、hook fixture、認証付き smoke、host contamination の assertion
- hooks 定義の検査（`assert-hook-definitions.sh codex`）

### Kiro CLI

`tests/runtime-smoke/adapters/kiro.sh` は Kiro CLI が利用可能な場合は `kiro-cli --help` を version log として記録する。Kiro CLI がコンテナ内で利用できない場合も、installer fallback を使って smoke を継続する。

Kiro adapter は以下を実行する。

- `plugins/ndf/dev.kiro/install.sh --project /tmp/runtime-project --with-slack`
- NDF installer の idempotency 確認
- `plugins/mcp/mcp-bigquery/dev.kiro/install.sh --project /tmp/runtime-project`
- MCP installer の idempotency 確認
- plugin files、MCP config、hook fixture、認証付き smoke、host contamination の assertion

## Assertion

共通 assertion は `tests/runtime-smoke/assertions/` に置く。

| assertion | 内容 |
|---|---|
| `assert-plugin-files.sh` | runtime 側の install 先に plugin manifest、Skill、hook、agent / prompt / MCP runtime link が存在することを確認する |
| `assert-mcp-config.sh` | `mcp-bigquery` config に `BIGQUERY_PROJECT`、`BIGQUERY_LOCATION`、`BIGQUERY_DATASET`、`BIGQUERY_KEY_FILE` の placeholder があり、secret 実値や `/tmp/runtime-secrets` が混入していないことを確認する |
| `assert-hook-fixtures.sh` | fixture payload で Claude / Codex / Kiro の hook script を非認証実行できることを確認する |
| `assert-hook-definitions.sh` | Claude Code / Codex に全プラグインの hooks 定義を読ませ、読み込みの報告（警告・誤り）が 1 件でもあれば落とす（「hooks 定義の検査」） |
| `assert-kiro-agent.sh` | Kiro の `ndf` エージェント定義と steering を検査する。`kiro-cli` が使える場合は `agent list` に `ndf` が現れること、`--set-default` で既定が切り替わることも確認し、確認後に既定を元へ戻す。文脈ファイルの合計文字数が予算内であることも検査する |
| `assert-authenticated-smoke.sh` | `--with-secrets` が有効な場合に、利用可能な runtime / BigQuery secret で認証付き smoke を実行する |
| `assert-no-host-contamination.sh` | `HOME` と project が `/tmp/runtime-*` 配下であり、repo root や host-like credential path が汚染されていないことを確認する |

hook fixture は `tests/runtime-smoke/fixtures/hook-session-start.json` と `tests/runtime-smoke/fixtures/hook-stop.json` を使用する。

## hooks 定義の検査

**hooks 定義をランタイム自身に読ませ、その読み込みの報告で判定する。** 受け取るキーの一覧は
こちらで持たない。`claude plugin validate`・`validate-runtime-plugins.sh` がすべて通った定義が、
利用者の起動時にランタイムの警告として見つかったことが 2 回ある（Codex と Claude Code）。

```text
assert-hook-definitions.sh <claude|codex>
```

| 項目 | 内容 |
|---|---|
| 対象 | `.claude-plugin/marketplace.json` の `plugins[].source` が指すディレクトリのうち、そのランタイムの `plugin.json` を持ち、`plugin.json` に `hooks` があるか `hooks/hooks.json` があるもの。実行時に見つけ、0 件なら落とす |
| 読む環境変数 | `REPO_ROOT` / `ARTIFACT_DIR` / `HOME`（既存の assertion と同じ既定値） |
| 終了コード 0 | 陽性対照に報告が出て、本物の定義に報告が 0 件で、対象がすべて読まれた |
| 終了コード 1 | 上のいずれかを満たさない。理由を標準エラーへ書く |
| 終了コード 2 | 引数が `claude` / `codex` 以外 |
| 書く先 | `$ARTIFACT_DIR/hook-definitions/`（ログと応答）と、読ませるたびに `mktemp -d` で作る設定ディレクトリ |
| 書かない先 | `$HOME/.claude` / `$HOME/.codex` / `$REPO_ROOT` |

ランタイムごとの読ませ方と判定は次のとおりである。

| runtime | 読ませ方 | 報告 | 「読まれた」 |
|---|---|---|---|
| Claude Code | `CLAUDE_CONFIG_DIR=<空> claude --debug-file <log> --plugin-dir <対象>... plugin list` | ログの `[WARN]` / `[ERROR]` の行のうち hooks に触れるもの | `Read hooks.json for plugin <名前>` / `Read manifest hooks for plugin <名前>` |
| Codex | `CODEX_HOME=<空>` へ登録・導入したうえで `lib/codex-hooks-list.py`（`codex app-server` へ `initialize` → `initialized` → `hooks/list`） | `data[].warnings` / `data[].errors` の要素 | `data[].hooks[].pluginId` の `@` より前 |

`plugin list` の終了コードは判定に使わない（読み込みに失敗しても 0 を返す）。0 以外なら起動
そのものの失敗として落とす。`codex-hooks-list.py` は応答が `error` を持つ・`result.data` が無い・
app-server が応答前に終わった・30 秒を超えたときに終了コード 1 を返す。

**Codex の「読まれた」は、hooks が 1 つ以上登録されたことを指す。** 中身が空の定義は報告なしで
一覧から消えるため、これも落ちる。**Codex は現状どの階層の未知キーも受け取るため、Codex の検査が
落とすのは読めない定義（`failed to parse plugin hooks config`）と、hooks が登録されない定義である。**
マッチャーグループの未知キーは Claude Code の検査が落とす。

陽性対照は `tests/runtime-smoke/fixtures/hooks-positive-control/` に置く。Claude Code 向けの
`hooks.json` はマッチャーグループに `description` を、Codex 向けの `codex.json` は読めない `type`
（`cmd`）を持つ。`plugins/` の外に置くのは、`validate-runtime-plugins.sh` の走査と配布物に入れない
ためである。

| 決定 | 理由 |
|---|---|
| ランタイムに読ませて判定し、受け取るキーの一覧を持たない | 受け取るキーはランタイムと版で違う。Claude Code はマッチャーグループと上位の未知キーを報告するが、コマンド階層の未知キーと上位の `description` は報告しない。Codex はどの階層の未知キーも受け取る。1 つの一覧では片方で受け取るキーを落とすか、受け取らないキーを見逃す。継続的統合は最新版を導入するため、判定がその版に揃う |
| Claude Code は `-p` ではなく `plugin list` で読ませる | モデルへの要求をせず、認証の無い環境でも終了コード 0 で 1 秒未満に終わる。`-p` は認証の誤りで 1 になり起動の失敗と見分けられず、API の再試行で数十秒かかる |
| Codex は app-server の `hooks/list` で報告を受け取る | 読み込みの失敗を非対話で返す経路は、確かめた範囲ではこれだけである（`codex exec` / `plugin list` は hooks について何も出さない）。app-server は experimental だが、応答の形が変われば陽性対照と取得の検査が落ちる |
| 陽性対照を毎回先に読ませる | 本物の判定は「報告 0 件」で通る。報告を出さない版（Claude Code 2.1.261）や報告の形が変わった版では、何も検査しないまま通る。最小の版を決めて `--version` と比べる形は、警告の入った版を特定できず、文言の変化にも効かない |
| 読ませるたびに空の設定ディレクトリを作る | アダプタが導入済みの設定へ `--plugin-dir` を重ねると、導入済みの定義も読まれて数が混ざり、他の検査が見る設定にも書き込む |
| 対象はマーケットプレイス定義から実行時に見つける | ファイルを列挙すると、hooks 定義を持つプラグインを足したときに書き足しを忘れる。マーケットプレイス定義は配布の唯一の入口である |
| 「読まれた」ことを読み込みの行で確かめる | 報告 0 件でも、プラグインがそもそも読まれていなければ検査にならない |
| agy と Kiro CLI を対象に入れない | agy はプラグイン配下の `hooks.json` を読まず `install-hooks.sh` が利用者の設定へ差し込む。Kiro CLI は変換したエージェント定義を読み、元の定義のキーを見ない |

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
| `hook-definitions/` | hooks 定義の検査が判定に使ったログ（Claude Code）と `hooks/list` の応答（Codex）。陽性対照と本物の両方 |

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
| hooks 定義の報告で落ちる | 手元で `plugins/ndf/hooks/claude.json` か `plugins/mcp/mcp-serena/hooks/hooks.json` のマッチャーグループへ `description` を置くと `--runtime claude` が 1、`plugins/ndf/hooks/codex.json` の `type` を `cmd` にすると `--runtime codex` が 1 になり、`smoke.log` に報告の行が出ること |
| 読まれないプラグインで落ちる | `hooks` を存在しないパスへ向ける（claude）/ 定義を `{"hooks":{}}` にする（codex）と `did not load hooks for: <名前>` で 1 になること |
| 陽性対照が報告を受け取れる | 陽性対照の壊し方を外すと `did not report the broken hooks fixture` で 1 になること |
| 検査の判定分岐 | `tests/runtime-smoke/test_hook_definitions_characterization.py`（claude / codex をスタブへ置き換える） |
| secret mode 拒否 | `bash scripts/runtime-smoke-test.sh --runtime claude --with-secrets=auto --keep-container` が非 0 になること |
| 旧参照残存 | `plugins/ndf` や `plugins/mcp-*` の旧配置参照が残っていないこと |

## 関連リンク

- [Runtime smoke README](../../tests/runtime-smoke/README.md)
- [issue #571](https://github.com/devbasex/ai-plugins/issues/571) / [PR #579](https://github.com/devbasex/ai-plugins/pull/579)（設計） / [PR #597](https://github.com/devbasex/ai-plugins/pull/597)（実装） — hooks 定義の検査
- [runtime smoke wrapper](../../scripts/runtime-smoke-test.sh)
- [runtime plugin validation](../../scripts/validate-runtime-plugins.sh)
- [Runtime plugin smoke workflow](../../.github/workflows/runtime-plugin-smoke.yml)
- [Runtime plugin authenticated smoke workflow](../../.github/workflows/runtime-plugin-authenticated-smoke.yml)
- [Runtime Plugin Distribution 仕様](runtime-plugin-distribution.md)
