#!/usr/bin/env python3
"""NDF の状態の置き場の根を出す・旧の置き場から利用者の台帳を移す（#2766）。結果は統一 JSON レポートを 1 行で標準出力へ出す。

| 副命令 | 役割 |
| --- | --- |
| `path` | 作業ディレクトリで使う 3 つの根（チェックアウト・利用者・コンテナ。`lib/state_home.py`）を出す |
| `migrate` | 旧 `~/.local/state/ndf` の利用者の台帳を `~/.claude/ndf/state` へ足し合わせる。何度打ってもよい |

終了コード: 0 移した・移すものが無い / 1 移し先に同じ名前のファイルがあり、旧に残したものがある（`items` に載せる）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import state_home as sh  # noqa: E402
from step_result import emit, result  # noqa: E402

TOOL = "state-home"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="state-home.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("path", help="3 つの根を出す")
    sub.add_parser("migrate", help="旧の根の利用者の台帳を利用者の根へ移す")
    args = ap.parse_args(argv)
    if args.cmd == "path":
        metrics = {"checkout": str(sh.checkout_home()), "user": str(sh.user_home()), "container": str(sh.container_home())}
        emit(result(TOOL, "ok", f"チェックアウトの根は {metrics['checkout']}、利用者の根は {metrics['user']}", metrics=metrics))
    r = sh.migrate()
    ok = r["status"] in ("migrated", "none")
    items = [{"kept": p} for p in r["kept"]]
    metrics = {"result": r["status"], "from": r["from"], "to": r["to"], "moved": len(r["moved"])}
    emit(result(TOOL, "ok" if ok else "stopped", r["summary"], items=items, metrics=metrics))


if __name__ == "__main__":
    main()
