"""手を 3 つ集めたら終わる判断の関数（耐久の有無で同じ判断を比べるテストが共有する）。

手の番号は 1 から 3、手の結果を集めた状態で終わる。`_durable_crash.py` は隣のモジュールとして import し、
pytest のテスト（`--import-mode=importlib`）はファイルの場所から読む。
"""

from __future__ import annotations

from ndf.core.loop import Next


def decide(state: list[int], result: int | None) -> Next[int, list[int]]:
    seen = state if result is None else [*state, result]
    if len(seen) == 3:
        return Next.finish(seen)
    return Next.step(len(seen) + 1, seen)
