"""launch-args の副命令（claude の起動の引数を結果 JSON で出す）。"""

from __future__ import annotations

import json

from ndf import identity
from ndf.adapters import claude, cli


def test_launch_args_subcommand_prints_the_same_values(capsys, last_result, monkeypatch):
    monkeypatch.setattr(claude, "list_plugins", lambda: [{"id": "ndf@m", "enabled": True}])
    assert cli.main([]) == 0

    out = last_result(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["items"] == [{"args": claude.plugin_args(identity.load().root).args}]


def test_launch_args_adds_a_notice_when_the_list_is_unreadable(capsys, last_result, monkeypatch):
    """一覧を読めなくても ok のまま、知らせの行を 1 件足す（I10a）。"""
    monkeypatch.setattr(claude, "list_plugins", lambda: None)
    assert cli.main([]) == 0

    out = last_result(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["items"] == [{"args": ["--plugin-dir", str(identity.load().root)]}, {"kind": "notice", "reason": claude.LIST_UNREADABLE}]


def test_launch_args_refuses_arguments(capsys):
    assert cli.main(["x"]) != 0
    assert json.loads(capsys.readouterr().out)["cause"]["kind"] == "invalid"
