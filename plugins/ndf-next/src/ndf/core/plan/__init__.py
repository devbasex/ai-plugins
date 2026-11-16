"""プランの実行（遷移・キュー・組み立て・再開・重なり・待ち）。

- `model`: プランの形（`plan.json`）を読み、実行の前に確かめる（IS3）
- `*_decide`: 判断の純粋な関数。遷移（`transition_decide`）・キュー（`queue_decide`）・組み立て（`assemble_decide`）・
  入力の確かめ（`inputs_decide`）・待ちの結果（`wait_decide`）
- `steps`: ステップのコマンドを打ち、結果 JSON を読む。前の子のプロセスが止まったかを確かめる
- `runner`: 耐久ワークフローの中で遷移の判断を回し、実行の回ごとに 1 つの鍵で DBOS を使う
- `cli`: 副命令 `plan`
"""
