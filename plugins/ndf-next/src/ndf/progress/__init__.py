"""工程のステートマシン（#3092）。工程の定義を読み、ステートマシンの型と判断・保存・サブコマンド `progress` を持つ。

- `model`: ステートマシンの型と純粋な判断（事象の畳み込み・書き手と遷移の確かめ）
- `stages`: 工程の定義の型と、順序の決まり・有効と無効の決め方
- `definition`: 工程の定義（`contracts/workflow/`）の読み込みと検査
- `store`: ステートマシンの保存（SQLite と PostgreSQL）と、DB の検査・バックアップ・戻し
- `command`: サブコマンド `ndf progress`
"""
