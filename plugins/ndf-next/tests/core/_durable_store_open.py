"""チェックアウトの置き場を用意するプロセス（test_durable_store.py が 2 つ同時に起こす）。

引数: <チェックアウトの置き場> <シグナルファイル>
シグナルファイルができるまで待ってから `prepare_store` を呼び、結果を JSON で標準出力へ 1 行書く。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from ndf.core import durable

ROOT, GO = Path(sys.argv[1]), Path(sys.argv[2])


def main() -> None:
    deadline = time.monotonic() + 30
    while not GO.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    try:
        ready = durable.prepare_store(ROOT)
    except durable.StoreStopped as exc:
        print(json.dumps({"kind": exc.kind}))
        return
    print(json.dumps({"kind": "ok", "retired_to": None if ready.retired_to is None else str(ready.retired_to)}))


if __name__ == "__main__":
    main()
