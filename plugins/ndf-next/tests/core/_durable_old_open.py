"""前の版の DBOS で作った鍵のファイルを今の殻で開き、途中の耐久ワークフローを回復する（test_durable_migrate.py が子プロセスで起こす）。

引数: <チェックアウトの置き場> <実行の鍵> <手の記録のファイル>
関数の名前は `_durable_old_make.py` と同じにする（回復は名前で引く）。回復した結果を JSON で標準出力へ 1 行書く。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ndf.core import durable

ROOT, RUN_KEY, LOG = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
WORKFLOW_ID = "old-wf"


@durable.step
def old_step(n: int) -> int:
    with LOG.open("a") as f:
        f.write(f"{n}\n")
    return n * 10


@durable.workflow
def old_flow() -> list[int]:
    return [old_step(1), old_step(2), old_step(3)]


def main() -> None:
    with durable.session(ROOT, RUN_KEY):
        print(json.dumps(durable.result(WORKFLOW_ID)))


if __name__ == "__main__":
    main()
