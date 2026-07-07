# Serena MCP ガイド

Serena MCPはセマンティックコードインテリジェンスツールです。
コード操作専用。メモリー機能は使用しません。

## アクティベーション

プロジェクトで使用する前にアクティベートが必要です。

```
mcp__plugin_mcp-serena_serena__activate_project /path/to/project
mcp__plugin_mcp-serena_serena__check_onboarding_performed
```

## コード探索（推奨順序）

### 1. ディレクトリ構造の確認

```
mcp__plugin_mcp-serena_serena__list_dir relative_path="src/" recursive=false
```

### 2. シンボル概要の取得（ファイル全体を読む前に）

```
mcp__plugin_mcp-serena_serena__get_symbols_overview relative_path="path/to/file.py"
```

### 3. シンボル検索

```
mcp__plugin_mcp-serena_serena__find_symbol name_path="/ClassName/method_name" relative_path="src/" include_body=true
```

### 4. パターン検索（シンボル名が不明な場合）

```
mcp__plugin_mcp-serena_serena__search_for_pattern substring_pattern="keyword" relative_path="src/"
```

### 5. リファレンス検索

```
mcp__plugin_mcp-serena_serena__find_referencing_symbols name_path="/SymbolName" relative_path="src/"
```

## コード編集

### シンボル単位の置換

```
mcp__plugin_mcp-serena_serena__replace_symbol_body name_path="/Class/method" relative_path="file.py" body="new code"
```

### シンボルの前後に挿入

```
mcp__plugin_mcp-serena_serena__insert_before_symbol name_path="/FirstSymbol" relative_path="file.py" content="new code"
mcp__plugin_mcp-serena_serena__insert_after_symbol name_path="/LastSymbol" relative_path="file.py" content="new code"
```

### シンボル名の変更

```
mcp__plugin_mcp-serena_serena__rename_symbol name_path="/OldName" relative_path="file.py" new_name="NewName"
```

## 使い分けの原則

- ファイル全体を読む前に `get_symbols_overview` で概要を確認
- シンボル名が分かっている場合は `find_symbol` で直接アクセス
- シンボル名が不明な場合は `search_for_pattern` で候補を特定
- 編集は `replace_symbol_body` でシンボル単位で行う（行ベース編集より安全）
