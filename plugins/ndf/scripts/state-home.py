#!/usr/bin/env python3
"""NDF の状態の置き場の根を出す・旧の置き場から移す（#2766）。結果は統一 JSON レポートを 1 行で標準出力へ出す。

| 副命令 | 役割 |
| --- | --- |
| `path` | 今の解決で使う根（`lib/state_home.py` の順）と、既定・旧の根を出す |
| `migrate` | 旧 `~/.local/state/ndf` を既定 `~/.claude/ndf/state` へ移す。旧を使っているプロセスがあれば何も変えずに 1 で終える |

終了コード: 0 移した・移すものが無い / 1 旧を使っているプロセスがある・新と旧が両方ある（何も変えない）。
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
    sub.add_parser("path", help="今の根と既定・旧の根を出す")
    sub.add_parser("migrate", help="旧の根を既定の根へ移す")
    args = ap.parse_args(argv)
    if args.cmd == "path":
        root = sh.state_home()
        metrics = {"root": str(root), "default": str(sh.default_home()), "legacy": str(sh.legacy_home())}
        emit(result(TOOL, "ok", f"状態の根は {root}", metrics=metrics))
    r = sh.migrate()
    ok = r["status"] in ("migrated", "none")
    items = [{"pid": h["pid"], "path": h["path"], "cmd": h["cmd"]} for h in r["holders"]]
    metrics = {"result": r["status"], "from": r["from"], "to": r["to"]}
    emit(result(TOOL, "ok" if ok else "stopped", r["summary"], items=items, metrics=metrics))


if __name__ == "__main__":
    main()
