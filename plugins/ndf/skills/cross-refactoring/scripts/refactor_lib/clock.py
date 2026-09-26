"""時刻の読み書き（#933）。中身はライブラリの `scripts/lib/clock.py` にある（#1142 の C4）。

状態ファイルの時刻（`statefile.now`）はタイムゾーンを持たず、コミットの時刻
（`git log --format=%cI`）は持つ。**比べる前に両方をタイムゾーン付きへ揃える。**
`parse` はタイムゾーンの無い時刻にこの機械の地方時を付ける（付けないまま比べると `TypeError` になる）。
状態ファイルへ書く形（`iso`）はタイムゾーン付きで秒まで。
"""
from __future__ import annotations

from clock import iso, now, parse, seconds_between  # noqa: F401  ライブラリの時刻をこのパッケージの名前で使う
