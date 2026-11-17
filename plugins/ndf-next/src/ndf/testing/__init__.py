"""テスト基盤（#1142 の T1）。

- 同時実行のスロット: `weight`（重さの判定）・`limits`（上限の値）・`machine`（作業マシンの CPU とメモリ）・
  `slots`（スロットの取得・待ち・メモリの監視）・
  `records`（スロットの記録の追記と集計）・`command`（サブコマンド `test-slot`）
- 隔離と揺れない待ち: `isolation`（本物の状態の保存先への書き込みを拒む部品。pytest に依らず、テストの前提 `forbid_real_state` は `tests/testing/_real_state.py` が持つ）・`waits`（条件を待つ）・
  `cmd_match`（コマンドを語の並びで比べる）
- テスト環境の回収の判定: `testenv`
- 範囲のテスト（落ちたテストだけの打ち直しを含む）: `scoped`（設定の suite の `scope_command` から組む）
- 振る舞いの確認の起動: `behavior`
- 障害の注入（落としたい時点で子の組を SIGKILL で落とす）: `faults`
"""
