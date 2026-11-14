"""1 つの実行の鍵で耐久ワークフローを流すプロセス（片付けと待ちの一覧のテストが子プロセスで起こす）。

引数: <チェックアウトの置き場> <実行の鍵> <流し方> [<URL>]
- finish: 耐久ワークフローを 1 つ流して終わる（抜けるときに鍵のファイルが retired/ へ移る）
- wait: メッセージを待つ耐久ワークフローを裏で起こし、`PENDING` になったら落ちる（待ちを抱えたまま残る）
- child-wait: 子を起こしてすぐ終わる親を流し、子が `PENDING` になったら落ちる（親は終わり、子は待ち）
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from ndf.core import durable

ROOT, RUN_KEY, MODE = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
URL = sys.argv[4] if len(sys.argv) > 4 else None


@durable.workflow
def finished(value: str) -> str:
    return value


@durable.workflow
def waiting() -> str | None:
    return durable.recv("go", 600)


@durable.workflow
def parent(child_id: str) -> str:
    durable.spawn(waiting, child_id)
    return "parent-done"


def _wait_pending(workflow_id: str) -> None:
    deadline = time.monotonic() + 60
    while durable.status(workflow_id) != "PENDING":
        if time.monotonic() > deadline:
            raise SystemExit(f"{workflow_id} が PENDING にならない")
        time.sleep(0.05)


def main() -> None:
    with durable.session(ROOT, RUN_KEY, URL):
        if MODE == "finish":
            print(json.dumps(durable.start(finished, f"wf-{RUN_KEY}", RUN_KEY)))
            return
        if MODE == "wait":
            durable.spawn(waiting, f"wf-{RUN_KEY}")
            _wait_pending(f"wf-{RUN_KEY}")
        elif MODE == "child-wait":
            durable.start(parent, f"wf-{RUN_KEY}", f"wf-{RUN_KEY}-child")
            _wait_pending(f"wf-{RUN_KEY}-child")
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    main()
