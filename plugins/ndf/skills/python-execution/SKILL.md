---
name: python-execution
description: |
  Python実行環境を自動判定し、適切なコマンドでPythonコードを実行します。uv、venv、システムPythonを自動検出。

  このSkillは以下を提供します:
  - プロジェクト環境の自動検出（uv/venv/システムPython）
  - 実行コマンドの自動選択
  - uv環境のセットアップガイド

  Triggers: "python", "uv", "スクリプト", "python環境"
allowed-tools:
  - Read
  - Bash
  - Glob
---

# Python Execution Skill

## 概要

Pythonコードを実行する前に、プロジェクトの実行環境を調査し、適切な方法で実行するためのガイドラインです。

## Step 1: 環境検出

```bash
ls -la pyproject.toml uv.lock .venv/ venv/ requirements.txt 2>/dev/null
```

## Step 2: 実行コマンド選択

| 検出ファイル | 実行方法 | 優先度 |
|-------------|---------|-------|
| `pyproject.toml` | `uv run python` | 最高 |
| `.venv/` | `.venv/bin/python` | 中 |
| `venv/` | `venv/bin/python` | 中 |
| 何もなし | `python3` | 最低 |

## Step 3: 実行

### uv環境（pyproject.tomlあり）

```bash
# 依存関係インストール（初回のみ）
uv sync

# 実行
uv run python script.py
uv run python -m module_name
```

**uvがない場合のインストール**:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc  # パスを反映
```

### venv環境（.venv/あり）

```bash
# 依存関係インストール（初回のみ）
.venv/bin/pip install -r requirements.txt

# 実行
.venv/bin/python script.py
```

### システムPython

```bash
python3 script.py
```

## ベストプラクティス

| DO | DON'T |
|----|-------|
| 実行前に環境を調査 | 環境を確認せずに実行 |
| README.md/CLAUDE.mdの指示を優先 | グローバル環境に依存関係をインストール |
| pyproject.tomlがあればuv使用 | source activateに依存 |
| 仮想環境のPythonをパス指定で実行 | python2を使用 |

## 詳細ガイド（必要時のみ参照）

| ファイル | 内容 | 参照タイミング |
|---------|------|--------------|
| `01-uv-setup.md` | uv詳細セットアップ、Pythonバージョン管理 | 初回セットアップ時 |
| `02-troubleshooting.md` | エラー解決策 | 問題発生時 |

## 関連Skill

- **corder-code-templates**: Pythonコードテンプレート
- **corder-test-generation**: Pythonテスト生成
