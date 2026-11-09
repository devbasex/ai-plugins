"""ループの記録の置き場と保留したループの 1 回の結果の名前（読み込みに副作用の無い定数だけ）。

記録のファイル名 RECORD は書き手の sprint_loop_record に置く（契約の検査はモジュールの中の定数だけを解決する）。

sprint_loop_record が正本として再び公開する。parallel_run のように記録を読むだけの側は、
書き手の sprint_loop_record を読み込まずにここから取る（読み込むと、入口の契約に書きの受け渡しが加わる）。
"""

from __future__ import annotations

from pathlib import Path

LOOP_DIR = Path(".ndf") / "loop"
RELEASE_PENDING, QUESTION_PENDING = "リリース待ち", "質問待ち"  # 答えを待たずに締めた保留したループの 1 回（#2436 の受け入れ条件 6a・6d）
PENDING = (RELEASE_PENDING, QUESTION_PENDING)
