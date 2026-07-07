# uv詳細セットアップガイド

> **Note**: 基本的な`uv sync`と`uv run python`はSKILL.mdを参照。このファイルは初回セットアップや詳細設定が必要な場合のみ参照。

## uvインストール

```bash
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# pip経由（代替）
pip install uv

# 確認
uv --version
```

## 依存関係管理

```bash
# uv.lockがある場合（推奨）
uv sync

# uv.lockがない場合
uv lock && uv sync

# 開発用依存関係も含める
uv sync --dev

# 特定のextraを含める
uv sync --extra test
```

## Pythonバージョン管理

```bash
# 特定バージョンをインストール
uv python install 3.12
uv python install 3.11

# プロジェクトで使用するバージョンを固定
uv python pin 3.12

# インストール済みバージョン一覧
uv python list
```

## 実行オプション

```bash
# スクリプト実行
uv run python script.py

# モジュール実行
uv run python -m pytest
uv run python -m mypy .

# 引数付き
uv run python script.py --arg value

# インタラクティブシェル
uv run python
```

## プロジェクト初期化（新規作成時）

```bash
# 新規プロジェクト作成
uv init my-project
cd my-project

# 依存関係追加
uv add requests
uv add --dev pytest

# ロックファイル生成
uv lock
```

## uv環境の利点

- **高速**: Rustで実装、pip比10-100倍速
- **再現性**: uv.lockで完全な依存関係固定
- **Pythonバージョン管理**: pyenvなしでバージョン切り替え
- **グローバル環境を汚染しない**: プロジェクト単位で隔離
