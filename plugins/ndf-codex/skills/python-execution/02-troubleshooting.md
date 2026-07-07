# Python実行 トラブルシューティング

## よくある問題と解決策

### Q: `uv: command not found`

**原因**: uvがインストールされていない

**解決策**:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# シェルを再起動するか、パスを通す
source ~/.bashrc  # または ~/.zshrc
```

### Q: `ModuleNotFoundError`

**原因**: 依存関係がインストールされていない

**解決策**:
```bash
# uv環境の場合
uv sync

# venv環境の場合
.venv/bin/pip install -r requirements.txt

# pyproject.tomlがある場合
.venv/bin/pip install -e .
```

### Q: `python: command not found`

**原因**: Pythonがインストールされていない、またはパスが通っていない

**解決策**:
```bash
# python3を試す
python3 --version

# uvでPythonをインストール
uv python install 3.12
```

### Q: 異なるPythonバージョンが必要

**解決策（uv環境）**:
```bash
# 特定バージョンをインストール
uv python install 3.11

# プロジェクトで使用するバージョンを固定
uv python pin 3.11

# そのバージョンで実行
uv run python script.py
```

### Q: `pyproject.toml`はあるが`uv.lock`がない

**解決策**:
```bash
# ロックファイルを生成
uv lock

# 依存関係をインストール
uv sync
```

### Q: 仮想環境が壊れている

**解決策**:
```bash
# 仮想環境を削除して再作成
rm -rf .venv

# uv環境の場合
uv sync

# 手動で作成する場合
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Q: パーミッションエラー

**解決策**:
```bash
# 仮想環境を使用（推奨）
uv sync
uv run python script.py

# どうしてもグローバルにインストールする場合（非推奨）
pip install --user package_name
```

### Q: SSL証明書エラー

**解決策**:
```bash
# macOSの場合
/Applications/Python\ 3.x/Install\ Certificates.command

# または環境変数で一時的に無効化（非推奨）
export PYTHONHTTPSVERIFY=0
```

## 関連リソース

- [uv公式ドキュメント](https://docs.astral.sh/uv/)
- [Python venv](https://docs.python.org/3/library/venv.html)
- [pyproject.toml仕様](https://packaging.python.org/en/latest/specifications/pyproject-toml/)
