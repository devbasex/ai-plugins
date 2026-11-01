# Runtime Smoke Tests

`scripts/runtime-smoke-test.sh` runs Claude and Codex plugin smoke tests in disposable Docker containers. The host home directory and credential directories are not mounted.

```bash
bash scripts/runtime-smoke-test.sh
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
```

Artifacts are written to `tmp/runtime-smoke/<runtime>/` by default:

- `smoke.log`
- `version.log`
- `generated-tree.txt`
- `junit.xml`
- `cli-versions.env` (the CLI version list the image was built with)

Secret modes:

- `--with-secrets=off`: PR-safe unauthenticated smoke.
- `--with-secrets=auto`: inject allowlisted secrets when present and skip authenticated checks when absent.
- `--with-secrets=required`: fail when no allowlisted secret exists.

`--keep-container` is local-debug only and is rejected with `auto` or `required` secret modes.

The BigQuery check calls `list_dataset_ids` on the remote BigQuery MCP server with the gcloud access token. It needs `BIGQUERY_ACCESS_TOKEN` and `BIGQUERY_PROJECT`. The token expires in an hour, so run it locally rather than from the GitHub workflow:

```bash
BIGQUERY_ACCESS_TOKEN=$(gcloud auth print-access-token) BIGQUERY_PROJECT=<PROJECT_ID> \
  bash scripts/runtime-smoke-test.sh --runtime claude --with-secrets=auto
```

The authenticated GitHub workflow passes only `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`.

## CLI の版

像は 2 ランタイムの CLI を、CLI の版の一覧 `tests/runtime-smoke/cli-versions.env` に書いた版で入れる。版を上げるときに書き換えるのはこのファイルだけで、Containerfile は版を持たない（`ARG` に既定値を置かない）。

| ランタイム | 一覧のキー | 取得先 |
| --- | --- | --- |
| claude | `CLAUDE_CODE_VERSION` | npm の `@anthropic-ai/claude-code@<版>` |
| codex | `CODEX_VERSION` | npm の `@openai/codex@<版>` |

`scripts/runtime-smoke-test.sh` は一覧の各行を `--build-arg` で像の作成へ渡し、`--cli-versions <パス>` で別の一覧を受ける。像の作成は、取得・`--version` の出力から取り出した版数と一覧の版数の完全一致のどれかが落ちたら失敗する。

配布の経路のスモーク（`runtime-plugin-smoke.yml` の `runtime-smoke (<ランタイム>)`）と認証つきのスモークは、一覧の版だけを入れる。その時点の最新の版は、別の workflow `runtime-plugin-smoke-latest.yml` が毎日 06:00 JST に develop の上で試す（Actions の画面から手でも起動できる。PR と push では走らず、必須のチェックに入らない）。落ちたら、題が `最新の版のスモークが落ちた: <ランタイム>` の課題をランタイムごとに 1 件作り、開いていればコメントを足す。課題には入れた版と実行の URL が載る。

### 版を上げる

1. 最新の版を一覧の形で出す（失敗の課題に載った版を使ってもよい）

   ```bash
   python3 scripts/runtime-smoke-latest.py resolve --runtime <claude|codex>
   ```

2. 出力の行で、`tests/runtime-smoke/cli-versions.env` の同じキーの行を置き換える
3. 手元でスモークを走らせ、`tmp/runtime-smoke/<ランタイム>/version.log` の版が一覧の版と同じことを見る

   ```bash
   bash scripts/runtime-smoke-test.sh --runtime <ランタイム> --with-secrets=off
   ```

4. PR を出し、`runtime-smoke (<ランタイム>)` が通ることを見る。落ちたらアダプタか検査を新しい版に合わせる修正を同じ PR か先の PR で入れる。失敗の課題は、その PR のマージの後に閉じる

## hooks 定義のチェック

`assertions/assert-hook-definitions.sh <claude|codex>` は、hooks 定義をランタイム自身に読ませ、
読み込みの報告（警告・誤り）が 1 件でもあれば落とす。受け取るキーの一覧はこちらで持たない。
決定の理由は [Runtime Plugin Container Smoke Test 仕様](../../docs/specifications/runtime-plugin-container-smoke.md#hooks-定義のチェック) にある。

| ランタイム | 読ませ方 | 報告として扱うもの | 「読まれた」の判定 |
| --- | --- | --- | --- |
| Claude Code | `claude --debug-file <log> --plugin-dir ... plugin list` | ログの `[WARN]` / `[ERROR]` の行のうち hooks に触れるもの | `Read hooks.json for plugin <名前>` / `Read manifest hooks for plugin <名前>` |
| Codex | `lib/codex-hooks-list.py`（`codex app-server` の `hooks/list`） | `data[].warnings` / `data[].errors` の要素 | `data[].hooks[].pluginId` |

- **対象は `.claude-plugin/marketplace.json` から実行時に見つける。** そのランタイムの `plugin.json` が `hooks` を持つか、`hooks/hooks.json` があるプラグインが対象になる。見つけた数が 0 なら落とす
- **読ませるたびに空の設定ディレクトリ（`CLAUDE_CONFIG_DIR` / `CODEX_HOME`）を作る。** アダプタが導入した設定には書き込まない
- **先に陽性対照（`fixtures/hooks-positive-control/`）を読ませる。** Claude Code にはマッチャーグループの `description` を、Codex には読めない `type` を持たせてある。報告が出なければ、本物の判定より前に落とす
- 判定に使ったログと応答は `hook-definitions/` に残る（成功したときだけ成果物へ写される）

Codex は今のところ未知キーを報告しない。Codex のチェックが落とすのは、読めない定義と、hooks が 1 つも登録されない定義である。

### 陽性対照で落ちたとき

`<ランタイム> <版> did not report the broken hooks fixture` は、ランタイムが壊れた定義を報告しなかったことを示す。原因は 2 つある。

1. **一覧の版が古い。** Claude Code 2.1.261 は同じ定義に警告を出さない。一覧（`cli-versions.env`）の版が報告を出さない版なら、「版を上げる」の手順で版を上げる

2. **ランタイムが報告の形を変えた。** 継続的統合でも落ちるなら、`hook-definitions/<ランタイム>-positive-control.*` のログと応答を読み、チェックの拾い方を新しい形に合わせる
