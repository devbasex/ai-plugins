# AI Plugins - Kiro CLI開発ガイドライン

共通の開発ガイドラインは **@AGENTS.md** を参照してください。

このファイルにはKiro CLI固有の設定のみを記載します。

## Kiro CLIでのコード探索

### 基本ワークフロー

#### 1. プロジェクト構造の把握
```bash
# ディレクトリ構造を確認
ls -la plugins/

# 特定のプラグインディレクトリを確認
ls -la plugins/ndf/
```

#### 2. コードインテリジェンスの活用

**シンボル検索**:
```
# プラグイン名でシンボルを検索
code search_symbols "ndf"

# 特定のファイル内のシンボル一覧
code get_document_symbols plugins/ndf/.claude-plugin/plugin.json
```

**パターン検索**:
```
# バージョン情報を検索
grep "version" --include="*.json"

# 特定のキーワードを検索
grep "MCP" --include="*.md"
```

#### 3. ファイル読み取り

```
# ファイル全体を読む
fs_read plugins/ndf/.claude-plugin/plugin.json

# 特定の行範囲を読む
fs_read plugins/ndf/README.md --start_line=1 --end_line=50
```

## Kiro CLI特有の注意事項

### サブエージェントの活用

複雑なタスクは専門のサブエージェントに委譲：

```
# 複数の独立したタスクを並列実行
use_subagent InvokeSubagents
```

### LSP統合

コードインテリジェンスを最大限活用：

```bash
# プロジェクトルートでLSP初期化（必要に応じて）
/code init
```

### AWS統合

AWS CLIを使用してリソース管理：

```bash
# S3バケット一覧
use_aws s3 list-buckets

# Lambda関数一覧
use_aws lambda list-functions
```
