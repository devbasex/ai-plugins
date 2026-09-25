# Runtime Smoke Tests

`scripts/runtime-smoke-test.sh` runs Claude, Codex, and Kiro plugin smoke tests in disposable Docker containers. The host home directory and credential directories are not mounted.

```bash
bash scripts/runtime-smoke-test.sh
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
bash scripts/runtime-smoke-test.sh --runtime kiro
```

Artifacts are written to `tmp/runtime-smoke/<runtime>/` by default:

- `smoke.log`
- `version.log`
- `generated-tree.txt`
- `junit.xml`

Secret modes:

- `--with-secrets=off`: PR-safe unauthenticated smoke.
- `--with-secrets=auto`: inject allowlisted secrets when present and skip authenticated checks when absent.
- `--with-secrets=required`: fail when no allowlisted secret exists.

`--keep-container` is local-debug only and is rejected with `auto` or `required` secret modes.

File secrets are passed with allowlisted keys:

```bash
bash scripts/runtime-smoke-test.sh --runtime claude --with-secrets=auto \
  --secret-file bigquery-key-file=/path/to/service-account.json
```

The authenticated GitHub workflow accepts `BIGQUERY_KEY_FILE_JSON` as a protected secret and writes it to a temporary file before passing it through `--secret-file`.

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

1. **手元の像が古い。** Claude Code 2.1.261 は同じ定義に警告を出さない。像はランタイムを版の固定なしで導入するため、キャッシュが残ると古い版のまま動く。キャッシュなしで作り直す

   ```bash
   docker build -f tests/runtime-smoke/Containerfile.base -t ai-plugins-runtime-smoke-base .
   docker build --no-cache -f tests/runtime-smoke/Containerfile.claude -t ai-plugins-runtime-smoke-claude .
   docker build --no-cache -f tests/runtime-smoke/Containerfile.codex -t ai-plugins-runtime-smoke-codex .
   ```

2. **ランタイムが報告の形を変えた。** 継続的統合でも落ちるなら、`hook-definitions/<ランタイム>-positive-control.*` のログと応答を読み、チェックの拾い方を新しい形に合わせる
