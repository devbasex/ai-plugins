"""後片付けと回収の結果の語（`items` の各件の `result`）。書く側（`merged`・`testenv`・`testenv_sweep`）と読む側（`cli`・`cli_env`）が同じ綴りを使う。"""

from __future__ import annotations

KEPT = "kept"  # 残した（理由を添える）
REMOVED = "removed"  # worktree を外した
DELETED = "deleted"  # ブランチを消した
ABSENT = "absent"  # 消すブランチが無かった
PULLED = "pulled"  # メインディレクトリを取り込んだ
STOPPED = "stopped"  # 止めた（取り込みが止まった・テスト環境を止めた）
RECLAIMED = "reclaimed"  # テスト環境を破棄して解放した
RELEASED = "released"  # 割り当てだけを解放した
