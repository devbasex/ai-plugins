"""秘密を入力と出力と例外に含む耐久ワークフローを流す（test_durable_secrets.py が子プロセスで起こす）。

引数: <チェックアウトの置き場> <実行の鍵>。秘密は環境変数 `NDF_TEST_TOKEN` と `NDF_PROGRESS_DB` の値。
流れている呼び出しには元の値が渡ることを、結果の JSON（標準出力の 1 行）で返す。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ndf.core import durable

ROOT, RUN_KEY = Path(sys.argv[1]), sys.argv[2]


@durable.step
def echo(value: dict) -> dict:
    return {"seen": value, "url": os.environ["NDF_PROGRESS_DB"]}


@durable.step
def fail(value: str) -> None:
    raise ValueError(f"落ちた: {value}")


@durable.workflow
def flow(value: dict) -> dict:
    out = echo(value)
    try:
        fail(value["token"])
    except ValueError as exc:
        out["error"] = str(exc)
    return out


def main() -> None:
    with durable.session(ROOT, RUN_KEY):
        print(
            json.dumps(durable.start(flow, "secret-wf", {"token": os.environ["NDF_TEST_TOKEN"], "pair": (1, os.environ["NDF_TEST_TOKEN"])}))
        )


if __name__ == "__main__":
    main()
