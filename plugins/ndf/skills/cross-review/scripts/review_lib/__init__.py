"""cross-review の `state.py` の中身（#1142 の C2）。

`state.py` は副命令の引数の解析と `main` だけを持ち、状態・GitHub・指摘・副命令の本体はこのパッケージが持つ。
import の向きは `__init__` ← `store`・`github`・`categories`・`review_focus` ← `workspace`・`ci`・`findings`・
`posts`・`fix_result` ← `participants` ← `matching` ← `commands/*` ← `state.py` の一方向である。
`commands/` どうしは import し合わない。

**呼ぶ側は移した先の関数をモジュールの属性として呼ぶ**（`github._gh_rest(...)`）。テストは関数を
定義したモジュールの上で差し替えるため、`from ... import 関数` で名前を取り込むと差し替えが効かない。

ライブラリ（`plugins/ndf/scripts/lib/`）と `classifications.py` の置き場所を `sys.path` に足すのはここだけである。
**モジュール名を `queue` にしない。** 標準ライブラリに同じ名前があり、ライブラリを
`sys.path` の先頭へ入れるとプロセス全体で標準ライブラリ側が隠れる。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
_LIB = _SCRIPTS.parents[2] / "scripts" / "lib"
for _path in (_LIB, _SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import clock  # noqa: E402
from proc import die, info  # noqa: E402,F401  結果を出して止まる・標準エラーへ 1 行

# 状態ファイルに書く時刻（地方時・秒まで）
_now = clock.now_iso


def _sh(cmd: list[str], check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"command failed ({' '.join(cmd)}): {r.stderr.strip()}")
    return r.stdout.strip()
