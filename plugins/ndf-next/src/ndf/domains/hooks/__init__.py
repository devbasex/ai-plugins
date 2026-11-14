"""hook の判定（#3345 の決定 88〜96）。

ランタイムの事象 1 つにつきエントリポイント 1 本が `main.run_entry` を呼び、guard の表（`contracts/hooks/guards.json`）を順に
呼ぶ。入力の解析は `context.HookContext` が 1 回だけ行う。環境が無いときのエントリポイントは `boot.run_boot` で、
標準ライブラリだけで `strict` の guard と環境の用意の起動だけを行う。

このパッケージの `__init__` は何も import しない（`strict`・`env`・`boot` の閉包を標準ライブラリだけに保つ）。
"""
