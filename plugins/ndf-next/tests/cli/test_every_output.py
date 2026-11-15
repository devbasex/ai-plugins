"""既存のテストが同じプロセスの中で ok の出力を出さない副命令を、ok を出す引数で呼ぶ（#3518 の決定 9）。

結果 JSON の形は `tests/conftest.py` の `_checked_emit` が確かめ、全体の実行の終わりに ok の出力の無い副命令を落とす。
ここには、ほかのテストが ok の出力を出さない副命令だけを足す（同じことを確かめるテストを重ねない）。
"""

from __future__ import annotations

import pytest

from ndf import cli

# 副命令の名前 → ok を出す引数（どれもプラグインの実物を読むだけで、書き換えない）
CALLS: dict[str, list[str]] = {
    "launch-args": [],
    "notify": ["send"],
    "manifests": ["render", "--check"],
    "hooks": ["render", "--check"],
    "contracts": ["check"],
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_ok_output(name, capsys, last_result):
    assert name in cli.COMMANDS
    assert cli.main([name, *CALLS[name]]) == 0
    report = last_result(capsys.readouterr().out)
    assert (report["tool"], report["status"]) == (name, "ok")
