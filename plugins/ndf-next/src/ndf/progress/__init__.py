"""工程の進行表（#3092）。工程の定義を読み、進行表の型と判断・保存・副命令 `progress` を持つ。

- `model`: 進行表の型と純粋な判断（事象の畳み込み・書き手と遷移の確かめ・順序の決まり・有効と無効の決め方）
- `definition`: 工程の定義（`contracts/workflow/`）の読み込みと検査
- `store`: 進行表の保存（SQLite と PostgreSQL）と、DB の検査・控え・戻し
- `command`: 副命令 `ndf progress`
"""
