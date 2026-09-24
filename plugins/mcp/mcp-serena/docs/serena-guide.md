# Serena MCP ガイド

Serena はシンボル単位でコードを読み書きするための MCP サーバです。コード操作だけに使い、
memory は使いません。

## 使い始める前に

**言語サーバの設定はプロジェクトごとに行う。** 使うリポジトリで 1 度
`/mcp-serena:language-servers` を実行し、`.serena/project.yml` の `language_servers` を
書きます（詳細は `README.md`）。プロジェクトの有効化は `--project-from-cwd` で自動に行われる
ため、`activate_project` は呼びません（Claude Code の文脈には無いツールです）。

ツール名の接頭辞はランタイムで違います。

| ランタイム | 接頭辞 |
| --- | --- |
| Claude Code | `mcp__plugin_mcp-serena_serena__` |
| Codex | `mcp__serena__` |

以下は接頭辞を省いて書きます。

## コードを読む（推奨の順序）

ファイルを丸ごと読む前に、シンボルの単位で必要な分だけを読みます。

### 1. シンボルの概要（ファイル全体を読む前に）

```text
get_symbols_overview relative_path="path/to/file.py"
```

### 2. シンボルの本体

```text
find_symbol name_path_pattern="ClassName/method_name" relative_path="src/" include_body=true
```

### 3. 呼び出し元

```text
find_referencing_symbols name_path="ClassName/method_name" relative_path="src/file.py"
```

シンボル名が分からないときは、Claude Code では `Grep`、Codex では `search_for_pattern` で
候補を絞ってから 2 へ進みます。

## コードを編集する

| 目的 | ツール |
| --- | --- |
| シンボルの本体を置き換える | `replace_symbol_body` |
| シンボルの前後に足す | `insert_before_symbol` / `insert_after_symbol` |
| 名前を変える（参照も追従する） | `rename_symbol` |
| 参照の無いシンボルを消す | `safe_delete_symbol` |

## 診断を取る

| ランタイム | 手段 |
| --- | --- |
| Claude Code（Python / TypeScript / PHP など） | 公式 LSP プラグインの診断が編集の次の手番に届く。呼ぶ手間は無い |
| Claude Code（Bash） | `get_diagnostics_for_file` |
| Codex | `get_diagnostics_for_file` |

## 誘導の hook

設定した言語のファイルを `Read`・`cat`・`sed -n` などで 3 回続けて読むか、grep を 3 回続ける
と（混在なら 4 回）、hook が 1 度だけ止めて上の手順を示します。数は止めた時点で戻るため、
必要ならそのまま同じ操作を続けて構いません。Serena のシンボル系のツールを呼ぶと数が戻ります。
