"""launch-args の副命令（claude の起動の引数を結果 JSON で出す）。"""

from __future__ import annotations

import json

from ndf import identity
from ndf.adapters import claude, cli


def test_launch_args_subcommand_prints_the_same_values(capsys, last_result):
    assert cli.main([]) == 0

    out = last_result(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["items"] == [{"args": claude.plugin_args(identity.load().root)}]


def test_launch_args_refuses_arguments(capsys):
    assert cli.main(["x"]) != 0
    assert json.loads(capsys.readouterr().out)["cause"]["kind"] == "invalid"
