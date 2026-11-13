"""テスト基盤（#1142 の T1）。

- 同時実行の枠: `weight`（重さの判定）・`limits`（上限の値）・`machine`（作業マシンの CPU とメモリ）・
  `slots`（枠の取得・待ち・メモリの監視・記録）・`command`（副命令 `test-slot`）
- 隔離と揺れない待ち: `isolation`（本物の状態の置き場への書き込みを落とす pytest の前提）・`waits`（条件を待つ）・
  `cmd_match`（コマンドを語の並びで比べる）
- テスト環境の回収の判定: `testenv`
- 振る舞いの確認の起動: `behavior`
"""
