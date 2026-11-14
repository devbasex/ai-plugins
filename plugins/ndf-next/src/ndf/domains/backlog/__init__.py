"""課題の棚卸し（M6）。

候補の収集（`candidates`）・順位の算出（`rank`）・反映（`apply`。判定の反映・起票・順位の表・移動・付け直し）・
付け直しの戻し（`revert`）・深さの上限と起動の判定（`depth-limits`・`depth-record`・`due`）と、解消する Issue を
証拠を確かめてから閉じる操作（`settle`）を `ndf issues` の副命令として持つ。

| モジュール | 責務 |
| --- | --- |
| `model.py` | 見積・行・設定の型と採点（WSJF） |
| `rank_decide.py` | 枠・切り出しの境界・前倒しと後ろ倒しの候補・古い却下（純粋） |
| `selection_decide.py` | 候補を上限で切る並びと持ち越し（純粋） |
| `estimates_decide.py` | 課題ごとの見積の採り方（純粋） |
| `plan_decide.py` | plan の却下と移動を順位の候補と照らす（純粋） |
| `settle_decide.py` | 閉じる証拠の入力の形と判定（純粋） |
| `actions_decide.py` | 判定の反映と起票の決まり（純粋） |
| `table_decide.py` | マイルストーンの説明の `### 順位` の節の描画と置き換え（純粋） |
| `due_decide.py` | 見直し・大整理を起動するかの判定（純粋） |
| `state.py` | 状態の置き場と JSON の読み書き |
| `depth.py` | 深さ（見直し・大整理）の秒の上限と打ち切りの記録 |
| `issues_gh.py` | GitHub のアダプター越しの Issue・マイルストーン・親の読み書き |
| `reparent.py` | 付け直しと戻し |
| `actions.py` | 判定の反映と起票（記録 ledger.json） |
| `tables.py` | 順位の表をマイルストーンの説明へ書く |
| `settle.py` | 証拠を GitHub で確かめて閉じる |
| `collect.py` | 候補の経路の収集 |
| `cli.py` | `ndf issues` の副命令 |
"""
