"""環境が無いときの hook のエントリポイントが、システムの python3 の下限を配る構成の設定から読む（#3687 の決定 3）。"""

from __future__ import annotations

import io

from ndf.contracts import toolchain
from ndf.domains.hooks import boot, tables

from ._hooks import records


def test_boot_reads_the_same_floor_as_the_contract_reader():
    # hook の経路は ndf.contracts を import しないため、同じ表を標準ライブラリで読む
    assert boot.hook_floor() == toolchain.hook_floor()


def test_boot_does_nothing_below_the_floor(monkeypatch):
    real = tables.load

    def load(rel):
        data = real(rel)
        if rel == toolchain.TABLE:
            return {**data, "python": {**data["python"], "hook_floor": "99.0"}}
        return data

    monkeypatch.setattr(tables, "load", load)
    out = io.StringIO()
    assert boot.run_boot(["boot.py", "claude", "PreToolUse"], io.StringIO("not json"), out) == 0
    assert out.getvalue() == ""
    # 下限の前で戻るので、入力を読まない（下限を満たせば input_shape になる入力）
    assert "error" not in records()[-1] and "runtime" not in records()[-1]
