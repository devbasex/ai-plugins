#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""`lib/monitor.py` への移設シム。

実体は [lib/monitor.py](lib/monitor.py) にある（cross-review と cross-refactoring が
共有する層）。このファイルは **既存の呼び出しパスと import を維持するためだけ**に残す。

`import` ではなく `exec` で読み込むのは、実体側で定義された関数の名前解決先を
**このモジュールの名前空間**にするためである。`import` すると
`mock.patch.object(monitor_mod, "_pid_alive", ...)` のような既存テストの差し替えが
実体側モジュールへ届かず、既存テストを書き換える必要が出る。
"""
from __future__ import annotations

import pathlib

_IMPL = pathlib.Path(__file__).resolve().parent / "lib" / "monitor.py"
exec(compile(_IMPL.read_text(encoding="utf-8"), str(_IMPL), "exec"), globals())
